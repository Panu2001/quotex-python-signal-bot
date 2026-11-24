"""
Quotex High-Frequency Quantitative Strategy Engine.
Specialized for 1-minute and 5-minute Binary Options (Live Forex & Quotex OTC).

Production-ready implementation utilizing Pandas and Pandas-TA with:
- Zero-repainting closed-candle evaluation
- 4 Modular Confluence Strategies (Mean Reversion, Trend Pullback, Momentum, Price Action S/R)
- Quantitative ATR volatility gating (rejects dead flatlines and news spikes)
- Confluence aggregator with conflict resolution and agreement boost
- Telegram-ready alert dispatch formatting
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

try:
    import warnings
    warnings.simplefilter(action="ignore", category=Warning)
    warnings.filterwarnings("ignore")
    import numpy as np
    import pandas as pd
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False
    np = None  # type: ignore[assignment]
    pd = None  # type: ignore[assignment]
    ta = None  # type: ignore[assignment]


SignalDirection = Literal["CALL", "PUT", "NONE"]


@dataclass(frozen=True)
class StrategyResult:
    signal: SignalDirection
    strategy: str
    confidence: int  # 1 to 100
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal": self.signal,
            "strategy": self.strategy,
            "confidence": self.confidence,
            "reason": self.reason,
        }


class StrategyEngine:
    """
    Production-grade Quantitative Strategy Engine for Quotex Binary Options.
    Evaluates historical closed OHLCV data using Pandas and Pandas-TA.
    """

    REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

    def __init__(
        self,
        min_candles: int = 200,
        atr_period: int = 14,
        atr_dead_threshold_ratio: float = 0.55,
        atr_spike_threshold_ratio: float = 2.60,
    ) -> None:
        """
        Initialize the Strategy Engine with quantitative parameters.

        :param min_candles: Minimum closed candles required (default: 200).
        :param atr_period: Lookback window for ATR calculations.
        :param atr_dead_threshold_ratio: Ratio below rolling ATR average marking dead/flat market.
        :param atr_spike_threshold_ratio: Ratio above rolling ATR average marking dangerous news anomaly.
        """
        self.min_candles = min_candles
        self.atr_period = atr_period
        self.atr_dead_threshold_ratio = atr_dead_threshold_ratio
        self.atr_spike_threshold_ratio = atr_spike_threshold_ratio

    # -------------------------------------------------------------------------
    # Validation & Indicator Preparation Pipeline
    # -------------------------------------------------------------------------
    def _validate_and_prepare(self, df: Any) -> Tuple[Any, Optional[str]]:
        """
        Validates columns, checks data depth, handles missing values,
        and computes all required indicators vectorized across the DataFrame.
        """
        if not HAS_PANDAS_TA:
            return None, "pandas and pandas-ta are required to run this quantitative engine."

        if df is None or not isinstance(df, pd.DataFrame):
            return df, "Input must be a valid Pandas DataFrame."

        missing_cols = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        if missing_cols:
            return df, f"Missing required DataFrame columns: {missing_cols}"

        if len(df) < self.min_candles:
            return df, f"Insufficient candle data: {len(df)} bars provided, need >= {self.min_candles}."

        # Work on an isolated copy to prevent side-effects on caller's DataFrame
        data = df.copy()

        # Ensure correct numeric types
        for col in ["open", "high", "low", "close", "volume"]:
            data[col] = pd.to_numeric(data[col], errors="coerce")

        # Drop corrupted rows
        data = data.dropna(subset=["open", "high", "low", "close"])
        if len(data) < self.min_candles:
            return data, "DataFrame dropped below minimum length after NaN cleanup."

        # Vectorized Indicator Computations via pandas-ta
        try:
            # 1. Bollinger Bands (20, 2.0)
            data.ta.bbands(length=20, std=2.0, append=True)

            # 2. RSI (14)
            data.ta.rsi(length=14, append=True)

            # 3. Trend EMAs (50, 200)
            data.ta.ema(length=50, append=True)
            data.ta.ema(length=200, append=True)

            # 4. Stochastic (14, 3, 3)
            data.ta.stoch(k=14, d=3, smooth_k=3, append=True)

            # 5. MACD (12, 26, 9)
            data.ta.macd(fast=12, slow=26, signal=9, append=True)

            # 6. ADX (14)
            data.ta.adx(length=14, append=True)

            # 7. ATR (14)
            data.ta.atr(length=self.atr_period, append=True)

        except Exception as exc:
            return data, f"Technical indicator calculation failure: {exc}"

        return data, None

    # -------------------------------------------------------------------------
    # Convenience Wrapper: Evaluate raw list of candle dicts
    # -------------------------------------------------------------------------
    def evaluate_candles(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert a list of candle dicts (from Quotex feed) to DataFrame and evaluate."""
        if not HAS_PANDAS_TA:
            return {
                "signal": "NONE",
                "strategy": "Dependency Missing",
                "confidence": 0,
                "reason": "pandas and pandas-ta are required to run StrategyEngine.",
            }

        df = pd.DataFrame(candles)
        if "timestamp" not in df.columns and "time" in df.columns:
            df["timestamp"] = df["time"]
        if "volume" not in df.columns:
            df["volume"] = 1.0

        return self.evaluate_all(df)

    # -------------------------------------------------------------------------
    # Market Volatility Gating Filter (ATR)
    # -------------------------------------------------------------------------
    def check_volatility_filter(self, df: Any) -> Tuple[bool, str, Dict[str, float]]:
        """
        Evaluates current ATR against rolling 30-bar baseline.
        Rejects low-liquidity/flat markets and erratic news spikes.
        """
        atr_col = [c for c in df.columns if c.startswith("ATR") or c.startswith("ATRr")]
        if not atr_col:
            return False, "ATR column not found in indicator set.", {}

        atr_series = df[atr_col[0]]
        curr_atr = float(atr_series.iloc[-1])
        rolling_atr = float(atr_series.iloc[-35:-1].mean())

        if math.isnan(curr_atr) or curr_atr <= 1e-9 or math.isnan(rolling_atr) or rolling_atr <= 1e-9:
            return False, "Invalid ATR metrics (zero or NaN).", {}

        ratio = curr_atr / rolling_atr

        metrics = {
            "current_atr": round(curr_atr, 6),
            "rolling_avg_atr": round(rolling_atr, 6),
            "volatility_ratio": round(ratio, 2),
        }

        if ratio < self.atr_dead_threshold_ratio:
            return False, f"Flat/Dead Market: ATR ratio {ratio:.2f}x is below {self.atr_dead_threshold_ratio}x minimum.", metrics

        if ratio > self.atr_spike_threshold_ratio:
            return False, f"Extreme Anomaly/News Spike: ATR ratio {ratio:.2f}x exceeds {self.atr_spike_threshold_ratio}x ceiling.", metrics

        return True, "Volatility conditions within nominal quantitative parameters.", metrics

    # -------------------------------------------------------------------------
    # Strategy 1: Mean Reversion (OTC & Ranging Markets)
    # -------------------------------------------------------------------------
    def strategy_mean_reversion(self, df: Any) -> Dict[str, Any]:
        """
        Strategy 1: Bollinger Bands (20,2) + RSI (14) + Candlestick Rejection Pin-bar.
        Optimized for Quotex OTC sessions and sideways ranges.
        """
        bbl_col = [c for c in df.columns if c.startswith("BBL")][0]
        bbu_col = [c for c in df.columns if c.startswith("BBU")][0]
        bbm_col = [c for c in df.columns if c.startswith("BBM")][0]
        rsi_col = "RSI_14"

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        o, h, l, c = curr["open"], curr["high"], curr["low"], curr["close"]
        bbl, bbu = curr[bbl_col], curr[bbu_col]
        rsi = curr[rsi_col]

        total_range = max(h - l, 1e-7)
        lower_wick = min(o, c) - l
        upper_wick = h - max(o, c)

        lower_wick_ratio = lower_wick / total_range
        upper_wick_ratio = upper_wick / total_range

        # CALL Setup: Price pierces/touches lower band + RSI oversold + Strong lower wick rejection
        if (l <= bbl or prev["low"] <= prev[bbl_col]) and rsi <= 34.0:
            if lower_wick_ratio >= 0.40 and c > (l + 0.35 * total_range):
                confidence = min(92, int(74 + (34.0 - rsi) * 1.5 + lower_wick_ratio * 15))
                return StrategyResult(
                    signal="CALL",
                    strategy="Mean Reversion (BB + RSI Rejection)",
                    confidence=confidence,
                    reason=(
                        f"Lower BB rejection ({bbl:.5f}) with RSI oversold ({rsi:.1f}) "
                        f"and {lower_wick_ratio:.0%} lower wick absorption."
                    ),
                ).to_dict()

        # PUT Setup: Price pierces/touches upper band + RSI overbought + Strong upper wick rejection
        if (h >= bbu or prev["high"] >= prev[bbu_col]) and rsi >= 66.0:
            if upper_wick_ratio >= 0.40 and c < (h - 0.35 * total_range):
                confidence = min(92, int(74 + (rsi - 66.0) * 1.5 + upper_wick_ratio * 15))
                return StrategyResult(
                    signal="PUT",
                    strategy="Mean Reversion (BB + RSI Rejection)",
                    confidence=confidence,
                    reason=(
                        f"Upper BB rejection ({bbu:.5f}) with RSI overbought ({rsi:.1f}) "
                        f"and {upper_wick_ratio:.0%} upper exhaustion wick."
                    ),
                ).to_dict()

        return StrategyResult(
            signal="NONE",
            strategy="Mean Reversion (BB + RSI Rejection)",
            confidence=0,
            reason="Price within normal Bollinger boundaries; no exhaustion pin-bar detected.",
        ).to_dict()

    # -------------------------------------------------------------------------
    # Strategy 2: Trend Pullback (Trending Markets)
    # -------------------------------------------------------------------------
    def strategy_trend_pullback(self, df: Any) -> Dict[str, Any]:
        """
        Strategy 2: EMA 50/200 Macro Trend + Stochastic (14,3,3) Crossover.
        Enters during minor corrective pullbacks in the dominant trend direction.
        """
        stoch_k_col = [c for c in df.columns if c.startswith("STOCHk")][0]
        stoch_d_col = [c for c in df.columns if c.startswith("STOCHd")][0]

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        price = curr["close"]
        ema50 = curr["EMA_50"]
        ema200 = curr["EMA_200"]

        k_now, d_now = curr[stoch_k_col], curr[stoch_d_col]
        k_prev, d_prev = prev[stoch_k_col], prev[stoch_d_col]

        # Bullish Macro Trend: Price > EMA50 > EMA200
        uptrend = (ema50 > ema200) and (price > ema50)
        # Bearish Macro Trend: Price < EMA50 < EMA200
        downtrend = (ema50 < ema200) and (price < ema50)

        # Bullish Stochastic crossover from oversold zone (< 28)
        bull_cross = (k_prev <= d_prev) and (k_now > d_now) and (k_prev <= 28.0 or k_now <= 28.0)
        # Bearish Stochastic crossover from overbought zone (> 72)
        bear_cross = (k_prev >= d_prev) and (k_now < d_now) and (k_prev >= 72.0 or k_now >= 72.0)

        if uptrend and bull_cross:
            confidence = min(94, int(80 + (28.0 - min(k_now, k_prev)) * 0.8))
            return StrategyResult(
                signal="CALL",
                strategy="Trend Pullback (EMA 50/200 + Stoch Cross)",
                confidence=confidence,
                reason=(
                    f"Bullish trend confirmed (Price > EMA50 > EMA200); Stochastic %K ({k_now:.1f}) "
                    f"crossed above %D ({d_now:.1f}) in oversold pullback zone."
                ),
            ).to_dict()

        if downtrend and bear_cross:
            confidence = min(94, int(80 + (max(k_now, k_prev) - 72.0) * 0.8))
            return StrategyResult(
                signal="PUT",
                strategy="Trend Pullback (EMA 50/200 + Stoch Cross)",
                confidence=confidence,
                reason=(
                    f"Bearish trend confirmed (Price < EMA50 < EMA200); Stochastic %K ({k_now:.1f}) "
                    f"crossed below %D ({d_now:.1f}) in overbought pullback zone."
                ),
            ).to_dict()

        return StrategyResult(
            signal="NONE",
            strategy="Trend Pullback (EMA 50/200 + Stoch Cross)",
            confidence=0,
            reason="Market not aligned in a clear EMA trend or Stochastic is outside trigger zones.",
        ).to_dict()

    # -------------------------------------------------------------------------
    # Strategy 3: Momentum Breakout
    # -------------------------------------------------------------------------
    def strategy_momentum_breakout(self, df: Any) -> Dict[str, Any]:
        """
        Strategy 3: MACD (12,26,9) Momentum Crossover + ADX (14) > 25 Trend-Strength.
        Captures directional momentum surges backed by trend expansion.
        """
        macd_col = [c for c in df.columns if c.startswith("MACD_")][0]
        macdh_col = [c for c in df.columns if c.startswith("MACDh_")][0]
        macds_col = [c for c in df.columns if c.startswith("MACDs_")][0]
        adx_col = "ADX_14"
        dmp_col = "DMP_14"
        dmn_col = "DMN_14"

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        adx = curr[adx_col]
        dmp, dmn = curr[dmp_col], curr[dmn_col]
        macd_line, sig_line, hist = curr[macd_col], curr[macds_col], curr[macdh_col]
        hist_prev = prev[macdh_col]

        # Trend Strength Filter: ADX must confirm strong directional momentum
        if adx < 25.0:
            return StrategyResult(
                signal="NONE",
                strategy="Momentum Breakout (MACD + ADX)",
                confidence=0,
                reason=f"ADX={adx:.1f} indicates weak trend / consolidation (threshold: >=25.0).",
            ).to_dict()

        # Bullish Momentum: +DI > -DI & (Histogram flipped positive or expanding)
        bullish_macd = (hist_prev <= 0 and hist > 0) or (hist > 0 and hist > hist_prev and macd_line > sig_line)
        if dmp > dmn and bullish_macd:
            confidence = min(90, int(75 + (adx - 25.0) * 0.75 + min(10, (dmp - dmn))))
            return StrategyResult(
                signal="CALL",
                strategy="Momentum Breakout (MACD + ADX)",
                confidence=confidence,
                reason=(
                    f"Bullish momentum cross: +DI ({dmp:.1f}) > -DI ({dmn:.1f}) "
                    f"with ADX directional strength {adx:.1f} and expanding MACD histogram."
                ),
            ).to_dict()

        # Bearish Momentum: -DI > +DI & (Histogram flipped negative or expanding downward)
        bearish_macd = (hist_prev >= 0 and hist < 0) or (hist < 0 and hist < hist_prev and macd_line < sig_line)
        if dmn > dmp and bearish_macd:
            confidence = min(90, int(75 + (adx - 25.0) * 0.75 + min(10, (dmn - dmp))))
            return StrategyResult(
                signal="PUT",
                strategy="Momentum Breakout (MACD + ADX)",
                confidence=confidence,
                reason=(
                    f"Bearish momentum cross: -DI ({dmn:.1f}) > +DI ({dmp:.1f}) "
                    f"with ADX directional strength {adx:.1f} and negative MACD histogram expansion."
                ),
            ).to_dict()

        return StrategyResult(
            signal="NONE",
            strategy="Momentum Breakout (MACD + ADX)",
            confidence=0,
            reason="MACD histogram flat or opposing directional DI lines.",
        ).to_dict()

    # -------------------------------------------------------------------------
    # Strategy 4: Price Action + Support/Resistance (Fractals & Engulfing)
    # -------------------------------------------------------------------------
    def strategy_price_action_sr(self, df: Any) -> Dict[str, Any]:
        """
        Strategy 4: Fractal Swing High/Low levels + Bullish/Bearish Engulfing confirmation.
        Detects institutional level rejections and immediate candlestick shifts.
        """
        atr_col = [c for c in df.columns if c.startswith("ATR") or c.startswith("ATRr")][0]
        atr_val = float(df[atr_col].iloc[-1])
        zone_tolerance = max(atr_val * 0.40, 1e-5)

        # Detect Fractal Swing Points over recent 60 closed bars
        window = df.iloc[-65:-1].copy()
        highs = window["high"].values
        lows = window["low"].values

        swing_highs: List[float] = []
        swing_lows: List[float] = []

        # Classical 5-bar Williams Fractal
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
                swing_highs.append(highs[i])
            if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
                swing_lows.append(lows[i])

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        o0, c0 = curr["open"], curr["close"]
        o1, c1 = prev["open"], prev["close"]
        body0 = abs(c0 - o0)
        body1 = abs(c1 - o1)

        # Candlestick Engulfing Pattern Checks
        bullish_engulfing = (c1 < o1) and (c0 > o0) and (c0 >= o1) and (o0 <= c1) and (body0 > body1 * 1.05)
        bearish_engulfing = (c1 > o1) and (c0 < o0) and (c0 <= o1) and (o0 >= c1) and (body0 > body1 * 1.05)

        # Proximity to Swing Support
        near_support = any(abs(curr["low"] - sup) <= zone_tolerance or abs(c0 - sup) <= zone_tolerance for sup in swing_lows)
        # Proximity to Swing Resistance
        near_resistance = any(abs(curr["high"] - res) <= zone_tolerance or abs(c0 - res) <= zone_tolerance for res in swing_highs)

        if bullish_engulfing and near_support:
            return StrategyResult(
                signal="CALL",
                strategy="Price Action + S/R (Fractal Engulfing)",
                confidence=88,
                reason=(
                    f"Bullish Engulfing pattern cleanly bouncing off confirmed fractal support zone "
                    f"(Tolerance: ±{zone_tolerance:.5f})."
                ),
            ).to_dict()

        if bearish_engulfing and near_resistance:
            return StrategyResult(
                signal="PUT",
                strategy="Price Action + S/R (Fractal Engulfing)",
                confidence=88,
                reason=(
                    f"Bearish Engulfing pattern rejecting confirmed fractal resistance ceiling "
                    f"(Tolerance: ±{zone_tolerance:.5f})."
                ),
            ).to_dict()

        return StrategyResult(
            signal="NONE",
            strategy="Price Action + S/R (Fractal Engulfing)",
            confidence=0,
            reason="No engulfing candle detected at a key fractal swing level.",
        ).to_dict()

    # -------------------------------------------------------------------------
    # Confluence Aggregator & Conflict Resolution
    # -------------------------------------------------------------------------
    def evaluate_all(self, df: Any) -> Dict[str, Any]:
        """
        Executes data pipeline, checks volatility filters, runs all 4 strategies,
        resolves conflicting votes, applies agreement bonuses, and produces the final trade verdict.
        """
        clean_df, err = self._validate_and_prepare(df)
        if err:
            return {
                "signal": "NONE",
                "strategy": "Pipeline Error",
                "confidence": 0,
                "reason": err,
                "active_votes": [],
                "volatility_status": "Invalid",
            }

        # 1. Market Condition Volatility Gate
        vol_passed, vol_reason, vol_metrics = self.check_volatility_filter(clean_df)
        if not vol_passed:
            return {
                "signal": "NONE",
                "strategy": "Risk Filter (ATR Gate)",
                "confidence": 0,
                "reason": vol_reason,
                "volatility_metrics": vol_metrics,
                "active_votes": [],
            }

        # 2. Run All 4 Modular Strategies
        strategies = [
            self.strategy_mean_reversion,
            self.strategy_trend_pullback,
            self.strategy_momentum_breakout,
            self.strategy_price_action_sr,
        ]

        votes = [strat(clean_df) for strat in strategies]
        call_votes = [v for v in votes if v["signal"] == "CALL"]
        put_votes = [v for v in votes if v["signal"] == "PUT"]

        # 3. Resolve Directional Conflicts (Strict Safety Guard)
        if call_votes and put_votes:
            call_names = ", ".join(v["strategy"] for v in call_votes)
            put_names = ", ".join(v["strategy"] for v in put_votes)
            return {
                "signal": "NONE",
                "strategy": "Conflict Resolver",
                "confidence": 0,
                "reason": (
                    f"Directional Deadlock: CALL voted by [{call_names}] while "
                    f"PUT voted by [{put_names}]. Trade aborted for capital preservation."
                ),
                "active_votes": votes,
                "volatility_metrics": vol_metrics,
            }

        # 4. Handle Coordinated Directional Agreement (Confluence Boost)
        active_group = call_votes if call_votes else put_votes
        if not active_group:
            return {
                "signal": "NONE",
                "strategy": "Confluence Engine",
                "confidence": 0,
                "reason": "Market in neutral state. No quantitative strategy met entry criteria.",
                "active_votes": votes,
                "volatility_metrics": vol_metrics,
            }

        target_direction: SignalDirection = active_group[0]["signal"]
        base_confidence = max(v["confidence"] for v in active_group)

        # Multi-strategy agreement bonus: +6% per confirming strategy beyond the first, capped at 98%
        agreement_count = len(active_group)
        confluence_boost = (agreement_count - 1) * 6
        final_confidence = min(98, base_confidence + confluence_boost)

        combined_strategy_names = " + ".join([v["strategy"].split(" (")[0] for v in active_group])
        composite_reasons = " | ".join([f"{v['strategy']}: {v['reason']}" for v in active_group])

        strategy_label = (
            f"Multi-Confluence ({combined_strategy_names})"
            if agreement_count > 1
            else active_group[0]["strategy"]
        )

        return {
            "signal": target_direction,
            "strategy": strategy_label,
            "confidence": final_confidence,
            "agreement_count": agreement_count,
            "reason": composite_reasons,
            "volatility_metrics": vol_metrics,
            "individual_votes": votes,
            "timestamp": int(clean_df.iloc[-1]["timestamp"]),
            "price": float(clean_df.iloc[-1]["close"]),
        }

    # -------------------------------------------------------------------------
    # Telegram-Ready Dispatch Formatter
    # -------------------------------------------------------------------------
    @staticmethod
    def format_telegram_alert(
        result: Dict[str, Any],
        asset: str,
        timeframe: str = "1m",
        expiry: str = "1m - 2m",
    ) -> str:
        """
        Formats the evaluation payload into an institutional-grade, Telegram-ready message.
        """
        sig = result.get("signal", "NONE")
        if sig == "NONE":
            return f"ℹ️ <b>[NO TRADE] {asset} ({timeframe})</b>: {result.get('reason', 'Neutral market conditions.')}"

        emoji = "🟢 CALL (BUY)" if sig == "CALL" else "🔴 PUT (SELL)"
        confidence = result.get("confidence", 0)
        strategy = result.get("strategy", "Unknown")
        reason = result.get("reason", "N/A")
        price = result.get("price", 0.0)
        vol_metrics = result.get("volatility_metrics", {})

        return (
            f"🚨 <b>QUOTEX QUANTITATIVE SIGNAL ALERT</b> 🚨\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Asset</b>: <code>{asset}</code>\n"
            f"⏱ <b>Timeframe</b>: <b>{timeframe}</b>\n"
            f"⏳ <b>Recommended Expiry</b>: <b>{expiry}</b>\n"
            f"🎯 <b>Direction</b>: <b>{emoji}</b>\n"
            f"🔥 <b>Confidence Score</b>: <b>{confidence}%</b>\n"
            f"💵 <b>Trigger Strike</b>: <code>{price:.5f}</code>\n"
            f"🏆 <b>Strategy Layer</b>: <i>{strategy}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <b>Quantitative Reason</b>:\n"
            f"{reason}\n\n"
            f"📈 <b>ATR Volatility Ratio</b>: <code>{vol_metrics.get('volatility_ratio', 1.0):.2f}x</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <b>Execution Rule</b>: Enter strictly within the first <b>0–5 seconds</b> of the fresh candle open (:00)."
        )


