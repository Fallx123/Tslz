"""
Market Data Tool (Equities)

Finnhub-backed snapshots and period summaries for US stocks/ETFs.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .registry import Tool
from ...data.providers import get_market_provider

logger = logging.getLogger(__name__)


TOOL_DEF = {
    "name": "get_market_data",
    "description": (
        "Get market data for US equities (Finnhub). Flexible usage:\n"
        "- Current snapshot: just pass symbols\n"
        "- Period analysis (trend, volatility, key levels): pass symbols + period\n"
        "- Custom date range: pass symbols + start_date and/or end_date\n\n"
        "Examples:\n"
        '  {"symbols": ["AAPL"]} → current AAPL price, day change, volume\n'
        '  {"symbols": ["SPY"], "period": "7d"} → SPY 7-day price action summary\n'
        '  {"symbols": ["AAPL", "MSFT"], "period": "30d"} → compare over 30d\n'
        '  {"symbols": ["TSLA"], "start_date": "2025-01-15", "end_date": "2025-02-01"}'
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbols": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Equity symbols to query (e.g., ["AAPL", "MSFT", "SPY"])',
            },
            "period": {
                "type": "string",
                "enum": ["1h", "4h", "24h", "7d", "30d", "90d"],
                "description": "Analysis period. Short (1h-4h) for micro, longer for swing.",
            },
            "start_date": {
                "type": "string",
                "description": 'Start date in ISO format (e.g., "2025-01-15").',
            },
            "end_date": {
                "type": "string",
                "description": 'End date in ISO format (e.g., "2025-02-01"). Defaults to now if omitted.',
            },
        },
        "required": ["symbols"],
    },
}


_PERIODS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}


def handle_get_market_data(
    symbols: list[str],
    period: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    provider = get_market_provider()
    results = []

    for symbol in symbols:
        symbol = symbol.upper()
        try:
            if period or start_date:
                now = datetime.now(timezone.utc)

                if start_date and end_date:
                    start_dt = _parse_date(start_date)
                    end_dt = _parse_date(end_date)
                    label = f"{start_date} to {end_date}"
                elif start_date:
                    start_dt = _parse_date(start_date)
                    end_dt = now
                    label = f"{start_date} to now"
                elif period:
                    delta = _PERIODS.get(period)
                    if not delta:
                        results.append(f"{symbol}: Invalid period '{period}'.")
                        continue
                    start_dt = now - delta
                    end_dt = now
                    label = period

                duration = end_dt - start_dt
                interval = _pick_interval(duration)

                start_ms = int(start_dt.timestamp() * 1000)
                end_ms = int(end_dt.timestamp() * 1000)

                candles = provider.get_candles(symbol, interval, start_ms, end_ms)
                snapshot = _format_snapshot(provider, symbol)
                summary = _compute_summary(candles, symbol, label)

                results.append(snapshot)
                results.append(summary)
            else:
                results.append(_format_snapshot(provider, symbol))
        except Exception as e:
            logger.error("Error fetching data for %s: %s", symbol, e)
            results.append(f"{symbol}: Error — {e}")

    return "\n---\n".join(results)


def register(registry) -> None:
    registry.register(Tool(
        name=TOOL_DEF["name"],
        description=TOOL_DEF["description"],
        parameters=TOOL_DEF["parameters"],
        handler=handle_get_market_data,
    ))


def _parse_date(date_str: str) -> datetime:
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _pick_interval(duration: timedelta) -> str:
    minutes = duration.total_seconds() / 60
    if minutes <= 120:
        return "5m"
    if minutes <= 720:
        return "15m"
    if minutes <= 2880:
        return "1h"
    if minutes <= 86400:
        return "4h"
    return "1d"


def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.0f}"
    if price >= 1:
        return f"${price:,.2f}"
    return f"${price:.4f}"


def _fmt_big(n: float) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:.0f}"


def _format_snapshot(provider, symbol: str) -> str:
    price = provider.get_price(symbol)
    if price is None:
        return f"{symbol}: Not found on Finnhub."

    prev = provider.get_prev_close(symbol) or price
    change_pct = ((price - prev) / prev) * 100 if prev else 0
    vol = provider.get_volume(symbol)

    parts = [f"{symbol} @ {_fmt_price(price)}", f"Day: {change_pct:+.2f}%"]
    if vol:
        parts.append(f"Vol: {_fmt_big(vol)}")
    return " | ".join(parts)


def _compute_summary(candles: list[dict], symbol: str, label: str) -> str:
    if not candles:
        return f"{symbol} ({label}): No data."

    opens = candles[0]["o"]
    closes = candles[-1]["c"]
    high = max(c["h"] for c in candles)
    low = min(c["l"] for c in candles)
    change = ((closes - opens) / opens) * 100 if opens else 0

    vol = sum(c.get("v", 0) for c in candles)
    trend = "Up" if change > 1 else "Down" if change < -1 else "Flat"

    return (
        f"{symbol} ({label}): {change:+.2f}% | Trend: {trend} | "
        f"Range: {_fmt_price(low)} - {_fmt_price(high)} | Vol: {_fmt_big(vol)}"
    )
