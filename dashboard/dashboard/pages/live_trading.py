"""Live Trading tab — candlestick chart with technical indicators and bot analysis."""

import reflex as rx
from ..state import AppState, Position


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pnl_color(pnl: rx.Var) -> rx.Var:
    return rx.cond(pnl > 0, "#4ade80", rx.cond(pnl < 0, "#f87171", "#a3a3a3"))


# ── Quick symbol buttons ──────────────────────────────────────────────────────

_QUICK_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META"]


def _quick_symbol_btn(sym: str) -> rx.Component:
    is_active = AppState.live_symbol == sym
    return rx.button(
        sym,
        on_click=AppState.select_live_symbol(sym),
        font_size="0.67rem",
        font_weight="600",
        font_family="JetBrains Mono, monospace",
        padding="3px 8px",
        border_radius="5px",
        border=rx.cond(is_active, "1px solid #6366f1", "1px solid #1f2937"),
        background=rx.cond(is_active, "#13131f", "transparent"),
        color=rx.cond(is_active, "#a5b4fc", "#525252"),
        cursor="pointer",
        transition="all 0.12s ease",
        _hover={"border_color": "#374151", "color": "#a3a3a3"},
        min_width="auto",
        height="auto",
    )


# ── Position row (in sidebar) ─────────────────────────────────────────────────

def _position_row(pos: Position) -> rx.Component:
    is_selected = AppState.live_symbol == pos.symbol
    return rx.box(
        rx.vstack(
            rx.text(
                pos.symbol,
                font_size="0.76rem",
                font_weight="600",
                color="#fafafa",
                white_space="nowrap",
                overflow="hidden",
                text_overflow="ellipsis",
                width="100%",
            ),
            rx.hstack(
                rx.badge(
                    pos.side.upper(),
                    color_scheme=rx.cond(pos.side == "long", "green", "red"),
                    variant="soft",
                    size="1",
                ),
                rx.spacer(),
                rx.text(
                    rx.cond(pos.pnl >= 0, "+", "") + pos.pnl.to(str) + "%",
                    font_size="0.72rem",
                    font_weight="600",
                    color=_pnl_color(pos.pnl),
                    font_family="JetBrains Mono, monospace",
                ),
                width="100%",
                align="center",
            ),
            spacing="1",
            width="100%",
        ),
        on_click=AppState.select_live_position(pos.symbol),
        padding="0.55rem 0.7rem",
        border_radius="7px",
        background=rx.cond(is_selected, "#1a1a2e", "transparent"),
        border=rx.cond(is_selected, "1px solid #6366f1", "1px solid transparent"),
        cursor="pointer",
        transition="all 0.13s ease",
        _hover={"background": "#111827", "border_color": "#374151"},
        width="100%",
    )


# ── Left sidebar ──────────────────────────────────────────────────────────────

def _sidebar() -> rx.Component:
    return rx.box(
        rx.vstack(
            # Positions section
            rx.text(
                "Positions",
                font_size="0.64rem",
                font_weight="700",
                color="#525252",
                text_transform="uppercase",
                letter_spacing="0.1em",
                padding_x="0.75rem",
                padding_top="1rem",
                padding_bottom="0.4rem",
            ),
            rx.cond(
                AppState.positions.length() > 0,
                rx.vstack(
                    rx.foreach(AppState.positions, _position_row),
                    spacing="1",
                    width="100%",
                    padding_x="0.4rem",
                ),
                rx.box(
                    rx.text("No open positions", font_size="0.75rem", color="#404040"),
                    padding_x="0.75rem",
                    padding_bottom="0.5rem",
                ),
            ),

            # Divider
            rx.divider(border_color="#1a1a1a", width="100%", margin_y="0.5rem"),

            # Quick symbol section
            rx.text(
                "Watch",
                font_size="0.64rem",
                font_weight="700",
                color="#525252",
                text_transform="uppercase",
                letter_spacing="0.1em",
                padding_x="0.75rem",
                padding_bottom="0.4rem",
            ),
            rx.box(
                rx.flex(
                    *[_quick_symbol_btn(s) for s in _QUICK_SYMBOLS],
                    flex_wrap="wrap",
                    gap="4px",
                    width="100%",
                ),
                padding_x="0.75rem",
                width="100%",
            ),

            spacing="0",
            width="100%",
        ),
        width="192px",
        min_width="192px",
        height="100%",
        border_right="1px solid #1a1a1a",
        overflow_y="auto",
        background="#0d0d0d",
        flex_shrink="0",
        style={
            "scrollbar_width": "none",
            "&::-webkit-scrollbar": {"display": "none"},
        },
    )


