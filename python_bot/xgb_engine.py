"""Independent 120-second candle-direction model and paper ledger."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path


BAR_SECONDS = 15
HORIZON_SECONDS = 120
WINDOW = 32
FEATURES = (
    "return_1", "return_4", "return_8", "return_16", "volatility_8",
    "volatility_24", "range_8", "body_fraction", "upper_wick", "lower_wick",
    "ema_spread", "rsi_balance", "breakout_position", "utc_sin", "utc_cos",
)
SCHEMA = 1


@dataclass(frozen=True)
class Candle:
    close_ts: int
    open: float
    high: float
    low: float
    close: float
    payout: float | None = None


@dataclass(frozen=True)
class Example:
    asset: str
    timestamp: int
    features: tuple[float, ...]
    outcome: int  # UP 1, DOWN 0, TIE -1
    payout: float | None

    @property
    def end(self) -> int:
        return self.timestamp + HORIZON_SECONDS


def symbol_market(asset: str) -> str:
    return "otc" if asset.upper().replace("_", "-").endswith("-OTC") else "real"


def validate_candle(candle: Candle) -> None:
    if candle.close_ts <= 0 or candle.close_ts % BAR_SECONDS:
        raise ValueError("Candle close timestamps must be Unix seconds on 15-second boundaries.")
    prices = (candle.open, candle.high, candle.low, candle.close)
    if not all(math.isfinite(value) and value > 0 for value in prices):
        raise ValueError("Invalid candle price.")
    if candle.low > min(candle.open, candle.close) or candle.high < max(candle.open, candle.close):
        raise ValueError("Invalid candle high/low.")
    if candle.payout is not None and not 0 < candle.payout <= 1:
        raise ValueError("Payout must be a decimal fraction between 0 and 1.")


def open_store(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS candles (
            asset TEXT NOT NULL, market TEXT NOT NULL, close_ts INTEGER NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
            payout REAL, PRIMARY KEY(asset, close_ts)
        );
        CREATE INDEX IF NOT EXISTS candles_market_time ON candles(market, close_ts);
        CREATE TABLE IF NOT EXISTS demo (
            asset TEXT NOT NULL, close_ts INTEGER NOT NULL, expiry_ts INTEGER NOT NULL,
            signal TEXT NOT NULL, probability_up REAL, payout REAL, strike REAL NOT NULL,
            model_id TEXT, reason TEXT NOT NULL, status TEXT NOT NULL,
            exit_price REAL, pnl REAL, PRIMARY KEY(asset, close_ts)
        );
    """)
    return conn


def write_candles(conn: sqlite3.Connection, asset: str, candles: list[Candle]) -> int:
    for candle in candles:
        validate_candle(candle)
    with conn:
        conn.executemany(
            """INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(asset, close_ts) DO UPDATE SET payout = COALESCE(candles.payout, excluded.payout)""",
            [(asset, symbol_market(asset), c.close_ts, c.open, c.high, c.low, c.close, c.payout) for c in candles],
        )
    return len(candles)


def read_candles(conn: sqlite3.Connection, market: str) -> dict[str, list[Candle]]:
    series: dict[str, list[Candle]] = {}
    for asset, *values in conn.execute(
        "SELECT asset, close_ts, open, high, low, close, payout FROM candles WHERE market = ? ORDER BY asset, close_ts",
        (market,),
    ):
        series.setdefault(asset, []).append(Candle(*values))
    return series


def _ema(values: list[float], span: int) -> float:
    alpha = 2 / (span + 1)
    result = values[0]
    for value in values[1:]:
        result += alpha * (value - result)
    return result


def features(window: list[Candle]) -> tuple[float, ...] | None:
    from statistics import pstdev

    if len(window) != WINDOW or window[-1].close_ts - window[0].close_ts != (WINDOW - 1) * BAR_SECONDS:
        return None
    if any(right.close_ts - left.close_ts != BAR_SECONDS for left, right in zip(window, window[1:])):
        return None
    candle = window[-1]
    prices = [item.close for item in window]
    returns = [math.log(right / left) for left, right in zip(prices, prices[1:])]
    span = max(candle.high - candle.low, 1e-12)
    prior = window[-21:-1]
    prior_high = max(item.high for item in prior)
    prior_low = min(item.low for item in prior)
    now = candle.close_ts % 86400 / 86400 * math.tau
    absolute_move = sum(abs(value) for value in returns[-14:])
    return (
        *(math.log(prices[-1] / prices[-1 - lag]) for lag in (1, 4, 8, 16)),
        pstdev(returns[-8:]), pstdev(returns[-24:]),
        (max(item.high for item in window[-8:]) - min(item.low for item in window[-8:])) / candle.close,
        (candle.close - candle.open) / span,
        (candle.high - max(candle.open, candle.close)) / span,
        (min(candle.open, candle.close) - candle.low) / span,
        (_ema(prices, 6) - _ema(prices, 18)) / candle.close,
        sum(returns[-14:]) / max(absolute_move, 1e-12),
        (candle.close - prior_low) / max(prior_high - prior_low, 1e-12),
        math.sin(now), math.cos(now),
    )


