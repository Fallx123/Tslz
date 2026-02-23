"""Chat page — messages + activity sidebar."""

import reflex as rx
from ..state import AppState, Position, WakeItem
from ..components import chat_bubble, chat_input, typing_indicator, streaming_bubble, tool_indicator, ticker_badge


# ---------------------------------------------------------------------------
# Chat area (left)
# ---------------------------------------------------------------------------

def _welcome() -> rx.Component:
    """Welcome message when no chat history."""
    return rx.box(
        rx.vstack(
            rx.box(
                rx.text("H", font_size="1.75rem", font_weight="600", color="#a5b4fc"),
                width="72px",
                height="72px",
                border_radius="20px",
                background="linear-gradient(135deg, #1e1b4b 0%, #312e81 100%)",
                display="flex",
                align_items="center",
                justify_content="center",
                box_shadow="0 4px 24px rgba(99, 102, 241, 0.2)",
            ),
            rx.heading("Hey, I'm Tslz", size="7", color="#fafafa", font_weight="600"),
            rx.text(
                "Your trading partner. Ask me anything about markets, "
                "discuss trade ideas, or just chat about what's happening.",
                color="#525252",
                max_width="400px",
                text_align="center",
                line_height="1.6",
                font_size="0.95rem",
            ),
            spacing="4",
            align="center",
        ),
        display="flex",
        align_items="center",
        justify_content="center",
        flex="1",
        width="100%",
    )


def _messages() -> rx.Component:
    """Scrollable messages container with auto-scroll."""
    return rx.box(
        rx.box(
            rx.vstack(
                rx.foreach(AppState.messages, chat_bubble),
                # Loading state
                rx.cond(
                    AppState.is_loading,
                    rx.vstack(
                        rx.cond(
                            AppState.streaming_text != "",
                            streaming_bubble(AppState.streaming_display, AppState.streaming_show_avatar),
                            rx.fragment(),
                        ),
                        rx.cond(
                            AppState.active_tool != "",
                            tool_indicator(AppState.active_tool_display, AppState.active_tool_color),
                            rx.cond(
                                AppState.streaming_text == "",
                                typing_indicator(),
                                rx.fragment(),
                            ),
                        ),
                        spacing="0",
                        width="100%",
                    ),
                    rx.box(),
                ),
                rx.box(id="chat-bottom"),
                width="100%",
                spacing="0",
            ),
            width="100%",
            max_width="960px",
            padding_x="24px",
            padding_y="24px",
            margin="0 auto",
        ),
        id="messages-container",
        flex="1",
        width="100%",
        overflow_y="auto",
        overscroll_behavior="none",
        style={
            "scrollbar_width": "none",
            "&::-webkit-scrollbar": {"display": "none"},
        },
    )


def _confidence_indicator() -> rx.Component:
    """AI Confidence Level strip — shows the bot's conviction on its last trade signal."""
    return rx.cond(
        AppState.show_ai_confidence,
        rx.box(
            rx.hstack(
                rx.text(
                    "AI CONFIDENCE",
                    font_size="0.58rem",
                    font_weight="700",
                    color="#404040",
                    letter_spacing="0.07em",
                    flex_shrink="0",
                ),
                # Progress bar track
                rx.box(
                    rx.box(
                        width=AppState.ai_confidence_bar_width,
                        height="100%",
                        background=AppState.ai_confidence_color,
                        border_radius="999px",
                        transition="width 0.6s ease, background 0.4s ease",
                    ),
                    width="90px",
                    height="3px",
                    background="#1e1e1e",
                    border_radius="999px",
                    flex_shrink="0",
                    overflow="hidden",
                ),
                # Percentage
                rx.text(
                    AppState.ai_confidence_pct_str,
                    font_size="0.72rem",
                    color=AppState.ai_confidence_color,
                    font_family="JetBrains Mono, monospace",
                    font_weight="700",
                    flex_shrink="0",
                ),
                # Tier label
                rx.text(
                    AppState.ai_confidence_label,
                    font_size="0.6rem",
                    color="#525252",
                    font_weight="600",
                    letter_spacing="0.04em",
                    flex_shrink="0",
                ),
                # Context (e.g. "AAPL long")
                rx.cond(
                    AppState.ai_confidence_context != "",
                    rx.text(
                        "· " + AppState.ai_confidence_context,
                        font_size="0.62rem",
                        color="#3a3a3a",
                        font_family="JetBrains Mono, monospace",
                        white_space="nowrap",
                        overflow="hidden",
                        text_overflow="ellipsis",
                    ),
                    rx.fragment(),
                ),
                spacing="2",
                align="center",
                width="100%",
            ),
            width="100%",
            max_width="960px",
            padding_x="4px",
            padding_y="6px",
        ),
        rx.fragment(),
    )


