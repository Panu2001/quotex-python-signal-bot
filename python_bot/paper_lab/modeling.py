"""
Decision-Time Feature Extraction, Probability Models (Baseline + Logistic Regression
with Platt Calibration), and Chronological Walk-Forward Evaluation.
Pure Python standard library — zero external ML dependencies required.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from typing import Any, Dict, List, Optional, Tuple

from strategies import STRATEGY_CATALOG, evaluate_all_strategies

from .config import DEFAULT_RULE_PARAMS, EvaluationCriteria, compute_break_even_probability


FEATURE_NAMES: List[str] = [
    "ret_1",
    "ret_3",
    "ret_5",
    "ret_10",
    "vol_5",
    "vol_15",
    "vol_ratio",
    "rsi_14_norm",
    "ema_spread_norm",
    "bb_zscore",
    "body_ratio",
    "utc_hour_sin",
    "utc_hour_cos",
]

STRATEGY_IDS = (
    "ema_ribbon",
    "macd_momentum",
    "supertrend",
    "rsi_bollinger",
    "sr_pivots",
    "candlestick_action",
    "donchian_squeeze",
)
STRATEGY_FEATURE_NAMES = [f"vote_{strategy_id}" for strategy_id in STRATEGY_IDS]
STRATEGY_CONFIG = {
    "enabled_strategies": {
        strategy["id"]: strategy["id"] in STRATEGY_IDS for strategy in STRATEGY_CATALOG
    }
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _sigmoid(z: float) -> float:
    z = _clamp(z, -30.0, 30.0)
    return 1.0 / (1.0 + math.exp(-z))


def _ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    e = values[0]
    for v in values[1:]:
        e = alpha * v + (1.0 - alpha) * e
    return e


def _std(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / n
    return math.sqrt(max(0.0, var))


def extract_features(candles: List[Dict[str, Any]], decision_ts: int) -> Optional[Dict[str, float]]:
    """
    Compute decision-time features strictly from candles with time <= decision_ts.
    Returns None if insufficient valid candles or missing/NaN values.
    """
    valid: List[Dict[str, float]] = []
    for c in candles:
        try:
            t = int(c["time"])
            if t > decision_ts:
                continue
            o = float(c["open"])
            h = float(c["high"])
            l = float(c["low"])
            cl = float(c["close"])
            if any(math.isnan(x) or math.isinf(x) or x <= 0 for x in (o, h, l, cl)):
                return None
            if h < l:
                return None
            valid.append({"time": float(t), "open": o, "high": h, "low": l, "close": cl})
        except (KeyError, TypeError, ValueError):
            return None

    if len(valid) < 22:
        return None

    closes = [c["close"] for c in valid]
    last = valid[-1]
    c_now = closes[-1]

    def log_ret(lag: int) -> float:
        prev = closes[-1 - lag]
        if prev <= 0:
            return 0.0
        return math.log(c_now / prev)

    rets_1 = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets_1) < 15:
        return None

    vol_5 = _std(rets_1[-5:])
    vol_15 = _std(rets_1[-15:])
    vol_ratio = (vol_5 / vol_15) if vol_15 > 1e-9 else 1.0

    # RSI-14
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(len(closes) - 14, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(len(closes) - 14, len(closes))]
    avg_g = sum(gains) / 14.0
    avg_l = sum(losses) / 14.0
    if avg_l <= 1e-12:
        rsi = 100.0 if avg_g > 0 else 50.0
    else:
        rs = avg_g / avg_l
        rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi_norm = (rsi - 50.0) / 50.0

    # EMA 9 vs EMA 21 spread normalized by price & volatility
    ema_fast = _ema(closes[-30:], 9)
    ema_slow = _ema(closes[-40:], 21)
    ema_spread = (ema_fast - ema_slow) / c_now

    # Bollinger Z-score (20)
    win20 = closes[-20:]
    mean20 = sum(win20) / 20.0
    std20 = _std(win20)
    bb_z = ((c_now - mean20) / std20) if std20 > 1e-12 else 0.0

    # Last candle body ratio
    rng = max(1e-12, last["high"] - last["low"])
    body_ratio = (last["close"] - last["open"]) / rng

    # UTC hour cyclic encoding
    utc_hour = (int(decision_ts) // 3600) % 24
    angle = 2.0 * math.pi * utc_hour / 24.0

    features = {
        "ret_1": round(_clamp(log_ret(1), -0.1, 0.1), 8),
        "ret_3": round(_clamp(log_ret(3), -0.15, 0.15), 8),
        "ret_5": round(_clamp(log_ret(5), -0.2, 0.2), 8),
        "ret_10": round(_clamp(log_ret(10), -0.3, 0.3), 8),
        "vol_5": round(_clamp(vol_5, 0.0, 0.1), 8),
        "vol_15": round(_clamp(vol_15, 0.0, 0.1), 8),
        "vol_ratio": round(_clamp(vol_ratio, 0.0, 5.0), 6),
        "rsi_14_norm": round(_clamp(rsi_norm, -1.0, 1.0), 6),
        "ema_spread_norm": round(_clamp(ema_spread, -0.05, 0.05), 8),
        "bb_zscore": round(_clamp(bb_z, -4.0, 4.0), 6),
        "body_ratio": round(_clamp(body_ratio, -1.0, 1.0), 6),
        "utc_hour_sin": round(math.sin(angle), 6),
        "utc_hour_cos": round(math.cos(angle), 6),
    }

    # Use the same strategy definitions as the scanner, with stable defaults
    # so changes to dashboard preferences don't alter a trained model's inputs.
    votes = evaluate_all_strategies(valid, config=STRATEGY_CONFIG)["votes"]
    direction = {"CALL": 1.0, "PUT": -1.0, "NEUTRAL": 0.0}
    for vote in votes:
        if vote["id"] in STRATEGY_IDS:
            features[f"vote_{vote['id']}"] = direction[vote["signal"]] * vote["strength"]
    return features


class ProbabilityModel:
    """
    Serializable binary-option UP-probability model.
    Supports:
    - 'momentum_baseline': transparent fixed-weight baseline rule
    - 'logistic_regression': L2-regularized logistic regression with z-score scaling
      and Platt/temperature calibration on a chronological holdout slice.
    """

    def __init__(
        self,
        model_type: str = "momentum_baseline",
        feature_names: Optional[List[str]] = None,
        means: Optional[List[float]] = None,
        stds: Optional[List[float]] = None,
        weights: Optional[List[float]] = None,
        bias: float = 0.0,
        cal_a: float = 1.0,
        cal_b: float = 0.0,
    ):
        self.model_type = model_type
        self.feature_names = list(feature_names or FEATURE_NAMES)
        n = len(self.feature_names)
        self.means = list(means) if means else [0.0] * n
        self.stds = list(stds) if stds else [1.0] * n
        self.weights = list(weights) if weights else [0.0] * n
        self.bias = float(bias)
        self.cal_a = float(cal_a)
        self.cal_b = float(cal_b)

    @classmethod
    def baseline(cls) -> "ProbabilityModel":
        """Fixed momentum + trend baseline model for cold start."""
        weights = {
            "ret_1": 0.35,
            "ret_3": 0.45,
            "ret_5": 0.35,
            "ret_10": 0.20,
            "vol_5": 0.0,
            "vol_15": 0.0,
            "vol_ratio": 0.0,
            "rsi_14_norm": -0.15,
            "ema_spread_norm": 0.50,
            "bb_zscore": -0.10,
            "body_ratio": 0.20,
            "utc_hour_sin": 0.0,
            "utc_hour_cos": 0.0,
        }
        scales = {
            "ret_1": 0.001,
            "ret_3": 0.002,
            "ret_5": 0.0025,
            "ret_10": 0.0035,
            "vol_5": 0.001,
            "vol_15": 0.001,
            "vol_ratio": 1.0,
            "rsi_14_norm": 0.5,
            "ema_spread_norm": 0.0015,
            "bb_zscore": 1.5,
            "body_ratio": 0.6,
            "utc_hour_sin": 1.0,
            "utc_hour_cos": 1.0,
        }
        return cls(
            model_type="momentum_baseline",
            feature_names=FEATURE_NAMES,
            means=[0.0 for _ in FEATURE_NAMES],
            stds=[scales[f] for f in FEATURE_NAMES],
            weights=[weights[f] for f in FEATURE_NAMES],
            bias=0.0,
            cal_a=0.65,
            cal_b=0.0,
        )

    def raw_logit(self, features: Dict[str, float]) -> float:
        z = self.bias
        for i, fname in enumerate(self.feature_names):
            fallback = self.means[i] if fname in STRATEGY_FEATURE_NAMES else 0.0
            x = float(features.get(fname, fallback))
            s = self.stds[i] if self.stds[i] > 1e-12 else 1.0
            xz = _clamp((x - self.means[i]) / s, -6.0, 6.0)
            z += self.weights[i] * xz
        return z

    def predict_up_prob(self, features: Dict[str, float]) -> float:
        raw = self.raw_logit(features)
        calibrated_logit = self.cal_a * raw + self.cal_b
        return _clamp(_sigmoid(calibrated_logit), 0.02, 0.98)

    def predict_direction_and_prob(self, features: Dict[str, float]) -> Tuple[str, float]:
        p_up = self.predict_up_prob(features)
        if p_up >= 0.5:
            return "CALL", p_up
        return "PUT", 1.0 - p_up

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_type": self.model_type,
            "feature_names": self.feature_names,
            "means": self.means,
            "stds": self.stds,
            "weights": self.weights,
            "bias": self.bias,
            "cal_a": self.cal_a,
            "cal_b": self.cal_b,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProbabilityModel":
        return cls(
            model_type=str(data.get("model_type", "momentum_baseline")),
            feature_names=list(data.get("feature_names", FEATURE_NAMES)),
            means=[float(x) for x in data.get("means", [])],
            stds=[float(x) for x in data.get("stds", [])],
            weights=[float(x) for x in data.get("weights", [])],
            bias=float(data.get("bias", 0.0)),
            cal_a=float(data.get("cal_a", 1.0)),
            cal_b=float(data.get("cal_b", 0.0)),
        )

    @classmethod
    def fit_logistic_regression(
        cls,
        samples: List[Dict[str, Any]],
        l2: float = 0.05,
        lr: float = 0.08,
        epochs: int = 180,
        feature_names: Optional[List[str]] = None,
    ) -> "ProbabilityModel":
        """
        Fit L2-regularized logistic regression on chronological labeled samples
        (each with 'features' dict and 'outcome' in {'UP', 'DOWN'}).
        Reserves the last 20% chronologically for Platt calibration so probabilities
        are well-calibrated on unseen future data.
        """
        decisive = [s for s in samples if s.get("outcome") in ("UP", "DOWN")]
        if len(decisive) < 20:
            return cls.baseline()

        split = max(14, int(len(decisive) * 0.8))
        train_rows = decisive[:split]
        cal_rows = decisive[split:]
        if feature_names is not None and cal_rows:
            calibration_start = int(cal_rows[0]["decision_ts"])
            train_rows = [row for row in train_rows if int(row["outcome_known_ts"]) < calibration_start]
            if len(train_rows) < 14 or {row["outcome"] for row in train_rows} != {"UP", "DOWN"}:
                raise ValueError("Insufficient settled outcomes before calibration.")

        # Previously saved rows have no votes. Wait for observed votes before
        # fitting a new input and treat missing values as centered, not neutral votes.
        fnames = list(feature_names) if feature_names is not None else list(FEATURE_NAMES) + [
            name for name in STRATEGY_FEATURE_NAMES
            if sum(name in row["features"] for row in train_rows) >= 20
        ]
        n_feat = len(fnames)
        n_train = len(train_rows)

        # Standardize using strictly training rows
        means: List[float] = []
        stds: List[float] = []
        y_train = [1.0 if r["outcome"] == "UP" else 0.0 for r in train_rows]

        for name in fnames:
            if name in STRATEGY_FEATURE_NAMES:
                col = [float(row["features"][name]) for row in train_rows if name in row["features"]]
            else:
                col = [float(row["features"].get(name, 0.0)) for row in train_rows]
            m = sum(col) / len(col)
            s = _std(col)
            means.append(m)
            stds.append(s if s > 1e-9 else 1.0)

        fallbacks = [means[i] if name in STRATEGY_FEATURE_NAMES else 0.0 for i, name in enumerate(fnames)]
        X_norm = [
            [
                _clamp((float(row["features"].get(name, fallbacks[j])) - means[j]) / stds[j], -6.0, 6.0)
                for j, name in enumerate(fnames)
            ]
            for row in train_rows
        ]

        weights = [0.0] * n_feat
        bias = 0.0
        for ep in range(epochs):
            step_lr = lr / (1.0 + 0.005 * ep)
            grad_w = [0.0] * n_feat
            grad_b = 0.0
            for i in range(n_train):
                z = bias + sum(weights[j] * X_norm[i][j] for j in range(n_feat))
                p = _sigmoid(z)
                err = p - y_train[i]
                grad_b += err
                for j in range(n_feat):
                    grad_w[j] += err * X_norm[i][j]
            bias -= step_lr * (grad_b / n_train)
            for j in range(n_feat):
                weights[j] -= step_lr * ((grad_w[j] / n_train) + l2 * weights[j])

        model = cls(
            model_type="logistic_regression",
            feature_names=fnames,
            means=means,
            stds=stds,
            weights=weights,
            bias=bias,
            cal_a=1.0,
            cal_b=0.0,
        )

        # Chronological Platt scaling calibration on holdout slice
        if len(cal_rows) >= 8:
            logits = [model.raw_logit(r["features"]) for r in cal_rows]
            y_cal = [1.0 if r["outcome"] == "UP" else 0.0 for r in cal_rows]
            cal_a = 1.0
            cal_b = 0.0
            for _ in range(120):
                ga = 0.0
                gb = 0.0
                for lg, yv in zip(logits, y_cal):
                    p = _sigmoid(cal_a * lg + cal_b)
                    err = p - yv
                    ga += err * lg
                    gb += err
                cal_a -= 0.05 * ((ga / len(cal_rows)) + 0.02 * (cal_a - 1.0))
                cal_b -= 0.05 * ((gb / len(cal_rows)) + 0.02 * cal_b)
                cal_a = _clamp(cal_a, 0.25, 2.5)
                cal_b = _clamp(cal_b, -1.5, 1.5)
            model.cal_a = round(cal_a, 6)
            model.cal_b = round(cal_b, 6)

        return model


def decide_on_observation(
    model: ProbabilityModel,
    rule_params: Dict[str, Any],
    features: Dict[str, float],
    decision_ts: int,
    payout: float,
    fee_rate: float = 0.0,
    tie_rule: str = "refund",
) -> Tuple[str, str, float, float, Optional[str]]:
    """
    Evaluate a single observation under (model, rule_params).
    Returns: (action ['TRADE'|'SKIP'], direction ['CALL'|'PUT'], estimated_prob, break_even_prob, skip_reason)
    """
    rules = dict(DEFAULT_RULE_PARAMS)
    rules.update(rule_params or {})

    direction, est_prob = model.predict_direction_and_prob(features)
    be_prob = compute_break_even_probability(payout=payout, fee_rate=fee_rate, tie_rule=tie_rule)

    utc_hour = (int(decision_ts) // 3600) % 24
    allowed_hours = rules.get("allowed_utc_hours", list(range(24)))
    if utc_hour not in allowed_hours:
        return "SKIP", direction, est_prob, be_prob, f"utc_hour_{utc_hour}_not_allowed"

    vol = float(features.get("vol_15", 0.0))
    if vol < float(rules["volatility_min"]):
        return "SKIP", direction, est_prob, be_prob, f"volatility_below_min({vol:.6f})"
    if vol > float(rules["volatility_max"]):
        return "SKIP", direction, est_prob, be_prob, f"volatility_above_max({vol:.6f})"

    required_prob = max(
        float(rules["min_signal_probability"]),
        be_prob + float(rules["min_edge_margin"]),
    )
    if est_prob < required_prob:
        return (
            "SKIP",
            direction,
            est_prob,
            be_prob,
            f"prob_{est_prob:.4f}_below_threshold_{required_prob:.4f}",
        )

    return "TRADE", direction, est_prob, be_prob, None


def compute_calibration_metrics(probs: List[float], outcomes: List[float], n_bins: int = 5) -> Dict[str, float]:
    """Compute Brier score and Expected Calibration Error (ECE)."""
    n = len(probs)
    if n == 0:
        return {"brier_score": 0.25, "ece": 0.0}
    brier = sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / n
    ece = 0.0
    for b in range(n_bins):
        lo = b / n_bins
        hi = (b + 1) / n_bins
        bucket = [
            (p, y)
            for p, y in zip(probs, outcomes)
            if (lo <= p < hi) or (b == n_bins - 1 and p == 1.0)
        ]
        if not bucket:
            continue
        avg_p = sum(p for p, _ in bucket) / len(bucket)
        avg_y = sum(y for _, y in bucket) / len(bucket)
        ece += (len(bucket) / n) * abs(avg_p - avg_y)
    return {"brier_score": round(brier, 6), "ece": round(ece, 6)}


def simulate_strategy_on_rows(
    model: ProbabilityModel,
    rule_params: Dict[str, Any],
    rows: List[Dict[str, Any]],
    stake: float = 10.0,
    starting_balance: float = 1000.0,
    min_breakdown_samples: int = 5,
) -> Dict[str, Any]:
    """
    Simulate (model, rule_params) over an exact sequence of chronological observations
    with identical settlement prices, payout, fees, and tie rules.
    """
    balance = float(starting_balance)
    peak = balance
    max_dd_pct = 0.0
    wins = 0
    losses = 0
    ties = 0
    net_return = 0.0
    probs: List[float] = []
    outcomes_bin: List[float] = []
    by_pair: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        action, direction, est_prob, _be, _reason = decide_on_observation(
            model=model,
            rule_params=rule_params,
            features=row["features"],
            decision_ts=int(row["decision_ts"]),
            payout=float(row["payout"]),
            fee_rate=float(row.get("fee_rate", 0.0)),
            tie_rule=str(row.get("tie_rule", "refund")),
        )
        if action != "TRADE":
            continue

        obs_outcome = str(row["outcome"])
        payout = float(row["payout"])
        fee_rate = float(row.get("fee_rate", 0.0))
        tie_rule = str(row.get("tie_rule", "refund"))

        if obs_outcome == "TIE":
            ties += 1
            pnl = -stake * (1.0 + fee_rate) if tie_rule == "loss" else -stake * fee_rate
            won_flag: Optional[float] = 0.0 if tie_rule == "loss" else None
        else:
            won = (direction == "CALL" and obs_outcome == "UP") or (
                direction == "PUT" and obs_outcome == "DOWN"
            )
            if won:
                wins += 1
                pnl = stake * (payout - fee_rate)
                won_flag = 1.0
            else:
                losses += 1
                pnl = -stake * (1.0 + fee_rate)
                won_flag = 0.0

        net_return += pnl
        balance += pnl
        if balance > peak:
            peak = balance
        if peak > 0:
            dd = ((peak - balance) / peak) * 100.0
            if dd > max_dd_pct:
                max_dd_pct = dd

        if won_flag is not None:
            probs.append(est_prob)
            outcomes_bin.append(won_flag)

        key = f"{row['asset']}|{int(row['expiry_sec'])}s"
        bucket = by_pair.setdefault(
            key,
            {"asset": row["asset"], "expiry_sec": int(row["expiry_sec"]), "trades": 0, "wins": 0, "losses": 0, "ties": 0, "net_return": 0.0},
        )
        bucket["trades"] += 1
        bucket["net_return"] = round(bucket["net_return"] + pnl, 4)
        if obs_outcome == "TIE":
            bucket["ties"] += 1
        elif won_flag == 1.0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1

    trade_count = wins + losses + ties
    decisive = wins + losses
    win_rate = (wins / decisive) if decisive > 0 else 0.0
    trade_freq = (trade_count / len(rows)) if rows else 0.0
    cal = compute_calibration_metrics(probs, outcomes_bin)

    breakdown = {}
    for k, b in by_pair.items():
        if b["trades"] >= min_breakdown_samples:
            d = b["wins"] + b["losses"]
            b["win_rate"] = round((b["wins"] / d) if d > 0 else 0.0, 4)
            breakdown[k] = b

    return {
        "observations": len(rows),
        "trade_count": trade_count,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "win_rate": round(win_rate, 4),
        "trade_frequency": round(trade_freq, 4),
        "net_paper_return": round(net_return, 4),
        "roi_pct": round((net_return / starting_balance) * 100.0, 2) if starting_balance > 0 else 0.0,
        "max_drawdown_pct": round(max_dd_pct, 2),
        "brier_score": cal["brier_score"],
        "ece": cal["ece"],
        "breakdown": breakdown,
    }


def evaluate_walk_forward(
    dataset: List[Dict[str, Any]],
    incumbent_model: ProbabilityModel,
    incumbent_rules: Dict[str, Any],
    candidate_rules: Dict[str, Any],
    criteria: EvaluationCriteria,
    retrain_candidate_model: bool = False,
    stake: float = 10.0,
    starting_balance: float = 1000.0,
    candidate_model_override: Optional[ProbabilityModel] = None,
) -> Dict[str, Any]:
    """
    Strict chronological walk-forward comparison of candidate vs incumbent.
    - Guarantees every training sample had outcome_known_ts < eval_start_ts (no look-ahead).
    - Compares candidate and incumbent on the exact same out-of-sample evaluation windows.
    - Evaluates promotion strictly against preconfigured EvaluationCriteria.
    """
    ordered = sorted(dataset, key=lambda r: int(r["decision_ts"]))
    n_total = len(ordered)
    n_windows = max(2, int(criteria.n_walk_forward_windows))
    min_train = max(15, int(criteria.min_train_samples))

    if n_total < min_train + n_windows * max(3, criteria.min_trades_per_window):
        return {
            "passed": False,
            "reason": f"Insufficient fresh labeled data ({n_total} observations; need >= {min_train + n_windows * max(3, criteria.min_trades_per_window)}).",
            "summary": {"observations_total": n_total, "windows_evaluated": 0},
            "breakdown": {},
        }

    # A batch of observations can share a timestamp, and its labels may settle
    # minutes later. Start evaluation only after enough outcomes were knowable.
    settled_times = sorted(int(row["outcome_known_ts"]) for row in ordered)
    first_eval = bisect_left(
        [int(row["decision_ts"]) for row in ordered],
        settled_times[min_train - 1] + 1,
    )
    eval_pool = ordered[first_eval:]
    win_size = len(eval_pool) // n_windows
    if win_size < 3:
        return {
            "passed": False,
            "reason": f"Evaluation window too small ({win_size} observations per window).",
            "summary": {"observations_total": n_total, "windows_evaluated": 0},
            "breakdown": {},
        }

    window_reports: List[Dict[str, Any]] = []
    all_eval_rows: List[Dict[str, Any]] = []
    cand_windows_won = 0
    cand_total_trades = 0
    cand_total_net = 0.0
    inc_total_net = 0.0
    cand_max_dd = 0.0
    cand_briers: List[float] = []
    inc_briers: List[float] = []
    final_trained_model: Optional[ProbabilityModel] = None

    for w_idx in range(n_windows):
        start_i = w_idx * win_size
        end_i = (w_idx + 1) * win_size if w_idx < n_windows - 1 else len(eval_pool)
        eval_rows = eval_pool[start_i:end_i]
        if not eval_rows:
            continue
        eval_start_ts = int(eval_rows[0]["decision_ts"])

        # Strict temporal barrier: ONLY training outcomes known BEFORE eval_start_ts
        train_rows = [
            r for r in ordered if int(r["outcome_known_ts"]) < eval_start_ts
        ]
        if len(train_rows) < min_train:
            return {
                "passed": False,
                "reason": f"Window {w_idx + 1} has only {len(train_rows)} strictly prior settled outcomes (min {min_train}).",
                "summary": {"observations_total": n_total, "windows_evaluated": w_idx},
                "breakdown": {},
            }

        if candidate_model_override is not None:
            cand_model = candidate_model_override
        elif retrain_candidate_model:
            cand_model = ProbabilityModel.fit_logistic_regression(train_rows)
            final_trained_model = cand_model
        else:
            cand_model = incumbent_model

        inc_res = simulate_strategy_on_rows(
            incumbent_model,
            incumbent_rules,
            eval_rows,
            stake=stake,
            starting_balance=starting_balance,
            min_breakdown_samples=criteria.min_asset_breakdown_samples,
        )
        cand_res = simulate_strategy_on_rows(
            cand_model,
            candidate_rules,
            eval_rows,
            stake=stake,
            starting_balance=starting_balance,
            min_breakdown_samples=criteria.min_asset_breakdown_samples,
        )

        beats = (
            cand_res["trade_count"] >= criteria.min_trades_per_window
            and cand_res["net_paper_return"] > inc_res["net_paper_return"]
        )
        if beats:
            cand_windows_won += 1

        cand_total_trades += cand_res["trade_count"]
        cand_total_net += cand_res["net_paper_return"]
        inc_total_net += inc_res["net_paper_return"]
        cand_max_dd = max(cand_max_dd, cand_res["max_drawdown_pct"])
        cand_briers.append(cand_res["brier_score"])
        inc_briers.append(inc_res["brier_score"])
        all_eval_rows.extend(eval_rows)

        window_reports.append(
            {
                "window": w_idx + 1,
                "train_samples": len(train_rows),
                "eval_samples": len(eval_rows),
                "eval_start_ts": eval_start_ts,
                "candidate": cand_res,
                "incumbent": inc_res,
                "candidate_beat_incumbent": beats,
            }
        )

    # Full out-of-sample aggregate breakdown
    agg_cand_model = final_trained_model or candidate_model_override or incumbent_model
    agg_cand = simulate_strategy_on_rows(
        agg_cand_model,
        candidate_rules,
        all_eval_rows,
        stake=stake,
        starting_balance=starting_balance,
        min_breakdown_samples=criteria.min_asset_breakdown_samples,
    )
    agg_inc = simulate_strategy_on_rows(
        incumbent_model,
        incumbent_rules,
        all_eval_rows,
        stake=stake,
        starting_balance=starting_balance,
        min_breakdown_samples=criteria.min_asset_breakdown_samples,
    )

    avg_cand_brier = sum(cand_briers) / len(cand_briers) if cand_briers else 0.25
    avg_inc_brier = sum(inc_briers) / len(inc_briers) if inc_briers else 0.25
    net_delta = round(cand_total_net - inc_total_net, 4)

    failures: List[str] = []
    if cand_total_trades < criteria.min_trades_total:
        failures.append(
            f"Candidate trade count {cand_total_trades} < minimum {criteria.min_trades_total}."
        )
    if cand_windows_won < criteria.min_windows_beating_incumbent:
        failures.append(
            f"Candidate beat incumbent in {cand_windows_won}/{len(window_reports)} windows (need >= {criteria.min_windows_beating_incumbent})."
        )
    if net_delta < criteria.min_net_return_improvement or net_delta <= 0:
        failures.append(
            f"Net paper return delta {net_delta:+.2f} did not exceed required improvement ({criteria.min_net_return_improvement:+.2f})."
        )
    if cand_max_dd > criteria.max_drawdown_pct:
        failures.append(
            f"Candidate max drawdown {cand_max_dd:.2f}% exceeded limit {criteria.max_drawdown_pct:.2f}%."
        )
    if (avg_cand_brier - avg_inc_brier) > criteria.max_brier_degradation:
        failures.append(
            f"Calibration Brier degraded by {avg_cand_brier - avg_inc_brier:+.4f} (limit {criteria.max_brier_degradation:.4f})."
        )

    passed = len(failures) == 0
    reason = (
        f"Passed walk-forward: +{net_delta:.2f} net improvement across {cand_windows_won}/{len(window_reports)} windows ({cand_total_trades} trades)."
        if passed
        else " | ".join(failures)
    )

    return {
        "passed": passed,
        "reason": reason,
        "summary": {
            "observations_total": n_total,
            "windows_evaluated": len(window_reports),
            "windows_beating_incumbent": cand_windows_won,
            "candidate": agg_cand,
            "incumbent": agg_inc,
            "net_return_delta": net_delta,
            "avg_candidate_brier": round(avg_cand_brier, 6),
            "avg_incumbent_brier": round(avg_inc_brier, 6),
            "windows": window_reports,
        },
        "breakdown": {
            "candidate_by_asset_expiry": agg_cand["breakdown"],
            "incumbent_by_asset_expiry": agg_inc["breakdown"],
        },
    }
