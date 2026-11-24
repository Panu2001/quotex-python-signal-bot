#!/usr/bin/env python3
"""
Quotex Multi-Strategy Python Signal Bot (Standalone Runner)
Run directly on any machine with Python 3.10+:
    python3 bot.py
"""

from __future__ import annotations
import json
import time
import os
from datetime import datetime
import market_feed as mf
import strategies as strat
import risk_manager as rm
import telegram_notifier as tg
import chart_renderer as cr
from quotex_client import QuotexClient


def load_config(path: str = "config.json") -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def run_bot() -> None:
    cfg = load_config()
    pairs = cfg.get("pairs", ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "BTCUSDT"])
    analyzed_assets = cfg.get("analyzed_assets", {}) or {}
    notify_assets = cfg.get("notify_assets", {}) or {}
    timeframe = cfg.get("timeframe", "1m")
    poll_interval = int(cfg.get("poll_interval_sec", 15))
    strategy_cfg = cfg.get("strategy_config", {})
    tg_min_conf = float(cfg.get("telegram_min_confidence", 70.0))
    lead_minutes = max(0, min(60, int(cfg.get("signal_lead_minutes", 0) or 0)))

    risk_cfg = rm.RiskConfig(
        base_stake=float(cfg.get("base_stake", 10.0)),
        payout_pct=float(cfg.get("default_payout", 85.0)),
        mode=cfg.get("risk_mode", "martingale"),
        martingale_multiplier=float(cfg.get("martingale_multiplier", 2.2)),
        max_martingale_steps=int(cfg.get("max_martingale_steps", 2)),
    )

    tg_token = cfg.get("telegram_bot_token", "")
    tg_chat = cfg.get("telegram_chat_id", "")

    qx = QuotexClient(
        ssid=cfg.get("quotex_ssid", ""),
        account_type=cfg.get("account_type", "DEMO"),
        auto_trade=bool(cfg.get("auto_trade", False)),
    )
    conn_status = qx.connect()

    active_pairs = [sym for sym in pairs if analyzed_assets.get(sym, True) is not False]

    print("=" * 64)
    print("  SIGNAL DESK — QUOTEX LIVE MULTI-STRATEGY ENGINE")
    print(f"  Mode: {conn_status['mode']} | Account: {conn_status['account_type']}")
    print(f"  Analyzing: {len(active_pairs)}/{len(pairs)} pairs | Timeframe: {timeframe}")
    print(f"  Telegram Gate: Qualified Quotex signals only (> {tg_min_conf:.0f}%)")
    print("=" * 64)

    last_alerted: dict[str, int] = {}

    while True:
        try:
            now_ts = datetime.now().strftime("%H:%M:%S")
            for sym in active_pairs:
                feed = mf.get_candles(symbol=sym, timeframe=timeframe, limit=140)
                candles = feed["candles"]
                if not candles or not feed.get("is_live"):
                    continue

                latest_bar_time = candles[-1]["time"]
                eval_cfg = {**strategy_cfg, "telegram_min_confidence": tg_min_conf}
                analysis = strat.evaluate_all_strategies(candles, eval_cfg, timeframe=timeframe)
                sig = analysis["signal"]
                conf = float(analysis["confidence"])
                is_qualified = bool(analysis.get("qualified", False))

                if sig in ("CALL", "PUT") and last_alerted.get(sym) != latest_bar_time:
                    last_alerted[sym] = latest_bar_time
                    stake_info = rm.calculate_next_stake(risk_cfg)
                    stake = stake_info["stake"]
                    expiry = analysis["recommended_expiry"]

                    print(
                        f"[{now_ts}] SIGNAL {sym:<11} | {sig:<4} | "
                        f"Conf: {conf:>5.1f}% | Qualified: {is_qualified} | Exp: {expiry} | "
                        f"Entry: {analysis['price']} | Stake: ${stake:.2f}"
                    )
                    for v in analysis["votes"]:
                        if v["signal"] == sig:
                            print(f"    -> {v['name']}: {v['reason']}")

                    notify_enabled = notify_assets.get(sym, True) is not False
                    if tg_token and tg_chat and notify_enabled and is_qualified:
                        entry_t, expiry_t, _ = tg.compute_entry_and_expiry_times(
                            expiry,
                            timeframe,
                            lead_minutes=lead_minutes,
                        )
                        png_bytes = cr.render_signal_chart_png(
                            candles=candles,
                            overlays=analysis.get("overlays"),
                            symbol_label=feed.get("name", sym),
                            timeframe=timeframe,
                            signal=sig,
                            confidence=conf,
                            price=analysis["price"],
                            decimals=int(feed.get("decimals", 5)),
                            entry_time_str=entry_t,
                            expiry_time_str=expiry_t,
                            expiry_label=expiry,
                            feed_source=str(feed.get("feed_source", "")),
                            payout=float(feed.get("payout", 85.0)),
                        )
                        caption = tg.format_signal_message(
                            symbol=feed.get("name", sym),
                            timeframe=timeframe,
                            signal=sig,
                            confidence=conf,
                            price=analysis["price"],
                            expiry=expiry,
                            stake=stake,
                            payout=feed["payout"],
                            votes=analysis["votes"],
                            entry_time=entry_t,
                            expiry_time=expiry_t,
                            lead_minutes=lead_minutes,
                            candles=candles,
                            bot_title="SIGNAL DESK",
                        )
                        tg.send_telegram_photo(tg_token, tg_chat, png_bytes, caption)
                    elif tg_token and tg_chat:
                        reason_skip = (
                            "muted in notify_assets"
                            if not notify_enabled
                            else str(analysis.get("qualification_reason") or "not qualified")
                        )
                        print(f"    [TELEGRAM AVOIDED] {reason_skip}")

                    if qx.auto_trade:
                        duration = tg.parse_expiry_seconds(expiry, timeframe)
                        order = qx.place_order(sym, sig, stake, duration, analysis["price"])
                        print(f"    [ORDER DISPATCHED] {order['order_id']}")
                else:
                    print(
                        f"[{now_ts}] SCAN   {sym:<11} | {sig:<7} | "
                        f"Conf: {conf:>5.1f}% | Price: {analysis['price']}"
                    )

            time.sleep(poll_interval)
        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            break
        except Exception as err:
            print(f"[ERROR] {err}")
            time.sleep(5)


if __name__ == "__main__":
    run_bot()
