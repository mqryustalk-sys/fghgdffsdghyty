import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.db import Base, make_engine, State, Record, Order, transaction
from app.engine import initialize, execute, sizing, update_quote, account
from app.strategy import ema, validate_bars, resample, signal

NOW = 1794225600.0  # Monday November 9 2026 12:00 UTC


@pytest.fixture
def engine(tmp_path):
    e = make_engine("sqlite:///" + str(tmp_path / "test.db"))
    Base.metadata.create_all(e)
    initialize(e)
    with transaction(e) as s:
        row = s.get(State, 1)
        row.data = {
            **row.data,
            "paused": False,
            "quote": dict(bid=1.1, ask=1.1001, time=NOW),
            "config": {**row.data["config"], "news_required": False, "cooldown_seconds": 0},
        }
        for i in range(4):
            s.add(
                Record(
                    id=f"sig{i}",
                    kind="signal",
                    at=NOW,
                    data=dict(
                        id=f"sig{i}",
                        symbol="EURUSD",
                        direction="BUY",
                        timestamp=NOW,
                        entry=1.1,
                        sl=1.099,
                        tp=1.103,
                        regime="normal",
                    ),
                )
            )
    return e


def test_sizing_is_aggregate():
    lot = sizing(50, 0.001, 20)
    assert lot == 0.02
    assert lot * 20 * (100 + 7) <= 50


def test_twenty_split_idempotency_and_hourly(engine):
    r = execute(engine, "sig0", 20, NOW)
    assert r["filled_orders"] == 20
    assert execute(engine, "sig0", 20, NOW)["replayed"]
    assert execute(engine, "sig1", 20, NOW)["filled_orders"] == 20
    r = execute(engine, "sig2", 1, NOW)
    assert not r["approved"]
    assert "2 setups/hour limit" in r["reasons"]
    with transaction(engine) as s:
        assert len(list(s.scalars(select(Order)))) == 40


def test_concurrent_reservation(engine):
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: execute(engine, "sig0", 20, NOW), range(8)))
    assert sum(not r.get("replayed", False) for r in results) == 1


def test_partial_rejection_disconnect(engine):
    assert execute(engine, "sig0", 4, NOW, fault="partial")["filled_orders"] == 2
    assert execute(engine, "sig1", 1, NOW, fault="disconnect")["filled_orders"] == 0


def test_news_fails_closed(engine):
    with transaction(engine) as s:
        row = s.get(State, 1)
        row.data = {**row.data, "config": {**row.data["config"], "news_required": True}}
    assert "News coverage unverified" in execute(engine, "sig0", 1, NOW)["reasons"]


def test_stale_and_spread(engine):
    assert "Missing/stale quote" in execute(engine, "sig0", 1, NOW + 180)["reasons"]
    with transaction(engine) as s:
        row = s.get(State, 1)
        row.data = {**row.data, "quote": dict(bid=1.1, ask=1.101, time=NOW)}
    assert "Spread limit" in execute(engine, "sig0", 1, NOW)["reasons"]


def test_stop_and_persistence(engine):
    execute(engine, "sig0", 1, NOW)
    with transaction(engine) as s:
        row = s.get(State, 1)
        st = dict(row.data)
        update_quote(s, st, dict(bid=1.098, ask=1.0981, time=NOW + 60))
        row.data = st
    other = make_engine(str(engine.url))
    with transaction(other) as s:
        assert s.scalar(select(Order)).data["status"] == "CLOSED"
        assert s.get(State, 1).data["balance"] < 10000


def test_loss_latches(engine):
    with transaction(engine) as s:
        row = s.get(State, 1)
        st = dict(row.data)
        st["balance"] = 9400
        update_quote(s, st, dict(bid=1.1, ask=1.1001, time=NOW))
        row.data = st
    assert "Drawdown limit" in execute(engine, "sig0", 1, NOW)["reasons"]


def test_indicators_and_data_validation():
    assert ema([1, 2, 3], 3) == [1, 1.5, 2.25]
    bars = [dict(time=i * 60, open=1, high=2, low=0.5, close=1.5) for i in range(20)]
    assert validate_bars(bars) == 0
    assert len(resample(bars[:19], 5)) == 3
    assert signal(bars) is None
    with pytest.raises(ValueError):
        validate_bars([bars[0], bars[0]])
    with pytest.raises(ValueError):
        validate_bars([{**bars[0], "close": float("nan")}])