def examples(series: dict[str, list[Candle]]) -> list[Example]:
    rows = []
    steps = HORIZON_SECONDS // BAR_SECONDS
    for asset, candles in series.items():
        for index in range(WINDOW - 1, len(candles) - steps):
            current = candles[index]
            future = candles[index + steps]
            if future.close_ts - current.close_ts != HORIZON_SECONDS:
                continue
            vector = features(candles[index - WINDOW + 1:index + 1])
            if vector is None:
                continue
            outcome = 1 if future.close > current.close else 0 if future.close < current.close else -1
            rows.append(Example(asset, current.close_ts, vector, outcome, current.payout))
    return sorted(rows, key=lambda row: (row.timestamp, row.asset))


def split_fit_calibration(rows: list[Example], cutoff: int) -> tuple[list[Example], list[Example]]:
    settled = [row for row in rows if row.end < cutoff and row.outcome >= 0]
    times = sorted({row.timestamp for row in settled})
    if not times:
        return [], []
    boundary = times[int(len(times) * 0.75)]
    return (
        [row for row in settled if row.end < boundary],
        [row for row in settled if row.timestamp >= boundary],
    )


def walk_forward(rows: list[Example], folds: int):
    times = sorted({row.timestamp for row in rows})
    if len(times) < 5 * folds:
        raise ValueError("Too few distinct timestamps for walk-forward validation.")
    first = len(times) // 2
    for fold in range(folds):
        start = first + (len(times) - first) * fold // folds
        stop = first + (len(times) - first) * (fold + 1) // folds
        cutoff = times[start]
        fit, calibration = split_fit_calibration(rows, cutoff)
        test = [row for row in rows if cutoff <= row.timestamp <= times[stop - 1]]
        yield fit, calibration, test


def _matrix(rows):
    import numpy as np

    return np.asarray(rows, dtype=float)


def _logit(probability: float) -> float:
    p = min(1 - 1e-6, max(1e-6, probability))
    return math.log(p / (1 - p))


def calibrated(raw: float, slope: float, intercept: float) -> float:
    score = min(40.0, max(-40.0, slope * _logit(raw) + intercept))
    return 1 / (1 + math.exp(-score))


def fit_model(fit: list[Example], calibration: list[Example]):
    from sklearn.linear_model import LogisticRegression
    from xgboost import XGBClassifier

    for label, samples, minimum in (("fit", fit, 120), ("calibration", calibration, 40)):
        if len(samples) < minimum or {row.outcome for row in samples} != {0, 1}:
            raise ValueError(f"Need at least {minimum} decisive {label} samples with both directions; got {len(samples)}.")
    model = XGBClassifier(
        n_estimators=80, max_depth=3, learning_rate=0.05, min_child_weight=6,
        subsample=0.85, colsample_bytree=0.85, tree_method="hist", n_jobs=2,
        objective="binary:logistic", eval_metric="logloss", random_state=42,
    )
    model.fit(_matrix([row.features for row in fit]), [row.outcome for row in fit])
    raw = model.predict_proba(_matrix([row.features for row in calibration]))[:, 1]
    sigmoid = LogisticRegression().fit([[_logit(value)] for value in raw], [row.outcome for row in calibration])
    return model, float(sigmoid.coef_[0][0]), float(sigmoid.intercept_[0])


def decision(probability_up: float, payout: float | None, margin: float) -> tuple[str, str]:
    if payout is None:
        return "SKIP", "Payout unavailable."
    direction = "UP" if probability_up > 0.5 else "DOWN"
    confidence = max(probability_up, 1 - probability_up)
    if confidence <= 1 / (1 + payout) + margin:
        return "SKIP", "Calibrated probability is below the payout-adjusted threshold."
    return direction, "Calibrated probability clears the payout-adjusted threshold."


def trade_result(direction: str, outcome: int, payout: float) -> float:
    if outcome == -1:
        return 0.0
    return payout if (direction == "UP") == (outcome == 1) else -1.0


