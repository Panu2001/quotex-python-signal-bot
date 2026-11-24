import { useEffect } from 'react';

const deliveredKey = 'stubay_desktop_alerts_v1';

export function upcomingAlerts(rows: unknown[], now: number) {
  return rows.flatMap((value) => {
    if (!value || typeof value !== 'object') return [];
    const row = value as Record<string, unknown>;
    if (row.status !== 'SCHEDULED' || typeof row.asset !== 'string' || typeof row.bucket !== 'number' ||
      typeof row.plan_json !== 'string') return [];
    try {
      const plan = JSON.parse(row.plan_json);
      if ((plan?.signal !== 'CALL' && plan?.signal !== 'PUT') ||
        !Number.isFinite(plan.entry_ts) || plan.entry_ts <= now ||
        !Number.isFinite(plan.expiry_ts) || plan.expiry_ts <= plan.entry_ts) return [];
      return [{
        id: `stubay:${row.bucket}:${row.asset}`, asset: row.asset, direction: plan.signal as 'CALL' | 'PUT',
        entry: plan.entry_ts as number, expiry: plan.expiry_ts as number,
      }];
    } catch {
      return [];
    }
  });
}

export function useStubayAlerts(enabled: boolean, onOpen: () => void, onBlocked: () => void) {
  useEffect(() => {
    if (!enabled || !('Notification' in window) || Notification.permission !== 'granted') return;
    const controller = new AbortController();
    const seen = new Set<string>();
    let timer: number | undefined;
    let initialized = false;
    let reportedError = false;

    const poll = async () => {
      try {
        const response = await fetch('/api/paper-lab/history?table=stubay_memory&limit=50', {
          signal: controller.signal, cache: 'no-store',
        });
        const result = await response.json();
        if (!response.ok || !result.ok || !Array.isArray(result.rows) || controller.signal.aborted) return;
        const now = Math.floor(Date.now() / 1000);
        const alerts = upcomingAlerts(result.rows, now);
        const deliver = () => {
          if (controller.signal.aborted) return;
          let delivered: Record<string, number> = {};
          try {
            const previous = JSON.parse(localStorage.getItem(deliveredKey) ?? '{}');
            delivered = Object.fromEntries(Object.entries(previous).filter(([, expiry]) =>
              typeof expiry === 'number' && expiry > now)) as Record<string, number>;
          } catch {
            // If storage is unavailable, deduplication still works in this tab.
          }
          for (const alert of alerts) {
            if (seen.has(alert.id)) continue;
            seen.add(alert.id);
            if (!initialized || delivered[alert.id] || Math.floor(Date.now() / 1000) >= alert.entry ||
              Notification.permission !== 'granted') continue;
            try {
              const notification = new Notification(`Stubay Ai · ${alert.asset} ${alert.direction}`, {
                body: `Paper signal · Entry ${new Date(alert.entry * 1000).toLocaleTimeString()}`,
                tag: alert.id,
              });
              notification.onclick = () => { window.focus(); onOpen(); notification.close(); };
              delivered[alert.id] = alert.expiry;
            } catch {
              if (!reportedError) onBlocked();
              reportedError = true;
            }
          }
          initialized = true;
          try { localStorage.setItem(deliveredKey, JSON.stringify(delivered)); } catch { /* optional */ }
        };
        if (navigator.locks) await navigator.locks.request(deliveredKey, deliver);
        else deliver();
      } catch {
        // A temporary backend failure does not mark any alerts as seen.
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, 5000);
      }
    };

    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [enabled, onOpen, onBlocked]);
}
