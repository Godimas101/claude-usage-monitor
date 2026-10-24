# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Claude Usage Monitor v2: a ONEDIR, windowed app.

Onedir, not onefile: a onefile exe unpacks all of its DLLs to %TEMP%\\_MEIxxxx on every launch,
and that per-launch extraction races Windows Defender (intermittently "Failed to load Python DLL
... module could not be found" right after an install). Onedir installs the files once (Inno
packages the whole folder), so there is no extraction race and startup is faster.

What the bundle carries besides the code:
  usage_monitor/webui           dashboard HTML/CSS/JS, the theme stylesheets and scripts, and the bundled
                                OFL fonts, which the dashboard (/static/fonts) and the Pillow skins both read
  usage_monitor/themes/*.json   the theme tokens
  usage_monitor/data/pricing.json
  VERSION, usage_monitor.ico
  pywebview                     collect_all('webview') brings its runtime JS and platform modules,
                                as in the GDC Sound spec

Each data file lands at the same path relative to the bundle root as it has in the repository, because
the app finds them through paths.resource_dir() (sys._MEIPASS, the _internal folder). The same exe is
the dashboard window: the monitor starts it with --dashboard --port N.

Not in the bundle, on purpose: tests/, docs/, design/, the parked v1 modules, and the dev server
(usage_monitor/ui/devserver.py, a fake-data server for building the UI). tests/test_update_check.py
evaluates this spec and fails when a resource the package reads is missing or something dev-only is in.

Local:  pyinstaller --noconfirm ClaudeUsageMonitor.spec   (or run build.bat)
CI:     .github/workflows/release.yml runs this on a VERSION bump that reaches main.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

PROJ = SPECPATH  # directory of this spec file (injected by PyInstaller)
PKG = os.path.join(PROJ, 'usage_monitor')

# collect_submodules() imports the package to list it, and finds nothing, without a word, when usage_monitor is not
# importable. `python -m PyInstaller` (build.bat) has the working folder on sys.path; the `pyinstaller` command
# (release.yml) does not. The CI rehearsal of 2026-10-24 built an exe without the skins, which get_skin() imports by
# name, so no widget could draw. The project goes first on the path, and the list is checked against the files below.
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

# Dev-only modules: nothing in the app imports them, and the hidden-import list below would pull them in.
DEV_ONLY = ['usage_monitor.ui.devserver']

APP_MODULES = collect_submodules('usage_monitor', filter=lambda name: name not in DEV_ONLY)


def _module_files(root, package):
    """Every module and package under ``package``, named from the files on disk (no import needed)."""
    names = set()
    for folder, dirs, files in os.walk(os.path.join(root, package)):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        dotted = os.path.relpath(folder, root).replace(os.sep, '.')
        names.update(dotted if f == '__init__.py' else f'{dotted}.{f[:-3]}' for f in files if f.endswith('.py'))
    return names


_missing = sorted(_module_files(PROJ, 'usage_monitor') - set(APP_MODULES) - set(DEV_ONLY))
if _missing:
    # A module left out is only noticed when it is used (the skins: no widget draws), so the build stops here instead.
    raise SystemExit(f"collect_submodules('usage_monitor') missed {len(_missing)} module(s), "
                     f"for example {', '.join(_missing[:3])}: usage_monitor is not importable from this spec")

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
    hiddenimports=wv_hidden + APP_MODULES,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # No Tk anywhere in v2. numpy: Pillow imports it only when it is installed and the app never does, but
    # a machine that has it would add ~26 MB (plus PyYAML and more that numpy drags in) to a local build
    # that the clean CI environment does not carry.
    excludes=['tkinter', 'pystray', 'pytest', 'numpy'] + DEV_ONLY,
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
