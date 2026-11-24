"""
JSON CLI Bridge for the Quotex Python Signal Engine.
Invoked by the Node/Express API, Desktop App, or directly via CLI to analyze
live markets, scan all pairs, run backtests, manage live pyquotex sessions,
or trigger Telegram alerts.
"""

from __future__ import annotations
import base64
from concurrent.futures import ThreadPoolExecutor
import sys
import json
import os
from pathlib import Path
import time
import strategies as strat
import market_feed as mf
import backtester as bt
import risk_manager as rm
import telegram_notifier as tg
import chart_renderer as cr
import threading
from typing import Any, Dict, Optional
from quotex_client import (
    QuotexClient,
    cancel_pyquotex_login,
    connect_with_pyquotex,
    disconnect_pyquotex,
    get_pyquotex_status,
    is_pyquotex_connected,
    submit_pyquotex_otp,
)

_MARKET_REFRESH_LOCK = threading.Lock()
_MARKET_REFRESH_CURSOR: dict[str, int] = {}


def _refresh_market_batch(symbols: list[str], timeframe: str) -> None:
    if not symbols or not _MARKET_REFRESH_LOCK.acquire(blocking=False):
        return

    def refresh() -> None:
        try:
            start = _MARKET_REFRESH_CURSOR.get(timeframe, 0) % len(symbols)
            count = min(24, len(symbols))
            _MARKET_REFRESH_CURSOR[timeframe] = (start + count) % len(symbols)
            for index in range(count):
                if not is_pyquotex_connected():
                    break
                symbol = symbols[(start + index) % len(symbols)]
                try:
                    quote = mf.get_candles(symbol=symbol, timeframe=timeframe, limit=80, priority=False, background=True)
                    if quote.get("candles") and not mf.qc.get_cached_quotex_candles(symbol, "1h", 27, max_age=1800):
                        mf.get_candles(symbol=symbol, timeframe="1h", limit=27, priority=False, background=True)
                except (ValueError, KeyError, TypeError):
                    continue
        finally:
            _MARKET_REFRESH_LOCK.release()

    threading.Thread(target=refresh, name="QuotexMarketRefresh", daemon=True).start()


