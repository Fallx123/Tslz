"""
Options Trading Tools

Provides three tools for options trading via Alpaca + Massive.com:
  - get_options_chain: Fetch and display available contracts
  - execute_option_trade: Buy/sell option contracts
  - close_option: Close an existing option position
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

from .registry import Tool
from ...data.providers.alpaca import get_provider as get_alpaca
from ...core.config import load_config

logger = logging.getLogger(__name__)

# OCC symbol format: Underlying (up to 6 chars) + YYMMDD + C/P + 8-digit strike (×1000)
_OCC_RE = re.compile(r'^[A-Z]{1,6}\d{6}[CP]\d{8}$')


def _parse_occ_symbol(symbol: str) -> tuple[str | None, date | None, str | None, float | None]:
    """Parse OCC symbol to (underlying, expiry_date, type, strike)."""
    m = re.match(r'^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$', symbol.upper())
    if not m:
        return None, None, None, None
    yy, mm, dd = int(m.group(2)), int(m.group(3)), int(m.group(4))
    opt_type = "call" if m.group(5) == "C" else "put"
    strike = int(m.group(6)) / 1000.0
    try:
        exp = date(2000 + yy, mm, dd)
    except Exception:
        exp = None
    return m.group(1), exp, opt_type, strike


def _fetch_chain(
    underlying: str,
    expiry: date | None = None,
    opt_type: str | None = None,
) -> list[dict]:
    """Fetch options chain for an underlying with optional expiry/type filters."""
    if not underlying:
        return []
    cfg = load_config()
    provider = getattr(cfg.options, "provider", "massive")
    if provider == "alpaca":
        from ...data.providers.alpaca_options import get_options_chain as _fetch_chain
    else:
        from ...data.providers.massive import get_options_chain as _fetch_chain

    return _fetch_chain(
        underlying=underlying,
        expiration_gte=expiry.isoformat() if expiry else None,
        contract_type=opt_type,
        limit=200,
    ) or []


def _lookup_contract_snapshot(
    symbol: str,
    underlying: str,
    expiry: date | None,
    opt_type: str | None,
    strike: float | None,
) -> dict | None:
    """Fetch a contract snapshot from the chain and match by symbol/strike."""
    if not underlying or not opt_type or not expiry:
        return None
    contracts = _fetch_chain(underlying=underlying, expiry=expiry, opt_type=opt_type)
    if not contracts:
        return None

    sym = symbol.upper()
    for c in contracts:
        if (c.get("symbol") or "").upper() == sym:
            return c

    if strike is not None:
        for c in contracts:
            if c.get("expiry") == expiry.isoformat() and float(c.get("strike", 0) or 0) == float(strike):
                return c
    return None


# ── Tool Definitions ──────────────────────────────────────────────────────────

GET_CHAIN_TOOL_DEF = {
    "name": "get_options_chain",
    "description": (
        "Fetch live options chain for a stock. ALWAYS call this before execute_option_trade.\n\n"
        "STRIKE SELECTION RULES — follow these exactly:\n"
        "  Calls: only consider strikes AT or BELOW current price (ATM/ITM). Never buy far OTM calls.\n"
        "  Puts:  only consider strikes AT or ABOVE current price (ATM/ITM). Never buy far OTM puts.\n\n"
        "CONVICTION → STRIKE GUIDANCE:\n"
        "  High (0.8+)  → pick the deepest ITM contract shown in 'HIGH CONVICTION' section (delta 0.65-0.85)\n"
        "  Medium (0.6) → pick the ATM contract shown in 'STANDARD' section (delta 0.45-0.65)\n\n"
        "The tool auto-filters out OTM contracts and labels the recommended picks for you."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Underlying equity symbol (e.g. AAPL, SPY)",
            },
            "expiration": {
                "type": "string",
                "description": "Target expiration date in YYYY-MM-DD format (used as gte filter).",
            },
            "contract_type": {
                "type": "string",
                "enum": ["call", "put"],
                "description": "Filter to calls or puts only.",
            },
            "min_strike": {
                "type": "number",
                "description": "Minimum strike price filter (optional).",
            },
            "max_strike": {
                "type": "number",
                "description": "Maximum strike price filter (optional).",
            },
        },
        "required": ["symbol"],
    },
}


EXECUTE_OPTION_TOOL_DEF = {
    "name": "execute_option_trade",
    "description": (
        "Buy or sell an options contract via Alpaca.\n\n"
        "REQUIRED: call get_options_chain first and use a symbol from 'HIGH CONVICTION' or 'STANDARD' section.\n"
        "OCC format: AAPL250321C00200000 = AAPL / 2025-03-21 / Call / $200 strike\n\n"
        "HARD RULES:\n"
        "  - Always use qty=1. Never trade more than 1 contract.\n"
        "  - Trades below 0.6 confidence are rejected.\n"
        "  - Calls must be ATM or ITM (strike ≤ price).\n"
        "  - Puts must be ATM or ITM (strike ≥ price).\n"
        "  - Always pick from the recommended strikes shown in get_options_chain output.\n"
        "  - DTE and IV gates apply (see trading settings).\n"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "OCC-format option symbol (e.g. AAPL250321C00200000)",
            },
            "side": {
                "type": "string",
                "enum": ["buy", "sell"],
                "description": "'buy' to open, 'sell' to close or go short.",
            },
            "qty": {
                "type": "integer",
                "description": "Number of contracts. Always use 1.",
                "minimum": 1,
                "maximum": 1,
            },
            "order_type": {
                "type": "string",
                "enum": ["market", "limit"],
                "description": "Order type. Use limit with a limit_price for price control.",
            },
            "limit_price": {
                "type": "number",
                "description": "Limit price per contract (required if order_type='limit').",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Conviction score (0.0–1.0). Rejected below 0.6.",
            },
            "reasoning": {
                "type": "string",
                "description": "Full thesis for this options trade.",
            },
        },
        "required": ["symbol", "side", "qty", "order_type", "confidence", "reasoning"],
    },
}


CLOSE_OPTION_TOOL_DEF = {
    "name": "close_option",
    "description": (
        "Close (sell-to-close) an open options position at market price via Alpaca.\n"
        "Provide the OCC-format symbol of the position to close."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "OCC-format option symbol to close (e.g. AAPL250321C00200000)",
            },
            "reasoning": {
                "type": "string",
                "description": "Reason for closing this position.",
            },
        },
        "required": ["symbol", "reasoning"],
    },
}


# ── Handlers ─────────────────────────────────────────────────────────────────

def _delta_val(c: dict) -> float:
    """Return a sortable absolute delta (0 if missing)."""
    d = c.get("delta")
    return abs(float(d)) if d is not None else 0.0


def _moneyness_label(strike: float, current: float, opt_type: str) -> str:
    """Return ITM / ATM / OTM label."""
    pct = (strike - current) / current * 100
    if opt_type == "call":
        if strike < current * 0.993:
            return "ITM"
        if strike <= current * 1.007:
            return "ATM"
        return "OTM"
    else:  # put
        if strike > current * 1.007:
            return "ITM"
        if strike >= current * 0.993:
            return "ATM"
        return "OTM"


def _closest_strike(
    strikes: list[float],
    current_price: float,
    opt_type: str,
) -> float | None:
    """Closest strike on the ITM side; fallback to absolute closest."""
    if not strikes:
        return None
    if opt_type == "call":
        side = [s for s in strikes if s <= current_price]
    else:
        side = [s for s in strikes if s >= current_price]
    if side:
        return min(side, key=lambda s: abs(s - current_price))
    return min(strikes, key=lambda s: abs(s - current_price))


def _is_itm_or_atm(strike: float, current_price: float, opt_type: str) -> bool:
    if opt_type == "call":
        return strike <= current_price
    return strike >= current_price


def handle_get_options_chain(
    symbol: str,
    expiration: str | None = None,
    contract_type: str | None = None,
    min_strike: float | None = None,
    max_strike: float | None = None,
) -> str:
    cfg = load_config()
    provider = getattr(cfg.options, "provider", "massive")
    if provider == "alpaca":
        from ...data.providers.alpaca_options import get_options_chain as _fetch_chain
    else:
        from ...data.providers.massive import get_options_chain as _fetch_chain

    symbol = symbol.upper()

    # Get current price for ITM/ATM filtering and recommendations
    current_price: float | None = None
    try:
        from ...data.providers import get_market_provider
        current_price = get_market_provider().get_price(symbol)
    except Exception:
        pass

    contracts = _fetch_chain(
        underlying=symbol,
        expiration_gte=expiration,
        contract_type=contract_type,
        limit=100,
    )

    if not contracts:
        return (
            f"No options data returned for {symbol}. "
            "Check that MASSIVE_API_KEY is set and the symbol is valid."
        )

    # Apply manual strike filters
    if min_strike is not None:
        contracts = [c for c in contracts if c["strike"] >= min_strike]
    if max_strike is not None:
        contracts = [c for c in contracts if c["strike"] <= max_strike]

    # ── Strike filter: ATM or ITM only; fallback to closest ITM strike ──────
    if current_price:
        filtered = []
        for c in contracts:
            t = (c.get("type") or "").lower()
            if t in ("call", "put"):
                if _is_itm_or_atm(float(c["strike"]), current_price, t):
                    filtered.append(c)
            else:
                filtered.append(c)  # unknown type — keep it

        if filtered:
            contracts = filtered
        else:
            # No ITM/ATM strikes — keep closest strike on each side
            calls = [c for c in contracts if (c.get("type") or "").lower() == "call"]
            puts = [c for c in contracts if (c.get("type") or "").lower() == "put"]
            keep = []
            if calls:
                call_strikes = [float(c["strike"]) for c in calls]
                target = _closest_strike(call_strikes, current_price, "call")
                keep.extend([c for c in calls if float(c["strike"]) == float(target)])
            if puts:
                put_strikes = [float(c["strike"]) for c in puts]
                target = _closest_strike(put_strikes, current_price, "put")
                keep.extend([c for c in puts if float(c["strike"]) == float(target)])
            contracts = keep if keep else contracts

    if not contracts:
        return f"No ITM/ATM contracts found for {symbol}. The stock may be outside the filter range."

    # Sort by expiry then by delta (highest absolute delta = deepest ITM first)
    contracts.sort(key=lambda c: (c["expiry"], -_delta_val(c)))

    header = f"{'Symbol':<26} {'Strike':>8} {'Expiry':>12} {'Type':>5} {'Bid':>7} {'Ask':>7} {'Delta':>7} {'IV':>7} {'Tag':>5}"
    sep = "─" * 88

    def _fmt_row(c: dict, tag: str = "") -> str:
        iv_pct = f"{c['iv'] * 100:.1f}%" if c.get("iv") else "—"
        delta_s = f"{c['delta']:+.3f}" if c.get("delta") is not None else "—"
        return (
            f"{c['symbol']:<26} {c['strike']:>8.2f} {c['expiry']:>12} {c['type'].upper():>5} "
            f"{c['bid']:>7.2f} {c['ask']:>7.2f} {delta_s:>7} {iv_pct:>7} {tag:>5}"
        )

    lines = []
    price_line = f"Current {symbol} price: ${current_price:.2f}" if current_price else ""
    if price_line:
        lines.append(price_line)

    # ── Split into CLOSEST ITM/ATM and OTHER ITM/ATM ────────────────────────
    # Closest ITM/ATM strike per option type (call/put).
    closest = []
    rest = []
    if current_price:
        calls = [c for c in contracts if (c.get("type") or "").lower() == "call"]
        puts = [c for c in contracts if (c.get("type") or "").lower() == "put"]
        if calls:
            call_strikes = [float(c["strike"]) for c in calls]
            target = _closest_strike(call_strikes, current_price, "call")
            closest.extend([c for c in calls if float(c["strike"]) == float(target)])
        if puts:
            put_strikes = [float(c["strike"]) for c in puts]
            target = _closest_strike(put_strikes, current_price, "put")
            closest.extend([c for c in puts if float(c["strike"]) == float(target)])

        # Everything else goes to rest
        closest_syms = {c["symbol"] for c in closest}
        rest = [c for c in contracts if c["symbol"] not in closest_syms]
    else:
        rest = contracts

    if closest:
        lines.append(f"\n★ CLOSEST ITM/ATM (preferred):")
        lines.append(header)
        lines.append(sep)
        for c in closest[:3]:
            lines.append(_fmt_row(c, "★"))

    if rest:
        lines.append(f"\n  Other ITM/ATM:")
        lines.append(header)
        lines.append(sep)
        for c in rest[:5]:
            tag = _moneyness_label(c["strike"], current_price, (c.get("type") or "call").lower()) if current_price else ""
            lines.append(_fmt_row(c, tag))

    lines.append(
        "\nPick a symbol from ★ CLOSEST ITM/ATM section above, "
        "then pass it to execute_option_trade."
    )
    return "\n".join(lines)


def handle_execute_option_trade(
    symbol: str,
    side: str,
    qty: int,
    order_type: str,
    confidence: float,
    reasoning: str,
    limit_price: float | None = None,
) -> str:
    from ...core.trading_settings import get_trading_settings
    ts = get_trading_settings()

    # Validate OCC format
    if not _OCC_RE.match(symbol.upper()):
        return (
            f"Error: '{symbol}' is not a valid OCC option symbol.\n"
            "Format: AAPL250321C00200000 (ticker + YYMMDD + C/P + 8-digit strike×1000)\n"
            "Use get_options_chain to find the correct symbol."
        )

    symbol = symbol.upper()

    # Confidence gate
    if confidence < 0.6:
        return f"Conviction too low ({confidence:.0%}). Minimum 0.6 required for options trades."

    # DTE + IV gates (buy side only)
    underlying, exp_date, opt_type, strike = _parse_occ_symbol(symbol)
    dte = None
    iv = None
    if side == "buy" and exp_date:
        dte = (exp_date - datetime.now(timezone.utc).date()).days
        if dte < 0:
            return f"BLOCKED: option {symbol} is already expired."
        min_dte = int(getattr(ts, "options_min_dte", 0) or 0)
        max_dte = int(getattr(ts, "options_max_dte", 0) or 0)
        if min_dte and dte < min_dte:
            return f"BLOCKED: DTE {dte}d below minimum {min_dte}d."
        if max_dte and dte > max_dte:
            return f"BLOCKED: DTE {dte}d above maximum {max_dte}d."

        iv_min = float(getattr(ts, "options_iv_min", 0.0) or 0.0)
        iv_max = float(getattr(ts, "options_iv_max", 0.0) or 0.0)
        if iv_min or iv_max:
            snap = _lookup_contract_snapshot(symbol, underlying, exp_date, opt_type, strike)
            iv_val = snap.get("iv") if snap else None
            iv = float(iv_val) if iv_val is not None else None
            if iv is None or iv <= 0:
                if not getattr(ts, "rules_allow_if_data_missing", False):
                    return "BLOCKED: IV unavailable for this contract."
            else:
                if iv_min and iv < iv_min:
                    return f"BLOCKED: IV {iv:.2f} below minimum {iv_min:.2f}."
                if iv_max and iv > iv_max:
                    return f"BLOCKED: IV {iv:.2f} above maximum {iv_max:.2f}."

    # Options-only cap enforcement (buy side only)
    if side == "buy":
        if limit_price is None:
            return "Error: limit_price required for options when caps are enabled."
        premium = float(limit_price)
        notional = premium * 100
        if notional > getattr(ts, "fixed_notional_cap_usd", 200.0):
            return f"BLOCKED: option premium ${notional:.2f} exceeds cap ${ts.fixed_notional_cap_usd:.2f}."
        if notional > getattr(ts, "max_loss_usd", 100.0):
            return f"BLOCKED: max loss ${notional:.2f} exceeds limit ${ts.max_loss_usd:.2f}."

    # Rules gate — evaluate on underlying direction
    rule_result = None
    occ_match = re.match(r'^([A-Z]{1,6})(\d{6})([CP])(\d{8})$', symbol)
    if occ_match:
        underlying = occ_match.group(1)
        opt_type = "call" if occ_match.group(3) == "C" else "put"
        if side == "buy":
            dir_side = "long" if opt_type == "call" else "short"
        else:  # sell
            dir_side = "short" if opt_type == "call" else "long"
        try:
            from ...core.trade_rules import evaluate_trade_rules
            rule_result = evaluate_trade_rules(underlying, dir_side)
            if not rule_result.allowed:
                detail = "; ".join(rule_result.reasons)
                extras = " | ".join(f"{k}={v}" for k, v in rule_result.details.items() if v)
                if extras:
                    detail += f" ({extras})"
                return f"BLOCKED by rules: {detail}"
        except Exception:
            pass

    if qty < 1:
        return "Error: qty must be at least 1 contract."
    qty = 1  # always trade exactly 1 contract

    # ── Strike guard: allow ATM or ITM only (fallback to closest ITM strike) ─
    # Parse strike and type from OCC symbol, then check against current price
    if occ_match and side == "buy":
        parsed_strike = int(occ_match.group(4)) / 1000.0
        parsed_type = "call" if occ_match.group(3) == "C" else "put"
        try:
            from ...data.providers import get_market_provider
            underlying = occ_match.group(1)
            current_price = get_market_provider().get_price(underlying)
            if current_price:
                if _is_itm_or_atm(parsed_strike, current_price, parsed_type):
                    pass
                else:
                    # Allow closest strike on the correct side if no ITM/ATM strike exists
                    contracts = _fetch_chain(underlying=underlying, expiry=exp_date, opt_type=parsed_type)
                    strikes = [float(c.get("strike", 0) or 0) for c in contracts if c.get("strike") is not None]
                    target = _closest_strike(strikes, current_price, parsed_type)
                    if target is None or float(parsed_strike) != float(target):
                        if parsed_type == "call":
                            return (
                                f"BLOCKED: {symbol} strike ${parsed_strike:.2f} is outside the ITM/ATM rule.\n"
                                f"Allowed: strike ≤ ${current_price:.2f} (closest ITM/ATM)."
                            )
                        return (
                            f"BLOCKED: {symbol} strike ${parsed_strike:.2f} is outside the ITM/ATM rule.\n"
                            f"Allowed: strike ≥ ${current_price:.2f} (closest ITM/ATM)."
                        )
        except Exception:
            pass  # price fetch failed — don't block, proceed

    provider = get_alpaca()
    try:
        result = provider.submit_option_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            limit_price=limit_price,
        )
    except Exception as e:
        logger.error("Option order failed: %s", e)
        return f"Error placing option order: {e}"

    # Parse premium from limit_price or estimate from order
    premium = limit_price if limit_price else 0.0
    max_loss = premium * 100 * qty if side == "buy" and premium > 0 else None

    # Extract strike + type from OCC symbol for breakeven calculation
    # OCC: TICKER(1-6) + YYMMDD(6) + C/P(1) + STRIKE×1000(8)
    try:
        m = re.match(r'^([A-Z]{1,6})(\d{6})([CP])(\d{8})$', symbol)
        if m:
            strike = int(m.group(4)) / 1000
            opt_type = "call" if m.group(3) == "C" else "put"
            if side == "buy" and premium > 0:
                if opt_type == "call":
                    breakeven = strike + premium
                else:
                    breakeven = strike - premium
            else:
                breakeven = None
        else:
            strike, opt_type, breakeven = None, None, None
    except Exception:
        strike, opt_type, breakeven = None, None, None

    lines = [
        f"EXECUTED: {symbol}",
        f"Side: {side.upper()} | Qty: {qty} contract(s) | Type: {order_type.upper()}",
    ]
    if limit_price:
        lines.append(f"Limit Price: ${limit_price:.2f}/contract")
    if max_loss is not None:
        lines.append(f"Max Loss (premium): ${max_loss:,.2f}")
    if dte is not None:
        lines.append(f"DTE: {dte} day(s)")
    if iv is not None:
        lines.append(f"IV: {iv * 100:.1f}%")
    if breakeven is not None:
        lines.append(f"Breakeven at expiry: ${breakeven:.2f}")
    lines.append(f"Conviction: {confidence:.0%}")
    if rule_result and rule_result.details:
        alpha = rule_result.details.get("alpha")
        models = rule_result.details.get("alpha_models")
        if alpha or models:
            line = f"Alpha Gate: {alpha or 'n/a'}"
            if models:
                line += f" | {models}"
            lines.append(line)
    lines.append(f"Reasoning recorded.")

    # Store in Nous memory (structured for journal)
    try:
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        from ...nous.client import NousClient
        from ...core.config import load_config

        def _order_attr(o, key):
            if isinstance(o, dict):
                return o.get(key)
            return getattr(o, key, None)

        filled_px = _order_attr(result, "filled_avg_price")
        entry_px = float(limit_price or filled_px or 0)
        size_usd = entry_px * 100 * qty if entry_px else 0
        body = {
            "text": f"Thesis: {reasoning}",
            "signals": {
                "action": "open",
                "symbol": symbol,
                "side": "long" if side == "buy" else "short",
                "entry": entry_px,
                "size_usd": size_usd,
                "trade_type": "options",
                "opened_at": _dt.now(_tz.utc).isoformat(),
            },
        }
        cfg = load_config()
        nous = NousClient(cfg.nous.url)
        nous.create_node(
            type="concept",
            subtype="custom:trade_entry",
            title=f"Option Entry {symbol}",
            body=_json.dumps(body),
        )
    except Exception as e:
        logger.debug("Nous store failed (non-critical): %s", e)

    return "\n".join(lines)


def handle_close_option(symbol: str, reasoning: str) -> str:
    if not _OCC_RE.match(symbol.upper()):
        return f"Error: '{symbol}' is not a valid OCC option symbol."

    symbol = symbol.upper()
    provider = get_alpaca()

    try:
        close_result = provider.close_option_position(symbol)
    except Exception as e:
        logger.error("Close option failed: %s", e)
        return f"Error closing option position: {e}"

    # Store in Nous memory (structured for journal)
    try:
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        from ...nous.client import NousClient
        from ...core.config import load_config

        def _order_attr(o, key):
            if isinstance(o, dict):
                return o.get(key)
            return getattr(o, key, None)

        order = close_result.get("order") if isinstance(close_result, dict) else close_result
        entry_px = float(close_result.get("entry_price", 0) or 0) if isinstance(close_result, dict) else 0
        qty = int(close_result.get("qty", 1) or 1) if isinstance(close_result, dict) else 1
        exit_px = float(_order_attr(order, "filled_avg_price") or 0)
        pnl_usd = (exit_px - entry_px) * 100 * qty if entry_px and exit_px else 0.0
        pnl_pct = ((exit_px - entry_px) / entry_px * 100) if entry_px and exit_px else 0.0

        body = {
            "text": f"Closed: {symbol}. Reasoning: {reasoning}",
            "signals": {
                "action": "close",
                "symbol": symbol,
                "side": "long",
                "entry": entry_px,
                "exit": exit_px,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "close_type": "full",
                "size_usd": entry_px * 100 * qty if entry_px else 0,
                "trade_type": "options",
                "opened_at": _dt.now(_tz.utc).isoformat(),
            },
        }
        cfg = load_config()
        nous = NousClient(cfg.nous.url)
        nous.create_node(
            type="concept",
            subtype="custom:trade_close",
            title=f"Option Close {symbol}",
            body=_json.dumps(body),
        )
    except Exception as e:
        logger.debug("Nous store failed (non-critical): %s", e)

    return f"CLOSED: {symbol} option position. Reasoning: {reasoning}"


# ── Registration ──────────────────────────────────────────────────────────────

def register(registry) -> None:
    registry.register(Tool(
        name=GET_CHAIN_TOOL_DEF["name"],
        description=GET_CHAIN_TOOL_DEF["description"],
        parameters=GET_CHAIN_TOOL_DEF["parameters"],
        handler=handle_get_options_chain,
    ))
    registry.register(Tool(
        name=EXECUTE_OPTION_TOOL_DEF["name"],
        description=EXECUTE_OPTION_TOOL_DEF["description"],
        parameters=EXECUTE_OPTION_TOOL_DEF["parameters"],
        handler=handle_execute_option_trade,
    ))
    registry.register(Tool(
        name=CLOSE_OPTION_TOOL_DEF["name"],
        description=CLOSE_OPTION_TOOL_DEF["description"],
        parameters=CLOSE_OPTION_TOOL_DEF["parameters"],
        handler=handle_close_option,
    ))
