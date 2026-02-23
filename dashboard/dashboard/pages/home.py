"""Home page — redesigned dashboard layout."""

import reflex as rx
from ..state import AppState, DaemonActivity, HomeMover, WatchpointGroup, MODEL_LABELS, PortfolioTableRow
from ..components import ticker_badge


# ─── Visual constants ─────────────────────────────────────────────────────────

_CARD_BG = "#111111"
_CARD_BORDER = "1px solid #1a1a1a"
_CARD_SHADOW = "0 8px 18px rgba(0,0,0,0.25)"
_PILL_BTN = {
    "background": "#0a0a0a",
    "border": "1px solid #1a1a1a",
    "color": "#a3a3a3",
    "border_radius": "10px",
    "height": "32px",
    "font_size": "0.75rem",
    "font_weight": "600",
    "_hover": {"background": "#111111", "border_color": "#262626"},
}
_PILL_BTN_ACTIVE = {
    **_PILL_BTN,
    "background": "#1a1a1a",
    "color": "#fafafa",
}


# ─── Section header helper ────────────────────────────────────────────────────

def _section_header(icon_name: str, icon_color: str, title: str, badge: rx.Component = None) -> rx.Component:
    return rx.hstack(
        rx.icon(icon_name, size=14, color=icon_color),
        rx.text(
            title,
            font_size="0.7rem",
            font_weight="700",
            color="#525252",
            text_transform="uppercase",
            letter_spacing="0.08em",
        ),
        rx.spacer(),
        badge if badge else rx.fragment(),
        spacing="2",
        align="center",
        width="100%",
    )


def _card(content: rx.Component, **kwargs) -> rx.Component:
    return rx.box(
        content,
        background=_CARD_BG,
        border=_CARD_BORDER,
        border_radius="16px",
        padding="1.05rem",
        box_shadow=_CARD_SHADOW,
        width="100%",
        **kwargs,
    )


# ─── Stat cards row ───────────────────────────────────────────────────────────

def _stat_card(title: str, value: rx.Component, subtitle: rx.Component = None, delta: rx.Component = None) -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.text(title, font_size="0.8rem", font_weight="600", color="#9ca3af"),
            value,
            delta if delta else rx.fragment(),
            subtitle if subtitle else rx.fragment(),
            spacing="1",
            width="100%",
        ),
        background=_CARD_BG,
        border=_CARD_BORDER,
        border_radius="14px",
        padding="1.05rem 1.1rem",
        box_shadow=_CARD_SHADOW,
        min_width="0",
        width="100%",
        flex="1",
    )


def _stat_cards_row() -> rx.Component:
    return rx.hstack(
        _stat_card(
            "Portfolio Value",
            rx.hstack(
                rx.text(
                    AppState.portfolio_value_str,
                    font_size="1.65rem",
                    font_weight="700",
                    color="#fafafa",
                ),
                rx.icon("eye", size=16, color="#6b7280"),
                spacing="2",
                align="center",
            ),
            delta=rx.text(
                AppState.portfolio_change_amount_and_pct,
                font_size="0.82rem",
                font_weight="600",
                color=AppState.portfolio_change_color,
            ),
        ),
        _stat_card(
            "Stocks Owned",
            rx.text(
                AppState.positions_count,
                font_size="1.6rem",
                font_weight="700",
                color="#fafafa",
            ),
            subtitle=rx.text("positions", font_size="0.82rem", color="#6b7280"),
        ),
        _stat_card(
            "Total Invested",
            rx.text(
                AppState.total_invested_str,
                font_size="1.6rem",
                font_weight="700",
                color="#fafafa",
            ),
            delta=rx.text(
                AppState.positions_pnl_str_and_pct,
                font_size="0.82rem",
                font_weight="600",
                color=AppState.positions_pnl_color,
            ),
        ),
        _stat_card(
            "Available Cash",
            rx.text(
                AppState.available_cash_str,
                font_size="1.6rem",
                font_weight="700",
                color="#fafafa",
            ),
            subtitle=rx.text("Ready to invest", font_size="0.82rem", color="#6b7280"),
        ),
        spacing="4",
        width="100%",
        align_items="stretch",
        flex_wrap="nowrap",
    )


