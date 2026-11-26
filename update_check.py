# update_check.py - GitHub-release update check + verified silent self-update.
#
# Shared across the SE tool suite. Pure stdlib, no external deps. Every network
# path of the CHECK fails silent: offline, rate-limited, or a bad response just
# means "no update" - never an error dialog, never a hang. The check runs on a
# daemon thread so the UI never blocks on it. The app runs it at start and again
# every six hours (app.UPDATE_RECHECK_S): one small GET each time, so a failed
# run costs nothing and is simply tried again later.
#
# Self-update works because the installers are per-user (PrivilegesRequired=
# lowest, installing under %localappdata%): a new installer overwrites in place
# with NO UAC prompt. We download it, verify it, then start a tiny hidden helper
# that waits for us to exit, runs the installer /VERYSILENT, and relaunches the app.
#
# What the self-update will and will not run (the installer runs unattended as
# the user, so this is the part that has to be right):
#   1. The installer URL must be https://github.com/<owner>/<repo>/releases/
#      download/<tag>/<name>.exe of THIS repo. Anything else is refused and the
#      release page is opened instead.
#   2. The download is hashed while it is written and compared with the "digest"
#      ("sha256:<hex>") that the GitHub releases API reports for that asset. A
#      mismatch deletes the file and opens the release page. A release whose
#      asset has no digest is never run silently either: the release page opens.
#      This shows that the file is the one GitHub lists for the release, not who
#      published it: the installer is not code-signed (docs/RELEASING.md).
#   3. Redirects must stay on https (GitHub serves the file from a CDN host).
#
# The app exits only once the helper is running, and the check is made twice: when
# the helper starts, and again right before the exit (shutting the windows down takes
# seconds, long enough for the helper to be killed). If it is gone by then, the release
# page opens and a restart helper brings the app back instead of quitting for nothing.
#
# The helper is a constant ASCII batch file. Nothing that varies (the installer's
# name, the exe to relaunch, our pid) is written into it: it reads them from
# environment variables, so no path can break the script or be mis-decoded by cmd,
# whatever characters the user's profile folder holds. cmd is started with
# CREATE_NO_WINDOW, not DETACHED_PROCESS: a detached cmd has no console, so every
# console program it starts opens a visible Windows Terminal window, and a pipe
# in the script hangs it.
#
# While an update runs, PROGRESS records how far it got (the phase, the bytes, why it
# failed) for the dashboard: see "What the dashboard sees of an update" below. That
# record is this copy's own (#58); the suite's other copies do not have it.

import errno
import hashlib
import hmac
import http.client
import json
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

# Under the app's logger namespace, so the lines reach monitor.log.
log = logging.getLogger("usage_monitor.update_check")

# What the helper passes to the installer. /MERGETASKS="!startup" keeps an update away from the user's
# "Start with Windows" entry (installer.iss task "startup"): the app repairs a stale path itself at launch.
SILENT_ARGS = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /MERGETASKS="!startup"'
MAX_INSTALLER_BYTES = 512 * 1024 * 1024                    # far above any real installer; stops a runaway download
MAX_JSON_BYTES = 4 * 1024 * 1024
HELPER_GRACE_S = 0.6                                       # the helper must still be running this long after it starts
HELPER_NAME = "update.cmd"
WORK_PREFIX = "usage-monitor-update-"

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

_REPO_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)$")
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")
_ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.exe$", re.IGNORECASE)
_DIGEST_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")

_update_lock = threading.Lock()   # one update at a time: a second click must not start a second helper


class UpdateError(Exception):
    """The update could not be applied. The app keeps running."""


class UpdateRejected(UpdateError):
    """The installer link is not this project's own release download."""


class UpdateVerificationError(UpdateError):
    """The installer could not be verified: its SHA-256 differs from the release's, or the release has none."""


# --- What the dashboard sees of an update (#58) ------------------------------
#
# One update runs per process (_update_lock), so one record, PROGRESS, says how it
# goes: the phase, the bytes written so far and the size the server announced, and
# for a failure its code and the phase it failed in. apply_update's worker writes
# it (begin once it holds the lock, fail in its except), _download reports every
# block it writes, _stage_update and _apply mark the phases. status() returns a
# copy; the app's local API serves it as GET /api/update/status. The record holds
# ids, never words: the page words them. Calling _download or _stage_update
# directly writes nothing here. Nothing in it changes what the update runs or when.

