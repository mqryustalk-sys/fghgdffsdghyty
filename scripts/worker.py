"""Optional paper execution loop; data provider must publish fresh candles/quotes."""

import os
import time
import httpx

url = os.getenv("TERMINAL_URL", "http://localhost:8000")
with httpx.Client(base_url=url, timeout=60) as client:
    r = client.post("/api/login", json={"username": os.environ["TERMINAL_USER"], "password": os.environ["TERMINAL_PASSWORD"]})
    r.raise_for_status()
    while True:
        try:
            r = client.post("/api/cycle", json={})
            print(r.status_code, r.text, flush=True)
            if r.status_code == 401:
                raise SystemExit("Session expired; restart worker")
        except httpx.HTTPError as e:
            print("Transport error:", e, flush=True)
        time.sleep(60)
