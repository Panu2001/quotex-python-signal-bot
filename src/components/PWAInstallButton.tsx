import React, { useState } from 'react';
import { MonitorDown } from 'lucide-react';
import { usePWAInstall } from '../hooks/usePWAInstall';

interface PWAInstallButtonProps {
  onOpenWindowsModule?: () => void;
}

export const PWAInstallButton: React.FC<PWAInstallButtonProps> = ({
  onOpenWindowsModule,
}) => {
  const { isInstallable, isInstalled, isIOS, install } = usePWAInstall();
  const [showIOSGuide, setShowIOSGuide] = useState(false);

  if (isInstalled) {
    return null;
  }

  if (isInstallable) {
    return (
      <button
        type="button"
        onClick={() => {
          void install();
        }}
        className="h-8 px-3 inline-flex items-center gap-1.5 text-xs font-medium rounded-md bg-orange-500/15 border border-orange-500/40 text-orange-300 hover:bg-orange-500/25 transition-colors whitespace-nowrap shrink-0 cursor-pointer"
      >
        <MonitorDown className="w-3.5 h-3.5" />
        <span>Install App</span>
      </button>
    );
  }

  if (isIOS) {
    return (
      <>
        <button
          type="button"
          onClick={() => setShowIOSGuide(true)}
          className="h-8 px-3 inline-flex items-center gap-1.5 text-xs font-medium rounded-md bg-[#161F31] border border-[#25324B] text-slate-200 hover:bg-[#1E2A40] transition-colors whitespace-nowrap shrink-0 cursor-pointer"
        >
          <MonitorDown className="w-3.5 h-3.5" />
          <span>Install</span>
        </button>

        {showIOSGuide && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-4">
            <div className="w-full max-w-sm rounded-lg border border-[#1E293D] bg-[#0F1523] p-5 shadow-xl">
              <h3 className="text-sm font-display font-semibold text-slate-100">
                Install SignalDesk on iOS
              </h3>
              <p className="mt-2 text-xs text-slate-300 leading-relaxed">
                1. Tap the <strong className="text-white">Share</strong> button in Safari.<br />
                2. Scroll down and choose <strong className="text-white">Add to Home Screen</strong>.
              </p>
              <button
                type="button"
                onClick={() => setShowIOSGuide(false)}
                className="mt-4 w-full py-2 text-xs font-medium rounded-md bg-[#161F31] border border-[#25324B] text-slate-200 hover:bg-[#1E2A40] cursor-pointer"
              >
                Close
              </button>
            </div>
          </div>
        )}
      </>
    );
  }

  if (onOpenWindowsModule) {
    return (
      <button
        type="button"
        onClick={onOpenWindowsModule}
        title="Install SignalDesk as a Windows Desktop App"
        className="hidden md:inline-flex h-8 px-3 items-center gap-1.5 text-xs font-medium rounded-md bg-[#161F31] border border-[#25324B] text-slate-200 hover:bg-[#1E2A40] transition-colors whitespace-nowrap shrink-0 cursor-pointer"
      >
        <MonitorDown className="w-3.5 h-3.5 text-orange-400" />
        <span>Install Windows App</span>
      </button>
    );
  }

  return null;
};
