"""
PaperLabService — End-to-End Persistent Paper-Trading & Learning Coordinator.
- Strict asset/expiry allowlist & paper-only execution.
- Restart recovery without retraining from scratch or duplicate settlements.
- Parallel shadow paper testing, automatic walk-forward + shadow promotion, and rollback.
- Integrates SQLite store, AuthManager, ProbabilityModel, AIFeedbackManager, TelegramOutboxManager.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from .ai_feedback import AIFeedbackManager
from .auth import AuthManager
from .config import DEFAULT_RULE_PARAMS, PaperLabConfig, compute_break_even_probability, default_backup_dir
from .modeling import (
    FEATURE_NAMES,
    ProbabilityModel,
    decide_on_observation,
    evaluate_walk_forward,
    extract_features,
)
from .storage import PaperLabStore, make_stable_id, ts_to_iso, utc_now_ts
from .telegram_outbox import TelegramOutboxManager
from .stubay import RECIPE, StubayWorkflow


class PaperLabService:
    """Single-machine persistent paper-trading engine."""

    def __init__(
        self,
        config: Optional[PaperLabConfig] = None,
        ai_transport: Optional[Callable[[str, str, Dict[str, Any]], Dict[str, Any]]] = None,
        telegram_transport: Optional[Callable[[str, str, str, int], Dict[str, Any]]] = None,
    ):
        self.config = config or PaperLabConfig()
        self.config.paper_only = True  # Hard invariant
        self.store = PaperLabStore(self.config.resolved_data_dir())
        self.auth = AuthManager(self.store)
        self.ai = AIFeedbackManager(self.store, self.config, transport=ai_transport)
        self.telegram = TelegramOutboxManager(self.store, self.config, transport=telegram_transport)

        self.cycle_lock = threading.Lock()
        self._backup_lock = threading.Lock()
        self._backup_thread: Optional[threading.Thread] = None
        self._next_backup_check = 0
        from .stubay_review import StubayReview

        self.stubay = StubayWorkflow(self)
        self.stubay_review = StubayReview(self.store)
        self._active_model: ProbabilityModel = ProbabilityModel.baseline()
        self._shadow_model: Optional[ProbabilityModel] = None
        self._bootstrap_or_resume()

    def close(self) -> None:
        self.stubay_review.closed.set()
        if self._backup_thread is not None:
            self._backup_thread.join()
        self.store.close()

    def schedule_backup(self, *, force: bool = False) -> None:
        """Snapshot without delaying the scan or settlement worker."""
        now = utc_now_ts()
        with self._backup_lock:
            if self._backup_thread is not None and self._backup_thread.is_alive():
                return
            if not force and now < self._next_backup_check:
                return
            self._next_backup_check = now + 300
            last = int(self.store.get_state("stubay_last_automatic_backup", "0") or "0")
            if not force and now - last < 3600:
                return
            with self.store._lock:
                has_memory = self.store._conn.execute("SELECT 1 FROM stubay_memory LIMIT 1").fetchone()
            if not has_memory:
                return
            self._backup_thread = threading.Thread(target=self._save_automatic_backup, daemon=True)
            self._backup_thread.start()

    def _save_automatic_backup(self) -> None:
        try:
            self.store.create_backup("stubay", destination=default_backup_dir())
            self.store.set_state("stubay_last_automatic_backup", str(utc_now_ts()))
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            self.store.log_event("Backup", "ERROR", f"Stubay automatic backup failed: {exc}")

    # ------------------------------------------------------------------
    # Startup & Restart Recovery (NEVER retrains from scratch on restart)
    # ------------------------------------------------------------------
    def _bootstrap_or_resume(self) -> None:
        active_model_id = self.store.get_state("active_model_version_id")
        active_rule_id = self.store.get_state("active_rule_version_id")

        if not active_model_id:
            # First-ever cold start: persist baseline model artifact
            baseline = ProbabilityModel.baseline()
            active_model_id = "mod_baseline_v1"
            self.store.save_model_version(
                model_version_id=active_model_id,
                model_type=baseline.model_type,
                artifact_payload=baseline.to_dict(),
                trained_on_count=0,
                feature_names=FEATURE_NAMES,
                metrics={"note": "initial_momentum_baseline"},
                status="APPROVED",
                verified_for_telegram=False,
            )
            self.store.set_state("active_model_version_id", active_model_id)
            self._active_model = baseline
            self.store.log_event("Lifecycle", "INFO", f"Initialized cold-start baseline model {active_model_id}")
        else:
            # Resume exact last approved model from durable disk artifact (SHA-256 verified)
            artifact = self.store.load_model_artifact(active_model_id)
            self._active_model = ProbabilityModel.from_dict(artifact)
            self.store.log_event(
                "Lifecycle",
                "INFO",
                f"Resumed approved model {active_model_id} from verified artifact without retraining.",
            )

        if not active_rule_id:
            active_rule_id = "rul_initial_v1"
            self.store.save_rule_version(
                rule_version_id=active_rule_id,
                params=self.config.initial_rules,
                source="INITIAL",
                status="APPROVED",
                verified_for_telegram=False,
            )
            self.store.set_state("active_rule_version_id", active_rule_id)

        shadow_model_id = self.store.get_state("shadow_model_version_id")
        if shadow_model_id:
            try:
                self._shadow_model = ProbabilityModel.from_dict(
                    self.store.load_model_artifact(shadow_model_id)
                )
            except Exception as exc:
                self.store.log_event("Lifecycle", "WARN", f"Could not load shadow model {shadow_model_id}: {exc}")
                self._shadow_model = None

        if self.store.get_state("strategy_paused") is None:
            self.store.set_state("strategy_paused", "0")

    def get_active_versions(self) -> Dict[str, Any]:
        active_model_id = self.store.get_state("active_model_version_id") or ""
        active_rule_id = self.store.get_state("active_rule_version_id") or ""
        shadow_model_id = self.store.get_state("shadow_model_version_id") or ""
        shadow_rule_id = self.store.get_state("shadow_rule_version_id") or ""
        prior_model_id = self.store.get_state("prior_model_version_id") or ""
        prior_rule_id = self.store.get_state("prior_rule_version_id") or ""
        paused = self.store.get_state("strategy_paused", "0") == "1"
        pause_reason = self.store.get_state("pause_reason", "") or ""
        return {
            "active_model": self.store.get_model_version(active_model_id),
            "active_rule": self.store.get_rule_version(active_rule_id),
            "shadow_model": self.store.get_model_version(shadow_model_id) if shadow_model_id else None,
            "shadow_rule": self.store.get_rule_version(shadow_rule_id) if shadow_rule_id else None,
            "prior_model_id": prior_model_id or None,
            "prior_rule_id": prior_rule_id or None,
            "strategy_paused": paused,
            "pause_reason": pause_reason,
        }

    # ------------------------------------------------------------------
    # Market Observation Ingestion, Skip Logging, Paper Trade & Shadow Execution
    # ------------------------------------------------------------------
    def process_market_observation(
        self,
        asset: str,
        expiry_sec: int,
        candles: List[Dict[str, Any]],
        decision_ts: int,
        quote_ts: int,
        payout: Optional[float],
        feed_source: str,
        feed_kind: str = "platform_settled",  # "platform_settled" or "proxy_simulation"
        stake: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Process one decision-time market observation:
        1. Enforce allowlist, freshness, and data completeness.
        2. Persist observation + features.
        3. Evaluate incumbent approved strategy (and shadow candidate if active).
        4. Record TRADE or SKIP; open paper trade if TRADE; enqueue Telegram alert if all 5 conditions hold.
        """
        sym = str(asset or "").strip().upper()
        exp_s = int(expiry_sec)
        now_ts = int(decision_ts)
        q_ts = int(quote_ts)
        f_kind = "platform_settled" if feed_kind == "platform_settled" else "proxy_simulation"
        versions = self.get_active_versions()
        active_model_rec = versions["active_model"]
        active_rule_rec = versions["active_rule"]
        active_model_id = active_model_rec["model_version_id"]
        active_rule_id = active_rule_rec["rule_version_id"]
        active_rules = active_rule_rec["params"]

        # Pre-validation checks (allowlist, freshness, payout, missing data)
        reject_reason: Optional[str] = None
        if sym not in self.config.asset_allowlist:
            reject_reason = f"unsupported_asset({sym})"
        elif exp_s not in self.config.expiry_allowlist_sec:
            reject_reason = f"unsupported_expiry({exp_s}s)"
        elif (now_ts - q_ts) > int(self.config.max_quote_age_sec) or q_ts > now_ts + 5:
            reject_reason = f"stale_or_invalid_quote(age={now_ts - q_ts}s)"
        elif payout is None or float(payout) <= 0 or float(payout) > 2.0:
            reject_reason = "missing_or_invalid_payout"

        if reject_reason:
            self.store.log_event(
                "MarketData",
                "INFO",
                f"Skipped {sym} {exp_s}s at {ts_to_iso(now_ts)}: {reject_reason}",
                {"asset": sym, "expiry_sec": exp_s, "reason": reject_reason},
                ts=now_ts,
            )
            return {"ok": True, "action": "SKIP", "reason": reject_reason}

        feats = extract_features(candles, now_ts)
        if feats is None:
            self.store.log_event(
                "MarketData",
                "INFO",
                f"Skipped {sym} {exp_s}s at {ts_to_iso(now_ts)}: insufficient_or_invalid_candles",
                ts=now_ts,
            )
            return {"ok": True, "action": "SKIP", "reason": "insufficient_or_invalid_candles"}

        decision_candles = [c for c in candles if int(c["time"]) <= now_ts]
        strike = float(decision_candles[-1]["close"])
        eff_payout = float(payout)
        eff_stake = float(stake if stake is not None else self.config.default_stake)

        obs_id, _ = self.store.record_observation(
            asset=sym,
            expiry_sec=exp_s,
            decision_ts=now_ts,
            quote_ts=q_ts,
            price=strike,
            payout=eff_payout,
            fee_rate=self.config.fee_rate,
            tie_rule=self.config.tie_rule,
            feed_source=feed_source,
            feed_kind=f_kind,
            features=feats,
        )

        # Evaluate incumbent strategy
        paused = versions["strategy_paused"]
        open_count = self.store.count_open_trades(is_shadow=False)
        max_open = int(active_rules.get("max_concurrent_open_trades", 3))

        action, direction, est_prob, be_prob, skip_reason = decide_on_observation(
            model=self._active_model,
            rule_params=active_rules,
            features=feats,
            decision_ts=now_ts,
            payout=eff_payout,
            fee_rate=self.config.fee_rate,
            tie_rule=self.config.tie_rule,
        )
        if paused and action == "TRADE":
            action = "SKIP"
            skip_reason = f"strategy_paused({versions['pause_reason'] or 'deterioration'})"
        elif action == "TRADE" and open_count >= max_open:
            action = "SKIP"
            skip_reason = f"max_concurrent_open_trades({open_count}/{max_open})"

        signal_id, _ = self.store.record_signal_or_skip(
            obs_id=obs_id,
            asset=sym,
            expiry_sec=exp_s,
            decision_ts=now_ts,
            direction=direction,
            estimated_prob=est_prob,
            break_even_prob=be_prob,
            payout=eff_payout,
            action=action,
            skip_reason=skip_reason,
            model_version_id=active_model_id,
            rule_version_id=active_rule_id,
            feed_kind=f_kind,
            is_shadow=False,
        )

        trade_rec: Optional[Dict[str, Any]] = None
        telegram_enqueued: List[Dict[str, Any]] = []
        telegram_gate_reason = "not_traded"

        if action == "TRADE":
            trade_rec, created = self.store.open_paper_trade(
                signal_id=signal_id,
                obs_id=obs_id,
                asset=sym,
                expiry_sec=exp_s,
                decision_ts=now_ts,
                strike=strike,
                direction=direction,
                estimated_prob=est_prob,
                stake=eff_stake,
                payout=eff_payout,
                fee_rate=self.config.fee_rate,
                tie_rule=self.config.tie_rule,
                model_version_id=active_model_id,
                rule_version_id=active_rule_id,
                feed_kind=f_kind,
                is_shadow=False,
            )
            if created:
                # Check strict 5-condition Telegram gate
                eligible, telegram_gate_reason = self.telegram.check_eligibility(
                    asset=sym,
                    expiry_sec=exp_s,
                    quote_age_sec=now_ts - q_ts,
                    expiry_ts=int(trade_rec["expiry_ts"]),
                    now_ts=now_ts,
                    payout=eff_payout,
                    estimated_prob=est_prob,
                    fee_rate=self.config.fee_rate,
                    tie_rule=self.config.tie_rule,
                    min_edge_margin=float(active_rules.get("min_edge_margin", 0.02)),
                    model_record=active_model_rec,
                    rule_record=active_rule_rec,
                    strategy_paused=paused,
                )
                if eligible:
                    telegram_enqueued = self.telegram.enqueue_paper_signal(
                        signal_id=signal_id,
                        asset=sym,
                        direction=direction,
                        strike=strike,
                        signal_ts=now_ts,
                        expiry_ts=int(trade_rec["expiry_ts"]),
                        payout=eff_payout,
                        estimated_prob=est_prob,
                        model_version_id=active_model_id,
                        rule_version_id=active_rule_id,
                        feed_kind=f_kind,
                        now_ts=now_ts,
                    )
        else:
            self.store.log_event(
                "Signal",
                "INFO",
                f"Skipped signal {sym} {exp_s}s ({direction} p={est_prob:.3f}): {skip_reason}",
                ts=now_ts,
            )

        # Parallel Shadow Paper Testing (if a candidate rule or model is in SHADOW state)
        shadow_trade_rec: Optional[Dict[str, Any]] = None
        shadow_rule_rec = versions["shadow_rule"]
        shadow_model_rec = versions["shadow_model"]
        if shadow_rule_rec or shadow_model_rec:
            s_model = self._shadow_model or self._active_model
            s_model_id = shadow_model_rec["model_version_id"] if shadow_model_rec else active_model_id
            s_rule_id = shadow_rule_rec["rule_version_id"] if shadow_rule_rec else active_rule_id
            s_rules = shadow_rule_rec["params"] if shadow_rule_rec else active_rules

            s_act, s_dir, s_prob, s_be, s_skip = decide_on_observation(
                model=s_model,
                rule_params=s_rules,
                features=feats,
                decision_ts=now_ts,
                payout=eff_payout,
                fee_rate=self.config.fee_rate,
                tie_rule=self.config.tie_rule,
            )
            s_sig_id, _ = self.store.record_signal_or_skip(
                obs_id=obs_id,
                asset=sym,
                expiry_sec=exp_s,
                decision_ts=now_ts,
                direction=s_dir,
                estimated_prob=s_prob,
                break_even_prob=s_be,
                payout=eff_payout,
                action=s_act,
                skip_reason=s_skip,
                model_version_id=s_model_id,
                rule_version_id=s_rule_id,
                feed_kind=f_kind,
                is_shadow=True,
            )
            if s_act == "TRADE":
                shadow_trade_rec, _ = self.store.open_paper_trade(
                    signal_id=s_sig_id,
                    obs_id=obs_id,
                    asset=sym,
                    expiry_sec=exp_s,
                    decision_ts=now_ts,
                    strike=strike,
                    direction=s_dir,
                    estimated_prob=s_prob,
                    stake=eff_stake,
                    payout=eff_payout,
                    fee_rate=self.config.fee_rate,
                    tie_rule=self.config.tie_rule,
                    model_version_id=s_model_id,
                    rule_version_id=s_rule_id,
                    feed_kind=f_kind,
                    is_shadow=True,
                )

        return {
            "ok": True,
            "obs_id": obs_id,
            "signal_id": signal_id,
            "action": action,
            "direction": direction,
            "estimated_prob": round(est_prob, 6),
            "break_even_prob": round(be_prob, 6),
            "skip_reason": skip_reason,
            "trade": trade_rec,
            "shadow_trade": shadow_trade_rec,
            "telegram_gate_reason": telegram_gate_reason,
            "telegram_enqueued": len(telegram_enqueued),
        }

    # ------------------------------------------------------------------
    # Settlement Reconciliation (Idempotent; NEVER infers from a later quote)
    # ------------------------------------------------------------------
    def settle_trade_explicit(
        self,
        trade_id: str,
        settlement_price: Optional[float],
        settlement_quote_ts: Optional[int],
        now_ts: Optional[int] = None,
        settlement_event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Settle a specific open trade if settlement_quote_ts matches expiry_ts within
        settlement_tolerance_sec. If settlement data is missing or far past expiry,
        marks the trade UNRESOLVED instead of inferring from a later quote.
        """
        now = now_ts if now_ts is not None else utc_now_ts()
        trade = self.store.get_trade(trade_id)
        if not trade:
            return {"ok": False, "error": f"Trade not found: {trade_id}"}
        if trade["status"] != "OPEN":
            return {"ok": True, "duplicate": True, "trade": trade}

        expiry_ts = int(trade["expiry_ts"])
        tol = int(self.config.settlement_tolerance_sec or 10)
        if now < expiry_ts:
            return {"ok": False, "error": "Paper trade has not reached expiry."}

        if settlement_price is None or settlement_quote_ts is None:
            res = self.store.settle_paper_trade(
                trade_id=trade_id,
                settlement_price=None,
                settled_ts=now,
                settlement_event_id=settlement_event_id,
                mark_unresolved=True,
            )
            self.store.log_event(
                "Settlement",
                "WARN",
                f"Marked trade {trade_id} UNRESOLVED: settlement data missing at expiry {ts_to_iso(expiry_ts)}.",
                ts=now,
            )
            return res

        if abs(int(settlement_quote_ts) - expiry_ts) > tol:
            # Strict rule: NEVER infer outcome from a later quote!
            res = self.store.settle_paper_trade(
                trade_id=trade_id,
                settlement_price=None,
                settled_ts=now,
                settlement_event_id=settlement_event_id,
                mark_unresolved=True,
            )
            self.store.log_event(
                "Settlement",
                "WARN",
                f"Marked trade {trade_id} UNRESOLVED: quote timestamp {ts_to_iso(int(settlement_quote_ts))} outside expiry tolerance (±{tol}s of {ts_to_iso(expiry_ts)}).",
                ts=now,
            )
            return res

        res = self.store.settle_paper_trade(
            trade_id=trade_id,
            settlement_price=float(settlement_price),
            settled_ts=int(settlement_quote_ts),
            outcome_known_ts=max(now, int(settlement_quote_ts)),
            settlement_event_id=settlement_event_id,
            mark_unresolved=False,
        )
        if res.get("ok") and not res.get("duplicate") and trade["rule_version_id"] != RECIPE:
            settled_trade = res["trade"]
            if not int(settled_trade.get("is_shadow", 0)):
                # Queue settled live paper trade for AI review & evaluate rollback thresholds
                versions = self.get_active_versions()
                metrics = self.compute_performance_summary(feed_kind=settled_trade["feed_kind"])
                if self.ai.transport is not None:
                    self.ai.enqueue_settled_trades_if_ready(
                        current_rules=versions["active_rule"]["params"],
                        summary_metrics=metrics,
                        now_ts=now,
                    )
                self._check_automatic_rollback(feed_kind=settled_trade["feed_kind"], now_ts=now)
            else:
                self._check_shadow_promotion(feed_kind=settled_trade["feed_kind"], now_ts=now)
        return res

    def reconcile_settlements(
        self,
        quote_lookup: Callable[[str, int, int], Optional[Tuple[float, int]]],
        now_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Reconcile all due OPEN paper trades AND counterfactual labels for skipped observations.
        quote_lookup(asset, target_expiry_ts, tolerance_sec) -> (settlement_price, quote_ts) or None.
        If settlement data is missing past tolerance, trade is marked UNRESOLVED (never inferred later).
        """
        now = now_ts if now_ts is not None else utc_now_ts()
        tol = int(self.config.settlement_tolerance_sec)

        due_trades = self.store.get_open_trades(due_before_ts=now)
        settled_cnt = 0
        unresolved_cnt = 0
        for tr in due_trades:
            exp_ts = int(tr["expiry_ts"])
            quote = quote_lookup(str(tr["asset"]), exp_ts, tol)
            if quote is not None:
                s_price, q_ts = quote
                res = self.settle_trade_explicit(
                    trade_id=str(tr["trade_id"]),
                    settlement_price=s_price,
                    settlement_quote_ts=q_ts,
                    now_ts=now,
                )
                if res.get("ok") and not res.get("duplicate"):
                    if res["trade"]["status"] == "UNRESOLVED":
                        unresolved_cnt += 1
                    else:
                        settled_cnt += 1
            elif (now - exp_ts) > tol:
                # Past tolerance window with no exact settlement data -> leave UNRESOLVED
                res = self.settle_trade_explicit(
                    trade_id=str(tr["trade_id"]),
                    settlement_price=None,
                    settlement_quote_ts=None,
                    now_ts=now,
                )
                if res.get("ok") and not res.get("duplicate"):
                    unresolved_cnt += 1

        # Backfill counterfactual settlement labels for skipped observations so learning isn't selection-biased
        unlabeled = self.store.get_unlabeled_observations(now_ts=now, limit=200)
        counterfactual_labeled = 0
        for obs in unlabeled:
            exp_ts = int(obs["decision_ts"]) + int(obs["expiry_sec"])
            quote = quote_lookup(str(obs["asset"]), exp_ts, tol)
            if quote is not None:
                s_price, q_ts = quote
                if abs(int(q_ts) - exp_ts) <= tol:
                    strike = float(obs["price"])
                    diff = float(s_price) - strike
                    outcome = "TIE" if abs(diff) <= 1e-9 else ("UP" if diff > 0 else "DOWN")
                    self.store.update_observation_counterfactual(
                        str(obs["obs_id"]), float(s_price), outcome, max(now, int(q_ts))
                    )
                    counterfactual_labeled += 1

        return {
            "ok": True,
            "due_checked": len(due_trades),
            "settled": settled_cnt,
            "unresolved": unresolved_cnt,
            "counterfactual_labeled": counterfactual_labeled,
        }

    # ------------------------------------------------------------------
    # Scheduled Retraining + Walk-Forward Evaluation
    # ------------------------------------------------------------------
    def maybe_retrain_model(
        self,
        feed_kind: str = "platform_settled",
        force: bool = False,
        now_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Retrain Logistic Regression on chronological labeled observations (both traded & skipped),
        test via walk-forward evaluation against incumbent, and advance to shadow/approved only if passed.
        """
        now = now_ts if now_ts is not None else utc_now_ts()
        if self.store.get_state("shadow_model_version_id") or self.store.get_state("shadow_rule_version_id"):
            return {
                "ok": True,
                "retrained": False,
                "reason": "A candidate is already being tested on new paper trades.",
            }
        last_retrain = int(self.store.get_state("last_retrain_ts", "0") or "0")
        last_count = int(self.store.get_state(f"last_retrain_count_{feed_kind}", "0") or "0")
        dataset = self.store.get_labeled_dataset(feed_kind)

        new_outcomes = len(dataset) - last_count
        if not force:
            if new_outcomes < int(self.config.retrain_min_new_outcomes):
                return {
                    "ok": True,
                    "retrained": False,
                    "reason": f"Only {new_outcomes} new labeled outcomes (need {self.config.retrain_min_new_outcomes}).",
                }
            if last_retrain > 0 and (now - last_retrain) < int(self.config.retrain_interval_sec):
                return {"ok": True, "retrained": False, "reason": "Retrain interval not elapsed."}

        versions = self.get_active_versions()
        inc_model_id = versions["active_model"]["model_version_id"]
        inc_rule_id = versions["active_rule"]["rule_version_id"]
        inc_rules = versions["active_rule"]["params"]

        wf = evaluate_walk_forward(
            dataset=dataset,
            incumbent_model=self._active_model,
            incumbent_rules=inc_rules,
            candidate_rules=inc_rules,
            criteria=self.config.evaluation,
            retrain_candidate_model=True,
            stake=self.config.default_stake,
            starting_balance=self.config.starting_balance,
        )

        cand_model = ProbabilityModel.fit_logistic_regression(dataset)
        cand_model_id = make_stable_id("mod")
        self.store.save_model_version(
            model_version_id=cand_model_id,
            model_type=cand_model.model_type,
            artifact_payload=cand_model.to_dict(),
            trained_on_count=len(dataset),
            feature_names=cand_model.feature_names,
            metrics=wf["summary"],
            status="SHADOW" if wf["passed"] else "ARCHIVED",
            verified_for_telegram=False,
            ts=now,
        )
        ev = self.store.record_evaluation_run(
            eval_type="WALK_FORWARD",
            candidate_model_id=cand_model_id,
            candidate_rule_id=inc_rule_id,
            incumbent_model_id=inc_model_id,
            incumbent_rule_id=inc_rule_id,
            feed_kind=feed_kind,
            passed=bool(wf["passed"]),
            reason=str(wf["reason"]),
            criteria=self.config.evaluation.__dict__,
            summary=wf["summary"],
            breakdown=wf["breakdown"],
            ts=now,
        )
        self.store.set_state("last_retrain_ts", str(now))
        self.store.set_state(f"last_retrain_count_{feed_kind}", str(len(dataset)))

        if wf["passed"]:
            self.store.set_state("shadow_model_version_id", cand_model_id)
            self._shadow_model = cand_model
            self.store.log_event(
                "Model",
                "INFO",
                f"Candidate model {cand_model_id} passed walk-forward and entered parallel SHADOW testing.",
                {"eval_id": ev["eval_id"]},
                ts=now,
            )
        else:
            self.store.log_event(
                "Model",
                "INFO",
                f"Candidate model {cand_model_id} failed walk-forward; kept incumbent {inc_model_id}: {wf['reason']}",
                {"eval_id": ev["eval_id"]},
                ts=now,
            )

        return {
            "ok": True,
            "retrained": True,
            "candidate_model_id": cand_model_id,
            "passed_walk_forward": bool(wf["passed"]),
            "reason": wf["reason"],
            "eval_id": ev["eval_id"],
            "summary": wf["summary"],
        }

    # ------------------------------------------------------------------
    # AI Proposal Processing -> Walk-Forward Gate -> Shadow Testing -> Auto Promotion
    # ------------------------------------------------------------------
    def process_ai_proposal_cycle(
        self,
        feed_kind: str = "platform_settled",
        now_ts: Optional[int] = None,
        ignore_rate_limit: bool = False,
    ) -> Dict[str, Any]:
        """
        Process one queued AI feedback batch, validate its proposal, and if a rule change
        is proposed, create a versioned candidate rule and run chronological walk-forward testing.
        Advance to parallel shadow paper testing ONLY if historical walk-forward passes.
        """
        now = now_ts if now_ts is not None else utc_now_ts()
        versions = self.get_active_versions()
        if versions["shadow_rule"] or versions["shadow_model"]:
            return {"ok": True, "processed": False, "reason": "A candidate is already in paper testing."}
        active_rules = versions["active_rule"]["params"]

        res = self.ai.process_next_batch(
            current_rules=active_rules,
            now_ts=now,
            ignore_rate_limit=ignore_rate_limit,
        )
        if not res.get("processed") or not res.get("ok"):
            return res

        decision = res.get("decision")
        if decision != "ACCEPTED_FOR_EVAL":
            return res

        return self._evaluate_rule_candidate(
            res, res["validated"]["merged_rules"], feed_kind, now, "AI_PROPOSAL"
        )

    def maybe_adapt_local_rules(self, now_ts: int) -> Dict[str, Any]:
        versions = self.get_active_versions()
        if versions["shadow_rule"] or versions["shadow_model"] or versions["strategy_paused"]:
            return {"processed": False}
        if self._active_model.model_type != "logistic_regression":
            return {"processed": False}

        last_ts = int(self.store.get_state("last_local_rule_eval_ts", "0") or "0")
        if now_ts - last_ts < int(self.config.retrain_interval_sec):
            return {"processed": False}

        dataset = self.store.get_labeled_dataset("platform_settled")
        last_count = int(self.store.get_state("last_local_rule_eval_count", "0") or "0")
        criteria = self.config.evaluation
        minimum = criteria.min_train_samples + criteria.n_walk_forward_windows * criteria.min_trades_per_window
        if len(dataset) < minimum or len(dataset) - last_count < int(self.config.retrain_min_new_outcomes):
            return {"processed": False}

        rules = versions["active_rule"]["params"]
        recent = self.store.get_recent_expiry_scores(
            versions["active_model"]["model_version_id"], versions["active_rule"]["rule_version_id"], now_ts
        )
        if not recent:
            return {"processed": False}
        current = float(rules["min_signal_probability"])
        step = 0.02 if sum(recent.values()) / len(recent) < 0 else -0.01
        target = round(min(0.85, max(0.52, current + step)), 4)
        if target == current:
            return {"processed": False}

        result = self._evaluate_rule_candidate(
            {"ok": True, "processed": True}, {**rules, "min_signal_probability": target},
            "platform_settled", now_ts, "LOCAL_MODEL",
        )
        self.store.set_state("last_local_rule_eval_ts", str(now_ts))
        self.store.set_state("last_local_rule_eval_count", str(len(dataset)))
        return result

    def _evaluate_rule_candidate(
        self, res: Dict[str, Any], candidate_rules: Dict[str, Any],
        feed_kind: str, now: int, source: str,
    ) -> Dict[str, Any]:
        versions = self.get_active_versions()
        active_model_id = versions["active_model"]["model_version_id"]
        active_rule_id = versions["active_rule"]["rule_version_id"]
        active_rules = versions["active_rule"]["params"]
        proposal_id = res.get("proposal_id")
        cand_rule_id = make_stable_id("rul")
        self.store.save_rule_version(
            rule_version_id=cand_rule_id,
            params=candidate_rules,
            source=source,
            parent_rule_version_id=active_rule_id,
            proposal_id=proposal_id,
            status="CANDIDATE",
            verified_for_telegram=False,
            ts=now,
        )

        dataset = self.store.get_labeled_dataset(feed_kind)
        wf = evaluate_walk_forward(
            dataset=dataset,
            incumbent_model=self._active_model,
            incumbent_rules=active_rules,
            candidate_rules=candidate_rules,
            criteria=self.config.evaluation,
            retrain_candidate_model=False,
            stake=self.config.default_stake,
            starting_balance=self.config.starting_balance,
        )
        ev = self.store.record_evaluation_run(
            eval_type="WALK_FORWARD",
            candidate_model_id=active_model_id,
            candidate_rule_id=cand_rule_id,
            incumbent_model_id=active_model_id,
            incumbent_rule_id=active_rule_id,
            feed_kind=feed_kind,
            passed=bool(wf["passed"]),
            reason=str(wf["reason"]),
            criteria=self.config.evaluation.__dict__,
            summary=wf["summary"],
            breakdown=wf["breakdown"],
            ts=now,
        )

        if wf["passed"]:
            self.store.update_rule_status(cand_rule_id, "SHADOW", verified_for_telegram=False)
            self.store.set_state("shadow_rule_version_id", cand_rule_id)
            if proposal_id:
                self.ai.update_proposal_decision(
                    proposal_id, "SHADOW_TESTING", wf["reason"], candidate_rule_id=cand_rule_id
                )
            self.store.log_event(
                "Strategy",
                "INFO",
                f"Candidate rule {cand_rule_id} passed walk-forward; promoted to parallel SHADOW paper testing.",
                {"proposal_id": proposal_id, "eval_id": ev["eval_id"]},
                ts=now,
            )
        else:
            self.store.update_rule_status(cand_rule_id, "REJECTED", verified_for_telegram=False)
            if proposal_id:
                self.ai.update_proposal_decision(
                    proposal_id, "REJECTED_EVAL", wf["reason"], candidate_rule_id=cand_rule_id
                )
            self.store.log_event(
                "Strategy",
                "INFO",
                f"Candidate rule {cand_rule_id} REJECTED by walk-forward evaluation; keeping incumbent {active_rule_id}.",
                {"proposal_id": proposal_id, "eval_id": ev["eval_id"], "reason": wf["reason"]},
                ts=now,
            )

        res["candidate_rule_id"] = cand_rule_id
        res["evaluation"] = {"eval_id": ev["eval_id"], "passed": wf["passed"], "reason": wf["reason"]}
        return res

    def _check_shadow_promotion(self, feed_kind: str, now_ts: int) -> Optional[Dict[str, Any]]:
        """Promote shadow candidate automatically ONLY when shadow evidence meets preconfigured thresholds."""
        versions = self.get_active_versions()
        shadow_rule = versions["shadow_rule"]
        shadow_model = versions["shadow_model"]
        if not shadow_rule and not shadow_model:
            return None

        cand_rule_id = shadow_rule["rule_version_id"] if shadow_rule else versions["active_rule"]["rule_version_id"]
        cand_model_id = shadow_model["model_version_id"] if shadow_model else versions["active_model"]["model_version_id"]
        inc_rule_id = versions["active_rule"]["rule_version_id"]
        inc_model_id = versions["active_model"]["model_version_id"]

        with self.store._lock:
            shadow_trades = self.store._conn.execute(
                """
                SELECT * FROM paper_trades
                WHERE is_shadow = 1
                  AND feed_kind = ?
                  AND rule_version_id = ?
                  AND model_version_id = ?
                  AND status IN ('WIN', 'LOSS', 'TIE')
                ORDER BY settled_ts ASC
                """,
                (feed_kind, cand_rule_id, cand_model_id),
            ).fetchall()

        if len(shadow_trades) < int(self.config.evaluation.min_shadow_trades):
            return None

        first_shadow_ts = int(shadow_trades[0]["decision_ts"])
        with self.store._lock:
            inc_trades = self.store._conn.execute(
                """
                SELECT * FROM paper_trades
                WHERE is_shadow = 0
                  AND feed_kind = ?
                  AND rule_version_id = ?
                  AND model_version_id = ?
                  AND decision_ts >= ?
                  AND status IN ('WIN', 'LOSS', 'TIE')
                ORDER BY settled_ts ASC
                """,
                (feed_kind, inc_rule_id, inc_model_id, first_shadow_ts),
            ).fetchall()

        cand_net = sum(float(t["net_result"] or 0.0) for t in shadow_trades)
        inc_net = sum(float(t["net_result"] or 0.0) for t in inc_trades)
        cand_wins = sum(1 for t in shadow_trades if t["status"] == "WIN")
        cand_dec = sum(1 for t in shadow_trades if t["status"] in ("WIN", "LOSS"))
        cand_wr = (cand_wins / cand_dec) if cand_dec > 0 else 0.0
        delta = round(cand_net - inc_net, 4)

        passed = delta >= float(self.config.evaluation.min_shadow_net_improvement) and cand_net > 0
        reason = (
            f"Shadow candidate outperformed incumbent by {delta:+.2f} over {len(shadow_trades)} shadow trades (WR {cand_wr*100:.1f}%)."
            if passed
            else f"Shadow candidate did not clear promotion bar (cand_net={cand_net:.2f}, delta={delta:+.2f})."
        )

        ev = self.store.record_evaluation_run(
            eval_type="SHADOW_PAPER",
            candidate_model_id=cand_model_id,
            candidate_rule_id=cand_rule_id,
            incumbent_model_id=inc_model_id,
            incumbent_rule_id=inc_rule_id,
            feed_kind=feed_kind,
            passed=passed,
            reason=reason,
            criteria=self.config.evaluation.__dict__,
            summary={
                "shadow_trades": len(shadow_trades),
                "candidate_net": round(cand_net, 4),
                "incumbent_net": round(inc_net, 4),
                "net_delta": delta,
                "candidate_win_rate": round(cand_wr, 4),
            },
            breakdown={},
            ts=now_ts,
        )

        if passed:
            # Save prior approved version for rollback and promote candidate
            if shadow_rule:
                self.store.set_state("prior_rule_version_id", inc_rule_id)
                self.store.update_rule_status(inc_rule_id, "ARCHIVED")
                self.store.update_rule_status(cand_rule_id, "APPROVED", verified_for_telegram=True)
                self.store.set_state("active_rule_version_id", cand_rule_id)
                self.store.set_state("shadow_rule_version_id", "")
                if shadow_rule.get("proposal_id"):
                    self.ai.update_proposal_decision(
                        str(shadow_rule["proposal_id"]), "PROMOTED", reason, cand_rule_id
                    )
            if shadow_model:
                self.store.set_state("prior_model_version_id", inc_model_id)
                self.store.update_model_status(inc_model_id, "ARCHIVED")
                self.store.update_model_status(cand_model_id, "APPROVED", verified_for_telegram=True)
                self.store.set_state("active_model_version_id", cand_model_id)
                self.store.set_state("shadow_model_version_id", "")
                self._active_model = self._shadow_model or self._active_model
                self._shadow_model = None
            # Also mark accompanying active version verified_for_telegram once live shadow passes
            self.store.update_model_status(self.store.get_state("active_model_version_id") or inc_model_id, "APPROVED", verified_for_telegram=True)
            self.store.update_rule_status(self.store.get_state("active_rule_version_id") or inc_rule_id, "APPROVED", verified_for_telegram=True)
            self.store.log_event(
                "Promotion",
                "INFO",
                f"PROMOTED candidate ({cand_model_id} / {cand_rule_id}) to APPROVED after shadow evaluation ({ev['eval_id']}).",
                ts=now_ts,
            )
        else:
            if shadow_rule:
                self.store.update_rule_status(cand_rule_id, "REJECTED")
                self.store.set_state("shadow_rule_version_id", "")
            if shadow_model:
                self.store.update_model_status(cand_model_id, "ARCHIVED")
                self.store.set_state("shadow_model_version_id", "")
                self._shadow_model = None
            self.store.log_event(
                "Promotion",
                "INFO",
                f"Shadow candidate ({cand_model_id} / {cand_rule_id}) did not pass shadow promotion; kept incumbent.",
                ts=now_ts,
            )
        return ev

    # ------------------------------------------------------------------
    # Automatic Rollback & Manual Rollback
    # ------------------------------------------------------------------
    def _check_automatic_rollback(self, feed_kind: str, now_ts: int) -> Optional[Dict[str, Any]]:
        rb = self.config.rollback
        versions = self.get_active_versions()
        active_rule_id = versions["active_rule"]["rule_version_id"]
        with self.store._lock:
            rows = self.store._conn.execute(
                """
                SELECT status, net_result FROM paper_trades
                WHERE is_shadow = 0
                  AND feed_kind = ?
                  AND rule_version_id = ?
                  AND status IN ('WIN', 'LOSS', 'TIE')
                ORDER BY settled_ts DESC
                LIMIT ?
                """,
                (feed_kind, active_rule_id, int(rb.rolling_window_trades)),
            ).fetchall()

        if not rows:
            return None

        # Consecutive losses check
        consec_losses = 0
        for r in rows:
            if r["status"] == "LOSS":
                consec_losses += 1
            elif r["status"] == "WIN":
                break

        trigger_reason: Optional[str] = None
        if consec_losses >= int(rb.max_consecutive_losses):
            trigger_reason = f"consecutive_losses({consec_losses}>={rb.max_consecutive_losses})"
        elif len(rows) >= int(rb.rolling_window_trades):
            wins = sum(1 for r in rows if r["status"] == "WIN")
            dec = sum(1 for r in rows if r["status"] in ("WIN", "LOSS"))
            wr = (wins / dec) if dec > 0 else 1.0
            net_sum = sum(float(r["net_result"] or 0.0) for r in rows)
            dd_pct = max(0.0, (-net_sum / max(1.0, self.config.starting_balance)) * 100.0)
            if wr < float(rb.min_rolling_win_rate):
                trigger_reason = f"rolling_win_rate({wr:.2%}<{rb.min_rolling_win_rate:.2%})"
            elif dd_pct >= float(rb.max_rolling_drawdown_pct):
                trigger_reason = f"rolling_drawdown({dd_pct:.1f}%>={rb.max_rolling_drawdown_pct:.1f}%)"

        if not trigger_reason:
            return None
        return self.rollback_or_pause(reason=trigger_reason, now_ts=now_ts)

    def rollback_or_pause(self, reason: str, now_ts: Optional[int] = None) -> Dict[str, Any]:
        now = now_ts if now_ts is not None else utc_now_ts()
        versions = self.get_active_versions()
        prior_rule_id = versions["prior_rule_id"]
        prior_model_id = versions["prior_model_id"]
        cur_rule_id = versions["active_rule"]["rule_version_id"]
        cur_model_id = versions["active_model"]["model_version_id"]

        if prior_rule_id or prior_model_id:
            if prior_rule_id:
                self.store.update_rule_status(cur_rule_id, "ROLLED_BACK", verified_for_telegram=False)
                self.store.update_rule_status(prior_rule_id, "APPROVED")
                self.store.set_state("active_rule_version_id", prior_rule_id)
                self.store.set_state("prior_rule_version_id", "")
            if prior_model_id:
                self.store.update_model_status(cur_model_id, "ROLLED_BACK", verified_for_telegram=False)
                self.store.update_model_status(prior_model_id, "APPROVED")
                self.store.set_state("active_model_version_id", prior_model_id)
                self.store.set_state("prior_model_version_id", "")
                self._active_model = ProbabilityModel.from_dict(
                    self.store.load_model_artifact(prior_model_id)
                )
            self.store.log_event(
                "Rollback",
                "WARN",
                f"AUTOMATIC ROLLBACK triggered ({reason}): reverted from ({cur_model_id}/{cur_rule_id}) to ({prior_model_id or cur_model_id}/{prior_rule_id or cur_rule_id}).",
                ts=now,
            )
            return {"ok": True, "action": "ROLLED_BACK", "reason": reason}

        # No prior version to roll back to -> pause strategy & Telegram alerts
        self.store.set_state("strategy_paused", "1")
        self.store.set_state("pause_reason", reason)
        self.store.log_event(
            "Rollback",
            "WARN",
            f"Strategy PAUSED due to deterioration ({reason}); Telegram signals halted.",
            ts=now,
        )
        return {"ok": True, "action": "PAUSED", "reason": reason}

    def resume_strategy(self) -> Dict[str, Any]:
        self.store.set_state("strategy_paused", "0")
        self.store.set_state("pause_reason", "")
        self.store.set_state("stubay_resumed_ts", str(utc_now_ts()))
        self.store.log_event("Lifecycle", "INFO", "Strategy resumed by operator.")
        return {"ok": True, "strategy_paused": False}

    # ------------------------------------------------------------------
    # Performance Reporting (STRICT separation of platform_settled vs proxy_simulation)
    # ------------------------------------------------------------------
    def compute_performance_summary(self, feed_kind: str = "platform_settled") -> Dict[str, Any]:
        with self.store._lock:
            rows = self.store._conn.execute(
                """
                SELECT * FROM paper_trades
                WHERE is_shadow = 0 AND feed_kind = ?
                ORDER BY decision_ts ASC
                """,
                (feed_kind,),
            ).fetchall()

        trades = [dict(r) for r in rows]
        open_cnt = sum(1 for t in trades if t["status"] == "OPEN")
        wins = sum(1 for t in trades if t["status"] == "WIN")
        losses = sum(1 for t in trades if t["status"] == "LOSS")
        ties = sum(1 for t in trades if t["status"] == "TIE")
        unresolved = sum(1 for t in trades if t["status"] == "UNRESOLVED")
        settled = [t for t in trades if t["status"] in ("WIN", "LOSS", "TIE")]

        net_pnl = round(sum(float(t["net_result"] or 0.0) for t in settled), 4)
        decisive = wins + losses
        win_rate = round((wins / decisive) if decisive > 0 else 0.0, 4)

        bal = float(self.config.starting_balance)
        peak = bal
        max_dd = 0.0
        probs: List[float] = []
        outs: List[float] = []
        for t in settled:
            bal += float(t["net_result"] or 0.0)
            if bal > peak:
                peak = bal
            if peak > 0:
                dd = ((peak - bal) / peak) * 100.0
                if dd > max_dd:
                    max_dd = dd
            if t["status"] in ("WIN", "LOSS"):
                probs.append(float(t["estimated_prob"]))
                outs.append(1.0 if t["status"] == "WIN" else 0.0)

        from .modeling import compute_calibration_metrics

        cal = compute_calibration_metrics(probs, outs)
        return {
            "feed_kind": feed_kind,
            "label": "Platform-Settled" if feed_kind == "platform_settled" else "Proxy Simulation (Unmixed)",
            "total_trades": len(trades),
            "open_trades": open_cnt,
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "unresolved": unresolved,
            "win_rate": win_rate,
            "net_paper_return": net_pnl,
            "current_paper_balance": round(bal, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "brier_score": cal["brier_score"],
            "ece": cal["ece"],
        }

    def get_dashboard_overview(self) -> Dict[str, Any]:
        with self.store._lock:
            memory_count = self.store._conn.execute("SELECT COUNT(*) FROM stubay_memory").fetchone()[0]
            trades = self.store._conn.execute(
                "SELECT COUNT(*) AS trades, COUNT(CASE WHEN status = 'WIN' THEN 1 END) AS wins, "
                "COUNT(CASE WHEN status = 'LOSS' THEN 1 END) AS losses FROM paper_trades WHERE is_shadow = 0",
            ).fetchone()
            latest_memory = self.store._conn.execute(
                "SELECT m.asset, m.status, m.plan_json, t.status AS outcome FROM stubay_memory AS m "
                "LEFT JOIN paper_trades AS t ON m.trade_id = t.trade_id ORDER BY m.bucket DESC, m.rowid DESC LIMIT 6"
            ).fetchall()
            obs_count = self.store._conn.execute("SELECT COUNT(*) AS c FROM market_observations").fetchone()["c"]
            sig_count = self.store._conn.execute("SELECT COUNT(*) AS c FROM signals_and_skips").fetchone()["c"]
            ai_pending = self.store._conn.execute(
                "SELECT COUNT(*) AS c FROM ai_feedback_queue WHERE status IN ('PENDING', 'FAILED_RETRY')"
            ).fetchone()["c"]
            ai_proposals_cnt = self.store._conn.execute("SELECT COUNT(*) AS c FROM ai_proposals").fetchone()["c"]
            tg_sent = self.store._conn.execute(
                "SELECT COUNT(*) AS c FROM telegram_outbox WHERE status = 'SENT'"
            ).fetchone()["c"]
            tg_pending = self.store._conn.execute(
                "SELECT COUNT(*) AS c FROM telegram_outbox WHERE status IN ('PENDING', 'RETRY_WAIT')"
            ).fetchone()["c"]
            tg_uncertain = self.store._conn.execute(
                "SELECT COUNT(*) AS c FROM telegram_outbox WHERE status = 'UNCERTAIN'"
            ).fetchone()["c"]
            eval_cnt = self.store._conn.execute("SELECT COUNT(*) AS c FROM evaluation_runs").fetchone()["c"]

        return {
            "stubay": {
                "trade_totals": dict(trades),
                "minimum_confidence": self.stubay.minimum_confidence(),
                "review": self.stubay_review.overview(),
                "status": self.store.get_state("stubay_status", "Waiting for its first scan."),
                "memory_count": int(memory_count),
                "latest": [{"asset": row["asset"], "status": row["outcome"] or row["status"],
                            "plan": json.loads(row["plan_json"])} for row in latest_memory],
            },
            "paper_only": True,
            "data_dir": str(self.store.data_dir),
            "schema_version": self.store.get_schema_version(),
            "asset_allowlist": self.config.asset_allowlist,
            "expiry_allowlist_sec": self.config.expiry_allowlist_sec,
            "versions": self.get_active_versions(),
            "metrics_by_feed_kind": {
                "platform_settled": self.compute_performance_summary("platform_settled"),
                "proxy_simulation": self.compute_performance_summary("proxy_simulation"),
            },
            "counts": {
                "observations": int(obs_count),
                "signals_and_skips": int(sig_count),
                "evaluation_runs": int(eval_cnt),
                "ai_queue_pending": int(ai_pending),
                "ai_proposals": int(ai_proposals_cnt),
                "telegram_sent": int(tg_sent),
                "telegram_pending": int(tg_pending),
                "telegram_uncertain": int(tg_uncertain),
            },
        }
