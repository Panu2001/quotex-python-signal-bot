import { Disclosure } from './Disclosure';
import React, { useMemo, useState } from 'react';
import { LogIn, Play } from 'lucide-react';
import { TimeframeSelector } from './TimeframeSelector';
import type { AssetMeta, BacktestResponse, BotConfig } from '../types/trading';

interface BacktesterViewProps {
  assets: Record<string, AssetMeta>;
  config: BotConfig;
  quotexConnected: boolean;
  onOpenQuotexLogin: () => void;
  defaultSymbol?: string;
  defaultTimeframe?: string;
}

export const BacktesterView: React.FC<BacktesterViewProps> = ({
  assets,
  config,
  quotexConnected,
  onOpenQuotexLogin,
  defaultSymbol = 'EURUSD-OTC',
  defaultTimeframe = '1m',
}) => {
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [timeframe, setTimeframe] = useState(defaultTimeframe);
  const [bars, setBars] = useState(360);
  const [expiryCandles, setExpiryCandles] = useState(2);
  const [payoutPct, setPayoutPct] = useState(85);
  const [martingaleSteps, setMartingaleSteps] = useState(config.max_martingale_steps);
  const [startingBalance, setStartingBalance] = useState(1000);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runBacktest = async () => {
    if (!quotexConnected) {
      setError('Connect your Quotex account under Quotex Login to evaluate accuracy on real Quotex historical candles.');
      return;
    }
    setIsRunning(true);
    setError(null);
    try {
      const resp = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          timeframe,
          bars,
          expiry_candles: expiryCandles,
          payout_pct: payoutPct,
          base_stake: config.base_stake,
          martingale_steps: martingaleSteps,
          martingale_mult: config.martingale_multiplier,
          starting_balance: startingBalance,
          config,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error) {
        throw new Error(data.error || 'Backtest failed');
      }
      setResult(data as BacktestResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Backtest failed');
    } finally {
      setIsRunning(false);
    }
  };

  const equityChart = useMemo(() => {
    if (!result || result.equity_curve.length < 2) return null;
    const pts = result.equity_curve;
    const width = 920;
    const height = 215;
    const padL = 58;
    const padR = 20;
    const padT = 18;
    const padB = 24;
    const plotW = width - padL - padR;
    const plotH = height - padT - padB;

    const vals = pts.map((p) => p.equity);
    const minE = Math.min(...vals, result.summary.starting_balance) * 0.99;
    const maxE = Math.max(...vals, result.summary.starting_balance) * 1.01;
    const span = Math.max(maxE - minE, 1);

    const toX = (idx: number) => padL + (idx / (pts.length - 1)) * plotW;
    const toY = (v: number) => padT + plotH * (1 - (v - minE) / span);

    const pathD = pts
      .map((p, i) => `${i === 0 ? 'M' : 'L'} ${toX(i).toFixed(1)} ${toY(p.equity).toFixed(1)}`)
      .join(' ');
    const areaD = `${pathD} L ${toX(pts.length - 1).toFixed(1)} ${(padT + plotH).toFixed(1)} L ${toX(0).toFixed(1)} ${(padT + plotH).toFixed(1)} Z`;

    const baseLineY = toY(result.summary.starting_balance);
    const isProfit = result.summary.net_profit >= 0;

    return { width, height, padL, padR, pathD, areaD, baseLineY, isProfit };
  }, [result]);

  return (
    <div className="p-6 max-w-[1380px] mx-auto space-y-6">
      {!quotexConnected && (
        <div className="border border-amber-500/35 rounded-lg bg-amber-500/8 px-4 py-3.5 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold text-amber-300">
              Quotex Login Required — Real Quotex Accuracy Evaluation Only
            </div>
            <p className="text-xs text-slate-300 mt-0.5">
              Performance backtests evaluate your active strategies strictly on live Quotex historical candles (no synthetic or third-party data).
            </p>
          </div>
          <button
            type="button"
            onClick={onOpenQuotexLogin}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors cursor-pointer shrink-0"
          >
            <LogIn className="w-3.5 h-3.5" />
            <span>Open Quotex Login</span>
          </button>
        </div>
      )}

      <div>
        <h2 className="text-base font-display font-semibold text-slate-100">
          Test your current strategy settings
        </h2>
        <p className="text-xs text-slate-400 mt-0.5">
          Choose a market and timeframe to test against Quotex price history.
        </p>
      </div>

      {/* Parameter Controls */}
      <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-5">
        <div className="flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="text-xs text-slate-400">Asset Pair</span>
            <select
              value={symbol}
              onChange={(e) => {
                const sym = e.target.value;
                setSymbol(sym);
                if (assets[sym]) setPayoutPct(assets[sym].payout);
              }}
              className="mt-1 block bg-[#0B101A] border border-[#232F46] rounded-md px-3 py-2 text-xs text-slate-100"
            >
              {Object.entries(assets).map(([sym, meta]) => (
                <option key={sym} value={sym}>
                  {meta.name}
                </option>
              ))}
            </select>
          </label>

          <div className="block">
            <span className="text-xs text-slate-400">Timeframe</span>
            <div className="mt-1">
              <TimeframeSelector
                value={timeframe}
                onChange={setTimeframe}
                size="md"
                align="left"
                ariaLabel="Backtest timeframe"
              />
            </div>
          </div>

          <Disclosure label="Backtest settings" className="basis-full">
            <div className="flex flex-wrap items-end gap-4">
              <label className="block">
                <span className="text-xs text-slate-400">History</span>
                <select
                  value={bars}
                  onChange={(e) => setBars(Number(e.target.value))}
                  className="mt-1 block bg-[#0B101A] border border-[#232F46] rounded-md px-3 py-2 text-xs font-mono tabular-nums text-slate-100"
                >
                  <option value={200}>200 bars</option>
                  <option value={360}>360 bars</option>
                  <option value={500}>500 bars</option>
                </select>
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">Expiry</span>
                <select
                  value={expiryCandles}
                  onChange={(e) => setExpiryCandles(Number(e.target.value))}
                  className="mt-1 block bg-[#0B101A] border border-[#232F46] rounded-md px-3 py-2 text-xs font-mono tabular-nums text-slate-100"
                >
                  <option value={1}>1 candle</option>
                  <option value={2}>2 candles</option>
                  <option value={3}>3 candles</option>
                  <option value={5}>5 candles</option>
                </select>
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">Martingale</span>
                <select
                  value={martingaleSteps}
                  onChange={(e) => setMartingaleSteps(Number(e.target.value))}
                  className="mt-1 block bg-[#0B101A] border border-[#232F46] rounded-md px-3 py-2 text-xs font-mono tabular-nums text-slate-100"
                >
                  <option value={0}>No Gale</option>
                  <option value={1}>1 Step (G1)</option>
                  <option value={2}>2 Steps (G2)</option>
                </select>
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">Starting Balance ($)</span>
                <input
                  type="number"
                  min={100}
                  max={100000}
                  value={startingBalance}
                  onChange={(e) => setStartingBalance(Number(e.target.value))}
                  className="mt-1 w-28 block bg-[#0B101A] border border-[#232F46] rounded-md px-3 py-2 text-xs font-mono tabular-nums text-slate-100"
                />
              </label>
            </div>
          </Disclosure>

          <button
            type="button"
            onClick={runBacktest}
            disabled={isRunning || !quotexConnected}
            className="inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-lg bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors whitespace-nowrap shrink-0 cursor-pointer disabled:opacity-50"
          >
            <Play className="w-3.5 h-3.5" />
            <span>{isRunning ? 'Running…' : 'Run backtest'}</span>
          </button>
        </div>

        {error && <p className="mt-3 text-xs text-rose-400">{error}</p>}
      </div>

      {!result ? (
        <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-14 text-center">
          <h3 className="text-sm font-display font-medium text-slate-200">
            {quotexConnected
              ? 'Your Quotex performance analytics start here.'
              : 'Connect Quotex Login to evaluate real Quotex signal accuracy.'}
          </h3>
          <p className="mt-1.5 text-xs text-slate-500 max-w-md mx-auto">
            {quotexConnected
              ? 'Run a candle-by-candle evaluation on live Quotex history to inspect win rate, equity curve, drawdown, and per-strategy accuracy.'
              : 'Synthetic and third-party feeds are disabled so your backtest metrics reflect true Quotex market behavior.'}
          </p>
          {quotexConnected ? (
            <button
              type="button"
              onClick={runBacktest}
              className="mt-5 inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-lg bg-[#161E2E] border border-[#27344E] text-slate-200 hover:bg-[#1E293D] transition-colors cursor-pointer"
            >
              <Play className="w-3.5 h-3.5" />
              <span>Run Quotex Backtest</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={onOpenQuotexLogin}
              className="mt-5 inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-lg bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors cursor-pointer"
            >
              <LogIn className="w-3.5 h-3.5" />
              <span>Open Quotex Login</span>
            </button>
          )}
        </div>
      ) : (
        <>
          {/* 4-Column Summary Row */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-4">
              <div className="text-xs text-slate-400">Net Profit & Equity</div>
              <div
                className={`mt-1 text-2xl font-mono tabular-nums font-bold ${
                  result.summary.net_profit >= 0 ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                {result.summary.net_profit >= 0 ? '+' : ''}${result.summary.net_profit.toFixed(2)}
              </div>
              <div className="mt-1 text-xs font-mono tabular-nums text-slate-400">
                ${result.summary.ending_balance.toFixed(2)} ({result.summary.roi_pct >= 0 ? '+' : ''}
                {result.summary.roi_pct}%)
              </div>
            </div>

            <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-4">
              <div className="text-xs text-slate-400">Total Win Rate</div>
              <div className="mt-1 text-2xl font-mono tabular-nums font-bold text-slate-100">
                {result.summary.win_rate.toFixed(1)}%
              </div>
              <div className="mt-1 text-xs font-mono tabular-nums text-slate-400">
                Break-even {result.breakeven_win_rate}% at {result.payout_pct}%
              </div>
            </div>

            <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-4">
              <div className="text-xs text-slate-400">Direct Accuracy</div>
              <div className="mt-1 text-2xl font-mono tabular-nums font-bold text-slate-100">
                {result.summary.direct_win_rate.toFixed(1)}%
              </div>
              <div className="mt-1 text-xs font-mono tabular-nums text-slate-400">
                {result.summary.direct_wins} direct · {result.summary.gale_wins} gale · {result.summary.losses} loss
              </div>
            </div>

            <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-4">
              <div className="text-xs text-slate-400">Profit Factor & Drawdown</div>
              <div className="mt-1 text-2xl font-mono tabular-nums font-bold text-slate-100">
                {result.summary.profit_factor.toFixed(2)}
              </div>
              <div className="mt-1 text-xs font-mono tabular-nums text-slate-400">
                Max DD {result.summary.max_drawdown_pct.toFixed(2)}% · {result.summary.total_trades} trades
              </div>
            </div>
          </div>

          {/* Equity Curve */}
          {equityChart && (
            <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] overflow-hidden">
              <div className="px-4 py-3 border-b border-[#1A2334] bg-[#121928] flex items-center justify-between">
                <h3 className="text-xs font-display font-semibold text-slate-100">
                  Simulated Equity Curve · {result.name} ({result.timeframe})
                </h3>
                <span className="text-xs font-mono tabular-nums text-slate-400">
                  {result.bars_tested} bars evaluated
                </span>
              </div>
              <div className="p-4 bg-[#0B101A]">
                <svg viewBox={`0 0 ${equityChart.width} ${equityChart.height}`} className="w-full h-auto block">
                  <line
                    x1={equityChart.padL}
                    y1={equityChart.baseLineY}
                    x2={equityChart.width - equityChart.padR}
                    y2={equityChart.baseLineY}
                    stroke="#334155"
                    strokeWidth="1"
                    strokeDasharray="4 4"
                  />
                  <text
                    x={6}
                    y={equityChart.baseLineY + 3.5}
                    fill="#94A3B8"
                    fontSize="10"
                    fontFamily="JetBrains Mono, monospace"
                  >
                    ${result.summary.starting_balance}
                  </text>
                  <path
                    d={equityChart.areaD}
                    fill={equityChart.isProfit ? '#10B981' : '#EF4444'}
                    fillOpacity="0.12"
                  />
                  <path
                    d={equityChart.pathD}
                    fill="none"
                    stroke={equityChart.isProfit ? '#10B981' : '#EF4444'}
                    strokeWidth="2"
                  />
                </svg>
              </div>
            </div>
          )}

          {/* Breakdown & Trades */}
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
            <div className="xl:col-span-6 border border-[#1A2334] rounded-lg bg-[#0F1523] overflow-hidden">
              <div className="px-4 py-3 border-b border-[#1A2334] bg-[#121928]">
                <h3 className="text-xs font-display font-semibold text-slate-100">
                  Strategy Accuracy Leaderboard
                </h3>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-[#1A2334] text-slate-400 bg-[#0D1320]">
                      <th className="py-2.5 px-4">Strategy</th>
                      <th className="py-2.5 px-3 text-right">Signals</th>
                      <th className="py-2.5 px-3 text-right">W / L</th>
                      <th className="py-2.5 px-4 text-right">Win Rate</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#172033]">
                    {[...result.strategy_breakdown]
                      .sort((a, b) => b.win_rate - a.win_rate)
                      .map((s) => (
                        <tr key={s.id} className="hover:bg-[#141D2F]">
                          <td className="py-2.5 px-4">
                            <div className="font-medium text-slate-100">{s.name}</div>
                            <div className="text-[11px] text-slate-500">{s.category}</div>
                          </td>
                          <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">
                            {s.signals}
                          </td>
                          <td className="py-2.5 px-3 text-right font-mono tabular-nums text-slate-300">
                            {s.wins} / {s.losses}
                          </td>
                          <td
                            className={`py-2.5 px-4 text-right font-mono tabular-nums font-semibold ${
                              s.win_rate >= result.breakeven_win_rate ? 'text-emerald-400' : 'text-amber-400'
                            }`}
                          >
                            {s.win_rate.toFixed(1)}%
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="xl:col-span-6 border border-[#1A2334] rounded-lg bg-[#0F1523] overflow-hidden">
              <div className="px-4 py-3 border-b border-[#1A2334] bg-[#121928]">
                <h3 className="text-xs font-display font-semibold text-slate-100">
                  Simulated Trades
                </h3>
              </div>
              <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-[#1A2334] text-slate-400 bg-[#0D1320]">
                      <th className="py-2.5 px-4">Bar</th>
                      <th className="py-2.5 px-3">Signal</th>
                      <th className="py-2.5 px-3 text-right">Entry</th>
                      <th className="py-2.5 px-3 text-right">Outcome</th>
                      <th className="py-2.5 px-4 text-right">P&L</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#172033] font-mono tabular-nums">
                    {result.recent_trades.map((t, idx) => (
                      <tr key={idx} className="hover:bg-[#141D2F]">
                        <td className="py-2 px-4 text-slate-400">#{t.bar}</td>
                        <td
                          className={`py-2 px-3 font-semibold ${
                            t.signal === 'CALL' ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {t.signal} ({t.confidence}%)
                        </td>
                        <td className="py-2 px-3 text-right text-slate-200">{t.entry}</td>
                        <td className="py-2 px-3 text-right">
                          <span className={t.result === 'WIN' ? 'text-emerald-400' : 'text-rose-400'}>
                            {t.result}
                            {t.step_won > 0 ? ` (G${t.step_won})` : ''}
                          </span>
                        </td>
                        <td
                          className={`py-2 px-4 text-right font-semibold ${
                            t.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
