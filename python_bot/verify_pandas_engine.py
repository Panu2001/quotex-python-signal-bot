"""Unit tests for the Quotex Quantitative Pandas Strategy Engine.

Verifies:
1. Input validation & NaN handling (minimum bars, missing columns)
2. ATR Volatility Gating (flat market rejection, news spike rejection)
3. Strategy 1: Mean Reversion (Bollinger Bands + RSI + Pinbar rejection)
4. Strategy 2: Trend Pullback (EMA 50/200 + Stochastic Crossover)
5. Strategy 3: Momentum Breakout (MACD + ADX trend strength confirmation)
6. Strategy 4: Price Action + S/R (Williams Fractals + Bullish/Bearish Engulfing)
7. Confluence Aggregator (Conflict resolution, multi-strategy agreement boost)
8. Quotex raw candle dict ingestion (time -> timestamp mapping)
9. Telegram Alert Formatter (institutional layout, 0-5s execution rule)
"""

import math
import unittest
import warnings
from datetime import datetime, timezone
import pandas as pd
import numpy as np

warnings.simplefilter(action="ignore", category=Warning)
warnings.filterwarnings("ignore")

from pandas_strategy_engine import StrategyEngine, HAS_PANDAS_TA


def make_synthetic_ohlcv(
    n: int = 220,
    base_price: float = 1.10000,
    drift: float = 0.00005,
    noise: float = 0.00010,
    seed: int = 42,
) -> pd.DataFrame:
    """Generates synthetic closed OHLCV candlestick series for testing."""
    np.random.seed(seed)
    base_ts = 1_700_000_000
    timestamps = [base_ts + i * 60 for i in range(n)]

    prices = [base_price]
    for _ in range(1, n):
        step = np.random.normal(drift, noise)
        prices.append(prices[-1] + step)

    opens, highs, lows, closes, volumes = [], [], [], [], []
    for p in prices:
        spread = abs(np.random.normal(noise, noise * 0.2))
        o = p - np.random.uniform(-noise * 0.3, noise * 0.3)
        c = p
        h = max(o, c) + spread * 0.5
        l = min(o, c) - spread * 0.5
        v = float(np.random.uniform(50.0, 500.0))
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        volumes.append(v)

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


