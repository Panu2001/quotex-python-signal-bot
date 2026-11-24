# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for SignalDesk standalone Windows desktop executable."""

from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "ui_dist"), "ui_dist"),
    (str(ROOT / "python_bot"), "python_bot"),
    (str(ROOT / "signaldesk.ico"), "."),
]

a = Analysis(
    [str(ROOT / "python_bot" / "desktop_app.py")],
    pathex=[str(ROOT / "python_bot")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "indicators",
        "strategies",
        "risk_manager",
        "market_feed",
        "backtester",
        "telegram_notifier",
        "quotex_client",
        "engine",
        "webview",
        "paper_lab",
        "paper_lab.stubay",
        "paper_lab.stubay_policy",
        "paper_lab.engine_service",
        "paper_lab.storage",
        "paper_lab.modeling",
        "paper_lab.config",
        "paper_lab.auth",
        "paper_lab.ai_feedback",
        "paper_lab.telegram_outbox",
        "paper_lab.stubay_review",
        "pyquotex",
        "pyquotex.utils.strategy",
        "pyquotex.expiration",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SignalDesk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / "signaldesk.ico"),
)
