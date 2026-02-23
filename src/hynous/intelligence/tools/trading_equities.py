"""
Trading Tools (Equities) — Alpaca

Provides get_account, execute_trade, close_position, modify_position for US equities.
Paper trading only by default (Alpaca paper).
"""

import logging
from datetime import datetime, timezone

from .registry import Tool
from ...data.providers.alpaca import get_provider as get_alpaca
from ...data.providers import get_market_provider
from ...core.trading_settings import get_trading_settings

logger = logging.getLogger(__name__)


ACCOUNT_TOOL_DEF = {
    "name": "get_account",
    "description": (
        "Check your Alpaca account — balance, positions, and/or orders.\n"
        "Views:\n"
        "- summary: Balance, buying power, equity overview\n"
        "- positions: Open positions with entry, mark, PnL\n"
        "- orders: All open orders\n"
        "- full: Everything at once\n"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "view": {
                "type": "string",
                "enum": ["summary", "positions", "orders", "full"],
                "description": "What to display.",
            },
            "symbol": {
                "type": "string",
                "description": "Filter to a specific symbol (e.g. AAPL, MSFT).",
            },
        },
    },
}


TRADE_TOOL_DEF = {
    "name": "execute_trade",
    "description": (
        "Execute a US equity trade via Alpaca. Every trade requires a thesis, stop loss, "
        "and take profit — no exceptions.\n\n"
        "Order types:\n"
        "- market (default)\n"
        "- limit\n\n"
        "Position sizing is AUTOMATIC from conviction if you don't pass qty/notional:\n"
        "  High (0.8+) → 30% of portfolio notional\n"
        "  Medium (0.6-0.79) → 20%\n"
        "  Below 0.6 → rejected\n\n"
        "Required parameters:\n"
        "- stop_loss: price where thesis is wrong\n"
        "- take_profit: price target\n"
        "- reasoning: full thesis\n"
        "- confidence: conviction score (0.0-1.0)\n"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Equity symbol (e.g. AAPL)"},
            "side": {"type": "string", "enum": ["long", "short"]},
            "order_type": {"type": "string", "enum": ["market", "limit"]},
            "limit_price": {"type": "number", "description": "Price for limit orders."},
            "qty": {"type": "number", "description": "Shares to trade (optional)."},
            "notional_usd": {"type": "number", "description": "Notional USD to trade (optional)."},
            "stop_loss": {"type": "number", "description": "Stop loss price."},
            "take_profit": {"type": "number", "description": "Take profit price."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string"},
        },
        "required": ["symbol", "side", "stop_loss", "take_profit", "confidence", "reasoning"],
    },
}


CLOSE_TOOL_DEF = {
    "name": "close_position",
    "description": "Close an open equity position via Alpaca.",
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "reasoning": {"type": "string"},
        },
        "required": ["symbol", "reasoning"],
    },
}


MODIFY_TOOL_DEF = {
    "name": "modify_position",
    "description": (
        "Modify an open equity position by updating stop loss and/or take profit.\n"
        "This cancels existing open orders for the symbol and submits a new bracket.\n"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "stop_loss": {"type": "number"},
            "take_profit": {"type": "number"},
            "reasoning": {"type": "string"},
        },
        "required": ["symbol", "reasoning"],
    },
}


def handle_get_account(view: str | None = None, symbol: str | None = None) -> str:
    provider = get_alpaca()
    acct = provider.get_account()

    if symbol:
        symbol = symbol.upper()

    if view is None:
        view = "full"

    sections = []
    if view in ("summary", "full"):
        sections.append(
            "Account:\n"
            f"  Equity: ${float(acct.equity):,.2f}\n"
            f"  Buying Power: ${float(acct.buying_power):,.2f}\n"
            f"  Cash: ${float(acct.cash):,.2f}"
        )

    if view in ("positions", "full"):
        positions = provider.get_user_state()["positions"]
        if symbol:
            positions = [p for p in positions if p["coin"] == symbol]
        if not positions:
            sections.append("Positions:\n  None")
        else:
            lines = ["Positions:"]
            for p in positions:
                lines.append(
                    f"  {p['coin']} {p['side'].upper()} {p['size']:.4g} @ ${p['entry_px']:.2f} "
                    f"-> ${p['mark_px']:.2f} ({p['return_pct']:+.2f}%)"
                )
            sections.append("\n".join(lines))

    if view in ("orders", "full"):
        orders = provider.get_open_orders(symbol=symbol)
        if not orders:
            sections.append("Orders:\n  None")
        else:
            lines = ["Orders:"]
            for o in orders:
                lines.append(
                    f"  {o.symbol} {o.side.upper()} {o.qty} {o.type.upper()} "
                    f"{'@ ' + str(o.limit_price) if o.limit_price else ''} (status: {o.status})"
                )
            sections.append("\n".join(lines))

    return "\n\n".join(sections)