# ─── Market Overview + Portfolio Chart ────────────────────────────────────────

def _market_overview() -> rx.Component:
    """Portfolio overview header with portfolio value + area chart."""
    return _card(
        rx.vstack(
            rx.hstack(
                rx.text("Your portfolio", font_size="0.9rem", font_weight="600", color="#e5e7eb"),
                rx.spacer(),
                rx.hstack(
                    rx.button("1D", **_PILL_BTN_ACTIVE),
                    rx.button("1M", **_PILL_BTN),
                    rx.button("1Y", **_PILL_BTN),
                    rx.button("All", **_PILL_BTN),
                    spacing="2",
                    align="center",
                ),
                width="100%",
                align="center",
            ),
            rx.hstack(
                rx.text(
                    AppState.portfolio_value_str,
                    font_size="1.6rem",
                    font_weight="700",
                    color="#fafafa",
                ),
                rx.text(
                    AppState.portfolio_change_str,
                    font_size="1.4rem",
                    font_weight="700",
                    color=AppState.portfolio_change_color,
                ),
                spacing="3",
                align="center",
            ),
            rx.text(
                AppState.portfolio_last_updated,
                font_size="0.78rem",
                color="#6b7280",
            ),
            rx.cond(
                AppState.portfolio_area_svg != "",
                rx.box(
                    rx.html(AppState.portfolio_area_svg, width="100%", height="100%"),
                    width="100%",
                    height="280px",
                    border_radius="12px",
                    overflow="hidden",
                    background="#0b0d10",
                    border="1px solid #1b1f2a",
                ),
                rx.box(
                    rx.text("Loading chart...", font_size="0.8rem", color="#6b7280"),
                    width="100%",
                    height="280px",
                    display="flex",
                    align_items="center",
                    justify_content="center",
                    border_radius="12px",
                    border="1px solid #1b1f2a",
                    background="#0b0d10",
                ),
            ),
            spacing="3",
            width="100%",
        ),
    )


# ─── Top Movers ───────────────────────────────────────────────────────────────

def _mover_row(m: HomeMover) -> rx.Component:
    return rx.hstack(
        ticker_badge(m.symbol, font_size="0.78rem"),
        rx.box(
            rx.cond(
                m.sparkline_svg != "",
                rx.html(m.sparkline_svg),
                rx.box(width="90px", height="26px"),
            ),
            width="90px",
            height="26px",
            margin_top="2px",
            margin_left="70px",
            display="flex",
            align_items="center",
            justify_content="center",
        ),
        rx.spacer(),
        rx.box(
            rx.text(m.price, font_size="0.82rem", color="#e5e5e5", font_weight="600"),
            padding="0.25rem 0.55rem",
            border_radius="999px",
            background=rx.cond(m.is_up, "rgba(34,197,94,0.12)", "rgba(239,68,68,0.12)"),
            border=rx.cond(m.is_up, "1px solid rgba(34,197,94,0.25)", "1px solid rgba(239,68,68,0.25)"),
        ),
        rx.text(
            m.change_str,
            font_size="0.78rem",
            font_weight="600",
            color=rx.cond(m.is_up, "#22c55e", "#ef4444"),
            min_width="60px",
            text_align="right",
        ),
        width="100%",
        align="center",
        spacing="2",
        padding_y="0.3rem",
        border_bottom="1px solid #1a1a1a",
    )


def _top_movers() -> rx.Component:
    return _card(
        rx.vstack(
            _section_header("zap", "#f59e0b", "Top Movers"),
            rx.cond(
                AppState.home_movers.length() > 0,
                rx.vstack(
                    rx.foreach(AppState.home_movers, _mover_row),
                    width="100%",
                    spacing="0",
                ),
                rx.center(
                    rx.text("Loading...", font_size="0.8rem", color="#404040"),
                    padding_y="1rem",
                ),
            ),
            spacing="3",
            width="100%",
        ),
        flex="1",
        min_width="220px",
    )