def test_api_auth_config_websocket_emergency(engine, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "engine", engine)
    salt = b"test-salt"
    with transaction(engine) as s:
        s.add(
            Record(
                id="user:tester",
                kind="user",
                at=time.time(),
                data=dict(salt=salt.hex(), hash=hashlib.scrypt(b"long-test-password", salt=salt, n=16384, r=8, p=1).hex()),
            )
        )
    client = TestClient(main.app)
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/login", json=dict(username="tester", password="wrong")).status_code == 401
    assert client.post("/api/login", json=dict(username="tester", password="long-test-password")).status_code == 200
    assert client.get("/api/state").json()["mode"] == "SIMULATED"
    assert client.post("/api/config", json=dict(risk_pct=50)).status_code == 422
    with client.websocket_connect("/api/ws", headers={"origin": "http://localhost:3000"}) as ws:
        assert ws.receive_json()["mode"] == "SIMULATED"
    execute(engine, "sig0", 1, NOW)
    assert client.post("/api/control", json=dict(action="emergency_stop")).status_code == 200
    assert client.get("/api/state").json()["account"]["positions"] == 0
    assert client.post("/api/control", json=dict(action="resume")).status_code == 409
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/state").status_code == 401


def test_sell(engine):
    with transaction(engine) as s:
        r = s.get(Record, "sig0")
        r.data = {**r.data, "direction": "SELL", "sl": 1.101, "tp": 1.098}
    assert execute(engine, "sig0", 1, NOW)["filled_orders"] == 1
    with transaction(engine) as s:
        row = s.get(State, 1)
        st = dict(row.data)
        update_quote(s, st, dict(bid=1.0978, ask=1.0979, time=NOW + 60))
        assert account(s, st)["balance"] > 10000


def test_rejected_attempts_reserve_capacity(engine):
    execute(engine, "sig0", 20, NOW, fault="reject")
    execute(engine, "sig1", 20, NOW, fault="reject")
    assert "40 orders/hour limit" in execute(engine, "sig2", 1, NOW)["reasons"]


def test_backtest_adverse_ambiguous_fill(monkeypatch):
    from app import backtest

    bars = [dict(time=NOW + i * 60, open=1.1, high=1.101, low=1.099, close=1.1) for i in range(4)]

    def sig(_):
        return dict(direction="BUY", sl=1.0995, tp=1.1008)

    monkeypatch.setattr(backtest, "signal", sig)
    r = backtest.run(bars)
    assert r["orders"] > 0
    assert all(t["pnl"] < 0 for t in r["trades"])
    assert r["commissions"] > 0
    assert r["max_drawdown_pct"] > 0


def test_confirmed_timeframe_no_future():
    bars = [dict(time=i * 60, open=1, high=2, low=0.5, close=1.5) for i in range(10)]
    before = resample(bars[:9], 5)
    bars[9]["close"] = 1.9
    assert before == resample(bars[:5], 5)
    assert len(resample(bars, 5)) == 2


def test_rollover_and_concurrent_distinct_signals(engine):
    with ThreadPoolExecutor(4) as pool:
        r = list(pool.map(lambda i: execute(engine, f"sig{i}", 20, NOW), range(4)))
    assert sum(x.get("filled_orders", 0) for x in r) <= 40


def test_cooldown(engine):
    with transaction(engine) as s:
        row = s.get(State, 1)
        row.data = {**row.data, "config": {**row.data["config"], "cooldown_seconds": 300}}
    execute(engine, "sig0", 1, NOW)
    assert "Cooldown" in execute(engine, "sig1", 1, NOW)["reasons"]


def test_trailing_stop_only_tightens(engine):
    with transaction(engine) as s:
        row = s.get(State, 1)
        row.data = {**row.data, "config": {**row.data["config"], "trailing_atr": 1.0}}
    execute(engine, "sig0", 1, NOW)
    with transaction(engine) as s:
        row = s.get(State, 1)
        st = dict(row.data)
        update_quote(s, st, dict(bid=1.1015, ask=1.1016, time=NOW + 60))
        stop = s.scalar(select(Order)).data["sl"]
        assert stop > 1.099
        update_quote(s, st, dict(bid=1.1014, ask=1.1015, time=NOW + 90))
        assert s.scalar(select(Order)).data["sl"] >= stop
