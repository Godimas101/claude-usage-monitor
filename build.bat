@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Builds the exe (PyInstaller, onedir) and the installer (Inno Setup) for the version in VERSION.
rem
rem   build.bat             build, then wait for a key
rem   build.bat nopause     build without waiting (scripts)
rem
rem Optional environment variables, each defaulting to the folder next to this file:
rem   CUM_DISTPATH   where PyInstaller writes the bundle        (default: dist)
rem   CUM_WORKPATH   PyInstaller's work folder                  (default: build)
rem   CUM_OUTDIR     where the installer is written             (default: installer)
rem Both tools stumble on paths over 260 characters: point these at a short folder if the checkout is deep.
rem
rem The build never runs the installer. Release builds come from .github/workflows/release.yml (docs/RELEASING.md).

set "DOPAUSE=pause"
if /i "%~1"=="nopause" set "DOPAUSE=rem"

if not defined CUM_DISTPATH set "CUM_DISTPATH=dist"
if not defined CUM_WORKPATH set "CUM_WORKPATH=build"
if not defined CUM_OUTDIR set "CUM_OUTDIR=installer"

if not exist VERSION (
    echo ERROR: the VERSION file is missing.
    %DOPAUSE%
    exit /b 1
)
set /p APPVER=<VERSION

echo ==========================================
echo  Claude Usage Monitor - Build %APPVER%
echo ==========================================

:: -- Tools ---------------------------------
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: PyInstaller was not found for "python".
    echo        Install the build requirements:  python -m pip install -r requirements.txt pyinstaller
    %DOPAUSE%
    exit /b 1
)

set "ISCC="
for %%p in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) do if exist %%p set "ISCC=%%~p"

if not defined ISCC (
    echo ERROR: Inno Setup 6 was not found.
    echo        Download from: https://jrsoftware.org/isdownload.php
    %DOPAUSE%
    exit /b 1
)

:: -- Toolchain -----------------------------
rem What built this. The release workflow does not pin its dependencies, so compare this line with the workflow's
rem last log (docs/RELEASING.md) before trusting a smoke run of a local build.
python -c "import sys, importlib.metadata as m; print('      Toolchain: Python', sys.version.split()[0], '| PyInstaller', m.version('pyinstaller'), '| Pillow', m.version('Pillow'), '| pywebview', m.version('pywebview'))" 2>nul || echo       The toolchain versions could not be read.

:: -- Step 1: PyInstaller -------------------
echo.
echo [1/3] Building the exe with PyInstaller...
python -m PyInstaller ClaudeUsageMonitor.spec --noconfirm --distpath "%CUM_DISTPATH%" --workpath "%CUM_WORKPATH%"
if errorlevel 1 (
    echo ERROR: PyInstaller failed.
    %DOPAUSE%
    exit /b 1
)
set "BUNDLE=%CUM_DISTPATH%\ClaudeUsageMonitor"

:: -- Step 2: check the bundle --------------
echo.
echo [2/3] Checking the bundle...
if not exist "%BUNDLE%\ClaudeUsageMonitor.exe" (
    echo ERROR: %BUNDLE%\ClaudeUsageMonitor.exe was not produced.
    %DOPAUSE%
    exit /b 1
)
rem The frozen exe must read the VERSION it was built with, or the updater would think it is always behind.
set "VERFILE=%CUM_WORKPATH%\built-version.txt"
if exist "%VERFILE%" del "%VERFILE%"
"%BUNDLE%\ClaudeUsageMonitor.exe" --print-version "%VERFILE%"
if errorlevel 1 (
    echo ERROR: the built exe could not report its version.
    %DOPAUSE%
    exit /b 1
)
set "BUILTVER="
if exist "%VERFILE%" set /p BUILTVER=<"%VERFILE%"
if not "%BUILTVER%"=="%APPVER%" (
    echo ERROR: the built exe reports version "%BUILTVER%", but VERSION says "%APPVER%".
    %DOPAUSE%
    exit /b 1
)
echo       The exe reports %BUILTVER%.

:: -- Step 3: Inno Setup --------------------
echo.
echo [3/3] Building the installer with Inno Setup...
"%ISCC%" /DAppVer=%APPVER% "/DDistDir=%BUNDLE%" "/O%CUM_OUTDIR%" installer.iss
if errorlevel 1 (
    echo ERROR: Inno Setup failed.
    %DOPAUSE%
    exit /b 1
)

set "SETUP=%CUM_OUTDIR%\ClaudeUsageMonitorSetup-v%APPVER%.exe"
echo.
echo ==========================================
echo  Build complete!
echo  Installer: %SETUP%
echo  GitHub lists the same SHA-256 as the asset digest after a release:
certutil -hashfile "%SETUP%" SHA256
echo ==========================================
%DOPAUSE%
exit /b 0
