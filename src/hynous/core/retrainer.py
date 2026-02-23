"""
Weekly Retrainer — adjust alpha/rules thresholds based on recent performance.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from .trade_analytics import get_trade_stats
from .trading_settings import get_trading_settings, save_trading_settings


def run_weekly_retrain(now_ts: float | None = None) -> Optional[str]:
    """Run retrain if due. Returns summary string if updated."""
    ts = get_trading_settings()
    if not getattr(ts, "retrain_enabled", True):
        return None

    now = datetime.now(timezone.utc) if now_ts is None else datetime.fromtimestamp(now_ts, tz=timezone.utc)
    last_ts = getattr(ts, "retrain_last_ts", 0.0) or 0.0
    last = datetime.fromtimestamp(last_ts, tz=timezone.utc) if last_ts > 0 else None
    interval_days = int(getattr(ts, "retrain_interval_days", 7))

    if last and (now - last) < timedelta(days=interval_days):
        return None

    created_after = (now - timedelta(days=interval_days)).isoformat()
    stats = get_trade_stats(created_after=created_after)
    min_trades = int(getattr(ts, "retrain_min_trades", 10))
    if stats.total_trades < min_trades:
        return None

    # Performance thresholds
    win_rate = stats.win_rate / 100.0 if stats.total_trades else 0.0
    pf = stats.profit_factor

    # Decide adjustment direction
    tighten = win_rate < 0.45 or (pf != float("inf") and pf < 1.1)
    loosen = win_rate > 0.60 and (pf == float("inf") or pf > 1.4)

    # Apply adjustments with clamps
    if tighten:
        _apply_adjustments(ts, direction="tighten")
        action = "tighten"
    elif loosen:
        _apply_adjustments(ts, direction="loosen")
        action = "loosen"
    else:
        action = "hold"

    ts.retrain_last_ts = now.timestamp()
    save_trading_settings(ts)

    return (
        f"Retrain {action}: {stats.total_trades} trades, "
        f"win {stats.win_rate:.0f}%, PF {pf:.2f}"
    )


def _apply_adjustments(ts, direction: str) -> None:
    """Adjust thresholds in place."""
    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    if direction == "tighten":
        ts.rules_volume_min_multiple = clamp(ts.rules_volume_min_multiple + 0.05, 1.0, 2.0)
        ts.rules_trend_min_move_pct = clamp(ts.rules_trend_min_move_pct + 0.05, 0.1, 1.5)
        ts.rules_atr_min_pct = clamp(ts.rules_atr_min_pct + 0.1, 0.2, 4.0)
        ts.rules_atr_max_pct = clamp(ts.rules_atr_max_pct - 0.2, 2.0, 10.0)
        ts.rules_alpha_params["momentum_min_pct"] = clamp(ts.rules_alpha_params.get("momentum_min_pct", 2.0) + 0.5, 1.0, 6.0)
        ts.rules_alpha_params["mean_reversion_z"] = clamp(ts.rules_alpha_params.get("mean_reversion_z", 1.5) + 0.2, 0.8, 3.0)
        ts.rules_alpha_params["pairs_lookback"] = int(clamp(ts.rules_alpha_params.get("pairs_lookback", 60) + 10, 20, 200))
    elif direction == "loosen":
        ts.rules_volume_min_multiple = clamp(ts.rules_volume_min_multiple - 0.05, 1.0, 2.0)
        ts.rules_trend_min_move_pct = clamp(ts.rules_trend_min_move_pct - 0.05, 0.1, 1.5)
        ts.rules_atr_min_pct = clamp(ts.rules_atr_min_pct - 0.1, 0.2, 4.0)
        ts.rules_atr_max_pct = clamp(ts.rules_atr_max_pct + 0.2, 2.0, 10.0)
        ts.rules_alpha_params["momentum_min_pct"] = clamp(ts.rules_alpha_params.get("momentum_min_pct", 2.0) - 0.5, 1.0, 6.0)
        ts.rules_alpha_params["mean_reversion_z"] = clamp(ts.rules_alpha_params.get("mean_reversion_z", 1.5) - 0.2, 0.8, 3.0)
        ts.rules_alpha_params["pairs_lookback"] = int(clamp(ts.rules_alpha_params.get("pairs_lookback", 60) - 10, 20, 200))
