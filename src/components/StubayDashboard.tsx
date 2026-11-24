import { useCallback, useEffect, useState } from 'react';
import { Activity, Bot, Check, List, LogIn, RotateCw, Sparkles, X } from 'lucide-react';

interface Trade {
  trade_id: string;
  asset: string;
  direction: 'CALL' | 'PUT';
  decision_ts: number;
  status: 'OPEN' | 'WIN' | 'LOSS' | 'TIE' | 'UNRESOLVED';
}

interface Stats {
  trades: number;
  wins: number;
  losses: number;
}

type WorkerStatus = {
  label: string;
  detail: string;
  tone: 'active' | 'learning' | 'stopped' | 'waiting' | 'unknown';
};

const checking: WorkerStatus = { label: 'Checking AI', detail: 'Waiting for worker status.', tone: 'unknown' };
const learningChecking: WorkerStatus = { label: 'Learning · Checking', detail: 'Waiting for learning status.', tone: 'unknown' };
const learningUnavailable: WorkerStatus = { label: 'Learning · Unavailable', detail: 'Could not verify learning activity.', tone: 'unknown' };
const statusColors = {
  active: ['border-emerald-400/20 bg-emerald-400/10 text-emerald-200', 'bg-emerald-400'],
  learning: ['border-violet-400/30 bg-violet-400/15 text-violet-200', 'bg-violet-300 motion-safe:animate-pulse'],
  waiting: ['border-sky-400/25 bg-sky-400/10 text-sky-200', 'bg-sky-400'],
  stopped: ['border-amber-400/25 bg-amber-400/10 text-amber-200', 'bg-amber-400'],
  unknown: ['border-slate-400/20 bg-slate-400/10 text-slate-300', 'bg-slate-400'],
} as const;

const outcomes = {
  WIN: ['Win', 'bg-emerald-400/10 text-emerald-300'],
  LOSS: ['Loss', 'bg-rose-400/10 text-rose-300'],
  TIE: ['Tie', 'bg-slate-400/10 text-slate-300'],
  OPEN: ['Open', 'bg-sky-400/10 text-sky-300'],
  UNRESOLVED: ['Unresolved', 'bg-amber-400/10 text-amber-300'],
} as const;

const timeFormat = new Intl.DateTimeFormat(undefined, {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
});

