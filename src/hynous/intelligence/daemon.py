"""
Hynous Daemon — Background Watchdog + Curiosity Engine + Periodic Review

The daemon runs in a background thread, polling market data and evaluating
conditions — all zero LLM tokens. When something interesting happens (a
watchpoint triggers, curiosity accumulates, or it's time for a review), it
wakes the agent with assembled context.

Three-tier token model:
  Tier 1: Python polling (0 tokens) — prices
  Tier 2: Reserved for future quick-gate filtering (~500 tokens)
  Tier 3: Full agent wake (~10-15K tokens) — tool use, reasoning, memory

Design: storm-014 (Memory-Triggered Watchdog & Curiosity-Driven Learning)

Usage:
    from hynous.intelligence.daemon import Daemon
    daemon = Daemon(agent, config)
    daemon.start()   # Background thread
    daemon.stop()    # Graceful shutdown
"""

import json
import logging
import queue as _queue_module
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..core.config import Config
from ..core.daemon_log import log_event, DaemonEvent, flush as flush_daemon_log

logger = logging.getLogger(__name__)

# Module-level reference so trading tools can check circuit breaker
_active_daemon: "Daemon | None" = None

# Queue for daemon wake conversations → consumed by the dashboard to show in chat.
# Each item: {"type": str, "title": str, "response": str}
_daemon_chat_queue: _queue_module.Queue = _queue_module.Queue()


def get_daemon_chat_queue() -> _queue_module.Queue:
    """Get the queue of daemon wake conversations for dashboard display."""
    return _daemon_chat_queue


def _notify_discord(wake_type: str, title: str, response: str):
    """Forward daemon notification to chat bots (Discord + Telegram)."""
    try:
        from ..discord.bot import notify
        notify(title, wake_type, response)
    except Exception:
        pass
    try:
        from ..telegram.bot import notify as tg_notify
        tg_notify(title, wake_type, response)
    except Exception:
        pass


def _queue_and_persist(wake_type: str, title: str, response: str, event_type: str = ""):
    """Put wake message in dashboard queue AND persistent wake log.

    The in-memory queue gives instant UI updates when the dashboard is open.
    The persistent log ensures messages survive restarts and are available
    even if the dashboard wasn't open when the wake happened.
    """
    item = {"type": wake_type, "title": title, "response": response}
    if event_type:
        item["event_type"] = event_type
    _daemon_chat_queue.put(item)
    try:
        from ..core.persistence import append_wake
        append_wake(wake_type, title, response)
    except Exception:
        pass


# Regex for detecting narrated trade entries in text (without actual tool calls).
# Matches trade-specific phrases, NOT generic "entering" (which could be
# "shorts entering the market" etc.). Patterns:
#   "Entering SOL long"  "Entering BTC micro short"  "going long"
#   "taking a short"  "Conviction: 0.68 — entering."
_TRADE_NARRATION_RE = re.compile(
    r'(?:'
    r'(?:entering|opening)\s+\w+\s+(?:long|short|micro|macro)'
    r'|going\s+(?:long|short)'
    r'|taking\s+a\s+(?:long|short)'
    r'|\u2014\s*entering\.'
    r')',
    re.IGNORECASE,
)

# OCC options symbol (e.g. AAPL250321C00200000)
_OPTION_OCC_RE = re.compile(r'^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$')


def _check_narrated_trade(response: str, agent) -> str | None:
    """Detect and fix narrated trades — agent said entry words without calling execute_trade.

    If detected, sends a follow-up message telling the agent to actually execute the trade.
    Returns the follow-up response if a correction was made, None otherwise.
    """
    if not response or not _TRADE_NARRATION_RE.search(response):
        return None
    if agent.last_chat_had_trade_tool():
        return None  # Tool was called — text is just confirmation, all good

    logger.warning(
        "NARRATED TRADE DETECTED — sending correction. Response: %s",
        response[:300],
    )

    # Send follow-up forcing tool execution
    correction = (
        "[SYSTEM] You just said you entered a trade in TEXT, but you never called "
        "execute_trade. Text is NOT execution — no position was opened. "
        "If you still want this trade, call execute_trade NOW with the exact parameters "
        "you described. If you changed your mind, say so clearly."
    )
    try:
        followup = agent.chat(correction, skip_snapshot=True, max_tokens=768, source="daemon:narration_fix")
        return followup
    except Exception as e:
        logger.error("Narration fix failed: %s", e)
        return None


def get_active_daemon() -> "Daemon | None":
    """Get the currently running daemon instance (if any).

    Used by trading tools to check circuit breaker and position limits.
    """
    return _active_daemon


class MarketSnapshot:
    """Lightweight cache of current market data for trigger evaluation.

    Updated by the daemon's polling loop. Evaluated against watchpoint
    triggers with zero LLM tokens.
    """

    def __init__(self):
        self.prices: dict[str, float] = {}       # symbol → mid price
        self.last_price_poll: float = 0           # Unix timestamp
        self.last_deriv_poll: float = 0           # Unix timestamp

    def price_summary(self, symbols: list[str]) -> str:
        """Format a compact price summary for wake messages."""
        lines = []
        for sym in symbols:
            price = self.prices.get(sym)
            if price:
                lines.append(f"{sym}: ${price:,.0f}")
        return "\n".join(lines)


