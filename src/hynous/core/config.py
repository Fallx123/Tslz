"""
Configuration loading for Hynous.

Loads settings from config/default.yaml and environment variables.
"""

import os
import yaml
from dotenv import load_dotenv
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


def _find_project_root() -> Path:
    """Walk up from this file to find the project root (where config/ lives)."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "config").is_dir():
            return current
        current = current.parent
    raise FileNotFoundError("Could not find project root (no config/ directory found)")


@dataclass
class AgentConfig:
    model: str = "claude-3-5-sonnet-20241022"  # Anthropic direct
    max_tokens: int = 2048
    temperature: float = 0.7


@dataclass
class NousConfig:
    url: str = "http://localhost:3100"
    server_dir: str = "~/Desktop/nous-build/packages/server"
    db_path: str = "storage/nous.db"
    auto_retrieve_limit: int = 5


@dataclass
class ExecutionConfig:
    mode: str = "paper"  # paper | testnet | live_confirm | live_auto
    market: str = "equities"  # equities only
    paper_balance: float = 50000
    symbols: list[str] = field(default_factory=lambda: ["SPY", "AAPL", "NVDA"])


@dataclass
class AlpacaConfig:
    """Alpaca trading settings."""
    paper: bool = True
    data_feed: str = "iex"  # iex | sip | delayed_sip | boats | overnight


@dataclass
class MemoryConfig:
    """Tiered memory settings — working window + Nous-backed compression."""
    window_size: int = 4            # Complete exchanges to keep in working window
    max_context_tokens: int = 800   # Token budget for injected recalled context
    retrieve_limit: int = 5         # Max Nous results per retrieval
    compression_model: str = "claude-haiku-4-5-20251001"  # Anthropic direct
    compress_enabled: bool = True   # Master switch for automatic compression
    gate_filter_enabled: bool = True  # Pre-storage quality gate (MF-15)


@dataclass
class OrchestratorConfig:
    """Intelligent Retrieval Orchestrator — multi-pass memory search."""
    enabled: bool = True                    # Master switch
    quality_threshold: float = 0.20         # Min top-result score to accept
    relevance_ratio: float = 0.4            # Dynamic cutoff: score >= top * ratio
    max_results: int = 8                    # Hard cap on merged results
    max_sub_queries: int = 4                # Max decomposition parts
    max_retries: int = 1                    # Reformulation attempts per sub-query
    timeout_seconds: float = 3.0            # Total orchestration timeout
    search_limit_per_query: int = 10        # Results to fetch per sub-query (overfetch)


@dataclass
class DaemonConfig:
    """Background daemon settings — watchdog + curiosity + periodic review."""
    enabled: bool = False                 # Master switch
    price_poll_interval: int = 60         # Seconds between price polls
    deriv_poll_interval: int = 300        # Seconds between derivatives/sentiment polls
    periodic_interval: int = 3600         # Seconds between periodic market reviews
    curiosity_threshold: int = 3          # Pending curiosity items before learning session
    curiosity_check_interval: int = 900   # Seconds between curiosity queue checks
    # FSRS memory decay
    decay_interval: int = 21600           # Seconds between batch decay cycles (6 hours)
    # Contradiction queue polling
    conflict_check_interval: int = 1800   # Seconds between conflict queue checks (30 min)
    # Nous health monitoring
    health_check_interval: int = 3600     # Seconds between Nous health checks (1 hour)
    # Embedding backfill
    embedding_backfill_interval: int = 43200  # Seconds between embedding backfill runs (12 hours)
    # Risk guardrails
    max_daily_loss_usd: float = 100       # Pause trading after this daily loss
    max_open_positions: int = 3           # Max simultaneous positions
    # Wake rate limiting
    max_wakes_per_hour: int = 6           # Rate limit on agent wakes
    wake_cooldown_seconds: int = 120      # Min seconds between non-priority wakes
    # Support/Resistance refresh (equities)
    support_resistance_interval: int = 21600    # Seconds between S/R recompute (6 hours)


@dataclass
class DiscordConfig:
    """Discord bot settings — chat relay + daemon notifications."""
    enabled: bool = False
    token: str = ""              # from DISCORD_BOT_TOKEN env var
    channel_id: int = 0          # channel for notifications + chat
    stats_channel_id: int = 0    # separate channel for stats panel (0 = use channel_id)
    allowed_user_ids: list[int] = field(default_factory=list)  # only respond to these Discord users (empty = any)


@dataclass
class TelegramConfig:
    """Telegram bot settings."""
    enabled: bool = False
    token: str = ""  # from TELEGRAM_BOT_TOKEN env var
    notify_chat_id: int = 0  # chat/channel to post daemon notifications
    commands_only: bool = True  # ignore plain text unless it's a command
    notify_trade_only: bool = True  # only send trade-related daemon notifications
    allowed_user_ids: list[int] = field(default_factory=list)
    allowed_chat_ids: list[int] = field(default_factory=list)


@dataclass
class OptionsConfig:
    """Options trading settings."""
    enabled: bool = False
    provider: str = "massive"  # massive | none


@dataclass
class WebhookConfig:
    """Webhook settings (TradingView)."""
    enabled: bool = False
    token: str = ""  # shared secret for webhook auth
    host: str = "0.0.0.0"
    port: int = 9010


@dataclass
class Config:
    """Main application configuration."""

    # API keys (from environment)
    openrouter_api_key: str = ""    # OpenRouter (optional)
    anthropic_api_key: str = ""     # Anthropic direct
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""

    # Sub-configs
    agent: AgentConfig = field(default_factory=AgentConfig)
    nous: NousConfig = field(default_factory=NousConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    options: OptionsConfig = field(default_factory=OptionsConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)

    # Paths
    project_root: Path = field(default_factory=_find_project_root)


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from YAML file and environment variables."""
    root = _find_project_root()
    # Load .env from project root so API keys are available everywhere.
    load_dotenv(root / ".env")

    # Load YAML
    yaml_path = Path(config_path) if config_path else root / "config" / "default.yaml"
    raw = {}
    if yaml_path.exists():
        with open(yaml_path) as f:
            raw = yaml.safe_load(f) or {}

    # Load .env if it exists (simple key=value parsing)
    env_path = root / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

    # Build config
    agent_raw = raw.get("agent", {})
    nous_raw = raw.get("nous", {})
    exec_raw = raw.get("execution", {})
    mem_raw = raw.get("memory", {})
    alpaca_raw = raw.get("alpaca", {})
    options_raw = raw.get("options", {})
    daemon_raw = raw.get("daemon", {})
    discord_raw = raw.get("discord", {})
    telegram_raw = raw.get("telegram", {})
    webhook_raw = raw.get("webhook", {})
    orch_raw = raw.get("orchestrator", {})

    return Config(
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        alpaca_api_key=os.environ.get("ALPACA_API_KEY_ID", ""),
        alpaca_api_secret=os.environ.get("ALPACA_API_SECRET_KEY", ""),
        agent=AgentConfig(
            model=agent_raw.get("model", "claude-3-5-sonnet-20241022"),
            max_tokens=agent_raw.get("max_tokens", 2048),
            temperature=agent_raw.get("temperature", 0.7),
        ),
        nous=NousConfig(
            url=nous_raw.get("url", "http://localhost:3100"),
            server_dir=nous_raw.get("server_dir", "~/Desktop/nous-build/packages/server"),
            db_path=nous_raw.get("db_path", "storage/nous.db"),
            auto_retrieve_limit=nous_raw.get("auto_retrieve_limit", 5),
        ),
        execution=ExecutionConfig(
            mode=exec_raw.get("mode", "paper"),
            market=exec_raw.get("market", "equities"),
            paper_balance=exec_raw.get("paper_balance", 50000),
            symbols=exec_raw.get("symbols", ["SPY", "AAPL", "NVDA"]),
        ),
        memory=MemoryConfig(
            window_size=mem_raw.get("window_size", 4),
            max_context_tokens=mem_raw.get("max_context_tokens", 800),
            retrieve_limit=mem_raw.get("retrieve_limit", 5),
            compression_model=mem_raw.get("compression_model", "claude-haiku-4-5-20251001"),
            compress_enabled=mem_raw.get("compress_enabled", True),
            gate_filter_enabled=mem_raw.get("gate_filter_enabled", True),
        ),
        alpaca=AlpacaConfig(
            paper=alpaca_raw.get("paper", True),
            data_feed=alpaca_raw.get("data_feed", "iex"),
        ),
        options=OptionsConfig(
            enabled=options_raw.get("enabled", False),
            provider=options_raw.get("provider", "massive"),
        ),
        daemon=DaemonConfig(
            enabled=daemon_raw.get("enabled", False),
            price_poll_interval=daemon_raw.get("price_poll_interval", 60),
            deriv_poll_interval=daemon_raw.get("deriv_poll_interval", 300),
            periodic_interval=daemon_raw.get("periodic_interval", 3600),
            curiosity_threshold=daemon_raw.get("curiosity_threshold", 3),
            curiosity_check_interval=daemon_raw.get("curiosity_check_interval", 900),
            decay_interval=daemon_raw.get("decay_interval", 21600),
            conflict_check_interval=daemon_raw.get("conflict_check_interval", 1800),
            health_check_interval=daemon_raw.get("health_check_interval", 3600),
            embedding_backfill_interval=daemon_raw.get("embedding_backfill_interval", 43200),
            max_daily_loss_usd=daemon_raw.get("max_daily_loss_usd", 100),
            max_open_positions=daemon_raw.get("max_open_positions", 3),
            max_wakes_per_hour=daemon_raw.get("max_wakes_per_hour", 6),
            wake_cooldown_seconds=daemon_raw.get("wake_cooldown_seconds", 120),
            support_resistance_interval=daemon_raw.get("support_resistance_interval", 21600),
        ),
        discord=DiscordConfig(
            enabled=discord_raw.get("enabled", False),
            token=os.environ.get("DISCORD_BOT_TOKEN", ""),
            channel_id=discord_raw.get("channel_id", 0),
            stats_channel_id=discord_raw.get("stats_channel_id", 0),
            allowed_user_ids=discord_raw.get("allowed_user_ids", []),
        ),
        telegram=TelegramConfig(
            enabled=telegram_raw.get("enabled", False),
            token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            notify_chat_id=telegram_raw.get("notify_chat_id", 0),
            commands_only=telegram_raw.get("commands_only", True),
            notify_trade_only=telegram_raw.get("notify_trade_only", True),
            allowed_user_ids=telegram_raw.get("allowed_user_ids", []),
            allowed_chat_ids=telegram_raw.get("allowed_chat_ids", []),
        ),
        webhook=WebhookConfig(
            enabled=webhook_raw.get("enabled", False),
            token=os.environ.get("TRADINGVIEW_WEBHOOK_TOKEN", ""),
            host=webhook_raw.get("host", "0.0.0.0"),
            port=webhook_raw.get("port", 9010),
        ),
        orchestrator=OrchestratorConfig(
            enabled=orch_raw.get("enabled", True),
            quality_threshold=orch_raw.get("quality_threshold", 0.20),
            relevance_ratio=orch_raw.get("relevance_ratio", 0.4),
            max_results=orch_raw.get("max_results", 8),
            max_sub_queries=orch_raw.get("max_sub_queries", 4),
            max_retries=orch_raw.get("max_retries", 1),
            timeout_seconds=orch_raw.get("timeout_seconds", 3.0),
            search_limit_per_query=orch_raw.get("search_limit_per_query", 10),
        ),
        project_root=root,
    )
