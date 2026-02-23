"""
Trade Rules Gate — deterministic checks before any live trade.

LLM can propose trades, but execution is blocked unless these rules pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple

from .trading_settings import get_trading_settings
from ..data.providers import get_market_provider
from ..data.providers.yahoo import YahooProvider


@dataclass
class RulesResult:
    allowed: bool
    reasons: List[str]
    details: Dict[str, str]


def evaluate_trade_rules(symbol: str, side: str) -> RulesResult:
    """Evaluate hard rules for a trade. Returns allow/block + reasons."""
    ts = get_trading_settings()
    if not getattr(ts, "rules_enabled", True):
        return RulesResult(True, [], {"rules": "disabled"})

    symbol = (symbol or "").upper()
    side = side.lower()

    reasons: List[str] = []
    details: Dict[str, str] = {}
    provider = get_market_provider()

    # --- Risk-off switch ---
    if getattr(ts, "rules_riskoff_enabled", True):
        risk_off, risk_detail, risk_missing = _risk_off_check(ts, provider)
        details["risk_off"] = risk_detail
        if risk_missing and getattr(ts, "rules_riskoff_strict", False):
            reasons.append("Risk-off check missing data (strict mode).")
        elif risk_off:
            # If VIX is extremely high, enforce short-only (block longs only)
            if "short-only" in risk_detail:
                if side == "long":
                    reasons.append("Risk-off regime active (VIX extreme, short-only).")
            else:
                mode = getattr(ts, "rules_riskoff_mode", "block_longs")
                if mode == "block_all":
                    reasons.append("Risk-off regime active (blocked).")
                else:
                    if side == "long":
                        reasons.append("Risk-off regime active (block longs).")

    # --- Market regime filter (benchmark alignment) ---
    if getattr(ts, "rules_regime_enabled", True):
        regime_symbol = getattr(ts, "rules_regime_symbol", "SPY") or "SPY"
        r_tfs = getattr(ts, "rules_regime_timeframes", ["1d"])
        r_results: List[Tuple[str, str, float | None]] = []
        missing = False
        for tf in r_tfs:
            candles = _get_candles(provider, regime_symbol, tf)
            if not candles or len(candles) < 2:
                missing = True
                r_results.append((tf, "missing", None))
                continue
            pct = _pct_change(candles[0]["c"], candles[-1]["c"])
            direction = _direction(pct, getattr(ts, "rules_regime_min_move_pct", 0.2))
            r_results.append((tf, direction, pct))

        if missing and not getattr(ts, "rules_allow_if_data_missing", False):
            reasons.append(f"Missing regime data for {regime_symbol}.")

        usable = [t for t in r_results if t[1] not in ("missing", "flat")]
        if not usable:
            reasons.append("Regime filter: no clear trend in benchmark.")
        else:
            if side == "long" and any(d == "down" for _, d, _ in usable):
                reasons.append("Regime filter: benchmark trend is down.")
            if side == "short" and any(d == "up" for _, d, _ in usable):
                reasons.append("Regime filter: benchmark trend is up.")

        details["regime"] = ", ".join(
            f"{tf}:{d}{'' if pct is None else f'({pct:+.2f}%)'}"
            for tf, d, pct in r_results
        )

    # --- Trend alignment ---
    if getattr(ts, "rules_require_trend_alignment", True):
        tfs = getattr(ts, "rules_trend_timeframes", ["1h", "4h", "1d"])
        tf_results: List[Tuple[str, str, float | None]] = []
        missing = False
        for tf in tfs:
            candles = _get_candles(provider, symbol, tf)
            if not candles or len(candles) < 2:
                missing = True
                tf_results.append((tf, "missing", None))
                continue
            pct = _pct_change(candles[0]["c"], candles[-1]["c"])
            direction = _direction(pct, getattr(ts, "rules_trend_min_move_pct", 0.2))
            tf_results.append((tf, direction, pct))

        if missing and not getattr(ts, "rules_allow_if_data_missing", False):
            reasons.append("Missing trend data for one or more timeframes.")

        # Evaluate alignment only if we have any usable signals
        usable = [t for t in tf_results if t[1] not in ("missing", "flat")]
        if not usable:
            reasons.append("No clear trend alignment (all flat or missing).")
        else:
            if side == "long":
                if any(d == "down" for _, d, _ in usable):
                    reasons.append("Trend misaligned: one or more timeframes are down.")
                if not any(d == "up" for _, d, _ in usable):
                    reasons.append("No upward trend signal across timeframes.")
            elif side == "short":
                if any(d == "up" for _, d, _ in usable):
                    reasons.append("Trend misaligned: one or more timeframes are up.")
                if not any(d == "down" for _, d, _ in usable):
                    reasons.append("No downward trend signal across timeframes.")

        details["trend"] = ", ".join(
            f"{tf}:{d}{'' if pct is None else f'({pct:+.2f}%)'}"
            for tf, d, pct in tf_results
        )

    # --- Volume confirmation ---
    if getattr(ts, "rules_require_volume_confirm", True):
        vol_multiple = _resolve_volume_multiple(ts, symbol)
        vol_ok, vol_detail = _volume_check(
            provider,
            symbol,
            lookback_days=getattr(ts, "rules_volume_lookback_days", 20),
            min_multiple=vol_multiple,
        )
        details["volume"] = vol_detail
        if not vol_ok:
            reasons.append("Volume confirmation failed.")

    # --- ATR / volatility filter ---
    if getattr(ts, "rules_atr_enabled", True):
        atr_ok, atr_detail = _atr_check(
            provider,
            symbol,
            timeframe=getattr(ts, "rules_atr_timeframe", "1d"),
            period=getattr(ts, "rules_atr_period", 14),
            min_pct=getattr(ts, "rules_atr_min_pct", 0.6),
            max_pct=getattr(ts, "rules_atr_max_pct", 6.0),
        )
        details["atr"] = atr_detail
        if not atr_ok:
            reasons.append("ATR filter failed.")

    # --- Alpha models gate (Lean-style signals) ---
    if getattr(ts, "rules_alpha_enabled", True):
        from .alpha_models import evaluate_alpha_models, aggregate_alpha
        models = getattr(ts, "rules_alpha_models", ["ema_cross", "rsi", "macd", "historical_returns"])
        params = getattr(ts, "rules_alpha_params", {})
        signals = evaluate_alpha_models(symbol, models, params)
        direction, conf, agg_detail = aggregate_alpha(signals)
        details["alpha"] = f"{direction} ({agg_detail})"
        details["alpha_models"] = ", ".join(
            f"{s.model}:{s.direction}({s.confidence:.2f})" for s in signals
        )
        # Require agreement with trade side unless neutral accepted
        require_agree = getattr(ts, "rules_alpha_require_agreement", True)
        if require_agree:
            if direction == "neutral":
                reasons.append("Alpha gate: no clear signal.")
            elif side != direction:
                reasons.append(f"Alpha gate: {direction} signal vs {side} trade.")

    allowed = len(reasons) == 0
    return RulesResult(allowed, reasons, details)


def _get_candles(provider, symbol: str, interval: str) -> list[dict]:
    """Fetch candles for a timeframe using a reasonable lookback window."""
    now = datetime.now(timezone.utc)
    days = _lookback_days(interval)
    start = now - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    try:
        return provider.get_candles(symbol, interval, start_ms, end_ms) or []
    except Exception:
        return []


def _lookback_days(interval: str) -> int:
    if interval == "1h":
        return 5
    if interval == "4h":
        return 20
    if interval == "1d":
        return 60
    return 10


def _pct_change(start: float, end: float) -> float:
    if start == 0:
        return 0.0
    return ((end - start) / start) * 100


def _direction(pct: float, min_move_pct: float) -> str:
    if pct >= min_move_pct:
        return "up"
    if pct <= -min_move_pct:
        return "down"
    return "flat"


def _volume_check(provider, symbol: str, lookback_days: int, min_multiple: float) -> tuple[bool, str]:
    """Check if latest daily volume >= avg * min_multiple."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(lookback_days + 5, 10))
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    try:
        candles = provider.get_candles(symbol, "1d", start_ms, end_ms) or []
    except Exception:
        candles = []

    if len(candles) < lookback_days + 1:
        return False, "insufficient daily volume data"

    recent = candles[-(lookback_days + 1):]
    latest = recent[-1]["v"]
    prev = [c["v"] for c in recent[:-1] if c.get("v") is not None]
    if not prev:
        return False, "insufficient daily volume data"

    avg = sum(prev) / len(prev)
    if avg <= 0:
        return False, "invalid avg volume"

    multiple = latest / avg
    ok = multiple >= min_multiple
    return ok, f"latest/avg={multiple:.2f}x (min {min_multiple:.2f}x)"


