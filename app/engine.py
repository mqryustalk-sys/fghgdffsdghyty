import math
import time
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from .db import State, Record, Basket, Order, transaction

DEFAULTS = dict(
    risk_pct=0.5,
    daily_loss_pct=2.0,
    drawdown_pct=5.0,
    max_open_risk_pct=1.0,
    max_positions=40,
    max_baskets=2,
    cooldown_seconds=300,
    max_spread_pips=2.0,
    max_slippage_pips=1.0,
    news_required=True,
    enabled=True,
    break_even=False,
    trailing_atr=0.0,
    max_duration_minutes=120,
)
META = dict(
    symbol="EURUSD",
    account_currency="USD",
    contract_size=100000.0,
    pip_size=0.0001,
    lot_min=0.01,
    lot_step=0.01,
    lot_max=100.0,
    leverage=30.0,
    commission_per_lot=7.0,
)


def uid():
    return str(uuid.uuid4())


def audit(s, kind, data, now=None):
    s.add(Record(id=uid(), kind=kind, at=now or time.time(), data=data))


def initialize(engine):
    with transaction(engine) as s:
        if not s.get(State, 1):
            s.add(
                State(
                    id=1,
                    data=dict(
                        balance=10000.0,
                        peak=10000.0,
                        day_start=10000.0,
                        day="",
                        paused=True,
                        stopped=False,
                        connected=True,
                        config=DEFAULTS,
                        quote=None,
                        news=None,
                        consecutive_losses=0,
                        metadata=META,
                    ),
                )
            )


def orders(s):
    return list(s.scalars(select(Order)))


def active(s):
    return [o for o in orders(s) if o.data["status"] == "FILLED"]


def sign(d):
    return 1 if d == "BUY" else -1


def pnl(d, quote):
    price = quote["bid"] if d["direction"] == "BUY" else quote["ask"]
    return (price - d["entry"]) * sign(d["direction"]) * d["lots"] * META["contract_size"]


def account(s, st):
    positions = active(s)
    floating = sum(pnl(o.data, st["quote"]) for o in positions) if st["quote"] else 0
    margin = sum(o.data["lots"] * META["contract_size"] * o.data["entry"] / META["leverage"] for o in positions)
    equity = st["balance"] + floating
    risk = sum(
        max(0, (o.data["entry"] - o.data["sl"]) * sign(o.data["direction"])) * o.data["lots"] * META["contract_size"]
        for o in positions
    )
    return dict(
        balance=st["balance"],
        equity=equity,
        floating_pnl=floating,
        margin=margin,
        free_margin=equity - margin,
        open_risk=risk,
        drawdown_pct=max(0, (st["peak"] - equity) / st["peak"] * 100),
        daily_pnl=equity - st["day_start"],
        positions=len(positions),
        eur_exposure=sum(sign(o.data["direction"]) * o.data["lots"] * 100000 for o in positions),
    )


def sizing(budget, distance, count):
    # Include round-trip commission in aggregate risk, floor to broker lot step.
    lots = (
        math.floor((budget / (distance * META["contract_size"] + META["commission_per_lot"])) / count / META["lot_step"] + 1e-9)
        * META["lot_step"]
    )
    return round(min(lots, META["lot_max"]), 2)


def close_order(s, st, o, reason, fraction=1.0):
    d = dict(o.data)
    qty = round(d["lots"] * fraction / META["lot_step"]) * META["lot_step"]
    if qty < META["lot_min"] or (d["lots"] - qty > 1e-8 and d["lots"] - qty < META["lot_min"] - 1e-8):
        raise ValueError("Close size violates lot increment")
    realized = pnl({**d, "lots": qty}, st["quote"]) - qty * META["commission_per_lot"] / 2
    st["balance"] += realized
    st["consecutive_losses"] = st["consecutive_losses"] + 1 if realized < 0 else 0
    audit(s, "execution", dict(order_id=o.id, basket_id=o.basket_id, reason=reason, lots=qty, pnl=realized, quote=st["quote"]))
    if qty >= d["lots"] - 1e-8:
        d.update(
            status="CLOSED",
            exit=st["quote"]["bid"] if d["direction"] == "BUY" else st["quote"]["ask"],
            realized_pnl=d.get("realized_pnl", 0) + realized,
            closed_at=st["quote"]["time"],
            exit_reason=reason,
        )
    else:
        d.update(lots=round(d["lots"] - qty, 2), realized_pnl=d.get("realized_pnl", 0) + realized)
    o.data = d
    s.flush()


