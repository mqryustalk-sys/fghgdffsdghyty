# Architecture and mathematical conventions

Browser → Next.js same-origin /api proxy → FastAPI → SQLAlchemy ledger.
SQLite uses BEGIN IMMEDIATE; PostgreSQL uses a transaction advisory lock. Every state mutation and reservation takes the same database lock. The ledger is authoritative after restart. JSON payloads provide extensibility; baskets reference signal records and orders reference baskets through foreign keys.

Modules: db (schema/transactions), strategy (causal features), engine (paper ledger/risk), backtest (isolated research), main (authenticated HTTP/WebSocket), gateway (separate Windows MT5 demo adapter). There is no live routing path from the terminal.

Paper state and child orders commit in one transaction because the simulated broker is the database itself. This atomicity does NOT generalize to a remote broker. Rejection/partial-fill/disconnect test modes create per-child terminal states. MT5 persists UNKNOWN before submission and requires human reconciliation of uncertain requests; it never retries an ambiguous order automatically.

## Indicators / structure
- EMA uses alpha=2/(period+1), seeded from first observed close.
- RSI uses mean positive/negative changes over the last 14 bars, not Wilder smoothing.
- ATR is the last 14 true ranges' simple mean.
- ADX is mean of rolling 14-bar DX values, a simple variant, not Wilder ADX.
- MACD = EMA12 − EMA26; signal EMA9. Bollinger = mean20 ± 2 population standard deviations. Momentum = close − close ten bars ago.
- Swings need two strictly lower highs / higher lows on each side; the latest two bars cannot be confirmed pivots.
- BOS: close breaks previous 20-bar high/low. CHoCH: BOS opposes prior 20-bar close trend.
- Sweep: bar breaches prior 20-bar extreme then closes back inside.
- FVG: current low > high two bars ago (bull), or current high < low two bars ago (bear).
- Order block: last opposite-bodied candle before a detected BOS.
- Equal extremes: highest two highs / lowest two lows differ by at most 0.5 pip.
- Premium/discount relative to midpoint of prior 20-bar range.
- Volatility high if ATR/close > 0.002.
- Scanner weights: EMA alignment 20, M5 structure 20, M15 confirmation 15, H1 bias 15, H4 context 10, MACD momentum 10, ADX strength 10. Requires score >=70 and M5 structure. Scores are not calibrated probabilities.
- Timeframe aggregation accepts complete minute-aligned buckets only. A scan sees only its input prefix. H4 context uses >=20 bars, not EMA200 H4.
- Entry stops = 1.5 ATR minimum 3 pips; target = 2R before spread and costs.

## Risk and execution
USD account, EURUSD, contract 100,000 EUR/lot, pip 0.0001 USD/EUR, hence USD 10/pip/lot. These are explicit simulator metadata, not universal broker assumptions. Minimum/step 0.01 lot, maximum 100 lots, leverage 30:1, commission USD 7/lot round trip. Sizing floors per-child volume to lot step after dividing the basket budget by child count and including commission. No martingale or recovery grid.

BUY fills ask, SELL fills bid. Position marks/closures use the executable side. Cash pays half commission on entry and half on exit. Stop gaps fill at the received quote, not at the promised stop price. Open exposure is reported in EUR. Daily cutover is UTC; peak equity persists. News coverage expires after one hour and high-impact EUR/USD events impose +/-15-minute blackout.

Reservations count all requested children including rejected ones, conservatively. Retrying a signal returns the original basket. Simulated account semantics are hedging. Netting is not emulated.

## Research
Backtests use next-bar open with adverse spread/slippage. If SL and TP are touched in one bar, stop wins. Stops gap adversely. Mark-to-market equity drives drawdown; fees included. Single-position, one-order baskets only. No optimization runs on holdout. Walk-forward repeats fixed rules in daily windows; bootstrap assumes IID trade PnL and does not prove future outcomes. Sharpe/Sortino use observed daily dollar-PnL series with sqrt(252), not full calendar-day percentage returns.

## Security boundary
One administrator role only, no public registration; opaque random 8-hour cookies stored as token hashes. Scrypt password hashes. HttpOnly/SameSite Strict cookies, configurable Secure for HTTPS. REST origin checks, WS origin/session checks, in-process login rate limit. No broker secrets in frontend. LLM commentary is currently deterministic; it has no execution authority. Redis is not required because the database handles serialization; distributed rate limiting remains future work.

Optional trailing stops use the ATR captured at entry, multiplied by the configured trailing_atr value. They only tighten, with a minimum one-pip distance from the executable quote. Break-even includes the simulator's round-trip commission equivalent. Neither mechanism guarantees an exit at the stop in a gap.
