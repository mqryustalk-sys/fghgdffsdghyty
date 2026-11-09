# Northstar FX — EUR/USD paper research terminal

A locally runnable implementation based on the supplied **Claude Code EUR/USD Forex Master Prompt**. It is a paper-trading research platform, **not a guaranteed profitable or live-ready bot**. Real-money routing is not exposed by the backend.

## Start locally (Python 3.12+, Node 22+)

Run from this directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m scripts.create_user
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev
```

- Terminal: http://localhost:3000
- Backend health: http://localhost:8000/api/health
- Interactive API documentation: http://localhost:8000/docs
- Defaults: USD 10,000 paper balance; paused; news verification required; no prices loaded.
- Create your own administrator with the interactive command; there is no default password.

For a production frontend build: `npm run build`, then copy `.next/static` to `.next/standalone/.next/static` and run `node .next/standalone/server.js`. Docker handles that copy.

## What works

- Next.js/TypeScript terminal with 15 navigation views, candlesticks, persistent risk editor, state tables, actions and reconnecting authenticated WebSocket updates.
- FastAPI, SQLAlchemy and Alembic; SQLite local storage, PostgreSQL deployment configuration.
- CSV validation/import and closed-bar M1/M5/M15/H1/H4 scanner; indicators, weighted confluence, explicitly defined market-structure features.
- BUY/SELL paper baskets with up to 20 child orders sharing one 0.5% risk budget; database-serialized reservations, signal idempotency, 2 setups/40 reserved orders per rolling hour.
- Spread, freshness, margin, aggregate risk, daily loss, drawdown, session, cooldown, position, basket and news gates.
- Persistent simulated positions, quote-driven SL/TP, time exits, optional break-even, partial close, emergency closure and latched stop.
- Next-bar candle backtesting with spread/slippage/commission, final-30% holdout, walk-forward windows and 500 trade-bootstrap paths.
- Salted scrypt authentication, server-side revocable sessions, same-origin checks, login throttling and audit records.
- Standalone Windows MT5 **demo-only** gateway source; Docker/HTTPS/backup configuration.

## Paper workflow

1. Log in. Under Market Scanner, enter the source name and import genuine M1 bid history.
2. CSV columns: `time,open,high,low,close`. Time is UTC epoch seconds or ISO 8601 with a timezone. Bars must be closed, ordered, unique and minute-aligned.
3. At least 20 complete H4 candles (~4,800 M1 bars, often more due to alignment/gaps) are required for a signal. Scanner abstention is expected.
4. Backtesting needs at least 5,000 bars. Imported historical prices are never treated as fresh quotes.
5. For paper execution, publish a current bid/ask via Broker Connection. Import provider-sourced news coverage, or deliberately disable the news requirement in Risk Management for simulation.
6. Resume the engine, scan current closed candles, select 1–20 child orders, and validate/execute. Rejected requests display their reasons. Entry requires a fresh signal and quote.
7. Feed new quotes via `POST /api/quote` to update positions and process stops. The simulator does not invent a price feed.
8. For repeated scanning, run `python -m scripts.worker` with `TERMINAL_USER` and `TERMINAL_PASSWORD` in the process environment. A separate data adapter must keep CSV history and quotes current. The worker submits one-order baskets and never overrides risk checks.

No supplied historical dataset means no measured profitability or strategy edge is claimed. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for the explicit gaps against the PDF, [TEST_RESULTS.md](TEST_RESULTS.md) for verification, and [RUNBOOK.md](RUNBOOK.md) for deployment/Windows instructions.
