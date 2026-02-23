"""
Massive.com Options Data Provider

Thin wrapper around the Massive.com REST API for options chain data.
Free tier: 5 requests/minute.

Reads MASSIVE_API_KEY from environment variables.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.massive.com/v3"
_TIMEOUT = 10


def get_options_chain(
    underlying: str,
    expiration_gte: Optional[str] = None,
    contract_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Fetch options chain for an underlying symbol from Massive.com.

    Args:
        underlying: Underlying ticker symbol (e.g. "AAPL")
        expiration_gte: Minimum expiration date filter (YYYY-MM-DD)
        contract_type: "call" or "put" (None = both)
        limit: Max contracts to return (default 50)

    Returns:
        List of contract dicts with keys:
        symbol, strike, expiry, type, greeks (dict), iv, bid, ask, open_interest
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "")
    if not api_key:
        logger.warning("MASSIVE_API_KEY not set — options chain unavailable")
        return []

    url = f"{_BASE_URL}/snapshot/options/{underlying.upper()}"
    params: dict = {"limit": limit}
    if expiration_gte:
        params["expiration_date.gte"] = expiration_gte
    if contract_type:
        params["contract_type"] = contract_type.lower()

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.HTTPError as e:
        logger.error("Massive.com HTTP error for %s: %s", underlying, e)
        return []
    except requests.exceptions.RequestException as e:
        logger.error("Massive.com request failed for %s: %s", underlying, e)
        return []
    except Exception as e:
        logger.error("Massive.com unexpected error: %s", e)
        return []

    results = data.get("results", []) if isinstance(data, dict) else data
    contracts = []
    for item in results:
        greeks = item.get("greeks", {}) or {}
        contracts.append({
            "symbol": item.get("ticker", item.get("symbol", "")),
            "strike": float(item.get("strike_price", item.get("strike", 0)) or 0),
            "expiry": item.get("expiration_date", item.get("expiry", "")),
            "type": item.get("contract_type", item.get("type", "")),
            "bid": float(item.get("bid", 0) or 0),
            "ask": float(item.get("ask", 0) or 0),
            "delta": float(greeks.get("delta", 0) or 0),
            "gamma": float(greeks.get("gamma", 0) or 0),
            "theta": float(greeks.get("theta", 0) or 0),
            "iv": float(item.get("implied_volatility", item.get("iv", 0)) or 0),
            "open_interest": int(item.get("open_interest", 0) or 0),
        })

    return contracts
