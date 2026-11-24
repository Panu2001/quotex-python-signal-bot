"""Collect, validate, train, and run a paper-only XGBoost signal engine."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from pathlib import Path

import xgb_engine as engine


DEFAULT_ASSETS = ["EURUSD", "EURUSD-OTC"]
LOGGER = logging.getLogger("signaldesk.xgboost")


def asset_name(value: str) -> str:
    return value.strip().upper().replace("_OTC", "-OTC")


def fraction(value: str) -> float:
    number = float(value)
    if not 0 < number <= 1:
        raise argparse.ArgumentTypeError("Use a decimal fraction above 0 and at most 1.")
    return number


def local_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment
    ):
        raise argparse.ArgumentTypeError("Use a loopback HTTP base URL, such as http://127.0.0.1:3000.")
    return value.rstrip("/")


def fetch_candles(base: str, asset: str) -> list[engine.Candle]:
    request = urllib.request.Request(
        base + "/api/analyze",
        data=json.dumps({"symbol": asset, "timeframe": "15s", "config": {}}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "SignalDesk-XGBoost/1.0"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        data = json.load(response)
    feed = data.get("feed") or {}
    if (
        feed.get("feed_source") != "Quotex Live (pyquotex)" or not feed.get("is_live")
        or feed.get("symbol") != asset or feed.get("timeframe") != "15s"
    ):
        raise ValueError("No authenticated 15-second Quotex candles for this asset.")
    now = min(int(time.time()), int(feed["server_time"]))
    unique = {int(candle["time"]): candle for candle in feed.get("candles", [])}
    closed = [
        start for start in sorted(unique)
        if start + engine.BAR_SECONDS <= now
        and (start + engine.BAR_SECONDS in unique or now - start >= 2 * engine.BAR_SECONDS)
    ]
    payout = float(feed.get("payout", 0)) / 100
    return [
        engine.Candle(
            start + engine.BAR_SECONDS, *(float(unique[start][key]) for key in ("open", "high", "low", "close")),
            payout if start == closed[-1] and now - start - engine.BAR_SECONDS <= 5 and 0 < payout <= 1 else None,
        )
        for start in closed
    ]


def import_csv(conn: sqlite3.Connection, path: Path) -> dict:
    groups: dict[str, list[engine.Candle]] = {}
    now = int(time.time())
    with path.open(newline="", encoding="utf-8-sig") as source:
        for line, row in enumerate(csv.DictReader(source), 2):
            try:
                asset = asset_name(row["asset"])
                payout = row.get("payout", "").strip()
                candle = engine.Candle(
                    int(row["close_ts"]), *(float(row[key]) for key in ("open", "high", "low", "close")),
                    float(payout) if payout else None,
                )
                if not asset or candle.close_ts > now:
                    raise ValueError("Missing asset or future close timestamp.")
                engine.validate_candle(candle)
            except (AttributeError, KeyError, ValueError, TypeError) as exc:
                raise ValueError(f"CSV row {line}: {exc}") from exc
            groups.setdefault(asset, []).append(candle)
    if not groups:
        raise ValueError("The CSV contains no candles.")
    return {asset: engine.write_candles(conn, asset, candles) for asset, candles in groups.items()}


def log_demo(conn: sqlite3.Connection, models: Path, asset: str, candles: list[engine.Candle], margin: float) -> None:
    if not candles:
        return
    latest = candles[-1]
    for result in engine.settle_demo(conn, asset, latest.close_ts):
        LOGGER.info("settlement %s", json.dumps(result))
    if int(time.time()) - latest.close_ts > 5:
        return
    if conn.execute("SELECT 1 FROM demo WHERE asset = ? AND close_ts = ?", (asset, latest.close_ts)).fetchone():
        return
    market = engine.symbol_market(asset)
    path = models / (market + ".ubj")
    vector = engine.features(candles[-engine.WINDOW:])
    signal, reason, probability, model_id = "SKIP", "Insufficient contiguous candles.", None, None
    payout = latest.payout
    if vector is not None:
        if not path.is_file():
            reason = f"No trained {market} model."
        else:
            model, meta = engine.load_model(path, market)
            model_id = f"{market}:{meta['cutoff']}"
            if latest.close_ts <= meta["known_through"] or latest.close_ts < meta["cutoff"]:
                reason = "This candle predates the model's training cutoff."
            else:
                probability = engine.predict(model, meta, vector)
                signal, reason = engine.decision(probability, payout, margin)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if signal != "SKIP" and conn.execute(
            "SELECT 1 FROM demo d JOIN candles c ON c.asset = d.asset AND c.close_ts = d.close_ts "
            "WHERE c.market = ? AND d.status = 'OPEN' LIMIT 1", (market,),
        ).fetchone():
            signal, reason = "SKIP", "A paper simulation is already open for this market."
        inserted = conn.execute(
            "INSERT OR IGNORE INTO demo VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (asset, latest.close_ts, latest.close_ts + engine.HORIZON_SECONDS, signal, probability, payout,
             latest.close, model_id, reason, "SKIPPED" if signal == "SKIP" else "OPEN"),
        ).rowcount
    if inserted:
        LOGGER.info("prediction %s", json.dumps({
            "asset": asset, "close_ts": latest.close_ts, "signal": signal, "probability_up": probability,
            "payout": payout, "reason": reason, "model": model_id,
        }))


def run_live(args, conn: sqlite3.Connection) -> int:
    while True:
        failures = 0
        for asset in dict.fromkeys(map(asset_name, args.assets)):
            try:
                candles = fetch_candles(args.base_url, asset)
                if not candles:
                    raise ValueError("No closed candles.")
                engine.write_candles(conn, asset, candles)
                LOGGER.info("%s: collected %s closed candles through %s", asset, len(candles), candles[-1].close_ts)
                if args.command == "demo":
                    log_demo(conn, args.data / "models", asset, candles, args.margin)
            except (OSError, KeyError, ValueError, TypeError) as exc:
                failures += 1
                LOGGER.warning("%s: %s", asset, exc)
        if args.once:
            return int(failures > 0)
        time.sleep(args.interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/xgb"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "demo"):
        sub = commands.add_parser(name)
        sub.add_argument("--assets", nargs="+", default=DEFAULT_ASSETS)
        sub.add_argument("--base-url", type=local_url, default="http://127.0.0.1:3000")
        sub.add_argument("--interval", type=float, default=3)
        sub.add_argument("--once", action="store_true")
        sub.add_argument("--margin", type=float, default=0.02)
    commands.add_parser("import").add_argument("csv", type=Path)
    for name in ("train", "backtest"):
        sub = commands.add_parser(name)
        sub.add_argument("--market", choices=("real", "otc"), required=True)
        sub.add_argument("--folds", type=int, default=3)
        sub.add_argument("--margin", type=float, default=0.02)
        sub.add_argument("--assume-payout", type=fraction)
    commands.add_parser("status")
    args = parser.parse_args()
    if not 0 <= getattr(args, "margin", 0) < 0.5:
        parser.error("--margin must be in [0, 0.5).")
    if not 2 <= getattr(args, "folds", 3) <= 10:
        parser.error("--folds must be between 2 and 10.")
    interval = getattr(args, "interval", 3)
    if not math.isfinite(interval) or interval < 1:
        parser.error("--interval must be at least one second.")

    args.data.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    handlers = [
        logging.StreamHandler(), logging.FileHandler(args.data / "engine.log", encoding="utf-8"),
    ]
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)
    try:
        with closing(engine.open_store(args.data / "history.sqlite3")) as conn:
            if args.command in ("collect", "demo"):
                return run_live(args, conn)
            if args.command == "import":
                output = import_csv(conn, args.csv)
            elif args.command == "status":
                output = {
                    "candles_by_market": dict(conn.execute("SELECT market, COUNT(*) FROM candles GROUP BY market")),
                    "demo_by_status": dict(conn.execute("SELECT status, COUNT(*) FROM demo GROUP BY status")),
                    "demo_net_units": conn.execute("SELECT COALESCE(SUM(pnl), 0) FROM demo").fetchone()[0],
                    "models": [str(path) for path in sorted((args.data / "models").glob("*.ubj"))],
                }
            else:
                rows = engine.examples(engine.read_candles(conn, args.market))
                report = engine.evaluate(rows, args.folds, args.margin, args.assume_payout)
                output = {"market": args.market, "examples": len(rows), **report}
                if args.command == "train":
                    path = args.data / "models" / (args.market + ".ubj")
                    engine.save_model(path, args.market, rows, int(time.time()), report)
                    output["model"] = str(path)
            print(json.dumps(output, indent=2))
        return 0
    except KeyboardInterrupt:
        return 0
    except (ImportError, OSError, ValueError, sqlite3.Error) as exc:
        LOGGER.error("%s", exc)
        return 1
    finally:
        for handler in handlers:
            LOGGER.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
