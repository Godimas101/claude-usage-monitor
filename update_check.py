# update_check.py — GitHub-release update check + silent self-update.
#
# Shared across the SE tool suite. Pure stdlib, no external deps. Every network
# path FAILS SILENT: offline, rate-limited, or a bad response just means "no
# update" — never an error dialog, never a hang. The check runs on a daemon
# thread so the UI never blocks on it.
#
# Self-update works because the installers are per-user (PrivilegesRequired=
# lowest, installing under %localappdata%): a new installer overwrites in place
# with NO UAC prompt. We download it, then spawn a tiny detached helper that
# waits for us to exit, runs the installer /VERYSILENT, and relaunches the app.

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request


def _ver_tuple(s):
    """'v1.2.0' / '1.2.0-beta.3' -> (1, 2, 0).  () if unparseable."""
    m = re.search(r"(\d+(?:\.\d+)*)", s or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def check_async(repo, current_version, on_result, *, timeout=6):
    """Look up the latest GitHub release on a daemon thread.

    Calls on_result(info) exactly once: info is a dict when a strictly newer
    release exists, else None (also None on any error). on_result runs on the
    worker thread — marshal to your UI thread inside it.
    """
    def worker():
        try:
            result = _check(repo, current_version, timeout)
        except Exception:
            result = None
        try:
            on_result(result)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def _check(repo, current_version, timeout):
    url = "https://api.github.com/repos/%s/releases/latest" % repo
    req = urllib.request.Request(url, headers={
        "User-Agent": "%s-update-check" % repo.split("/")[-1],
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)

    tag = data.get("tag_name") or ""
    if not _ver_tuple(tag) or _ver_tuple(tag) <= _ver_tuple(current_version):
        return None  # up to date (or a malformed/older tag)

    asset_url = None
    for a in data.get("assets", []):
        if (a.get("name") or "").lower().endswith(".exe"):
            asset_url = a.get("browser_download_url")
            break

    return {
        "version":   tag.lstrip("v"),
        "tag":       tag,
        "url":       data.get("html_url") or ("https://github.com/%s/releases/latest" % repo),
        "asset_url": asset_url,
        "notes":     data.get("body") or "",
    }


def can_self_update(info) -> bool:
    """True when a one-click silent update is possible: we're a frozen build and
    the release ships a downloadable .exe installer. From source, there's
    nothing to overwrite — the caller should open the release page instead."""
    return bool(is_frozen() and info and info.get("asset_url"))


def apply_update(asset_url, *, on_error=None, on_before_exit=None):
    """Download the installer, then silently apply it and relaunch — this
    process exits on success. The download runs on a daemon thread so the UI
    stays responsive; on failure, on_error(exc) is called and the app keeps
    running. Only meaningful in a frozen build (see can_self_update)."""
    def worker():
        try:
            dst = os.path.join(tempfile.gettempdir(),
                               os.path.basename(asset_url) or "update.exe")
            _download(asset_url, dst)
            _spawn_installer(dst)
            if on_before_exit:
                try:
                    on_before_exit()
                except Exception:
                    pass
            os._exit(0)  # hard exit so the running .exe unlocks for overwrite
        except Exception as exc:
            if on_error:
                try:
                    on_error(exc)
                except Exception:
                    pass

    threading.Thread(target=worker, daemon=True).start()


def _download(url, dst):
    req = urllib.request.Request(url, headers={"User-Agent": "se-tool-update-check"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dst, "wb") as f:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)


def _helper_script(installer_path, exe):
    """Batch that waits for us to exit, runs the installer silently, relaunches
    the app, and deletes the installer + itself. Split out so it's testable
    without executing anything."""
    lines = [
        "@echo off",
        "ping 127.0.0.1 -n 4 >nul",                                     # ~3s: wait for the old app to exit + unlock the exe
        '"%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART' % installer_path,
        "ping 127.0.0.1 -n 8 >nul",                                     # ~7s: let the silent install flush + Defender scan the fresh exe before we launch it
        'del "%s" >nul 2>&1' % installer_path,
        'if exist "%s" start "" "%s"' % (exe, exe),                     # relaunch only once the new exe is actually in place
        'del "%~f0" >nul 2>&1',                                         # self-delete (must be last)
    ]
    return "\r\n".join(lines) + "\r\n"


def _spawn_installer(installer_path):
    """Write and launch a detached helper that applies the update after we exit."""
    exe = sys.executable  # installed .exe path; the installer overwrites it in place
    helper = installer_path + ".run.cmd"
    # cmd reads .cmd in the system ANSI codepage — write it that way so non-ASCII
    # install paths survive.
    with open(helper, "w", encoding="mbcs", errors="replace") as f:
        f.write(_helper_script(installer_path, exe))

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        ["cmd", "/c", helper],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
