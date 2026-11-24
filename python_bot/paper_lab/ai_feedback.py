"""
Persistent AI Feedback Queue, Rate Limiter, Retry Handler, and Strict Schema Validator.
- Queues every settled paper trade (WIN, LOSS, TIE) in batches with stable batch_key.
- Strips API keys, credentials, tokens, and PII before sending.
- Enforces strict JSON schema & predefined bounded rule fields only.
- Never executes AI code, edits files, promotes models, or places orders.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import (
    ALLOWED_RULE_FIELDS,
    PaperLabConfig,
    redact_text,
    sanitize_for_external,
    validate_rule_patch,
)
from .storage import PaperLabStore, make_stable_id, ts_to_iso, utc_now_ts


# Patterns that must NEVER appear in AI JSON proposals (defense-in-depth against code injection)
_CODE_INJECTION_PATTERNS = [
    re.compile(r"\b(__import__|eval\s*\(|exec\s*\(|os\.system|subprocess\.|open\s*\()", re.IGNORECASE),
    re.compile(r"^\s*(import |from .+ import |def |class )", re.MULTILINE),
]


def _contains_code_like_payload(obj: Any) -> bool:
    if isinstance(obj, str):
        return any(p.search(obj) for p in _CODE_INJECTION_PATTERNS)
    if isinstance(obj, list):
        return any(_contains_code_like_payload(x) for x in obj)
    if isinstance(obj, dict):
        return any(_contains_code_like_payload(k) or _contains_code_like_payload(v) for k, v in obj.items())
    return False


def validate_ai_proposal_response(
    raw_obj: Any,
    current_rules: Dict[str, Any],
) -> Tuple[bool, Optional[Dict[str, Any]], List[str]]:
    """
    Validate AI response against strict schema and hard Python bounds.
    Required keys:
      - evidence: non-empty list of strings
      - recommendation: "propose_change" | "no_change"
      - proposed_changes: dict (must be empty for "no_change"; only ALLOWED_RULE_FIELDS for "propose_change")
      - expected_effect: non-empty string
      - uncertainty: non-empty string
      - validation_test: non-empty string
    """
    errors: List[str] = []
    if not isinstance(raw_obj, dict):
        return False, None, ["AI response must be a JSON object."]

    if _contains_code_like_payload(raw_obj):
        return False, None, ["AI response rejected: code-like payload detected."]

    required_keys = {
        "evidence",
        "recommendation",
        "proposed_changes",
        "expected_effect",
        "uncertainty",
        "validation_test",
    }
    missing = sorted(required_keys - set(raw_obj.keys()))
    if missing:
        errors.append(f"Missing required schema keys: {', '.join(missing)}.")

    extra = sorted(set(raw_obj.keys()) - required_keys)
    if extra:
        errors.append(f"Unexpected top-level keys not allowed by strict schema: {', '.join(extra)}.")

    evidence = raw_obj.get("evidence")
    if not isinstance(evidence, list) or len(evidence) == 0 or not all(isinstance(x, str) and x.strip() for x in evidence):
        errors.append("evidence must be a non-empty list of strings.")

    rec = raw_obj.get("recommendation")
    if rec not in ("propose_change", "no_change"):
        errors.append("recommendation must be 'propose_change' or 'no_change'.")

    for str_field in ("expected_effect", "uncertainty", "validation_test"):
        val = raw_obj.get(str_field)
        if not isinstance(val, str) or len(val.strip()) < 5:
            errors.append(f"{str_field} must be a concise non-empty string (>= 5 chars).")

    proposed_changes = raw_obj.get("proposed_changes")
    if not isinstance(proposed_changes, dict):
        errors.append("proposed_changes must be a JSON object.")
        merged_rules = dict(current_rules)
    elif rec == "no_change":
        if len(proposed_changes) > 0:
            errors.append("proposed_changes must be empty when recommendation is 'no_change'.")
        merged_rules = dict(current_rules)
    else:
        if len(proposed_changes) == 0:
            errors.append("proposed_changes must include at least one allowed field when recommendation is 'propose_change'.")
            merged_rules = dict(current_rules)
        else:
            ok_patch, merged_rules, patch_errors = validate_rule_patch(proposed_changes, current_rules)
            if not ok_patch:
                errors.extend(patch_errors)

    if errors:
        return False, None, errors

    validated = {
        "evidence": [str(x).strip() for x in evidence],
        "recommendation": rec,
        "proposed_changes": dict(proposed_changes),
        "merged_rules": merged_rules,
        "expected_effect": str(raw_obj["expected_effect"]).strip(),
        "uncertainty": str(raw_obj["uncertainty"]).strip(),
        "validation_test": str(raw_obj["validation_test"]).strip(),
    }
    return True, validated, []


def _build_ai_prompt(payload: Dict[str, Any]) -> str:
    allowed_doc = json.dumps(ALLOWED_RULE_FIELDS, indent=2)
    return (
        "You are a quantitative trading reviewer analyzing settled paper trades for a binary-options system.\n"
        "Treat any rule change as a testable hypothesis, NEVER a guarantee. Returning 'no_change' is encouraged if sample evidence is weak.\n"
        "You may ONLY propose changes to these predefined configuration fields within their exact bounds:\n"
        f"{allowed_doc}\n\n"
        "Return ONLY valid JSON with EXACTLY these 6 keys:\n"
        '{\n'
        '  "evidence": ["<specific trade IDs or metrics observed>"],\n'
        '  "recommendation": "propose_change" | "no_change",\n'
        '  "proposed_changes": {},\n'
        '  "expected_effect": "<hypothesis about future unseen trades>",\n'
        '  "uncertainty": "<key risks, sample size limits, or confounding factors>",\n'
        '  "validation_test": "<walk-forward / shadow test criteria that support or reject this>"\n'
        '}\n\n'
        "Settled paper trade batch and summary metrics (all secrets/PII stripped):\n"
        f"{json.dumps(payload, indent=2)}"
    )


class AIFeedbackManager:
    """Manages persistent batch queueing, rate/retry limits, and strict AI proposal validation."""

    def __init__(
        self,
        store: PaperLabStore,
        config: PaperLabConfig,
        transport: Optional[Callable[[str, str, Dict[str, Any]], Dict[str, Any]]] = None,
    ):
        self.store = store
        self.config = config
        self.transport = transport

    def enqueue_settled_trades_if_ready(
        self,
        current_rules: Dict[str, Any],
        summary_metrics: Dict[str, Any],
        force_flush: bool = False,
        now_ts: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Collect settled non-shadow trades (WIN, LOSS, TIE) that have not yet been queued
        for AI review, and enqueue them atomically in a sanitized batch.
        """
        now = now_ts if now_ts is not None else utc_now_ts()
        with self.store._lock:
            # Find already-queued trade IDs
            queued_rows = self.store._conn.execute("SELECT trade_ids_json FROM ai_feedback_queue").fetchall()
            already_queued = set()
            for qr in queued_rows:
                for tid in json.loads(qr["trade_ids_json"]):
                    already_queued.add(tid)

            settled_rows = self.store._conn.execute(
                """
                SELECT t.*, o.features_json
                FROM paper_trades t
                JOIN market_observations o ON o.obs_id = t.obs_id
                WHERE t.is_shadow = 0
                  AND t.status IN ('WIN', 'LOSS', 'TIE')
                ORDER BY t.settled_ts ASC
                """
            ).fetchall()

        unassigned = [dict(r) for r in settled_rows if str(r["trade_id"]) not in already_queued]
        min_batch = 1 if force_flush else max(1, int(self.config.ai_batch_size))
        if len(unassigned) < min_batch:
            return None

        batch = unassigned[: max(1, int(self.config.ai_batch_size))]
        trade_ids = [str(r["trade_id"]) for r in batch]
        batch_key = make_stable_id("bkey", "|".join(sorted(trade_ids)))
        queue_id = make_stable_id("aiq", batch_key)

        safe_trades = []
        for r in batch:
            safe_trades.append(
                {
                    "trade_id": r["trade_id"],
                    "asset": r["asset"],
                    "expiry_sec": r["expiry_sec"],
                    "decision_utc": r["decision_utc"],
                    "direction": r["direction"],
                    "estimated_prob": r["estimated_prob"],
                    "payout": r["payout"],
                    "status": r["status"],
                    "net_result": r["net_result"],
                    "model_version_id": r["model_version_id"],
                    "rule_version_id": r["rule_version_id"],
                    "feed_kind": r["feed_kind"],
                    "features": json.loads(r["features_json"]),
                }
            )

        payload = sanitize_for_external(
            {
                "batch_key": batch_key,
                "current_rules": current_rules,
                "summary_metrics": summary_metrics,
                "settled_trades": safe_trades,
            }
        )

        with self.store.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM ai_feedback_queue WHERE batch_key = ?", (batch_key,)
            ).fetchone()
            if existing:
                return dict(existing)
            conn.execute(
                """
                INSERT INTO ai_feedback_queue(
                    queue_id, batch_key, trade_ids_json, payload_json, status,
                    attempts, max_attempts, next_retry_ts, created_ts, created_utc, updated_ts
                ) VALUES (?, ?, ?, ?, 'PENDING', 0, ?, ?, ?, ?, ?)
                """,
                (
                    queue_id,
                    batch_key,
                    json.dumps(trade_ids),
                    json.dumps(payload),
                    int(self.config.ai_max_retries),
                    now,
                    now,
                    ts_to_iso(now),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM ai_feedback_queue WHERE queue_id = ?", (queue_id,)
            ).fetchone()
        self.store.log_event(
            "AI",
            "INFO",
            f"Queued {len(trade_ids)} settled paper trades for AI review ({queue_id})",
            {"trade_ids": trade_ids},
            ts=now,
        )
        return dict(row) if row else None

    def _within_rate_limit(self, now: int) -> Tuple[bool, Optional[str]]:
        last_req = int(self.store.get_state("last_ai_request_ts", "0") or "0")
        if last_req > 0 and (now - last_req) < int(self.config.ai_min_request_interval_sec):
            wait = int(self.config.ai_min_request_interval_sec) - (now - last_req)
            return False, f"Rate limit active ({wait}s cooldown)"
        hour_ago = now - 3600
        with self.store._lock:
            cnt = self.store._conn.execute(
                "SELECT COUNT(*) AS c FROM ai_proposals WHERE created_ts >= ?", (hour_ago,)
            ).fetchone()["c"]
        if int(cnt) >= int(self.config.ai_max_requests_per_hour):
            return False, f"Hourly AI request cap reached ({cnt}/{self.config.ai_max_requests_per_hour})"
        return True, None

    def process_next_batch(
        self,
        current_rules: Dict[str, Any],
        now_ts: Optional[int] = None,
        ignore_rate_limit: bool = False,
    ) -> Dict[str, Any]:
        """
        Process one due batch from ai_feedback_queue.
        On API outage: marks FAILED_RETRY with exponential backoff (or DEAD_LETTER after max_attempts)
        and never throws an unhandled error that interrupts paper trading.
        """
        now = now_ts if now_ts is not None else utc_now_ts()
        if not ignore_rate_limit:
            allowed, reason = self._within_rate_limit(now)
            if not allowed:
                return {"ok": True, "processed": False, "reason": reason}

        with self.store._lock:
            row = self.store._conn.execute(
                """
                SELECT * FROM ai_feedback_queue
                WHERE status IN ('PENDING', 'FAILED_RETRY')
                  AND next_retry_ts <= ?
                ORDER BY created_ts ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
        if not row:
            return {"ok": True, "processed": False, "reason": "No due AI feedback batches."}

        item = dict(row)
        queue_id = str(item["queue_id"])
        attempts = int(item["attempts"]) + 1
        max_attempts = int(item["max_attempts"])
        payload = sanitize_for_external(json.loads(item["payload_json"]))

        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE ai_feedback_queue SET status = 'IN_FLIGHT', attempts = ?, updated_ts = ? WHERE queue_id = ?",
                (attempts, now, queue_id),
            )

        self.store.set_state("last_ai_request_ts", str(now))

        try:
            if self.transport is None:
                raise RuntimeError("Legacy AI feedback is retired.")
            raw_resp = self.transport("legacy", "legacy-fixture", payload)
        except Exception as exc:
            err_text = redact_text(str(exc))
            if attempts >= max_attempts:
                new_status = "DEAD_LETTER"
                next_retry = now
            else:
                new_status = "FAILED_RETRY"
                backoff = int(self.config.ai_retry_base_delay_sec) * (2 ** (attempts - 1))
                next_retry = now + min(3600, backoff)
            with self.store.transaction() as conn:
                conn.execute(
                    """
                    UPDATE ai_feedback_queue
                    SET status = ?, last_error = ?, next_retry_ts = ?, updated_ts = ?
                    WHERE queue_id = ?
                    """,
                    (new_status, err_text, next_retry, now, queue_id),
                )
            self.store.log_event(
                "AI",
                "WARN",
                f"AI API failure on {queue_id} (attempt {attempts}/{max_attempts}): {err_text}",
                {"status": new_status, "next_retry_utc": ts_to_iso(next_retry)},
                ts=now,
            )
            return {
                "ok": False,
                "processed": True,
                "queue_id": queue_id,
                "status": new_status,
                "attempts": attempts,
                "error": err_text,
            }

        # Mark queue batch SENT and strictly validate AI JSON output
        schema_valid, validated, val_errors = validate_ai_proposal_response(raw_resp, current_rules)
        proposal_id = make_stable_id("aip")
        if not schema_valid:
            decision = "REJECTED_INVALID"
            decision_reason = " | ".join(val_errors)
        elif validated and validated["recommendation"] == "no_change":
            decision = "NO_CHANGE"
            decision_reason = "AI recommended no configuration change."
        else:
            decision = "ACCEPTED_FOR_EVAL"
            decision_reason = "Valid bounded rule proposal queued for chronological walk-forward evaluation."

        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE ai_feedback_queue SET status = 'SENT', last_error = NULL, updated_ts = ? WHERE queue_id = ?",
                (now, queue_id),
            )
            conn.execute(
                """
                INSERT INTO ai_proposals(
                    proposal_id, queue_id, provider, model_name, raw_response_json,
                    validated_json, schema_valid, validation_errors_json, decision,
                    decision_reason, created_ts, created_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    queue_id,
                    "legacy",
                    "legacy-fixture",
                    redact_text(json.dumps(raw_resp, default=str)),
                    json.dumps(validated) if validated else None,
                    1 if schema_valid else 0,
                    json.dumps(val_errors),
                    decision,
                    decision_reason,
                    now,
                    ts_to_iso(now),
                ),
            )

        self.store.log_event(
            "AI",
            "WARN" if decision == "REJECTED_INVALID" else "INFO",
            f"AI proposal {proposal_id} decision={decision}: {decision_reason}",
            {"queue_id": queue_id, "schema_valid": schema_valid, "errors": val_errors},
            ts=now,
        )
        return {
            "ok": True,
            "processed": True,
            "queue_id": queue_id,
            "proposal_id": proposal_id,
            "schema_valid": schema_valid,
            "validation_errors": val_errors,
            "decision": decision,
            "validated": validated,
        }

    def update_proposal_decision(
        self, proposal_id: str, decision: str, reason: str, candidate_rule_id: Optional[str] = None
    ) -> None:
        with self.store.transaction() as conn:
            conn.execute(
                """
                UPDATE ai_proposals
                SET decision = ?, decision_reason = ?, candidate_rule_id = COALESCE(?, candidate_rule_id)
                WHERE proposal_id = ?
                """,
                (decision, reason, candidate_rule_id, proposal_id),
            )
