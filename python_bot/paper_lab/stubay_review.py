"""Stubay review stub - external LLM review integration removed."""
from __future__ import annotations
import threading
from typing import Any, Dict, List


MODEL_KEY = "stubay_review_model"
REVIEW_PREFIX = "stubay_review:v2:"
LATEST_KEY = "stubay_review_latest"
TREND_PREFIX = "stubay_review_trend:"
LATEST_TREND_KEY = "stubay_review_latest_trend"
LANE_KEY = "stubay_review_last_lane"
LEASE_KEY = "stubay_review_worker_lease"


def candidate_rules(store: Any, asset: str) -> List[Dict[str, Any]]:
    return []


class StubayReview:
    """Disabled review coordinator with external review integration removed."""

    def __init__(self, store: Any):
        self.store = store
        self.status = "Off"
        self.closed = threading.Event()

    def configure(self, enabled: bool, model: str) -> None:
        self.status = "Off"

    def schedule(self) -> None:
        pass

    def overview(self) -> Dict[str, Any]:
        return {
            "model": "",
            "status": "Off",
            "latest": None,
            "latest_trend": None,
        }
