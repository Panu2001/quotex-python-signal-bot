import { Disclosure } from './Disclosure';
import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowDownRight,
  ArrowUpRight,
  Bell,
  BellOff,
  Download,
  LogIn,
  RotateCcw,
  Trash2,
  X,
} from 'lucide-react';
import type {
  AnalysisResponse,
  AssetMeta,
  BotConfig,
  NotificationPermissionState,
  ScanPairResult,
  SignalLogEntry,
} from '../types/trading';

export interface PaperOrderRequest {
  symbol: string;
  direction: 'CALL' | 'PUT';
  stake: number;
  expiry: string;
  entryMode: 'immediate' | 'future';
  leadMinutes: number;
  sourceLabel?: string;
}

interface SignalsModuleProps {
  signalLogs: SignalLogEntry[];
  assets: Record<string, AssetMeta>;
  selectedSymbol: string;
  timeframe: string;
  analysisData: AnalysisResponse | null;
  scanResults: ScanPairResult[];
  quotexConnected: boolean;
  onOpenQuotexLogin: () => void;
  config: BotConfig;
  onUpdateConfig: (next: BotConfig) => void;
  isBotRunning: boolean;
  desktopNotificationsEnabled: boolean;
  notificationPermission: NotificationPermissionState;
  onToggleDesktopNotifications: () => void;
  onSendTestDesktopNotification: () => void;
  onPlacePaperOrder: (order: PaperOrderRequest) => void;
  onCancelPendingTrade: (id: string) => void;
  onResetPaperAccount: () => void;
  onClearSignals: () => void;
}

type ResultFilter = 'ALL' | 'PENDING' | 'ACTIVE' | 'WIN' | 'LOSS' | 'TIE';
type DirectionFilter = 'ALL' | 'CALL' | 'PUT';

const EXPIRY_CHOICES = ['30s', '1m', '2m', '3m', '5m', '15m'];
const STAKE_PRESETS = [5, 10, 25, 50, 100];

function formatRemainingCountdown(sec: number): string {
  const safe = Math.max(0, Math.floor(sec));
  if (safe < 60) return `${safe}s`;
  if (safe < 3600) {
    const m = Math.floor(safe / 60);
    const s = safe % 60;
    return `${m}m ${String(s).padStart(2, '0')}s`;
  }
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  return `${h}h ${String(m).padStart(2, '0')}m`;
}

function formatPrice(val: number | undefined, decimals = 5): string {
  if (val === undefined || !Number.isFinite(val) || val === 0) return '—';
  return val.toFixed(Math.max(2, Math.min(6, decimals)));
}