def _resolve_volume_multiple(ts, symbol: str) -> float:
    """Resolve volume multiple using symbol/sector/market-cap overrides."""
    base = float(getattr(ts, "rules_volume_min_multiple", 1.1))
    sym = symbol.upper()

    symbol_over = getattr(ts, "rules_volume_symbol_overrides", {}) or {}
    if sym in symbol_over:
        return float(symbol_over[sym])

    sector_map = getattr(ts, "rules_sector_map", {}) or {}
    sector_over = getattr(ts, "rules_volume_sector_overrides", {}) or {}
    sector = sector_map.get(sym)
    if sector and sector in sector_over:
        return float(sector_over[sector])

    cap_map = getattr(ts, "rules_marketcap_map", {}) or {}
    cap_over = getattr(ts, "rules_volume_marketcap_overrides", {}) or {}
    cap = cap_map.get(sym)
    if cap and cap in cap_over:
        return float(cap_over[cap])

    return base


def _atr_check(
    provider,
    symbol: str,
    timeframe: str,
    period: int,
    min_pct: float,
    max_pct: float,
) -> tuple[bool, str]:
    """ATR% check using the given timeframe/period."""
    candles = _get_candles(provider, symbol, timeframe)
    if not candles or len(candles) < period + 1:
        return False, "insufficient candles for ATR"

    tr_vals = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        high = c["h"]
        low = c["l"]
        prev_close = p["c"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_vals.append(tr)

    if len(tr_vals) < period:
        return False, "insufficient TR samples"

    atr = sum(tr_vals[-period:]) / period
    close = candles[-1]["c"]
    if close <= 0:
        return False, "invalid close for ATR"

    atr_pct = (atr / close) * 100
    ok = min_pct <= atr_pct <= max_pct
    return ok, f"ATR%={atr_pct:.2f} (min {min_pct:.2f}, max {max_pct:.2f})"


def _risk_off_check(ts, provider) -> tuple[bool, str, bool]:
    """Return (risk_off, detail, missing_data)."""
    missing = False
    triggers = []

    vix_symbol = getattr(ts, "rules_riskoff_vix_symbol", "^VIX")
    vix_threshold = getattr(ts, "rules_riskoff_vix_threshold", 22.0)
    vix_short_only = getattr(ts, "rules_riskoff_vix_short_only_threshold", 30.0)
    vix_px = None
    if vix_symbol:
        vix_px = _get_price_any(provider, vix_symbol)
        if vix_px is None:
            missing = True
        elif vix_px >= vix_threshold:
            triggers.append(f"VIX {vix_px:.2f} >= {vix_threshold:.2f}")
        if vix_px is not None and vix_px >= vix_short_only:
            triggers.append(f"VIX {vix_px:.2f} >= {vix_short_only:.2f} (short-only)")

    bench_symbol = getattr(ts, "rules_riskoff_benchmark_symbol", "SPY")
    # Trend down check
    tfs = getattr(ts, "rules_riskoff_trend_timeframes", ["1d"])
    trend_hits = []
    for tf in tfs:
        candles = _get_candles(provider, bench_symbol, tf)
        if not candles or len(candles) < 2:
            missing = True
            continue
        pct = _pct_change(candles[0]["c"], candles[-1]["c"])
        if pct <= -abs(getattr(ts, "rules_riskoff_trend_min_move_pct", 0.2)):
            trend_hits.append(f"{tf} {pct:+.2f}%")
    if trend_hits:
        triggers.append(f"Benchmark trend down ({', '.join(trend_hits)})")

    # Drawdown check
    lookback = getattr(ts, "rules_riskoff_drawdown_lookback_days", 5)
    dd_pct = getattr(ts, "rules_riskoff_drawdown_pct", 2.5)
    dd = _drawdown_pct(provider, bench_symbol, lookback_days=lookback)
    if dd is None:
        missing = True
    elif dd >= dd_pct:
        triggers.append(f"{bench_symbol} drawdown {dd:.2f}% >= {dd_pct:.2f}%")

    risk_off = len(triggers) > 0
    detail = "; ".join(triggers) if triggers else "no triggers"
    return risk_off, detail, missing


def _drawdown_pct(provider, symbol: str, lookback_days: int) -> float | None:
    candles = _get_candles(provider, symbol, "1d")
    if not candles:
        return None
    recent = candles[-(lookback_days + 1):] if len(candles) > lookback_days else candles
    if len(recent) < 2:
        return None
    closes = [c["c"] for c in recent if c.get("c") is not None]
    if not closes:
        return None
    peak = max(closes)
    last = closes[-1]
    if peak <= 0:
        return None
    return ((peak - last) / peak) * 100


def _get_price_any(provider, symbol: str) -> float | None:
    """Try primary provider first; fallback to Yahoo for index symbols."""
    try:
        px = provider.get_price(symbol)
        if px:
            return float(px)
    except Exception:
        pass
    try:
        return YahooProvider().get_price(symbol)
    except Exception:
        return None
