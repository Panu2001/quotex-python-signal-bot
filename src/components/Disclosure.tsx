import type { ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

interface DisclosureProps {
  label: string;
  children: ReactNode;
  className?: string;
  initiallyOpen?: boolean;
}

export function Disclosure({ label, children, className = '', initiallyOpen = false }: DisclosureProps) {
  return (
    <details open={initiallyOpen || undefined} className={`group/disclosure min-w-0 rounded-lg border border-[#1A2334] bg-[#0F1523] ${className}`}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-lg px-4 py-3 text-sm font-medium text-slate-200 hover:bg-[#141C2C] focus-visible:outline-2 focus-visible:outline-orange-400 [&::-webkit-details-marker]:hidden">
        {label}
        <ChevronDown aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-400 group-open/disclosure:rotate-180" />
      </summary>
      <div className="min-w-0 space-y-3 border-t border-[#1A2334] p-3">{children}</div>
    </details>
  );
}
