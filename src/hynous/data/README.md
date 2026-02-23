# Data Module

> Market data providers - Hynous's window to the markets.

---

## Structure

```
data/
├── providers/
│   ├── alpaca_data.py   # Equities market data
│   ├── finnhub.py       # Equities market data (optional)
│   └── yahoo.py         # Equities market data (fallback)
└── __init__.py
```

---

## Providers

| Provider | Data | Priority |
|----------|------|----------|
| Alpaca | Prices, historical bars | P0 |
| Finnhub | Market data backup | P1 |
| Yahoo | Historical OHLCV fallback | P2 |

---

## Provider Interface

All providers follow the same pattern:

```python
class BaseProvider:
    async def get_price(self, symbol: str) -> float: ...
    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list: ...
```

---

## Example Provider

```python
class AlpacaProvider:
    def __init__(self, api_key: str, api_secret: str):
        ...

    def get_price(self, symbol: str) -> float:
        ...
```

---

## Adding a New Provider

1. Create file in `providers/`
2. Implement the base interface
3. Export in `__init__.py`
4. Create corresponding tools in `intelligence/tools/`
