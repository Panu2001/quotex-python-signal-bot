import React, { useState, useRef, useEffect, useMemo } from 'react';
import { ChevronDown, Loader2, LogIn, Minus, Plus, Search } from 'lucide-react';
import { CandlestickChart } from './CandlestickChart';
import { TimeframeSelector } from './TimeframeSelector';
import type {
  AnalysisResponse,
  AssetMeta,
  Candle,
  QuotexLiveStatus,
} from '../types/trading';

interface LiveChartModuleProps {
  assets: Record<string, AssetMeta>;
  selectedSymbol: string;
  onSelectSymbol: (sym: string) => void;
  timeframe: string;
  onChangeTimeframe: (tf: string) => void;
  analysisData: AnalysisResponse | null;
  quotexStatus: QuotexLiveStatus | null;
  isConnected: boolean;
  isLoading: boolean;
  loadError: string | null;
  onRunSingleScan: () => void;
  onOpenQuotexLogin: () => void;
}

const ZOOM_STEPS = [40, 60, 80, 110, 145];

export const LiveChartModule: React.FC<LiveChartModuleProps> = ({
  assets,
  selectedSymbol,
  onSelectSymbol,
  timeframe,
  onChangeTimeframe,
  analysisData,
  quotexStatus,
  isConnected,
  isLoading,
  loadError,
  onRunSingleScan,
  onOpenQuotexLogin,
}) => {
  const [zoomIdx, setZoomIdx] = useState(2); // 80 bars default
  const [showBB, setShowBB] = useState(true);
  const [showEMA, setShowEMA] = useState(true);
  const [showSuperTrend, setShowSuperTrend] = useState(true);
  const [showSR, setShowSR] = useState(false);
  const [marketsOpen, setMarketsOpen] = useState(false);
  const [marketSearch, setMarketSearch] = useState('');
  const [hoveredCandle, setHoveredCandle] = useState<Candle | null>(null);
  const [hoveredRsi, setHoveredRsi] = useState<number | null>(null);

  const dropdownRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setMarketsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    setHoveredCandle(null);
    setHoveredRsi(null);
  }, [selectedSymbol, timeframe]);

  const matchesSelection = analysisData?.feed.symbol === selectedSymbol &&
    analysisData.feed.timeframe === timeframe;
  const feed = matchesSelection ? analysisData.feed : undefined;
  const analysis = matchesSelection ? analysisData.analysis : undefined;
  const decimals = feed?.decimals ?? assets[selectedSymbol]?.decimals ?? 5;
  const lastCandle = feed?.candles[feed.candles.length - 1] ?? null;
  const displayCandle = hoveredCandle ?? lastCandle;
  const lastRsi = analysis?.overlays.rsi[analysis.overlays.rsi.length - 1] ?? null;
  const displayRsi = hoveredRsi ?? lastRsi;

  const isQuotexConnected = Boolean(quotexStatus?.connected);
  const hasChartData = Boolean(
    isConnected &&
      isQuotexConnected &&
      feed &&
      analysis &&
      feed.is_live &&
      feed.feed_source.includes('Quotex Live') &&
      feed.candles.length > 0
  );
  const selectedName = assets[selectedSymbol]?.name ?? selectedSymbol;
  const isSwitchingAsset = Boolean(
    isLoading && isQuotexConnected && (!feed || feed.symbol !== selectedSymbol || feed.timeframe !== timeframe)
  );

  const filteredAssets = useMemo(() => {
    const entries = Object.entries(assets);
    if (!marketSearch.trim()) return entries;
    const q = marketSearch.trim().toLowerCase();
    const compact = q.replace(/[^a-z0-9]/g, '');
    return entries.filter(
      ([sym, meta]) =>
        sym.toLowerCase().includes(q) ||
        (compact.length > 0 && sym.toLowerCase().replace(/[^a-z0-9]/g, '').includes(compact)) ||
        meta.name.toLowerCase().includes(q) ||
        meta.category.toLowerCase().includes(q)
    );
  }, [assets, marketSearch]);

  function closeAssetPicker() {
    setMarketsOpen(false);
    setMarketSearch('');
    dropdownRef.current?.querySelector('button')?.focus();
  }

  function selectAsset(symbol: string) {
    onSelectSymbol(symbol);
    closeAssetPicker();
  }

  return (
    <div className="h-full flex flex-col bg-[#0B101A]">
      <div className="px-5 py-3 border-b border-[#1A2334] bg-[#0D1320] flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex flex-wrap items-center gap-2">
            {/* Chart asset picker */}
            <div className="relative" ref={dropdownRef}>
              <button
                type="button"
                aria-label={`Search chart assets, currently ${selectedName}`}
                aria-expanded={marketsOpen}
                onClick={() => setMarketsOpen((v) => !v)}
                className="h-8 px-3 inline-flex items-center gap-2 text-xs font-medium rounded-md bg-[#121928] border border-[#232F46] text-slate-200 hover:bg-[#1A2336] transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                <Search className="w-3.5 h-3.5 text-slate-400" />
                <span className="truncate">{selectedName}</span>
                <ChevronDown className="w-3.5 h-3.5 text-slate-400" />
              </button>

              {marketsOpen && (
                <div className="absolute left-0 mt-1.5 w-72 max-w-[calc(100vw-2.5rem)] rounded-lg bg-[#101725] border border-[#232F46] shadow-xl z-40 overflow-hidden">
                  <div className="px-3.5 py-2.5 border-b border-[#1C2639] space-y-2">
                    <div className="relative">
                      <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                      <input
                        type="search"
                        autoFocus
                        onKeyDown={(event) => {
                          if (event.key === 'Escape') {
                            event.preventDefault();
                            closeAssetPicker();
                          } else if (event.key === 'Enter' && marketSearch.trim() && filteredAssets[0]) {
                            event.preventDefault();
                            selectAsset(filteredAssets[0][0]);
                          }
                        }}
                        placeholder="Search assets"
                        aria-label="Search assets"
                        value={marketSearch}
                        onChange={(e) => setMarketSearch(e.target.value)}
                        className="w-full h-7 pl-8 pr-2.5 bg-[#0B101A] border border-[#232F46] rounded text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500"
                      />
                    </div>
                  </div>
                  <div className="max-h-72 overflow-y-auto divide-y divide-[#182133]">
                    {filteredAssets.length === 0 && (
                      <p className="px-3.5 py-2.5 text-xs text-slate-400">No matching assets</p>
                    )}
                    {filteredAssets.map(([sym, meta]) => {
                      const isSel = sym === selectedSymbol;
                      return (
                        <button
                          key={sym}
                          type="button"
                          onClick={() => selectAsset(sym)}
                          className={`w-full px-3.5 py-2.5 flex items-center justify-between text-left text-xs transition-colors cursor-pointer ${
                            isSel ? 'bg-orange-500/10 text-orange-300' : 'hover:bg-[#172033] text-slate-200'
                          }`}
                        >
                          <div>
                            <div className="font-medium">{meta.name}</div>
                            <div className="text-[11px] text-slate-500 font-mono tabular-nums">
                              {meta.category} · {meta.payout}%
                            </div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
            <TimeframeSelector
              value={timeframe}
              onChange={onChangeTimeframe}
              size="sm"
              align="container"
              ariaLabel="Chart view timeframe (display only)"
            />

            {hasChartData && (
              <>
                <div className="flex items-center">
                  <button
                    type="button"
                    title="Zoom out"
                    onClick={() => setZoomIdx((z) => Math.min(ZOOM_STEPS.length - 1, z + 1))}
                    className="h-8 w-8 inline-flex items-center justify-center rounded-l-md bg-[#121928] border border-[#232F46] text-slate-300 hover:bg-[#1A2336] transition-colors cursor-pointer"
                  >
                    <Minus className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    title="Zoom in"
                    onClick={() => setZoomIdx((z) => Math.max(0, z - 1))}
                    className="h-8 w-8 inline-flex items-center justify-center rounded-r-md bg-[#121928] border border-l-0 border-[#232F46] text-slate-300 hover:bg-[#1A2336] transition-colors cursor-pointer"
                  >
                    <Plus className="w-3.5 h-3.5" />
                  </button>
                </div>

                <button
                  type="button"
                  disabled={isLoading}
                  onClick={() => {
                    setZoomIdx(2);
                    onRunSingleScan();
                  }}
                  className="h-8 px-3 text-xs font-medium rounded-md bg-[#121928] border border-[#232F46] text-slate-300 hover:bg-[#1A2336] transition-colors whitespace-nowrap shrink-0 cursor-pointer disabled:opacity-50"
                >
                  Refresh
                </button>
              </>
            )}
          </div>
          {hasChartData && isLoading && (
            <span role="status" className="inline-flex items-center gap-1.5 text-xs text-slate-400">
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
              {isSwitchingAsset ? 'Switching chart…' : 'Refreshing…'}
            </span>
          )}
        </div>

        {hasChartData && loadError && (
          <p role="status" className="text-xs text-amber-300">{loadError}</p>
        )}

        {hasChartData && (
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs font-mono tabular-nums">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-slate-400">
              <span>
                O <span className="text-slate-200">{displayCandle ? displayCandle.open.toFixed(decimals) : '—'}</span>
              </span>
              <span>
                H <span className="text-slate-200">{displayCandle ? displayCandle.high.toFixed(decimals) : '—'}</span>
              </span>
              <span>
                L <span className="text-slate-200">{displayCandle ? displayCandle.low.toFixed(decimals) : '—'}</span>
              </span>
              <span>
                C{' '}
                <span
                  className={
                    displayCandle
                      ? displayCandle.close >= displayCandle.open
                        ? 'text-emerald-400'
                        : 'text-rose-400'
                      : 'text-slate-200'
                  }
                >
                  {displayCandle ? displayCandle.close.toFixed(decimals) : '—'}
                </span>
              </span>
              {displayRsi !== null && (
                <span>
                  RSI{' '}
                  <span
                    className={
                      displayRsi >= 70 ? 'text-rose-400' : displayRsi <= 30 ? 'text-emerald-400' : 'text-slate-200'
                    }
                  >
                    {displayRsi.toFixed(1)}
                  </span>
                </span>
              )}
            </div>

            <div className="flex items-center gap-1.5 font-sans">
              {[
                { label: 'BB', active: showBB, toggle: () => setShowBB((v) => !v) },
                { label: 'EMA', active: showEMA, toggle: () => setShowEMA((v) => !v) },
                { label: 'SuperTrend', active: showSuperTrend, toggle: () => setShowSuperTrend((v) => !v) },
                { label: 'S/R', active: showSR, toggle: () => setShowSR((v) => !v) },
              ].map((ov) => (
                <button
                  key={ov.label}
                  type="button"
                  onClick={ov.toggle}
                  className={`px-2 py-0.5 text-[11px] font-medium rounded border transition-colors whitespace-nowrap cursor-pointer ${
                    ov.active
                      ? 'bg-[#172033] border-[#2B3A55] text-slate-200'
                      : 'bg-transparent border-transparent text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {ov.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="flex-1 min-h-[460px] flex flex-col">
        {hasChartData && feed && analysis ? (
          <div className="flex-1 p-3 sm:p-4">
            <CandlestickChart
              candles={feed.candles}
              overlays={analysis.overlays}
              supports={analysis.levels.supports}
              resistances={analysis.levels.resistances}
              decimals={feed.decimals}
              signal={analysis.signal}
              timeframe={timeframe}
              visibleCount={ZOOM_STEPS[zoomIdx]}
              showBB={showBB}
              showEMA={showEMA}
              showSuperTrend={showSuperTrend}
              showSR={showSR}
              onHoverCandle={(c, r) => {
                setHoveredCandle(c);
                setHoveredRsi(r);
              }}
            />
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-center px-6 py-20 max-w-lg mx-auto">
            <h2 className="text-base font-display font-medium text-slate-100">
              {isSwitchingAsset
                ? `Loading ${selectedName} (${timeframe})...`
                : !isQuotexConnected || feed?.live_required
                ? 'Connect to Quotex to load live candles'
                : feed?.error
                ? `No Live Quotex Candlesticks for ${feed.name}`
                : 'No candles available'}
            </h2>
            {!isSwitchingAsset && isQuotexConnected && !feed?.live_required && (
              <p className="mt-2 text-xs text-slate-400 leading-relaxed">
                {loadError || feed?.error || 'Choose another asset or reload the chart.'}
              </p>
            )}
            <div className="mt-5 flex flex-wrap items-center justify-center gap-2.5">
              {!isQuotexConnected || feed?.live_required ? (
                <button
                  type="button"
                  onClick={onOpenQuotexLogin}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-semibold rounded-lg bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors cursor-pointer"
                >
                  <LogIn className="w-3.5 h-3.5" />
                  <span>Quotex Login</span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={onRunSingleScan}
                  disabled={isLoading}
                  className="px-4 py-2 text-xs font-medium rounded-lg bg-[#161E2E] border border-[#27344E] text-slate-200 hover:bg-[#1E293D] transition-colors cursor-pointer disabled:opacity-50"
                >
                  {isLoading ? 'Loading Quotex Feed...' : 'Reload Quotex Candles'}
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Slim Bottom Summary Strip */}
      {hasChartData && feed && analysis && (
        <div className="px-5 py-2.5 border-t border-[#1A2334] bg-[#0D1320] flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs text-slate-400 font-mono tabular-nums flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>{feed.candles.length} candles</span>
            <span>·</span>
            <span>Payout <strong className={feed.payout >= 80 ? 'text-emerald-400' : 'text-amber-400'}>{feed.payout}%</strong></span>
            <span>·</span>
            <span>Regime <strong className="text-cyan-300">{analysis.market_regime || analysis.volatility_regime}</strong></span>
            {analysis.mtf_confirmation?.mtf_trend && (
              <>
                <span>·</span>
                <span>5m MTF <strong className={analysis.mtf_confirmation.mtf_trend === 'BULLISH' ? 'text-emerald-400' : analysis.mtf_confirmation.mtf_trend === 'BEARISH' ? 'text-rose-400' : 'text-slate-300'}>{analysis.mtf_confirmation.mtf_trend}</strong></span>
              </>
            )}
            {analysis.candlestick_quality?.quality_score !== undefined && (
              <>
                <span>·</span>
                <span>Candle Quality <strong className={analysis.candlestick_quality.quality_score >= 0.7 ? 'text-emerald-400' : 'text-amber-400'}>{(analysis.candlestick_quality.quality_score * 100).toFixed(0)}%</strong></span>
              </>
            )}
            {analysis.entry_timing_guard && (
              <>
                <span>·</span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                  analysis.entry_timing_guard.is_optimal_open
                    ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                    : 'bg-slate-800 text-slate-400'
                }`}>
                  {analysis.entry_timing_guard.is_optimal_open ? '00s Open Window' : `Bar: :${String(analysis.entry_timing_guard.second_in_candle).padStart(2, '0')}`}
                </span>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
