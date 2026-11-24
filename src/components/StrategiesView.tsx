import { Disclosure } from './Disclosure';
import React from 'react';
import { Check, LogIn, RotateCcw } from 'lucide-react';
import type { AnalysisResponse, BotConfig, StrategyCatalogItem } from '../types/trading';

interface StrategiesViewProps {
  catalog: StrategyCatalogItem[];
  config: BotConfig;
  analysisData: AnalysisResponse | null;
  quotexConnected: boolean;
  onOpenQuotexLogin: () => void;
  onUpdateConfig: (next: BotConfig) => void;
  onResetDefaults: () => void;
  onApplyAndScan: () => void;
}

export const StrategiesView: React.FC<StrategiesViewProps> = ({
  catalog,
  config,
  analysisData,
  quotexConnected,
  onOpenQuotexLogin,
  onUpdateConfig,
  onResetDefaults,
  onApplyAndScan,
}) => {
  const toggleStrategy = (id: string) => {
    const current = config.enabled_strategies[id] ?? true;
    onUpdateConfig({
      ...config,
      enabled_strategies: {
        ...config.enabled_strategies,
        [id]: !current,
      },
    });
  };

  const setWeight = (id: string, weight: number) => {
    onUpdateConfig({
      ...config,
      strategy_weights: {
        ...config.strategy_weights,
        [id]: weight,
      },
    });
  };

  const enabledCount = catalog.filter((s) => config.enabled_strategies[s.id] ?? true).length;
  const isQuotexLive = Boolean(
    quotexConnected &&
      analysisData?.feed.is_live &&
      analysisData.feed.feed_source.includes('Quotex Live')
  );
  const voteMap = new Map(
    isQuotexLive ? (analysisData?.analysis.votes ?? []).map((v) => [v.id, v]) : []
  );

  return (
    <div className="p-6 max-w-[1380px] mx-auto space-y-6">
      {!quotexConnected && (
        <div className="border border-amber-500/35 rounded-lg bg-amber-500/8 px-4 py-3.5 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold text-amber-300">
              Quotex Login Required for Live Strategy Votes
            </div>
            <p className="text-xs text-slate-300 mt-0.5">
              Strategy confluence votes evaluate strictly on live Quotex candlesticks. You can adjust weights and risk rules anytime; connect Quotex Login to stream live votes.
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

      {/* Header Action Strip */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-display font-semibold text-slate-100">
            Trading Strategies & Risk Rules
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            {enabledCount} of {catalog.length} Python strategies enabled · Evaluates live Quotex candles for Signals, Telegram qualification, and Performance
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            type="button"
            onClick={onResetDefaults}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg border border-[#232F46] bg-[#121928] text-slate-300 hover:bg-[#1A2336] transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Reset Defaults</span>
          </button>
          <button
            type="button"
            onClick={onApplyAndScan}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-semibold rounded-lg bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            <Check className="w-3.5 h-3.5" />
            <span>Apply & Open Chart</span>
          </button>
        </div>
      </div>

      <Disclosure label="Indicator and risk settings">
        {/* Global Indicator & Risk Parameters */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-5 space-y-4">
            <h3 className="text-sm font-display font-semibold text-slate-100 border-b border-[#1A2334] pb-2.5">
              Confluence & Indicator Parameters
            </h3>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <label className="block">
                <span className="text-xs text-slate-400">Min Confidence %</span>
                <input
                  type="number"
                  min={35}
                  max={95}
                  value={config.min_confidence}
                  onChange={(e) => onUpdateConfig({ ...config, min_confidence: Number(e.target.value) })}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">RSI Oversold</span>
                <input
                  type="number"
                  min={10}
                  max={45}
                  value={config.rsi_oversold}
                  onChange={(e) => onUpdateConfig({ ...config, rsi_oversold: Number(e.target.value) })}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">RSI Overbought</span>
                <input
                  type="number"
                  min={55}
                  max={90}
                  value={config.rsi_overbought}
                  onChange={(e) => onUpdateConfig({ ...config, rsi_overbought: Number(e.target.value) })}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">ADX Trend Min</span>
                <input
                  type="number"
                  min={10}
                  max={40}
                  value={config.adx_threshold}
                  onChange={(e) => onUpdateConfig({ ...config, adx_threshold: Number(e.target.value) })}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">EMA Fast</span>
                <input
                  type="number"
                  min={3}
                  max={20}
                  value={config.ema_fast}
                  onChange={(e) => onUpdateConfig({ ...config, ema_fast: Number(e.target.value) })}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">EMA Slow</span>
                <input
                  type="number"
                  min={12}
                  max={55}
                  value={config.ema_slow}
                  onChange={(e) => onUpdateConfig({ ...config, ema_slow: Number(e.target.value) })}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">BB Period</span>
                <input
                  type="number"
                  min={10}
                  max={50}
                  value={config.bb_period}
                  onChange={(e) => onUpdateConfig({ ...config, bb_period: Number(e.target.value) })}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">BB Std Dev</span>
                <input
                  type="number"
                  step={0.1}
                  min={1.0}
                  max={3.5}
                  value={config.bb_std}
                  onChange={(e) => onUpdateConfig({ ...config, bb_std: Number(e.target.value) })}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>
            </div>
          </div>

          <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-5 space-y-4">
            <h3 className="text-sm font-display font-semibold text-slate-100 border-b border-[#1A2334] pb-2.5">
              Money & Risk Management
            </h3>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <label className="block col-span-2">
                <span className="text-xs text-slate-400">Stake Sizing Mode</span>
                <select
                  value={config.risk_mode}
                  onChange={(e) =>
                    onUpdateConfig({
                      ...config,
                      risk_mode: e.target.value as BotConfig['risk_mode'],
                    })
                  }
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm text-slate-100"
                >
                  <option value="martingale">Martingale Recovery</option>
                  <option value="fixed">Fixed Flat Stake</option>
                  <option value="anti_martingale">Anti-Martingale (Win Streak)</option>
                  <option value="compounding">Full Compounding</option>
                </select>
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">Base Stake ($)</span>
                <input
                  type="number"
                  min={1}
                  max={5000}
                  value={config.base_stake}
                  onChange={(e) => onUpdateConfig({ ...config, base_stake: Number(e.target.value) })}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">Gale Multiplier</span>
                <input
                  type="number"
                  step={0.1}
                  min={1.5}
                  max={3.5}
                  value={config.martingale_multiplier}
                  onChange={(e) =>
                    onUpdateConfig({ ...config, martingale_multiplier: Number(e.target.value) })
                  }
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>

              <label className="block col-span-2">
                <span className="text-xs text-slate-400">Max Martingale Steps</span>
                <input
                  type="number"
                  min={0}
                  max={4}
                  value={config.max_martingale_steps}
                  onChange={(e) =>
                    onUpdateConfig({ ...config, max_martingale_steps: Number(e.target.value) })
                  }
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-1.5 text-sm font-mono tabular-nums text-slate-100"
                />
              </label>

              <div className="col-span-2 flex flex-col justify-end">
                <div className="text-xs text-slate-400">
                  Max Sequence Risk:{' '}
                  <span className="font-mono tabular-nums text-slate-100 font-semibold">
                    $
                    {Array.from({ length: config.max_martingale_steps + 1 })
                      .reduce<number>(
                        (sum, _, step) =>
                          sum + config.base_stake * Math.pow(config.martingale_multiplier, step),
                        0
                      )
                      .toFixed(2)}
                  </span>
                </div>
                <div className="text-[11px] text-slate-500 mt-0.5">
                  Initial entry + {config.max_martingale_steps} recovery steps
                </div>
              </div>
            </div>
          </div>
        </div>
      </Disclosure>

      {/* All 11 Strategies Catalog with Live Votes */}
      <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] overflow-hidden">
        <div className="px-5 py-3.5 border-b border-[#1A2334] bg-[#121928] flex items-center justify-between">
          <h3 className="text-sm font-display font-semibold text-slate-100">
            All 11 Trading Strategies
          </h3>
          {isQuotexLive && analysisData ? (
            <span className="text-xs text-emerald-400 font-mono">
              Live Quotex votes for {analysisData.feed.name}
            </span>
          ) : (
            <span className="text-xs text-amber-400 font-mono">
              Quotex Login Required for live votes
            </span>
          )}
        </div>

        <div className="divide-y divide-[#172033]">
          {catalog.map((strat, index) => {
            const enabled = config.enabled_strategies[strat.id] ?? true;
            const weight = config.strategy_weights[strat.id] ?? strat.default_weight;
            const liveVote = voteMap.get(strat.id);

            return (
              <div
                key={strat.id}
                className={`px-5 py-4 flex flex-col lg:flex-row lg:items-center justify-between gap-4 transition-colors ${
                  enabled ? 'bg-transparent' : 'opacity-50 bg-[#0B101A]/40'
                }`}
              >
                <div className="space-y-1 max-w-2xl">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-mono tabular-nums text-slate-500">
                      {String(index + 1).padStart(2, '0')}.
                    </span>
                    <span className="text-sm font-display font-semibold text-slate-100">
                      {strat.name}
                    </span>
                    <span className="text-xs text-slate-400">
                      · {strat.category} · {strat.recommended_expiry}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400 leading-relaxed">{strat.description}</p>
                  {liveVote && enabled && (
                    <div className="text-xs font-mono text-slate-300 pt-0.5">
                      Live:{' '}
                      <span
                        className={
                          liveVote.signal === 'CALL'
                            ? 'text-emerald-400 font-semibold'
                            : liveVote.signal === 'PUT'
                            ? 'text-rose-400 font-semibold'
                            : 'text-slate-500'
                        }
                      >
                        {liveVote.signal}
                      </span>{' '}
                      <span className="text-slate-500">— {liveVote.reason}</span>
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-5 shrink-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-400">Weight</span>
                    <input
                      type="range"
                      min={0.5}
                      max={2.0}
                      step={0.05}
                      value={weight}
                      onChange={(e) => setWeight(strat.id, Number(e.target.value))}
                      className="w-24 accent-orange-500 cursor-pointer"
                    />
                    <span className="text-xs font-mono tabular-nums text-slate-200 w-10 text-right">
                      {weight.toFixed(2)}x
                    </span>
                  </div>

                  <button
                    type="button"
                    onClick={() => toggleStrategy(strat.id)}
                    className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                      enabled
                        ? 'bg-emerald-500/12 border-emerald-500/35 text-emerald-300'
                        : 'bg-[#121928] border-[#232F46] text-slate-400'
                    }`}
                  >
                    {enabled ? 'Enabled' : 'Disabled'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