class Daemon:
    """Background autonomous loop for Hynous.

    Responsibilities:
    1. Poll market data at intervals (equities prices)
    2. Evaluate watchpoint triggers against cached data
    3. Count curiosity items and trigger learning sessions
    4. Periodically wake the agent for market reviews
    5. Coordinate with user chat via agent._chat_lock
    """

    def __init__(self, agent, config: Config):
        self.agent = agent
        self.config = config
        self.snapshot = MarketSnapshot()

        self._running = False
        self._thread: threading.Thread | None = None
        self._market_open_cached: bool = False
        self._market_open_cache_until: float = 0.0

        # Cached provider references (avoid re-importing in every method)
        self._hl_provider = None
        self._nous_client = None

        # Pre-fetched deep market data for briefing injection
        from .briefing import DataCache
        self._data_cache = DataCache()

        # Timing trackers
        self._last_review: float = 0
        self._last_curiosity_check: float = 0
        self._last_learning_session: float = 0  # Cooldown to prevent runaway loop
        self._last_decay_cycle: float = 0
        self._last_conflict_check: float = 0
        self._last_health_check: float = 0
        self._last_embedding_backfill: float = 0
        self._last_sr_refresh: float = 0
        self._last_retrain_check: float = 0

        # Nous health state
        self._nous_healthy: bool = True

        # Data-change gate: watchpoints only checked when data is fresh
        self._data_changed: bool = False

        # Position tracking (fill detection)
        self._prev_positions: dict[str, dict] = {}    # coin → {side, size, entry_px}
        self._tracked_triggers: dict[str, list] = {}  # coin → trigger orders snapshot
        self._last_fill_check: float = 0
        self._fill_fires: int = 0
        self._processed_fills: set[str] = set()       # Fill hashes already processed

        # Profit level tracking: {coin: {tier: last_alert_timestamp}}
        self._profit_alerts: dict[str, dict[str, float]] = {}
        self._profit_sides: dict[str, str] = {}  # coin → side (detect flips)
        self._peak_roe: dict[str, float] = {}  # coin → max ROE % seen during hold
        self._options_dte_alerts: dict[str, float] = {}
        self._last_options_dte_check: float = 0

        # Position type registry: {coin: {"type": "micro"|"macro", "entry_time": float}}
        # Populated by trading tool via register_position_type(), inferred on restart
        self._position_types: dict[str, dict] = {}

        # Risk guardrails (circuit breaker)
        self._daily_realized_pnl: float = 0.0
        self._daily_reset_date: str = ""    # YYYY-MM-DD UTC
        self._trading_paused: bool = False
        self._daily_pnl_path = self._resolve_storage_path("daily_pnl.json")

        # Trade activity tracking (for conviction system awareness)
        self._entries_today: int = 0
        self._micro_entries_today: int = 0
        self._entries_this_week: int = 0
        self._last_entry_time: float = 0
        self._last_close_time: float = 0

        # Wake rate limiting
        self._wake_timestamps: list[float] = []
        self._last_wake_time: float = 0

        # Cached counts (for snapshot, avoids re-querying Nous)
        self._active_watchpoint_count: int = 0
        self._active_thesis_count: int = 0
        self._pending_curiosity_count: int = 0

        # Heartbeat — updated every loop iteration, checked by dashboard watchdog
        self._heartbeat: float = time.time()

        # Coach cross-wake state
        self._pending_thoughts: list[str] = []       # Haiku questions (max 3)
        self._wake_fingerprints: list[frozenset] = []  # Last 5 tool+mutation fingerprints

        # Stats
        self.wake_count: int = 0
        self.watchpoint_fires: int = 0
        self.learning_sessions: int = 0
        self.decay_cycles_run: int = 0
        self.conflict_checks: int = 0
        self.health_checks: int = 0
        self.embedding_backfills: int = 0
        self.polls: int = 0
        self._review_count: int = 0

    # ================================================================
    # Cached Provider Access
    # ================================================================

    def _get_provider(self):
        """Get cached trading provider for current market."""
        if self._hl_provider is None:
            from ..data.providers import get_trading_provider
            self._hl_provider = get_trading_provider(config=self.config)
        return self._hl_provider

    def _get_nous(self):
        """Get cached Nous client."""
        if self._nous_client is None:
            from ..nous.client import get_client
            self._nous_client = get_client()
        return self._nous_client

    # ================================================================
    # Lifecycle
    # ================================================================

    def start(self):
        """Start the daemon loop in a background thread."""
        if self._running:
            return
        global _active_daemon
        _active_daemon = self
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="hynous-daemon",
        )
        self._thread.start()
        logger.info("Daemon started (price=%ds, deriv=%ds, review=%ds, curiosity=%ds, "
                     "decay=%ds, conflicts=%ds, health=%ds, backfill=%ds)",
                     self.config.daemon.price_poll_interval,
                     self.config.daemon.deriv_poll_interval,
                     self.config.daemon.periodic_interval,
                     self.config.daemon.curiosity_check_interval,
                     self.config.daemon.decay_interval,
                     self.config.daemon.conflict_check_interval,
                     self.config.daemon.health_check_interval,
                     self.config.daemon.embedding_backfill_interval)

    def stop(self):
        """Stop the daemon loop gracefully."""
        global _active_daemon
        self._running = False
        if self._thread:
            self._thread.join(timeout=15)
            self._thread = None
        if _active_daemon is self:
            _active_daemon = None
        flush_daemon_log()  # Persist any buffered events
        try:
            from ..core.equity_tracker import flush as flush_equity
            flush_equity()
        except Exception:
            pass
        logger.info("Daemon stopped (wakes=%d, watchpoints=%d, learning=%d)",
                     self.wake_count, self.watchpoint_fires, self.learning_sessions)

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def trading_paused(self) -> bool:
        """Whether the circuit breaker has paused trading."""
        return self._trading_paused

    @property
    def daily_realized_pnl(self) -> float:
        """Today's realized PnL (resets at UTC midnight)."""
        return self._daily_realized_pnl

    def record_trade_entry(self):
        """Record a new trade entry for activity tracking (called by trading tool)."""
        self._check_daily_reset()
        self._entries_today += 1
        self._entries_this_week += 1
        self._last_entry_time = time.time()
        self._persist_daily_pnl()

    def record_micro_entry(self):
        """Record a micro trade entry (called by trading tool when trade_type='micro')."""
        self._micro_entries_today += 1
        self._persist_daily_pnl()

    def register_position_type(self, coin: str, trade_type: str = "macro"):
        """Register trade type for a position. Called by trading tool on entry."""
        self._position_types[coin] = {
            "type": trade_type,
            "entry_time": time.time(),
        }
        self._persist_position_types()

    def get_position_type(self, coin: str) -> dict:
        """Get trade type info for a position. Infers from leverage if unregistered."""
        if coin in self._position_types:
            return self._position_types[coin]
        # Fallback: infer from leverage in _prev_positions
        prev = self._prev_positions.get(coin, {})
        leverage = prev.get("leverage", 20)
        inferred = "micro" if leverage >= 15 else "macro"
        return {"type": inferred, "entry_time": 0}

    def get_peak_roe(self, coin: str) -> float:
        """Get max favorable excursion (peak ROE %) tracked during hold."""
        return self._peak_roe.get(coin.upper(), 0.0)

    @property
    def last_trade_ago(self) -> str:
        """Human-readable time since last trade (entry or close)."""
        last = max(self._last_entry_time, self._last_close_time)
        if last == 0:
            return "never"
        elapsed = time.time() - last
        if elapsed < 3600:
            return f"{int(elapsed / 60)}m"
        elif elapsed < 86400:
            return f"{elapsed / 3600:.0f}h"
        return f"{elapsed / 86400:.0f}d"

    @property
    def status(self) -> dict:
        """Current daemon status for dashboard display."""
        return {
            "running": self.is_running,
            "wake_count": self.wake_count,
            "watchpoint_fires": self.watchpoint_fires,
            "fill_fires": self._fill_fires,
            "learning_sessions": self.learning_sessions,
            "polls": self.polls,
            "trading_paused": self._trading_paused,
            "daily_pnl": self._daily_realized_pnl,
            "snapshot": {
                "prices": dict(self.snapshot.prices),
            },
        }

    @property
    def next_review_seconds(self) -> int:
        """Seconds until next periodic review (doubled on weekends)."""
        if not self._last_review:
            return 0
        interval = self.config.daemon.periodic_interval
        if datetime.now(timezone.utc).weekday() >= 5:
            interval *= 2
        elapsed = time.time() - self._last_review
        remaining = int(interval - elapsed)
        return max(remaining, 0)

    @property
    def cooldown_remaining(self) -> int:
        """Seconds remaining in wake cooldown (0 = ready)."""
        if not self._last_wake_time:
            return 0
        cooldown = self.config.daemon.wake_cooldown_seconds
        elapsed = time.time() - self._last_wake_time
        remaining = int(cooldown - elapsed)
        return max(remaining, 0)

    @property
    def wakes_this_hour(self) -> int:
        """Number of wakes in the last 60 minutes."""
        cutoff = time.time() - 3600
        return len([t for t in self._wake_timestamps if t > cutoff])

    @property
    def reviews_until_learning(self) -> int:
        """Reviews until next learning session (every 3rd review)."""
        return 3 - (self._review_count % 3)

    @property
    def review_count(self) -> int:
        """Total periodic reviews completed."""
        return self._review_count

    @property
    def last_wake_time(self) -> float:
        """Unix timestamp of last wake."""
        return self._last_wake_time

    @property
    def micro_entries_today(self) -> int:
        """Number of micro trades entered today."""
        return self._micro_entries_today

    def _is_us_equities_session_open(self, now_ts: float | None = None) -> bool:
        """Market-open gate with Alpaca clock, fallback to Mon-Fri 06:30-13:00 PT.

        Alpaca clock handles holidays and unexpected closure days.
        """
        now = now_ts if now_ts is not None else time.time()
        if now < self._market_open_cache_until:
            return self._market_open_cached

        # Primary source of truth: Alpaca market clock.
        try:
            provider = self._get_provider()
            if hasattr(provider, "is_market_open"):
                is_open = bool(provider.is_market_open())
                self._market_open_cached = is_open
                self._market_open_cache_until = now + 60
                return is_open
        except Exception:
            pass

        # Fallback when clock is unavailable.
        now_la = datetime.fromtimestamp(now, tz=ZoneInfo("America/Los_Angeles"))
        if now_la.weekday() in (5, 6):  # Sat, Sun
            self._market_open_cached = False
            self._market_open_cache_until = now + 60
            return False
        open_t = now_la.replace(hour=6, minute=30, second=0, microsecond=0)
        close_t = now_la.replace(hour=13, minute=0, second=0, microsecond=0)
        is_open = open_t <= now_la < close_t
        self._market_open_cached = is_open
        self._market_open_cache_until = now + 60
        return is_open

    # ================================================================
    # Main Loop
    # ================================================================

    def _loop(self):
        """The daemon's heartbeat. Runs in a background thread."""
        # Startup health check — verify Nous is reachable
        self._check_health(startup=True)
        # Seed clusters if none exist
        self._seed_clusters()

        # Initial data fetch
        self._poll_prices()
        self._poll_derivatives()
        self._init_position_tracking()
        self._last_review = time.time()
        self._last_curiosity_check = time.time()
        self._last_decay_cycle = time.time()
        self._last_conflict_check = time.time()
        self._last_health_check = time.time()
        self._last_embedding_backfill = time.time()
        self._last_fill_check = time.time()
        self._load_daily_pnl()

        while self._running:
            try:
                now = time.time()
                self._heartbeat = now
                market_open = self._is_us_equities_session_open(now)

                # 0. Daily reset check (circuit breaker)
                self._check_daily_reset()

                # 1. Price polling (default every 60s)
                if now - self.snapshot.last_price_poll >= self.config.daemon.price_poll_interval:
                    self._poll_prices()

                # 1a. Fast trigger check EVERY loop (10s) for open positions
                # SL/TP must fire promptly — can't wait 60s between checks.
                # Fetches fresh prices only for position symbols (1 cheap API call).
                if market_open:
                    self._fast_trigger_check()

                # 1b. Full position tracking + profit monitoring (every 60s)
                if now - self._last_fill_check >= self.config.daemon.price_poll_interval:
                    live_positions = self._check_positions() if market_open else None
                    if market_open:
                        self._check_profit_levels(live_positions)
                    self._last_fill_check = now

                # 2. Derivatives polling (default every 300s)
                if now - self.snapshot.last_deriv_poll >= self.config.daemon.deriv_poll_interval:
                    self._poll_derivatives()

                # 2a. Options DTE monitoring (default every 300s)
                if market_open and now - self._last_options_dte_check >= self.config.daemon.deriv_poll_interval:
                    self._last_options_dte_check = now
                    self._check_options_dte()

                # 3. Check watchpoints ONLY when data has changed (not every 10s)
                if market_open and self._data_changed:
                    self._data_changed = False
                    triggered = self._check_watchpoints()
                    for wp in triggered:
                        self._wake_for_watchpoint(wp)

                # 4. Curiosity check (default every 15 min)
                if now - self._last_curiosity_check >= self.config.daemon.curiosity_check_interval:
                    self._last_curiosity_check = now
                    self._check_curiosity()

                # 5. Periodic review (1h weekdays, 2h weekends)
                review_interval = self.config.daemon.periodic_interval
                if datetime.now(timezone.utc).weekday() >= 5:  # Sat=5, Sun=6
                    review_interval *= 2
                if market_open and now - self._last_review >= review_interval:
                    self._last_review = now
                    self._wake_for_review()

                # 6. FSRS batch decay (default every 6 hours)
                if now - self._last_decay_cycle >= self.config.daemon.decay_interval:
                    self._last_decay_cycle = now
                    self._run_decay_cycle()

                # 7. Contradiction queue check (default every 30 min)
                if now - self._last_conflict_check >= self.config.daemon.conflict_check_interval:
                    self._last_conflict_check = now
                    self._check_conflicts()

                # 8. Nous health check (default every 1 hour)
                if now - self._last_health_check >= self.config.daemon.health_check_interval:
                    self._last_health_check = now
                    self._check_health()

                # 9. Embedding backfill (default every 12 hours)
                if now - self._last_embedding_backfill >= self.config.daemon.embedding_backfill_interval:
                    self._last_embedding_backfill = now
                    self._run_embedding_backfill()

                # 10. Support/Resistance refresh
                if now - self._last_sr_refresh >= self.config.daemon.support_resistance_interval:
                    self._last_sr_refresh = now
                    self._refresh_support_resistance()

                # 11. Weekly retrain
                if now - self._last_retrain_check >= 3600:
                    self._last_retrain_check = now
                    self._run_weekly_retrain()

            except Exception as e:
                log_event(DaemonEvent("error", "Loop error", str(e)))
                logger.error("Daemon loop error: %s", e)

            # Sleep between checks — 10s granularity
            time.sleep(10)

    # ================================================================
    # Tier 1: Data Polling (Zero Tokens)
    # ================================================================

    def _poll_prices(self):
        """Fetch current prices. Zero tokens."""
        try:
            from ..data.providers import get_market_provider
            provider = get_market_provider(self.config)
            for sym in self.config.execution.symbols:
                price = provider.get_price(sym)
                if price:
                    self.snapshot.prices[sym] = price

            self.snapshot.last_price_poll = time.time()
            self._data_changed = True
            self.polls += 1
        except Exception as e:
            logger.debug("Price poll failed: %s", e)

    def _poll_derivatives(self):
        """Poll supplemental equity data and refresh caches. Zero tokens."""

        # Refresh trigger orders cache for fill classification
        self._refresh_trigger_cache()

        # Pre-fetch deep data for briefing (position assets only)
        try:
            position_symbols = list(self._prev_positions.keys())
            if position_symbols:
                self._data_cache.poll(self._get_provider(), position_symbols)
        except Exception as e:
            logger.debug("DataCache poll failed: %s", e)

        # Record equity snapshot (every deriv poll = ~5 min)
        try:
            from ..core.equity_tracker import record_snapshot
            provider = self._get_provider()
            if provider.can_trade:
                state = provider.get_user_state()
                record_snapshot(
                    account_value=state["account_value"],
                    unrealized_pnl=state["unrealized_pnl"],
                    daily_realized_pnl=self._daily_realized_pnl,
                    position_count=len(state.get("positions", [])),
                )
        except Exception as e:
            logger.debug("Equity snapshot failed: %s", e)

        self.snapshot.last_deriv_poll = time.time()
        self._data_changed = True
        self.polls += 1

    # ================================================================
    # Support / Resistance (Equities)
    # ================================================================

    def _refresh_support_resistance(self) -> None:
        """Compute and store support/resistance for tracked symbols."""
        try:
            from .tools.support_resistance import handle_get_support_resistance
            for sym in self.config.execution.symbols:
                try:
                    handle_get_support_resistance(symbol=sym, lookback_days=120, interval="1d")
                except Exception as e:
                    logger.debug("S/R refresh failed for %s: %s", sym, e)
        except Exception as e:
            logger.debug("S/R refresh error: %s", e)

    def _run_weekly_retrain(self) -> None:
        """Adjust alpha/rules thresholds based on recent performance."""
        try:
            from ..core.retrainer import run_weekly_retrain
            summary = run_weekly_retrain()
            if summary:
                log_event(DaemonEvent("learning", "Weekly retrain", summary))
        except Exception as e:
            logger.debug("Weekly retrain failed: %s", e)

    # ================================================================
    # Watchpoint System
    # ================================================================

    def _check_watchpoints(self) -> list[dict]:
        """Query Nous for active watchpoints and evaluate triggers.

        Returns list of triggered watchpoint dicts with keys:
            node, data (parsed JSON body), trigger
        """
        if not self.snapshot.prices:
            return []  # No data yet

        triggered = []
        try:
            nous = self._get_nous()

            # List all active watchpoints
            watchpoints = nous.list_nodes(
                subtype="custom:watchpoint",
                lifecycle="ACTIVE",
                limit=50,
            )

            self._active_watchpoint_count = len(watchpoints)

            # Also cache thesis count (zero extra calls during this check)
            try:
                theses = nous.list_nodes(
                    subtype="custom:thesis", lifecycle="ACTIVE", limit=50,
                )
                self._active_thesis_count = len(theses)
            except Exception:
                pass

            for wp in watchpoints:
                body = wp.get("content_body", "")
                if not body:
                    continue

                try:
                    data = json.loads(body)
                except (json.JSONDecodeError, TypeError):
                    continue

                trigger = data.get("trigger")
                if not trigger:
                    continue

                # Check expiry
                expiry = trigger.get("expiry")
                if expiry:
                    try:
                        exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                        if exp_dt < datetime.now(timezone.utc):
                            self._expire_watchpoint(nous, wp)
                            continue
                    except ValueError:
                        pass

                # Evaluate trigger condition
                if self._evaluate_trigger(trigger):
                    triggered.append({
                        "node": wp,
                        "data": data,
                        "trigger": trigger,
                    })
                    # Mark as fired to prevent re-triggering
                    self._fire_watchpoint(nous, wp)

        except Exception as e:
            logger.debug("Watchpoint check failed: %s", e)

        return triggered

    def _evaluate_trigger(self, trigger: dict) -> bool:
        """Evaluate a single trigger condition against the market snapshot."""
        condition = trigger.get("condition", "")
        symbol = trigger.get("symbol", "")
        value = trigger.get("value", 0)

        if not condition or not symbol:
            return False

        price = self.snapshot.prices.get(symbol)

        if condition == "price_below":
            return price is not None and price <= value

        elif condition == "price_above":
            return price is not None and price >= value

        # Future: oi_change, liquidation_spike (need historical tracking)
        return False

    @staticmethod
    def _expire_watchpoint(nous, wp: dict):
        """Mark a watchpoint as expired."""
        try:
            nous.update_node(wp["id"], state_lifecycle="DORMANT")
            logger.info("Watchpoint expired: %s", wp.get("content_title", "?"))
        except Exception:
            pass

    @staticmethod
    def _fire_watchpoint(nous, wp: dict):
        """Mark a watchpoint as fired — DORMANT = permanently dead.

        The agent must create a new watchpoint if it wants to monitor again.
        """
        try:
            nous.update_node(wp["id"], state_lifecycle="DORMANT")
            logger.info("Watchpoint fired → DORMANT: %s", wp.get("content_title", "?"))
        except Exception:
            pass

    # ================================================================
    # Position Tracking & Fill Detection
    # ================================================================

    def _init_position_tracking(self):
        """Snapshot current positions on daemon start (no wake on existing)."""
        try:
            provider = self._get_provider()
            if not provider.can_trade:
                return

            state = provider.get_user_state()
            for p in state.get("positions", []):
                self._prev_positions[p["coin"]] = {
                    "side": p["side"],
                    "size": p["size"],
                    "entry_px": p["entry_px"],
                    "leverage": p.get("leverage", 20),
                }

            # Load persisted position types first (survives restarts)
            self._load_position_types()

            # Infer position types for any remaining unregistered positions
            for coin, data in self._prev_positions.items():
                if coin not in self._position_types:
                    lev = data.get("leverage", 20)
                    self._position_types[coin] = {
                        "type": "micro" if lev >= 15 else "macro",
                        "entry_time": 0,  # Unknown — daemon just started
                    }

            # Initial trigger cache
            self._refresh_trigger_cache()
            logger.info("Position tracking initialized: %d position(s)",
                         len(self._prev_positions))
        except Exception as e:
            logger.debug("Position tracking init failed: %s", e)

    def _refresh_trigger_cache(self):
        """Cache current trigger orders for fill classification."""
        try:
            provider = self._get_provider()
            if not provider.can_trade:
                return

            triggers = provider.get_trigger_orders()
            self._tracked_triggers.clear()
            for t in triggers:
                coin = t["coin"]
                if coin not in self._tracked_triggers:
                    self._tracked_triggers[coin] = []
                self._tracked_triggers[coin].append(t)
        except Exception as e:
            logger.debug("Trigger cache refresh failed: %s", e)

    def _fast_trigger_check(self):
        """Check SL/TP triggers every loop iteration (~10s) with fresh prices.

        The full price poll runs every 60s for tracked symbols.
        But SL/TP triggers on open positions need faster checking — a trade
        can hit TP and reverse within 60s, causing missed exits.

        This fetches fresh prices ONLY for position symbols (1 API call)
        and runs check_triggers + peak ROE tracking on every loop.
        """
        provider = self._get_provider()
        if not hasattr(provider, "check_triggers") or not self._prev_positions:
            return

        try:
            # Fetch fresh prices only for symbols with open positions
            position_syms = list(self._prev_positions.keys())
            fresh_prices = {}
            all_mids = provider.get_all_prices()
            for sym in position_syms:
                if sym in all_mids:
                    fresh_prices[sym] = all_mids[sym]
                    # Also update snapshot so briefing sees latest
                    self.snapshot.prices[sym] = all_mids[sym]

            if not fresh_prices:
                return

            # Check SL/TP/liquidation triggers with fresh prices
            events = provider.check_triggers(fresh_prices)
            for event in events:
                self._update_daily_pnl(event["realized_pnl"])
                self._record_trigger_close(event)
                self._wake_for_fill(
                    event["coin"], event["side"], event["entry_px"],
                    event["exit_px"], event["realized_pnl"],
                    event["classification"],
                )

            if events:
                for event in events:
                    self._position_types.pop(event["coin"], None)
                self._persist_position_types()
                state = provider.get_user_state()
                positions = state.get("positions", [])
                self._prev_positions = {
                    p["coin"]: {"side": p["side"], "size": p["size"], "entry_px": p["entry_px"], "leverage": p.get("leverage", 20)}
                    for p in positions
                }

            # Track peak ROE on every check (not just every 60s)
            for sym in position_syms:
                px = fresh_prices.get(sym)
                if not px:
                    continue
                pos = self._prev_positions.get(sym)
                if not pos:
                    continue
                entry_px = pos.get("entry_px", 0)
                leverage = pos.get("leverage", 20)
                if entry_px <= 0:
                    continue
                side = pos.get("side", "long")
                if side == "long":
                    price_pct = (px - entry_px) / entry_px * 100
                else:
                    price_pct = (entry_px - px) / entry_px * 100
                roe_pct = price_pct * leverage
                if roe_pct > self._peak_roe.get(sym, 0):
                    self._peak_roe[sym] = roe_pct

        except Exception as e:
            logger.debug("Fast trigger check failed: %s", e)

    def _check_positions(self) -> list[dict] | None:
        """Compare current positions to cached snapshot. Detect closes.

        Returns the raw positions list from get_user_state() so callers
        (_check_profit_levels) can reuse it without a second API call.
        """
        try:
            provider = self._get_provider()
            if not provider.can_trade:
                return None

            # Paper mode: check SL/TP/liquidation triggers internally
            if hasattr(provider, "check_triggers") and self.snapshot.prices:
                events = provider.check_triggers(self.snapshot.prices)
                for event in events:
                    self._update_daily_pnl(event["realized_pnl"])
                    self._record_trigger_close(event)
                    self._wake_for_fill(
                        event["coin"], event["side"], event["entry_px"],
                        event["exit_px"], event["realized_pnl"],
                        event["classification"],
                    )
                # Refresh cached positions after any closes
                if events:
                    # Clean up position types for closed positions (after wake_for_fill used them)
                    for event in events:
                        self._position_types.pop(event["coin"], None)
                    self._persist_position_types()
                    state = provider.get_user_state()
                    positions = state.get("positions", [])
                    self._prev_positions = {
                        p["coin"]: {"side": p["side"], "size": p["size"], "entry_px": p["entry_px"], "leverage": p.get("leverage", 20)}
                        for p in positions
                    }
                    return positions

            # Testnet/live flow: detect closes by comparing snapshots
            state = provider.get_user_state()
            positions = state.get("positions", [])
            current = {}
            for p in positions:
                current[p["coin"]] = {
                    "side": p["side"],
                    "size": p["size"],
                    "entry_px": p["entry_px"],
                    "leverage": p.get("leverage", 20),
                }

            # Detect closed positions: was in _prev but not in current
            closed_coins = []
            for coin, prev_data in self._prev_positions.items():
                if coin not in current:
                    # Position closed — find the fill details
                    self._handle_position_close(provider, coin, prev_data)
                    closed_coins.append(coin)

            # Clean up position types for closed positions (after wake_for_fill used them)
            for coin in closed_coins:
                self._position_types.pop(coin, None)
            if closed_coins:
                self._persist_position_types()

            # Update snapshot
            self._prev_positions = current
            return positions

        except Exception as e:
            logger.debug("Position check failed: %s", e)
            return None

    def _handle_position_close(self, provider, coin: str, prev_data: dict):
        """Handle a detected position close — find fills, classify, wake agent."""
        side = prev_data["side"]
        entry_px = prev_data["entry_px"]

        # Look up recent fills to get exit price and PnL
        # Use _last_fill_check as lookback start (not wall clock) — handles daemon delays
        try:
            start_ms = int(max(self._last_fill_check - 60, 0) * 1000)  # 60s buffer
            fills = provider.get_user_fills(start_ms)

            # Find the closing fill for this coin
            close_fill = None
            for f in reversed(fills):  # newest first
                if f["coin"] == coin and f["direction"].startswith("Close"):
                    close_fill = f
                    break

            # Dedup: skip if we already processed this fill
            if close_fill:
                fill_hash = close_fill.get("hash", "")
                if fill_hash and fill_hash in self._processed_fills:
                    logger.debug("Skipping already-processed fill: %s %s", coin, fill_hash)
                    return
                if fill_hash:
                    self._processed_fills.add(fill_hash)
                    # Cap set size
                    if len(self._processed_fills) > 100:
                        self._processed_fills = set(list(self._processed_fills)[-50:])

            if close_fill:
                exit_px = close_fill["price"]
                realized_pnl = close_fill["closed_pnl"]
            else:
                # No fill found — estimate from last known price
                exit_px = self.snapshot.prices.get(coin, 0)
                if side == "long":
                    realized_pnl = (exit_px - entry_px) * prev_data["size"]
                else:
                    realized_pnl = (entry_px - exit_px) * prev_data["size"]

        except Exception as e:
            logger.debug("Fill lookup failed for %s: %s", coin, e)
            exit_px = self.snapshot.prices.get(coin, 0)
            realized_pnl = 0
            close_fill = None

        # Refresh trigger cache for accurate classification
        # (triggers may have been placed since last deriv poll)
        self._refresh_trigger_cache()

        # Classify the exit
        triggers = self._tracked_triggers.get(coin, [])
        classification = self._classify_fill(coin, close_fill, triggers)

        # Update daily PnL for circuit breaker
        self._update_daily_pnl(realized_pnl)

        # Record to Nous (SL/TP auto-fills aren't written by agent)
        if classification in ("stop_loss", "take_profit", "liquidation"):
            self._record_trigger_close({
                "coin": coin, "side": side, "entry_px": entry_px,
                "exit_px": exit_px, "realized_pnl": realized_pnl,
                "classification": classification,
            })

        # Wake the agent with the appropriate message
        self._wake_for_fill(coin, side, entry_px, exit_px, realized_pnl, classification)

    def _record_trigger_close(self, event: dict):
        """Write a trade_close node to Nous when paper SL/TP/liquidation fires.

        This ensures auto-fills are persisted in memory just like manual closes.
        Reuses the same _store_to_nous / _find_trade_entry pattern from trading tools.
        """
        try:
            from .tools.trading import _store_to_nous, _find_trade_entry

            coin = event["coin"]
            side = event["side"]
            entry_px = event["entry_px"]
            exit_px = event["exit_px"]
            pnl = event["realized_pnl"]
            classification = event["classification"]  # stop_loss, take_profit, liquidation

            pnl_pct = ((exit_px - entry_px) / entry_px * 100) if entry_px else 0
            if side == "short":
                pnl_pct = -pnl_pct

            label = classification.replace("_", " ").title()
            sign = "+" if pnl >= 0 else ""
            title = f"CLOSED {side.upper()} {coin} @ ${exit_px:,.2f}"
            summary = (
                f"CLOSED {side.upper()} {coin} | ${entry_px:,.2f} → ${exit_px:,.2f} "
                f"| PnL {sign}${pnl:.4f} ({sign}{pnl_pct:.2f}%)"
            )
            content = (
                f"Closed full {side} {coin}.\n"
                f"Entry: ${entry_px:,.2f} → Exit: ${exit_px:,.2f}\n"
                f"PnL: {sign}${pnl:.4f} ({sign}{pnl_pct:.2f}%)\n"
                f"Reason: {label} triggered automatically."
            )
            # Get opened_at from position type registry (set on entry)
            type_info = self._position_types.get(coin, {})
            entry_time = type_info.get("entry_time", 0)
            opened_at = ""
            if entry_time > 0:
                from datetime import datetime, timezone
                opened_at = datetime.fromtimestamp(entry_time, tz=timezone.utc).isoformat()

            # Get peak ROE (MFE) and trade_type before they're cleaned up
            mfe_pct = self._peak_roe.get(coin, 0.0)
            trade_type = type_info.get("type", "macro")

            signals = {
                "action": "close",
                "side": side,
                "symbol": coin,
                "entry": entry_px,
                "exit": exit_px,
                "pnl_usd": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 2),
                "close_type": classification,
                "opened_at": opened_at,
                "mfe_pct": round(mfe_pct, 2),
                "trade_type": trade_type,
            }

            # Find the matching entry node for edge linking
            entry_id = _find_trade_entry(coin)

            node_id = _store_to_nous(
                subtype="custom:trade_close",
                title=title,
                content=content,
                summary=summary,
                signals=signals,
                link_to=entry_id,
            )
            if node_id:
                logger.info("Recorded %s close for %s in Nous: %s", classification, coin, node_id)
            else:
                logger.warning("Failed to record %s close for %s in Nous", classification, coin)
        except Exception as e:
            logger.error("_record_trigger_close failed for %s: %s", event.get("coin", "?"), e)

    def _classify_fill(
        self, coin: str, fill: dict | None, triggers: list[dict],
    ) -> str:
        """Classify a position close as 'stop_loss', 'take_profit', or 'manual'.

        Priority:
        1. OID match — fill OID matches a trigger order OID (definitive)
        2. Price match — fill price within 1.5% of SL/TP trigger price
        3. Fallback — 'manual'
        """
        if not fill or not triggers:
            return "manual"

        fill_oid = fill.get("oid")
        fill_px = fill.get("price", 0)

        # 1. Direct OID match
        if fill_oid:
            for t in triggers:
                if t.get("oid") == fill_oid:
                    return t.get("order_type", "manual").replace("_", "_")

        # 2. Price proximity match (1.5% tolerance for slippage)
        if fill_px > 0:
            for t in triggers:
                trigger_px = t.get("trigger_px")
                if not trigger_px or trigger_px <= 0:
                    continue
                pct_diff = abs(fill_px - trigger_px) / trigger_px
                if pct_diff <= 0.015:
                    return t.get("order_type", "manual")

        return "manual"

    # ================================================================
    # Options DTE Monitoring
    # ================================================================

    def _check_options_dte(self):
        """Alert when options positions approach expiry (DTE thresholds)."""
        try:
            provider = self._get_provider()
            if not provider.can_trade:
                return

            state = provider.get_user_state()
            positions = state.get("positions", [])
            if not positions:
                return

            from ..core.trading_settings import get_trading_settings
            ts = get_trading_settings()
            warn_days = int(getattr(ts, "options_dte_warn_days", 0) or 0)
            urgent_days = int(getattr(ts, "options_dte_urgent_days", 0) or 0)
            cooldown_hours = float(getattr(ts, "options_dte_alert_cooldown_hours", 0) or 0)
            cooldown = cooldown_hours * 3600 if cooldown_hours > 0 else 0

            now_ts = time.time()
            today = datetime.now(timezone.utc).date()
            open_symbols = set()

            for p in positions:
                symbol = p.get("coin", "")
                if not symbol:
                    continue
                m = _OPTION_OCC_RE.match(symbol)
                if not m:
                    continue

                open_symbols.add(symbol)
                try:
                    exp_date = date(2000 + int(m.group(2)), int(m.group(3)), int(m.group(4)))
                except Exception:
                    continue

                dte = (exp_date - today).days
                if dte < 0:
                    continue

                level = None
                if urgent_days and dte <= urgent_days:
                    level = "urgent"
                elif warn_days and dte <= warn_days:
                    level = "warn"
                if not level:
                    continue

                last = self._options_dte_alerts.get(symbol, 0)
                if cooldown and now_ts - last < cooldown:
                    continue
                self._options_dte_alerts[symbol] = now_ts
                self._wake_for_options_dte(symbol, dte, level, p)

            # Cleanup alerts for closed option positions
            for sym in list(self._options_dte_alerts.keys()):
                if sym not in open_symbols:
                    del self._options_dte_alerts[sym]

        except Exception as e:
            logger.debug("Options DTE check failed: %s", e)

    def _wake_for_options_dte(self, symbol: str, dte: int, level: str, position: dict):
        """Wake the agent for expiring option positions."""
        side = position.get("side", "long").upper()
        entry_px = float(position.get("entry_px", 0) or 0)
        mark_px = float(position.get("mark_px", 0) or 0)
        unrealized = float(position.get("unrealized_pnl", 0) or 0)
        pnl_sign = "+" if unrealized >= 0 else ""

        header = f"[DAEMON WAKE — Option Expiry: {symbol} in {dte}d]"
        if level == "urgent":
            footer = (
                f"Expiry is imminent ({dte} day{'s' if dte != 1 else ''}). "
                "Decide now: close, roll, or accept assignment."
            )
            priority = True
        else:
            footer = (
                f"Expiry approaching ({dte} days). "
                "Plan the exit: close or roll before expiry."
            )
            priority = False

        pnl_line = (
            f"{symbol} {side} | Entry: ${entry_px:,.2f} → Mark: ${mark_px:,.2f} "
            f"| PnL: {pnl_sign}${abs(unrealized):,.2f}"
        )

        message = f"{header}\n\n{pnl_line}\n\n{footer}"
        response = self._wake_agent(
            message,
            priority=priority,
            max_coach_cycles=0,
            max_tokens=512,
            source="daemon:options_dte",
        )
        if response:
            log_event(DaemonEvent(
                "options_dte",
                f"{symbol} expiring in {dte}d",
                f"{symbol} {side} | DTE {dte}d | PnL {pnl_sign}${abs(unrealized):,.2f}",
            ))
            _queue_and_persist("Options", f"Expiry: {symbol} ({dte}d)", response)
            _notify_discord("Options", f"Expiry: {symbol} ({dte}d)", response)
            logger.info("Options DTE alert: %s %s (DTE %dd)", symbol, side, dte)

    # ================================================================
    # Profit Level Monitoring
    # ================================================================

    @staticmethod
    def _alert_cooldown(trade_type: str) -> int:
        """Cooldown between repeated profit alerts for same position+tier.

        Scalps are short-lived — need faster re-alerts.
        Swings are patient — longer gaps are fine.
        """
        if trade_type == "micro":
            return 300   # 5 min — micros live 3-15 min, need fast re-alerts
        return 2700      # 45 min

    @staticmethod
    def _profit_thresholds(leverage: int) -> tuple[float, float, float, float]:
        """Get profit alert thresholds scaled by leverage.

        Two regimes to keep price-move thresholds sensible:
        - High leverage (>=15x, scalp): tight ROE thresholds (7/10/15/-7)
        - Low leverage (<15x, swing): wider ROE thresholds (20/35/50/-15)

        Price-move equivalents:
          Scalp 20x: nudge 0.35%, take 0.5%, urgent 0.75%
          Swing 10x: nudge 2%, take 3.5%, urgent 5%
          Swing  5x: nudge 4%, take 7%, urgent 10%
        """
        if leverage >= 15:
            return (7.0, 10.0, 15.0, -7.0)
        return (20.0, 35.0, 50.0, -15.0)

    def _check_profit_levels(self, positions: list[dict] | None = None):
        """Check unrealized P&L on open positions and wake agent at thresholds.

        Uses live return_pct from provider — no manual ROE computation needed.
        Thresholds scale with leverage: high lev = scalp (tight), low lev = swing (wide).

        Args:
            positions: Raw positions list from _check_positions(). If None,
                       skips this cycle (avoids redundant API call).
        """
        if not positions:
            return

        try:
            now = time.time()

            for p in positions:
                coin = p["coin"]
                side = p["side"]
                entry_px = p["entry_px"]
                mark_px = p["mark_px"]
                roe_pct = p["return_pct"]  # Already leveraged return on margin
                leverage = p.get("leverage", 20)

                # Track peak ROE for MFE (max favorable excursion)
                if roe_pct > self._peak_roe.get(coin, 0):
                    self._peak_roe[coin] = roe_pct

                # Reset alerts if position side flipped (close long → open short)
                prev_side = self._profit_sides.get(coin)
                if prev_side and prev_side != side:
                    self._profit_alerts.pop(coin, None)
                self._profit_sides[coin] = side

                if coin not in self._profit_alerts:
                    self._profit_alerts[coin] = {}
                alerts = self._profit_alerts[coin]

                # Look up trade type for this position
                type_info = self.get_position_type(coin)
                trade_type = type_info["type"]

                # Leverage-aware thresholds
                nudge, take, urgent, risk = self._profit_thresholds(leverage)

                # Check profit tiers (highest first for priority)
                if roe_pct >= urgent:
                    self._maybe_alert(coin, "urgent_profit", roe_pct, side, entry_px, mark_px, now, alerts, trade_type)
                elif roe_pct >= take:
                    self._maybe_alert(coin, "take_profit", roe_pct, side, entry_px, mark_px, now, alerts, trade_type)
                elif roe_pct >= nudge:
                    self._maybe_alert(coin, "profit_nudge", roe_pct, side, entry_px, mark_px, now, alerts, trade_type)

                # Check profit fading: peak was strong but profit is dying
                peak = self._peak_roe.get(coin, 0)
                if peak >= nudge and roe_pct < peak * 0.4:
                    self._maybe_alert(coin, "profit_fading", roe_pct, side, entry_px, mark_px, now, alerts, trade_type)

                # Check risk: significant loss with no stop loss
                if roe_pct <= risk:
                    triggers = self._tracked_triggers.get(coin, [])
                    has_sl = any(t.get("order_type") == "stop_loss" for t in triggers)
                    if not has_sl:
                        self._maybe_alert(coin, "risk_no_sl", roe_pct, side, entry_px, mark_px, now, alerts, trade_type)

                # Check micro overstay (>60 min)
                if trade_type == "micro" and type_info["entry_time"] > 0:
                    if now - type_info["entry_time"] > 3600:
                        self._maybe_alert(coin, "micro_overstay", roe_pct, side, entry_px, mark_px, now, alerts, "micro")

            # Clean up alerts + sides + peak ROE for closed positions
            open_coins = {p["coin"] for p in positions}
            for coin in list(self._profit_alerts.keys()):
                if coin not in open_coins:
                    del self._profit_alerts[coin]
                    self._profit_sides.pop(coin, None)
            for coin in list(self._peak_roe):
                if coin not in open_coins:
                    del self._peak_roe[coin]

        except Exception as e:
            logger.debug("Profit level check failed: %s", e)

    def _maybe_alert(
        self, coin: str, tier: str, roe_pct: float,
        side: str, entry_px: float, mark_px: float,
        now: float, alerts: dict, trade_type: str = "macro",
    ):
        """Fire profit alert if not on cooldown."""
        last = alerts.get(tier, 0)
        cooldown = self._alert_cooldown(trade_type)
        if now - last < cooldown:
            return
        alerts[tier] = now
        self._wake_for_profit(coin, side, entry_px, mark_px, roe_pct, tier, trade_type)

    def _wake_for_profit(
        self, coin: str, side: str, entry_px: float,
        mark_px: float, roe_pct: float, tier: str,
        trade_type: str = "macro",
    ):
        """Wake the agent with a profit/risk alert. Adapts tone to trade type."""
        type_info = self.get_position_type(coin)
        leverage = self._prev_positions.get(coin, {}).get("leverage", 20)
        is_scalp = trade_type == "micro"
        type_label = f"scalp {leverage}x" if is_scalp else f"swing {leverage}x"

        # Hold duration (if known)
        hold_str = ""
        hold_mins = 0
        if type_info["entry_time"] > 0:
            hold_mins = max(0, int((time.time() - type_info["entry_time"]) / 60))
            if hold_mins < 60:
                hold_str = f" | Held: {hold_mins}m"
            else:
                hold_str = f" | Held: {hold_mins // 60}h{hold_mins % 60}m"

        pnl_line = (
            f"{coin} {side.upper()} ({type_label})"
            f" | Entry: ${entry_px:,.0f} → Mark: ${mark_px:,.0f}"
            f" | ROE: {roe_pct:+.1f}%{hold_str}"
        )

        if tier == "micro_overstay":
            overstay_mins = hold_mins or 60
            header = f"[DAEMON WAKE — Micro Overstay: {coin} {side.upper()} {overstay_mins}m]"
            footer = (
                f"This scalp has been open {overstay_mins} minutes — micro trades should be 15-60 min. "
                f"You're at {roe_pct:+.1f}% ROE. Close it, or if your thesis evolved, acknowledge it's now a swing."
            )
            priority = False
        elif tier == "urgent_profit":
            header = f"[DAEMON WAKE — TAKE PROFIT: {coin} {side.upper()} +{roe_pct:.0f}%]"
            if is_scalp:
                footer = f"This scalp is up {roe_pct:+.0f}%. That's a clean win — close it and move on."
            else:
                footer = f"Swing up {roe_pct:+.0f}% — your thesis played out. Take profit or give a clear reason to hold."
            priority = True
        elif tier == "take_profit":
            header = f"[DAEMON WAKE — Profit Alert: {coin} {side.upper()} +{roe_pct:.0f}%]"
            if is_scalp:
                footer = f"Scalp up {roe_pct:+.0f}%. Lock in the gain — don't let a quick win turn into a hold."
            else:
                footer = f"Swing position up {roe_pct:+.0f}%. Consider taking some off the table or trail your stop."
            priority = True
        elif tier == "profit_nudge":
            header = f"[DAEMON WAKE — {coin} {side.upper()} +{roe_pct:.0f}%]"
            if is_scalp:
                footer = f"Scalp up {roe_pct:+.0f}%. CLOSE THIS TRADE NOW. This is peak micro profit — take it before it reverses."
            else:
                footer = f"Swing building nicely at +{roe_pct:.0f}%. Trail your stop to lock in the move."
            priority = False
        elif tier == "profit_fading":
            peak = self._peak_roe.get(coin, 0)
            header = f"[DAEMON WAKE — PROFIT FADING: {coin} {side.upper()} peaked +{peak:.0f}% → now {roe_pct:+.0f}%]"
            if is_scalp:
                footer = (
                    f"Scalp peaked at +{peak:.0f}% ROE but now at {roe_pct:+.0f}%. "
                    f"Your profit is dying. CLOSE NOW or you lose it all."
                )
            else:
                footer = (
                    f"Swing peaked at +{peak:.0f}% ROE but dropped to {roe_pct:+.0f}%. "
                    f"Profit is fading fast. Take what's left or tighten your stop."
                )
            priority = True
        elif tier == "risk_no_sl":
            header = f"[DAEMON WAKE — RISK: {coin} {side.upper()} {roe_pct:+.0f}%]"
            if is_scalp:
                footer = f"Scalp down {roe_pct:+.0f}% with no SL. Close or set a tight stop immediately."
            else:
                footer = f"Swing down {roe_pct:+.0f}% with no stop loss. Your thesis needs a line in the sand — set one."
            priority = True
        else:
            return

        message = f"{header}\n\n{pnl_line}\n\n{footer}"
        response = self._wake_agent(message, priority=priority, max_coach_cycles=0, max_tokens=1024, source="daemon:profit")
        if response:
            log_event(DaemonEvent(
                "profit", f"{tier}: {coin} {side}",
                f"ROE {roe_pct:+.1f}% ({type_label}) | Entry ${entry_px:,.0f} → ${mark_px:,.0f}",
            ))
            _queue_and_persist("Profit", f"{tier.replace('_', ' ').title()}: {coin}", response)
            _notify_discord("Profit", f"{tier.replace('_', ' ').title()}: {coin}", response)
            logger.info("Profit alert: %s %s %s (ROE %+.1f%%, %s)", tier, coin, side, roe_pct, trade_type)

    # ================================================================
    # Risk Guardrails (Circuit Breaker)
    # ================================================================

    def _check_daily_reset(self):
        """Reset daily PnL counters at UTC midnight."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            if self._daily_reset_date and self._trading_paused:
                log_event(DaemonEvent(
                    "circuit_breaker", "Circuit breaker reset",
                    f"New day ({today}). Daily PnL reset from ${self._daily_realized_pnl:+.2f}.",
                ))
                logger.info("Circuit breaker reset — new day %s", today)
            self._daily_realized_pnl = 0.0
            self._trading_paused = False
            self._entries_today = 0
            self._micro_entries_today = 0
            # Weekly reset on Monday
            if datetime.now(timezone.utc).weekday() == 0:
                self._entries_this_week = 0
            self._daily_reset_date = today
            self._persist_daily_pnl()

    def _update_daily_pnl(self, realized_pnl: float):
        """Update daily PnL and check circuit breaker threshold."""
        self._check_daily_reset()
        self._daily_realized_pnl += realized_pnl
        self._last_close_time = time.time()

        max_loss = self.config.daemon.max_daily_loss_usd
        if max_loss > 0 and self._daily_realized_pnl <= -max_loss and not self._trading_paused:
            self._trading_paused = True
            log_event(DaemonEvent(
                "circuit_breaker", "Trading paused",
                f"Daily loss ${self._daily_realized_pnl:+.2f} exceeds "
                f"-${max_loss:.0f} limit. Paused until UTC midnight.",
            ))
            logger.warning("CIRCUIT BREAKER: Daily loss $%.2f exceeds limit $%.0f",
                            abs(self._daily_realized_pnl), max_loss)
        self._persist_daily_pnl()

    def _resolve_storage_path(self, filename: str) -> Path:
        """Resolve a file under storage/ regardless of cwd."""
        current = Path(__file__).resolve().parent
        for _ in range(10):
            if (current / "config").is_dir():
                return current / "storage" / filename
            current = current.parent
        return Path("storage") / filename

    def _load_daily_pnl(self):
        """Load daily PnL state from disk."""
        try:
            if not self._daily_pnl_path.exists():
                return
            data = json.loads(self._daily_pnl_path.read_text())
            self._daily_reset_date = str(data.get("date", "") or "")
            self._daily_realized_pnl = float(data.get("daily_realized_pnl", 0.0) or 0.0)
            self._entries_today = int(data.get("entries_today", 0) or 0)
            self._micro_entries_today = int(data.get("micro_entries_today", 0) or 0)
            self._trading_paused = bool(data.get("trading_paused", False))
        except Exception:
            pass

    def _persist_daily_pnl(self):
        """Persist daily PnL state to disk."""
        try:
            self._daily_pnl_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "date": self._daily_reset_date,
                "daily_realized_pnl": self._daily_realized_pnl,
                "entries_today": self._entries_today,
                "micro_entries_today": self._micro_entries_today,
                "trading_paused": self._trading_paused,
            }
            self._daily_pnl_path.write_text(json.dumps(payload))
        except Exception:
            pass

    # ================================================================
    # Wake Messages
    # ================================================================

    def _wake_for_watchpoint(self, wp_data: dict):
        """Assemble context and wake the agent for a triggered watchpoint."""
        node = wp_data["node"]
        data = wp_data["data"]
        trigger = wp_data["trigger"]

        title = node.get("content_title", "Untitled watchpoint")
        content = data.get("text", "")
        signals = data.get("signals_at_creation", {})
        symbol = trigger.get("symbol", "?")
        condition = trigger.get("condition", "?")
        value = trigger.get("value", 0)

        # Current data
        current_price = self.snapshot.prices.get(symbol, 0)
        lines = [
            f"[DAEMON WAKE — Watchpoint Triggered: {title}]",
            "",
            f"Your alert fired: {symbol} {condition.replace('_', ' ')} {value}.",
            f"Current: ${current_price:,.0f}",
            "",
            "What you were thinking when you set this:",
            content,
        ]

        if signals:
            lines.append("")
            lines.append("Market conditions when you set this alert:")
            for k, v in signals.items():
                lines.append(f"  {k}: {v}")

        lines.extend([
            "",
            "This alert is now DEAD. Decide: act on it, or set a new one. Keep your response to 1-3 sentences.",
        ])

        message = "\n".join(lines)
        response = self._wake_agent(message, max_coach_cycles=0, max_tokens=1024, source="daemon:watchpoint")
        if response:
            self.watchpoint_fires += 1
            log_event(DaemonEvent(
                "watchpoint", title,
                f"{symbol} {condition.replace('_', ' ')} {value}",
            ))
            _queue_and_persist("Watchpoint", title, response)
            _notify_discord("Watchpoint", title, response)
            logger.info("Watchpoint wake complete: %s (%d chars)", title, len(response))

    def _wake_for_fill(
        self,
        coin: str,
        side: str,
        entry_px: float,
        exit_px: float,
        realized_pnl: float,
        classification: str,
    ):
        """Wake the agent when a position closes. Adapts tone to trade type + classification."""
        # Look up trade type BEFORE cleanup (still in registry at this point)
        type_info = self._position_types.get(coin, {"type": "macro", "entry_time": 0})
        trade_type = type_info["type"]
        is_scalp = trade_type == "micro"
        type_label = "Scalp" if is_scalp else "Swing"

        pnl_sign = "+" if realized_pnl >= 0 else ""
        pnl_pct = ((exit_px - entry_px) / entry_px * 100) if entry_px > 0 else 0
        if side == "short":
            pnl_pct = -pnl_pct

        # Hold duration
        hold_str = ""
        if type_info["entry_time"] > 0:
            hold_mins = max(0, int((time.time() - type_info["entry_time"]) / 60))
            if hold_mins < 60:
                hold_str = f" | Held: {hold_mins}m"
            else:
                hold_str = f" | Held: {hold_mins // 60}h{hold_mins % 60}m"

        pnl_line = (
            f"Entry: ${entry_px:,.0f} → Exit: ${exit_px:,.0f} | "
            f"PnL: {pnl_sign}${abs(realized_pnl):,.2f} ({pnl_pct:+.1f}%){hold_str}"
        )

        if classification == "stop_loss":
            header = f"[DAEMON WAKE — Stop Loss: {coin} {side.upper()} ({type_label})]"
            if is_scalp:
                footer = "Scalp stopped out. Quick review: was the entry timing right? Was the SL appropriate for the timeframe? One lesson, then move on."
            else:
                footer = "Stopped out of swing position. Recall your thesis — what invalidated it? Store a real lesson (what would you do differently?), archive the thesis, clean up watchpoints, and scan for what's next."
        elif classification == "take_profit":
            header = f"[DAEMON WAKE — Take Profit: {coin} {side.upper()} ({type_label})]"
            if is_scalp:
                footer = (
                    "Scalp TP hit — clean trade. REQUIRED: store a playbook (memory_type='playbook') with: "
                    "setup conditions, entry timing, risk params that worked, what's repeatable. "
                    f"Title: 'Playbook: {coin} <pattern_name>'"
                )
            else:
                footer = (
                    "Swing TP hit. REQUIRED: store a playbook (memory_type='playbook') with: "
                    "thesis and what confirmed it, entry signal, hold logic through drawdowns, "
                    "risk params that worked, what's repeatable. "
                    f"Title: 'Playbook: {coin} <pattern_name>'. "
                    "Then archive thesis, clean watchpoints, look for follow-up."
                )
        else:
            header = f"[DAEMON WAKE — Position Closed: {coin} {side.upper()} ({type_label})]"
            if realized_pnl >= 0:
                # Profitable close — extract the playbook
                if is_scalp:
                    footer = (
                        "Scalp closed in profit. Store a playbook (memory_type='playbook'): "
                        f"setup, timing, what worked. Title: 'Playbook: {coin} <pattern>'."
                    )
                else:
                    footer = (
                        "Swing closed in profit. Store a playbook (memory_type='playbook'): "
                        "thesis, entry signal, risk params, what worked. "
                        f"Title: 'Playbook: {coin} <pattern>'. Archive thesis, clean watchpoints."
                    )
            else:
                if is_scalp:
                    footer = "Scalp closed at a loss. Was the exit timing right? Store the lesson — specific to THIS setup, not a global rule."
                else:
                    footer = "Swing closed at a loss. Store why — what specifically went wrong with THIS setup? Clean up watchpoints, scan the market."

        lines = [header, "", pnl_line, "", footer]

        # Append circuit breaker warning if trading is paused
        if self._trading_paused:
            lines.extend([
                "",
                "[CIRCUIT BREAKER ACTIVE]",
                f"Daily loss has reached ${abs(self._daily_realized_pnl):,.2f}. "
                "Trading is paused until tomorrow UTC.",
                "Focus on analysis and learning, not new entries.",
            ])

        message = "\n".join(lines)
        fill_tokens = 1536 if classification in ("stop_loss", "take_profit") else 512
        response = self._wake_agent(message, priority=True, max_coach_cycles=0, max_tokens=fill_tokens, source="daemon:fill")
        if response:
            self._fill_fires += 1
            fill_title = f"{classification.replace('_', ' ').title()}: {coin} {side} ({type_label})"
            log_event(DaemonEvent(
                "fill", fill_title,
                f"Entry: ${entry_px:,.0f} → Exit: ${exit_px:,.0f} | "
                f"PnL: {pnl_sign}${abs(realized_pnl):,.2f} ({pnl_pct:+.1f}%)",
            ))
            _queue_and_persist("Fill", fill_title, response, event_type="fill")
            _notify_discord("Fill", fill_title, response)
            logger.info("Fill wake complete: %s %s %s %s (PnL: %s%.2f)",
                         classification, trade_type, coin, side, pnl_sign, abs(realized_pnl))

    # ================================================================
    # Coach Cross-Wake Intelligence
    # ================================================================

    def _format_wake_history(self) -> str:
        """Format recent daemon events for the coach prompt."""
        from ..core.daemon_log import get_events

        events = get_events(limit=5)
        if not events:
            return ""

        lines = [f"Recent Wake History (last {len(events)}):"]
        for event in events:
            etype = event.get("type", "?")
            title = event.get("title", "?")
            detail = event.get("detail", "")
            ts = event.get("timestamp", "")

            age = _format_event_age(ts) if ts else "?"
            if len(detail) > 80:
                detail = detail[:77] + "..."
            lines.append(f"  {age}: [{etype}] {title} — {detail}")

        return "\n".join(lines)

    def _store_thought(self, question: str):
        """Store a Haiku question for injection into the next wake."""
        self._pending_thoughts.append(question)
        # Cap at 3 thoughts max
        if len(self._pending_thoughts) > 3:
            self._pending_thoughts = self._pending_thoughts[-3:]
        logger.info("Stored pending thought: %s", question[:60])

    def _update_fingerprint(self, audit: dict):
        """Update wake fingerprint for staleness detection by warnings."""
        tools_used = frozenset(tc["name"] for tc in self.agent._last_tool_calls)
        mutations = frozenset(n["subtype"] for n in audit["nodes_created"])
        fingerprint = tools_used | mutations

        self._wake_fingerprints.append(fingerprint)
        if len(self._wake_fingerprints) > 5:
            self._wake_fingerprints.pop(0)

    # ================================================================
    # Health Check (Stub)
    # ================================================================

    def _check_health(self, startup: bool = False):
        """Basic health check placeholder.

        Prevents daemon crashes if health check is referenced but not implemented.
        """
        if startup:
            logger.info("Health check stub: startup OK")
        else:
            logger.debug("Health check stub: OK")

    def _seed_clusters(self):
        """Placeholder for cluster seeding (no-op)."""
        logger.debug("Cluster seed stub: OK")


# ====================================================================
# Coach Intelligence Helpers
# ====================================================================

def _format_event_age(iso_timestamp: str) -> str:
    """Format an ISO timestamp as relative age (e.g. '3h ago', '45m ago')."""
    try:
        ts = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - ts
        total_seconds = int(delta.total_seconds())

        if total_seconds < 60:
            return "just now"
        elif total_seconds < 3600:
            return f"{total_seconds // 60}m ago"
        elif total_seconds < 86400:
            return f"{total_seconds // 3600}h ago"
        else:
            return f"{delta.days}d ago"
    except Exception:
        return "?"


# ====================================================================
# Standalone entry point
# ====================================================================

def run_standalone():
    """Run the daemon as a standalone process (no dashboard).

    Usage: python3 -m hynous.intelligence.daemon
    """
    import signal

    from ..core.config import load_config
    from ..nous.server import ensure_running
    from .agent import Agent

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_config()

    # Start Nous server
    if not ensure_running():
        logger.warning("Nous server not available — memory tools will fail")

    # Initialize agent
    agent = Agent(config=config)

    # Start daemon
    daemon = Daemon(agent, config)
    daemon.start()

    logger.info("Daemon running. Press Ctrl+C to stop.")

    # Wait for shutdown signal
    stop_event = threading.Event()

    def _sig_handler(sig, frame):
        logger.info("Shutdown signal received")
        stop_event.set()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    stop_event.wait()
    daemon.stop()
    logger.info("Daemon exited cleanly")


if __name__ == "__main__":
    run_standalone()
