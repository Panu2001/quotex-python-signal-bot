"""
SignalDesk Paper-Trading Learning Lab
=====================================
Persistent, SQLite-backed, paper-only binary options learning system with:
- Strict allowlist & decision-time market observation validation
- Chronological walk-forward testing & probability calibration
- Persistent AI feedback queue with strict schema-bounded proposals
- Parallel shadow paper testing, automatic promotion & rollback
- Persistent Telegram PAPER SIGNAL outbox with idempotency & timeout safety
- Authenticated history access, export, and atomic DB + model backup/restore
"""

from .config import PaperLabConfig, load_paper_lab_config
from .storage import PaperLabStore
from .engine_service import PaperLabService

__all__ = [
    "PaperLabConfig",
    "load_paper_lab_config",
    "PaperLabStore",
    "PaperLabService",
]
