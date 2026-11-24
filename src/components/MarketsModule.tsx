import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Download,
  LogIn,
  Plus,
  RefreshCw,
  Search,
  Star,
  Trash2,
  X,
} from 'lucide-react';
import type { AssetMeta, ScanPairResult } from '../types/trading';

export interface CustomAssetInput {
  symbol: string;
  name: string;
  category: string;
  payout: number;
  base: number;
  decimals: number;
  source: 'otc' | 'quotex';
}

interface MarketsModuleProps {
  assets: Record<string, AssetMeta>;
  scanResults: ScanPairResult[];
  selectedSymbol: string;
  quotexConnected: boolean;
  onOpenQuotexLogin: () => void;
  onOpenChart: (symbol: string) => void;
  onRefreshScan: () => void;
  onAddCustomAsset: (asset: CustomAssetInput) => Promise<{ ok: boolean; error?: string }>;
  onRemoveCustomAsset: (symbol: string) => Promise<void>;
  isLoading: boolean;
}

const CATEGORIES = [
  'All',
  'OTC Forex',
  'Live Forex',
  'Crypto Live',
  'Commodities',
  'Indices & Stocks',
];

const CURRENCY_REGIONS: Record<string, string> = {
  AUD: 'AU', BRL: 'BR', CAD: 'CA', CHF: 'CH', CNY: 'CN', COP: 'CO', EUR: 'EU',
  GBP: 'GB', HKD: 'HK', IDR: 'ID', INR: 'IN', JPY: 'JP', MXN: 'MX', NGN: 'NG',
  NZD: 'NZ', PKR: 'PK', SGD: 'SG', TRY: 'TR', USD: 'US', ZAR: 'ZA',
};

function currencyFlags(symbol: string) {
  const pair = /^([A-Z]{3})([A-Z]{3})(?:-OTC)?$/.exec(symbol);
  if (!pair) return '';
  const regions = [CURRENCY_REGIONS[pair[1]], CURRENCY_REGIONS[pair[2]]];
  if (!regions.every(Boolean)) return '';
  return regions.map((region) =>
    String.fromCodePoint(...Array.from(region, (letter) => 127397 + letter.charCodeAt(0)))
  ).join('');
}

