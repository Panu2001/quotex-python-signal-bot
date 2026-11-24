"""Local, paper-only signal planning and durable outcome memory."""

from __future__ import annotations

from datetime import datetime

import json
import math
import threading
from html import escape
from typing import Any, Callable

from chart_renderer import render_signal_chart_png

from xgb_engine import BAR_SECONDS, FEATURES, WINDOW, Candle, features, validate_candle

from .config import STUBAY_NOTICE_SECONDS, compute_break_even_probability
from .modeling import ProbabilityModel
from .storage import utc_now_ts
from .stubay_policy import PROBABILITY_MARGIN, assess_setup, paper_risk, trend_policy_improves

try:
    from pyquotex.utils.strategy import TripleConfirmationStrategy
    from pyquotex.expiration import get_expiration_time_quotex
    HAS_PYQUOTEX_UTILS = True
except ImportError:
    HAS_PYQUOTEX_UTILS = False
    get_expiration_time_quotex = None
    TripleConfirmationStrategy = None


def pyquotex_expiry_timestamp(entry_ts: int, duration_sec: int) -> int:
    """Calculate expiration timestamp for scheduled trades."""
    return entry_ts + duration_sec

SCAN_SECONDS = 60
CANDLE_REFRESH_SECONDS = 5
MODEL = "stubay-local-logistic-v1"
# Stable ledger recipe identifier includes records from both implementations.
RECIPE = "stubay_groq_v1"
DURATION_SECONDS = 120
FEED_KIND = "proxy_simulation"
MIN_CONFIDENCE_KEY = "stubay_min_signal_confidence"
DEFAULT_MIN_CONFIDENCE = 50


def closed_candles(feed: dict, now: int) -> list[dict]:
    if not feed.get("is_live"):
        return []
    raw = {int(candle["time"]): candle for candle in feed.get("candles", [])}
    candles = []
    for start, candle in sorted(raw.items()):
        end = start + BAR_SECONDS
        # Quotex timestamps the start of the bar. Its end, not the arrival
        # of a subsequent bar, determines whether the bar has closed.
        if end > now:
            continue
        parsed = Candle(end, *(float(candle[key]) for key in ("open", "high", "low", "close")))
        validate_candle(parsed)
        candles.append({"time": start, "open": parsed.open, "high": parsed.high,
                        "low": parsed.low, "close": parsed.close})
    return candles


REVIEW_RULES = {"require_stronger_call": "CALL", "require_stronger_put": "PUT", "require_stronger_signal": "BOTH"}
REVIEW_MARGIN = 0.03


def validated_review(snapshot, model, validation, threshold):
    """Use a bounded confidence filter only on evidence newer than the review."""
    for review in snapshot.get("review_rules", []):
        direction = REVIEW_RULES.get(review.get("rule"))
        if not direction:
            continue
        rows = [row for row in validation if row["decision_ts"] > review["created_ts"] and row.get("regime") != "volatile"]
        if len(rows) < 24:
            continue
        baseline = candidate = 0.0
        accepted = removed = 0
        for row in rows:
            up = model.predict_up_prob(row["features"])
            confidence = max(up, 1 - up)
            if confidence <= threshold:
                continue
            call = up > 0.5
            result = snapshot["payout"] / 100 if call == (row["outcome"] == "UP") else -1.0
            result -= snapshot.get("fee_rate", 0.0)
            baseline += result
            if direction in ("BOTH", "CALL" if call else "PUT") and confidence <= threshold + REVIEW_MARGIN:
                removed += 1
            else:
                accepted += 1
                candidate += result
        if accepted >= 8 and removed >= 4 and (candidate - baseline) / len(rows) >= 0.02:
            return direction
        return None
    return None


PATTERN_FEATURES = ("trend_continuation", "pullback_continuation", "range_breakout", "wick_reversal", "pyquotex_triple_confirmation")