# ─── Day PnL Heatmap ──────────────────────────────────────────────────────────

def _pnl_tile(pos) -> rx.Component:
    """Colored tile for a single open position's day PnL."""
    return rx.box(
        rx.vstack(
            rx.text(
                pos.symbol_short,
                font_size="0.84rem",
                font_weight="700",
                color="#e5e7eb",
                letter_spacing="0.03em",
            ),
            rx.text(
                pos.pnl.to(str) + "%",
                font_size="1.05rem",
                font_weight="800",
                color=rx.cond(pos.pnl >= 0, "#22c55e", "#ef4444"),
            ),
            rx.text(
                pos.pnl_usd.to(str),
                font_size="0.78rem",
                color=rx.cond(pos.pnl >= 0, "#16a34a", "#f87171"),
            ),
            spacing="1",
            align="center",
        ),
        background=rx.cond(
            pos.pnl >= 0,
            "rgba(16,185,129,0.10)",
            "rgba(239,68,68,0.15)",
        ),
        border=rx.cond(
            pos.pnl >= 0,
            "1px solid rgba(34,197,94,0.25)",
            "1px solid rgba(239,68,68,0.35)",
        ),
        border_radius="14px",
        padding="0.85rem 1rem",
        text_align="center",
        flex="1",
        min_width="80px",
    )


def _day_pnl_heatmap() -> rx.Component:
    return _card(
        rx.vstack(
            _section_header("grid-2x2", "#a78bfa", "Day PnL"),
            rx.cond(
                AppState.positions.length() > 0,
                rx.flex(
                    rx.foreach(AppState.positions, _pnl_tile),
                    flex_wrap="wrap",
                    gap="0.5rem",
                    width="100%",
                ),
                rx.center(
                    rx.text("No open positions", font_size="0.8rem", color="#404040"),
                    padding_y="1rem",
                ),
            ),
            spacing="3",
            width="100%",
        ),
        flex="1",
        min_width="220px",
    )


# ─── Portfolio Positions Table ────────────────────────────────────────────────

def _portfolio_col(
    text: str,
    width: str,
    align: str = "left",
    color: str = "#7f8498",
    weight: str = "600",
    background: str = "transparent",
) -> rx.Component:
    return rx.box(
        rx.text(
            text,
            font_size="0.74rem",
            font_weight=weight,
            color=color,
            text_align=align,
            letter_spacing="0.08em",
            text_transform="uppercase",
            white_space="nowrap",
        ),
        width=width,
        min_width=width,
        padding_x="0.2rem",
        border_radius="4px",
        background=background,
        display="flex",
        justify_content="flex-start" if align == "left" else "flex-end",
    )


def _option_badge(row: PortfolioTableRow) -> rx.Component:
    return rx.box(
        row.option_type,
        font_size="0.72rem",
        font_weight="700",
        letter_spacing="0.05em",
        padding="0.15rem 0.5rem",
        border_radius="8px",
        color=rx.cond(row.option_type == "PUT", "#f87171", "#4ade80"),
        background=rx.cond(row.option_type == "PUT", "rgba(239,68,68,0.12)", "rgba(34,197,94,0.12)"),
        border=rx.cond(row.option_type == "PUT", "1px solid rgba(239,68,68,0.2)", "1px solid rgba(34,197,94,0.2)"),
    )


