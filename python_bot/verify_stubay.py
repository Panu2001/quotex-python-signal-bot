"""Offline regression checks for the Stubay Ai paper workflow."""

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from paper_lab import PaperLabService
from paper_lab.config import PaperLabConfig
from paper_lab.stubay import closed_candles, local_plan, validate_plan, validated_review
from paper_lab.telegram_outbox import default_telegram_http_transport


NOW = 1_800_000_000
ASSET = "EURUSD-OTC"


def feed(timestamp):
    return {
        "candles": [
            dict(time=timestamp - 15 * (60 - index), open=1.1, high=1.12, low=1.09, close=1.11)
            for index in range(61)
        ],
        "is_live": True,
        "payout": 85,
    }


def forecast(snapshot, _history, _threshold):
    entry = snapshot["allowed_entries"][0]
    return dict(signal="CALL", future_trend="Rising", reason="Higher lows", confidence=70,
                entry_ts=entry, expiry_ts=entry + 120)


class StubayChecks(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.sent = []
        config = PaperLabConfig(data_dir=folder.name, asset_allowlist=[ASSET],
                                telegram_enabled=True, telegram_allowed_chat_ids=["100"])
        self.service = PaperLabService(config=config, telegram_transport=self.transport)
        self.addCleanup(self.service.close)

    def transport(self, *args):
        self.sent.append(args)
        return {"ok": True, "message_id": "test"}

    def scan(self, timestamp=NOW, handler=forecast):
        with patch("paper_lab.stubay.utc_now_ts", return_value=timestamp), \
             patch("paper_lab.stubay.local_plan", side_effect=handler):
            self.service.stubay.scan(timestamp, lambda **_kwargs: feed(timestamp))

    def test_local_model_skips_insufficient_data_and_expired_plan(self):
        series = feed(NOW)["candles"][:-1]
        snapshot = {"allowed_entries": [NOW + 120], "allowed_expiries": [120], "candles": series}
        self.assertEqual(local_plan(snapshot, series, 0.6)["signal"], "WAIT")
        with self.assertRaises(ValueError):
            validate_plan(forecast(snapshot, [], 0.6), snapshot, NOW + 120, 5)

    def test_volatile_setup_blocks_the_baseline(self):
        history = feed(NOW)["candles"][:-1]
        # Wicks with no body must be rejected even without higher-timeframe history.
        for bar in history:
            bar["open"] = bar["close"]
        snapshot = {"allowed_entries": [NOW + 120], "allowed_expiries": [120], "candles": history}
        with patch("paper_lab.stubay.ProbabilityModel.fit_logistic_regression") as fit:
            result = local_plan(snapshot, history, 0.60)
        self.assertEqual(result["signal"], "WAIT")
        self.assertEqual(result["regime"], "volatile")
        self.assertIn("blocked", result["reason"])
        fit.assert_not_called()

    def test_risk_reserves_pending_stake_and_fees(self):
        from paper_lab.stubay_policy import paper_risk

        self.scan()
        with self.service.store.transaction() as conn:
            # The scheduled stake used to be computed from the next requested stake.
            conn.execute("UPDATE stubay_memory SET plan_json = json_set(plan_json, '$.paper_stake', 45)")
            stake, reason = paper_risk(conn, "stubay_groq_v1", NOW, 1000, 5, 0.01)
        self.assertEqual(stake, 0)
        self.assertIn("exposure limit", reason)

    def test_review_requires_later_improving_validation(self):
        from types import SimpleNamespace

        model = SimpleNamespace(predict_up_prob=lambda features: features["up"])
        snapshot = {"payout": 85, "review_rules": [{"rule": "require_stronger_call", "created_ts": 100}]}
        rows = [
            {"decision_ts": 101 + index, "features": {"up": 0.61 if index < 8 else 0.70},
             "outcome": "DOWN" if index < 8 else "UP"}
            for index in range(24)
        ]
        self.assertEqual(validated_review(snapshot, model, rows, 0.60), "CALL")
        self.assertIsNone(validated_review(snapshot, model, rows[:-1], 0.60))
        for row in rows:
            row["outcome"] = "UP"
        self.assertIsNone(validated_review(snapshot, model, rows, 0.60))
        snapshot["review_rules"][0]["created_ts"] = 130
        self.assertIsNone(validated_review(snapshot, model, rows, 0.60))

    def test_latest_closed_bar_does_not_need_a_successor(self):
        candles = closed_candles(feed(NOW - 15), NOW + 10)
        self.assertEqual(candles[-1]["time"], NOW - 15)
        candles = closed_candles(feed(NOW), NOW + 10)
        self.assertEqual(candles[-1]["time"], NOW - 15)

    def test_scan_refreshes_stale_candles_before_deciding(self):
        with patch("paper_lab.stubay.utc_now_ts", return_value=NOW + 10), \
             patch("paper_lab.stubay.local_plan", side_effect=forecast) as plan, \
             patch.object(self.service.stubay, "_chart", return_value=b"PNG"):
            def get_feed(**options):
                return feed(NOW - 15 if options.get("force_refresh") else NOW - 30)
            market = Mock(side_effect=get_feed)
            self.service.stubay.scan(NOW, market)
            self.assertEqual(market.call_count, 2)
            self.assertTrue(market.call_args.kwargs["force_refresh"])
            self.assertTrue(all(call.kwargs["priority"] for call in market.call_args_list))
            self.assertTrue(all(call.kwargs["timeframe"] == "15s" for call in market.call_args_list))
            self.assertEqual(plan.call_args.args[0]["as_of"], NOW)

    def test_collection_rotates_without_generating_paper_signals(self):
        workflow = self.service.stubay
        self.service.config.asset_allowlist = [ASSET, "GBPUSD-OTC"]
        market = Mock()
        workflow.collect_candles(NOW, False, market)
        self.assertFalse(market.called)
        workflow.collect_candles(NOW, True, market)
        workflow._candle_refresh_thread.join(2)
        workflow.collect_candles(NOW + 1, True, market)
        self.assertEqual(market.call_count, 1)
        workflow.collect_candles(NOW + 5, True, market)
        workflow._candle_refresh_thread.join(2)
        self.assertEqual([call.kwargs["symbol"] for call in market.call_args_list], [ASSET, "GBPUSD-OTC"])
        self.assertTrue(all(call.kwargs["timeframe"] == "15s" for call in market.call_args_list))
        self.assertEqual(self.service.get_dashboard_overview()["stubay"]["memory_count"], 0)
        self.assertEqual(self.service.store.query_history("telegram_outbox"), [])

    def test_disconnected_worker_and_duplicate_scan_preserve_results(self):
        self.scan()
        workflow = self.service.stubay
        before = self.service.store.get_state("stubay_status")
        self.assertEqual(workflow.schedule(NOW, False, None), "Waiting for Quotex login.")
        self.assertEqual(self.service.store.get_state("stubay_status"), before)
        self.scan()
        self.assertIn("already recorded 1 local decisions", self.service.store.get_state("stubay_status"))

    def test_configured_minimum_blocks_signal_and_accepts_equality(self):
        self.service.stubay.set_minimum_confidence(71)
        self.scan()
        latest = self.service.get_dashboard_overview()["stubay"]
        self.assertEqual(latest["minimum_confidence"], 71)
        self.assertEqual(latest["latest"][0]["status"], "WAIT")
        self.assertEqual(self.service.store.query_history("telegram_outbox"), [])
        self.service.stubay.set_minimum_confidence(70)
        self.scan(NOW + 600)
        self.assertEqual(self.service.get_dashboard_overview()["stubay"]["latest"][0]["status"], "SCHEDULED")

    def test_one_trade_and_alert_then_settlement_memory(self):
        self.scan()
        self.scan()
        with patch("paper_lab.telegram_outbox.get_telegram_bot_token", return_value="mock"):
            self.assertEqual(self.service.telegram.process_outbox(NOW)["sent"], 1)
        caption = self.sent[0][2]
        self.assertEqual([line.split("</b>")[0][3:] for line in caption.splitlines()],
                         ["Asset", "Date", "Entry time", "Expiry time", "Time zone", "Signal", "Reason"])
        self.assertIn("Higher lows", caption)
        self.assertRegex(caption, r"Entry time</b>: [0-9]{2}:[0-9]{2}\n")
        self.assertRegex(self.sent[0][2], r"[+-]\d{2}:\d{2}")
        lookup = lambda _asset, timestamp, _tolerance: (1.1, timestamp)
        self.assertEqual(self.service.stubay.open_due(NOW + 120, lookup), 1)
        self.assertEqual(self.service.stubay.open_due(NOW + 120, lookup), 0)
        outcome = self.service.reconcile_settlements(
            lambda _asset, timestamp, _tolerance: (1.12, timestamp), now_ts=NOW + 240
        )
        self.assertEqual(outcome["settled"], 1)
        self.assertEqual(self.service.get_dashboard_overview()["stubay"]["latest"][0]["status"], "WIN")

    def test_telegram_does_not_send_with_less_than_a_minute_to_act(self):
        self.scan()
        with patch("paper_lab.telegram_outbox.get_telegram_bot_token", return_value="mock"):
            self.assertEqual(self.service.telegram.process_outbox(NOW + 61)["expired"], 1)
        self.assertEqual(self.sent, [])

    def test_late_entry_is_never_backfilled(self):
        self.scan()
        def unexpected_quote(*_args):
            self.fail("Must not even fetch a strike after the entry deadline")
        self.assertEqual(self.service.stubay.open_due(NOW + 150, unexpected_quote), 0)
        self.assertEqual(self.service.get_dashboard_overview()["stubay"]["latest"][0]["status"], "MISSED_ENTRY")

    def test_pause_and_exact_entry_and_photo_transport(self):
        self.scan()
        self.service.config.auto_loop_enabled = False
        self.assertIn("paused", self.service.stubay.schedule(NOW + 1, True, None))
        self.assertEqual(self.service.stubay.open_due(NOW + 120, lambda _a, t, _z: (1.1, t - 5)), 0)
        self.assertEqual(self.service.stubay.open_due(NOW + 125, lambda _a, t, _z: (1.1, t)), 1)
        trade = self.service.store.get_open_trades()[0]
        premature = self.service.settle_trade_explicit(trade["trade_id"], 1.12, NOW + 240, NOW + 230)
        self.assertFalse(premature["ok"])
        with patch("paper_lab.telegram_outbox.send_telegram_photo",
                   return_value={"ok": True, "result": {"message_id": 17}}) as photo:
            self.assertEqual(default_telegram_http_transport("mock", "100", "caption", image=b"PNG")["message_id"], "17")
            self.assertEqual(photo.call_args.args[2], b"PNG")

    def test_old_quota_cannot_pause_local_model_and_history_survives(self):
        self.scan()
        self.service.store.set_state("stubay_quota_until", str(NOW + 900))
        with patch.object(self.service.stubay, "scan") as scan:
            self.assertIn("locally", self.service.stubay.schedule(NOW + 1, True, None))
            self.service.stubay.thread.join(3)
            scan.assert_called_once()
        self.assertEqual(self.service.get_dashboard_overview()["stubay"]["memory_count"], 1)


if __name__ == "__main__":
    unittest.main()
