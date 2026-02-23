"""Ticker symbol badge with auto-detected brand color."""

import reflex as rx


def _symbol_color(symbol: rx.Var[str]) -> rx.Var[str]:
    """Map equities ticker to a consistent accent color."""
    return rx.match(
        symbol,
        ("SPY", "#22c55e"),
        ("AAPL", "#60a5fa"),
        ("MSFT", "#38bdf8"),
        ("NVDA", "#f59e0b"),
        ("TSLA", "#ef4444"),
        ("AMD", "#a855f7"),
        ("QQQ", "#14b8a6"),
        "#818cf8",  # fallback indigo
    )


def ticker_badge(
    symbol: rx.Var[str],
    font_size: str = "0.85rem",
    font_weight: str = "500",
    color: str = "#e5e5e5",
) -> rx.Component:
    """Ticker symbol with colored dot indicator."""
    return rx.hstack(
        rx.box(
            width="8px",
            height="8px",
            border_radius="50%",
            background=_symbol_color(symbol),
            flex_shrink="0",
        ),
        rx.text(
            symbol,
            font_size=font_size,
            font_weight=font_weight,
            color=color,
        ),
        spacing="1",
        align="center",
    )