def _portfolio_row(row: PortfolioTableRow) -> rx.Component:
    return rx.hstack(
        rx.box(
            rx.vstack(
                rx.hstack(
                    rx.text(row.instrument, font_size="1.0rem", font_weight="700", color="#e7e9f0"),
                    _option_badge(row),
                    spacing="2",
                    align="center",
                ),
                rx.text(row.subtitle, font_size="0.72rem", color="#6f758a"),
                spacing="1",
                align_items="start",
            ),
            width="230px",
            min_width="230px",
        ),
        _portfolio_col(row.strike_expiry, "170px", color="#9aa0b5", weight="500", background="#0a0a0a"),
        _portfolio_col(row.dte, "90px", color="#fbbf24"),
        _portfolio_col(row.qty, "85px", color="#8f94a8"),
        _portfolio_col(row.entry, "140px", color="#8f94a8"),
        _portfolio_col(row.current, "140px", color="#8f94a8"),
        _portfolio_col(row.pnl, "120px", color=rx.cond(row.is_pnl_up, "#4ade80", "#f87171"), weight="700"),
        _portfolio_col(row.delta, "120px", color="#8f94a8"),
        _portfolio_col(row.theta, "120px", color="#8f94a8"),
        width="100%",
        align="center",
        spacing="0",
        padding_x="1rem",
        padding_y="0.95rem",
        border_bottom="1px solid #1a1a1a",
    )


def _portfolio_table() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.hstack(
                _portfolio_col("Instrument", "230px"),
                _portfolio_col("Strike / Expiry", "170px", background="#0a0a0a"),
                _portfolio_col("DTE", "90px"),
                _portfolio_col("Qty", "85px"),
                _portfolio_col("Entry", "140px"),
                _portfolio_col("Current", "140px"),
                _portfolio_col("P&L", "120px"),
                _portfolio_col("Delta", "120px"),
                _portfolio_col("Theta", "120px"),
                width="100%",
                spacing="0",
                padding_x="1rem",
                padding_y="0.9rem",
                background="#0a0a0a",
                border_bottom="1px solid #1a1a1a",
            ),
            rx.cond(
                AppState.portfolio_table_rows.length() > 0,
                rx.vstack(
                    rx.foreach(AppState.portfolio_table_rows, _portfolio_row),
                    width="100%",
                    spacing="0",
                ),
                rx.center(
                    rx.text("No open positions", font_size="0.84rem", color="#6f758a"),
                    padding_y="1rem",
                ),
            ),
            width="100%",
            spacing="0",
            align_items="stretch",
        ),
        background=_CARD_BG,
        border=_CARD_BORDER,
        border_radius="14px",
        overflow_x="auto",
        width="100%",
    )


# ─── Recent Transactions ───────────────────────────────────────────────────────

def _fill_row(event: DaemonActivity) -> rx.Component:
    return rx.hstack(
        rx.box(
            rx.icon("arrow-up-right", size=12, color="#22c55e"),
            width="26px",
            height="26px",
            border_radius="7px",
            background="rgba(34,197,94,0.1)",
            display="flex",
            align_items="center",
            justify_content="center",
            flex_shrink="0",
        ),
        rx.vstack(
            rx.text(event.title, font_size="0.8rem", font_weight="500", color="#e5e5e5"),
            rx.text(event.detail, font_size="0.68rem", color="#525252", line_height="1.3"),
            spacing="0",
        ),
        spacing="3",
        align="start",
        width="100%",
        padding_y="0.35rem",
        border_bottom="1px solid #1a1a1a",
    )


def _recent_transactions() -> rx.Component:
    return _card(
        rx.vstack(
            _section_header("receipt", "#22d3ee", "Recent Transactions"),
            rx.cond(
                AppState.home_recent_fills.length() > 0,
                rx.vstack(
                    rx.foreach(AppState.home_recent_fills, _fill_row),
                    width="100%",
                    spacing="0",
                ),
                rx.center(
                    rx.text("No recent trades", font_size="0.8rem", color="#404040"),
                    padding_y="1rem",
                ),
            ),
            spacing="3",
            width="100%",
        ),
    )


# ─── News ─────────────────────────────────────────────────────────────────────

def _news_section() -> rx.Component:
    return _card(
        rx.vstack(
                rx.hstack(
                    _section_header("newspaper", "#22d3ee", "News"),
                    rx.text("via Alpaca News", font_size="0.6rem", color="#404040"),
                    rx.icon(
                        rx.cond(AppState.news_expanded, "chevron-up", "chevron-down"),
                        size=14,
                    color="#525252",
                    cursor="pointer",
                ),
                width="100%",
                align="center",
                cursor="pointer",
                on_click=AppState.toggle_news_expanded,
            ),
            rx.cond(
                AppState.news_expanded,
                rx.html(AppState.news_feed_html),
                rx.fragment(),
            ),
            spacing="3",
            width="100%",
        ),
    )


