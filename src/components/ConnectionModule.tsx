import React, { useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  KeyRound,
  LogIn,
  LogOut,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import type { AssetMeta, BotConfig, QuotexLiveStatus } from '../types/trading';

interface ConnectionModuleProps {
  config: BotConfig;
  quotexStatus: QuotexLiveStatus | null;
  onUpdateConfig: (next: BotConfig) => void;
  onQuotexSessionChanged: (
    nextStatus?: QuotexLiveStatus | null,
    updatedAssets?: Record<string, AssetMeta>
  ) => void;
  onOpenLiveChart: () => void;
  onLogEvent: (level: 'INFO' | 'SIGNAL' | 'WARN' | 'ERROR', msg: string) => void;
}

export const ConnectionModule: React.FC<ConnectionModuleProps> = ({
  config,
  quotexStatus,
  onUpdateConfig,
  onQuotexSessionChanged,
  onOpenLiveChart,
  onLogEvent,
}) => {
  const [authTab, setAuthTab] = useState<'credentials' | 'ssid'>('credentials');

  // Credentials stay in memory only (never persisted to localStorage)
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  // Step 2 OTP state — only appears AFTER clicking Login and Quotex requests an OTP
  const [pendingLoginId, setPendingLoginId] = useState<string | null>(null);
  const [otpPrompt, setOtpPrompt] = useState<string>('');
  const [otpCode, setOtpCode] = useState('');
  const [verifyingOtp, setVerifyingOtp] = useState(false);
  const [syncingAssets, setSyncingAssets] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  const [testing, setTesting] = useState(false);
  const [showTechnicalDetails, setShowTechnicalDetails] = useState(false);
  const [copiedTrace, setCopiedTrace] = useState(false);
  const [testResult, setTestResult] = useState<{
    mode?: string;
    authorized?: boolean;
    balance?: number | null;
    message: string;
    technical_details?: string;
  } | null>(null);
  const otpResultRef = useRef<HTMLDivElement>(null);
  const [showOtpFailure, setShowOtpFailure] = useState(false);

  useEffect(() => {
    if (showOtpFailure && testResult && !testResult.authorized) {
      otpResultRef.current?.scrollIntoView({ block: 'nearest' });
    }
  }, [showOtpFailure, testResult]);

  const handleTestConnection = async (forceFresh = false) => {
    setShowOtpFailure(false);
    setTesting(true);
    setTestResult(null);
    setShowTechnicalDetails(false);
    setCopiedTrace(false);
    setPendingLoginId(null);
    setOtpCode('');
    try {
      const payload =
        authTab === 'credentials'
          ? {
              auth_method: 'credentials',
              email: email.trim(),
              password,
              account_type: config.account_type,
              force_fresh: forceFresh,
            }
          : {
              auth_method: 'ssid',
              ssid: config.quotex_ssid,
              cookie: config.quotex_cookie,
              user_agent: config.quotex_user_agent,
              account_type: config.account_type,
            };

      const resp = await fetch('/api/quotex/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      const message = data.message || data.error || 'Quotex connection check finished.';

      if (data.mode === 'OTP_REQUIRED' && data.login_id) {
        setPendingLoginId(String(data.login_id));
        setOtpPrompt(
          String(data.prompt || 'Enter the verification code sent to your email.')
        );
        setTestResult({
          mode: 'OTP_REQUIRED',
          authorized: false,
          message,
        });
        onLogEvent('INFO', 'Quotex login paused: awaiting Email OTP verification code.');
      } else {
        setTestResult({
          mode: data.mode,
          authorized: Boolean(data.authorized),
          balance: typeof data.balance === 'number' ? data.balance : null,
          message,
          technical_details:
            typeof data.technical_details === 'string' ? data.technical_details : undefined,
        });
        onLogEvent(data.authorized ? 'INFO' : 'WARN', `Quotex ${authTab} check: ${message}`);
        if (data.authorized && data.mode === 'LIVE_PYQUOTEX') {
          onQuotexSessionChanged({
            connected: true,
            authorized: true,
            mode: 'LIVE_PYQUOTEX',
            account_type: data.account_type || config.account_type,
            email: data.email || email.trim(),
            balance: typeof data.balance === 'number' ? data.balance : null,
            has_pyquotex: true,
            live_assets_count: Number(data.live_assets_count || 0),
          });
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unable to run Quotex connection test.';
      setTestResult({ message });
      onLogEvent('ERROR', message);
    } finally {
      setTesting(false);
    }
  };

  const handleVerifyOtp = async () => {
    if (!pendingLoginId || verifyingOtp) return;
    setShowOtpFailure(false);
    if (!otpCode.trim()) {
      setTestResult({
        mode: 'OTP_REQUIRED',
        authorized: false,
        message: 'Enter the 6-digit verification code sent to your email.',
      });
      return;
    }

    setVerifyingOtp(true);
    try {
      const resp = await fetch('/api/quotex/verify-otp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          login_id: pendingLoginId,
          otp: otpCode.trim(),
        }),
      });
      const data = await resp.json();
      const message = data.message || data.error || 'OTP verification finished.';

      if (data.mode === 'OTP_UNAVAILABLE') {
        setOtpCode('');
        setTestResult({ mode: data.mode, authorized: false, message });
        setShowOtpFailure(true);
        return;
      }
      if (data.mode === 'OTP_REQUIRED' && data.login_id) {
        setPendingLoginId(String(data.login_id));
        setOtpCode('');
        setOtpPrompt(typeof data.prompt === 'string' ? data.prompt : message);
        setTestResult({ mode: 'OTP_REQUIRED', authorized: false, message });
      } else {
        setPendingLoginId(null);
        setOtpCode('');
        setTestResult({
          mode: data.mode,
          authorized: Boolean(data.authorized),
          balance: typeof data.balance === 'number' ? data.balance : null,
          message,
          technical_details:
            typeof data.technical_details === 'string' ? data.technical_details : undefined,
        });
        onLogEvent(data.authorized ? 'INFO' : 'WARN', `Quotex OTP verify: ${message}`);
        setShowOtpFailure(!data.authorized);
        if (data.authorized && data.mode === 'LIVE_PYQUOTEX') {
          onQuotexSessionChanged({
            connected: true,
            authorized: true,
            mode: 'LIVE_PYQUOTEX',
            account_type: data.account_type || config.account_type,
            email: data.email || email.trim(),
            balance: typeof data.balance === 'number' ? data.balance : null,
            has_pyquotex: true,
            live_assets_count: Number(data.live_assets_count || 0),
          });
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to verify OTP code.';
      setTestResult({ mode: 'AUTH_FAILED', authorized: false, message });
      setShowOtpFailure(true);
      onLogEvent('ERROR', message);
    } finally {
      setVerifyingOtp(false);
    }
  };

  const handleCancelOtp = async () => {
    const id = pendingLoginId;
    setPendingLoginId(null);
    setOtpCode('');
    setTestResult(null);
    if (id) {
      try {
        await fetch('/api/quotex/cancel-otp', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ login_id: id }),
        });
      } catch {
        // ignore cancel errors
      }
    }
  };

  const handleDisconnectQuotex = async () => {
    setDisconnecting(true);
    try {
      const resp = await fetch('/api/quotex/disconnect', { method: 'POST' });
      const data = await resp.json();
      setTestResult({
        mode: 'DISCONNECTED',
        authorized: false,
        message: 'Disconnected live Quotex pyquotex session.',
      });
      onLogEvent('WARN', 'Disconnected active Quotex pyquotex live session.');
      onQuotexSessionChanged(data as QuotexLiveStatus);
    } catch (err) {
      onLogEvent('ERROR', err instanceof Error ? err.message : 'Failed to disconnect Quotex.');
    } finally {
      setDisconnecting(false);
    }
  };

  const handleSyncQuotexAssets = async () => {
    setSyncingAssets(true);
    try {
      const resp = await fetch('/api/quotex/sync-assets', { method: 'POST' });
      const data = await resp.json();
      if (data.ok) {
        const msg = `Synced ${data.discovered ?? 0} live Quotex assets & payouts (${data.added ?? 0} new added).`;
        setTestResult({
          mode: 'LIVE_PYQUOTEX',
          authorized: true,
          balance: quotexStatus?.balance ?? null,
          message: msg,
        });
        onLogEvent('INFO', msg);
        onQuotexSessionChanged(quotexStatus, data.assets as Record<string, AssetMeta>);
      } else {
        setTestResult({
          mode: 'SYNC_FAILED',
          authorized: Boolean(quotexStatus?.connected),
          message: data.error || 'Could not sync Quotex assets.',
        });
      }
    } catch (err) {
      onLogEvent('ERROR', err instanceof Error ? err.message : 'Failed to sync Quotex assets.');
    } finally {
      setSyncingAssets(false);
    }
  };

  return (
    <div className="p-6 max-w-[1240px] mx-auto space-y-6">
      {/* Persistent Live Quotex Session Status Banner */}
      {quotexStatus?.connected && (
        <div className="border border-emerald-500/35 rounded-lg bg-emerald-950/20 p-4 flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-0.5 min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-emerald-300">
              <span>Live Quotex Candlestick Feed Active ({quotexStatus.account_type})</span>
              {typeof quotexStatus.balance === 'number' && (
                <span className="font-mono tabular-nums text-emerald-200">
                  · Balance: ${quotexStatus.balance.toFixed(2)}
                </span>
              )}
            </div>
            <div className="text-xs text-slate-400">
              Signed in via <code className="text-slate-200">pyquotex</code>
              {quotexStatus.email ? ` as ${quotexStatus.email}` : ''}. 
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={onOpenLiveChart}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-md bg-emerald-600 hover:bg-emerald-500 text-white transition-colors cursor-pointer"
            >
              <span>Open Live Chart</span>
              <ArrowUpRight className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              onClick={handleSyncQuotexAssets}
              disabled={syncingAssets}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-[#121928] border border-[#232F46] text-slate-200 hover:bg-[#1A2336] transition-colors cursor-pointer disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${syncingAssets ? 'animate-spin' : ''}`} />
              <span>{syncingAssets ? 'Syncing...' : 'Sync Quotex Assets'}</span>
            </button>
            <button
              type="button"
              onClick={handleDisconnectQuotex}
              disabled={disconnecting}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-[#261620] border border-[#4A2535] text-rose-300 hover:bg-[#321B29] transition-colors cursor-pointer disabled:opacity-50"
            >
              <LogOut className="w-3.5 h-3.5" />
              <span>{disconnecting ? 'Disconnecting...' : 'Disconnect'}</span>
            </button>
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div
          role="group"
          aria-label="Quotex authentication method"
          className="inline-flex items-center p-1 rounded-lg bg-[#0F1523] border border-[#1A2334]"
        >
          <button
            type="button"
            onClick={() => {
              setAuthTab('credentials');
              setTestResult(null);
            }}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap cursor-pointer inline-flex items-center gap-1.5 ${
              authTab === 'credentials'
                ? 'bg-orange-500/15 text-orange-300 border border-orange-500/35'
                : 'border border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <LogIn className="w-3.5 h-3.5" />
            <span>Email and password</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setAuthTab('ssid');
              setPendingLoginId(null);
              setTestResult(null);
            }}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap cursor-pointer inline-flex items-center gap-1.5 ${
              authTab === 'ssid'
                ? 'bg-orange-500/15 text-orange-300 border border-orange-500/35'
                : 'border border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <KeyRound className="w-3.5 h-3.5" />
            <span>Browser session</span>
          </button>
        </div>
      </div>

      <div className="max-w-3xl">
        {/* Auth Form Card */}
        <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] p-5 space-y-5">
          {authTab === 'credentials' ? (
            <div className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <label className="block">
                  <span className="text-xs text-slate-400">Quotex Account Email</span>
                  <input
                    type="email"
                    autoComplete="username"
                    placeholder="you@example.com"
                    value={email}
                    disabled={Boolean(pendingLoginId)}
                    onChange={(e) => setEmail(e.target.value)}
                    className="mt-1.5 w-full bg-[#0B101A] border border-[#232F46] rounded-lg px-3.5 py-2.5 text-xs text-slate-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500 disabled:opacity-60"
                  />
                </label>

                <label className="block">
                  <span className="text-xs text-slate-400">Quotex Password (memory only)</span>
                  <input
                    type="password"
                    autoComplete="current-password"
                    placeholder="••••••••••••"
                    value={password}
                    disabled={Boolean(pendingLoginId)}
                    onChange={(e) => setPassword(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !testing && !pendingLoginId) {
                        void handleTestConnection();
                      }
                    }}
                    className="mt-1.5 w-full bg-[#0B101A] border border-[#232F46] rounded-lg px-3.5 py-2.5 text-xs text-slate-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500 disabled:opacity-60"
                  />
                </label>
              </div>

              {/* Step 2: Appears ONLY after pressing Login and Quotex asks for Email OTP */}
              {pendingLoginId && (
                <div className="p-4 rounded-lg bg-[#0B101A] border border-orange-500/40 space-y-3">
                  <div className="flex items-center gap-2 text-xs font-medium text-orange-300">
                    <ShieldCheck className="w-4 h-4 shrink-0" />
                    <span>Email verification code</span>
                  </div>
                  <p className="text-xs text-slate-300">{otpPrompt}</p>
                  <div className="flex flex-wrap items-center gap-2.5">
                    <input
                      type="text"
                      inputMode="numeric"
                      autoFocus
                      placeholder="Enter OTP code from email..."
                      value={otpCode}
                      onChange={(e) => setOtpCode(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !verifyingOtp) {
                          e.preventDefault();
                          void handleVerifyOtp();
                        }
                      }}
                      className="flex-1 min-w-[180px] bg-[#0F1523] border border-[#2A3955] rounded-lg px-3.5 py-2 text-sm font-mono tracking-widest text-slate-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500"
                    />
                    <button
                      type="button"
                      onClick={handleVerifyOtp}
                      disabled={verifyingOtp}
                      className="px-4 py-2 text-xs font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white transition-colors cursor-pointer disabled:opacity-50 whitespace-nowrap"
                    >
                      {verifyingOtp ? 'Verifying OTP...' : 'Verify OTP & Complete Login'}
                    </button>
                    <button
                      type="button"
                      onClick={handleCancelOtp}
                      disabled={verifyingOtp}
                      className="px-3 py-2 text-xs font-medium rounded-lg bg-[#161F31] border border-[#25324B] text-slate-300 hover:bg-[#1E2A40] transition-colors cursor-pointer"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <label className="block">
                <span className="text-xs text-slate-400">
                  Quotex SSID Token (raw token or full <code className="text-slate-300">42[&quot;authorization&quot;,...]</code> packet)
                </span>
                <input
                  type="password"
                  placeholder="Paste SSID session token from DevTools WS Messages..."
                  value={config.quotex_ssid}
                  onChange={(e) => onUpdateConfig({ ...config, quotex_ssid: e.target.value })}
                  className="mt-1.5 w-full bg-[#0B101A] border border-[#232F46] rounded-lg px-3.5 py-2.5 text-xs font-mono text-slate-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500"
                />
              </label>

              <label className="block">
                <span className="text-xs text-slate-400">
                  Browser Cookie Header (from the same logged-in <code className="text-slate-300">qxbroker.com</code> WS Request Headers)
                </span>
                <input
                  type="password"
                  placeholder="Paste full Cookie header string (includes session & cf cookies)..."
                  value={config.quotex_cookie}
                  onChange={(e) => onUpdateConfig({ ...config, quotex_cookie: e.target.value })}
                  className="mt-1.5 w-full bg-[#0B101A] border border-[#232F46] rounded-lg px-3.5 py-2.5 text-xs font-mono text-slate-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500"
                />
              </label>

              <label className="block">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-400">Matching Browser User-Agent</span>
                  <button
                    type="button"
                    onClick={() =>
                      onUpdateConfig({ ...config, quotex_user_agent: navigator.userAgent })
                    }
                    className="text-[11px] text-orange-400 hover:text-orange-300 cursor-pointer"
                  >
                    Use Current Browser UA
                  </button>
                </div>
                <input
                  type="text"
                  placeholder="Mozilla/5.0 (Windows NT 10.0; Win64; x64)..."
                  value={config.quotex_user_agent}
                  onChange={(e) =>
                    onUpdateConfig({ ...config, quotex_user_agent: e.target.value })
                  }
                  className="mt-1.5 w-full bg-[#0B101A] border border-[#232F46] rounded-lg px-3.5 py-2.5 text-xs font-mono text-slate-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500"
                />
              </label>
            </div>
          )}

          {/* Quotex account selection */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-1">
            <label className="block">
              <span className="text-xs text-slate-400">Account Environment</span>
              <select
                value={config.account_type}
                onChange={(e) =>
                  onUpdateConfig({
                    ...config,
                    account_type: e.target.value as 'DEMO' | 'REAL',
                  })
                }
                className="mt-1.5 w-full bg-[#0B101A] border border-[#232F46] rounded-lg px-3 py-2.5 text-xs text-slate-100"
              >
                <option value="DEMO">DEMO Practice Account (Recommended)</option>
                <option value="REAL">REAL Live Balance</option>
              </select>
            </label>

            <p className="self-end text-xs text-slate-400">
              Stubay Ai uses this connection for market data and places simulated trades only.
            </p>
          </div>

          {!pendingLoginId && (
            <div className="flex flex-wrap items-center gap-2.5 pt-1">
              <button
                type="button"
                onClick={() => void handleTestConnection(false)}
                disabled={testing}
                className="inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-lg bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors cursor-pointer disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${testing ? 'animate-spin' : ''}`} />
                <span>
                  {testing
                    ? 'Logging in to Quotex...'
                    : authTab === 'credentials'
                    ? 'Login to Quotex'
                    : 'Test Browser Session'}
                </span>
              </button>

              {authTab === 'credentials' && (
                <button
                  type="button"
                  onClick={() => void handleTestConnection(true)}
                  disabled={testing}
                  title="Deletes cached pyquotex session.json and forces a brand-new Quotex login"
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg bg-[#121928] border border-[#232F46] text-slate-200 hover:bg-[#1A2336] transition-colors cursor-pointer disabled:opacity-50 whitespace-nowrap"
                >
                  <span>Fresh Login (Reset Session)</span>
                </button>
              )}

              <span className="text-xs text-slate-500 font-mono">
                {authTab === 'credentials'
                  ? 'Step 1: Email & Password → Step 2: OTP if requested'
                  : 'Endpoint: wss://ws2.qxbroker.com/socket.io/'}
              </span>
            </div>
          )}

          {testResult && (
            <div ref={otpResultRef} role={showOtpFailure ? 'alert' : 'status'} className="p-3.5 rounded-lg bg-[#0B101A] border border-[#1E293D] text-xs space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <span
                  className={`font-medium ${
                    testResult.authorized
                      ? 'text-emerald-400'
                      : testResult.mode === 'OTP_REQUIRED'
                      ? 'text-orange-300'
                      : testResult.mode === 'CLOUDFLARE_BLOCKED' ||
                        testResult.mode === 'AUTH_FAILED'
                      ? 'text-rose-400'
                      : 'text-amber-400'
                  }`}
                >
                  {testResult.mode ? `Status: ${testResult.mode}` : 'Connection Check'}
                </span>
                {typeof testResult.balance === 'number' && (
                  <span className="font-mono tabular-nums text-emerald-300 font-semibold">
                    Balance: ${testResult.balance.toFixed(2)}
                  </span>
                )}
              </div>
              <div className="text-slate-300 leading-relaxed break-words">
                {testResult.message}
              </div>

              {(testResult.technical_details ||
                (authTab === 'credentials' &&
                  !testResult.authorized &&
                  testResult.mode === 'AUTH_FAILED')) && (
                <div className="pt-1 flex flex-wrap items-center justify-between gap-2 border-t border-[#182234]">
                  <div className="flex flex-wrap items-center gap-3">
                    {testResult.technical_details && (
                      <button
                        type="button"
                        onClick={() => setShowTechnicalDetails((v) => !v)}
                        className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
                      >
                        {showTechnicalDetails ? (
                          <ChevronDown className="w-3.5 h-3.5" />
                        ) : (
                          <ChevronRight className="w-3.5 h-3.5" />
                        )}
                        <span>
                          {showTechnicalDetails ? 'Hide technical details' : 'Show technical details'}
                        </span>
                      </button>
                    )}

                    {testResult.technical_details && showTechnicalDetails && (
                      <button
                        type="button"
                        onClick={() => {
                          if (!testResult.technical_details) return;
                          void navigator.clipboard?.writeText(testResult.technical_details);
                          setCopiedTrace(true);
                          setTimeout(() => setCopiedTrace(false), 1800);
                        }}
                        className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-400 hover:text-orange-300 transition-colors cursor-pointer"
                      >
                        {copiedTrace ? (
                          <>
                            <Check className="w-3 h-3 text-emerald-400" />
                            <span className="text-emerald-400">Copied</span>
                          </>
                        ) : (
                          <>
                            <Copy className="w-3 h-3" />
                            <span>Copy details</span>
                          </>
                        )}
                      </button>
                    )}
                  </div>

                  {authTab === 'credentials' &&
                    !testResult.authorized &&
                    testResult.mode === 'AUTH_FAILED' && (
                      <button
                        type="button"
                        onClick={() => {
                          setAuthTab('ssid');
                          setPendingLoginId(null);
                        }}
                        className="inline-flex items-center gap-1 text-[11px] font-medium text-orange-400 hover:text-orange-300 transition-colors cursor-pointer whitespace-nowrap"
                      >
                        <KeyRound className="w-3 h-3" />
                        <span>Use Browser Session (SSID + Cookie)</span>
                      </button>
                    )}
                </div>
              )}

              {testResult.technical_details && showTechnicalDetails && (
                <pre className="p-3 rounded-md bg-[#070B12] border border-[#182234] text-[11px] font-mono text-slate-400 overflow-x-auto max-h-48 whitespace-pre-wrap break-words leading-relaxed">
                  {testResult.technical_details}
                </pre>
              )}
            </div>
          )}
        </div>

      </div>
    </div>
  );
};