class UpdateTooLarge(UpdateError):
    """The installer is over the size cap, as announced or as counted."""


class UpdateCutShort(UpdateError):
    """The download ended before the Content-Length the server announced."""


class UpdateDigestMismatch(UpdateVerificationError):
    """The downloaded installer's SHA-256 differs from the one the release lists."""


class UpdateHelperFailed(UpdateError):
    """The update helper could not be started, or stopped at once."""


class UpdateRedirectRefused(urllib.error.HTTPError):
    """A redirect to a plain-http address, refused. Still an HTTPError, as it always was."""


STALL_S = 15                # no new data for this long while bytes are missing: the record says "stalled"
DOWNLOAD_TIMEOUT_S = 60     # the socket timeout of an update's requests: a receive that waits this long fails
PHASES = ("idle", "downloading", "checking", "starting", "restarting", "failed")
ACTIVE_PHASES = ("downloading", "checking", "starting", "restarting")
REASONS = ("timeout", "network", "http", "redirect", "too_large", "cut_short", "digest", "rejected", "helper", "local",
           "error")
# A socket error that names the network, raised as a plain OSError (not a ConnectionError) by a read of the body.
_NETWORK_ERRNOS = frozenset(getattr(errno, name) for name in (
    "ENETDOWN", "ENETUNREACH", "ENETRESET", "EHOSTDOWN", "EHOSTUNREACH", "ECONNABORTED", "ECONNRESET", "ECONNREFUSED")
    if hasattr(errno, name))


def failure_code(exc):
    """The REASONS code of an update that failed with ``exc``.

    network is the connection: a URLError that is neither an HTTP answer nor a timeout, a ConnectionError, a TLS
    error, a failed name lookup, a socket error naming the network. Any other OSError is local (a full disk, a file
    held by a scanner, a temp folder or helper file that cannot be written)."""
    if isinstance(exc, UpdateTooLarge):
        return "too_large"
    if isinstance(exc, (UpdateCutShort, http.client.IncompleteRead)):
        return "cut_short"
    if isinstance(exc, UpdateDigestMismatch):
        return "digest"
    if isinstance(exc, UpdateRejected):
        return "rejected"
    if isinstance(exc, UpdateHelperFailed):
        return "helper"
    if isinstance(exc, UpdateRedirectRefused):
        return "redirect"
    if isinstance(exc, urllib.error.HTTPError):
        return "http"
    if isinstance(exc, TimeoutError) or (isinstance(exc, urllib.error.URLError)
                                         and isinstance(exc.reason, TimeoutError)):
        return "timeout"
    if isinstance(exc, (urllib.error.URLError, ConnectionError, ssl.SSLError, socket.gaierror)):
        return "network"
    if isinstance(exc, OSError):
        return "network" if exc.errno in _NETWORK_ERRNOS else "local"
    return "error"


