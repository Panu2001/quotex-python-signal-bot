export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface StrategyVote {
  id: string;
  name: string;
  category: string;
  signal: 'CALL' | 'PUT' | 'NEUTRAL';
  strength: number;
  weight: number;
  reason: string;
  metrics: Record<string, string | number>;
}

export interface StrategyCatalogItem {
  id: string;
  name: string;
  category: string;
  default_weight: number;
  recommended_expiry: string;
  description: string;
}

export interface AssetMeta {
  name: string;
  category: string;
  payout: number;
  base: number;
  vol: number;
  decimals: number;
  source: string;
  custom?: boolean;
}

export interface MarketOverlays {
  ema_fast: (number | null)[];
  ema_slow: (number | null)[];
  ema_200: (number | null)[];
  bb_upper: (number | null)[];
  bb_mid: (number | null)[];
  bb_lower: (number | null)[];
  supertrend: (number | null)[];
  supertrend_dir: number[];
  sar: number[];
  rsi: (number | null)[];
  macd_hist: (number | null)[];
}

export interface QuotexLiveStatus {
  connected: boolean;
  authorized: boolean;
  mode: string;
  account_type: 'DEMO' | 'REAL' | string;
  email?: string;
  balance?: number | null;
  connected_at?: number | null;
  has_pyquotex?: boolean;
  live_assets_count?: number;
  last_error?: string | null;
}

export interface AnalysisResponse {
  feed: {
    symbol: string;
    name: string;
    category: string;
    payout: number;
    decimals: number;
    timeframe: string;
    feed_source: string;
    is_live?: boolean;
    live_required?: boolean;
    error?: string | null;
    candles: Candle[];
  };
  quotex_status?: QuotexLiveStatus;
  analysis: {
    signal: 'CALL' | 'PUT' | 'NEUTRAL';
    confidence: number;
    qualified?: boolean;
    qualification_reason?: string;
    recommended_expiry: string;
    volatility_regime: 'Low' | 'Normal' | 'High';
    market_regime?: 'RANGING' | 'TRENDING' | 'VOLATILE' | 'NORMAL' | string;
    bollinger_bandwidth?: number;
    candlestick_quality?: Record<string, any>;
    mtf_confirmation?: Record<string, any>;
    entry_timing_guard?: Record<string, any>;
    atr_pct: number;
    price: number;
    call_count: number;
    put_count: number;
    neutral_count: number;
    call_score: number;
    put_score: number;
    votes: StrategyVote[];
    levels: {
      supports: number[];
      resistances: number[];
    };
    overlays: MarketOverlays;
  };
  stake_info: {
    stake: number;
    step: number;
    halted: boolean;
    reason: string;
    breakeven_wr: number;
  };
}

export interface ScanPairResult {
  change_24h?: number | null;
  payout_1m?: number | null;
  payout_5m?: number | null;
  symbol: string;
  name: string;
  category: string;
  payout: number;
  decimals: number;
  feed_source: string;
  is_live?: boolean;
  live_required?: boolean;
  analyzed?: boolean;
  price: number;
  signal: 'CALL' | 'PUT' | 'NEUTRAL';
  confidence: number;
  qualified?: boolean;
  qualification_reason?: string;
  recommended_expiry: string;
  volatility_regime: string;
  call_count: number;
  put_count: number;
  neutral_count: number;
  latest_bar_time?: number;
  top_reasons: string[];
  custom?: boolean;
}

export interface BacktestResponse {
  symbol: string;
  name: string;
  timeframe: string;
  bars_tested: number;
  expiry_candles: number;
  payout_pct: number;
  breakeven_win_rate: number;
  summary: {
    starting_balance: number;
    ending_balance: number;
    net_profit: number;
    roi_pct: number;
    total_trades: number;
    total_wins: number;
    direct_wins: number;
    gale_wins: number;
    losses: number;
    win_rate: number;
    direct_win_rate: number;
    profit_factor: number;
    max_drawdown_pct: number;
  };
  equity_curve: { bar: number; time: number; equity: number }[];
  strategy_breakdown: {
    id: string;
    name: string;
    category: string;
    signals: number;
    wins: number;
    losses: number;
    win_rate: number;
  }[];
  recent_trades: {
    bar: number;
    time: number;
    signal: 'CALL' | 'PUT';
    confidence: number;
    entry: number;
    exit: number;
    step_won: number;
    result: 'WIN' | 'LOSS';
    pnl: number;
    balance: number;
  }[];
}


export interface BotConfig {
  min_confidence: number;
  rsi_oversold: number;
  rsi_overbought: number;
  bb_period: number;
  bb_std: number;
  ema_fast: number;
  ema_slow: number;
  adx_threshold: number;
  base_stake: number;
  risk_mode: 'martingale' | 'fixed' | 'anti_martingale' | 'compounding';
  martingale_multiplier: number;
  max_martingale_steps: number;
  enabled_strategies: Record<string, boolean>;
  strategy_weights: Record<string, number>;
  telegram_bot_token: string;
  telegram_chat_id: string;
  telegram_auto_send: boolean;
  telegram_min_confidence: number;
  signal_lead_minutes: number;
  paper_starting_balance: number;
  analyzed_assets: Record<string, boolean>;
  notify_assets: Record<string, boolean>;
  desktop_notifications: boolean;
  quotex_ssid: string;
  quotex_cookie: string;
  quotex_user_agent: string;
  account_type: 'DEMO' | 'REAL';
  auto_trade: boolean;
}

export type NotificationPermissionState = NotificationPermission | 'unsupported';

export interface SignalLogEntry {
  id: string;
  symbol: string;
  timeframe: string;
  signal: 'CALL' | 'PUT';
  confidence: number;
  entryPrice: number;
  currentPrice?: number;
  exitPrice?: number;
  stake: number;
  payout: number;
  expirySec: number;
  createdAt: number;
  entryAt: number;
  entryLocked: boolean;
  expiresAt: number;
  status: 'PENDING' | 'ACTIVE' | 'WIN' | 'LOSS' | 'TIE';
  pnl: number;
  topStrategy: string;
  source?: 'AUTO' | 'MANUAL' | 'AI';
  decimals?: number;
}

export interface EngineLogEntry {
  id: string;
  timestamp: number;
  level: 'INFO' | 'SIGNAL' | 'WARN' | 'ERROR';
  module: 'Engine' | 'Signals' | 'Telegram' | 'Connection' | 'AI';
  message: string;
}

export interface BotFile {
  name: string;
  size: number;
  content: string;
}
