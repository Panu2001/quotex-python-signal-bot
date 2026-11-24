"""
Complete Multi-Strategy Trading Engine for Quotex Binary Options.
Includes 11 technical & price-action strategies + weighted confluence aggregator.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
import indicators as ind


@dataclass
class StrategyVote:
    id: str
    name: str
    category: str
    signal: str  # "CALL" | "PUT" | "NEUTRAL"
    strength: float  # 0.0 to 1.0
    weight: float
    reason: str
    metrics: Dict[str, Any]


STRATEGY_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "rsi_bollinger",
        "name": "RSI + Bollinger Bands Reversal",
        "category": "Mean Reversion",
        "default_weight": 1.35,
        "recommended_expiry": "1m - 2m",
        "description": "Triggers CALL when price pierces lower Bollinger Band(20,2) with RSI(14) oversold (<30) and reverses; PUT on upper band exhaustion with RSI >70.",
    },
    {
        "id": "ema_ribbon",
        "name": "EMA 9/21 Cross + EMA 200 Filter",
        "category": "Trend Following",
        "default_weight": 1.25,
        "recommended_expiry": "3m - 5m",
        "description": "Captures fast EMA(9) crossing EMA(21) strictly in alignment with the macro EMA(200) slope and price position.",
    },
    {
        "id": "macd_momentum",
        "name": "MACD (12,26,9) Momentum Cross",
        "category": "Momentum",
        "default_weight": 1.15,
        "recommended_expiry": "2m - 3m",
        "description": "Detects MACD line crossing the Signal line supported by expanding histogram bars and zero-line momentum.",
    },
    {
        "id": "stochastic_reversal",
        "name": "Stochastic (14,3,3) Extreme Cross",
        "category": "Oscillator",
        "default_weight": 1.10,
        "recommended_expiry": "1m - 2m",
        "description": "Identifies %K crossing %D upward from oversold (<22) or downward from overbought (>78) exhaustion zones.",
    },
    {
        "id": "psar_adx",
        "name": "Parabolic SAR + ADX(14) Strength",
        "category": "Trend Strength",
        "default_weight": 1.20,
        "recommended_expiry": "2m - 5m",
        "description": "Confirms Parabolic SAR dot flips only when ADX > 22 indicates directional strength and +DI/-DI separation agrees.",
    },
    {
        "id": "supertrend",
        "name": "SuperTrend ATR(10, 3.0)",
        "category": "Volatility Trend",
        "default_weight": 1.30,
        "recommended_expiry": "3m - 5m",
        "description": "Volatility-adjusted trailing trend engine signaling on fresh SuperTrend regime flips or pullbacks toward the active band.",
    },
    {
        "id": "candlestick_action",
        "name": "Candlestick Price Action",
        "category": "Price Action",
        "default_weight": 1.25,
        "recommended_expiry": "1m - 2m",
        "description": "Recognizes Bullish/Bearish Engulfing, Pin Bars (Hammer/Shooting Star), Morning/Evening Stars, and Three Soldiers/Crows.",
    },
    {
        "id": "sr_pivots",
        "name": "Support/Resistance & Floor Pivots",
        "category": "Market Structure",
        "default_weight": 1.20,
        "recommended_expiry": "2m - 3m",
        "description": "Detects wick rejections off recent swing Support/Resistance zones and classical Floor Pivot levels (S1/S2/R1/R2).",
    },
    {
        "id": "cci_williams",
        "name": "CCI(20) + Williams %R(14) Dual",
        "category": "Oscillator",
        "default_weight": 1.00,
        "recommended_expiry": "1m - 2m",
        "description": "Dual-oscillator confirmation requiring CCI extreme (<-100 or >+100) paired with Williams %R reversal (<-80 or >-20).",
    },
    {
        "id": "ichimoku_cloud",
        "name": "Ichimoku Kinko Hyo Cloud",
        "category": "Macro Trend",
        "default_weight": 1.15,
        "recommended_expiry": "3m - 5m",
        "description": "Evaluates Tenkan-sen / Kijun-sen cross relative to the Kumo Cloud (Senkou Span A/B) for high-conviction directional moves.",
    },
    {
        "id": "donchian_squeeze",
        "name": "Donchian(20) Volatility Breakout",
        "category": "Breakout",
        "default_weight": 1.05,
        "recommended_expiry": "2m - 3m",
        "description": "Spots clean breakouts above/below the 20-bar Donchian channel coupled with Bollinger bandwidth expansion.",
    },
    {
        "id": "pyquotex_triple_confirmation",
        "name": "PyQuotex Triple Confirmation",
        "category": "Multi-Layer Confluence",
        "default_weight": 1.40,
        "recommended_expiry": "1m (60s)",
        "description": "PyQuotex native strategy: combines EMA(20/50) trend alignment, RSI(7) directional bias, and Stochastic(14,3,3) extreme crossover.",
    },
    {
        "id": "adaptive_24h_trend_pattern",
        "name": "24-Hour Adaptive Trend & Pattern Engine",
        "category": "Adaptive Trend Learning",
        "default_weight": 1.45,
        "recommended_expiry": "1m - 3m",
        "description": "Analyzes multi-hour macro wave structures, 24-hour equilibrium bounds, and directional impulse/pullback continuations with adaptive regression slope.",
    },
]


def _extract_series(candles: List[Dict[str, Any]]):
    opens = [float(c["open"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    volumes = [float(c.get("volume", 1.0)) for c in candles]
    return opens, highs, lows, closes, volumes


def _format_expiry(timeframe: str, volatility_regime: str, confidence: float) -> str:
    tf_map = {
        "5s": ("15s", "30s", "1m"),
        "10s": ("30s", "1m", "1m"),
        "15s": ("30s", "1m", "2m"),
        "30s": ("1m", "2m", "3m"),
        "1m": ("1m", "2m", "5m"),
        "2m": ("2m", "4m", "6m"),
        "3m": ("3m", "6m", "9m"),
        "5m": ("5m", "10m", "15m"),
        "10m": ("10m", "20m", "30m"),
        "15m": ("15m", "30m", "45m"),
        "30m": ("30m", "1h", "2h"),
        "1h": ("1h", "2h", "4h"),
        "4h": ("4h", "8h", "1d"),
        "1d": ("1d", "2d", "3d"),
    }
    short_e, mid_e, long_e = tf_map.get(timeframe, ("1m", "2m", "5m"))
    if volatility_regime == "High":
        return short_e if confidence >= 75 else mid_e
    if volatility_regime == "Low":
        return long_e
    return short_e if confidence >= 78 else mid_e


def evaluate_all_strategies(
    candles: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    timeframe: str = "1m",
) -> Dict[str, Any]:
    cfg = config or {}
    enabled_map: Dict[str, bool] = cfg.get("enabled_strategies", {})
    weights_map: Dict[str, float] = cfg.get("strategy_weights", {})
    min_confidence: float = float(cfg.get("min_confidence", 62.0))
    tg_min_confidence: float = float(cfg.get("telegram_min_confidence", 70.0))
    rsi_oversold: float = float(cfg.get("rsi_oversold", 30.0))
    rsi_overbought: float = float(cfg.get("rsi_overbought", 70.0))
    bb_period: int = int(cfg.get("bb_period", 20))
    bb_std: float = float(cfg.get("bb_std", 2.0))
    ema_fast_p: int = int(cfg.get("ema_fast", 9))
    ema_slow_p: int = int(cfg.get("ema_slow", 21))
    adx_threshold: float = float(cfg.get("adx_threshold", 20.0))

    if len(candles) < 26:
        return {
            "signal": "NEUTRAL",
            "confidence": 0.0,
            "qualified": False,
            "qualification_reason": "Awaiting live Quotex candles (minimum 26 bars required).",
            "recommended_expiry": _format_expiry(timeframe, "Normal", 0.0),
            "volatility_regime": "Normal",
            "market_regime": "NORMAL",
            "bollinger_bandwidth": 0.0,
            "candlestick_quality": {},
            "mtf_confirmation": {},
            "entry_timing_guard": {},
            "atr_pct": 0.0,
            "price": float(candles[-1]["close"]) if candles else 0.0,
            "call_count": 0,
            "put_count": 0,
            "neutral_count": 0,
            "call_score": 0.0,
            "put_score": 0.0,
            "votes": [],
            "levels": {"supports": [], "resistances": []},
            "overlays": {
                "ema_fast": [],
                "ema_slow": [],
                "ema_200": [],
                "bb_upper": [],
                "bb_mid": [],
                "bb_lower": [],
                "supertrend": [],
                "supertrend_dir": [],
                "sar": [],
                "rsi": [],
                "macd_hist": [],
            },
            "error": "Need at least 26 live Quotex candles for full multi-strategy evaluation.",
        }

    opens, highs, lows, closes, _ = _extract_series(candles)
    last = len(closes) - 1
    prev = last - 1
    price = closes[last]

    # Compute full indicator arrays once (adapt long-window lengths cleanly when 26-59 bars)
    n_bars = len(closes)
    eff_bb = min(bb_period, max(10, n_bars - 2))
    eff_slow = min(ema_slow_p, max(12, n_bars - 3))
    eff_fast = min(ema_fast_p, max(5, eff_slow - 2))
    eff_ema200 = min(200, max(20, n_bars - 3))
    eff_don = min(20, max(10, n_bars - 2))
    eff_senkou = min(52, max(20, n_bars - 2))
    eff_kijun = min(26, max(14, n_bars - 2))

    rsi_vals = ind.rsi(closes, 14)
    bb_upper, bb_mid, bb_lower = ind.bollinger_bands(closes, eff_bb, bb_std)
    ema_fast = ind.ema(closes, eff_fast)
    ema_slow = ind.ema(closes, eff_slow)
    ema_200 = ind.ema(closes, eff_ema200)
    macd_line, sig_line, macd_hist = ind.macd(closes, 12, min(26, max(16, n_bars - 2)), 9)
    stoch_k, stoch_d = ind.stochastic(highs, lows, closes, 14, 3, 3)
    atr_vals = ind.atr(highs, lows, closes, 14)
    adx_vals, pdi_vals, mdi_vals = ind.adx(highs, lows, closes, 14)
    sar_vals, sar_trend = ind.parabolic_sar(highs, lows, 0.02, 0.2)
    st_line, st_dir = ind.supertrend(highs, lows, closes, 10, 3.0)
    cci_vals = ind.cci(highs, lows, closes, min(20, max(12, n_bars - 2)))
    wr_vals = ind.williams_r(highs, lows, closes, 14)
    ichi = ind.ichimoku(highs, lows, closes, 9, eff_kijun, eff_senkou)
    don_u, don_m, don_l = ind.donchian_channel(highs, lows, eff_don)
    supports, resistances = ind.swing_levels(highs, lows, lookback=min(60, n_bars), wing=min(3, max(2, n_bars // 12)))

    # Volatility regime from ATR%
    cur_atr = atr_vals[last] or (highs[last] - lows[last])
    atr_pct = (cur_atr / price) * 100.0 if price > 0 else 0.0
    avg_atr_list = [x for x in atr_vals[-30:] if x is not None]
    avg_atr = sum(avg_atr_list) / len(avg_atr_list) if avg_atr_list else cur_atr
    vol_ratio = cur_atr / avg_atr if avg_atr > 0 else 1.0
    volatility_regime = (
        "High" if vol_ratio > 1.35 else ("Low" if vol_ratio < 0.72 else "Normal")
    )

    # Bollinger Bandwidth and Regime Detection
    bbw_vals = ind.bollinger_bandwidth(bb_upper, bb_lower, bb_mid)
    cur_bbw = bbw_vals[last] or 0.0
    avg_bbw_list = [x for x in bbw_vals[-30:] if x is not None]
    avg_bbw = sum(avg_bbw_list) / len(avg_bbw_list) if avg_bbw_list else cur_bbw
    cur_adx = adx_vals[last] or 20.0

    # Market Regime Classification:
    # - RANGING: ADX < 20 or tight BBW squeeze (mean-reversion conditions)
    # - TRENDING: ADX >= 25 with directional expansion (trend-following conditions)
    # - VOLATILE: High ATR ratio > 1.35
    # - NORMAL: Standard baseline
    if vol_ratio > 1.35:
        market_regime = "VOLATILE"
    elif cur_adx >= 25.0:
        market_regime = "TRENDING"
    elif cur_adx < 20.0 or (avg_bbw > 0 and cur_bbw < avg_bbw * 0.70):
        market_regime = "RANGING"
    else:
        market_regime = "NORMAL"

    quarantined_strategies = set(cfg.get("quarantined_strategies", []))
    strategy_win_rates = cfg.get("strategy_win_rates", {})

    votes: List[StrategyVote] = []

    def get_w(sid: str, default_w: float) -> float:
        # Check dynamic quarantining (rolling win rate < 52%)
        if sid in quarantined_strategies or strategy_win_rates.get(sid, 1.0) < 0.52:
            return 0.0
        w = float(weights_map.get(sid, default_w))
        # Regime-based dynamic weighting:
        if market_regime == "RANGING":
            if sid in ("rsi_bollinger", "stochastic_reversal", "sr_pivots", "cci_williams"):
                w *= 1.25
            elif sid in ("donchian_squeeze", "ema_ribbon", "supertrend"):
                w *= 0.65
        elif market_regime == "TRENDING":
            if sid in ("adaptive_24h_trend_pattern", "pyquotex_triple_confirmation", "ema_ribbon", "supertrend", "macd_momentum", "psar_adx"):
                w *= 1.25
        return round(w, 3)

    def is_enabled(sid: str) -> bool:
        return enabled_map.get(sid, True)

    # 1. RSI + Bollinger Bands Reversal
    if is_enabled("rsi_bollinger"):
        r_now = rsi_vals[last] or 50.0
        r_prev = rsi_vals[prev] or 50.0
        up = bb_upper[last] or price
        lo = bb_lower[last] or price
        sig = "NEUTRAL"
        strength = 0.0
        reason = f"RSI(14)={r_now:.1f} inside BB channel"

        if (lows[last] <= lo or closes[prev] <= (bb_lower[prev] or lo)) and r_now <= rsi_oversold + 6:
            sig = "CALL"
            strength = min(1.0, 0.55 + max(0.0, (rsi_oversold + 5 - r_now) / 25.0))
            reason = f"Price touched lower BB ({lo:.5f}) with oversold RSI={r_now:.1f}"
        elif (highs[last] >= up or closes[prev] >= (bb_upper[prev] or up)) and r_now >= rsi_overbought - 6:
            sig = "PUT"
            strength = min(1.0, 0.55 + max(0.0, (r_now - (rsi_overbought - 5)) / 25.0))
            reason = f"Price rejected upper BB ({up:.5f}) with overbought RSI={r_now:.1f}"
        elif r_now < rsi_oversold and r_now > r_prev:
            sig = "CALL"
            strength = 0.65
            reason = f"RSI(14) turning up from oversold ({r_prev:.1f} -> {r_now:.1f})"
        elif r_now > rsi_overbought and r_now < r_prev:
            sig = "PUT"
            strength = 0.65
            reason = f"RSI(14) turning down from overbought ({r_prev:.1f} -> {r_now:.1f})"

        # Counter-trend suppression in strong trending regime
        if market_regime == "TRENDING":
            p_val = pdi_vals[last]
            m_val = mdi_vals[last]
            e200 = ema_200[last]
            e200_val = e200 if e200 is not None else price
            is_up = p_val is not None and m_val is not None and p_val > m_val and price > e200_val
            is_down = p_val is not None and m_val is not None and m_val > p_val and price < e200_val
            if sig == "PUT" and is_up:
                strength = round(strength * 0.50, 2)
                reason += " (Counter-trend fade suppressed in strong uptrend)"
            elif sig == "CALL" and is_down:
                strength = round(strength * 0.50, 2)
                reason += " (Counter-trend fade suppressed in strong downtrend)"

        votes.append(
            StrategyVote(
                id="rsi_bollinger",
                name="RSI + Bollinger Bands Reversal",
                category="Mean Reversion",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("rsi_bollinger", 1.35),
                reason=reason,
                metrics={"rsi": round(r_now, 2), "bb_upper": round(up, 5), "bb_lower": round(lo, 5)},
            )
        )

    # 2. EMA 9/21 + EMA 200 Trend Filter
    if is_enabled("ema_ribbon"):
        ef_now = ema_fast[last] or price
        ef_prev = ema_fast[prev] or price
        es_now = ema_slow[last] or price
        es_prev = ema_slow[prev] or price
        e200 = ema_200[last] or price
        sig = "NEUTRAL"
        strength = 0.0
        reason = "EMAs tangled without directional alignment"

        bull_cross = ef_prev <= es_prev and ef_now > es_now
        bear_cross = ef_prev >= es_prev and ef_now < es_now
        if bull_cross:
            sig = "CALL"
            strength = 0.95 if price > e200 else 0.68
            reason = f"Fresh EMA({ema_fast_p}/{ema_slow_p}) bullish cross" + (" above EMA200" if price > e200 else "")
        elif bear_cross:
            sig = "PUT"
            strength = 0.95 if price < e200 else 0.68
            reason = f"Fresh EMA({ema_fast_p}/{ema_slow_p}) bearish cross" + (" below EMA200" if price < e200 else "")
        elif ef_now > es_now and price > ef_now and price > e200:
            sig = "CALL"
            strength = 0.62
            reason = f"Bullish EMA stack (Price > EMA{ema_fast_p} > EMA{ema_slow_p} > EMA200)"
        elif ef_now < es_now and price < ef_now and price < e200:
            sig = "PUT"
            strength = 0.62
            reason = f"Bearish EMA stack (Price < EMA{ema_fast_p} < EMA{ema_slow_p} < EMA200)"

        votes.append(
            StrategyVote(
                id="ema_ribbon",
                name="EMA 9/21 Cross + EMA 200 Filter",
                category="Trend Following",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("ema_ribbon", 1.25),
                reason=reason,
                metrics={"ema_fast": round(ef_now, 5), "ema_slow": round(es_now, 5), "ema_200": round(e200, 5)},
            )
        )

    # 3. MACD Momentum Cross
    if is_enabled("macd_momentum"):
        ml_now = macd_line[last] or 0.0
        sl_now = sig_line[last] or 0.0
        h_now = macd_hist[last] or 0.0
        h_prev = macd_hist[prev] or 0.0
        sig = "NEUTRAL"
        strength = 0.0
        reason = "MACD histogram flat"

        if h_prev <= 0 and h_now > 0:
            sig = "CALL"
            strength = 0.90
            reason = "MACD crossed above signal line (bullish histogram flip)"
        elif h_prev >= 0 and h_now < 0:
            sig = "PUT"
            strength = 0.90
            reason = "MACD crossed below signal line (bearish histogram flip)"
        elif h_now > 0 and h_now > h_prev and ml_now > sl_now:
            sig = "CALL"
            strength = 0.60
            reason = "Positive MACD histogram expanding upward"
        elif h_now < 0 and h_now < h_prev and ml_now < sl_now:
            sig = "PUT"
            strength = 0.60
            reason = "Negative MACD histogram expanding downward"

        votes.append(
            StrategyVote(
                id="macd_momentum",
                name="MACD (12,26,9) Momentum Cross",
                category="Momentum",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("macd_momentum", 1.15),
                reason=reason,
                metrics={"macd": round(ml_now, 6), "signal": round(sl_now, 6), "hist": round(h_now, 6)},
            )
        )

    # 4. Stochastic (14,3,3) Extreme Cross
    if is_enabled("stochastic_reversal"):
        k_val = stoch_k[last]
        d_val = stoch_d[last]
        k_p_val = stoch_k[prev]
        d_p_val = stoch_d[prev]
        k_now: float = float(k_val) if k_val is not None else 50.0
        d_now: float = float(d_val) if d_val is not None else 50.0
        k_prev: float = float(k_p_val) if k_p_val is not None else 50.0
        d_prev: float = float(d_p_val) if d_p_val is not None else 50.0
        sig = "NEUTRAL"
        strength = 0.0
        reason = f"Stochastic %K={k_now:.1f} / %D={d_now:.1f} mid-range"

        if k_prev <= d_prev and k_now > d_now and k_now < 35:
            sig = "CALL"
            strength = 0.92 if k_now < 22 else 0.72
            reason = f"Stochastic %K ({k_now:.1f}) crossed above %D ({d_now:.1f}) in oversold zone"
        elif k_prev >= d_prev and k_now < d_now and k_now > 65:
            sig = "PUT"
            strength = 0.92 if k_now > 78 else 0.72
            reason = f"Stochastic %K ({k_now:.1f}) crossed below %D ({d_now:.1f}) in overbought zone"
        elif k_now < 18 and k_now > k_prev:
            sig = "CALL"
            strength = 0.58
            reason = f"Stochastic deep oversold curl up (%K={k_now:.1f})"
        elif k_now > 82 and k_now < k_prev:
            sig = "PUT"
            strength = 0.58
            reason = f"Stochastic deep overbought curl down (%K={k_now:.1f})"

        # Counter-trend suppression in strong trending regime
        if market_regime == "TRENDING":
            p_val = pdi_vals[last]
            m_val = mdi_vals[last]
            e200 = ema_200[last]
            e200_val = e200 if e200 is not None else price
            is_up = p_val is not None and m_val is not None and p_val > m_val and price > e200_val
            is_down = p_val is not None and m_val is not None and m_val > p_val and price < e200_val
            if sig == "PUT" and is_up:
                strength = round(strength * 0.50, 2)
                reason += " (Counter-trend fade suppressed in strong uptrend)"
            elif sig == "CALL" and is_down:
                strength = round(strength * 0.50, 2)
                reason += " (Counter-trend fade suppressed in strong downtrend)"

        votes.append(
            StrategyVote(
                id="stochastic_reversal",
                name="Stochastic (14,3,3) Extreme Cross",
                category="Oscillator",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("stochastic_reversal", 1.10),
                reason=reason,
                metrics={"k": round(k_now, 2), "d": round(d_now, 2)},
            )
        )

    # 5. Parabolic SAR + ADX
    if is_enabled("psar_adx"):
        adx_now = adx_vals[last] or 15.0
        pdi = pdi_vals[last] or 0.0
        mdi = mdi_vals[last] or 0.0
        t_now = sar_trend[last]
        t_prev = sar_trend[prev]
        sig = "NEUTRAL"
        strength = 0.0
        reason = f"ADX={adx_now:.1f} below trend threshold ({adx_threshold:.0f})"

        if adx_now >= adx_threshold:
            if t_now == 1 and (t_prev == -1 or pdi > mdi):
                sig = "CALL"
                strength = 0.92 if t_prev == -1 else 0.65
                reason = f"SAR bullish {'flip' if t_prev == -1 else 'trend'} confirmed by ADX={adx_now:.1f} (+DI>{mdi:.1f})"
            elif t_now == -1 and (t_prev == 1 or mdi > pdi):
                sig = "PUT"
                strength = 0.92 if t_prev == 1 else 0.65
                reason = f"SAR bearish {'flip' if t_prev == 1 else 'trend'} confirmed by ADX={adx_now:.1f} (-DI>{pdi:.1f})"

        votes.append(
            StrategyVote(
                id="psar_adx",
                name="Parabolic SAR + ADX(14) Strength",
                category="Trend Strength",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("psar_adx", 1.20),
                reason=reason,
                metrics={"sar": round(sar_vals[last], 5), "adx": round(adx_now, 2), "pdi": round(pdi, 1), "mdi": round(mdi, 1)},
            )
        )

    # 6. SuperTrend ATR(10, 3.0)
    if is_enabled("supertrend"):
        sd_now = st_dir[last]
        sd_prev = st_dir[prev]
        sl_val = st_line[last] or price
        sig = "NEUTRAL"
        strength = 0.0
        reason = "SuperTrend awaiting direction"

        if sd_now == 1 and sd_prev == -1:
            sig = "CALL"
            strength = 0.95
            reason = f"SuperTrend flipped BULLISH at {sl_val:.5f}"
        elif sd_now == -1 and sd_prev == 1:
            sig = "PUT"
            strength = 0.95
            reason = f"SuperTrend flipped BEARISH at {sl_val:.5f}"
        elif sd_now == 1 and closes[last] > opens[last]:
            sig = "CALL"
            strength = 0.58
            reason = f"Bullish SuperTrend active support ({sl_val:.5f})"
        elif sd_now == -1 and closes[last] < opens[last]:
            sig = "PUT"
            strength = 0.58
            reason = f"Bearish SuperTrend active resistance ({sl_val:.5f})"

        votes.append(
            StrategyVote(
                id="supertrend",
                name="SuperTrend ATR(10, 3.0)",
                category="Volatility Trend",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("supertrend", 1.30),
                reason=reason,
                metrics={"supertrend": round(sl_val, 5), "direction": "BULL" if sd_now == 1 else "BEAR"},
            )
        )

    # 7. Candlestick Price Action Patterns
    if is_enabled("candlestick_action"):
        o0, h0, l0, c0 = opens[last], highs[last], lows[last], closes[last]
        o1, h1, l1, c1 = opens[prev], highs[prev], lows[prev], closes[prev]
        o2, h2, l2, c2 = opens[last - 2], highs[last - 2], lows[last - 2], closes[last - 2]

        body0 = abs(c0 - o0)
        body1 = abs(c1 - o1)
        range0 = max(h0 - l0, 1e-9)
        upper_wick0 = h0 - max(o0, c0)
        lower_wick0 = min(o0, c0) - l0

        sig = "NEUTRAL"
        strength = 0.0
        pattern = "No reversal pattern"

        # Bullish Engulfing
        if c1 < o1 and c0 > o0 and c0 >= o1 and o0 <= c1 and body0 > body1:
            sig, strength, pattern = "CALL", 0.90, "Bullish Engulfing candle"
        # Bearish Engulfing
        elif c1 > o1 and c0 < o0 and o0 >= c1 and c0 <= o1 and body0 > body1:
            sig, strength, pattern = "PUT", 0.90, "Bearish Engulfing candle"
        # Hammer / Bullish Pin Bar
        elif lower_wick0 >= 2.0 * max(body0, range0 * 0.05) and upper_wick0 <= body0 * 0.6 and body0 / range0 < 0.35:
            sig, strength, pattern = "CALL", 0.80, "Bullish Hammer / Pin Bar rejection"
        # Shooting Star / Bearish Pin Bar
        elif upper_wick0 >= 2.0 * max(body0, range0 * 0.05) and lower_wick0 <= body0 * 0.6 and body0 / range0 < 0.35:
            sig, strength, pattern = "PUT", 0.80, "Bearish Shooting Star rejection"
        # Morning Star (3-candle)
        elif c2 < o2 and abs(c1 - o1) < abs(c2 - o2) * 0.45 and c0 > o0 and c0 > (o2 + c2) / 2:
            sig, strength, pattern = "CALL", 0.85, "Morning Star 3-bar reversal"
        # Evening Star (3-candle)
        elif c2 > o2 and abs(c1 - o1) < abs(c2 - o2) * 0.45 and c0 < o0 and c0 < (o2 + c2) / 2:
            sig, strength, pattern = "PUT", 0.85, "Evening Star 3-bar reversal"
        # Three White Soldiers / Black Crows
        elif c2 > o2 and c1 > o1 and c0 > o0 and c0 > c1 > c2:
            sig, strength, pattern = "CALL", 0.68, "Three White Soldiers momentum"
        elif c2 < o2 and c1 < o1 and c0 < o0 and c0 < c1 < c2:
            sig, strength, pattern = "PUT", 0.68, "Three Black Crows momentum"

        votes.append(
            StrategyVote(
                id="candlestick_action",
                name="Candlestick Price Action",
                category="Price Action",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("candlestick_action", 1.25),
                reason=pattern,
                metrics={"pattern": pattern, "body_ratio": round(body0 / range0, 2)},
            )
        )

    # 8. Support / Resistance & Floor Pivots
    if is_enabled("sr_pivots"):
        look_slice = candles[-25:-1]
        piv = ind.floor_pivots(
            max(x["high"] for x in look_slice),
            min(x["low"] for x in look_slice),
            look_slice[-1]["close"],
        )
        zone_tol = cur_atr * 0.45
        all_supports = supports + [piv["S1"], piv["S2"]]
        all_resistances = resistances + [piv["R1"], piv["R2"]]

        sig = "NEUTRAL"
        strength = 0.0
        reason = "Price mid-range between S/R & pivot zones"

        near_sup = [s for s in all_supports if abs(lows[last] - s) <= zone_tol or abs(price - s) <= zone_tol]
        near_res = [r for r in all_resistances if abs(highs[last] - r) <= zone_tol or abs(price - r) <= zone_tol]

        # Breakout Retest (Role Reversal: Prior Resistance tested as Support in Uptrend)
        role_reversal_call = False
        role_reversal_put = False
        reversal_level = 0.0

        for r in resistances:
            if any(h > r + zone_tol for h in highs[max(0, last - 12) : last - 2]) and lows[last] <= r + zone_tol and closes[last] >= r - zone_tol * 0.5:
                role_reversal_call = True
                reversal_level = r
                break

        for s in supports:
            if any(l < s - zone_tol for l in lows[max(0, last - 12) : last - 2]) and highs[last] >= s - zone_tol and closes[last] <= s + zone_tol * 0.5:
                role_reversal_put = True
                reversal_level = s
                break

        if role_reversal_call and closes[last] >= opens[last]:
            sig = "CALL"
            strength = 0.94
            reason = f"Breakout Retest (Role Reversal): Prior Resistance ({reversal_level:.5f}) held as Support"
        elif role_reversal_put and closes[last] <= opens[last]:
            sig = "PUT"
            strength = 0.94
            reason = f"Breakdown Retest (Role Reversal): Prior Support ({reversal_level:.5f}) held as Resistance"
        elif near_sup and closes[last] >= opens[last]:
            lvl = near_sup[-1]
            sig = "CALL"
            strength = 0.82
            reason = f"Bullish bounce off Support/Pivot zone ({lvl:.5f})"
        elif near_res and closes[last] <= opens[last]:
            lvl = near_res[-1]
            sig = "PUT"
            strength = 0.82
            reason = f"Bearish rejection at Resistance/Pivot zone ({lvl:.5f})"

        votes.append(
            StrategyVote(
                id="sr_pivots",
                name="Support/Resistance & Floor Pivots",
                category="Market Structure",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("sr_pivots", 1.20),
                reason=reason,
                metrics={"pivot": round(piv["P"], 5), "s1": round(piv["S1"], 5), "r1": round(piv["R1"], 5)},
            )
        )

    # 9. CCI(20) + Williams %R(14) Dual
    if is_enabled("cci_williams"):
        c_val = cci_vals[last]
        c_p_val = cci_vals[prev]
        w_val = wr_vals[last]
        cci_now: float = float(c_val) if c_val is not None else 0.0
        cci_prev: float = float(c_p_val) if c_p_val is not None else 0.0
        wr_now: float = float(w_val) if w_val is not None else -50.0
        sig = "NEUTRAL"
        strength = 0.0
        reason = f"CCI={cci_now:.0f} / W%R={wr_now:.0f} neutral"

        if cci_now < -100 and wr_now < -80 and cci_now > cci_prev:
            sig = "CALL"
            strength = 0.86
            reason = f"Dual oversold reversal: CCI={cci_now:.0f}, W%R={wr_now:.0f}"
        elif cci_now > 100 and wr_now > -20 and cci_now < cci_prev:
            sig = "PUT"
            strength = 0.86
            reason = f"Dual overbought reversal: CCI={cci_now:.0f}, W%R={wr_now:.0f}"
        elif cci_prev <= 0 and cci_now > 0 and wr_now > -50:
            sig = "CALL"
            strength = 0.60
            reason = f"CCI zero-line bullish cross ({cci_now:.0f})"
        elif cci_prev >= 0 and cci_now < 0 and wr_now < -50:
            sig = "PUT"
            strength = 0.60
            reason = f"CCI zero-line bearish cross ({cci_now:.0f})"

        votes.append(
            StrategyVote(
                id="cci_williams",
                name="CCI(20) + Williams %R(14) Dual",
                category="Oscillator",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("cci_williams", 1.00),
                reason=reason,
                metrics={"cci": round(cci_now, 1), "williams_r": round(wr_now, 1)},
            )
        )

    # 10. Ichimoku Cloud
    if is_enabled("ichimoku_cloud"):
        tk_now = ichi["tenkan"][last] or price
        kj_now = ichi["kijun"][last] or price
        tk_prev = ichi["tenkan"][prev] or price
        kj_prev = ichi["kijun"][prev] or price
        sa = ichi["senkou_a"][last] or price
        sb = ichi["senkou_b"][last] or price
        cloud_top = max(sa, sb)
        cloud_bot = min(sa, sb)

        sig = "NEUTRAL"
        strength = 0.0
        reason = "Price consolidating inside Kumo cloud"

        if tk_prev <= kj_prev and tk_now > kj_now and price >= cloud_bot:
            sig = "CALL"
            strength = 0.90 if price > cloud_top else 0.72
            reason = "Tenkan-sen crossed above Kijun-sen with Kumo support"
        elif tk_prev >= kj_prev and tk_now < kj_now and price <= cloud_top:
            sig = "PUT"
            strength = 0.90 if price < cloud_bot else 0.72
            reason = "Tenkan-sen crossed below Kijun-sen below Kumo resistance"
        elif price > cloud_top and tk_now > kj_now:
            sig = "CALL"
            strength = 0.62
            reason = "Bullish price & TK alignment above Kumo cloud"
        elif price < cloud_bot and tk_now < kj_now:
            sig = "PUT"
            strength = 0.62
            reason = "Bearish price & TK alignment below Kumo cloud"

        votes.append(
            StrategyVote(
                id="ichimoku_cloud",
                name="Ichimoku Kinko Hyo Cloud",
                category="Macro Trend",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("ichimoku_cloud", 1.15),
                reason=reason,
                metrics={"tenkan": round(tk_now, 5), "kijun": round(kj_now, 5), "cloud_top": round(cloud_top, 5), "cloud_bot": round(cloud_bot, 5)},
            )
        )

    # 11. Donchian(20) Volatility Breakout
    if is_enabled("donchian_squeeze"):
        du_prev = don_u[prev] or price
        dl_prev = don_l[prev] or price
        sig = "NEUTRAL"
        strength = 0.0
        reason = "Price inside 20-period Donchian range"

        if closes[last] > du_prev and closes[last] > opens[last]:
            sig = "CALL"
            strength = 0.84
            reason = f"20-bar Donchian high breakout above {du_prev:.5f}"
        elif closes[last] < dl_prev and closes[last] < opens[last]:
            sig = "PUT"
            strength = 0.84
            reason = f"20-bar Donchian low breakdown below {dl_prev:.5f}"

        votes.append(
            StrategyVote(
                id="donchian_squeeze",
                name="Donchian(20) Volatility Breakout",
                category="Breakout",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("donchian_squeeze", 1.05),
                reason=reason,
                metrics={"donchian_high": round(du_prev, 5), "donchian_low": round(dl_prev, 5)},
            )
        )

    # 12. PyQuotex Triple Confirmation (EMA 20/50 + RSI 7 + Stochastic 14/3/3)
    if is_enabled("pyquotex_triple_confirmation"):
        pq_sig = "NEUTRAL"
        pq_strength = 0.0
        pq_reason = "PyQuotex triple confirmation neutral (awaiting EMA 20/50, RSI 7, and Stoch alignment)"
        try:
            from pyquotex.utils.strategy import TripleConfirmationStrategy
            pq_strat = TripleConfirmationStrategy(None)
            candle_dicts = [
                {"open": float(o), "high": float(h), "low": float(l), "close": float(c)}
                for o, h, l, c in zip(opens, highs, lows, closes)
            ]
            res = pq_strat.analyze(candle_dicts)
            if res == "call":
                pq_sig = "CALL"
                pq_strength = 0.88
                pq_reason = "PyQuotex Triple Confirmation: Uptrend (EMA 20>50) + RSI(7)>50 + Bullish Stoch crossover"
            elif res == "put":
                pq_sig = "PUT"
                pq_strength = 0.88
                pq_reason = "PyQuotex Triple Confirmation: Downtrend (EMA 20<50) + RSI(7)<50 + Bearish Stoch crossover"
        except Exception:
            pass

        votes.append(
            StrategyVote(
                id="pyquotex_triple_confirmation",
                name="PyQuotex Triple Confirmation",
                category="Multi-Layer Confluence",
                signal=pq_sig,
                strength=round(pq_strength, 2),
                weight=get_w("pyquotex_triple_confirmation", 1.40),
                reason=pq_reason,
                metrics={"pyquotex_strategy": pq_sig},
            )
        )

    # 13. 24-Hour Adaptive Trend & Pattern Engine
    if is_enabled("adaptive_24h_trend_pattern"):
        n_hist = len(closes)
        lookback_macro = min(n_hist, 1440)
        recent_macro_h = highs[-lookback_macro:]
        recent_macro_l = lows[-lookback_macro:]

        macro_high = max(recent_macro_h)
        macro_low = min(recent_macro_l)
        macro_range = max(1e-9, macro_high - macro_low)
        equilibrium = (macro_high + macro_low) / 2.0
        rel_pos = (price - macro_low) / macro_range

        # Multi-horizon slopes:
        # 1. Short horizon (last 12 bars)
        p_short = min(12, n_hist - 1)
        short_slope = (closes[last] - closes[last - p_short]) / (closes[last - p_short] if closes[last - p_short] > 0 else 1.0)

        # 2. Medium horizon (last 36 bars)
        p_med = min(36, n_hist - 1)
        med_slope = (closes[last] - closes[last - p_med]) / (closes[last - p_med] if closes[last - p_med] > 0 else 1.0)

        # 3. Macro trend bias (price vs EMA 200 or equilibrium)
        e200_last = ema_200[last]
        macro_bull = price > equilibrium and (e200_last is None or price > e200_last)
        macro_bear = price < equilibrium and (e200_last is None or price < e200_last)

        # Pullback pattern detection:
        is_bull_resumption = (
            short_slope > 0
            and med_slope > 0
            and macro_bull
            and closes[last] > opens[last]
            and closes[last] > highs[prev]
            and lows[prev] <= (ema_fast[prev] or lows[prev])
        )
        is_bear_resumption = (
            short_slope < 0
            and med_slope < 0
            and macro_bear
            and closes[last] < opens[last]
            and closes[last] < lows[prev]
            and highs[prev] >= (ema_fast[prev] or highs[prev])
        )

        sig = "NEUTRAL"
        strength = 0.0
        reason = f"Macro equilibrium {equilibrium:.5f} (pos: {rel_pos*100:.0f}%), slope neutral"

        if is_bull_resumption:
            sig = "CALL"
            strength = 0.92
            reason = f"24h Trend Wave: Bullish pullback resumption above equilibrium ({rel_pos*100:.0f}% macro rank)"
        elif is_bear_resumption:
            sig = "PUT"
            strength = 0.92
            reason = f"24h Trend Wave: Bearish pullback resumption below equilibrium ({rel_pos*100:.0f}% macro rank)"
        elif short_slope > 0.0015 and med_slope > 0.0025 and macro_bull:
            sig = "CALL"
            strength = 0.80
            reason = f"24h Trend Wave: Sustained macro bullish impulse (short slope: +{short_slope*100:.2f}%)"
        elif short_slope < -0.0015 and med_slope < -0.0025 and macro_bear:
            sig = "PUT"
            strength = 0.80
            reason = f"24h Trend Wave: Sustained macro bearish impulse (short slope: {short_slope*100:.2f}%)"
        elif rel_pos <= 0.15 and closes[last] > opens[last] and (rsi_vals[last] or 50.0) < 35:
            sig = "CALL"
            strength = 0.75
            reason = f"24h Dynamic Demand: Demand zone bounce at {macro_low:.5f} (discount rank {rel_pos*100:.0f}%)"
        elif rel_pos >= 0.85 and closes[last] < opens[last] and (rsi_vals[last] or 50.0) > 65:
            sig = "PUT"
            strength = 0.75
            reason = f"24h Dynamic Supply: Supply zone rejection at {macro_high:.5f} (premium rank {rel_pos*100:.0f}%)"

        votes.append(
            StrategyVote(
                id="adaptive_24h_trend_pattern",
                name="24-Hour Adaptive Trend & Pattern Engine",
                category="Adaptive Trend Learning",
                signal=sig,
                strength=round(strength, 2),
                weight=get_w("adaptive_24h_trend_pattern", 1.45),
                reason=reason,
                metrics={
                    "macro_high": round(macro_high, 5),
                    "macro_low": round(macro_low, 5),
                    "equilibrium": round(equilibrium, 5),
                    "macro_pos_pct": round(rel_pos * 100, 1),
                    "short_slope_pct": round(short_slope * 100, 3),
                },
            )
        )

    # Weighted Confluence Aggregation
    call_score = 0.0
    put_score = 0.0
    total_active_weight = 0.0
    call_count = 0
    put_count = 0

    for v in votes:
        total_active_weight += v.weight
        if v.signal == "CALL":
            call_score += v.weight * v.strength
            call_count += 1
        elif v.signal == "PUT":
            put_score += v.weight * v.strength
            put_count += 1

    net_score = call_score - put_score
    dominant_count = call_count if net_score > 0 else put_count
    opposing_count = put_count if net_score > 0 else call_count
    dom_score = call_score if net_score > 0 else put_score
    opp_score = put_score if net_score > 0 else call_score

    # Confluence Confidence: Multi-indicator directional consensus
    if dominant_count == 0 or abs(net_score) < 0.01:
        raw_conf = 0.0
    elif dominant_count == 1:
        raw_conf = min(50.0, 35.0 + (dom_score / max(dom_score + opp_score, 0.01)) * 15.0)
    elif dominant_count == 2:
        consensus = (dom_score - opp_score) / max(dom_score + opp_score, 0.01)
        raw_conf = min(60.0, max(30.0, 45.0 + consensus * 15.0 - opposing_count * 8.0))
    else:  # dominant_count >= 3 (multi-strategy confluence threshold)
        consensus = (dom_score - opp_score) / max(dom_score + opp_score, 0.01)
        raw_conf = 58.0 + consensus * 24.0 + min(dominant_count - 3, 4) * 3.0 - opposing_count * 6.0
        if dominant_count >= 4 and opposing_count <= 1:
            raw_conf = min(96.0, raw_conf + 6.0)

    confidence = round(min(98.0, max(0.0, raw_conf)), 1)

    if confidence >= min_confidence and net_score > 0:
        final_signal = "CALL"
    elif confidence >= min_confidence and net_score < 0:
        final_signal = "PUT"
    else:
        final_signal = "NEUTRAL"

    # 1. Multi-Timeframe Trend Confirmation (5m aggregate)
    mtf = ind.higher_timeframe_trend(closes, highs, lows, aggregate_period=5)
    mtf_trend = mtf.get("mtf_trend", "FLAT")
    mtf_tag = ""
    if final_signal == "CALL":
        if mtf_trend == "BULLISH":
            confidence = min(98.0, confidence + 4.0)
            mtf_tag = " [MTF 5m Bullish Confirmed]"
        elif mtf_trend == "BEARISH":
            confidence = max(0.0, confidence - 7.0)
            mtf_tag = " [MTF 5m Macro Conflict]"
    elif final_signal == "PUT":
        if mtf_trend == "BEARISH":
            confidence = min(98.0, confidence + 4.0)
            mtf_tag = " [MTF 5m Bearish Confirmed]"
        elif mtf_trend == "BULLISH":
            confidence = max(0.0, confidence - 7.0)
            mtf_tag = " [MTF 5m Macro Conflict]"

    # 2. Candlestick Quality & Volume Filter
    candle_qual = ind.candlestick_quality(opens[last], highs[last], lows[last], closes[last], cur_atr)
    candlestick_disqualified = False
    candlestick_reason = ""
    if candle_qual["is_doji"] and final_signal in ("CALL", "PUT"):
        confidence = max(0.0, confidence - 8.0)
        candlestick_disqualified = True
        candlestick_reason = "Rejected: Current candle is an indecisive Doji bar (|body| < 15% ATR)."
    elif final_signal == "CALL" and candle_qual["exhaustion_bull"]:
        confidence = max(0.0, confidence - 10.0)
        candlestick_disqualified = True
        candlestick_reason = "Rejected: Heavy upper exhaustion wick indicates seller rejection at highs."
    elif final_signal == "PUT" and candle_qual["exhaustion_bear"]:
        confidence = max(0.0, confidence - 10.0)
        candlestick_disqualified = True
        candlestick_reason = "Rejected: Heavy lower exhaustion wick indicates buyer rejection at lows."

    confidence = round(confidence, 1)

    # Re-evaluate final signal threshold after MTF & Candlestick quality adjustments
    if confidence < min_confidence:
        final_signal = "NEUTRAL"

    # Strict Telegram Qualification Check:
    # Only high-conviction directional signals with clear multi-strategy consensus qualify.
    qualified = False
    qualification_reason = ""
    if final_signal not in ("CALL", "PUT"):
        qualification_reason = f"No directional Quotex trend signal ({confidence:.1f}% < {min_confidence:.0f}% min)."
    elif candlestick_disqualified:
        qualification_reason = candlestick_reason
    elif mtf_tag == " [MTF 5m Macro Conflict]" and confidence <= tg_min_confidence + 2.0:
        qualification_reason = f"Macro timeframe conflict ({mtf_trend} 5m trend opposes {final_signal})."
    elif confidence < tg_min_confidence:
        qualification_reason = f"Confidence {confidence:.1f}% does not exceed {tg_min_confidence:.0f}% Telegram gate."
    elif dominant_count < 3:
        qualification_reason = f"Weak confluence: only {dominant_count} strategies agree (need 3+)."
    elif opposing_count > 2 or (dominant_count - opposing_count) < 2:
        qualification_reason = (
            f"Conflicting Quotex votes ({dominant_count} aligned vs {opposing_count} opposing)."
        )
    else:
        qualified = True
        qualification_reason = (
            f"Qualified {final_signal}: {confidence:.1f}% confidence with {dominant_count} aligned strategies{mtf_tag}."
        )

    # Expiry recommendation based on active timeframe, volatility & confidence
    rec_expiry = _format_expiry(timeframe, volatility_regime, confidence)

    # Entry timing check
    last_time = int(candles[-1].get("time", 0)) if candles else 0
    timing_guard = ind.check_candle_entry_window(last_time, 60, 5)

    return {
        "signal": final_signal,
        "confidence": confidence,
        "qualified": qualified,
        "qualification_reason": qualification_reason,
        "recommended_expiry": rec_expiry,
        "volatility_regime": volatility_regime,
        "market_regime": market_regime,
        "bollinger_bandwidth": round(cur_bbw, 5),
        "candlestick_quality": candle_qual,
        "mtf_confirmation": mtf,
        "entry_timing_guard": timing_guard,
        "atr_pct": round(atr_pct, 4),
        "price": price,
        "call_count": call_count,
        "put_count": put_count,
        "neutral_count": len(votes) - call_count - put_count,
        "call_score": round(call_score, 2),
        "put_score": round(put_score, 2),
        "votes": [asdict(v) for v in votes],
        "levels": {
            "supports": [round(x, 5) for x in supports],
            "resistances": [round(x, 5) for x in resistances],
        },
        "overlays": {
            "ema_fast": [round(x, 6) if x is not None else None for x in ema_fast],
            "ema_slow": [round(x, 6) if x is not None else None for x in ema_slow],
            "ema_200": [round(x, 6) if x is not None else None for x in ema_200],
            "bb_upper": [round(x, 6) if x is not None else None for x in bb_upper],
            "bb_mid": [round(x, 6) if x is not None else None for x in bb_mid],
            "bb_lower": [round(x, 6) if x is not None else None for x in bb_lower],
            "supertrend": [round(x, 6) if x is not None else None for x in st_line],
            "supertrend_dir": st_dir,
            "sar": [round(x, 6) for x in sar_vals],
            "rsi": [round(x, 2) if x is not None else None for x in rsi_vals],
            "macd_hist": [round(x, 6) if x is not None else None for x in macd_hist],
        },
    }