export const MarketsModule: React.FC<MarketsModuleProps> = ({
  assets,
  scanResults,
  selectedSymbol,
  quotexConnected,
  onOpenQuotexLogin,
  onOpenChart,
  onRefreshScan,
  onAddCustomAsset,
  onRemoveCustomAsset,
  isLoading,
}) => {
  const [category, setCategory] = useState('All');
  const [query, setQuery] = useState('');

  const [favorites, setFavorites] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem('signaldesk_market_favorites') || '[]');
      return Array.isArray(saved) ? saved.filter((value): value is string => typeof value === 'string') : [];
    } catch {
      return [];
    }
  });
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [sortPayout, setSortPayout] = useState<'payout_1m' | 'payout_5m' | null>(null);

  useEffect(() => {
    try { localStorage.setItem('signaldesk_market_favorites', JSON.stringify(favorites)); } catch { /* storage unavailable */ }
  }, [favorites]);

  const [trackedAssets, setTrackedAssets] = useState<string[] | null>(null);
  const [trackingError, setTrackingError] = useState('');
  const [savingAsset, setSavingAsset] = useState<string | null>(null);
  const savingAssetRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    async function loadTracking() {
      try {
        const response = await fetch('/api/paper-lab/settings', { cache: 'no-store', signal: controller.signal });
        const result = await response.json();
        if (!response.ok || !result.ok || !Array.isArray(result.settings?.asset_allowlist)) {
          throw new Error('Tracking settings unavailable');
        }
        if (!controller.signal.aborted) setTrackedAssets(result.settings.asset_allowlist);
      } catch {
        if (!controller.signal.aborted) setTrackingError('Unable to load Stubay tracking. Reopen Markets to retry.');
      }
    }
    void loadTracking();
    return () => controller.abort();
  }, []);

  async function updateTracking(symbols: string[], enabled: boolean) {
    if (savingAssetRef.current) return;
    savingAssetRef.current = true;
    setSavingAsset(symbols.length === 1 ? symbols[0] : "bulk");
    setTrackingError('');
    try {
      const response = await fetch('/api/paper-lab/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset_tracking: { symbols, enabled } }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok || !Array.isArray(result.overview?.asset_allowlist)) {
        throw new Error('Tracking settings could not be saved');
      }
      setTrackedAssets(result.overview.asset_allowlist);
    } catch {
      setTrackingError('Unable to save Stubay tracking. Try again.');
    } finally {
      savingAssetRef.current = false;
      setSavingAsset(null);
    }
  }

  // Add Custom Asset panel state
  const [showAddForm, setShowAddForm] = useState(false);
  const [symbol, setSymbol] = useState('');
  const [name, setName] = useState('');
  const [newCategory, setNewCategory] = useState('OTC Forex');
  const [payout, setPayout] = useState('87');
  const [basePrice, setBasePrice] = useState('1.0500');
  const [decimals, setDecimals] = useState('5');
  const [source, setSource] = useState<'otc' | 'quotex'>('otc');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const marketRows = useMemo(() => {
    type MarketRow = Pick<ScanPairResult, 'symbol' | 'name' | 'category' | 'payout' | 'decimals' | 'price' | 'feed_source' | 'custom' | 'is_live' | 'live_required' | 'change_24h' | 'payout_1m' | 'payout_5m'>;
    const rows = new Map<string, MarketRow>();
    for (const [symbol, asset] of Object.entries(assets)) {
      rows.set(symbol, { symbol, ...asset, price: 0, feed_source: "", live_required: !quotexConnected });
    }
    for (const pair of scanResults) rows.set(pair.symbol, pair);
    for (const symbol of trackedAssets ?? []) {
      if (!rows.has(symbol)) rows.set(symbol, { symbol, name: symbol, category: 'Other', payout: 0, price: 0, decimals: 0, feed_source: "" });
    }
    return Array.from(rows.values());
  }, [assets, quotexConnected, scanResults, trackedAssets]);

  const filtered = useMemo(() => {
    const rows = marketRows.filter((p) => {
      if (favoritesOnly && !favorites.includes(p.symbol)) return false;
      if (category !== 'All' && p.category !== category) return false;
      const q = query.trim().toLowerCase();
      return p.name.toLowerCase().includes(q) || p.symbol.toLowerCase().includes(q);
    });
    return sortPayout ? rows.sort((a, b) => (b[sortPayout] ?? -1) - (a[sortPayout] ?? -1)) : rows;
  }, [marketRows, category, query, favorites, favoritesOnly, sortPayout]);

  const handleSubmitAsset = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (!symbol.trim()) {
      setFormError('Enter a Quotex symbol such as USDNGN-OTC, NZDJPY-OTC, or EURUSD.');
      return;
    }
    setSaving(true);
    const res = await onAddCustomAsset({
      symbol: symbol.trim().toUpperCase(),
      name: name.trim() || symbol.trim().toUpperCase(),
      category: newCategory,
      payout: Number(payout) || 85,
      base: Number(basePrice) || 1.0,
      decimals: Number(decimals) || 5,
      source,
    });
    setSaving(false);
    if (!res.ok) {
      setFormError(res.error || 'Could not add custom asset.');
      return;
    }
    setSymbol('');
    setName('');
    setShowAddForm(false);
  };

  return (
    <div className="p-6 max-w-[1380px] mx-auto space-y-5">
      {!quotexConnected && (
        <div className="border border-amber-500/35 rounded-lg bg-amber-500/8 px-4 py-3.5 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold text-amber-300">
              Connect to Quotex for live prices
            </div>
          </div>
          <button
            type="button"
            onClick={onOpenQuotexLogin}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors cursor-pointer shrink-0"
          >
            <LogIn className="w-3.5 h-3.5" />
            <span>Quotex Login</span>
          </button>
        </div>
      )}

      {/* Header Controls */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              type="text"
              placeholder="Search assets"
              aria-label="Search assets"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="h-9 pl-8 pr-3 w-48 bg-[#101725] border border-[#1E293D] rounded-lg text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500"
            />
          </div>

          <button
            type="button"
            onClick={() => {
              setShowAddForm((v) => !v);
              setFormError(null);
            }}
            className="h-9 px-3.5 inline-flex items-center gap-1.5 text-xs font-semibold rounded-lg bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors cursor-pointer whitespace-nowrap"
          >
            {showAddForm ? <X className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
            <span>{showAddForm ? 'Close' : 'Add Asset'}</span>
          </button>

          <button
            type="button"
            onClick={onRefreshScan}
            disabled={isLoading}
            className="h-9 px-3.5 inline-flex items-center gap-1.5 text-xs font-medium rounded-lg bg-[#121928] border border-[#232F46] text-slate-200 hover:bg-[#1A2336] transition-colors cursor-pointer disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            <span>{isLoading ? 'Scanning…' : 'Scan Now'}</span>
          </button>

          <div className="h-9 px-3 inline-flex items-center gap-2 text-xs font-medium rounded-lg bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 select-none">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
            <span>Continuous Real-Time Scanner Active</span>
          </div>
        </div>
      </div>

      {/* Add Custom Asset Panel */}
      {showAddForm && (
        <form
          onSubmit={handleSubmitAsset}
          className="border border-orange-500/35 rounded-lg bg-[#0F1523] p-5 space-y-4"
        >
          <div className="flex items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-display font-semibold text-slate-100">
                Add Custom Quotex Asset
              </h3>
              <p className="text-xs text-slate-400 mt-0.5">
                Add any Quotex OTC or Quotex Live pair, stock, commodity, or crypto symbol (e.g.{' '}
                <code className="text-slate-200">USDNGN-OTC</code>,{' '}
                <code className="text-slate-200">MCD-OTC</code>,{' '}
                <code className="text-slate-200">EURUSD</code>).
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-7 gap-3">
            <label className="block lg:col-span-1">
              <span className="text-[11px] text-slate-400">Symbol</span>
              <input
                type="text"
                required
                placeholder="USDNGN-OTC"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-2 text-xs font-mono text-slate-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500"
              />
            </label>

            <label className="block lg:col-span-2">
              <span className="text-[11px] text-slate-400">Display Name</span>
              <input
                type="text"
                placeholder="USD/NGN (OTC)"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-2 text-xs text-slate-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-orange-500"
              />
            </label>

            <label className="block">
              <span className="text-[11px] text-slate-400">Category</span>
              <select
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
                className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-2 text-xs text-slate-100"
              >
                <option value="OTC Forex">OTC Forex</option>
                <option value="Live Forex">Live Forex</option>
                <option value="Crypto Live">Crypto Live</option>
                <option value="Commodities">Commodities</option>
                <option value="Indices & Stocks">Indices & Stocks</option>
              </select>
            </label>

            <label className="block">
              <span className="text-[11px] text-slate-400">Feed Source</span>
              <select
                value={source}
                onChange={(e) => {
                  const next = e.target.value as 'otc' | 'quotex';
                  setSource(next);
                  if (next === 'quotex') setNewCategory('Live Forex');
                  if (next === 'otc') setNewCategory('OTC Forex');
                }}
                className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-2 text-xs text-slate-100"
              >
                <option value="otc">Quotex OTC (pyquotex)</option>
                <option value="quotex">Quotex Live Market (pyquotex)</option>
              </select>
            </label>

            <label className="block">
              <span className="text-[11px] text-slate-400">Base Price / Dec</span>
              <div className="mt-1 grid grid-cols-2 gap-1.5">
                <input
                  type="number"
                  step="any"
                  value={basePrice}
                  onChange={(e) => setBasePrice(e.target.value)}
                  className="w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2 py-2 text-xs font-mono text-slate-100"
                />
                <input
                  type="number"
                  min={0}
                  max={6}
                  value={decimals}
                  onChange={(e) => setDecimals(e.target.value)}
                  className="w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2 py-2 text-xs font-mono text-slate-100"
                />
              </div>
            </label>

            <label className="block">
              <span className="text-[11px] text-slate-400">Payout %</span>
              <input
                type="number"
                min={50}
                max={98}
                value={payout}
                onChange={(e) => setPayout(e.target.value)}
                className="mt-1 w-full bg-[#0B101A] border border-[#232F46] rounded-md px-2.5 py-2 text-xs font-mono text-slate-100"
              />
            </label>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            {formError ? (
              <span className="text-xs text-rose-400">{formError}</span>
            ) : (
              <span className="text-[11px] text-slate-500">
                Saved permanently to custom_assets.json and immediately available in Live Chart, AI Analysis, and Performance.
              </span>
            )}
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 text-xs font-semibold rounded-md bg-orange-500 hover:bg-orange-400 text-slate-950 transition-colors cursor-pointer disabled:opacity-50"
            >
              {saving ? 'Adding...' : 'Save Asset'}
            </button>
          </div>
        </form>
      )}

      {/* Category Tabs + Bulk Analysis & Notification Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {CATEGORIES.map((cat) => {
            return (
              <button
                key={cat}
                type="button"
                onClick={() => setCategory(cat)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors whitespace-nowrap cursor-pointer ${
                  category === cat
                    ? 'bg-orange-500/12 border-orange-500/35 text-orange-300'
                    : 'bg-[#101725] border-[#1C2639] text-slate-400 hover:text-slate-200'
                }`}
              >
                {cat}
              </button>
            );
          })}
        </div>



      </div>

      <div className="flex flex-wrap gap-2">
        <button type="button"
          disabled={!trackedAssets || savingAsset !== null || !filtered.some((asset) => !trackedAssets.includes(asset.symbol.toUpperCase()))}
          onClick={() => void updateTracking(filtered.map((asset) => asset.symbol), true)}
          className="rounded-md border border-[#25324B] px-3 py-1.5 text-xs text-slate-200 hover:bg-[#1E2A40] disabled:opacity-50">
          Enable visible assets
        </button>
        <a href="/SignalDesk_Chrome_Market_Bridge.zip" download
          className="inline-flex items-center gap-1.5 rounded-md border border-[#25324B] px-3 py-1.5 text-xs text-slate-200 hover:bg-[#1E2A40]">
          <Download className="size-3.5" aria-hidden="true" />
          Download Chrome extension
        </a>
        <button type="button" aria-pressed={favoritesOnly} onClick={() => setFavoritesOnly(!favoritesOnly)}
          className="rounded-md border border-[#25324B] px-3 py-1.5 text-xs text-slate-200 aria-pressed:border-amber-400/50">
          Favorites only
        </button>
        <button type="button"
          disabled={!trackedAssets || savingAsset !== null || !filtered.some((asset) => trackedAssets.includes(asset.symbol.toUpperCase()))}
          onClick={() => void updateTracking(filtered.map((asset) => asset.symbol), false)}
          className="rounded-md border border-[#25324B] px-3 py-1.5 text-xs text-slate-200 hover:bg-[#1E2A40] disabled:opacity-50">
          Disable visible assets
        </button>
        {savingAsset === 'bulk' && <span role="status" className="self-center text-xs text-slate-400">Saving…</span>}
      </div>
      {trackingError && <p role="alert" className="text-xs text-rose-300">{trackingError}</p>}
      {/* Clean Market Data Table */}
      <div className="border border-[#1A2334] rounded-lg bg-[#0F1523] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-[#1A2334] bg-[#121928] text-[11px] font-medium text-slate-400">
                <th className="py-3 px-4">Asset</th>
                <th className="py-3 px-4 text-right">Price</th>
                <th className="py-3 px-4 text-right whitespace-nowrap" title="Change from the nearest hourly close at or before 24 hours ago">24h change</th>
                {(['payout_1m', 'payout_5m'] as const).map((key) => (
                  <th key={key} className="py-3 px-4 text-right whitespace-nowrap" aria-sort={sortPayout === key ? 'descending' : 'none'}>
                    <button type="button" onClick={() => setSortPayout(sortPayout === key ? null : key)} className="hover:text-white">
                      {key === 'payout_1m' ? 'Payout 1+ min' : 'Payout 5+ min'}{sortPayout === key ? ' ↓' : ''}
                    </button>
                  </th>
                ))}
                <th className="py-3 px-4">Feed</th>
                <th className="py-3 px-4">Stubay Ai</th>
                <th className="py-3 px-4 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#172033] text-xs">
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-slate-400">
                    {marketRows.length === 0
                      ? 'No market data yet. Refresh to load the latest prices.'
                      : 'No pairs match your current filter.'}
                  </td>
                </tr>
              ) : (
                filtered.map((pair) => {
                  const isTracked = trackedAssets?.includes(pair.symbol.toUpperCase()) ?? false;
                  const isSelected = pair.symbol === selectedSymbol;
                  return (
                    <tr
                      key={pair.symbol}
                      className={`transition-colors ${
                        isSelected
                          ? 'bg-orange-500/5'
                          : 'hover:bg-[#141D2F]'
                      }`}
                    >
                      <td className="py-3 px-4">
                        <div className="font-medium text-slate-100 flex items-center gap-2">
                          <button type="button" aria-pressed={favorites.includes(pair.symbol)} aria-label={`Favorite ${pair.name}`}
                            onClick={() => setFavorites((previous) => previous.includes(pair.symbol)
                              ? previous.filter((symbol) => symbol !== pair.symbol) : [...previous, pair.symbol])}
                            className="shrink-0 rounded p-1 text-slate-400 hover:text-amber-300 aria-pressed:text-amber-300">
                            <Star className={`size-4 ${favorites.includes(pair.symbol) ? 'fill-current' : ''}`} aria-hidden="true" />
                          </button>
                          <span className="whitespace-nowrap">
                            <span aria-hidden="true" className="mr-1.5">{currencyFlags(pair.symbol)}</span>
                            {pair.name}
                          </span>
                        </div>
                        <div className="text-[11px] text-slate-500">
                          {pair.symbol}
                        </div>
                      </td>

                      <td className="py-3 px-4 text-right font-mono tabular-nums text-slate-200">
                        {pair.price > 0 ? pair.price.toFixed(pair.decimals) : '—'}
                      </td>

                      <td className={`py-3 px-4 text-right tabular-nums ${pair.change_24h == null ? 'text-slate-500' : pair.change_24h < 0 ? 'text-rose-400' : pair.change_24h > 0 ? 'text-emerald-400' : 'text-slate-300'}`}>
                        {pair.change_24h == null ? '—' : `${pair.change_24h > 0 ? '+' : ''}${pair.change_24h.toFixed(2)}%`}
                      </td>
                      <td className="py-3 px-4 text-right tabular-nums text-orange-300">{pair.payout_1m == null ? '—' : `${pair.payout_1m}%`}</td>
                      <td className="py-3 px-4 text-right tabular-nums text-orange-300">{pair.payout_5m == null ? '—' : `${pair.payout_5m}%`}</td>

                      <td className="py-3 px-4 text-slate-400">
                        {pair.feed_source === 'Browser bridge' ? 'Browser bridge' : pair.live_required ? 'Login required' : pair.is_live ? 'Live' : pair.price > 0 ? 'Last quote' : 'Awaiting quote'}
                      </td>

                      <td className="py-3 px-4">
                        <button
                          type="button"
                          role="switch"
                          aria-label={`Stubay tracking for ${pair.symbol}`}
                          aria-checked={isTracked}
                          disabled={trackedAssets === null || savingAsset !== null}
                          onClick={() => void updateTracking([pair.symbol], !isTracked)}
                          className="inline-flex min-w-20 items-center gap-2 rounded-md px-2 py-1.5 text-xs text-slate-300 focus-visible:outline focus-visible:outline-orange-400 disabled:cursor-wait disabled:opacity-50"
                        >
                          <span aria-hidden="true" className={`relative h-4 w-7 shrink-0 rounded-full ${isTracked ? 'bg-emerald-600' : 'bg-slate-600'}`}>
                            <span className={`absolute left-0.5 top-0.5 size-3 rounded-full bg-white transition-transform ${isTracked ? 'translate-x-3' : ''}`} />
                          </span>
                          {savingAsset === pair.symbol ? 'Saving' : isTracked ? 'On' : 'Off'}
                        </button>
                      </td>

                      <td className="py-3 px-4 text-right">
                        <div className="inline-flex items-center gap-1.5">
                          {pair.custom && (
                            <button
                              type="button"
                              onClick={() => void onRemoveCustomAsset(pair.symbol)}
                              title={`Remove custom asset ${pair.symbol}`}
                              aria-label={`Remove ${pair.symbol}`}
                              className="p-1.5 rounded-md border border-[#24324B] bg-[#161F31] text-slate-400 hover:text-rose-300 hover:border-rose-500/40 transition-colors cursor-pointer"
                            >
                              <Trash2 className="w-3 h-3" />
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => onOpenChart(pair.symbol)}
                            className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md bg-[#161F31] border border-[#24324B] text-slate-200 hover:bg-[#1E2A40] transition-colors whitespace-nowrap cursor-pointer"
                          >
                            <span>Chart</span>
                            <ArrowUpRight className="w-3 h-3" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