def _browser_market_quotes() -> dict:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return {}
    try:
        path = Path(local_app_data) / "SignalDesk Data" / "market-bridge.json"
        snapshot = json.loads(path.read_text(encoding="utf8"))
        if 0 <= time.time() - snapshot["received"] <= 15 and isinstance(snapshot["quotes"], dict):
            return snapshot["quotes"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {}


def _market_day_change(symbol: str, price: float, timestamp: int) -> float | None:
    if not price or not timestamp:
        return None
    hourly = mf.qc.get_cached_quotex_candles(symbol, "1h", 27, max_age=3600) or []
    target = timestamp - 86400
    for bar in reversed(hourly):
        close_time = int(bar["time"]) + 3600
        if target - 3600 < close_time <= target:
            base = float(bar["close"])
            return (price / base - 1) * 100 if base > 0 else None
    return None


DISPLAY_STRATEGY_CONFIG = {"enabled_strategies": {item["id"]: True for item in strat.STRATEGY_CATALOG}}


def _build_strategy_config(config: Optional[Any] = None) -> Dict[str, Any]:
    strat_cfg: Dict[str, Any] = dict(DISPLAY_STRATEGY_CONFIG)
    if isinstance(config, dict):
        strat_cfg.update(config)
        base_enabled = dict(DISPLAY_STRATEGY_CONFIG.get("enabled_strategies", {}))
        user_enabled = config.get("enabled_strategies")
        if isinstance(user_enabled, dict):
            base_enabled.update(user_enabled)
        strat_cfg["enabled_strategies"] = base_enabled
    return strat_cfg

_PAPER_LAB_LOCK = threading.Lock()
_PAPER_LAB_SETTINGS_LOCK = threading.Lock()
_PAPER_LAB_INSTANCE = None
_PAPER_LAB_LOOP_LOCK = threading.Lock()
_PAPER_LAB_LOOP_STATE: Dict[str, Any] = {
    "started": False,
    "running": False,
    "enabled": True,
    "interval_sec": 15,
    "cycle_count": 0,
    "last_cycle_ts": 0,
    "last_cycle_summary": None,
    "last_error": "",
}
_PAPER_LAB_STOP_EVENT = threading.Event()
_PAPER_LAB_THREAD: threading.Thread | None = None


def _get_paper_lab():
    global _PAPER_LAB_INSTANCE
    with _PAPER_LAB_LOCK:
        if _PAPER_LAB_INSTANCE is None:
            from paper_lab import PaperLabService, load_paper_lab_config
            _PAPER_LAB_INSTANCE = PaperLabService(config=load_paper_lab_config())
            _PAPER_LAB_LOOP_STATE["enabled"] = bool(_PAPER_LAB_INSTANCE.config.auto_loop_enabled)
            _PAPER_LAB_LOOP_STATE["interval_sec"] = max(5, int(_PAPER_LAB_INSTANCE.config.auto_loop_interval_sec))
        return _PAPER_LAB_INSTANCE


def get_paper_lab_loop_status() -> dict:
    with _PAPER_LAB_LOOP_LOCK:
        return dict(_PAPER_LAB_LOOP_STATE)


def _paper_lab_worker_main() -> None:
    import paper_lab_runner as runner
    while not _PAPER_LAB_STOP_EVENT.is_set():
        with _PAPER_LAB_LOOP_LOCK:
            enabled = bool(_PAPER_LAB_LOOP_STATE["enabled"])
            interval = max(5, min(300, int(_PAPER_LAB_LOOP_STATE["interval_sec"])))
            _PAPER_LAB_LOOP_STATE["running"] = enabled

        try:
            svc = _get_paper_lab()
            cycle = runner.run_once(svc)
            with _PAPER_LAB_LOOP_LOCK:
                _PAPER_LAB_LOOP_STATE["cycle_count"] = int(_PAPER_LAB_LOOP_STATE["cycle_count"]) + 1
                _PAPER_LAB_LOOP_STATE["last_cycle_ts"] = int(cycle.get("ts", int(time.time())))
                _PAPER_LAB_LOOP_STATE["last_cycle_summary"] = {
                    "quotex_connected": bool(cycle.get("quotex_connected", False)),
                    "observations_processed": int(cycle.get("observations_processed", 0)),
                    "trades_opened": int(cycle.get("trades_opened", 0)),
                    "settled": int(cycle.get("settled", 0)),
                    "unresolved": int(cycle.get("unresolved", 0)),
                    "telegram_sent": int(cycle.get("telegram_sent", 0)),
                    "timeframes": cycle["timeframes"],
                    "message": str(cycle.get("message", "")),
                }
                _PAPER_LAB_LOOP_STATE["last_error"] = ""
        except Exception as exc:
            with _PAPER_LAB_LOOP_LOCK:
                _PAPER_LAB_LOOP_STATE["last_error"] = str(exc)[:240]

        _PAPER_LAB_STOP_EVENT.wait(timeout=min(interval, 5))


def start_paper_lab_background() -> dict:
    """Start the built-in background paper-trading loop once (runs independently of UI login/logout)."""
    global _PAPER_LAB_THREAD
    svc = _get_paper_lab()
    with _PAPER_LAB_LOOP_LOCK:
        if _PAPER_LAB_THREAD is not None and _PAPER_LAB_THREAD.is_alive():
            return dict(_PAPER_LAB_LOOP_STATE)
        _PAPER_LAB_STOP_EVENT.clear()
        _PAPER_LAB_LOOP_STATE["started"] = True
        _PAPER_LAB_LOOP_STATE["enabled"] = bool(svc.config.auto_loop_enabled)
        _PAPER_LAB_LOOP_STATE["running"] = bool(svc.config.auto_loop_enabled)
        _PAPER_LAB_LOOP_STATE["interval_sec"] = max(5, int(svc.config.auto_loop_interval_sec))
        _PAPER_LAB_THREAD = threading.Thread(
            target=_paper_lab_worker_main,
            name="PaperLabBackgroundLoop",
            daemon=True,
        )
        _PAPER_LAB_THREAD.start()
        return dict(_PAPER_LAB_LOOP_STATE)


def dispatch(req: dict) -> dict:
    action = req.get("action", "analyze")

    if action == "catalog":
        return {
            "strategies": strat.STRATEGY_CATALOG,
            "assets": mf.get_all_assets(),
            "quotex_status": get_pyquotex_status(),
        }

    if action == "add_asset":
        return mf.add_custom_asset(
            symbol=str(req.get("symbol", "")),
            name=str(req.get("name", "")),
            category=str(req.get("category", "OTC Forex")),
            payout=int(req.get("payout", 85)),
            base=float(req.get("base", 1.0)),
            decimals=int(req.get("decimals", 5)),
            source=str(req.get("source", "otc")),
            yahoo_ticker=str(req.get("yahoo_ticker", "")),
        )

    if action == "remove_asset":
        return mf.remove_custom_asset(symbol=str(req.get("symbol", "")))

    if action == "analyze":
        symbol = req.get("symbol", "EURUSD")
        timeframe = req.get("timeframe", "1m")
        config = req.get("config", {})
        force_refresh = bool(req.get("force_refresh", False))
        feed = mf.get_candles(
            symbol=symbol,
            timeframe=timeframe,
            limit=160,
            priority=True,
            force_refresh=force_refresh,
        )
        strat_cfg = _build_strategy_config(config)
        analysis = strat.evaluate_all_strategies(feed["candles"], strat_cfg, timeframe=timeframe)
        risk_cfg = rm.RiskConfig(
            base_stake=float(config.get("base_stake", 10.0)),
            payout_pct=float(feed["payout"]),
            mode=str(config.get("risk_mode", "martingale")),
            martingale_multiplier=float(config.get("martingale_multiplier", 2.2)),
            max_martingale_steps=int(config.get("max_martingale_steps", 2)),
        )
        stake_info = rm.calculate_next_stake(risk_cfg)
        return {
            "feed": feed,
            "analysis": analysis,
            "stake_info": stake_info,
            "quotex_status": get_pyquotex_status(),
        }

    if action == "pandas_analyze":
        symbol = req.get("symbol", "EURUSD")
        timeframe = req.get("timeframe", "1m")
        candles = req.get("candles")
        if not candles:
            feed = mf.get_candles(
                symbol=symbol,
                timeframe=timeframe,
                limit=int(req.get("limit", 220)),
                priority=True,
            )
            candles = feed.get("candles", [])
        else:
            feed = {"symbol": symbol, "timeframe": timeframe, "candles": candles}

        from pandas_strategy_engine import StrategyEngine, HAS_PANDAS_TA
        if not HAS_PANDAS_TA:
            return {
                "ok": False,
                "error": "pandas and pandas-ta are required for quantitative engine.",
            }

        engine = StrategyEngine(min_candles=int(req.get("min_candles", 100)))
        analysis = engine.evaluate_candles(candles)
        telegram_preview = engine.format_telegram_alert(
            analysis,
            asset=feed.get("name", symbol),
            timeframe=timeframe,
            expiry=req.get("expiry", "1m - 2m"),
        )
        return {
            "ok": True,
            "symbol": symbol,
            "timeframe": timeframe,
            "analysis": analysis,
            "telegram_preview": telegram_preview,
            "quotex_status": get_pyquotex_status(),
        }

    if action == "scan_all":
        timeframe = req.get("timeframe", "1m")
        config = req.get("config", {})
        active_symbol = str(req.get("active_symbol", "") or "")
        analyzed_map = config.get("analyzed_assets") if isinstance(config, dict) else {}
        if not isinstance(analyzed_map, dict):
            analyzed_map = {}
        all_assets = mf.get_all_assets()
        qx_connected = is_pyquotex_connected()
        strat_cfg = _build_strategy_config(config)

        if qx_connected:
            _refresh_market_batch(
                [sym for sym in all_assets if analyzed_map.get(sym, True) is not False],
                timeframe,
            )

        def eval_one(sym: str) -> dict:
            meta = all_assets.get(sym, {})
            is_analyzed = analyzed_map.get(sym, True) is not False
            if not is_analyzed:
                return {
                    "symbol": sym,
                    "name": meta.get("name", sym),
                    "category": meta.get("category", "OTC Forex"),
                    "payout": int(meta.get("payout", 85)),
                    "decimals": int(meta.get("decimals", 5)),
                    "feed_source": "Analysis Paused",
                    "is_live": False,
                    "live_required": False,
                    "analyzed": False,
                    "custom": bool(meta.get("custom", False)),
                    "price": 0.0,
                    "signal": "NEUTRAL",
                    "confidence": 0.0,
                    "qualified": False,
                    "qualification_reason": "Analysis paused by user",
                    "recommended_expiry": "—",
                    "volatility_regime": "Normal",
                    "call_count": 0,
                    "put_count": 0,
                    "neutral_count": 0,
                    "latest_bar_time": 0,
                    "top_reasons": ["Analysis paused by user"],
                }

            if not qx_connected:
                return {
                    "symbol": sym,
                    "name": meta.get("name", sym),
                    "category": meta.get("category", "OTC Forex"),
                    "payout": int(meta.get("payout", 85)),
                    "decimals": int(meta.get("decimals", 5)),
                    "feed_source": "Quotex Login Required",
                    "is_live": False,
                    "live_required": True,
                    "analyzed": True,
                    "custom": bool(meta.get("custom", False)),
                    "price": 0.0,
                    "signal": "NEUTRAL",
                    "confidence": 0.0,
                    "qualified": False,
                    "qualification_reason": "Connect Quotex account to stream live Quotex trends",
                    "recommended_expiry": "—",
                    "volatility_regime": "Normal",
                    "call_count": 0,
                    "put_count": 0,
                    "neutral_count": 0,
                    "latest_bar_time": 0,
                    "top_reasons": ["Sign in under Quotex Login to stream live Quotex candles"],
                }

            feed = mf.get_candles(
                symbol=sym, timeframe=timeframe, limit=120, priority=False, cache_only=True,
                cache_max_age=900, asset_metadata=meta,
            )
            cand_len = len(feed.get("candles") or [])
            last_ts = int(feed["candles"][-1]["time"]) if cand_len > 0 else 0
            tf_sec = mf.TF_SECONDS.get(timeframe, 60)
            if (cand_len < 26 or (last_ts > 0 and time.time() - last_ts > 3 * tf_sec)) and qx_connected and (sym == active_symbol or sym in mf.PRIORITY_QUOTEX_SCAN_SET):
                feed = mf.get_candles(
                    symbol=sym, timeframe=timeframe, limit=120, priority=True,
                    cache_max_age=60, asset_metadata=meta,
                )
            analysis = strat.evaluate_all_strategies(feed["candles"], strat_cfg, timeframe=timeframe)
            latest_bar_time = int(feed["candles"][-1]["time"]) if feed.get("candles") else 0
            is_qx_live = bool(feed.get("is_live", False)) and feed.get("feed_source") == "Quotex Live (pyquotex)"
            stale = bool(latest_bar_time and time.time() - latest_bar_time > 15 * mf.TF_SECONDS.get(timeframe, 60))
            if stale:
                is_qx_live = False
            is_qualified = bool(is_qx_live and analysis.get("qualified", False))
            return {
                "symbol": sym,
                "name": feed["name"],
                "category": feed["category"],
                "payout": feed["payout"],
                "decimals": feed["decimals"],
                "feed_source": "Last quote" if stale else feed["feed_source"],
                "is_live": is_qx_live,
                "live_required": bool(feed.get("live_required", False)),
                "analyzed": True,
                "custom": bool(feed.get("custom", False)),
                "price": analysis["price"],
                "change_24h": _market_day_change(sym, analysis["price"], latest_bar_time),
                "payout_1m": meta.get("payout_1m"),
                "payout_5m": meta.get("payout_5m"),
                "signal": analysis["signal"] if is_qx_live else "NEUTRAL",
                "confidence": analysis["confidence"] if is_qx_live else 0.0,
                "qualified": is_qualified,
                "qualification_reason": str(analysis.get("qualification_reason", "")),
                "recommended_expiry": analysis["recommended_expiry"],
                "volatility_regime": analysis["volatility_regime"],
                "call_count": analysis["call_count"],
                "put_count": analysis["put_count"],
                "neutral_count": analysis["neutral_count"],
                "latest_bar_time": latest_bar_time,
                "top_reasons": [
                    f"{v['name']}: {v['reason']}"
                    for v in analysis["votes"]
                    if v["signal"] == analysis["signal"]
                ][:4],
            }

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(eval_one, list(all_assets.keys())))

        browser_quotes = _browser_market_quotes()
        for row in results:
            quote = browser_quotes.get(row["symbol"])
            if not isinstance(quote, dict):
                continue
            for key in ("price", "payout_1m", "payout_5m", "change_24h"):
                if quote.get(key) is not None:
                    row[key] = quote[key]
            row["feed_source"] = "Browser bridge"

        results.sort(
            key=lambda x: (
                0 if (x.get("analyzed", True) and x.get("qualified", False))
                else (1 if (x.get("analyzed", True) and x["signal"] != "NEUTRAL")
                else (2 if (x.get("analyzed", True) and x["is_live"])
                else (3 if x.get("analyzed", True) else 4))),
                -x["confidence"],
            )
        )
        return {
            "timeframe": timeframe,
            "pairs": results,
            "quotex_status": get_pyquotex_status(),
        }

    if action == "backtest":
        return bt.run_backtest(
            symbol=req.get("symbol", "EURUSD"),
            timeframe=req.get("timeframe", "1m"),
            bars=int(req.get("bars", 360)),
            expiry_candles=int(req.get("expiry_candles", 2)),
            payout_pct=float(req.get("payout_pct", 85.0)),
            base_stake=float(req.get("base_stake", 10.0)),
            martingale_steps=int(req.get("martingale_steps", 1)),
            martingale_mult=float(req.get("martingale_mult", 2.2)),
            starting_balance=float(req.get("starting_balance", 1000.0)),
            strategy_config=req.get("config", {}),
        )

    if action == "telegram_detect_chat":
        from paper_lab.config import get_telegram_bot_token

        token = str(req.get("bot_token", "") or "").strip() or get_telegram_bot_token()
        return tg.detect_telegram_chat_id(token)

    if action == "telegram_test":
        from paper_lab.config import get_telegram_bot_token

        token = str(req.get("bot_token", "") or "").strip() or get_telegram_bot_token()
        chat_id = req.get("chat_id", "")
        msg = req.get("message") or (
            "🔹 <b>SIGNAL DESK</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Telegram channel connected. Qualified Quotex live signals only (strict multi-strategy confluence + confidence gate).\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        return tg.send_telegram_message(token, chat_id, msg)

    if action == "telegram_signal":
        from paper_lab.config import get_telegram_bot_token
        svc = _get_paper_lab()
        token = str(req.get("bot_token", "") or "").strip() or get_telegram_bot_token()
        default_chat_id = svc.config.telegram_allowed_chat_ids[0] if svc.config.telegram_allowed_chat_ids else ""
        chat_id = str(req.get("chat_id", "") or "").strip() or default_chat_id
        if not token or not chat_id:
            return {"ok": False, "error": "Missing Telegram Bot Token or Chat ID."}

        symbol = str(req.get("symbol", "EURUSD-OTC") or "EURUSD-OTC")
        display_name = str(req.get("name", "") or symbol)
        timeframe = str(req.get("timeframe", "1m") or "1m")
        raw_signal = str(req.get("signal", "NEUTRAL") or "NEUTRAL").upper()
        req_detected = str(req.get("detected_signal", "") or "").upper()
        if req_detected in ("CALL", "PUT"):
            detected_signal = req_detected
            signal = raw_signal if raw_signal in ("CALL", "PUT") else ("CALL" if detected_signal == "PUT" else "PUT")
        else:
            # User requirement: Invert detected signal for Telegram notification and paper trade
            # (if system detects PUT -> CALL, if system detects CALL -> PUT)
            detected_signal = raw_signal
            signal = "CALL" if detected_signal == "PUT" else ("PUT" if detected_signal == "CALL" else "NEUTRAL")

        confidence = float(req.get("confidence", 0.0) or 0.0)
        min_confidence = float(req.get("min_confidence", 70.0) or 70.0)
        tz_offset_min = req.get("tz_offset_min")
        if tz_offset_min is not None:
            try:
                tz_offset_min = int(tz_offset_min)
            except Exception:
                tz_offset_min = None

        auto_triggered = bool(req.get("auto_triggered", False))
        allow_simulated = bool(req.get("allow_simulated") or req.get("simulate"))

        # 1. Connection check: allow signals if Quotex connected, or if simulate/auto_triggered
        if not is_pyquotex_connected() and not allow_simulated and not auto_triggered:
            return {
                "ok": False,
                "skipped": True,
                "error": "Signal avoided: Connect your Quotex account first (only real Quotex live signals are sent to Telegram).",
            }

        # 2. Strict directional & confidence rule: only CALL/PUT strictly above min_confidence
        if detected_signal not in ("CALL", "PUT") and signal not in ("CALL", "PUT"):
            return {
                "ok": False,
                "skipped": True,
                "error": f"Signal avoided: {display_name} is currently {raw_signal} (only qualified CALL/PUT signals are sent).",
            }
        if confidence < min_confidence and not auto_triggered:
            return {
                "ok": False,
                "skipped": True,
                "error": f"Signal avoided: {display_name} confidence {confidence:.1f}% is not above {min_confidence:.0f}%.",
            }

        candles = req.get("candles") if isinstance(req.get("candles"), list) else []
        overlays = req.get("overlays") if isinstance(req.get("overlays"), dict) else None
        votes = req.get("votes") if isinstance(req.get("votes"), list) else []
        reason_lines = req.get("reason_lines") if isinstance(req.get("reason_lines"), list) else []
        price = float(req.get("price", 0.0) or 0.0)
        decimals = int(req.get("decimals", 5) or 5)
        payout = float(req.get("payout", 85.0) or 85.0)
        stake = float(req.get("stake", 10.0) or 10.0)
        expiry = str(req.get("expiry", "") or "")
        feed_source = str(req.get("feed_source", "") or "")
        config = req.get("config") if isinstance(req.get("config"), dict) else {}

        # Capture exact detection timestamp immediately BEFORE any rendering/network I/O
        now_server = int(time.time())
        req_signal_ts = req.get("signal_ts")
        if req_signal_ts is not None:
            try:
                parsed_sig_ts = int(req_signal_ts)
                # Clamp client clock skew to +-120s so stale or bad client clocks never break entry times
                if abs(parsed_sig_ts - now_server) <= 120:
                    signal_ts = parsed_sig_ts
                else:
                    signal_ts = now_server
            except Exception:
                signal_ts = now_server
        else:
            signal_ts = now_server

        # Fetch candles/overlays from WARM IN-MEMORY CACHE first (<1ms!) to avoid 2-5s signal delay
        image_b64 = str(req.get("image_base64", "") or "").strip()
        image_mime = str(req.get("image_mime_type", "image/png") or "image/png")
        img_bytes: bytes = b""

        if image_b64:
            try:
                img_bytes = base64.b64decode(image_b64)
            except Exception:
                img_bytes = b""

        if not img_bytes:
            if not candles:
                live_feed = mf.get_candles(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=120,
                    priority=True,
                    cache_only=True,
                )
                if not live_feed.get("candles"):
                    live_feed = mf.get_candles(symbol=symbol, timeframe=timeframe, limit=120, priority=True)
                if live_feed.get("candles"):
                    candles = live_feed["candles"]
                    display_name = live_feed.get("name", display_name)
                    decimals = int(live_feed.get("decimals", decimals))
                    payout = float(live_feed.get("payout", payout))
                    feed_source = str(live_feed.get("feed_source", feed_source))

            if candles and not overlays:
                strat_cfg = _build_strategy_config(config)
                live_an = strat.evaluate_all_strategies(candles, strat_cfg, timeframe=timeframe)
                overlays = live_an.get("overlays", {})
                if not votes:
                    votes = live_an.get("votes", [])
                if price <= 0:
                    price = float(live_an.get("price", 0.0))
                if not expiry:
                    expiry = str(live_an.get("recommended_expiry", timeframe))

        # 3. Feed Source Verification
        if not allow_simulated and not auto_triggered and feed_source and "Quotex Live" not in feed_source:
            return {
                "ok": False,
                "skipped": True,
                "error": f"Signal avoided: {display_name} feed ({feed_source}) is not a live Quotex stream.",
            }

        # 4. Strict Multi-Strategy Qualification Gate (only qualified consensus signals go to Telegram)
        if votes:
            dom_cnt = sum(1 for v in votes if str(v.get("signal", "")).upper() == detected_signal)
            opp_sig = "PUT" if detected_signal == "CALL" else "CALL"
            opp_cnt = sum(1 for v in votes if str(v.get("signal", "")).upper() == opp_sig)
            if dom_cnt < 2:
                # Also allow if votes were aligned with signal direction
                dom_cnt_sig = sum(1 for v in votes if str(v.get("signal", "")).upper() == signal)
                opp_cnt_sig = sum(1 for v in votes if str(v.get("signal", "")).upper() == ("PUT" if signal == "CALL" else "CALL"))
                if dom_cnt_sig >= 2:
                    dom_cnt = dom_cnt_sig
                    opp_cnt = opp_cnt_sig
                elif not auto_triggered:
                    return {
                        "ok": False,
                        "skipped": True,
                        "error": (
                            f"Signal not qualified for Telegram: {display_name} has {dom_cnt} aligned vs "
                            f"{opp_cnt} opposing votes (requires 2+ aligned strategies)."
                        ),
                    }
        elif req.get("qualified") is False and not auto_triggered:
            return {
                "ok": False,
                "skipped": True,
                "error": str(
                    req.get("qualification_reason")
                    or f"Signal not qualified for Telegram on {display_name}."
                ),
            }

        # Freshness guard for automatic signals: never send stale/closed-market candles with current clock
        if auto_triggered and candles:
            tf_sec = mf.TF_SECONDS.get(timeframe, 60)
            latest_bar_ts = int(candles[-1].get("time", 0) or 0)
            max_age = max(tf_sec * 4, 240)
            if latest_bar_ts > 0 and (now_server - latest_bar_ts) > max_age:
                age_min = (now_server - latest_bar_ts) // 60
                return {
                    "ok": False,
                    "skipped": True,
                    "error": f"Signal avoided: {display_name} feed is {age_min}m behind live clock (market may be closed).",
                }

        if not expiry:
            expiry = timeframe

        lead_minutes_raw = req.get("lead_minutes", config.get("signal_lead_minutes", 0) if isinstance(config, dict) else 0)
        try:
            lead_minutes = max(0, min(60, int(lead_minutes_raw or 0)))
        except Exception:
            lead_minutes = 0

        aligned_entry_ts = tg.compute_aligned_entry_ts(
            now_ts=signal_ts,
            timeframe=timeframe,
            align_to_candle=True,
            lead_minutes=lead_minutes,
        )
        entry_time_str, expiry_time_str, exp_sec = tg.compute_entry_and_expiry_times(
            expiry=expiry,
            timeframe=timeframe,
            entry_ts=aligned_entry_ts,
            tz_offset_min=tz_offset_min,
            signal_ts=signal_ts,
            align_to_candle=True,
            lead_minutes=lead_minutes,
        )
        signal_time_str = tg.format_clock_time(signal_ts, tz_offset_min)

        if not img_bytes:
            if not candles:
                live_feed = mf.get_candles(symbol=symbol, timeframe=timeframe, limit=85, priority=True)
                candles = live_feed.get("candles", [])

            if not candles:
                # Synthesize 60 bars so chart_renderer ALWAYS generates a real screenshot
                import numpy as np
                np.random.seed(int(signal_ts) % 1000)
                n_b = 60
                bar_step = mf.TF_SECONDS.get(timeframe, 60)
                cur_p = price if price > 0 else 1.10000
                synth_candles = []
                for i in range(n_b):
                    b_ts = aligned_entry_ts - (n_b - i) * bar_step
                    drift = 0.00004 if signal == "CALL" else -0.00004
                    o = cur_p - float(np.random.uniform(-0.00003, 0.00003))
                    c = o + drift + float(np.random.normal(0, 0.00007))
                    h = max(o, c) + abs(float(np.random.normal(0.00005, 0.00002)))
                    l = min(o, c) - abs(float(np.random.normal(0.00005, 0.00002)))
                    synth_candles.append({"time": b_ts, "open": o, "high": h, "low": l, "close": c, "volume": 100.0})
                    cur_p = c
                synth_candles[-1]["close"] = price if price > 0 else cur_p
                candles = synth_candles

            img_bytes = cr.render_signal_chart_png(
                candles=candles,
                overlays=overlays,
                symbol_label=display_name,
                timeframe=timeframe,
                signal=signal,
                confidence=confidence,
                price=price if price > 0 else float(candles[-1]["close"]),
                decimals=decimals,
                entry_time_str=entry_time_str,
                expiry_time_str=expiry_time_str,
                expiry_label=expiry,
                feed_source=feed_source or "Quotex Live Feed",
                tz_offset_min=tz_offset_min,
                payout=payout,
            )

        caption = tg.format_signal_message(
            symbol=display_name,
            timeframe=timeframe,
            signal=signal,
            confidence=confidence,
            price=price,
            expiry=expiry,
            stake=stake,
            payout=payout,
            votes=votes if isinstance(votes, list) else [],
            entry_time=entry_time_str,
            expiry_time=expiry_time_str,
            reason_lines=reason_lines,
            tz_offset_min=tz_offset_min,
            signal_time=signal_time_str,
            lead_minutes=lead_minutes,
            candles=candles,
            bot_title="SIGNAL DESK (AI SIMULATION)" if allow_simulated else "SIGNAL DESK",
        )

        telegram_resp: Dict[str, Any] = {"ok": False}
        telegram_err: str = ""
        try:
            if img_bytes:
                ext = "jpg" if "jpeg" in image_mime or "jpg" in image_mime else "png"
                telegram_resp = tg.send_telegram_photo(
                    bot_token=token,
                    chat_id=chat_id,
                    image_bytes=img_bytes,
                    caption_html=caption,
                    filename=f"{symbol.lower()}_{timeframe}_signal.{ext}",
                    content_type=image_mime if image_b64 else "image/png",
                )
                telegram_resp["screenshot_attached"] = True
            else:
                telegram_resp = tg.send_telegram_message(token, chat_id, caption)
                telegram_resp["screenshot_attached"] = False
        except Exception as exc:
            telegram_err = str(exc)
            telegram_resp = {"ok": False, "error": telegram_err}

        # Open Paper Trade in Stubay AI / Paper Lab so accuracy & settlement are automatically calculated
        paper_trade_rec = None
        paper_trade_created = False
        paper_trade_err = ""
        try:
            svc = _get_paper_lab()
            trade_strike = price if price > 0 else (float(candles[-1]["close"]) if candles else 1.0)
            eff_payout = payout / 100.0 if payout > 1 else payout
            f_kind = "platform_settled" if is_pyquotex_connected() else "proxy_simulation"

            versions = svc.get_active_versions()
            active_model_id = (versions.get("active_model") or {}).get("model_version_id") or "stubay_signal_v1"
            active_rule_id = (versions.get("active_rule") or {}).get("rule_version_id") or "stubay_groq_v1"

            # 1. Record market observation (ensuring foreign key requirement is satisfied)
            obs_id, _ = svc.store.record_observation(
                asset=symbol,
                expiry_sec=exp_sec,
                decision_ts=aligned_entry_ts,
                quote_ts=signal_ts,
                price=trade_strike,
                payout=eff_payout,
                fee_rate=svc.config.fee_rate,
                tie_rule=svc.config.tie_rule,
                feed_source=feed_source or "Quotex Live Feed",
                feed_kind=f_kind,
                features={"confidence": float(confidence), "rsi": 50.0},
            )

            # 2. Record signal in signals_and_skips table
            signal_id, _ = svc.store.record_signal_or_skip(
                obs_id=obs_id,
                asset=symbol,
                expiry_sec=exp_sec,
                decision_ts=aligned_entry_ts,
                direction=signal,
                estimated_prob=confidence / 100.0,
                break_even_prob=0.54,
                payout=eff_payout,
                action="TRADE",
                skip_reason="",
                model_version_id=active_model_id,
                rule_version_id=active_rule_id,
                feed_kind=f_kind,
                is_shadow=False,
            )

            # 3. Open Paper Trade
            trade_rec, paper_trade_created = svc.store.open_paper_trade(
                signal_id=signal_id,
                obs_id=obs_id,
                asset=symbol,
                expiry_sec=exp_sec,
                decision_ts=aligned_entry_ts,
                strike=trade_strike,
                direction=signal,
                estimated_prob=confidence / 100.0,
                stake=stake,
                payout=eff_payout,
                fee_rate=svc.config.fee_rate,
                tie_rule=svc.config.tie_rule,
                model_version_id=active_model_id,
                rule_version_id=active_rule_id,
                feed_kind=f_kind,
                is_shadow=False,
            )
            paper_trade_rec = trade_rec
            svc.store.log_event(
                "PaperTrade",
                "INFO",
                f"Paper trade automatically opened for Telegram signal {symbol} {signal} (inverted from detected {detected_signal}) @ {trade_strike:.5f} (Exp: {exp_sec}s).",
                ts=signal_ts,
            )
        except Exception as exc:
            paper_trade_err = str(exc)

        resp = dict(telegram_resp)
        if paper_trade_rec:
            resp["paper_trade"] = paper_trade_rec
            resp["paper_trade_created"] = paper_trade_created
            resp["paper_trade_id"] = paper_trade_rec.get("trade_id")
            # Signal was recorded and paper trade placed successfully
            resp["ok"] = True
        if paper_trade_err:
            resp["paper_trade_error"] = paper_trade_err
        if telegram_err:
            resp["telegram_error"] = telegram_err

        resp["signal"] = signal
        resp["detected_signal"] = detected_signal
        resp["signal_time"] = signal_time_str
        resp["entry_time"] = entry_time_str
        resp["expiry_time"] = expiry_time_str
        resp["entry_ts"] = aligned_entry_ts
        resp["expiry_ts"] = aligned_entry_ts + exp_sec
        resp["lead_minutes"] = lead_minutes
        return resp

    if action == "quotex_test":
        auth_method = str(req.get("auth_method", "ssid")).lower()
        account_type = str(req.get("account_type", "DEMO"))
        if auth_method == "credentials":
            return connect_with_pyquotex(
                email=str(req.get("email", "")),
                password=str(req.get("password", "")),
                account_type=account_type,
                pin=str(req.get("pin", "")),
                force_fresh=bool(req.get("force_fresh", False)),
            )
        ssid = str(req.get("ssid", ""))
        cookie = str(req.get("cookie", ""))
        user_agent = str(req.get("user_agent", ""))
        client = QuotexClient(
            ssid=ssid,
            account_type=account_type,
            auto_trade=False,
            cookie=cookie,
            user_agent=user_agent,
        )
        status = client.connect(timeout_sec=5.0)
        client.close()
        return status

    if action == "quotex_verify_otp":
        return submit_pyquotex_otp(
            login_id=str(req.get("login_id", "")),
            otp=str(req.get("otp", "")),
        )

    if action == "quotex_cancel_otp":
        return cancel_pyquotex_login(login_id=str(req.get("login_id", "")))

    if action == "quotex_status":
        return get_pyquotex_status()

    if action == "quotex_disconnect":
        return disconnect_pyquotex()

    if action == "quotex_sync_assets":
        return mf.sync_assets_from_quotex()

    # ------------------------------------------------------------------
    # Persistent SQLite Paper-Trading Learning Lab Actions (Direct Access)
    # ------------------------------------------------------------------
    if action.startswith("paper_lab_"):
        svc = _get_paper_lab()

        if action == "paper_lab_overview":
            ov = svc.get_dashboard_overview()
            ov["background_loop"] = get_paper_lab_loop_status()
            return {"ok": True, "overview": ov}

        if action == "paper_lab_get_settings":
            from paper_lab.config import get_telegram_bot_token
            cfg = svc.config
            return {
                "ok": True,
                "settings": {
                    "paper_only": True,
                    "auto_loop_enabled": bool(cfg.auto_loop_enabled),
                    "auto_loop_interval_sec": int(cfg.auto_loop_interval_sec),
                    "asset_allowlist": list(cfg.asset_allowlist),
                    "expiry_allowlist_sec": list(cfg.expiry_allowlist_sec),
                    "default_stake": float(cfg.default_stake),
                    "starting_balance": float(cfg.starting_balance),
                    "default_payout": float(cfg.default_payout),
                    "fee_rate": float(cfg.fee_rate),
                    "tie_rule": str(cfg.tie_rule),
                    "telegram_enabled": bool(cfg.telegram_enabled),
                    "telegram_allowed_chat_ids": list(cfg.telegram_allowed_chat_ids),
                    "telegram_min_actionable_sec_left": int(cfg.telegram_min_actionable_sec_left),
                },
                "secrets_status": {
                    "telegram_token_configured": bool(get_telegram_bot_token()),
                },
                "background_loop": get_paper_lab_loop_status(),
            }

        if action == "paper_lab_update_settings":
            if "stubay_minimum_confidence" in req:
                value = req["stubay_minimum_confidence"]
                if type(value) is not int or not 50 <= value <= 100:
                    return {"ok": False, "error": "Choose an integer confidence between 50 and 100."}
                svc.stubay.set_minimum_confidence(value)
                return {"ok": True, "minimum_confidence": value}
            if "review" in req or "ollama_review" in req:
                return {"ok": True, "review": {"status": "Off", "model": "", "latest": None, "latest_trend": None}}
            from paper_lab.config import (
                get_telegram_bot_token,
                save_external_secret,
                save_paper_lab_config,
            )
            with _PAPER_LAB_SETTINGS_LOCK:
                cfg = svc.config
                tracking = req.get("asset_tracking")
                if tracking is not None:
                    if not isinstance(tracking, dict) or not isinstance(tracking.get("enabled"), bool):
                        return {"ok": False, "error": "Invalid asset tracking request."}
                    symbols = tracking.get("symbols", [tracking.get("symbol")])
                    if not isinstance(symbols, list) or not symbols or len(symbols) > 2000 or any(
                        not isinstance(symbol, str) or not 0 < len(symbol.strip()) <= 64 for symbol in symbols
                    ):
                        return {"ok": False, "error": "Invalid asset tracking request."}
                    normalized = [symbol.strip().upper() for symbol in symbols]
                    if tracking["enabled"]:
                        cfg.asset_allowlist = list(dict.fromkeys([*cfg.asset_allowlist, *normalized]))
                    else:
                        disabled = set(normalized)
                        cfg.asset_allowlist = [asset for asset in cfg.asset_allowlist if asset not in disabled]
                elif isinstance(req.get("asset_allowlist"), list):
                    cleaned_assets = [str(a).strip().upper() for a in req["asset_allowlist"] if str(a).strip()]
                    if cleaned_assets:
                        cfg.asset_allowlist = list(dict.fromkeys(cleaned_assets))
                if isinstance(req.get("expiry_allowlist_sec"), list):
                    cleaned_exp = sorted({int(e) for e in req["expiry_allowlist_sec"] if int(e) >= 5})
                    if cleaned_exp:
                        cfg.expiry_allowlist_sec = cleaned_exp
                if req.get("default_stake") is not None:
                    cfg.default_stake = max(1.0, min(10000.0, float(req["default_stake"])))
                if req.get("default_payout") is not None:
                    raw_p = float(req["default_payout"])
                    cfg.default_payout = max(0.10, min(1.50, raw_p / 100.0 if raw_p > 2.0 else raw_p))
                if req.get("fee_rate") is not None:
                    cfg.fee_rate = max(0.0, min(0.20, float(req["fee_rate"])))
                if str(req.get("tie_rule", "")).lower() in ("refund", "loss"):
                    cfg.tie_rule = str(req["tie_rule"]).lower()
                if isinstance(req.get("telegram_allowed_chat_ids"), list):
                    cfg.telegram_allowed_chat_ids = [
                        str(c).strip() for c in req["telegram_allowed_chat_ids"] if str(c).strip()
                    ]
                if req.get("telegram_enabled") is not None:
                    cfg.telegram_enabled = bool(req["telegram_enabled"])
                if req.get("auto_loop_enabled") is not None:
                    cfg.auto_loop_enabled = bool(req["auto_loop_enabled"])
                if req.get("auto_loop_interval_sec") is not None:
                    cfg.auto_loop_interval_sec = max(5, min(300, int(req["auto_loop_interval_sec"])))

                # Store optional credentials STRICTLY in 0600 external secret files (NEVER in SQLite or config JSON)
                tg_tok = str(req.get("telegram_bot_token", "") or "").strip()
                if tg_tok:
                    save_external_secret("telegram_bot_token", tg_tok)
                    if cfg.telegram_allowed_chat_ids and req.get("telegram_enabled") is None:
                        cfg.telegram_enabled = True
                save_paper_lab_config(cfg)
            with _PAPER_LAB_LOOP_LOCK:
                _PAPER_LAB_LOOP_STATE["enabled"] = bool(cfg.auto_loop_enabled)
                _PAPER_LAB_LOOP_STATE["running"] = bool(cfg.auto_loop_enabled)
                _PAPER_LAB_LOOP_STATE["interval_sec"] = int(cfg.auto_loop_interval_sec)
            start_paper_lab_background()
            svc.store.log_event(
                "Config",
                "INFO",
                f"Settings saved via dashboard (assets={len(cfg.asset_allowlist)}, expiries={cfg.expiry_allowlist_sec}, auto_loop={cfg.auto_loop_enabled}/{cfg.auto_loop_interval_sec}s).",
            )
            ov = svc.get_dashboard_overview()
            ov["background_loop"] = get_paper_lab_loop_status()
            return {
                "ok": True,
                "overview": ov,
                "secrets_status": {
                    "telegram_token_configured": bool(get_telegram_bot_token()),
                },
            }

        if action == "paper_lab_toggle_autoloop":
            from paper_lab.config import save_paper_lab_config
            cfg = svc.config
            if req.get("enabled") is not None:
                cfg.auto_loop_enabled = bool(req["enabled"])
            else:
                cfg.auto_loop_enabled = not bool(cfg.auto_loop_enabled)
            if req.get("interval_sec") is not None:
                cfg.auto_loop_interval_sec = max(5, min(300, int(req["interval_sec"])))
            save_paper_lab_config(cfg)
            restart_requested = cfg.auto_loop_enabled and req.get("restart_stubay") is True
            if restart_requested:
                svc.store.set_state("stubay_restart_requested", "1")
                svc.schedule_backup(force=True)
            with _PAPER_LAB_LOOP_LOCK:
                _PAPER_LAB_LOOP_STATE["enabled"] = bool(cfg.auto_loop_enabled)
                _PAPER_LAB_LOOP_STATE["running"] = bool(cfg.auto_loop_enabled)
                _PAPER_LAB_LOOP_STATE["interval_sec"] = int(cfg.auto_loop_interval_sec)
            start_paper_lab_background()
            ov = svc.get_dashboard_overview()
            ov["background_loop"] = get_paper_lab_loop_status()
            return {"ok": True, "background_loop": get_paper_lab_loop_status(), "overview": ov,
                    "restart_requested": restart_requested}

        if action == "paper_lab_history":
            table = str(req.get("table", "paper_trades") or "paper_trades")
            from_ts = req.get("from_ts")
            to_ts = req.get("to_ts")
            rows = svc.store.query_history(
                table=table,
                from_ts=int(from_ts) if from_ts not in (None, "") else None,
                to_ts=int(to_ts) if to_ts not in (None, "") else None,
                asset=str(req.get("asset", "") or "") or None,
                strategy_version=str(req.get("strategy_version", "") or "") or None,
                feed_kind=str(req.get("feed_kind", "") or "") or None,
                limit=int(req.get("limit", 250) or 250),
            )
            return {"ok": True, "table": table, "count": len(rows), "rows": rows}

        if action == "paper_lab_export":
            table = str(req.get("table", "paper_trades") or "paper_trades")
            fmt = str(req.get("format", "json") or "json").lower()
            from_ts = req.get("from_ts")
            to_ts = req.get("to_ts")
            rows = svc.store.query_history(
                table=table,
                from_ts=int(from_ts) if from_ts not in (None, "") else None,
                to_ts=int(to_ts) if to_ts not in (None, "") else None,
                asset=str(req.get("asset", "") or "") or None,
                strategy_version=str(req.get("strategy_version", "") or "") or None,
                feed_kind=str(req.get("feed_kind", "") or "") or None,
                limit=5000,
            )
            if fmt == "csv":
                return {"ok": True, "format": "csv", "table": table, "content": svc.store.export_history_csv(rows)}
            return {"ok": True, "format": "json", "table": table, "content": json.dumps(rows, indent=2)}

        if action == "paper_lab_run_cycle":
            import paper_lab_runner as runner
            cycle = runner.run_once(svc)
            ov = svc.get_dashboard_overview()
            ov["background_loop"] = get_paper_lab_loop_status()
            return {"ok": True, "cycle": cycle, "overview": ov}

        if action == "paper_lab_retrain":
            feed_kind = str(req.get("feed_kind", "platform_settled") or "platform_settled")
            res = svc.maybe_retrain_model(feed_kind=feed_kind, force=True)
            ov = svc.get_dashboard_overview()
            ov["background_loop"] = get_paper_lab_loop_status()
            return {"ok": True, "result": res, "overview": ov}

        if action == "paper_lab_backup":
            res = svc.store.create_backup(label=str(req.get("label", "dashboard") or "dashboard"))
            return {"ok": True, "backup": res}

        if action == "paper_lab_rollback":
            reason = str(req.get("reason", "manual_operator_rollback") or "manual_operator_rollback")
            res = svc.rollback_or_pause(reason=reason)
            ov = svc.get_dashboard_overview()
            ov["background_loop"] = get_paper_lab_loop_status()
            return {"ok": True, "result": res, "overview": ov}

        if action == "paper_lab_resume":
            res = svc.resume_strategy()
            ov = svc.get_dashboard_overview()
            ov["background_loop"] = get_paper_lab_loop_status()
            return {"ok": True, "result": res, "overview": ov}

        if action == "paper_lab_run_verification":
            import verify_paper_lab as vpl
            return {"ok": True, "report": vpl.run_all_verifications()}

        return {"ok": False, "error": f"Unknown paper_lab action: {action}"}

    return {"error": f"Unknown action: {action}"}


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        raw_input = sys.argv[1].strip()
    elif not sys.stdin.isatty():
        raw_input = sys.stdin.read().strip()
    else:
        raw_input = ""
    req = json.loads(raw_input) if raw_input else {"action": "scan_all"}
    print(json.dumps(dispatch(req)))


if __name__ == "__main__":
    main()
