import { useState } from 'react';
import { ArrowUpRight, Globe } from 'lucide-react';

export function QuotexBrowserModule() {
  const [launching, setLaunching] = useState(false);
  const [status, setStatus] = useState('');

  async function launch() {
    setLaunching(true);
    setStatus('');
    try {
      const response = await fetch('/api/quotex/browser', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-SignalDesk-Action': 'quotex-browser' },
        body: '{}',
      });
      if (response.status === 404) {
        setStatus('This backend does not include Quotex Browser. Restart the updated SignalDesk Windows app.');
        return;
      }
      if (response.status === 501) {
        setStatus('The embedded browser is available in the SignalDesk Windows app.');
        return;
      }
      const result = await response.json();
      setStatus(response.ok && result.ok ? 'Quotex Browser window opened.' : result.error || 'Could not open Quotex Browser.');
    } catch {
      setStatus('Could not reach the SignalDesk browser launcher.');
    } finally {
      setLaunching(false);
    }
  }

  return (
    <section className="mx-auto max-w-2xl space-y-4 p-6">
      <div className="space-y-4 rounded-xl border border-[#232F46] bg-[#101725] p-5">
        <p className="text-sm text-slate-300">View the live chart and market list in the Quotex Browser window. Sign in on the Quotex page when prompted.</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" disabled={launching} onClick={() => void launch()}
            className="inline-flex items-center gap-2 rounded-lg bg-sky-500 px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-60">
            <Globe className="size-4" aria-hidden="true" />
            {launching ? 'Opening…' : 'Open Quotex Browser'}
          </button>
          <a href="https://quotex.com/" target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#39445E] px-3 py-2 text-sm text-slate-200 hover:bg-white/5">
            Open official site <ArrowUpRight className="size-4" aria-hidden="true" />
          </a>
        </div>
        {status && <p role="status" className="text-sm text-slate-300">{status}</p>}
      </div>
    </section>
  );
}
