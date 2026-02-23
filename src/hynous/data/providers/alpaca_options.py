"""
Alpaca Options Data Provider

Uses Alpaca Market Data option chain endpoint to fetch contracts, quotes,
greeks, and IV for a given underlying.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from alpaca.data.historical import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest
from alpaca.trading.enums import ContractType

from ...core.config import load_config

logger = logging.getLogger(__name__)

# OCC symbol format: Underlying (1-6) + YYMMDD + C/P + 8-digit strike (×1000)
_OCC_RE = re.compile(r'^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$')


def _parse_occ(symbol: str) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """Parse OCC symbol to (strike, expiry YYYY-MM-DD, type call/put)."""
    m = _OCC_RE.match(symbol.upper())
    if not m:
        return None, None, None
    yy, mm, dd = int(m.group(2)), int(m.group(3)), int(m.group(4))
    strike = int(m.group(6)) / 1000.0
    opt_type = "call" if m.group(5) == "C" else "put"
    try:
        exp = date(2000 + yy, mm, dd).isoformat()
    except Exception:
        exp = None
    return strike, exp, opt_type


def get_options_chain(
    underlying: str,
    expiration_gte: Optional[str] = None,
    contract_type: Optional[str] = None,
    limit: int = 60,
) -> list[dict]:
    """Fetch options chain for an underlying via Alpaca options data API."""
    cfg = load_config()
    api_key = cfg.alpaca_api_key
    api_secret = cfg.alpaca_api_secret
    if not api_key or not api_secret:
        logger.warning("Alpaca API keys not set — options chain unavailable")
        return []

    client = OptionHistoricalDataClient(api_key, api_secret)

    type_enum = None
    if contract_type:
        if contract_type.lower() == "call":
            type_enum = ContractType.CALL
        elif contract_type.lower() == "put":
            type_enum = ContractType.PUT

    req = OptionChainRequest(
        underlying_symbol=underlying.upper(),
        expiration_date_gte=expiration_gte,
        type=type_enum,
    )

    try:
        snapshots = client.get_option_chain(req)
    except Exception as e:
        logger.error("Alpaca options chain failed for %s: %s", underlying, e)
        return []

    out: list[dict] = []
    for sym, snap in snapshots.items():
        strike, exp, opt_type = _parse_occ(sym)
        if strike is None or exp is None or opt_type is None:
            continue
        quote = snap.latest_quote
        greeks = snap.greeks
        out.append({
            "symbol": sym,
            "strike": strike,
            "expiry": exp,
            "type": opt_type,
            "bid": float(quote.bid_price) if quote and quote.bid_price is not None else 0.0,
            "ask": float(quote.ask_price) if quote and quote.ask_price is not None else 0.0,
            "delta": float(greeks.delta) if greeks and greeks.delta is not None else None,
            "iv": float(snap.implied_volatility) if snap.implied_volatility is not None else None,
            "open_interest": 0,
        })

    if limit and len(out) > limit:
        out = out[:limit]
    return out
