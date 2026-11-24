#!/usr/bin/env python3
"""
Standalone Background Paper-Trading Service for SignalDesk Paper Lab.
Runs independently of dashboard user login/logout sessions:
- Polls ONLY allowlisted assets with bounded concurrency.
- Persists observations, features, signals, skips, paper trades, and settlements to SQLite.
- Reconciles open trades and counterfactual labels on restart and every tick.
- Processes persistent AI feedback queue & Telegram PAPER SIGNAL outbox safely.

Usage:
  export TELEGRAM_BOT_TOKEN="..."      # optional, kept outside DB
  python3 python_bot/paper_lab_runner.py --config python_bot/paper_lab_config.sample.json
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BOT_DIR = Path(__file__).resolve().parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

import market_feed as mf
from quotex_client import LIVE_QUOTEX_MANAGER, is_pyquotex_connected
from paper_lab import PaperLabService, load_paper_lab_config
from paper_lab.storage import utc_now_ts


def _quote_lookup_from_quotex(
    asset: str, target_expiry_ts: int, tolerance_sec: int,
    feeds: Dict[Tuple[str, str], Dict[str, Any]],
) -> Optional[Tuple[float, int]]:
    """
    Look up exact settlement price from authenticated Quotex live candles within tolerance_sec.
    Checks fine-grained 5s/15s candles first so trades opened mid-minute match their exact
    expiry timestamp within tolerance, falling back to 1m candles. Returns None if no candle
    exists within tolerance (so caller leaves the trade UNRESOLVED rather than guessing).
    """
    now = utc_now_ts()
    best: Optional[Tuple[int, float, int]] = None
    for tf, bar_sec, lim in (("5s", 5, 120), ("15s", 15, 80), ("1m", 60, 30)):
        key = (asset, tf)
        if key not in feeds:
            feeds[key] = mf.get_candles(symbol=asset, timeframe=tf, limit=lim, priority=False, background=True)
        feed = feeds[key]
        if not feed.get("is_live") or not feed.get("candles"):
            continue
        for c in feed["candles"]:
            close_ts = int(c["time"]) + bar_sec
            dist = abs(close_ts - target_expiry_ts)
            if close_ts <= now and dist <= tolerance_sec and (best is None or dist < best[0]):
                best = (dist, float(c["close"]), close_ts)
        if best is not None and best[0] <= 5:
            break
    if best is not None:
        return best[1], best[2]

    if now >= target_expiry_ts:
        try:
            feed = mf.get_candles(symbol=asset, timeframe="15s", limit=30, priority=True)
            if feed.get("is_live") and feed.get("candles"):
                candles = feed["candles"]
                for c in candles:
                    c_start = int(c["time"])
                    c_close = c_start + 15
                    if c_start == target_expiry_ts:
                        return float(c["open"]), target_expiry_ts
                    if c_close == target_expiry_ts:
                        return float(c["close"]), target_expiry_ts
                past = [c for c in candles if int(c["time"]) + 15 <= now]
                if past:
                    nearest = min(past, key=lambda c: abs(int(c["time"]) + 15 - target_expiry_ts))
                    if abs(int(nearest["time"]) + 15 - target_expiry_ts) <= max(tolerance_sec, 30):
                        return float(nearest["close"]), target_expiry_ts
        except Exception:
            pass
    return None


def run_once(service: PaperLabService) -> Dict[str, Any]:
    with service.cycle_lock:
        return _run_cycle(service)


def _run_cycle(service: PaperLabService) -> Dict[str, Any]:
    now = utc_now_ts()
    failed_outage = LIVE_QUOTEX_MANAGER.maintain_connection()
    connected = is_pyquotex_connected()
    if failed_outage and not connected and service.config.telegram_enabled and service.config.telegram_allowed_chat_ids:
        if service.store.get_state("stubay_quotex_alerted") != failed_outage:
            with service.store.transaction():
                service.telegram.enqueue_alert(
                    f"quotex-disconnect:{failed_outage}", "Quotex", "STATUS",
                    "<b>Stubay Ai</b> · Quotex disconnected. Automatic reconnection failed. Open Quotex Login if prompted.",
                    now + 3600, now,
                )
                service.store.set_state("stubay_quotex_alerted", failed_outage)
    settlement_feeds: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def lookup(asset: str, target: int, tolerance: int) -> Optional[Tuple[float, int]]:
        if not connected:
            return None
        return _quote_lookup_from_quotex(asset, target, tolerance, settlement_feeds)

    status = service.stubay.schedule(now, connected, mf.get_candles)
    opened = service.stubay.open_due(now, lookup)
    reconcile = service.reconcile_settlements(lookup, now_ts=utc_now_ts())
    service.stubay.collect_candles(utc_now_ts(), connected, mf.get_candles)
    service.stubay_review.schedule()
    telegram = service.telegram.process_outbox()
    service.schedule_backup()
    return {
        "ok": True,
        "ts": utc_now_ts(),
        "quotex_connected": connected,
        "observations_processed": 0,
        "timeframes": {},
        "trades_opened": opened,
        "settled": reconcile["settled"],
        "unresolved": reconcile["unresolved"],
        "telegram_sent": telegram["sent"],
        "reconcile": reconcile,
        "telegram": telegram,
        "stubay_status": status,
        "message": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SignalDesk Paper-Trading Background Service")
    parser.add_argument("--config", type=str, default=None, help="Path to paper_lab JSON config")
    parser.add_argument("--interval", type=int, default=15, help="Loop interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--backup", action="store_true", help="Create an atomic DB + model backup and exit")
    args = parser.parse_args()

    cfg = load_paper_lab_config(args.config)
    service = PaperLabService(config=cfg)

    if args.backup:
        res = service.store.create_backup(label="cli")
        print(res)
        service.close()
        return 0

    stop = False

    def handle_sig(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    print(
        f"[PaperLab] Started paper-only service | DB={service.store.db_path} | "
        f"Allowlist={cfg.asset_allowlist} | Expiries={cfg.expiry_allowlist_sec}s"
    )
    try:
        while not stop:
            cycle = run_once(service)
            print(
                f"[PaperLab] obs={cycle['observations_processed']} "
                f"settled={cycle['reconcile']['settled']} "
                f"unresolved={cycle['reconcile']['unresolved']} "
                f"tg_sent={cycle['telegram']['sent']}"
            )
            if args.once:
                break
            time.sleep(max(3, int(args.interval)))
    finally:
        service.close()
        print("[PaperLab] Cleanly shut down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
