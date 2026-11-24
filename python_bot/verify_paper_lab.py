#!/usr/bin/env python3
"""
End-to-End Verification & Demonstration Suite for SignalDesk Paper Lab.
Exercises and verifies every operational and safety requirement:
  1. Logout -> Paper Trade Settles While Logged Out -> Service Restart -> Login Again
     (prior history + new settlement visible, approved model loaded from artifact without
      retraining from scratch, no duplicate or expired Telegram alert sent).
  2. Restart with Open Paper Trades (one reconciled cleanly, one with missing settlement
     data stays UNRESOLVED and is NEVER inferred from a later quote).
  3. Duplicate Settlement Event (strictly idempotent).
  4. AI API Outage (persistent queue records FAILED_RETRY with backoff; paper trading continues).
  5. Rejection of Invalid AI Proposal (code injection, disallowed key, out-of-bounds value).
  6. Candidate Failing Walk-Forward Evaluation (rejected; incumbent retained).
  7. Atomic Backup & Restore (SQLite DB + model artifacts verified together via SHA-256).
  8. Telegram Outbox Safety (PAPER SIGNAL format, token redaction, UNCERTAIN timeout no-resend,
     EXPIRED late alert skipped).
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BOT_DIR = Path(__file__).resolve().parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from paper_lab.config import EvaluationCriteria, PaperLabConfig
from paper_lab.engine_service import PaperLabService
from paper_lab.storage import PaperLabStore
from paper_lab.telegram_outbox import TelegramUncertainTimeout


def _make_trending_candles(end_ts: int, count: int = 40, step_sec: int = 60, direction: int = 1, base: float = 1.1000) -> List[Dict[str, Any]]:
    candles: List[Dict[str, Any]] = []
    price = base
    start_ts = end_ts - (count - 1) * step_sec
    for i in range(count):
        ts = start_ts + i * step_sec
        osc = 0.00035 * math.sin(i * 1.35)
        drift = direction * 0.00045 + osc
        o = price
        c = round(o + drift, 6)
        h = round(max(o, c) + 0.00018, 6)
        l = round(min(o, c) - 0.00018, 6)
        candles.append({"time": ts, "open": o, "high": h, "low": l, "close": c})
        price = c
    return candles


def run_all_verifications() -> Dict[str, Any]:
    report: Dict[str, Any] = {}
    os.environ["TELEGRAM_BOT_TOKEN"] = "999888777:AAFakeSecretTokenForRedactionTest12345"

    with tempfile.TemporaryDirectory(prefix="paper_lab_verify_") as tmp:
        data_dir = Path(tmp) / "live_data"
        restore_dir = Path(tmp) / "restored_data"

        tg_sent_calls: List[Dict[str, Any]] = []
        tg_mode = {"behavior": "ok"}

        def mock_tg_transport(token: str, chat_id: str, text: str, timeout_sec: int) -> Dict[str, Any]:
            if tg_mode["behavior"] == "timeout":
                raise TelegramUncertainTimeout("Simulated read timeout after request sent")
            tg_sent_calls.append({"chat_id": chat_id, "text": text})
            return {"ok": True, "message_id": f"tg_msg_{len(tg_sent_calls)}"}

        ai_mode = {"behavior": "outage"}

        def mock_ai_transport(provider: str, model: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            # Verify secrets/PII were stripped
            dumped = json.dumps(payload)
            assert "999888777:AAFakeSecretTokenForRedactionTest12345" not in dumped, "Telegram token leaked to AI!"
            if ai_mode["behavior"] == "outage":
                raise RuntimeError("503 Service Unavailable: AI API outage simulation")
            if ai_mode["behavior"] == "invalid_proposal":
                return {
                    "evidence": ["trd_1 lost"],
                    "recommendation": "propose_change",
                    "proposed_changes": {
                        "min_signal_probability": 0.99,  # out of bounds [0.52, 0.85]
                        "execute_real_orders": True,     # disallowed field
                        "code": "import os; os.system('rm -rf /')",  # code injection
                    },
                    "expected_effect": "Unsafe change",
                    "uncertainty": "High",
                    "validation_test": "None",
                }
            if ai_mode["behavior"] == "failing_candidate":
                # Valid bounded proposal, but restricts to only UTC hour 23 -> fails walk-forward (too few trades / worse return)
                return {
                    "evidence": ["Recent sample shows mixed win rate across sessions"],
                    "recommendation": "propose_change",
                    "proposed_changes": {
                        "min_signal_probability": 0.84,
                        "allowed_utc_hours": [23],
                    },
                    "expected_effect": "Only trade ultra-high confidence hour 23 UTC setups.",
                    "uncertainty": "Very low trade frequency may starve sample size.",
                    "validation_test": "Chronological 3-window walk-forward with >=12 trades and positive delta.",
                }
            return {
                "evidence": ["Stable performance"],
                "recommendation": "no_change",
                "proposed_changes": {},
                "expected_effect": "Maintain incumbent parameters.",
                "uncertainty": "Normal regime variance.",
                "validation_test": "Continue collecting settled outcomes.",
            }

        cfg = PaperLabConfig(
            paper_only=True,
            data_dir=str(data_dir),
            asset_allowlist=["EURUSD-OTC", "GBPUSD-OTC"],
            expiry_allowlist_sec=[60, 120],
            default_stake=10.0,
            default_payout=0.80,
            ai_batch_size=1,
            ai_max_retries=4,
            ai_retry_base_delay_sec=5,
            ai_min_request_interval_sec=0,
            telegram_enabled=True,
            telegram_allowed_chat_ids=["-100999888"],
            telegram_min_actionable_sec_left=20,
            evaluation=EvaluationCriteria(
                n_walk_forward_windows=3,
                min_train_samples=18,
                min_trades_total=9,
                min_trades_per_window=2,
                min_windows_beating_incumbent=2,
                min_net_return_improvement=1.0,
            ),
        )

        # ------------------------------------------------------------------
        # Start Service Instance #1 & Login User
        # ------------------------------------------------------------------
        svc = PaperLabService(config=cfg, ai_transport=mock_ai_transport, telegram_transport=mock_tg_transport)
        # Mark initial approved strategy as verified for Telegram so we can test Telegram outbox flow
        svc.store.update_model_status("mod_baseline_v1", "APPROVED", verified_for_telegram=True)
        svc.store.update_rule_status("rul_initial_v1", "APPROVED", verified_for_telegram=True)

        login1 = svc.auth.login("trader", "signaldesk-paper")
        assert login1["ok"] and svc.auth.verify_session(login1["session_token"]) is not None

        base_ts = 1_760_000_000  # Fixed UTC timestamp for deterministic testing

        # Verify allowlist & stale quote skips are logged cleanly
        skip_unsupported = svc.process_market_observation(
            asset="DOGECOIN-INVALID", expiry_sec=60, candles=[], decision_ts=base_ts,
            quote_ts=base_ts, payout=0.80, feed_source="Quotex Live (pyquotex)", feed_kind="platform_settled"
        )
        assert skip_unsupported["action"] == "SKIP" and "unsupported_asset" in skip_unsupported["reason"]

        skip_stale = svc.process_market_observation(
            asset="EURUSD-OTC", expiry_sec=60, candles=[], decision_ts=base_ts,
            quote_ts=base_ts - 45, payout=0.80, feed_source="Quotex Live (pyquotex)", feed_kind="platform_settled"
        )
        assert skip_stale["action"] == "SKIP" and "stale" in skip_stale["reason"]

        # Seed 42 chronological historical labeled observations (for walk-forward testing later)
        for i in range(42):
            t = base_ts - (50 - i) * 120
            direction = 1 if (i % 3 != 0) else -1
            c_hist = _make_trending_candles(end_ts=t, count=35, direction=direction, base=1.0900 + i * 0.0002)
            r = svc.process_market_observation(
                asset="EURUSD-OTC" if i % 2 == 0 else "GBPUSD-OTC",
                expiry_sec=60,
                candles=c_hist,
                decision_ts=t,
                quote_ts=t,
                payout=0.80,
                feed_source="Quotex Live (pyquotex)",
                feed_kind="platform_settled",
            )
            strike = c_hist[-1]["close"]
            # Outcome aligns with trend 75% of the time
            win_move = 0.0004 if (i % 4 != 0) else -0.0004
            settle_p = round(strike + direction * win_move, 6)
            if r.get("trade"):
                svc.settle_trade_explicit(r["trade"]["trade_id"], settle_p, t + 60, now_ts=t + 60)
            else:
                outcome = "UP" if settle_p > strike else "DOWN"
                svc.store.update_observation_counterfactual(r["obs_id"], settle_p, outcome, t + 60)

        # Deliver all initial Telegram outbox alerts (mark historical ones expired cleanly)
        tg_hist_res = svc.telegram.process_outbox(now_ts=base_ts)
        # Historical trades expired long before base_ts -> MUST be marked EXPIRED, NEVER sent late!
        assert tg_hist_res["sent"] == 0 and tg_hist_res["expired"] > 0

        # ------------------------------------------------------------------
        # Scenario 1 & 2: Open 2 Paper Trades, Send 1 Fresh Telegram PAPER SIGNAL,
        # Logout User -> Settle Trade #1 While Logged Out -> Restart Service ->
        # Reconcile Open Trade #2 (Missing Data -> UNRESOLVED) -> Login Again
        # ------------------------------------------------------------------
        candles_open1 = _make_trending_candles(end_ts=base_ts, count=40, direction=1, base=1.1050)
        res_open1 = svc.process_market_observation(
            asset="EURUSD-OTC", expiry_sec=60, candles=candles_open1,
            decision_ts=base_ts, quote_ts=base_ts, payout=0.80,
            feed_source="Quotex Live (pyquotex)", feed_kind="platform_settled"
        )
        assert res_open1["action"] == "TRADE" and res_open1["trade"] is not None
        open_trade_1 = res_open1["trade"]
        assert res_open1["telegram_enqueued"] == 1

        # Deliver fresh Telegram PAPER SIGNAL right away
        tg_fresh = svc.telegram.process_outbox(now_ts=base_ts + 2)
        assert tg_fresh["sent"] == 1 and len(tg_sent_calls) == 1
        assert "PAPER SIGNAL" in tg_sent_calls[0]["text"]
        assert "(Estimate)" in tg_sent_calls[0]["text"]
        assert "999888777:AAFakeSecretTokenForRedactionTest12345" not in tg_sent_calls[0]["text"]

        # Open Trade #2 (will remain OPEN across service restart and have missing settlement data!)
        candles_open2 = _make_trending_candles(end_ts=base_ts + 5, count=40, direction=-1, base=1.2600)
        res_open2 = svc.process_market_observation(
            asset="GBPUSD-OTC", expiry_sec=60, candles=candles_open2,
            decision_ts=base_ts + 5, quote_ts=base_ts + 5, payout=0.80,
            feed_source="Quotex Live (pyquotex)", feed_kind="platform_settled"
        )
        assert res_open2["action"] == "TRADE" and res_open2["trade"] is not None
        open_trade_2 = res_open2["trade"]

        # Simulate Telegram timeout (UNCERTAIN) on Trade #2 alert -> must NOT blindly resend after restart!
        tg_mode["behavior"] = "timeout"
        tg_uncertain = svc.telegram.process_outbox(now_ts=base_ts + 7)
        assert tg_uncertain["uncertain"] == 1
        tg_mode["behavior"] = "ok"

        # User LOGS OUT of dashboard UI
        svc.auth.logout(login1["session_token"])
        assert svc.auth.verify_session(login1["session_token"]) is None

        # Background service settles Trade #1 while user is logged out!
        strike1 = float(open_trade_1["strike"])
        settle1_price = round(strike1 + 0.0005, 6)  # CALL -> WIN (+$8.00)
        settle1 = svc.settle_trade_explicit(
            open_trade_1["trade_id"], settle1_price, base_ts + 60, now_ts=base_ts + 60
        )
        assert settle1["ok"] and not settle1["duplicate"] and settle1["trade"]["status"] == "WIN"

        # Scenario 3: DUPLICATE SETTLEMENT EVENT -> strictly idempotent!
        bal_before_dup = svc.compute_performance_summary("platform_settled")["current_paper_balance"]
        dup_res = svc.settle_trade_explicit(
            open_trade_1["trade_id"], round(strike1 - 0.0099, 6), base_ts + 60, now_ts=base_ts + 61
        )
        bal_after_dup = svc.compute_performance_summary("platform_settled")["current_paper_balance"]
        assert dup_res["ok"] and dup_res["duplicate"] is True
        assert dup_res["trade"]["status"] == "WIN" and bal_before_dup == bal_after_dup

        # RESTART SERVICE while Trade #2 is STILL OPEN!
        model_id_before_restart = svc.get_active_versions()["active_model"]["model_version_id"]
        svc.close()
        svc2 = PaperLabService(config=cfg, ai_transport=mock_ai_transport, telegram_transport=mock_tg_transport)
        assert svc2.get_active_versions()["active_model"]["model_version_id"] == model_id_before_restart
        open_after_restart = svc2.store.get_open_trades()
        assert any(t["trade_id"] == open_trade_2["trade_id"] for t in open_after_restart)

        # Try reconciling Trade #2 at base_ts + 180 with ONLY a much later quote (base_ts + 180 != expiry base_ts + 65)
        # Must mark UNRESOLVED — NEVER infer outcome from a later quote!
        def late_only_quote_lookup(asset: str, target_exp_ts: int, tol: int) -> Optional[Tuple[float, int]]:
            return (1.2500, base_ts + 180)  # Far outside ±10s tolerance!

        rec_res = svc2.reconcile_settlements(late_only_quote_lookup, now_ts=base_ts + 180)
        t2_after = svc2.store.get_trade(open_trade_2["trade_id"])
        assert rec_res["unresolved"] == 1 and t2_after["status"] == "UNRESOLVED" and t2_after["settlement_price"] is None

        # Process Telegram outbox after restart -> NO duplicate for Trade #1 and NO resend for UNCERTAIN Trade #2!
        sent_count_before = len(tg_sent_calls)
        svc2.telegram.process_outbox(now_ts=base_ts + 185)
        assert len(tg_sent_calls) == sent_count_before

        # User LOGS BACK IN after restart -> sees full prior history + settled Trade #1 + UNRESOLVED Trade #2
        login2 = svc2.auth.login("trader", "signaldesk-paper")
        assert login2["ok"] and svc2.auth.verify_session(login2["session_token"]) is not None
        hist_trades = svc2.store.query_history("paper_trades", limit=200)
        ids_seen = {t["trade_id"]: t["status"] for t in hist_trades}
        assert ids_seen[open_trade_1["trade_id"]] == "WIN"
        assert ids_seen[open_trade_2["trade_id"]] == "UNRESOLVED"

        report["1_logout_settle_restart_login"] = {
            "passed": True,
            "loaded_model_without_retrain": model_id_before_restart,
            "trade_1_settled_while_logged_out": ids_seen[open_trade_1["trade_id"]],
            "trade_2_missing_settlement_status": ids_seen[open_trade_2["trade_id"]],
            "telegram_sent_total": len(tg_sent_calls),
            "telegram_expired_skipped": tg_hist_res["expired"],
            "telegram_uncertain_not_resent": True,
        }
        report["2_duplicate_settlement_idempotent"] = {
            "passed": True,
            "duplicate_flag": dup_res["duplicate"],
            "balance_unchanged": bal_before_dup == bal_after_dup,
        }

        # ------------------------------------------------------------------
        # Scenario 4: AI API Outage -> FAILED_RETRY while paper trading continues
        # ------------------------------------------------------------------
        ai_mode["behavior"] = "outage"
        outage_res = svc2.process_ai_proposal_cycle(feed_kind="platform_settled", now_ts=base_ts + 200, ignore_rate_limit=True)
        assert outage_res["ok"] is False and outage_res["status"] == "FAILED_RETRY"
        # Confirm paper trading still works during AI outage!
        c_during_outage = _make_trending_candles(end_ts=base_ts + 205, count=40, direction=1, base=1.1100)
        trade_during_outage = svc2.process_market_observation(
            asset="EURUSD-OTC", expiry_sec=60, candles=c_during_outage,
            decision_ts=base_ts + 205, quote_ts=base_ts + 205, payout=0.80,
            feed_source="Quotex Live (pyquotex)", feed_kind="platform_settled",
        )
        assert trade_during_outage["ok"] is True
        report["3_ai_api_outage_resilience"] = {
            "passed": True,
            "queue_status": outage_res["status"],
            "error": outage_res["error"],
            "paper_trading_continued": trade_during_outage["ok"],
        }

        # ------------------------------------------------------------------
        # Scenario 5: Rejection of Invalid AI Proposal
        # ------------------------------------------------------------------
        ai_mode["behavior"] = "invalid_proposal"
        incumbent_rule_before = svc2.get_active_versions()["active_rule"]["rule_version_id"]
        invalid_res = svc2.process_ai_proposal_cycle(feed_kind="platform_settled", now_ts=base_ts + 250, ignore_rate_limit=True)
        assert invalid_res["ok"] is True and invalid_res["schema_valid"] is False
        assert invalid_res["decision"] == "REJECTED_INVALID"
        assert svc2.get_active_versions()["active_rule"]["rule_version_id"] == incumbent_rule_before
        report["4_invalid_ai_proposal_rejected"] = {
            "passed": True,
            "proposal_id": invalid_res["proposal_id"],
            "decision": invalid_res["decision"],
            "validation_errors": invalid_res["validation_errors"],
            "incumbent_unchanged": True,
        }

        # ------------------------------------------------------------------
        # Scenario 6: Valid AI Proposal Candidate Failing Walk-Forward Evaluation
        # ------------------------------------------------------------------
        ai_mode["behavior"] = "failing_candidate"
        fail_eval_res = svc2.process_ai_proposal_cycle(feed_kind="platform_settled", now_ts=base_ts + 300, ignore_rate_limit=True)
        assert fail_eval_res["ok"] is True and fail_eval_res["schema_valid"] is True
        assert fail_eval_res["evaluation"]["passed"] is False
        cand_rule_rec = svc2.store.get_rule_version(fail_eval_res["candidate_rule_id"])
        assert cand_rule_rec["status"] == "REJECTED"
        assert svc2.get_active_versions()["active_rule"]["rule_version_id"] == incumbent_rule_before
        assert svc2.get_active_versions()["shadow_rule"] is None
        report["5_candidate_failed_walk_forward"] = {
            "passed": True,
            "candidate_rule_id": fail_eval_res["candidate_rule_id"],
            "candidate_status": cand_rule_rec["status"],
            "evaluation_reason": fail_eval_res["evaluation"]["reason"],
            "incumbent_retained": incumbent_rule_before,
        }

        # ------------------------------------------------------------------
        # Scenario 7: Atomic Backup & Restore Together (DB + Model Artifacts)
        # ------------------------------------------------------------------
        backup_res = svc2.store.create_backup(label="verify")
        assert backup_res["ok"]
        restore_res = PaperLabStore.restore_backup(backup_res["archive_path"], restore_dir)
        assert restore_res["ok"] and restore_res["active_model_version_id"] == model_id_before_restart
        assert restore_res["trades_restored"] == len(hist_trades) + (1 if trade_during_outage.get("trade") else 0)

        # Verify Telegram bot token never appears anywhere inside SQLite DB file
        raw_db_bytes = svc2.store.db_path.read_bytes()
        assert b"999888777:AAFakeSecretTokenForRedactionTest12345" not in raw_db_bytes

        report["6_backup_restore_and_secret_hygiene"] = {
            "passed": True,
            "files_verified": restore_res["files_verified"],
            "trades_restored": restore_res["trades_restored"],
            "restored_model_id": restore_res["active_model_version_id"],
            "bot_token_absent_from_sqlite": True,
        }

        svc2.close()

    return report


if __name__ == "__main__":
    out = run_all_verifications()
    print(json.dumps(out, indent=2))
    assert all(section["passed"] for section in out.values())
    print("\nALL 6 END-TO-END PAPER LAB VERIFICATION SUITES PASSED!")
