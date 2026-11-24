"""
Pure-Python Technical Indicator Library for Quotex Binary Options Signal Bot.
Zero external numerical dependencies required; runs on Python 3.10+ stdlib.
"""

from __future__ import annotations
import math
from typing import List, Tuple, Optional, Dict, Any


def sma(values: List[float], period: int) -> List[Optional[float]]:
    if period <= 0:
        return [None] * len(values)
    out: List[Optional[float]] = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: List[float], period: int) -> List[Optional[float]]:
    if period <= 0 or len(values) < period:
        return [None] * len(values)
    out: List[Optional[float]] = [None] * len(values)
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n <= period:
        return out

    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period + 1, n):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def bollinger_bands(
    closes: List[float], period: int = 20, std_dev: float = 2.0
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    n = len(closes)
    upper: List[Optional[float]] = [None] * n
    middle = sma(closes, period)
    lower: List[Optional[float]] = [None] * n

    for i in range(period - 1, n):
        mid = middle[i]
        if mid is None:
            continue
        window = closes[i - period + 1 : i + 1]
        variance = sum((x - mid) ** 2 for x in window) / period
        sd = math.sqrt(variance)
        upper[i] = mid + std_dev * sd
        lower[i] = mid - std_dev * sd
    return upper, middle, lower


def macd(
    closes: List[float], fast: int = 12, slow: int = 26, signal_period: int = 9
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    n = len(closes)
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line: List[Optional[float]] = [None] * n
    valid_macd: List[float] = []
    valid_indices: List[int] = []

    for i in range(n):
        f = ema_fast[i]
        s = ema_slow[i]
        if f is not None and s is not None:
            val = f - s
            macd_line[i] = val
            valid_macd.append(val)
            valid_indices.append(i)

    signal_line: List[Optional[float]] = [None] * n
    hist: List[Optional[float]] = [None] * n

    if len(valid_macd) >= signal_period:
        sig_vals = ema(valid_macd, signal_period)
        for idx, sv in zip(valid_indices, sig_vals):
            signal_line[idx] = sv
            if sv is not None and macd_line[idx] is not None:
                hist[idx] = macd_line[idx] - sv  # type: ignore[operator]

    return macd_line, signal_line, hist


def stochastic(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    k_period: int = 14,
    k_smooth: int = 3,
    d_period: int = 3,
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    n = len(closes)
    raw_k: List[Optional[float]] = [None] * n
    for i in range(k_period - 1, n):
        hh = max(highs[i - k_period + 1 : i + 1])
        ll = min(lows[i - k_period + 1 : i + 1])
        denom = hh - ll
        raw_k[i] = 50.0 if denom == 0 else ((closes[i] - ll) / denom) * 100.0

    k_vals = [x for x in raw_k if x is not None]
    k_indices = [i for i, x in enumerate(raw_k) if x is not None]
    smooth_k_series = sma(k_vals, k_smooth)

    k_out: List[Optional[float]] = [None] * n
    for idx, val in zip(k_indices, smooth_k_series):
        k_out[idx] = val

    valid_k = [x for x in k_out if x is not None]
    valid_k_idx = [i for i, x in enumerate(k_out) if x is not None]
    d_series = sma(valid_k, d_period)

    d_out: List[Optional[float]] = [None] * n
    for idx, val in zip(valid_k_idx, d_series):
        d_out[idx] = val

    return k_out, d_out


def atr(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> List[Optional[float]]:
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n < period + 1:
        return out

    trs: List[float] = [highs[0] - lows[0]]
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    first_atr = sum(trs[1 : period + 1]) / period
    out[period] = first_atr
    prev = first_atr
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def adx(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    n = len(closes)
    adx_out: List[Optional[float]] = [None] * n
    pdi_out: List[Optional[float]] = [None] * n
    mdi_out: List[Optional[float]] = [None] * n
    if n < period * 2 + 1:
        return adx_out, pdi_out, mdi_out

    tr_list = [0.0] * n
    pdm_list = [0.0] * n
    mdm_list = [0.0] * n

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        pdm_list[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        mdm_list[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr_list[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    tr_smooth = sum(tr_list[1 : period + 1])
    pdm_smooth = sum(pdm_list[1 : period + 1])
    mdm_smooth = sum(mdm_list[1 : period + 1])

    dx_vals: List[float] = []
    dx_indices: List[int] = []

    for i in range(period, n):
        if i > period:
            tr_smooth = tr_smooth - (tr_smooth / period) + tr_list[i]
            pdm_smooth = pdm_smooth - (pdm_smooth / period) + pdm_list[i]
            mdm_smooth = mdm_smooth - (mdm_smooth / period) + mdm_list[i]

        pdi = 100.0 * (pdm_smooth / tr_smooth) if tr_smooth > 0 else 0.0
        mdi = 100.0 * (mdm_smooth / tr_smooth) if tr_smooth > 0 else 0.0
        pdi_out[i] = pdi
        mdi_out[i] = mdi
        denom = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0
        dx_vals.append(dx)
        dx_indices.append(i)

    if len(dx_vals) >= period:
        seed_adx = sum(dx_vals[:period]) / period
        adx_out[dx_indices[period - 1]] = seed_adx
        prev_adx = seed_adx
        for k in range(period, len(dx_vals)):
            prev_adx = (prev_adx * (period - 1) + dx_vals[k]) / period
            adx_out[dx_indices[k]] = prev_adx

    return adx_out, pdi_out, mdi_out


def parabolic_sar(
    highs: List[float], lows: List[float], step: float = 0.02, max_step: float = 0.2
) -> Tuple[List[float], List[int]]:
    n = len(highs)
    if n == 0:
        return [], []
    sar = [0.0] * n
    trend = [1] * n  # 1 = uptrend, -1 = downtrend

    uptrend = True if n < 2 or highs[1] >= highs[0] else False
    af = step
    ep = highs[0] if uptrend else lows[0]
    sar[0] = lows[0] if uptrend else highs[0]
    trend[0] = 1 if uptrend else -1

    for i in range(1, n):
        prev_sar = sar[i - 1]
        next_sar = prev_sar + af * (ep - prev_sar)

        if uptrend:
            if i >= 2:
                next_sar = min(next_sar, lows[i - 1], lows[i - 2])
            else:
                next_sar = min(next_sar, lows[i - 1])

            if lows[i] < next_sar:
                uptrend = False
                next_sar = ep
                ep = lows[i]
                af = step
            else:
                if highs[i] > ep:
                    ep = highs[i]
                    af = min(af + step, max_step)
        else:
            if i >= 2:
                next_sar = max(next_sar, highs[i - 1], highs[i - 2])
            else:
                next_sar = max(next_sar, highs[i - 1])

            if highs[i] > next_sar:
                uptrend = True
                next_sar = ep
                ep = highs[i]
                af = step
            else:
                if lows[i] < ep:
                    ep = lows[i]
                    af = min(af + step, max_step)

        sar[i] = next_sar
        trend[i] = 1 if uptrend else -1

    return sar, trend


def supertrend(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 10,
    multiplier: float = 3.0,
) -> Tuple[List[Optional[float]], List[int]]:
    n = len(closes)
    atr_vals = atr(highs, lows, closes, period)
    line: List[Optional[float]] = [None] * n
    direction: List[int] = [0] * n

    final_upper = [0.0] * n
    final_lower = [0.0] * n

    for i in range(n):
        a = atr_vals[i]
        if a is None:
            continue
        hl2 = (highs[i] + lows[i]) / 2.0
        basic_upper = hl2 + multiplier * a
        basic_lower = hl2 - multiplier * a

        if i == 0 or atr_vals[i - 1] is None:
            final_upper[i] = basic_upper
            final_lower[i] = basic_lower
            direction[i] = 1 if closes[i] >= basic_lower else -1
            line[i] = final_lower[i] if direction[i] == 1 else final_upper[i]
            continue

        prev_fu = final_upper[i - 1]
        prev_fl = final_lower[i - 1]

        final_upper[i] = (
            basic_upper if (basic_upper < prev_fu or closes[i - 1] > prev_fu) else prev_fu
        )
        final_lower[i] = (
            basic_lower if (basic_lower > prev_fl or closes[i - 1] < prev_fl) else prev_fl
        )

        prev_dir = direction[i - 1]
        if prev_dir == 1:
            if closes[i] < final_lower[i]:
                direction[i] = -1
                line[i] = final_upper[i]
            else:
                direction[i] = 1
                line[i] = final_lower[i]
        else:
            if closes[i] > final_upper[i]:
                direction[i] = 1
                line[i] = final_lower[i]
            else:
                direction[i] = -1
                line[i] = final_upper[i]

    return line, direction


def cci(
    highs: List[float], lows: List[float], closes: List[float], period: int = 20
) -> List[Optional[float]]:
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    tp = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]
    tp_sma = sma(tp, period)

    for i in range(period - 1, n):
        m = tp_sma[i]
        if m is None:
            continue
        window = tp[i - period + 1 : i + 1]
        mean_dev = sum(abs(x - m) for x in window) / period
        if mean_dev == 0:
            out[i] = 0.0
        else:
            out[i] = (tp[i] - m) / (0.015 * mean_dev)
    return out


def williams_r(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> List[Optional[float]]:
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        hh = max(highs[i - period + 1 : i + 1])
        ll = min(lows[i - period + 1 : i + 1])
        denom = hh - ll
        out[i] = -50.0 if denom == 0 else -100.0 * ((hh - closes[i]) / denom)
    return out


def ichimoku(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
) -> Dict[str, List[Optional[float]]]:
    n = len(closes)

    def midpoint(period: int) -> List[Optional[float]]:
        res: List[Optional[float]] = [None] * n
        for i in range(period - 1, n):
            hh = max(highs[i - period + 1 : i + 1])
            ll = min(lows[i - period + 1 : i + 1])
            res[i] = (hh + ll) / 2.0
        return res

    tenkan = midpoint(tenkan_period)
    kijun = midpoint(kijun_period)
    senkou_a: List[Optional[float]] = [None] * n
    for i in range(n):
        if tenkan[i] is not None and kijun[i] is not None:
            senkou_a[i] = (tenkan[i] + kijun[i]) / 2.0  # type: ignore[operator]
    senkou_b = midpoint(senkou_b_period)

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
    }


def donchian_channel(
    highs: List[float], lows: List[float], period: int = 20
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    n = len(highs)
    upper: List[Optional[float]] = [None] * n
    lower: List[Optional[float]] = [None] * n
    mid: List[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        hh = max(highs[i - period + 1 : i + 1])
        ll = min(lows[i - period + 1 : i + 1])
        upper[i] = hh
        lower[i] = ll
        mid[i] = (hh + ll) / 2.0
    return upper, mid, lower


def floor_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    p = (high + low + close) / 3.0
    r1 = 2.0 * p - low
    s1 = 2.0 * p - high
    r2 = p + (high - low)
    s2 = p - (high - low)
    r3 = high + 2.0 * (p - low)
    s3 = low - 2.0 * (high - p)
    return {"P": p, "R1": r1, "R2": r2, "R3": r3, "S1": s1, "S2": s2, "S3": s3}


def swing_levels(
    highs: List[float], lows: List[float], lookback: int = 60, wing: int = 3
) -> Tuple[List[float], List[float]]:
    n = len(highs)
    start = max(wing, n - lookback)
    end = n - wing
    resistances: List[float] = []
    supports: List[float] = []
    for i in range(start, end):
        h = highs[i]
        l = lows[i]
        if all(h >= highs[j] for j in range(i - wing, i + wing + 1) if j != i):
            resistances.append(h)
        if all(l <= lows[j] for j in range(i - wing, i + wing + 1) if j != i):
            supports.append(l)
    return supports[-4:], resistances[-4:]


def bollinger_bandwidth(
    upper: List[Optional[float]],
    lower: List[Optional[float]],
    middle: List[Optional[float]],
) -> List[Optional[float]]:
    """Bollinger Bandwidth = (Upper - Lower) / Middle. Measures band squeeze vs expansion."""
    n = len(middle)
    out: List[Optional[float]] = [None] * n
    for i in range(n):
        u, l, m = upper[i], lower[i], middle[i]
        if u is not None and l is not None and m is not None and m > 0:
            out[i] = (u - l) / m
    return out


def candlestick_quality(
    open_val: float,
    high_val: float,
    low_val: float,
    close_val: float,
    atr_val: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Evaluates candlestick structure for binary options execution.
    Detects indecisive Dojis and exhaustion rejection wicks.
    """
    total_range = max(0.0, high_val - low_val)
    body = abs(close_val - open_val)
    upper_wick = high_val - max(open_val, close_val)
    lower_wick = min(open_val, close_val) - low_val

    body_ratio = (body / total_range) if total_range > 0 else 0.0
    upper_wick_ratio = (upper_wick / total_range) if total_range > 0 else 0.0
    lower_wick_ratio = (lower_wick / total_range) if total_range > 0 else 0.0

    is_bullish = close_val > open_val
    is_bearish = close_val < open_val

    # Doji definition: body less than 15% of range or less than 15% of recent ATR
    is_doji = (body_ratio < 0.15) or (atr_val is not None and atr_val > 0 and body < 0.15 * atr_val)

    # Exhaustion wicks: rejection wick exceeds 45% of total bar range
    exhaustion_bull = upper_wick_ratio > 0.45  # Buyers pushed up but got slammed back down
    exhaustion_bear = lower_wick_ratio > 0.45  # Sellers pushed down but got bought back up

    # Quality score from 0.0 to 1.0 (penalizing dojis and extreme opposing wicks)
    score = 1.0
    if is_doji:
        score -= 0.50
    if exhaustion_bull or exhaustion_bear:
        score -= 0.25
    if body_ratio >= 0.50:
        score = min(1.0, score + 0.15)

    return {
        "body_ratio": round(body_ratio, 4),
        "upper_wick_ratio": round(upper_wick_ratio, 4),
        "lower_wick_ratio": round(lower_wick_ratio, 4),
        "is_doji": is_doji,
        "is_bullish": is_bullish,
        "is_bearish": is_bearish,
        "exhaustion_bull": exhaustion_bull,
        "exhaustion_bear": exhaustion_bear,
        "quality_score": max(0.0, min(1.0, round(score, 2))),
    }


def higher_timeframe_trend(
    closes: List[float],
    highs: List[float],
    lows: List[float],
    aggregate_period: int = 5,
) -> Dict[str, Any]:
    """
    Synthesizes higher timeframe candles (e.g. 5m from 1m series)
    and computes macro directional trend and alignment.
    """
    n = len(closes)
    if n < aggregate_period * 3:
        return {
            "mtf_trend": "FLAT",
            "mtf_slope": 0.0,
            "mtf_bars_count": n // aggregate_period if aggregate_period > 0 else 0,
            "mtf_bias": "NEUTRAL",
        }

    # Aggregate into higher-timeframe bars
    agg_closes: List[float] = []
    agg_highs: List[float] = []
    agg_lows: List[float] = []

    for i in range(0, n - aggregate_period + 1, aggregate_period):
        chunk_c = closes[i : i + aggregate_period]
        chunk_h = highs[i : i + aggregate_period]
        chunk_l = lows[i : i + aggregate_period]
        agg_closes.append(chunk_c[-1])
        agg_highs.append(max(chunk_h))
        agg_lows.append(min(chunk_l))

    if len(agg_closes) < 3:
        return {
            "mtf_trend": "FLAT",
            "mtf_slope": 0.0,
            "mtf_bars_count": len(agg_closes),
            "mtf_bias": "NEUTRAL",
        }

    # Macro EMA on aggregated series
    p_ema = min(20, max(3, len(agg_closes) - 1))
    macro_ema = ema(agg_closes, p_ema)
    last_c = agg_closes[-1]
    last_ema = macro_ema[-1] or last_c
    prev_ema = macro_ema[-2] or last_ema

    slope = (last_ema - prev_ema) / (prev_ema if prev_ema > 0 else 1.0)

    if last_c > last_ema and slope >= 0:
        trend = "BULLISH"
        bias = "CALL"
    elif last_c < last_ema and slope <= 0:
        trend = "BEARISH"
        bias = "PUT"
    else:
        trend = "FLAT"
        bias = "NEUTRAL"

    return {
        "mtf_trend": trend,
        "mtf_slope": round(slope, 6),
        "mtf_bars_count": len(agg_closes),
        "mtf_bias": bias,
        "mtf_ema": round(last_ema, 5),
    }


def check_candle_entry_window(
    current_ts: int,
    candle_period_sec: int = 60,
    optimal_window_sec: int = 5,
) -> Dict[str, Any]:
    """
    Evaluates whether the current timestamp is within the optimal 00-second candle open window.
    Entering within the first 3-5s avoids mid-candle noise and expiry mismatch.
    """
    sec_in_candle = current_ts % candle_period_sec
    sec_to_close = candle_period_sec - sec_in_candle

    is_optimal_open = sec_in_candle <= optimal_window_sec
    is_approaching_close = sec_to_close <= 3

    if is_optimal_open:
        timing_status = "OPTIMAL_OPEN"
        actionable = True
    elif is_approaching_close:
        timing_status = "PRE_CLOSE_SETUP"
        actionable = True
    else:
        timing_status = "MID_CANDLE_NOISE"
        actionable = False

    return {
        "second_in_candle": sec_in_candle,
        "seconds_to_close": sec_to_close,
        "is_optimal_open": is_optimal_open,
        "is_approaching_close": is_approaching_close,
        "timing_status": timing_status,
        "actionable": actionable,
    }
