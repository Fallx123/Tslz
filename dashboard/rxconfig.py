"""Reflex configuration for Hynous Dashboard."""

import os
import reflex as rx

_api_url = os.environ.get("API_URL", "http://localhost:8010")

config = rx.Config(
    app_name="dashboard",
    title="Hynous",
    description="Equities/Options Intelligence Dashboard",
    api_url=_api_url,
    frontend_port=3010,
    backend_port=8010,

    # Disable sitemap plugin warning
    plugins=[],

    # Theme
    tailwind={
        "theme": {
            "extend": {
                "colors": {
                    "background": "#0a0a0a",
                    "surface": "#141414",
                    "border": "#262626",
                    "muted": "#737373",
                    "accent": "#6366f1",
                    "accent-hover": "#4f46e5",
                    "positive": "#22c55e",
                    "negative": "#ef4444",
                },
            },
        },
    },
)
