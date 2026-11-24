"""
SignalDesk - Standalone Windows Desktop Application Launcher.

Serves the compiled SignalDesk React dashboard (ui_dist/) and handles every
trading API route directly in pure Python (no Node.js required on Windows),
then opens a dedicated desktop window via WebView2 (pywebview) or Microsoft
Edge / Chrome --app mode.
"""

from __future__ import annotations

import ctypes
import io
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure sibling Python modules are importable both from source and PyInstaller bundle
if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = Path(sys.executable).parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent
    APP_DIR = BUNDLE_DIR

if str(BUNDLE_DIR) not in sys.path:
    sys.path.insert(0, str(BUNDLE_DIR))

# Under pythonw.exe on Windows, sys.stdout / sys.stderr are None; guard them so
# third-party libraries calling print() never crash with stdio errors.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SignalDesk"
LOG_FILE = LOG_DIR / "signaldesk.log"
QUOTEX_BROWSER_URL = "https://quotex.com/"
_QUOTEX_BROWSER_LOCK = threading.Lock()
_quotex_browser_process = None


def log_line(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def show_windows_error(title: str, message: str) -> None:
    log_line(f"ERROR: {title} - {message}")
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
        except Exception:
            pass


if "--headless-server" not in sys.argv:
    os.environ["SIGNALDESK_DESKTOP"] = "1"

try:
    from engine import dispatch, start_paper_lab_background  # noqa: E402
    import market_feed as mf  # noqa: E402
except Exception as exc:
    show_windows_error(
        "SignalDesk Startup Error",
        f"Failed to load SignalDesk Python modules:\n{exc}\n\nLog: {LOG_FILE}",
    )
    raise


class FastThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def get_request(self) -> Any:
        conn, addr = super().get_request()
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        return conn, addr


def locate_ui_dist() -> Optional[Path]:
    """Locate bundled production UI directory (ui_dist or ../dist)."""
    candidates = [
        BUNDLE_DIR / "ui_dist",
        APP_DIR / "ui_dist",
        APP_DIR.parent / "ui_dist",
        APP_DIR.parent / "dist",
    ]
    for path in candidates:
        if path.is_dir() and (path / "index.html").is_file():
            return path
    return None


def locate_python_dir() -> Path:
    for candidate in (BUNDLE_DIR / "python_bot", APP_DIR / "python_bot", BUNDLE_DIR, APP_DIR):
        if (candidate / "engine.py").is_file():
            return candidate
    return BUNDLE_DIR


UI_DIST_DIR = locate_ui_dist()
PYTHON_DIR = locate_python_dir()


def check_existing_server(start_port: int = 8080) -> Optional[str]:
    """Return existing SignalDesk URL if an instance is already running."""
    for port in range(start_port, start_port + 10):
        url = f"http://127.0.0.1:{port}/api/health"
        try:
            with urllib.request.urlopen(url, timeout=0.35) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("app") == "SignalDesk" and data.get("mode") == "desktop":
                    return f"http://127.0.0.1:{port}"
        except Exception:
            continue
    return None


def find_free_port(preferred: int = 8080) -> int:
    for port in [preferred] + list(range(8081, 8110)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def list_bot_files() -> Dict[str, Any]:
    files = []
    for entry in sorted(PYTHON_DIR.iterdir(), key=lambda p: p.name):
        if entry.name.startswith("__") or entry.suffix == ".pyc" or not entry.is_file():
            continue
        try:
            content = entry.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append({"name": entry.name, "size": entry.stat().st_size, "content": content})
    return {"files": files}


def build_bot_zip_bytes(custom_config: Optional[Dict[str, Any]] = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in sorted(PYTHON_DIR.iterdir(), key=lambda p: p.name):
            if entry.name.startswith("__") or entry.suffix == ".pyc" or not entry.is_file():
                continue
            arcname = f"quotex_signal_bot/{entry.name}"
            if entry.name == "config.json" and custom_config:
                zf.writestr(arcname, json.dumps(custom_config, indent=2))
            else:
                zf.write(entry, arcname)
    return buf.getvalue()


class SignalDeskHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _send_json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path

        if req_path == "/api/health":
            self._send_json(200, {"ok": True, "app": "SignalDesk",
                                  "mode": "preview" if "--headless-server" in sys.argv else "desktop"})
            return

        if req_path == "/api/catalog":
            try:
                self._send_json(200, dispatch({"action": "catalog"}))
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if req_path == "/api/bot-files":
            try:
                self._send_json(200, list_bot_files())
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if req_path == "/api/quotex/status":
            try:
                self._send_json(200, dispatch({"action": "quotex_status"}))
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if req_path == "/api/windows-files":
            self._send_json(200, {"files": [], "uiBundleReady": UI_DIST_DIR is not None})
            return

        if req_path == "/api/paper-lab/overview":
            try:
                self._send_json(200, dispatch({"action": "paper_lab_overview"}))
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
            return

        if req_path == "/api/paper-lab/settings":
            try:
                self._send_json(200, dispatch({"action": "paper_lab_get_settings"}))
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
            return

        if req_path == "/api/paper-lab/history":
            try:
                qs = urllib.parse.parse_qs(parsed.query)
                self._send_json(
                    200,
                    dispatch(
                        {
                            "action": "paper_lab_history",
                            "table": (qs.get("table") or ["paper_trades"])[0],
                            "from_ts": (qs.get("from_ts") or [""])[0],
                            "to_ts": (qs.get("to_ts") or [""])[0],
                            "asset": (qs.get("asset") or [""])[0],
                            "strategy_version": (qs.get("strategy_version") or [""])[0],
                            "feed_kind": (qs.get("feed_kind") or [""])[0],
                            "limit": int((qs.get("limit") or ["250"])[0] or "250"),
                        }
                    ),
                )
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
            return

        if req_path == "/api/paper-lab/export":
            try:
                qs = urllib.parse.parse_qs(parsed.query)
                fmt = (qs.get("format") or ["json"])[0].lower()
                table = (qs.get("table") or ["paper_trades"])[0]
                res = dispatch(
                    {
                        "action": "paper_lab_export",
                        "table": table,
                        "format": fmt,
                        "from_ts": (qs.get("from_ts") or [""])[0],
                        "to_ts": (qs.get("to_ts") or [""])[0],
                        "asset": (qs.get("asset") or [""])[0],
                        "strategy_version": (qs.get("strategy_version") or [""])[0],
                        "feed_kind": (qs.get("feed_kind") or [""])[0],
                    }
                )
                raw = str(res.get("content", "")).encode("utf-8")
                ext = "csv" if fmt == "csv" else "json"
                ctype = "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Disposition", f'attachment; filename="paper_lab_{table}.{ext}"')
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
            return

        if UI_DIST_DIR is None:
            msg = b"SignalDesk UI assets (ui_dist/) were not found."
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(msg)
            return

        rel = req_path.lstrip("/")
        if rel == "registerSW.js":
            # In local Windows desktop mode, unregister any old localhost SW so reinstalls always load fresh UI
            cleanup_js = (
                b"if ('serviceWorker' in navigator) {"
                b" navigator.serviceWorker.getRegistrations().then(function(regs){"
                b" regs.forEach(function(r){ r.unregister(); });"
                b" });"
                b"}\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(cleanup_js)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(cleanup_js)
            return

        target = (UI_DIST_DIR / rel).resolve() if rel else (UI_DIST_DIR / "index.html").resolve()
        if not str(target).startswith(str(UI_DIST_DIR.resolve())) or not target.is_file():
            target = (UI_DIST_DIR / "index.html").resolve()

        data = target.read_bytes()
        mime, _ = mimetypes.guess_type(str(target))
        if target.suffix == ".js":
            mime = "application/javascript; charset=utf-8"
        elif target.suffix == ".css":
            mime = "text/css; charset=utf-8"
        elif target.suffix == ".svg":
            mime = "image/svg+xml"
        elif target.suffix == ".webmanifest":
            mime = "application/manifest+json"

        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path
        try:
            body = self._read_json_body()
        except Exception as exc:
            self._send_json(400, {"error": f"Invalid JSON body: {exc}"})
            return

        try:
            if req_path == "/api/dispatch":
                self._send_json(200, dispatch(body))
            elif req_path == "/api/analyze":
                res = dispatch(
                    {
                        "action": "analyze",
                        "symbol": body.get("symbol", "EURUSD"),
                        "timeframe": body.get("timeframe", "1m"),
                        "config": body.get("config", {}),
                        "force_refresh": bool(body.get("force_refresh", False)),
                    }
                )
                self._send_json(200, res)
            elif req_path == "/api/scan":
                res = dispatch(
                    {
                        "action": "scan_all",
                        "timeframe": body.get("timeframe", "1m"),
                        "active_symbol": body.get("active_symbol", ""),
                        "config": body.get("config", {}),
                    }
                )
                self._send_json(200, res)
            elif req_path == "/api/backtest":
                payload = {"action": "backtest", **body}
                self._send_json(200, dispatch(payload))
            elif req_path == "/api/telegram/detect-chat":
                res = dispatch(
                    {
                        "action": "telegram_detect_chat",
                        "bot_token": body.get("bot_token", ""),
                    }
                )
                self._send_json(200, res)
            elif req_path == "/api/telegram/test":
                res = dispatch(
                    {
                        "action": "telegram_test",
                        "bot_token": body.get("bot_token", ""),
                        "chat_id": body.get("chat_id", ""),
                        "message": body.get("message", ""),
                    }
                )
                self._send_json(200, res)
            elif req_path == "/api/telegram/signal":
                res = dispatch({"action": "telegram_signal", **body})
                self._send_json(200, res)
            elif req_path == "/api/quotex/browser":
                expected_origin = f"http://127.0.0.1:{self.server.server_address[1]}"
                if self.headers.get("Origin") != expected_origin or self.headers.get("X-SignalDesk-Action") != "quotex-browser":
                    self._send_json(403, {"ok": False, "error": "Open Quotex Browser from SignalDesk."})
                elif "--headless-server" in sys.argv:
                    self._send_json(501, {"ok": False, "error": "Quotex Browser is available in the Windows app."})
                else:
                    try:
                        open_quotex_browser()
                        self._send_json(200, {"ok": True})
                    except (RuntimeError, OSError) as exc:
                        self._send_json(409, {"ok": False, "error": str(exc)})
            elif req_path == "/api/quotex/test":
                res = dispatch(
                    {
                        "action": "quotex_test",
                        "auth_method": body.get("auth_method", "ssid"),
                        "ssid": body.get("ssid", ""),
                        "cookie": body.get("cookie", ""),
                        "user_agent": body.get("user_agent", ""),
                        "email": body.get("email", ""),
                        "password": body.get("password", ""),
                        "pin": body.get("pin", ""),
                        "account_type": body.get("account_type", "DEMO"),
                        "force_fresh": bool(body.get("force_fresh", False)),
                    }
                )
                self._send_json(200, res)
            elif req_path == "/api/quotex/verify-otp":
                res = dispatch(
                    {
                        "action": "quotex_verify_otp",
                        "login_id": body.get("login_id", ""),
                        "otp": body.get("otp", ""),
                    }
                )
                self._send_json(200, res)
            elif req_path == "/api/quotex/cancel-otp":
                res = dispatch(
                    {
                        "action": "quotex_cancel_otp",
                        "login_id": body.get("login_id", ""),
                    }
                )
                self._send_json(200, res)
            elif req_path == "/api/quotex/disconnect":
                self._send_json(200, dispatch({"action": "quotex_disconnect"}))
            elif req_path == "/api/quotex/sync-assets":
                self._send_json(200, dispatch({"action": "quotex_sync_assets"}))
            elif req_path == "/api/assets/add":
                res = dispatch({"action": "add_asset", **body})
                self._send_json(200, res)
            elif req_path == "/api/assets/remove":
                res = dispatch({"action": "remove_asset", "symbol": body.get("symbol", "")})
                self._send_json(200, res)
            elif req_path == "/api/windows-download":
                scripts = APP_DIR.parent / "windows_app"
                if not scripts.is_dir() or UI_DIST_DIR is None:
                    self._send_json(503, {"error": "Download the current installer from the SignalDesk project preview."})
                    return
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for folder, prefix in ((scripts, ""), (PYTHON_DIR, "python_bot/"), (UI_DIST_DIR, "ui_dist/")):
                        for file in sorted(folder.rglob("*")):
                            relative = file.relative_to(folder)
                            if not file.is_file() or file.name in {"config.json", "session.json", "paper_lab_config.json", "custom_assets.json"} or file.suffix == ".pyc" or any(
                                part.startswith(".") or part == "__pycache__" for part in relative.parts
                            ):
                                continue
                            archive.write(file, f"SignalDesk_Windows/{prefix}{relative.as_posix()}")
                payload = buffer.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Disposition", 'attachment; filename="SignalDesk_Windows_Installer.zip"')
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif req_path == "/api/bot-download":
                zip_bytes = build_bot_zip_bytes(body.get("config"))
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="quotex_python_signal_bot.zip"',
                )
                self.send_header("Content-Length", str(len(zip_bytes)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(zip_bytes)
            elif req_path == "/api/paper-lab/settings":
                self._send_json(200, dispatch({"action": "paper_lab_update_settings", **body}))
            elif req_path.startswith("/api/paper-lab/action/"):
                op = req_path.split("/api/paper-lab/action/", 1)[1].strip("/")
                op_map = {
                    "cycle": "paper_lab_run_cycle",
                    "retrain": "paper_lab_retrain",
                    "backup": "paper_lab_backup",
                    "rollback": "paper_lab_rollback",
                    "resume": "paper_lab_resume",
                    "verify": "paper_lab_run_verification",
                    "toggle-loop": "paper_lab_toggle_autoloop",
                }
                act = op_map.get(op)
                if not act:
                    self._send_json(404, {"ok": False, "error": f"Unknown Paper Lab operation: {op}"})
                else:
                    self._send_json(200, dispatch({"action": act, **body}))
            elif req_path == "/api/shutdown":
                self._send_json(200, {"ok": True, "message": "Shutting down SignalDesk server."})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._send_json(404, {"error": f"Unknown API endpoint: {req_path}"})
        except Exception as exc:
            log_line(f"API error on {req_path}: {exc}")
            self._send_json(500, {"error": str(exc)})


def find_chromium_app_browser() -> Optional[str]:
    """Find Microsoft Edge or Google Chrome on Windows for frameless --app window mode."""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    for cmd in ("msedge", "chrome", "chromium", "google-chrome"):
        found = shutil.which(cmd)
        if found:
            return found
    return None


def open_browser_app_window(url: str) -> Optional[subprocess.Popen[Any]]:
    browser = find_chromium_app_browser()
    if browser:
        profile_dir = LOG_DIR / "EdgeAppProfile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            browser,
            f"--app={url}",
            "--new-window",
            "--window-size=1420,880",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile_dir}",
        ]
        log_line(f"Launching app window: {' '.join(args)}")
        return subprocess.Popen(args)
    log_line(f"No Edge/Chrome found; opening default browser at {url}")
    webbrowser.open(url)
    return None


def open_quotex_browser() -> None:
    global _quotex_browser_process
    from importlib.util import find_spec

    if os.name != "nt" or find_spec("webview") is None:
        raise RuntimeError("Install the current SignalDesk Windows package to use Quotex Browser.")
    with _QUOTEX_BROWSER_LOCK:
        if _quotex_browser_process is not None and _quotex_browser_process.poll() is None:
            raise RuntimeError("Quotex Browser is already open. Switch to its window.")
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve()))
        _quotex_browser_process = subprocess.Popen(
            [*command, "--quotex-browser"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def launch_quotex_browser() -> None:
    import webview

    profile = LOG_DIR.parent / "SignalDesk Data" / "Quotex Browser"
    profile.mkdir(parents=True, exist_ok=True)
    webview.create_window(
        "Quotex Browser | SignalDesk", QUOTEX_BROWSER_URL,
        width=1320, height=850, min_size=(800, 540),
    )
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(profile))


def launch_desktop_window(url: str, server: ThreadingHTTPServer) -> None:
    """Open SignalDesk in native pywebview if available, otherwise Edge/Chrome app mode."""
    try:
        import webview  # type: ignore

        log_line(f"Launching pywebview window for {url}")
        webview.create_window(
            "SignalDesk - Quotex Signal Bot",
            url,
            width=1400,
            height=880,
            min_size=(1024, 680),
            background_color="#0B101A",
        )
        webview.start()
        return
    except Exception:
        pass

    started = time.monotonic()
    proc = open_browser_app_window(url)
    if proc is not None:
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            return

        # On Windows, Edge/Chrome may hand off to an already running process and exit
        # within <3s. Keep the local server alive in that case so the window stays responsive!
        elapsed = time.monotonic() - started
        if elapsed >= 3.0:
            log_line(f"Desktop window closed after {elapsed:.1f}s; shutting down server.")
            return
        log_line(
            f"Browser launcher returned in {elapsed:.2f}s (handoff to running browser); keeping server alive."
        )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


def main() -> None:
    if "--quotex-browser" in sys.argv:
        try:
            launch_quotex_browser()
        except Exception as exc:
            show_windows_error("Quotex Browser", f"Could not open WebView2. {exc}")
        return
    if "--headless-server" in sys.argv:
        port = find_free_port(8080)
        server = FastThreadingHTTPServer(("127.0.0.1", port), SignalDeskHandler)
        mf.prewarm_top_assets("1m")
        try:
            start_paper_lab_background()
        except Exception as exc:
            log_line(f"PaperLab background auto-start skipped: {exc}")
        log_line(f"Headless server listening on http://127.0.0.1:{port}")
        print(f"PORT={port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return

    try:
        # If SignalDesk server is already running locally, just open a new window to it!
        existing_url = check_existing_server(8080)
        if existing_url:
            log_line(f"Reusing already running server at {existing_url}")
            open_browser_app_window(existing_url)
            return

        port = find_free_port(8080)
        url = f"http://127.0.0.1:{port}"
        server = FastThreadingHTTPServer(("127.0.0.1", port), SignalDeskHandler)
        mf.prewarm_top_assets("1m")
        try:
            start_paper_lab_background()
        except Exception as exc:
            log_line(f"PaperLab background auto-start skipped: {exc}")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        log_line(f"SignalDesk server started on {url} (UI={UI_DIST_DIR})")

        try:
            launch_desktop_window(url, server)
        finally:
            server.shutdown()
            server.server_close()
    except Exception:
        tb = traceback.format_exc()
        show_windows_error(
            "SignalDesk Desktop Error",
            f"SignalDesk could not start.\n\nDetails were saved to:\n{LOG_FILE}\n\n{tb[-600:]}",
        )
        raise


if __name__ == "__main__":
    main()
