# Operations

## Local startup
Follow README. Always run migrations before starting the API and create an administrator before using the terminal. Working directory must be project root for SQLite. Keep terminal.db and its WAL together; stop services before a filesystem backup, or use Python's sqlite3 backup API.

The local services are localhost:3000 (frontend) and localhost:8000 (API). Do not expose development HTTP to the public internet. Production uses HTTPS and COOKIE_SECURE=true.

## Docker / Ubuntu
Copy .env.example to .env, choose a long URL-safe POSTGRES_PASSWORD, set FRONTEND_ORIGIN to the exact browser origin.
```sh
docker compose up -d --build
docker compose exec backend python -m scripts.create_user
```
For HTTPS set DOMAIN, FRONTEND_ORIGIN=https://your-domain and COOKIE_SECURE=true; point DNS to the server, then `docker compose --profile https up -d`. Only Caddy ports need public access. Frontend/API direct ports bind loopback. PostgreSQL is internal.

Check `docker compose ps`, `docker compose logs --tail=100 backend`, and /api/health. A status of uninitialized means the user/bootstrap step has not run. Back up using scripts/backup.sh. Restore only into an EMPTY database with scripts/restore.sh; it never silently drops existing tables.

## News import
POST /api/news with a real source attribution and epoch seconds:
```json
{"source":"Your licensed provider","coverage_start":1794222000,"coverage_end":1794229200,"events":[]}
```
Empty events means the operator asserts the provider has no matching events in that window. Do not submit empty coverage merely to bypass a missing provider. This manual adapter is not independent verification. Include each event's time, currency (EUR/USD), impact (low/medium/high), title.

## Execution / recovery
Quote updates are the simulator clock. No updates means no newly evaluated exits; do not interpret unchanged PnL as a live mark. A network interruption does not fabricate new quotes. Reconnect and send fresh data; committed positions remain.

Pause blocks entries but quote-driven position exits still operate. Emergency stop closes all paper positions at the last available quote and latches stopped=true. That is a simulated liquidation, not a promise of real execution. Resume cannot clear the latch. For a new research experiment, back up the old ledger and use a new database URL, rerun migrations and create a user; keep the prior experiment for audit.

Worker: provide TERMINAL_USER and TERMINAL_PASSWORD securely in its process environment, then python -m scripts.worker. TERMINAL_URL defaults to http://localhost:8000. Its 8-hour session expires explicitly; restart/re-authenticate. It does not ingest external prices or news on its own.

## Windows MT5 gateway (UNVALIDATED)
Requires Windows, compatible Python/MetaTrader5 wheel, MT5 terminal installed and logged into a DEMO account.
```powershell
py -m venv .venv
.venv\Scripts\pip install -r gateway\requirements.txt
$env:GATEWAY_TOKEN = "<generate a random token of at least 32 characters>"
$env:MT5_SYMBOL = "EURUSD"
.venv\Scripts\python -m uvicorn gateway.main:app --host 127.0.0.1 --port 8100
```
Keep loopback or put an HTTPS reverse proxy/private tunnel in front. Bearer token required. Health/account, symbol/tick, candles, positions and deal history endpoints support diagnosis. Broker suffixes use MT5_SYMBOL. Real accounts are always rejected.

Demo order routing additionally requires MT5_DEMO_EXECUTION=enabled. Before enabling, verify symbol contract, volume constraints, stop levels, filling policy and account netting/hedging behavior on that broker. IOC is currently assumed and unsupported symbols will reject. Close via /orders with position ticket and opposite side; modifications via /modify. API /docs documents requests. This gateway does not enforce the terminal's basket-risk policy independently and is NOT wired to the terminal execution path. Do not expose it as a general remote order endpoint.

An UNKNOWN request is never resent. Reconcile gateway.db key/comment against /history and /positions, including partial fills, before deciding on further action. There is no automated reconciliation daemon. Do not delete idempotency records to force a retry.

## Builds behind an enterprise/cloud TLS proxy
Both Dockerfiles support optional BuildKit secret proxy_ca. Example:
```sh
docker build --secret id=proxy_ca,src=/path/to/trusted-ca-bundle.pem -f Dockerfile.backend -t eurusd-backend .
docker build --secret id=proxy_ca,src=/path/to/trusted-ca-bundle.pem -f Dockerfile.frontend -t eurusd-frontend .
```
This mounts trust material only during dependency installation; TLS verification remains enabled.