# ─── Sidebar: Watchlist ────────────────────────────────────────────────────────

def _wp_row(group: WatchpointGroup) -> rx.Component:
    return rx.hstack(
        rx.box(
            ticker_badge(group.symbol, font_size="0.75rem"),
            width="70px",
        ),
        rx.box(
            rx.cond(
                group.sparkline_svg != "",
                rx.html(group.sparkline_svg),
                rx.box(width="80px", height="24px"),
            ),
            width="80px",
            height="24px",
            margin_left="6px",
            display="flex",
            align_items="center",
            justify_content="center",
        ),
        rx.spacer(),
        rx.box(
            rx.cond(
                group.price != "",
                rx.box(
                    rx.text(group.price, font_size="0.78rem", color="#e5e5e5", font_weight="600"),
                    padding="0.2rem 0.55rem",
                    border_radius="999px",
                    background=rx.cond(group.is_up, "rgba(34,197,94,0.12)", "rgba(239,68,68,0.12)"),
                    border=rx.cond(group.is_up, "1px solid rgba(34,197,94,0.25)", "1px solid rgba(239,68,68,0.25)"),
                    width="92px",
                    text_align="center",
                ),
                rx.box(width="92px"),
            ),
            display="flex",
            justify_content="flex-end",
        ),
        width="100%",
        align="center",
        padding_y="0.3rem",
        border_bottom="1px solid #1a1a1a",
        spacing="2",
    )


def _watchlist_sidebar() -> rx.Component:
    return _card(
        rx.vstack(
            _section_header(
                "eye", "#fbbf24", "Watchlist",
            ),
            rx.cond(
                AppState.watchpoint_groups.length() > 0,
                rx.vstack(
                    rx.foreach(AppState.watchpoint_groups, _wp_row),
                    width="100%",
                    spacing="0",
                ),
                rx.center(
                    rx.text("No watchpoints", font_size="0.8rem", color="#404040"),
                    padding_y="0.75rem",
                ),
            ),
            spacing="3",
            width="100%",
        ),
    )


# ─── Sidebar: Alerts ──────────────────────────────────────────────────────────

def _alerts_sidebar() -> rx.Component:
    return _card(
        rx.vstack(
            _section_header("bell", "#f87171", "Alerts"),
            rx.text(
                "Tell Tslz to set an alert — e.g. \"BTC above 90k\"",
                font_size="0.68rem",
                color="#525252",
                line_height="1.4",
            ),
            rx.form(
                rx.hstack(
                    rx.input(
                        name="alert",
                        placeholder="e.g. ETH below 2000",
                        value=AppState.home_alert_input,
                        on_change=AppState.set_home_alert_input,
                        background="#0a0a0a",
                        border="1px solid #1a1a1a",
                        border_radius="8px",
                        color="#e5e5e5",
                        font_size="0.78rem",
                        height="34px",
                        flex="1",
                        _focus={"border_color": "#2a2a2a", "outline": "none"},
                        _placeholder={"color": "#404040"},
                    ),
                    rx.button(
                        rx.icon("send", size=14),
                        type="submit",
                        background="#1a1a1a",
                        color="#fafafa",
                        border="none",
                        border_radius="8px",
                        height="34px",
                        width="34px",
                        padding="0",
                        cursor="pointer",
                        flex_shrink="0",
                        _hover={"background": "#262626"},
                    ),
                    spacing="2",
                    width="100%",
                ),
                on_submit=AppState.submit_home_alert,
                width="100%",
            ),
            spacing="3",
            width="100%",
        ),
    )


# ─── Sidebar: Model + Chat ────────────────────────────────────────────────────

