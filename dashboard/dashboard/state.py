"""
Application State

Central state management for the Hynous dashboard.
All reactive state lives here.
"""

import json
import re
import asyncio
import time
import threading
import reflex as rx
import logging
from pathlib import Path
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

# Poll interval for live portfolio updates (seconds)
_POLL_INTERVAL = 15

# Background task decorator — rx.background not exposed in Reflex 0.8.x
# but the machinery exists. Setting the marker attribute is all that's needed.
from reflex.state import BACKGROUND_TASK_MARKER

def _background(fn):
    """Mark an async method as a Reflex background task."""
    setattr(fn, BACKGROUND_TASK_MARKER, True)
    return fn

# --- Financial value highlighting ---
# Matches: +1.5%, +$500, +0.0034%, +$1.2M, etc.
_POS_RE = re.compile(r'(\+\$?[\d,]+\.?\d*[KMBkmb]?%?)')
# Matches: -1.5%, -$500, etc. Requires preceding space/paren/pipe to avoid markdown list markers
_NEG_RE = re.compile(r'(?<=[ (|:])(-\$?[\d,]+\.?\d*[KMBkmb]?%?)')

_GREEN = '<span style="color:#4ade80;text-shadow:0 0 6px rgba(74,222,128,0.2)">'
_RED = '<span style="color:#f87171;text-shadow:0 0 6px rgba(248,113,113,0.2)">'
_END = '</span>'


def _highlight(text: str) -> str:
    """Add subtle color glow to financial values in text.

    Positive values (+X%, +$X) get green glow.
    Negative values (-X%, -$X) get red glow.
    Uses raw HTML spans — requires use_raw=True in rx.markdown (default).
    """
    text = _POS_RE.sub(f'{_GREEN}\\1{_END}', text)
    text = _NEG_RE.sub(f'{_RED}\\1{_END}', text)
    return text

# Portfolio value history for sparkline
_portfolio_history: list[float] = []


def _build_sparkline_svg(values: list[float], width: int = 80, height: int = 24, color: str = "#22c55e") -> str:
    """Build a tiny SVG sparkline from a list of values."""
    if len(values) < 2:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    points = []
    for i, v in enumerate(values):
        x = round(i / (len(values) - 1) * width, 1)
        y = round(height - ((v - mn) / rng) * height, 1)
        points.append(f"{x},{y}")
    pts = " ".join(points)
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" style="display:block">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity="0.7"/></svg>'
    )


def _clean_symbol(symbol: str) -> str:
    base = (symbol or "").strip().upper()
    if not base:
        return ""
    # Strip OCC option suffix: AAPL260306C00345000 -> AAPL
    for i, ch in enumerate(base):
        if ch.isdigit():
            base = base[:i]
            break
    # Remove common separators
    for sep in (" ", "-", ".", "/"):
        if sep in base:
            base = base.split(sep)[0]
    return base