def _count(value):
    """A byte count: a non-negative int, else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class UpdateProgress:
    """The record of this process's update. Thread-safe; no method raises. ``clock`` is monotonic seconds."""

    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._phase, self._version = "idle", None
        self._done, self._total = 0, None
        self._reason, self._failed_in, self._http_status = None, None, None
        self._started = self._changed = None

    def begin(self, version):
        """A new run of the release ``version`` (None when unknown): downloading, nothing received, size unknown."""
        with self._lock:
            now = self._clock()
            self._phase, self._version = "downloading", version if isinstance(version, str) else None
            self._done, self._total = 0, None
            self._reason, self._failed_in, self._http_status = None, None, None
            self._started = self._changed = now

    def received(self, done, total):
        """``done`` bytes written of ``total`` (None: the server did not say). Ignored outside the download. Only new
        bytes count as a change (the stall clock): the headers alone, with their total, do not."""
        with self._lock:
            done, total = _count(done) or 0, _count(total)
            if self._phase == "downloading":
                if done != self._done:
                    self._changed = self._clock()
                self._done, self._total = done, total

    def phase(self, name):
        """The run moved on to ``name``: checking, starting or restarting. Ignored when no run is going."""
        with self._lock:
            if name in ACTIVE_PHASES[1:] and self._phase in ACTIVE_PHASES:
                self._phase = name
                self._changed = self._clock()

    def fail(self, exc):
        """The run failed with ``exc``: its code (failure_code), its HTTP status if any, the phase it failed in."""
        with self._lock:
            self._failed_in = self._phase if self._phase in ACTIVE_PHASES else None
            self._phase, self._reason = "failed", failure_code(exc)
            code = getattr(exc, "code", None) if isinstance(exc, urllib.error.HTTPError) else None
            self._http_status = code if isinstance(code, int) and 100 <= code <= 599 else None
            self._changed = self._clock()

    def snapshot(self):
        """The record as a dict (what GET /api/update/status answers, once the server has checked each field).

        age_s counts from the start of the run and idle_s from its last change, new bytes or a new phase (0 with no
        run). stalled: downloading, bytes still missing (the size is unknown or not reached) and no new bytes for
        STALL_S seconds, counted from the start of the run until the first ones arrive."""
        with self._lock:
            now = self._clock()
            age = now - self._started if self._started is not None else 0.0
            idle = now - self._changed if self._changed is not None else 0.0
            missing = self._total is None or self._done < self._total
            return {
                "phase": self._phase, "version": self._version, "done": self._done, "total": self._total,
                "age_s": round(max(0.0, age), 1), "idle_s": round(max(0.0, idle), 1),
                "stalled": self._phase == "downloading" and missing and idle >= STALL_S,
                "reason": self._reason, "failed_in": self._failed_in, "http_status": self._http_status,
                "limit_mb": MAX_INSTALLER_BYTES // (1024 * 1024), "timeout_s": DOWNLOAD_TIMEOUT_S,
            }


PROGRESS = UpdateProgress()


def status():
    """The record of this process's update: PROGRESS.snapshot(), PROGRESS looked up at the call."""
    return PROGRESS.snapshot()


def _ver_tuple(s):
    """'v1.2.0' / '1.2.0-beta.3' -> (1, 2, 0).  () if unparseable."""
    m = re.search(r"(\d+(?:\.\d+)*)", s or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def is_newer(version, than) -> bool:
    """True when version ``version`` is strictly newer than ``than`` ('2.1.0' over '2.0.9', '3.0' over '2.9.9').

    The same comparison _check makes against the running version. Text that holds no number ('', 'nightly') is
    older than every real version, and a value that is not text at all is read as empty (a settings file that
    was edited by hand can hold anything)."""
    return _ver_tuple(version if isinstance(version, str) else "") > _ver_tuple(than if isinstance(than, str) else "")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


# --- Addresses and digests ---------------------------------------------------

def _split_repo(repo):
    """'owner/name' -> (owner, name). ValueError for anything that is not a plain GitHub slug."""
    m = _REPO_RE.match(repo) if isinstance(repo, str) else None
    if not m:
        raise ValueError("not an owner/name repository: %r" % (repo,))
    return m.group(1), m.group(2)


def _default_repo():
    """The repository this build belongs to: REPO from the app's own version.py."""
    try:
        import version
        repo = getattr(version, "REPO", None)
    except Exception:
        return None
    return repo if isinstance(repo, str) else None


def _asset_parts(url, repo):
    """(tag, file name) when ``url`` is an installer download of ``repo``'s own releases, else None.

    Accepted: https://github.com/<owner>/<repo>/releases/download/<tag>/<name>.exe - exactly that
    shape, host github.com only, no credentials, port, query or fragment, no percent-escapes, owner
    and repo as given (GitHub ignores their case)."""
    try:
        owner, name = _split_repo(repo)
    except ValueError:
        return None
    if not isinstance(url, str) or len(url) > 1024 or not url.isascii() or not url.isprintable() or " " in url:
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme != "https" or parts.hostname != "github.com" or parts.username is not None
            or parts.password is not None or port not in (None, 443) or parts.query or parts.fragment):
        return None
    segments = parts.path.split("/")
    if len(segments) != 7 or segments[0] != "":
        return None
    _, seg_owner, seg_repo, releases, download, tag, asset = segments
    if (seg_owner.lower() != owner.lower() or seg_repo.lower() != name.lower()
            or releases != "releases" or download != "download"):
        return None
    if not _TAG_RE.match(tag) or not _ASSET_RE.match(asset):
        return None
    return tag, asset


