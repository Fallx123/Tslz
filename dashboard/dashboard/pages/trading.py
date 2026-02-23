"""Trading tab — built step by step."""

import reflex as rx
from ..state import AppState
from ..components import (
    chat_bubble,
    chat_input,
    typing_indicator,
    streaming_bubble,
    tool_indicator,
)

_BORDER = "1px solid #141414"
_MONO   = "JetBrains Mono, monospace"
_TFS    = ["1m", "5m", "15m", "1h", "4h", "1d"]


# ── Chat Panel (left) ──────────────────────────────────────────────────────────

def _chat_messages() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.foreach(AppState.messages, chat_bubble),
            rx.cond(
                AppState.is_loading,
                rx.vstack(
                    rx.cond(
                        AppState.streaming_text != "",
                        streaming_bubble(
                            AppState.streaming_display,
                            AppState.streaming_show_avatar,
                        ),
                        rx.fragment(),
                    ),
                    rx.cond(
                        AppState.active_tool != "",
                        tool_indicator(
                            AppState.active_tool_display,
                            AppState.active_tool_color,
                        ),
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
            rx.box(id="trading-chat-bottom"),
            spacing="0",
            width="100%",
            padding="16px",
        ),
        flex="1",
        width="100%",
        overflow_y="auto",
        style={
            "scrollbar_width": "none",
            "&::-webkit-scrollbar": {"display": "none"},
        },
    )


def _chat_panel() -> rx.Component:
    return rx.box(
        rx.vstack(
            # Header
            rx.hstack(
                rx.hstack(
                    rx.box(
                        rx.text("T", font_size="0.75rem", font_weight="700", color="#a5b4fc"),
                        width="26px",
                        height="26px",
                        border_radius="8px",
                        background="linear-gradient(135deg,#1e1b4b,#312e81)",
                        display="flex",
                        align_items="center",
                        justify_content="center",
                    ),
                    rx.text("Tslz", font_size="0.85rem", font_weight="700", color="#e2e8f0"),
                    rx.box(
                        width="6px", height="6px", border_radius="50%",
                        background=AppState.agent_status_color,
                        margin_top="1px",
                    ),
                    spacing="2",
                    align="center",
                ),
                rx.spacer(),
                rx.text("Chat", font_size="0.6rem", color="#404040",
                        text_transform="uppercase", letter_spacing="0.08em"),
                width="100%",
                align="center",
                padding="0.875rem 1rem",
                border_bottom=_BORDER,
            ),
            _chat_messages(),
            rx.box(
                chat_input(
                    on_submit=AppState.send_message,
                    is_loading=AppState.is_loading,
                    on_stop=AppState.stop_generation,
                ),
                width="100%",
                padding="0.75rem 1rem",
                border_top=_BORDER,
            ),
            spacing="0",
            width="100%",
            height="100%",
        ),
        width="380px",
        min_width="380px",
        height="100%",
        background="#0a0a0a",
        border_right=_BORDER,
        display="flex",
        flex_direction="column",
        flex_shrink="0",
    )


# ── Center Chart Panel ─────────────────────────────────────────────────────────

def _tf_btn(tf: str) -> rx.Component:
    is_active = AppState.trading_timeframe == tf
    return rx.button(
        tf.upper(),
        on_click=AppState.set_trading_timeframe(tf),
        font_size="0.67rem",
        font_weight="600",
        font_family=_MONO,
        padding="3px 9px",
        border_radius="5px",
        border=rx.cond(is_active, "1px solid #6366f1", "1px solid #1f1f1f"),
        background=rx.cond(is_active, "#13131f", "transparent"),
        color=rx.cond(is_active, "#a5b4fc", "#404040"),
        cursor="pointer",
        transition="all 0.1s",
        _hover={"border_color": "#374151", "color": "#a3a3a3"},
        min_width="auto",
        height="auto",
    )


def _chart_header() -> rx.Component:
    return rx.hstack(
        # Left: mark price + symbol dropdown
        rx.hstack(
            rx.text(
                AppState.trading_selected_mark_str,
                font_size="1rem",
                font_weight="700",
                color="#f1f5f9",
                font_family=_MONO,
            ),
            rx.select(
                AppState.trading_position_symbols,
                value=AppState.trading_selected_symbol,
                on_change=AppState.select_trading_position,
                placeholder="Select symbol…",
                size="1",
                variant="ghost",
                style={
                    "& > button": {
                        "background": "transparent",
                        "border": "1px solid #1f1f1f",
                        "border_radius": "5px",
                        "padding": "3px 8px",
                        "color": "#64748b",
                        "font_family": _MONO,
                        "font_size": "0.75rem",
                        "font_weight": "600",
                        "cursor": "pointer",
                        "_hover": {"border_color": "#374151", "color": "#a3a3a3"},
                    },
                },
            ),
            spacing="3",
            align="center",
        ),
        rx.spacer(),
        # Right: timeframe buttons
        rx.hstack(
            *[_tf_btn(tf) for tf in _TFS],
            spacing="1",
        ),
        width="100%",
        align="center",
        padding="0.6rem 0.875rem",
        border_bottom=_BORDER,
        flex_shrink="0",
    )


def _center_chart() -> rx.Component:
    return rx.box(
        rx.vstack(
            _chart_header(),
            # Chart iframe (clean Plotly chart — no TradingView)
            rx.cond(
                AppState.trading_live_chart_url != "",
                rx.el.iframe(
                    src=AppState.trading_live_chart_url,
                    width="100%",
                    height="100%",
                    style={"border": "none", "display": "block"},
                    allow="fullscreen",
                ),
                rx.center(
                    rx.vstack(
                        rx.icon("chart-candlestick", size=36, color="#1c1c1c"),
                        rx.text("Select a position to view chart",
                                font_size="0.8rem", color="#2a2a2a"),
                        spacing="3",
                        align="center",
                    ),
                    flex="1",
                    width="100%",
                ),
            ),
            spacing="0",
            width="100%",
            height="100%",
        ),
        flex="1",
        height="100%",
        overflow="hidden",
        display="flex",
        flex_direction="column",
    )


# ── Page ──────────────────────────────────────────────────────────────────────

def trading_page() -> rx.Component:
    return rx.box(
        rx.hstack(
            _chat_panel(),
            _center_chart(),
            # More panels will go here
            spacing="0",
            width="100%",
            height="100%",
            align="stretch",
        ),
        width="100%",
        height="100%",
        background="#0a0a0a",
        overflow="hidden",
    )
