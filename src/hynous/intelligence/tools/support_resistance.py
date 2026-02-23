"""
Support/Resistance Tool (Equities)

Computes recent support and resistance levels and stores them in memory.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .registry import Tool
from ...data.providers import get_market_provider
from ...nous.client import get_client

logger = logging.getLogger(__name__)


TOOL_DEF = {
    "name": "get_support_resistance",
    "description": (
        "Compute support and resistance levels for a US equity using recent price action.\n"
        "Returns clustered levels with strength (touch count). Automatically stores the result in memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Equity symbol (e.g., AAPL)"},
            "lookback_days": {"type": "integer", "description": "Lookback window in days (default 120)."},
            "interval": {
                "type": "string",
                "enum": ["1h", "4h", "1d"],
                "description": "Candle interval for level detection.",
            },
        },
        "required": ["symbol"],
    },
}


def handle_get_support_resistance(
    symbol: str,
    lookback_days: int = 120,
    interval: str = "1d",
) -> str:
    symbol = symbol.upper()
    provider = get_market_provider()

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    candles = provider.get_candles(
        symbol,
        interval,
        int(start.timestamp() * 1000),
        int(end.timestamp() * 1000),
    )
    if not candles:
        return f"{symbol}: No candle data for support/resistance."

    levels = _compute_levels(candles)
    if not levels:
        return f"{symbol}: Could not derive support/resistance."

    # Store in memory for persistence
    try:
        client = get_client()
        payload = json.dumps({"levels": levels}, indent=2)
        client.create_node(
            type="market",
            subtype="support_resistance",
            title=f"{symbol} support/resistance levels",
            body=payload,
            summary=f"{len(levels)} levels from last {lookback_days}d ({interval})",
            event_time=end.isoformat(),
            event_confidence=0.6,
            event_source="computed",
        )
    except Exception as e:
        logger.debug("S/R memory store failed: %s", e)

    lines = [f"{symbol} Support/Resistance ({lookback_days}d, {interval}):"]
    for lvl in levels[:8]:
        lines.append(
            f"  {lvl['type'].upper()} {lvl['price']:.2f} "
            f"(touches: {lvl['touches']}, strength: {lvl['strength']:.2f})"
        )
    if len(levels) > 8:
        lines.append(f"  +{len(levels) - 8} more levels")
    return "\n".join(lines)


def register(registry) -> None:
    registry.register(Tool(
        name=TOOL_DEF["name"],
        description=TOOL_DEF["description"],
        parameters=TOOL_DEF["parameters"],
        handler=handle_get_support_resistance,
    ))


def _compute_levels(candles: list[dict]) -> list[dict]:
    """Simple pivot-based S/R with clustering."""
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]

    pivots = []
    for i in range(2, len(candles) - 2):
        h = highs[i]
        l = lows[i]
        if h >= highs[i - 1] and h >= highs[i - 2] and h >= highs[i + 1] and h >= highs[i + 2]:
            pivots.append(("resistance", h))
        if l <= lows[i - 1] and l <= lows[i - 2] and l <= lows[i + 1] and l <= lows[i + 2]:
            pivots.append(("support", l))

    if not pivots:
        return []

    # Cluster levels by proximity (0.5% bands)
    clusters: list[dict] = []
    for p_type, price in pivots:
        placed = False
        for c in clusters:
            if abs(price - c["price"]) / c["price"] <= 0.005:
                c["prices"].append(price)
                c["touches"] += 1
                placed = True
                break
        if not placed:
            clusters.append({
                "type": p_type,
                "price": price,
                "prices": [price],
                "touches": 1,
            })

    levels = []
    for c in clusters:
        avg_price = sum(c["prices"]) / len(c["prices"])
        strength = min(1.0, c["touches"] / 6)
        levels.append({
            "type": c["type"],
            "price": avg_price,
            "touches": c["touches"],
            "strength": strength,
        })

    # Sort: strongest first, then price
    levels.sort(key=lambda x: (-x["strength"], x["price"]))
    return levels