def _input() -> rx.Component:
    """Fixed input area at bottom."""
    return rx.box(
        rx.vstack(
            _confidence_indicator(),
            rx.box(
                chat_input(
                    on_submit=AppState.send_message,
                    is_loading=AppState.is_loading,
                    on_stop=AppState.stop_generation,
                ),
                width="100%",
                max_width="960px",
            ),
            rx.text(
                "Tslz can make mistakes. Always verify important information.",
                font_size="0.65rem",
                color="#333",
                text_align="center",
                padding_top="8px",
            ),
            width="100%",
            align="center",
            spacing="1",
            padding_x="24px",
        ),
        width="100%",
        padding_y="16px",
        background="#0a0a0a",
        border_top="1px solid #141414",
    )


# ---------------------------------------------------------------------------
# Sidebar primitives
# ---------------------------------------------------------------------------

def _label(text: str) -> rx.Component:
    """Section header in sidebar."""
    return rx.text(
        text,
        font_size="0.6rem",
        font_weight="600",
        color="#404040",
        text_transform="uppercase",
        letter_spacing="0.06em",
    )


def _sep() -> rx.Component:
    """Horizontal separator."""
    return rx.box(width="100%", height="1px", background="#1a1a1a")


def _stat_line(label: str, value) -> rx.Component:
    """Key-value stat row."""
    return rx.hstack(
        rx.text(label, font_size="0.68rem", color="#525252"),
        rx.spacer(),
        rx.text(
            value,
            font_size="0.7rem",
            color="#a3a3a3",
            font_family="JetBrains Mono, monospace",
        ),
        width="100%",
        align="center",
    )


# ---------------------------------------------------------------------------
# Sidebar sections
# ---------------------------------------------------------------------------

def _status_section() -> rx.Component:
    """Agent + Daemon status in one compact row."""
    return rx.hstack(
        # Agent
        rx.hstack(
            rx.box(
                width="6px",
                height="6px",
                border_radius="50%",
                background=AppState.agent_status_color,
                flex_shrink="0",
            ),
            rx.text("Agent", font_size="0.65rem", color="#525252"),
            rx.text(AppState.agent_status_display, font_size="0.68rem", color="#a3a3a3"),
            spacing="1",
            align="center",
        ),
        rx.spacer(),
        # Daemon
        rx.hstack(
            rx.box(
                width="6px",
                height="6px",
                border_radius="50%",
                background=AppState.daemon_status_color,
                flex_shrink="0",
            ),
            rx.text(AppState.daemon_status_text, font_size="0.68rem", color="#a3a3a3"),
            spacing="1",
            align="center",
        ),
        width="100%",
        align="center",
    )


def _position_row(pos: Position) -> rx.Component:
    """Single position in sidebar."""
    return rx.hstack(
        rx.text(
            pos.symbol_short,
            font_size="0.78rem",
            color="#fafafa",
            font_weight="700",
            letter_spacing="0.02em",
        ),
        rx.box(
            rx.text(
                rx.cond(pos.pnl_usd >= 0, "+$", "-$")
                + rx.cond(pos.pnl_usd >= 0, pos.pnl_usd, pos.pnl_usd * -1).to(str),
                font_size="0.68rem",
                color=rx.cond(pos.pnl_usd >= 0, "#4ade80", "#f87171"),
                font_family="JetBrains Mono, monospace",
                font_weight="600",
            ),
            padding="0.18rem 0.45rem",
            border_radius="999px",
            border=rx.cond(pos.pnl_usd >= 0, "1px solid rgba(34,197,94,0.35)", "1px solid rgba(239,68,68,0.45)"),
            background=rx.cond(pos.pnl_usd >= 0, "rgba(34,197,94,0.08)", "rgba(239,68,68,0.12)"),
            width="fit-content",
        ),
        spacing="2",
        width="100%",
        padding_y="4px",
        align="center",
        justify_content="space-between",
    )