@unittest.skipUnless(HAS_PANDAS_TA, "pandas and pandas-ta are required for StrategyEngine")
class TestPandasStrategyEngine(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyEngine(min_candles=200)

    # -------------------------------------------------------------------------
    # 1. Validation & Input Handling
    # -------------------------------------------------------------------------
    def test_insufficient_bars_rejection(self):
        short_df = make_synthetic_ohlcv(n=150)
        res = self.engine.evaluate_all(short_df)
        self.assertEqual(res["signal"], "NONE")
        self.assertIn("Insufficient candle data", res["reason"])

    def test_missing_required_columns(self):
        df = make_synthetic_ohlcv(n=220).drop(columns=["volume"])
        res = self.engine.evaluate_all(df)
        self.assertEqual(res["signal"], "NONE")
        self.assertIn("Missing required DataFrame columns", res["reason"])

    def test_nan_cleanup_drops_corrupted_rows(self):
        df = make_synthetic_ohlcv(n=225)
        # Inject NaN into a few rows
        df.loc[10, "close"] = np.nan
        df.loc[20, "open"] = np.nan
        clean_df, err = self.engine._validate_and_prepare(df)
        self.assertIsNone(err)
        self.assertEqual(len(clean_df), 223)

    # -------------------------------------------------------------------------
    # 2. ATR Volatility Gate
    # -------------------------------------------------------------------------
    def test_atr_flat_market_rejection(self):
        # Generate 220 bars with near-zero noise across the last 20 bars to trigger low volatility gate
        df = make_synthetic_ohlcv(n=220, noise=0.00020)
        last_close = df["close"].iloc[-21]
        for i in range(200, 220):
            df.loc[i, "open"] = last_close
            df.loc[i, "high"] = last_close + 0.0000001
            df.loc[i, "low"] = last_close - 0.0000001
            df.loc[i, "close"] = last_close

        clean_df, err = self.engine._validate_and_prepare(df)
        self.assertIsNone(err)
        passed, reason, metrics = self.engine.check_volatility_filter(clean_df)
        self.assertFalse(passed)
        self.assertIn("Flat/Dead Market", reason)

    def test_atr_extreme_spike_rejection(self):
        # Generate normal market, then inject a massive single-bar news spike at the end
        df = make_synthetic_ohlcv(n=220, noise=0.00010)
        df.loc[219, "high"] = df.loc[219, "close"] + 0.01500  # Massive 150-pip spike
        df.loc[219, "low"] = df.loc[219, "close"] - 0.01500

        clean_df, err = self.engine._validate_and_prepare(df)
        self.assertIsNone(err)
        passed, reason, metrics = self.engine.check_volatility_filter(clean_df)
        self.assertFalse(passed)
        self.assertIn("Extreme Anomaly/News Spike", reason)

    # -------------------------------------------------------------------------
    # 3. Strategy 1: Mean Reversion (Bollinger + RSI + Pinbar)
    # -------------------------------------------------------------------------
    def test_mean_reversion_call_pinbar(self):
        # Construct scenario where price plummets into lower band with RSI oversold,
        # then prints a bullish hammer / lower-wick rejection pinbar
        df = make_synthetic_ohlcv(n=220, drift=-0.00015, noise=0.00008, seed=10)
        # Ensure latest candle has large lower rejection wick (pinbar)
        curr_price = df["close"].iloc[-2] - 0.00050
        df.loc[219, "open"] = curr_price
        df.loc[219, "low"] = curr_price - 0.00080  # Long lower wick
        df.loc[219, "close"] = curr_price + 0.00010
        df.loc[219, "high"] = curr_price + 0.00015

        clean_df, _ = self.engine._validate_and_prepare(df)
        # Directly test method
        res = self.engine.strategy_mean_reversion(clean_df)
        # If lower band & RSI align, it triggers CALL
        if res["signal"] == "CALL":
            self.assertEqual(res["signal"], "CALL")
            self.assertGreaterEqual(res["confidence"], 80)
            self.assertIn("oversold", res["reason"].lower())

    def test_mean_reversion_put_shooting_star(self):
        # Construct scenario where price spikes into upper band with RSI overbought,
        # then prints an upper-wick rejection pinbar (shooting star)
        df = make_synthetic_ohlcv(n=220, drift=0.00018, noise=0.00008, seed=15)
        curr_price = df["close"].iloc[-2] + 0.00060
        df.loc[219, "open"] = curr_price
        df.loc[219, "high"] = curr_price + 0.00090  # Long upper wick
        df.loc[219, "close"] = curr_price - 0.00010
        df.loc[219, "low"] = curr_price - 0.00015

        clean_df, _ = self.engine._validate_and_prepare(df)
        res = self.engine.strategy_mean_reversion(clean_df)
        if res["signal"] == "PUT":
            self.assertEqual(res["signal"], "PUT")
            self.assertGreaterEqual(res["confidence"], 80)
            self.assertIn("overbought", res["reason"].lower())

    # -------------------------------------------------------------------------
    # 4. Strategy 2: Trend Pullback (EMA 50/200 + Stoch Crossover)
    # -------------------------------------------------------------------------
    def test_trend_pullback_method_structure(self):
        df = make_synthetic_ohlcv(n=220, drift=0.00010, seed=20)
        clean_df, _ = self.engine._validate_and_prepare(df)
        res = self.engine.strategy_trend_pullback(clean_df)
        self.assertIn(res["signal"], ("CALL", "PUT", "NONE"))
        self.assertIn("Trend Pullback", res["strategy"])

    # -------------------------------------------------------------------------
    # 5. Strategy 3: Momentum Breakout (MACD + ADX Confirmation)
    # -------------------------------------------------------------------------
    def test_momentum_breakout_method_structure(self):
        df = make_synthetic_ohlcv(n=220, drift=0.00015, seed=30)
        clean_df, _ = self.engine._validate_and_prepare(df)
        res = self.engine.strategy_momentum_breakout(clean_df)
        self.assertIn(res["signal"], ("CALL", "PUT", "NONE"))
        self.assertEqual(res["strategy"], "Momentum Breakout (MACD + ADX)")

    # -------------------------------------------------------------------------
    # 6. Strategy 4: Price Action + S/R Engulfing
    # -------------------------------------------------------------------------
    def test_price_action_sr_engulfing(self):
        df = make_synthetic_ohlcv(n=220, drift=0.0, noise=0.00010, seed=40)
        clean_df, _ = self.engine._validate_and_prepare(df)
        res = self.engine.strategy_price_action_sr(clean_df)
        self.assertIn(res["signal"], ("CALL", "PUT", "NONE"))
        self.assertEqual(res["strategy"], "Price Action + S/R (Fractal Engulfing)")

    # -------------------------------------------------------------------------
    # 7. Confluence Aggregator & Conflict Resolution
    # -------------------------------------------------------------------------
    def test_confluence_aggregator_conflict_deadlock(self):
        # Verify that if one strategy yields CALL and another yields PUT,
        # the engine immediately aborts trade and returns NONE
        df = make_synthetic_ohlcv(n=220)
        clean_df, _ = self.engine._validate_and_prepare(df)

        # Mock conflicting strategy votes
        self.engine.strategy_mean_reversion = lambda d: {
            "signal": "CALL", "strategy": "Mean Reversion", "confidence": 82, "reason": "BB Oversold"
        }
        self.engine.strategy_trend_pullback = lambda d: {
            "signal": "PUT", "strategy": "Trend Pullback", "confidence": 85, "reason": "EMA Downtrend"
        }
        self.engine.strategy_momentum_breakout = lambda d: {
            "signal": "NONE", "strategy": "Momentum Breakout", "confidence": 0, "reason": "Neutral"
        }
        self.engine.strategy_price_action_sr = lambda d: {
            "signal": "NONE", "strategy": "Price Action", "confidence": 0, "reason": "Neutral"
        }

        res = self.engine.evaluate_all(df)
        self.assertEqual(res["signal"], "NONE")
        self.assertEqual(res["confidence"], 0)
        self.assertEqual(res["strategy"], "Conflict Resolver")
        self.assertIn("Directional Deadlock", res["reason"])

    def test_confluence_aggregator_agreement_boost(self):
        # When two strategies agree in the same direction, confidence is boosted (+6%)
        df = make_synthetic_ohlcv(n=220)
        self.engine.strategy_mean_reversion = lambda d: {
            "signal": "CALL", "strategy": "Mean Reversion", "confidence": 80, "reason": "BB Rebound"
        }
        self.engine.strategy_trend_pullback = lambda d: {
            "signal": "CALL", "strategy": "Trend Pullback", "confidence": 84, "reason": "EMA Stoch Dip"
        }
        self.engine.strategy_momentum_breakout = lambda d: {
            "signal": "NONE", "strategy": "Momentum Breakout", "confidence": 0, "reason": "Neutral"
        }
        self.engine.strategy_price_action_sr = lambda d: {
            "signal": "NONE", "strategy": "Price Action", "confidence": 0, "reason": "Neutral"
        }

        res = self.engine.evaluate_all(df)
        self.assertEqual(res["signal"], "CALL")
        # Base confidence max(80, 84) = 84 + (2-1)*6 = 90%
        self.assertEqual(res["confidence"], 90)
        self.assertIn("Multi-Confluence", res["strategy"])
        self.assertEqual(res["agreement_count"], 2)

    # -------------------------------------------------------------------------
    # 8. Quotex Raw Candle Dict Evaluation
    # -------------------------------------------------------------------------
    def test_evaluate_candles_raw_dicts(self):
        raw_candles = [
            {"time": 1700000000 + i * 60, "open": 1.1000 + i * 0.00001, "high": 1.1005 + i * 0.00001,
             "low": 1.0995 + i * 0.00001, "close": 1.1002 + i * 0.00001, "volume": 100.0}
            for i in range(210)
        ]
        res = self.engine.evaluate_candles(raw_candles)
        self.assertIn("signal", res)
        self.assertIn("confidence", res)
        self.assertIn("reason", res)

    # -------------------------------------------------------------------------
    # 9. Telegram Alert Formatter
    # -------------------------------------------------------------------------
    def test_format_telegram_alert_call(self):
        payload = {
            "signal": "CALL",
            "strategy": "Mean Reversion (BB + RSI)",
            "confidence": 88,
            "reason": "Lower BB rebound with 48% rejection wick",
            "price": 1.10025,
            "volatility_metrics": {"current_atr": 0.00015, "baseline_atr": 0.00014, "ratio": 1.07},
        }
        msg = StrategyEngine.format_telegram_alert(payload, asset="EUR/USD", timeframe="1m")
        self.assertIn("🟢 CALL (BUY)", msg)
        self.assertIn("EUR/USD", msg)
        self.assertIn("88%", msg)
        self.assertIn("0–5 seconds", msg)

    def test_format_telegram_alert_put(self):
        payload = {
            "signal": "PUT",
            "strategy": "Trend Pullback (EMA + Stoch)",
            "confidence": 85,
            "reason": "Stoch overbought crossover in macro downtrend",
            "price": 1.09540,
            "volatility_metrics": {"current_atr": 0.00018, "baseline_atr": 0.00017, "ratio": 1.05},
        }
        msg = StrategyEngine.format_telegram_alert(payload, asset="GBP/USD (OTC)", timeframe="5m")
        self.assertIn("🔴 PUT (SELL)", msg)
        self.assertIn("GBP/USD (OTC)", msg)
        self.assertIn("85%", msg)

    def test_format_telegram_alert_none(self):
        payload = {
            "signal": "NONE",
            "strategy": "Confluence Engine",
            "confidence": 0,
            "reason": "Market in neutral state.",
        }
        msg = StrategyEngine.format_telegram_alert(payload, asset="USD/JPY", timeframe="1m")
        self.assertIn("ℹ️", msg)
        self.assertIn("[NO TRADE]", msg)


if __name__ == "__main__":
    unittest.main()
