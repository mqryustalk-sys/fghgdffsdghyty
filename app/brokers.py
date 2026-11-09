"""Read-only broker integration boundary; live orders are intentionally not exposed."""

from typing import Protocol
import httpx


class BrokerReader(Protocol):
    def health(self) -> dict: ...
    def positions(self) -> list[dict]: ...
    def metadata(self) -> dict: ...


class MetaTrader5Gateway:
    """Server-side HTTPS demo gateway reader. Never instantiate in browser code."""

    def __init__(self, url: str, token: str):
        if not url.startswith("https://"):
            raise ValueError("Remote MT5 gateway requires HTTPS")
        self.client = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=10)

    def health(self) -> dict:
        response = self.client.get("/health")
        response.raise_for_status()
        return response.json()

    def positions(self) -> list[dict]:
        response = self.client.get("/positions")
        response.raise_for_status()
        return response.json()

    def metadata(self) -> dict:
        response = self.client.get("/symbol")
        response.raise_for_status()
        return response.json()

    def close(self):
        self.client.close()
