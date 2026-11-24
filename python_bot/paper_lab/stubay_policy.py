"""Rules for Stubay's two-minute paper strategy and execution."""
from datetime import datetime
from itertools import groupby
from math import floor

from indicators import adx, atr, ema

DAILY_TRADES = 10
DAILY_LOSS = 0.05
STAKE_FRACTION = 0.01
PROBABILITY_MARGIN = 0.05


def aggregate_closed(candles, seconds):
    result = []
    prior_end = None
    for end, group in groupby(candles, lambda bar: ((bar.close_ts - 1) // seconds + 1) * seconds):
        bars = list(group)
        if end > candles[-1].close_ts:
            break
        if len(bars) != seconds // 15 or any(
            bar.close_ts != end - (len(bars) - 1 - index) * 15
            for index, bar in enumerate(bars)
        ):
            result.clear()
            prior_end = None
            continue
        if prior_end is not None and end - prior_end != seconds:
            result.clear()
        prior_end = end
        result.append((bars[0].open, max(b.high for b in bars), min(b.low for b in bars), bars[-1].close))
    return result


def _is_volatile(bars):
    if len(bars) < 20:
        return False
    opens, highs, lows, closes = map(list, zip(*bars))
    volatility = atr(highs, lows, closes)
    typical = volatility[-6]
    bodies = sum(abs(o - c) for o, c in zip(opens[-5:], closes[-5:]))
    ranges = sum(h - l for h, l in zip(highs[-5:], lows[-5:]))
    return bool((typical and volatility[-1] > 1.8 * typical) or (ranges and bodies < 0.25 * ranges))


def assess_setup(candles):
    minute = aggregate_closed(candles[-240:], 60)
    higher = aggregate_closed(candles, 900)
    output = {"regime": "unknown", "setup_score": 0, "setup_direction": None,
              "signals_agreeing": [], "signals_conflicting": []}
    recent = [(bar.open, bar.high, bar.low, bar.close) for bar in candles[-32:]]
    if _is_volatile(recent) or _is_volatile(minute):
        output.update(regime="volatile", signals_conflicting=["ATR spike or wick-heavy candles"])
        return output
    if len(minute) < 50 or len(higher) < 5:
        return output
    opens, highs, lows, closes = map(list, zip(*minute))
    strength = adx(highs, lows, closes)[0][-1]
    if strength is None or strength <= 25:
        output["regime"] = "ranging" if strength is not None and strength < 20 else "unclear"
        return output
    average = ema(closes, 50)
    up = closes[-1] > average[-1]
    sign = 1 if up else -1
    direction = "CALL" if up else "PUT"
    output.update(regime="trending", setup_direction=direction)
    pullback_level = ema(closes, 21)[-2]
    higher_close = [bar[3] for bar in higher[-5:]]
    trend = (
        min(lows[-4:]) > min(lows[-8:-4]) and max(highs[-4:]) > max(highs[-8:-4])
        if up else max(highs[-4:]) < max(highs[-8:-4]) and min(lows[-4:]) < min(lows[-8:-4])
    )
    last_range = highs[-1] - lows[-1]
    last_body = abs(closes[-1] - opens[-1])
    body_quality = (last_body >= 0.25 * last_range) if last_range > 0 else False
    opposing_wick = (highs[-1] - max(opens[-1], closes[-1])) if up else (min(opens[-1], closes[-1]) - lows[-1])
    wick_quality = (opposing_wick <= 0.50 * last_range) if last_range > 0 else False

    evidence = (
        ("Market structure", 25, trend),
        ("EMA pullback", 25, min(lows[-4:-1]) <= pullback_level <= max(highs[-4:-1])),
        ("Momentum", 20, sign * (closes[-1] - closes[-4]) > 0),
        ("Continuation candle", 15, sign * (closes[-1] - opens[-1]) > 0 and
         (closes[-1] > highs[-2] if up else closes[-1] < lows[-2]) and body_quality and wick_quality),
        ("15m trend", 15, sign * (higher_close[-1] - higher_close[0]) > 0),
    )
    for name, weight, agrees in evidence:
        output["signals_agreeing" if agrees else "signals_conflicting"].append(name)
        if agrees:
            output["setup_score"] += weight
    # A pullback and its continuation must both exist to qualify this setup.
    if (output["setup_score"] < 70 or len(output["signals_agreeing"]) < 3
            or not all(item[2] for item in (evidence[0], evidence[1], evidence[3], evidence[4]))):
        output["setup_direction"] = None
    return output


def trend_policy_improves(scored_rows, threshold, payout, fee):
    eligible = [(row, up) for row, up in scored_rows if row.get("regime") != "volatile" and max(up, 1 - up) > threshold]
    if len(scored_rows) < 40 or len(eligible) < 10:
        return False
    baseline = filtered = 0.0
    retained = 0
    for row, up in eligible:
        direction = "CALL" if up > 0.5 else "PUT"
        outcome = (payout if (direction == "CALL") == (row["outcome"] == "UP") else -1) - fee
        baseline += outcome
        if row.get("setup_direction") == direction:
            filtered += outcome
            retained += 1
    return retained >= 10 and filtered > max(0.0, baseline) + 0.02 * len(scored_rows)


def paper_risk(conn, recipe, now, starting_balance, requested_stake, fee=0.0, include_pending=True):
    day = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    resumed_row = conn.execute("SELECT value FROM system_state WHERE key = 'stubay_resumed_ts'").fetchone()
    resumed_ts = float(resumed_row[0]) if resumed_row and str(resumed_row[0]).replace(".", "", 1).isdigit() else 0.0
    cutoff = max(day, resumed_ts)
    net = conn.execute(
        "SELECT COALESCE(SUM(net_result), 0) FROM paper_trades WHERE rule_version_id = ?", (recipe,),
    ).fetchone()[0]
    today = conn.execute(
        "SELECT COUNT(CASE WHEN decision_ts >= ? THEN 1 END), COALESCE(SUM(CASE WHEN settled_ts >= ? THEN net_result ELSE 0 END), 0) "
        "FROM paper_trades WHERE rule_version_id = ? AND (decision_ts >= ? OR settled_ts >= ?)",
        (day, day, recipe, day, day),
    ).fetchone()
    streak = conn.execute(
        "SELECT status FROM paper_trades WHERE rule_version_id = ? AND settled_ts >= ? "
        "AND status IN ('WIN','LOSS') ORDER BY settled_ts DESC, trade_id DESC LIMIT 3", (recipe, cutoff),
    ).fetchall()
    pending, reserved = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(COALESCE(json_extract(plan_json, '$.paper_stake'), ?) * (1 + ?)), 0) "
        "FROM stubay_memory WHERE status = 'SCHEDULED' AND entry_ts >= ?",
        (requested_stake, fee, day),
    ).fetchone() if include_pending else (0, 0.0)
    exposure = conn.execute(
        "SELECT COALESCE(SUM(stake * (1 + fee_rate)), 0) FROM paper_trades WHERE rule_version_id = ? AND status = 'OPEN'",
        (recipe,),
    ).fetchone()[0]
    balance = starting_balance + net
    day_start_balance = balance - today[1]
    stake = min(requested_stake, floor(balance * STAKE_FRACTION * 100) / 100)
    if today[0] + pending >= DAILY_TRADES:
        return 0, "Daily paper-trade limit reached."
    if len(streak) == 3 and all(row[0] == "LOSS" for row in streak):
        return 0, "Paused after three consecutive paper losses today."
    if stake <= 0 or day_start_balance <= 0 or max(-today[1], 0) + exposure + reserved + stake * (1 + fee) > day_start_balance * DAILY_LOSS:
        return 0, "Daily paper-loss or exposure limit reached."
    return stake, None
