"""
Lean-Style Alpha Models — deterministic signal generators.

Implements a subset of QuantConnect Lean supported alpha models
with reasonable defaults for equities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from ..data.providers import get_market_provider


@dataclass
class AlphaSignal:
    model: str
    direction: str  # "long", "short", "neutral"
    confidence: float
    detail: str


def evaluate_alpha_models(
    symbol: str,
    models: List[str],
    params: Dict | None = None,
) -> List[AlphaSignal]:
    """Evaluate requested alpha models for a symbol."""
    params = params or {}
    provider = get_market_provider()
    sym = symbol.upper()
    out: List[AlphaSignal] = []

    for m in models:
        name = m.lower()
        if name in ("null", "nullalpha", "nullalphamodel"):
            out.append(AlphaSignal("null", "neutral", 0.0, "No signal"))
        elif name in ("constant", "constantalphamodellong"):
            out.append(AlphaSignal("constant", "long", 0.5, "Constant long"))
        elif name in ("constant_short", "constant_shortalpha"):
            out.append(AlphaSignal("constant_short", "short", 0.5, "Constant short"))
        elif name in ("historical_returns", "historicalreturnsalphamodel"):
            lookback = int(params.get("historical_returns_lookback", 20))
            out.append(_historical_returns(provider, sym, lookback))
        elif name in ("momentum", "momentumalphamodel"):
            lookback = int(params.get("momentum_lookback", 20))
            min_pct = float(params.get("momentum_min_pct", 2.0))
            out.append(_momentum(provider, sym, lookback, min_pct))
        elif name in ("mean_reversion", "meanreversionalphamodel"):
            lookback = int(params.get("mean_reversion_lookback", 20))
            z = float(params.get("mean_reversion_z", 1.5))
            out.append(_mean_reversion(provider, sym, lookback, z))
        elif name in ("ema_cross", "emacrossalphamodel"):
            fast = int(params.get("ema_fast", 12))
            slow = int(params.get("ema_slow", 26))
            out.append(_ema_cross(provider, sym, fast, slow))
        elif name in ("macd", "macdalphamodell"):
            fast = int(params.get("macd_fast", 12))
            slow = int(params.get("macd_slow", 26))
            signal = int(params.get("macd_signal", 9))
            out.append(_macd(provider, sym, fast, slow, signal))
        elif name in ("rsi", "rsialphamodell"):
            period = int(params.get("rsi_period", 14))
            overbought = float(params.get("rsi_overbought", 70))
            oversold = float(params.get("rsi_oversold", 30))
            out.append(_rsi(provider, sym, period, overbought, oversold))
        elif name in ("pairs", "pairs_trading", "basepairstradingalphamodel", "pearsoncorrelationpairstradingalphamodel"):
            # Requires a pair symbol. Use params["pairs_map"][sym] if provided.
            pairs_map = params.get("pairs_map", {})
            pair = pairs_map.get(sym)
            if not pair:
                out.append(AlphaSignal("pairs", "neutral", 0.0, "Missing pair symbol"))
            else:
                lookback = int(params.get("pairs_lookback", 60))
                out.append(_pairs_mean_reversion(provider, sym, pair, lookback))
        else:
            out.append(AlphaSignal(name, "neutral", 0.0, "Unknown model"))

    return out


def aggregate_alpha(signals: List[AlphaSignal]) -> Tuple[str, float, str]:
    """Aggregate alpha signals into a single direction."""
    if not signals:
        return "neutral", 0.0, "no signals"
    score = 0.0
    for s in signals:
        if s.direction == "long":
            score += s.confidence
        elif s.direction == "short":
            score -= s.confidence
    if score > 0.1:
        return "long", min(1.0, abs(score) / len(signals)), f"score {score:.2f}"
    if score < -0.1:
        return "short", min(1.0, abs(score) / len(signals)), f"score {score:.2f}"
    return "neutral", 0.0, f"score {score:.2f}"


# ── Model Implementations ────────────────────────────────────────────────────

def _historical_returns(provider, symbol: str, lookback: int) -> AlphaSignal:
    candles = _get_candles(provider, symbol, "1d", lookback + 5)
    if len(candles) < lookback + 1:
        return AlphaSignal("historical_returns", "neutral", 0.0, "insufficient data")
    start = candles[-(lookback + 1)]["c"]
    end = candles[-1]["c"]
    pct = _pct_change(start, end)
    direction = "long" if pct > 0 else "short" if pct < 0 else "neutral"
    conf = min(1.0, abs(pct) / 10)
    return AlphaSignal("historical_returns", direction, conf, f"{lookback}d {pct:+.2f}%")


def _momentum(provider, symbol: str, lookback: int, min_pct: float) -> AlphaSignal:
    candles = _get_candles(provider, symbol, "1d", lookback + 5)
    if len(candles) < lookback + 1:
        return AlphaSignal("momentum", "neutral", 0.0, "insufficient data")
    start = candles[-(lookback + 1)]["c"]
    end = candles[-1]["c"]
    pct = _pct_change(start, end)
    if pct >= min_pct:
        conf = min(1.0, pct / (min_pct * 3))
        return AlphaSignal("momentum", "long", conf, f"{lookback}d {pct:+.2f}%")
    if pct <= -min_pct:
        conf = min(1.0, abs(pct) / (min_pct * 3))
        return AlphaSignal("momentum", "short", conf, f"{lookback}d {pct:+.2f}%")
    return AlphaSignal("momentum", "neutral", 0.0, f"{lookback}d {pct:+.2f}%")


def _mean_reversion(provider, symbol: str, lookback: int, z: float) -> AlphaSignal:
    candles = _get_candles(provider, symbol, "1d", lookback + 5)
    closes = [c["c"] for c in candles]
    if len(closes) < lookback:
        return AlphaSignal("mean_reversion", "neutral", 0.0, "insufficient data")
    window = closes[-lookback:]
    mean = sum(window) / len(window)
    sd = _stddev(window)
    if sd == 0:
        return AlphaSignal("mean_reversion", "neutral", 0.0, "flat")
    zscore = (window[-1] - mean) / sd
    if zscore >= z:
        return AlphaSignal("mean_reversion", "short", 0.6, f"z {zscore:.2f} >= {z:.2f}")
    if zscore <= -z:
        return AlphaSignal("mean_reversion", "long", 0.6, f"z {zscore:.2f} <= -{z:.2f}")
    return AlphaSignal("mean_reversion", "neutral", 0.0, f"z {zscore:.2f}")


def _ema_cross(provider, symbol: str, fast: int, slow: int) -> AlphaSignal:
    candles = _get_candles(provider, symbol, "1d", slow + 10)
    closes = [c["c"] for c in candles]
    if len(closes) < slow:
        return AlphaSignal("ema_cross", "neutral", 0.0, "insufficient data")
    fast_ema = _ema(closes, fast)
    slow_ema = _ema(closes, slow)
    if fast_ema is None or slow_ema is None:
        return AlphaSignal("ema_cross", "neutral", 0.0, "insufficient data")
    if fast_ema > slow_ema:
        return AlphaSignal("ema_cross", "long", 0.6, f"fast {fast_ema:.2f} > slow {slow_ema:.2f}")
    if fast_ema < slow_ema:
        return AlphaSignal("ema_cross", "short", 0.6, f"fast {fast_ema:.2f} < slow {slow_ema:.2f}")
    return AlphaSignal("ema_cross", "neutral", 0.0, "flat")


def _macd(provider, symbol: str, fast: int, slow: int, signal: int) -> AlphaSignal:
    candles = _get_candles(provider, symbol, "1d", slow + signal + 10)
    closes = [c["c"] for c in candles]
    if len(closes) < slow + signal:
        return AlphaSignal("macd", "neutral", 0.0, "insufficient data")
    macd_line = _ema(closes, fast) - _ema(closes, slow)
    # Approximate signal line with EMA of macd series using last N
    macd_series = _macd_series(closes, fast, slow, signal + 10)
    signal_line = _ema(macd_series, signal) if macd_series else None
    if signal_line is None:
        return AlphaSignal("macd", "neutral", 0.0, "insufficient data")
    if macd_line > signal_line:
        return AlphaSignal("macd", "long", 0.6, f"macd {macd_line:.3f} > signal {signal_line:.3f}")
    if macd_line < signal_line:
        return AlphaSignal("macd", "short", 0.6, f"macd {macd_line:.3f} < signal {signal_line:.3f}")
    return AlphaSignal("macd", "neutral", 0.0, "flat")


def _rsi(provider, symbol: str, period: int, overbought: float, oversold: float) -> AlphaSignal:
    candles = _get_candles(provider, symbol, "1d", period + 5)
    closes = [c["c"] for c in candles]
    if len(closes) < period + 1:
        return AlphaSignal("rsi", "neutral", 0.0, "insufficient data")
    rsi = _compute_rsi(closes, period)
    if rsi is None:
        return AlphaSignal("rsi", "neutral", 0.0, "insufficient data")
    if rsi <= oversold:
        return AlphaSignal("rsi", "long", 0.7, f"rsi {rsi:.1f} <= {oversold}")
    if rsi >= overbought:
        return AlphaSignal("rsi", "short", 0.7, f"rsi {rsi:.1f} >= {overbought}")
    return AlphaSignal("rsi", "neutral", 0.0, f"rsi {rsi:.1f}")


def _pairs_mean_reversion(provider, a: str, b: str, lookback: int) -> AlphaSignal:
    ca = _get_candles(provider, a, "1d", lookback + 5)
    cb = _get_candles(provider, b, "1d", lookback + 5)
    if len(ca) < lookback or len(cb) < lookback:
        return AlphaSignal("pairs", "neutral", 0.0, "insufficient data")
    ra = [c["c"] for c in ca[-lookback:]]
    rb = [c["c"] for c in cb[-lookback:]]
    if len(ra) != len(rb):
        return AlphaSignal("pairs", "neutral", 0.0, "mismatched data")
    spread = [ra[i] - rb[i] for i in range(len(ra))]
    mean = sum(spread) / len(spread)
    last = spread[-1]
    # Mean reversion: if spread above mean, short A; if below, long A
    if last > mean:
        return AlphaSignal("pairs", "short", 0.5, f"spread {last:.2f} > mean {mean:.2f}")
    if last < mean:
        return AlphaSignal("pairs", "long", 0.5, f"spread {last:.2f} < mean {mean:.2f}")
    return AlphaSignal("pairs", "neutral", 0.0, "flat")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_candles(provider, symbol: str, interval: str, lookback_days: int) -> list[dict]:
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=lookback_days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    try:
        return provider.get_candles(symbol, interval, start_ms, end_ms) or []
    except Exception:
        return []


def _ema(values: List[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _macd_series(values: List[float], fast: int, slow: int, n: int) -> List[float]:
    if len(values) < slow + n:
        return []
    macd_vals = []
    for i in range(slow, len(values)):
        fast_ema = _ema(values[:i], fast)
        slow_ema = _ema(values[:i], slow)
        if fast_ema is None or slow_ema is None:
            continue
        macd_vals.append(fast_ema - slow_ema)
    return macd_vals[-n:]


def _compute_rsi(values: List[float], period: int) -> float | None:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        d = values[i] - values[i - 1]
        if d >= 0:
            gains += d
        else:
            losses += abs(d)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - (100 / (1 + rs))


def _pct_change(start: float, end: float) -> float:
    if start == 0:
        return 0.0
    return ((end - start) / start) * 100


def _stddev(values: List[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var ** 0.5
