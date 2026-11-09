# Verification — 2026-11-09

## Passed
- Backend: 17 pytest tests. Aggregate lot sizing, 20-child baskets, signal idempotency, rolling limits, concurrent same/different-signal submissions, partial fills, disconnect/rejections, fail-closed news, stale/spread gates, stop-loss persistence across engine restart, loss latch, indicator/data validation, BUY/SELL PnL, API authentication/config validation, WebSocket authentication/state, emergency close, logout revocation, adverse ambiguous-candle backtests, completed-timeframe causality, cooldown, monotonic trailing stops.
- Ruff static checks and Python compile checks; gateway compiled but not imported (Windows-only dependency).
- Alembic upgrade → downgrade → upgrade against a fresh SQLite database.
- Next.js 15.5.29 production build including TypeScript checks, with patched PostCSS 8.5.23 and Sharp 0.35.5.
- npm audit: 0 known vulnerabilities at verification time.
- Chromium Playwright browser workflow: authenticated overview, actual account equity, configuration persistence, pause/resume, missing-data error, desktop/mobile screenshots. Final run recorded below.
- Compose syntax validation.
- Backend Docker build and final container smoke: Alembic migration, account initialization, application health returned status=ok, mode=SIMULATED, live_enabled=false.
- Frontend Docker build passed before the dependency security updates; final patched frontend was built locally. Final frontend image has not been rebuilt.
- Actual local HTTP frontend/backend and authenticated WebSocket ran together.

## Failures investigated and corrected
- Git access initially failed inside restricted sandbox; repository read succeeded with approved network access.
- API test initially stalled because local socket operations were restricted; all tests passed with local socket access.
- Initial Playwright assertion matched Next.js's route announcer as well as the application error. Narrowed to application error; passed.
- Docker dependency fetch initially failed proxy CA trust. Added optional BuildKit CA secret; TLS-verified builds succeeded.
- Docker runtime first failed to read migration configuration. Source files had restrictive ownership; Dockerfile now assigns application ownership to its non-root user. Final container smoke passed.
- Docker's VFS build cache exhausted the 7.9 GB workspace during testing. Removed generated test images/cache, recovering space. No application data was deleted.
- Dependency audit found critical/high advisories in initial pins. Upgraded Next/Playwright and patched transitive dependencies; final audit clean.

## Not verified
Windows MT5 execution, live broker orders, external calendar/data providers, PostgreSQL concurrency/migrations on a running PostgreSQL server, full Compose runtime, HTTPS issuance, public deployment, backup/restore against PostgreSQL, external AI models. No historical profitability report: no real market dataset supplied. Tests use explicitly synthetic inputs.

## Reproduce
```sh
.venv/bin/pytest -q
.venv/bin/ruff check app tests scripts gateway
cd frontend
npm ci
npm run build
npm audit
```

Browser test setup (isolated local paper database): run `python -m scripts.prepare_browser_test`, start services, then `cd frontend && npx playwright test`. It uses a generated temporary password at /tmp/forex-browser-password and Chromium at /usr/bin/chromium; change the executable path for macOS/Windows. Never run this test-user bootstrap against a shared deployment.

Screenshots: artifacts/overview.png and artifacts/mobile.png. They depict actual empty paper-account state, not fabricated trades.