def _positions_section() -> rx.Component:
    """Positions — only rendered when positions exist."""
    return rx.cond(
        AppState.positions.length() > 0,
        rx.vstack(
            _label("Positions"),
            rx.vstack(
                rx.foreach(AppState.positions, _position_row),
                spacing="1",
                width="100%",
            ),
            spacing="2",
            width="100%",
        ),
        rx.fragment(),
    )


def _alpha_section() -> rx.Component:
    """Alpha gate summary for tracked symbols."""
    def _alpha_row(a):
        color = rx.cond(
            a.direction == "long", "#22c55e",
            rx.cond(a.direction == "short", "#ef4444", "#737373"),
        )
        symbol_short = rx.cond(
            a.symbol.contains("("),
            a.symbol.split("(")[0],
            a.symbol,
        )
        symbol_meta = rx.cond(
            a.symbol.contains("("),
            "(" + a.symbol.split("(")[1],
            "",
        )
        return rx.vstack(
            rx.hstack(
                rx.vstack(
                    rx.text(symbol_short, font_size="0.78rem", color="#fafafa", font_weight="600"),
                    rx.cond(
                        symbol_meta != "",
                        rx.text(symbol_meta, font_size="0.62rem", color="#525252"),
                        rx.fragment(),
                    ),
                    spacing="0",
                ),
                rx.spacer(),
                rx.text(
                    a.direction.upper() + " " + a.confidence_str,
                    font_size="0.72rem",
                    color=color,
                    font_weight="600",
                ),
                spacing="2",
                width="100%",
            ),
            rx.text(
                a.detail,
                font_size="0.65rem",
                color="#525252",
                white_space="nowrap",
                overflow="hidden",
                text_overflow="ellipsis",
                width="100%",
            ),
            spacing="1",
            width="100%",
            padding_y="2px",
        )

    return rx.vstack(
        _label("Alpha Gate"),
        rx.cond(
            AppState.alpha_status.length() > 0,
            rx.vstack(
                rx.foreach(AppState.alpha_status, _alpha_row),
                spacing="1",
                width="100%",
            ),
            rx.text(
                "No alpha data",
                font_size="0.7rem",
                color="#333",
                font_style="italic",
                padding_y="0.25rem",
            ),
        ),
        spacing="2",
        width="100%",
    )



def _dot_color(category):
    """Dot color by wake category."""
    return rx.cond(
        category == "fill", "#22c55e",
        rx.cond(
            category == "review", "#818cf8",
            rx.cond(
                category == "error", "#ef4444",
                rx.cond(
                    category == "watchpoint", "#fbbf24",
                    "#60a5fa",
                )
            )
        )
    )


def _wake_row(item: WakeItem) -> rx.Component:
    """Single wake event — click to view full details."""
    color = _dot_color(item.category)
    return rx.hstack(
        rx.box(
            width="5px",
            height="5px",
            border_radius="50%",
            background=color,
            flex_shrink="0",
            margin_top="5px",
        ),
        rx.vstack(
            rx.text(
                item.timestamp,
                font_size="0.58rem",
                color="#333",
                font_family="JetBrains Mono, monospace",
            ),
            rx.text(
                item.content,
                font_size="0.72rem",
                color="#737373",
                overflow="hidden",
                text_overflow="ellipsis",
                white_space="nowrap",
                max_width="100%",
            ),
            spacing="0",
            flex="1",
            min_width="0",
        ),
        spacing="2",
        width="100%",
        align="start",
        padding_y="3px",
        cursor="pointer",
        border_radius="4px",
        _hover={"background": "#141414"},
        on_click=AppState.view_wake_detail(item.full_content, item.category, item.timestamp),
    )


def _activity_section() -> rx.Component:
    """Scrollable activity feed — newest events first."""
    return rx.box(
        rx.vstack(
            _label("Activity"),
            rx.cond(
                AppState.wake_feed.length() > 0,
                rx.vstack(
                    rx.foreach(AppState.wake_feed, _wake_row),
                    spacing="0",
                    width="100%",
                ),
                rx.text(
                    "No activity yet",
                    font_size="0.7rem",
                    color="#333",
                    font_style="italic",
                    padding_y="0.5rem",
                ),
            ),
            spacing="2",
            width="100%",
        ),
        flex="1",
        min_height="0",
        width="100%",
        overflow_y="auto",
        style={
            "scrollbar_width": "none",
            "&::-webkit-scrollbar": {"display": "none"},
        },
    )


