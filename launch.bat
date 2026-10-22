@echo off
rem Run Claude Usage Monitor from source. Extra arguments pass through to main.py.
rem Dev instance beside an installed copy: set CUM_DEV_INSTANCE=1 and CUM_APPDATA_DIR=DEVFOLDER first.
rem To see errors and the log in a console, run "python main.py" instead (pythonw has no console).
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
    echo pythonw.exe was not found on PATH. Install Python 3.10 or newer from python.org.
    pause
    exit /b 1
)
pythonw main.py %*
