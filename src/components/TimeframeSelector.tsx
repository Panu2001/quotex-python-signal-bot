import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

export const QUOTEX_TIMEFRAMES = [
  '5s',
  '10s',
  '15s',
  '30s',
  '1m',
  '2m',
  '3m',
  '5m',
  '10m',
  '15m',
  '30m',
  '1h',
  '4h',
  '1d',
] as const;

export type QuotexTimeframe = (typeof QUOTEX_TIMEFRAMES)[number];

interface TimeframeSelectorProps {
  value: string;
  onChange: (tf: string) => void;
  size?: 'sm' | 'md';
  align?: 'left' | 'right' | 'container';
  fullWidth?: boolean;
  ariaLabel?: string;
}

export const TimeframeSelector: React.FC<TimeframeSelectorProps> = ({
  value,
  onChange,
  size = 'sm',
  align = 'left',
  fullWidth = false,
  ariaLabel = 'Select timeframe',
}) => {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const triggerClasses =
    size === 'md'
      ? 'h-9 px-3 rounded-lg bg-[#0B101A] border border-[#232F46]'
      : 'h-8 px-2.5 rounded-md bg-[#121928] border border-[#232F46]';

  return (
    <div
      ref={containerRef}
      className={`${align === 'container' ? '' : 'relative'} inline-block ${fullWidth ? 'w-full' : ''}`}
    >
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={`${triggerClasses} ${
          fullWidth ? 'w-full justify-between' : 'min-w-[68px] justify-between'
        } inline-flex items-center gap-2 text-xs font-mono font-medium text-slate-100 hover:bg-[#1A2336] focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500 transition-colors cursor-pointer`}
      >
        <span>{value}</span>
        <ChevronDown
          className={`w-3.5 h-3.5 text-slate-400 transition-transform ${
            open ? 'rotate-180' : ''
          }`}
        />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Quotex timeframes"
          className={`absolute ${
            align === 'right' ? 'right-0' : 'left-0'
          } mt-1.5 w-[248px] max-w-[calc(100vw-2.5rem)] p-2.5 rounded-lg bg-[#141A29] border border-[#25324B] shadow-2xl z-50`}
        >
          <div className="grid grid-cols-4 gap-1.5">
            {QUOTEX_TIMEFRAMES.map((tf) => {
              const isSelected = tf === value;
              return (
                <button
                  key={tf}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  onClick={() => {
                    onChange(tf);
                    setOpen(false);
                  }}
                  className={`h-9 rounded-md text-[13px] font-sans font-medium tabular-nums transition-colors cursor-pointer flex items-center justify-center border ${
                    isSelected
                      ? 'bg-orange-500/15 border-orange-500/45 text-orange-300 font-semibold'
                      : 'bg-transparent border-transparent text-slate-100 hover:bg-[#1E2940] hover:border-[#2C3C5B]'
                  }`}
                >
                  {tf}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
