"""
Configuration, allowlists, bounds, and secret redaction for SignalDesk Paper Lab.
Secrets (Telegram bot token, AI API keys) are read strictly from environment
variables or external secret files and are NEVER persisted to SQLite.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Hard Python bounds for AI-proposable strategy rule fields.
# AI output is treated strictly as data and validated against these exact bounds.
ALLOWED_RULE_FIELDS: Dict[str, Dict[str, Any]] = {
    "min_signal_probability": {"type": "float", "min": 0.52, "max": 0.85},
    "min_edge_margin": {"type": "float", "min": 0.005, "max": 0.15},
    "volatility_min": {"type": "float", "min": 0.0, "max": 0.02},
    "volatility_max": {"type": "float", "min": 0.0005, "max": 0.10},
    "allowed_utc_hours": {"type": "int_list", "min": 0, "max": 23, "min_len": 1, "max_len": 24},
    "max_concurrent_open_trades": {"type": "int", "min": 1, "max": 10},
}

DEFAULT_RULE_PARAMS: Dict[str, Any] = {
    "min_signal_probability": 0.58,
    "min_edge_margin": 0.02,
    "volatility_min": 0.00005,
    "volatility_max": 0.015,
    "allowed_utc_hours": list(range(24)),
    "max_concurrent_open_trades": 3,
}


def compute_break_even_probability(
    payout: float,
    fee_rate: float = 0.0,
    tie_rule: str = "refund",
    assumed_tie_rate: float = 0.0,
) -> float:
    """
    Compute exact break-even win probability given payout, fees, and tie rule.
    Example: payout=0.80, fee_rate=0.0, tie_rule='refund' -> 1.0 / 1.8 = 0.5556 (55.6%).
    If tie_rule == 'loss', ties count as losses so break-even shifts upward.
    """
    p = max(0.05, min(2.0, float(payout)))
    f = max(0.0, min(0.25, float(fee_rate)))
    # Win net return = p - f; Loss net return = -(1 + f)
    # Break-even: w*(p - f) - (1 - w - t)*(1 + f) - (t*(1 + f) if tie=='loss' else t*f) = 0
    if tie_rule == "loss":
        # w*(p - f) = (1 - w)*(1 + f)  =>  w*(p + 1) = 1 + f
        return min(0.99, (1.0 + f) / (1.0 + p))
    # Refund on tie: conditional on decisive outcome (1 - t), EV = 0 when w_cond = (1 + f)/(1 + p)
    t = max(0.0, min(0.2, float(assumed_tie_rate)))
    if p <= f:
        return 0.99
    return min(0.99, ((1.0 - t) * (1.0 + f) + t * f) / (1.0 + p))


def _is_placeholder_env_value(val: str) -> bool:
    low = (val or "").strip().lower()
    if not low:
        return True
    bad_markers = (
        "set by your own",
        "your_",
        "replace_me",
        "placeholder",
        "optional",
    )
    return any(m in low for m in bad_markers)


def default_data_dir() -> Path:
    """
    Resolve a persistent application data directory outside temporary folders.
    Respects PAPER_LAB_DATA_DIR if set to a valid path; otherwise falls back to
    <repo>/data/paper_lab.
    """
    env_dir = os.environ.get("PAPER_LAB_DATA_DIR", "").strip()
    if env_dir and not _is_placeholder_env_value(env_dir):
        candidate = Path(env_dir).expanduser().resolve()
        if not candidate.is_file():
            return candidate
    repo_data = Path(__file__).resolve().parent.parent.parent / "data" / "paper_lab"
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        shared = Path(local_app_data) / "SignalDesk Data" / "paper_lab"
        if "SIGNALDESK_DESKTOP" in os.environ or (shared / "paper_lab.sqlite3").is_file():
            return shared.resolve()
    return repo_data.resolve()


def default_backup_dir() -> Path:
    """Keep automatic backups outside the working checkout and preview copy."""
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "SignalDesk Data" / "Stubay Backups"
    return Path.home() / ".local" / "share" / "SignalDesk" / "Stubay Backups"


STUBAY_NOTICE_SECONDS = 60


@dataclass
class EvaluationCriteria:
    """Preconfigured promotion criteria — must be fixed before evaluating candidates."""

    n_walk_forward_windows: int = 3
    min_train_samples: int = 30
    min_trades_total: int = 12
    min_trades_per_window: int = 3
    min_windows_beating_incumbent: int = 2
    min_net_return_improvement: float = 0.0
    max_drawdown_pct: float = 35.0
    max_brier_degradation: float = 0.02
    min_shadow_trades: int = 10
    min_shadow_net_improvement: float = 0.0
    min_asset_breakdown_samples: int = 5


@dataclass
class RollbackThresholds:
    """Deterioration thresholds for automatic rollback or pause of live approved strategy."""

    rolling_window_trades: int = 15
    min_rolling_win_rate: float = 0.45
    max_consecutive_losses: int = 6
    max_rolling_drawdown_pct: float = 25.0


@dataclass
class PaperLabConfig:
    paper_only: bool = True
    data_dir: str = ""
    auto_loop_enabled: bool = True
    auto_loop_interval_sec: int = 15
    asset_allowlist: List[str] = field(
        default_factory=lambda: ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "AUDUSD-OTC"]
    )
    expiry_allowlist_sec: List[int] = field(default_factory=lambda: [60, 120, 300])
    default_stake: float = 10.0
    starting_balance: float = 1000.0
    default_payout: float = 0.80
    min_payout_floor_pct: float = 80.0
    strict_entry_window_sec: int = 10
    fee_rate: float = 0.0
    tie_rule: str = "refund"  # "refund" or "loss"
    max_quote_age_sec: int = 15
    settlement_tolerance_sec: int = 10
    retrain_min_new_outcomes: int = 20
    retrain_interval_sec: int = 1800
    max_concurrent_workers: int = 4

    # AI Feedback Queue limits
    ai_batch_size: int = 5
    ai_max_retries: int = 5
    ai_retry_base_delay_sec: int = 30
    ai_min_request_interval_sec: int = 30
    ai_max_requests_per_hour: int = 20

    # Telegram settings (bot token is NEVER saved in SQLite or config files!)
    telegram_enabled: bool = False
    telegram_allowed_chat_ids: List[str] = field(default_factory=list)
    telegram_min_actionable_sec_left: int = 20
    telegram_max_retries: int = 3
    telegram_request_timeout_sec: int = 8
    invert_signals: bool = False

    # Evaluation & Rollback
    evaluation: EvaluationCriteria = field(default_factory=EvaluationCriteria)
    rollback: RollbackThresholds = field(default_factory=RollbackThresholds)

    # Initial strategy rules
    initial_rules: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_RULE_PARAMS))

    def resolved_data_dir(self) -> Path:
        if self.data_dir and self.data_dir.strip():
            return Path(self.data_dir).expanduser().resolve()
        return default_data_dir()

    def db_path(self) -> Path:
        return self.resolved_data_dir() / "paper_lab.sqlite3"

    def models_dir(self) -> Path:
        return self.resolved_data_dir() / "models"

    def backups_dir(self) -> Path:
        return self.resolved_data_dir() / "backups"

    def to_safe_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "PaperLabConfig":
        raw = dict(raw or {})
        eval_raw = raw.pop("evaluation", None)
        rb_raw = raw.pop("rollback", None)
        cfg = cls()
        for k, v in raw.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        cfg.paper_only = True  # Hard enforced: system is paper-only
        if isinstance(eval_raw, dict):
            for k, v in eval_raw.items():
                if hasattr(cfg.evaluation, k):
                    setattr(cfg.evaluation, k, v)
        if isinstance(rb_raw, dict):
            for k, v in rb_raw.items():
                if hasattr(cfg.rollback, k):
                    setattr(cfg.rollback, k, v)
        # Ensure allowlists are clean
        cfg.asset_allowlist = [str(a).strip().upper() for a in cfg.asset_allowlist if str(a).strip()]
        cfg.expiry_allowlist_sec = sorted({int(e) for e in cfg.expiry_allowlist_sec if int(e) > 0})
        cfg.telegram_allowed_chat_ids = [
            str(c).strip() for c in cfg.telegram_allowed_chat_ids if str(c).strip()
        ]
        merged_rules = dict(DEFAULT_RULE_PARAMS)
        if isinstance(cfg.initial_rules, dict):
            merged_rules.update(cfg.initial_rules)
        cfg.initial_rules = merged_rules
        return cfg


def default_config_path() -> Path:
    fallback = (Path(__file__).resolve().parent.parent / "paper_lab_config.json").resolve()
    env_cfg = os.environ.get("PAPER_LAB_CONFIG", "").strip()
    if env_cfg and not _is_placeholder_env_value(env_cfg):
        candidate = Path(env_cfg).expanduser().resolve()
        if not candidate.is_dir() and candidate.suffix.lower() == ".json":
            return candidate
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        shared = Path(local_app_data) / "SignalDesk Data"
        if "SIGNALDESK_DESKTOP" in os.environ or (shared / "paper_lab" / "paper_lab.sqlite3").is_file():
            return shared / "paper_lab_config.json"
    return fallback


def load_paper_lab_config(config_path: Optional[str | Path] = None) -> PaperLabConfig:
    if config_path is None:
        candidate = default_config_path()
        if candidate.is_file():
            config_path = candidate
    if config_path is not None:
        p = Path(config_path)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return PaperLabConfig.from_dict(data)
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError(f"Paper Lab could not read its configuration at {p}; no replacement store was opened.") from exc
    return PaperLabConfig()


def save_paper_lab_config(
    cfg: PaperLabConfig, config_path: Optional[str | Path] = None
) -> Path:
    """Persist non-secret PaperLabConfig atomically to JSON. Never writes API keys or bot tokens."""
    target = Path(config_path).expanduser().resolve() if config_path else default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    safe = cfg.to_safe_dict()
    safe["paper_only"] = True
    safe["data_dir"] = str(cfg.resolved_data_dir())
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(safe, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def _secrets_dir() -> Path:
    """External secret directory outside SQLite and outside config JSON."""
    d = default_data_dir() / ".secrets"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except Exception:
        pass
    return d


def save_external_secret(name: str, value: str) -> None:
    """Store a secret (e.g., telegram_bot_token) in a 0600 external file outside SQLite."""
    safe_name = re.sub(r"[^a-z0-9_]", "", (name or "").lower())
    if not safe_name:
        return
    path = _secrets_dir() / safe_name
    clean = (value or "").strip()
    if not clean:
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(clean, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(path)


def read_external_secret(name: str) -> str:
    safe_name = re.sub(r"[^a-z0-9_]", "", (name or "").lower())
    if not safe_name:
        return ""
    path = _secrets_dir() / safe_name
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""


def get_telegram_bot_token() -> str:
    """
    Read Telegram bot token ONLY from environment variable or external secret file.
    Never stored in SQLite.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token and not _is_placeholder_env_value(token):
        return token
    secret_file = os.environ.get("TELEGRAM_BOT_TOKEN_FILE", "").strip()
    if secret_file and not _is_placeholder_env_value(secret_file) and Path(secret_file).is_file():
        return Path(secret_file).read_text(encoding="utf-8").strip()
    return read_external_secret("telegram_bot_token")