def backtest(predictions: list[tuple[Example, float]], margin: float, assumed_payout: float | None) -> dict:
    results = []
    busy_until = 0
    skipped_payout = 0
    for row, probability in predictions:
        payout = row.payout if row.payout is not None else assumed_payout
        if payout is None:
            skipped_payout += 1
            continue
        signal, _ = decision(probability, payout, margin)
        if signal == "SKIP" or row.timestamp < busy_until:
            continue
        results.append(trade_result(signal, row.outcome, payout))
        busy_until = row.end
    equity = peak = drawdown = 0.0
    for result in results:
        equity += result
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(results), "wins": sum(value > 0 for value in results),
        "losses": sum(value < 0 for value in results), "ties": results.count(0.0),
        "net_units": round(equity, 6), "max_drawdown_units": round(drawdown, 6),
        "missing_payout_rows": skipped_payout, "assumed_payout_for_missing": assumed_payout,
    }


def evaluate(rows: list[Example], folds: int, margin: float, assumed_payout: float | None) -> dict:
    predictions = []
    reports = []
    for index, (fit, calibration, test) in enumerate(walk_forward(rows, folds), 1):
        if len(test) < 20:
            raise ValueError(f"Fold {index} needs at least 20 test observations.")
        model, slope, intercept = fit_model(fit, calibration)
        raw = model.predict_proba(_matrix([row.features for row in test]))[:, 1]
        probabilities = [calibrated(value, slope, intercept) for value in raw]
        scored = [(r.outcome, float(p), c) for r, p, c in zip(test, raw, probabilities) if r.outcome >= 0]
        if not scored:
            raise ValueError(f"Fold {index} has no decisive test outcomes.")
        reports.append({
            "fold": index, "fit": len(fit), "calibration": len(calibration), "test": len(test),
            "test_start": test[0].timestamp, "test_end": test[-1].timestamp,
            "brier_raw": round(sum((p - y) ** 2 for y, p, _ in scored) / len(scored), 6),
            "brier_calibrated": round(sum((c - y) ** 2 for y, _, c in scored) / len(scored), 6),
            "brier_even_odds": 0.25,
            "direction_accuracy": round(sum((c > 0.5) == bool(y) for y, _, c in scored) / len(scored), 6),
        })
        predictions.extend(zip(test, probabilities))
    return {"folds": reports, "backtest": backtest(predictions, margin, assumed_payout)}


def save_model(path: Path, market: str, rows: list[Example], cutoff: int, report: dict) -> dict:
    fit, calibration = split_fit_calibration(rows, cutoff)
    model, slope, intercept = fit_model(fit, calibration)
    metadata = {
        "schema": SCHEMA, "market": market, "bar_seconds": BAR_SECONDS, "horizon_seconds": HORIZON_SECONDS,
        "features": list(FEATURES), "slope": slope, "intercept": intercept,
        "known_through": max(row.end for row in calibration), "cutoff": cutoff,
        "report": report,
    }
    model.get_booster().set_attr(signaldesk=json.dumps(metadata, allow_nan=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".ubj", delete=False) as tmp:
        temp_path = Path(tmp.name)
    try:
        model.save_model(temp_path)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    return metadata


def load_model(path: Path, market: str):
    from xgboost import XGBClassifier

    model = XGBClassifier(n_jobs=2)
    model.load_model(path)
    metadata = json.loads(model.get_booster().attr("signaldesk") or "{}")
    expected = (SCHEMA, market, BAR_SECONDS, HORIZON_SECONDS, list(FEATURES))
    actual = tuple(metadata.get(key) for key in ("schema", "market", "bar_seconds", "horizon_seconds", "features"))
    if actual != expected:
        raise ValueError("Model market, horizon, or feature schema does not match.")
    return model, metadata


def predict(model, metadata: dict, vector: tuple[float, ...]) -> float:
    raw = float(model.predict_proba(_matrix([vector]))[0, 1])
    return calibrated(raw, metadata["slope"], metadata["intercept"])


def settle_demo(conn: sqlite3.Connection, asset: str, latest: int) -> list[dict]:
    settled = []
    with conn:
        rows = conn.execute(
            """SELECT d.close_ts, d.expiry_ts, d.signal, d.payout, d.strike, c.close
               FROM demo d LEFT JOIN candles c ON c.asset = d.asset AND c.close_ts = d.expiry_ts
               WHERE d.asset = ? AND d.status = 'OPEN' AND d.expiry_ts <= ?""", (asset, latest),
        ).fetchall()
        for start, expiry, signal, payout, strike, quote in rows:
            if quote is None and latest < expiry + 60:
                continue
            result = None if quote is None else trade_result(signal, 1 if quote > strike else 0 if quote < strike else -1, payout)
            status = "UNRESOLVED" if result is None else "WIN" if result > 0 else "LOSS" if result < 0 else "TIE"
            updated = conn.execute(
                "UPDATE demo SET status = ?, exit_price = ?, pnl = ? WHERE asset = ? AND close_ts = ? AND status = 'OPEN'",
                (status, quote, result, asset, start),
            )
            if updated.rowcount:
                settled.append({"asset": asset, "close_ts": start, "expiry_ts": expiry, "status": status, "pnl": result})
    return settled
