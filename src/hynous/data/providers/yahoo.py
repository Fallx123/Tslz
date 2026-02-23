"""
Yahoo Finance Data Provider (Stocks)

Lightweight wrapper around yfinance for US equities market data.
Used for price snapshots and historical candles.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)


class YahooProvider:
    """Synchronous Yahoo Finance market data provider."""

    def get_price(self, symbol: str) -> Optional[float]:
        """Get latest price for a symbol."""
        symbol = symbol.upper()
        try:
            ticker = yf.Ticker(symbol)
            info = getattr(ticker, "fast_info", {}) or {}
            price = info.get("last_price") or info.get("lastPrice")
            if price:
                return float(price)

            hist = ticker.history(period="1d", interval="1m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.debug("Yahoo price fetch failed for %s: %s", symbol, e)
        return None

    def get_prev_close(self, symbol: str) -> Optional[float]:
        """Get previous close price."""
        symbol = symbol.upper()
        try:
            ticker = yf.Ticker(symbol)
            info = getattr(ticker, "fast_info", {}) or {}
            prev = info.get("previous_close") or info.get("previousClose")
            if prev:
                return float(prev)

            hist = ticker.history(period="2d", interval="1d")
            if len(hist) >= 2:
                return float(hist["Close"].iloc[-2])
        except Exception as e:
            logger.debug("Yahoo prev close fetch failed for %s: %s", symbol, e)
        return None

    def get_volume(self, symbol: str) -> Optional[float]:
        """Get most recent daily volume."""
        symbol = symbol.upper()
        try:
            hist = yf.Ticker(symbol).history(period="1d", interval="1d")
            if not hist.empty:
                return float(hist["Volume"].iloc[-1])
        except Exception as e:
            logger.debug("Yahoo volume fetch failed for %s: %s", symbol, e)
        return None

    def get_candles(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict]:
        """Get OHLCV candles.

        Returns list of dicts with keys: t, o, h, l, c, v
        """
        symbol = symbol.upper()
        start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

        yf_interval = _map_interval(interval)
        # Avoid noisy "possibly delisted" logs for intraday requests after-hours.
        if interval in {"1m", "2m", "5m", "15m", "30m", "1h", "4h"}:
            try:
                from zoneinfo import ZoneInfo
                now_et = datetime.now(ZoneInfo("America/New_York"))
                is_weekend = now_et.weekday() >= 5
                is_open = (now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 30)) and now_et.hour < 16
                if is_weekend or not is_open:
                    yf_interval = "1d"
                    # Use a wider daily window to keep charts populated.
                    start_dt = now_et - datetime.timedelta(days=30)
                    end_dt = now_et
            except Exception:
                pass
        try:
            df = yf.download(
                symbol,
                start=start_dt,
                end=end_dt,
                interval=yf_interval,
                progress=False,
                auto_adjust=False,
            )
        except Exception as e:
            logger.debug("Yahoo candle fetch failed for %s: %s", symbol, e)
            return []

        if df is None or df.empty:
            return []

        # Newer yfinance returns MultiIndex columns (column, ticker) for downloads.
        # Flatten to single-level if needed.
        if isinstance(df.columns, __import__("pandas").MultiIndex):
            df.columns = df.columns.get_level_values(0)

        candles: list[dict] = []
        for idx, row in df.iterrows():
            ts = int(idx.to_pydatetime().replace(tzinfo=timezone.utc).timestamp() * 1000)
            candles.append({
                "t": ts,
                "o": float(row["Open"]),
                "h": float(row["High"]),
                "l": float(row["Low"]),
                "c": float(row["Close"]),
                "v": float(row.get("Volume", 0) or 0),
            })
        return candles


def _map_interval(interval: str) -> str:
    """Map internal interval to yfinance interval."""
    mapping = {
        "1m": "1m",
        "2m": "2m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "60m",
        "4h": "60m",
        "1d": "1d",
    }
    return mapping.get(interval, "1d")
