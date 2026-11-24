"""Unit tests for the trading accuracy enhancements:
1. Candlestick Quality & Volume Filter (Doji, exhaustion wicks)
2. Bollinger Bandwidth & Regime Decoupling (ADX/BBW routing)
3. Multi-Timeframe (MTF) Trend Confirmation
4. Payout Floor enforcement (>=80%)
5. Dynamic Rolling Win-Rate Strategy Quarantining (<52%)
6. Strict 00-Second Bar-Close Timing & Entry Window Guard
"""

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import indicators as ind
import strategies as strat
from paper_lab.config import PaperLabConfig
from paper_lab.storage import PaperLabStore
from paper_lab.stubay import StubayWorkflow

NOW = 1_800_000_000  # Divisible by 15 and 60


class AccuracyEnhancementsTest(unittest.TestCase):
    def test_candlestick_quality_doji(self):
        # Doji: open and close almost identical, large high-low
        res = ind.candlestick_quality(1.1000, 1.1050, 1.0950, 1.1001, atr_val=0.0050)
        self.assertTrue(res["is_doji"])
        self.assertLess(res["body_ratio"], 0.15)
        self.assertLess(res["quality_score"], 0.60)

    def test_candlestick_quality_solid_body(self):
        # Solid bullish candle
        res = ind.candlestick_quality(1.1000, 1.1060, 1.0990, 1.1055, atr_val=0.0050)
        self.assertFalse(res["is_doji"])
        self.assertTrue(res["is_bullish"])
        self.assertGreaterEqual(res["body_ratio"], 0.70)
        self.assertGreaterEqual(res["quality_score"], 0.85)

    def test_candlestick_quality_exhaustion_wicks(self):
        # Upper exhaustion wick (bullish trap): opened at 1.1000, surged to 1.1080, slammed back to 1.1010
        res_upper = ind.candlestick_quality(1.1000, 1.1080, 1.0990, 1.1010, atr_val=0.0050)
        self.assertTrue(res_upper["exhaustion_bull"])
        self.assertGreater(res_upper["upper_wick_ratio"], 0.45)

        # Lower exhaustion wick (bearish trap): opened at 1.1050, dumped to 1.0970, rejected back to 1.1040
        res_lower = ind.candlestick_quality(1.1050, 1.1060, 1.0970, 1.1040, atr_val=0.0050)
        self.assertTrue(res_lower["exhaustion_bear"])
        self.assertGreater(res_lower["lower_wick_ratio"], 0.45)

    def test_bollinger_bandwidth(self):
        upper = [1.12, 1.14, 1.16]
        lower = [1.08, 1.06, 1.04]
        mid = [1.10, 1.10, 1.10]
        bbw = ind.bollinger_bandwidth(upper, lower, mid)
        self.assertEqual(len(bbw), 3)
        self.assertAlmostEqual(bbw[0], (1.12 - 1.08) / 1.10, places=5)
        self.assertAlmostEqual(bbw[2], (1.16 - 1.04) / 1.10, places=5)

    def test_higher_timeframe_trend_synthesis(self):
        # 30 bars of an uptrend
        closes = [1.0 + i * 0.001 for i in range(30)]
        highs = [c + 0.0005 for c in closes]
        lows = [c - 0.0005 for c in closes]
        mtf = ind.higher_timeframe_trend(closes, highs, lows, aggregate_period=5)
        self.assertEqual(mtf["mtf_bars_count"], 6)
        self.assertEqual(mtf["mtf_trend"], "BULLISH")
        self.assertEqual(mtf["mtf_bias"], "CALL")
        self.assertGreater(mtf["mtf_slope"], 0.0)

    def test_candle_entry_timing_guard(self):
        base_ts = NOW  # Divisible by 60 -> exactly :00
        # At :02 seconds into the candle -> optimal open
        res_open = ind.check_candle_entry_window(base_ts + 2, 60, 5)
        self.assertTrue(res_open["is_optimal_open"])
        self.assertTrue(res_open["actionable"])
        self.assertEqual(res_open["timing_status"], "OPTIMAL_OPEN")

        # At :25 seconds into the candle -> mid-candle noise
        res_mid = ind.check_candle_entry_window(base_ts + 25, 60, 5)
        self.assertFalse(res_mid["is_optimal_open"])
        self.assertFalse(res_mid["actionable"])
        self.assertEqual(res_mid["timing_status"], "MID_CANDLE_NOISE")

        # At :58 seconds into the candle -> pre-close setup
        res_close = ind.check_candle_entry_window(base_ts + 58, 60, 5)
        self.assertTrue(res_close["is_approaching_close"])
        self.assertTrue(res_close["actionable"])
        self.assertEqual(res_close["timing_status"], "PRE_CLOSE_SETUP")

    def test_regime_routing_metadata_and_quarantining(self):
        # Create 40 synthetic candles
        candles = []
        for i in range(40):
            candles.append({
                "time": NOW + i * 60,
                "open": 1.1000 + i * 0.0005,
                "high": 1.1010 + i * 0.0005,
                "low": 1.0995 + i * 0.0005,
                "close": 1.1008 + i * 0.0005,
                "volume": 100.0,
            })
        # Evaluate with quarantined strategy
        cfg = {"quarantined_strategies": ["rsi_bollinger"]}
        eval_res = strat.evaluate_all_strategies(candles, cfg, "1m")
        self.assertIn("market_regime", eval_res)
        self.assertIn("candlestick_quality", eval_res)
        self.assertIn("mtf_confirmation", eval_res)
        self.assertIn("entry_timing_guard", eval_res)
        # Quarantined strategy must have 0.0 weight
        for v in eval_res["votes"]:
            if v["id"] == "rsi_bollinger":
                self.assertEqual(v["weight"], 0.0)

    def test_payout_floor_enforcement_in_stubay(self):
        tmp = TemporaryDirectory()
        try:
            store = PaperLabStore(tmp.name)
            service = Mock()
            service.store = store
            config = PaperLabConfig(data_dir=tmp.name, min_payout_floor_pct=80.0, asset_allowlist=["EURUSD-OTC"])
            service.config = config
            workflow = StubayWorkflow(service)

            # Mock feed with 75% payout (below 80% floor)
            def low_payout_feed(**_kwargs):
                return {
                    "candles": [
                        {"time": NOW - 15 * (60 - i), "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.10}
                        for i in range(61)
                    ],
                    "is_live": True,
                    "payout": 75.0,
                }

            with patch("paper_lab.stubay.utc_now_ts", return_value=NOW):
                workflow.scan(NOW, low_payout_feed)

            # Check that asset was recorded as SKIPPED due to payout floor
            with store._lock:
                row = store._conn.execute(
                    "SELECT status, plan_json FROM stubay_memory WHERE bucket = ?", (NOW,)
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "SKIPPED")
                self.assertIn("below the 80% profitability floor", row[1])
        finally:
            store.close()
            tmp.cleanup()

    def test_strategy_rolling_win_rate_quarantine(self):
        tmp = TemporaryDirectory()
        try:
            store = PaperLabStore(tmp.name)
            store._conn.execute("PRAGMA foreign_keys = OFF;")
            # Insert trades with PRAGMA foreign_keys = OFF to test rolling win-rate logic in isolation
            with store.transaction() as conn:
                for i in range(10):
                    status_a = "WIN" if i < 3 else "LOSS"
                    conn.execute(
                        """
                        INSERT INTO paper_trades(
                            trade_id, signal_id, obs_id, asset, expiry_sec, decision_ts,
                            decision_utc, strike, direction, expiry_ts, expiry_utc,
                            estimated_prob, stake, payout, fee_rate, tie_rule,
                            model_version_id, rule_version_id, feed_kind, is_shadow,
                            status, settled_ts, settled_utc, net_result
                        ) VALUES (?, ?, ?, 'EURUSD-OTC', 60, ?, '2026-01-01', 1.1, 'CALL',
                                  ?, '2026-01-01', 0.6, 10.0, 0.85, 0.0, 'refund',
                                  'strat_a', 'r1', 'proxy', 0, ?, ?, '2026-01-01', 8.5)
                        """,
                        (f"trd_a_{i}", f"sig_a_{i}", f"obs_a_{i}", NOW + i * 60, NOW + (i + 1) * 60, status_a, NOW + (i + 1) * 60),
                    )
                for i in range(10):
                    status_b = "WIN" if i < 8 else "LOSS"
                    conn.execute(
                        """
                        INSERT INTO paper_trades(
                            trade_id, signal_id, obs_id, asset, expiry_sec, decision_ts,
                            decision_utc, strike, direction, expiry_ts, expiry_utc,
                            estimated_prob, stake, payout, fee_rate, tie_rule,
                            model_version_id, rule_version_id, feed_kind, is_shadow,
                            status, settled_ts, settled_utc, net_result
                        ) VALUES (?, ?, ?, 'EURUSD-OTC', 60, ?, '2026-01-01', 1.1, 'CALL',
                                  ?, '2026-01-01', 0.6, 10.0, 0.85, 0.0, 'refund',
                                  'strat_b', 'r1', 'proxy', 0, ?, ?, '2026-01-01', 8.5)
                        """,
                        (f"trd_b_{i}", f"sig_b_{i}", f"obs_b_{i}", NOW + i * 60, NOW + (i + 1) * 60, status_b, NOW + (i + 1) * 60),
                    )

            stats = store.get_strategy_rolling_win_rates(min_trades=5, lookback=50)
            self.assertIn("strat_a", stats)
            self.assertIn("strat_b", stats)
            self.assertTrue(stats["strat_a"]["is_quarantined"])
            self.assertLess(stats["strat_a"]["win_rate"], 0.52)
            self.assertFalse(stats["strat_b"]["is_quarantined"])
            self.assertGreaterEqual(stats["strat_b"]["win_rate"], 0.80)
        finally:
            store.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
