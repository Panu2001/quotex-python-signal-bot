"""
Quotex Execution & Live Market Data Bridge
(Persistent Live pyquotex Session + 2-Step Interactive OTP + Browser SSID Client).

Supports:
1. Two-step Email & Password -> Email OTP login via community `pyquotex` / `quotexapi`
   (hooks `builtins.input` safely so `pythonw.exe` on Windows never hits `lost sys.stdin`,
   pausing for the user to enter their Email OTP in the SignalDesk UI).
2. Persistent authenticated `pyquotex` WebSocket session that stays open after login
   to stream REAL live Quotex candlesticks (OTC & Live pairs), real payouts, open
   assets, and optional live order execution.
3. Direct Browser Session (`SSID` + matching `Cookie` + `User-Agent`) via `websocket-client`.
"""

from __future__ import annotations

import asyncio
import builtins
import importlib
import inspect
import json
import os
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    import websocket  # type: ignore
    HAS_WEBSOCKET = True
except ImportError:
    websocket = None
    HAS_WEBSOCKET = False


def _compat_reason_phrase(self: Any) -> str:
    """Fallback `reason_phrase` property for HTTP Response classes lacking httpx's attribute."""
    for attr in ("reason", "status_text", "statusText", "msg"):
        val = getattr(self, attr, None)
        if isinstance(val, bytes):
            try:
                val = val.decode("utf-8", errors="replace")
            except Exception:
                val = ""
        if val:
            return str(val)
    code = getattr(self, "status_code", None)
    if code is None:
        code = getattr(self, "status", None)
    if code is not None:
        try:
            from http import HTTPStatus
            return HTTPStatus(int(code)).phrase
        except Exception:
            pass
    return ""


def _patch_response_class(cls: Any) -> bool:
    """Attach non-destructive `reason_phrase` property to a Response class if missing."""
    if not isinstance(cls, type) or hasattr(cls, "reason_phrase"):
        return False
    try:
        setattr(cls, "reason_phrase", property(_compat_reason_phrase))
        return True
    except Exception:
        return False


def _install_pyquotex_http_compat() -> int:
    """
    Ensure HTTP `Response` classes used by community `pyquotex` / `quotexapi`
    (requests, cloudscraper, curl_cffi, aiohttp, playwright, urllib3, http.client)
    expose `.reason_phrase`, fixing:
        AttributeError: 'Response' object has no attribute 'reason_phrase'
    """
    patched = 0
    candidate_specs = (
        ("requests.models", "Response"),
        ("requests", "Response"),
        ("urllib3.response", "HTTPResponse"),
        ("http.client", "HTTPResponse"),
        ("curl_cffi.requests.models", "Response"),
        ("curl_cffi.requests", "Response"),
        ("aiohttp.client_reqrep", "ClientResponse"),
        ("playwright.async_api._generated", "Response"),
        ("playwright.sync_api._generated", "Response"),
        ("playwright.async_api", "Response"),
        ("playwright.sync_api", "Response"),
    )
    for mod_name, attr_name in candidate_specs:
        mod = sys.modules.get(mod_name)
        if mod is None:
            try:
                mod = importlib.import_module(mod_name)
            except Exception:
                continue
        cls = getattr(mod, attr_name, None)
        if cls is not None and _patch_response_class(cls):
            patched += 1

    # Also scan already-imported HTTP / pyquotex / cloudscraper modules for custom Response subclasses
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if not any(
            k in mod_name
            for k in ("requests", "cloudscraper", "curl_cffi", "playwright", "aiohttp", "pyquotex", "quotexapi", "httpx")
        ):
            continue
        for attr_name in ("Response", "ClientResponse", "HTTPResponse"):
            cls = getattr(mod, attr_name, None)
            if cls is not None and _patch_response_class(cls):
                patched += 1
    return patched


_LOGIN_CHALLENGE_MESSAGE = (
    "Quotex blocked this automated sign-in with a Cloudflare challenge (HTTP 403). "
    "Sign in on the official Quotex website to check your account, "
    "and contact Quotex support about supported app access if this continues."
)


def _is_login_challenge(message: str) -> bool:
    detail = message.lower()
    return "403" in detail and "cloudflare" in detail


def _format_pyquotex_technical_details(
    exc: Optional[BaseException] = None,
    extra: str = "",
    cls: Any = None,
) -> str:
    lines: List[str] = [
        f"Python: {sys.version.split()[0]} ({sys.platform})",
    ]
    if cls is not None:
        try:
            mod = sys.modules.get(cls.__module__)
            mod_file = getattr(mod, "__file__", "") if mod is not None else ""
            lines.append(f"Adapter: {cls.__module__}.{cls.__name__}" + (f" ({mod_file})" if mod_file else ""))
        except Exception:
            pass
    if extra:
        lines.append(f"Detail: {extra}")
    if exc is not None:
        lines.append(f"Exception: {type(exc).__name__}: {exc}")
        tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
        if tb_text:
            tb_lines = tb_text.splitlines()
            if len(tb_lines) > 18:
                tb_lines = ["... (earlier frames omitted) ..."] + tb_lines[-18:]
            lines.append("")
            lines.extend(tb_lines)
    return "\n".join(lines).strip()


def _load_pyquotex_class() -> Any:
    """Detect installed community Quotex client (`pyquotex` or `quotexapi`)."""
    _install_pyquotex_http_compat()
    candidates = [
        ("pyquotex.stable_api", "Quotex"),
        ("quotexapi.stable_api", "Quotex"),
    ]
    for mod_name, attr_name in candidates:
        try:
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, attr_name, None)
            if cls is not None:
                _install_pyquotex_http_compat()
                return cls
        except Exception:
            continue
    return None


PYQUOTEX_CLS = _load_pyquotex_class()
HAS_PYQUOTEX = PYQUOTEX_CLS is not None


# Quotex Socket.IO asset naming conventions
ASSET_SYMBOL_MAP: Dict[str, str] = {
    "EURUSD-OTC": "EURUSD_otc",
    "GBPUSD-OTC": "GBPUSD_otc",
    "USDJPY-OTC": "USDJPY_otc",
    "AUDCAD-OTC": "AUDCAD_otc",
    "EURJPY-OTC": "EURJPY_otc",
    "USDCHF-OTC": "USDCHF_otc",
    "USDCAD-OTC": "USDCAD_otc",
    "AUDUSD-OTC": "AUDUSD_otc",
    "NZDUSD-OTC": "NZDUSD_otc",
    "EURGBP-OTC": "EURGBP_otc",
    "GBPJPY-OTC": "GBPJPY_otc",
    "AUDJPY-OTC": "AUDJPY_otc",
    "CADJPY-OTC": "CADJPY_otc",
    "CHFJPY-OTC": "CHFJPY_otc",
    "EURAUD-OTC": "EURAUD_otc",
    "GBPAUD-OTC": "GBPAUD_otc",
    "EURCAD-OTC": "EURCAD_otc",
    "USDBRL-OTC": "USDBRL_otc",
    "USDINR-OTC": "USDINR_otc",
    "USDMXN-OTC": "USDMXN_otc",
    "USDZAR-OTC": "USDZAR_otc",
    "USDPKR-OTC": "USDPKR_otc",
    "USDBDT-OTC": "USDBDT_otc",
    "USDTRY-OTC": "USDTRY_otc",
    "XAUUSD-OTC": "XAUUSD_otc",
    "XAGUSD-OTC": "XAGUSD_otc",
    "UKBRENT-OTC": "UKBrent_otc",
    "USCRUDE-OTC": "USCrude_otc",
    "US100-OTC": "NASDAQ_otc",
    "US30-OTC": "DJI_otc",
    "SP500-OTC": "SP500_otc",
    "AAPL-OTC": "AAPL_otc",
    "TSLA-OTC": "TSLA_otc",
    "MSFT-OTC": "MSFT_otc",
    "INTC-OTC": "INTC_otc",
    "BA-OTC": "BA_otc",
    "BTCUSDT": "BTCUSD",
    "ETHUSDT": "ETHUSD",
    "SOLUSDT": "SOLUSD",
    "XRPUSDT": "XRPUSD",
    "BNBUSDT": "BNBUSD",
    "DOGEUSDT": "DOGEUSD",
    "ADAUSDT": "ADAUSD",
    "LTCUSDT": "LTCUSD",
    "AVAXUSDT": "AVAXUSD",
    "DOTUSDT": "DOTUSD",
    "LINKUSDT": "LINKUSD",
    "TONUSDT": "TONUSD",
}

