"""
SQLite Persistent Storage, Schema Migrations, Recovery & Atomic Backups.
All timestamps are stored in UTC (both integer epoch seconds and ISO-8601 UTC strings).
"""

from __future__ import annotations

import contextlib
import csv
import datetime
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .config import DEFAULT_RULE_PARAMS, redact_text


def utc_now_ts() -> int:
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())


def ts_to_iso(ts: Optional[int]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def make_stable_id(prefix: str, seed: Optional[str] = None) -> str:
    if seed is not None:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}_{digest}"
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


MIGRATIONS: List[Tuple[int, str, str]] = [
    (
        1,
        "initial_paper_lab_schema",
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'owner',
            created_ts INTEGER NOT NULL,
            created_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS auth_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id),
            token_hash TEXT NOT NULL UNIQUE,
            created_ts INTEGER NOT NULL,
            expires_ts INTEGER NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            revoked_ts INTEGER
        );

        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_ts INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS model_versions (
            model_version_id TEXT PRIMARY KEY,
            model_type TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            trained_on_count INTEGER NOT NULL DEFAULT 0,
            feature_names_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            status TEXT NOT NULL,
            verified_for_telegram INTEGER NOT NULL DEFAULT 0,
            created_ts INTEGER NOT NULL,
            created_utc TEXT NOT NULL,
            approved_ts INTEGER
        );

        CREATE TABLE IF NOT EXISTS rule_versions (
            rule_version_id TEXT PRIMARY KEY,
            parent_rule_version_id TEXT,
            params_json TEXT NOT NULL,
            source TEXT NOT NULL,
            proposal_id TEXT,
            status TEXT NOT NULL,
            verified_for_telegram INTEGER NOT NULL DEFAULT 0,
            created_ts INTEGER NOT NULL,
            created_utc TEXT NOT NULL,
            approved_ts INTEGER
        );

        CREATE TABLE IF NOT EXISTS market_observations (
            obs_id TEXT PRIMARY KEY,
            asset TEXT NOT NULL,
            expiry_sec INTEGER NOT NULL,
            decision_ts INTEGER NOT NULL,
            decision_utc TEXT NOT NULL,
            quote_ts INTEGER NOT NULL,
            price REAL NOT NULL,
            payout REAL NOT NULL,
            fee_rate REAL NOT NULL DEFAULT 0.0,
            tie_rule TEXT NOT NULL DEFAULT 'refund',
            feed_source TEXT NOT NULL,
            feed_kind TEXT NOT NULL,
            features_json TEXT NOT NULL,
            counterfactual_settlement_price REAL,
            counterfactual_outcome TEXT,
            counterfactual_settled_ts INTEGER,
            UNIQUE(asset, expiry_sec, decision_ts, feed_kind)
        );
        CREATE INDEX IF NOT EXISTS idx_obs_asset_ts ON market_observations(asset, decision_ts);

        CREATE TABLE IF NOT EXISTS signals_and_skips (
            signal_id TEXT PRIMARY KEY,
            obs_id TEXT NOT NULL REFERENCES market_observations(obs_id),
            asset TEXT NOT NULL,
            expiry_sec INTEGER NOT NULL,
            decision_ts INTEGER NOT NULL,
            decision_utc TEXT NOT NULL,
            direction TEXT NOT NULL,
            estimated_prob REAL NOT NULL,
            break_even_prob REAL NOT NULL,
            payout REAL NOT NULL,
            action TEXT NOT NULL,
            skip_reason TEXT,
            model_version_id TEXT NOT NULL,
            rule_version_id TEXT NOT NULL,
            feed_kind TEXT NOT NULL,
            is_shadow INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sig_ts ON signals_and_skips(decision_ts);
        CREATE INDEX IF NOT EXISTS idx_sig_asset ON signals_and_skips(asset, expiry_sec);

        CREATE TABLE IF NOT EXISTS paper_trades (
            trade_id TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL REFERENCES signals_and_skips(signal_id),
            obs_id TEXT NOT NULL REFERENCES market_observations(obs_id),
            asset TEXT NOT NULL,
            expiry_sec INTEGER NOT NULL,
            decision_ts INTEGER NOT NULL,
            decision_utc TEXT NOT NULL,
            strike REAL NOT NULL,
            direction TEXT NOT NULL,
            expiry_ts INTEGER NOT NULL,
            expiry_utc TEXT NOT NULL,
            estimated_prob REAL NOT NULL,
            stake REAL NOT NULL,
            payout REAL NOT NULL,
            fee_rate REAL NOT NULL DEFAULT 0.0,
            tie_rule TEXT NOT NULL DEFAULT 'refund',
            model_version_id TEXT NOT NULL,
            rule_version_id TEXT NOT NULL,
            feed_kind TEXT NOT NULL,
            is_shadow INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'OPEN',
            settlement_price REAL,
            settled_ts INTEGER,
            settled_utc TEXT,
            net_result REAL,
            settlement_event_id TEXT,
            UNIQUE(signal_id)
        );
        CREATE INDEX IF NOT EXISTS idx_trades_status_exp ON paper_trades(status, expiry_ts);
        CREATE INDEX IF NOT EXISTS idx_trades_strategy ON paper_trades(rule_version_id, model_version_id);

        CREATE TABLE IF NOT EXISTS evaluation_runs (
            eval_id TEXT PRIMARY KEY,
            eval_type TEXT NOT NULL,
            candidate_model_id TEXT NOT NULL,
            candidate_rule_id TEXT NOT NULL,
            incumbent_model_id TEXT NOT NULL,
            incumbent_rule_id TEXT NOT NULL,
            feed_kind TEXT NOT NULL,
            passed INTEGER NOT NULL,
            reason TEXT NOT NULL,
            criteria_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            breakdown_json TEXT NOT NULL,
            created_ts INTEGER NOT NULL,
            created_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_feedback_queue (
            queue_id TEXT PRIMARY KEY,
            batch_key TEXT NOT NULL UNIQUE,
            trade_ids_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            next_retry_ts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_ts INTEGER NOT NULL,
            created_utc TEXT NOT NULL,
            updated_ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_aiq_status ON ai_feedback_queue(status, next_retry_ts);

        CREATE TABLE IF NOT EXISTS ai_proposals (
            proposal_id TEXT PRIMARY KEY,
            queue_id TEXT REFERENCES ai_feedback_queue(queue_id),
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            raw_response_json TEXT NOT NULL,
            validated_json TEXT,
            schema_valid INTEGER NOT NULL,
            validation_errors_json TEXT NOT NULL,
            decision TEXT NOT NULL,
            decision_reason TEXT NOT NULL,
            candidate_rule_id TEXT,
            created_ts INTEGER NOT NULL,
            created_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telegram_outbox (
            outbox_id TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            asset TEXT NOT NULL,
            direction TEXT NOT NULL,
            payload_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            telegram_message_id TEXT,
            expiry_ts INTEGER NOT NULL,
            last_error TEXT,
            created_ts INTEGER NOT NULL,
            created_utc TEXT NOT NULL,
            updated_ts INTEGER NOT NULL,
            UNIQUE(signal_id, chat_id)
        );
        CREATE INDEX IF NOT EXISTS idx_tgo_status ON telegram_outbox(status, expiry_ts);
        CREATE TABLE IF NOT EXISTS audit_logs (
            log_id TEXT PRIMARY KEY,
            ts INTEGER NOT NULL,
            ts_utc TEXT NOT NULL,
            category TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts);
        """,
    ),
    (
        2,
        "stubay_signal_memory",
        """
        ALTER TABLE telegram_outbox ADD COLUMN payload_image BLOB;
        CREATE TABLE stubay_memory (
            bucket INTEGER NOT NULL,
            asset TEXT NOT NULL,
            status TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            screenshot BLOB NOT NULL,
            plan_json TEXT NOT NULL,
            entry_ts INTEGER,
            trade_id TEXT REFERENCES paper_trades(trade_id),
            PRIMARY KEY (bucket, asset)
        );
        CREATE INDEX idx_stubay_pending ON stubay_memory(status, entry_ts);
        """,
    ),
]


class PaperLabStore:
    """Thread-safe SQLite store with WAL, transactional writes, migrations, and atomic backups."""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.models_dir = self.data_dir / "models"
        self.backups_dir = self.data_dir / "backups"
        self.db_path = self.data_dir / "paper_lab.sqlite3"
        self._lock = threading.RLock()

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # Explicit transaction control
        )
        self._conn.row_factory = sqlite3.Row
        self._configure_pragmas()
        self._apply_migrations()

    def _configure_pragmas(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.execute("PRAGMA busy_timeout=5000;")

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            in_tx = self._conn.in_transaction
            if not in_tx:
                self._conn.execute("BEGIN IMMEDIATE;")
            try:
                yield self._conn
                if not in_tx:
                    self._conn.execute("COMMIT;")
            except Exception:
                if not in_tx and self._conn.in_transaction:
                    self._conn.execute("ROLLBACK;")
                raise

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def _apply_migrations(self) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_ts INTEGER NOT NULL,
                    applied_utc TEXT NOT NULL
                );
                """
            )
            applied = {
                int(row["version"])
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for version, name, sql in MIGRATIONS:
                if version in applied:
                    continue
                for stmt in sql.split(";"):
                    s = stmt.strip()
                    if s:
                        conn.execute(s + ";")
                now = utc_now_ts()
                conn.execute(
                    "INSERT INTO schema_migrations(version, name, applied_ts, applied_utc) VALUES (?, ?, ?, ?)",
                    (version, name, now, ts_to_iso(now)),
                )

    def get_schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
            ).fetchone()
            return int(row["v"]) if row else 0

    # ------------------------------------------------------------------
    # System State Checkpoints
    # ------------------------------------------------------------------
    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else default

    def set_state(self, key: str, value: str) -> None:
        now = utc_now_ts()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO system_state(key, value, updated_ts) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ts=excluded.updated_ts
                """,
                (key, str(value), now),
            )

    def get_all_state(self) -> Dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM system_state").fetchall()
            return {str(r["key"]): str(r["value"]) for r in rows}

    # ------------------------------------------------------------------
    # Redacted Audit Logging
    # ------------------------------------------------------------------
    def log_event(
        self,
        category: str,
        level: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        ts: Optional[int] = None,
    ) -> str:
        now = ts if ts is not None else utc_now_ts()
        log_id = make_stable_id("log")
        safe_msg = redact_text(message)
        safe_details = redact_text(json.dumps(details or {}, default=str)) if details else None
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs(log_id, ts, ts_utc, category, level, message, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (log_id, now, ts_to_iso(now), category, level.upper(), safe_msg, safe_details),
            )
        return log_id

    # ------------------------------------------------------------------
    # Model & Rule Versions
    # ------------------------------------------------------------------
    def save_model_version(
        self,
        model_version_id: str,
        model_type: str,
        artifact_payload: Dict[str, Any],
        trained_on_count: int,
        feature_names: List[str],
        metrics: Dict[str, Any],
        status: str = "CANDIDATE",
        verified_for_telegram: bool = False,
        ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        now = ts if ts is not None else utc_now_ts()
        artifact_filename = f"{model_version_id}.json"
        artifact_path = self.models_dir / artifact_filename
        tmp_path = self.models_dir / f".{artifact_filename}.tmp"
        tmp_path.write_text(json.dumps(artifact_payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, artifact_path)
        digest = sha256_file(artifact_path)
        rel_path = f"models/{artifact_filename}"

        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO model_versions(
                    model_version_id, model_type, artifact_path, artifact_sha256,
                    trained_on_count, feature_names_json, metrics_json, status,
                    verified_for_telegram, created_ts, created_utc, approved_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_version_id) DO UPDATE SET
                    status=excluded.status,
                    verified_for_telegram=excluded.verified_for_telegram,
                    metrics_json=excluded.metrics_json,
                    approved_ts=excluded.approved_ts
                """,
                (
                    model_version_id,
                    model_type,
                    rel_path,
                    digest,
                    int(trained_on_count),
                    json.dumps(feature_names),
                    json.dumps(metrics),
                    status,
                    1 if verified_for_telegram else 0,
                    now,
                    ts_to_iso(now),
                    now if status == "APPROVED" else None,
                ),
            )
        return self.get_model_version(model_version_id) or {}

    def get_model_version(self, model_version_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM model_versions WHERE model_version_id = ?",
                (model_version_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def load_model_artifact(self, model_version_id: str) -> Dict[str, Any]:
        rec = self.get_model_version(model_version_id)
        if not rec:
            raise FileNotFoundError(f"Model version not found in database: {model_version_id}")
        artifact_full = self.data_dir / rec["artifact_path"]
        if not artifact_full.is_file():
            raise FileNotFoundError(f"Model artifact missing on disk: {artifact_full}")
        actual_sha = sha256_file(artifact_full)
        if actual_sha != rec["artifact_sha256"]:
            raise ValueError(
                f"Model artifact SHA-256 mismatch for {model_version_id}: expected {rec['artifact_sha256']}, got {actual_sha}"
            )
        return json.loads(artifact_full.read_text(encoding="utf-8"))

    def update_model_status(
        self, model_version_id: str, status: str, verified_for_telegram: Optional[bool] = None
    ) -> None:
        now = utc_now_ts()
        with self.transaction() as conn:
            if verified_for_telegram is not None:
                conn.execute(
                    """
                    UPDATE model_versions
                    SET status = ?, verified_for_telegram = ?,
                        approved_ts = CASE WHEN ? = 'APPROVED' THEN COALESCE(approved_ts, ?) ELSE approved_ts END
                    WHERE model_version_id = ?
                    """,
                    (status, 1 if verified_for_telegram else 0, status, now, model_version_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE model_versions
                    SET status = ?,
                        approved_ts = CASE WHEN ? = 'APPROVED' THEN COALESCE(approved_ts, ?) ELSE approved_ts END
                    WHERE model_version_id = ?
                    """,
                    (status, status, now, model_version_id),
                )

    def save_rule_version(
        self,
        rule_version_id: str,
        params: Dict[str, Any],
        source: str,
        parent_rule_version_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
        status: str = "CANDIDATE",
        verified_for_telegram: bool = False,
        ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        now = ts if ts is not None else utc_now_ts()
        merged = dict(DEFAULT_RULE_PARAMS)
        merged.update(params or {})
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO rule_versions(
                    rule_version_id, parent_rule_version_id, params_json, source,
                    proposal_id, status, verified_for_telegram, created_ts, created_utc, approved_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rule_version_id) DO UPDATE SET
                    status=excluded.status,
                    verified_for_telegram=excluded.verified_for_telegram,
                    approved_ts=excluded.approved_ts
                """,
                (
                    rule_version_id,
                    parent_rule_version_id,
                    json.dumps(merged, sort_keys=True),
                    source,
                    proposal_id,
                    status,
                    1 if verified_for_telegram else 0,
                    now,
                    ts_to_iso(now),
                    now if status == "APPROVED" else None,
                ),
            )
        return self.get_rule_version(rule_version_id) or {}

    def get_rule_version(self, rule_version_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM rule_versions WHERE rule_version_id = ?",
                (rule_version_id,),
            ).fetchone()
            if not row:
                return None
            d = self._row_to_dict(row)
            d["params"] = json.loads(d["params_json"])
            return d

    def update_rule_status(
        self, rule_version_id: str, status: str, verified_for_telegram: Optional[bool] = None
    ) -> None:
        now = utc_now_ts()
        with self.transaction() as conn:
            if verified_for_telegram is not None:
                conn.execute(
                    """
                    UPDATE rule_versions
                    SET status = ?, verified_for_telegram = ?,
                        approved_ts = CASE WHEN ? = 'APPROVED' THEN COALESCE(approved_ts, ?) ELSE approved_ts END
                    WHERE rule_version_id = ?
                    """,
                    (status, 1 if verified_for_telegram else 0, status, now, rule_version_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE rule_versions
                    SET status = ?,
                        approved_ts = CASE WHEN ? = 'APPROVED' THEN COALESCE(approved_ts, ?) ELSE approved_ts END
                    WHERE rule_version_id = ?
                    """,
                    (status, status, now, rule_version_id),
                )

    # ------------------------------------------------------------------
    # Observations, Signals, Skips, and Paper Trades
    # ------------------------------------------------------------------
    def record_observation(
        self,
        asset: str,
        expiry_sec: int,
        decision_ts: int,
        quote_ts: int,
        price: float,
        payout: float,
        fee_rate: float,
        tie_rule: str,
        feed_source: str,
        feed_kind: str,
        features: Dict[str, float],
    ) -> Tuple[str, bool]:
        obs_id = make_stable_id(
            "obs", f"{asset}|{expiry_sec}|{decision_ts}|{feed_kind}"
        )
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT obs_id FROM market_observations WHERE obs_id = ?", (obs_id,)
            ).fetchone()
            if existing:
                return str(existing["obs_id"]), False
            conn.execute(
                """
                INSERT INTO market_observations(
                    obs_id, asset, expiry_sec, decision_ts, decision_utc, quote_ts,
                    price, payout, fee_rate, tie_rule, feed_source, feed_kind, features_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obs_id,
                    asset,
                    int(expiry_sec),
                    int(decision_ts),
                    ts_to_iso(decision_ts),
                    int(quote_ts),
                    float(price),
                    float(payout),
                    float(fee_rate),
                    tie_rule,
                    feed_source,
                    feed_kind,
                    json.dumps(features, sort_keys=True),
                ),
            )
        return obs_id, True

    def update_observation_counterfactual(
        self, obs_id: str, settlement_price: float, outcome: str, settled_ts: int
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE market_observations
                SET counterfactual_settlement_price = ?,
                    counterfactual_outcome = ?,
                    counterfactual_settled_ts = ?
                WHERE obs_id = ? AND counterfactual_outcome IS NULL
                """,
                (float(settlement_price), outcome, int(settled_ts), obs_id),
            )

    def get_unlabeled_observations(self, now_ts: int, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM market_observations
                WHERE counterfactual_outcome IS NULL
                  AND (decision_ts + expiry_sec) <= ?
                ORDER BY decision_ts ASC
                LIMIT ?
                """,
                (int(now_ts), int(limit)),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def record_signal_or_skip(
        self,
        obs_id: str,
        asset: str,
        expiry_sec: int,
        decision_ts: int,
        direction: str,
        estimated_prob: float,
        break_even_prob: float,
        payout: float,
        action: str,
        skip_reason: Optional[str],
        model_version_id: str,
        rule_version_id: str,
        feed_kind: str,
        is_shadow: bool = False,
    ) -> Tuple[str, bool]:
        signal_id = make_stable_id(
            "sig",
            f"{obs_id}|{model_version_id}|{rule_version_id}|{'shadow' if is_shadow else 'live'}",
        )
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT signal_id FROM signals_and_skips WHERE signal_id = ?", (signal_id,)
            ).fetchone()
            if existing:
                return str(existing["signal_id"]), False
            conn.execute(
                """
                INSERT INTO signals_and_skips(
                    signal_id, obs_id, asset, expiry_sec, decision_ts, decision_utc,
                    direction, estimated_prob, break_even_prob, payout, action,
                    skip_reason, model_version_id, rule_version_id, feed_kind, is_shadow
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    obs_id,
                    asset,
                    int(expiry_sec),
                    int(decision_ts),
                    ts_to_iso(decision_ts),
                    direction,
                    round(float(estimated_prob), 6),
                    round(float(break_even_prob), 6),
                    float(payout),
                    action,
                    skip_reason,
                    model_version_id,
                    rule_version_id,
                    feed_kind,
                    1 if is_shadow else 0,
                ),
            )
        return signal_id, True

    def open_paper_trade(
        self,
        signal_id: str,
        obs_id: str,
        asset: str,
        expiry_sec: int,
        decision_ts: int,
        strike: float,
        direction: str,
        estimated_prob: float,
        stake: float,
        payout: float,
        fee_rate: float,
        tie_rule: str,
        model_version_id: str,
        rule_version_id: str,
        feed_kind: str,
        is_shadow: bool = False,
    ) -> Tuple[Dict[str, Any], bool]:
        trade_id = make_stable_id("trd", f"{signal_id}")
        expiry_ts = int(decision_ts) + int(expiry_sec)
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM paper_trades WHERE signal_id = ?", (signal_id,)
            ).fetchone()
            if existing:
                return self._row_to_dict(existing), False
            conn.execute(
                """
                INSERT INTO paper_trades(
                    trade_id, signal_id, obs_id, asset, expiry_sec, decision_ts, decision_utc,
                    strike, direction, expiry_ts, expiry_utc, estimated_prob, stake, payout,
                    fee_rate, tie_rule, model_version_id, rule_version_id, feed_kind,
                    is_shadow, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
                """,
                (
                    trade_id,
                    signal_id,
                    obs_id,
                    asset,
                    int(expiry_sec),
                    int(decision_ts),
                    ts_to_iso(decision_ts),
                    float(strike),
                    direction,
                    expiry_ts,
                    ts_to_iso(expiry_ts),
                    round(float(estimated_prob), 6),
                    float(stake),
                    float(payout),
                    float(fee_rate),
                    tie_rule,
                    model_version_id,
                    rule_version_id,
                    feed_kind,
                    1 if is_shadow else 0,
                ),
            )
            row = conn.execute(
                "SELECT * FROM paper_trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            return self._row_to_dict(row), True

    def count_open_trades(self, is_shadow: bool = False) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM paper_trades WHERE status = 'OPEN' AND is_shadow = ?",
                (1 if is_shadow else 0,),
            ).fetchone()
            return int(row["c"]) if row else 0

    def get_open_trades(self, due_before_ts: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if due_before_ts is not None:
                rows = self._conn.execute(
                    "SELECT * FROM paper_trades WHERE status = 'OPEN' AND expiry_ts <= ? ORDER BY expiry_ts ASC",
                    (int(due_before_ts),),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM paper_trades WHERE status = 'OPEN' ORDER BY expiry_ts ASC"
                ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_recent_expiry_scores(
        self, model_id: str, rule_id: str, now_ts: int
    ) -> Dict[Tuple[str, int], float]:
        """Return recent paper return per unit staked, grouped by asset and expiry."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT asset, expiry_sec, SUM(net_result) / SUM(stake) AS score
                FROM paper_trades
                WHERE feed_kind = 'platform_settled' AND is_shadow = 0
                  AND model_version_id = ? AND rule_version_id = ?
                  AND status IN ('WIN', 'LOSS', 'TIE')
                  AND settled_ts BETWEEN ? AND ?
                GROUP BY asset, expiry_sec
                HAVING COUNT(*) >= 10 AND SUM(stake) > 0
                """,
                (model_id, rule_id, int(now_ts) - 7 * 86400, int(now_ts)),
            ).fetchall()
        return {(str(row["asset"]), int(row["expiry_sec"])): float(row["score"]) for row in rows}

    def get_trade(self, trade_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM paper_trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def settle_paper_trade(
        self,
        trade_id: str,
        settlement_price: Optional[float],
        settled_ts: int,
        settlement_event_id: Optional[str] = None,
        mark_unresolved: bool = False,
        outcome_known_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Transactional, strictly idempotent paper trade settlement.
        Duplicate settlement calls return {'ok': True, 'duplicate': True, 'trade': ...}
        without modifying stored outcome or net_result.
        """
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM paper_trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": f"Trade not found: {trade_id}"}
            trade = self._row_to_dict(row)
            if trade["status"] != "OPEN":
                return {"ok": True, "duplicate": True, "trade": trade}

            if mark_unresolved or settlement_price is None:
                status = "UNRESOLVED"
                net_result = 0.0
                s_price = None
            else:
                s_price = float(settlement_price)
                strike = float(trade["strike"])
                stake = float(trade["stake"])
                payout = float(trade["payout"])
                fee_rate = float(trade["fee_rate"])
                tie_rule = str(trade["tie_rule"] or "refund")
                direction = str(trade["direction"]).upper()
                diff = s_price - strike
                if abs(diff) <= 1e-9:
                    status = "TIE"
                    if tie_rule == "loss":
                        net_result = round(-stake * (1.0 + fee_rate), 4)
                    else:
                        net_result = round(-stake * fee_rate, 4)
                else:
                    won = (direction == "CALL" and diff > 0) or (direction == "PUT" and diff < 0)
                    if won:
                        status = "WIN"
                        net_result = round(stake * (payout - fee_rate), 4)
                    else:
                        status = "LOSS"
                        net_result = round(-stake * (1.0 + fee_rate), 4)

            ev_id = settlement_event_id or make_stable_id("stl", f"{trade_id}|{settled_ts}")
            cur = conn.execute(
                """
                UPDATE paper_trades
                SET status = ?, settlement_price = ?, settled_ts = ?, settled_utc = ?,
                    net_result = ?, settlement_event_id = ?
                WHERE trade_id = ? AND status = 'OPEN'
                """,
                (
                    status,
                    s_price,
                    int(settled_ts),
                    ts_to_iso(settled_ts),
                    net_result,
                    ev_id,
                    trade_id,
                ),
            )
            if cur.rowcount == 0:
                latest = conn.execute(
                    "SELECT * FROM paper_trades WHERE trade_id = ?", (trade_id,)
                ).fetchone()
                return {"ok": True, "duplicate": True, "trade": self._row_to_dict(latest)}

            # Also backfill counterfactual label on the observation if known
            if s_price is not None:
                obs_outcome = (
                    "TIE"
                    if abs(s_price - float(trade["strike"])) <= 1e-9
                    else ("UP" if s_price > float(trade["strike"]) else "DOWN")
                )
                conn.execute(
                    """
                    UPDATE market_observations
                    SET counterfactual_settlement_price = COALESCE(counterfactual_settlement_price, ?),
                        counterfactual_outcome = COALESCE(counterfactual_outcome, ?),
                        counterfactual_settled_ts = COALESCE(counterfactual_settled_ts, ?)
                    WHERE obs_id = ?
                    """,
                    (s_price, obs_outcome, int(outcome_known_ts if outcome_known_ts is not None else settled_ts), trade["obs_id"]),
                )

            updated = conn.execute(
                "SELECT * FROM paper_trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            return {"ok": True, "duplicate": False, "trade": self._row_to_dict(updated)}

    def get_strategy_rolling_win_rates(
        self, min_trades: int = 5, lookback: int = 30
    ) -> Dict[str, Dict[str, Any]]:
        """
        Calculates rolling win rates per strategy / model rule from recent settled paper trades.
        Strategies with win rate < 52% over >= 5 trades are flagged for quarantine.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT model_version_id, status
                FROM paper_trades
                WHERE status IN ('WIN', 'LOSS', 'TIE')
                ORDER BY settled_ts DESC
                LIMIT ?
                """,
                (int(lookback),),
            ).fetchall()

            stats: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                sid = r[0] or "unknown"
                st = r[1]
                if sid not in stats:
                    stats[sid] = {"wins": 0, "losses": 0, "ties": 0, "total": 0}
                stats[sid]["total"] += 1
                if st == "WIN":
                    stats[sid]["wins"] += 1
                elif st == "LOSS":
                    stats[sid]["losses"] += 1
                elif st == "TIE":
                    stats[sid]["ties"] += 1

            for sid, d in stats.items():
                decisive = d["wins"] + d["losses"]
                wr = (d["wins"] / decisive) if decisive > 0 else 0.5
                d["win_rate"] = round(wr, 4)
                d["is_quarantined"] = (d["total"] >= min_trades) and (wr < 0.52)
            return stats

    # ------------------------------------------------------------------
    # Evaluation Runs
    # ------------------------------------------------------------------
    def record_evaluation_run(
        self,
        eval_type: str,
        candidate_model_id: str,
        candidate_rule_id: str,
        incumbent_model_id: str,
        incumbent_rule_id: str,
        feed_kind: str,
        passed: bool,
        reason: str,
        criteria: Dict[str, Any],
        summary: Dict[str, Any],
        breakdown: Dict[str, Any],
        ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        now = ts if ts is not None else utc_now_ts()
        eval_id = make_stable_id("evl")
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_runs(
                    eval_id, eval_type, candidate_model_id, candidate_rule_id,
                    incumbent_model_id, incumbent_rule_id, feed_kind, passed,
                    reason, criteria_json, summary_json, breakdown_json,
                    created_ts, created_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eval_id,
                    eval_type,
                    candidate_model_id,
                    candidate_rule_id,
                    incumbent_model_id,
                    incumbent_rule_id,
                    feed_kind,
                    1 if passed else 0,
                    reason,
                    json.dumps(criteria),
                    json.dumps(summary),
                    json.dumps(breakdown),
                    now,
                    ts_to_iso(now),
                ),
            )
            row = conn.execute(
                "SELECT * FROM evaluation_runs WHERE eval_id = ?", (eval_id,)
            ).fetchone()
            return self._row_to_dict(row)

    # ------------------------------------------------------------------
    # History Querying & Exporting (with Date, Asset, Strategy, Feed Kind Filters)
    # ------------------------------------------------------------------
    def query_history(
        self,
        table: str,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        asset: Optional[str] = None,
        strategy_version: Optional[str] = None,
        feed_kind: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        allowed_tables: Dict[str, Dict[str, Optional[str]]] = {
            "market_observations": {"ts": "decision_ts", "asset": "asset", "strategy": None, "feed": "feed_kind"},
            "signals_and_skips": {"ts": "decision_ts", "asset": "asset", "strategy": "rule_version_id", "feed": "feed_kind"},
            "paper_trades": {"ts": "decision_ts", "asset": "asset", "strategy": "rule_version_id", "feed": "feed_kind"},
            "model_versions": {"ts": "created_ts", "asset": None, "strategy": "model_version_id", "feed": None},
            "rule_versions": {"ts": "created_ts", "asset": None, "strategy": "rule_version_id", "feed": None},
            "evaluation_runs": {"ts": "created_ts", "asset": None, "strategy": "candidate_rule_id", "feed": "feed_kind"},
            "ai_feedback_queue": {"ts": "created_ts", "asset": None, "strategy": None, "feed": None},
            "ai_proposals": {"ts": "created_ts", "asset": None, "strategy": "candidate_rule_id", "feed": None},
            "telegram_outbox": {"ts": "created_ts", "asset": "asset", "strategy": None, "feed": None},
            "stubay_memory": {"ts": "bucket", "asset": "asset", "strategy": None, "feed": None},
            "audit_logs": {"ts": "ts", "asset": None, "strategy": None, "feed": None},
        }
        if table not in allowed_tables:
            raise ValueError(f"Unsupported history table: {table}")
        meta = allowed_tables[table]
        clauses: List[str] = []
        params: List[Any] = []
        if from_ts is not None and meta["ts"]:
            clauses.append(f"{meta['ts']} >= ?")
            params.append(int(from_ts))
        if to_ts is not None and meta["ts"]:
            clauses.append(f"{meta['ts']} <= ?")
            params.append(int(to_ts))
        if asset and meta["asset"]:
            clauses.append(f"{meta['asset']} = ?")
            params.append(asset.strip().upper())
        if strategy_version and meta["strategy"]:
            if table in ("signals_and_skips", "paper_trades"):
                clauses.append("(rule_version_id = ? OR model_version_id = ?)")
                params.extend([strategy_version.strip(), strategy_version.strip()])
            else:
                clauses.append(f"{meta['strategy']} = ?")
                params.append(strategy_version.strip())
        if feed_kind and meta["feed"]:
            clauses.append(f"{meta['feed']} = ?")
            params.append(feed_kind.strip())

        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        order_col = meta["ts"] or "rowid"
        columns = {
            "stubay_memory": "bucket, asset, status, plan_json, entry_ts, trade_id",
            "telegram_outbox": "outbox_id, signal_id, chat_id, asset, direction, payload_text, status, attempts, max_attempts, expiry_ts, telegram_message_id, last_error, created_ts, created_utc, updated_ts",
        }.get(table, "*")
        sql = f"SELECT {columns} FROM {table}{where_sql} ORDER BY {order_col} DESC LIMIT ?"
        params.append(max(1, min(5000, int(limit))))

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def export_history_csv(self, rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return ""
        buf = io.StringIO()
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        return buf.getvalue()

    def get_labeled_dataset(self, feed_kind: str) -> List[Dict[str, Any]]:
        """
        Return chronological labeled observations (both traded and skipped counterfactuals)
        for a single feed_kind — NEVER mixing proxy and platform-settled results.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    o.obs_id, o.asset, o.expiry_sec, o.decision_ts, o.price,
                    o.payout, o.fee_rate, o.tie_rule, o.feed_kind, o.features_json,
                    o.counterfactual_settlement_price AS settlement_price,
                    o.counterfactual_outcome AS outcome,
                    o.counterfactual_settled_ts AS outcome_known_ts
                FROM market_observations o
                WHERE o.feed_kind = ?
                  AND o.counterfactual_outcome IN ('UP', 'DOWN', 'TIE')
                  AND o.counterfactual_settled_ts IS NOT NULL
                ORDER BY o.decision_ts ASC
                """,
                (feed_kind,),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                d = self._row_to_dict(r)
                d["features"] = json.loads(d["features_json"])
                out.append(d)
            return out

    # ------------------------------------------------------------------
    # Atomic Backup & Restore (Database + Model Artifacts Together)
    # ------------------------------------------------------------------
    def create_backup(self, label: str = "manual", destination: Optional[Path] = None) -> Dict[str, Any]:
        now = utc_now_ts()
        stamp = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        safe_label = "".join(c for c in label if c.isalnum() or c in ("-", "_"))[:24] or "backup"
        archive_name = f"paper_lab_{safe_label}_{stamp}_{uuid.uuid4().hex[:8]}.tar.gz"
        backup_dir = destination if destination is not None else self.backups_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        archive_path = backup_dir / archive_name

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            db_snap = tmp_dir / "paper_lab.sqlite3"
            with self._lock:
                dst = sqlite3.connect(str(db_snap))
                try:
                    self._conn.backup(dst)
                finally:
                    dst.close()

            manifest: Dict[str, Any] = {
                "created_ts": now,
                "created_utc": ts_to_iso(now),
                "schema_version": self.get_schema_version(),
                "files": {
                    "paper_lab.sqlite3": sha256_file(db_snap),
                },
            }

            models_snap = tmp_dir / "models"
            models_snap.mkdir(parents=True, exist_ok=True)
            for mf in sorted(self.models_dir.glob("*.json")):
                dest_m = models_snap / mf.name
                shutil.copy2(mf, dest_m)
                manifest["files"][f"models/{mf.name}"] = sha256_file(dest_m)

            manifest_path = tmp_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

            tmp_archive = backup_dir / f".{archive_name}.tmp"
            try:
                with tarfile.open(tmp_archive, "w:gz") as tar:
                    tar.add(db_snap, arcname="paper_lab.sqlite3")
                    tar.add(manifest_path, arcname="manifest.json")
                    tar.add(models_snap, arcname="models")
                os.replace(tmp_archive, archive_path)
            finally:
                tmp_archive.unlink(missing_ok=True)

        self.log_event(
            "Backup",
            "INFO",
            f"Created atomic DB + model backup {archive_name}",
            {"files_count": len(manifest["files"]), "schema_version": manifest["schema_version"]},
        )
        return {
            "ok": True,
            "archive_name": archive_name,
            "archive_path": str(archive_path),
            "files_count": len(manifest["files"]),
            "created_utc": manifest["created_utc"],
        }

    @classmethod
    def restore_backup(cls, archive_path: Path | str, target_data_dir: Path | str) -> Dict[str, Any]:
        archive = Path(archive_path).expanduser().resolve()
        target = Path(target_data_dir).expanduser().resolve()
        if not archive.is_file():
            raise FileNotFoundError(f"Backup archive not found: {archive}")
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError("Choose a new or empty restore directory. Existing Stubay data is protected.")

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar.getmembers():
                    member_path = (tmp_dir / member.name).resolve()
                    if (
                        not member_path.is_relative_to(tmp_dir)
                        or not (member.isfile() or member.isdir())
                    ):
                        raise ValueError(f"Unsafe path in backup archive: {member.name}")
                tar.extractall(tmp_dir)

            manifest_path = tmp_dir / "manifest.json"
            if not manifest_path.is_file():
                raise ValueError("Backup archive missing manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            checksums = manifest.get("files", {})
            if not isinstance(checksums, dict) or "paper_lab.sqlite3" not in checksums:
                raise ValueError("Backup manifest does not contain a database checksum.")
            for rel_file, expected_sha in checksums.items():
                extracted = (tmp_dir / rel_file).resolve()
                if not extracted.is_relative_to(tmp_dir):
                    raise ValueError(f"Unsafe path in backup manifest: {rel_file}")
                if not extracted.is_file():
                    raise ValueError(f"Backup file missing: {rel_file}")
                actual_sha = sha256_file(extracted)
                if actual_sha != expected_sha:
                    raise ValueError(
                        f"Checksum mismatch for {rel_file}: expected {expected_sha}, got {actual_sha}"
                    )

            target.mkdir(parents=True, exist_ok=True)
            (target / "models").mkdir(parents=True, exist_ok=True)
            (target / "backups").mkdir(parents=True, exist_ok=True)

            files = [tmp_dir / "paper_lab.sqlite3"]
            files.extend(
                model for model in (tmp_dir / "models").glob("*.json")
                if model.relative_to(tmp_dir).as_posix() in checksums
            )
            for source in files:
                with source.open("rb") as contents, (target / source.relative_to(tmp_dir)).open("xb") as restored_file:
                    shutil.copyfileobj(contents, restored_file)

        # Verify restored store & active model artifact load cleanly
        restored = cls(target)
        try:
            active_model_id = restored.get_state("active_model_version_id")
            if active_model_id:
                restored.load_model_artifact(active_model_id)
            trades_count = restored._conn.execute("SELECT COUNT(*) AS c FROM paper_trades").fetchone()["c"]
            return {
                "ok": True,
                "target_data_dir": str(target),
                "schema_version": restored.get_schema_version(),
                "active_model_version_id": active_model_id,
                "trades_restored": int(trades_count),
                "files_verified": len(manifest.get("files", {})),
            }
        finally:
            restored.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {k: row[k] for k in row.keys()}