def _stats_section() -> rx.Component:
    """Compact stats footer."""
    return rx.vstack(
        _label("Stats"),
        _stat_line("Daily PnL", AppState.daemon_daily_pnl),
        _stat_line("Wakes", AppState.daemon_wake_count),
        _stat_line("Next Review", AppState.daemon_next_review),
        _stat_line("Last Wake", AppState.daemon_last_wake_ago),
        spacing="1",
        width="100%",
    )


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def _sidebar() -> rx.Component:
    """Right activity sidebar — status, positions, feed, stats."""
    return rx.box(
        # Top — fixed sections
        rx.vstack(
            _status_section(),
            _sep(),
            _positions_section(),
            rx.cond(
                AppState.positions.length() > 0,
                _sep(),
                rx.fragment(),
            ),
            _alpha_section(),
            _sep(),
            spacing="3",
            width="100%",
            flex_shrink="0",
        ),
        # Middle — scrollable activity feed
        _activity_section(),
        # Bottom — pinned stats
        rx.vstack(
            _sep(),
            _stats_section(),
            spacing="3",
            width="100%",
            flex_shrink="0",
        ),
        width="280px",
        min_width="280px",
        height="100%",
        padding="0.75rem",
        border_left="1px solid #141414",
        background="#0c0c0c",
        display="flex",
        flex_direction="column",
        gap="0.5rem",
    )


# ---------------------------------------------------------------------------
# Wake detail dialog
# ---------------------------------------------------------------------------

def _wake_detail_dialog() -> rx.Component:
    """Dialog showing full wake response content."""
    color = _dot_color(AppState.wake_detail_category)
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                # Header
                rx.hstack(
                    rx.box(
                        width="8px",
                        height="8px",
                        border_radius="50%",
                        background=color,
                        flex_shrink="0",
                    ),
                    rx.text(
                        AppState.wake_detail_category.upper(),
                        font_size="0.7rem",
                        font_weight="600",
                        color=color,
                        letter_spacing="0.04em",
                    ),
                    rx.text(
                        AppState.wake_detail_time,
                        font_size="0.7rem",
                        color="#525252",
                        font_family="JetBrains Mono, monospace",
                    ),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.icon(
                            "x",
                            size=16,
                            color="#525252",
                            cursor="pointer",
                            _hover={"color": "#fafafa"},
                        ),
                    ),
                    spacing="2",
                    align="center",
                    width="100%",
                ),
                # Body
                rx.box(
                    rx.text(
                        AppState.wake_detail_content,
                        font_size="0.82rem",
                        color="#d4d4d4",
                        line_height="1.6",
                        white_space="pre-wrap",
                        word_break="break-word",
                    ),
                    max_height="60vh",
                    overflow_y="auto",
                    width="100%",
                    padding="0.5rem 0",
                    style={
                        "scrollbar_width": "thin",
                        "scrollbar_color": "#262626 transparent",
                    },
                ),
                spacing="3",
                width="100%",
            ),
            max_width="560px",
            background="#111111",
            border="1px solid #1a1a1a",
            border_radius="12px",
            padding="1.25rem",
        ),
        open=AppState.wake_detail_open,
        on_open_change=lambda _: AppState.close_wake_detail(),
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def chat_page() -> rx.Component:
    """Full-height chat layout with activity sidebar."""
    return rx.box(
        rx.hstack(
            # Main chat area
            rx.box(
                rx.vstack(
                    rx.cond(
                        AppState.messages.length() > 0,
                        _messages(),
                        _welcome(),
                    ),
                    _input(),
                    width="100%",
                    height="100%",
                    spacing="0",
                    align="center",
                ),
                flex="1",
                height="100%",
                background="#0a0a0a",
                display="flex",
                flex_direction="column",
                overflow="hidden",
            ),
            # Activity sidebar
            _sidebar(),
            spacing="0",
            width="100%",
            height="100%",
            overflow="hidden",
        ),
        # Wake detail dialog (rendered outside flex to avoid layout issues)
        _wake_detail_dialog(),
        width="100%",
        height="100%",
        position="relative",
    )
