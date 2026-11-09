"""Next-bar fills, adverse spread/slippage, stop-first ambiguous candles."""

import random
from statistics import mean, pstdev
from .strategy import signal
from .engine import sizing
from datetime import datetime, timezone


def run(bars, start=0, spread_pips=1.0, slippage_pips=0.2):
    balance = 10000.0
    peak = balance
    drawdown = 0.0
    trades = []
    pending = None
    position = None
    daily = {}
    setups = []
    equity = []
    day_start = balance
    day = None
    halted = False
    for i, b in enumerate(bars):
        if i < start:
            continue
        current_day = datetime.fromtimestamp(b["time"], timezone.utc).date()
        if current_day != day:
            day = current_day
            day_start = balance
        spread = spread_pips * 0.0001
        slip = slippage_pips * 0.0001
        if pending and not position and not halted:
            side = 1 if pending["direction"] == "BUY" else -1
            entry = b["open"] + (spread if side == 1 else 0) + side * slip
            distance = (entry - pending["sl"]) * side
            if distance > 0 and (pending["tp"] - entry) * side >= distance:
                lots = sizing(balance * 0.005, distance, 1)
                if lots >= 0.01:
                    position = dict(**pending, fill=entry, lots=lots, opened=b["time"])
            pending = None
        if position:
            p = position
            side = 1 if p["direction"] == "BUY" else -1
            low = b["low"] + (spread if side == -1 else 0)
            high = b["high"] + (spread if side == -1 else 0)
            stop = low <= p["sl"] if side == 1 else high >= p["sl"]
            target = high >= p["tp"] if side == 1 else low <= p["tp"]
            expired = b["time"] - p["opened"] >= 7200
            if stop or target or expired or i == len(bars) - 1:
                open_exit = b["open"] + (spread if side == -1 else 0)
                price = (
                    (min(p["sl"], open_exit) if side == 1 else max(p["sl"], open_exit))
                    if stop
                    else p["tp"]
                    if target
                    else b["close"] + (spread if side == -1 else 0)
                )
                price -= side * slip
                gross = (price - p["fill"]) * side * p["lots"] * 100000
                cost = p["lots"] * 7
                net = gross - cost
                balance += net
                trades.append(
                    dict(
                        time=b["time"],
                        direction=p["direction"],
                        pnl=net,
                        gross=gross,
                        commission=cost,
                        duration=b["time"] - p["opened"],
                        session="London/NY" if 7 <= datetime.fromtimestamp(b["time"], timezone.utc).hour < 21 else "Asia",
                    )
                )
                daily[str(day)] = daily.get(str(day), 0) + net
                position = None
        floating = (
            0
            if not position
            else (b["close"] + (spread if position["direction"] == "SELL" else 0) - position["fill"])
            * (1 if position["direction"] == "BUY" else -1)
            * position["lots"]
            * 100000
            - position["lots"] * 7
        )
        eq = balance + floating
        peak = max(peak, eq)
        drawdown = max(drawdown, (peak - eq) / peak * 100)
        equity.append(dict(time=b["time"], equity=eq))
        halted = halted or drawdown >= 5
        setups = [t for t in setups if t > b["time"] - 3600]
        if (
            not position
            and not halted
            and balance > day_start * 0.98
            and len(setups) < 2
            and (not setups or b["time"] - setups[-1] >= 300)
            and i < len(bars) - 1
        ):
            if datetime.fromtimestamp(b["time"], timezone.utc).weekday() < 5:
                pending = signal(bars[max(0, i - 5999) : i + 1])
                if pending:
                    setups.append(b["time"])
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]
    returns = list(daily.values())
    std = pstdev(returns) if len(returns) > 1 else 0
    downside = (mean([min(x, 0) ** 2 for x in returns]) ** 0.5) if returns else 0
    return dict(
        mode="HISTORICAL CANDLE SIMULATION",
        net_pnl=balance - 10000,
        gross_pnl=sum(t["gross"] for t in trades),
        commissions=sum(t["commission"] for t in trades),
        win_rate=len(wins) / len(trades) if trades else 0,
        profit_factor=sum(wins) / abs(sum(losses)) if losses else None,
        max_drawdown_pct=drawdown,
        sharpe=mean(returns) / std * 252**0.5 if std else None,
        sortino=mean(returns) / downside * 252**0.5 if downside else None,
        expectancy=mean([t["pnl"] for t in trades]) if trades else 0,
        orders=len(trades),
        setups=len(trades),
        spread_pips=spread_pips,
        slippage_pips=slippage_pips,
        trades=trades,
        equity=equity[:: max(1, len(equity) // 500)],
        limitations=[
            "One order per setup; no tick replay or news history",
            "One-bar latency; no parameter optimization",
            "Daily return statistics use observed trading days",
        ],
    )


def research(bars):
    split = int(len(bars) * 0.7)
    result = run(bars, start=split)
    result["by_direction"] = {
        key: {
            "orders": sum(t["direction"] == key for t in result["trades"]),
            "net_pnl": sum(t["pnl"] for t in result["trades"] if t["direction"] == key),
        }
        for key in ("BUY", "SELL")
    }
    result["by_session"] = {
        key: {
            "orders": sum(t["session"] == key for t in result["trades"]),
            "net_pnl": sum(t["pnl"] for t in result["trades"] if t["session"] == key),
        }
        for key in ("London/NY", "Asia")
    }
    result["average_duration_seconds"] = mean([t["duration"] for t in result["trades"]]) if result["trades"] else 0
    result["split"] = dict(training_bars=split, out_of_sample_bars=len(bars) - split, optimized=False)
    result["walk_forward"] = [
        dict(start=i, end=min(i + 1440, len(bars)), result=run(bars[: min(i + 1440, len(bars))], start=i)["net_pnl"])
        for i in range(max(4800, split), len(bars), 1440)
    ]
    values = [t["pnl"] for t in result["trades"]]
    rng = random.Random(42)
    endings = []
    for _ in range(500):
        endings.append(10000 + sum(rng.choices(values, k=len(values))) if values else 10000)
    endings.sort()
    result["monte_carlo"] = dict(
        method="IID trade bootstrap; ignores serial dependence", runs=500, p05=endings[25], p50=endings[250], p95=endings[475]
    )
    return result
