# Readiness and remaining scope

Readiness: locally integration-tested PAPER RESEARCH PROTOTYPE. Not a complete implementation of every requirement in the PDF and not live validated.

- No licensed market-data or economic-calendar provider is configured; manual CSV, quote and calendar APIs work. Automated execution cannot operate unattended without a continuously refreshed external data adapter.
- Windows MT5 source is implemented but cannot be exercised on this Linux host. Demo-only, IOC assumption, no terminal integration, automatic reconciliation, or validated netting behavior.
- Only USD-denominated EURUSD simulated contract metadata is supported; arbitrary account-currency conversion is not implemented.
- No tick backtests, fill-depth/liquidity model, broker latency distribution, partial-fill stochastic model or basket backtesting. Backtest rules are narrower than the live paper ledger (single position, one child, no news calendar, no 3-loss latch).
- Reversal-based profit protection is not implemented. Optional trailing uses entry-time ATR, not a continuously recalculated ATR. No basket-level price-stop editor; children share initial signal exits. Break-even and time exits are implemented.
- Database uses a generic typed record store for users, events, signals, market history, configs and reports; not a separate normalized table for every PDF entity. No Redis cache, distributed rate limiter or multi-role authorization.
- Strategy is one configurable confluence rule, not multiple independently selectable strategies. EMA/RSI/ADX definitions are explicit simple variants. No demonstrated edge; no genuine market data was supplied.
- Dashboard chart has candles but no execution markers. Performance view shows account-level state; full historical performance metrics are in saved backtest reports. Weekly/monthly account attribution, table filters are incomplete; saved research reports include long/short/session aggregates.
- AI interface currently provides deterministic local commentary only; no external LLM adapter.
- Data history is stored as one bounded JSON dataset and loaded in memory; unsuitable for large tick archives. Imports replace history instead of appending. Economic coverage is an operator assertion.
- Stops only process on received quotes. Emergency/manual exits use the last stored quote, even if stale, explicitly as simulation.
- In-process login throttling resets on restart and is not distributed. API docs remain public; operational data requires auth.
- Gateway order validation is not equivalent to the terminal risk engine. Do not enable gateway demo execution without broker-specific validation.
- HTTPS issuance and public deployment have not been performed. See TEST_RESULTS for exact Docker validation status.
