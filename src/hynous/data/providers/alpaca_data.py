"""
Alpaca Market Data Provider (Equities)

Uses Alpaca's stock data REST API for latest quotes/trades and bars.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame

from ...core.config import load_config

logger = logging.getLogger(__name__)


class AlpacaDataProvider:
    """Synchronous Alpaca market data provider."""

    def __init__(self):
        cfg = load_config()
        api_key = cfg.alpaca_api_key
        api_secret = cfg.alpaca_api_secret
        self._feed = getattr(cfg.alpaca, "data_feed", "iex")
        self._client = StockHistoricalDataClient(api_key, api_secret)

    def get_price(self, symbol: str) -> Optional[float]:
        """Latest quote midpoint if available, else last close."""
        symbol = symbol.upper()
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=self._feed)
            res = self._client.get_stock_latest_quote(req)
            quote = res.get(symbol)
            if quote and quote.bid_price is not None and quote.ask_price is not None:
                return float((quote.bid_price + quote.ask_price) / 2)
        except Exception as e:
            logger.debug("Alpaca latest quote failed for %s: %s", symbol, e)

        # Fallback to latest bar
        try:
            bars = self.get_candles(symbol, "1d", _days_ago_ms(5), _now_ms())
            if bars:
                return float(bars[-1]["c"])
        except Exception:
            pass
        return None

    def get_prev_close(self, symbol: str) -> Optional[float]:
        """Previous daily close from recent bars."""
        symbol = symbol.upper()
        try:
            bars = self.get_candles(symbol, "1d", _days_ago_ms(10), _now_ms())
            if len(bars) >= 2:
                return float(bars[-2]["c"])
        except Exception as e:
            logger.debug("Alpaca prev close failed for %s: %s", symbol, e)
        return None

    def get_volume(self, symbol: str) -> Optional[float]:
        """Latest daily volume."""
        symbol = symbol.upper()
        try:
            bars = self.get_candles(symbol, "1d", _days_ago_ms(5), _now_ms())
            if bars:
                return float(bars[-1]["v"])
        except Exception:
            pass
        return None

    def get_candles(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict]:
        """Get OHLCV candles. Returns list of dicts with t,o,h,l,c,v."""
        symbol = symbol.upper()
        tf = _map_timeframe(interval)
        start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start_dt,
            end=end_dt,
            feed=self._feed,
        )
        try:
            bars = self._client.get_stock_bars(req).data.get(symbol, [])
        except Exception as e:
            logger.debug("Alpaca bars failed for %s: %s", symbol, e)
            return []

        out: list[dict] = []
        for b in bars:
            out.append({
                "t": int(b.timestamp.replace(tzinfo=timezone.utc).timestamp() * 1000),
                "o": float(b.open),
                "h": float(b.high),
                "l": float(b.low),
                "c": float(b.close),
                "v": float(b.volume),
            })
        return out


def _map_timeframe(interval: str) -> TimeFrame:
    mapping = {
        "1m": TimeFrame.Minute,
        "5m": TimeFrame(5, TimeFrame.Unit.Minute),
        "15m": TimeFrame(15, TimeFrame.Unit.Minute),
        "30m": TimeFrame(30, TimeFrame.Unit.Minute),
        "1h": TimeFrame.Hour,
        "4h": TimeFrame(4, TimeFrame.Unit.Hour),
        "1d": TimeFrame.Day,
    }
    return mapping.get(interval, TimeFrame.Day)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _days_ago_ms(days: int) -> int:
    return int((datetime.now(timezone.utc).timestamp() - days * 86400) * 1000)
