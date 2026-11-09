import asyncio
import csv
import hashlib
import hmac
import io
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from .db import make_engine, transaction, State, Record, Basket, Order
from .engine import audit, account, execute, active, close_order, update_quote
from .strategy import validate_bars, signal, indicators, structure
from .backtest import research

engine = make_engine()
app = FastAPI(title="EUR/USD Paper Terminal", version="1.0.0")
origin = os.getenv("FRONTEND_ORIGIN", os.getenv("RENDER_EXTERNAL_URL", "http://localhost:3000"))
app.add_middleware(
    CORSMiddleware, allow_origins=[origin], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type"]
)
attempts = defaultdict(deque)


@app.middleware("http")
async def protection(request, call_next):
    if request.method == "POST" and request.headers.get("origin") not in (None, origin):
        return Response("Origin denied", status_code=403)
    if int(request.headers.get("content-length", "0")) > 20_000_000:
        return Response("Body too large", status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def user_from_token(token):
    if not token:
        raise HTTPException(401, "Login required")
    key = hashlib.sha256(token.encode()).hexdigest()
    with transaction(engine) as s:
        r = s.get(Record, key)
        if not r or r.kind != "session" or r.data["expires"] < time.time():
            raise HTTPException(401, "Session expired")
        return r.data["username"]


def auth(request: Request):
    return user_from_token(request.cookies.get("session"))


class Login(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(body: Login, response: Response, request: Request):
    key = request.client.host
    now = time.time()
    q = attempts[key]
    while q and q[0] < now - 60:
        q.popleft()
    if len(q) >= 10:
        raise HTTPException(429, "Too many login attempts")
    q.append(now)
    with transaction(engine) as s:
        r = s.get(Record, "user:" + body.username)
        if not r or not hmac.compare_digest(
            hashlib.scrypt(body.password.encode(), salt=bytes.fromhex(r.data["salt"]), n=16384, r=8, p=1).hex(), r.data["hash"]
        ):
            raise HTTPException(401, "Invalid credentials")
        token = secrets.token_urlsafe(32)
        s.add(
            Record(
                id=hashlib.sha256(token.encode()).hexdigest(),
                kind="session",
                at=now,
                data=dict(username=body.username, expires=now + 28800),
            )
        )
    response.set_cookie(
        "session", token, httponly=True, samesite="strict", secure=os.getenv("COOKIE_SECURE", "false") == "true", max_age=28800
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response, u=Depends(auth)):
    with transaction(engine) as s:
        r = s.get(Record, hashlib.sha256(request.cookies["session"].encode()).hexdigest())
        s.delete(r)
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/health")
def health():
    with transaction(engine) as s:
        ready = s.get(State, 1) is not None
    return dict(status="ok" if ready else "uninitialized", mode="SIMULATED", live_enabled=False)


def snapshot():
    with transaction(engine) as s:
        st = s.get(State, 1).data
        records = list(
            s.scalars(
                select(Record)
                .where(Record.kind.in_(["signal", "risk", "execution", "validation", "basket", "config", "control", "import"]))
                .order_by(Record.at.desc())
                .limit(100)
            )
        )
        bs = list(s.scalars(select(Basket).order_by(Basket.at.desc()).limit(100)))
        os_ = list(s.scalars(select(Order).order_by(Order.at.desc()).limit(1000)))
        market = s.get(Record, "market")
        recent = [b for b in bs if b.at > time.time() - 3600]
        return dict(
            mode="SIMULATED",
            demo_scenarios=os.getenv("ENABLE_DEMO_SCENARIOS") == "true",
            account=account(s, st),
            state=st,
            baskets=[dict(id=b.id, at=b.at, **b.data) for b in bs],
            orders=[dict(id=o.id, basket_id=o.basket_id, at=o.at, **o.data) for o in os_],
            events=[dict(id=r.id, kind=r.kind, at=r.at, data=r.data) for r in records],
            candles=market.data["bars"][-300:] if market else [],
            market=market.data.get("source") if market else None,
            capacity=dict(setups_used=len(recent), orders_used=sum(b.data["requested_orders"] for b in recent)),
        )


@app.get("/api/state")
def state(u=Depends(auth)):
    return snapshot()


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    risk_pct: float = Field(0.5, gt=0, le=0.5)
    daily_loss_pct: float = Field(2, gt=0, le=2)
    drawdown_pct: float = Field(5, gt=0, le=5)
    max_open_risk_pct: float = Field(1, gt=0, le=2)
    max_positions: int = Field(40, ge=1, le=40)
    max_baskets: int = Field(2, ge=1, le=2)
    cooldown_seconds: int = Field(300, ge=0, le=86400)
    max_spread_pips: float = Field(2, gt=0, le=5)
    max_slippage_pips: float = Field(1, ge=0, le=3)
    news_required: bool = True
    enabled: bool = True
    break_even: bool = False
    trailing_atr: float = Field(0, ge=0, le=10)
    max_duration_minutes: int = Field(120, ge=1, le=1440)


@app.post("/api/config")
def config(body: Config, u=Depends(auth)):
    with transaction(engine) as s:
        row = s.get(State, 1)
        before = row.data["config"]
        row.data = {**row.data, "config": body.model_dump()}
        audit(s, "config", dict(user=u, before=before, after=body.model_dump()))
    return {"ok": True}


class Control(BaseModel):
    action: Literal["pause", "resume", "emergency_stop", "connect", "disconnect"]


@app.post("/api/control")
def control(body: Control, u=Depends(auth)):
    with transaction(engine) as s:
        row = s.get(State, 1)
        st = dict(row.data)
        if body.action == "resume":
            if st["stopped"]:
                raise HTTPException(409, "Emergency/loss stop is latched; see runbook")
            st["paused"] = False
        elif body.action == "pause":
            st["paused"] = True
        elif body.action == "emergency_stop":
            st.update(paused=True, stopped=True)
            for o in active(s):
                close_order(s, st, o, "emergency_stop")
        else:
            st["connected"] = body.action == "connect"
        row.data = st
        audit(s, "control", dict(action=body.action, user=u))
    return {"ok": True}


class Import(BaseModel):
    csv: str = Field(max_length=18_000_000)
    source: str = Field(min_length=3, max_length=200)


@app.post("/api/market/import")
def import_market(body: Import, u=Depends(auth)):
    try:
        bars = []
        for row in csv.DictReader(io.StringIO(body.csv)):
            at = row["time"]
            if not at.replace(".", "", 1).isdigit():
                dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    raise ValueError("Timestamps must include UTC offset")
                at = dt.timestamp()
            bars.append({k: float(row[k]) for k in ("open", "high", "low", "close")} | {"time": float(at)})
        gaps = validate_bars(bars)
        if len(bars) > 100000:
            raise ValueError("Maximum 100,000 candles per import")
        if bars[-1]["time"] + 60 > time.time():
            raise ValueError("Only completed candles allowed")
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    with transaction(engine) as s:
        current = s.get(Record, "market")
        data = dict(bars=bars, source=body.source, gaps=gaps)
        if current:
            current.data = data
        else:
            s.add(Record(id="market", kind="market", at=time.time(), data=data))
        audit(s, "import", dict(source=body.source, bars=len(bars), missing_minutes=gaps))
    return dict(bars=len(bars), missing_minutes=gaps)


@app.post("/api/scan")
def scan(u=Depends(auth)):
    with transaction(engine) as s:
        market = s.get(Record, "market")
        if not market:
            raise HTTPException(409, "Import real M1 CSV first")
        bars = market.data["bars"]
        sig = signal(bars[-6000:])
        if sig and not s.get(Record, sig["id"]):
            s.add(Record(id=sig["id"], kind="signal", at=sig["timestamp"], data=sig))
    return dict(
        signal=sig,
        indicators=indicators(bars) if len(bars) >= 200 else None,
        structure=structure(bars) if len(bars) >= 25 else None,
        message="No qualifying setup / insufficient complete timeframes" if not sig else "Setup found",
    )


class Quote(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    time: float


@app.post("/api/quote")
def quote(body: Quote, u=Depends(auth)):
    if body.ask < body.bid or abs(time.time() - body.time) > 120:
        raise HTTPException(422, "Invalid or stale quote")
    with transaction(engine) as s:
        row = s.get(State, 1)
        st = dict(row.data)
        if st["quote"] and body.time <= st["quote"]["time"]:
            raise HTTPException(409, "Duplicate/out-of-order quote")
        update_quote(s, st, body.model_dump())
        row.data = st
    return {"ok": True}


class Execute(BaseModel):
    signal_id: str
    count: int = Field(1, ge=1, le=20)


@app.post("/api/execute")
def submit(body: Execute, u=Depends(auth)):
    try:
        return execute(engine, body.signal_id, body.count)
    except ValueError as e:
        raise HTTPException(422, str(e))


class Close(BaseModel):
    order_id: str
    fraction: float = Field(1, gt=0, le=1)


@app.post("/api/close")
def close(body: Close, u=Depends(auth)):
    with transaction(engine) as s:
        o = s.get(Order, body.order_id)
        row = s.get(State, 1)
        st = dict(row.data)
        if not o or o.data["status"] != "FILLED":
            raise HTTPException(409, "No open position")
        try:
            close_order(s, st, o, "manual", body.fraction)
        except ValueError as e:
            raise HTTPException(422, str(e))
        row.data = st
    return {"ok": True}


class Event(BaseModel):
    time: float
    currency: Literal["EUR", "USD"]
    impact: Literal["low", "medium", "high"]
    title: str


class News(BaseModel):
    source: str = Field(min_length=3)
    coverage_start: float
    coverage_end: float
    events: list[Event]


@app.post("/api/news")
def news(body: News, u=Depends(auth)):
    if body.coverage_start >= body.coverage_end:
        raise HTTPException(422, "Invalid coverage interval")
    with transaction(engine) as s:
        row = s.get(State, 1)
        row.data = {**row.data, "news": body.model_dump() | {"verified_at": time.time()}}
        audit(s, "config", dict(user=u, news_source=body.source))
    return {"ok": True}


@app.post("/api/backtest")
def backtest(u=Depends(auth)):
    with transaction(engine) as s:
        m = s.get(Record, "market")
        if not m or len(m.data["bars"]) < 5000:
            raise HTTPException(409, "At least 5000 M1 candles required")
        bars = m.data["bars"]
        source = m.data["source"]
    result = research(bars)
    result["source"] = source
    with transaction(engine) as s:
        audit(s, "backtest", result)
    return result


@app.get("/api/backtests")
def backtests(u=Depends(auth)):
    with transaction(engine) as s:
        return [
            dict(id=r.id, at=r.at, **r.data)
            for r in s.scalars(select(Record).where(Record.kind == "backtest").order_by(Record.at.desc()).limit(10))
        ]


@app.get("/api/commentary")
def commentary(u=Depends(auth)):
    snap = snapshot()
    return dict(
        provider="deterministic-local",
        text=f"Paper account equity: ${snap['account']['equity']:.2f}. Open positions: {snap['account']['positions']}. Engine {'paused' if snap['state']['paused'] else 'enabled'}. No LLM provider configured. Commentary cannot submit orders.",
    )


@app.websocket("/api/ws")
async def websocket(ws: WebSocket):
    if ws.headers.get("origin") != origin:
        await ws.close(code=1008)
        return
    try:
        user_from_token(ws.cookies.get("session"))
    except HTTPException:
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        while True:
            user_from_token(ws.cookies.get("session"))
            await ws.send_json(await asyncio.to_thread(snapshot))
            try:
                message = await asyncio.wait_for(ws.receive(), timeout=2)
                if message["type"] == "websocket.disconnect":
                    return
            except asyncio.TimeoutError:
                pass
    except Exception:
        await ws.close()


@app.post("/api/cycle")
def cycle(u=Depends(auth)):
    """Data adapters call after publishing a fresh quote and completed M1 history."""
    result = scan(u)
    if not result["signal"]:
        return result
    return execute(engine, result["signal"]["id"], 1)


class CloseBasket(BaseModel):
    basket_id: str


@app.post("/api/basket/close")
def close_basket(body: CloseBasket, u=Depends(auth)):
    with transaction(engine) as s:
        row = s.get(State, 1)
        st = dict(row.data)
        positions = [o for o in active(s) if o.basket_id == body.basket_id]
        if not positions:
            raise HTTPException(409, "No open positions in this basket")
        for order in positions:
            close_order(s, st, order, "basket_close")
        row.data = st
        audit(s, "control", {"user": u, "basket_id": body.basket_id, "action": "close_basket"})
    return {"closed": len(positions)}


class DemoScenario(BaseModel):
    direction: Literal["BUY", "SELL"]


@app.post("/api/demo/scenario")
def demo_scenario(body: DemoScenario, u=Depends(auth)):
    """Explicit synthetic test fixture, never a strategy recommendation."""
    if os.getenv("ENABLE_DEMO_SCENARIOS") != "true":
        raise HTTPException(404, "Demo scenarios disabled")
    import math

    now = time.time()
    with transaction(engine) as s:
        row = s.get(State, 1)
        st = dict(row.data)
        if active(s):
            raise HTTPException(409, "Close existing paper positions before starting another test")
        if st["stopped"]:
            raise HTTPException(409, "Emergency stop is latched")
        last_minute = int(now // 60) * 60 - 60
        bars = []
        for i in range(6000):
            opening = 1.1 + 0.001 * math.sin(i / 120) + 0.0003 * math.sin(i / 9)
            closing = 1.1 + 0.001 * math.sin((i + 1) / 120) + 0.0003 * math.sin((i + 1) / 9)
            bars.append(
                dict(
                    time=last_minute - (5999 - i) * 60,
                    open=opening,
                    close=closing,
                    high=max(opening, closing) + 0.0001,
                    low=min(opening, closing) - 0.0001,
                )
            )
        price = bars[-1]["close"]
        data = dict(bars=bars, source="SYNTHETIC TEST SCENARIO — NOT REAL MARKET DATA", gaps=0)
        current = s.get(Record, "market")
        if current:
            current.data = data
        else:
            s.add(Record(id="market", kind="market", at=now, data=data))
        st["quote"] = dict(bid=price, ask=price + 0.0001, time=now)
        st["paused"] = False
        st["config"] = {**st["config"], "news_required": False}
        row.data = st
        side = 1 if body.direction == "BUY" else -1
        entry = price + 0.0001 if side == 1 else price
        signal_id = "synthetic-" + secrets.token_hex(16)
        sig = dict(
            id=signal_id,
            symbol="EURUSD",
            direction=body.direction,
            timestamp=now,
            entry=entry,
            sl=entry - side * 0.001,
            tp=entry + side * 0.002,
            rr=2,
            strategy="SYNTHETIC TEST FIXTURE",
            confidence=0,
            contributions={},
            regime="normal",
            explanation="User-requested synthetic execution test. Not a market signal.",
        )
        s.add(Record(id=signal_id, kind="signal", at=now, data=sig))
        audit(s, "control", dict(user=u, action="synthetic_test", news_filter_disabled_for_simulation=True))
    return execute(engine, signal_id, 1, now)


# Optional same-origin static frontend for the single-container hosted demo.
if os.getenv("SERVE_FRONTEND_DIR"):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=os.environ["SERVE_FRONTEND_DIR"], html=True), name="terminal")
