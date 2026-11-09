"""Run only on Windows with MetaTrader5 installed. Demo accounts only."""

import os
import sqlite3
import json
import threading
from datetime import datetime, timezone
import secrets
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field
import MetaTrader5 as mt5

app = FastAPI(title="MT5 demo gateway")
lock = threading.Lock()
db = sqlite3.connect("gateway.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, result TEXT NOT NULL)")
db.commit()


def auth(authorization: str = Header("")):
    key = os.environ.get("GATEWAY_TOKEN", "")
    if len(key) < 32 or not secrets.compare_digest(authorization, "Bearer " + key):
        raise HTTPException(401, "Invalid token")
    if not mt5.initialize():
        raise HTTPException(503, str(mt5.last_error()))
    info = mt5.account_info()
    if not info or info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise HTTPException(403, "Gateway permits DEMO accounts only")
    return info


@app.get("/health")
def health(a=Depends(auth)):
    return dict(connected=True, mode="DEMO", account=a._asdict())


@app.get("/symbol")
def symbol(a=Depends(auth)):
    name = os.getenv("MT5_SYMBOL", "EURUSD")
    if not mt5.symbol_select(name, True):
        raise HTTPException(503, "Symbol unavailable")
    info = mt5.symbol_info(name)
    tick = mt5.symbol_info_tick(name)
    return dict(metadata=info._asdict(), tick=tick._asdict() if tick else None)


@app.get("/positions")
def positions(a=Depends(auth)):
    result = mt5.positions_get(symbol=os.getenv("MT5_SYMBOL", "EURUSD"))
    if result is None:
        raise HTTPException(503, str(mt5.last_error()))
    return [x._asdict() for x in result]


@app.get("/history")
def history(a=Depends(auth)):
    result = mt5.history_deals_get(datetime(2020, 1, 1, tzinfo=timezone.utc), datetime.now(timezone.utc))
    if result is None:
        raise HTTPException(503, str(mt5.last_error()))
    return [x._asdict() for x in result]


@app.get("/candles")
def candles(count: int = 1000, a=Depends(auth)):
    if not 1 <= count <= 10000:
        raise HTTPException(422, "Count out of range")
    rates = mt5.copy_rates_from_pos(os.getenv("MT5_SYMBOL", "EURUSD"), mt5.TIMEFRAME_M1, 1, count)
    if rates is None:
        raise HTTPException(503, str(mt5.last_error()))
    return [{k: float(row[k]) for k in rates.dtype.names} for row in rates]


class OrderRequest(BaseModel):
    key: str = Field(min_length=10, max_length=100)
    side: str
    lots: float = Field(gt=0, le=1)
    sl: float = Field(gt=0)
    tp: float = Field(gt=0)
    position: int | None = None


@app.post("/orders")
def order(body: OrderRequest, a=Depends(auth)):
    if os.environ.get("MT5_DEMO_EXECUTION") != "enabled":
        raise HTTPException(403, "Demo execution disabled")
    if body.side not in ("BUY", "SELL"):
        raise HTTPException(422, "Invalid side")
    with lock:
        prior = db.execute("SELECT result FROM requests WHERE key=?", (body.key,)).fetchone()
        if prior:
            return json.loads(prior[0])
        symbol = os.getenv("MT5_SYMBOL", "EURUSD")
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        if not tick or not info:
            raise HTTPException(503, "No quote")
        if datetime.now(timezone.utc).timestamp() - tick.time > 30:
            raise HTTPException(409, "Stale tick")
        if (
            body.lots < info.volume_min
            or body.lots > info.volume_max
            or abs(body.lots / info.volume_step - round(body.lots / info.volume_step)) > 1e-6
        ):
            raise HTTPException(422, "Invalid volume")
        request = dict(
            action=mt5.TRADE_ACTION_DEAL,
            symbol=symbol,
            volume=body.lots,
            type=mt5.ORDER_TYPE_BUY if body.side == "BUY" else mt5.ORDER_TYPE_SELL,
            price=tick.ask if body.side == "BUY" else tick.bid,
            sl=body.sl,
            tp=body.tp,
            deviation=10,
            magic=741228,
            comment=body.key[:31],
            type_time=mt5.ORDER_TIME_GTC,
            type_filling=mt5.ORDER_FILLING_IOC,
        )
        if body.position:
            request["position"] = body.position
        check = mt5.order_check(request)
        if check is None or check.retcode != 0:
            raise HTTPException(409, str(check))
        # Persist uncertain state BEFORE network call. Never automatically resend.
        db.execute("INSERT INTO requests VALUES (?,?)", (body.key, json.dumps(dict(status="UNKNOWN", key=body.key))))
        db.commit()
        result = mt5.order_send(request)
        output = (
            dict(status="UNKNOWN", error=str(mt5.last_error()))
            if result is None
            else dict(
                retcode=result.retcode,
                order=result.order,
                deal=result.deal,
                volume=result.volume,
                price=result.price,
                comment=result.comment,
            )
        )
        db.execute("UPDATE requests SET result=? WHERE key=?", (json.dumps(output), body.key))
        db.commit()
        return output


class Modify(BaseModel):
    position: int
    sl: float = Field(gt=0)
    tp: float = Field(gt=0)


@app.post("/modify")
def modify(body: Modify, a=Depends(auth)):
    if os.environ.get("MT5_DEMO_EXECUTION") != "enabled":
        raise HTTPException(403, "Demo execution disabled")
    result = mt5.order_send(
        dict(
            action=mt5.TRADE_ACTION_SLTP, position=body.position, sl=body.sl, tp=body.tp, symbol=os.getenv("MT5_SYMBOL", "EURUSD")
        )
    )
    if result is None:
        raise HTTPException(503, str(mt5.last_error()))
    return dict(retcode=result.retcode, comment=result.comment)