def _release_page(repo, tag=None):
    """The release page to open when the silent update cannot go ahead."""
    try:
        owner, name = _split_repo(repo)
    except ValueError:
        return "https://github.com/"
    base = "https://github.com/%s/%s/releases/" % (owner, name)
    return base + ("tag/" + tag if tag and _TAG_RE.match(tag) else "latest")


def _digest_hex(value):
    """'sha256:<64 hex>' -> the lowercase hex, else None (missing, null or another algorithm)."""
    m = _DIGEST_RE.match(value) if isinstance(value, str) else None
    return m.group(1).lower() if m else None


def _open_url(url):
    """Open a page in the default browser (a separate function so tests can replace it)."""
    try:
        webbrowser.open(url)
    except Exception:
        log.exception("could not open %s", url)


# --- HTTP --------------------------------------------------------------------

class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects (GitHub sends downloads to a CDN host) but never to plain http."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise UpdateRedirectRefused(req.full_url, code, "refused a redirect to a non-https address", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnlyRedirects)


def _urlopen(req, timeout):
    """urlopen with the https-only redirect rule (a separate function so tests can replace it)."""
    return _opener.open(req, timeout=timeout)


def _headers(repo, accept):
    return {"User-Agent": "%s-update-check" % _split_repo(repo)[1], "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28"}


def _get_json(url, repo, timeout):
    req = urllib.request.Request(url, headers=_headers(repo, "application/vnd.github+json"))
    with _urlopen(req, timeout) as resp:
        raw = resp.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("the release answer is too large")
    return json.loads(raw.decode("utf-8"))


# --- The check ---------------------------------------------------------------

def check_async(repo, current_version, on_result, *, timeout=6, on_error=None):
    """Look up the latest GitHub release on a daemon thread.

    Calls on_result(info) exactly once: info is a dict when a strictly newer
    release exists, else None (also None on any error). on_result runs on the
    worker thread - marshal to your UI thread inside it.

    A caller that must tell "nothing newer" from "could not look" passes
    on_error: when the lookup fails (offline, DNS, rate limited, GitHub down,
    a bad repo name) it is called with the exception, on the worker thread and
    before on_result(None), which still follows. It is never called for a lookup
    that got an answer, newer or not. An exception it raises is ignored.

    info: version, tag, url (release page), notes, and for the installer asset
    asset_url (None when there is no usable one), asset_name, asset_digest
    ("sha256:<hex>" or None) and asset_size.
    """
    def worker():
        try:
            result = _check(repo, current_version, timeout)
        except Exception as exc:
            # Silent for the user: no dialog, nothing in the log at its default level. The app looks again every six
            # hours, so a lookup that fails (offline, rate limited, GitHub down) is simply tried again later.
            log.debug("update check failed: %s", type(exc).__name__)
            result = None
            if on_error is not None:
                try:
                    on_error(exc)
                except Exception:
                    pass
        try:
            on_result(result)
        except Exception:
            pass

    threading.Thread(target=worker, name="update-check", daemon=True).start()


def _check(repo, current_version, timeout):
    owner, name = _split_repo(repo)
    data = _get_json("https://api.github.com/repos/%s/%s/releases/latest" % (owner, name), repo, timeout)
    if not isinstance(data, dict):
        return None

    tag = data.get("tag_name") or ""
    if not isinstance(tag, str) or not _ver_tuple(tag) or _ver_tuple(tag) <= _ver_tuple(current_version):
        return None  # up to date (or a malformed/older tag)

    # The first .exe asset whose link is this project's own release download.
    asset = None
    for candidate in data.get("assets") or []:
        if not isinstance(candidate, dict) or not str(candidate.get("name") or "").lower().endswith(".exe"):
            continue
        if _asset_parts(candidate.get("browser_download_url"), repo):
            asset = candidate
            break
    size = asset.get("size") if asset else None

    page = data.get("html_url")
    return {
        "version":      tag.lstrip("v"),
        "tag":          tag,
        "url":          page if isinstance(page, str) and page.startswith("https://github.com/") else _release_page(repo),
        "asset_url":    asset.get("browser_download_url") if asset else None,
        "asset_name":   asset.get("name") if asset else None,
        "asset_digest": ("sha256:" + _digest_hex(asset.get("digest"))) if asset and _digest_hex(asset.get("digest")) else None,
        "asset_size":   size if isinstance(size, int) and not isinstance(size, bool) else None,
        "notes":        data.get("body") or "",
    }


