import React, { useEffect, useMemo, useState } from 'react';
import { Download, LogIn, Search, Trash2 } from 'lucide-react';
import type { EngineLogEntry } from '../types/trading';

interface LogsModuleProps {
  logs: EngineLogEntry[];
  quotexConnected: boolean;
  onOpenQuotexLogin: () => void;
  onClearLogs: () => void;
}

const MODULES = ['All', 'Engine', 'AI', 'Signals', 'Telegram', 'Connection'] as const;

export const LogsModule: React.FC<LogsModuleProps> = ({
  logs,
  quotexConnected,
  onOpenQuotexLogin,
  onClearLogs,
}) => {
  const [moduleFilter, setModuleFilter] = useState<(typeof MODULES)[number]>('All');
  const [query, setQuery] = useState('');

  const [auditLogs, setAuditLogs] = useState<EngineLogEntry[]>([]);
  const [syncFailed, setSyncFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    async function refresh() {
      try {
        const response = await fetch('/api/paper-lab/history?table=audit_logs&limit=200', {
          cache: 'no-store', signal: controller.signal,
        });
        const result = await response.json();
        if (!response.ok || !result.ok || !Array.isArray(result.rows)) throw new Error('Audit log unavailable');
        if (controller.signal.aborted) return;
        setAuditLogs(result.rows.map((row: {log_id: string; ts: number; level: string; category: string; message: string}): EngineLogEntry => ({
          id: row.log_id,
          timestamp: row.ts * 1000,
          level: row.level === 'ERROR' ? 'ERROR' : row.level === 'WARN' || row.level === 'WARNING' ? 'WARN' : row.level === 'SIGNAL' ? 'SIGNAL' : 'INFO',
          module: /telegram/i.test(row.category) ? 'Telegram' : /stubay|learning/i.test(row.category) ? 'AI' : 'Engine',
          message: row.message,
        })));
        setSyncFailed(false);
      } catch {
        if (!controller.signal.aborted) setSyncFailed(true);
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(refresh, 5000);
      }
    }
    void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, []);

  const allLogs = useMemo(
    () => [...logs, ...auditLogs].sort((a, b) => b.timestamp - a.timestamp),
    [logs, auditLogs],
  );

  const filtered = useMemo(() => {
    return allLogs.filter((entry) => {
      if (moduleFilter !== 'All' && entry.module !== moduleFilter) return false;
      if (query.trim()) {
        return entry.message.toLowerCase().includes(query.toLowerCase());
      }
      return true;
    });
  }, [allLogs, moduleFilter, query]);

  const exportLogs = () => {
    const lines = allLogs
      .map(
        (l) =>
          `[${new Date(l.timestamp).toISOString()}] [${l.level}] [${l.module}] ${l.message}`
      )
      .join('\n');
    const blob = new Blob([lines], { type: 'text/plain;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'signaldesk_engine.log';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="p-6 max-w-[1380px] mx-auto space-y-5">
      {!quotexConnected && (
        <div className="border border-amber-500/35 rounded-lg bg-amber-500/8 px-4 py-3 flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs text-slate-300">
            <span className="font-semibold text-amber-300">Quotex Disconnected:</span> You can still view saved engine events.
          </div>
          <button
            type="button"
            onClick={onOpenQuotexLogin}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-md bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors cursor-pointer shrink-0"
          >
            <LogIn className="w-3.5 h-3.5" />
            <span>Open Quotex Login</span>
          </button>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-sm text-slate-300">
            {allLogs.length} {allLogs.length === 1 ? 'event' : 'events'} · {syncFailed ? 'Reconnecting' : 'Auto-refreshes every 5s'}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              type="text"
              placeholder="Search logs"
              aria-label="Search logs"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="h-9 pl-8 pr-3 w-48 bg-[#101725] border border-[#1E293D] rounded-lg text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500"
            />
          </div>

          <button
            type="button"
            onClick={exportLogs}
            disabled={allLogs.length === 0}
            className="h-9 inline-flex items-center gap-1.5 px-3 text-xs font-medium rounded-lg bg-[#121928] border border-[#232F46] text-slate-200 hover:bg-[#1A2336] transition-colors cursor-pointer disabled:opacity-45"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Download logs</span>
          </button>

          <button
            type="button"
            onClick={onClearLogs}
            disabled={logs.length === 0}
            className="h-9 inline-flex items-center gap-1.5 px-3 text-xs font-medium rounded-lg bg-[#121928] border border-[#232F46] text-slate-300 hover:text-rose-300 transition-colors cursor-pointer disabled:opacity-45"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Clear</span>
          </button>
        </div>
      </div>

      {/* Module Tabs */}
      <div className="flex flex-wrap items-center gap-1.5">
        {MODULES.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setModuleFilter(m)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors cursor-pointer ${
              moduleFilter === m
                ? 'bg-orange-500/12 border-orange-500/35 text-orange-300'
                : 'bg-[#101725] border-[#1C2639] text-slate-400 hover:text-slate-200'
            }`}
          >
            {m}
          </button>
        ))}
      </div>

      {/* Console Log Stream */}
      <div className="border border-[#1A2334] rounded-lg bg-[#090D16] overflow-hidden">
        <div className="px-4 py-2.5 border-b border-[#172033] bg-[#0F1523] flex items-center justify-between text-xs text-slate-400">
          <span>Python Engine Activity Stream</span>
          <span className="font-mono tabular-nums">{filtered.length} shown</span>
        </div>

        <div className="divide-y divide-[#131B2C] max-h-[560px] overflow-y-auto font-mono text-xs tabular-nums">
          {filtered.length === 0 ? (
            <div className="py-12 text-center font-sans text-slate-500">
              No log entries match the current filter.
            </div>
          ) : (
            filtered.map((entry) => (
              <div
                key={entry.id}
                className="px-4 py-2.5 flex items-start gap-4 hover:bg-[#101726] transition-colors"
              >
                <span className="text-slate-500 shrink-0">
                  {new Date(entry.timestamp).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: false,
                  })}
                </span>
                <span
                  className={`w-14 shrink-0 font-semibold ${
                    entry.level === 'SIGNAL'
                      ? 'text-emerald-400'
                      : entry.level === 'ERROR'
                      ? 'text-rose-400'
                      : entry.level === 'WARN'
                      ? 'text-amber-400'
                      : 'text-sky-400'
                  }`}
                >
                  {entry.level}
                </span>
                <span className="w-24 shrink-0 text-slate-400">{entry.module}</span>
                <span className="text-slate-200 break-words flex-1">{entry.message}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
