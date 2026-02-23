"""
Finnhub Data Provider (Stocks)

REST wrapper for the Finnhub API v1.
Provides real-time quotes, OHLCV candles, news, and market status for US equities.

Key decisions:
- Singleton pattern via get_provider() (same as other providers)
- API key loaded from FINNHUB_API_KEY env var
- Finnhub candle timestamps are in seconds; we accept/return milliseconds externally
- Candle resolutions: 1, 5, 15, 30, 60 (minutes), D, W, M
- 60-call/minute rate limit on free tier — sufficient for our usage
"""

import os
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_provider: Optional["FinnhubProvider"] = None


def get_provider() -> "FinnhubProvider":
    """Get or create the singleton FinnhubProvider."""
    global _provider
    if _provider is None:
        api_key = os.environ.get("FINNHUB_API_KEY", "")
        if not api_key:
            raise ValueError("FINNHUB_API_KEY not set. Add it to your .env file.")
        _provider = FinnhubProvider(api_key)
    return _provider


class FinnhubProvider:
    """Synchronous Finnhub API v1 client for US equities."""

    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str):
        self._session = requests.Session()
        self._session.headers["X-Finnhub-Token"] = api_key
        logger.info("FinnhubProvider initialized")

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{self.BASE_URL}{path}"
        resp = self._session.get(url, params=params or {}, timeout=10)
        resp.raise_for_status()
        return resp.json()

    # ================================================================
    # Quote & Snapshot
    # ================================================================

    def get_quote(self, symbol: str) -> dict | None:
        """Full real-time quote for a symbol.

        Returns dict with keys:
            c: current price, pc: prev close, o: open,
            h: day high, l: day low, d: change, dp: change_pct, t: timestamp
        Returns None if symbol not found.
        """
        symbol = symbol.upper()
        try:
            data = self._get("/quote", {"symbol": symbol})
            if not data or data.get("c", 0) == 0:
                return None
            return data
        except Exception as e:
            logger.error("Finnhub quote error for %s: %s", symbol, e)
            return None

    def get_price(self, symbol: str) -> float | None:
        """Get current price for a symbol."""
        q = self.get_quote(symbol)
        return float(q["c"]) if q else None

    def get_prev_close(self, symbol: str) -> float | None:
        """Get previous close price."""
        q = self.get_quote(symbol)
        return float(q["pc"]) if q else None

    def get_volume(self, symbol: str) -> float | None:
        """Get most recent daily volume via Yahoo Finance fallback."""
        from .yahoo import YahooProvider
        return YahooProvider().get_volume(symbol)

    # ================================================================
    # Candles
    # ================================================================

    def get_candles(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict]:
        """Get OHLCV candles via Finnhub, fallback to Yahoo.

        Returns list of dicts with keys: t (ms), o, h, l, c, v (all float).
        """
        symbol = symbol.upper()
        try:
            res = _map_interval(interval)
            start_sec = int(start_ms / 1000)
            end_sec = int(end_ms / 1000)
            data = self._get("/stock/candle", {
                "symbol": symbol,
                "resolution": res,
                "from": start_sec,
                "to": end_sec,
            })
            if data and data.get("s") == "ok":
                t = data.get("t", [])
                o = data.get("o", [])
                h = data.get("h", [])
                l = data.get("l", [])
                c = data.get("c", [])
                v = data.get("v", [])
                candles: list[dict] = []
                for i in range(len(t)):
                    candles.append({
                        "t": int(t[i]) * 1000,
                        "o": float(o[i]),
                        "h": float(h[i]),
                        "l": float(l[i]),
                        "c": float(c[i]),
                        "v": float(v[i]),
                    })
                return candles
        except Exception as e:
            logger.debug("Finnhub candle fetch failed for %s: %s", symbol, e)

        from .yahoo import YahooProvider
        return YahooProvider().get_candles(symbol, interval, start_ms, end_ms)

    # ================================================================
    # News
    # ================================================================

    def get_news(self, symbol: str, days: int = 7) -> list[dict]:
        """Get recent company news articles.

        Returns list of dicts with keys:
            headline, summary, source, url, datetime (unix), category
        """
        symbol = symbol.upper()
        import datetime
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)

        try:
            articles = self._get("/company-news", {
                "symbol": symbol,
                "from": start.isoformat(),
                "to": end.isoformat(),
            })
        except Exception as e:
            logger.error("Finnhub news error for %s: %s", symbol, e)
            return []

        result = []
        for a in (articles or [])[:50]:
            result.append({
                "headline": a.get("headline", ""),
                "summary": (a.get("summary", "") or "")[:200],
                "source": a.get("source", ""),
                "url": a.get("url", ""),
                "datetime": a.get("datetime", 0),
                "category": a.get("category", ""),
            })
        return result

    # ================================================================
    # Market Status
    # ================================================================

    def is_market_open(self) -> bool:
        """Returns True if the US stock market is currently open."""
        try:
            data = self._get("/stock/market-status", {"exchange": "US"})
            return bool(data.get("isOpen", False))
        except Exception as e:
            logger.debug("Finnhub market status error: %s", e)
            return False


def _map_interval(interval: str) -> str:
    """Map internal interval string to Finnhub resolution."""
    mapping = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "4h": "60",   # Finnhub has no 4h; use 60m
        "1d": "D",
        "1w": "W",
    }
    return mapping.get(interval, "D")
