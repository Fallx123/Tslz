"""
Trading Settings — runtime-adjustable trading parameters.

Central dataclass with all tunable thresholds, persisted to
storage/trading_settings.json. Thread-safe singleton with lazy loading.
"""

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _storage_path() -> Path:
    """Get the trading settings file path."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "config").is_dir():
            return current / "storage" / "trading_settings.json"
        current = current.parent
    return Path("storage/trading_settings.json")


@dataclass
class TradingSettings:
    """All adjustable trading parameters."""

    # --- Macro trade limits ---
    macro_sl_min_pct: float = 1.0
    macro_sl_max_pct: float = 5.0
    macro_tp_min_pct: float = 2.0
    macro_tp_max_pct: float = 15.0
    macro_leverage_min: int = 5
    macro_leverage_max: int = 20

    # --- Micro trade limits ---
    micro_sl_min_pct: float = 0.2
    micro_sl_warn_pct: float = 0.3
    micro_sl_max_pct: float = 0.8
    micro_tp_min_pct: float = 0.20  # Minimum TP distance — must clear round-trip fees
    micro_tp_max_pct: float = 1.0
    micro_leverage: int = 20

    # --- Risk management ---
    rr_floor_reject: float = 1.0
    rr_floor_warn: float = 1.5
    portfolio_risk_cap_reject: float = 10.0
    portfolio_risk_cap_warn: float = 5.0
    roe_at_stop_reject: float = 25.0
    roe_at_stop_warn: float = 15.0
    roe_target: float = 15.0

    # --- Conviction sizing (margin % of portfolio) ---
    tier_high_margin_pct: int = 30
    tier_medium_margin_pct: int = 20
    tier_speculative_margin_pct: int = 10
    tier_pass_threshold: float = 0.6

    # --- General limits ---
    max_position_usd: float = 10000
    max_open_positions: int = 3
    max_daily_loss_usd: float = 100

    # --- Rules gate (hard checks before execution) ---
    rules_enabled: bool = True
    rules_allow_if_data_missing: bool = False
    rules_require_trend_alignment: bool = True
    rules_trend_timeframes: list[str] = field(default_factory=lambda: ["1h", "4h", "1d"])
    rules_trend_min_move_pct: float = 0.2
    rules_require_volume_confirm: bool = True
    rules_volume_lookback_days: int = 20
    rules_volume_min_multiple: float = 1.1
    rules_volume_symbol_overrides: dict[str, float] = field(default_factory=dict)
    rules_volume_sector_overrides: dict[str, float] = field(default_factory=dict)
    rules_volume_marketcap_overrides: dict[str, float] = field(default_factory=dict)
    rules_sector_map: dict[str, str] = field(default_factory=dict)
    rules_marketcap_map: dict[str, str] = field(default_factory=dict)

    # --- Market regime filter ---
    rules_regime_enabled: bool = True
    rules_regime_symbol: str = "SPY"
    rules_regime_timeframes: list[str] = field(default_factory=lambda: ["1d"])
    rules_regime_min_move_pct: float = 0.2

    # --- ATR / volatility filter ---
    rules_atr_enabled: bool = True
    rules_atr_timeframe: str = "1d"
    rules_atr_period: int = 14
    rules_atr_min_pct: float = 0.6
    rules_atr_max_pct: float = 6.0

    # --- Risk-off switch ---
    rules_riskoff_enabled: bool = True
    rules_riskoff_mode: str = "block_longs"  # "block_longs" or "block_all"
    rules_riskoff_strict: bool = False
    rules_riskoff_vix_symbol: str = "^VIX"
    rules_riskoff_vix_threshold: float = 22.0
    rules_riskoff_vix_short_only_threshold: float = 30.0
    rules_riskoff_benchmark_symbol: str = "SPY"
    rules_riskoff_trend_timeframes: list[str] = field(default_factory=lambda: ["1d"])
    rules_riskoff_trend_min_move_pct: float = 0.2
    rules_riskoff_drawdown_lookback_days: int = 5
    rules_riskoff_drawdown_pct: float = 2.5

    # --- Alpha models gate ---
    rules_alpha_enabled: bool = True
    rules_alpha_require_agreement: bool = True
    rules_alpha_models: list[str] = field(default_factory=lambda: [
        "momentum", "mean_reversion", "pairs", "ema_cross", "rsi", "macd", "historical_returns"
    ])
    rules_alpha_params: dict[str, float] = field(default_factory=lambda: {
        "momentum_lookback": 20,
        "momentum_min_pct": 2.0,
        "mean_reversion_lookback": 20,
        "mean_reversion_z": 1.5,
        "ema_fast": 12,
        "ema_slow": 26,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "rsi_period": 14,
        "rsi_overbought": 70,
        "rsi_oversold": 30,
        "historical_returns_lookback": 20,
        "pairs_lookback": 60,
        "pairs_map": {},
    })

    # --- Weekly retrain ---
    retrain_enabled: bool = True
    retrain_interval_days: int = 7
    retrain_min_trades: int = 10
    retrain_last_ts: float = 0.0

    # --- User caps ---
    fixed_notional_cap_usd: float = 200.0
    max_loss_usd: float = 100.0
    options_only: bool = True

    # --- Options gates ---
    options_min_dte: int = 5
    options_max_dte: int = 45
    options_iv_min: float = 0.20
    options_iv_max: float = 1.50
    options_dte_warn_days: int = 7
    options_dte_urgent_days: int = 1
    options_dte_alert_cooldown_hours: int = 6


_lock = threading.Lock()
_cached: TradingSettings | None = None


def _atomic_write(path: Path, data: str) -> None:
    """Write to file atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_trading_settings() -> TradingSettings:
    """Get the current trading settings (lazy-loaded, cached)."""
    global _cached
    if _cached is not None:
        return _cached

    with _lock:
        if _cached is not None:
            return _cached

        path = _storage_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                ts = TradingSettings()
                for k, v in data.items():
                    if hasattr(ts, k):
                        setattr(ts, k, type(getattr(ts, k))(v))
                _cached = ts
                return _cached
            except Exception:
                pass

        _cached = TradingSettings()
        return _cached


def save_trading_settings(ts: TradingSettings) -> None:
    """Persist trading settings to disk and update cache."""
    global _cached
    with _lock:
        path = _storage_path()
        _atomic_write(path, json.dumps(asdict(ts), indent=2))
        _cached = ts


def update_setting(key: str, value) -> TradingSettings:
    """Update a single setting by key, save, and return updated settings."""
    from dataclasses import replace
    ts = get_trading_settings()
    if not hasattr(ts, key):
        raise KeyError(f"Unknown setting: {key}")
    expected_type = type(getattr(ts, key))
    new_ts = replace(ts, **{key: expected_type(value)})
    save_trading_settings(new_ts)
    return new_ts


def reset_trading_settings() -> TradingSettings:
    """Reset all settings to defaults, save, and return."""
    ts = TradingSettings()
    save_trading_settings(ts)
    return ts
