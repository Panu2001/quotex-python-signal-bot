"""
Live Market Data Feed for SignalDesk (100% Quotex Live — ZERO Simulated or Third-Party Fallback Candles).

Data Source Policy:
1. Quotex Live (`pyquotex` persistent WebSocket session) — Streams exact live Quotex
   candlesticks for ALL OTC & Live assets whenever the user is logged into Quotex.
2. When logged out of Quotex — Returns an explicit `live_required: True` empty state.
   Never generates synthetic candlesticks and never falls back to Yahoo/Binance feeds,
   so Markets, Strategy, AI Analysis, Signals, Paper Trades, Performance, and Logs
   evaluate strictly real Quotex trends and accuracy.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import quotex_client as qc

DEFAULT_ASSETS: Dict[str, Dict[str, Any]] = {
    # --- Live Forex (8) — Real live market candles via Quotex Live or Yahoo Finance ---
    "EURUSD": {"name": "EUR/USD", "category": "Live Forex", "payout": 86, "base": 1.0950, "vol": 0.00028, "decimals": 5, "source": "yahoo", "yahoo": "EURUSD=X"},
    "GBPUSD": {"name": "GBP/USD", "category": "Live Forex", "payout": 85, "base": 1.2670, "vol": 0.00034, "decimals": 5, "source": "yahoo", "yahoo": "GBPUSD=X"},
    "USDJPY": {"name": "USD/JPY", "category": "Live Forex", "payout": 86, "base": 159.50, "vol": 0.042, "decimals": 3, "source": "yahoo", "yahoo": "USDJPY=X"},
    "AUDUSD": {"name": "AUD/USD", "category": "Live Forex", "payout": 84, "base": 0.6530, "vol": 0.00022, "decimals": 5, "source": "yahoo", "yahoo": "AUDUSD=X"},
    "USDCAD": {"name": "USD/CAD", "category": "Live Forex", "payout": 84, "base": 1.3935, "vol": 0.00031, "decimals": 5, "source": "yahoo", "yahoo": "USDCAD=X"},
    "EURGBP": {"name": "EUR/GBP", "category": "Live Forex", "payout": 84, "base": 0.8552, "vol": 0.00020, "decimals": 5, "source": "yahoo", "yahoo": "EURGBP=X"},
    "EURJPY": {"name": "EUR/JPY", "category": "Live Forex", "payout": 85, "base": 167.22, "vol": 0.048, "decimals": 3, "source": "yahoo", "yahoo": "EURJPY=X"},
    "GBPJPY": {"name": "GBP/JPY", "category": "Live Forex", "payout": 85, "base": 195.52, "vol": 0.058, "decimals": 3, "source": "yahoo", "yahoo": "GBPJPY=X"},

    # --- Crypto Live via Quotex Live / Binance Spot / Yahoo Crypto (12) ---
    "BTCUSDT": {"name": "BTC/USD", "category": "Crypto Live", "payout": 85, "base": 63000.0, "vol": 85.0, "decimals": 2, "source": "binance", "yahoo": "BTC-USD"},
    "ETHUSDT": {"name": "ETH/USD", "category": "Crypto Live", "payout": 84, "base": 3120.0, "vol": 4.2, "decimals": 2, "source": "binance", "yahoo": "ETH-USD"},
    "SOLUSDT": {"name": "SOL/USD", "category": "Crypto Live", "payout": 82, "base": 218.50, "vol": 0.55, "decimals": 2, "source": "binance", "yahoo": "SOL-USD"},
    "XRPUSDT": {"name": "XRP/USD", "category": "Crypto Live", "payout": 81, "base": 1.1240, "vol": 0.0045, "decimals": 4, "source": "binance", "yahoo": "XRP-USD"},
    "BNBUSDT": {"name": "BNB/USD", "category": "Crypto Live", "payout": 82, "base": 628.40, "vol": 1.25, "decimals": 2, "source": "binance", "yahoo": "BNB-USD"},
    "DOGEUSDT": {"name": "DOGE/USD", "category": "Crypto Live", "payout": 80, "base": 0.3785, "vol": 0.0022, "decimals": 4, "source": "binance", "yahoo": "DOGE-USD"},
    "ADAUSDT": {"name": "ADA/USD", "category": "Crypto Live", "payout": 80, "base": 0.7420, "vol": 0.0038, "decimals": 4, "source": "binance", "yahoo": "ADA-USD"},
    "LTCUSDT": {"name": "LTC/USD", "category": "Crypto Live", "payout": 81, "base": 89.40, "vol": 0.28, "decimals": 2, "source": "binance", "yahoo": "LTC-USD"},
    "AVAXUSDT": {"name": "AVAX/USD", "category": "Crypto Live", "payout": 80, "base": 35.80, "vol": 0.16, "decimals": 2, "source": "binance", "yahoo": "AVAX-USD"},
    "DOTUSDT": {"name": "DOT/USD", "category": "Crypto Live", "payout": 80, "base": 6.240, "vol": 0.026, "decimals": 3, "source": "binance", "yahoo": "DOT-USD"},
    "LINKUSDT": {"name": "LINK/USD", "category": "Crypto Live", "payout": 81, "base": 14.850, "vol": 0.065, "decimals": 3, "source": "binance", "yahoo": "LINK-USD"},
    "TONUSDT": {"name": "TON/USD", "category": "Crypto Live", "payout": 80, "base": 5.420, "vol": 0.025, "decimals": 3, "source": "binance", "yahoo": "TON11419-USD"},

    # --- OTC Forex (24) — Requires live Quotex (pyquotex) session ---
    "EURUSD-OTC": {"name": "EUR/USD (OTC)", "category": "OTC Forex", "payout": 89, "base": 1.0950, "vol": 0.00032, "decimals": 5, "source": "otc"},
    "GBPUSD-OTC": {"name": "GBP/USD (OTC)", "category": "OTC Forex", "payout": 87, "base": 1.2675, "vol": 0.00038, "decimals": 5, "source": "otc"},
    "USDJPY-OTC": {"name": "USD/JPY (OTC)", "category": "OTC Forex", "payout": 88, "base": 159.50, "vol": 0.045, "decimals": 3, "source": "otc"},
    "AUDCAD-OTC": {"name": "AUD/CAD (OTC)", "category": "OTC Forex", "payout": 85, "base": 0.9085, "vol": 0.00030, "decimals": 5, "source": "otc"},
    "EURJPY-OTC": {"name": "EUR/JPY (OTC)", "category": "OTC Forex", "payout": 86, "base": 167.28, "vol": 0.052, "decimals": 3, "source": "otc"},
    "USDCHF-OTC": {"name": "USD/CHF (OTC)", "category": "OTC Forex", "payout": 86, "base": 0.8845, "vol": 0.00028, "decimals": 5, "source": "otc"},
    "USDCAD-OTC": {"name": "USD/CAD (OTC)", "category": "OTC Forex", "payout": 86, "base": 1.3940, "vol": 0.00034, "decimals": 5, "source": "otc"},
    "AUDUSD-OTC": {"name": "AUD/USD (OTC)", "category": "OTC Forex", "payout": 87, "base": 0.6532, "vol": 0.00025, "decimals": 5, "source": "otc"},
    "NZDUSD-OTC": {"name": "NZD/USD (OTC)", "category": "OTC Forex", "payout": 84, "base": 0.5924, "vol": 0.00024, "decimals": 5, "source": "otc"},
    "EURGBP-OTC": {"name": "EUR/GBP (OTC)", "category": "OTC Forex", "payout": 86, "base": 0.8554, "vol": 0.00022, "decimals": 5, "source": "otc"},
    "GBPJPY-OTC": {"name": "GBP/JPY (OTC)", "category": "OTC Forex", "payout": 87, "base": 195.60, "vol": 0.065, "decimals": 3, "source": "otc"},
    "AUDJPY-OTC": {"name": "AUD/JPY (OTC)", "category": "OTC Forex", "payout": 85, "base": 100.82, "vol": 0.038, "decimals": 3, "source": "otc"},
    "CADJPY-OTC": {"name": "CAD/JPY (OTC)", "category": "OTC Forex", "payout": 84, "base": 110.65, "vol": 0.040, "decimals": 3, "source": "otc"},
    "CHFJPY-OTC": {"name": "CHF/JPY (OTC)", "category": "OTC Forex", "payout": 84, "base": 174.45, "vol": 0.056, "decimals": 3, "source": "otc"},
    "EURAUD-OTC": {"name": "EUR/AUD (OTC)", "category": "OTC Forex", "payout": 85, "base": 1.6598, "vol": 0.00046, "decimals": 5, "source": "otc"},
    "GBPAUD-OTC": {"name": "GBP/AUD (OTC)", "category": "OTC Forex", "payout": 85, "base": 1.9405, "vol": 0.00052, "decimals": 5, "source": "otc"},
    "EURCAD-OTC": {"name": "EUR/CAD (OTC)", "category": "OTC Forex", "payout": 85, "base": 1.5112, "vol": 0.00042, "decimals": 5, "source": "otc"},
    "USDBRL-OTC": {"name": "USD/BRL (OTC)", "category": "OTC Forex", "payout": 90, "base": 5.7850, "vol": 0.0028, "decimals": 4, "source": "otc"},
    "USDINR-OTC": {"name": "USD/INR (OTC)", "category": "OTC Forex", "payout": 89, "base": 84.420, "vol": 0.018, "decimals": 3, "source": "otc"},
    "USDMXN-OTC": {"name": "USD/MXN (OTC)", "category": "OTC Forex", "payout": 88, "base": 20.185, "vol": 0.0095, "decimals": 4, "source": "otc"},
    "USDZAR-OTC": {"name": "USD/ZAR (OTC)", "category": "OTC Forex", "payout": 87, "base": 18.120, "vol": 0.0085, "decimals": 4, "source": "otc"},
    "USDPKR-OTC": {"name": "USD/PKR (OTC)", "category": "OTC Forex", "payout": 88, "base": 278.40, "vol": 0.075, "decimals": 2, "source": "otc"},
    "USDBDT-OTC": {"name": "USD/BDT (OTC)", "category": "OTC Forex", "payout": 88, "base": 119.50, "vol": 0.035, "decimals": 2, "source": "otc"},
    "USDTRY-OTC": {"name": "USD/TRY (OTC)", "category": "OTC Forex", "payout": 86, "base": 34.480, "vol": 0.014, "decimals": 4, "source": "otc"},

    # --- Commodities (4) ---
    "XAUUSD-OTC": {"name": "Gold XAU/USD (OTC)", "category": "Commodities", "payout": 86, "base": 2642.50, "vol": 1.45, "decimals": 2, "source": "otc"},
    "XAGUSD-OTC": {"name": "Silver XAG/USD (OTC)", "category": "Commodities", "payout": 84, "base": 30.850, "vol": 0.048, "decimals": 3, "source": "otc"},
    "UKBRENT-OTC": {"name": "UK Brent Oil (OTC)", "category": "Commodities", "payout": 83, "base": 72.80, "vol": 0.14, "decimals": 2, "source": "otc"},
    "USCRUDE-OTC": {"name": "US Crude Oil (OTC)", "category": "Commodities", "payout": 83, "base": 69.10, "vol": 0.14, "decimals": 2, "source": "otc"},

    # --- Indices & Stocks (8) ---
    "US100-OTC": {"name": "NASDAQ 100 (OTC)", "category": "Indices & Stocks", "payout": 85, "base": 20890.0, "vol": 14.5, "decimals": 1, "source": "otc"},
    "US30-OTC": {"name": "Dow Jones 30 (OTC)", "category": "Indices & Stocks", "payout": 85, "base": 43520.0, "vol": 22.0, "decimals": 1, "source": "otc"},
    "SP500-OTC": {"name": "S&P 500 (OTC)", "category": "Indices & Stocks", "payout": 85, "base": 5920.0, "vol": 3.8, "decimals": 1, "source": "otc"},
    "AAPL-OTC": {"name": "Apple Inc. (OTC)", "category": "Indices & Stocks", "payout": 84, "base": 228.40, "vol": 0.35, "decimals": 2, "source": "otc"},
    "TSLA-OTC": {"name": "Tesla Inc. (OTC)", "category": "Indices & Stocks", "payout": 84, "base": 338.20, "vol": 0.95, "decimals": 2, "source": "otc"},
    "MSFT-OTC": {"name": "Microsoft (OTC)", "category": "Indices & Stocks", "payout": 84, "base": 418.60, "vol": 0.52, "decimals": 2, "source": "otc"},
    "INTC-OTC": {"name": "Intel Corp. (OTC)", "category": "Indices & Stocks", "payout": 83, "base": 24.45, "vol": 0.08, "decimals": 2, "source": "otc"},
    "BA-OTC": {"name": "Boeing Co. (OTC)", "category": "Indices & Stocks", "payout": 83, "base": 146.80, "vol": 0.32, "decimals": 2, "source": "otc"},
}

TF_SECONDS = {
    "5s": 5,
    "10s": 10,
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# timeframe -> (binance_interval, resample_seconds)
BINANCE_TF_PARAMS: Dict[str, Tuple[str, int]] = {
    "5s": ("1s", 5),
    "10s": ("1s", 10),
    "15s": ("1s", 15),
    "30s": ("1s", 30),
    "1m": ("1m", 60),
    "2m": ("1m", 120),
    "3m": ("3m", 180),
    "5m": ("5m", 300),
    "10m": ("5m", 600),
    "15m": ("15m", 900),
    "30m": ("30m", 1800),
    "1h": ("1h", 3600),
    "4h": ("4h", 14400),
    "1d": ("1d", 86400),
}

YAHOO_TF_PARAMS: Dict[str, Tuple[str, str, int]] = {
    # timeframe -> (yahoo_interval, yahoo_range, resample_seconds)
    "1m": ("1m", "1d", 60),
    "2m": ("2m", "5d", 120),
    "3m": ("1m", "2d", 180),
    "5m": ("5m", "5d", 300),
    "10m": ("5m", "5d", 600),
    "15m": ("15m", "5d", 900),
    "30m": ("30m", "1mo", 1800),
    "1h": ("60m", "1mo", 3600),
    "4h": ("60m", "3mo", 14400),
    "1d": ("1d", "1y", 86400),
}

# Priority Quotex OTC pairs refreshed automatically during background scans when logged in
PRIORITY_QUOTEX_SCAN_SET = {
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDJPY-OTC",
    "AUDCAD-OTC",
    "EURJPY-OTC",
    "XAUUSD-OTC",
}


def _custom_assets_path() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"]) / "SignalDesk"
        base.mkdir(parents=True, exist_ok=True)
        return base / "custom_assets.json"
    return Path(__file__).resolve().parent / "custom_assets.json"


def load_custom_assets() -> Dict[str, Dict[str, Any]]:
    path = _custom_assets_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {}


def save_custom_assets(custom: Dict[str, Dict[str, Any]]) -> None:
    path = _custom_assets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(custom, indent=2), encoding="utf-8")


def _open_quotex_assets(discovered: dict) -> dict:
    assets = {}
    for qx_name, info in discovered.items():
        if not info.get("open", True):
            continue
        clean = qx_name.strip()
        if not clean:
            continue
        if clean.lower().endswith("_otc"):
            sym = f"{clean[:-4].upper()}-OTC"
            cat = "OTC Forex" if len(clean[:-4]) == 6 else "Indices & Stocks"
            disp = (
                f"{clean[:3].upper()}/{clean[3:6].upper()} (OTC)"
                if len(clean[:-4]) == 6
                else f"{clean[:-4]} (OTC)"
            )
        else:
            sym = clean.upper()
            cat = "Live Forex" if len(sym) == 6 and sym.isalpha() else "Indices & Stocks"
            disp = f"{sym[:3]}/{sym[3:]}" if len(sym) == 6 and sym.isalpha() else sym

        assets[sym] = {
            "name": disp, "category": cat, "payout": int(info.get("payout", 85)),
            "base": 1.0, "vol": 0.001, "decimals": 3 if "JPY" in sym else 5,
            "source": "otc" if sym.endswith("-OTC") else "quotex", "qx_name": clean,
            "payout_1m": info.get("payout_1m"), "payout_5m": info.get("payout_5m"),
        }
    return assets


def get_all_assets() -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in DEFAULT_ASSETS.items()}
    for sym, meta in load_custom_assets().items():
        if isinstance(meta, dict):
            entry = dict(meta)
            entry["custom"] = True
            merged[sym] = entry

    if qc.is_pyquotex_connected():
        for symbol, meta in _open_quotex_assets(qc.cached_live_quotex_assets()).items():
            current = merged.setdefault(symbol, meta)
            current["payout_1m"] = meta["payout_1m"]
            current["payout_5m"] = meta["payout_5m"]

    # Overlay live payouts from active pyquotex session when available
    if qc.is_pyquotex_connected():
        for sym, meta in merged.items():
            live_payout = qc.get_live_quotex_payout(sym)
            if live_payout is not None:
                meta["payout"] = live_payout

    return merged


# Backwards-compatible alias kept synced with defaults
ASSETS: Dict[str, Dict[str, Any]] = DEFAULT_ASSETS


def add_custom_asset(
    symbol: str,
    name: str,
    category: str = "OTC Forex",
    payout: int = 85,
    base: float = 1.0,
    decimals: int = 5,
    source: str = "otc",
    yahoo_ticker: str = "",
) -> Dict[str, Any]:
    clean_sym = symbol.strip().upper().replace(" ", "")
    if not clean_sym or len(clean_sym) > 24:
        raise ValueError("Provide a valid asset symbol (e.g. USDNGN-OTC or PEPEUSDT).")
    clean_name = name.strip() or clean_sym
    clean_source = source if source in ("quotex", "otc", "yahoo", "binance") else "otc"
    clean_base = max(0.00001, float(base))
    clean_decimals = max(0, min(6, int(decimals)))
    clean_payout = max(50, min(98, int(payout)))
    vol = max(clean_base * 0.00045, 10 ** (-max(clean_decimals, 2)))

    custom = load_custom_assets()
    entry: Dict[str, Any] = {
        "name": clean_name,
        "category": category.strip() or "OTC Forex",
        "payout": clean_payout,
        "base": clean_base,
        "vol": round(vol, 6),
        "decimals": clean_decimals,
        "source": clean_source,
        "custom": True,
    }
    if yahoo_ticker.strip():
        entry["yahoo"] = yahoo_ticker.strip()

    custom[clean_sym] = entry
    save_custom_assets(custom)
    return {"ok": True, "symbol": clean_sym, "assets": get_all_assets()}


def remove_custom_asset(symbol: str) -> Dict[str, Any]:
    clean_sym = symbol.strip().upper()
    custom = load_custom_assets()
    removed = custom.pop(clean_sym, None) is not None
    if removed:
        save_custom_assets(custom)
    return {"ok": removed, "symbol": clean_sym, "assets": get_all_assets()}


def sync_assets_from_quotex() -> Dict[str, Any]:
    """Pull live open Quotex assets and payouts from the active pyquotex session."""
    if not qc.is_pyquotex_connected():
        return {
            "ok": False,
            "added": 0,
            "discovered": 0,
            "error": "Log in to Quotex first before syncing live Quotex assets.",
            "assets": get_all_assets(),
        }

    discovered = qc.sync_live_quotex_assets()
    custom = load_custom_assets()
    added = 0
    updated = 0

    for sym, meta in _open_quotex_assets(discovered).items():
        if sym in custom:
            if custom[sym].get("qx_name") != meta["qx_name"] or custom[sym].get("payout") != meta["payout"]:
                custom[sym].update(qx_name=meta["qx_name"], payout=meta["payout"])
                updated += 1
            continue
        if sym not in DEFAULT_ASSETS:
            custom[sym] = meta
            added += 1

    if added > 0 or updated > 0:
        save_custom_assets(custom)

    return {
        "ok": True,
        "discovered": len(discovered),
        "added": added,
        "assets": get_all_assets(),
    }


# ---------------------------------------------------------------------------
# High-Speed HTTP/1.1 Keep-Alive Pool + Unified SWR Cache (Zero External Deps)
# ---------------------------------------------------------------------------
import gzip
import http.client
import queue
import ssl

_SSL_CONTEXT = ssl.create_default_context()


class _HostKeepAlivePool:
    """Thread-safe stdlib HTTPS keep-alive connection pool per host."""

    def __init__(self, max_per_host: int = 16) -> None:
        self._lock = threading.Lock()
        self._pools: Dict[str, "queue.LifoQueue[http.client.HTTPSConnection]"] = {}
        self._max_per_host = max_per_host

    def _get_pool(self, host: str) -> "queue.LifoQueue[http.client.HTTPSConnection]":
        with self._lock:
            q = self._pools.get(host)
            if q is None:
                q = queue.LifoQueue(maxsize=self._max_per_host)
                self._pools[host] = q
            return q

    def get_json(
        self,
        host: str,
        path: str,
        headers: Dict[str, str],
        timeout: float = 3.0,
    ) -> Any:
        q = self._get_pool(host)
        req_headers = {
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip",
            **headers,
        }
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            conn: Optional[http.client.HTTPSConnection] = None
            reused = False
            if attempt == 0:
                try:
                    conn = q.get_nowait()
                    reused = True
                except queue.Empty:
                    conn = None
            if conn is None:
                conn = http.client.HTTPSConnection(host, timeout=timeout, context=_SSL_CONTEXT)
            else:
                conn.timeout = timeout
                if conn.sock is not None:
                    try:
                        conn.sock.settimeout(timeout)
                    except Exception:
                        pass
            try:
                conn.request("GET", path, headers=req_headers)
                resp = conn.getresponse()
                body = resp.read()
                status = resp.status
                encoding = (resp.getheader("Content-Encoding") or "").lower()
                conn_hdr = (resp.getheader("Connection") or "").lower()
                if conn_hdr == "close":
                    try:
                        conn.close()
                    except Exception:
                        pass
                else:
                    try:
                        q.put_nowait(conn)
                    except queue.Full:
                        try:
                            conn.close()
                        except Exception:
                            pass
                if status >= 400:
                    raise ConnectionError(f"HTTP {status} from {host}")
                if encoding == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body.decode("utf-8"))
            except Exception as exc:
                last_exc = exc
                try:
                    conn.close()
                except Exception:
                    pass
                # Retry once immediately if a reused keep-alive socket was closed by peer
                if reused and attempt == 0:
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise ConnectionError(f"Request failed for {host}{path}")


_HTTP_POOL = _HostKeepAlivePool(max_per_host=20)

_CACHE_LOCK = threading.RLock()
# Unified cache keyed by (symbol_or_ticker, timeframe) WITHOUT `limit` so
# /api/analyze (160), /api/scan (120), and /api/telegram/signal ALWAYS share cache!
_BINANCE_CACHE: Dict[Tuple[str, str], Tuple[float, List[Dict[str, Any]]]] = {}
_BINANCE_OFFLINE_UNTIL: float = 0.0
_YAHOO_CACHE: Dict[Tuple[str, str], Tuple[float, List[Dict[str, Any]]]] = {}
_YAHOO_OFFLINE_UNTIL: float = 0.0
_INFLIGHT_EVENTS: Dict[Tuple[str, str, str], threading.Event] = {}
_BG_REFRESHING: set[Tuple[str, str, str]] = set()


def _resample_candles_sec(candles: List[Dict[str, Any]], step_sec: int) -> List[Dict[str, Any]]:
    if step_sec <= 1 or not candles:
        return candles
    buckets: Dict[int, Dict[str, Any]] = {}
    for c in candles:
        b = (int(c["time"]) // step_sec) * step_sec
        if b not in buckets:
            buckets[b] = {
                "time": b,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
        else:
            cur = buckets[b]
            cur["high"] = max(cur["high"], c["high"])
            cur["low"] = min(cur["low"], c["low"])
            cur["close"] = c["close"]
            cur["volume"] = cur["volume"] + c["volume"]
    return [buckets[k] for k in sorted(buckets.keys())]


def _slice_limit(candles: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if limit > 0 and len(candles) > limit:
        return candles[-limit:]
    return list(candles)


def _fetch_binance_network(symbol: str, timeframe: str, store_limit: int = 200) -> List[Dict[str, Any]]:
    global _BINANCE_OFFLINE_UNTIL
    interval, target_sec = BINANCE_TF_PARAMS.get(timeframe, ("1m", 60))
    base_sec = 1 if interval == "1s" else (60 if interval == "1m" else (300 if interval == "5m" else target_sec))
    mult = max(1, target_sec // max(base_sec, 1))
    req_limit = min(1000, store_limit * mult + mult)

    path = f"/api/v3/klines?symbol={urllib.parse.quote(symbol)}&interval={interval}&limit={req_limit}"
    try:
        raw = _HTTP_POOL.get_json(
            "api.binance.com",
            path,
            headers={"User-Agent": "SignalDesk/1.0", "Accept": "application/json"},
            timeout=2.5,
        )
    except Exception:
        with _CACHE_LOCK:
            _BINANCE_OFFLINE_UNTIL = time.monotonic() + 20.0
        raise

    candles: List[Dict[str, Any]] = []
    for k in raw:
        candles.append({
            "time": int(k[0] // 1000),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })

    if mult > 1:
        candles = _resample_candles_sec(candles, target_sec)
    if len(candles) > store_limit:
        candles = candles[-store_limit:]

    with _CACHE_LOCK:
        _BINANCE_CACHE[(symbol, timeframe)] = (time.monotonic(), candles)
    return candles


def _schedule_bg_refresh(provider: str, key: str, timeframe: str, fn: Any) -> None:
    bg_key = (provider, key, timeframe)
    with _CACHE_LOCK:
        if bg_key in _BG_REFRESHING:
            return
        _BG_REFRESHING.add(bg_key)

    def _worker() -> None:
        try:
            fn()
        except Exception:
            pass
        finally:
            with _CACHE_LOCK:
                _BG_REFRESHING.discard(bg_key)

    threading.Thread(target=_worker, daemon=True, name=f"Refresh-{provider}-{key}").start()


def fetch_binance_klines(
    symbol: str,
    timeframe: str = "1m",
    limit: int = 160,
    priority: bool = True,
    force_refresh: bool = False,
    cache_only: bool = False,
) -> List[Dict[str, Any]]:
    now = time.monotonic()
    cache_key = (symbol, timeframe)
    is_sub = TF_SECONDS.get(timeframe, 60) < 60
    fresh_ttl = (1.8 if priority else 4.0) if is_sub else (4.0 if priority else 12.0)
    stale_ttl = 6.0 if is_sub else 22.0
    store_limit = max(limit, 180)
    inflight_key = ("binance", symbol, timeframe)

    wait_event: Optional[threading.Event] = None
    own_event: Optional[threading.Event] = None
    swr_result: Optional[List[Dict[str, Any]]] = None

    with _CACHE_LOCK:
        cached = _BINANCE_CACHE.get(cache_key)
        if cached and len(cached[1]) >= min(limit, 20):
            age = now - cached[0]
            if cache_only or (not force_refresh and age < fresh_ttl):
                return _slice_limit(cached[1], limit)
            if not force_refresh and age < stale_ttl and now >= _BINANCE_OFFLINE_UNTIL:
                swr_result = _slice_limit(cached[1], limit)
        if swr_result is None:
            if cache_only:
                return _slice_limit(cached[1], limit) if cached else []
            if now < _BINANCE_OFFLINE_UNTIL:
                if cached:
                    return _slice_limit(cached[1], limit)
                raise ConnectionError("Binance API cooldown active")

            existing_evt = _INFLIGHT_EVENTS.get(inflight_key)
            if existing_evt is not None:
                wait_event = existing_evt
            else:
                own_event = threading.Event()
                _INFLIGHT_EVENTS[inflight_key] = own_event

    if swr_result is not None:
        _schedule_bg_refresh(
            "binance",
            symbol,
            timeframe,
            lambda: _fetch_binance_network(symbol, timeframe, store_limit),
        )
        return swr_result

    if wait_event is not None:
        wait_event.wait(timeout=3.0)
        with _CACHE_LOCK:
            cached_after = _BINANCE_CACHE.get(cache_key)
            if cached_after:
                return _slice_limit(cached_after[1], limit)
        return []

    try:
        candles = _fetch_binance_network(symbol, timeframe, store_limit)
        return _slice_limit(candles, limit)
    finally:
        if own_event is not None:
            with _CACHE_LOCK:
                _INFLIGHT_EVENTS.pop(inflight_key, None)
            own_event.set()


def _fetch_yahoo_network(
    yahoo_ticker: str, timeframe: str, decimals: int, store_limit: int = 200
) -> List[Dict[str, Any]]:
    global _YAHOO_OFFLINE_UNTIL
    yf_interval, yf_range, target_sec = YAHOO_TF_PARAMS[timeframe]
    if store_limit > 260 and yf_range == "1d":
        yf_range = "5d"

    encoded = urllib.parse.quote(yahoo_ticker)
    path = (
        f"/v8/finance/chart/{encoded}"
        f"?interval={yf_interval}&range={yf_range}&includePrePost=true"
    )
    try:
        payload = _HTTP_POOL.get_json(
            "query1.finance.yahoo.com",
            path,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
            timeout=3.2,
        )
    except Exception:
        with _CACHE_LOCK:
            _YAHOO_OFFLINE_UNTIL = time.monotonic() + 15.0
        raise

    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        return []
    res = results[0]
    timestamps = res.get("timestamp") or []
    quotes = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    volumes = quotes.get("volume") or []

    candles: List[Dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        if idx >= len(closes):
            break
        o = opens[idx] if idx < len(opens) else None
        h = highs[idx] if idx < len(highs) else None
        l = lows[idx] if idx < len(lows) else None
        c = closes[idx]
        if o is None or h is None or l is None or c is None:
            continue
        v = volumes[idx] if idx < len(volumes) and volumes[idx] is not None else 1.0
        candles.append({
            "time": int(ts),
            "open": round(float(o), decimals),
            "high": round(float(h), decimals),
            "low": round(float(l), decimals),
            "close": round(float(c), decimals),
            "volume": float(v) if float(v) > 0 else 1.0,
        })

    # Always align Yahoo candle timestamps to exact timeframe bucket boundaries
    # (Yahoo's latest live bar often carries an unaligned mid-minute second like :27/:43,
    # which caused signal barKey duplicates and chart/signal time mismatches!)
    candles = _resample_candles_sec(candles, target_sec)

    if len(candles) > store_limit:
        candles = candles[-store_limit:]

    with _CACHE_LOCK:
        _YAHOO_CACHE[(yahoo_ticker, timeframe)] = (time.monotonic(), candles)
    return candles


def fetch_yahoo_klines(
    yahoo_ticker: str,
    timeframe: str = "1m",
    limit: int = 160,
    decimals: int = 5,
    priority: bool = True,
    force_refresh: bool = False,
    cache_only: bool = False,
) -> List[Dict[str, Any]]:
    if timeframe not in YAHOO_TF_PARAMS:
        return []

    now = time.monotonic()
    cache_key = (yahoo_ticker, timeframe)
    fresh_ttl = 4.0 if priority else 12.0
    stale_ttl = 24.0
    store_limit = max(limit, 180)
    inflight_key = ("yahoo", yahoo_ticker, timeframe)

    wait_event: Optional[threading.Event] = None
    own_event: Optional[threading.Event] = None
    swr_result: Optional[List[Dict[str, Any]]] = None

    with _CACHE_LOCK:
        cached = _YAHOO_CACHE.get(cache_key)
        if cached and len(cached[1]) >= min(limit, 20):
            age = now - cached[0]
            if cache_only or (not force_refresh and age < fresh_ttl):
                return _slice_limit(cached[1], limit)
            if not force_refresh and age < stale_ttl and now >= _YAHOO_OFFLINE_UNTIL:
                swr_result = _slice_limit(cached[1], limit)
        if swr_result is None:
            if cache_only:
                return _slice_limit(cached[1], limit) if cached else []
            if now < _YAHOO_OFFLINE_UNTIL:
                if cached:
                    return _slice_limit(cached[1], limit)
                raise ConnectionError("Yahoo Finance API cooldown active")

            existing_evt = _INFLIGHT_EVENTS.get(inflight_key)
            if existing_evt is not None:
                wait_event = existing_evt
            else:
                own_event = threading.Event()
                _INFLIGHT_EVENTS[inflight_key] = own_event

    if swr_result is not None:
        _schedule_bg_refresh(
            "yahoo",
            yahoo_ticker,
            timeframe,
            lambda: _fetch_yahoo_network(yahoo_ticker, timeframe, decimals, store_limit),
        )
        return swr_result

    if wait_event is not None:
        wait_event.wait(timeout=3.5)
        with _CACHE_LOCK:
            cached_after = _YAHOO_CACHE.get(cache_key)
            if cached_after:
                return _slice_limit(cached_after[1], limit)
        return []

    try:
        candles = _fetch_yahoo_network(yahoo_ticker, timeframe, decimals, store_limit)
        return _slice_limit(candles, limit)
    finally:
        if own_event is not None:
            with _CACHE_LOCK:
                _INFLIGHT_EVENTS.pop(inflight_key, None)
            own_event.set()


def prewarm_top_assets(timeframe: str = "1m") -> None:
    """Quietly warm top Quotex assets only when an authenticated Quotex session is active."""
    if not qc.is_pyquotex_connected():
        return

    def _runner() -> None:
        if not qc.is_pyquotex_connected():
            return
        for sym in ("EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "EURUSD", "GBPUSD"):
            try:
                get_candles(sym, timeframe=timeframe, limit=180, priority=False)
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True, name="SignalDeskPrewarm").start()


def get_candles(
    symbol: str,
    timeframe: str = "1m",
    limit: int = 160,
    seed_offset: int = 0,
    priority: bool = True,
    force_refresh: bool = False,
    cache_only: bool = False,
    background: bool = False,
    cache_max_age: float = 30.0,
    asset_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Fetch strictly REAL Quotex live candlesticks — NEVER synthetic/simulated candles
    and NEVER third-party Yahoo/Binance fallbacks.
    """
    _ = seed_offset  # kept for signature compatibility; synthetic seeding removed
    meta = asset_metadata if asset_metadata is not None else get_all_assets().get(symbol)
    if meta is None:
        is_otc = symbol.upper().endswith("-OTC") or symbol.upper().endswith("_OTC")
        meta = {
            "name": symbol,
            "category": "OTC Forex" if is_otc else "Live Forex",
            "payout": 85,
            "base": 1.0,
            "vol": 0.001,
            "decimals": 3 if "JPY" in symbol.upper() else 5,
            "source": "otc" if is_otc else "quotex",
        }

    decimals = int(meta.get("decimals", 5))
    qx_name = str(meta.get("qx_name", "") or "")
    feed_source = "Quotex Login Required"
    candles: List[Dict[str, Any]] = []
    live_required = False
    error_msg: Optional[str] = None

    # STRICT QUOTEX-ONLY LIVE DATA FEED via authenticated pyquotex WebSocket session
    if not qc.is_pyquotex_connected():
        live_required = True
        feed_source = "Quotex Login Required"
        error_msg = (
            "Connect your Quotex account under Quotex Login to stream live Quotex "
            "candlesticks, trends, signals, and paper trade prices."
        )
    else:
        if cache_only:
            cached_qx = qc.get_cached_quotex_candles(
                symbol=symbol, timeframe=timeframe, limit=limit, max_age=cache_max_age
            )
            if cached_qx:
                candles = cached_qx
                feed_source = "Quotex Live (pyquotex)"
            else:
                feed_source = "Quotex Live (Loading)"
        elif priority or background or symbol in PRIORITY_QUOTEX_SCAN_SET:
            qx_candles, qx_err = qc.fetch_live_quotex_candles(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                cache_ttl=1.8 if priority else 10.0,
                timeout=6.0 if priority else 2.5,
                qx_name=qx_name,
                max_pages=4 if priority else 1,
                priority=priority,
                force_refresh=force_refresh,
            )
            if qx_candles:
                candles = qx_candles
                feed_source = "Quotex Live (pyquotex)"
            else:
                feed_source = "Quotex Asset Closed"
                error_msg = (
                    qx_err
                    or f"{meta['name']} is currently closed or unavailable on Quotex ({timeframe})."
                )
        else:
            cached_qx = qc.get_cached_quotex_candles(
                symbol=symbol, timeframe=timeframe, limit=limit, max_age=24.0
            )
            if cached_qx:
                candles = cached_qx
                feed_source = "Quotex Live (pyquotex)"
            else:
                feed_source = "Quotex Live (Open Chart)"

    server_now = int(time.time())
    latest_bar_time = int(candles[-1]["time"]) if candles else 0
    candle_age_sec = max(0, server_now - latest_bar_time) if latest_bar_time > 0 else None

    return {
        "symbol": symbol,
        "name": meta["name"],
        "category": meta["category"],
        "payout": meta["payout"],
        "decimals": decimals,
        "timeframe": timeframe,
        "feed_source": feed_source,
        "is_live": len(candles) > 0 and feed_source == "Quotex Live (pyquotex)",
        "live_required": live_required,
        "error": error_msg,
        "custom": bool(meta.get("custom", False)),
        "server_time": server_now,
        "latest_bar_time": latest_bar_time,
        "candle_age_sec": candle_age_sec,
        "candles": candles,
    }