_SECRET_PATTERNS = [
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"),  # Telegram bot token
    re.compile(r"\bAIza[0-9A-Za-z\-_]{25,}\b"),     # Gemini API key
    re.compile(r"\bsk-[A-Za-z0-9\-_]{16,}\b"),     # OpenAI API key
    re.compile(r"\bgsk_[A-Za-z0-9\-_]{16,}\b"),    # Groq API key
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # Email PII
]

_SENSITIVE_KEY_NAMES = {
    "api_key",
    "apikey",
    "bot_token",
    "token",
    "password",
    "password_hash",
    "secret",
    "cookie",
    "ssid",
    "authorization",
    "email",
    "phone",
    "session_token",
}


def redact_text(text: str) -> str:
    """Redact API keys, Telegram bot tokens, and emails from log/text output."""
    if not text:
        return text
    out = str(text)
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    # Also redact active runtime secrets if present
    for env_key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"):
        val = os.environ.get(env_key, "").strip()
        if val and len(val) >= 8 and val in out:
            out = out.replace(val, "[REDACTED]")
    for sec_name in ("telegram_bot_token", "gemini_api_key", "openai_api_key", "groq_api_key"):
        val = read_external_secret(sec_name)
        if val and len(val) >= 8 and val in out:
            out = out.replace(val, "[REDACTED]")
    return out


