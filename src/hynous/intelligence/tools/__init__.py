"""
Tools - Functions Hynous can call

Each module defines tools for a specific domain:
- market_equities.py: Price data, period analysis, comparisons
- memory.py: Search and store knowledge (future)
- trading_equities.py: Execute trades, manage positions (equities/options)

Pattern: each module has TOOL_DEF + handler + register(registry).
See market_equities.py for the reference implementation.
"""

from .registry import ToolRegistry, get_registry

__all__ = ["ToolRegistry", "get_registry"]
