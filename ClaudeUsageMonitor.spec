# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Claude Usage Monitor v2: a ONEDIR, windowed app.

Onedir, not onefile: a onefile exe unpacks all of its DLLs to %TEMP%\\_MEIxxxx on every launch,
and that per-launch extraction races Windows Defender (intermittently "Failed to load Python DLL
... module could not be found" right after an install). Onedir installs the files once (Inno
packages the whole folder), so there is no extraction race and startup is faster.

What the bundle carries besides the code:
  usage_monitor/webui           dashboard HTML/CSS/JS and the bundled OFL fonts, which the dashboard
                                (/static/fonts) and the Pillow skins both read
  usage_monitor/themes/*.json   the theme tokens
  usage_monitor/data/pricing.json
  VERSION, usage_monitor.ico
  pywebview                     collect_all('webview') brings its runtime JS and platform modules,
                                as in the GDC Sound spec

The same exe is the dashboard window: the monitor starts it with --dashboard --port N.

Local:  pyinstaller --noconfirm ClaudeUsageMonitor.spec   (or run build.bat)
CI:     .github/workflows/release.yml runs this on a VERSION bump that reaches main.
"""
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

PROJ = SPECPATH  # directory of this spec file (injected by PyInstaller)
PKG = os.path.join(PROJ, 'usage_monitor')

wv_datas, wv_binaries, wv_hidden = collect_all('webview')

a = Analysis(
    [os.path.join(PROJ, 'main.py')],
    pathex=[PROJ],
    binaries=wv_binaries,
    datas=[
        (os.path.join(PKG, 'webui'), os.path.join('usage_monitor', 'webui')),
        (os.path.join(PKG, 'themes', '*.json'), os.path.join('usage_monitor', 'themes')),
        (os.path.join(PKG, 'data', 'pricing.json'), os.path.join('usage_monitor', 'data')),
        (os.path.join(PROJ, 'VERSION'), '.'),
        (os.path.join(PROJ, 'usage_monitor.ico'), '.'),
    ] + wv_datas,
    # The ui package loads its modules by name (lazy exports, importlib), so list them all.
    hiddenimports=wv_hidden + collect_submodules('usage_monitor'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'pystray', 'pytest'],  # no Tk anywhere in v2
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,        # onedir: binaries go into the COLLECT folder
    name='ClaudeUsageMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(PROJ, 'usage_monitor.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ClaudeUsageMonitor',
)
