"""Lightweight indicator computations for candles.

Input: list of candle dicts with keys t,o,h,l,c,v.
Output: dict of arrays aligned to candles length.
"""

from __future__ import annotations

from typing import Iterable


def _sma(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def _ema(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        result[i] = values[i] * k + (result[i - 1] or 0.0) * (1 - k)
    return result


def _rsi(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period + 1:
        return result
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    result[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        result[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return result


def _macd(values: list[float], fast: int = 12, slow: int = 26, sig: int = 9):
    fe = _ema(values, fast)
    se = _ema(values, slow)
    ml: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if fe[i] is None or se[i] is None:
            ml[i] = None
        else:
            ml[i] = (fe[i] or 0.0) - (se[i] or 0.0)
    valid = [(i, v) for i, v in enumerate(ml) if v is not None]
    sl: list[float | None] = [None] * len(values)
    hl: list[float | None] = [None] * len(values)
    if len(valid) >= sig:
        ema_sig = _ema([v for _, v in valid], sig)
        for idx, (i, _) in enumerate(valid):
            if idx >= sig - 1:
                sl[i] = ema_sig[idx]
    for i in range(len(values)):
        if ml[i] is not None and sl[i] is not None:
            hl[i] = (ml[i] or 0.0) - (sl[i] or 0.0)
    return ml, sl, hl


def _bollinger(values: list[float], period: int = 20, k: float = 2.0):
    mid = _sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        w = values[i - period + 1 : i + 1]
        avg = mid[i] or 0.0
        std = (sum((x - avg) ** 2 for x in w) / period) ** 0.5
        upper[i] = avg + k * std
        lower[i] = avg - k * std
    return upper, mid, lower


def compute_indicators(candles: Iterable[dict]) -> dict:
    candles_list = list(candles)
    if not candles_list:
        return {}

    closes = [float(c["c"]) for c in candles_list]
    volumes = [float(c.get("v", 0) or 0) for c in candles_list]

    sma50 = _sma(closes, 50)
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    bb_upper, bb_mid, bb_lower = _bollinger(closes)
    rsi14 = _rsi(closes, 14)
    macd_l, macd_s, macd_h = _macd(closes)
    vol_sma = _sma(volumes, 20)

    return {
        "sma50": sma50,
        "ema12": ema12,
        "ema26": ema26,
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
        "rsi": rsi14,
        "macd": macd_l,
        "macd_signal": macd_s,
        "macd_hist": macd_h,
        "vol_sma": vol_sma,
    }