TF_SECONDS: Dict[str, int] = {
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


def to_quotex_asset_candidates(symbol: str, qx_name: str = "") -> List[str]:
    """Return ordered Quotex asset identifiers to try for a SignalDesk symbol."""
    clean = symbol.strip()
    upper = clean.upper()
    candidates: List[str] = []

    if qx_name and qx_name.strip():
        candidates.append(qx_name.strip())

    if upper in ASSET_SYMBOL_MAP:
        candidates.append(ASSET_SYMBOL_MAP[upper])

    if upper.endswith("-OTC"):
        base = upper[:-4]
        candidates.append(f"{base}_otc")
        candidates.append(f"{base}_OTC")
    elif upper.endswith("_OTC"):
        base = upper[:-4]
        candidates.append(f"{base}_otc")
        candidates.append(upper)
    elif upper.endswith("USDT"):
        base = upper[:-4]
        candidates.append(f"{base}USD")
        candidates.append(f"{base}USD_otc")
    else:
        candidates.append(clean)
        candidates.append(upper)

    # Deduplicate while preserving order
    seen = set()
    ordered: List[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _normalize_pyquotex_candles(raw: Any, limit: int) -> List[Dict[str, Any]]:
    """Normalize candlestick payloads across pyquotex / quotexapi forks."""
    if not raw:
        return []
    if isinstance(raw, dict):
        # Some forks wrap list inside {"data": [...]} or {"candles": [...]}
        for key in ("candles", "data", "history", "result"):
            val = raw.get(key)
            if isinstance(val, list):
                raw = val
                break
    if not isinstance(raw, list):
        return []

    by_time: Dict[int, Dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        ts_raw = item.get("time")
        if ts_raw is None:
            ts_raw = item.get("from")
        if ts_raw is None:
            ts_raw = item.get("timestamp")
        if ts_raw is None:
            continue

        try:
            ts_num = float(ts_raw)
            if ts_num > 1e12:
                ts_num = ts_num / 1000.0
            ts = int(ts_num)

            o_raw = item.get("open")
            c_raw = item.get("close")
            h_raw = item.get("high") if item.get("high") is not None else item.get("max")
            l_raw = item.get("low") if item.get("low") is not None else item.get("min")
            v_raw = item.get("ticks") if item.get("ticks") is not None else item.get("volume", 1.0)

            if o_raw is None or c_raw is None:
                continue
            o = float(o_raw)
            c = float(c_raw)
            h = float(h_raw) if h_raw is not None else max(o, c)
            l = float(l_raw) if l_raw is not None else min(o, c)
            v = float(v_raw) if v_raw is not None else 1.0
            if o <= 0 or c <= 0:
                continue
            h = max(h, o, c)
            l = min(l, o, c)

            by_time[ts] = {
                "time": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
        except Exception:
            continue

    ordered = [by_time[t] for t in sorted(by_time.keys())]
    if limit > 0 and len(ordered) > limit:
        ordered = ordered[-limit:]
    return ordered


# ---------------------------------------------------------------------------
# Thread-safe interactive OTP hook for pyquotex under pythonw.exe (no stdin)
# ---------------------------------------------------------------------------
_INPUT_LOCK = threading.Lock()
_REAL_INPUT = builtins.input
_THREAD_SESSIONS: Dict[int, "PyQuotexLoginSession"] = {}
_PENDING_LOGINS: Dict[str, "PyQuotexLoginSession"] = {}


def _hooked_input(prompt: Any = "") -> str:
    tid = threading.get_ident()
    session = _THREAD_SESSIONS.get(tid)
    if session is None and len(_PENDING_LOGINS) == 1:
        # Handle pyquotex builds that call input() inside asyncio.to_thread / executor
        session = next(iter(_PENDING_LOGINS.values()))
    if session is None:
        return _REAL_INPUT(prompt)
    return session.wait_for_otp(str(prompt or ""))


class _HookedStdin:
    """Fallback stdin stream when running under pythonw.exe (where sys.stdin is None)."""

    def readline(self, _size: int = -1) -> str:
        return _hooked_input("") + "\n"

    def isatty(self) -> bool:
        return False


def _ensure_input_hook_installed() -> None:
    import sys as _sys

    with _INPUT_LOCK:
        if builtins.input is not _hooked_input:
            builtins.input = _hooked_input
        if _sys.stdin is None:
            _sys.stdin = _HookedStdin()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Persistent Live pyquotex Session Manager (keeps WebSocket alive after login)
# ---------------------------------------------------------------------------
class LivePyQuotexManager:
    """
    Maintains a single persistent asyncio event loop thread and the active
    authenticated `pyquotex` client so live Quotex candlesticks, payouts,
    and assets can be queried continuously after 2-step login.
    """

    def __init__(self) -> None:
        self._state_lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_thread_id: Optional[int] = None
        self._loop_ready = threading.Event()
        self._fetch_lock: Optional[asyncio.Lock] = None

        self._client: Any = None
        self._connected: bool = False
        self._email: str = ""
        self._account_type: str = "DEMO"
        self._balance: Optional[float] = None
        self._connected_at: Optional[int] = None
        self._last_error: Optional[str] = None

        self._maintenance_task = None
        self._maintenance_after = 0.0
        self._outage_id: Optional[str] = None
        self._failed_outage_id: Optional[str] = None

        # Short TTL caches + rolling history buffer for candles and open assets/payouts
        self._candle_cache: Dict[Tuple[str, str], Tuple[float, List[Dict[str, Any]]]] = {}
        self._candle_history: Dict[Tuple[str, str], Dict[int, Dict[str, Any]]] = {}
        self._resolved_asset_map: Dict[str, str] = {}
        self._assets_meta_cache: Dict[str, Dict[str, Any]] = {}
        self._assets_fetched_at: float = 0.0
        self._priority_waiters: int = 0
        self._last_ok_fetch_at: float = 0.0
        self._backfill_inflight: set[Tuple[str, str]] = set()
        self._backfill_after: Dict[Tuple[str, str], float] = {}
        self._refresh_inflight: set[Tuple[str, str]] = set()

    def ensure_loop(self) -> Tuple[asyncio.AbstractEventLoop, int]:
        with self._state_lock:
            if self._loop is not None and self._loop.is_running() and self._loop_thread_id is not None:
                return self._loop, self._loop_thread_id

            self._loop_ready.clear()

            def _loop_runner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                self._loop_thread_id = threading.get_ident()
                self._fetch_lock = asyncio.Lock()
                self._loop_ready.set()
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            self._loop_thread = threading.Thread(
                target=_loop_runner, name="SignalDeskPyQuotexLoop", daemon=True
            )
            self._loop_thread.start()

        self._loop_ready.wait(timeout=5.0)
        assert self._loop is not None and self._loop_thread_id is not None
        return self._loop, self._loop_thread_id

    def run_coro(self, coro: Any, timeout: float = 20.0) -> Any:
        loop, _ = self.ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            fut.cancel()
            raise

    def is_connected(self) -> bool:
        with self._state_lock:
            return bool(self._connected and self._client is not None)

    def get_status(self) -> Dict[str, Any]:
        with self._state_lock:
            connected = bool(self._connected and self._client is not None)
            return {
                "connected": connected,
                "authorized": connected,
                "mode": "LIVE_PYQUOTEX" if connected else "DISCONNECTED",
                "account_type": self._account_type,
                "email": self._email if connected else "",
                "balance": self._balance if connected else None,
                "connected_at": self._connected_at if connected else None,
                "has_pyquotex": _load_pyquotex_class() is not None,
                "live_assets_count": len(self._assets_meta_cache),
                "last_error": self._last_error,
            }

    def maintain_connection(self) -> Optional[str]:
        """Check connection health and recover a dropped authenticated client in the background."""
        with self._state_lock:
            client = self._client
            loop = self._loop
            if client is None or loop is None or not loop.is_running():
                return None
            connected = self._connected
            if not connected and self._outage_id is None:
                self._outage_id = str(time.time_ns())
                self._maintenance_after = 0
            if time.monotonic() >= self._maintenance_after and (
                self._maintenance_task is None or self._maintenance_task.done()
            ):
                self._maintenance_after = time.monotonic() + (20 if connected else 120)
                self._maintenance_task = asyncio.run_coroutine_threadsafe(
                    self._maintain_client(client, connected), loop
                )
            return self._failed_outage_id if not connected else None

    async def _maintain_client(self, client: Any, connected: bool) -> None:
        if connected and not hasattr(client, "check_connect"):
            return
        try:
            call = client.check_connect if connected else client.connect
            result = call()
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=5 if connected else 25)
            ok = bool(result[0]) if isinstance(result, tuple) else result is not False if connected else bool(result)
            if ok and not connected and hasattr(client, "change_account"):
                with self._state_lock:
                    if self._client is not client:
                        return
                    mode = "PRACTICE" if self._account_type == "DEMO" else "REAL"
                changed = client.change_account(mode)
                if inspect.isawaitable(changed):
                    await asyncio.wait_for(changed, timeout=5)
        except Exception:
            ok = False
        with self._state_lock:
            if self._client is not client:
                return
            if connected and self._connected and not ok:
                self._connected = False
                self._last_error = "Quotex WebSocket connection dropped."
            elif not connected and ok:
                self._connected = True
                self._connected_at = int(time.time())
                self._last_ok_fetch_at = 0
                self._candle_cache.clear()
                self._last_error = None
                self._outage_id = self._failed_outage_id = None
            elif not connected:
                self._last_error = "Automatic Quotex reconnect failed. Sign in again if required."
                self._failed_outage_id = self._outage_id

    async def _close_client_async(self, client: Any) -> None:
        if client is None:
            return
        for close_name in ("close", "disconnect"):
            if hasattr(client, close_name):
                try:
                    c_res = getattr(client, close_name)()
                    if inspect.isawaitable(c_res):
                        await c_res
                except Exception:
                    pass
                break

    async def set_active_client_async(
        self,
        client: Any,
        email: str,
        account_type: str,
        balance: Optional[float],
    ) -> None:
        with self._state_lock:
            old_client = self._client
            self._client = client
            self._connected = True
            self._outage_id = self._failed_outage_id = None
            self._maintenance_after = 0
            self._email = email
            self._account_type = account_type.upper()
            self._balance = balance
            self._connected_at = int(time.time())
            self._last_error = None
            self._candle_cache.clear()
            self._candle_history.clear()
            self._resolved_asset_map.clear()
            self._assets_meta_cache.clear()
            self._assets_fetched_at = 0.0
            self._last_ok_fetch_at = 0.0
            self._backfill_inflight.clear()
            self._backfill_after.clear()
            self._refresh_inflight.clear()

        if old_client is not None and old_client is not client:
            await self._close_client_async(old_client)

    def disconnect(self) -> Dict[str, Any]:
        with self._state_lock:
            client = self._client
            self._client = None
            self._connected = False
            self._outage_id = self._failed_outage_id = None
            self._balance = None
            self._connected_at = None
            self._candle_cache.clear()
            self._candle_history.clear()
            self._resolved_asset_map.clear()
            self._assets_meta_cache.clear()
            self._assets_fetched_at = 0.0
            self._last_ok_fetch_at = 0.0
            self._backfill_inflight.clear()
            self._backfill_after.clear()
            self._refresh_inflight.clear()

        if client is not None and self._loop is not None and self._loop.is_running():
            try:
                self.run_coro(self._close_client_async(client), timeout=5.0)
            except Exception:
                pass
        return self.get_status()

    async def _call_get_candles(
        self, client: Any, qx_asset: str, end_from_time: int, offset_sec: int, period_sec: int
    ) -> Any:
        fn = getattr(client, "get_candles", None)
        if fn is None:
            raise RuntimeError("Installed pyquotex client does not expose get_candles().")
        api = getattr(client, "api", None)
        before = (getattr(api, "candle_v2_data", None) or {}).get(qx_asset)
        res = fn(qx_asset, end_from_time, offset_sec, period_sec)
        if inspect.isawaitable(res):
            res = await res

        # PyQuotex can return only the tick-derived tail from its high-level
        # method. The same request may also have delivered broker OHLC bars.
        response = (getattr(api, "candle_v2_data", None) or {}).get(qx_asset)
        if response is before or not isinstance(response, dict) or response.get("asset") != qx_asset:
            return res
        candles = response.get("candles")
        if not isinstance(candles, list):
            return res
        records = []
        for bar in candles:
            if isinstance(bar, dict):
                records.append(bar)
            elif isinstance(bar, (list, tuple)) and len(bar) >= 5:
                records.append(dict(zip(("time", "open", "close", "high", "low"), bar)))
        window_start = end_from_time - offset_sec - period_sec
        broker_bars = {
            bar["time"]: bar for bar in _normalize_pyquotex_candles(records, 450)
            if window_start <= bar["time"] <= end_from_time and bar["time"] % period_sec == 0
        }
        # Preserve the live tail for periods overlapping the broker history.
        broker_bars.update((bar["time"], bar) for bar in _normalize_pyquotex_candles(res, 450))
        return [broker_bars[ts] for ts in sorted(broker_bars)]

    async def _backfill_history_async(
        self,
        upper_sym: str,
        matched_asset: str,
        timeframe: str,
        limit: int,
        max_extra_pages: int = 4,
    ) -> None:
        """
        Quietly paginate older Quotex candles in the background after fast initial paint.
        History shares the candle request lock with chart updates.
        """
        hist_key = (upper_sym, timeframe)
        period_sec = TF_SECONDS.get(timeframe, 60)
        page_offset = period_sec * 60
        try:
            for page in range(max(0, max_extra_pages)):
                with self._state_lock:
                    client = self._client
                    connected = self._connected
                    cur_hist = self._candle_history.get(hist_key, {})
                    if not connected or client is None or len(cur_hist) >= limit or not cur_hist:
                        return
                    earliest_ts = min(cur_hist.keys())
                    prev_count = len(cur_hist)

                cursor_end = max(0, earliest_ts - period_sec)
                if cursor_end <= 0:
                    return

                assert self._fetch_lock is not None
                async with self._fetch_lock:
                    with self._state_lock:
                        client = self._client
                        if not self._connected or client is None:
                            return
                        existing_hist = dict(self._candle_history.get(hist_key, {}))
                    if len(existing_hist) >= limit or not existing_hist:
                        return
                    earliest_ts = min(existing_hist.keys())
                    prev_count = len(existing_hist)
                    cursor_end = max(0, earliest_ts - period_sec)
                    if cursor_end <= 0:
                        return
                    try:
                        older_bars = []
                        history = getattr(client, "get_historical_candles", None)
                        if page == 0 and history is not None:
                            try:
                                raw = await asyncio.wait_for(
                                    history(matched_asset, period_sec * limit, period_sec, timeout=2, max_workers=1),
                                    timeout=2.3,
                                )
                                older_bars = _normalize_pyquotex_candles(raw, limit)
                            except (TimeoutError, TypeError, AttributeError):
                                pass
                        if not any(bar["time"] < earliest_ts for bar in older_bars):
                            raw_older = await asyncio.wait_for(
                                self._call_get_candles(client, matched_asset, cursor_end, page_offset, period_sec),
                                timeout=2.2,
                            )
                            older_bars = _normalize_pyquotex_candles(raw_older, limit)
                        if not older_bars:
                            return
                        for bar in older_bars:
                            existing_hist.setdefault(int(bar["time"]), bar)
                        if len(existing_hist) <= prev_count:
                            return
                        sorted_times = sorted(existing_hist.keys())
                        if len(sorted_times) > 450:
                            keep = sorted_times[-450:]
                            existing_hist = {t: existing_hist[t] for t in keep}
                            sorted_times = keep
                        candles = [existing_hist[t] for t in sorted_times]
                        now_done = time.monotonic()
                        with self._state_lock:
                            if self._client is not client or not self._connected:
                                return
                            self._candle_history[hist_key] = existing_hist
                            self._candle_cache[hist_key] = (now_done, candles)
                    except Exception:
                        return
                await asyncio.sleep(0.02)
        finally:
            with self._state_lock:
                self._backfill_inflight.discard(hist_key)

    async def _fetch_candles_async(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        cache_ttl: float,
        qx_name: str = "",
        max_pages: int = 4,
        priority: bool = True,
        force_refresh: bool = False,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        now = time.monotonic()
        upper_sym = symbol.upper()
        hist_key = (upper_sym, timeframe)

        with self._state_lock:
            cached = self._candle_cache.get(hist_key)
            if not force_refresh and cached and (now - cached[0]) < cache_ttl and cached[1]:
                bars = cached[1]
                return (bars[-limit:] if len(bars) > limit else list(bars)), None
            client = self._client
            connected = self._connected
            fallback_hist = self._candle_history.get(hist_key)

        if not connected or client is None:
            return [], "Not logged in to Quotex."

        # Preemption: never let background market scans starve an active chart switch!
        if not priority and self._priority_waiters > 0:
            if fallback_hist:
                sorted_fb = sorted(fallback_hist.keys())
                return [fallback_hist[t] for t in sorted_fb[-limit:]], None
            return [], None

        period_sec = TF_SECONDS.get(timeframe, 60)

        assert self._fetch_lock is not None
        if priority:
            self._priority_waiters += 1
        try:
            async with self._fetch_lock:
                # Re-check cache after acquiring lock (another request may have filled it)
                now2 = time.monotonic()
                with self._state_lock:
                    cached2 = self._candle_cache.get(hist_key)
                    if not force_refresh and cached2 and (now2 - cached2[0]) < cache_ttl and cached2[1]:
                        bars2 = cached2[1]
                        return (bars2[-limit:] if len(bars2) > limit else list(bars2)), None
                    client = self._client
                    if not self._connected or client is None:
                        return [], "Quotex session disconnected."
                    existing_hist = dict(self._candle_history.get(hist_key, {}))
                    last_ok = self._last_ok_fetch_at
                    resolved = self._resolved_asset_map.get(upper_sym)
                    assets_meta = self._assets_meta_cache

                if not priority and self._priority_waiters > 0:
                    if existing_hist:
                        sorted_fb = sorted(existing_hist.keys())
                        return [existing_hist[t] for t in sorted_fb[-limit:]], None
                    return [], None

                # Verify socket connection only if we haven't had a healthy fetch in the last 25s
                if hasattr(client, "check_connect") and (now2 - last_ok) > 25.0:
                    try:
                        alive = client.check_connect()
                        if inspect.isawaitable(alive):
                            alive = await alive
                        if alive is False:
                            with self._state_lock:
                                if self._client is client:
                                    self._last_error = "Quotex WebSocket connection dropped."
                                    self._connected = False
                            return [], self._last_error
                    except Exception:
                        pass

                # Build candidate Quotex asset names (start with cached resolved name or exact live asset)
                candidates = to_quotex_asset_candidates(symbol, qx_name=qx_name)
                if assets_meta:
                    # Prioritize candidates known to exist in Quotex's live asset map
                    known_first = [c for c in candidates if c in assets_meta]
                    unknown_rest = [c for c in candidates if c not in assets_meta]
                    candidates = known_first + unknown_rest
                if resolved and resolved in candidates:
                    candidates.remove(resolved)
                    candidates.insert(0, resolved)
                elif resolved:
                    candidates.insert(0, resolved)

                end_from_time = int(time.time())
                last_err: Optional[str] = None
                matched_asset: Optional[str] = None

                # Fast tail fetch when we already have >= 20 bars in rolling history;
                # otherwise fetch a rich first chunk and backfill deeper history asynchronously.
                has_warm_history = len(existing_hist) >= 20
                first_offset = period_sec * (16 if has_warm_history else max(60, min(limit, 120)))
                first_timeout = 2.6 if priority else 1.8

                for qx_asset in candidates:
                    try:
                        raw = await asyncio.wait_for(
                            self._call_get_candles(client, qx_asset, end_from_time, first_offset, period_sec),
                            timeout=first_timeout,
                        )
                        batch = _normalize_pyquotex_candles(raw, limit * 2)
                        if batch:
                            matched_asset = qx_asset
                            for bar in batch:
                                existing_hist[int(bar["time"])] = bar
                            break
                    except asyncio.TimeoutError:
                        last_err = f"Quotex timed out returning candles for {qx_asset}."
                    except Exception as exc:
                        last_err = str(exc)[:220]

                if not matched_asset:
                    if existing_hist:
                        sorted_fb = sorted(existing_hist.keys())
                        return [existing_hist[t] for t in sorted_fb[-limit:]], None
                    return [], last_err or f"Quotex returned no live candles for {symbol} (market may be closed)."

                # Synchronous pagination only on cold start if < 20 bars returned; deeper history backfills async!
                sync_extra_pages = 0
                if not has_warm_history and len(existing_hist) < 20 and max_pages > 1:
                    sync_extra_pages = 1

                if sync_extra_pages > 0:
                    page_offset = period_sec * 60
                    earliest_ts = min(existing_hist.keys())
                    cursor_end = max(0, earliest_ts - period_sec)
                    if cursor_end > 0:
                        try:
                            raw_older = await asyncio.wait_for(
                                self._call_get_candles(client, matched_asset, cursor_end, page_offset, period_sec),
                                timeout=2.2,
                            )
                            older_bars = _normalize_pyquotex_candles(raw_older, limit)
                            for bar in older_bars:
                                existing_hist.setdefault(int(bar["time"]), bar)
                        except Exception:
                            pass

                # Trim rolling history to last 450 bars and store full rolling list in cache
                sorted_times = sorted(existing_hist.keys())
                if len(sorted_times) > 450:
                    keep = sorted_times[-450:]
                    existing_hist = {t: existing_hist[t] for t in keep}
                    sorted_times = keep

                full_candles = [existing_hist[t] for t in sorted_times]
                candles = full_candles[-limit:] if len(full_candles) > limit else full_candles
                now_done = time.monotonic()
                schedule_backfill = False
                with self._state_lock:
                    self._candle_history[hist_key] = existing_hist
                    self._candle_cache[hist_key] = (now_done, full_candles)
                    self._resolved_asset_map[upper_sym] = matched_asset
                    self._last_error = None
                    self._last_ok_fetch_at = now_done
                    if (priority and len(existing_hist) < limit and max_pages > 1
                            and hist_key not in self._backfill_inflight
                            and now_done >= self._backfill_after.get(hist_key, 0)):
                        self._backfill_after[hist_key] = now_done + 20
                        self._backfill_inflight.add(hist_key)
                        schedule_backfill = True

                if schedule_backfill and self._loop is not None:
                    extra_pages = max(1, max_pages - 1 - sync_extra_pages)
                    self._loop.create_task(
                        self._backfill_history_async(
                            upper_sym=upper_sym,
                            matched_asset=matched_asset,
                            timeframe=timeframe,
                            limit=limit,
                            max_extra_pages=extra_pages,
                        )
                    )
                return candles, None
        finally:
            if priority:
                self._priority_waiters = max(0, self._priority_waiters - 1)

    def _schedule_swr_refresh(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        qx_name: str,
        max_pages: int,
    ) -> None:
        hist_key = (symbol.upper(), timeframe)
        with self._state_lock:
            if hist_key in self._refresh_inflight or not self._connected or self._client is None:
                return
            loop = self._loop
            if loop is None or not loop.is_running():
                return
            self._refresh_inflight.add(hist_key)

        async def _bg() -> None:
            try:
                await self._fetch_candles_async(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=limit,
                    cache_ttl=0.0,
                    qx_name=qx_name,
                    max_pages=max_pages,
                    priority=False,
                    force_refresh=True,
                )
            except Exception:
                pass
            finally:
                with self._state_lock:
                    self._refresh_inflight.discard(hist_key)

        try:
            asyncio.run_coroutine_threadsafe(_bg(), loop)
        except Exception:
            with self._state_lock:
                self._refresh_inflight.discard(hist_key)

    def fetch_live_candles(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 160,
        cache_ttl: float = 2.0,
        timeout: float = 6.0,
        qx_name: str = "",
        max_pages: int = 4,
        priority: bool = True,
        force_refresh: bool = False,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if not self.is_connected():
            return [], "Not logged in to Quotex."

        # Ultra-fast lock-free cache / SWR path (avoids crossing threads to asyncio loop!)
        now = time.monotonic()
        hist_key = (symbol.upper(), timeframe)
        is_sub = TF_SECONDS.get(timeframe, 60) < 60
        stale_ttl = 4.0 if is_sub else 12.0
        swr_bars: Optional[List[Dict[str, Any]]] = None
        with self._state_lock:
            cached = self._candle_cache.get(hist_key)
            if not force_refresh and cached and cached[1]:
                age = now - cached[0]
                bars = cached[1]
                if age < cache_ttl:
                    return (bars[-limit:] if len(bars) > limit else list(bars)), None
                if age < stale_ttl and len(bars) >= min(limit, 20):
                    swr_bars = bars[-limit:] if len(bars) > limit else list(bars)
        if swr_bars is not None:
            self._schedule_swr_refresh(symbol, timeframe, limit, qx_name, max_pages)
            return swr_bars, None

        try:
            return self.run_coro(
                self._fetch_candles_async(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=limit,
                    cache_ttl=cache_ttl,
                    qx_name=qx_name,
                    max_pages=max_pages,
                    priority=priority,
                    force_refresh=force_refresh,
                ),
                timeout=timeout,
            )
        except Exception as exc:
            # Graceful fallback to cached bars if a live poll transiently timed out
            with self._state_lock:
                fb = self._candle_cache.get(hist_key)
                if fb and fb[1] and time.monotonic() - fb[0] < stale_ttl:
                    bars_fb = fb[1]
                    return (bars_fb[-limit:] if len(bars_fb) > limit else list(bars_fb)), None
            return [], f"Quotex live candle error: {str(exc)[:220]}"

    def get_cached_candles(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 120,
        max_age: float = 24.0,
    ) -> Optional[List[Dict[str, Any]]]:
        """Return recently cached Quotex candles without blocking on WebSocket."""
        now = time.monotonic()
        hist_key = (symbol.upper(), timeframe)
        with self._state_lock:
            cached = self._candle_cache.get(hist_key)
            if cached and (now - cached[0]) <= max_age and cached[1]:
                candles = cached[1]
                return candles[-limit:] if len(candles) > limit else list(candles)
        return None

    async def _sync_assets_async(self) -> Dict[str, Dict[str, Any]]:
        with self._state_lock:
            client = self._client
            connected = self._connected
        if not connected or client is None:
            return {}

        discovered: Dict[str, Dict[str, Any]] = {}

        def duration_payout(profit, key):
            try:
                value = float(profit[key])
                return value if 0 < value <= 100 else None
            except (KeyError, TypeError, ValueError):
                return None

        def canonical(name):
            return "".join(char for char in name.upper() if char.isalnum())

        assert self._fetch_lock is not None
        async with self._fetch_lock:
            # 1. Try get_payment() which returns dict of asset -> {open, profit: {1M, 5M}}
            if hasattr(client, "get_payment"):
                try:
                    pay = client.get_payment()
                    if inspect.isawaitable(pay):
                        pay = await pay
                    if isinstance(pay, dict):
                        for raw_name, info in pay.items():
                            if not isinstance(info, dict):
                                continue
                            is_open = bool(info.get("open", True))
                            profit_obj = info.get("profit") or {}
                            payout = 85
                            if isinstance(profit_obj, dict):
                                val = profit_obj.get("1M") or profit_obj.get("5M") or next(iter(profit_obj.values()), 85)
                                try:
                                    payout = int(float(val))
                                except Exception:
                                    payout = 85
                            elif isinstance(profit_obj, (int, float)):
                                payout = int(profit_obj)
                            discovered[str(raw_name)] = {
                                "qx_name": str(raw_name),
                                "open": is_open,
                                "payout": max(50, min(98, payout)),
                                "payout_1m": duration_payout(profit_obj, "1M"),
                                "payout_5m": duration_payout(profit_obj, "5M"),
                            }
                except Exception:
                    pass

            # 2. Fallback/supplement with get_all_assets()
            if hasattr(client, "get_all_assets"):
                try:
                    all_a = client.get_all_assets()
                    if inspect.isawaitable(all_a):
                        all_a = await all_a
                    if isinstance(all_a, dict):
                        by_name = {canonical(name): (name, meta) for name, meta in discovered.items()}
                        for raw_name in all_a:
                            key = str(raw_name)
                            display, meta = by_name.get(canonical(key), (key, {}))
                            profit = client.get_payout_by_asset(key, "all") if hasattr(client, "get_payout_by_asset") else None
                            if inspect.isawaitable(profit):
                                profit = await profit
                            payout_1m = duration_payout(profit, "1M") or meta.get("payout_1m")
                            payout_5m = duration_payout(profit, "5M") or meta.get("payout_5m")
                            if display != key:
                                discovered.pop(display, None)
                            discovered[key] = {
                                "qx_name": key, "open": meta.get("open", True),
                                "payout": meta.get("payout", int(payout_1m or payout_5m or 85)),
                                "payout_1m": payout_1m, "payout_5m": payout_5m,
                            }
                except Exception:
                    pass

            # Refresh balance while we're here
            if hasattr(client, "get_balance"):
                try:
                    bal = client.get_balance()
                    if inspect.isawaitable(bal):
                        bal = await bal
                    if bal is not None:
                        with self._state_lock:
                            self._balance = float(bal)
                except Exception:
                    pass

        with self._state_lock:
            self._assets_meta_cache = discovered
            self._assets_fetched_at = time.monotonic()
        return discovered

    def sync_live_assets(self, timeout: float = 12.0) -> Dict[str, Dict[str, Any]]:
        if not self.is_connected():
            return {}
        try:
            return self.run_coro(self._sync_assets_async(), timeout=timeout)
        except Exception:
            return {}

    def get_live_payout_for_symbol(self, symbol: str) -> Optional[int]:
        with self._state_lock:
            if not self._assets_meta_cache:
                return None
            for cand in to_quotex_asset_candidates(symbol):
                meta = self._assets_meta_cache.get(cand)
                if meta and "payout" in meta:
                    return int(meta["payout"])
        return None


LIVE_QUOTEX_MANAGER = LivePyQuotexManager()


def _get_pyquotex_session_dir() -> str:
    session_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "SignalDesk",
        "quotex_session",
    )
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


def _clear_pyquotex_session_state(session_dir: Optional[str] = None) -> List[str]:
    """
    Purge stale pyquotex `session.json` files and reset module-level
    `global_value` flags that otherwise cause persistent
    'Websocket connection rejected.' errors after cookie/SSID expiry.
    """
    removed: List[str] = []
    search_dirs = [
        session_dir or _get_pyquotex_session_dir(),
        os.getcwd(),
        os.path.dirname(os.path.abspath(__file__)),
    ]
    seen_paths: set[str] = set()
    for base in search_dirs:
        if not base:
            continue
        candidate = os.path.abspath(os.path.join(base, "session.json"))
        if candidate in seen_paths:
            continue
        seen_paths.add(candidate)
        if os.path.isfile(candidate):
            try:
                os.remove(candidate)
                removed.append(candidate)
            except Exception:
                pass

    # Reset stale module-level flags inside pyquotex / quotexapi if imported
    for mod_name in ("pyquotex.global_value", "quotexapi.global_value"):
        mod = sys.modules.get(mod_name)
        if mod is None:
            try:
                mod = importlib.import_module(mod_name)
            except Exception:
                mod = None
        if mod is not None:
            for attr, val in (
                ("SSID", None),
                ("check_websocket_if_connect", None),
                ("check_rejected_connection", False),
                ("check_accepted_connection", False),
                ("check_websocket_if_error", False),
                ("websocket_error_reason", None),
                ("ssl_Mutual_exclusion", False),
                ("started_listen_instruments", False),
            ):
                if hasattr(mod, attr):
                    try:
                        setattr(mod, attr, val)
                    except Exception:
                        pass
    return removed


class PyQuotexLoginSession:
    """
    Runs `pyquotex` login on `LIVE_QUOTEX_MANAGER`'s persistent event loop so that:
    1. When Quotex emails an OTP and `pyquotex` calls `input(...)`, login pauses cleanly
       and returns `mode: "OTP_REQUIRED"` instead of crashing with `lost sys.stdin`.
    2. Once authentication succeeds, the live `pyquotex` WebSocket client stays open inside
       `LIVE_QUOTEX_MANAGER` to feed real-time Quotex candlesticks into the Live Chart!
    3. Automatically clears stale cached `session.json` + resets `pyquotex.global_value`
       and retries once if Quotex rejects an expired WebSocket session.
    """

    def __init__(
        self,
        email: str,
        password: str,
        account_type: str = "DEMO",
        force_fresh: bool = False,
    ):
        self.login_id = f"qx-{uuid.uuid4().hex[:10]}"
        self.email = email.strip()
        self.password = password
        self.account_type = account_type.upper()
        self.force_fresh = bool(force_fresh)
        self.created_at = time.time()

        self.otp_prompt: str = ""
        self.otp_value: Optional[str] = None
        self.otp_was_submitted = False
        self.cancelled: bool = False
        self.result: Optional[Dict[str, Any]] = None

        self.otp_requested_event = threading.Event()
        self.otp_submitted_event = threading.Event()
        self.done_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def wait_for_otp(self, prompt: str) -> str:
        self.otp_prompt = prompt.strip() or "Enter the verification code sent to your email."
        self.otp_value = None
        self.otp_submitted_event.clear()
        self.otp_requested_event.set()

        # Wait up to 5 minutes for the user to enter OTP in the UI
        received = self.otp_submitted_event.wait(timeout=300.0)
        if not received or self.cancelled or not self.otp_value:
            raise RuntimeError("OTP verification timed out or was cancelled.")
        code = self.otp_value
        self.otp_value = None
        return code

    async def wait_for_otp_async(self, prompt: str) -> str:
        return await asyncio.to_thread(self.wait_for_otp, prompt)

    def cancel(self) -> None:
        self.cancelled = True
        self.otp_submitted_event.set()

    def start(self, initial_wait_sec: float = 75.0) -> Dict[str, Any]:
        _ensure_input_hook_installed()
        _PENDING_LOGINS[self.login_id] = self
        self._thread = threading.Thread(target=self._worker_main, daemon=True)
        self._thread.start()

        deadline = time.monotonic() + initial_wait_sec
        while time.monotonic() < deadline:
            if self.done_event.is_set():
                _PENDING_LOGINS.pop(self.login_id, None)
                return self.result or {
                    "connected": False,
                    "authorized": False,
                    "mode": "AUTH_FAILED",
                    "account_type": self.account_type,
                    "has_pyquotex": True,
                    "message": "Quotex login exited unexpectedly.",
                }
            if self.otp_requested_event.is_set():
                return {
                    "connected": False,
                    "authorized": False,
                    "mode": "OTP_REQUIRED",
                    "login_id": self.login_id,
                    "prompt": self.otp_prompt,
                    "account_type": self.account_type,
                    "has_pyquotex": True,
                    "message": "Quotex sent a verification code (OTP) to your email. Enter it below to complete login.",
                }
            time.sleep(0.1)

        self.cancel()
        _PENDING_LOGINS.pop(self.login_id, None)
        return {
            "connected": False,
            "authorized": False,
            "mode": "AUTH_FAILED",
            "account_type": self.account_type,
            "has_pyquotex": True,
            "message": "Timed out waiting for Quotex login response.",
        }

    def submit_otp(self, otp: str, wait_sec: float = 45.0) -> Dict[str, Any]:
        code = otp.strip()
        if not code:
            return {
                "connected": False,
                "authorized": False,
                "mode": "OTP_REQUIRED",
                "login_id": self.login_id,
                "prompt": self.otp_prompt,
                "account_type": self.account_type,
                "has_pyquotex": True,
                "message": "Please enter the OTP code sent to your email.",
            }

        self.otp_requested_event.clear()
        self.otp_was_submitted = True
        self.otp_value = code
        self.otp_submitted_event.set()

        deadline = time.monotonic() + wait_sec
        while time.monotonic() < deadline:
            if self.done_event.is_set():
                _PENDING_LOGINS.pop(self.login_id, None)
                return self.result or {
                    "connected": False,
                    "authorized": False,
                    "mode": "AUTH_FAILED",
                    "account_type": self.account_type,
                    "has_pyquotex": True,
                    "message": "Quotex OTP verification ended without a result.",
                }
            if self.otp_requested_event.is_set():
                return {
                    "connected": False,
                    "authorized": False,
                    "mode": "OTP_REQUIRED",
                    "login_id": self.login_id,
                    "prompt": self.otp_prompt,
                    "account_type": self.account_type,
                    "has_pyquotex": True,
                    "message": "Quotex requested another verification code. Check your email and try again.",
                }
            time.sleep(0.1)

        self.cancel()
        _PENDING_LOGINS.pop(self.login_id, None)
        return {
            "connected": False,
            "authorized": False,
            "mode": "AUTH_FAILED",
            "account_type": self.account_type,
            "has_pyquotex": True,
            "message": "Timed out waiting for Quotex to verify the OTP code.",
        }

    def _worker_main(self) -> None:
        loop, loop_tid = LIVE_QUOTEX_MANAGER.ensure_loop()
        worker_tid = threading.get_ident()
        _THREAD_SESSIONS[worker_tid] = self
        _THREAD_SESSIONS[loop_tid] = self
        try:
            fut = asyncio.run_coroutine_threadsafe(self._async_connect(), loop)
            self.result = fut.result(timeout=340.0)
        except Exception as exc:
            blocked = _is_login_challenge(str(exc))
            self.result = {
                "connected": False,
                "authorized": False,
                "mode": "CLOUDFLARE_BLOCKED" if blocked else "AUTH_FAILED",
                "account_type": self.account_type,
                "has_pyquotex": True,
                "message": _LOGIN_CHALLENGE_MESSAGE if blocked else f"pyquotex login error: {str(exc)[:280]}",
                "technical_details": _format_pyquotex_technical_details(exc=exc, cls=_load_pyquotex_class()),
            }
        finally:
            _THREAD_SESSIONS.pop(worker_tid, None)
            _THREAD_SESSIONS.pop(loop_tid, None)
            self.done_event.set()

    async def _async_connect(self) -> Dict[str, Any]:
        _install_pyquotex_http_compat()
        cls = _load_pyquotex_class()
        if cls is None:
            return {
                "connected": False,
                "authorized": False,
                "mode": "PYQUOTEX_NOT_INSTALLED",
                "account_type": self.account_type,
                "has_pyquotex": False,
                "message": (
                    "Community library `pyquotex` is not installed in this Python environment. "
                    "On Windows, double-click Install_PyQuotex.bat in your SignalDesk folder then restart SignalDesk."
                ),
            }

        session_dir = _get_pyquotex_session_dir()
        init_kwargs: Dict[str, Any] = {"email": self.email, "password": self.password}
        try:
            sig = inspect.signature(cls.__init__)
            if "lang" in sig.parameters:
                init_kwargs["lang"] = "en"
            if "root_path" in sig.parameters:
                init_kwargs["root_path"] = session_dir
            if "on_otp_callback" in sig.parameters:
                init_kwargs["on_otp_callback"] = self.wait_for_otp_async
        except Exception:
            pass

        # Close any existing stale live client first to avoid duplicate socket rejection
        with LIVE_QUOTEX_MANAGER._state_lock:
            prev_client = LIVE_QUOTEX_MANAGER._client
            LIVE_QUOTEX_MANAGER._client = None
            LIVE_QUOTEX_MANAGER._connected = False
        if prev_client is not None:
            await LIVE_QUOTEX_MANAGER._close_client_async(prev_client)

        if self.force_fresh:
            _clear_pyquotex_session_state(session_dir)

        async def _try_connect_once() -> Tuple[Any, bool, str]:
            _install_pyquotex_http_compat()
            cli = cls(**init_kwargs)
            _install_pyquotex_http_compat()
            res = cli.connect()
            if inspect.isawaitable(res):
                res = await res
            ok = False
            why = ""
            if isinstance(res, tuple):
                ok = bool(res[0])
                why = str(res[1]) if len(res) > 1 else ""
            else:
                ok = bool(res)
            return cli, ok, why

        client: Any = None
        keep_open = False
        retried_fresh = False
        try:
            client, connected, reason = await _try_connect_once()

            # Auto-recovery: pyquotex frequently returns "Websocket connection rejected."
            # when an expired session.json / Cloudflare cookie is reused on disk.
            # Clear stale session files + reset global_value and retry once cleanly!
            reason_lower = reason.lower()
            is_ws_rejected = any(
                tok in reason_lower
                for tok in ("rejected", "websocket", "403", "closed", "session", "expired", "handshake")
            ) or (not connected and not reason_lower)

            if (
                not connected and not self.cancelled and is_ws_rejected
                and not self.force_fresh and not self.otp_was_submitted and not _is_login_challenge(reason)
            ):
                await LIVE_QUOTEX_MANAGER._close_client_async(client)
                client = None
                _clear_pyquotex_session_state(session_dir)
                retried_fresh = True
                await asyncio.sleep(0.6)
                client, connected, reason = await _try_connect_once()

            if not connected:
                # Never leave a broken session.json behind after a rejected connection
                _clear_pyquotex_session_state(session_dir)
                detail = reason or "Quotex login did not succeed. Verify your email, password, or OTP code."
                blocked = _is_login_challenge(detail)
                if blocked:
                    detail = _LOGIN_CHALLENGE_MESSAGE
                elif retried_fresh and ("rejected" in detail.lower() or "websocket" in detail.lower()):
                    detail = (
                        f"{detail} SignalDesk automatically cleared the expired session cache and retried, "
                        "but Quotex still rejected the WebSocket handshake. Wait 30–60s (disable VPN/proxy if active), "
                        "click 'Fresh Login (Reset Session)', or sign in via the Browser Session (SSID + Cookie) tab."
                    )
                return {
                    "connected": False,
                    "authorized": False,
                    "mode": "CLOUDFLARE_BLOCKED" if blocked else "AUTH_FAILED",
                    "account_type": self.account_type,
                    "has_pyquotex": True,
                    "retried_fresh": retried_fresh,
                    "message": detail,
                    "technical_details": _format_pyquotex_technical_details(
                        extra=f"connect() returned connected=False, reason={reason!r}, force_fresh={self.force_fresh}, retried_fresh={retried_fresh}",
                        cls=cls,
                    ),
                }

            mode_name = "PRACTICE" if self.account_type == "DEMO" else "REAL"
            if hasattr(client, "change_account"):
                change_res = client.change_account(mode_name)
                if inspect.isawaitable(change_res):
                    try:
                        await asyncio.wait_for(change_res, timeout=8)
                    except TimeoutError as exc:
                        raise RuntimeError("Quotex did not confirm the selected account after login.") from exc

            balance: Optional[float] = None
            if hasattr(client, "get_balance"):
                try:
                    bal_res = client.get_balance()
                    if inspect.isawaitable(bal_res):
                        bal_res = await asyncio.wait_for(bal_res, timeout=3)
                    balance = float(bal_res)
                except Exception:
                    balance = None

            # KEEP the authenticated pyquotex client alive for live candlestick streaming!
            await LIVE_QUOTEX_MANAGER.set_active_client_async(
                client=client,
                email=self.email,
                account_type=self.account_type,
                balance=balance,
            )
            keep_open = True

            # Pre-load live Quotex asset payouts in the background (best-effort)
            live_assets_count = 0
            try:
                discovered = await asyncio.wait_for(LIVE_QUOTEX_MANAGER._sync_assets_async(), timeout=4.0)
                live_assets_count = len(discovered)
            except Exception:
                pass

            return {
                "connected": True,
                "authorized": True,
                "mode": "LIVE_PYQUOTEX",
                "account_type": self.account_type,
                "email": self.email,
                "has_pyquotex": True,
                "balance": balance,
                "live_assets_count": live_assets_count,
                "retried_fresh": retried_fresh,
                "message": (
                    f"Connected to Quotex {self.account_type} via pyquotex — Live Quotex candlestick feed active"
                    + (f" (Balance: ${balance:,.2f})." if balance is not None else ".")
                ),
            }
        finally:
            if not keep_open and client is not None:
                await LIVE_QUOTEX_MANAGER._close_client_async(client)


def _prune_stale_logins() -> None:
    now = time.time()
    for lid, sess in list(_PENDING_LOGINS.items()):
        if now - sess.created_at > 360:
            sess.cancel()
            _PENDING_LOGINS.pop(lid, None)


def connect_with_pyquotex(
    email: str,
    password: str,
    account_type: str = "DEMO",
    pin: str = "",
    force_fresh: bool = False,
) -> Dict[str, Any]:
    """Start Step 1 of interactive Quotex email/password login via pyquotex."""
    _prune_stale_logins()
    if _load_pyquotex_class() is None:
        return {
            "connected": False,
            "authorized": False,
            "mode": "PYQUOTEX_NOT_INSTALLED",
            "account_type": account_type.upper(),
            "has_pyquotex": False,
            "message": (
                "Community library `pyquotex` is not installed in this Python environment. "
                "On Windows, double-click Install_PyQuotex.bat in your SignalDesk folder (or run: "
                "pip install git+https://github.com/cleitonleonel/pyquotex.git && playwright install chromium) "
                "then restart SignalDesk."
            ),
        }

    if not email.strip() or not password:
        return {
            "connected": False,
            "authorized": False,
            "mode": "MISSING_CREDENTIALS",
            "account_type": account_type.upper(),
            "has_pyquotex": True,
            "message": "Enter both your Quotex email and password, then click Login.",
        }

    # Cancel any earlier unfinished login attempts cleanly
    for old_id, old_sess in list(_PENDING_LOGINS.items()):
        old_sess.cancel()
        _PENDING_LOGINS.pop(old_id, None)

    session = PyQuotexLoginSession(
        email=email,
        password=password,
        account_type=account_type,
        force_fresh=force_fresh,
    )
    step1 = session.start()
    if step1.get("mode") == "OTP_REQUIRED" and pin.strip():
        return session.submit_otp(pin.strip())
    return step1


def submit_pyquotex_otp(login_id: str, otp: str) -> Dict[str, Any]:
    """Step 2: Supply the Email OTP to an active paused pyquotex login session."""
    _prune_stale_logins()
    session = _PENDING_LOGINS.get(login_id.strip())
    if session is None:
        return {
            "connected": False,
            "authorized": False,
            "mode": "OTP_EXPIRED",
            "message": "Login session expired or was not found. Please click Login to Quotex again.",
        }
    return session.submit_otp(otp)


def cancel_pyquotex_login(login_id: str) -> Dict[str, Any]:
    session = _PENDING_LOGINS.pop(login_id.strip(), None)
    if session is not None:
        session.cancel()
    return {"ok": True}


def is_pyquotex_connected() -> bool:
    return LIVE_QUOTEX_MANAGER.is_connected()


def get_pyquotex_status() -> Dict[str, Any]:
    return LIVE_QUOTEX_MANAGER.get_status()


def disconnect_pyquotex() -> Dict[str, Any]:
    return LIVE_QUOTEX_MANAGER.disconnect()


def fetch_live_quotex_candles(
    symbol: str,
    timeframe: str = "1m",
    limit: int = 160,
    cache_ttl: float = 2.0,
    timeout: float = 6.0,
    qx_name: str = "",
    max_pages: int = 4,
    priority: bool = True,
    force_refresh: bool = False,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    return LIVE_QUOTEX_MANAGER.fetch_live_candles(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        cache_ttl=cache_ttl,
        timeout=timeout,
        qx_name=qx_name,
        max_pages=max_pages,
        priority=priority,
        force_refresh=force_refresh,
    )


def get_cached_quotex_candles(
    symbol: str,
    timeframe: str = "1m",
    limit: int = 120,
    max_age: float = 18.0,
) -> Optional[List[Dict[str, Any]]]:
    return LIVE_QUOTEX_MANAGER.get_cached_candles(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        max_age=max_age,
    )


def cached_live_quotex_assets() -> Dict[str, Dict[str, Any]]:
    with LIVE_QUOTEX_MANAGER._state_lock:
        return dict(LIVE_QUOTEX_MANAGER._assets_meta_cache)


def sync_live_quotex_assets() -> Dict[str, Dict[str, Any]]:
    return LIVE_QUOTEX_MANAGER.sync_live_assets()


def get_live_quotex_payout(symbol: str) -> Optional[int]:
    return LIVE_QUOTEX_MANAGER.get_live_payout_for_symbol(symbol)


class QuotexClient:
    """
    Quotex WebSocket client using Engine.IO / Socket.IO v3 frames.
    Supports pasting your browser SSID along with matching Cookie & User-Agent
    headers from your active logged-in qxbroker.com session.
    """

    WS_URL = "wss://ws2.qxbroker.com/socket.io/?EIO=3&transport=websocket"

    def __init__(
        self,
        ssid: str = "",
        account_type: str = "DEMO",
        auto_trade: bool = False,
        cookie: str = "",
        user_agent: str = "",
    ):
        self.ssid = self._clean_ssid(ssid)
        self.cookie = cookie.strip()
        self.user_agent = (
            user_agent.strip()
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        self.account_type = account_type.upper()
        self.auto_trade = auto_trade
        self.connected = False
        self.authorized = False
        self.last_error: Optional[str] = None
        self._ws: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._auth_event = threading.Event()

    @staticmethod
    def _clean_ssid(raw: str) -> str:
        val = raw.strip()
        if val.startswith("42["):
            try:
                parsed = json.loads(val[2:])
                if isinstance(parsed, list) and len(parsed) > 1 and isinstance(parsed[1], dict):
                    return str(parsed[1].get("session", "")).strip()
            except Exception:
                pass
        return val

    def connect(self, timeout_sec: float = 6.0) -> Dict[str, Any]:
        if not self.ssid:
            self.connected = False
            return {
                "connected": False,
                "authorized": False,
                "mode": "SIGNAL_ONLY",
                "account_type": self.account_type,
                "has_websocket_lib": HAS_WEBSOCKET,
                "has_pyquotex": HAS_PYQUOTEX,
                "message": "No SSID token provided; running in Signal & Paper Simulation mode.",
            }

        if not HAS_WEBSOCKET:
            self.connected = False
            return {
                "connected": False,
                "authorized": False,
                "mode": "MISSING_DEPENDENCY",
                "account_type": self.account_type,
                "has_websocket_lib": False,
                "has_pyquotex": HAS_PYQUOTEX,
                "message": "Install `websocket-client` (`pip install websocket-client`) to open a Quotex WebSocket connection.",
            }

        self._stop_event.clear()
        self._auth_event.clear()
        self.last_error = None

        is_demo = 1 if self.account_type == "DEMO" else 0
        headers = [
            "Origin: https://qxbroker.com",
            f"User-Agent: {self.user_agent}",
        ]
        if self.cookie:
            headers.append(f"Cookie: {self.cookie}")

        def on_open(ws_app: Any) -> None:
            self.connected = True
            auth_payload = json.dumps([
                "authorization",
                {
                    "session": self.ssid,
                    "isDemo": is_demo,
                    "tournamentId": 0,
                },
            ])
            ws_app.send("40")
            ws_app.send(f"42{auth_payload}")

        def on_message(ws_app: Any, message: str) -> None:
            if message == "2":
                ws_app.send("3")
                return
            if "authorization" in message or "s_authorization" in message:
                self.authorized = True
                self._auth_event.set()
            elif "not_authorized" in message or "error" in message.lower():
                self.last_error = f"Quotex auth rejected: {message[:180]}"
                self._auth_event.set()

        def on_error(_ws_app: Any, err: Any) -> None:
            raw = str(err)
            lower = raw.lower()
            if "403" in lower and ("cloudflare" in lower or "forbidden" in lower):
                ray_id = ""
                marker = "cf-ray"
                idx = lower.find(marker)
                if idx != -1:
                    snippet = raw[idx : idx + 64]
                    parts = snippet.split("'")
                    if len(parts) >= 3:
                        ray_id = parts[2].strip()
                self.last_error = (
                    "CLOUDFLARE_BLOCKED::"
                    "Quotex (Cloudflare) rejected the direct WebSocket handshake (HTTP 403 Forbidden)."
                    + (f" Ray ID: {ray_id}." if ray_id else "")
                    + " Try pasting your active browser Cookie + User-Agent alongside your SSID, or switch to the Quotex Login (pyquotex) tab on your local Windows PC."
                )
            else:
                clean = raw.split("<!DOCTYPE", 1)[0].split("<html", 1)[0].strip()
                self.last_error = (clean or raw)[:280]
            self._auth_event.set()

        def on_close(_ws_app: Any, _code: Any, _msg: Any) -> None:
            self.connected = False
            self.authorized = False

        self._ws = websocket.WebSocketApp(
            self.WS_URL,
            header=headers,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        self._thread = threading.Thread(
            target=lambda: self._ws.run_forever(ping_interval=20, ping_timeout=10),
            daemon=True,
        )
        self._thread.start()

        def heartbeat() -> None:
            while not self._stop_event.is_set() and self.connected:
                try:
                    if self._ws:
                        self._ws.send("2")
                except Exception:
                    break
                self._stop_event.wait(20)

        threading.Thread(target=heartbeat, daemon=True).start()

        self._auth_event.wait(timeout=timeout_sec)

        if self.authorized:
            return {
                "connected": True,
                "authorized": True,
                "mode": "LIVE_WEBSOCKET",
                "account_type": self.account_type,
                "has_websocket_lib": True,
                "has_pyquotex": HAS_PYQUOTEX,
                "message": f"Connected and authorized on Quotex {self.account_type} WebSocket.",
            }

        if self.last_error and self.last_error.startswith("CLOUDFLARE_BLOCKED::"):
            return {
                "connected": False,
                "authorized": False,
                "mode": "CLOUDFLARE_BLOCKED",
                "account_type": self.account_type,
                "has_websocket_lib": True,
                "has_pyquotex": HAS_PYQUOTEX,
                "message": self.last_error.split("CLOUDFLARE_BLOCKED::", 1)[1],
            }

        return {
            "connected": self.connected,
            "authorized": False,
            "mode": "AUTH_PENDING_OR_FAILED",
            "account_type": self.account_type,
            "has_websocket_lib": True,
            "has_pyquotex": HAS_PYQUOTEX,
            "message": self.last_error or "WebSocket opened, but Quotex SSID authorization did not confirm within timeout.",
        }

    def close(self) -> None:
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self.connected = False
        self.authorized = False

    def place_order(
        self,
        asset: str,
        direction: str,
        amount: float,
        duration_sec: int = 60,
        entry_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        order_id = f"QX-{uuid.uuid4().hex[:8].upper()}"
        qx_asset = ASSET_SYMBOL_MAP.get(asset, asset.replace("-", "_"))
        action = "call" if direction.upper() == "CALL" else "put"
        executed_live = False

        if self.connected and self.authorized and self.auto_trade and self._ws:
            request_id = int(time.time() * 1000)
            frame = json.dumps([
                "orders/open",
                {
                    "asset": qx_asset,
                    "amount": round(float(amount), 2),
                    "time": int(duration_sec),
                    "action": action,
                    "isDemo": 1 if self.account_type == "DEMO" else 0,
                    "tournamentId": 0,
                    "requestId": request_id,
                    "optionType": 100,
                },
            ])
            try:
                self._ws.send(f"42{frame}")
                executed_live = True
            except Exception as exc:
                self.last_error = str(exc)

        return {
            "order_id": order_id,
            "asset": qx_asset,
            "direction": direction.upper(),
            "amount": round(amount, 2),
            "duration_sec": duration_sec,
            "entry_price": entry_price,
            "account_type": self.account_type,
            "executed_live": executed_live,
            "error": self.last_error if not executed_live and self.auto_trade else None,
            "timestamp": int(time.time()),
        }