def chart_patterns(window: list[Candle]) -> dict[str, float]:
    """Describe observable setups using only already closed candles."""
    older, middle, recent = window[-12:-8], window[-8:-4], window[-4:]
    highs = [max(bar.high for bar in group) for group in (older, middle, recent)]
    lows = [min(bar.low for bar in group) for group in (older, middle, recent)]
    trend = 0
    if highs[0] < highs[1] < highs[2] and lows[0] < lows[1] < lows[2]:
        trend = 1
    elif highs[0] > highs[1] > highs[2] and lows[0] > lows[1] > lows[2]:
        trend = -1

    prior, pullback, last = window[-12:-4], window[-4:-1], window[-1]
    direction = int(prior[-1].close > prior[0].close) - int(prior[-1].close < prior[0].close)
    retrace = (
        direction == 1 and pullback[-1].close < prior[-1].close
        and min(bar.low for bar in pullback) > min(bar.low for bar in prior)
        and last.close > max(bar.high for bar in pullback)
    ) or (
        direction == -1 and pullback[-1].close > prior[-1].close
        and max(bar.high for bar in pullback) < max(bar.high for bar in prior)
        and last.close < min(bar.low for bar in pullback)
    )
    recent_range = window[-9:-1]
    breakout = int(last.close > max(bar.high for bar in recent_range)) - int(last.close < min(bar.low for bar in recent_range))
    span = last.high - last.low
    reversal = 0
    if span > 0:
        if direction == 1 and last.close < last.open and last.high > max(bar.high for bar in recent_range) and last.high - last.open >= span * 0.45:
            reversal = -1
        elif direction == -1 and last.close > last.open and last.low < min(bar.low for bar in recent_range) and last.open - last.low >= span * 0.45:
            reversal = 1

    pq_vote = 0.0
    if TripleConfirmationStrategy is not None and len(window) >= 20:
        try:
            strat = TripleConfirmationStrategy(None)
            strat.ema_slow = min(50, max(12, len(window) - 2))
            strat.ema_fast = min(20, max(5, strat.ema_slow // 2))
            strat._min_candles = strat.ema_slow + 2
            cdicts = [{"open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in window]
            res = strat.analyze(cdicts)
            if res == "call":
                pq_vote = 1.0
            elif res == "put":
                pq_vote = -1.0
        except Exception:
            pass

    return dict(zip(PATTERN_FEATURES, (float(trend), float(direction if retrace else 0), float(breakout), float(reversal), float(pq_vote))))


def _validation_performance(model, rows, threshold, payout, fee):
    brier = paper_return = 0.0
    trades = 0
    for row in rows:
        probability = model.predict_up_prob(row["features"])
        up = row["outcome"] == "UP"
        brier += (probability - up) ** 2
        if row.get("regime") != "volatile" and max(probability, 1 - probability) > threshold:
            paper_return += (payout if (probability > 0.5) == up else -1) - fee
            trades += 1
    return brier / len(rows), paper_return / len(rows), trades


def local_plan(snapshot: dict, history: list[dict], minimum_probability: float) -> dict:
    series = [
        Candle(int(c["time"]) + BAR_SECONDS, *(float(c[k]) for k in ("open", "high", "low", "close")))
        for c in history
    ]
    latest = series[-1].close_ts
    entry = snapshot["allowed_entries"][0]
    entry_steps = (entry - latest) // BAR_SECONDS
    horizon_steps = DURATION_SECONDS // BAR_SECONDS
    total_steps = entry_steps + horizon_steps

    def vector(end_index: int) -> dict | None:
        window = series[end_index - WINDOW + 1:end_index + 1]
        result = features(window)
        if result is None:
            return None
        return {**dict(zip(FEATURES, result)), **chart_patterns(window)}

    setup = assess_setup(series)

    def wait(reason: str, confidence: float = 50.0) -> dict:
        return {**setup, "signal": "WAIT", "future_trend": "Unconfirmed", "reason": reason, "confidence": confidence}

    if setup["regime"] == "volatile":
        return wait("Volatile or choppy candles. Paper signal blocked.")
    current = vector(len(series) - 1)
    if current is None:
        return wait("Waiting for contiguous closed candles.")
    rows = []
    for index in range(WINDOW - 1, len(series) - total_steps):
        end = series[index + total_steps]
        start = series[index + entry_steps]
        timestamp = series[index].close_ts
        if end.close_ts >= latest or end.close_ts - timestamp != total_steps * BAR_SECONDS:
            continue
        feature_row = vector(index)
        if feature_row is None or start.close == end.close:
            continue
        rows.append({
            "features": feature_row, "decision_ts": timestamp, "outcome_known_ts": end.close_ts,
            "history_end": index + 1,
            "outcome": "UP" if end.close > start.close else "DOWN",
        })
    if len(rows) < 50:
        return wait(f"Collecting local training data ({len(rows)}/50 settled examples).")

    validation_start = rows[int(len(rows) * 0.75)]["decision_ts"]
    training = [row for row in rows if row["outcome_known_ts"] < validation_start]
    validation = [row for row in rows if row["decision_ts"] >= validation_start]
    if len(training) < 30 or len(validation) < 10 or {r["outcome"] for r in validation} != {"UP", "DOWN"}:
        return wait("Waiting for enough recent outcomes in both directions.")
    if {row["outcome"] for row in training} != {"UP", "DOWN"}:
        return wait("Training history needs outcomes in both directions.")
    try:
        model = ProbabilityModel.fit_logistic_regression(training, epochs=70, feature_names=list(FEATURES))
    except ValueError as exc:
        return wait(str(exc))
    for row in validation:
        history_end = row["history_end"]
        row.update(assess_setup(series[max(0, history_end - 1000):history_end]))

    threshold = max(minimum_probability, snapshot.get("minimum_confidence", 50) / 100)
    payout = snapshot.get("payout", 85) / 100
    fee = snapshot.get("fee_rate", 0.0)
    brier, baseline_return, _ = _validation_performance(model, validation, threshold, payout, fee)
    model_label = "Local model"
    if len(validation) >= 40:
        try:
            candidate = ProbabilityModel.fit_logistic_regression(
                training, epochs=70, feature_names=[*FEATURES, *PATTERN_FEATURES],
            )
            candidate_brier, candidate_return, candidate_trades = _validation_performance(
                candidate, validation, threshold, payout, fee,
            )
            if (candidate_trades >= 10 and candidate_return > max(0, baseline_return) + 0.02
                    and candidate_brier < min(0.25, brier) - 0.005):
                model, brier = candidate, candidate_brier
                model_label = "Chart-pattern model"
        except ValueError:
            pass
    scored_rows = []
    if len(validation) >= 40 and len(series) >= 300:
        scored_rows = [(row, model.predict_up_prob(row["features"])) for row in validation]
    trend_filter = trend_policy_improves(scored_rows, threshold, payout, fee)
    setup["trend_filter_validated"] = trend_filter
    probability_up = model.predict_up_prob(current)
    confidence = max(probability_up, 1.0 - probability_up)
    if brier >= 0.28 and confidence < 0.70:
        return wait("Recent validation did not beat an even-odds forecast.", confidence * 100)
    if confidence <= minimum_probability:
        return wait("Estimated probability did not clear the payout threshold.", confidence * 100)

    direction = "CALL" if probability_up > 0.5 else "PUT"
    if trend_filter and setup["setup_direction"] != direction:
        return wait("Validated trend setup did not agree with the forecast.", confidence * 100)
    review_direction = validated_review(snapshot, model, validation, minimum_probability)
    if review_direction in ("BOTH", direction) and confidence <= minimum_probability + REVIEW_MARGIN:
        return wait("A validated review calls for stronger confidence in this direction.", confidence * 100)

    # PyQuotex Triple Confirmation Strategy integration
    pq_signal = None
    if TripleConfirmationStrategy is not None and len(series) >= 20:
        try:
            pq_strat = TripleConfirmationStrategy(None)
            pq_strat.ema_slow = min(50, max(12, len(series) - 2))
            pq_strat.ema_fast = min(20, max(5, pq_strat.ema_slow // 2))
            pq_strat._min_candles = pq_strat.ema_slow + 2
            cdicts = [{"open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in series[-70:]]
            res = pq_strat.analyze(cdicts)
            if res in ("call", "put"):
                pq_signal = res.upper()
        except Exception:
            pass

    if pq_signal and pq_signal != direction:
        return wait(f"PyQuotex Triple Confirmation disagrees ({pq_signal} vs {direction}).", confidence * 100)

    pq_reason = ""
    if pq_signal and pq_signal == direction:
        confidence = min(0.99, confidence + 0.05)
        pq_reason = " [PyQuotex Triple Confirmation Verified]"
        agreeing = setup.setdefault("signals_agreeing", [])
        if "PyQuotex Triple Confirmation" not in agreeing:
            agreeing.append("PyQuotex Triple Confirmation")

    expiry = pyquotex_expiry_timestamp(entry, DURATION_SECONDS)
    return {
        **setup, "signal": direction, "future_trend": "Up" if direction == "CALL" else "Down",
        "reason": f"{model_label}: {confidence:.0%} for {direction} (validation Brier {brier:.3f}){pq_reason}.",
        "confidence": confidence * 100, "entry_ts": entry, "expiry_ts": expiry,
        "pyquotex_strategy": "Triple Confirmation (EMA/RSI/Stoch)" if pq_signal else "Confluence",
    }


def validate_plan(raw: dict, snapshot: dict, now: int, minimum_lead: int) -> dict:
    direction = raw.get("signal")
    reason = str(raw.get("reason") or "").strip()[:240]
    trend = str(raw.get("future_trend") or "").strip()[:180]
    if not reason or not trend or direction not in ("CALL", "PUT", "WAIT"):
        raise ValueError("Invalid verdict")
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 100:
        raise ValueError("Invalid confidence")
    plan = {key: raw[key] for key in ("regime", "setup_score", "signals_agreeing",
                                      "signals_conflicting", "trend_filter_validated") if key in raw}
    plan.update(signal=direction, reason=reason, future_trend=trend, confidence=confidence)
    if direction == "WAIT":
        return plan
    entry, expiry = raw.get("entry_ts"), raw.get("expiry_ts")
    if (
        type(entry) is not int or type(expiry) is not int
        or entry % 60 != 0 or expiry % 60 != 0
        or entry not in snapshot["allowed_entries"]
        or expiry - entry not in snapshot["allowed_expiries"]
        or entry - now < minimum_lead
    ):
        raise ValueError("Expired or invalid entry and expiry")
    return {**plan, "entry_ts": entry, "expiry_ts": expiry}


class StubayWorkflow:
    def __init__(self, service: Any):
        self.service = service
        self.store = service.store
        self.thread: threading.Thread | None = None
        self._candle_refresh_thread: threading.Thread | None = None
        self._candle_refresh_after = 0
        self._candle_refresh_cursor = 0

    def minimum_confidence(self) -> int:
        return int(self.store.get_state(MIN_CONFIDENCE_KEY, str(DEFAULT_MIN_CONFIDENCE)))

    def set_minimum_confidence(self, value: int) -> None:
        self.store.set_state(MIN_CONFIDENCE_KEY, str(value))

    def _set_status(self, text: str) -> None:
        if self.store.get_state("stubay_status") != text:
            self.store.set_state("stubay_status", text)

    def collect_candles(self, now: int, connected: bool, get_feed: Callable) -> None:
        config = self.service.config
        assets = tuple(config.asset_allowlist)
        if not connected or not config.auto_loop_enabled or not assets:
            return
        if now < self._candle_refresh_after or (self.thread and self.thread.is_alive()):
            return
        if self._candle_refresh_thread and self._candle_refresh_thread.is_alive():
            return
        index = self._candle_refresh_cursor % len(assets)
        asset = assets[index]
        self._candle_refresh_cursor = index + 1
        self._candle_refresh_after = now + CANDLE_REFRESH_SECONDS

        def refresh():
            try:
                get_feed(symbol=asset, timeframe="15s", limit=240, priority=False, background=True)
            except Exception:
                # The scheduled scan has its own priority fetch and retry.
                return

        self._candle_refresh_thread = threading.Thread(
            target=refresh, name="StubayCandleRefresh", daemon=True,
        )
        self._candle_refresh_thread.start()

    def schedule(self, now: int, connected: bool, feed: Callable) -> str:
        if not self.service.config.auto_loop_enabled:
            status = "New scans are paused. Scheduled paper trades still process."
            self._set_status(status)
            return status
        if self.thread and self.thread.is_alive():
            return "Analyzing markets locally."
        if not connected:
            # Session state is local to this worker. A disconnected preview
            # must not overwrite a result saved by the connected desktop.
            return "Waiting for Quotex login."
        bucket = now - now % SCAN_SECONDS
        with self.store.transaction():
            restart = self.store.get_state("stubay_restart_requested") == "1"
            local_started = self.store.get_state("stubay_runtime_model") == MODEL
            if local_started and not restart and int(self.store.get_state("stubay_scan_bucket", "-1") or "-1") >= bucket:
                return self.store.get_state("stubay_status") or "Continuous scanning active · Watching for qualified signals."
            if restart:
                self.store.set_state("stubay_restart_requested", "0")
            self.store.set_state("stubay_runtime_model", MODEL)
            self.store.set_state("stubay_scan_bucket", str(bucket))
            self._set_status("Analyzing markets locally.")
        self.thread = threading.Thread(target=self.scan, args=(bucket, feed), daemon=True)
        self.thread.start()
        return "Analyzing markets locally."

    def scan(self, bucket: int, get_feed: Callable) -> None:
        processed = analyzed = scheduled = 0
        config = self.service.config
        try:
            for asset in tuple(config.asset_allowlist):
                if not config.auto_loop_enabled:
                    self._set_status("New scans are paused.")
                    return
                with self.store._lock:
                    existing = self.store._conn.execute(
                        "SELECT status FROM stubay_memory WHERE bucket = ? AND asset = ?", (bucket, asset),
                    ).fetchone()
                if existing:
                    continue
                processed += 1
                # A scheduled scan needs the same history and timeout as Live Chart.
                # Low-priority market refreshes may return stale cache under chart load.
                feed = get_feed(symbol=asset, timeframe="15s", limit=240, priority=True, background=True)
                now = utc_now_ts()
                candles = closed_candles(feed, now)
                if not candles or now - (candles[-1]["time"] + BAR_SECONDS) > config.max_quote_age_sec:
                    feed = get_feed(
                        symbol=asset, timeframe="15s", limit=240,
                        priority=True, background=True, force_refresh=True,
                    )
                    now = utc_now_ts()
                    candles = closed_candles(feed, now)
                if not candles or now - (candles[-1]["time"] + BAR_SECONDS) > config.max_quote_age_sec:
                    self._record(bucket, asset, "SKIPPED", {}, b"", {"reason": "Closed Quotex candles are unavailable or stale."})
                    continue
                payout = float(feed["payout"])
                if not math.isfinite(payout) or not 0 < payout <= 200:
                    self._record(bucket, asset, "SKIPPED", {}, b"", {"reason": "Invalid payout."})
                    continue
                min_payout_floor = getattr(config, "min_payout_floor_pct", 80.0)
                if payout < min_payout_floor:
                    self._record(bucket, asset, "SKIPPED", {}, b"", {"reason": f"Payout ({payout:.0f}%) is below the {min_payout_floor:.0f}% profitability floor."})
                    continue
                last_close = candles[-1]["time"] + BAR_SECONDS
                notice = max(STUBAY_NOTICE_SECONDS, config.telegram_min_actionable_sec_left)
                earliest = now + notice + BAR_SECONDS
                entry = ((earliest + 59) // 60) * 60
                if entry - now < 90:
                    entry += 60
                expiry = pyquotex_expiry_timestamp(entry, DURATION_SECONDS)
                snapshot = {
                    "asset": asset, "timeframe": "15s", "as_of": last_close, "candles": candles, "payout": payout,
                    "allowed_entries": [entry],
                    "allowed_expiries": [DURATION_SECONDS],
                    "model": MODEL,
                }
                from .stubay_review import candidate_rules

                snapshot["fee_rate"] = config.fee_rate
                snapshot["minimum_confidence"] = self.minimum_confidence()
                snapshot["review_rules"] = candidate_rules(self.store, asset)
                image = self._chart(snapshot, "WAIT", 0)
                threshold = compute_break_even_probability(payout / 100, config.fee_rate, config.tie_rule) + PROBABILITY_MARGIN
                try:
                    self._set_status("Learning from closed Quotex candles.")
                    history = self._history(asset, candles, last_close)
                    self._settle_hypotheticals(asset, history, last_close)
                    raw = local_plan(snapshot, history, threshold)
                    plan = validate_plan(raw, snapshot, utc_now_ts(), notice)
                except (ValueError, KeyError, TypeError):
                    self._record(bucket, asset, "SKIPPED", snapshot, image, {"reason": "The local plan was unavailable or too late."})
                    continue
                analyzed += 1
                if not config.auto_loop_enabled:
                    self._record(bucket, asset, "PAUSED", snapshot, image, plan)
                    return
                with self.store.transaction() as conn:
                    minimum = self.minimum_confidence()
                    if plan["signal"] != "WAIT" and plan["confidence"] < minimum:
                        plan.update(signal="WAIT", reason=f"Signal confidence is below your {minimum}% minimum.")
                    if plan["signal"] != "WAIT":
                        if getattr(config, "invert_signals", False):
                            raw_sig = plan["signal"]
                            inv_sig = "CALL" if raw_sig == "PUT" else "PUT"
                            plan["detected_signal"] = raw_sig
                            plan["signal"] = inv_sig
                            plan["future_trend"] = "Up" if inv_sig == "CALL" else "Down"
                            plan["reason"] = f"[Inverted] {plan.get('reason', '')}"
                        stake, blocked = paper_risk(conn, RECIPE, utc_now_ts(), config.starting_balance, config.default_stake, config.fee_rate)
                        if blocked:
                            plan.update(signal="WAIT", reason=blocked)
                        else:
                            plan["paper_stake"] = stake
                    if plan["signal"] == "WAIT":
                        plan.pop("entry_ts", None)
                        plan.pop("expiry_ts", None)
                        plan["hypothetical_entry_ts"] = entry
                        plan["hypothetical_expiry_ts"] = expiry
                    status = "WAIT" if plan["signal"] == "WAIT" else "SCHEDULED"
                    if self._record(bucket, asset, status, snapshot, image, plan) and status == "SCHEDULED":
                        self._queue_signal(bucket, asset, snapshot, plan)
                        scheduled += 1
            if not processed:
                with self.store._lock:
                    completed = self.store._conn.execute(
                        "SELECT COUNT(*), COUNT(CASE WHEN status IN ('WAIT','SCHEDULED','OPENED',"
                        "'MISSED_ENTRY','RISK_BLOCKED') THEN 1 END) "
                        "FROM stubay_memory WHERE bucket = ?", (bucket,),
                    ).fetchone()
                if completed[0]:
                    self._set_status(f"Current scan already recorded {completed[1]} local decisions across {completed[0]} assets.")
                else:
                    self._set_status("No assets enabled for Stubay scanning.")
                return
            summary = f"Last scan: {analyzed} local decisions, {processed - analyzed} skipped, {scheduled} paper trades scheduled."
            self._set_status(summary)
            self.store.log_event("Stubay Ai", "INFO", summary)
        except Exception as exc:
            self._set_status(f"Scan failed ({type(exc).__name__}).")
            self.store.log_event("Stubay Ai", "ERROR", f"Scan failed ({type(exc).__name__}).")

    def _settle_hypotheticals(self, asset, candles, as_of):
        closes = {int(bar["time"]) + BAR_SECONDS: float(bar["close"]) for bar in candles}
        with self.store.transaction() as conn:
            rows = conn.execute(
                "SELECT bucket, plan_json FROM stubay_memory WHERE asset = ? AND status = 'WAIT' "
                "ORDER BY bucket DESC LIMIT 64", (asset,),
            ).fetchall()
            for row in rows:
                plan = json.loads(row["plan_json"])
                entry = plan.get("hypothetical_entry_ts")
                expiry = plan.get("hypothetical_expiry_ts")
                if "hypothetical_outcome" in plan or not entry or not expiry or expiry > as_of:
                    continue
                if entry not in closes or expiry not in closes:
                    continue
                strike, settlement = closes[entry], closes[expiry]
                plan["hypothetical_outcome"] = {
                    "entry_price": strike, "expiry_price": settlement,
                    "direction": "UP" if settlement > strike else ("DOWN" if settlement < strike else "TIE"),
                }
                conn.execute(
                    "UPDATE stubay_memory SET plan_json = ? WHERE bucket = ? AND asset = ?",
                    (json.dumps(plan), row["bucket"], asset),
                )

    def _record(self, bucket: int, asset: str, status: str, snapshot: dict, image: bytes, plan: dict) -> bool:
        with self.store.transaction() as conn:
            result = conn.execute(
                "INSERT OR IGNORE INTO stubay_memory(bucket, asset, status, snapshot_json, screenshot, plan_json, entry_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (bucket, asset, status, json.dumps(snapshot), image, json.dumps(plan), plan.get("entry_ts")),
            )
            return result.rowcount == 1

    def _history(self, asset: str, candles: list[dict], as_of: int) -> list[dict]:
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT snapshot_json FROM stubay_memory WHERE asset = ? ORDER BY bucket DESC LIMIT 16",
                (asset,),
            ).fetchall()
        merged = {}
        for row in reversed(rows):
            snapshot = json.loads(row["snapshot_json"])
            timeframe = snapshot.get("timeframe")
            historical = {int(candle["time"]): candle for candle in snapshot.get("candles", [])}
            if timeframe == "5s":
                # Reuse earlier saved market snapshots where all three bars exist.
                for start in historical:
                    if start % BAR_SECONDS or not all(start + offset in historical for offset in (5, 10)):
                        continue
                    bars = [historical[start + offset] for offset in (0, 5, 10)]
                    if start + BAR_SECONDS <= min(as_of, int(snapshot.get("as_of", 0))):
                        merged[start] = {"time": start, "open": bars[0]["open"], "close": bars[-1]["close"],
                                         "high": max(b["high"] for b in bars), "low": min(b["low"] for b in bars)}
            elif timeframe == "15s":
                merged.update({start: c for start, c in historical.items() if start + BAR_SECONDS <= as_of})
        merged.update({int(candle["time"]): candle for candle in candles})
        return [merged[key] for key in sorted(merged)[-1000:]]

    def _chart(self, snapshot: dict, signal: str, confidence: float, entry: str = "", expiry: str = "", tz_offset_min: int = 0) -> bytes:
        candles = snapshot["candles"][-20:]
        return render_signal_chart_png(
            candles, None, snapshot["asset"], snapshot["timeframe"], signal, confidence, float(candles[-1]["close"]),
            entry_time_str=entry, expiry_time_str=expiry, tz_offset_min=tz_offset_min, payout=snapshot["payout"],
            watermark="Stubay Ai",
        )

    def _queue_signal(self, bucket: int, asset: str, snapshot: dict, plan: dict) -> None:
        config = self.service.config
        if not config.telegram_enabled:
            return
        entry, expiry = plan["entry_ts"], plan["expiry_ts"]
        entry_local = datetime.fromtimestamp(entry).astimezone()
        expiry_local = datetime.fromtimestamp(expiry).astimezone()
        entry_text = entry_local.strftime("%H:%M")
        expiry_text = expiry_local.strftime("%H:%M")
        if expiry_local.date() != entry_local.date():
            expiry_text += expiry_local.strftime(" (%Y-%m-%d)")
        offset_td = entry_local.utcoffset()
        entry_offset = int(offset_td.total_seconds() // 60) if offset_td is not None else 0
        zone = "UTC" + entry_local.isoformat()[-6:]
        if expiry_local.utcoffset() != entry_local.utcoffset():
            zone += " at entry / UTC" + expiry_local.isoformat()[-6:] + " at expiry"
        fields = (
            ("Asset", asset),
            ("Date", entry_local.strftime("%Y-%m-%d")),
            ("Entry time", entry_text),
            ("Expiry time", expiry_text),
            ("Time zone", zone),
            ("Signal", plan["signal"]),
            ("Reason", plan["reason"]),
        )
        caption = chr(10).join(f"<b>{label}</b>: {escape(str(value))}" for label, value in fields)
        self.service.telegram.enqueue_alert(
            f"stubay:{bucket}:{asset}", asset, plan["signal"], caption, entry, utc_now_ts(),
            self._chart(snapshot, plan["signal"], plan["confidence"], entry_text, expiry_text, entry_offset),
        )

    def open_due(self, now: int, lookup: Callable) -> int:
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT bucket, asset, plan_json, snapshot_json FROM stubay_memory "
                "WHERE status = 'SCHEDULED' AND entry_ts <= ? ORDER BY entry_ts",
                (now,),
            ).fetchall()
        opened = 0
        config = self.service.config
        for row in rows:
            plan = json.loads(row["plan_json"])
            entry, expiry = plan["entry_ts"], plan["expiry_ts"]
            deadline = min(expiry, entry + 30)
            quote = lookup(row["asset"], entry, 0) if now < deadline else None
            if quote and (quote[1] != entry or not math.isfinite(quote[0]) or quote[0] <= 0):
                quote = None
            if not quote and now < deadline and now >= entry:
                try:
                    import market_feed as mf
                    live_feed = mf.get_candles(symbol=row["asset"], timeframe="15s", limit=15, priority=True)
                    if live_feed.get("candles"):
                        quote = (float(live_feed["candles"][-1]["close"]), entry)
                except Exception:
                    pass
            with self.store.transaction() as conn:
                current = conn.execute(
                    "SELECT status FROM stubay_memory WHERE bucket = ? AND asset = ?",
                    (row["bucket"], row["asset"]),
                ).fetchone()
                if current is None or current["status"] != "SCHEDULED":
                    continue
                if not quote:
                    if now >= deadline:
                        conn.execute("UPDATE stubay_memory SET status = 'MISSED_ENTRY' WHERE bucket = ? AND asset = ?",
                                     (row["bucket"], row["asset"]))
                    continue
                stake, blocked = paper_risk(conn, RECIPE, now, config.starting_balance,
                                            plan.get("paper_stake", config.default_stake), config.fee_rate, include_pending=False)
                if blocked:
                    plan["reason"] = blocked
                    conn.execute("UPDATE stubay_memory SET status = 'RISK_BLOCKED', plan_json = ? WHERE bucket = ? AND asset = ?",
                                 (json.dumps(plan), row["bucket"], row["asset"]))
                    continue
                duration = expiry - entry
                snapshot = json.loads(row["snapshot_json"])
                payout = snapshot["payout"] / 100
                model_id = snapshot.get("model", "legacy-stubay")
                asset = row["asset"]
                price, quote_ts = quote
                probability = plan["confidence"] / 100
                obs_id, _ = self.store.record_observation(
                    asset, duration, entry, quote_ts, price, payout, config.fee_rate, config.tie_rule,
                    "Quotex 5s candle close", FEED_KIND, {},
                )
                signal_id, _ = self.store.record_signal_or_skip(
                    obs_id, asset, duration, entry, plan["signal"], probability,
                    compute_break_even_probability(payout, config.fee_rate, config.tie_rule),
                    payout, "TRADE", None, model_id, RECIPE, FEED_KIND,
                )
                trade, created = self.store.open_paper_trade(
                    signal_id, obs_id, asset, duration, entry, price, plan["signal"], probability,
                    stake, payout, config.fee_rate, config.tie_rule, model_id, RECIPE, FEED_KIND,
                )
                conn.execute("UPDATE stubay_memory SET status = 'OPENED', trade_id = ? WHERE bucket = ? AND asset = ?",
                             (trade["trade_id"], row["bucket"], asset))
                opened += int(created)
        return opened
