"""Causal indicators. All candles are CLOSED bid candles with UTC epoch timestamps."""

import hashlib
import math
from statistics import mean, pstdev


def ema(values, period):
    result = [values[0]]
    for value in values[1:]:
        result.append(result[-1] + 2 / (period + 1) * (value - result[-1]))
    return result


def indicators(bars):
    c = [b["close"] for b in bars]
    tr = [
        max(b["high"] - b["low"], abs(b["high"] - bars[i - 1]["close"]), abs(b["low"] - bars[i - 1]["close"]))
        for i, b in enumerate(bars)
        if i
    ]
    delta = [b - a for a, b in zip(c, c[1:])]
    gain = mean([max(d, 0) for d in delta[-14:]])
    loss = mean([max(-d, 0) for d in delta[-14:]])
    plus, minus = [], []
    for a, b in zip(bars, bars[1:]):
        up, down = b["high"] - a["high"], a["low"] - b["low"]
        plus.append(max(up, 0) if up > down else 0)
        minus.append(max(down, 0) if down > up else 0)
    # Rolling DX mean: explicitly a simple ADX variant, not Wilder smoothing.
    dx = []
    for i in range(14, len(tr) + 1):
        p, m = sum(plus[i - 14 : i]), sum(minus[i - 14 : i])
        dx.append(100 * abs(p - m) / (p + m) if p + m else 0)
    fast, slow = ema(c, 12), ema(c, 26)
    macd = [a - b for a, b in zip(fast, slow)]
    atr = mean(tr[-14:])
    return {
        **{f"ema{p}": ema(c, p)[-1] for p in (20, 50, 100, 200)},
        "rsi": 100 - 100 / (1 + gain / loss) if loss else (100 if gain else 50),
        "atr": atr,
        "adx": mean(dx[-14:]) if dx else 0,
        "macd": macd[-1],
        "macd_signal": ema(macd, 9)[-1],
        "bb_upper": mean(c[-20:]) + 2 * pstdev(c[-20:]),
        "bb_lower": mean(c[-20:]) - 2 * pstdev(c[-20:]),
        "momentum": c[-1] - c[-11],
        "regime": "high" if atr / c[-1] > 0.002 else "normal",
    }


def resample(bars, minutes):
    groups = {}
    for b in bars:
        key = int(b["time"] // (minutes * 60)) * minutes * 60
        groups.setdefault(key, []).append(b)
    result = []
    for key, group in sorted(groups.items()):
        if len(group) != minutes or group[-1]["time"] != key + (minutes - 1) * 60:
            continue
        result.append(
            dict(
                time=key + minutes * 60 - 60,
                open=group[0]["open"],
                high=max(b["high"] for b in group),
                low=min(b["low"] for b in group),
                close=group[-1]["close"],
            )
        )
    return result


def structure(bars):
    last = bars[-1]
    prior = bars[-21:-1]
    hi, lo = max(b["high"] for b in prior), min(b["low"] for b in prior)
    swings_hi = [
        bars[i]["high"]
        for i in range(2, len(bars) - 2)
        if bars[i]["high"] > max(bars[j]["high"] for j in (i - 2, i - 1, i + 1, i + 2))
    ]
    swings_lo = [
        bars[i]["low"]
        for i in range(2, len(bars) - 2)
        if bars[i]["low"] < min(bars[j]["low"] for j in (i - 2, i - 1, i + 1, i + 2))
    ]
    bos = 1 if last["close"] > hi else -1 if last["close"] < lo else 0
    previous_trend = 1 if bars[-2]["close"] > bars[-21]["close"] else -1
    sweep = 1 if last["low"] < lo and last["close"] > lo else -1 if last["high"] > hi and last["close"] < hi else 0
    fvg = 1 if last["low"] > bars[-3]["high"] else -1 if last["high"] < bars[-3]["low"] else 0
    ob = next((b for b in reversed(prior) if (b["close"] - b["open"]) * bos < 0), None) if bos else None
    return dict(
        bos=bos,
        choch=bos if bos and bos != previous_trend else 0,
        sweep=sweep,
        fvg=fvg,
        order_block=ob,
        swing_high=swings_hi[-1] if swings_hi else hi,
        swing_low=swings_lo[-1] if swings_lo else lo,
        equal_highs=abs(sorted(b["high"] for b in prior)[-1] - sorted(b["high"] for b in prior)[-2]) <= 0.00005,
        equal_lows=abs(sorted(b["low"] for b in prior)[0] - sorted(b["low"] for b in prior)[1]) <= 0.00005,
        zone="discount" if last["close"] < (hi + lo) / 2 else "premium",
    )


def signal(bars, threshold=70):
    # 20 completed H4 bars minimum; 200 M1 bars for indicators.
    frames = {m: resample(bars, m) for m in (5, 15, 60, 240)}
    if len(bars) < 200 or any(len(v) < 20 for v in frames.values()):
        return None
    ind = indicators(bars)
    st = structure(frames[5])
    direction = 1 if ind["ema20"] > ind["ema50"] > ind["ema200"] else -1 if ind["ema20"] < ind["ema50"] < ind["ema200"] else 0
    if not direction:
        return None
    contributions = {
        "ema_alignment": 20,
        "m5_structure": 20 if direction in (st["bos"], st["sweep"]) else 0,
        "m15_confirmation": 15
        if (frames[15][-1]["close"] - ema([b["close"] for b in frames[15]], 20)[-1]) * direction > 0
        else 0,
        "h1_bias": 15 if (frames[60][-1]["close"] - ema([b["close"] for b in frames[60]], 20)[-1]) * direction > 0 else 0,
        "h4_context": 10 if (frames[240][-1]["close"] - ema([b["close"] for b in frames[240]], 20)[-1]) * direction > 0 else 0,
        "momentum": 10 if (ind["macd"] - ind["macd_signal"]) * direction > 0 else 0,
        "trend_strength": 10 if ind["adx"] > 20 else 0,
    }
    if sum(contributions.values()) < threshold or not contributions["m5_structure"]:
        return None
    entry = bars[-1]["close"]
    distance = max(ind["atr"] * 1.5, 0.0003)
    at = bars[-1]["time"]
    return dict(
        id=hashlib.sha256(f"eurusd-v1:{at}:{direction}".encode()).hexdigest(),
        symbol="EURUSD",
        direction="BUY" if direction == 1 else "SELL",
        timestamp=at,
        entry=entry,
        sl=entry - direction * distance,
        tp=entry + direction * distance * 2,
        rr=2,
        strategy="confluence-v1",
        confidence=sum(contributions.values()),
        contributions=contributions,
        indicators=ind,
        structure=st,
        regime=ind["regime"],
        explanation="Weighted closed-bar multi-timeframe confluence; score is not a probability.",
    )


def validate_bars(bars):
    if not bars:
        raise ValueError("No candles supplied")
    previous = None
    gaps = 0
    for b in bars:
        if any(not math.isfinite(float(b[k])) for k in ("time", "open", "high", "low", "close")):
            raise ValueError("Non-finite candle")
        if (
            b["time"] % 60
            or b["low"] <= 0
            or not b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]
        ):
            raise ValueError("Invalid M1 OHLC or timestamp alignment")
        if previous is not None:
            if b["time"] <= previous:
                raise ValueError("Duplicate or unordered timestamps")
            gaps += max(0, int((b["time"] - previous) / 60) - 1)
        previous = b["time"]
    return gaps
