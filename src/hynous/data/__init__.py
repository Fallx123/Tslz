"""
Data Layer - Market Data Providers

Connects Hynous to the outside world:
- Alpaca/Finnhub/Yahoo market data (equities)

These are wrapped by intelligence/tools/ for agent use.

Usage:
    from hynous.data.providers import get_market_provider

    provider = get_market_provider()
    price = provider.get_price("AAPL")
"""
__all__ = []
