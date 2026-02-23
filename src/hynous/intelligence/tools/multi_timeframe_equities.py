"""
Multi-Timeframe Analysis Tool (Equities)
"""

import logging
from datetime import datetime, timedelta, timezone

from .registry import Tool
from ...data.providers import get_market_provider

logger = logging.getLogger(__name__)


TOOL_DEF = {
    "name": "get_multi_timeframe",
    "description": (
        "Get multi-timeframe price analysis for a US equity.\n"
        "Analyzes 24h, 7d, and 30d simultaneously in one call.\n"
        'Example: {"symbol": "AAPL"}'
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": 'Equity symbol (e.g., "AAPL")'},
        },
        "required": ["symbol"],
    },
}

_TIMEFRAMES = [
    ("24h", timedelta(hours=24), "1h"),
    ("7d", timedelta(days=7), "4h"),
    ("30d", timedelta(days=30), "1d"),
]


def handle_get_multi_timeframe(symbol: str) -> str:
    symbol = symbol.upper()
    provider = get_market_provider()

    price = provider.get_price(symbol)
    if price is None:
        return f"{symbol}: Not found on Finnhub."

    now = datetime.now(timezone.utc)
    tf_data = []
    per_tf_lines = []

    for label, delta, interval in _TIMEFRAMES:
        start_ms = int((now - delta).timestamp() * 1000)
        end_ms = int(now.timestamp() * 1000)

        try:
            candles = provider.get_candles(symbol, interval, start_ms, end_ms)
        except Exception as e:
            logger.error("Candle fetch error for %s %s: %s", symbol, label, e)
            per_tf_lines.append(f"  {label}: Error fetching data")
            continue

        if not candles:
            per_tf_lines.append(f"  {label}: No data")
            continue

        analysis = _analyze_timeframe(candles)
        tf_data.append({"label": label, **analysis})

        sign = "+" if analysis["change"] > 0 else ""
        per_tf_lines.append(
            f"  {label}: {sign}{analysis['change']:.1f}% | "
            f"{analysis['trend']} | "
            f"Vol: {analysis['vol_label']} | "
            f"Range: {_fmt_price(analysis['low'])} - {_fmt_price(analysis['high'])}"
        )

    prev = provider.get_prev_close(symbol) or price
    day_change = ((price - prev) / prev) * 100 if prev else 0

    lines = [f"{symbol} @ {_fmt_price(price)} | Day: {day_change:+.2f}%", ""]
    lines.append("Timeframes:")
    lines.extend(per_tf_lines)

    if len(tf_data) >= 2:
        lines.append("")
        lines.append("Cross-Timeframe:")
        lines.extend(_cross_timeframe(tf_data))

    return "\n".join(lines)


def register(registry) -> None:
    registry.register(Tool(
        name=TOOL_DEF["name"],
        description=TOOL_DEF["description"],
        parameters=TOOL_DEF["parameters"],
        handler=handle_get_multi_timeframe,
    ))


def _analyze_timeframe(candles: list[dict]) -> dict:
    closes = [c["c"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]

    start_price = candles[0]["o"]
    end_price = closes[-1]
    change = ((end_price - start_price) / start_price) * 100 if start_price else 0

    n = len(closes)
    third = max(n // 3, 1)
    first_avg = sum(closes[:third]) / third
    last_avg = sum(closes[-third:]) / third
    trend_pct = ((last_avg - first_avg) / first_avg) * 100 if first_avg else 0

    if trend_pct > 2:
        trend = "Bullish"
    elif trend_pct < -2:
        trend = "Bearish"
    else:
        trend = "Sideways"

    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            returns.append(abs((closes[i] - closes[i - 1]) / closes[i - 1]) * 100)
    vol_score = sum(returns) / len(returns) if returns else 0

    if vol_score < 0.5:
        vol_label = "Low"
    elif vol_score < 1.5:
        vol_label = "Moderate"
    elif vol_score < 3.0:
        vol_label = "High"
    else:
        vol_label = "Extreme"

    return {
        "change": change,
        "trend": trend,
        "trend_pct": trend_pct,
        "vol_label": vol_label,
        "vol_score": vol_score,
        "high": max(highs),
        "low": min(lows),
    }


def _cross_timeframe(tf_data: list[dict]) -> list[str]:
    lines = []
    trends = [tf["trend"] for tf in tf_data]
    labels = [tf["label"] for tf in tf_data]

    unique_trends = set(trends)
    if len(unique_trends) == 1:
        lines.append(f"  Alignment: All {trends[0].lower()} across timeframes")
    else:
        long_trend = trends[-1]
        lines.append(
            f"  Alignment: Short-term ({labels[0]}) is {trends[0].lower()}, "
            f"long-term ({labels[-1]}) is {long_trend.lower()}"
        )

    vols = [tf["vol_score"] for tf in tf_data]
    if vols == sorted(vols):
        lines.append("  Volatility: Expanding into longer timeframes")
    elif vols == sorted(vols, reverse=True):
        lines.append("  Volatility: Compressing into longer timeframes")
    else:
        lines.append("  Volatility: Mixed across timeframes")

    return lines


def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.0f}"
    if price >= 1:
        return f"${price:,.2f}"
    return f"${price:.4f}"
