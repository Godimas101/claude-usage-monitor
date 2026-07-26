# -*- mode: python ; coding: utf-8 -*-

# Built as a ONEDIR app (not onefile). A onefile exe unpacks all of its DLLs to
# %TEMP%\_MEIxxxx on every launch, and that per-launch extraction races Windows
# Defender — intermittently failing with "Failed to load Python DLL ... module
# could not be found" right after an install. Onedir installs the files once
# (Inno packages the whole folder), so there's no extraction race and startup is
# faster. The installer experience is identical.

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
    exclude_binaries=True,        # onedir: binaries go into the COLLECT folder
    name='ClaudeUsageMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='usage_monitor.ico',
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