export function StubayDashboard({ onOpenQuotexLogin }: { onOpenQuotexLogin: () => void }) {
  const [trades, setTrades] = useState<Trade[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loadError, setLoadError] = useState('');
  const [restartMessage, setRestartMessage] = useState('');
  const [restartFailed, setRestartFailed] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [workerStatus, setWorkerStatus] = useState({
    worker: checking,
    learning: learningChecking,
  });

  const [minimumConfidence, setMinimumConfidence] = useState<number | null>(null);
  const [confidenceDraft, setConfidenceDraft] = useState<number | null>(null);
  const [confidenceSaving, setConfidenceSaving] = useState(false);
  const [confidenceError, setConfidenceError] = useState('');

  async function saveConfidence() {
    if (confidenceDraft === null) return;
    setConfidenceSaving(true);
    setConfidenceError('');
    try {
      const response = await fetch('/api/paper-lab/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stubay_minimum_confidence: confidenceDraft }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok || result.minimum_confidence !== confidenceDraft) {
        throw new Error('Unable to save confidence');
      }
      setMinimumConfidence(result.minimum_confidence);
      setConfidenceDraft(null);
    } catch {
      setConfidenceError('Could not save the minimum confidence. Try again.');
    } finally {
      setConfidenceSaving(false);
    }
  }

  const refresh = useCallback(async (signal?: AbortSignal) => {
    let workerLoaded = false;
    try {
      const overviewResponse = await fetch('/api/paper-lab/overview', { signal });
      if (!overviewResponse.ok) throw new Error('Overview unavailable');
      const overview = await overviewResponse.json();
      if (!overview.ok) throw new Error('Overview unavailable');
      workerLoaded = true;
      const worker = overview.overview?.background_loop;
      const stubay = overview.overview?.stubay;
      if (!signal?.aborted) {
        if (typeof stubay?.minimum_confidence === 'number') setMinimumConfidence(stubay.minimum_confidence);
      }
      const now = Math.floor(Date.now() / 1000);
      const detail = String(stubay?.status || 'Waiting for the next scan.');
      const unavailable = { label: 'Status unavailable', detail: 'The worker has not reported recently.', tone: 'unknown' } as const;
      let status: WorkerStatus = checking;
      if (!worker) status = unavailable;
      else if (worker.last_error) status = { label: 'Stopped', detail: String(worker.last_error), tone: 'stopped' };
      else if (!worker.enabled || !worker.started || !worker.running) {
        status = { label: 'Stopped', detail: 'Background automation is disabled or has not started.', tone: 'stopped' };
      } else if (worker.last_cycle_ts && now - worker.last_cycle_ts > 90) status = unavailable;
      else if (worker.last_cycle_summary?.quotex_connected === false) {
        status = { label: 'Waiting for Quotex', detail: 'Sign in to Quotex to resume market scans.', tone: 'waiting' };
      } else if (detail.startsWith('Scan failed')) {
        status = { label: 'Stopped', detail, tone: 'stopped' };
      } else {
        status = { label: 'Running in background', detail, tone: 'active' };
      }
      const learning: WorkerStatus = status.tone === 'unknown' ? learningUnavailable
        : status.tone === 'waiting' ? { label: 'Learning · Waiting for Quotex', detail: status.detail, tone: 'waiting' }
        : status.tone === 'stopped' ? { label: 'Learning · Paused', detail: status.detail, tone: 'stopped' }
        : detail.startsWith('Learning') ? { label: 'Learning · Active', detail, tone: 'learning' }
        : { label: 'Learning · Idle', detail, tone: 'unknown' };
      if (!signal?.aborted) setWorkerStatus({ worker: status, learning });
      const totals: Stats | undefined = overview.overview?.stubay?.trade_totals;
      const limit = totals ? 50 : 5000;
      const response = await fetch(
        `/api/paper-lab/history?table=paper_trades&limit=${limit}`, { signal },
      );
      const history = await response.json();
      if (!response.ok || !history.ok || !Array.isArray(history.rows)) throw new Error('History unavailable');
      const rows: Trade[] = history.rows;
      if (signal?.aborted) return;
      setTrades(rows.slice(0, 50));
      setStats(totals ?? {
        trades: rows.length, wins: rows.filter((trade) => trade.status === 'WIN').length,
        losses: rows.filter((trade) => trade.status === 'LOSS').length,
      });
      setLoadError('');
    } catch {
      if (!signal?.aborted) {
        setLoadError('Could not load trades.');
        if (!workerLoaded) {
          setWorkerStatus({
            worker: { label: 'Status unavailable', detail: 'Could not refresh Stubay Ai status.', tone: 'unknown' },
            learning: learningUnavailable,
          });
        }
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const tick = async () => {
      await refresh(controller.signal);
      if (!controller.signal.aborted) timer = window.setTimeout(tick, 6000);
    };
    let timer: number | undefined;
    void tick();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [refresh]);

  const restart = async () => {
    setRestarting(true);
    setRestartMessage('');
    setRestartFailed(false);
    try {
      const response = await fetch('/api/paper-lab/action/toggle-loop', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true, restart_stubay: true }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error('Restart failed');
      setRestartMessage(result.restart_requested ? 'Restart requested.' : 'Automation resumed.');
      await refresh();
    } catch {
      setRestartFailed(true);
      setRestartMessage('Could not restart Stubay Ai.');
    } finally {
      setRestarting(false);
    }
  };

  const decisive = stats ? stats.wins + stats.losses : 0;
  const widgets = [
    { label: 'Trades', value: stats?.trades ?? '—', Icon: List, color: 'text-violet-300' },
    { label: 'Wins', value: stats?.wins ?? '—', Icon: Check, color: 'text-emerald-300' },
    { label: 'Losses', value: stats?.losses ?? '—', Icon: X, color: 'text-rose-300' },
    { label: 'Accuracy', value: stats && decisive ? `${(stats.wins * 100 / decisive).toFixed(1)}%` : '—',
      Icon: Activity, color: 'text-cyan-300' },
  ];

  return (
    <div className="mx-auto max-w-[1160px] space-y-6 px-4 py-5 sm:px-8 sm:py-8">
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-violet-400/15 bg-gradient-to-r from-[#1D1934] to-[#101827] p-5">
        <div className="relative grid size-16 shrink-0 place-items-center rounded-2xl border border-violet-300/30 bg-gradient-to-br from-violet-500 to-indigo-900 text-white shadow-lg shadow-indigo-950/60" aria-hidden="true">
          <Bot className="size-9" strokeWidth={1.6} />
          <Sparkles className="absolute -right-2 -top-2 size-5 rounded-md bg-[#21194B] p-0.5 text-violet-200" />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {[workerStatus.worker, workerStatus.learning].map((status, index) => (
            <div
              key={index === 0 ? "worker" : "learning"}
              role="status"
              title={status.detail}
              aria-label={`${status.label}. ${status.detail}`}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1.5 text-xs font-medium ${statusColors[status.tone][0]}`}
            >
              <span className={`size-2 rounded-full ${statusColors[status.tone][1]}`} aria-hidden="true" />
              {status.label}
            </div>
          ))}
        </div>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2 text-right">
          {workerStatus.worker.tone === 'waiting' ? (
            <button type="button" onClick={onOpenQuotexLogin}
              className="inline-flex items-center gap-2 rounded-lg border border-sky-400/25 bg-sky-400/10 px-3.5 py-2.5 text-xs font-semibold text-sky-100 hover:bg-sky-400/20">
              <LogIn className="size-4" aria-hidden="true" />
              Open Quotex Login
            </button>
          ) : <button type="button" onClick={restart} disabled={restarting}
            className="inline-flex items-center gap-2 rounded-lg border border-violet-300/20 bg-violet-400/10 px-3.5 py-2.5 text-xs font-semibold text-violet-100 hover:bg-violet-400/20 disabled:opacity-50">
            <RotateCw className={restarting ? 'size-4 animate-spin' : 'size-4'} aria-hidden="true" />
            {restarting ? 'Restarting…' : 'Restart AI'}
          </button>}
          {restartMessage && <p role={restartFailed ? 'alert' : 'status'} className={`mt-1.5 text-xs ${restartFailed ? 'text-rose-300' : 'text-slate-300'}`}>{restartMessage}</p>}
        </div>
      </div>

      <section className="rounded-xl border border-[#202B3E] bg-[#111A29] px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <label htmlFor="stubay-min-confidence" className="text-sm font-medium text-slate-200">
            Minimum signal confidence
          </label>
          <input id="stubay-min-confidence" type="range" min={50} max={100} step={1}
            value={confidenceDraft ?? minimumConfidence ?? 50}
            aria-valuetext={`${confidenceDraft ?? minimumConfidence ?? 50}%`}
            disabled={minimumConfidence === null || confidenceSaving}
            onChange={(event) => {
              setConfidenceDraft(Number(event.target.value));
              setConfidenceError('');
            }}
            className="min-w-28 flex-1 accent-violet-400 disabled:opacity-50"
          />
          <output htmlFor="stubay-min-confidence" className="w-11 text-right text-sm tabular-nums text-violet-200">
            {minimumConfidence === null ? '—' : `${confidenceDraft ?? minimumConfidence}%`}
          </output>
          <button type="button" onClick={() => void saveConfidence()}
            disabled={confidenceSaving || confidenceDraft === null || confidenceDraft === minimumConfidence}
            className="rounded-md border border-violet-400/30 px-2.5 py-1.5 text-xs text-violet-200 disabled:opacity-50">
            {confidenceSaving ? 'Saving…' : 'Save'}
          </button>
        </div>
        {confidenceError && <p role="alert" className="mt-2 text-xs text-rose-300">{confidenceError}</p>}
      </section>

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {widgets.map(({ label, value, Icon, color }) => (
          <div key={label} className="rounded-xl border border-[#202B3E] bg-[#111A29] p-4">
            <dt className="flex items-center justify-between gap-2 text-xs font-medium text-slate-400">
              <span title={label === 'Accuracy' ? 'Wins divided by wins plus losses' : undefined}>{label}</span>
              <Icon className={`size-4 ${color}`} aria-hidden="true" />
            </dt>
            <dd className="mt-2 text-2xl font-semibold tabular-nums text-white">{value}</dd>
          </div>
        ))}
      </dl>

      <section aria-labelledby="stubay-trade-log" className="overflow-hidden rounded-xl border border-[#202B3E] bg-[#111A29]">
        <h2 id="stubay-trade-log" className="border-b border-[#202B3E] px-4 py-3 text-sm font-semibold text-white">Paper trade log</h2>
        {loadError && <p role="alert" className="px-4 pt-3 text-xs text-rose-300">{loadError}</p>}
        {!trades?.length ? (
          <p className="px-4 py-8 text-center text-sm text-slate-400">
            {trades ? 'No paper trades yet.' : loadError ? '' : 'Loading trades…'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-slate-400">
                <tr><th className="px-4 py-3">Time</th><th className="px-4 py-3">Asset</th><th className="px-4 py-3">Signal</th><th className="px-4 py-3">Result</th></tr>
              </thead>
              <tbody className="divide-y divide-[#202B3E] border-t border-[#202B3E]">
                {trades.map((trade) => {
                  const [outcome, tone] = outcomes[trade.status] ?? ['Unknown', 'text-slate-400'];
                  return (
                    <tr key={trade.trade_id} className="hover:bg-white/[0.025]">
                      <td className="whitespace-nowrap px-4 py-3 text-slate-400">{timeFormat.format(trade.decision_ts * 1000)}</td>
                      <td className="whitespace-nowrap px-4 py-3 font-medium text-white">{trade.asset}</td>
                      <td className="px-4 py-3 text-slate-300">{trade.direction}</td>
                      <td className="px-4 py-3"><span className={`rounded-md px-2 py-1 font-medium ${tone}`}>{outcome}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
