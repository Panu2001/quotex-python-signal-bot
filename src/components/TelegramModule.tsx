import { useCallback, useEffect, useState } from 'react';
import { Link2, Save } from 'lucide-react';
import type { BotConfig } from '../types/trading';

interface PaperLabSettings {
  telegram_enabled: boolean;
  telegram_allowed_chat_ids: string[];
}

interface SecretsStatus {
  telegram_token_configured: boolean;
}

export function TelegramModule({ botConfig }: { botConfig: BotConfig }) {
  const [statusBanner, setStatusBanner] = useState<{
    tone: 'ok' | 'warn' | 'err';
    text: string;
  } | null>(null);
  const [settings, setSettings] = useState<PaperLabSettings | null>(null);
  const [secretsStatus, setSecretsStatus] = useState<SecretsStatus | null>(null);
  const [chatIdsInput, setChatIdsInput] = useState('');
  const [tgTokenInput, setTgTokenInput] = useState('');
  const [savingSettings, setSavingSettings] = useState(false);

  const fetchSettings = useCallback(async () => {
    try {
      const resp = await fetch('/api/paper-lab/settings');
      if (!resp.ok) throw new Error('Settings unavailable');
      const data = await resp.json();
      if (data?.ok && data.settings) {
        const s = data.settings as PaperLabSettings;
        setSettings(s);
        setChatIdsInput((s.telegram_allowed_chat_ids || []).join(', '));
        if (data.secrets_status) {
          setSecretsStatus(data.secrets_status as SecretsStatus);
        }
      }
    } catch {
      setStatusBanner({ tone: 'err', text: 'Unable to load Telegram settings.' });
    }
  }, []);


  useEffect(() => { void fetchSettings(); }, [fetchSettings]);

  const handleSaveSettings = async (overridePayload?: Record<string, unknown>, customSuccessMsg?: string) => {
    if (!settings) return;
    setSavingSettings(true);
    setStatusBanner(null);
    try {
      const parsedChatIds = chatIdsInput
        .split(/[\s,]+/)
        .map((c) => c.trim())
        .filter(Boolean);

      const payload: Record<string, unknown> = {
        telegram_enabled: settings.telegram_enabled,
        telegram_allowed_chat_ids: parsedChatIds,
        ...(overridePayload ?? {}),
      };
      if (tgTokenInput.trim()) {
        payload.telegram_bot_token = tgTokenInput.trim();
      }

      const resp = await fetch('/api/paper-lab/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!data?.ok) {
        setStatusBanner({ tone: 'err', text: data?.error || 'Could not save Telegram settings.' });
        return;
      }
      if (data.secrets_status) setSecretsStatus(data.secrets_status as SecretsStatus);
      setTgTokenInput('');
      await fetchSettings();
      setStatusBanner({
        tone: 'ok',
        text:
          customSuccessMsg ||
          'Telegram settings saved.',
      });
    } catch {
      setStatusBanner({ tone: 'err', text: 'Failed to save Telegram settings.' });
    } finally {
      setSavingSettings(false);
    }
  };

  const handleSyncFromSignalDesk = async () => {
    if (!settings) return;
    const chatIds = new Set(
      chatIdsInput
        .split(/[\s,]+/)
        .map((c) => c.trim())
        .filter(Boolean)
    );
    if (botConfig.telegram_chat_id?.trim()) {
      chatIds.add(botConfig.telegram_chat_id.trim());
    }
    const nextChatList = Array.from(chatIds);
    setChatIdsInput(nextChatList.join(', '));

    const payload: Record<string, unknown> = {
      telegram_allowed_chat_ids: nextChatList,
      telegram_enabled: Boolean(
        (botConfig.telegram_bot_token || secretsStatus?.telegram_token_configured) && nextChatList.length > 0
      ),
    };
    if (botConfig.telegram_bot_token?.trim()) {
      payload.telegram_bot_token = botConfig.telegram_bot_token.trim();
    }
    await handleSaveSettings(
      payload,
      'Imported existing Telegram settings.'
    );
  };

  const [telegramCheck, setTelegramCheck] = useState<'detect' | 'test' | null>(null);
  const checkTelegram = async (action: 'detect' | 'test') => {
    const token = tgTokenInput.trim() || botConfig.telegram_bot_token?.trim();
    const chatId = chatIdsInput.split(/[\s,]+/).find(Boolean);
    if ((!token && !secretsStatus?.telegram_token_configured) || (action === 'test' && !chatId)) {
      setStatusBanner({ tone: 'err', text: action === 'test' ? 'Enter a bot token and chat ID.' : 'Enter a bot token.' });
      return;
    }
    setTelegramCheck(action);
    try {
      const response = await fetch(action === 'test' ? '/api/telegram/test' : '/api/telegram/detect-chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(action === 'test'
          ? { bot_token: token, chat_id: chatId, message: 'Stubay Ai test alert.' }
          : { bot_token: token }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error('Telegram check failed');
      if (action === 'detect') {
        const found = String(result.chat_id ?? '').trim();
        if (!/^-?\d+$/.test(found)) throw new Error('No chat ID returned');
        setChatIdsInput((existing) => [...new Set([...existing.split(/[\s,]+/).filter(Boolean), found])].join(', '));
      }
      setStatusBanner({ tone: 'ok', text: action === 'test' ? 'Test alert sent.' : 'Chat ID found.' });
    } catch {
      setStatusBanner({ tone: 'err', text: action === 'test'
        ? 'Test alert failed. Check the token and chat ID.'
        : 'Chat ID lookup failed. Check the token and send your bot a message first.' });
    } finally {
      setTelegramCheck(null);
    }
  };

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-2xl mx-auto">
      <div className="flex justify-end">
        <button type="button" disabled={savingSettings || !settings}
          onClick={() => void handleSyncFromSignalDesk()}
          className="inline-flex items-center gap-2 rounded-md border border-[#25324B] px-3 py-2 text-xs text-slate-200 disabled:opacity-50">
          <Link2 className="size-4" aria-hidden="true" />
          Import Telegram settings
        </button>
      </div>
      {/* Status Banner */}
      {statusBanner && (
        <div
          className={`rounded-lg border px-4 py-3 text-xs flex items-center justify-between gap-3 ${
            statusBanner.tone === 'ok'
              ? 'border-emerald-500/35 bg-emerald-500/10 text-emerald-200'
              : statusBanner.tone === 'warn'
              ? 'border-amber-500/35 bg-amber-500/10 text-amber-200'
              : 'border-rose-500/35 bg-rose-500/10 text-rose-200'
          }`}
        >
          <span>{statusBanner.text}</span>
          <button
            type="button"
            onClick={() => setStatusBanner(null)}
            className="text-current opacity-70 hover:opacity-100 cursor-pointer"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* No-Code Setup & Configuration Panel */}
      {settings && (
        <div className="rounded-xl border border-orange-500/30 bg-[#0F1523] p-5 space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1A2334] pb-3">
            <div>
              <h3 className="font-display text-sm font-semibold text-white">Paper signal alerts</h3>
            </div>
            <button
              type="button"
              disabled={savingSettings}
              onClick={() => void handleSaveSettings()}
              className="h-9 px-4 rounded-lg bg-orange-500 hover:bg-orange-400 text-slate-950 font-semibold text-xs inline-flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              <span>{savingSettings ? 'Saving…' : 'Save settings'}</span>
            </button>
          </div>

            <div className="rounded-lg border border-[#1A2334] bg-[#0B101A] p-3.5 space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-white">
                  Telegram alerts
                </span>
                <span
                  className={`px-2 py-0.5 rounded text-[10px] font-mono ${
                    secretsStatus?.telegram_token_configured
                      ? 'bg-emerald-500/15 text-emerald-300'
                      : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  {secretsStatus?.telegram_token_configured ? 'Token saved' : 'Token needed'}
                </span>
              </div>
              <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={settings.telegram_enabled}
                  onChange={(e) => setSettings({ ...settings, telegram_enabled: e.target.checked })}
                  className="accent-emerald-400"
                />
                <span>Send automatic paper signals</span>
              </label>
              <p className="text-[11px] text-slate-400 pl-5">
                Signals are sent to Telegram immediately whenever a qualified candlestick setup is detected (continuous scan, zero repainting). A Paper Trade is automatically opened at the same instant.
              </p>
              <div>
                <label htmlFor="telegram-chat-ids" className="block text-[10px] uppercase text-slate-400 mb-1">
                  Chat IDs (comma separated)
                </label>
                <input
                  id="telegram-chat-ids"
                  type="text"
                  value={chatIdsInput}
                  onChange={(e) => setChatIdsInput(e.target.value)}
                  placeholder="e.g. -1001234567890"
                  className="w-full h-8 px-2.5 rounded-md bg-[#0F1523] border border-[#25324B] text-xs text-white font-mono"
                />
              </div>
              <div>
                <label htmlFor="telegram-token" className="block text-[10px] uppercase text-slate-400 mb-1">
                  Bot token
                </label>
                <input
                  id="telegram-token"
                  type="password"
                  value={tgTokenInput}
                  onChange={(e) => setTgTokenInput(e.target.value)}
                  placeholder="Leave blank to keep the saved token"
                  className="w-full h-8 px-2.5 rounded-md bg-[#0F1523] border border-[#25324B] text-xs text-white font-mono"
                />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" disabled={telegramCheck !== null} onClick={() => void checkTelegram('detect')}
                  className="rounded border border-[#25324B] px-2.5 py-1.5 text-xs text-slate-200 disabled:opacity-50">
                  {telegramCheck === 'detect' ? 'Looking up…' : 'Find chat ID'}
                </button>
                <button type="button" disabled={telegramCheck !== null} onClick={() => void checkTelegram('test')}
                  className="rounded border border-[#25324B] px-2.5 py-1.5 text-xs text-slate-200 disabled:opacity-50">
                  {telegramCheck === 'test' ? 'Sending…' : 'Send test alert'}
                </button>
              </div>
            </div>
        </div>
      )}

      {!settings && !statusBanner && <p className="text-xs text-slate-400">Loading settings…</p>}
    </div>
  );
}