def can_self_update(info) -> bool:
    """True when a one-click silent update is possible: we're a frozen build and
    the release ships a downloadable .exe installer of this repository's own releases
    (the link policy at the top of this file) with a SHA-256 digest to check it against.
    From source, or for a release without a digest or with a link that is not ours,
    there's nothing that can be applied safely - the caller should open the release
    page instead."""
    return bool(is_frozen() and info and _asset_parts(info.get("asset_url"), _default_repo())
                and _digest_hex(info.get("asset_digest")))


# --- The update --------------------------------------------------------------

def apply_update(asset_url, *, on_error=None, on_before_exit=None, repo=None, digest=None):
    """Download the installer, verify it, then silently apply it and relaunch -
    this process exits on success. The work runs on a daemon thread so the UI
    stays responsive; on failure, on_error(exc) is called and the app keeps
    running. Only meaningful in a frozen build (see can_self_update).

    The installer is checked against the digest the GitHub releases API lists for
    the asset (``digest`` "sha256:<hex>" when the caller already has it from the
    check, else it is looked up here). ``repo`` defaults to version.REPO. When the
    link, the digest or the file is not acceptable, nothing is run: the release
    page opens in the browser and on_error receives an UpdateError.

    PROGRESS records the run once this request holds the lock, so a second request, which is
    ignored, never touches the record of the one that is running."""
    def worker():
        if not _update_lock.acquire(blocking=False):
            log.info("an update is already in progress; ignoring the second request")
            return
        progress = PROGRESS
        try:
            progress.begin(_release_version(asset_url, repo))
            _apply(asset_url, repo=repo, digest=digest, on_before_exit=on_before_exit, progress=progress)
        except Exception as exc:
            progress.fail(exc)
            log.warning("the update was not applied: %s", exc)
            if on_error:
                try:
                    on_error(exc)
                except Exception:
                    pass
        finally:
            _update_lock.release()   # reached only when we are still running, i.e. it failed

    threading.Thread(target=worker, name="self-update", daemon=True).start()


def _release_version(asset_url, repo):
    """The version ``asset_url`` installs, as _check reports it (the tag without its "v"); None for a link not ours."""
    parts = _asset_parts(asset_url, repo or _default_repo())
    return parts[0].lstrip("v") if parts else None


def _apply(asset_url, *, repo, digest, on_before_exit, progress=None):
    staged = {}
    proc = _stage_update(asset_url, repo=repo, digest=digest, staged=staged, progress=progress)
    # The helper is running and waiting for us, so it is safe to go.
    if progress is not None:
        progress.phase("restarting")
    if on_before_exit:
        try:
            on_before_exit()
        except Exception:
            log.exception("the pre-exit hook failed; leaving anyway, the helper is waiting")
    waiting = _helper_is_waiting(proc, staged)
    log.info("update staged (helper pid %s); exiting for the %s", getattr(proc, "pid", "?"),
             "installer" if waiting else "restart")
    _exit_process()


def _helper_is_waiting(proc, staged):
    """True when the update helper is still running just before the app exits.

    _spawn_installer looked 0.6 s after the start, but the pre-exit hook (windows, tray icon, data
    service) takes seconds, and a security product that kills scripts started from %TEMP%, a job
    object or a user can end the hidden cmd.exe in that time. Exiting then would leave nothing
    running and nothing updated. So the helper is looked at again here, right before the exit. When it
    is gone the release page opens, so the user can still update by hand, and a restart helper (the
    same script without the installer step) starts the app again once this process has exited. The
    app is not left running: its windows are already closed and its single-instance mutex is held
    until it exits. Returns False after that, or when there was nothing to start the app from."""
    code = proc.poll()
    if code is None:
        return True
    log.warning("the update helper stopped (exit code %s) while the app was closing; opening the release page "
                "and starting the app again instead of quitting for nothing", code)
    if staged.get("page"):
        _open_url(staged["page"])
    installer = staged.get("installer")
    if installer:
        try:
            _spawn_installer(installer, install=False)
        except (UpdateError, OSError) as exc:
            log.warning("the app could not be started again either: %s", exc)
            shutil.rmtree(os.path.dirname(installer), ignore_errors=True)
    return False