def _parse_occ_symbol(symbol: str) -> dict | None:
    """Parse OCC option symbol like AAPL260306C00345000."""
    m = re.match(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$", (symbol or "").upper())
    if not m:
        return None
    yy = int(m.group(2))
    mm = int(m.group(3))
    dd = int(m.group(4))
    exp = datetime(2000 + yy, mm, dd)
    strike = int(m.group(6)) / 1000.0
    return {
        "underlying": m.group(1),
        "expiry": exp,
        "option_type": "CALL" if m.group(5) == "C" else "PUT",
        "strike": strike,
    }


def _build_area_svg(values: list[float], width: int = 300, height: int = 72, color: str = "#22c55e") -> str:
    """Build an SVG area chart with gradient fill from a list of values."""
    if len(values) < 2:
        return ""
    mn, mx = min(values), max(values)
    pad = 4  # pixels of padding top/bottom
    pts: list[tuple[float, float]] = []
    if mx == mn:
        mid = height / 2
        for i, _ in enumerate(values):
            x = round(i / (len(values) - 1) * width, 1)
            pts.append((x, round(mid, 1)))
    else:
        rng = mx - mn
        for i, v in enumerate(values):
            x = round(i / (len(values) - 1) * width, 1)
            y = round(height - pad - ((v - mn) / rng) * (height - pad * 2), 1)
            pts.append((x, y))
    polyline = " ".join(f"{x},{y}" for x, y in pts)
    first_x, first_y = pts[0]
    last_x, last_y = pts[-1]
    area_path = (
        f"M{first_x},{first_y} "
        + " ".join(f"L{x},{y}" for x, y in pts[1:])
        + f" L{last_x},{height} L{first_x},{height} Z"
    )
    gid = f"ag{abs(hash(color)) % 9999}"
    return (
        f'<svg width="100%" height="100%" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" style="display:block;overflow:visible">'
        f'<defs>'
        f'<linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.25"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0.02"/>'
        f'</linearGradient>'
        f'</defs>'
        f'<path d="{area_path}" fill="url(#{gid})"/>'
        f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


# Human-readable names for tool indicators
_TOOL_DISPLAY = {
    "get_market_data": "Fetching market data",
    "get_multi_timeframe": "Multi-timeframe analysis",
    "search_web": "Searching the web",
    "get_my_costs": "Checking costs",
    "store_memory": "Storing memory",
    "recall_memory": "Searching memories",
    "get_account": "Checking account",
    "execute_trade": "Executing trade",
    "close_position": "Closing position",
    "modify_position": "Modifying position",
    "delete_memory": "Deleting memory",
    "manage_watchpoints": "Managing watchpoints",
    "get_trade_stats": "Checking trade stats",
    "explore_memory": "Exploring memory graph",
    "manage_conflicts": "Managing conflicts",
    "manage_clusters": "Managing clusters",
}
# --- Model preference persistence ---
_MODEL_PREFS_FILE = Path(__file__).resolve().parents[2] / "storage" / "model_prefs.json"

def _save_model_prefs(main: str, sub: str) -> None:
    """Persist model selection to disk so it survives restarts."""
    try:
        _MODEL_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MODEL_PREFS_FILE.write_text(json.dumps({"model": main, "sub_model": sub}))
    except Exception:
        pass

def _load_model_prefs() -> tuple[str, str] | None:
    """Load saved model preferences, or None if not saved."""
    try:
        if _MODEL_PREFS_FILE.exists():
            d = json.loads(_MODEL_PREFS_FILE.read_text())
            return d.get("model", ""), d.get("sub_model", "")
    except Exception:
        pass
    return None


# --- Model selection options (label, litellm model ID) ---
_MODEL_OPTIONS: list[tuple[str, str]] = [
    # Anthropic (direct — no OpenRouter needed)
    ("Claude Sonnet 4.6", "claude-sonnet-4-6"),
    ("Claude Opus 4.6", "claude-opus-4-6"),
    ("Claude Haiku 4.5", "claude-haiku-4-5-20251001"),
    ("Claude Sonnet 4.5", "claude-sonnet-4-5-20250929"),
    # OpenAI — GPT-5.2
    ("GPT-5.2 Pro", "openrouter/openai/gpt-5.2-pro"),
    ("GPT-5.2", "openrouter/openai/gpt-5.2"),
    ("GPT-5.2 Codex", "openrouter/openai/gpt-5.2-codex"),
    ("GPT-5.2 Chat", "openrouter/openai/gpt-5.2-chat"),
    # OpenAI — GPT-5.1
    ("GPT-5.1", "openrouter/openai/gpt-5.1"),
    ("GPT-5.1 Codex Max", "openrouter/openai/gpt-5.1-codex-max"),
    ("GPT-5.1 Codex", "openrouter/openai/gpt-5.1-codex"),
    ("GPT-5.1 Codex Mini", "openrouter/openai/gpt-5.1-codex-mini"),
    ("GPT-5.1 Chat", "openrouter/openai/gpt-5.1-chat"),
    # OpenAI — GPT-5
    ("GPT-5", "openrouter/openai/gpt-5-2025-08-07"),
    ("GPT-5 Mini", "openrouter/openai/gpt-5-mini-2025-08-07"),
    ("GPT-5 Nano", "openrouter/openai/gpt-5-nano-2025-08-07"),
    # OpenAI — GPT-4.1
    ("GPT-4.1", "openrouter/openai/gpt-4.1-2025-04-14"),
    ("GPT-4.1 Mini", "openrouter/openai/gpt-4.1-mini-2025-04-14"),
    ("GPT-4.1 Nano", "openrouter/openai/gpt-4.1-nano-2025-04-14"),
    # xAI — Grok
    ("Grok 4.1 Fast", "openrouter/x-ai/grok-4.1-fast"),
    ("Grok 4 Fast", "openrouter/x-ai/grok-4-fast"),
    ("Grok 4", "openrouter/x-ai/grok-4-07-09"),
    ("Grok 3", "openrouter/x-ai/grok-3"),
    ("Grok 3 Mini", "openrouter/x-ai/grok-3-mini"),
    ("Grok Code Fast", "openrouter/x-ai/grok-code-fast-1"),
    # DeepSeek
    ("DeepSeek V3.2", "openrouter/deepseek/deepseek-v3.2"),
    ("DeepSeek V3.2 Speciale", "openrouter/deepseek/deepseek-v3.2-speciale"),
    # Google
    ("Gemini 3 Pro", "openrouter/google/gemini-3-pro-preview"),
    ("Gemini 3 Flash", "openrouter/google/gemini-3-flash-preview"),
    # Mistral
    ("Mistral Large 3", "openrouter/mistralai/mistral-large-2512"),
    ("Devstral 2", "openrouter/mistralai/devstral-2512"),
    # Qwen
    ("Qwen3 Max", "openrouter/qwen/qwen3-max-thinking"),
    ("Qwen3 Coder", "openrouter/qwen/qwen3-coder-next"),
]
_LABEL_TO_MODEL = {label: model_id for label, model_id in _MODEL_OPTIONS}
_MODEL_TO_LABEL = {model_id: label for label, model_id in _MODEL_OPTIONS}
MODEL_LABELS = [label for label, _ in _MODEL_OPTIONS]

_TOOL_TAG = {
    "get_market_data": "market data",
    "get_multi_timeframe": "multi-TF",
    "search_web": "web search",
    "get_my_costs": "costs",
    "store_memory": "memory",
    "recall_memory": "memory",
    "get_account": "account",
    "execute_trade": "trade",
    "close_position": "close",
    "modify_position": "modify",
    "delete_memory": "delete",
    "manage_watchpoints": "watchpoints",
    "get_trade_stats": "stats",
    "explore_memory": "explore",
    "manage_conflicts": "conflicts",
    "manage_clusters": "clusters",
}


# Regex for extracting confidence from bot response text (fallback when no tool call)
_CONF_PCT_RE = re.compile(r'(?:confidence|conviction)[^:]*[:\s]+([0-9]{1,3})\s*%', re.IGNORECASE)
_CONF_DEC_RE = re.compile(r'(?:confidence|conviction)[^:]*[:\s]+(0\.[0-9]+)', re.IGNORECASE)


def _extract_confidence_from_text(text: str) -> float:
    """Parse confidence/conviction score from response text. Returns -1.0 if not found."""
    m = _CONF_PCT_RE.search(text)
    if m:
        val = float(m.group(1)) / 100.0
        if 0.0 <= val <= 1.0:
            return val
    m = _CONF_DEC_RE.search(text)
    if m:
        val = float(m.group(1))
        if 0.0 <= val <= 1.0:
            return val
    return -1.0


class Message(BaseModel):
    """Chat message model."""
    sender: str  # "user" or "hynous"
    content: str
    timestamp: str
    tools_used: list[str] = []
    show_avatar: bool = True  # False when grouped with previous same-sender message


class WakeItem(BaseModel):
    """Unified wake item for the activity sidebar."""
    category: str = ""  # fill, review, watchpoint, wake, learning, error
    content: str = ""  # first line (truncated for sidebar display)
    full_content: str = ""  # complete response text
    timestamp: str = ""


class Activity(BaseModel):
    """Activity log entry."""
    type: str  # "chat", "trade", "alert", "system"
    title: str
    level: str  # "info", "success", "warning", "error"
    timestamp: str


class DaemonActivity(BaseModel):
    """Daemon activity log entry (from daemon_log.py)."""
    type: str       # "wake", "watchpoint", "fill", "learning", "review", "error", "skip"
    title: str
    detail: str
    timestamp: str


class Position(BaseModel):
    """Trading position."""
    symbol: str
    symbol_short: str = ""
    side: str  # "long" or "short"
    size: float  # USD notional
    entry: float  # entry price
    mark: float = 0.0  # current mark price
    pnl: float = 0.0  # return % on equity
    pnl_usd: float = 0.0  # unrealized PnL in USD
    leverage: int = 1
    realized_pnl: float = 0.0  # Locked-in PnL from partial exits
    stop_loss: float = 0.0   # SL price from open bracket orders
    take_profit: float = 0.0  # TP price from open bracket orders
    contracts: int = 1
    delta: Optional[float] = None
    theta: Optional[float] = None


class PortfolioTableRow(BaseModel):
    """Display row model for home portfolio table."""
    instrument: str
    option_type: str
    subtitle: str = ""
    strike_expiry: str = "Spot"
    dte: str = "N/A"
    qty: str = "x1"
    entry: str = "$0.00"
    current: str = "$0.00"
    pnl: str = "$0"
    is_pnl_up: bool = False
    delta: str = "N/A"
    theta: str = "N/A"


class ClosedTrade(BaseModel):
    """Closed trade for journal display."""
    symbol: str
    side: str
    entry_px: float
    exit_px: float
    pnl_pct: float
    pnl_usd: float
    closed_at: str
    close_type: str = "full"
    duration_hours: float = 0.0
    duration_str: str = ""  # Pre-formatted: "4.2h" or "1.3d"
    date: str = ""  # Pre-formatted date string (YYYY-MM-DD)
    trade_id: str = ""  # close node ID (for expand tracking)
    thesis: str = ""  # from linked trade_entry
    stop_loss: float = 0.0
    take_profit: float = 0.0
    confidence: float = 0.0  # 0-1
    rr_ratio: float = 0.0
    trade_type: str = ""  # "micro" or "macro"
    mfe_pct: float = 0.0  # max favorable excursion ROE %
    fee_loss: bool = False  # directionally correct but fees ate profit
    detail_html: str = ""  # pre-rendered HTML for expanded view


class PhantomRecord(BaseModel):
    """Resolved phantom for regret tab display."""
    symbol: str
    side: str           # "long" / "short"
    result: str         # "missed_opportunity" / "good_pass"
    pnl_pct: float      # leveraged ROE %
    anomaly_type: str   # e.g. "price_spike", "book_flip"
    category: str       # "micro" / "macro"
    date: str           # pre-formatted date string
    headline: str       # anomaly headline from title
    phantom_id: str = ""  # node ID (for expand tracking)
    entry_px: float = 0.0
    exit_px: float = 0.0
    detail_html: str = ""  # pre-rendered HTML for expanded view


class PlaybookRecord(BaseModel):
    """Playbook entry for regret tab display."""
    title: str
    content: str
    date: str           # pre-formatted date string


class HomeMover(BaseModel):
    """Top mover entry for home page dashboard."""
    symbol: str = ""
    price: str = ""
    change_pct: float = 0.0
    change_str: str = ""
    volume: str = ""
    is_up: bool = True
    sparkline_svg: str = ""


class AlphaStatus(BaseModel):
    """Alpha gate snapshot for sidebar display."""
    symbol: str = ""
    direction: str = ""  # long / short / neutral
    confidence: float = 0.0
    confidence_str: str = ""
    detail: str = ""


def _build_trade_detail_html(d: dict) -> str:
    """Build pre-rendered HTML for an expanded trade detail panel."""
    parts = []
    thesis = d.get("thesis", "")
    if thesis:
        esc = thesis.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(
            f'<div style="color:#a3a3a3;font-size:0.82rem;line-height:1.5;'
            f'margin-bottom:8px;padding:8px 10px;background:#111;border-radius:4px;'
            f'border-left:2px solid #525252">{esc}</div>'
        )

    metrics = []
    tt = d.get("trade_type", "")
    if tt:
        color = "#a855f7" if tt == "micro" else "#3b82f6"
        metrics.append(("Type", f'<span style="color:{color};font-weight:500">{tt.upper()}</span>'))
    conf = d.get("confidence", 0)
    if conf > 0:
        metrics.append(("Conviction", f"{conf*100:.0f}%"))
    sl = d.get("stop_loss", 0)
    if sl > 0:
        metrics.append(("Stop Loss", f"${sl:,.2f}" if sl >= 1 else f"${sl:.4f}"))
    tp = d.get("take_profit", 0)
    if tp > 0:
        metrics.append(("Take Profit", f"${tp:,.2f}" if tp >= 1 else f"${tp:.4f}"))
    rr = d.get("rr_ratio", 0)
    if rr > 0:
        metrics.append(("R:R", f"{rr:.1f}:1"))
    mfe = d.get("mfe_pct", 0)
    if mfe > 0:
        metrics.append(("Peak Profit", f"+{mfe:.1f}% ROE"))

    if metrics:
        cells = "".join(
            f'<div style="padding:4px 0"><span style="color:#525252;font-size:0.7rem;'
            f'text-transform:uppercase;letter-spacing:0.05em;display:block">{label}</span>'
            f'<span style="color:#e5e5e5;font-size:0.82rem">{val}</span></div>'
            for label, val in metrics
        )
        parts.append(
            f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px 16px">{cells}</div>'
        )

    if not parts:
        return '<div style="color:#525252;font-size:0.82rem">No details available</div>'
    return "".join(parts)


def _build_phantom_detail_html(sig: dict, headline: str = "") -> str:
    """Build pre-rendered HTML for an expanded phantom detail panel."""
    parts = []
    ep = sig.get("entry_price", 0) or sig.get("entry_px", 0)
    xp = sig.get("exit_price", 0) or sig.get("exit_px", 0)
    if ep > 0:
        parts.append(f'<span style="color:#525252">Entry:</span> ${ep:,.2f}')
    if xp > 0:
        parts.append(f'<span style="color:#525252">Exit:</span> ${xp:,.2f}')
    cat = sig.get("category", "")
    if cat:
        color = "#a855f7" if cat == "micro" else "#3b82f6"
        parts.append(f'<span style="color:{color};font-weight:500">{cat.upper()}</span>')
    at = sig.get("anomaly_type", "")
    if at and headline:
        esc = headline.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(f'<span style="color:#525252">Signal:</span> {esc}')
    desc = sig.get("description", "")
    if desc:
        esc = desc.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(f'<div style="color:#a3a3a3;font-size:0.8rem;margin-top:4px">{esc}</div>')

    if not parts:
        return '<div style="color:#525252;font-size:0.82rem">No details available</div>'
    return '<div style="font-size:0.82rem;color:#e5e5e5;line-height:1.7">' + "<br>".join(parts) + "</div>"


def _enrich_trade(close_node: dict, nous_client) -> dict:
    """Follow edges from a trade_close node to its entry node, extract enrichment data."""
    result = {
        "trade_id": close_node.get("id", ""),
        "thesis": "",
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "confidence": 0.0,
        "rr_ratio": 0.0,
        "trade_type": "",
        "mfe_pct": 0.0,
        "fee_loss": False,
    }

    # Get MFE + trade_type + fee_loss from close node's signals
    try:
        close_body = json.loads(close_node.get("content_body", "{}"))
        close_signals = close_body.get("signals", {})
        result["mfe_pct"] = float(close_signals.get("mfe_pct", 0))
        result["fee_loss"] = bool(close_signals.get("fee_loss", False))
        if close_signals.get("trade_type"):
            result["trade_type"] = close_signals["trade_type"]
    except Exception:
        pass

    # Follow part_of edge (in) to find the linked trade_entry node
    node_id = close_node.get("id")
    if not node_id or not nous_client:
        return result

    try:
        edges = nous_client.get_edges(node_id, direction="in")
        entry_node = None
        for edge in edges:
            if edge.get("type") == "part_of":
                source_id = edge.get("source_id")
                if source_id:
                    entry_node = nous_client.get_node(source_id)
                    if entry_node:
                        break

        # Fallback: list by subtype if no edge found (search fails without embeddings)
        if not entry_node:
            try:
                close_body_fb = json.loads(close_node.get("content_body", "{}"))
                symbol = close_body_fb.get("signals", {}).get("symbol", "")
                closed_at = close_node.get("created_at", "")
                if symbol:
                    candidates = nous_client.list_nodes(
                        subtype="custom:trade_entry",
                        created_before=closed_at if closed_at else None,
                        limit=10,
                    )
                    # list_nodes returns newest first — first symbol match is best
                    for candidate in candidates:
                        title = candidate.get("content_title", "")
                        if symbol.upper() in title.upper():
                            entry_node = candidate
                            break
            except Exception:
                pass

        if not entry_node:
            return result

        # Parse entry node
        entry_body_raw = entry_node.get("content_body", "")
        try:
            entry_body = json.loads(entry_body_raw)
        except (json.JSONDecodeError, TypeError):
            return result

        # Thesis text
        result["thesis"] = entry_body.get("text", "")
        # Extract thesis from text field — it starts with "Thesis: "
        if result["thesis"].startswith("Thesis: "):
            # Get just the thesis part (before "Entry:")
            lines = result["thesis"].split("\n")
            thesis_parts = []
            for line in lines:
                if line.startswith("Entry:") or line.startswith("Stop Loss:") or line.startswith("Confidence:"):
                    break
                thesis_parts.append(line.replace("Thesis: ", "", 1) if line.startswith("Thesis: ") else line)
            result["thesis"] = "\n".join(thesis_parts).strip()

        # Signals
        entry_signals = entry_body.get("signals", {})
        result["stop_loss"] = float(entry_signals.get("stop", 0))
        result["take_profit"] = float(entry_signals.get("target", 0))
        result["confidence"] = float(entry_signals.get("confidence", 0))
        result["rr_ratio"] = float(entry_signals.get("rr_ratio", 0))
        # Only set from entry if close signals didn't already have it
        if not result["trade_type"]:
            result["trade_type"] = entry_signals.get("trade_type", "macro")

    except Exception:
        pass

    return result


class DaemonActivityFormatted(BaseModel):
    """Daemon activity with pre-formatted relative time."""
    type: str = ""
    title: str = ""
    detail: str = ""
    time_display: str = ""  # "3m ago"


class WatchpointGroup(BaseModel):
    """Watchpoint group for a single symbol (used in accordion)."""
    symbol: str = ""
    count: str = "0"
    detail_html: str = ""
    price: str = ""
    change_str: str = ""
    is_up: bool = True
    sparkline_svg: str = ""


class ClusterDisplay(BaseModel):
    """Cluster card data with pre-rendered health bar HTML."""
    name: str = ""
    node_count: str = "0"
    health_html: str = ""
    accent: str = "#525252"


class ConflictItem(BaseModel):
    """Structured conflict for per-item resolution UI."""
    conflict_id: str = ""
    conflict_type: str = ""
    confidence: str = ""
    old_title: str = ""
    old_body: str = ""
    new_title: str = ""
    new_body: str = ""


# --- Agent + Daemon singletons (shared across all sessions) ---

_agent = None
_agent_error: Optional[str] = None
_daemon = None
_agent_lock = threading.Lock()


def _get_agent():
    """Lazily initialize the Hynous agent and daemon.

    Thread-safe: uses double-checked locking so only one Agent is created
    even when called concurrently from stream_response and init_and_start_daemon.
    """
    global _agent, _agent_error, _daemon

    if _agent is not None:
        return _agent

    with _agent_lock:
        # Double-check after acquiring lock
        if _agent is not None:
            return _agent

        try:
            from hynous.nous.server import ensure_running
            if not ensure_running():
                logger.warning("Nous server not available — memory tools will fail")

            from hynous.intelligence import Agent
            _agent = Agent()
            _agent_error = None
            logger.info("Hynous agent initialized successfully")

            # Apply saved model preferences (survives restarts)
            prefs = _load_model_prefs()
            if prefs:
                main, sub = prefs
                if main:
                    _agent.config.agent.model = main
                if sub:
                    _agent.config.memory.compression_model = sub
                logger.info("Applied saved model prefs: %s / %s", main, sub)

            # Apply saved trading settings (propagates to config + prompt)
            try:
                from hynous.core.trading_settings import get_trading_settings
                _apply_trading_settings(get_trading_settings())
            except Exception:
                pass

            if _agent.config.daemon.enabled and _daemon is None:
                from hynous.intelligence.daemon import Daemon
                _daemon = Daemon(_agent, _agent.config)
                _daemon.start()
                logger.info("Daemon auto-started")

            if _agent.config.discord.enabled:
                try:
                    from hynous.discord.bot import start_bot
                    start_bot(_agent, _agent.config)
                except Exception as e:
                    logger.warning("Discord bot failed to start: %s", e)

            return _agent
        except Exception as e:
            _agent_error = str(e)
            logger.error(f"Failed to initialize agent: {e}")
            return None


def _apply_trading_settings(ts) -> None:
    """Propagate TradingSettings to agent, daemon config, and prompt."""
    if _agent is None:
        return
    try:
        # Rebuild system prompt (picks up new values via _build_ground_rules)
        _agent.rebuild_system_prompt()
        # Propagate to config objects used by daemon
        _agent.config.daemon.max_daily_loss_usd = ts.max_daily_loss_usd
        _agent.config.daemon.max_open_positions = ts.max_open_positions
    except Exception:
        pass


def _apply_execution_settings(live: bool) -> None:
    """Apply execution mode to the live agent config."""
    if _agent is None:
        return
    try:
        _agent.config.execution.mode = "live_confirm" if live else "paper"
        if getattr(_agent.config, "alpaca", None) is not None:
            _agent.config.alpaca.paper = not live
        # Reset providers so next call re-reads config for live/paper
        try:
            from hynous.data.providers import alpaca as alpaca_mod
            alpaca_mod._provider = None
        except Exception:
            pass
    except Exception:
        pass


def _config_path() -> Path:
    """Locate config/default.yaml from the dashboard module."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "config").is_dir():
            return current / "config" / "default.yaml"
        current = current.parent
    return Path("config/default.yaml")


def _update_execution_config_file(live: bool) -> None:
    """Persist execution mode + Alpaca paper flag to config/default.yaml."""
    path = _config_path()
    if not path.exists():
        return
    try:
        lines = path.read_text().splitlines()
    except Exception:
        return

    section = None
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.endswith(":") and not line.startswith(" "):
            section = stripped[:-1]
        if section == "execution" and line.lstrip().startswith("mode:"):
            indent = line[:len(line) - len(line.lstrip())]
            mode = "live_confirm" if live else "paper"
            line = f'{indent}mode: "{mode}"'
        elif section == "alpaca" and line.lstrip().startswith("paper:"):
            indent = line[:len(line) - len(line.lstrip())]
            val = "false" if live else "true"
            line = f"{indent}paper: {val}"
        out.append(line)

    try:
        path.write_text("\n".join(out) + "\n")
    except Exception:
        return


def _reset_agent():
    """Force re-initialization of the agent. Call after code changes."""
    global _agent, _agent_error
    _agent = None
    _agent_error = None


class AppState(rx.State):
    """Main application state."""

    # === Auth State ===
    is_authenticated: bool = True
    show_splash: bool = False
    show_dock: bool = False
    login_error: str = ""
    _session_token: str = rx.Cookie(name="hynous_session", max_age=86400 * 7)

    # === Auth Methods ===

    def authenticate(self, form_data: dict):
        """Validate password, set session cookie."""
        import hashlib, hmac, os
        password = form_data.get("password", "")
        expected = os.getenv("DASHBOARD_PASSWORD", "")
        if not expected:
            self.is_authenticated = True
            self.show_splash = False
            return AppState.load_page
        if hmac.compare_digest(password, expected):
            token = hashlib.sha256(expected.encode()).hexdigest()
            self._session_token = token
            self.is_authenticated = True
            self.show_splash = False
            self.login_error = ""
            return AppState.load_page
        else:
            self.login_error = "Wrong password"

    def enter_app(self):
        """Enter app from splash screen (no password)."""
        self.is_authenticated = True
        self.show_splash = False
        return AppState.load_page

    def logout(self):
        """Clear auth state and cookie."""
        self.is_authenticated = False
        self.show_splash = True
        self._session_token = ""

    def toggle_dock(self):
        """Toggle left dock visibility."""
        self.show_dock = not self.show_dock

    def _check_session(self):
        """Validate session cookie against expected password hash."""
        import hashlib, os
        expected = os.getenv("DASHBOARD_PASSWORD", "")
        if not expected:
            return self.is_authenticated
        valid_token = hashlib.sha256(expected.encode()).hexdigest()
        if self._session_token == valid_token:
            self.is_authenticated = True
            return True
        self.is_authenticated = False
        return False

    @rx.var(cache=False)
    def is_equities(self) -> bool:
        return True

    # === Chat State ===
    messages: List[Message] = []
    current_input: str = ""
    is_loading: bool = False
    streaming_text: str = ""
    active_tool: str = ""
    _pending_input: str = ""  # Backend-only: passed to background streaming task

    # === Agent State ===
    agent_status: str = "idle"  # "idle", "thinking", "online", "error"

    # === AI Confidence State ===
    ai_confidence: float = -1.0   # -1 = no signal, 0.0-1.0 = conviction score
    ai_confidence_context: str = ""  # e.g. "AAPL long"

    # === Portfolio State (updated by background poller) ===
    portfolio_value: float = 0.0  # Live account value
    portfolio_initial: float = 0.0  # Set from config/provider on first poll
    portfolio_change: float = 0.0  # % change from initial
    positions: List[Position] = []
    _polling: bool = False  # Guard against duplicate pollers

    # === Activity State ===
    activities: List[Activity] = []

    # === Debug State ===
    debug_traces: list[dict] = []
    debug_selected_trace_id: str = ""
    debug_selected_trace: dict = {}
    debug_filter_source: str = ""
    debug_filter_status: str = ""
    debug_expanded_spans: list[str] = []  # IDs of expanded spans (multi-open)

    # === Navigation ===
    current_page: str = "home"
    trading_selected_symbol: str = ""  # Currently viewed position in trading tab

    @rx.var(cache=False)
    def trading_chart_ticker(self) -> str:
        """Underlying ticker for the chart (strips OCC suffix from option symbols)."""
        import re
        sym = self.trading_selected_symbol
        if not sym:
            return ""
        m = re.match(r'^([A-Z]{1,6})\d{6}[CP]\d{8}$', sym)
        return m.group(1) if m else sym

    @rx.var(cache=False)
    def trading_chart_url(self) -> str:
        """TradingView embed URL for the selected symbol."""
        ticker = self.trading_chart_ticker
        if not ticker:
            return ""
        return (
            f"https://www.tradingview.com/widgetembed/"
            f"?symbol={ticker}&interval=5&theme=dark&style=1&locale=en"
            f"&hide_top_toolbar=0&hide_side_toolbar=0&allow_symbol_change=0"
            f"&save_image=0&calendar=0&hide_legend=0&hotlist=0&details=0"
        )

    @rx.var(cache=False)
    def trading_selected_mark_str(self) -> str:
        """Mark price string for the selected trading position."""
        for pos in self.positions:
            if pos.symbol == self.trading_selected_symbol:
                return f"${pos.mark:,.2f}"
        return "—"

    @rx.var(cache=False)
    def trading_position_symbols(self) -> list[str]:
        """List of open position symbols for the chart dropdown."""
        return [pos.symbol for pos in self.positions]

    trading_timeframe: str = "15m"

    def set_trading_timeframe(self, tf: str):
        self.trading_timeframe = tf

    @rx.var(cache=False)
    def trading_live_chart_url(self) -> str:
        """URL for the clean Plotly chart in the trading tab."""
        ticker = self.trading_chart_ticker
        if not ticker:
            return ""
        params = f"symbol={ticker}&tf={self.trading_timeframe}"
        # Only add price lines for non-option symbols (options have tiny premiums
        # that are completely off-scale compared to the underlying chart)
        import re as _re2
        is_option = bool(_re2.match(r'^[A-Z]{1,6}\d{6}[CP]\d{8}$', self.trading_selected_symbol))
        if not is_option:
            for pos in self.positions:
                if pos.symbol == self.trading_selected_symbol:
                    if pos.entry > 0:
                        params += f"&entry={pos.entry}"
                    if pos.stop_loss > 0:
                        params += f"&sl={pos.stop_loss}"
                    if pos.take_profit > 0:
                        params += f"&tp={pos.take_profit}"
                    if pos.side:
                        params += f"&side={pos.side}"
                    break
        return f"/trading_chart.html?{params}"

    # === Live Trading State ===
    live_symbol: str = "AAPL"
    live_timeframe: str = "15m"
    live_entry_px: float = 0.0
    live_sl_px: float = 0.0
    live_tp_px: float = 0.0
    live_side: str = ""

    @rx.var(cache=False)
    def live_chart_url(self) -> str:
        """URL for the live trading chart iframe."""
        params = f"symbol={self.live_symbol}&tf={self.live_timeframe}"
        if self.live_entry_px > 0:
            params += f"&entry={self.live_entry_px}"
        if self.live_sl_px > 0:
            params += f"&sl={self.live_sl_px}"
        if self.live_tp_px > 0:
            params += f"&tp={self.live_tp_px}"
        if self.live_side:
            params += f"&side={self.live_side}"
        return f"/live_chart.html?{params}"

    # === Unread Indicators ===
    chat_unread: bool = False
    journal_unread: bool = False
    memory_unread: bool = False

    # === Sparkline ===
    portfolio_sparkline_svg: str = ""
    portfolio_history_last_save_ts: float = 0.0

    # === Support / Resistance ===
    support_levels_html: str = ""

    # === Collapsible Cards ===
    news_expanded: bool = True
    clusters_sidebar_expanded: bool = True

    # === Activity Sidebar (Chat Page) ===
    wake_feed: List[WakeItem] = []
    wake_detail_content: str = ""
    wake_detail_category: str = ""
    wake_detail_time: str = ""
    wake_detail_open: bool = False

    # === Home Dashboard State ===
    home_movers: List[HomeMover] = []
    home_movers_last_refresh_date: str = ""
    home_movers_last_refresh_ts: float = 0.0
    home_alert_input: str = ""
    portfolio_area_svg: str = ""

    # === Alpha Gate Sidebar ===
    alpha_status: List[AlphaStatus] = []
    alpha_last_refresh_ts: float = 0.0

    # === Trading Settings ===
    settings_dirty: bool = False
    # Macro
    settings_macro_sl_min: float = 1.0
    settings_macro_sl_max: float = 5.0
    settings_macro_tp_min: float = 2.0
    settings_macro_tp_max: float = 15.0
    settings_macro_lev_min: int = 5
    settings_macro_lev_max: int = 20
    # Micro
    settings_micro_sl_min: float = 0.2
    settings_micro_sl_warn: float = 0.3
    settings_micro_sl_max: float = 0.5
    settings_micro_tp_min: float = 0.15
    settings_micro_tp_max: float = 1.0
    settings_micro_leverage: int = 20
    # Risk
    settings_rr_floor_reject: float = 1.0
    settings_rr_floor_warn: float = 1.5
    settings_risk_cap_reject: float = 10.0
    settings_risk_cap_warn: float = 5.0
    settings_roe_reject: float = 25.0
    settings_roe_warn: float = 15.0
    settings_roe_target: float = 15.0
    # Sizing
    settings_tier_high: int = 30
    settings_tier_medium: int = 20
    settings_tier_speculative: int = 10
    settings_tier_pass: float = 0.3
    # Limits
    settings_max_position: float = 10000
    settings_max_positions: int = 3
    settings_max_daily_loss: float = 100
    settings_live_trading: bool = False

    # === Collapsible Toggles ===

    def toggle_news_expanded(self):
        self.news_expanded = not self.news_expanded

    def toggle_clusters_sidebar(self):
        self.clusters_sidebar_expanded = not self.clusters_sidebar_expanded

    def view_wake_detail(self, content: str, category: str, timestamp: str):
        """Open wake detail dialog with full content."""
        self.wake_detail_content = content
        self.wake_detail_category = category
        self.wake_detail_time = timestamp
        self.wake_detail_open = True

    def close_wake_detail(self):
        self.wake_detail_open = False

    # === Stop Generation ===

    def stop_generation(self):
        """Interrupt agent stream."""
        global _agent
        if _agent:
            _agent.abort_stream()
        self.is_loading = False
        self.active_tool = ""
        self.agent_status = "idle"

    # === Chat Actions ===

    def set_input(self, value: str):
        """Update the current input value."""
        self.current_input = value

    def _append_msg(self, msg: Message):
        """Append a message, setting show_avatar based on previous sender."""
        if self.messages and self.messages[-1].sender == msg.sender:
            msg.show_avatar = False
        self.messages.append(msg)

    def _recompute_avatars(self):
        """Recompute show_avatar for all messages (used after bulk load)."""
        for i, msg in enumerate(self.messages):
            if i == 0:
                msg.show_avatar = True
            else:
                msg.show_avatar = msg.sender != self.messages[i - 1].sender

    def _format_time(self) -> str:
        """Format current time for display (Pacific timezone)."""
        from hynous.core.clock import now as pacific_now
        t = pacific_now()
        tz = t.strftime("%Z").lower()  # "pst" or "pdt"
        return t.strftime("%I:%M %p").lstrip("0").lower() + f" {tz}"

    def send_message(self, form_data: dict = {}):
        """Send a message and kick off background streaming.

        Accepts form data from the uncontrolled input (form submit sends
        {message: str}). Can also be called programmatically via
        _pending_input for suggestions and quick-chat.
        """
        # Get message from form data or _pending_input (for programmatic sends)
        text = form_data.get("message", "").strip() if form_data else ""
        if not text:
            text = self._pending_input.strip()
        if not text:
            return

        # Add user message
        user_msg = Message(
            sender="user",
            content=text,
            timestamp=self._format_time()
        )
        self._append_msg(user_msg)

        # Store input for background task
        self._pending_input = text
        self.is_loading = True
        self.streaming_text = ""
        self.active_tool = ""
        self.agent_status = "thinking"
        self.ai_confidence = -1.0
        self.ai_confidence_context = ""

        # Chain to background streaming task (state delta with user msg is sent first)
        return AppState.stream_response

    @_background
    async def stream_response(self):
        """Stream agent response as a background task.

        Runs chat_stream() in a thread so it never blocks the event loop.
        State updates are pushed via async with self between chunks.
        """
        async with self:
            user_input = self._pending_input

        # Run in thread so agent init (7s on first call) doesn't block event loop
        agent = await asyncio.to_thread(_get_agent)
        tools_used = []

        if agent:
            try:
                # Run sync streaming generator in a thread, consume via queue
                import queue
                chunk_queue: queue.Queue = queue.Queue()
                sentinel = object()

                def _produce():
                    try:
                        for chunk_type, chunk_data in agent.chat_stream(user_input):
                            chunk_queue.put((chunk_type, chunk_data))
                    except Exception as e:
                        chunk_queue.put(("error", str(e)))
                    chunk_queue.put(sentinel)

                # Start producer thread
                import threading
                producer = threading.Thread(target=_produce, daemon=True)
                producer.start()

                # Consume chunks in batches — push state every ~80ms
                # instead of per-token. Reduces state lock acquisitions
                # and computed var evaluations by ~10-50x.
                done = False
                while not done:
                    text_batch = []
                    tool_event = None
                    error_msg = None
                    replace_text = None

                    # Drain all available chunks without blocking
                    confidence_event: dict | None = None
                    while True:
                        try:
                            item = chunk_queue.get_nowait()
                        except Exception:
                            break  # queue empty

                        if item is sentinel:
                            done = True
                            break
                        chunk_type, chunk_data = item
                        if chunk_type == "error":
                            error_msg = chunk_data
                            done = True
                            break
                        elif chunk_type == "text":
                            text_batch.append(chunk_data)
                        elif chunk_type == "tool":
                            tool_event = chunk_data
                            break  # Push tool events immediately
                        elif chunk_type == "replace":
                            replace_text = chunk_data
                        elif chunk_type == "confidence":
                            try:
                                confidence_event = json.loads(chunk_data)
                            except Exception:
                                pass

                    # Push batched updates in a single state lock
                    if text_batch or tool_event or error_msg or replace_text or confidence_event:
                        async with self:
                            if error_msg:
                                self.streaming_text += f"\n\nSomething went wrong: {error_msg}"
                                self.agent_status = "error"
                            if replace_text:
                                # Agent stripped text tool calls — replace with clean version
                                self.streaming_text = replace_text
                            elif text_batch:
                                self.active_tool = ""
                                self.streaming_text += "".join(text_batch)
                            if tool_event:
                                if self.streaming_text.strip():
                                    self._append_msg(Message(
                                        sender="hynous",
                                        content=_highlight(self.streaming_text),
                                        timestamp=self._format_time(),
                                    ))
                                    self.streaming_text = ""
                                self.active_tool = tool_event
                                if tool_event not in tools_used:
                                    tools_used.append(tool_event)
                            if confidence_event:
                                conf_val = confidence_event.get("value", -1)
                                if isinstance(conf_val, (int, float)) and 0.0 <= conf_val <= 1.0:
                                    self.ai_confidence = float(conf_val)
                                    sym = confidence_event.get("symbol", "")
                                    side = confidence_event.get("side", "")
                                    if sym and side:
                                        self.ai_confidence_context = f"{sym.upper()} {side}"

                    if not done:
                        await asyncio.sleep(0.08)  # ~80ms between UI pushes

                producer.join(timeout=5)

            except Exception as e:
                logger.error(f"Agent chat error: {e}")
                async with self:
                    self.streaming_text += "\n\nSomething went wrong on my end. Give me a moment and try again."
                    self.agent_status = "error"
        else:
            async with self:
                self.streaming_text = (
                    f"I can't connect to my brain right now. "
                    f"Make sure OPENROUTER_API_KEY is set in your .env file.\n\n"
                    f"Error: {_agent_error or 'Unknown'}"
                )
                self.agent_status = "error"

        # Finalize
        async with self:
            response = self.streaming_text
            display_tools = [_TOOL_TAG.get(t, t) for t in tools_used]
            hynous_msg = Message(
                sender="hynous",
                content=_highlight(response),
                timestamp=self._format_time(),
                tools_used=display_tools,
            )
            self._append_msg(hynous_msg)

            # Fallback: if no tool-based confidence was captured, try parsing the text
            if self.ai_confidence < 0 and response:
                parsed_conf = _extract_confidence_from_text(response)
                if parsed_conf >= 0:
                    self.ai_confidence = parsed_conf

            self.streaming_text = ""
            self.active_tool = ""
            self._add_activity("chat", f"Chat: {user_input[:30]}...", "info")
            self.is_loading = False
            if self.agent_status != "error":
                self.agent_status = "idle"

        # Persist to disk (outside state lock)
        self._save_chat(agent)

    def send_suggestion(self, suggestion: str):
        """Send a suggestion as a message."""
        self._pending_input = suggestion
        return self.send_message()

    def load_page(self):
        """Load persisted messages + start portfolio polling on page load."""
        # Session check (skipped — splash disabled, always authenticated)
        self.is_authenticated = True

        # Always start fresh on page load (no persisted chat)
        self.messages = []
        self._recompute_avatars()
        # Load persisted portfolio history for chart continuity
        try:
            from hynous.core.persistence import load_portfolio_history
            hist = [v for v in load_portfolio_history() if v > 0]
            if hist:
                _portfolio_history.clear()
                _portfolio_history.extend(hist[-120:])
            elif self.portfolio_value > 0:
                # Seed with slight variation so chart isn't flat on first run
                base = float(self.portfolio_value)
                _portfolio_history.clear()
                _portfolio_history.extend([
                    round(base * 0.985, 2),
                    round(base * 0.992, 2),
                    round(base * 0.978, 2),
                    round(base * 1.004, 2),
                    round(base * 0.990, 2),
                    round(base * 1.012, 2),
                    round(base * 0.998, 2),
                    round(base * 1.020, 2),
                    round(base * 1.006, 2),
                    round(base * 1.015, 2),
                    round(base * 0.995, 2),
                    round(base * 1.008, 2),
                ])
            if len(_portfolio_history) >= 2:
                self.portfolio_area_svg = _build_area_svg(
                    _portfolio_history, width=760, height=240, color="#facc15"
                )
        except Exception:
            pass

        # Sync daemon status for this session (global _daemon may already be running)
        if _daemon is not None and _daemon.is_running:
            self.agent_status = "idle"

        # Start background tasks (init_daemon ensures agent+daemon start)
        return [AppState.init_daemon, AppState.poll_portfolio, AppState.load_watchpoints, AppState.load_clusters, AppState.load_home_movers, AppState.load_stock_news, AppState.load_alpha_status]

    @_background
    async def load_stock_news(self):
        """Fetch latest news for dashboard (Alpaca)."""
        news = await asyncio.to_thread(self._fetch_stock_news)
        async with self:
            # Only overwrite if we actually got something
            if news:
                self.stock_news = news
        new_ids = await asyncio.to_thread(AppState._process_news, news, list(self.news_seen_ids or []))
        if new_ids:
            async with self:
                merged = (self.news_seen_ids or []) + new_ids
                self.news_seen_ids = merged[-500:]

    @_background
    async def load_alpha_status(self):
        """Fetch alpha gate status for sidebar."""
        alpha = await asyncio.to_thread(self._fetch_alpha_status)
        async with self:
            self.alpha_status = alpha

    @staticmethod
    def _fetch_stock_news() -> list[dict]:
        """Fetch news from Alpaca."""
        try:
            from hynous.core.config import load_config
            from hynous.data.providers.alpaca import get_provider as get_alpaca
            cfg = load_config()
            alpaca = get_alpaca(cfg)
            articles = alpaca.get_stock_news([], limit=20)
            if articles:
                return articles
        except Exception:
            pass
        return []

    @staticmethod
    def _fetch_alpha_status() -> list["AlphaStatus"]:
        """Compute alpha gate status for positions or tracked symbols."""
        try:
            from hynous.core.config import load_config
            from hynous.core.alpha_models import evaluate_alpha_models, aggregate_alpha
            from hynous.core.trading_settings import get_trading_settings
            import re as _re
            cfg = load_config()
            ts = get_trading_settings()

            occ_re = _re.compile(r'^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$')

            def _format_symbol(sym: str) -> str:
                m = occ_re.match(sym)
                if not m:
                    return sym
                underlying = m.group(1)
                yy, mm, dd = m.group(2), m.group(3), m.group(4)
                opt_type = "C" if m.group(5) == "C" else "P"
                strike = int(m.group(6)) / 1000.0
                strike_str = f"{strike:.0f}" if strike.is_integer() else f"{strike:.1f}"
                return f"{underlying} ({yy}-{mm}-{dd} {opt_type} {strike_str})"

            symbols = []
            # Prefer open positions if available (cached state)
            try:
                from hynous.data.providers import get_trading_provider
                state = get_trading_provider(cfg).get_user_state()
                for p in state.get("positions", []):
                    sym = (p.get("coin") or "").upper()
                    if sym:
                        symbols.append(sym)
            except Exception:
                pass

            if not symbols:
                symbols = list(getattr(getattr(cfg, "execution", None), "symbols", []) or [])[:5]

            models = getattr(ts, "rules_alpha_models", ["ema_cross", "rsi", "macd", "historical_returns"])
            params = getattr(ts, "rules_alpha_params", {})

            out = []
            for sym in symbols:
                signals = evaluate_alpha_models(sym, models, params)
                direction, conf, _ = aggregate_alpha(signals)
                detail = ", ".join(f"{s.model}:{s.direction}" for s in signals)
                out.append(AlphaStatus(
                    symbol=_format_symbol(sym),
                    direction=direction,
                    confidence=conf,
                    confidence_str=f"{int(conf * 100)}%",
                    detail=detail[:160],
                ))
            return out
        except Exception:
            return []
    @staticmethod
    def _process_news(news: list[dict], seen_ids: list[str]) -> list[str]:
        """Ingest news into memory and auto-trade based on setup."""
        if not news:
            return []
        seen = set(seen_ids or [])
        new_ids = []

        try:
            from hynous.data.providers import get_market_provider
            from hynous.data.providers.alpaca import get_provider as get_alpaca
            from hynous.core.config import load_config
            cfg = load_config()
            market = get_market_provider(cfg)
            alpaca = get_alpaca(cfg)
        except Exception:
            market = None
            alpaca = None

        def _sma(values, period):
            if len(values) < period:
                return None
            return sum(values[-period:]) / period

        def _rsi(values, period=14):
            if len(values) < period + 1:
                return None
            gains, losses = [], []
            for i in range(-period, 0):
                d = values[i] - values[i - 1]
                gains.append(max(d, 0))
                losses.append(max(-d, 0))
            avg_g = sum(gains) / period
            avg_l = sum(losses) / period
            if avg_l == 0:
                return 100.0
            rs = avg_g / avg_l
            return 100 - 100 / (1 + rs)

        def _analyze_headline(title: str, body: str = ""):
            t = f"{title} {body}".lower()
            bearish = [
                "miss", "misses", "cuts guidance", "guidance cut", "downgrade", "lawsuit",
                "sec", "investigation", "probe", "recall", "fraud", "bankruptcy",
                "layoffs", "weak demand", "slump", "plunge", "drop", "warns",
            ]
            bullish = [
                "beats", "beat", "raises guidance", "guidance raise", "upgrade",
                "record revenue", "record profit", "surge", "rally", "soars",
                "approval", "contract wins", "buyback", "dividend increase",
            ]
            macro = [
                "fed", "inflation", "cpi", "ppi", "jobs report", "rate", "yield",
                "gdp", "recession", "oil", "opec", "tariff", "trade war",
            ]
            impact = "unclear"
            bias = "neutral"
            stance = "flat"
            if any(k in t for k in macro):
                impact = "macro"
            if any(k in t for k in bullish):
                bias = "bullish"
                stance = "long"
                impact = "likely"
            if any(k in t for k in bearish):
                bias = "bearish"
                stance = "short"
                impact = "likely"
            return impact, bias, stance

        max_loss_usd = 50.0
        max_trades_per_run = 1
        trades_done = 0

        open_syms = set()
        try:
            if alpaca:
                pos = alpaca.get_user_state().get("positions", [])
                open_syms = {p["coin"] for p in pos}
        except Exception:
            pass

        for n in news:
            nid = str(n.get("id") or n.get("url") or n.get("title") or "")
            if not nid or nid in seen:
                continue
            new_ids.append(nid)
            title = n.get("title", "")
            body = n.get("body", "")
            impact, bias, stance = _analyze_headline(title, body)

            # Store in Nous
            try:
                import json as _json
                from datetime import datetime as _dt, timezone as _tz
                from hynous.nous.client import NousClient
                from hynous.core.config import load_config as _lc
                cfg = _lc()
                client = NousClient(cfg.nous.url)
                payload = {
                    "text": title,
                    "signals": {
                        "impact": impact,
                        "bias": bias,
                        "stance": stance,
                        "source": n.get("source", ""),
                        "url": n.get("url", ""),
                    },
                }
                client.create_node(
                    type="concept",
                    subtype="custom:news",
                    title=f"News: {title[:80]}",
                    body=_json.dumps(payload),
                    event_time=_dt.now(_tz.utc).isoformat(),
                )
            except Exception:
                pass

            if trades_done >= max_trades_per_run:
                continue
            if stance not in ("long", "short"):
                continue

            cats = n.get("categories", "")
            symbols = [s.strip().upper() for s in cats.split(",") if s.strip()]
            if not symbols:
                continue

            for sym in symbols[:3]:
                if sym in open_syms:
                    continue
                if not market or not alpaca:
                    continue
                try:
                    import time as _time
                    now_ms = int(_time.time() * 1000)
                    start_ms = now_ms - 60 * 24 * 60 * 60 * 1000
                    candles = market.get_candles(sym, "1d", start_ms, now_ms)
                    if len(candles) < 55:
                        continue
                    closes = [c["c"] for c in candles]
                    vols = [c["v"] for c in candles]
                    last = closes[-1]
                    sma20 = _sma(closes, 20)
                    sma50 = _sma(closes, 50)
                    rsi = _rsi(closes, 14)
                    vol_avg = _sma(vols, 20)
                    if not (sma20 and sma50 and rsi and vol_avg):
                        continue
                    vol_ratio = vols[-1] / vol_avg if vol_avg else 0

                    long_ok = last > sma20 and last > sma50 and 45 <= rsi <= 70 and vol_ratio >= 1.5
                    short_ok = last < sma20 and last < sma50 and 30 <= rsi <= 55 and vol_ratio >= 1.5
                    if stance == "long" and not long_ok:
                        continue
                    if stance == "short" and not short_ok:
                        continue

                    if stance == "long":
                        stop = min(sma20, last * 0.98)
                        tp = last + 2 * (last - stop)
                        side = "long"
                    else:
                        stop = max(sma20, last * 1.02)
                        tp = last - 2 * (stop - last)
                        side = "short"
                    risk_per_share = abs(last - stop)
                    if risk_per_share <= 0:
                        continue
                    qty = int(max_loss_usd / risk_per_share)
                    if qty < 1:
                        continue

                    alpaca.submit_market_bracket(
                        symbol=sym,
                        qty=qty,
                        side=side,
                        stop_loss=round(stop, 2),
                        take_profit=round(tp, 2),
                    )
                    trades_done += 1
                    open_syms.add(sym)
                    break
                except Exception:
                    continue

        return new_ids

    @_background
    async def init_daemon(self):
        """Ensure agent + daemon are initialized (runs in background on page load)."""
        agent = await asyncio.to_thread(_get_agent)
        if agent:
            async with self:
                self.selected_model = agent.config.agent.model
                self.selected_sub_model = agent.config.memory.compression_model

    def _save_chat(self, agent=None):
        """Persist current messages and agent history to disk."""
        try:
            from hynous.core.persistence import save
            ui_data = [m.model_dump() for m in self.messages]
            history = agent._history if agent else []
            save(ui_data, history)
        except Exception as e:
            logger.error(f"Failed to save chat: {e}")

    def clear_messages(self):
        """Clear all messages, agent history, and persisted data."""
        self.messages = []
        agent = _get_agent()
        if agent:
            agent.clear_history()
        try:
            from hynous.core.persistence import clear
            clear()
        except Exception:
            pass

    # === Portfolio Polling ===

    def start_polling(self):
        """Start the background portfolio poller on page load."""
        if not self._polling:
            return AppState.poll_portfolio

    @_background
    async def poll_portfolio(self):
        """Poll broker every few seconds for live portfolio data.

        Runs as a Reflex background task — outside the main state lock.
        Updates portfolio_value, portfolio_change, and positions.
        Zero LLM tokens — pure Python → broker REST.
        """
        async with self:
            if self._polling:
                return
            self._polling = True

        _cluster_tick = 0
        while True:
            try:
                # Run sync broker API call in a thread so it doesn't
                # block the Reflex event loop.
                state_data = await asyncio.to_thread(self._fetch_portfolio)

                if state_data is not None:
                    value, positions, initial = state_data
                    # Track portfolio history (skip invalid/zero values)
                    if value > 0:
                        if len(_portfolio_history) == 0:
                            # Seed on first valid value if no history exists yet
                            base = float(value)
                            _portfolio_history.extend([
                                round(base * 0.985, 2),
                                round(base * 0.992, 2),
                                round(base * 0.978, 2),
                                round(base * 1.004, 2),
                                round(base * 0.990, 2),
                                round(base * 1.012, 2),
                                round(base * 0.998, 2),
                                round(base * 1.020, 2),
                                round(base * 1.006, 2),
                                round(base * 1.015, 2),
                                round(base * 0.995, 2),
                                round(base * 1.008, 2),
                            ])
                        _portfolio_history.append(round(value, 2))
                        if len(_portfolio_history) > 120:
                            _portfolio_history.pop(0)
                    async with self:
                        self.portfolio_value = round(value, 2)
                        if initial > 0:
                            self.portfolio_initial = initial
                            self.portfolio_change = round(
                                ((value - initial) / initial) * 100, 2
                            )
                        self.positions = positions
                        # Update sparkline SVG
                        if len(_portfolio_history) >= 3:
                            self.portfolio_sparkline_svg = _build_sparkline_svg(
                                _portfolio_history, width=80, height=24,
                                color="#22c55e" if self.portfolio_change >= 0 else "#ef4444",
                            )
                        if len(_portfolio_history) >= 2:
                            self.portfolio_area_svg = _build_area_svg(
                                _portfolio_history, width=760, height=240, color="#facc15"
                            )
                        # Persist portfolio history once per minute
                        now_ts = time.time()
                        if now_ts - self.portfolio_history_last_save_ts >= 60:
                            try:
                                from hynous.core.persistence import save_portfolio_history
                                save_portfolio_history(_portfolio_history)
                                self.portfolio_history_last_save_ts = now_ts
                            except Exception:
                                pass
                        # Refresh stock news every minute
                        if now_ts - self.stock_news_last_refresh_ts >= 60:
                            try:
                                await self.load_stock_news()
                                self.stock_news_last_refresh_ts = now_ts
                            except Exception:
                                pass
                        # Live top movers refresh (every 5 minutes) + daily refresh at 6:30 PM PST
                        try:
                            from zoneinfo import ZoneInfo
                            now_la = datetime.now(ZoneInfo("America/Los_Angeles"))
                            now_ts = now_la.timestamp()
                            if now_ts - self.home_movers_last_refresh_ts >= 300:
                                await self.load_home_movers()
                                self.home_movers_last_refresh_ts = now_ts
                            if now_la.hour == 18 and now_la.minute == 30:
                                today = now_la.strftime("%Y-%m-%d")
                                if self.home_movers_last_refresh_date != today:
                                    await self.load_home_movers()
                                    self.home_movers_last_refresh_date = today
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Portfolio poll error: {e}")

            # Drain daemon chat queue — show daemon wakes in the chat feed
            try:
                from hynous.intelligence.daemon import get_daemon_chat_queue
                dq = get_daemon_chat_queue()
                new_daemon_msgs = []
                while not dq.empty():
                    try:
                        new_daemon_msgs.append(dq.get_nowait())
                    except Exception:
                        break
                # Also drain persistent wake log (catches anything missed)
                try:
                    from hynous.core.persistence import drain_wake_log
                    for entry in drain_wake_log():
                        # Dedup: skip if already in queue batch
                        if not any(d['response'] == entry['response'] for d in new_daemon_msgs):
                            new_daemon_msgs.append(entry)
                except Exception:
                    pass
                if new_daemon_msgs:
                    async with self:
                        self.is_waking = False  # Clear manual wake indicator
                        # Route ALL daemon wakes to sidebar activity feed
                        for item in new_daemon_msgs:
                            raw = item.get('response', '')
                            first_line = raw.split("\n")[0][:200] if raw else ""
                            # Classify wake type
                            evt = item.get('event_type', '')
                            src = item.get('type', '')
                            if evt == 'fill' or src == 'Fill':
                                cat = 'fill'
                            elif src in ('Review', 'Periodic'):
                                cat = 'review'
                            elif src == 'Watchpoint':
                                cat = 'watchpoint'
                            elif src == 'Learning':
                                cat = 'learning'
                            elif evt == 'error':
                                cat = 'error'
                            else:
                                cat = 'wake'
                            self.wake_feed = ([
                                WakeItem(
                                    category=cat,
                                    content=first_line,
                                    full_content=raw,
                                    timestamp=item.get('timestamp', self._format_time()),
                                )
                            ] + self.wake_feed)[:100]
                        # Journal unread for fills
                        for item in new_daemon_msgs:
                            if item.get('event_type') == 'fill':
                                if self.current_page != "journal":
                                    self.journal_unread = True
                                break
            except Exception:
                pass

            # Refresh memory data every ~60s (4 polls)
            _cluster_tick += 1
            if _cluster_tick % 4 == 0:
                try:
                    cluster_data, health_data, conflict_data, wp_data, sr_html = await asyncio.to_thread(
                        lambda: (
                            AppState._fetch_clusters(),
                            AppState._fetch_memory_health(),
                            AppState._fetch_conflicts(),
                            AppState._fetch_watchpoints(),
                            AppState._fetch_support_resistance(),
                        )
                    )
                    async with self:
                        self.cluster_displays = cluster_data["clusters"]
                        self.cluster_total = str(cluster_data["total"])
                        self.memory_node_count = health_data["node_count"]
                        self.memory_edge_count = health_data["edge_count"]
                        self.memory_health_ratio = health_data["health_ratio"]
                        self.memory_lifecycle_html = health_data["lifecycle_html"]
                        # Memory unread when new conflicts appear
                        if conflict_data["count"] != "0" and self.conflict_count == "0":
                            if self.current_page != "memory":
                                self.memory_unread = True
                        self.conflict_count = conflict_data["count"]
                        self.watchpoint_groups = wp_data["groups"]
                        self.watchpoint_count = str(wp_data["count"])
                        self.support_levels_html = sr_html
                except Exception:
                    pass

            # Refresh debug traces when on debug page
            try:
                async with self:
                    if self.current_page == "debug":
                        self.load_debug_traces()
                        # Also refresh selected trace if viewing one (live span updates)
                        if self.debug_selected_trace_id:
                            from hynous.core.request_tracer import get_tracer
                            trace = get_tracer().get_trace(self.debug_selected_trace_id)
                            if trace:
                                self.debug_selected_trace = trace
            except Exception:
                pass

            # Refresh alpha status every ~60s
            try:
                now_ts = time.time()
                if now_ts - self.alpha_last_refresh_ts >= 60:
                    await self.load_alpha_status()
                    self.alpha_last_refresh_ts = now_ts
            except Exception:
                pass

            # Daemon watchdog — restart if thread died or is hung
            try:
                if _daemon is not None:
                    if not _daemon.is_running:
                        logger.warning("Daemon thread died — restarting")
                        _daemon.start()
                    elif time.time() - _daemon._heartbeat > 180:
                        logger.warning("Daemon thread hung (no heartbeat for 3min) — restarting")
                        _daemon._running = False  # Signal loop to stop
                        if _daemon._thread:
                            _daemon._thread.join(timeout=5)
                        _daemon._thread = None
                        _daemon.start()
            except Exception as e:
                logger.debug(f"Daemon watchdog error: {e}")

            await asyncio.sleep(_POLL_INTERVAL)

    # === Activity Actions ===

    def _add_activity(self, type: str, title: str, level: str = "info"):
        """Add an activity to the log."""
        activity = Activity(
            type=type,
            title=title,
            level=level,
            timestamp=datetime.now().isoformat()
        )
        self.activities.insert(0, activity)
        # Keep only last 50 activities
        self.activities = self.activities[:50]

    # === Computed Vars ===

    @staticmethod
    def _fetch_portfolio() -> tuple[float, list["Position"]] | None:
        """Fetch portfolio data from active provider (sync, runs in thread)."""
        from hynous.data.providers import get_trading_provider
        from hynous.core.config import load_config

        config = load_config()
        provider = get_trading_provider(config=config)

        state = provider.get_user_state()
        value = state["account_value"]
        raw_positions = state["positions"]
        initial = config.execution.paper_balance
        option_symbols = [p["coin"] for p in raw_positions if _parse_occ_symbol(p.get("coin", ""))]
        option_greeks = provider.get_option_greeks(option_symbols) if option_symbols else {}

        # Fetch open orders to extract TP/SL from bracket legs
        tp_sl_map: dict[str, tuple[float, float]] = {}
        try:
            open_orders = provider.get_open_orders()
            by_symbol: dict[str, list] = {}
            for o in open_orders:
                by_symbol.setdefault(o.symbol, []).append(o)
            for sym, orders in by_symbol.items():
                tp, sl = 0.0, 0.0
                for o in orders:
                    otype = str(getattr(o, "type", "")).lower()
                    if "limit" in otype and getattr(o, "limit_price", None):
                        tp = float(o.limit_price)
                    if "stop" in otype and getattr(o, "stop_price", None):
                        sl = float(o.stop_price)
                tp_sl_map[sym] = (tp, sl)
        except Exception:
            pass

        positions = [
            Position(
                symbol=p["coin"],
                symbol_short=_clean_symbol(p["coin"]),
                side=p["side"],
                size=round(p["size_usd"], 2),
                entry=p["entry_px"],
                mark=p["mark_px"],
                pnl=round(p["return_pct"], 2),
                pnl_usd=round(p["unrealized_pnl"], 2),
                leverage=p.get("leverage", 1),
                realized_pnl=0.0,
                take_profit=tp_sl_map.get(p["coin"], (0.0, 0.0))[0],
                stop_loss=tp_sl_map.get(p["coin"], (0.0, 0.0))[1],
                contracts=int(abs(float(p.get("size", 1) or 1))),
                delta=option_greeks.get(p["coin"], {}).get("delta"),
                theta=option_greeks.get(p["coin"], {}).get("theta"),
            )
            for p in raw_positions
        ]

        return (round(value, 2), positions, initial)

    @rx.var
    def portfolio_value_str(self) -> str:
        """Formatted portfolio value — updated by background poller."""
        if self.portfolio_value > 0:
            return f"${self.portfolio_value:,.2f}"
        return "Connecting..."

    @rx.var
    def portfolio_change_str(self) -> str:
        """Formatted portfolio change %."""
        if self.portfolio_value == 0:
            return "Waiting for data"
        if self.portfolio_change == 0:
            return "Testnet trading"
        sign = "+" if self.portfolio_change > 0 else ""
        return f"{sign}{self.portfolio_change:.2f}% all time"

    @rx.var
    def portfolio_change_color(self) -> str:
        """Color for portfolio change."""
        if self.portfolio_change > 0:
            return "#22c55e"
        elif self.portfolio_change < 0:
            return "#ef4444"
        return "#fafafa"

    @rx.var(cache=False)
    def portfolio_change_amount_and_pct(self) -> str:
        """Formatted portfolio change in USD and percent."""
        if self.portfolio_value <= 0 or self.portfolio_initial <= 0:
            return "—"
        delta = self.portfolio_value - self.portfolio_initial
        sign = "+" if delta > 0 else "-"
        if delta == 0:
            sign = ""
        return f"{sign}${abs(delta):,.2f} ({self.portfolio_change:.1f}%)"

    @rx.var(cache=False)
    def total_invested(self) -> float:
        """Sum of open position notionals."""
        return round(sum(p.size for p in self.positions), 2)

    @rx.var(cache=False)
    def total_invested_str(self) -> str:
        """Formatted invested total."""
        if self.total_invested <= 0:
            return "$0.00"
        return f"${self.total_invested:,.2f}"

    @rx.var(cache=False)
    def positions_unrealized_pnl(self) -> float:
        """Sum of unrealized PnL across positions."""
        return round(sum(p.pnl_usd for p in self.positions), 2)

    @rx.var(cache=False)
    def positions_pnl_str_and_pct(self) -> str:
        """Formatted positions PnL in USD and percent of invested."""
        invested = self.total_invested
        if invested <= 0:
            return "—"
        pnl = self.positions_unrealized_pnl
        sign = "+" if pnl > 0 else "-"
        if pnl == 0:
            sign = ""
        pct = (pnl / invested) * 100 if invested > 0 else 0.0
        return f"{sign}${abs(pnl):,.2f} ({pct:.1f}%)"

    @rx.var(cache=False)
    def positions_pnl_color(self) -> str:
        """Color for unrealized PnL text."""
        pnl = self.positions_unrealized_pnl
        if pnl > 0:
            return "#22c55e"
        if pnl < 0:
            return "#ef4444"
        return "#a3a3a3"

    @rx.var(cache=False)
    def available_cash(self) -> float:
        """Estimate available cash from portfolio value minus invested."""
        if self.portfolio_value <= 0:
            return 0.0
        cash = self.portfolio_value - self.total_invested
        return round(cash if cash > 0 else 0.0, 2)

    @rx.var(cache=False)
    def available_cash_str(self) -> str:
        """Formatted available cash estimate."""
        return f"${self.available_cash:,.2f}"

    @rx.var(cache=False)
    def portfolio_last_updated(self) -> str:
        now = datetime.now()
        return now.strftime("%B %d, %I:%M %p")

    @rx.var(cache=False)
    def wallet_total_str(self) -> str:
        """Total monthly cost as formatted string."""
        try:
            from hynous.core.costs import get_month_summary
            s = get_month_summary()
            return f"${s['total_usd']:.2f}"
        except Exception:
            return "$0.00"

    @rx.var(cache=False)
    def wallet_subtitle(self) -> str:
        """Subtitle for wallet card."""
        try:
            from hynous.core.costs import get_month_summary
            s = get_month_summary()
            return f"{s['month']} operating costs"
        except Exception:
            return "This month"

    @rx.var(cache=False)
    def wallet_llm_cost(self) -> str:
        """Total LLM API cost string."""
        try:
            from hynous.core.costs import get_month_summary
            s = get_month_summary()
            return f"${s['llm']['total_cost_usd']:.2f}"
        except Exception:
            return "$0.00"

    @rx.var(cache=False)
    def wallet_llm_calls(self) -> str:
        """Total LLM API call count."""
        try:
            from hynous.core.costs import get_month_summary
            return str(get_month_summary()["llm"]["total_calls"])
        except Exception:
            return "0"

    @rx.var(cache=False)
    def wallet_llm_tokens(self) -> str:
        """Total LLM token usage string."""
        try:
            from hynous.core.costs import get_month_summary
            llm = get_month_summary()["llm"]
            return f"{llm['total_input_tokens']:,} in / {llm['total_output_tokens']:,} out"
        except Exception:
            return "0 in / 0 out"

    @rx.var(cache=False)
    def wallet_models_html(self) -> str:
        """Pre-rendered HTML for per-model cost breakdown in wallet dialog."""
        try:
            from hynous.core.costs import get_month_summary
            from html import escape
            models = get_month_summary()["llm"]["models"]
            if not models:
                return '<div style="color:#404040;font-size:0.75rem">No LLM calls yet</div>'
            rows = []
            for m in models:
                if m["calls"] == 0:
                    continue
                rows.append(
                    f'<div style="display:flex;align-items:center;gap:0.5rem;padding:0.375rem 0;'
                    f'border-bottom:1px solid #1a1a1a">'
                    f'<div style="flex:1;min-width:0">'
                    f'<div style="color:#d4d4d4;font-size:0.78rem;font-weight:500">'
                    f'{escape(m["label"])}</div>'
                    f'<div style="display:flex;gap:0.5rem;font-size:0.65rem;margin-top:2px">'
                    f'<span style="color:#a78bfa;font-weight:500">${m["cost_usd"]:.2f}</span>'
                    f'<span style="color:#404040">\u00b7</span>'
                    f'<span style="color:#525252">{m["calls"]} calls</span>'
                    f'<span style="color:#404040">\u00b7</span>'
                    f'<span style="color:#404040">{m["input_tokens"]:,} in / {m["output_tokens"]:,} out</span>'
                    f'</div></div></div>'
                )
            return "".join(rows)
        except Exception:
            return ""

    @rx.var(cache=False)
    def wallet_sonnet_cost(self) -> str:
        """Sonnet model cost string (legacy)."""
        return "$0.00"

    @rx.var(cache=False)
    def wallet_sonnet_calls(self) -> str:
        """Sonnet model call count (legacy)."""
        try:
            from hynous.core.costs import get_month_summary
            return str(get_month_summary()['claude']['sonnet']['calls'])
        except Exception:
            return "0"

    @rx.var(cache=False)
    def wallet_haiku_cost(self) -> str:
        """Haiku model cost string."""
        try:
            from hynous.core.costs import get_month_summary
            return f"${get_month_summary()['claude']['haiku']['cost_usd']:.2f}"
        except Exception:
            return "$0.00"

    @rx.var(cache=False)
    def wallet_haiku_calls(self) -> str:
        """Haiku model call count."""
        try:
            from hynous.core.costs import get_month_summary
            return str(get_month_summary()['claude']['haiku']['calls'])
        except Exception:
            return "0"

    @rx.var(cache=False)
    def wallet_cache_savings(self) -> str:
        """Cache savings string."""
        try:
            from hynous.core.costs import get_month_summary
            return f"${get_month_summary()['claude']['cache_savings_usd']:.2f}"
        except Exception:
            return "$0.00"

    @rx.var(cache=False)
    def wallet_perplexity_cost(self) -> str:
        """Perplexity API cost string."""
        try:
            from hynous.core.costs import get_month_summary
            return f"${get_month_summary()['perplexity']['cost_usd']:.2f}"
        except Exception:
            return "$0.00"

    @rx.var(cache=False)
    def wallet_perplexity_calls(self) -> str:
        """Perplexity API call count."""
        try:
            from hynous.core.costs import get_month_summary
            return str(get_month_summary()["perplexity"]["calls"])
        except Exception:
            return "0"

    @rx.var
    def positions_count(self) -> str:
        """Number of open positions — updated by background poller."""
        return str(len(self.positions))

    @rx.var(cache=False)
    def portfolio_table_rows(self) -> List[PortfolioTableRow]:
        """Rows for the home portfolio table section."""
        rows: List[PortfolioTableRow] = []
        now = datetime.now()
        for p in self.positions:
            parsed = _parse_occ_symbol(p.symbol)
            if parsed:
                exp = parsed["expiry"]
                dte_days = max((exp.date() - now.date()).days, 0)
                strike = parsed["strike"]
                strike_str = f"${strike:.0f}" if float(strike).is_integer() else f"${strike:.2f}"
                strike_expiry = f"{strike_str} · {exp.strftime('%b %d')}"
                option_type = parsed["option_type"]
            else:
                dte_days = 0
                strike_expiry = "Spot"
                option_type = "LONG" if p.side == "long" else "SHORT"

            delta_text = "N/A"
            theta_text = "N/A"
            if p.delta is not None:
                delta_text = f"{p.delta:+.2f}"
            if p.theta is not None:
                theta_text = f"{p.theta:+.2f}"

            pnl_up = p.pnl_usd >= 0
            pnl_sign = "+" if p.pnl_usd > 0 else "-"
            if p.pnl_usd == 0:
                pnl_sign = ""

            rows.append(
                PortfolioTableRow(
                    instrument=p.symbol_short or p.symbol,
                    option_type=option_type,
                    subtitle=("Open position" if parsed else p.side.capitalize()),
                    strike_expiry=strike_expiry,
                    dte=(f"{dte_days}d" if parsed else "N/A"),
                    qty=f"x{max(p.contracts, 1)}",
                    entry=f"${p.entry:,.2f}",
                    current=f"${p.mark:,.2f}",
                    pnl=f"{pnl_sign}${abs(p.pnl_usd):,.0f}",
                    is_pnl_up=pnl_up,
                    delta=delta_text,
                    theta=theta_text,
                )
            )
        return rows

    @rx.var(cache=False)
    def total_trades_str(self) -> str:
        """Total closed trades count."""
        try:
            from hynous.core.trade_analytics import get_trade_stats
            stats = get_trade_stats()
            return str(stats.total_trades)
        except Exception:
            return "0"

    @rx.var
    def message_count(self) -> str:
        """Number of messages as string."""
        return str(len(self.messages))

    @rx.var(cache=False)
    def daemon_running(self) -> bool:
        """Whether the background daemon is active.

        cache=False because this reads module-level _daemon which Reflex
        auto_deps can't track. Without this, the value is permanently cached.
        """
        return _daemon is not None and _daemon.is_running

    @rx.var(cache=False)
    def daemon_wake_count(self) -> str:
        """Total daemon wakes this session."""
        if _daemon is None:
            return "0"
        return str(_daemon.wake_count)

    @rx.var(cache=False)
    def daemon_status_text(self) -> str:
        """Daemon status: Running / Stopped / Paused."""
        if _daemon is None or not _daemon.is_running:
            return "Stopped"
        if _daemon.trading_paused:
            return "Paused"
        return "Running"

    @rx.var(cache=False)
    def daemon_status_color(self) -> str:
        """Color for daemon status dot."""
        if _daemon is None or not _daemon.is_running:
            return "#525252"
        if _daemon.trading_paused:
            return "#ef4444"
        return "#22c55e"

    @rx.var(cache=False)
    def daemon_daily_pnl(self) -> str:
        """Today's realized PnL string."""
        if _daemon is None:
            return "$0.00"
        pnl = _daemon.daily_realized_pnl
        sign = "+" if pnl >= 0 else ""
        return f"{sign}${pnl:,.2f}"

    @rx.var(cache=False)
    def daemon_trading_paused(self) -> bool:
        """Whether circuit breaker is active."""
        return _daemon is not None and _daemon.trading_paused

    @rx.var(cache=False)
    def daemon_activities(self) -> list[DaemonActivity]:
        """Last 20 daemon events from the activity log."""
        try:
            from hynous.core.daemon_log import get_events
            raw = get_events(limit=20)
            return [DaemonActivity(**e) for e in raw]
        except Exception:
            return []

    @rx.var(cache=False)
    def daemon_next_review(self) -> str:
        """Countdown to next periodic review."""
        if _daemon is None or not _daemon.is_running:
            return "—"
        secs = _daemon.next_review_seconds
        if secs <= 0:
            return "Soon"
        mins = secs // 60
        if mins >= 60:
            return f"{mins // 60}h {mins % 60}m"
        return f"{mins}m"

    @rx.var(cache=False)
    def daemon_cooldown(self) -> str:
        """Wake cooldown status."""
        if _daemon is None or not _daemon.is_running:
            return "—"
        remaining = _daemon.cooldown_remaining
        if remaining <= 0:
            return "Ready"
        return f"{remaining}s"

    @rx.var(cache=False)
    def daemon_cooldown_active(self) -> bool:
        """Whether wake cooldown is active."""
        if _daemon is None:
            return False
        return _daemon.cooldown_remaining > 0

    @rx.var(cache=False)
    def daemon_wake_rate(self) -> str:
        """Wakes this hour / max."""
        if _daemon is None or not _daemon.is_running:
            return "—"
        current = _daemon.wakes_this_hour
        max_h = _daemon.config.daemon.max_wakes_per_hour
        return f"{current}/{max_h}"

    @rx.var(cache=False)
    def daemon_reviews_until_learning(self) -> str:
        """Reviews until next learning session."""
        if _daemon is None or not _daemon.is_running:
            return "—"
        n = _daemon.reviews_until_learning
        if n <= 1:
            return "Next review"
        return f"In {n} reviews"

    @rx.var(cache=False)
    def daemon_last_wake_ago(self) -> str:
        """Time since last wake."""
        if _daemon is None or not _daemon.is_running:
            return "—"
        ts = _daemon.last_wake_time
        if not ts:
            return "Never"
        import time as _time
        elapsed = int(_time.time() - ts)
        if elapsed < 60:
            return "Just now"
        mins = elapsed // 60
        if mins < 60:
            return f"{mins}m ago"
        hours = mins // 60
        return f"{hours}h {mins % 60}m ago"

    @rx.var(cache=False)
    def daemon_review_count(self) -> str:
        """Total reviews completed."""
        if _daemon is None:
            return "0"
        return str(_daemon.review_count)

    @rx.var(cache=False)
    def daemon_today_wakes(self) -> list[DaemonActivityFormatted]:
        """Today's daemon events with relative timestamps."""
        try:
            from hynous.core.daemon_log import get_events
            from datetime import datetime, timezone, timedelta
            raw = get_events(limit=50)
            today = datetime.now(timezone.utc).date()
            result = []
            for e in raw:
                ts_str = e.get("timestamp", "")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    continue
                if ts.date() != today:
                    continue
                # Skip "skip" events for cleaner display
                if e.get("type") == "skip":
                    continue
                # Format relative time
                now = datetime.now(timezone.utc)
                delta = int((now - ts).total_seconds())
                if delta < 60:
                    age = "Just now"
                elif delta < 3600:
                    age = f"{delta // 60}m ago"
                else:
                    age = f"{delta // 3600}h {(delta % 3600) // 60}m ago"
                result.append(DaemonActivityFormatted(
                    type=e.get("type", ""),
                    title=e.get("title", ""),
                    detail=e.get("detail", ""),
                    time_display=age,
                ))
            return result[:15]  # Cap at 15
        except Exception:
            return []

    @rx.var
    def agent_status_display(self) -> str:
        """Capitalized agent status."""
        return self.agent_status.capitalize()

    @rx.var
    def agent_status_color(self) -> str:
        """Color for agent status dot."""
        colors = {
            "online": "#22c55e",
            "thinking": "#eab308",
            "error": "#ef4444",
        }
        return colors.get(self.agent_status, "#525252")

    @rx.var
    def streaming_show_avatar(self) -> bool:
        """Whether the streaming bubble should show the Hynous avatar.

        False when the last saved message is already from Hynous (grouped).
        """
        if not self.messages:
            return True
        return self.messages[-1].sender != "hynous"

    @rx.var
    def streaming_display(self) -> str:
        """Streaming text with financial highlights applied."""
        if not self.streaming_text:
            return ""
        return _highlight(self.streaming_text)

    @rx.var
    def active_tool_display(self) -> str:
        """Human-readable name for the currently active tool."""
        if not self.active_tool:
            return ""
        return _TOOL_DISPLAY.get(self.active_tool, self.active_tool)

    @rx.var
    def active_tool_color(self) -> str:
        """Accent color for the currently active tool indicator."""
        colors = {
            "get_market_data": "#60a5fa",
            "get_multi_timeframe": "#a78bfa",
            "search_web": "#e879f9",
            "get_my_costs": "#94a3b8",
            "store_memory": "#a3e635",
            "recall_memory": "#a3e635",
            "get_account": "#f59e0b",
            "execute_trade": "#22c55e",
            "close_position": "#ef4444",
            "modify_position": "#a78bfa",
            "get_trade_stats": "#f97316",
            "explore_memory": "#a3e635",
            "manage_conflicts": "#f87171",
            "manage_clusters": "#a5b4fc",
        }
        return colors.get(self.active_tool, "#a5b4fc")

    # === AI Confidence Computed Vars ===

    @rx.var(cache=False)
    def show_ai_confidence(self) -> bool:
        """Whether to display the confidence indicator."""
        return self.ai_confidence >= 0

    @rx.var(cache=False)
    def ai_confidence_pct_str(self) -> str:
        """Confidence as a percentage string, e.g. '78%'."""
        if self.ai_confidence < 0:
            return "—"
        return f"{int(self.ai_confidence * 100)}%"

    @rx.var(cache=False)
    def ai_confidence_bar_width(self) -> str:
        """CSS width for the progress bar fill."""
        if self.ai_confidence < 0:
            return "0%"
        return f"{int(self.ai_confidence * 100)}%"

    @rx.var(cache=False)
    def ai_confidence_label(self) -> str:
        """Tier label: HIGH / MEDIUM / LOW."""
        if self.ai_confidence >= 0.8:
            return "HIGH"
        if self.ai_confidence >= 0.6:
            return "MEDIUM"
        if self.ai_confidence >= 0:
            return "LOW"
        return ""

    @rx.var(cache=False)
    def ai_confidence_color(self) -> str:
        """Color reflecting conviction tier."""
        if self.ai_confidence >= 0.8:
            return "#22c55e"
        if self.ai_confidence >= 0.6:
            return "#f59e0b"
        if self.ai_confidence >= 0:
            return "#ef4444"
        return "#404040"

    # === Daemon Controls ===

    is_waking: bool = False  # True while a manual wake is in progress

    def wake_agent_now(self):
        """Trigger an immediate daemon review wake.

        Non-blocking: fires the wake in a daemon background thread.
        Response appears in chat feed via the daemon chat queue.
        The is_waking flag is cleared by poll_portfolio when the response arrives.
        """
        if _daemon is None or not _daemon.is_running:
            self._add_activity("system", "Cannot wake — daemon not running", "warning")
            return
        if self.is_waking:
            return  # Already waking

        self.is_waking = True
        _daemon.trigger_manual_wake()
        self._add_activity("system", "Manual wake triggered", "info")

    def toggle_daemon(self, checked: bool = True):
        """Toggle daemon on/off.

        Fully synchronous — if agent needs init on first toggle, this blocks
        for ~7s. Acceptable since the user explicitly requested the action.
        After first init, toggling is instant.
        """
        global _daemon

        if not checked:
            # Stop — always instant
            if _daemon is not None and _daemon.is_running:
                _daemon.stop()
                self._add_activity("system", "Daemon stopped", "info")
            return

        # Start — initialize agent if needed
        agent = _get_agent()
        if not agent:
            self._add_activity("system", f"Daemon failed: {_agent_error}", "error")
            return

        if _daemon is None:
            from hynous.intelligence.daemon import Daemon
            _daemon = Daemon(agent, agent.config)
        if not _daemon.is_running:
            _daemon.start()
        self._add_activity("system", "Daemon started", "info")

    # === Navigation ===

    # Trading tab removed — navigation methods deleted.

    def select_live_position(self, symbol: str):
        """Load a position into the live chart (entry/SL/TP markers)."""
        self.live_symbol = symbol
        for pos in self.positions:
            if pos.symbol == symbol:
                self.live_entry_px = pos.entry
                self.live_sl_px    = pos.stop_loss
                self.live_tp_px    = pos.take_profit
                self.live_side     = pos.side
                return
        self.live_entry_px = 0.0
        self.live_sl_px    = 0.0
        self.live_tp_px    = 0.0
        self.live_side     = ""

    def select_live_symbol(self, symbol: str):
        """Switch the live chart to a different symbol (no entry markers)."""
        self.live_symbol   = symbol.upper().strip()
        self.live_entry_px = 0.0
        self.live_sl_px    = 0.0
        self.live_tp_px    = 0.0
        self.live_side     = ""

    def set_live_timeframe(self, tf: str):
        """Change the live chart timeframe."""
        self.live_timeframe = tf

    def go_to_home(self):
        """Navigate to home page."""
        self.current_page = "home"
        return AppState.load_home_movers

    @_background
    async def load_home_movers(self):
        """Fetch top movers from Hyperliquid for home dashboard."""
        movers = await asyncio.to_thread(self._fetch_home_movers)
        async with self:
            self.home_movers = movers
            # Also refresh area chart SVG
            color = "#facc15"
            if len(_portfolio_history) >= 2:
                self.portfolio_area_svg = _build_area_svg(
                    _portfolio_history, width=760, height=240, color=color
                )

    @staticmethod
    def _fetch_home_movers() -> list["HomeMover"]:
        """Fetch top movers by daily % change (equities)."""
        try:
            import time as _time
            from hynous.core.config import load_config
            from hynous.data.providers import get_market_provider

            config = load_config()
            provider = get_market_provider(config)
            from hynous.data.providers.yahoo import YahooProvider
            fallback = YahooProvider()
            symbols = list(getattr(getattr(config, "execution", None), "symbols", []) or [])
            if not symbols:
                symbols = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD"]

            now_ms = int(_time.time() * 1000)
            start_ms = now_ms - 6 * 60 * 60 * 1000

            movers = []
            for sym in symbols:
                name = sym.upper()
                mark = provider.get_price(name) or 0
                prev = 0
                try:
                    prev = provider.get_prev_close(name) or 0
                except Exception:
                    prev = 0
                # Fallback to Yahoo if primary provider yields no data
                if mark <= 0 or prev <= 0:
                    try:
                        mark = fallback.get_price(name) or mark
                        prev = fallback.get_prev_close(name) or prev
                    except Exception:
                        pass
                if mark <= 0 or prev <= 0:
                    continue
                change_pct = ((mark - prev) / prev) * 100
                is_up = change_pct >= 0
                sign = "+" if is_up else ""
                # Format price
                if mark >= 1000:
                    price_str = f"${mark:,.0f}"
                elif mark >= 1:
                    price_str = f"${mark:,.2f}"
                else:
                    price_str = f"${mark:.4f}"
                # Format volume
                vol = 0
                try:
                    vol = provider.get_volume(name) or 0
                except Exception:
                    vol = 0
                if vol <= 0:
                    try:
                        vol = fallback.get_volume(name) or vol
                    except Exception:
                        pass
                if vol >= 1_000_000_000:
                    vol_str = f"{vol/1e9:.1f}B"
                elif vol >= 1_000_000:
                    vol_str = f"{vol/1e6:.1f}M"
                elif vol >= 1_000:
                    vol_str = f"{vol/1e3:.1f}K"
                else:
                    vol_str = f"{vol:.0f}"

                # Sparkline (last ~6 hours, 5m). If provider is Yahoo or data flat/empty, fallback to 30d daily.
                spark = ""
                try:
                    if provider.__class__.__name__ != "YahooProvider":
                        start_6h = now_ms - 6 * 60 * 60 * 1000
                        candles = provider.get_candles(name, "5m", start_6h, now_ms)
                        closes = [c["c"] for c in candles][-72:]
                        if len(closes) >= 5 and (max(closes) - min(closes)) > 0:
                            spark = _build_sparkline_svg(
                                closes, width=90, height=26,
                                color="#22c55e" if is_up else "#ef4444",
                            )
                except Exception:
                    spark = ""
                if not spark:
                    try:
                        days_30 = now_ms - 30 * 24 * 60 * 60 * 1000
                        candles = provider.get_candles(name, "1d", days_30, now_ms)
                        closes = [c["c"] for c in candles][-30:]
                        if len(closes) >= 2:
                            spark = _build_sparkline_svg(
                                closes, width=90, height=26,
                                color="#22c55e" if is_up else "#ef4444",
                            )
                    except Exception:
                        spark = ""
                if not spark:
                    try:
                        days_30 = now_ms - 30 * 24 * 60 * 60 * 1000
                        candles = fallback.get_candles(name, "1d", days_30, now_ms)
                        closes = [c["c"] for c in candles][-30:]
                        if len(closes) >= 2:
                            spark = _build_sparkline_svg(
                                closes, width=90, height=26,
                                color="#22c55e" if is_up else "#ef4444",
                            )
                    except Exception:
                        spark = ""
                if not spark:
                    # Fallback: simple 2-point line from prev close to current
                    if prev > 0 and mark > 0:
                        spark = _build_sparkline_svg(
                            [prev, mark], width=90, height=26,
                            color="#22c55e" if is_up else "#ef4444",
                        )
                    else:
                        # Absolute fallback: flat line so UI never looks empty
                        spark = _build_sparkline_svg(
                            [1, 1], width=90, height=26,
                            color="#22c55e" if is_up else "#ef4444",
                        )
                movers.append(HomeMover(
                    symbol=name,
                    price=price_str,
                    change_pct=round(change_pct, 2),
                    change_str=f"{sign}{change_pct:.2f}%",
                    volume=vol_str,
                    is_up=is_up,
                    sparkline_svg=spark,
                ))

            # Sort by absolute % change, take top 10
            movers.sort(key=lambda m: abs(m.change_pct), reverse=True)
            return movers[:10]
        except Exception:
            return []

    def set_home_alert_input(self, value: str):
        """Update the alert input text."""
        self.home_alert_input = value

    def submit_home_alert(self, form_data: dict = {}):
        """Send an alert request to the agent via chat."""
        text = (form_data.get("alert", "") or self.home_alert_input).strip()
        if not text:
            return
        self.home_alert_input = ""
        self.current_page = "chat"
        self.chat_unread = False
        self._pending_input = f"Set a watchpoint alert: {text}"
        return self.send_message()

    @rx.var(cache=False)
    def home_recent_fills(self) -> list[DaemonActivity]:
        """Recent fill events from daemon log for home transactions section."""
        try:
            from hynous.core.daemon_log import get_events
            raw = get_events(limit=50)
            fills = [DaemonActivity(**e) for e in raw if e.get("type") == "fill"]
            return fills[:5]
        except Exception:
            return []

    def go_to_chat(self):
        """Navigate to chat page."""
        self.current_page = "chat"
        self.chat_unread = False

    def go_to_memory(self):
        """Navigate to memory management page and load data."""
        self.current_page = "memory"
        self.memory_unread = False
        return AppState.load_memory_page

    def go_to_chat_with_message(self, msg: str):
        """Navigate to chat and send a message."""
        self.current_page = "chat"
        self.chat_unread = False
        self._pending_input = msg
        return self.send_message()

    # === Model Selection ===
    selected_model: str = "openrouter/anthropic/claude-sonnet-4-5-20250929"
    selected_sub_model: str = "openrouter/anthropic/claude-haiku-4-5-20251001"

    @rx.var
    def selected_model_label(self) -> str:
        """Human-readable label for the currently selected main model."""
        return _MODEL_TO_LABEL.get(self.selected_model, self.selected_model)

    @rx.var
    def selected_sub_model_label(self) -> str:
        """Human-readable label for the currently selected sub-agent model."""
        return _MODEL_TO_LABEL.get(self.selected_sub_model, self.selected_sub_model)

    def set_agent_model(self, label: str):
        """Switch the main agent model at runtime."""
        model_id = _LABEL_TO_MODEL.get(label, label)
        self.selected_model = model_id
        agent = _get_agent()
        if agent:
            agent.config.agent.model = model_id
            agent.rebuild_system_prompt()
        _save_model_prefs(model_id, self.selected_sub_model)

    def set_sub_model(self, label: str):
        """Switch the sub-agent (coach/compression) model at runtime."""
        model_id = _LABEL_TO_MODEL.get(label, label)
        self.selected_sub_model = model_id
        agent = _get_agent()
        if agent:
            agent.config.memory.compression_model = model_id
        _save_model_prefs(self.selected_model, model_id)

    # === Watchlist State ===

    watchpoint_groups: List[WatchpointGroup] = []
    watchpoint_count: str = "0"

    @_background
    async def load_watchpoints(self):
        """Fetch active watchpoints from Nous, grouped by symbol."""
        data = await asyncio.to_thread(self._fetch_watchpoints)
        async with self:
            self.watchpoint_groups = data["groups"]
            self.watchpoint_count = str(data["count"])

    @staticmethod
    def _fetch_watchpoints() -> dict:
        """Fetch watchpoints from Nous (sync, runs in thread).

        Returns WatchpointGroup list with pre-rendered HTML detail.
        """
        try:
            from hynous.nous.client import get_client
            import json as _json
            from html import escape
            from hynous.data.providers import get_market_provider
            from hynous.data.providers.yahoo import YahooProvider
            import time as _time

            client = get_client()
            nodes = client.list_nodes(subtype="custom:watchpoint", lifecycle="ACTIVE", limit=50)

            # Group by symbol
            by_symbol: dict[str, list[tuple[bool, str, str]]] = {}
            for n in nodes:
                body = _json.loads(n.get("content_body", "{}"))
                trigger = body.get("trigger", {})
                symbol = trigger.get("symbol", "?")
                condition = trigger.get("condition", "?")
                value = trigger.get("value", 0)
                title = n.get("content_title", "")

                # Format value
                if "price" in condition and value >= 1000:
                    val_str = f"${value:,.0f}"
                elif "price" in condition:
                    val_str = f"${value}"
                else:
                    val_str = str(value)

                # Short title: strip symbol/price prefix
                title_short = title
                for prefix in [f"{symbol} ${value:,.0f}", f"{symbol} ${value}", symbol]:
                    if title_short.startswith(prefix):
                        title_short = title_short[len(prefix):].lstrip(" —-")
                        break

                # Condition + direction
                is_up = "above" in condition
                if "above" in condition:
                    cond_label = f"above {val_str}"
                else:
                    cond_label = f"below {val_str}"

                by_symbol.setdefault(symbol, []).append(
                    (is_up, cond_label, title_short[:50])
                )

            # Build groups with pre-rendered HTML
            groups: list[WatchpointGroup] = []
            provider = get_market_provider()
            fallback = YahooProvider()
            now_ms = int(_time.time() * 1000)
            for sym in sorted(by_symbol.keys()):
                wp_items = by_symbol[sym]
                html_parts = []
                for is_up, cond, title in wp_items:
                    color = "#4ade80" if is_up else "#f87171"
                    bg = "rgba(74,222,128,0.05)" if is_up else "rgba(248,113,113,0.05)"
                    icon = "↗" if is_up else "↘"
                    title_html = (
                        f'<div style="font-size:0.72rem;color:#a3a3a3;padding-top:2px;line-height:1.4">'
                        f'{escape(title)}</div>'
                    ) if title else ""
                    html_parts.append(
                        f'<div style="padding:0.5rem 0.625rem;border-left:2px solid {color};'
                        f'background:{bg};border-radius:0 6px 6px 0;margin-bottom:0.375rem">'
                        f'<div style="display:flex;align-items:center;gap:0.375rem">'
                        f'<span style="color:{color};font-size:0.82rem">{icon}</span>'
                        f'<span style="font-size:0.78rem;font-weight:500;color:{color}">'
                        f'{escape(cond)}</span>'
                        f'</div>{title_html}</div>'
                    )
                # Price + change + sparkline (like Top Movers)
                price_str = ""
                change_str = ""
                is_up = True
                spark = ""
                try:
                    mark = provider.get_price(sym) or 0
                    prev = 0
                    try:
                        prev = provider.get_prev_close(sym) or 0
                    except Exception:
                        prev = 0
                    if mark <= 0 or prev <= 0:
                        mark = fallback.get_price(sym) or mark
                        prev = fallback.get_prev_close(sym) or prev
                    if mark > 0 and prev > 0:
                        change_pct = ((mark - prev) / prev) * 100
                        is_up = change_pct >= 0
                        sign = "+" if is_up else ""
                        if mark >= 1000:
                            price_str = f"${mark:,.0f}"
                        elif mark >= 1:
                            price_str = f"${mark:,.2f}"
                        else:
                            price_str = f"${mark:.4f}"
                        change_str = f"{sign}{change_pct:.2f}%"
                except Exception:
                    pass

                # Sparkline (last ~6 hours, 5m) with fallbacks
                try:
                    if provider.__class__.__name__ != "YahooProvider":
                        start_6h = now_ms - 6 * 60 * 60 * 1000
                        candles = provider.get_candles(sym, "5m", start_6h, now_ms)
                        closes = [c["c"] for c in candles][-72:]
                        if len(closes) >= 5 and (max(closes) - min(closes)) > 0:
                            spark = _build_sparkline_svg(
                                closes, width=80, height=24,
                                color="#22c55e" if is_up else "#ef4444",
                            )
                except Exception:
                    spark = ""
                if not spark:
                    try:
                        days_30 = now_ms - 30 * 24 * 60 * 60 * 1000
                        candles = provider.get_candles(sym, "1d", days_30, now_ms)
                        closes = [c["c"] for c in candles][-30:]
                        if len(closes) >= 2:
                            spark = _build_sparkline_svg(
                                closes, width=80, height=24,
                                color="#22c55e" if is_up else "#ef4444",
                            )
                    except Exception:
                        spark = ""
                if not spark:
                    try:
                        days_30 = now_ms - 30 * 24 * 60 * 60 * 1000
                        candles = fallback.get_candles(sym, "1d", days_30, now_ms)
                        closes = [c["c"] for c in candles][-30:]
                        if len(closes) >= 2:
                            spark = _build_sparkline_svg(
                                closes, width=80, height=24,
                                color="#22c55e" if is_up else "#ef4444",
                            )
                    except Exception:
                        spark = ""
                if not spark:
                    try:
                        if price_str:
                            prev_f = float(prev or 0)
                            mark_f = float(mark or 0)
                            if prev_f > 0 and mark_f > 0:
                                spark = _build_sparkline_svg(
                                    [prev_f, mark_f], width=80, height=24,
                                    color="#22c55e" if is_up else "#ef4444",
                                )
                    except Exception:
                        spark = ""

                groups.append(WatchpointGroup(
                    symbol=sym,
                    count=str(len(wp_items)),
                    detail_html="".join(html_parts),
                    price=price_str,
                    change_str=change_str,
                    is_up=is_up,
                    sparkline_svg=spark,
                ))

            return {"groups": groups, "count": len(nodes)}
        except Exception:
            return {"groups": [], "count": 0}

    # === Clusters State ===

    cluster_displays: List[ClusterDisplay] = []
    cluster_total: str = "0"

    @_background
    async def load_clusters(self):
        """Fetch cluster data from Nous."""
        data = await asyncio.to_thread(self._fetch_clusters)
        async with self:
            self.cluster_displays = data["clusters"]
            self.cluster_total = str(data["total"])

    @staticmethod
    def _fetch_clusters() -> dict:
        """Fetch clusters with health info (sync, runs in thread)."""
        try:
            from hynous.nous.client import get_client
            from html import escape

            client = get_client()
            clusters = client.list_clusters()

            # Color map for known cluster names
            accent_map = {
                "BTC": "#f7931a",
                "ETH": "#627eea",
                "SOL": "#9945ff",
                "Thesis": "#60a5fa",
                "Theses": "#60a5fa",
                "Lessons": "#a3e635",
                "Trade History": "#f59e0b",
            }

            displays: list[ClusterDisplay] = []
            total_nodes = 0

            for cl in clusters:
                cid = cl.get("id", "")
                name = cl.get("name", "?")
                node_count = cl.get("node_count", 0)
                total_nodes += node_count
                accent = accent_map.get(name, "#a5b4fc")

                # Fetch health for this cluster
                health_html = ""
                try:
                    health = client.get_cluster_health(cid)
                    active = health.get("active_nodes", 0)
                    weak = health.get("weak_nodes", 0)
                    dormant = health.get("dormant_nodes", 0)
                    total = active + weak + dormant

                    if total > 0:
                        a_pct = round(active / total * 100)
                        w_pct = round(weak / total * 100)
                        d_pct = 100 - a_pct - w_pct

                        # Health bar
                        bar = (
                            f'<div style="display:flex;height:4px;border-radius:2px;overflow:hidden;'
                            f'background:#1a1a1a;margin-top:6px">'
                        )
                        if a_pct > 0:
                            bar += f'<div style="width:{a_pct}%;background:#22c55e"></div>'
                        if w_pct > 0:
                            bar += f'<div style="width:{w_pct}%;background:#eab308"></div>'
                        if d_pct > 0:
                            bar += f'<div style="width:{d_pct}%;background:#404040"></div>'
                        bar += '</div>'

                        # Labels
                        labels = []
                        if active:
                            labels.append(f'<span style="color:#22c55e">{active} active</span>')
                        if weak:
                            labels.append(f'<span style="color:#eab308">{weak} weak</span>')
                        if dormant:
                            labels.append(f'<span style="color:#525252">{dormant} dormant</span>')
                        label_row = (
                            f'<div style="display:flex;gap:8px;font-size:0.65rem;margin-top:4px">'
                            f'{" ".join(labels)}</div>'
                        )
                        health_html = bar + label_row
                    else:
                        health_html = (
                            '<div style="font-size:0.65rem;color:#404040;margin-top:6px">'
                            'Empty cluster</div>'
                        )
                except Exception:
                    health_html = ""

                displays.append(ClusterDisplay(
                    name=name,
                    node_count=str(node_count),
                    health_html=health_html,
                    accent=accent,
                ))

            return {"clusters": displays, "total": total_nodes}
        except Exception:
            return {"clusters": [], "total": 0}

    # === News State ===

    stock_news: list[dict] = []  # Recent news headlines
    stock_news_last_refresh_ts: float = 0.0
    news_seen_ids: list[str] = []

    @rx.var(cache=False)
    def news_feed_html(self) -> str:
        """Pre-rendered HTML for news headlines card."""
        import time as _time
        if not self.stock_news:
            return (
                '<div style="font-size:0.8rem;color:#404040;text-align:center;padding:1rem 0;">'
                'No news yet — Alpaca News will appear here</div>'
            )
        def _analyze_headline(title: str, body: str = "") -> tuple[str, str, str]:
            """Return (impact, bias, stance) from simple keyword heuristics."""
            t = f"{title} {body}".lower()
            bearish = [
                "miss", "misses", "cuts guidance", "guidance cut", "downgrade", "lawsuit",
                "sec", "investigation", "probe", "recall", "fraud", "bankruptcy",
                "layoffs", "weak demand", "slump", "plunge", "drop", "warns",
            ]
            bullish = [
                "beats", "beat", "raises guidance", "guidance raise", "upgrade",
                "record revenue", "record profit", "surge", "rally", "soars",
                "approval", "contract wins", "buyback", "dividend increase",
            ]
            macro = [
                "fed", "inflation", "cpi", "ppi", "jobs report", "rate", "yield",
                "gdp", "recession", "oil", "opec", "tariff", "trade war",
            ]
            impact = "unclear"
            bias = "neutral"
            stance = "flat"
            if any(k in t for k in macro):
                impact = "macro"
            if any(k in t for k in bullish):
                bias = "bullish"
                stance = "long"
                impact = "likely"
            if any(k in t for k in bearish):
                bias = "bearish"
                stance = "short"
                impact = "likely"
            return impact, bias, stance
        rows = []
        now = _time.time()
        def _trim(s: str, n: int = 110) -> str:
            return (s[: n - 1] + "…") if len(s) > n else s
        for n in self.stock_news[:8]:
            pub = n.get("published_on", 0)
            age_s = int(now - pub) if pub else 0
            if age_s < 60:
                age = "now"
            elif age_s < 3600:
                age = f"{age_s // 60}m"
            else:
                age = f"{age_s // 3600}h"
            title = n.get("title", "")
            source = n.get("source", "")
            url = n.get("url", "")
            from html import escape
            title = _trim(escape(title), 120)
            source = escape(source)
            url = escape(url)
            impact, bias, stance = _analyze_headline(title, n.get("body", ""))
            bias_color = "#4ade80" if bias == "bullish" else "#f87171" if bias == "bearish" else "#737373"
            impact_color = "#60a5fa" if impact == "macro" else "#a3a3a3" if impact == "unclear" else "#f59e0b"
            rows.append(
                f'<div style="display:flex;flex-direction:column;gap:0.2rem;padding:0.4rem 0;border-bottom:1px solid #1a1a1a;">'
                f'  <div style="display:flex;gap:0.5rem;align-items:baseline;min-width:0;overflow:hidden;">'
                f'    <span style="width:28px;color:#525252;flex-shrink:0;font-size:0.7rem;text-align:right">{age}</span>'
                f'    <a href="{url}" target="_blank" rel="noopener noreferrer" '
                f'style="flex:1;min-width:0;color:#a3a3a3;font-size:0.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-decoration:none;">{title}</a>'
                f'    <span style="color:#525252;font-size:0.65rem;flex-shrink:0">{source}</span>'
                f'  </div>'
                f'  <div style="display:flex;gap:0.6rem;align-items:center;padding-left:28px;">'
                f'    <span style="font-size:0.65rem;color:{impact_color};text-transform:uppercase;letter-spacing:0.06em">{impact}</span>'
                f'    <span style="font-size:0.65rem;color:{bias_color};text-transform:uppercase;letter-spacing:0.06em">{bias}</span>'
                f'    <span style="font-size:0.65rem;color:#94a3b8;">position: <strong style="color:{bias_color}">{stance}</strong></span>'
                f'  </div>'
                f'</div>'
            )
        return '<div style="font-family:inherit;overflow:hidden;width:100%;">' + "".join(rows) + '</div>'

    @rx.var(cache=False)
    def micro_entries_today(self) -> str:
        """Micro trades entered today."""
        if _daemon is None:
            return "0"
        return str(_daemon.micro_entries_today)

    @rx.var(cache=False)
    def entries_today(self) -> str:
        """Total trades entered today."""
        if _daemon is None:
            return "0"
        return str(_daemon._entries_today)

    # === Memory Management State ===

    memory_node_count: str = "0"
    memory_edge_count: str = "0"
    memory_health_ratio: str = "0%"
    memory_lifecycle_html: str = ""

    conflict_items: List[ConflictItem] = []
    conflict_count: str = "0"
    show_conflicts: bool = False

    stale_html: str = ""  # Pre-rendered dormant node list
    stale_count: str = "0"
    stale_filter: str = "DORMANT"
    show_stale: bool = False

    decay_running: bool = False
    decay_result: str = ""

    backfill_running: bool = False
    backfill_result: str = ""

    cluster_backfill_running: bool = False
    cluster_backfill_result: str = ""

    @_background
    async def load_memory_page(self):
        """Load all memory management data."""
        async with self:
            lifecycle = self.stale_filter
        health, conflicts, stale, clusters = await asyncio.to_thread(
            self._fetch_memory_page_data, lifecycle
        )
        async with self:
            self.memory_node_count = health["node_count"]
            self.memory_edge_count = health["edge_count"]
            self.memory_health_ratio = health["health_ratio"]
            self.memory_lifecycle_html = health["lifecycle_html"]
            self.conflict_items = conflicts["items"]
            self.conflict_count = conflicts["count"]
            self.stale_html = stale["html"]
            self.stale_count = stale["count"]
            self.cluster_displays = clusters["clusters"]
            self.cluster_total = str(clusters["total"])

    @staticmethod
    def _fetch_memory_page_data(lifecycle: str = "DORMANT") -> tuple:
        """Fetch all memory page data in one thread."""
        health = AppState._fetch_memory_health()
        conflicts = AppState._fetch_conflicts()
        stale = AppState._fetch_stale(lifecycle)
        clusters = AppState._fetch_clusters()
        return health, conflicts, stale, clusters

    @staticmethod
    def _fetch_memory_health() -> dict:
        """Fetch overall memory health from Nous."""
        try:
            from hynous.nous.client import get_client
            client = get_client()
            h = client.health()

            total_nodes = h.get("node_count", 0)
            edges = h.get("edge_count", 0)
            lifecycle = h.get("lifecycle", {})
            active = lifecycle.get("ACTIVE", 0)
            weak = lifecycle.get("WEAK", 0)
            dormant = lifecycle.get("DORMANT", 0)
            total = active + weak + dormant
            ratio = round(active / total * 100) if total > 0 else 0

            # Show active+weak as headline count (dormant are archived/hidden)
            live_nodes = active + weak

            # Pre-render lifecycle bar
            if total > 0:
                a_pct = round(active / total * 100)
                w_pct = round(weak / total * 100)
                d_pct = 100 - a_pct - w_pct

                bar = (
                    '<div style="display:flex;height:8px;border-radius:4px;overflow:hidden;'
                    'background:#1a1a1a;margin:8px 0 10px 0;width:100%">'
                )
                if a_pct > 0:
                    bar += f'<div style="width:{a_pct}%;background:#22c55e"></div>'
                if w_pct > 0:
                    bar += f'<div style="width:{w_pct}%;background:#eab308"></div>'
                if d_pct > 0:
                    bar += f'<div style="width:{d_pct}%;background:#404040"></div>'
                bar += '</div>'

                labels = (
                    f'<div style="display:flex;gap:12px;font-size:0.68rem;flex-wrap:wrap">'
                    f'<span style="color:#22c55e;white-space:nowrap">{active} active</span>'
                    f'<span style="color:#eab308;white-space:nowrap">{weak} weak</span>'
                    f'<span style="color:#525252;white-space:nowrap">{dormant} archived</span>'
                    f'</div>'
                )
                lifecycle_html = bar + labels
            else:
                lifecycle_html = '<div style="color:#404040;font-size:0.7rem">No memories yet</div>'

            return {
                "node_count": str(live_nodes),
                "edge_count": str(edges),
                "health_ratio": f"{ratio}%",
                "lifecycle_html": lifecycle_html,
            }
        except Exception:
            return {"node_count": "0", "edge_count": "0", "health_ratio": "0%", "lifecycle_html": ""}

    @staticmethod
    def _fetch_support_resistance() -> str:
        """Fetch latest support/resistance levels from memory and render HTML."""
        try:
            from hynous.nous.client import get_client
            from hynous.core.config import load_config
            client = get_client()
            cfg = load_config()
            symbols = [s.upper() for s in cfg.execution.symbols]

            nodes = client.list_nodes(type="market", subtype="support_resistance", limit=200)
            by_symbol: dict[str, dict] = {}
            for n in nodes:
                title = n.get("content_title", "")
                sym = title.split(" ")[0].upper() if title else ""
                if sym and sym not in symbols:
                    continue
                created = n.get("created_at") or n.get("createdAt") or ""
                existing = by_symbol.get(sym)
                if existing and created and existing.get("created_at", "") >= created:
                    continue
                body = n.get("content_body") or n.get("contentBody") or ""
                by_symbol[sym] = {
                    "created_at": created,
                    "body": body,
                }

            if not by_symbol:
                return "<div style='color:#525252;'>No support/resistance data yet.</div>"

            cards = []
            for sym in symbols:
                data = by_symbol.get(sym)
                if not data:
                    continue
                levels = []
                try:
                    import json as _json
                    parsed = _json.loads(data["body"]) if data["body"] else {}
                    levels = parsed.get("levels", [])
                except Exception:
                    levels = []

                lines = []
                for lvl in levels[:6]:
                    lines.append(
                        f"<div style='display:flex;gap:8px;'>"
                        f"<span style='color:#a3a3a3;min-width:64px;'>{lvl.get('type','').upper()}</span>"
                        f"<span style='color:#e5e5e5;'>${float(lvl.get('price',0)):.2f}</span>"
                        f"<span style='color:#525252;'>({int(lvl.get('touches',0))}x)</span>"
                        f"</div>"
                    )
                body_html = "".join(lines) if lines else "<div style='color:#525252;'>No levels</div>"

                cards.append(
                    f"<div style='padding:10px 12px;border:1px solid #1a1a1a;border-radius:10px;"
                    f"background:#0f0f0f;margin-bottom:8px;'>"
                    f"<div style='color:#e5e5e5;font-weight:600;margin-bottom:6px;'>{sym}</div>"
                    f"{body_html}</div>"
                )

            return "".join(cards) if cards else "<div style='color:#525252;'>No support/resistance data.</div>"
        except Exception:
            return "<div style='color:#525252;'>Support/resistance unavailable.</div>"

    @staticmethod
    def _fetch_conflicts() -> dict:
        """Fetch pending conflicts as structured ConflictItem list."""
        try:
            from hynous.nous.client import get_client
            client = get_client()
            conflicts = client.get_conflicts(status="pending")

            if not conflicts:
                return {"items": [], "count": "0"}

            items = []
            for c in conflicts[:100]:  # Cap at 100
                cid = c.get("id", "?")
                old_id = c.get("old_node_id", "?")
                new_id = c.get("new_node_id")
                new_content = c.get("new_content", "")
                confidence = c.get("detection_confidence", 0)
                ctype = c.get("conflict_type", "?")

                # Fetch old node
                old_title, old_body = old_id, ""
                try:
                    old_node = client.get_node(old_id)
                    if old_node:
                        old_title = old_node.get("content_title", old_id)
                        old_body = (old_node.get("content_body", "") or "")[:200]
                except Exception:
                    pass

                # Fetch new node
                new_title, new_body = "", ""
                if new_id:
                    try:
                        new_node = client.get_node(new_id)
                        if new_node:
                            new_title = new_node.get("content_title", "")
                            new_body = (new_node.get("content_body", "") or "")[:200]
                    except Exception:
                        pass

                conf_pct = f"{confidence:.0%}" if isinstance(confidence, float) else str(confidence)

                items.append(ConflictItem(
                    conflict_id=cid,
                    conflict_type=ctype,
                    confidence=conf_pct,
                    old_title=old_title,
                    old_body=old_body,
                    new_title=new_title or new_content[:80],
                    new_body=new_body or new_content[:200],
                ))

            return {"items": items, "count": str(len(conflicts))}
        except Exception:
            return {"items": [], "count": "0"}

    @staticmethod
    def _fetch_stale(lifecycle: str = "DORMANT") -> dict:
        """Fetch memories by lifecycle with pre-rendered HTML."""
        try:
            from hynous.nous.client import get_client
            from html import escape
            from datetime import datetime, timezone
            client = get_client()
            nodes = client.list_nodes(lifecycle=lifecycle, limit=30)

            if not nodes:
                return {"html": "", "count": "0"}

            now = datetime.now(timezone.utc)
            parts = []
            for n in nodes:
                nid = n.get("id", "?")
                title = n.get("content_title", "Untitled")
                subtype = (n.get("content_subtype", "") or "").replace("custom:", "")
                created = n.get("created_at", "")
                retrievability = n.get("neural_retrievability", 0)
                retr_pct = f"{retrievability:.0%}" if isinstance(retrievability, float) else "?"

                # Compute age
                days_old = "?"
                try:
                    ct = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    days_old = f"{(now - ct).days}d"
                except Exception:
                    pass

                parts.append(
                    f'<div style="display:flex;align-items:center;gap:0.75rem;padding:0.625rem 0;'
                    f'border-bottom:1px solid #1a1a1a" data-nid="{escape(nid)}">'
                    f'<div style="flex:1;min-width:0">'
                    f'<div style="color:#fafafa;font-size:0.78rem;font-weight:500;'
                    f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{escape(title)}</div>'
                    f'<div style="display:flex;gap:0.5rem;font-size:0.65rem;margin-top:2px">'
                    f'<span style="color:#525252">{escape(subtype)}</span>'
                    f'<span style="color:#404040">{days_old} old</span>'
                    f'<span style="color:#404040">recall: {retr_pct}</span></div></div></div>'
                )

            return {"html": "".join(parts), "count": str(len(nodes))}
        except Exception:
            return {"html": "", "count": "0"}

    @_background
    async def run_decay(self):
        """Run FSRS decay cycle."""
        async with self:
            self.decay_running = True
            self.decay_result = ""
        try:
            result = await asyncio.to_thread(self._exec_decay)
            # Refresh health after decay
            health = await asyncio.to_thread(self._fetch_memory_health)
            async with self:
                self.decay_result = result
                self.decay_running = False
                self.memory_node_count = health["node_count"]
                self.memory_edge_count = health["edge_count"]
                self.memory_health_ratio = health["health_ratio"]
                self.memory_lifecycle_html = health["lifecycle_html"]
        except Exception as e:
            async with self:
                self.decay_result = f"Error: {e}"
                self.decay_running = False

    @staticmethod
    def _exec_decay() -> str:
        from hynous.nous.client import get_client
        client = get_client()
        result = client.run_decay()
        transitioned = result.get("transitioned", 0)
        processed = result.get("processed", 0)
        return f"Processed {processed} nodes, {transitioned} transitioned"

    @_background
    async def run_backfill(self):
        """Run embedding backfill."""
        async with self:
            self.backfill_running = True
            self.backfill_result = ""
        try:
            result = await asyncio.to_thread(self._exec_backfill)
            async with self:
                self.backfill_result = result
                self.backfill_running = False
        except Exception as e:
            async with self:
                self.backfill_result = f"Error: {e}"
                self.backfill_running = False

    @staticmethod
    def _exec_backfill() -> str:
        from hynous.nous.client import get_client
        client = get_client()
        result = client.backfill_embeddings()
        embedded = result.get("embedded", 0)
        total = result.get("total", 0)
        return f"Embedded {embedded}/{total} nodes"

    @_background
    async def run_cluster_backfill(self):
        """Retroactively assign all existing nodes to matching clusters."""
        async with self:
            self.cluster_backfill_running = True
            self.cluster_backfill_result = ""
        try:
            result = await asyncio.to_thread(self._exec_cluster_backfill)
            clusters = await asyncio.to_thread(self._fetch_clusters)
            async with self:
                self.cluster_backfill_result = result
                self.cluster_backfill_running = False
                self.cluster_displays = clusters["clusters"]
                self.cluster_total = str(clusters["total"])
        except Exception as e:
            async with self:
                self.cluster_backfill_result = f"Error: {e}"
                self.cluster_backfill_running = False

    @staticmethod
    def _exec_cluster_backfill() -> str:
        """Scan all ACTIVE nodes and assign to matching clusters."""
        import re as _re
        import json as _json
        from hynous.nous.client import get_client

        client = get_client()
        clusters = client.list_clusters()
        if not clusters:
            return "No clusters to backfill"

        nodes = client.list_nodes(lifecycle="ACTIVE", limit=500)
        if not nodes:
            return "No active nodes"

        assigned = 0
        for node in nodes:
            nid = node.get("id")
            subtype = node.get("subtype", "") or node.get("content_subtype", "")
            title = node.get("content_title", "")
            body = node.get("content_body", "")
            # Extract text from JSON body
            if body and body.startswith("{"):
                try:
                    body = _json.loads(body).get("text", body)
                except Exception:
                    pass
            text_upper = f"{title} {body}".upper()

            for cl in clusters:
                cid = cl.get("id")
                if not cid:
                    continue
                matched = False

                # Strategy 1: Subtype match
                auto_subs = cl.get("auto_subtypes")
                if auto_subs:
                    if isinstance(auto_subs, str):
                        try:
                            auto_subs = _json.loads(auto_subs)
                        except Exception:
                            auto_subs = None
                    if isinstance(auto_subs, list) and subtype in auto_subs:
                        matched = True

                # Strategy 2: Keyword match (cluster name in content)
                if not matched:
                    cluster_name = (cl.get("name") or "").upper()
                    if cluster_name and len(cluster_name) >= 2:
                        pattern = r'(?<![A-Z])' + _re.escape(cluster_name) + r'(?![A-Z])'
                        if _re.search(pattern, text_upper):
                            matched = True

                if matched:
                    try:
                        client.add_to_cluster(cid, node_id=nid)
                        assigned += 1
                    except Exception:
                        pass

        return f"Scanned {len(nodes)} nodes, {assigned} assignments made"

    @_background
    async def resolve_all_conflicts(self):
        """Batch resolve all pending conflicts as new_is_current."""
        try:
            result = await asyncio.to_thread(self._exec_batch_resolve, "new_is_current")
            # Refresh conflicts
            conflicts = await asyncio.to_thread(self._fetch_conflicts)
            health = await asyncio.to_thread(self._fetch_memory_health)
            async with self:
                self.conflict_items = conflicts["items"]
                self.conflict_count = conflicts["count"]
                self.memory_node_count = health["node_count"]
                self.memory_health_ratio = health["health_ratio"]
                self.memory_lifecycle_html = health["lifecycle_html"]
        except Exception:
            pass

    @_background
    async def resolve_all_keep_both(self):
        """Batch resolve all pending conflicts as keep_both."""
        try:
            result = await asyncio.to_thread(self._exec_batch_resolve, "keep_both")
            conflicts = await asyncio.to_thread(self._fetch_conflicts)
            async with self:
                self.conflict_items = conflicts["items"]
                self.conflict_count = conflicts["count"]
        except Exception:
            pass

    @staticmethod
    def _exec_batch_resolve(resolution: str) -> dict:
        from hynous.nous.client import get_client
        client = get_client()
        conflicts = client.get_conflicts(status="pending")
        if not conflicts:
            return {"resolved": 0}
        items = [{"conflict_id": c["id"], "resolution": resolution} for c in conflicts]
        return client.batch_resolve_conflicts(items)

    @_background
    async def bulk_archive_stale(self):
        """Archive all dormant memories."""
        try:
            await asyncio.to_thread(self._exec_bulk_archive)
            async with self:
                lifecycle = self.stale_filter
            stale = await asyncio.to_thread(self._fetch_stale, lifecycle)
            health = await asyncio.to_thread(self._fetch_memory_health)
            async with self:
                self.stale_html = stale["html"]
                self.stale_count = stale["count"]
                self.memory_node_count = health["node_count"]
                self.memory_health_ratio = health["health_ratio"]
                self.memory_lifecycle_html = health["lifecycle_html"]
        except Exception:
            pass

    @staticmethod
    def _exec_bulk_archive():
        from hynous.nous.client import get_client
        client = get_client()
        nodes = client.list_nodes(lifecycle="DORMANT", limit=100)
        archived = 0
        for n in nodes:
            try:
                client.update_node(n["id"], state_lifecycle="ARCHIVE")
                archived += 1
            except Exception:
                pass
        logger.info("Bulk archive: %d/%d dormant nodes archived", archived, len(nodes))

    def set_stale_filter(self, val: str):
        """Update stale lifecycle filter and refresh."""
        self.stale_filter = val
        return AppState.load_stale_filtered

    @_background
    async def load_stale_filtered(self):
        """Re-fetch stale nodes with current filter."""
        async with self:
            lifecycle = self.stale_filter
        stale = await asyncio.to_thread(self._fetch_stale, lifecycle)
        async with self:
            self.stale_html = stale["html"]
            self.stale_count = stale["count"]

    def toggle_conflicts(self):
        """Toggle conflicts dialog."""
        self.show_conflicts = not self.show_conflicts

    def toggle_stale(self):
        """Toggle stale memories dialog."""
        self.show_stale = not self.show_stale

    def resolve_one_conflict(self, conflict_id: str, resolution: str):
        """Resolve a single conflict and refresh."""
        return AppState._exec_resolve_one(conflict_id, resolution)

    @_background
    async def _exec_resolve_one(self, conflict_id: str, resolution: str):
        """Background: resolve one conflict, refresh list."""
        try:
            await asyncio.to_thread(self._do_resolve_one, conflict_id, resolution)
            conflicts = await asyncio.to_thread(self._fetch_conflicts)
            health = await asyncio.to_thread(self._fetch_memory_health)
            async with self:
                self.conflict_items = conflicts["items"]
                self.conflict_count = conflicts["count"]
                self.memory_node_count = health["node_count"]
                self.memory_health_ratio = health["health_ratio"]
                self.memory_lifecycle_html = health["lifecycle_html"]
        except Exception:
            pass

    @staticmethod
    def _do_resolve_one(conflict_id: str, resolution: str):
        from hynous.nous.client import get_client
        client = get_client()
        client.resolve_conflict(conflict_id, resolution)

    # === Journal State ===

    journal_win_rate: str = "—"
    journal_total_pnl: str = "—"
    journal_profit_factor: str = "—"
    journal_total_trades: str = "0"
    journal_fee_losses: str = "0"
    journal_current_streak: str = "—"
    journal_max_win_streak: str = "0"
    journal_max_loss_streak: str = "0"
    journal_avg_duration: str = "—"
    equity_days: int = 30
    closed_trades: List[ClosedTrade] = []
    symbol_breakdown: list[dict] = []

    # Regret tab
    journal_tab: str = "trades"
    regret_missed_count: str = "0"
    regret_good_pass_count: str = "0"
    regret_miss_rate: str = "—"
    regret_miss_rate_high: bool = False  # True if miss rate >= 50%
    regret_phantoms: list[PhantomRecord] = []
    regret_playbooks: list[PlaybookRecord] = []
    journal_expanded_trades: list[str] = []
    journal_expanded_phantoms: list[str] = []
    journal_show_all_trades: bool = False
    journal_show_all_phantoms: bool = False

    def set_journal_tab(self, tab: str):
        self.journal_tab = tab

    def toggle_trade_detail(self, trade_id: str):
        """Toggle expand/collapse for a trade row."""
        if trade_id in self.journal_expanded_trades:
            self.journal_expanded_trades = [t for t in self.journal_expanded_trades if t != trade_id]
        else:
            self.journal_expanded_trades = self.journal_expanded_trades + [trade_id]

    def toggle_phantom_detail(self, phantom_id: str):
        """Toggle expand/collapse for a phantom row."""
        if phantom_id in self.journal_expanded_phantoms:
            self.journal_expanded_phantoms = [p for p in self.journal_expanded_phantoms if p != phantom_id]
        else:
            self.journal_expanded_phantoms = self.journal_expanded_phantoms + [phantom_id]

    def toggle_show_all_trades(self):
        self.journal_show_all_trades = not self.journal_show_all_trades

    def toggle_show_all_phantoms(self):
        self.journal_show_all_phantoms = not self.journal_show_all_phantoms

    def set_equity_days(self, days: str):
        """Update equity chart timeframe."""
        self.equity_days = int(days)

    def go_to_journal(self):
        """Navigate to journal page and load data."""
        self.current_page = "journal"
        self.journal_unread = False
        return AppState.load_journal

    def go_to_debug(self):
        """Navigate to the debug page."""
        self.current_page = "debug"
        return AppState.load_debug_traces

    def go_to_settings(self):
        """Navigate to settings page and load current values."""
        self.current_page = "settings"
        self._load_settings_state()

    def _load_settings_state(self):
        """Load current TradingSettings into state fields."""
        from hynous.core.trading_settings import get_trading_settings
        from hynous.core.config import load_config
        ts = get_trading_settings()
        cfg = load_config()
        self.settings_macro_sl_min = ts.macro_sl_min_pct
        self.settings_macro_sl_max = ts.macro_sl_max_pct
        self.settings_macro_tp_min = ts.macro_tp_min_pct
        self.settings_macro_tp_max = ts.macro_tp_max_pct
        self.settings_macro_lev_min = ts.macro_leverage_min
        self.settings_macro_lev_max = ts.macro_leverage_max
        self.settings_micro_sl_min = ts.micro_sl_min_pct
        self.settings_micro_sl_warn = ts.micro_sl_warn_pct
        self.settings_micro_sl_max = ts.micro_sl_max_pct
        self.settings_micro_tp_min = ts.micro_tp_min_pct
        self.settings_micro_tp_max = ts.micro_tp_max_pct
        self.settings_micro_leverage = ts.micro_leverage
        self.settings_rr_floor_reject = ts.rr_floor_reject
        self.settings_rr_floor_warn = ts.rr_floor_warn
        self.settings_risk_cap_reject = ts.portfolio_risk_cap_reject
        self.settings_risk_cap_warn = ts.portfolio_risk_cap_warn
        self.settings_roe_reject = ts.roe_at_stop_reject
        self.settings_roe_warn = ts.roe_at_stop_warn
        self.settings_roe_target = ts.roe_target
        self.settings_tier_high = ts.tier_high_margin_pct
        self.settings_tier_medium = ts.tier_medium_margin_pct
        self.settings_tier_speculative = ts.tier_speculative_margin_pct
        self.settings_tier_pass = ts.tier_pass_threshold
        self.settings_max_position = ts.max_position_usd
        self.settings_max_positions = ts.max_open_positions
        self.settings_max_daily_loss = ts.max_daily_loss_usd
        self.settings_live_trading = str(cfg.execution.mode).startswith("live")
        self.settings_dirty = False

    def save_settings(self):
        """Build TradingSettings from state, save, and apply."""
        from hynous.core.trading_settings import TradingSettings, save_trading_settings
        ts = TradingSettings(
            macro_sl_min_pct=self.settings_macro_sl_min,
            macro_sl_max_pct=self.settings_macro_sl_max,
            macro_tp_min_pct=self.settings_macro_tp_min,
            macro_tp_max_pct=self.settings_macro_tp_max,
            macro_leverage_min=self.settings_macro_lev_min,
            macro_leverage_max=self.settings_macro_lev_max,
            micro_sl_min_pct=self.settings_micro_sl_min,
            micro_sl_warn_pct=self.settings_micro_sl_warn,
            micro_sl_max_pct=self.settings_micro_sl_max,
            micro_tp_min_pct=self.settings_micro_tp_min,
            micro_tp_max_pct=self.settings_micro_tp_max,
            micro_leverage=self.settings_micro_leverage,
            rr_floor_reject=self.settings_rr_floor_reject,
            rr_floor_warn=self.settings_rr_floor_warn,
            portfolio_risk_cap_reject=self.settings_risk_cap_reject,
            portfolio_risk_cap_warn=self.settings_risk_cap_warn,
            roe_at_stop_reject=self.settings_roe_reject,
            roe_at_stop_warn=self.settings_roe_warn,
            roe_target=self.settings_roe_target,
            tier_high_margin_pct=self.settings_tier_high,
            tier_medium_margin_pct=self.settings_tier_medium,
            tier_speculative_margin_pct=self.settings_tier_speculative,
            tier_pass_threshold=self.settings_tier_pass,
            max_position_usd=self.settings_max_position,
            max_open_positions=self.settings_max_positions,
            max_daily_loss_usd=self.settings_max_daily_loss,
        )
        save_trading_settings(ts)
        _apply_trading_settings(ts)
        _update_execution_config_file(self.settings_live_trading)
        _apply_execution_settings(self.settings_live_trading)
        self.settings_dirty = False

    def reset_settings(self):
        """Reset all settings to defaults."""
        from hynous.core.trading_settings import reset_trading_settings
        ts = reset_trading_settings()
        _apply_trading_settings(ts)
        self._load_settings_state()

    # --- Per-field setters (mark dirty) ---
    def set_settings_macro_sl_min(self, v: str):
        self.settings_macro_sl_min = float(v); self.settings_dirty = True
    def set_settings_macro_sl_max(self, v: str):
        self.settings_macro_sl_max = float(v); self.settings_dirty = True
    def set_settings_macro_tp_min(self, v: str):
        self.settings_macro_tp_min = float(v); self.settings_dirty = True
    def set_settings_macro_tp_max(self, v: str):
        self.settings_macro_tp_max = float(v); self.settings_dirty = True
    def set_settings_macro_lev_min(self, v: str):
        self.settings_macro_lev_min = int(float(v)); self.settings_dirty = True
    def set_settings_macro_lev_max(self, v: str):
        self.settings_macro_lev_max = int(float(v)); self.settings_dirty = True
    def set_settings_micro_sl_min(self, v: str):
        self.settings_micro_sl_min = float(v); self.settings_dirty = True
    def set_settings_micro_sl_warn(self, v: str):
        self.settings_micro_sl_warn = float(v); self.settings_dirty = True
    def set_settings_micro_sl_max(self, v: str):
        self.settings_micro_sl_max = float(v); self.settings_dirty = True
    def set_settings_micro_tp_min(self, v: str):
        self.settings_micro_tp_min = float(v); self.settings_dirty = True
    def set_settings_micro_tp_max(self, v: str):
        self.settings_micro_tp_max = float(v); self.settings_dirty = True
    def set_settings_micro_leverage(self, v: str):
        self.settings_micro_leverage = int(float(v)); self.settings_dirty = True
    def set_settings_rr_floor_reject(self, v: str):
        self.settings_rr_floor_reject = float(v); self.settings_dirty = True
    def set_settings_rr_floor_warn(self, v: str):
        self.settings_rr_floor_warn = float(v); self.settings_dirty = True
    def set_settings_risk_cap_reject(self, v: str):
        self.settings_risk_cap_reject = float(v); self.settings_dirty = True
    def set_settings_risk_cap_warn(self, v: str):
        self.settings_risk_cap_warn = float(v); self.settings_dirty = True
    def set_settings_roe_reject(self, v: str):
        self.settings_roe_reject = float(v); self.settings_dirty = True
    def set_settings_roe_warn(self, v: str):
        self.settings_roe_warn = float(v); self.settings_dirty = True
    def set_settings_roe_target(self, v: str):
        self.settings_roe_target = float(v); self.settings_dirty = True
    def set_settings_tier_high(self, v: str):
        self.settings_tier_high = int(float(v)); self.settings_dirty = True
    def set_settings_tier_medium(self, v: str):
        self.settings_tier_medium = int(float(v)); self.settings_dirty = True
    def set_settings_tier_speculative(self, v: str):
        self.settings_tier_speculative = int(float(v)); self.settings_dirty = True
    def set_settings_tier_pass(self, v: str):
        self.settings_tier_pass = float(v); self.settings_dirty = True
    def set_settings_max_position(self, v: str):
        self.settings_max_position = float(v); self.settings_dirty = True
    def set_settings_max_positions(self, v: str):
        self.settings_max_positions = int(float(v)); self.settings_dirty = True
    def set_settings_max_daily_loss(self, v: str):
        self.settings_max_daily_loss = float(v); self.settings_dirty = True
    def set_settings_live_trading(self, v: bool):
        self.settings_live_trading = v; self.settings_dirty = True

    def load_debug_traces(self):
        """Load recent traces for the debug sidebar."""
        try:
            from hynous.core.request_tracer import get_tracer
            tracer = get_tracer()
            self.debug_traces = tracer.get_recent_traces(limit=50)
        except Exception as e:
            logger.error("Failed to load debug traces: %s", e)
            self.debug_traces = []

    def select_debug_trace(self, trace_id: str):
        """Select a trace to view in detail."""
        self.debug_selected_trace_id = trace_id
        self.debug_expanded_spans = []
        try:
            from hynous.core.request_tracer import get_tracer
            trace = get_tracer().get_trace(trace_id)
            if trace:
                self.debug_selected_trace = trace
            else:
                self.debug_selected_trace = {}
        except Exception as e:
            logger.error("Failed to load trace %s: %s", trace_id, e)
            self.debug_selected_trace = {}

    def toggle_debug_span(self, span_id: str):
        """Toggle expansion of a span in the trace detail view."""
        if span_id in self.debug_expanded_spans:
            self.debug_expanded_spans = [s for s in self.debug_expanded_spans if s != span_id]
        else:
            self.debug_expanded_spans = self.debug_expanded_spans + [span_id]

    def set_debug_filter_source(self, value: str):
        """Set the source filter for debug traces."""
        self.debug_filter_source = value
        return AppState.load_debug_traces

    def set_debug_filter_status(self, value: str):
        """Set the status filter for debug traces."""
        self.debug_filter_status = value
        return AppState.load_debug_traces

    def refresh_debug_traces(self):
        """Refresh the trace list (called by manual refresh or polling)."""
        return AppState.load_debug_traces

    def load_debug_payload(self, payload_hash: str) -> str:
        """Load a content-addressed payload for display."""
        try:
            from hynous.core.trace_log import load_payload
            content = load_payload(payload_hash)
            return content or "(payload not found)"
        except Exception:
            return "(error loading payload)"

    @rx.var(cache=False)
    def debug_spans_display(self) -> list[dict]:
        """Prepare spans for display with pre-computed fields.

        This computed var is CRITICAL for Reflex compatibility:
        - rx.foreach only accepts single-argument lambdas (no index)
        - Python dict lookups don't work on reactive rx.Var values
        - We solve both by pre-processing spans server-side here

        IMPORTANT: This method also resolves all *_hash payload references
        to their actual content. Spans store large content (LLM messages,
        responses, injected context) via content-addressed hashes on disk.
        Here we load those payloads so the UI shows actual text, not hashes.

        Each span dict gets extra keys:
        - "span_id": unique identifier for expand/collapse
        - "label": display label ("LLM Call", "Tool", etc.)
        - "color": hex color for the badge/border
        - "summary": one-line description
        - "detail_json": formatted JSON string for expanded view (with resolved content)
        - "is_error": bool for error indicator
        """
        import json as _json
        spans = self.debug_selected_trace.get("spans", [])
        result = []

        # Lazy-load payload resolver (only import if we have spans)
        _load_payload = None
        if spans:
            try:
                from hynous.core.trace_log import load_payload
                _load_payload = load_payload
            except Exception:
                pass

        for i, span in enumerate(spans):
            span_type = span.get("type", "unknown")

            # Map span type -> label + color
            label_map = {
                "context": ("Context", "#818cf8"),
                "retrieval": ("Retrieval", "#34d399"),
                "llm_call": ("LLM Call", "#f59e0b"),
                "tool_execution": ("Tool", "#60a5fa"),
                "memory_op": ("Memory", "#a78bfa"),
                "compression": ("Compress", "#fb923c"),
                "queue_flush": ("Queue", "#94a3b8"),
                "trade_step": ("Trade", "#f472b6"),
            }
            label, color = label_map.get(span_type, (span_type, "#525252"))

            # Build summary based on span type
            if span_type == "llm_call":
                tokens = span.get("input_tokens", 0) + span.get("output_tokens", 0)
                model = span.get("model", "?")
                if tokens:
                    summary = f"{model} — {tokens} tok"
                else:
                    summary = f"{model} — streamed"
            elif span_type == "tool_execution":
                summary = span.get("tool_name", "")
            elif span_type == "retrieval":
                q = span.get("query", "")
                n = span.get("results_count", 0)
                summary = f'"{q[:50]}" → {n} results' if q else ""
            elif span_type == "memory_op":
                summary = f"{span.get('operation', '')}: {span.get('title', '')}"
            elif span_type == "context":
                parts = []
                if span.get("has_briefing"):
                    parts.append("briefing")
                if span.get("has_snapshot"):
                    parts.append("snapshot")
                summary = "Injected: " + ", ".join(parts) if parts else "Context injection"
            elif span_type == "compression":
                summary = f"{span.get('exchanges_evicted', 0)} exchanges evicted"
            elif span_type == "queue_flush":
                summary = f"{span.get('items_count', 0)} items"
            elif span_type == "trade_step":
                _step = span.get("step", "")
                _tool = span.get("trade_tool", "")
                _detail = span.get("detail", "")
                _ok = span.get("success", True)
                summary = f"{'[FAIL] ' if not _ok else ''}{_step}: {_detail}" if _detail else _step
            else:
                summary = ""

            # ---- Resolve payload hashes to actual content ----
            resolved = dict(span)
            if _load_payload:
                for key in list(resolved.keys()):
                    if key.endswith("_hash"):
                        try:
                            content = _load_payload(resolved[key])
                            if content is not None:
                                content_key = key.replace("_hash", "_content")
                                try:
                                    resolved[content_key] = _json.loads(content)
                                except (ValueError, TypeError):
                                    resolved[content_key] = content
                                del resolved[key]
                        except Exception:
                            pass  # Keep the hash if loading fails

            # JSON detail for expanded view (now with resolved content)
            try:
                detail = _json.dumps(resolved, indent=2, default=str)
            except Exception:
                detail = str(resolved)

            result.append({
                "span_id": f"span-{i}",
                "type": span_type,
                "label": label,
                "color": color,
                "summary": summary,
                "duration_ms": span.get("duration_ms", 0),
                "is_error": span.get("success") is False or "error" in span,
                "detail_json": detail,
            })
        return result

    @_background
    async def load_journal(self):
        """Load trade stats and equity data for journal page."""
        data = await asyncio.to_thread(self._fetch_journal_data)
        if data:
            async with self:
                stats, trades, breakdown = data
                self.journal_win_rate = f"{stats['win_rate']:.0f}%" if stats['total_trades'] > 0 else "—"
                sign = "+" if stats['total_pnl'] >= 0 else ""
                self.journal_total_pnl = f"{sign}${stats['total_pnl']:.2f}" if stats['total_trades'] > 0 else "—"
                pf = stats['profit_factor']
                self.journal_profit_factor = f"{pf:.2f}" if pf != float('inf') else "∞" if stats['total_trades'] > 0 else "—"
                self.journal_total_trades = str(stats['total_trades'])
                self.journal_fee_losses = str(stats.get('fee_losses', 0))
                # Streaks
                streak = stats['current_streak']
                if streak > 0:
                    self.journal_current_streak = f"+{streak}W"
                elif streak < 0:
                    self.journal_current_streak = f"{streak}L"
                else:
                    self.journal_current_streak = "—"
                self.journal_max_win_streak = str(stats['max_win_streak'])
                self.journal_max_loss_streak = str(stats['max_loss_streak'])
                # Avg duration
                dur = stats['avg_duration_hours']
                if dur >= 24:
                    self.journal_avg_duration = f"{dur / 24:.1f}d"
                elif dur > 0:
                    self.journal_avg_duration = f"{dur:.1f}h"
                else:
                    self.journal_avg_duration = "—"
                self.closed_trades = trades
                self.symbol_breakdown = breakdown

        # Regret data (phantom tracker + playbooks from Nous)
        regret = await asyncio.to_thread(self._fetch_regret_data)
        if regret:
            phantoms = []
            for node in regret["missed"] + regret["good"]:
                try:
                    body = json.loads(node.get("content_body", "{}"))
                    sig = body.get("signals", {})
                    created = node.get("created_at", "")
                    date_str = created.split("T")[0] if "T" in created else created[:10]
                    headline = node.get("content_title", "")
                    node_id = node.get("id", "")
                    ep = float(sig.get("entry_price", 0) or sig.get("entry_px", 0) or 0)
                    xp = float(sig.get("exit_price", 0) or sig.get("exit_px", 0) or 0)
                    detail_html = _build_phantom_detail_html(sig, headline)
                    phantoms.append(PhantomRecord(
                        symbol=sig.get("symbol", "?"),
                        side=sig.get("side", "?"),
                        result=sig.get("result", "?"),
                        pnl_pct=round(float(sig.get("pnl_pct", 0)), 1),
                        anomaly_type=sig.get("anomaly_type", "?"),
                        category=sig.get("category", "?"),
                        date=date_str,
                        headline=headline,
                        phantom_id=node_id or f"p_{date_str}_{sig.get('symbol', '')}",
                        entry_px=ep,
                        exit_px=xp,
                        detail_html=detail_html,
                    ))
                except Exception:
                    continue
            # Sort newest first
            phantoms.sort(key=lambda p: p.date, reverse=True)

            pbs = []
            for node in regret["playbooks"]:
                try:
                    created = node.get("created_at", "")
                    date_str = created.split("T")[0] if "T" in created else created[:10]
                    body_raw = node.get("content_body", "")
                    # Body may be JSON or plain text
                    try:
                        body_data = json.loads(body_raw)
                        content = body_data.get("text", body_raw)
                    except (json.JSONDecodeError, TypeError):
                        content = body_raw
                    pbs.append(PlaybookRecord(
                        title=node.get("content_title", "Untitled"),
                        content=content or "",
                        date=date_str,
                    ))
                except Exception:
                    continue
            pbs.sort(key=lambda p: p.date, reverse=True)

            missed_count = sum(1 for p in phantoms if p.result == "missed_opportunity")
            good_count = sum(1 for p in phantoms if p.result == "good_pass")
            total = missed_count + good_count
            rate_pct = (missed_count / total * 100) if total > 0 else 0
            rate = f"{rate_pct:.0f}%" if total > 0 else "—"

            async with self:
                self.regret_phantoms = phantoms
                self.regret_playbooks = pbs
                self.regret_missed_count = str(missed_count)
                self.regret_good_pass_count = str(good_count)
                self.regret_miss_rate = rate
                self.regret_miss_rate_high = rate_pct >= 50

    @staticmethod
    def _fetch_journal_data():
        """Fetch journal data from trade analytics + Nous enrichment (sync, runs in thread)."""
        try:
            from hynous.core.trade_analytics import get_trade_stats
            stats = get_trade_stats()

            # Fetch raw close nodes from Nous for enrichment
            close_nodes_by_key: dict[tuple, dict] = {}
            nous_client = None
            try:
                from hynous.nous.client import get_client
                nous_client = get_client()
                if nous_client:
                    raw_nodes = nous_client.list_nodes(subtype="custom:trade_close", limit=500)
                    for node in raw_nodes:
                        try:
                            body = json.loads(node.get("content_body", "{}"))
                            sig = body.get("signals", {})
                            sym = sig.get("symbol", "")
                            created = node.get("created_at", "")
                            if sym and created:
                                # Key: (symbol, date prefix) for matching to TradeRecords
                                close_nodes_by_key[(sym, created[:16])] = node
                        except Exception:
                            continue
            except Exception:
                pass

            trades = []
            for t in stats.trades:
                dur_h = round(t.duration_hours, 1)
                if dur_h >= 24:
                    dur_str = f"{dur_h / 24:.1f}d"
                elif dur_h >= 1:
                    dur_str = f"{dur_h:.1f}h"
                elif dur_h > 0:
                    dur_str = f"{max(1, int(dur_h * 60))}m"
                else:
                    dur_str = "—"

                # Match to raw Nous node for enrichment
                enrichment = {}
                close_node = close_nodes_by_key.get((t.symbol, t.closed_at[:16]))
                if not close_node:
                    # Try broader match by symbol + date
                    for key, node in close_nodes_by_key.items():
                        if key[0] == t.symbol and key[1][:10] == t.closed_at[:10]:
                            close_node = node
                            break
                if close_node and nous_client:
                    enrichment = _enrich_trade(close_node, nous_client)

                detail_html = _build_trade_detail_html(enrichment) if enrichment else ""

                trades.append(ClosedTrade(
                    symbol=t.symbol,
                    side=t.side,
                    entry_px=t.entry_px,
                    exit_px=t.exit_px,
                    pnl_pct=round(t.pnl_pct, 2),
                    pnl_usd=round(t.pnl_usd, 2),
                    closed_at=t.closed_at,
                    close_type=t.close_type,
                    duration_hours=dur_h,
                    duration_str=dur_str,
                    date=t.closed_at.split("T")[0] if "T" in t.closed_at else t.closed_at[:10],
                    trade_id=enrichment.get("trade_id", f"{t.symbol}_{t.closed_at[:16]}"),
                    thesis=enrichment.get("thesis", ""),
                    stop_loss=enrichment.get("stop_loss", 0.0),
                    take_profit=enrichment.get("take_profit", 0.0),
                    confidence=enrichment.get("confidence", 0.0),
                    rr_ratio=enrichment.get("rr_ratio", 0.0),
                    trade_type=enrichment.get("trade_type", ""),
                    mfe_pct=enrichment.get("mfe_pct", 0.0),
                    fee_loss=enrichment.get("fee_loss", False) or t.fee_loss,
                    detail_html=detail_html,
                ))
            breakdown = [
                {
                    "symbol": sym,
                    "trades": d["trades"],
                    "win_rate": d["win_rate"],
                    "pnl": f"${d['pnl']:.2f}",
                    "pnl_positive": d["pnl"] >= 0,
                }
                for sym, d in sorted(stats.by_symbol.items())
            ]
            return (
                {
                    "win_rate": stats.win_rate,
                    "total_pnl": stats.total_pnl,
                    "profit_factor": stats.profit_factor,
                    "total_trades": stats.total_trades,
                    "current_streak": stats.current_streak,
                    "max_win_streak": stats.max_win_streak,
                    "max_loss_streak": stats.max_loss_streak,
                    "avg_duration_hours": stats.avg_duration_hours,
                    "fee_losses": stats.fee_losses,
                },
                trades,
                breakdown,
            )
        except Exception:
            return None

    @staticmethod
    def _fetch_regret_data() -> dict | None:
        """Fetch phantom + playbook data from Nous (sync, runs in thread)."""
        try:
            from hynous.nous.client import get_client
            client = get_client()
            if client is None:
                return None

            missed = client.list_nodes(subtype="custom:missed_opportunity", limit=500)
            good = client.list_nodes(subtype="custom:good_pass", limit=500)
            playbooks = client.list_nodes(subtype="custom:playbook", limit=30)

            return {"missed": missed, "good": good, "playbooks": playbooks}
        except Exception:
            return None

    @rx.var(cache=False)
    def journal_equity_data(self) -> list[dict]:
        """Equity curve data for chart. cache=False: reads external file."""
        try:
            from hynous.core.equity_tracker import get_equity_data
            data = get_equity_data(days=self.equity_days)
            # Format for recharts
            result = []
            for point in data:
                ts = point.get("timestamp", 0)
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                result.append({
                    "date": dt.strftime("%m/%d"),
                    "value": point.get("account_value", 0),
                    "pnl": point.get("unrealized_pnl", 0),
                })
            return result
        except Exception:
            return []
