import { Disclosure } from './Disclosure';
import React, { useEffect, useState } from 'react';
import { Check, Copy, Download, FileCode } from 'lucide-react';
import type { BotFile } from '../types/trading';

interface PythonCodeViewProps {
  onDownloadZip: () => void;
  onDownloadCompleteSource: () => Promise<void>;
}

export const PythonCodeView: React.FC<PythonCodeViewProps> = ({
  onDownloadZip,
  onDownloadCompleteSource,
}) => {
  const [files, setFiles] = useState<BotFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<string>('bot.py');
  const [copied, setCopied] = useState(false);
  const [downloadingSource, setDownloadingSource] = useState(false);

  useEffect(() => {
    fetch('/api/bot-files')
      .then((r) => r.json())
      .then((d) => {
        if (Array.isArray(d.files)) {
          setFiles(d.files);
          if (d.files.some((f: BotFile) => f.name === 'bot.py')) {
            setSelectedFile('bot.py');
          } else if (d.files.length > 0) {
            setSelectedFile(d.files[0].name);
          }
        }
      })
      .catch(() => {});
  }, []);

  const currentFile = files.find((f) => f.name === selectedFile);

  const handleCopy = () => {
    if (!currentFile) return;
    navigator.clipboard.writeText(currentFile.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  return (
    <div className="p-6 max-w-[1380px] mx-auto space-y-6">
      {/* Header & Download Strip */}
      <div className="flex flex-wrap items-center justify-between gap-4 border border-[#1A2334] rounded-lg bg-[#0F1523] p-5">
        <div className="space-y-1">
          <h2 className="text-base font-display font-semibold text-slate-100">
            Download the Python bot
          </h2>
          <p className="text-xs text-slate-400">
            Includes your current settings in <code className="text-slate-200">config.json</code>.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            disabled={downloadingSource}
            onClick={async () => {
              setDownloadingSource(true);
              try {
                await onDownloadCompleteSource();
              } finally {
                setDownloadingSource(false);
              }
            }}
            className="inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-lg bg-[#121928] border border-sky-500/40 text-sky-300 hover:bg-sky-500/15 transition-colors whitespace-nowrap shrink-0 cursor-pointer disabled:opacity-50"
          >
            <Download className="w-3.5 h-3.5" />
            <span>{downloadingSource ? 'Packaging Source...' : 'Download Complete Source (.zip)'}</span>
          </button>
          <button
            type="button"
            onClick={onDownloadZip}
            className="inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-lg bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Download Python Bot (.zip)</span>
          </button>
        </div>
      </div>

      <Disclosure label="Run the Python bot">
        <p className="text-xs text-slate-400">Extract the ZIP and run this command in the extracted folder.</p>
          <div className="px-3 py-2 rounded-md bg-[#0B101A] border border-[#1C2639] font-mono text-xs text-slate-300">
            pip install -r requirements.txt && python3 bot.py
          </div>
      </Disclosure>

      {/* File Explorer + Source Viewer */}
      <div className="grid grid-cols-1 lg:grid-cols-12 border border-[#1A2334] rounded-lg bg-[#0F1523] overflow-hidden">
        <div className="lg:col-span-3 border-b lg:border-b-0 lg:border-r border-[#1A2334] bg-[#0D1320] divide-y divide-[#172033]">
          <div className="px-4 py-3 text-xs font-display font-semibold text-slate-300">
            python_bot/ ({files.length} files)
          </div>
          <div className="max-h-[560px] overflow-y-auto divide-y divide-[#151D2E]">
            {files.map((f) => (
              <button
                key={f.name}
                type="button"
                onClick={() => setSelectedFile(f.name)}
                className={`w-full text-left px-4 py-2.5 flex items-center justify-between gap-2 text-xs font-mono transition-colors cursor-pointer ${
                  f.name === selectedFile
                    ? 'bg-orange-500/12 text-orange-300'
                    : 'text-slate-300 hover:bg-[#141D2F]'
                }`}
              >
                <span className="inline-flex items-center gap-2 truncate">
                  <FileCode className="w-3.5 h-3.5 shrink-0 text-slate-500" />
                  <span className="truncate">{f.name}</span>
                </span>
                <span className="text-[11px] text-slate-500 tabular-nums shrink-0">
                  {(f.size / 1024).toFixed(1)}k
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="lg:col-span-9 flex flex-col bg-[#090D16]">
          <div className="px-4 py-2.5 border-b border-[#1A2334] bg-[#0F1523] flex items-center justify-between">
            <span className="text-xs font-mono text-slate-200">{selectedFile}</span>
            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded border border-[#232F46] bg-[#121928] text-slate-300 hover:bg-[#1A2336] transition-colors cursor-pointer"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copied ? 'Copied' : 'Copy File'}</span>
            </button>
          </div>
          <pre className="p-4 text-xs font-mono text-slate-200 overflow-auto max-h-[560px] leading-relaxed">
            <code>{currentFile?.content ?? 'Loading Python source...'}</code>
          </pre>
        </div>
      </div>
    </div>
  );
};