def _exit_process():
    os._exit(0)  # hard exit so the running .exe unlocks for overwrite


def _stage_update(asset_url, *, repo=None, digest=None, timeout=DOWNLOAD_TIMEOUT_S, staged=None, progress=None):
    """Check the link and the digest, download and verify the installer, start the helper.

    Returns the running helper process. Raises UpdateRejected (link), UpdateVerificationError
    (digest) or another UpdateError / OSError; the release page has been opened for the first
    two, and nothing is left on disk after any of them. ``staged``, when given, is a dict that
    receives "page" (the release page) and "installer" (the downloaded file, which sits in a
    folder of its own) so the caller can recover if the helper dies before the app exits.
    ``progress``, when given, is an UpdateProgress that hears the bytes of the download and is
    moved on to "checking" when the file is in and to "starting" once it checked out."""
    if not is_frozen():
        raise UpdateError("self-update runs only in the installed build")
    repo = repo or _default_repo()
    if not repo:
        raise UpdateError("the repository to update from is unknown")

    parts = _asset_parts(asset_url, repo)
    if parts is None:
        _open_url(_release_page(repo))
        raise UpdateRejected("the installer link is not a release download of %s" % repo)
    tag, name = parts
    page = _release_page(repo, tag)
    if staged is not None:
        staged["page"] = page

    expected = _digest_hex(digest) or _lookup_digest(repo, tag, name, page, timeout)

    work = tempfile.mkdtemp(prefix=WORK_PREFIX, dir=tempfile.gettempdir())
    try:
        installer = os.path.join(work, name)
        actual = _download(asset_url, installer, repo=repo, timeout=timeout,
                           on_progress=progress.received if progress is not None else None)
        if progress is not None:
            progress.phase("checking")
        if not hmac.compare_digest(actual, expected):
            log.warning("update refused: %s hashes to %s, the release lists %s", name, actual, expected)
            _open_url(page)
            raise UpdateDigestMismatch("the downloaded installer does not match the release's SHA-256")
        log.info("update verified: %s sha256 %s", name, actual)
        if staged is not None:
            staged["installer"] = installer
        if progress is not None:
            progress.phase("starting")
        return _spawn_installer(installer)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _lookup_digest(repo, tag, name, page, timeout):
    """The lowercase SHA-256 the releases API lists for asset ``name`` of release ``tag``.

    Opens the release page and raises UpdateVerificationError when the release lists none."""
    owner, repo_name = _split_repo(repo)
    url = "https://api.github.com/repos/%s/%s/releases/tags/%s" % (owner, repo_name, urllib.parse.quote(tag, safe=""))
    data = _get_json(url, repo, timeout)
    asset = next((a for a in (data.get("assets") or []) if isinstance(a, dict) and a.get("name") == name), None)
    if asset is None:
        _open_url(page)
        raise UpdateVerificationError("release %s does not list %s" % (tag, name))
    found = _digest_hex(asset.get("digest"))
    if found is None:
        log.warning("update refused: release %s lists no SHA-256 for %s", tag, name)
        _open_url(page)
        raise UpdateVerificationError("release %s has no SHA-256 digest for %s" % (tag, name))
    return found