def sanitize_for_external(obj: Any) -> Any:
    """Recursively strip sensitive keys and redact secrets before sending to AI APIs or logs."""
    if isinstance(obj, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).lower() in _SENSITIVE_KEY_NAMES:
                continue
            cleaned[k] = sanitize_for_external(v)
        return cleaned
    if isinstance(obj, list):
        return [sanitize_for_external(item) for item in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def validate_rule_patch(patch: Dict[str, Any], current_rules: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], List[str]]:
    """
    Validate a proposed rule configuration patch against ALLOWED_RULE_FIELDS and hard Python bounds.
    Returns (is_valid, merged_rules, errors).
    """
    errors: List[str] = []
    if not isinstance(patch, dict):
        return False, dict(current_rules), ["proposed_changes must be a JSON object"]

    merged = dict(DEFAULT_RULE_PARAMS)
    merged.update(current_rules or {})

    for key, val in patch.items():
        if key not in ALLOWED_RULE_FIELDS:
            errors.append(f"Field '{key}' is not in the allowed configurable rule fields.")
            continue
        spec = ALLOWED_RULE_FIELDS[key]
        ftype = spec["type"]
        if ftype == "float":
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                errors.append(f"Field '{key}' must be a number.")
                continue
            fval = float(val)
            if fval != fval or fval < spec["min"] or fval > spec["max"]:
                errors.append(f"Field '{key}'={fval} out of bounds [{spec['min']}, {spec['max']}].")
                continue
            merged[key] = round(fval, 6)
        elif ftype == "int":
            if isinstance(val, bool) or not isinstance(val, int):
                errors.append(f"Field '{key}' must be an integer.")
                continue
            if val < spec["min"] or val > spec["max"]:
                errors.append(f"Field '{key}'={val} out of bounds [{spec['min']}, {spec['max']}].")
                continue
            merged[key] = int(val)
        elif ftype == "int_list":
            if not isinstance(val, list):
                errors.append(f"Field '{key}' must be a list of integers.")
                continue
            if len(val) < spec["min_len"] or len(val) > spec["max_len"]:
                errors.append(
                    f"Field '{key}' length {len(val)} out of bounds [{spec['min_len']}, {spec['max_len']}]."
                )
                continue
            clean_list: List[int] = []
            bad_item = False
            for item in val:
                if isinstance(item, bool) or not isinstance(item, int) or item < spec["min"] or item > spec["max"]:
                    errors.append(f"Field '{key}' contains invalid hour value: {item!r}.")
                    bad_item = True
                    break
                clean_list.append(int(item))
            if not bad_item:
                merged[key] = sorted(set(clean_list))

    # Cross-field invariant: volatility_min < volatility_max
    if merged["volatility_min"] >= merged["volatility_max"]:
        errors.append(
            f"volatility_min ({merged['volatility_min']}) must be strictly less than volatility_max ({merged['volatility_max']})."
        )

    return len(errors) == 0, merged, errors