function formatClockTime(tsSec: number): string {
  return new Date(tsSec * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export const SignalsModule: React.FC<SignalsModuleProps> = ({
  signalLogs,
  assets,
  selectedSymbol,
  timeframe,
  analysisData,
  scanResults,
  quotexConnected,
  onOpenQuotexLogin,
  config,
  onUpdateConfig,
  isBotRunning,
  desktopNotificationsEnabled,
  notificationPermission,
  onToggleDesktopNotifications,
  onSendTestDesktopNotification,
  onPlacePaperOrder,
  onCancelPendingTrade,
  onResetPaperAccount,
  onClearSignals,
}) => {
  const [resultFilter, setResultFilter] = useState<ResultFilter>('ALL');
  const [directionFilter, setDirectionFilter] = useState<DirectionFilter>('ALL');

  // Paper Trade Simulator Ticket State
  const [orderSymbol, setOrderSymbol] = useState<string>(selectedSymbol);
  const [orderStake, setOrderStake] = useState<number>(config.base_stake || 10);
  const [orderExpiry, setOrderExpiry] = useState<string>(
    EXPIRY_CHOICES.includes(timeframe) ? timeframe : '1m'
  );
  const [entryMode, setEntryMode] = useState<'immediate' | 'future'>('immediate');
  const [nowSec, setNowSec] = useState<number>(() => Math.floor(Date.now() / 1000));

  useEffect(() => {
    setOrderSymbol(selectedSymbol);
  }, [selectedSymbol]);

  useEffect(() => {
    const id = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(id);
  }, []);

  const startingBalance = Math.max(50, Number(config.paper_starting_balance) || 1000);
  const leadMinutes = Math.max(0, Math.min(60, Math.floor(config.signal_lead_minutes ?? 0)));

  const orderAssetMeta = assets[orderSymbol];
  const orderScanMatch = useMemo(
    () => scanResults.find((p) => p.symbol === orderSymbol),
    [scanResults, orderSymbol]
  );
  const isAnalysisQuotexLive = Boolean(
    quotexConnected &&
      analysisData?.feed.symbol === orderSymbol &&
      analysisData.feed.is_live &&
      analysisData.feed.feed_source.includes('Quotex Live')
  );
  const isScanQuotexLive = Boolean(
    quotexConnected &&
      orderScanMatch?.is_live &&
      orderScanMatch.feed_source.includes('Quotex Live')
  );
  const orderLivePrice = isAnalysisQuotexLive
    ? analysisData?.analysis.price ?? 0
    : isScanQuotexLive
    ? orderScanMatch?.price ?? 0
    : 0;
  const orderTrendSignal = isAnalysisQuotexLive
    ? analysisData?.analysis.signal
    : isScanQuotexLive
    ? orderScanMatch?.signal
    : undefined;
  const orderTrendConfidence = isAnalysisQuotexLive
    ? analysisData?.analysis.confidence
    : isScanQuotexLive
    ? orderScanMatch?.confidence
    : undefined;
  const orderPayout = isAnalysisQuotexLive
    ? analysisData?.feed.payout ?? orderAssetMeta?.payout ?? 85
    : orderAssetMeta?.payout ?? orderScanMatch?.payout ?? 85;
  const orderDecimals = isAnalysisQuotexLive
    ? analysisData?.feed.decimals ?? orderAssetMeta?.decimals ?? 5
    : orderAssetMeta?.decimals ?? orderScanMatch?.decimals ?? 5;

  const filtered = useMemo(() => {
    return signalLogs.filter((l) => {
      if (resultFilter !== 'ALL' && l.status !== resultFilter) return false;
      if (directionFilter !== 'ALL' && l.signal !== directionFilter) return false;
      return true;
    });
  }, [signalLogs, resultFilter, directionFilter]);

  const wins = signalLogs.filter((l) => l.status === 'WIN').length;
  const losses = signalLogs.filter((l) => l.status === 'LOSS').length;
  const ties = signalLogs.filter((l) => l.status === 'TIE').length;
  const pendingCount = signalLogs.filter((l) => l.status === 'PENDING').length;
  const activeCount = signalLogs.filter((l) => l.status === 'ACTIVE').length;
  const openStakeLocked = signalLogs
    .filter((l) => l.status === 'ACTIVE' || l.status === 'PENDING')
    .reduce((acc, l) => acc + l.stake, 0);

  const settledDecisive = wins + losses;
  const winRate = settledDecisive > 0 ? ((wins / settledDecisive) * 100).toFixed(1) : '0.0';
  const netPnl = signalLogs.reduce((acc, l) => acc + l.pnl, 0);
  const grossProfit = signalLogs.filter((l) => l.pnl > 0).reduce((a, l) => a + l.pnl, 0);
  const grossLoss = Math.abs(signalLogs.filter((l) => l.pnl < 0).reduce((a, l) => a + l.pnl, 0));
  const profitFactor =
    grossLoss > 0 ? (grossProfit / grossLoss).toFixed(2) : grossProfit > 0 ? '∞' : '0.00';

  const currentPaperBalance = Number((startingBalance + netPnl).toFixed(2));
  const availablePaperBalance = Number(Math.max(0, currentPaperBalance - openStakeLocked).toFixed(2));
  const roiPct = startingBalance > 0 ? ((netPnl / startingBalance) * 100).toFixed(1) : '0.0';

  // Equity curve points from settled trades (oldest to newest)
  const equityPoints = useMemo(() => {
    const settled = signalLogs
      .filter((l) => l.status === 'WIN' || l.status === 'LOSS' || l.status === 'TIE')
      .slice()
      .reverse();
    const pts: number[] = [startingBalance];
    let running = startingBalance;
    for (const t of settled) {
      running = Number((running + t.pnl).toFixed(2));
      pts.push(running);
    }
    return pts;
  }, [signalLogs, startingBalance]);

  const equitySparkline = useMemo(() => {
    if (equityPoints.length < 2) return null;
    const w = 220;
    const h = 44;
    const min = Math.min(...equityPoints);
    const max = Math.max(...equityPoints);
    const span = Math.max(1, max - min);
    const coords = equityPoints.map((val, idx) => {
      const x = (idx / (equityPoints.length - 1)) * w;
      const y = h - 4 - ((val - min) / span) * (h - 8);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return {
      path: `M ${coords.join(' L ')}`,
      positive: equityPoints[equityPoints.length - 1] >= startingBalance,
    };
  }, [equityPoints, startingBalance]);

  const filteredStats = useMemo(() => {
    const count = filtered.length;
    const pnl = filtered.reduce((acc, l) => acc + l.pnl, 0);
    const avgConf =
      count > 0 ? filtered.reduce((acc, l) => acc + l.confidence, 0) / count : 0;

    const strategyCounts = new Map<string, number>();
    for (const item of filtered) {
      strategyCounts.set(item.topStrategy, (strategyCounts.get(item.topStrategy) ?? 0) + 1);
    }
    let dominantStrategy = '—';
    let maxHits = 0;
    strategyCounts.forEach((hits, strat) => {
      if (hits > maxHits) {
        maxHits = hits;
        dominantStrategy = strat;
      }
    });

    return { count, pnl, avgConf, dominantStrategy };
  }, [filtered]);

  const submitPaperTrade = (direction: 'CALL' | 'PUT') => {
    const safeStake = Math.max(1, Math.min(5000, Number(orderStake) || 10));
    onPlacePaperOrder({
      symbol: orderSymbol,
      direction,
      stake: safeStake,
      expiry: orderExpiry,
      entryMode,
      leadMinutes: entryMode === 'future' ? Math.max(1, leadMinutes) : 0,
      sourceLabel: entryMode === 'future' ? `Scheduled Paper (+${Math.max(1, leadMinutes)}m)` : 'Manual Paper Trade',
    });
  };

  const exportCsv = () => {
    const source = filtered.length > 0 ? filtered : signalLogs;
    const header =
      'SignalTime,EntryTime,ExpiryTime,Symbol,Timeframe,Direction,Confidence,Entry,Exit,Stake,Payout,Status,PnL,Strategy\n';
    const rows = source
      .map((l) =>
        [
          new Date(l.createdAt * 1000).toISOString(),
          new Date((l.entryAt || l.createdAt) * 1000).toISOString(),
          new Date(l.expiresAt * 1000).toISOString(),
          l.symbol,
          l.timeframe,
          l.signal,
          l.confidence,
          l.entryPrice,
          l.exitPrice ?? '',
          l.stake,
          l.payout,
          l.status,
          l.pnl,
          `"${l.topStrategy}"`,
        ].join(',')
      )
      .join('\n');
    const blob = new Blob([header + rows], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const suffix = resultFilter === 'ALL' ? 'all' : resultFilter.toLowerCase();
    a.download = `quotex_paper_signals_${suffix}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const notificationsActive =
    notificationPermission === 'granted' && desktopNotificationsEnabled;

  const notificationStatusText =
    notificationPermission === 'unsupported'
      ? 'Desktop notifications are not supported in this browser.'
      : notificationPermission === 'denied'
      ? 'Blocked by browser settings. Allow notifications for this site to receive background signal alerts.'
      : notificationsActive
      ? isBotRunning
        ? 'Active — desktop alerts fire on new signals while this tab is unfocused.'
        : 'Enabled — start the bot to receive desktop alerts while this tab is unfocused.'
      : 'Paused — enable to get OS desktop notifications for new signals when this tab is unfocused.';

  const resultOptions: { value: ResultFilter; label: string; count: number }[] = [
    { value: 'ALL', label: 'ALL', count: signalLogs.length },
    { value: 'PENDING', label: 'PENDING', count: pendingCount },
    { value: 'ACTIVE', label: 'ACTIVE', count: activeCount },
    { value: 'WIN', label: 'WIN', count: wins },
    { value: 'LOSS', label: 'LOSS', count: losses },
    { value: 'TIE', label: 'TIE', count: ties },
  ];

  const potentialProfit = Number(((Math.max(1, orderStake) * orderPayout) / 100).toFixed(2));

  return (
    <div className="p-6 max-w-[1380px] mx-auto space-y-6">
      {!quotexConnected && (
        <div className="border border-amber-500/35 rounded-lg bg-amber-500/8 px-4 py-3.5 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold text-amber-300">
              Sign in to place manual paper trades
            </div>
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

      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={exportCsv}
            disabled={signalLogs.length === 0}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg bg-[#121928] border border-[#232F46] text-slate-200 hover:bg-[#1A2336] transition-colors cursor-pointer disabled:opacity-45"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Download CSV</span>
          </button>
          <button
            type="button"
            onClick={onResetPaperAccount}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg bg-[#121928] border border-[#232F46] text-slate-200 hover:bg-[#1A2336] transition-colors cursor-pointer"
          >
            <RotateCcw className="w-3.5 h-3.5 text-orange-400" />
            <span>Reset Paper Account</span>
          </button>
          <button
            type="button"
            onClick={onClearSignals}
            disabled={signalLogs.length === 0}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg bg-[#121928] border border-[#232F46] text-slate-300 hover:text-rose-300 transition-colors cursor-pointer disabled:opacity-45"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Clear Ledger</span>
          </button>
        </div>
      </div>

      {/* Paper Trading Account Summary Strip + Equity Curve */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-4">
          <div className="flex items-center justify-between gap-2 text-xs text-slate-400">
            <span>Paper Balance</span>
            <span className="font-mono text-[10px] text-slate-500">
              Start ${startingBalance.toFixed(0)}
            </span>
          </div>
          <div className="mt-1 text-xl font-mono tabular-nums font-bold text-slate-100">
            ${currentPaperBalance.toFixed(2)}
          </div>
          <div className="mt-0.5 text-[11px] font-mono tabular-nums text-slate-500">
            Avail: ${availablePaperBalance.toFixed(2)}
            {openStakeLocked > 0 ? ` · In Play $${openStakeLocked.toFixed(0)}` : ''}
          </div>
        </div>

        <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-4">
          <div className="text-xs text-slate-400">Net Session P&L</div>
          <div
            className={`mt-1 text-xl font-mono tabular-nums font-bold ${
              netPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            {netPnl >= 0 ? '+' : ''}${netPnl.toFixed(2)}
          </div>
          <div className="mt-0.5 text-[11px] font-mono tabular-nums text-slate-500">
            ROI {Number(roiPct) >= 0 ? '+' : ''}{roiPct}% · PF {profitFactor}
          </div>
        </div>

        <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-4">
          <div className="text-xs text-slate-400">Win Rate</div>
          <div className="mt-1 text-xl font-mono tabular-nums font-bold text-slate-100">
            {winRate}%
          </div>
          <div className="mt-0.5 text-[11px] font-mono tabular-nums text-slate-400">
            <span className="text-emerald-400">{wins}W</span>
            <span className="text-slate-600 mx-1">/</span>
            <span className="text-rose-400">{losses}L</span>
            {ties > 0 && (
              <>
                <span className="text-slate-600 mx-1">/</span>
                <span className="text-slate-300">{ties}T</span>
              </>
            )}
          </div>
        </div>

        <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-4">
          <div className="text-xs text-slate-400">Open Simulation Queue</div>
          <div className="mt-1 text-xl font-mono tabular-nums font-bold text-amber-400">
            {activeCount} <span className="text-xs font-normal text-slate-400">Active</span>
            {pendingCount > 0 && (
              <span className="text-sky-400 text-sm ml-2">+{pendingCount} Pending</span>
            )}
          </div>
          <div className="mt-0.5 text-[11px] font-mono text-slate-500">
            Future Lead: {leadMinutes === 0 ? 'Next candle' : `${leadMinutes}m ahead`}
          </div>
        </div>

        <div className="col-span-2 lg:col-span-1 border border-[#1A2334] rounded-lg bg-[#0F1523] p-4 flex flex-col justify-between">
          <div className="text-xs text-slate-400">Equity Curve</div>
          {equitySparkline ? (
            <svg viewBox="0 0 220 44" className="w-full h-11 mt-1 overflow-visible">
              <path
                d={equitySparkline.path}
                fill="none"
                stroke={equitySparkline.positive ? '#34d399' : '#fb7185'}
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : (
            <div className="text-[11px] text-slate-500 mt-2">
              Settle 1+ paper trades to plot curve
            </div>
          )}
        </div>
      </div>

      <Disclosure label="Place a paper trade">
        {/* Interactive Paper Trade Simulator Ticket + Future Signal Lead Time Control */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
          {/* Left: Paper Trade Execution Ticket */}
          <div className="lg:col-span-8 border border-[#1A2334] rounded-lg bg-[#0F1523] p-5 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-display font-semibold text-slate-100">
                    Quotex Paper Trade Simulator
                  </h3>
                  {quotexConnected && orderTrendSignal && (
                    <span
                      className={`text-[11px] font-mono font-semibold px-2 py-0.5 rounded border ${
                        orderTrendSignal === 'CALL'
                          ? 'bg-emerald-500/12 border-emerald-500/35 text-emerald-300'
                          : orderTrendSignal === 'PUT'
                          ? 'bg-rose-500/12 border-rose-500/35 text-rose-300'
                          : 'bg-[#121928] border-[#232F46] text-slate-400'
                      }`}
                    >
                      Quotex Trend:{' '}
                      {orderTrendSignal === 'CALL'
                        ? 'CALL ▲'
                        : orderTrendSignal === 'PUT'
                        ? 'PUT ▼'
                        : 'WAIT'}
                      {orderTrendConfidence !== undefined ? ` · ${orderTrendConfidence.toFixed(0)}%` : ''}
                    </span>
                  )}
                </div>
                <p className="text-xs text-slate-400 mt-0.5">
                  Locks real Quotex live entry price and settles at expiry against live Quotex trends (ties refund stake).
                </p>
              </div>
              <div className="text-right font-mono tabular-nums">
                <div className="text-xs text-slate-200 font-semibold">
                  {!quotexConnected
                    ? 'Quotex Login Req.'
                    : orderLivePrice > 0
                    ? formatPrice(orderLivePrice, orderDecimals)
                    : 'Fetching Quotex...'}
                </div>
                <div className="text-[11px] text-emerald-400">
                  Payout {orderPayout}% (+${potentialProfit.toFixed(2)})
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {/* Asset */}
              <label className="block">
                <span className="text-[11px] text-slate-400">Asset Pair</span>
                <select
                  value={orderSymbol}
                  onChange={(e) => setOrderSymbol(e.target.value)}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-2 text-xs text-slate-100"
                >
                  {Object.entries(assets).map(([sym, meta]) => (
                    <option key={sym} value={sym}>
                      {meta.name} ({meta.payout}%)
                    </option>
                  ))}
                </select>
              </label>

              {/* Stake */}
              <div>
                <span className="text-[11px] text-slate-400 block">Stake ($)</span>
                <input
                  type="number"
                  min={1}
                  max={5000}
                  step={1}
                  value={orderStake}
                  onChange={(e) => setOrderStake(Math.max(1, Number(e.target.value) || 1))}
                  className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-2 text-xs font-mono tabular-nums text-slate-100"
                />
                <div className="mt-1.5 flex items-center gap-1">
                  {STAKE_PRESETS.map((amt) => (
                    <button
                      key={amt}
                      type="button"
                      onClick={() => setOrderStake(amt)}
                      className={`px-1.5 py-0.5 text-[10px] font-mono rounded border cursor-pointer ${
                        orderStake === amt
                          ? 'bg-orange-500/15 border-orange-500/40 text-orange-300'
                          : 'bg-[#0B101A] border-[#1F2A3E] text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      ${amt}
                    </button>
                  ))}
                </div>
              </div>

              {/* Expiry */}
              <div>
                <span className="text-[11px] text-slate-400 block">Expiry Duration</span>
                <div className="mt-1 grid grid-cols-3 gap-1">
                  {EXPIRY_CHOICES.map((exp) => (
                    <button
                      key={exp}
                      type="button"
                      onClick={() => setOrderExpiry(exp)}
                      className={`py-1.5 text-xs font-mono rounded border transition-colors cursor-pointer ${
                        orderExpiry === exp
                          ? 'bg-orange-500/15 border-orange-500/40 text-orange-300 font-semibold'
                          : 'bg-[#0B101A] border-[#232F46] text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      {exp}
                    </button>
                  ))}
                </div>
              </div>

              {/* Entry Timing */}
              <div>
                <span className="text-[11px] text-slate-400 block">Entry Timing</span>
                <div className="mt-1 grid grid-cols-1 gap-1">
                  <button
                    type="button"
                    onClick={() => setEntryMode('immediate')}
                    className={`px-2.5 py-1.5 text-xs rounded border text-left transition-colors cursor-pointer ${
                      entryMode === 'immediate'
                        ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300 font-medium'
                        : 'bg-[#0B101A] border-[#232F46] text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Now
                  </button>
                  <button
                    type="button"
                    onClick={() => setEntryMode('future')}
                    className={`px-2.5 py-1.5 text-xs rounded border text-left transition-colors cursor-pointer ${
                      entryMode === 'future'
                        ? 'bg-sky-500/15 border-sky-500/40 text-sky-300 font-medium'
                        : 'bg-[#0B101A] border-[#232F46] text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Schedule
                  </button>
                  {entryMode === 'future' && (
                    <label className="flex items-center gap-2 text-xs text-slate-400">
                      Minutes ahead
                      <input type="number" min={1} max={60} value={Math.max(1, leadMinutes)}
                        onChange={(event) => onUpdateConfig({
                          ...config,
                          signal_lead_minutes: Math.max(1, Math.min(60, Math.floor(Number(event.target.value) || 1))),
                        })}
                        className="w-16 rounded border border-[#232F46] bg-[#0B101A] px-2 py-1 text-slate-200" />
                    </label>
                  )}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 pt-1">
              <button
                type="button"
                disabled={!quotexConnected}
                onClick={() => submitPaperTrade('CALL')}
                title={!quotexConnected ? 'Connect Quotex Login to place live-trend paper trades' : undefined}
                className="py-2.5 px-4 inline-flex items-center justify-center gap-2 text-xs font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <ArrowUpRight className="w-4 h-4" />
                <span>
                  Paper CALL ▲ · ${orderStake} ({orderExpiry})
                </span>
              </button>
              <button
                type="button"
                disabled={!quotexConnected}
                onClick={() => submitPaperTrade('PUT')}
                title={!quotexConnected ? 'Connect Quotex Login to place live-trend paper trades' : undefined}
                className="py-2.5 px-4 inline-flex items-center justify-center gap-2 text-xs font-semibold rounded-lg bg-rose-600 hover:bg-rose-500 text-white transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <ArrowDownRight className="w-4 h-4" />
                <span>
                  Paper PUT ▼ · ${orderStake} ({orderExpiry})
                </span>
              </button>
            </div>
          </div>

          {/* Right: Future Signal Lead Time + Paper Balance Setting */}
          <div className="lg:col-span-4 border border-[#1A2334] rounded-lg bg-[#0F1523] p-5 space-y-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs font-medium text-slate-200">Starting Paper Capital</div>
                </div>
              <div className="flex items-center gap-1">
                <span className="text-xs font-mono text-slate-400">$</span>
                <input
                  type="number"
                  min={50}
                  max={1000000}
                  step={100}
                  value={startingBalance}
                  onChange={(e) =>
                    onUpdateConfig({
                      ...config,
                      paper_starting_balance: Math.max(50, Number(e.target.value) || 1000),
                    })
                  }
                  className="w-24 bg-[#0B101A] border border-[#232F46] rounded px-2 py-1 text-xs font-mono text-right text-slate-100"
                />
              </div>
            </div>
          </div>
        </div>
      </Disclosure>

      <Disclosure label="Desktop alert settings">
        {/* Desktop Notifications Control Strip */}
        <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] px-4 py-3.5 flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="text-sm font-medium text-slate-100">Browser Desktop Notifications</div>
            <p className="text-xs text-slate-400 mt-0.5">{notificationStatusText}</p>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {notificationPermission === 'granted' && (
              <button
                type="button"
                onClick={onSendTestDesktopNotification}
                className="px-3 py-1.5 text-xs font-medium rounded-md bg-[#121928] border border-[#232F46] text-slate-200 hover:bg-[#1A2336] transition-colors whitespace-nowrap cursor-pointer"
              >
                Send Test
              </button>
            )}
            <button
              type="button"
              onClick={onToggleDesktopNotifications}
              disabled={notificationPermission === 'unsupported'}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-colors whitespace-nowrap cursor-pointer disabled:opacity-45 ${
                notificationsActive
                  ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300'
                  : 'bg-[#121928] border-[#232F46] text-slate-200 hover:bg-[#1A2336]'
              }`}
            >
              {notificationsActive ? (
                <Bell className="w-3.5 h-3.5" />
              ) : (
                <BellOff className="w-3.5 h-3.5" />
              )}
              <span>
                {notificationPermission === 'default'
                  ? 'Enable Alerts'
                  : notificationsActive
                  ? 'Enabled'
                  : 'Disabled'}
              </span>
            </button>
          </div>
        </div>
      </Disclosure>

      <Disclosure label="Filter trades">
        {/* Outcome & Direction Filter Controls + Pattern Summary */}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <div
              role="group"
              aria-label="Filter signals by outcome"
              className="inline-flex flex-wrap items-center p-1 rounded-lg bg-[#0F1523] border border-[#1A2334]"
            >
              {resultOptions.map((opt) => {
                const isActive = resultFilter === opt.value;
                const activeStyles =
                  opt.value === 'WIN'
                    ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/35'
                    : opt.value === 'LOSS'
                    ? 'bg-rose-500/15 text-rose-300 border-rose-500/35'
                    : opt.value === 'PENDING'
                    ? 'bg-sky-500/15 text-sky-300 border-sky-500/35'
                    : opt.value === 'ACTIVE'
                    ? 'bg-amber-500/15 text-amber-300 border-amber-500/35'
                    : 'bg-orange-500/15 text-orange-300 border-orange-500/35';

                return (
                  <button
                    key={opt.value}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => setResultFilter(opt.value)}
                    className={`px-2.5 py-1.5 text-xs font-medium rounded-md border transition-colors whitespace-nowrap shrink-0 cursor-pointer inline-flex items-center gap-1.5 ${
                      isActive
                        ? activeStyles
                        : 'border-transparent text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <span>{opt.label}</span>
                    <span className="font-mono tabular-nums text-[11px] opacity-80">
                      {opt.count}
                    </span>
                  </button>
                );
              })}
            </div>

            <div
              role="group"
              aria-label="Filter signals by direction"
              className="inline-flex items-center p-1 rounded-lg bg-[#0F1523] border border-[#1A2334]"
            >
              {(['ALL', 'CALL', 'PUT'] as DirectionFilter[]).map((dir) => (
                <button
                  key={dir}
                  type="button"
                  aria-pressed={directionFilter === dir}
                  onClick={() => setDirectionFilter(dir)}
                  className={`px-2.5 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                    directionFilter === dir
                      ? 'bg-[#1A2336] text-slate-100'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {dir === 'ALL' ? 'All Directions' : dir}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400 font-mono tabular-nums">
            <span>
              Showing <span className="text-slate-200">{filteredStats.count}</span>
            </span>
            <span aria-hidden="true" className="text-slate-700">·</span>
            <span>
              Subset P&L{' '}
              <span
                className={
                  filteredStats.pnl > 0
                    ? 'text-emerald-400'
                    : filteredStats.pnl < 0
                    ? 'text-rose-400'
                    : 'text-slate-200'
                }
              >
                {filteredStats.pnl >= 0 ? '+' : ''}${filteredStats.pnl.toFixed(2)}
              </span>
            </span>
            <span aria-hidden="true" className="text-slate-700">·</span>
            <span>
              Avg Conf <span className="text-slate-200">{filteredStats.avgConf.toFixed(1)}%</span>
            </span>
            <span aria-hidden="true" className="text-slate-700">·</span>
            <span className="font-sans">
              Top Strategy: <span className="text-slate-200">{filteredStats.dominantStrategy}</span>
            </span>
          </div>
        </div>
      </Disclosure>

      {/* Signals & Paper Trades Table */}
      <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-[#1A2334] bg-[#121928] text-[11px] font-medium text-slate-400">
                <th className="py-3 px-4">Entry → Expiry</th>
                <th className="py-3 px-4">Asset</th>
                <th className="py-3 px-4">Direction</th>
                <th className="py-3 px-4 text-right">Entry Price</th>
                <th className="py-3 px-4 text-right">Live / Exit</th>
                <th className="py-3 px-4 text-right">Stake</th>
                <th className="py-3 px-4">Strategy / Source</th>
                <th className="py-3 px-4 text-right">Status / P&L</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#172033] text-xs font-mono tabular-nums">
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center font-sans text-slate-400">
                    {signalLogs.length === 0 ? (
                      'No manual paper trades yet.'
                    ) : (
                      <div className="space-y-2">
                        <div>
                          No {resultFilter === 'ALL' ? '' : `${resultFilter} `}
                          {directionFilter === 'ALL' ? '' : `${directionFilter} `}
                          signals match the active filter.
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            setResultFilter('ALL');
                            setDirectionFilter('ALL');
                          }}
                          className="px-3 py-1.5 text-xs font-medium rounded-md bg-[#161F31] border border-[#25324B] text-slate-200 hover:bg-[#1E2A40] transition-colors cursor-pointer"
                        >
                          Show All Signals
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ) : (
                filtered.map((log) => {
                  const dec = log.decimals ?? assets[log.symbol]?.decimals ?? 5;
                  const entryAt = log.entryAt || log.createdAt;
                  const remToEntry = Math.max(0, entryAt - nowSec);
                  const remToExpiry = Math.max(0, log.expiresAt - nowSec);

                  const liveMark =
                    log.status === 'ACTIVE'
                      ? log.currentPrice ??
                        (analysisData?.feed.symbol === log.symbol
                          ? analysisData.analysis.price
                          : scanResults.find((p) => p.symbol === log.symbol)?.price) ??
                        log.entryPrice
                      : log.exitPrice;

                  let liveBadge: { label: string; cls: string } | null = null;
                  if (log.status === 'ACTIVE' && liveMark !== undefined && log.entryPrice > 0) {
                    const diff = liveMark - log.entryPrice;
                    if (Math.abs(diff) <= 1e-9) {
                      liveBadge = { label: 'TIE', cls: 'text-slate-300' };
                    } else {
                      const inMoney = log.signal === 'CALL' ? diff > 0 : diff < 0;
                      liveBadge = inMoney
                        ? { label: 'ITM', cls: 'text-emerald-400' }
                        : { label: 'OTM', cls: 'text-rose-400' };
                    }
                  }

                  return (
                    <tr key={log.id} className="hover:bg-[#141D2F]">
                      <td className="py-3 px-4 text-slate-300 whitespace-nowrap">
                        <div>
                          {formatClockTime(entryAt)}{' '}
                          <span className="text-slate-600">→</span>{' '}
                          {formatClockTime(log.expiresAt)}
                        </div>
                        <div className="text-[10px] text-slate-500">
                          Alert {formatClockTime(log.createdAt)} · {Math.round(log.expirySec)}s
                        </div>
                      </td>
                      <td className="py-3 px-4 font-sans font-medium text-slate-100 whitespace-nowrap">
                        <div>{assets[log.symbol]?.name ?? log.symbol}</div>
                        <div className="text-[11px] font-mono text-slate-500">
                          {log.timeframe} · {log.confidence.toFixed(0)}% · {log.payout}%
                        </div>
                      </td>
                      <td
                        className={`py-3 px-4 font-semibold whitespace-nowrap ${
                          log.signal === 'CALL' ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        {log.signal} {log.signal === 'CALL' ? '▲' : '▼'}
                      </td>
                      <td className="py-3 px-4 text-right text-slate-200 whitespace-nowrap">
                        {log.status === 'PENDING' ? (
                          <span className="text-slate-500">Locks at entry</span>
                        ) : (
                          formatPrice(log.entryPrice, dec)
                        )}
                      </td>
                      <td className="py-3 px-4 text-right whitespace-nowrap">
                        {log.status === 'PENDING' ? (
                          <span className="text-slate-500">—</span>
                        ) : log.status === 'ACTIVE' ? (
                          <span className={liveBadge?.cls ?? 'text-slate-300'}>
                            {formatPrice(liveMark, dec)}
                            {liveBadge && (
                              <span className="ml-1.5 text-[10px] font-semibold">
                                {liveBadge.label}
                              </span>
                            )}
                          </span>
                        ) : (
                          <span className="text-slate-300">{formatPrice(log.exitPrice, dec)}</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-right text-slate-200 whitespace-nowrap">
                        ${log.stake.toFixed(2)}
                      </td>
                      <td className="py-3 px-4 font-sans text-slate-400 max-w-[200px] truncate">
                        {log.topStrategy}
                      </td>
                      <td className="py-3 px-4 text-right font-semibold whitespace-nowrap">
                        {log.status === 'PENDING' ? (
                          <div className="inline-flex items-center gap-2">
                            <span className="text-sky-400">
                              Enters in {formatRemainingCountdown(remToEntry)}
                            </span>
                            <button
                              type="button"
                              onClick={() => onCancelPendingTrade(log.id)}
                              title="Cancel scheduled paper trade"
                              className="p-0.5 rounded text-slate-500 hover:text-rose-400 cursor-pointer"
                            >
                              <X className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ) : log.status === 'ACTIVE' ? (
                          <span className="text-amber-400">
                            {remToExpiry > 0
                              ? `${formatRemainingCountdown(remToExpiry)} left`
                              : 'Settling...'}
                          </span>
                        ) : log.status === 'TIE' ? (
                          <span className="text-slate-300">TIE ($0.00 refunded)</span>
                        ) : (
                          <span className={log.status === 'WIN' ? 'text-emerald-400' : 'text-rose-400'}>
                            {log.status} ({log.pnl >= 0 ? '+' : ''}${log.pnl.toFixed(2)})
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
