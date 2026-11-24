"""
Historical Candle Backtester for Quotex Multi-Strategy Signal Bot.
Evaluates win rate, direct wins, Martingale recovery wins, drawdown,
profit factor, equity curve, and per-strategy accuracy.
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional
import strategies as strat
import risk_manager as rm
import market_feed as mf


import quotex_client as qc


def run_backtest(
    symbol: str = "EURUSD-OTC",
    timeframe: str = "1m",
    bars: int = 360,
    expiry_candles: int = 2,
    payout_pct: float = 85.0,
    base_stake: float = 10.0,
    martingale_steps: int = 1,
    martingale_mult: float = 2.2,
    starting_balance: float = 1000.0,
    strategy_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not qc.is_pyquotex_connected():
        return {
            "error": (
                "Quotex connection required. Sign in under Quotex Login to evaluate "
                "real Quotex historical candles and signal accuracy."
            )
        }

    feed = mf.get_candles(symbol=symbol, timeframe=timeframe, limit=bars, priority=True)
    candles = feed["candles"]
    payout_val = float(payout_pct or feed["payout"])

    if feed.get("feed_source") != "Quotex Live (pyquotex)":
        return {
            "error": feed.get("error")
            or f"Live Quotex candles are currently unavailable for {feed['name']} ({timeframe})."
        }

    warmup = 30
    min_needed = warmup + expiry_candles + martingale_steps * expiry_candles + 5
    if len(candles) < min_needed:
        err = feed.get("error") or (
            f"Only {len(candles)} live Quotex candles returned for {feed['name']} ({timeframe}); "
            f"need at least {min_needed} candles to evaluate accuracy."
        )
        return {"error": err}

    balance = float(starting_balance)
    peak_balance = balance
    max_drawdown = 0.0

    trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = [
        {"bar": warmup, "time": candles[warmup]["time"], "equity": round(balance, 2)}
    ]

    strat_stats: Dict[str, Dict[str, int]] = {
        s["id"]: {"wins": 0, "losses": 0, "signals": 0} for s in strat.STRATEGY_CATALOG
    }

    i = warmup
    total_direct_wins = 0
    total_gale_wins = 0
    total_losses = 0
    gross_profit = 0.0
    gross_loss = 0.0

    while i < len(candles) - expiry_candles - martingale_steps * expiry_candles - 1:
        window = candles[: i + 1]
        res = strat.evaluate_all_strategies(window, strategy_config, timeframe=timeframe)
        sig = res["signal"]

        # Track individual strategy vote accuracy on every bar with non-neutral vote
        future_close = candles[i + expiry_candles]["close"]
        entry_price = candles[i]["close"]
        for v in res["votes"]:
            vsig = v["signal"]
            if vsig in ("CALL", "PUT"):
                sid = v["id"]
                strat_stats[sid]["signals"] += 1
                won = (future_close > entry_price) if vsig == "CALL" else (future_close < entry_price)
                if won:
                    strat_stats[sid]["wins"] += 1
                else:
                    strat_stats[sid]["losses"] += 1

        if sig in ("CALL", "PUT"):
            step = 0
            trade_won = False
            net_pnl = 0.0
            exit_bar = i + expiry_candles
            stake = base_stake

            while step <= martingale_steps:
                cur_entry = candles[i + step * expiry_candles]["close"]
                cur_exit_idx = i + (step + 1) * expiry_candles
                if cur_exit_idx >= len(candles):
                    break
                cur_exit = candles[cur_exit_idx]["close"]
                exit_bar = cur_exit_idx

                won = (cur_exit > cur_entry) if sig == "CALL" else (cur_exit < cur_entry)
                if won:
                    pnl = stake * (payout_val / 100.0)
                    net_pnl += pnl
                    trade_won = True
                    break
                else:
                    net_pnl -= stake
                    step += 1
                    stake = stake * martingale_mult

            balance += net_pnl
            if net_pnl >= 0:
                gross_profit += net_pnl
            else:
                gross_loss += abs(net_pnl)

            if trade_won:
                if step == 0:
                    total_direct_wins += 1
                else:
                    total_gale_wins += 1
            else:
                total_losses += 1

            peak_balance = max(peak_balance, balance)
            dd = ((peak_balance - balance) / peak_balance) * 100.0 if peak_balance > 0 else 0.0
            max_drawdown = max(max_drawdown, dd)

            trades.append({
                "bar": i,
                "time": candles[i]["time"],
                "signal": sig,
                "confidence": res["confidence"],
                "entry": entry_price,
                "exit": candles[exit_bar]["close"],
                "step_won": step if trade_won else -1,
                "result": "WIN" if trade_won else "LOSS",
                "pnl": round(net_pnl, 2),
                "balance": round(balance, 2),
            })
            equity_curve.append({
                "bar": exit_bar,
                "time": candles[exit_bar]["time"],
                "equity": round(balance, 2),
            })
            i = exit_bar + 1
        else:
            i += 1

    total_trades = len(trades)
    total_wins = total_direct_wins + total_gale_wins
    win_rate = round((total_wins / total_trades) * 100.0, 1) if total_trades > 0 else 0.0
    direct_wr = round((total_direct_wins / total_trades) * 100.0, 1) if total_trades > 0 else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (9.99 if gross_profit > 0 else 0.0)

    strategy_breakdown = []
    for s in strat.STRATEGY_CATALOG:
        st = strat_stats[s["id"]]
        tot = st["wins"] + st["losses"]
        strategy_breakdown.append({
            "id": s["id"],
            "name": s["name"],
            "category": s["category"],
            "signals": st["signals"],
            "wins": st["wins"],
            "losses": st["losses"],
            "win_rate": round((st["wins"] / tot) * 100.0, 1) if tot > 0 else 0.0,
        })

    return {
        "symbol": symbol,
        "name": feed["name"],
        "timeframe": timeframe,
        "bars_tested": bars,
        "expiry_candles": expiry_candles,
        "payout_pct": payout_val,
        "breakeven_win_rate": rm.break_even_win_rate(payout_val),
        "summary": {
            "starting_balance": round(starting_balance, 2),
            "ending_balance": round(balance, 2),
            "net_profit": round(balance - starting_balance, 2),
            "roi_pct": round(((balance - starting_balance) / starting_balance) * 100.0, 1) if starting_balance > 0 else 0.0,
            "total_trades": total_trades,
            "total_wins": total_wins,
            "direct_wins": total_direct_wins,
            "gale_wins": total_gale_wins,
            "losses": total_losses,
            "win_rate": win_rate,
            "direct_win_rate": direct_wr,
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_drawdown, 2),
        },
        "equity_curve": equity_curve,
        "strategy_breakdown": strategy_breakdown,
        "recent_trades": list(reversed(trades[-40:])),
    }
