import { useStubayAlerts } from './hooks/useStubayAlerts';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Bell,
  BellOff,
  Columns2,
  Bot,
  FileText,
  Grid2x2,
  Globe,
  Menu,
  MonitorDown,
  Play,
  Power,
  Send,
  Settings,
  Square,
  X,
} from 'lucide-react';
import { LiveChartModule } from './components/LiveChartModule';
import { MarketsModule, type CustomAssetInput } from './components/MarketsModule';
import { LogsModule } from './components/LogsModule';
import { ConnectionModule } from './components/ConnectionModule';
import { QuotexBrowserModule } from './components/QuotexBrowserModule';
import { WindowsAppModule } from './components/WindowsAppModule';
import { TelegramModule } from './components/TelegramModule';
import { StubayDashboard } from './components/StubayDashboard';
import { OfflineIndicator } from './components/OfflineIndicator';
import type {
  AnalysisResponse,
  AssetMeta,
  BotConfig,
  EngineLogEntry,
  NotificationPermissionState,
  QuotexLiveStatus,
  ScanPairResult,
} from './types/trading';

type ModuleId =
  | 'live-chart'
  | 'markets'
  | 'paper-lab'
  | 'telegram'
  | 'logs'
  | 'connection'
  | 'quotex-browser'
  | 'windows-app';

const VALID_MODULES: ModuleId[] = [
  'live-chart',
  'markets',
  'paper-lab',
  'telegram',
  'logs',
  'connection',
  'quotex-browser',
  'windows-app',
];

const DEFAULT_CONFIG: BotConfig = {
  min_confidence: 62,
  rsi_oversold: 30,
  rsi_overbought: 70,
  bb_period: 20,
  bb_std: 2.0,
  ema_fast: 9,
  ema_slow: 21,
  adx_threshold: 20,
  base_stake: 10,
  risk_mode: 'martingale',
  martingale_multiplier: 2.2,
  max_martingale_steps: 2,
  enabled_strategies: {},
  strategy_weights: {},
  telegram_bot_token: '',
  telegram_chat_id: '',
  telegram_auto_send: true,
  telegram_min_confidence: 60,
  signal_lead_minutes: 0,
  paper_starting_balance: 1000,
  analyzed_assets: {},
  notify_assets: {},
  desktop_notifications: true,
  quotex_ssid: '',
  quotex_cookie: '',
  quotex_user_agent: '',
  account_type: 'DEMO',
  auto_trade: false,
};

const STORAGE_KEY = 'quotex_python_bot_config_v2';

function readInitialModule(): ModuleId {
  const hash = window.location.hash.replace('#', '');
  if (hash === 'stubay-settings') return 'telegram';
  if (['ai-analysis', 'signals'].includes(hash)) return 'paper-lab';
  return VALID_MODULES.find((id) => id === hash) ?? 'live-chart';
}

const CHART_REQUEST_TIMEOUT_MS = 10_000;
const CHART_LOAD_ERROR = 'The chart did not load. Retry to request fresh candles.';

