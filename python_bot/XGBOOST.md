# SignalDesk XGBoost paper engine

This optional worker reads the existing local SignalDesk server. Keep your current SignalDesk instance open and connected to Quotex. The worker uses its authenticated candle feed without managing login or orders. Existing scans, Stubay Ai, Telegram, and account settings remain in the main app.

## Setup

From the project root in PowerShell:

```powershell
python -m venv .venv-xgb
.\.venv-xgb\Scripts\python.exe -m pip install -r python_bot\requirements-xgb.txt
$xgb = '.\.venv-xgb\Scripts\python.exe'
& $xgb python_bot\verify_xgb.py -v
```

## Collect and import

```powershell
& $xgb python_bot\xgb_signal_bot.py collect --assets EURUSD EURUSD-OTC --once
& $xgb python_bot\xgb_signal_bot.py collect --assets EURUSD EURUSD-OTC
& $xgb python_bot\xgb_signal_bot.py status
```

Remove `--once` to keep collecting. Stop with Ctrl+C. Use `--base-url http://127.0.0.1:PORT` if your connected instance is on another port. Collection reads the latest batch of 15-second Quotex candles from the local API. Continuous collection or CSV import is needed to accumulate enough history. Symbols ending in `-OTC` are stored separately.

To import existing 15-second OHLC data:

```powershell
& $xgb python_bot\xgb_signal_bot.py import C:\path\to\candles.csv
```

CSV header:

```csv
asset,close_ts,open,high,low,close,payout
```

Use UTC Unix **close** timestamps aligned to 15 seconds and payout fractions such as `0.82` for 82%. Leave payout blank if it wasn’t recorded at the time. The live collector records the displayed payout only for a fresh, completed candle. It does not copy today's payout to older candles.

## Validate and train

```powershell
& $xgb python_bot\xgb_signal_bot.py backtest --market real
& $xgb python_bot\xgb_signal_bot.py backtest --market otc
& $xgb python_bot\xgb_signal_bot.py train --market real
& $xgb python_bot\xgb_signal_bot.py train --market otc
```

Use `--assume-payout 0.80` to run a scenario for missing payout rows. Otherwise those rows contribute to direction and calibration metrics only. The backtest uses one simulated position at a time per market and a fixed stake of one unit. A win earns the payout, a loss costs one unit, and a tie refunds the stake. Results are out-of-sample estimates based on candle closes and can differ from broker order timing or settlement.

The features use a trailing 32-candle window with returns, volatility, price ranges, moving averages, wick/body ratios, and time of day. The label is the direction from the current close to the close 120 seconds later. Missing intervals invalidate examples. Ties stay in backtesting and are excluded from binary model fitting and calibration.

Each expanding walk-forward fold uses three chronological slices for model fitting, sigmoid probability calibration, and testing. Labels must settle before the next slice begins, and all assets with the same timestamp stay on the same side of a split. The first fold needs at least 120 decisive fit samples, 40 decisive calibration samples, and 20 test samples. The fit and calibration slices must each include both directions. The report compares raw and calibrated Brier scores with even odds and reports payout-aware returns separately. Training saves a separate artifact for each market after validation completes. Review the report before relying on a trained model in demo mode.

## Run the paper demo

```powershell
& $xgb python_bot\xgb_signal_bot.py demo --assets EURUSD EURUSD-OTC
& $xgb python_bot\xgb_signal_bot.py status
```

The demo logs `UP`, `DOWN`, or `SKIP` with a calibrated probability and reason. It skips stale data, missing models or payouts, and setups whose estimated probability falls below the break-even threshold plus the configured margin. Entries and settlements are simulated at candle closes 120 seconds apart. Missing settlement quotes are marked unresolved. There is no order submission or automatic Telegram dispatch in this worker.

Collected data, simulated outcomes, and logs are kept in `data/xgb/`. The two models are in `data/xgb/models/`. Use the global `--data PATH` option before a command to select another storage directory.
