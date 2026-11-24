import { useState } from 'react';
import { Download } from 'lucide-react';

export function WindowsAppModule({ onDownloadWindowsInstaller }: {
  onDownloadWindowsInstaller: () => Promise<void>;
}) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');

  async function download() {
    setDownloading(true);
    setError('');
    try {
      await onDownloadWindowsInstaller();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Windows download failed.');
    } finally {
      setDownloading(false);
    }
  }

  return (
    <section className="mx-auto max-w-2xl space-y-4 p-6">
      <div className="space-y-4 rounded-xl border border-[#232F46] bg-[#101725] p-5">
        <p className="text-sm text-slate-300">Extract the ZIP and run Install_SignalDesk.bat. Use the same Windows account to keep your Stubay learning data across installations.</p>
        <button type="button" onClick={download} disabled={downloading}
          className="inline-flex items-center gap-2 rounded-lg bg-orange-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-orange-400 disabled:opacity-60">
          <Download className="h-4 w-4" />
          {downloading ? 'Preparing Windows app…' : 'Download Windows app (.zip)'}
        </button>
        {error && <p role="alert" className="text-sm text-rose-300">{error}</p>}
      </div>
    </section>
  );
}
