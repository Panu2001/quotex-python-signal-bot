"""Offline regression checks with generated candle data and a mocked live feed."""

import io
import json
import math
import random
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import xgb_engine as engine
import xgb_signal_bot as cli

try:
    import sklearn
    import xgboost
    HAS_XGB = True
except ImportError:
    HAS_XGB = False


START = 1_750_000_005


def bars(count, seed=10):
    rng = random.Random(seed)
    price = 100.0
    output = []
    for index in range(count):
        close = price + rng.uniform(-0.16, 0.16) + 0.06 * math.sin(index / 8)
        output.append(engine.Candle(
            START + index * 15, price, max(price, close) + 0.03, min(price, close) - 0.03, close, 0.8
        ))
        price = close
    return output


class XgbChecks(unittest.TestCase):
    def test_fold_boundaries_and_gap(self):
        candles = bars(760)
        rows = engine.examples({"EURUSD": candles, "GBPUSD": bars(760, 11)})
        for fit, calibration, test in engine.walk_forward(rows, 3):
            self.assertLess(max(row.end for row in fit), min(row.timestamp for row in calibration))
            self.assertLess(max(row.end for row in calibration), min(row.timestamp for row in test))
            self.assertEqual(len(test) % 2, 0)
        sample = engine.examples({"EURUSD": candles[:60]})[0]
        self.assertEqual(sample.features, engine.features(candles[:engine.WINDOW]))
        self.assertIsNone(engine.features(candles[:10] + candles[11:33]))
        self.assertEqual(sample.end - sample.timestamp, 120)

    @unittest.skipUnless(HAS_XGB, "xgboost and scikit-learn are optional dependencies")
    def test_validated_models_round_trip(self):
        with TemporaryDirectory() as folder:
            for market, asset in (("real", "EURUSD"), ("otc", "EURUSD-OTC")):
                rows = engine.examples({asset: bars(760)})
                report = engine.evaluate(rows, 3, 0.02, None)
                self.assertEqual(len(report["folds"]), 3)
                self.assertEqual(report["backtest"]["missing_payout_rows"], 0)
                path = Path(folder) / f"{market}.ubj"
                engine.save_model(path, market, rows, rows[-1].end + 15, report)
                model, meta = engine.load_model(path, market)
                self.assertTrue(0 < engine.predict(model, meta, rows[-1].features) < 1)
                with self.assertRaises(ValueError):
                    engine.load_model(path, "otc" if market == "real" else "real")

    def test_collect_only_closed_candles_and_current_payout(self):
        closes = bars(3)
        now = closes[1].close_ts + 2
        data = {"feed": {
            "symbol": "EURUSD", "timeframe": "15s", "is_live": True,
            "feed_source": "Quotex Live (pyquotex)", "server_time": now, "payout": 82,
            "candles": [dict(time=c.close_ts - 15, open=c.open, high=c.high, low=c.low, close=c.close)
                        for c in closes],
        }}
        with patch.object(cli.time, "time", return_value=now), patch.object(
            cli.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(data).encode())
        ):
            collected = cli.fetch_candles("http://localhost:3000", "EURUSD")
        self.assertEqual([c.close_ts for c in collected], [c.close_ts for c in closes[:2]])
        self.assertEqual([c.payout for c in collected], [None, 0.82])

    def test_payout_and_demo_ledger(self):
        self.assertEqual(engine.decision(0.62, 0.50, 0.02)[0], "SKIP")
        self.assertEqual(engine.decision(0.62, 0.90, 0.02)[0], "UP")
        self.assertEqual(engine.decision(0.35, 0.85, 0.02)[0], "DOWN")
        self.assertEqual(engine.decision(0.9, None, 0.02)[0], "SKIP")
        candles = bars(48)
        with TemporaryDirectory() as folder, closing(engine.open_store(Path(folder) / "history.sqlite3")) as conn:
            asset = "EURUSD"
            engine.write_candles(conn, asset, candles)
            entry, expiry = candles[31], candles[39]
            with conn:
                conn.execute("INSERT INTO demo VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                             (asset, entry.close_ts, expiry.close_ts, "UP", 0.75, 0.8, entry.close,
                              "test", "test", "OPEN"))
            self.assertEqual(engine.settle_demo(conn, asset, expiry.close_ts - 1), [])
            settled = engine.settle_demo(conn, asset, expiry.close_ts)[0]
            self.assertEqual(settled["pnl"], 0.8 if expiry.close > entry.close else -1)
            self.assertEqual(engine.settle_demo(conn, asset, expiry.close_ts), [])

    def test_demo_logs_signal_once_and_respects_market_exposure(self):
        candles = bars(40)
        timestamp = candles[-1].close_ts
        with TemporaryDirectory() as folder, closing(engine.open_store(Path(folder) / "history.sqlite3")) as conn:
            models = Path(folder)
            (models / "real.ubj").touch()
            with patch.object(cli.time, "time", return_value=timestamp + 2), patch.object(
                engine, "load_model", return_value=(None, {"cutoff": timestamp - 15, "known_through": timestamp - 30})
            ), patch.object(engine, "predict", return_value=0.8):
                for asset in ("EURUSD", "EURUSD", "GBPUSD"):
                    engine.write_candles(conn, asset, candles)
                    cli.log_demo(conn, models, asset, candles, 0.02)
            self.assertEqual(conn.execute("SELECT asset, signal FROM demo ORDER BY asset").fetchall(),
                             [("EURUSD", "UP"), ("GBPUSD", "SKIP")])


if __name__ == "__main__":
    unittest.main()
