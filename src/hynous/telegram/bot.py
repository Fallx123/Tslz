"""Telegram Bot - Command + Chat relay for Hynous."""

from __future__ import annotations

import atexit
import json
import logging
import threading
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_bot: "HynousTelegramBot | None" = None


class HynousTelegramBot:
    """Simple Telegram long-polling bot using Bot API over HTTPS."""

    def __init__(self, agent, config):
        self.agent = agent
        self.config = config
        self.token = config.telegram.token
        self._running = False
        self._thread: threading.Thread | None = None
        self._offset = 0

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hynous-telegram")
        self._thread.start()
        logger.info("Telegram bot thread started")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("Telegram bot stopped")

    def _api(self, method: str, payload: dict | None = None, timeout: int = 30) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = None
        headers = {}
        if payload is not None:
            data = urllib.parse.urlencode(payload).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _send(self, chat_id: int, text: str) -> None:
        if not text:
            return
        limit = 3900
        chunks = [text[i:i + limit] for i in range(0, len(text), limit)]
        for chunk in chunks:
            try:
                self._api("sendMessage", {"chat_id": str(chat_id), "text": chunk})
            except Exception as e:
                logger.debug("Telegram send failed: %s", e)

    def _is_allowed(self, message: dict) -> bool:
        cfg = self.config.telegram
        frm = message.get("from") or {}
        chat = message.get("chat") or {}
        user_id = int(frm.get("id", 0) or 0)
        chat_id = int(chat.get("id", 0) or 0)

        if cfg.allowed_user_ids and user_id not in cfg.allowed_user_ids:
            return False
        if cfg.allowed_chat_ids and chat_id not in cfg.allowed_chat_ids:
            return False
        return True

    def _format_status(self) -> str:
        from ..intelligence.daemon import get_active_daemon

        d = get_active_daemon()
        if d is None or not d.is_running:
            return "Daemon: OFF"

        st = d.status
        prices = st.get("snapshot", {}).get("prices", {}) or {}
        symbols = ", ".join(f"{k} ${v:,.2f}" for k, v in list(prices.items())[:6]) if prices else "none"
        return (
            "Daemon: ON\n"
            f"Wakes: {st.get('wake_count', 0)} | Polls: {st.get('polls', 0)}\n"
            f"Watchpoints: {st.get('watchpoint_fires', 0)} | Fills: {st.get('fill_fires', 0)}\n"
            f"Daily PnL: ${st.get('daily_pnl', 0):,.2f}\n"
            f"Scanning: {symbols}"
        )

    def _format_portfolio(self) -> str:
        from ..data.providers import get_trading_provider

        provider = get_trading_provider(self.config)
        state = provider.get_user_state()
        equity = float(state.get("account_value", 0) or 0)
        upnl = float(state.get("unrealized_pnl", 0) or 0)
        lines = [f"Equity: ${equity:,.2f}", f"Unrealized: ${upnl:,.2f}", ""]
        positions = state.get("positions", []) or []
        if not positions:
            lines.append("No open positions")
            return "\n".join(lines)
        lines.append("Positions:")
        for p in positions[:15]:
            lines.append(
                f"- {p.get('coin','?')} {str(p.get('side','')).upper()} "
                f"@ ${float(p.get('entry_px',0) or 0):,.2f} -> ${float(p.get('mark_px',0) or 0):,.2f} "
                f"({float(p.get('return_pct',0) or 0):+.2f}%)"
            )
        return "\n".join(lines)

    def _format_pnl(self) -> str:
        from ..intelligence.daemon import get_active_daemon
        from ..data.providers import get_trading_provider

        provider = get_trading_provider(self.config)
        state = provider.get_user_state()
        d = get_active_daemon()
        daily = d.daily_realized_pnl if d else 0.0
        return (
            f"Equity: ${float(state.get('account_value', 0) or 0):,.2f}\n"
            f"Unrealized: ${float(state.get('unrealized_pnl', 0) or 0):,.2f}\n"
            f"Daily Realized: ${daily:,.2f}"
        )

    def _format_trades(self) -> str:
        from ..core.daemon_log import get_events

        events = [e for e in get_events(limit=80) if e.get("type") in ("fill", "profit")]
        if not events:
            return "No recent trade/fill events"
        lines = ["Recent trade events:"]
        for e in events[:12]:
            lines.append(f"- [{e.get('type')}] {e.get('title','')}: {e.get('detail','')[:120]}")
        return "\n".join(lines)

    def _format_scan(self) -> str:
        from ..core.daemon_log import get_events

        events = get_events(limit=12)
        if not events:
            return "No recent daemon events"
        lines = ["Recent daemon activity:"]
        for e in events:
            lines.append(f"- [{e.get('type')}] {e.get('title','')}")
        return "\n".join(lines)

    def _format_settings(self) -> str:
        from ..core.trading_settings import get_trading_settings

        ts = get_trading_settings()
        return (
            "Trading settings:\n"
            f"- max_open_positions: {ts.max_open_positions}\n"
            f"- max_daily_loss_usd: {ts.max_daily_loss_usd}\n"
            f"- options_only: {ts.options_only}\n"
            f"- options_dte_warn_days: {ts.options_dte_warn_days}\n"
            f"- options_dte_urgent_days: {ts.options_dte_urgent_days}\n"
            f"- rules_enabled: {ts.rules_enabled}\n"
            f"- alpha_enabled: {ts.rules_alpha_enabled}"
        )

    def _daemon_on(self) -> str:
        from ..intelligence.daemon import get_active_daemon, Daemon

        d = get_active_daemon()
        if d is not None and d.is_running:
            return "Daemon already ON"
        d = Daemon(self.agent, self.config)
        d.start()
        return "Daemon started"

    def _daemon_off(self) -> str:
        from ..intelligence.daemon import get_active_daemon

        d = get_active_daemon()
        if d is None or not d.is_running:
            return "Daemon already OFF"
        d.stop()
        return "Daemon stopped"

    @staticmethod
    def _help_text() -> str:
        return (
            "Commands:\n"
            "/status - daemon status and scan snapshot\n"
            "/scan - recent daemon activity\n"
            "/portfolio - account + open positions\n"
            "/pnl - equity/unrealized/daily realized PnL\n"
            "/trades - recent fill/profit events\n"
            "/settings - key trading settings\n"
            "/daemon_on - start daemon\n"
            "/daemon_off - stop daemon\n"
            "/chat <message> - chat with agent\n"
            "/help - this menu"
        )

    def _handle_message(self, message: dict) -> None:
        if not self._is_allowed(message):
            return

        chat_id = int((message.get("chat") or {}).get("id", 0) or 0)
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return

        try:
            if text in ("/start", "/help"):
                self._send(chat_id, self._help_text())
                return
            if text.startswith("/status"):
                self._send(chat_id, self._format_status())
                return
            if text.startswith("/scan"):
                self._send(chat_id, self._format_scan())
                return
            if text.startswith("/portfolio"):
                self._send(chat_id, self._format_portfolio())
                return
            if text.startswith("/pnl"):
                self._send(chat_id, self._format_pnl())
                return
            if text.startswith("/trades"):
                self._send(chat_id, self._format_trades())
                return
            if text.startswith("/settings"):
                self._send(chat_id, self._format_settings())
                return
            if text.startswith("/daemon_on"):
                self._send(chat_id, self._daemon_on())
                return
            if text.startswith("/daemon_off"):
                self._send(chat_id, self._daemon_off())
                return

            if text.startswith("/chat "):
                prompt = text[6:].strip()
            elif text.startswith("/"):
                self._send(chat_id, "Unknown command. Use /help")
                return
            else:
                if getattr(self.config.telegram, "commands_only", True):
                    self._send(chat_id, "Commands only. Use /help")
                    return
                prompt = text

            sender = (message.get("from") or {}).get("first_name") or "Telegram"
            prefixed = f"[{sender} via Telegram] {prompt}"
            response = self.agent.chat(prefixed, source="telegram")
            self._send(chat_id, response)
        except Exception as e:
            logger.error("Telegram message handler failed: %s", e)
            self._send(chat_id, f"Error: {e}")

    def _loop(self) -> None:
        while self._running:
            try:
                res = self._api(
                    "getUpdates",
                    {
                        "timeout": "25",
                        "offset": str(self._offset),
                        "allowed_updates": json.dumps(["message"]),
                    },
                    timeout=35,
                )
                if not res.get("ok"):
                    time.sleep(2)
                    continue
                for upd in res.get("result", []):
                    uid = int(upd.get("update_id", 0) or 0)
                    if uid >= self._offset:
                        self._offset = uid + 1
                    msg = upd.get("message")
                    if msg:
                        self._handle_message(msg)
            except Exception as e:
                logger.debug("Telegram poll error: %s", e)
                time.sleep(2)

    def send_notification(self, title: str, wake_type: str, response: str) -> None:
        if getattr(self.config.telegram, "notify_trade_only", True):
            if wake_type.lower() not in {"fill", "trade"}:
                return
        chat_id = int(getattr(self.config.telegram, "notify_chat_id", 0) or 0)
        if not chat_id:
            return
        text = f"[{wake_type}] {title}\n\n{response}"
        self._send(chat_id, text)


def start_bot(agent, config) -> bool:
    """Start Telegram bot in background thread."""
    global _bot
    if _bot is not None and _bot.is_running:
        return True
    if not config.telegram.enabled:
        return False
    if not config.telegram.token:
        logger.warning("Telegram bot enabled but TELEGRAM_BOT_TOKEN not set")
        return False
    _bot = HynousTelegramBot(agent, config)
    _bot.start()
    atexit.register(stop_bot)
    return True


def stop_bot() -> None:
    """Stop Telegram bot."""
    global _bot
    if _bot is not None:
        _bot.stop()
    _bot = None


def notify(title: str, wake_type: str, response: str) -> None:
    """Send daemon notification to Telegram notify chat."""
    if _bot is None or not _bot.is_running:
        return
    try:
        _bot.send_notification(title, wake_type, response)
    except Exception as e:
        logger.debug("Telegram notify failed: %s", e)


def get_bot() -> HynousTelegramBot | None:
    return _bot
