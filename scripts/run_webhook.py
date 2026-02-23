"""
Run TradingView Webhook Server

Usage:
    python -m scripts.run_webhook
"""

import uvicorn

from hynous.core.config import load_config


def main():
    cfg = load_config()
    host = cfg.webhook.host if cfg.webhook else "0.0.0.0"
    port = cfg.webhook.port if cfg.webhook else 9010
    uvicorn.run(
        "hynous.webhook.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
