"""
Data Providers - External market data sources.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .alpaca import AlpacaProvider
from .alpaca_data import AlpacaDataProvider
from .yahoo import YahooProvider
from .finnhub import FinnhubProvider

__all__ = ["AlpacaProvider", "AlpacaDataProvider", "YahooProvider", "FinnhubProvider"]

logger = logging.getLogger(__name__)

def _is_market_open() -> bool:
    """US equities regular session hours (09:30–16:00 ET, weekdays)."""
    try:
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5:
            return False
        if now.hour < 9 or (now.hour == 9 and now.minute < 30):
            return False
        if now.hour >= 16:
            return False
        return True
    except Exception:
        return True


class SafeMarketProvider:
    """Wraps a provider and falls back on suspicious prices when market is closed."""

    def __init__(self, primary, fallback=None, max_gap_pct: float = 0.30):
        self._primary = primary
        self._fallback = fallback or YahooProvider()
        self._max_gap_pct = max_gap_pct

    def get_price(self, symbol: str):
        p = None
        try:
            p = self._primary.get_price(symbol)
        except Exception:
            p = None
        fb = None
        try:
            fb = self._fallback.get_price(symbol)
        except Exception:
            fb = None
        if p is None:
            return fb

        # Cross-check against fallback to catch stale/split/glitch prints.
        if fb and fb > 0:
            gap_fb = abs(p - fb) / fb
            if gap_fb >= self._max_gap_pct:
                logger.warning(
                    "SafeMarketProvider: primary/fallback gap %.1f%% for %s (primary %.2f vs fallback %.2f) — using fallback",
                    gap_fb * 100, symbol, p, fb
                )
                return fb

        # If market is closed and gap is extreme vs prev close, fallback.
        if not _is_market_open():
            prev = None
            try:
                prev = self._primary.get_prev_close(symbol)
            except Exception:
                prev = None
            if prev is None:
                try:
                    prev = self._fallback.get_prev_close(symbol)
                except Exception:
                    prev = None
            if prev and prev > 0:
                gap = abs(p - prev) / prev
                if gap >= self._max_gap_pct:
                    if fb:
                        logger.warning(
                            "SafeMarketProvider: suspicious gap %.1f%% for %s (%.2f vs prev %.2f) — using fallback %.2f",
                            gap * 100, symbol, p, prev, fb
                        )
                        return fb
        return p

    def get_prev_close(self, symbol: str):
        try:
            return self._primary.get_prev_close(symbol)
        except Exception:
            return self._fallback.get_prev_close(symbol)

    def get_volume(self, symbol: str):
        try:
            v = self._primary.get_volume(symbol)
            return v if v else self._fallback.get_volume(symbol)
        except Exception:
            return self._fallback.get_volume(symbol)

    def get_candles(self, symbol: str, interval: str, start_ms: int, end_ms: int):
        try:
            data = self._primary.get_candles(symbol, interval, start_ms, end_ms)
            return data if data else self._fallback.get_candles(symbol, interval, start_ms, end_ms)
        except Exception:
            return self._fallback.get_candles(symbol, interval, start_ms, end_ms)


def get_market_provider(config=None):
    """Return the market data provider for the active market."""
    import os
    if config is None:
        # No config passed — auto-detect from environment
        if os.environ.get("ALPACA_API_KEY_ID") and os.environ.get("ALPACA_API_SECRET_KEY"):
            from .alpaca_data import AlpacaDataProvider
            logger.info("Market data provider: AlpacaDataProvider (auto, strict)")
            return AlpacaDataProvider()
        if os.environ.get("FINNHUB_API_KEY"):
            from .finnhub import get_provider as get_finnhub
            logger.info("Market data provider: FinnhubProvider (auto)")
            return get_finnhub()
        logger.info("Market data provider: YahooProvider (auto)")
        return YahooProvider()
    if os.environ.get("ALPACA_API_KEY_ID") and os.environ.get("ALPACA_API_SECRET_KEY"):
        from .alpaca_data import AlpacaDataProvider
        logger.info("Market data provider: AlpacaDataProvider (equities, strict)")
        return AlpacaDataProvider()
    # Prefer Finnhub if key is set, fall back to Yahoo
    if os.environ.get("FINNHUB_API_KEY"):
        from .finnhub import get_provider as get_finnhub
        logger.info("Market data provider: FinnhubProvider (equities)")
        return get_finnhub()
    logger.info("Market data provider: YahooProvider (equities)")
    return YahooProvider()


def get_trading_provider(config=None):
    """Return the trading provider for the active market."""
    from .alpaca import get_provider as get_alpaca
    return get_alpaca(config=config)