export default function App() {
  const [activeModule, setActiveModule] = useState<ModuleId>(readInitialModule);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const [config, setConfig] = useState<BotConfig>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const previous = JSON.parse(saved);
        const savedConfig = Object.fromEntries(
          Object.keys(DEFAULT_CONFIG).filter((key) => Object.hasOwn(previous, key)).map((key) => [key, previous[key]])
        );
        if (savedConfig.telegram_auto_send === undefined) {
          savedConfig.telegram_auto_send = true;
        }
        return { ...DEFAULT_CONFIG, ...savedConfig };
      }
    } catch {
      // ignore storage errors
    }
    return DEFAULT_CONFIG;
  });

  const [assets, setAssets] = useState<Record<string, AssetMeta>>({});
  const [selectedSymbol, setSelectedSymbol] = useState('EURUSD-OTC');
  // Engine timeframe drives Markets scanner, Strategy, AI Analysis, Signals, Paper Trades, Performance & Telegram
  const [timeframe] = useState('1m');
  // Isolated Chart View Timer — ONLY changes the Live Chart candlestick view, never affects other modules
  const [chartTimeframe, setChartTimeframe] = useState('1m');

  const [analysisData, setAnalysisData] = useState<AnalysisResponse | null>(null);
  const [chartViewData, setChartViewData] = useState<AnalysisResponse | null>(null);
  const [scanResults, setScanResults] = useState<ScanPairResult[]>([]);
  const [quotexStatus, setQuotexStatus] = useState<QuotexLiveStatus | null>(null);
  const [isChartLoading, setIsChartLoading] = useState(false);
  const [isChartViewLoading, setIsChartViewLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);
  const [chartViewError, setChartViewError] = useState<string | null>(null);
  const [isScanLoading, setIsScanLoading] = useState(false);
  const [isConnected, setIsConnected] = useState(true);
  const [isBotRunning, setIsBotRunning] = useState(true);

  const [engineLogs, setEngineLogs] = useState<EngineLogEntry[]>([]);
  const [notificationPermission, setNotificationPermission] =
    useState<NotificationPermissionState>(() =>
      typeof window !== 'undefined' && 'Notification' in window
        ? window.Notification.permission
        : 'unsupported'
    );

  const configRef = useRef(config);
  const selectedSymbolRef = useRef(selectedSymbol);
  const timeframeRef = useRef(timeframe);
  const chartTimeframeRef = useRef(chartTimeframe);
  const chartCacheRef = useRef<Map<string, AnalysisResponse>>(new Map());
  const chartAbortRef = useRef<AbortController | null>(null);
  const chartReqIdRef = useRef(0);
  const chartInFlightRef = useRef(false);
  const chartViewAbortRef = useRef<AbortController | null>(null);
  const chartViewReqIdRef = useRef(0);
  const chartViewInFlightRef = useRef(false);
  const scanInFlightRef = useRef(false);
  const lastScanAlertedRef = useRef<Map<string, number>>(new Map());
  const strategyInitRef = useRef(false);
  const analyzedInitRef = useRef(false);


  useEffect(() => {
    configRef.current = config;
  }, [config]);

  useEffect(() => {
    selectedSymbolRef.current = selectedSymbol;
  }, [selectedSymbol]);

  useEffect(() => {
    timeframeRef.current = timeframe;
  }, [timeframe]);

  useEffect(() => {
    chartTimeframeRef.current = chartTimeframe;
  }, [chartTimeframe]);





  const appendLog = useCallback(
    (
      level: EngineLogEntry['level'],
      module: EngineLogEntry['module'],
      message: string
    ) => {
      const entry: EngineLogEntry = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        timestamp: Date.now(),
        level,
        module,
        message,
      };
      setEngineLogs((prev) => [entry, ...prev].slice(0, 200));
    },
    []
  );

  // Sync URL hash navigation
  const selectModule = useCallback((id: ModuleId) => {
    setActiveModule(id);
    if (window.location.hash !== `#${id}`) {
      window.history.replaceState(null, '', `#${id}`);
    }
  }, []);

  useEffect(() => {
    const onHashChange = () => {
      const hash = window.location.hash.replace('#', '');
      if (hash === 'stubay-settings') selectModule('telegram');
      else if (['ai-analysis', 'signals'].includes(hash)) setActiveModule('paper-lab');
      else {
        const module = VALID_MODULES.find((id) => id === hash);
        if (module) setActiveModule(module);
      }
    };
    window.addEventListener('hashchange', onHashChange);
    if (window.location.hash === '#stubay-settings') selectModule('telegram');
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [selectModule]);

  const openStubayAlert = useCallback(() => selectModule('paper-lab'), [selectModule]);
  const reportStubayAlertError = useCallback(() => {
    appendLog('WARN', 'Signals', 'Browser could not display Stubay Ai desktop alerts.');
  }, [appendLog]);
  useStubayAlerts(
    notificationPermission === 'granted' && config.desktop_notifications,
    openStubayAlert,
    reportStubayAlertError,
  );

  const handleToggleDesktopNotifications = useCallback(async () => {
    if (typeof window === 'undefined' || !('Notification' in window)) {
      setNotificationPermission('unsupported');
      appendLog('WARN', 'Signals', 'Desktop notifications are not supported by this browser.');
      return;
    }

    const currentPerm = window.Notification.permission;
    setNotificationPermission(currentPerm);

    if (currentPerm === 'denied') {
      appendLog(
        'WARN',
        'Signals',
        'Desktop notifications are blocked. Allow notifications in browser site settings.'
      );
      return;
    }

    if (currentPerm === 'default') {
      try {
        const result = await window.Notification.requestPermission();
        setNotificationPermission(result);
        if (result === 'granted') {
          setConfig((prev) => ({ ...prev, desktop_notifications: true }));
          appendLog(
            'INFO',
            'Signals',
            'Desktop alerts enabled for new Stubay Ai paper signals.'
          );
        } else {
          appendLog('WARN', 'Signals', 'Desktop notification permission was not granted.');
        }
      } catch {
        appendLog('WARN', 'Signals', 'Unable to request desktop notification permission in this context.');
      }
      return;
    }

    setConfig((prev) => {
      const next = !prev.desktop_notifications;
      appendLog(
        'INFO',
        'Signals',
        next ? 'Desktop notifications resumed.' : 'Desktop notifications paused.'
      );
      return { ...prev, desktop_notifications: next };
    });
  }, [appendLog]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
    } catch {
      // ignore storage errors
    }
  }, [config]);

  const loadCatalog = useCallback(() => {
    fetch('/api/catalog')
      .then((r) => r.json())
      .then((d) => {
        if (d.assets) setAssets(d.assets);
        if (d.quotex_status) setQuotexStatus(d.quotex_status as QuotexLiveStatus);
        appendLog(
          'INFO',
          'Engine',
          `Loaded ${d.strategies?.length ?? 11} Python strategies across ${Object.keys(d.assets ?? {}).length} market pairs.`
        );
      })
      .catch(() => {
        appendLog('ERROR', 'Engine', 'Failed to load Python strategy catalog.');
      });
  }, [appendLog]);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  const strategyFingerprint = useMemo(
    () =>
      JSON.stringify({
        min_confidence: config.min_confidence,
        rsi_oversold: config.rsi_oversold,
        rsi_overbought: config.rsi_overbought,
        bb_period: config.bb_period,
        bb_std: config.bb_std,
        ema_fast: config.ema_fast,
        ema_slow: config.ema_slow,
        adx_threshold: config.adx_threshold,
        enabled_strategies: config.enabled_strategies,
        strategy_weights: config.strategy_weights,
        base_stake: config.base_stake,
        risk_mode: config.risk_mode,
        martingale_multiplier: config.martingale_multiplier,
        max_martingale_steps: config.max_martingale_steps,
      }),
    [
      config.min_confidence,
      config.rsi_oversold,
      config.rsi_overbought,
      config.bb_period,
      config.bb_std,
      config.ema_fast,
      config.ema_slow,
      config.adx_threshold,
      config.enabled_strategies,
      config.strategy_weights,
      config.base_stake,
      config.risk_mode,
      config.martingale_multiplier,
      config.max_martingale_steps,
    ]
  );

  const analyzedFingerprint = useMemo(
    () => JSON.stringify(config.analyzed_assets ?? {}),
    [config.analyzed_assets]
  );

  const processSignalAndTrade = useCallback(
    (pair: ScanPairResult, tf: string) => {
      const isCallOrPut = pair.signal === 'CALL' || pair.signal === 'PUT';
      if (!isCallOrPut) return;

      const minConf = Math.min(
        configRef.current.min_confidence ?? 60,
        configRef.current.telegram_min_confidence ?? 60
      );
      const meetsConfidence = pair.confidence >= minConf;
      // Trigger if qualified by consensus or if directional confidence meets the minimum threshold
      const shouldTrigger = pair.qualified || meetsConfidence;
      if (!shouldTrigger) return;

      const nowSec = Math.floor(Date.now() / 1000);
      const candleTs = pair.latest_bar_time && pair.latest_bar_time > 0
        ? pair.latest_bar_time
        : Math.floor(nowSec / 60) * 60;

      const lastAlerted = lastScanAlertedRef.current.get(pair.symbol) ?? 0;
      if (candleTs === lastAlerted) return;

      // User requirement: Invert detected signal for Telegram notification & Paper trade
      // System detects PUT -> execute CALL | System detects CALL -> execute PUT
      const invertedSignal: 'CALL' | 'PUT' = pair.signal === 'PUT' ? 'CALL' : 'PUT';

      appendLog(
        'INFO',
        'Signals',
        `⚡ [SIGNAL DETECTED] ${pair.name} · System Detected ${pair.signal} ➔ Executing Inverted Trade: ${invertedSignal} (${pair.confidence}% confidence) | Exp: ${pair.recommended_expiry}`
      );

      // 1. Desktop Notification
      if (
        configRef.current.desktop_notifications &&
        typeof window !== 'undefined' &&
        'Notification' in window &&
        window.Notification.permission === 'granted'
      ) {
        try {
          const notif = new window.Notification(`Quotex Signal: ${pair.name} · ${invertedSignal}`, {
            body: `${invertedSignal} (System detected ${pair.signal}, ${pair.confidence}%)\nExpiry: ${pair.recommended_expiry}\nRule: Enter within 0–5s of candle open!`,
            tag: `sig-${pair.symbol}-${candleTs}`,
          });
          notif.onclick = () => {
            window.focus();
            setSelectedSymbol(pair.symbol);
            setActiveModule('live-chart');
            notif.close();
          };
        } catch {
          // Ignore notification error
        }
      }

      // 2. Real-Time Telegram Dispatch & Automatic Paper Trade Placement
      const autoSendEnabled = configRef.current.telegram_auto_send !== false;
      const notifyEnabled = configRef.current.notify_assets?.[pair.symbol] ?? true;
      if (autoSendEnabled && notifyEnabled) {
        lastScanAlertedRef.current.set(pair.symbol, candleTs);
        void fetch('/api/telegram/signal', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            bot_token: configRef.current.telegram_bot_token || undefined,
            chat_id: configRef.current.telegram_chat_id || undefined,
            symbol: pair.symbol,
            name: pair.name,
            timeframe: tf,
            signal: invertedSignal,
            detected_signal: pair.signal,
            confidence: pair.confidence,
            min_confidence: minConf,
            price: pair.price,
            decimals: pair.decimals,
            payout: pair.payout,
            expiry: pair.recommended_expiry,
            feed_source: pair.feed_source,
            auto_triggered: true,
            signal_ts: candleTs,
            reason_lines: pair.top_reasons,
            qualified: true,
            allow_simulated: true,
          }),
        })
          .then((r) => r.json())
          .then((tgRes) => {
            if (tgRes?.ok) {
              const tradeId = tgRes.paper_trade_id || (tgRes.paper_trade?.trade_id) || 'OPEN';
              appendLog(
                'INFO',
                'Telegram',
                `🚀 [SIGNAL SENT & TRADE PLACED] ${pair.name} · ${invertedSignal} (Detected: ${pair.signal}, ${pair.confidence}%) -> Dispatched to Telegram with chart screenshot & Paper Trade opened (Trade ID: ${tradeId}).`
              );
            } else if (tgRes?.error) {
              appendLog('WARN', 'Telegram', `Telegram auto-dispatch: ${tgRes.error}`);
            }
          })
          .catch((err) => {
            appendLog('ERROR', 'Telegram', `Telegram dispatch error: ${String(err)}`);
          });
      }
    },
    [appendLog]
  );

  const fetchActiveChart = useCallback(
    async (
      sym: string,
      tf: string,
      cfg: BotConfig,
      options?: { silent?: boolean; forceFresh?: boolean }
    ) => {
      const silent = options?.silent === true;
      const forceFresh = options?.forceFresh === true;
      const cacheKey = `${sym}:${tf}`;

      // 0ms instant switch when switching back to a recently viewed pair/timeframe
      if (!silent && !forceFresh) {
        const cached = chartCacheRef.current.get(cacheKey);
        if (cached) {
          setAnalysisData(cached);
        }
      }

      // Cancel any previous superseded chart request (prevents lag & stale overwrites on rapid clicks)
      if (chartAbortRef.current) {
        chartAbortRef.current.abort();
      }
      const controller = new AbortController();
      chartAbortRef.current = controller;
      const reqId = ++chartReqIdRef.current;

      chartInFlightRef.current = true;
      if (!silent) {
        setIsChartLoading(true);
        setChartError(null);
      }
      const timeout = AbortSignal.timeout(CHART_REQUEST_TIMEOUT_MS);

      try {
        const resp = await fetch('/api/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            symbol: sym,
            timeframe: tf,
            config: cfg,
            force_refresh: forceFresh,
          }),
          signal: AbortSignal.any([controller.signal, timeout]),
        });
        if (!resp.ok) throw new Error(CHART_LOAD_ERROR);
        const analyzeRes = await resp.json();

        // Discard stale out-of-order response if user already clicked another asset/timeframe
        if (reqId !== chartReqIdRef.current) return;
        const requestedTimeframe = tf;
        if (analyzeRes?.feed?.symbol !== sym || analyzeRes?.feed?.timeframe !== requestedTimeframe || !analyzeRes?.analysis) {
          throw new Error(CHART_LOAD_ERROR);
        }
        setChartError(null);


        if (analyzeRes?.feed && analyzeRes?.analysis) {
          const typed = analyzeRes as AnalysisResponse;
          // Maintain a bounded 40-entry client cache for instant back-and-forth switching
          chartCacheRef.current.delete(cacheKey);
          chartCacheRef.current.set(cacheKey, typed);
          if (chartCacheRef.current.size > 40) {
            const oldestKey = chartCacheRef.current.keys().next().value;
            if (oldestKey) chartCacheRef.current.delete(oldestKey);
          }

          setAnalysisData(typed);
          if (typed.quotex_status) setQuotexStatus(typed.quotex_status);
          setIsConnected(true);

          // Real-time signal processing directly from active chart analysis
          const latestCandle = typed.feed.candles[typed.feed.candles.length - 1];
          const topReasons = typed.analysis.votes
            .filter((v) => v.signal === typed.analysis.signal)
            .slice(0, 3)
            .map((v) => `${v.name}: ${v.reason}`);
          const activePairForTrade: ScanPairResult = {
            symbol: sym,
            name: typed.feed.name || sym,
            category: typed.feed.category || 'OTC Forex',
            payout: typed.feed.payout || 85,
            decimals: typed.feed.decimals || 5,
            feed_source: typed.feed.feed_source,
            is_live: typed.feed.is_live,
            live_required: typed.feed.live_required,
            analyzed: true,
            custom: false,
            price: typed.analysis.price,
            change_24h: null,
            payout_1m: null,
            payout_5m: null,
            signal: typed.analysis.signal,
            confidence: typed.analysis.confidence,
            qualified: typed.analysis.qualified,
            qualification_reason: typed.analysis.qualification_reason,
            call_count: typed.analysis.call_count,
            put_count: typed.analysis.put_count,
            neutral_count: 0,
            recommended_expiry: typed.analysis.recommended_expiry,
            volatility_regime: typed.analysis.volatility_regime,
            latest_bar_time: latestCandle?.time ?? Math.floor(Date.now() / 1000),
            top_reasons: topReasons,
          };
          processSignalAndTrade(activePairForTrade, tf);

          // Sync the active pair's row inside Markets / dropdown
          setScanResults((prev) => {
            if (prev.length === 0) return prev;
            const idx = prev.findIndex((p) => p.symbol === sym);
            if (idx === -1) return prev;
            const existing = prev[idx];
            const updated: ScanPairResult = {
              ...existing,
              price: typed.analysis.price,
              signal: typed.analysis.signal,
              confidence: typed.analysis.confidence,
              qualified: typed.analysis.qualified,
              qualification_reason: typed.analysis.qualification_reason,
              call_count: typed.analysis.call_count,
              put_count: typed.analysis.put_count,
              recommended_expiry: typed.analysis.recommended_expiry,
              volatility_regime: typed.analysis.volatility_regime,
              feed_source: typed.feed.feed_source,
              is_live: typed.feed.is_live,
              live_required: typed.feed.live_required,
              latest_bar_time: latestCandle?.time ?? existing.latest_bar_time,
              top_reasons: topReasons.length > 0 ? topReasons : existing.top_reasons,
            };
            const next = prev.slice();
            next[idx] = updated;
            return next;
          });

        }
      } catch {
        if (controller.signal.aborted) return;
        if (reqId === chartReqIdRef.current && !silent) {
          setChartError(timeout.aborted ? 'The chart request timed out. Retry to load fresh candles.' : CHART_LOAD_ERROR);
          appendLog('ERROR', 'Engine', `Quotex feed update failed for ${sym} (${tf}).`);
        }
      } finally {
        if (reqId === chartReqIdRef.current) {
          chartInFlightRef.current = false;
          setIsChartLoading(false);
        }
      }
    },
    [appendLog, processSignalAndTrade]
  );

  // Isolated chart-view-only fetch when the chart timer differs from the engine timeframe.
  // Purely updates chartViewData for rendering — NEVER affects Signals, Markets, Strategy, Telegram, AI, or Paper Trades.
  const fetchChartViewOnly = useCallback(
    async (
      sym: string,
      viewTf: string,
      cfg: BotConfig,
      options?: { silent?: boolean; forceFresh?: boolean }
    ) => {
      const silent = options?.silent === true;
      const forceFresh = options?.forceFresh === true;
      const cacheKey = `${sym}:${viewTf}`;

      if (!silent && !forceFresh) {
        const cached = chartCacheRef.current.get(cacheKey);
        if (cached) {
          setChartViewData(cached);
        }
      }

      if (chartViewAbortRef.current) {
        chartViewAbortRef.current.abort();
      }
      const controller = new AbortController();
      chartViewAbortRef.current = controller;
      const reqId = ++chartViewReqIdRef.current;

      chartViewInFlightRef.current = true;
      if (!silent) {
        setIsChartViewLoading(true);
        setChartViewError(null);
      }
      const timeout = AbortSignal.timeout(CHART_REQUEST_TIMEOUT_MS);

      try {
        const resp = await fetch('/api/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            symbol: sym,
            timeframe: viewTf,
            config: cfg,
            force_refresh: forceFresh,
          }),
          signal: AbortSignal.any([controller.signal, timeout]),
        });
        if (!resp.ok) throw new Error(CHART_LOAD_ERROR);
        const res = await resp.json();
        if (reqId !== chartViewReqIdRef.current) return;
        const requestedTimeframe = viewTf;
        if (res?.feed?.symbol !== sym || res?.feed?.timeframe !== requestedTimeframe || !res?.analysis) {
          throw new Error(CHART_LOAD_ERROR);
        }
        setChartViewError(null);

        if (res?.feed && res?.analysis) {
          const typed = res as AnalysisResponse;
          chartCacheRef.current.delete(cacheKey);
          chartCacheRef.current.set(cacheKey, typed);
          setChartViewData(typed);
        }
      } catch {
        if (controller.signal.aborted) return;
        if (reqId === chartViewReqIdRef.current && !silent) {
          setChartViewError(timeout.aborted ? 'The chart request timed out. Retry to load fresh candles.' : CHART_LOAD_ERROR);
        }
      } finally {
        if (reqId === chartViewReqIdRef.current) {
          chartViewInFlightRef.current = false;
          setIsChartViewLoading(false);
        }
      }
    },
    []
  );

  const fetchMarketScan = useCallback(
    async (sym: string, tf: string, cfg: BotConfig) => {
      // Strict single-flight guard: never allow overlapping pair scans to pile up
      if (scanInFlightRef.current) return;
      scanInFlightRef.current = true;
      setIsScanLoading(true);

      try {
        const resp = await fetch('/api/scan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ timeframe: tf, config: { ...cfg, analyzed_assets: cfg.analyzed_assets || {} }, active_symbol: sym }),
        });
        const scanRes = await resp.json();
        if (scanRes?.quotex_status) {
          setQuotexStatus(scanRes.quotex_status as QuotexLiveStatus);
        }

        if (Array.isArray(scanRes?.pairs)) {
          const pairs = scanRes.pairs as ScanPairResult[];
          setScanResults(pairs);

          // Real-time automatic signal detection across the continuous scanner pool
          for (const pair of pairs) {
            processSignalAndTrade(pair, tf);
          }
        }
      } catch {
        appendLog('ERROR', 'Engine', `Background Quotex market scan failed (${tf}).`);
      } finally {
        scanInFlightRef.current = false;
        setIsScanLoading(false);
      }
    },
    [appendLog, processSignalAndTrade]
  );

  // 1. Instant active engine analysis when selected pair, engine timeframe, or connection changes
  useEffect(() => {
    if (!isConnected) return;
    void fetchActiveChart(selectedSymbol, timeframe, configRef.current);
  }, [selectedSymbol, timeframe, isConnected, fetchActiveChart]);

  // 1b. Isolated chart-view fetch ONLY when the Live Chart timer differs from engine timeframe
  useEffect(() => {
    if (!isConnected) return;
    if (chartTimeframe === timeframe) {
      setChartViewData(null);
      return;
    }
    void fetchChartViewOnly(selectedSymbol, chartTimeframe, configRef.current);
  }, [selectedSymbol, chartTimeframe, timeframe, isConnected, fetchChartViewOnly]);

  // 2. Background market scan on initial connect or engine timeframe switch (NEVER triggered by chart view timer!)
  useEffect(() => {
    if (!isConnected) return;
    const timer = setTimeout(() => {
      void fetchMarketScan(selectedSymbolRef.current, timeframe, configRef.current);
    }, 250);
    return () => clearTimeout(timer);
  }, [timeframe, isConnected, fetchMarketScan]);

  // 3. Debounced recalculation when strategy/risk parameters change
  useEffect(() => {
    if (!strategyInitRef.current) {
      strategyInitRef.current = true;
      return;
    }
    if (!isConnected) return;
    const timer = setTimeout(() => {
      void fetchActiveChart(selectedSymbolRef.current, timeframeRef.current, configRef.current, {
        forceFresh: true,
      });
      if (chartTimeframeRef.current !== timeframeRef.current) {
        void fetchChartViewOnly(selectedSymbolRef.current, chartTimeframeRef.current, configRef.current, {
          forceFresh: true,
        });
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [strategyFingerprint, isConnected, fetchActiveChart, fetchChartViewOnly]);

  // 4. Debounced background scan update when per-asset Analyze toggles change
  useEffect(() => {
    if (!analyzedInitRef.current) {
      analyzedInitRef.current = true;
      return;
    }
    if (!isConnected) return;
    const timer = setTimeout(() => {
      void fetchMarketScan(selectedSymbolRef.current, timeframeRef.current, configRef.current);
    }, 450);
    return () => clearTimeout(timer);
  }, [analyzedFingerprint, isConnected, fetchMarketScan]);

  // 5. Fast live engine tick polling (drives Signals, Strategy, Paper Trades, Telegram)
  useEffect(() => {
    if (!isConnected || !isBotRunning) return;
    const isSubMinute = timeframe === '5s' || timeframe === '10s' || timeframe === '15s' || timeframe === '30s';
    const pollMs = isSubMinute ? 2000 : 3000;
    const id = setInterval(() => {
      if (!chartInFlightRef.current) {
        void fetchActiveChart(selectedSymbol, timeframe, configRef.current, { silent: true });
      }
    }, pollMs);
    return () => clearInterval(id);
  }, [isConnected, isBotRunning, selectedSymbol, timeframe, fetchActiveChart]);

  // 5b. Isolated live chart-view tick polling ONLY when viewing a different chart timer in Live Chart
  useEffect(() => {
    if (!isConnected || !isBotRunning || activeModule !== 'live-chart') return;
    if (chartTimeframe === timeframe) return;
    const isSubMinute =
      chartTimeframe === '5s' ||
      chartTimeframe === '10s' ||
      chartTimeframe === '15s' ||
      chartTimeframe === '30s';
    const pollMs = isSubMinute ? 2000 : 3000;
    const id = setInterval(() => {
      if (!chartViewInFlightRef.current) {
        void fetchChartViewOnly(selectedSymbol, chartTimeframe, configRef.current, { silent: true });
      }
    }, pollMs);
    return () => clearInterval(id);
  }, [isConnected, isBotRunning, activeModule, selectedSymbol, chartTimeframe, timeframe, fetchChartViewOnly]);

  // 6. Continuous, adaptive market scanner (runs across all pairs continuously, not just on a timer or on the Markets tab)
  useEffect(() => {
    if (!isConnected || !isBotRunning) return;
    let cancelled = false;
    let timerId: ReturnType<typeof setTimeout> | undefined;

    const runContinuousScan = async () => {
      if (cancelled) return;
      if (!scanInFlightRef.current) {
        await fetchMarketScan(selectedSymbolRef.current, timeframeRef.current, configRef.current);
      }
      if (cancelled) return;

      // Adaptive high-frequency pacing:
      // Near candle boundary (:55 - :05), scan at 500ms cadence to catch fresh candle boundaries immediately.
      // Mid-candle (:06 - :54), stream continuously every 1200ms.
      const currentSecond = new Date().getSeconds();
      const isCandleBoundary = currentSecond >= 55 || currentSecond <= 5;
      const nextDelayMs = isCandleBoundary ? 500 : 1200;

      timerId = setTimeout(runContinuousScan, nextDelayMs);
    };

    void runContinuousScan();

    return () => {
      cancelled = true;
      if (timerId) clearTimeout(timerId);
    };
  }, [isConnected, isBotRunning, timeframe, fetchMarketScan]);

  const handleToggleBot = () => {
    if (!isConnected) {
      setIsConnected(true);
      setIsBotRunning(true);
      appendLog('INFO', 'Engine', 'Scanner started and connected to market feed.');
      void fetchActiveChart(selectedSymbol, timeframe, configRef.current, { forceFresh: true });
      return;
    }
    setIsBotRunning((prev) => {
      const next = !prev;
      appendLog('INFO', 'Engine', next ? 'Auto-scanner resumed.' : 'Auto-scanner paused.');
      return next;
    });
  };

  const handleStopAndDisconnect = () => {
    setIsBotRunning(false);
    setIsConnected(false);
    chartAbortRef.current?.abort();
    chartViewAbortRef.current?.abort();
    appendLog('WARN', 'Connection', 'Scanner stopped and disconnected from live feed.');
  };

  const handleRunSingleScan = () => {
    setIsConnected(true);
    if (chartTimeframe !== timeframe) {
      appendLog('INFO', 'Engine', `Refreshing chart view for ${selectedSymbol} (${chartTimeframe}).`);
      void fetchChartViewOnly(selectedSymbol, chartTimeframe, configRef.current, { forceFresh: true });
      return;
    }
    appendLog('INFO', 'Engine', `Refreshing live Quotex chart for ${selectedSymbol} (${timeframe}).`);
    void fetchActiveChart(selectedSymbol, timeframe, configRef.current, { forceFresh: true });
  };

  const handleRefreshMarketsScan = () => {
    setIsConnected(true);
    appendLog('INFO', 'Engine', `Refreshing Quotex market scan (${timeframe}).`);
    void fetchMarketScan(selectedSymbol, timeframe, configRef.current);
  };

  const handleAddCustomAsset = async (
    input: CustomAssetInput
  ): Promise<{ ok: boolean; error?: string }> => {
    try {
      const resp = await fetch('/api/assets/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        return { ok: false, error: data.error || 'Unable to save custom asset.' };
      }
      if (data.assets) setAssets(data.assets);
      appendLog('INFO', 'Engine', `Added custom Quotex asset ${input.symbol} (${input.name}).`);
      void fetchMarketScan(selectedSymbol, timeframe, configRef.current);
      return { ok: true };
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : 'Network error adding custom asset.',
      };
    }
  };

  const handleRemoveCustomAsset = async (sym: string) => {
    try {
      const resp = await fetch('/api/assets/remove', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: sym }),
      });
      const data = await resp.json();
      if (data.assets) setAssets(data.assets);
      if (selectedSymbol === sym) setSelectedSymbol('EURUSD-OTC');
      appendLog('INFO', 'Engine', `Removed custom asset ${sym}.`);
      void fetchMarketScan(selectedSymbol === sym ? 'EURUSD-OTC' : selectedSymbol, timeframe, configRef.current);
    } catch {
      appendLog('ERROR', 'Engine', `Failed to remove custom asset ${sym}.`);
    }
  };

  const handleQuotexSessionChanged = useCallback(
    (nextStatus?: QuotexLiveStatus | null, updatedAssets?: Record<string, AssetMeta>) => {
      chartCacheRef.current.clear();
      setChartViewData(null);
      if (nextStatus !== undefined) {
        setQuotexStatus(nextStatus);
      }
      if (updatedAssets) {
        setAssets(updatedAssets);
      } else {
        loadCatalog();
      }
      setIsConnected(true);
      void fetchActiveChart(selectedSymbolRef.current, timeframeRef.current, configRef.current, {
        forceFresh: true,
      });
      if (chartTimeframeRef.current !== timeframeRef.current) {
        void fetchChartViewOnly(selectedSymbolRef.current, chartTimeframeRef.current, configRef.current, {
          forceFresh: true,
        });
      }
      void fetchMarketScan(selectedSymbolRef.current, timeframeRef.current, configRef.current);
    },
    [fetchActiveChart, fetchChartViewOnly, fetchMarketScan, loadCatalog]
  );

  const handleDownloadWindowsInstaller = async () => {
    const resp = await fetch('/api/windows-download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    if (!resp.ok) {
      appendLog('ERROR', 'Engine', 'Failed to package Windows application archive.');
      throw new Error('Could not prepare the Windows package. Try again.');
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'SignalDesk_Windows_Installer.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    appendLog('INFO', 'Engine', 'Exported Windows desktop application package (SignalDesk_Windows_Installer.zip).');
  };

  const navItems: {
    id: ModuleId;
    label: string;
    icon: typeof Columns2;
    iconBg: string;
    iconColor: string;
  }[] = [
    {
      id: 'live-chart',
      label: 'Live chart',
      icon: Columns2,
      iconBg: 'bg-emerald-500/15',
      iconColor: 'text-emerald-400',
    },
    {
      id: 'markets',
      label: 'Markets',
      icon: Grid2x2,
      iconBg: 'bg-blue-500/15',
      iconColor: 'text-blue-400',
    },
    {
      id: 'paper-lab',
      label: 'Stubay Ai',
      icon: Bot,
      iconBg: 'bg-violet-500/15',
      iconColor: 'text-violet-300',
    },
    {
      id: 'telegram',
      label: 'Telegram',
      icon: Send,
      iconBg: 'bg-violet-500/15',
      iconColor: 'text-violet-300',
    },
    {
      id: 'logs',
      label: 'Logs',
      icon: FileText,
      iconBg: 'bg-rose-500/15',
      iconColor: 'text-rose-400',
    },
    {
      id: 'connection',
      label: 'Quotex Login',
      icon: Settings,
      iconBg: 'bg-cyan-500/15',
      iconColor: 'text-cyan-400',
    },
    {
      id: 'quotex-browser',
      label: 'Quotex Browser',
      icon: Globe,
      iconBg: 'bg-sky-500/15',
      iconColor: 'text-sky-300',
    },
    {
      id: 'windows-app',
      label: 'Windows App',
      icon: MonitorDown,
      iconBg: 'bg-orange-500/15',
      iconColor: 'text-orange-400',
    },
  ];

  const activeNav = navItems.find((n) => n.id === activeModule) ?? navItems[0];

  const renderNavItem = (item: (typeof navItems)[number], onSelect?: () => void) => {
    const Icon = item.icon;
    const active = activeModule === item.id;
    return (
      <button
        key={item.id}
        type="button"
        aria-current={active ? 'page' : undefined}
        onClick={() => {
          selectModule(item.id);
          onSelect?.();
        }}
        className={`w-full flex items-center gap-3 px-2.5 py-2 rounded-lg text-[13px] font-medium transition-colors whitespace-nowrap cursor-pointer border ${
          active
            ? 'bg-[#1D191E] border-orange-500/30 text-orange-400'
            : 'border-transparent text-slate-300 hover:bg-[#141C2C] hover:text-white'
        }`}
      >
        <span
          className={`w-7 h-7 rounded-md inline-flex items-center justify-center shrink-0 ${item.iconBg} ${item.iconColor}`}
        >
          <Icon className="w-3.5 h-3.5" />
        </span>
        <span className="truncate">{item.label}</span>
      </button>
    );
  };

  const renderSidebarContent = (onSelect?: () => void) => (
    <div className="flex flex-col h-full">
      {/* SignalDesk Brand Lockup */}
      <div className="px-4 py-4 flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 shadow-sm">
          <ArrowUpRight className="w-5 h-5 text-white stroke-[2.5]" />
        </div>
        <div className="min-w-0">
          <div className="font-display font-bold text-[15px] leading-tight text-white tracking-tight">
            SignalDesk
          </div>
        </div>
      </div>

      {/* Module Navigation List */}
      <nav aria-label="Workspace" className="flex-1 px-2.5 pb-3 space-y-1 overflow-y-auto">
        {navItems.map((item) => renderNavItem(item, onSelect))}
      </nav>
    </div>
  );

  return (
    <div className="min-h-screen h-screen flex bg-[#0B101A] text-slate-100 overflow-hidden">
      {/* Desktop Sidebar */}
      <aside className="hidden lg:flex lg:flex-col lg:w-60 lg:shrink-0 border-r border-[#1A2334] bg-[#0D1320]">
        {renderSidebarContent()}
      </aside>

      {/* Mobile Drawer */}
      {mobileMenuOpen && (
        <div className="fixed inset-0 z-50 lg:hidden flex">
          <div
            className="fixed inset-0 bg-black/65"
            onClick={() => setMobileMenuOpen(false)}
          />
          <aside className="relative w-64 max-w-[82vw] bg-[#0D1320] border-r border-[#1A2334] flex flex-col z-10">
            <button
              type="button"
              onClick={() => setMobileMenuOpen(false)}
              className="absolute top-3.5 right-3 p-1.5 text-slate-400 hover:text-white"
              aria-label="Close navigation"
            >
              <X className="w-4 h-4" />
            </button>
            {renderSidebarContent(() => setMobileMenuOpen(false))}
          </aside>
        </div>
      )}

      {/* Main Content Shell */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Clean SignalDesk Top Header Bar */}
        <header className="h-12 px-4 sm:px-5 border-b border-[#1A2334] bg-[#0D1320] flex items-center justify-between gap-4 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <button
              type="button"
              onClick={() => setMobileMenuOpen(true)}
              className="lg:hidden p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-[#161F31]"
              aria-label="Open menu"
            >
              <Menu className="w-4 h-4" />
            </button>
            <h1 className="font-display font-semibold text-sm text-slate-100 truncate">
              {activeNav.label}
            </h1>
          </div>

          <div className="flex items-center gap-2.5 shrink-0">
            {isConnected && quotexStatus?.connected && (
              <span className="hidden md:inline text-xs text-slate-400 mr-1 whitespace-nowrap">
                Quotex {quotexStatus.account_type} live
              </span>
            )}


            <button
              type="button"
              onClick={handleToggleDesktopNotifications}
              disabled={notificationPermission === 'unsupported'}
              title={
                notificationPermission === 'granted' && config.desktop_notifications
                  ? 'Disable Stubay Ai desktop alerts'
                  : notificationPermission === 'denied'
                  ? 'Desktop notifications blocked in browser settings'
                  : 'Enable Stubay Ai desktop alerts'
              }
              aria-label="Stubay Ai desktop alerts"
              aria-pressed={notificationPermission === 'granted' && config.desktop_notifications}
              className={`h-8 w-8 inline-flex items-center justify-center rounded-md border transition-colors shrink-0 cursor-pointer disabled:opacity-40 ${
                notificationPermission === 'granted' && config.desktop_notifications
                  ? 'bg-emerald-500/12 border-emerald-500/35 text-emerald-300 hover:bg-emerald-500/20'
                  : 'bg-[#161F31] border-[#25324B] text-slate-400 hover:text-slate-100 hover:bg-[#1E2A40]'
              }`}
            >
              {notificationPermission === 'granted' && config.desktop_notifications ? (
                <Bell className="w-3.5 h-3.5" />
              ) : (
                <BellOff className="w-3.5 h-3.5" />
              )}
            </button>

            <div
              className={`h-8 px-2.5 inline-flex items-center gap-1.5 text-xs font-medium rounded-md border select-none transition-colors whitespace-nowrap shrink-0 ${
                isBotRunning && isConnected
                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                  : 'bg-slate-800/40 border-slate-700/50 text-slate-400'
              }`}
              title={isBotRunning && isConnected ? 'Continuous real-time market scanner is active across all pairs' : 'Continuous scanner is paused'}
            >
              <span className="relative flex h-2 w-2">
                {isBotRunning && isConnected && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                )}
                <span className={`relative inline-flex rounded-full h-2 w-2 ${isBotRunning && isConnected ? 'bg-emerald-500' : 'bg-slate-500'}`}></span>
              </span>
              <span>{isBotRunning && isConnected ? 'Continuous Scan' : 'Scan Paused'}</span>
            </div>

            <button
              type="button"
              onClick={handleToggleBot}
              className="h-8 px-3 inline-flex items-center gap-1.5 text-xs font-medium rounded-md bg-[#161F31] border border-[#25324B] text-slate-100 hover:bg-[#1E2A40] transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              {isBotRunning && isConnected ? (
                <>
                  <Square className="w-3 h-3 fill-current" />
                  <span>Pause scanner</span>
                </>
              ) : (
                <>
                  <Play className="w-3 h-3 fill-current" />
                  <span>Resume scanner</span>
                </>
              )}
            </button>

            {isConnected && quotexStatus?.connected && (
              <button
                type="button"
                onClick={handleStopAndDisconnect}
                title="Stop scanner and disconnect feed"
                aria-label="Stop scanner and disconnect feed"
                className="h-8 w-8 inline-flex items-center justify-center rounded-md border border-[#4A2535] bg-[#261620] text-rose-300 hover:bg-[#321B29] transition-colors shrink-0 cursor-pointer"
              >
                <Power className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </header>

        {/* Dedicated Module Viewport */}
        <main className="flex-1 overflow-y-auto bg-[#0B101A]">
          {activeModule === 'live-chart' && (
            <LiveChartModule
              assets={assets}
              selectedSymbol={selectedSymbol}
              onSelectSymbol={setSelectedSymbol}
              timeframe={chartTimeframe}
              onChangeTimeframe={setChartTimeframe}
              analysisData={chartTimeframe === timeframe ? analysisData : chartViewData}
              quotexStatus={quotexStatus}
              isConnected={isConnected}
              isLoading={chartTimeframe === timeframe ? isChartLoading : isChartViewLoading}
              loadError={chartTimeframe === timeframe ? chartError : chartViewError}
              onRunSingleScan={handleRunSingleScan}
              onOpenQuotexLogin={() => selectModule('connection')}
            />
          )}

          {activeModule === 'markets' && (
            <MarketsModule
              assets={assets}
              scanResults={scanResults}
              selectedSymbol={selectedSymbol}
              quotexConnected={Boolean(quotexStatus?.connected)}
              onOpenQuotexLogin={() => selectModule('connection')}
              onOpenChart={(sym) => {
                setSelectedSymbol(sym);
                setIsConnected(true);
                selectModule('live-chart');
              }}
              onRefreshScan={handleRefreshMarketsScan}
              onAddCustomAsset={handleAddCustomAsset}
              onRemoveCustomAsset={handleRemoveCustomAsset}
              isLoading={isScanLoading}
            />
          )}

          {activeModule === 'paper-lab' && <StubayDashboard onOpenQuotexLogin={() => selectModule('connection')} />}

          {activeModule === 'telegram' && (
            <TelegramModule botConfig={config} />
          )}

          {activeModule === 'logs' && (
            <LogsModule
              logs={engineLogs}
              quotexConnected={Boolean(quotexStatus?.connected)}
              onOpenQuotexLogin={() => selectModule('connection')}
              onClearLogs={() => setEngineLogs([])}
            />
          )}

          {activeModule === 'connection' && (
            <ConnectionModule
              config={config}
              quotexStatus={quotexStatus}
              onUpdateConfig={setConfig}
              onQuotexSessionChanged={handleQuotexSessionChanged}
              onOpenLiveChart={() => selectModule('live-chart')}
              onLogEvent={(level, msg) => appendLog(level, 'Connection', msg)}
            />
          )}

          {activeModule === 'quotex-browser' && <QuotexBrowserModule />}

          {activeModule === 'windows-app' && (
            <WindowsAppModule
              onDownloadWindowsInstaller={handleDownloadWindowsInstaller}
            />
          )}
        </main>
      </div>
      <OfflineIndicator />
    </div>
  );
}
