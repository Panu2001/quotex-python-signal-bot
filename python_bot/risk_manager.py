"""
Risk Management & Stake Sizing Engine for Quotex Binary Options.
Supports Fixed Stake, Martingale Recovery, Anti-Martingale, and Compounding.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class RiskConfig:
    base_stake: float = 10.0
    payout_pct: float = 85.0
    mode: str = "martingale"  # "fixed" | "martingale" | "anti_martingale" | "compounding"
    martingale_multiplier: float = 2.2
    max_martingale_steps: int = 2
    daily_stop_loss: float = 100.0
    daily_take_profit: float = 150.0


def break_even_win_rate(payout_pct: float) -> float:
    if payout_pct <= 0:
        return 100.0
    return round((100.0 / (100.0 + payout_pct)) * 100.0, 2)


def expected_value_per_dollar(win_rate_pct: float, payout_pct: float) -> float:
    p = win_rate_pct / 100.0
    q = 1.0 - p
    return round(p * (payout_pct / 100.0) - q, 4)


def calculate_next_stake(
    cfg: RiskConfig,
    consecutive_losses: int = 0,
    consecutive_wins: int = 0,
    daily_pnl: float = 0.0,
) -> Dict[str, Any]:
    if daily_pnl <= -abs(cfg.daily_stop_loss):
        return {"stake": 0.0, "halted": True, "reason": "Daily stop-loss limit reached"}
    if daily_pnl >= abs(cfg.daily_take_profit):
        return {"stake": 0.0, "halted": True, "reason": "Daily take-profit target reached"}

    stake = cfg.base_stake
    step = 0

    if cfg.mode == "martingale" and consecutive_losses > 0:
        step = min(consecutive_losses, cfg.max_martingale_steps)
        if consecutive_losses > cfg.max_martingale_steps:
            # Reset after max martingale cap to prevent account blowup
            step = 0
            stake = cfg.base_stake
        else:
            stake = cfg.base_stake * (cfg.martingale_multiplier ** step)

    elif cfg.mode == "anti_martingale" and consecutive_wins > 0:
        step = min(consecutive_wins, 3)
        stake = cfg.base_stake * (1.5 ** step)

    elif cfg.mode == "compounding" and consecutive_wins > 0:
        step = min(consecutive_wins, 3)
        stake = cfg.base_stake * ((1.0 + cfg.payout_pct / 100.0) ** step)

    return {
        "stake": round(stake, 2),
        "step": step,
        "halted": False,
        "reason": "Nominal",
        "breakeven_wr": break_even_win_rate(cfg.payout_pct),
    }