# ── Timeframe tab ─────────────────────────────────────────────────────────────

def _tf_btn(label: str, value: str) -> rx.Component:
    is_active = AppState.live_timeframe == value
    return rx.button(
        label,
        on_click=AppState.set_live_timeframe(value),
        font_size="0.67rem",
        font_weight="600",
        font_family="JetBrains Mono, monospace",
        padding="3px 10px",
        border_radius="5px",
        border=rx.cond(is_active, "1px solid #6366f1", "1px solid #1f2937"),
        background=rx.cond(is_active, "#13131f", "transparent"),
        color=rx.cond(is_active, "#a5b4fc", "#525252"),
        cursor="pointer",
        transition="all 0.12s ease",
        _hover={"border_color": "#374151", "color": "#a3a3a3"},
        min_width="auto",
        height="auto",
    )


# ── Chart iframe ──────────────────────────────────────────────────────────────

def _chart_iframe() -> rx.Component:
    return rx.el.iframe(
        src=AppState.live_chart_url,
        width="100%",
        height="100%",
        style={"border": "none", "display": "block"},
        allow="fullscreen",
    )


# ── Main content ──────────────────────────────────────────────────────────────

def _main_content() -> rx.Component:
    return rx.vstack(
        # Header row: symbol + timeframe selector
        rx.hstack(
            rx.hstack(
                rx.box(
                    width="8px",
                    height="8px",
                    border_radius="50%",
                    background="#6366f1",
                    box_shadow="0 0 8px rgba(99,102,241,0.5)",
                    class_name="market-radar-active",
                ),
                rx.text(
                    AppState.live_symbol,
                    font_size="1.1rem",
                    font_weight="700",
                    color="#fafafa",
                    font_family="JetBrains Mono, monospace",
                ),
                rx.text(
                    "· Live Analysis",
                    font_size="0.75rem",
                    color="#404040",
                ),
                spacing="2",
                align="center",
            ),
            rx.spacer(),
            # Timeframe tabs
            rx.hstack(
                _tf_btn("5m",  "5m"),
                _tf_btn("15m", "15m"),
                _tf_btn("1h",  "1h"),
                _tf_btn("4h",  "4h"),
                _tf_btn("1d",  "1d"),
                spacing="1",
                align="center",
            ),
            width="100%",
            align="center",
            padding_bottom="0.6rem",
        ),

        # Chart (fills remaining space)
        rx.box(
            _chart_iframe(),
            flex="1",
            width="100%",
            min_height="0",
            border_radius="10px",
            overflow="hidden",
            border="1px solid #1a1a1a",
        ),

        spacing="0",
        width="100%",
        height="100%",
    )


# ── Page ──────────────────────────────────────────────────────────────────────

def live_trading_page() -> rx.Component:
    return rx.box(
        rx.hstack(
            _sidebar(),
            rx.box(
                _main_content(),
                flex="1",
                height="100%",
                padding="1.1rem 1.25rem",
                overflow="hidden",
                display="flex",
                flex_direction="column",
            ),
            width="100%",
            height="100%",
            spacing="0",
            align="stretch",
        ),
        width="100%",
        height="100%",
        overflow="hidden",
    )