def update_quote(s, st, quote):
    st["quote"] = quote
    for o in active(s):
        d = dict(o.data)
        side = sign(d["direction"])
        price = quote["bid"] if side == 1 else quote["ask"]
        reason = "stop_loss" if (price - d["sl"]) * side <= 0 else "take_profit" if (price - d["tp"]) * side >= 0 else None
        if quote["time"] - o.at >= st["config"]["max_duration_minutes"] * 60:
            reason = reason or "time_exit"
        if reason:
            close_order(s, st, o, reason)
        else:
            previous_stop = d["sl"]
            if st["config"]["break_even"] and (price - d["entry"]) * side >= d["initial_distance"]:
                d["sl"] = max(d["sl"], d["entry"] + 0.00007) if side == 1 else min(d["sl"], d["entry"] - 0.00007)
            if st["config"]["trailing_atr"] > 0:
                gap = max(d.get("atr", d["initial_distance"] / 1.5) * st["config"]["trailing_atr"], 0.0001)
                candidate = price - side * gap
                d["sl"] = max(d["sl"], candidate) if side == 1 else min(d["sl"], candidate)
            if d["sl"] != previous_stop:
                audit(s, "risk", {"order_id": o.id, "reason": "stop_tightened", "before": previous_stop, "after": d["sl"]})
                o.data = d
    a = account(s, st)
    st["peak"] = max(st["peak"], a["equity"])
    day = datetime.fromtimestamp(quote["time"], timezone.utc).date().isoformat()
    if day != st["day"]:
        st.update(day=day, day_start=a["equity"])
    if (
        a["drawdown_pct"] >= st["config"]["drawdown_pct"]
        or a["daily_pnl"] <= -st["day_start"] * st["config"]["daily_loss_pct"] / 100
    ):
        st["stopped"] = True
        st["paused"] = True
        audit(s, "risk", {"reason": "Loss limit reached"})
        for o in active(s):
            close_order(s, st, o, "loss_limit")