# =============================================================================
# Runnable Test Block (__main__)
# =============================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("🚀 Initializing Quotex Quantitative Strategy Engine (Pandas & Pandas-TA)")
    print("=" * 70)

    if not HAS_PANDAS_TA:
        print("[!] pandas and pandas-ta are required to run this engine.")
        print("    Install via: pip install pandas pandas-ta")
        exit(0)

    # 1. Synthesize 220 bars of realistic OHLCV market data
    np.random.seed(42)
    n_bars = 220
    base_ts = int(datetime.now(timezone.utc).timestamp()) - (n_bars * 60)

    timestamps = [base_ts + i * 60 for i in range(n_bars)]
    price = 1.10000
    prices = [price]

    for i in range(1, n_bars):
        drift = 0.00008 if i < 195 else -0.00006  # Pullback over bars 195-218
        if i == n_bars - 1:
            drift = 0.00015  # Reversal bar at support on bar 219
        step = np.random.normal(drift, 0.00018)
        price += step
        prices.append(price)

    opens, highs, lows, closes, volumes = [], [], [], [], []
    for p in prices:
        spread = abs(np.random.normal(0.00012, 0.00004))
        o = p - np.random.uniform(-0.00005, 0.00005)
        c = p
        h = max(o, c) + spread * np.random.uniform(0.3, 1.0)
        l = min(o, c) - spread * np.random.uniform(0.3, 1.0)
        v = np.random.uniform(80.0, 450.0)
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        volumes.append(v)

    mock_df = pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })

    # 2. Instantiate and run Strategy Engine
    engine = StrategyEngine(min_candles=200)

    print(f"\n[1/3] Ingested Mock Data: {len(mock_df)} closed candles.")
    print(f"      Initial Bar: {mock_df['close'].iloc[0]:.5f} | Latest Bar: {mock_df['close'].iloc[-1]:.5f}")

    print("\n[2/3] Evaluating Confluence Engine across all 4 algorithmic models...")
    result = engine.evaluate_all(mock_df)

    print("\n[3/3] Engine Evaluation Payload:")
    print(f"      Signal:     {result['signal']}")
    print(f"      Strategy:   {result['strategy']}")
    print(f"      Confidence: {result['confidence']}%")
    print(f"      Reason:     {result['reason']}")

    print("\n" + "=" * 70)
    print("📢 FORMATTED TELEGRAM SIGNAL DISPATCH:")
    print("=" * 70)
    telegram_message = engine.format_telegram_alert(
        result=result,
        asset="EUR/USD (OTC)",
        timeframe="1m (60s)",
        expiry="1m - 2m",
    )
    print(telegram_message)
    print("=" * 70)