def _model_select(label: str, value: rx.Var[str], on_change) -> rx.Component:
    return rx.vstack(
        rx.text(
            label,
            font_size="0.6rem",
            font_weight="600",
            color="#525252",
            text_transform="uppercase",
            letter_spacing="0.05em",
        ),
        rx.select(
            MODEL_LABELS,
            value=value,
            on_change=on_change,
            size="1",
            variant="ghost",
            width="100%",
            color="#a3a3a3",
            style={
                "& button": {
                    "background": "#0a0a0a",
                    "border": "1px solid #1a1a1a",
                    "border_radius": "8px",
                    "color": "#a3a3a3",
                    "font_size": "0.75rem",
                    "width": "100%",
                    "cursor": "pointer",
                    "&:hover": {"border_color": "#2a2a2a"},
                },
            },
        ),
        spacing="1",
        width="100%",
    )


def _model_and_chat_sidebar() -> rx.Component:
    return _card(
        rx.vstack(
            _section_header("cpu", "#a78bfa", "AI Model"),
            _model_select("Main Model", AppState.selected_model_label, AppState.set_agent_model),
            _model_select("Sub-Agent", AppState.selected_sub_model_label, AppState.set_sub_model),
            rx.divider(border_color="#1a1a1a"),
            rx.button(
                rx.icon("message-circle", size=15),
                "Chat with Tslz",
                on_click=AppState.go_to_chat,
                width="100%",
                background="#1a1a1a",
                color="#fafafa",
                border="none",
                border_radius="10px",
                height="40px",
                font_size="0.85rem",
                font_weight="500",
                cursor="pointer",
                _hover={"background": "#262626"},
                transition="background 0.15s ease",
                class_name="electric-btn",
            ),
            # Daemon quick toggle
            rx.hstack(
                rx.text("Daemon", font_size="0.82rem", color="#a3a3a3"),
                rx.spacer(),
                rx.hstack(
                    rx.box(
                        width="6px",
                        height="6px",
                        border_radius="50%",
                        background=AppState.daemon_status_color,
                    ),
                    rx.text(AppState.daemon_status_text, font_size="0.75rem", color="#737373"),
                    spacing="2",
                    align="center",
                ),
                rx.switch(
                    checked=AppState.daemon_running,
                    on_change=AppState.toggle_daemon,
                    color_scheme="indigo",
                    cursor="pointer",
                    size="1",
                ),
                width="100%",
                align="center",
                spacing="2",
            ),
            spacing="3",
            width="100%",
        ),
    )


# ─── Main layout ──────────────────────────────────────────────────────────────

def home_page() -> rx.Component:
    """Home page — two-column dashboard layout."""
    return rx.box(
        rx.hstack(
            # ── Left/main column ──────────────────────────────────────────────
            rx.vstack(
                # 0) Key stats cards
                _stat_cards_row(),

                # 1) Portfolio positions table (screenshot style)
                _portfolio_table(),

                # 2) Top Movers + Day PnL side by side
                rx.hstack(
                    _top_movers(),
                    _day_pnl_heatmap(),
                    spacing="4",
                    width="100%",
                    align_items="stretch",
                    flex_wrap="wrap",
                ),

                # 3) News
                _news_section(),

                flex="1",
                spacing="4",
                width="100%",
                min_width="0",
                align_items="stretch",
            ),

            # ── Right sidebar ─────────────────────────────────────────────────
            rx.vstack(
                _watchlist_sidebar(),
                _alerts_sidebar(),
                _model_and_chat_sidebar(),
                width="280px",
                min_width="240px",
                max_width="300px",
                flex_shrink="0",
                spacing="4",
                align_items="stretch",
            ),

            width="100%",
            spacing="4",
            align_items="start",
        ),
        width="100%",
        height="100%",
        padding="22px",
        max_width="100%",
        margin="0",
        overflow_y="auto",
        overscroll_behavior="none",
        style={
            "background": "#0a0a0a",
            "scrollbar_width": "thin",
            "scrollbar_color": "#262626 transparent",
        },
    )
