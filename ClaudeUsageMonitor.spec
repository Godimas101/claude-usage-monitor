# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('usage_monitor.ico', '.'), ('VERSION', '.')],
    hiddenimports=[
        'pystray._win32',
        'PIL._tkinter_finder',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ClaudeUsageMonitor',
    debug=False,
    strip=False,
    # UPX is disabled on purpose. The GitHub runner has UPX installed, and
    # UPX-packed Python DLLs intermittently trip Windows Defender on first
    # launch after install ("Failed to load Python DLL … module could not be
    # found"). Uncompressed is a few MB larger but loads reliably.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon='usage_monitor.ico',
)