def execute(engine, signal_id, count=1, now=None, fault=None):
    now = now or time.time()
    with transaction(engine) as s:
        existing = s.scalar(select(Basket).where(Basket.signal_id == signal_id))
        if existing:
            return dict(id=existing.id, **existing.data, replayed=True)
        record = s.get(Record, signal_id)
        if not record or record.kind != "signal":
            raise ValueError("Unknown signal")
        sig = record.data
        row = s.get(State, 1)
        st = dict(row.data)
        cfg = st["config"]
        q = st["quote"]
        a = account(s, st)
        reasons = []
        if not 1 <= count <= 20:
            reasons.append("1–20 orders required")
        if st["paused"] or st["stopped"] or not cfg["enabled"]:
            reasons.append("Engine paused/stopped or strategy disabled")
        if not st["connected"]:
            reasons.append("Broker disconnected")
        if not q or now - q["time"] > 120 or q["time"] > now + 5:
            reasons.append("Missing/stale quote")
        if now - sig["timestamp"] > 120 or sig["timestamp"] > now + 5:
            reasons.append("Stale signal")
        if datetime.fromtimestamp(now, timezone.utc).weekday() >= 5:
            reasons.append("Weekend session")
        if cfg["news_required"]:
            news = st.get("news")
            if not news or now - news["verified_at"] > 3600 or not news["coverage_start"] <= now <= news["coverage_end"]:
                reasons.append("News coverage unverified")
            elif any(
                abs(e["time"] - now) <= 900 and e["currency"] in ("EUR", "USD") and e["impact"] == "high" for e in news["events"]
            ):
                reasons.append("High-impact news blackout")
        recent = list(s.scalars(select(Basket).where(Basket.at > now - 3600)))
        if len(recent) >= 2:
            reasons.append("2 setups/hour limit")
        if sum(b.data["requested_orders"] for b in recent) + count > 40:
            reasons.append("40 orders/hour limit")
        if recent and now - max(b.at for b in recent) < cfg["cooldown_seconds"]:
            reasons.append("Cooldown")
        opened = active(s)
        if len(opened) + count > cfg["max_positions"]:
            reasons.append("Position limit")
        if len({o.basket_id for o in opened}) >= cfg["max_baskets"]:
            reasons.append("Basket limit")
        if a["drawdown_pct"] >= cfg["drawdown_pct"]:
            reasons.append("Drawdown limit")
        if a["daily_pnl"] <= -st["day_start"] * cfg["daily_loss_pct"] / 100:
            reasons.append("Daily loss limit")
        if st["consecutive_losses"] >= 3:
            reasons.append("3 consecutive losses")
        if sig["regime"] == "high":
            reasons.append("High volatility")
        entry = (q["ask"] if sig["direction"] == "BUY" else q["bid"]) if q else sig["entry"]
        side = sign(sig["direction"])
        distance = (entry - sig["sl"]) * side
        if distance <= 0 or (sig["tp"] - entry) * side < distance:
            reasons.append("Invalid stops or reward/risk below 1")
        if q and (q["ask"] - q["bid"]) / 0.0001 > cfg["max_spread_pips"] + 1e-8:
            reasons.append("Spread limit")
        if abs(entry - sig["entry"]) / 0.0001 > cfg["max_slippage_pips"] + cfg["max_spread_pips"]:
            reasons.append("Price deviation")
        lots = sizing(a["equity"] * cfg["risk_pct"] / 100, max(distance, 0.00001), max(count, 1))
        risk = lots * count * (max(distance, 0) * 100000 + 7)
        if lots < 0.01:
            reasons.append("Risk budget below minimum lot size")
        if risk + a["open_risk"] > a["equity"] * cfg["max_open_risk_pct"] / 100:
            reasons.append("Aggregate risk limit")
        if lots * count * 100000 * entry / 30 > a["free_margin"]:
            reasons.append("Insufficient margin")
        report = dict(approved=not reasons, reasons=reasons, lots_per_order=lots, aggregate_risk=risk, entry=entry)
        audit(s, "validation", dict(signal_id=signal_id, **report), now)
        if reasons:
            return report
        basket = Basket(
            id=uid(),
            signal_id=signal_id,
            at=now,
            data=dict(requested_orders=count, signal=sig, risk=risk, status="RESERVED", report=report),
        )
        s.add(basket)
        s.flush()
        filled = 0
        for i in range(count):
            status = "REJECTED" if fault == "reject" or (fault == "partial" and i >= count // 2) else "FILLED"
            if fault == "disconnect":
                status = "CANCELLED"
            d = dict(
                direction=sig["direction"],
                lots=lots,
                original_lots=lots,
                entry=entry,
                sl=sig["sl"],
                tp=sig["tp"],
                initial_distance=distance,
                atr=sig.get("indicators", {}).get("atr", distance / 1.5),
                status=status,
                realized_pnl=-lots * 7 / 2 if status == "FILLED" else 0,
                mode="SIMULATED",
            )
            s.add(Order(id=f"{basket.id}:{i}", basket_id=basket.id, at=now, data=d))
            if status == "FILLED":
                st["balance"] -= lots * 7 / 2
                filled += 1
        basket.data = {
            **basket.data,
            "status": "FILLED" if filled == count else "PARTIAL" if filled else "REJECTED",
            "filled_orders": filled,
        }
        row.data = st
        audit(s, "basket", dict(id=basket.id, filled=filled), now)
        return dict(id=basket.id, **basket.data)