def _download(url, dst, *, repo=None, timeout=DOWNLOAD_TIMEOUT_S, max_bytes=MAX_INSTALLER_BYTES, on_progress=None):
    """Fetch ``url`` into ``dst`` and return the lowercase SHA-256 of what was written.

    Written to dst + '.part' and moved into place only when complete, so a cut-off download never
    looks like an installer. Raises UpdateError (UpdateTooLarge) for a body over ``max_bytes`` or
    (UpdateCutShort) one shorter than the Content-Length the server announced.

    ``on_progress(done, announced)``, when given, is called once the headers are in (done 0) and
    after every block written; ``announced`` is the Content-Length or None. A callback that raises
    is logged and the download goes on. Blocks are read as they arrive (read1, up to 64 KB), so a
    slow line still reports every few KB."""
    req = urllib.request.Request(url, headers=_headers(repo or "unknown/update", "application/octet-stream"))
    part = dst + ".part"
    digest = hashlib.sha256()
    total = 0

    def report(done):
        if on_progress is not None:
            try:
                on_progress(done, announced)
            except Exception:
                log.debug("the download's progress callback failed", exc_info=True)

    try:
        with _urlopen(req, timeout) as resp:
            announced = resp.headers.get("Content-Length")
            announced = int(announced) if announced and announced.isdigit() else None
            if announced is not None and announced > max_bytes:
                raise UpdateTooLarge("the installer is larger than %d MB" % (max_bytes // (1024 * 1024)))
            report(0)
            read = getattr(resp, "read1", None) or resp.read     # a test's fake response may have read only
            with open(part, "wb") as out:
                while True:
                    chunk = read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise UpdateTooLarge("the installer is larger than %d MB" % (max_bytes // (1024 * 1024)))
                    digest.update(chunk)
                    out.write(chunk)
                    report(total)
        if announced is not None and total != announced:
            raise UpdateCutShort("the download was cut short (%d of %d bytes)" % (total, announced))
        os.replace(part, dst)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return digest.hexdigest()


# --- The helper --------------------------------------------------------------

def _helper_script(install=True):
    """The batch file that waits for us to exit, runs the installer silently, relaunches the app
    and removes its own folder. ``install=False`` is the restart helper: the same file without the
    installer step, used when the update helper died while the app was closing (see
    _helper_is_waiting). It holds no path and no value that varies: it reads
    USAGE_MONITOR_UPDATE_PID (our pid), USAGE_MONITOR_UPDATE_SETUP (the installer's file name, beside the script),
    USAGE_MONITOR_UPDATE_EXE (what to relaunch) and USAGE_MONITOR_UPDATE_LOG from the environment, so the file is the
    same ASCII text for everybody. System tools are called by absolute path (a Git for Windows
    'find' ahead of System32 on PATH must not answer), there is no pipe in it (see the top of this
    file), and every echo puts its redirection first (a digit right before > would be read as a
    handle number). Split out so it's testable without executing anything."""
    ping = '"%SystemRoot%\\System32\\ping.exe"'
    lines = [
        "@echo off",
        "setlocal EnableExtensions DisableDelayedExpansion",
        'set "USAGE_MONITOR_N=0"',
        # The restart helper adds to the log of the update helper that stopped, so the story stays in one place.
        (">" if install else ">>") + '"%USAGE_MONITOR_UPDATE_LOG%" echo %DATE% %TIME% ' + ("update helper started" if install else "restart helper started, no update"),
        # Wait for the monitor to be gone (up to ~2.5 min) before the installer moves its files. Setup would close a
        # monitor that is still running (#53), but this one is already on its way out.
        ":wait",
        '"%SystemRoot%\\System32\\tasklist.exe" /FI "PID eq %USAGE_MONITOR_UPDATE_PID%" /NH >"%~dp0pid.txt" 2>nul',
        '"%SystemRoot%\\System32\\find.exe" " %USAGE_MONITOR_UPDATE_PID% " "%~dp0pid.txt" >nul 2>nul',
        "if %ERRORLEVEL% EQU 0 goto still",
        # find says 1 when the pid is no longer listed. Anything else means the check itself failed: give the app a few seconds.
        "if %ERRORLEVEL% GTR 1 " + ping + " -n 6 127.0.0.1 >nul",
        "goto go",
        ":still",
        "set /a USAGE_MONITOR_N+=1",
        "if %USAGE_MONITOR_N% GEQ 120 goto giveup",
        ping + " -n 2 127.0.0.1 >nul",
        "goto wait",
        ":go",
        ping + " -n 3 127.0.0.1 >nul",                                  # ~2 s: let the exe and its DLLs unlock
    ]
    if install:
        lines += [
            '"%~dp0%USAGE_MONITOR_UPDATE_SETUP%" ' + SILENT_ARGS + ' /LOG="%TEMP%\\ClaudeUsageMonitor-setup.log"',
            '>>"%USAGE_MONITOR_UPDATE_LOG%" echo %DATE% %TIME% installer exit code %ERRORLEVEL%',
            ping + " -n 8 127.0.0.1 >nul",                                  # ~7 s: let the install flush and Defender scan the new exe before we launch it
        ]
    lines += [
        'cd /d "%TEMP%"',                                                # the relaunched app must not hold this folder as its current directory
        'set "USAGE_MONITOR_N="',
        'set "USAGE_MONITOR_UPDATE_PID="',
        'set "USAGE_MONITOR_UPDATE_SETUP="',
        'if exist "%USAGE_MONITOR_UPDATE_EXE%" start "" "%USAGE_MONITOR_UPDATE_EXE%"',      # relaunch only once the exe is actually in place
        "goto cleanup",
        ":giveup",
        '>>"%USAGE_MONITOR_UPDATE_LOG%" echo %DATE% %TIME% the monitor never exited, so ' + ("the installer was not run" if install else "the app was not started again"),
        ":cleanup",
        'cd /d "%TEMP%"',
        'rmdir /s /q "%~dp0"',                                           # the installer, pid.txt and this script (must be last)
    ]
    return "\r\n".join(lines) + "\r\n"


def _helper_environment(installer_path, exe, pid):
    env = dict(os.environ)
    env.update({
        "USAGE_MONITOR_UPDATE_PID": str(pid),
        "USAGE_MONITOR_UPDATE_SETUP": os.path.basename(installer_path),
        "USAGE_MONITOR_UPDATE_EXE": exe,
        "USAGE_MONITOR_UPDATE_LOG": os.path.join(tempfile.gettempdir(), "ClaudeUsageMonitor-update.log"),
    })
    return env


def _helper_command(helper_path):
    """(command line, cwd) that run the helper script with cmd.exe.

    A folder on a drive is entered and the script is started as .\\update.cmd, so the command line
    holds no part of the path at all (an environment variable named in the folder, as in %PATH%,
    would otherwise be expanded inside it). A UNC folder cannot be cmd's current directory, so
    there the script is named in full: cmd /s /c ""<script>"" keeps the quotes around it, and & ^
    ( ) , ; = @ ! in the path stay inert."""
    comspec = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    if not os.path.isfile(comspec):
        comspec = "cmd.exe"
    folder, script = os.path.split(helper_path)
    prefix = '"%s" /d /e:on /v:off /s /c ' % comspec
    if folder.startswith("\\\\"):
        return prefix + '""%s""' % helper_path, None
    return prefix + ".\\" + script, folder or None


def _launch(command, cwd, env):
    """Start the helper hidden and detached from us (a separate function so tests can replace it)."""
    return subprocess.Popen(
        command, cwd=cwd, env=env, close_fds=True,
        creationflags=_CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _spawn_installer(installer_path, exe=None, pid=None, install=True):
    """Write the helper beside the installer and start it. Returns the running process.

    Raises UpdateError when it cannot be started or stops at once, so the caller can stay up
    instead of quitting for an update that will never happen. ``install=False`` starts the
    restart helper instead (it waits for us to exit, starts the app again and removes the folder)."""
    helper = os.path.join(os.path.dirname(installer_path), HELPER_NAME)
    with open(helper, "w", encoding="ascii", newline="") as f:
        f.write(_helper_script(install))
    env = _helper_environment(installer_path, exe or sys.executable, os.getpid() if pid is None else pid)
    command, cwd = _helper_command(helper)
    try:
        proc = _launch(command, cwd, env)
    except OSError as exc:
        raise UpdateHelperFailed("could not start the update helper: %s" % exc) from exc
    try:
        code = proc.wait(timeout=HELPER_GRACE_S)
    except subprocess.TimeoutExpired:
        return proc  # still running: it is waiting for us to exit
    raise UpdateHelperFailed("the update helper stopped at once (exit code %s)" % code)