def handle_execute_trade(
    symbol: str,
    side: str,
    order_type: str = "market",
    limit_price: float | None = None,
    qty: float | None = None,
    notional_usd: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    confidence: float | None = None,
    reasoning: str | None = None,
) -> str:
    provider = get_alpaca()
    market = get_market_provider()
    ts = get_trading_settings()

    symbol = symbol.upper()
    is_buy = side == "long"

    if getattr(ts, "options_only", False):
        return "BLOCKED: options_only is enabled. Use execute_option_trade."

    if stop_loss is None or take_profit is None:
        return "Error: stop_loss and take_profit are required."
    if confidence is None:
        return "Error: confidence is required."

    price = market.get_price(symbol)
    if not price:
        return f"Error: could not get price for {symbol}."

    # Rules gate — deterministic checks before any trade
    rule_result = None
    try:
        from ...core.trade_rules import evaluate_trade_rules
        rule_result = evaluate_trade_rules(symbol, side)
        if not rule_result.allowed:
            detail = "; ".join(rule_result.reasons)
            extras = " | ".join(f"{k}={v}" for k, v in rule_result.details.items() if v)
            if extras:
                detail += f" ({extras})"
            return f"BLOCKED by rules: {detail}"
    except Exception:
        pass

    if limit_price is not None:
        limit_price = round(float(limit_price), 2)
    stop_loss = round(float(stop_loss), 2)
    take_profit = round(float(take_profit), 2)
    ref_price = limit_price if order_type == "limit" and limit_price else price
    if is_buy and stop_loss >= ref_price:
        return "Error: stop_loss must be below entry for a long."
    if (not is_buy) and stop_loss <= ref_price:
        return "Error: stop_loss must be above entry for a short."
    if is_buy and take_profit <= ref_price:
        return "Error: take_profit must be above entry for a long."
    if (not is_buy) and take_profit >= ref_price:
        return "Error: take_profit must be below entry for a short."

    if confidence < ts.tier_pass_threshold:
        return f"Conviction too low ({confidence:.0%}). Set a watchpoint and revisit."

    acct = provider.get_account()
    portfolio = float(acct.equity or 0)

    if notional_usd is None and qty is None:
        if confidence >= 0.8:
            notional_usd = portfolio * (ts.tier_high_margin_pct / 100)
            tier = "High"
        elif confidence >= 0.6:
            notional_usd = portfolio * (ts.tier_medium_margin_pct / 100)
            tier = "Medium"
        else:
            notional_usd = portfolio * (ts.tier_speculative_margin_pct / 100)
            tier = "Speculative"
    else:
        tier = "Custom"

    if notional_usd is not None:
        qty = round(notional_usd / ref_price, 4)

    if not qty or qty <= 0:
        return "Error: invalid qty after sizing."

    if notional_usd and notional_usd > ts.max_position_usd:
        return f"Error: ${notional_usd:,.0f} exceeds safety cap of ${ts.max_position_usd:,.0f}."

    # Risk/Reward and portfolio risk checks
    risk_per_share = abs(ref_price - stop_loss)
    reward_per_share = abs(take_profit - ref_price)
    rr = reward_per_share / risk_per_share if risk_per_share > 0 else 0
    if rr < ts.rr_floor_reject:
        return f"REJECTED: R:R is {rr:.2f}:1. Minimum is {ts.rr_floor_warn}:1."

    loss_at_stop = risk_per_share * qty
    portfolio_risk_pct = (loss_at_stop / portfolio) * 100 if portfolio else 0
    if portfolio_risk_pct > ts.portfolio_risk_cap_reject:
        return (
            f"REJECTED: Risks {portfolio_risk_pct:.1f}% of portfolio at stop. "
            f"Max allowed {ts.portfolio_risk_cap_reject:.0f}%."
        )

    try:
        if order_type == "limit":
            if limit_price is None:
                return "Error: limit_price required for limit orders."
            provider.submit_limit_bracket(
                symbol=symbol,
                qty=qty,
                side=side,
                limit_price=limit_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
        else:
            provider.submit_market_bracket(
                symbol=symbol,
                qty=qty,
                side=side,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
    except Exception as e:
        logger.error("Alpaca order error: %s", e)
        return f"Error executing trade: {e}"

    return (
        f"EXECUTED: {symbol} {side.upper()} | Qty {qty:.4g}\n"
        f"Entry Ref: ${ref_price:.2f} | SL ${stop_loss:.2f} | TP ${take_profit:.2f}\n"
        f"Conviction: {confidence:.0%} ({tier}) | R:R {rr:.2f}:1\n"
        f"{_format_alpha_line(rule_result)}"
        f"Reasoning received."
    )


def _format_alpha_line(rule_result) -> str:
    if not rule_result or not getattr(rule_result, "details", None):
        return ""
    alpha = rule_result.details.get("alpha")
    models = rule_result.details.get("alpha_models")
    if not alpha and not models:
        return ""
    line = f"Alpha Gate: {alpha}"
    if models:
        line += f" | {models}"
    return line + "\n"


def handle_close_position(symbol: str, reasoning: str) -> str:
    provider = get_alpaca()
    symbol = symbol.upper()
    try:
        provider.close_position(symbol)
    except Exception as e:
        return f"Error closing position: {e}"
    return f"CLOSED: {symbol}. Reasoning: {reasoning}"


def handle_modify_position(
    symbol: str,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    reasoning: str | None = None,
) -> str:
    provider = get_alpaca()
    market = get_market_provider()
    symbol = symbol.upper()

    if stop_loss is None or take_profit is None:
        return "Error: provide both stop_loss and take_profit to reset exit orders."
    stop_loss = round(float(stop_loss), 2)
    take_profit = round(float(take_profit), 2)

    price = market.get_price(symbol)
    if not price:
        return f"Error: could not get price for {symbol}."

    # Cancel existing open orders for symbol
    try:
        orders = provider.get_open_orders(symbol=symbol)
        for o in orders:
            try:
                provider._trading.cancel_order_by_id(o.id)
            except Exception:
                pass
    except Exception:
        pass

    # Re-submit OCO exits on current position size
    try:
        positions = provider.get_user_state()["positions"]
        pos = next((p for p in positions if p["coin"] == symbol), None)
        if not pos:
            return f"No open position for {symbol}."
        qty = pos["size"]
        side = pos["side"]
        provider.submit_oco_exit(
            symbol=symbol,
            qty=qty,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
    except Exception as e:
        return f"Error modifying position: {e}"

    return f"UPDATED: {symbol} SL ${stop_loss:.2f} TP ${take_profit:.2f}. Reasoning: {reasoning}"


def register(registry) -> None:
    registry.register(Tool(
        name=ACCOUNT_TOOL_DEF["name"],
        description=ACCOUNT_TOOL_DEF["description"],
        parameters=ACCOUNT_TOOL_DEF["parameters"],
        handler=handle_get_account,
    ))
    registry.register(Tool(
        name=TRADE_TOOL_DEF["name"],
        description=TRADE_TOOL_DEF["description"],
        parameters=TRADE_TOOL_DEF["parameters"],
        handler=handle_execute_trade,
    ))
    registry.register(Tool(
        name=CLOSE_TOOL_DEF["name"],
        description=CLOSE_TOOL_DEF["description"],
        parameters=CLOSE_TOOL_DEF["parameters"],
        handler=handle_close_position,
    ))
    registry.register(Tool(
        name=MODIFY_TOOL_DEF["name"],
        description=MODIFY_TOOL_DEF["description"],
        parameters=MODIFY_TOOL_DEF["parameters"],
        handler=handle_modify_position,
    ))
