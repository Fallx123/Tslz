"""
TradingView Webhook Server

Receives TradingView alerts and forwards them to the Hynous agent.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from ..core.config import load_config
from ..core.persistence import append_wake
from ..intelligence import Agent

logger = logging.getLogger(__name__)

app = FastAPI(title="Hynous Webhook Server")

_agent: Agent | None = None
_agent_lock = threading.Lock()


def _get_agent() -> Agent:
    global _agent
    if _agent is not None:
        return _agent
    with _agent_lock:
        if _agent is not None:
            return _agent
        _agent = Agent()
        return _agent


def _require_token(token: str | None) -> None:
    cfg = load_config()
    expected = cfg.webhook.token if cfg.webhook else ""
    if expected:
        if not token or token != expected:
            raise HTTPException(status_code=401, detail="Invalid webhook token")


def _parse_payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {"message": raw}
    return {"message": str(raw)}


def _build_agent_message(payload: dict) -> str:
    symbol = payload.get("symbol") or payload.get("ticker")
    action = payload.get("action") or payload.get("side")
    price = payload.get("price")
    stop_loss = payload.get("stop_loss") or payload.get("sl")
    take_profit = payload.get("take_profit") or payload.get("tp")
    confidence = payload.get("confidence")
    timeframe = payload.get("timeframe")
    strategy = payload.get("strategy") or payload.get("strategy_name")
    note = payload.get("note") or payload.get("message")

    lines = ["[TRADINGVIEW ALERT]"]
    if symbol:
        lines.append(f"Symbol: {symbol}")
    if action:
        lines.append(f"Action: {action}")
    if price:
        lines.append(f"Price: {price}")
    if stop_loss:
        lines.append(f"Stop Loss: {stop_loss}")
    if take_profit:
        lines.append(f"Take Profit: {take_profit}")
    if confidence is not None:
        lines.append(f"Confidence: {confidence}")
    if timeframe:
        lines.append(f"Timeframe: {timeframe}")
    if strategy:
        lines.append(f"Strategy: {strategy}")
    if note:
        lines.append(f"Note: {note}")

    lines.append(
        "You MUST compute the best entry, stop loss, and take profit using current data "
        "plus any historical support/resistance in memory. Use get_support_resistance if needed. "
        "Then execute_trade with SL/TP and conviction. Do not ask questions."
    )
    return "\n".join(lines)


def _process_alert(payload: dict) -> None:
    agent = _get_agent()
    message = _build_agent_message(payload)
    try:
        response = agent.chat(message, source="webhook:tradingview")
    except Exception as e:
        logger.error("Webhook processing failed: %s", e)
        response = f"Webhook processing error: {e}"

    # Enforce trade execution if it didn't happen
    if not agent.last_chat_had_trade_tool():
        correction = (
            "[SYSTEM] You must execute_trade for this TradingView alert. "
            "Compute entry/SL/TP from current data and stored support/resistance, "
            "then call execute_trade now. No refusal."
        )
        try:
            response = agent.chat(correction, skip_snapshot=False, source="webhook:trade_enforce")
        except Exception as e:
            logger.error("Webhook enforcement failed: %s", e)

    title = payload.get("symbol") or payload.get("ticker") or "TradingView Alert"
    append_wake("Webhook", title, response)


@app.post("/webhook/tradingview")
async def tradingview_webhook(
    request: Request,
    token: str | None = None,
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
):
    raw = await request.body()
    payload = _parse_payload(raw)

    # Accept token from header, query param, or payload
    payload_token = None
    if isinstance(payload, dict):
        payload_token = payload.get("token")
    _require_token(x_webhook_token or token or payload_token)

    # Fire-and-forget processing so TradingView gets fast 200 response
    threading.Thread(target=_process_alert, args=(payload,), daemon=True).start()

    return {
        "status": "ok",
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
