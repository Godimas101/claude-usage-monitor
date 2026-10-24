"""Tests for update_check.py and for the release plumbing that has to agree with it.

update_check.py checks GitHub for a newer release and, in the installed build, runs the new installer
unattended. What it will run, and how it starts the helper that runs it, is what these tests pin:

  * the check: which release and which installer asset it reports, and the asset's SHA-256 digest;
  * the link policy: only this repository's own release downloads on github.com;
  * verification: a digest that matches goes ahead, a mismatch or a missing digest never runs and opens
    the release page, and the app exits only once the helper is running;
  * the helper: a constant ASCII batch file that holds no path, started so that no character in the user's
    temp folder (& ^ % ( ) , ; = @ !, spaces, accents) can break it. The real cmd.exe tests start processes,
    so they need CUM_UI_TESTS=1 like the other process tests. None of them may open a console window on the
    desktop: the app the helper relaunches is a windowed stand-in (StandInApp), every console program a test starts
    carries CREATE_NO_WINDOW, and the last section checks all the test files for a call that forgets it.

The last two sections check that installer.iss and ClaudeUsageMonitor.spec say what the app and the updater
rely on: the single-instance mutex, the Run entry, the file names, the bundled resources.

Nothing here touches the network: a fake opener stands in for it (one test uses a server on 127.0.0.1).
"""
from __future__ import annotations

import ast
import glob
import hashlib
import http.client
import http.server
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import update_check as uc
import version

UI = pytest.mark.skipif(
    os.environ.get("CUM_UI_TESTS") != "1", reason="starts processes: set CUM_UI_TESTS=1",
)
# Every console program a test starts (ping, cmd, ISCC, PowerShell, python) gets this flag. A program that is started
# by a process without a console of its own (pytest run from an editor, a task runner, an agent) otherwise opens a
# terminal window on the desktop, one per process. test_no_test_starts_a_console_program_without_the_flag keeps it so.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ROOT = Path(__file__).resolve().parent.parent
REPO = version.REPO
TAG = "v2.0.0"
SETUP = "ClaudeUsageMonitorSetup-v2.0.0.exe"
URL = f"https://github.com/{REPO}/releases/download/{TAG}/{SETUP}"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
API_TAG = f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}"
PAGE = f"https://github.com/{REPO}/releases/tag/{TAG}"
LATEST_PAGE = f"https://github.com/{REPO}/releases/latest"
PAYLOAD = b"MZ\x90\x00" + bytes(range(256)) * 300 + b"end of the fake installer"
SHA = hashlib.sha256(PAYLOAD).hexdigest()


# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakeResponse:
    """What urlopen returns: a context manager with headers and read(n)."""

    def __init__(self, body: bytes, *, announce: int | None = -1) -> None:
        self._body = io.BytesIO(body)
        self.headers: dict[str, str] = {}
        if announce == -1:
            announce = len(body)
        if announce is not None:
            self.headers["Content-Length"] = str(announce)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class FakeNet:
    """Replaces update_check._urlopen: serves registered bodies and records every request."""

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.requests: list[str] = []
        self.headers: list[dict[str, str]] = []

    def add_json(self, url: str, obj: Any) -> None:
        self.routes[url] = json.dumps(obj).encode("utf-8")

    def add_bytes(self, url: str, body: bytes, **kwargs: Any) -> None:
        self.routes[url] = (body, kwargs)

    def add_error(self, url: str, exc: Exception) -> None:
        self.routes[url] = exc

    def __call__(self, req: urllib.request.Request, timeout: float) -> FakeResponse:
        self.requests.append(req.full_url)
        self.headers.append({k.lower(): v for k, v in req.header_items()})
        route = self.routes.get(req.full_url)
        if route is None:
            raise urllib.error.URLError(f"no route for {req.full_url}")
        if isinstance(route, Exception):
            raise route
        if isinstance(route, tuple):
            body, kwargs = route
            return FakeResponse(body, **kwargs)
        return FakeResponse(route)


class FakeProc:
    """The helper process: running until told otherwise."""

    pid = 4242

    def __init__(self, exit_code: int | None = None) -> None:
        self.exit_code = exit_code

    def wait(self, timeout: float | None = None) -> int:
        if self.exit_code is None:
            raise subprocess.TimeoutExpired("helper", timeout or 0)
        return self.exit_code

    def poll(self) -> int | None:
        return self.exit_code

    def die(self, code: int = 1) -> None:
        """The helper is killed: by a security product, a job object or a user."""
        self.exit_code = code


def asset_json(name: str = SETUP, *, digest: Any = f"sha256:{SHA}", url: Any = URL, size: Any = len(PAYLOAD)) -> dict[str, Any]:
    return {"name": name, "browser_download_url": url, "digest": digest, "size": size,
            "state": "uploaded", "content_type": "application/x-msdownload"}


DEFAULT_ASSETS = object()


def release_json(tag: Any = TAG, assets: Any = DEFAULT_ASSETS, **extra: Any) -> dict[str, Any]:
    """A GitHub release answer. ``assets=None`` is a JSON null; leave it out for the one installer asset."""
    data = {"tag_name": tag, "html_url": f"https://github.com/{REPO}/releases/tag/{tag}", "body": "notes",
            "assets": [asset_json()] if assets is DEFAULT_ASSETS else assets}
    data.update(extra)
    return data


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> FakeNet:
    fake = FakeNet()
    monkeypatch.setattr(uc, "_urlopen", fake)
    return fake


# ── Version numbers ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "numbers"), [
    ("v2.0.0", (2, 0, 0)), ("1.2.0-beta.3", (1, 2, 0)), ("2.10", (2, 10)), ("", ()), (None, ()), ("nightly", ()),
])
def test_version_tuples(text: str | None, numbers: tuple[int, ...]) -> None:
    assert uc._ver_tuple(text) == numbers


@pytest.mark.parametrize(("candidate", "than", "expected"), [
    ("2.1.0", "2.0.9", True), ("2.0.9", "2.1.0", False), ("2.1.0", "2.1.0", False),      # strictly newer
    ("v3.0", "2.9.9", True), ("2.10.0", "2.9.0", True), ("2.9.0", "2.10.0", False),      # numbers, not text
    ("3.0.0-rc.1", "2.9.9", True), ("1.1.5", "2.0.0", False),
    ("2.1.0", "", True), ("2.1.0", "nightly", True), ("", "2.1.0", False), ("nightly", "2.1.0", False),
    ("2.1.0", None, True), (None, "2.1.0", False), (None, None, False), ("", "", False),
    ("2.1.0", 5, True), (5, "2.1.0", False), (["2.1.0"], "2.0.0", False),                  # a hand-edited settings file
])
def test_is_newer_compares_version_numbers(candidate: Any, than: Any, expected: bool) -> None:
    """What decides whether a release is news: the same comparison _check makes, safe for any stored value."""
    assert uc.is_newer(candidate, than) is expected


# ── The check ─────────────────────────────────────────────────────────────────

def test_a_newer_release_reports_the_installer_and_its_digest(net: FakeNet) -> None:
    net.add_json(API_LATEST, release_json())
    info = uc._check(REPO, "1.1.5", 6)
    assert info == {
        "version": "2.0.0", "tag": TAG, "url": PAGE, "notes": "notes",
        "asset_url": URL, "asset_name": SETUP, "asset_digest": f"sha256:{SHA}", "asset_size": len(PAYLOAD),
    }
    assert net.requests == [API_LATEST]
    assert net.headers[0]["user-agent"] and net.headers[0]["accept"] == "application/vnd.github+json"


@pytest.mark.parametrize("tag", ["v1.1.5", "v1.1.4", "v1.0", "nightly", "", None])
def test_nothing_newer_means_no_update(net: FakeNet, tag: str | None) -> None:
    net.add_json(API_LATEST, release_json(tag=tag))
    assert uc._check(REPO, "1.1.5", 6) is None


@pytest.mark.parametrize("body", [[], "text", 5, None])
def test_an_answer_that_is_not_a_release_means_no_update(net: FakeNet, body: Any) -> None:
    net.add_json(API_LATEST, body)
    assert uc._check(REPO, "1.1.5", 6) is None


@pytest.mark.parametrize(("listed", "expected"), [
    (f"sha256:{SHA}", f"sha256:{SHA}"),
    (f"sha256:{SHA.upper()}", f"sha256:{SHA}"),             # normalised, so comparisons are plain
    (None, None),                                            # an asset uploaded before GitHub listed digests
    ("", None),
    (f"sha512:{SHA}{SHA}", None),                            # another algorithm is not one we can check
    (f"sha256:{SHA[:-1]}", None),
    (f"sha256:{SHA}0", None),
    (f"sha256:{SHA[:-1]}g", None),
    (f"md5:{SHA[:32]}", None),
    (12345, None),
])
def test_the_digest_is_reported_only_when_it_is_a_sha256(net: FakeNet, listed: Any, expected: str | None) -> None:
    net.add_json(API_LATEST, release_json(assets=[asset_json(digest=listed)]))
    info = uc._check(REPO, "1.1.5", 6)
    assert info is not None and info["asset_digest"] == expected
    assert info["asset_url"] == URL  # the installer is still offered; whether it may run silently is can_self_update's call


def test_the_first_usable_exe_is_the_installer(net: FakeNet) -> None:
    other = f"https://github.com/{REPO}/releases/download/{TAG}/Other.exe"
    net.add_json(API_LATEST, release_json(assets=[
        "not an asset", None, 7,
        asset_json("notes.zip", url=f"https://github.com/{REPO}/releases/download/{TAG}/notes.zip"),
        asset_json("Evil.exe", url="https://evil.example/Setup.exe"),                      # a link off the project
        asset_json("Other.exe", url=other, digest=None),
        asset_json(),
    ]))
    info = uc._check(REPO, "1.1.5", 6)
    assert info is not None and info["asset_url"] == other and info["asset_name"] == "Other.exe"


@pytest.mark.parametrize("assets", [[], None, [asset_json("notes.zip", url=URL[:-4] + ".zip")],
                                    [asset_json(url="https://evil.example/Setup.exe")],
                                    [asset_json(url=f"https://github.com/someone/else/releases/download/{TAG}/{SETUP}")]])
def test_a_release_without_a_usable_installer_still_reports_the_version(net: FakeNet, assets: Any) -> None:
    net.add_json(API_LATEST, release_json(assets=assets))
    info = uc._check(REPO, "1.1.5", 6)
    assert info is not None and info["version"] == "2.0.0"
    assert info["asset_url"] is None and info["asset_name"] is None and info["asset_digest"] is None
    assert not uc.can_self_update(info)


@pytest.mark.parametrize("page", ["https://evil.example/x", "http://github.com/x", None, 5])
def test_the_release_page_is_always_a_github_page(net: FakeNet, page: Any) -> None:
    net.add_json(API_LATEST, release_json(html_url=page))
    info = uc._check(REPO, "1.1.5", 6)
    assert info is not None and info["url"] == LATEST_PAGE


def test_check_async_reports_once_on_its_own_thread(net: FakeNet) -> None:
    net.add_json(API_LATEST, release_json())
    got: list[Any] = []
    threads: list[str] = []
    done = threading.Event()

    def on_result(info: Any) -> None:
        got.append(info)
        threads.append(threading.current_thread().name)
        done.set()

    uc.check_async(REPO, "1.1.5", on_result)
    assert done.wait(5)
    time.sleep(0.05)
    assert len(got) == 1 and got[0]["version"] == "2.0.0"
    assert threads[0] != threading.main_thread().name


@pytest.mark.parametrize("failure", [urllib.error.URLError("offline"), TimeoutError(), ValueError("x"),
                                     urllib.error.HTTPError(API_LATEST, 403, "rate limited", {}, None)])
def test_check_async_fails_silent(net: FakeNet, failure: Exception) -> None:
    net.add_error(API_LATEST, failure)
    got: list[Any] = []
    done = threading.Event()
    uc.check_async(REPO, "1.1.5", lambda info: (got.append(info), done.set()))
    assert done.wait(5) and got == [None]


@pytest.mark.parametrize("failure", [urllib.error.URLError("offline"), TimeoutError(), ValueError("x"),
                                     urllib.error.HTTPError(API_LATEST, 403, "rate limited", {}, None),
                                     urllib.error.HTTPError(API_LATEST, 503, "unavailable", {}, None)])
def test_check_async_tells_a_caller_who_asks_that_the_lookup_failed(net: FakeNet, failure: Exception) -> None:
    """None means "nothing newer" and also "could not look". A caller that has to tell them apart (the dashboard's
    "Check for updates now" must not say "up to date" for a lookup that failed) passes on_error: it gets the exception
    first, then on_result(None) as ever."""
    net.add_error(API_LATEST, failure)
    events: list[tuple[str, Any]] = []
    done = threading.Event()
    uc.check_async(REPO, "1.1.5", lambda info: (events.append(("result", info)), done.set()),
                   on_error=lambda exc: events.append(("error", exc)))
    assert done.wait(5)
    assert events == [("error", failure), ("result", None)]


def test_check_async_with_a_bad_repo_name_is_a_failed_lookup_too() -> None:
    events: list[tuple[str, Any]] = []
    done = threading.Event()
    uc.check_async("not a repo; rm -rf", "1.1.5", lambda info: (events.append(("result", info)), done.set()),
                   on_error=lambda exc: events.append(("error", type(exc).__name__)))
    assert done.wait(5) and events == [("error", "ValueError"), ("result", None)]


@pytest.mark.parametrize("tag", ["v2.0.0", "v1.1.5", "v1.0"])
def test_check_async_does_not_call_on_error_for_a_lookup_that_got_an_answer(net: FakeNet, tag: str) -> None:
    """Newer, the same or older: GitHub answered, so the lookup is not a failure."""
    net.add_json(API_LATEST, release_json(tag=tag))
    events: list[str] = []
    done = threading.Event()
    uc.check_async(REPO, "1.1.5", lambda info: (events.append("result"), done.set()), on_error=lambda exc: events.append("error"))
    assert done.wait(5)
    time.sleep(0.05)
    assert events == ["result"]


def test_check_async_survives_an_on_error_that_raises(net: FakeNet) -> None:
    net.add_error(API_LATEST, urllib.error.URLError("offline"))
    got: list[Any] = []
    done = threading.Event()

    def boom(_exc: Exception) -> None:
        raise RuntimeError("callback bug")

    uc.check_async(REPO, "1.1.5", lambda info: (got.append(info), done.set()), on_error=boom)
    assert done.wait(5) and got == [None]


def test_a_failed_check_is_a_debug_line_and_nothing_louder(net: FakeNet, caplog: pytest.LogCaptureFixture) -> None:
    """The app checks every six hours, so a failed lookup is not worth a warning: monitor.log (INFO and up) stays
    quiet, and a developer who turns debug on can still see why no update was found. The line names the kind of
    failure, never the address or the answer."""
    net.add_error(API_LATEST, urllib.error.URLError("offline"))
    done = threading.Event()
    with caplog.at_level("DEBUG", logger="usage_monitor.update_check"):
        uc.check_async(REPO, "1.1.5", lambda info: done.set())
        assert done.wait(5)
    records = [r for r in caplog.records if r.name == "usage_monitor.update_check"]
    assert [r.levelname for r in records] == ["DEBUG"]
    assert "URLError" in records[0].getMessage() and "github" not in records[0].getMessage()


def test_the_check_runs_on_a_thread_of_its_own_named_for_it(net: FakeNet) -> None:
    net.add_json(API_LATEST, release_json())
    names: list[str] = []
    done = threading.Event()
    uc.check_async(REPO, "1.1.5", lambda info: (names.append(threading.current_thread().name), done.set()))
    assert done.wait(5) and names == ["update-check"]


def test_check_async_survives_a_callback_that_raises(net: FakeNet) -> None:
    net.add_json(API_LATEST, release_json())
    called = threading.Event()

    def boom(_info: Any) -> None:
        called.set()
        raise RuntimeError("callback bug")

    uc.check_async(REPO, "1.1.5", boom)
    assert called.wait(5)


def test_check_async_with_a_bad_repo_name_gives_none() -> None:
    got: list[Any] = []
    done = threading.Event()
    uc.check_async("not a repo; rm -rf", "1.1.5", lambda info: (got.append(info), done.set()))
    assert done.wait(5) and got == [None]


def test_the_answer_is_read_with_a_size_cap(net: FakeNet) -> None:
    net.routes[API_LATEST] = b"[" + b" " * (uc.MAX_JSON_BYTES + 10) + b"]"
    with pytest.raises(ValueError, match="too large"):
        uc._check(REPO, "1.1.5", 6)


@pytest.mark.parametrize(("frozen", "info", "expected"), [
    (True, {"asset_url": URL, "asset_digest": f"sha256:{SHA}"}, True),
    (False, {"asset_url": URL, "asset_digest": f"sha256:{SHA}"}, False),     # from source there is nothing to overwrite
    (True, {"asset_url": URL, "asset_digest": None}, False),                  # no digest, no silent run
    (True, {"asset_url": URL}, False),
    (True, {"asset_url": URL, "asset_digest": "sha256:abc"}, False),
    (True, {"asset_url": "https://evil.example/Setup.exe", "asset_digest": f"sha256:{SHA}"}, False),   # a link that is not ours
    (True, {"asset_url": f"https://github.com/someone/else/releases/download/{TAG}/{SETUP}", "asset_digest": f"sha256:{SHA}"}, False),
    (True, {"asset_url": None, "asset_digest": f"sha256:{SHA}"}, False),
    (True, {}, False),
    (True, None, False),
])
def test_can_self_update(monkeypatch: pytest.MonkeyPatch, frozen: bool, info: Any, expected: bool) -> None:
    monkeypatch.setattr(uc, "is_frozen", lambda: frozen)
    assert uc.can_self_update(info) is expected


# ── The link policy ───────────────────────────────────────────────────────────

OWNER, NAME = REPO.split("/")
GOOD = f"https://github.com/{REPO}/releases/download/{TAG}/{SETUP}"


@pytest.mark.parametrize("url", [
    GOOD,
    f"https://GitHub.com/{REPO.upper()}/releases/download/{TAG}/{SETUP}",     # GitHub ignores case in owner and repo
    f"https://github.com:443/{REPO}/releases/download/{TAG}/{SETUP}",
    f"https://github.com/{REPO}/releases/download/v2.0.0+build.5/{SETUP}",
    f"https://github.com/{REPO}/releases/download/{TAG}/SETUP.EXE",
])
def test_this_repositorys_release_downloads_are_accepted(url: str) -> None:
    assert uc._asset_parts(url, REPO) is not None


def test_the_parts_are_the_tag_and_the_file_name() -> None:
    assert uc._asset_parts(GOOD, REPO) == (TAG, SETUP)


@pytest.mark.parametrize("url", [
    f"http://github.com/{REPO}/releases/download/{TAG}/{SETUP}",                          # not https
    f"https://github.com.evil.example/{REPO}/releases/download/{TAG}/{SETUP}",
    f"https://evil.example/{REPO}/releases/download/{TAG}/{SETUP}",
    f"https://github.com@evil.example/{REPO}/releases/download/{TAG}/{SETUP}",
    f"https://user:pw@github.com/{REPO}/releases/download/{TAG}/{SETUP}",
    f"https://github.com./{REPO}/releases/download/{TAG}/{SETUP}",
    f"https://api.github.com/{REPO}/releases/download/{TAG}/{SETUP}",
    f"https://raw.githubusercontent.com/{REPO}/releases/download/{TAG}/{SETUP}",
    f"https://github.com:8443/{REPO}/releases/download/{TAG}/{SETUP}",
    f"https://github.com/{REPO}/releases/download/{TAG}/{SETUP}?token=1",
    f"https://github.com/{REPO}/releases/download/{TAG}/{SETUP}#frag",
    f"https://github.com/someone-else/{NAME}/releases/download/{TAG}/{SETUP}",            # another owner
    f"https://github.com/{OWNER}/another-repo/releases/download/{TAG}/{SETUP}",           # another repo of the same owner
    f"https://github.com/{REPO}/archive/main.zip",
    f"https://github.com/{REPO}/releases/tag/{TAG}",
    f"https://github.com/{REPO}/releases/download/{SETUP}",
    f"https://github.com/{REPO}/releases/download/{TAG}/extra/{SETUP}",
    f"https://github.com/{REPO}/releases/download/{TAG}/notes.zip",                       # not an installer
    f"https://github.com/{REPO}/releases/download/{TAG}/Setup",
    f"https://github.com/{REPO}/releases/download/{TAG}/Setup%20(1).exe",                 # no percent escapes
    f"https://github.com/{REPO}/releases/download/..%2f..%2fx/{SETUP}",
    f"https://github.com/{REPO}/releases/download/../{SETUP}",
    f"https://github.com/{REPO}/releases/download/./{SETUP}",
    f"https://github.com/{REPO}/releases/download/{TAG}/.exe",
    f"https://github.com/{REPO}/releases/download/{TAG}/Set up.exe",
    f"https://github.com/{REPO}/releases/download/{TAG}/Setup\u00e9.exe",
    f"https://\uff47ithub.com/{REPO}/releases/download/{TAG}/{SETUP}",
    f" {GOOD}", f"{GOOD}\n", f"{GOOD}\t", GOOD + "x" * 1100,
    "file:///C:/Windows/System32/calc.exe", "//github.com/x", "", "github.com", None, 42, b"https://github.com/x.exe",
])
def test_anything_else_is_refused(url: Any) -> None:
    assert uc._asset_parts(url, REPO) is None


@pytest.mark.parametrize("repo", ["", "owner", "a/b/c", "not a repo", "../x", "a/..", "x" * 3, None, 5])
def test_a_repository_that_is_not_a_plain_slug_accepts_no_link(repo: Any) -> None:
    assert uc._asset_parts(GOOD, repo) is None


def test_release_pages() -> None:
    assert uc._release_page(REPO, TAG) == PAGE
    assert uc._release_page(REPO) == LATEST_PAGE
    assert uc._release_page(REPO, "../x") == LATEST_PAGE
    assert uc._release_page("bad repo") == "https://github.com/"


# ── Downloading ───────────────────────────────────────────────────────────────

def test_a_download_is_hashed_as_it_is_written(net: FakeNet, tmp_path: Path) -> None:
    net.add_bytes(URL, PAYLOAD)
    dst = tmp_path / SETUP
    assert uc._download(URL, str(dst), repo=REPO) == SHA
    assert dst.read_bytes() == PAYLOAD
    assert [p.name for p in tmp_path.iterdir()] == [SETUP]  # no .part left behind


def test_a_download_without_a_content_length_is_still_complete_when_it_ends(net: FakeNet, tmp_path: Path) -> None:
    net.add_bytes(URL, PAYLOAD, announce=None)
    assert uc._download(URL, str(tmp_path / SETUP), repo=REPO) == SHA


def test_a_download_cut_short_is_not_kept(net: FakeNet, tmp_path: Path) -> None:
    net.add_bytes(URL, PAYLOAD[:1000], announce=len(PAYLOAD))
    with pytest.raises(uc.UpdateError, match="cut short"):
        uc._download(URL, str(tmp_path / SETUP), repo=REPO)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("announce", [-1, None])
def test_a_download_over_the_size_cap_is_stopped(net: FakeNet, tmp_path: Path, announce: int | None) -> None:
    net.add_bytes(URL, PAYLOAD, announce=announce)
    with pytest.raises(uc.UpdateError, match="larger than"):
        uc._download(URL, str(tmp_path / SETUP), repo=REPO, max_bytes=1000)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_request_leaves_nothing(net: FakeNet, tmp_path: Path) -> None:
    net.add_error(URL, urllib.error.HTTPError(URL, 404, "Not Found", {}, None))
    with pytest.raises(urllib.error.HTTPError):
        uc._download(URL, str(tmp_path / SETUP), repo=REPO)
    assert list(tmp_path.iterdir()) == []


def test_redirects_may_not_leave_https() -> None:
    handler = uc._HttpsOnlyRedirects()
    req = urllib.request.Request("https://github.com/x")
    headers = http.client.HTTPMessage()
    with pytest.raises(urllib.error.HTTPError, match="non-https"):
        handler.redirect_request(req, io.BytesIO(), 302, "Found", headers, "http://evil.example/x")
    followed = handler.redirect_request(req, io.BytesIO(), 302, "Found", headers, "https://release-assets.githubusercontent.com/x")
    assert followed is not None and followed.full_url.startswith("https://release-assets.githubusercontent.com/")


class _Server:
    """A throwaway HTTP server on 127.0.0.1 for the real opener: a file, a redirect, and a cut-off body."""

    def __init__(self) -> None:
        body = PAYLOAD * 6  # a few hundred KB: more than one read

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/file":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/file")
                    self.end_headers()
                elif self.path == "/cut":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body[:5000])
                    self.wfile.flush()
                    self.close_connection = True
                else:
                    self.send_error(404)

            def log_message(self, *_args: object) -> None:
                pass

        self.body = body
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[_Server]:
    srv = _Server()
    yield srv
    srv.close()


def test_the_real_opener_streams_and_hashes_a_file(server: _Server, tmp_path: Path) -> None:
    dst = tmp_path / "Setup.exe"
    assert uc._download(server.url + "/file", str(dst), repo=REPO) == hashlib.sha256(server.body).hexdigest()
    assert dst.read_bytes() == server.body


def test_the_real_opener_refuses_a_redirect_to_plain_http(server: _Server, tmp_path: Path) -> None:
    with pytest.raises(urllib.error.HTTPError, match="non-https"):
        uc._download(server.url + "/redirect", str(tmp_path / "Setup.exe"), repo=REPO)
    assert list(tmp_path.iterdir()) == []


def test_the_real_opener_notices_a_body_cut_short(server: _Server, tmp_path: Path) -> None:
    with pytest.raises((uc.UpdateError, http.client.IncompleteRead)):
        uc._download(server.url + "/cut", str(tmp_path / "Setup.exe"), repo=REPO)
    assert list(tmp_path.iterdir()) == []


def test_the_real_opener_reports_an_http_error(server: _Server, tmp_path: Path) -> None:
    with pytest.raises(urllib.error.HTTPError):
        uc._download(server.url + "/missing", str(tmp_path / "Setup.exe"), repo=REPO)
    assert list(tmp_path.iterdir()) == []


# ── Staging an update: link, digest, download, helper ─────────────────────────

class Stage:
    """A frozen build with a fake network and helper process, a temp folder under tmp_path and a recorded browser."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.monkeypatch = monkeypatch
        self.temp = tmp_path / "temp"
        self.temp.mkdir()
        self.net = FakeNet()
        self.opened: list[str] = []
        self.launched: list[tuple[str, str | None, dict[str, str]]] = []
        self.events: list[str] = []
        self.proc: Any = FakeProc()
        self.queue: list[Any] = []                 # processes handed out first, one per launch; then self.proc
        self.launch_error: Exception | None = None
        monkeypatch.setattr(uc, "is_frozen", lambda: True)
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(self.temp))
        monkeypatch.setattr(uc, "_urlopen", self.net)
        monkeypatch.setattr(uc, "_open_url", self.opened.append)
        monkeypatch.setattr(uc, "_launch", self.launch)

    def launch(self, command: str, cwd: str | None, env: dict[str, str]) -> Any:
        self.events.append("launch")
        if self.launch_error is not None:
            raise self.launch_error
        self.launched.append((command, cwd, env))
        return self.queue.pop(0) if self.queue else self.proc

    def serve(self, *, payload: bytes = PAYLOAD, digest: Any = f"sha256:{SHA}", listed: bool = True, **kwargs: Any) -> None:
        assets = [asset_json(digest=digest)] if listed else [asset_json("Other.exe", url=URL.replace(SETUP, "Other.exe"))]
        self.net.add_json(API_TAG, release_json(assets=assets))
        self.net.add_bytes(URL, payload, **kwargs)

    def work_dirs(self) -> list[Path]:
        return sorted(p for p in self.temp.iterdir() if p.name.startswith(uc.WORK_PREFIX))


@pytest.fixture
def stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Stage:
    return Stage(monkeypatch, tmp_path)


def test_a_matching_digest_stages_the_update(stage: Stage) -> None:
    stage.serve()
    proc = uc._stage_update(URL, repo=REPO)
    assert proc is stage.proc
    assert stage.net.requests == [API_TAG, URL]          # the digest is read before the download starts
    assert stage.opened == []
    (work,) = stage.work_dirs()
    assert (work / SETUP).read_bytes() == PAYLOAD
    assert (work / uc.HELPER_NAME).read_bytes() == uc._helper_script().encode("ascii")
    (command, cwd, env), = stage.launched
    assert cwd == str(work) and command.endswith(r" /d /e:on /v:off /s /c .\update.cmd")
    assert env["CUM_UPDATE_SETUP"] == SETUP
    assert env["CUM_UPDATE_PID"] == str(os.getpid())
    assert env["CUM_UPDATE_EXE"] == sys.executable
    assert env["CUM_UPDATE_LOG"] == str(stage.temp / "ClaudeUsageMonitor-update.log")


def test_the_digest_the_caller_already_has_saves_the_lookup(stage: Stage) -> None:
    stage.serve()
    uc._stage_update(URL, repo=REPO, digest=f"sha256:{SHA}")
    assert stage.net.requests == [URL]


def test_the_repository_defaults_to_the_apps_own(stage: Stage) -> None:
    stage.serve()
    uc._stage_update(URL)
    assert stage.net.requests == [API_TAG, URL]


def test_a_digest_mismatch_deletes_the_file_and_opens_the_release_page(stage: Stage, caplog: pytest.LogCaptureFixture) -> None:
    stage.serve(digest="sha256:" + "0" * 64)
    with caplog.at_level("WARNING", logger="usage_monitor.update_check"), pytest.raises(uc.UpdateVerificationError, match="does not match"):
        uc._stage_update(URL, repo=REPO)
    assert stage.work_dirs() == [] and list(stage.temp.iterdir()) == []   # the installer is gone
    assert stage.launched == []                                           # nothing was run
    assert stage.opened == [PAGE]
    assert SHA in caplog.text and "0" * 64 in caplog.text                 # the log names both digests


def test_the_update_log_lines_reach_the_apps_log_file(stage: Stage, tmp_path: Path) -> None:
    """The module logs under usage_monitor.*, where setup_logging() sends everything to monitor.log."""
    import logging

    from usage_monitor.log import setup_logging

    logger = logging.getLogger("usage_monitor")
    saved_handlers, saved_level = list(logger.handlers), logger.level
    log_file = tmp_path / "monitor.log"
    setup_logging(path=log_file)
    try:
        stage.serve(digest="sha256:" + "0" * 64)
        with pytest.raises(uc.UpdateVerificationError):
            uc._stage_update(URL, repo=REPO)
        for handler in logger.handlers:
            handler.flush()
        text = log_file.read_text(encoding="utf-8")
        assert "usage_monitor.update_check" in text and "update refused" in text and SHA in text
    finally:
        for handler in list(logger.handlers):
            if handler not in saved_handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.setLevel(saved_level)


def test_a_digest_given_by_the_caller_is_checked_too(stage: Stage) -> None:
    stage.serve()
    with pytest.raises(uc.UpdateVerificationError):
        uc._stage_update(URL, repo=REPO, digest="sha256:" + "f" * 64)
    assert stage.launched == [] and stage.work_dirs() == [] and stage.opened == [PAGE]


@pytest.mark.parametrize("listed", [None, "", "sha512:" + "0" * 128, "sha256:abc", 5])
def test_a_release_without_a_usable_digest_never_runs_silently(stage: Stage, listed: Any) -> None:
    stage.serve(digest=listed)
    with pytest.raises(uc.UpdateVerificationError, match="no SHA-256"):
        uc._stage_update(URL, repo=REPO)
    assert stage.net.requests == [API_TAG]                 # not even downloaded
    assert stage.launched == [] and stage.work_dirs() == [] and stage.opened == [PAGE]


def test_an_asset_the_release_does_not_list_is_refused(stage: Stage) -> None:
    stage.serve(listed=False)
    with pytest.raises(uc.UpdateVerificationError, match="does not list"):
        uc._stage_update(URL, repo=REPO)
    assert stage.net.requests == [API_TAG] and stage.launched == [] and stage.opened == [PAGE]


@pytest.mark.parametrize("url", [
    f"https://evil.example/{REPO}/releases/download/{TAG}/{SETUP}",
    f"http://github.com/{REPO}/releases/download/{TAG}/{SETUP}",
    f"https://github.com/someone-else/{NAME}/releases/download/{TAG}/{SETUP}",
    f"https://github.com/{REPO}/archive/main.zip",
    "file:///C:/Windows/System32/calc.exe", "", None,
])
def test_a_link_off_the_project_is_refused_before_anything_is_fetched(stage: Stage, url: Any) -> None:
    stage.serve()
    with pytest.raises(uc.UpdateRejected):
        uc._stage_update(url, repo=REPO)
    assert stage.net.requests == []                        # no API call, no download
    assert stage.launched == [] and stage.work_dirs() == []
    assert stage.opened == [LATEST_PAGE]                   # the user is sent to the project's own page


def test_it_will_not_run_from_source(stage: Stage, monkeypatch: pytest.MonkeyPatch) -> None:
    stage.serve()
    monkeypatch.setattr(uc, "is_frozen", lambda: False)
    with pytest.raises(uc.UpdateError, match="installed build"):
        uc._stage_update(URL, repo=REPO)
    assert stage.net.requests == [] and stage.launched == []


def test_a_network_failure_leaves_nothing_and_opens_nothing(stage: Stage) -> None:
    stage.net.add_json(API_TAG, release_json())
    stage.net.add_error(URL, urllib.error.URLError("connection reset"))
    with pytest.raises(urllib.error.URLError):
        uc._stage_update(URL, repo=REPO)
    assert stage.work_dirs() == [] and stage.launched == [] and stage.opened == []


def test_a_download_cut_short_leaves_nothing(stage: Stage) -> None:
    stage.serve(payload=PAYLOAD[:500], announce=len(PAYLOAD))
    with pytest.raises(uc.UpdateError, match="cut short"):
        uc._stage_update(URL, repo=REPO)
    assert stage.work_dirs() == [] and stage.launched == []


def test_a_helper_that_cannot_be_started_is_an_error_not_an_exit(stage: Stage) -> None:
    stage.serve()
    stage.launch_error = OSError("blocked by policy")
    with pytest.raises(uc.UpdateError, match="could not start the update helper"):
        uc._stage_update(URL, repo=REPO)
    assert stage.work_dirs() == []                         # the installer is not left behind either


def test_a_helper_that_stops_at_once_is_an_error_not_an_exit(stage: Stage) -> None:
    stage.serve()
    stage.proc = FakeProc(exit_code=1)
    with pytest.raises(uc.UpdateError, match="stopped at once"):
        uc._stage_update(URL, repo=REPO)
    assert stage.work_dirs() == []


NASTY_FOLDERS = [
    "plain name", "Tom&Jerry", "a^b", "100%", "x%PATH%y", "(work), a; b=c @d !e", "caf\u00e9 \u00fcber \u0141ukasz",
]


@pytest.mark.parametrize("folder", NASTY_FOLDERS)
def test_staging_does_not_depend_on_what_the_temp_folder_is_called(
    stage: Stage, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: str,
) -> None:
    nasty = tmp_path / folder
    nasty.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(nasty))
    stage.serve()
    uc._stage_update(URL, repo=REPO)
    (work,) = [p for p in nasty.iterdir() if p.name.startswith(uc.WORK_PREFIX)]
    (command, cwd, env), = stage.launched
    assert cwd == str(work)                                # the folder is entered, not named on the command line
    assert folder not in command and str(nasty) not in command
    assert (work / uc.HELPER_NAME).read_bytes() == uc._helper_script().encode("ascii")   # the same ASCII text for everybody
    assert env["CUM_UPDATE_LOG"] == str(nasty / "ClaudeUsageMonitor-update.log")


# ── apply_update: the order of things and the single flight ───────────────────

class Run:
    """apply_update() with the hard exit replaced, and a record of the order things happened in."""

    def __init__(self, stage: Stage, monkeypatch: pytest.MonkeyPatch) -> None:
        self.stage = stage
        self.errors: list[BaseException] = []
        self.done = threading.Event()
        monkeypatch.setattr(uc, "_exit_process", self._exit)

    def _exit(self) -> None:
        self.stage.events.append("exit")
        self.done.set()

    def start(self, *, before_exit: Callable[[], None] | None = None, **kwargs: Any) -> None:
        def hook() -> None:
            self.stage.events.append("before_exit")
            if before_exit:
                before_exit()

        def on_error(exc: BaseException) -> None:
            self.errors.append(exc)
            self.done.set()

        uc.apply_update(kwargs.pop("url", URL), repo=REPO, on_error=on_error, on_before_exit=hook, **kwargs)
        assert self.done.wait(10), "apply_update never finished"
        time.sleep(0.05)  # let the worker release the lock


@pytest.fixture
def run(stage: Stage, monkeypatch: pytest.MonkeyPatch) -> Run:
    return Run(stage, monkeypatch)


def test_the_app_exits_only_after_the_helper_is_running(stage: Stage, run: Run) -> None:
    stage.serve()
    run.start()
    assert stage.events == ["launch", "before_exit", "exit"]
    assert run.errors == []


def test_the_app_still_exits_when_its_pre_exit_hook_fails(stage: Stage, run: Run) -> None:
    stage.serve()

    def boom() -> None:
        raise RuntimeError("teardown bug")

    run.start(before_exit=boom)
    assert stage.events == ["launch", "before_exit", "exit"]   # the helper is already waiting for us


def test_a_helper_that_dies_while_the_app_is_closing_is_replaced_by_a_restart_helper(stage: Stage, run: Run) -> None:
    """The pre-exit hook takes seconds, long after the 0.6 s grace check. A helper killed in that time must not
    leave the user with no app and no update: the app looks again right before it exits."""
    stage.serve()
    first, second = FakeProc(), FakeProc()
    stage.queue = [first, second]
    run.start(before_exit=lambda: first.die(137))
    assert stage.events == ["launch", "before_exit", "launch", "exit"]   # something is running before the app goes
    assert stage.opened == [PAGE]                                         # and the user can still update by hand
    assert run.errors == []
    assert len(stage.launched) == 2
    (work,) = stage.work_dirs()                                           # the restart helper removes it when it is done
    assert (work / uc.HELPER_NAME).read_bytes() == uc._helper_script(install=False).encode("ascii")
    assert stage.launched[1][0] == stage.launched[0][0] and stage.launched[1][1] == str(work)   # started the same safe way


def test_a_helper_that_is_still_running_at_the_exit_is_left_alone(stage: Stage, run: Run) -> None:
    stage.serve()
    run.start()
    assert stage.events == ["launch", "before_exit", "exit"] and stage.opened == []
    (work,) = stage.work_dirs()
    assert (work / uc.HELPER_NAME).read_bytes() == uc._helper_script().encode("ascii")   # the installer helper, untouched


def test_when_even_the_restart_helper_cannot_start_the_app_still_exits_and_leaves_nothing_behind(stage: Stage, run: Run) -> None:
    stage.serve()
    first = FakeProc()
    stage.queue = [first, FakeProc(exit_code=1)]                         # the restart helper stops at once
    run.start(before_exit=lambda: first.die(1))
    assert stage.events == ["launch", "before_exit", "launch", "exit"]   # nothing else can be done: its windows are gone
    assert stage.opened == [PAGE]
    assert stage.work_dirs() == []                                        # the installer is not left behind in %TEMP%


def test_the_app_is_not_restarted_when_it_cannot_be_started_from_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    """Called with nothing staged (a stand-in for a staging step that recorded nothing): look, open nothing, start nothing."""
    monkeypatch.setattr(uc, "_open_url", lambda url: pytest.fail("opened " + url))
    monkeypatch.setattr(uc, "_spawn_installer", lambda *a, **k: pytest.fail("started a helper"))
    assert uc._helper_is_waiting(FakeProc(), {}) is True
    assert uc._helper_is_waiting(FakeProc(exit_code=3), {}) is False


@pytest.mark.parametrize("setup", ["mismatch", "no_digest", "foreign_link", "helper_missing", "network"])
def test_the_app_keeps_running_when_the_update_cannot_go_ahead(stage: Stage, run: Run, setup: str) -> None:
    stage.serve()
    url = URL
    if setup == "mismatch":
        stage.serve(digest="sha256:" + "1" * 64)
    elif setup == "no_digest":
        stage.serve(digest=None)
    elif setup == "foreign_link":
        url = "https://evil.example/Setup.exe"
    elif setup == "helper_missing":
        stage.launch_error = OSError("no cmd")
    elif setup == "network":
        stage.net.add_error(URL, urllib.error.URLError("offline"))
    run.start(url=url)
    assert "exit" not in stage.events and "before_exit" not in stage.events
    assert len(run.errors) == 1 and isinstance(run.errors[0], Exception)
    assert stage.work_dirs() == []


def test_a_rejected_update_reports_its_reason_to_on_error(stage: Stage, run: Run) -> None:
    stage.serve(digest="sha256:" + "2" * 64)
    run.start()
    assert isinstance(run.errors[0], uc.UpdateVerificationError)
    run.errors.clear()
    run.done.clear()
    run.start(url="https://evil.example/Setup.exe")
    assert isinstance(run.errors[0], uc.UpdateRejected)


def test_a_second_request_while_one_is_running_is_ignored(stage: Stage, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def slow(asset_url: str, **_kwargs: Any) -> FakeProc:
        calls.append(asset_url)
        entered.set()
        release.wait(10)
        return FakeProc()

    monkeypatch.setattr(uc, "_stage_update", slow)
    uc.apply_update(URL, repo=REPO)
    assert entered.wait(5)
    uc.apply_update(URL, repo=REPO)       # a second click on "Update"
    time.sleep(0.2)
    release.set()
    assert run.done.wait(10)
    time.sleep(0.1)
    assert calls == [URL]


# ── The helper script and how it is started ───────────────────────────────────

def test_the_helper_script_is_a_constant_ascii_batch_file() -> None:
    script = uc._helper_script()
    assert script == uc._helper_script() and script.isascii()
    assert script.endswith("\r\n") and "\n" not in script.replace("\r\n", "")
    assert script.startswith("@echo off\r\n")


def test_the_helper_script_holds_nothing_that_varies() -> None:
    """Only environment variables carry the installer's name, the exe to relaunch, the pid and the log."""
    script = uc._helper_script()
    used = set(re.findall(r"%(CUM_[A-Z_]+)%", script))
    provided = set(uc._helper_environment(r"C:\x\Setup.exe", r"C:\x\App.exe", 1))
    assert used - provided == {"CUM_N"}                    # the loop counter is the script's own
    assert not re.search(r"[A-Za-z]:\\", script.replace("%SystemRoot%", ""))   # no drive path anywhere
    assert "Users" not in script and sys.executable not in script


def test_the_helper_script_runs_the_installer_the_way_the_updater_promises() -> None:
    script = uc._helper_script()
    assert f'"%~dp0%CUM_UPDATE_SETUP%" {uc.SILENT_ARGS} /LOG=' in script
    for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", '/MERGETASKS="!startup"'):
        assert flag in uc.SILENT_ARGS


def test_the_helper_script_has_no_pipe_and_calls_system_tools_by_absolute_path() -> None:
    script = uc._helper_script()
    assert "|" not in script                                # a pipe hangs a cmd that has no console window
    for line in script.splitlines():
        for tool in ("tasklist", "find", "ping"):
            if re.search(rf"\b{tool}(\.exe)?\b", line) and not line.lstrip().startswith(("echo", ">")):
                assert f"%SystemRoot%\\System32\\{tool}.exe" in line, line


def test_every_echo_in_the_helper_script_puts_its_redirection_first() -> None:
    """'echo ... 0>> log' would redirect handle 0 and print the text instead of logging it."""
    for line in uc._helper_script().splitlines():
        if re.search(r"\becho\b", line) and line != "@echo off":
            assert line.startswith(">"), line


def test_the_helper_script_cleans_up_last_and_waits_for_the_monitor_first() -> None:
    lines = uc._helper_script().splitlines()
    assert lines[-1] == 'rmdir /s /q "%~dp0"'
    first_wait = next(i for i, line in enumerate(lines) if "tasklist.exe" in line)
    installer = next(i for i, line in enumerate(lines) if "CUM_UPDATE_SETUP%\" " in line)
    relaunch = next(i for i, line in enumerate(lines) if line.startswith("if exist") and "start" in line)
    assert first_wait < installer < relaunch < len(lines) - 1


def test_the_restart_helper_waits_for_the_monitor_starts_it_again_and_runs_no_installer() -> None:
    """The same script without the installer step: what runs when the update helper died while the app was closing."""
    script = uc._helper_script(install=False)
    lines = script.splitlines()
    assert script.isascii() and script.endswith("\r\n") and lines[-1] == 'rmdir /s /q "%~dp0"'
    assert "CUM_UPDATE_SETUP%\" " not in script and "/VERYSILENT" not in script and "installer exit code" not in script
    assert lines[3].startswith('>>"%CUM_UPDATE_LOG%" echo') and "restart helper" in lines[3]   # adds to the log of the one that stopped
    first_wait = next(i for i, line in enumerate(lines) if "tasklist.exe" in line)
    relaunch = next(i for i, line in enumerate(lines) if line.startswith("if exist") and "start" in line)
    assert first_wait < relaunch < len(lines) - 1
    # The same rules as the update helper: nothing that varies, no pipe, system tools by absolute path, redirection first.
    used = set(re.findall(r"%(CUM_[A-Z_]+)%", script))
    assert used - set(uc._helper_environment(r"C:\x\Setup.exe", r"C:\x\App.exe", 1)) == {"CUM_N"}
    assert "|" not in script and not re.search(r"[A-Za-z]:\\", script.replace("%SystemRoot%", ""))
    for line in lines:
        if re.search(r"\becho\b", line) and line != "@echo off":
            assert line.startswith(">"), line
    # The update helper is what the default gives, and it still starts a fresh log.
    assert uc._helper_script() == uc._helper_script(install=True)
    assert uc._helper_script().splitlines()[3].startswith('>"%CUM_UPDATE_LOG%" echo') and "update helper started" in uc._helper_script()


def test_a_helper_in_a_drive_folder_is_started_by_its_relative_name() -> None:
    helper = r"C:\Users\Tom&Jerry (x)\AppData\Local\Temp\cum-update-ab12\update.cmd"
    command, cwd = uc._helper_command(helper)
    assert cwd == r"C:\Users\Tom&Jerry (x)\AppData\Local\Temp\cum-update-ab12"
    assert command.endswith(r'" /d /e:on /v:off /s /c .\update.cmd')
    assert "Tom" not in command and "AppData" not in command


def test_a_helper_in_a_unc_folder_is_named_in_full_inside_doubled_quotes() -> None:
    helper = r"\\server\share\Tom&Jerry ^ 100%\cum-update-ab12\update.cmd"
    command, cwd = uc._helper_command(helper)
    assert cwd is None                                     # cmd cannot use a UNC folder as its current directory
    assert command.endswith(f' /d /e:on /v:off /s /c ""{helper}""')


def test_the_helper_is_started_hidden_with_its_own_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_popen(command: Any, **kwargs: Any) -> FakeProc:
        seen.update(command=command, **kwargs)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    uc._launch("cmd line", r"C:\x", {"A": "1"})
    flags = seen["creationflags"]
    assert flags & 0x08000000                              # CREATE_NO_WINDOW: no terminal window, pipes work
    assert flags & 0x00000200                              # CREATE_NEW_PROCESS_GROUP
    assert not flags & 0x00000008                          # not DETACHED_PROCESS (a console child would open a window)
    assert seen["command"] == "cmd line" and seen["cwd"] == r"C:\x" and seen["env"] == {"A": "1"}
    assert seen["stdin"] is seen["stdout"] is seen["stderr"] is subprocess.DEVNULL and seen["close_fds"] is True


# ── The real helper under the real cmd.exe (CUM_UI_TESTS=1) ───────────────────
#
# No test in this section may open a console window. Three things start console programs: the stand-in "monitor"
# (ping.exe, started by the test: NO_WINDOW), the helper's own cmd.exe (started by update_check with CREATE_NO_WINDOW,
# so ping.exe, find.exe and the stand-in installer share its hidden console), and the app the helper relaunches. The
# real app is a windowed exe; a stand-in for it has to be one too, see StandInApp.

class StandInApp:
    """The program the helper relaunches, standing in for the monitor's windowed exe. It cannot open a console.

    It is pythonw.exe (GUI subsystem: whatever starts it, no console window) run through `start`, exactly as the
    helper starts the real app. It has no script. The sitecustomize.py that Python finds through PYTHONPATH writes
    ``marker`` as the interpreter starts, and Python then exits at once (no stdin). The exe is reached through a
    junction in a folder whose name holds spaces and cmd metacharacters, so the quoted `start "" "%CUM_UPDATE_EXE%"`
    runs on a hard path, the way a user's profile folder makes it one.

    The first stand-in was a .cmd file. `start` runs a .cmd with cmd /K, which opens a console window that never
    closes: one left on the desktop of whoever ran the tests, every run, per relaunch.

    ``exe`` is what CUM_UPDATE_EXE should be, ``env`` the variables the helper must inherit for the marker to be
    written, ``gone()`` removes the junction (never what it points at) and is safe to call again."""

    def __init__(self, root: Path, marker: Path) -> None:
        base = Path(getattr(sys, "_base_executable", sys.executable)).resolve().parent
        if not (base / "pythonw.exe").is_file():
            pytest.skip("no pythonw.exe beside this Python to stand in for the windowed app")
        hook = root / "hook"
        hook.mkdir(parents=True)
        (hook / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('started')\n", encoding="utf-8")
        self._junction: Path | None = None
        folder = root / "Tom&Jerry (x) 100%"
        folder.mkdir()
        try:
            import _winapi
            _winapi.CreateJunction(str(base), str(folder / "py"))
            self._junction = folder / "py"
        except (ImportError, AttributeError, OSError):
            pass            # no junction here: the exe is reached by its own path
        self.exe = str((self._junction or base) / "pythonw.exe")
        self.env = {"PYTHONPATH": str(hook)}

    def gone(self) -> None:
        junction, self._junction = self._junction, None
        if junction is not None:
            os.rmdir(junction)      # only the link: the Python installation it points at stays


def wait_for(condition: Callable[[], bool], timeout: float = 15.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return bool(condition())


@UI
def test_the_real_helper_waits_for_the_monitor_installs_relaunches_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Seven helpers at once, each in a temp folder with other characters, each against a stand-in monitor
    (a process that lives ~5 s) and a stand-in installer (ping.exe, which exits 1 on the unknown switches).
    The helper must not run the installer before the monitor is gone, must run it through the folder name,
    must relaunch the app when its exe exists (a windowed stand-in, StandInApp), and must remove its own folder
    whether or not it could."""
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    ping = str(system32 / "ping.exe")
    cases: list[dict[str, Any]] = []
    app: StandInApp | None = None
    try:
        for index, folder in enumerate(NASTY_FOLDERS):
            temp = tmp_path / folder
            work = temp / "cum-update-abc123"
            work.mkdir(parents=True)
            shutil.copy(ping, work / "Setup.exe")
            relaunched = tmp_path / f"relaunched{index}.txt"
            extra_env: dict[str, str] = {}
            if index == 0:
                app = StandInApp(tmp_path / "standin", relaunched)
                exe, extra_env = app.exe, app.env
            else:
                exe = str(tmp_path / "no such app.exe")        # missing: the helper must finish all the same
            monitor = subprocess.Popen([ping, "-n", "6", "127.0.0.1"], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            monkeypatch.setattr(tempfile, "gettempdir", lambda t=temp: str(t))
            monkeypatch.setenv("TEMP", str(temp))
            with monkeypatch.context() as scoped:        # the helper copies the environment as it starts
                for name, value in extra_env.items():
                    scoped.setenv(name, value)
                helper = uc._spawn_installer(str(work / "Setup.exe"), exe=exe, pid=monitor.pid)
            cases.append({"temp": temp, "work": work, "monitor": monitor, "helper": helper,
                          "relaunched": relaunched, "log": temp / "ClaudeUsageMonitor-update.log", "installer_ran_after_monitor": None})

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            for case in cases:
                if case["installer_ran_after_monitor"] is None and case["log"].exists() \
                        and "installer exit code" in case["log"].read_text(errors="replace"):
                    case["installer_ran_after_monitor"] = case["monitor"].poll() is not None
            if all(not case["work"].exists() for case in cases) and all(c["helper"].poll() is not None for c in cases):
                break
            time.sleep(0.25)

        for case in cases:
            label = case["temp"].name
            log_text = case["log"].read_text(errors="replace")
            assert not case["work"].exists(), f"{label}: the helper left its folder behind"
            assert "update helper started" in log_text, label
            assert "installer exit code 1" in log_text, f"{label}: the installer was not run through the path\n{log_text}"
            assert case["installer_ran_after_monitor"] is True, f"{label}: the installer ran while the monitor was alive"
        # The relaunched program starts a moment after cmd runs `start` (the interpreter has to come up).
        assert wait_for(cases[0]["relaunched"].exists), "the helper did not start the app again"
        assert cases[0]["relaunched"].read_text() == "started"
    finally:
        for case in cases:
            for proc in (case["monitor"], case["helper"]):
                if proc.poll() is None:
                    proc.kill()
        if app is not None:
            app.gone()


@UI
def test_a_real_helper_that_stops_at_once_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    work = tmp_path / "cum-update-x"
    work.mkdir()
    (work / "Setup.exe").write_bytes(b"x")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(uc, "_helper_script", lambda install=True: "@echo off\r\nexit 3\r\n")
    with pytest.raises(uc.UpdateError, match="exit code 3"):
        uc._spawn_installer(str(work / "Setup.exe"), exe="x", pid=1)


@UI
def test_the_real_restart_helper_waits_for_the_monitor_starts_the_app_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The restart helper under the real cmd.exe, in a temp folder full of metacharacters: it must not run the
    installer next to it (ping.exe stands in; it would log an exit code), must start the app only once the monitor
    (a ping that lives ~4 s) is gone, must add to the log of the helper that stopped, and must remove its folder.
    The app it starts is a windowed stand-in (StandInApp), never a program that opens a console."""
    ping = Path(os.environ["SystemRoot"]) / "System32" / "ping.exe"
    temp = tmp_path / "Tom&Jerry (x) 100%"
    work = temp / "cum-update-abc123"
    work.mkdir(parents=True)
    shutil.copy(ping, work / "Setup.exe")
    marker = tmp_path / "restarted.txt"
    app = StandInApp(tmp_path / "standin", marker)
    log = temp / "ClaudeUsageMonitor-update.log"
    log.write_text("update helper started\r\n", encoding="ascii")            # what the helper that stopped had written
    monitor = subprocess.Popen([str(ping), "-n", "5", "127.0.0.1"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setenv("TEMP", str(temp))
    with monkeypatch.context() as scoped:            # the helper copies the environment as it starts
        for name, value in app.env.items():
            scoped.setenv(name, value)
        helper = uc._spawn_installer(str(work / "Setup.exe"), exe=app.exe, pid=monitor.pid, install=False)
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not (marker.exists() and not work.exists()):
            time.sleep(0.25)
        assert marker.exists() and marker.read_text() == "started"
        assert monitor.poll() is not None, "the app was started while the monitor was still alive"
        assert not work.exists(), "the restart helper left its folder behind"
        text = log.read_text(errors="replace")
        assert text.startswith("update helper started") and "restart helper started" in text
        assert "installer exit code" not in text, "the restart helper ran the installer"
    finally:
        for proc in (monitor, helper):
            if proc.poll() is None:
                proc.kill()
        app.gone()


@UI
def test_a_real_helper_in_a_unc_folder_with_metacharacters_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The administrative share of this PC stands in for a network folder: cmd cannot enter it, so the helper is named in full."""
    unc_root = Path("\\\\localhost\\" + str(tmp_path).replace(":", "$", 1))
    if not unc_root.exists():
        pytest.skip("the administrative share C$ is not reachable here")
    work = unc_root / "Tom&Jerry ^ 100% (x)" / "cum-update-abc123"
    work.mkdir(parents=True)
    ping = Path(os.environ["SystemRoot"]) / "System32" / "ping.exe"
    shutil.copy(ping, work / "Setup.exe")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monitor = subprocess.Popen([str(ping), "-n", "3", "127.0.0.1"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    helper = uc._spawn_installer(str(work / "Setup.exe"), exe=str(tmp_path / "none.exe"), pid=monitor.pid)
    try:
        log = tmp_path / "ClaudeUsageMonitor-update.log"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not (log.exists() and "installer exit code" in log.read_text(errors="replace")):
            time.sleep(0.25)
        assert "installer exit code 1" in log.read_text(errors="replace")
    finally:
        helper.kill()
        monitor.kill()


# ── installer.iss: what the updater and the app rely on ───────────────────────

ISS = (ROOT / "installer.iss").read_text(encoding="utf-8")
SPEC_TEXT = (ROOT / "ClaudeUsageMonitor.spec").read_text(encoding="utf-8")


def iss_defines() -> dict[str, str]:
    return dict(re.findall(r'^\s*#define (\w+) "([^"]*)"', ISS, re.M))


def iss_expand(text: str) -> str:
    for name, value in iss_defines().items():
        text = text.replace("{#" + name + "}", value)
    return text


def iss_section(name: str) -> list[str]:
    match = re.search(rf"^\[{name}\]\s*$(.*?)(?=^\[|\Z)", ISS, re.M | re.S)
    assert match, f"[{name}] is missing"
    return [line.strip() for line in match.group(1).splitlines() if line.strip() and not line.strip().startswith(";")]


def iss_setup(name: str) -> str:
    for line in iss_section("Setup"):
        if line.startswith(name + "="):
            return iss_expand(line.split("=", 1)[1])
    raise AssertionError(f"[Setup] has no {name}")


def iss_parameters(line: str) -> dict[str, str]:
    """One entry line -> its parameters. A quoted value loses its quotes and a doubled quote inside becomes one quote,
    so ValueData: "" "{app}" "" (written without the spaces in the script) reads as "{app}" with the quotes kept."""
    out: dict[str, str] = {}
    for key, raw in re.findall(r'(\w+):\s*("(?:[^"]|"")*"|[^;]*)', line):
        raw = raw.strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1].replace('""', '"')
        out[key] = raw
    return out


def iss_code_constant(name: str) -> str:
    match = re.search(rf"^\s*{name}\s*=\s*'([^']*)'", ISS, re.M)
    assert match, f"[Code] has no constant {name}"
    return iss_expand(match.group(1))


def pascal_code(text: str) -> str:
    """The text without // comments (a // inside a string literal, as in a URL, stays)."""
    lines = []
    for line in text.splitlines():
        in_string = False
        for index, char in enumerate(line):
            if char == "'":
                in_string = not in_string            # a doubled quote inside a string toggles twice
            elif char == "/" and line[index:index + 2] == "//" and not in_string:
                line = line[:index]
                break
        lines.append(line.rstrip())
    return "\n".join(lines)


def iss_routine(name: str) -> str:
    """One [Code] procedure or function, from its header to the next routine or section comment, without comments."""
    match = re.search(rf"^(?:procedure|function) {name}\b.*?(?=^(?:procedure|function) |^// ----|\Z)", ISS, re.M | re.S)
    assert match, f"[Code] has no routine {name}"
    return pascal_code(match.group(0))


def test_the_app_id_is_the_one_the_v1_installs_already_carry() -> None:
    """A different AppId would install beside v1 instead of over it. The v1 uninstall key is 'Claude Usage Monitor_is1'."""
    assert iss_setup("AppId") == iss_setup("AppName") == "Claude Usage Monitor"
    assert iss_code_constant("UninstallKey").endswith("\\Uninstall\\Claude Usage Monitor_is1")
    assert iss_setup("DefaultDirName") == r"{localappdata}\ClaudeUsageMonitor"
    assert iss_setup("PrivilegesRequired") == "lowest"


def test_setup_stops_while_the_monitor_runs() -> None:
    from usage_monitor import app as app_mod

    assert iss_setup("AppMutex") == app_mod.MUTEX_NAME == "Global\\ClaudeUsageMonitor"


def test_restart_manager_closes_what_is_using_the_files_and_restarts_nothing() -> None:
    assert iss_setup("CloseApplications") == "yes"
    assert iss_setup("RestartApplications") == "no"
    assert "*.exe" in iss_setup("CloseApplicationsFilter")


def test_the_dashboard_window_setup_closes_is_the_one_the_app_opens() -> None:
    from usage_monitor.ui import dashboard

    assert iss_code_constant("DashboardTitle") == dashboard.WINDOW_TITLE
    assert "PostMessage(Wnd, WmClose" in ISS and "FindWindowByWindowName(DashboardTitle)" in ISS
    # Asked to close before the files are replaced, and again before they are removed.
    assert "CloseDashboardWindow;" in ISS[ISS.index("function PrepareToInstall"):ISS.index("procedure CurStepChanged")]
    assert "CloseDashboardWindow;" in ISS[ISS.index("procedure CurUninstallStepChanged"):]


def test_the_start_with_windows_task_is_checked_by_default() -> None:
    (task,) = [iss_parameters(line) for line in iss_section("Tasks")]
    assert task["Name"] == "startup"
    assert "unchecked" not in task.get("Flags", "").split()
    assert "Windows" in task["Description"]


def test_the_start_with_windows_task_writes_the_entry_the_app_writes() -> None:
    from usage_monitor.ui import startup

    (entry,) = [iss_parameters(line) for line in iss_section("Registry")]
    assert entry["Root"] == "HKCU" and entry["Subkey"] == startup.RUN_KEY
    assert entry["ValueType"] == "string" and entry["ValueName"] == startup.VALUE_NAME
    assert entry["Tasks"] == "startup" and "uninsdeletevalue" in entry["Flags"].split()
    installed = r"C:\Users\Someone Else\AppData\Local\ClaudeUsageMonitor\ClaudeUsageMonitor.exe"
    data = iss_expand(entry["ValueData"]).replace("{app}", installed.rsplit("\\", 1)[0])
    assert data == startup.command_line(frozen=True, executable=installed)


def test_the_uninstaller_removes_the_entry_of_this_install_including_the_one_v1_wrote() -> None:
    from usage_monitor.ui import startup

    assert iss_code_constant("RunKey") == startup.RUN_KEY and iss_code_constant("RunValue") == startup.VALUE_NAME
    body = ISS[ISS.index("procedure RemoveStartupEntry"):ISS.index("procedure AskToDeleteUserData")]
    assert "Ours := '\"' + ExpandConstant('{app}\\{#ExeName}') + '\"';" in body
    assert "Pos(Lowercase(Ours), Lowercase(Current)) = 1" in body      # a prefix match, see below
    assert "RegDeleteValue(HKCU, RunKey, RunValue)" in body
    # What v1.1.5 wrote for the frozen build: the exe, then the path of a script that is not there.
    exe = r"C:\Users\Someone\AppData\Local\ClaudeUsageMonitor\ClaudeUsageMonitor.exe"
    v1_value = f'"{exe}" "{exe.rsplit(chr(92), 1)[0]}\\_internal\\main.py"'
    assert v1_value.lower().startswith(startup.command_line(frozen=True, executable=exe).lower())
    assert v1_value != startup.command_line(frozen=True, executable=exe)   # so an equality test would have missed it


def test_a_silent_update_leaves_the_start_with_windows_entry_alone() -> None:
    assert '/MERGETASKS="!startup"' in uc.SILENT_ARGS
    (task,) = [iss_parameters(line) for line in iss_section("Tasks")]
    assert task["Name"] == "startup"                       # the name the updater deselects


def test_a_silent_update_keeps_the_users_choice_by_code_not_by_the_state_of_the_task_box() -> None:
    """v1.1.5's updater cannot be changed and runs Setup with no /MERGETASKS, so the default-checked task would write
    the entry for every v1 user who had it off. Setup rebuilds the task list with its defaults after InitializeWizard,
    so nothing set there lasts: the decision is a Check function on the [Registry] entry.

        Setup run                              entry before    entry after
        v1 updater (silent, no /MERGETASKS)    missing         missing
        v1 updater                             v1's two parts  the quoted exe
        v2 updater (/MERGETASKS="!startup")    either          untouched (the task is off)
        first install, silent                  missing         the quoted exe
        interactive                            -               what the box says
    """
    (entry,) = [iss_parameters(line) for line in iss_section("Registry")]
    assert entry["Tasks"] == "startup" and entry["Check"] == "WriteStartupEntry"
    body = iss_routine("WriteStartupEntry")
    assert re.search(r"if WizardSilent and InstalledBefore then\s+Result := StartupEntryExists\s+else\s+Result := True;", body)
    assert '/MERGETASKS="!startup"' in uc.SILENT_ARGS      # v2's own updater: the task is off, so the entry is not processed at all


def test_the_start_with_windows_box_is_set_once_as_the_page_appears_and_is_only_ever_unchecked() -> None:
    assert "WizardSelectTasks" not in iss_routine("InitializeWizard")       # Setup rebuilds the task list after it
    page = iss_routine("CurPageChanged")
    for needle in ("wpSelectTasks", "StartupBoxSet := True", "InstalledBefore", "StartupEntryExists"):
        assert needle in page, needle
    # Only "!startup": the event also fires in a silent run, where checking the box would override /MERGETASKS="!startup".
    assert re.findall(r"WizardSelectTasks\('([^']*)'\)", pascal_code(ISS)) == ["!startup"]


# --- F2: the folder is proven replaceable before it is wiped ------------------------------------------------

def test_the_bundle_folder_is_proven_replaceable_before_install_delete_wipes_it() -> None:
    """[InstallDelete] empties {app}\\_internal before one new file is copied. With a file held open, v1.1.5's updater (which
    cannot be changed) would be left with no program at all, so Setup renames the folder away and back first, and stops
    with the old install untouched when it cannot."""
    assert iss_section("InstallDelete") == ['Type: filesandordirs; Name: "{app}\\_internal"']
    assert re.search(r"if CurStep = ssInstall then\s+RequireBundleFolderFree;", iss_routine("CurStepChanged"))
    body = iss_routine("RequireBundleFolderFree")
    assert "ExpandConstant('{app}\\_internal')" in body
    assert body.index("RenameFile(Folder, Probe)") < body.index("RenameFile(Probe, Folder)")   # away, then straight back
    assert body.count("Abort;") == 2 and "Sleep(200)" in body                                    # it retries, then stops
    assert "DeleteFile" not in body and "DelTree" not in body                                    # the probe changes nothing


# --- F7: the WebView2 bootstrapper is verified before it is run --------------------------------------------

def webview2_signature_script(path: str) -> str:
    """The PowerShell that Setup runs for ``path``: the [Code] constant, with the path quoted the way the code quotes it."""
    match = re.search(r"WebView2SignatureCheck = '((?:[^']|'')*)';", ISS)
    assert match, "[Code] has no WebView2SignatureCheck"
    return match.group(1).replace("''", "'").replace("%s", path.replace("'", "''"))


def test_the_webview2_bootstrapper_is_run_only_after_its_signature_checks_out() -> None:
    install = iss_routine("InstallWebView2")
    guard, shell = install.index("WebView2InstallerIsMicrosofts("), install.index("ShellExec(")
    assert guard < shell and "Exit;" in install[guard:shell]            # a failed check ends the step before anything is run
    check = iss_routine("WebView2InstallerIsMicrosofts")
    assert "StringChangeEx(Quoted, '''', '''''', True)" in check       # an apostrophe in the user's temp path cannot end the quotes
    assert "{sys}\\WindowsPowerShell\\v1.0\\powershell.exe" in check and "Code = 0" in check
    script = webview2_signature_script("C:\\Users\\O'Brien\\Setup.exe")
    for needle in ("Get-AuthenticodeSignature -LiteralPath 'C:\\Users\\O''Brien\\Setup.exe'", "$s.Status -eq 'Valid'",
                   "$s.SignerCertificate.Subject -like 'CN=Microsoft Corporation,*'", "exit 0", "exit 1"):
        assert needle in script, needle


def test_the_installer_has_the_name_the_release_workflow_and_build_script_use() -> None:
    assert iss_setup("OutputBaseFilename") == "ClaudeUsageMonitorSetup-v{#AppVer}"      # {#AppVer} is the VERSION, filled in by ISCC
    assert iss_setup("OutputDir") == "installer"
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "installer/ClaudeUsageMonitorSetup-v${{ steps.ver.outputs.version }}.exe" in workflow
    assert "ClaudeUsageMonitorSetup-v%APPVER%.exe" in (ROOT / "build.bat").read_text(encoding="utf-8")


def test_the_installer_packages_the_folder_and_exe_the_spec_builds(spec: types.SimpleNamespace) -> None:
    assert iss_defines()["DistDir"] == r"dist\ClaudeUsageMonitor"
    assert iss_defines()["ExeName"] == spec.exe.kw["name"] + ".exe" == spec.coll.kw["name"] + ".exe"
    assert iss_defines()["DistDir"].endswith("\\" + spec.coll.kw["name"])
    assert any(line.startswith('Source: "{#DistDir}\\*"') for line in iss_section("Files"))
    assert all("ClaudeUsageMonitor.exe" in iss_expand(line) for line in iss_section("Run"))


def test_the_uninstaller_keeps_the_settings_unless_the_user_says_otherwise() -> None:
    from usage_monitor import paths

    assert iss_section("UninstallDelete") == []            # nothing is deleted unasked
    assert "{userappdata}\\" + paths.APP_FOLDER in ISS      # %APPDATA%\ClaudeUsageMonitor, where paths.app_data_dir() is
    prompt = ISS.index("MsgBox('Do you also want to delete")
    assert "MB_YESNO or MB_DEFBUTTON2" in ISS[prompt:prompt + 700]   # defaults to No
    guard = ISS.index("if UninstallSilent then", ISS.index("procedure AskToDeleteUserData"))
    assert guard < prompt < ISS.index("DelTree(Folder")
    assert "SameText(ExtractFileName(RemoveBackslash(Folder)), 'ClaudeUsageMonitor')" in ISS


def test_the_webview2_offer_follows_microsofts_detection_rule() -> None:
    assert iss_code_constant("WebView2Client").endswith(r"\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
    assert iss_code_constant("WebView2Url") == "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
    for needle in ("WebView2VersionIn(HKLM32)", "WebView2VersionIn(HKCU)", "WebView2VersionIn(HKLM64)", "'pv'", "'0.0.0.0'"):
        assert needle in ISS, needle
    offer = ISS.index("procedure OfferWebView2")
    assert "WizardSilent" in ISS[offer:offer + 200]         # never in a silent install, so a self-update never downloads it
    assert "web browser" in ISS[offer:]                     # the explanation: without it the dashboard opens in the browser


def test_the_iss_is_ascii() -> None:
    """Inno reads the script in the system code page unless it has a BOM; keep it plain."""
    ISS.encode("ascii")


# ── installer.iss, compiled (CUM_UI_TESTS=1) and run in a sandbox (CUM_INSTALLER_E2E=1 as well) ────────────────
#
# The text checks above cannot see whether the [Code] section compiles, or what a silent upgrade does to the
# registry and the program folder. ISCC checks the first; the sandbox runs check the second. NOTHING here ever
# runs the real installer or touches an installed copy: the sandbox is the real script with every identity renamed
# (AppId, install folder, Run value, mutex, Start menu group, user-data folder), and it is refused unless none of the
# real names is left in it. The runs are opt-in because they install and uninstall (a renamed) program.

SANDBOX = "CUM Test Sandbox"                         # AppId and AppName: the uninstall key is "CUM Test Sandbox_is1"
SANDBOX_RUN_VALUE = "CUMTestSandbox"
SANDBOX_MUTEX = "Global\\CUMTestSandboxMutex"
SANDBOX_DATA = "CUMTestSandboxData"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
V1_SWITCHES = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]               # what v1.1.5's updater passes (it cannot change)
V2_SWITCHES = [*V1_SWITCHES, '/MERGETASKS="!startup"']                          # what update_check.py passes
REAL_IDENTITY = (                                                                # none of these may survive in a sandbox script
    "{localappdata}", "{userappdata}\\ClaudeUsageMonitor", "Global\\ClaudeUsageMonitor", "'ClaudeUsageMonitor'",
    '"ClaudeUsageMonitor"', "AppName=Claude Usage Monitor", '#define AppIdValue "Claude Usage Monitor"',
    "DefaultGroupName=Claude Usage Monitor", "DashboardTitle = 'Claude Usage Monitor'",
)


def find_iscc() -> Path | None:
    """Inno Setup's command-line compiler: $ISCC, the usual install folders, or the PATH."""
    candidates = [os.environ.get("ISCC"), r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                  r"C:\Program Files\Inno Setup 6\ISCC.exe", shutil.which("ISCC")]
    return next((Path(candidate) for candidate in candidates if candidate and Path(candidate).is_file()), None)


needs_iscc = pytest.mark.skipif(find_iscc() is None, reason="Inno Setup 6 (ISCC.exe) is not installed")
SANDBOX_RUNS = pytest.mark.skipif(
    os.environ.get("CUM_UI_TESTS") != "1" or os.environ.get("CUM_INSTALLER_E2E") != "1" or find_iscc() is None,
    reason="runs a renamed copy of the installer: set CUM_UI_TESTS=1 and CUM_INSTALLER_E2E=1 (needs Inno Setup 6)",
)


def make_dummy_dist(root: Path) -> Path:
    """A stand-in for the PyInstaller bundle: the installer only copies these files, it never starts the exe."""
    dist = root / "dist"
    (dist / "_internal").mkdir(parents=True)
    (dist / "ClaudeUsageMonitor.exe").write_bytes(b"MZ a stand-in: the installer only copies this file")
    (dist / "_internal" / "base_library.zip").write_bytes(b"PK stand-in")
    (dist / "_internal" / "python.dll").write_bytes(b"stand-in")
    return dist


def compile_iss(script: Path, out: Path, dist: Path, version: str) -> subprocess.CompletedProcess[str]:
    iscc = find_iscc()
    assert iscc is not None
    out.mkdir(parents=True, exist_ok=True)
    return subprocess.run([str(iscc), "/Q", f"/DAppVer={version}", f"/DDistDir={dist}", f"/O{out}", str(script)],
                          capture_output=True, text=True, timeout=300, cwd=str(out.parent), creationflags=NO_WINDOW)


@UI
@needs_iscc
def test_the_installer_script_compiles(tmp_path: Path) -> None:
    """ISCC type-checks the [Code] section, so a mistyped routine, an unknown function or a missing declaration
    fails here, not on release day. It only compiles: Setup is never started."""
    out = tmp_path / "out"
    done = compile_iss(ROOT / "installer.iss", out, make_dummy_dist(tmp_path), "9.9.9")
    assert done.returncode == 0, done.stdout + done.stderr
    assert [p.name for p in out.iterdir()] == ["ClaudeUsageMonitorSetup-v9.9.9.exe"]


@UI
def test_the_signature_check_passes_microsoft_corporation_and_nothing_else(tmp_path: Path) -> None:
    """The PowerShell of InstallWebView2, run on real files. It only reads signatures; nothing is installed."""
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    powershell = system32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"

    def check(path: Path) -> int:
        done = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", webview2_signature_script(str(path))], capture_output=True, timeout=180,
                              creationflags=NO_WINDOW)
        return done.returncode

    unsigned = tmp_path / "unsigned.exe"
    unsigned.write_bytes(b"MZ unsigned")
    assert check(unsigned) == 1                           # no signature
    assert check(tmp_path / "missing.exe") == 1           # no such file
    assert check(system32 / "notepad.exe") == 1           # a valid signature, but Windows' own, not "Microsoft Corporation"

    microsoft = next((Path(found) for pattern in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application\*\msedgewebview2.exe")
        for found in glob.glob(pattern)), None)
    if microsoft is None:
        pytest.skip("no program signed by Microsoft Corporation (Edge, WebView2) to test the passing case with")
    assert check(microsoft) == 0
    odd = tmp_path / "O'Brien & Co (x) [1]"               # a quote in the user's temp folder must not end the quoting
    odd.mkdir()
    shutil.copy(microsoft, odd / "MicrosoftEdgeWebview2Setup.exe")
    assert check(odd / "MicrosoftEdgeWebview2Setup.exe") == 0
    tampered = tmp_path / "tampered.exe"
    data = bytearray(microsoft.read_bytes())
    data[len(data) // 3] ^= 0xFF                          # one changed byte inside the signed part
    tampered.write_bytes(bytes(data))
    assert check(tampered) == 1


def sandbox_script(real: str, app: Path) -> str:
    """installer.iss with every identity renamed to the sandbox's. Each swap must match exactly once, so a change to the
    script that this does not know about fails loudly instead of leaving a real name in."""
    swaps = [
        ('#define AppIdValue "Claude Usage Monitor"', f'#define AppIdValue "{SANDBOX}"'),
        ("AppName=Claude Usage Monitor", f"AppName={SANDBOX}"),
        ("DefaultDirName={localappdata}\\ClaudeUsageMonitor", f"DefaultDirName={app}"),
        ("DefaultGroupName=Claude Usage Monitor", f"DefaultGroupName={SANDBOX}"),
        ("AppMutex=Global\\ClaudeUsageMonitor", f"AppMutex={SANDBOX_MUTEX}"),
        ("SetupIconFile=usage_monitor.ico", f"SetupIconFile={ROOT / 'usage_monitor.ico'}"),
        ('Name: "{group}\\Claude Usage Monitor"', f'Name: "{{group}}\\{SANDBOX}"'),
        ('Name: "{group}\\Uninstall Claude Usage Monitor"', f'Name: "{{group}}\\Uninstall {SANDBOX}"'),
        ('ValueName: "ClaudeUsageMonitor"', f'ValueName: "{SANDBOX_RUN_VALUE}"'),
        ("RunValue = 'ClaudeUsageMonitor';", f"RunValue = '{SANDBOX_RUN_VALUE}';"),
        ("DashboardTitle = 'Claude Usage Monitor';", f"DashboardTitle = '{SANDBOX} Window That Does Not Exist';"),
        ("Folder := ExpandConstant('{userappdata}\\ClaudeUsageMonitor');", f"Folder := ExpandConstant('{{userappdata}}\\{SANDBOX_DATA}');"),
        ("SameText(ExtractFileName(RemoveBackslash(Folder)), 'ClaudeUsageMonitor')",
         f"SameText(ExtractFileName(RemoveBackslash(Folder)), '{SANDBOX_DATA}')"),
    ]
    text = real
    for old, new in swaps:
        assert text.count(old) == 1, f"installer.iss changed: sandbox_script no longer finds {old!r} exactly once"
        text = text.replace(old, new)
    left = [name for name in REAL_IDENTITY if name in text]
    assert not left, f"the sandbox script still holds the real identity {left}: running it could replace an installed copy"
    assert text.count(f'AppIdValue "{SANDBOX}"') == 1 and "AppId={#AppIdValue}" in text
    return text


def v1_stand_in_script(app: Path) -> str:
    """What v1.1.5's installer leaves behind that matters here: an install with the same AppId (so the same uninstall key)
    and the same folder layout, and no Run value (v1's app wrote its own). Version 1.1.5."""
    return "\n".join([
        "[Setup]", f"AppId={SANDBOX}", f"AppName={SANDBOX}", "AppVersion=1.1.5", f"DefaultDirName={app}",
        f"DefaultGroupName={SANDBOX}", "DisableProgramGroupPage=yes", "PrivilegesRequired=lowest", "Compression=none",
        "OutputBaseFilename=v1like", "[Files]",
        'Source: "{#DistDir}\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs', ""])


class Sandbox:
    """A renamed copy of the installer, a stand-in v1 installer with the same AppId, and what a run leaves behind."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.app = root / "app"
        self.logs = root / "logs"
        self.logs.mkdir()
        dist = make_dummy_dist(root)
        scripts = root / "scripts"
        scripts.mkdir()
        (scripts / "v2.iss").write_text(sandbox_script((ROOT / "installer.iss").read_text(encoding="ascii"), self.app), encoding="ascii")
        (scripts / "v1.iss").write_text(v1_stand_in_script(self.app), encoding="ascii")
        self.v2 = self._build(scripts / "v2.iss", "v2", dist, "2.0.0", "ClaudeUsageMonitorSetup-v2.0.0.exe")
        self.v1 = self._build(scripts / "v1.iss", "v1", dist, "1.1.5", "v1like.exe")

    def _build(self, script: Path, name: str, dist: Path, version: str, exe: str) -> Path:
        done = compile_iss(script, self.root / "out" / name, dist, version)
        assert done.returncode == 0, done.stdout + done.stderr
        return self.root / "out" / name / exe

    # What a run does to the machine, always under the sandbox's own names.
    def run(self, setup: Path, switches: list[str], label: str) -> int:
        """Run a sandbox Setup with the command line v1.1.5's helper builds (one string, so /MERGETASKS="!startup" keeps its quotes)."""
        log = self.logs / (re.sub(r"\W+", "_", label) + ".log")
        done = subprocess.run(f'"{setup}" {" ".join(switches)} /LOG="{log}"', stdin=subprocess.DEVNULL, capture_output=True, timeout=240,
                              creationflags=NO_WINDOW)
        return done.returncode

    def run_value(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return str(winreg.QueryValueEx(key, SANDBOX_RUN_VALUE)[0])
        except FileNotFoundError:
            return None

    def set_run_value(self, value: str | None) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if value is None:
                try:
                    winreg.DeleteValue(key, SANDBOX_RUN_VALUE)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(key, SANDBOX_RUN_VALUE, 0, winreg.REG_SZ, value)

    def display_version(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{UNINSTALL_ROOT}\{SANDBOX}_is1") as key:
                return str(winreg.QueryValueEx(key, "DisplayVersion")[0])
        except FileNotFoundError:
            return None

    def files(self) -> list[tuple[str, int]]:
        """What is in the install folder (the uninstaller aside), by relative path and size."""
        return sorted((p.relative_to(self.app).as_posix(), p.stat().st_size)
                      for p in self.app.rglob("*") if p.is_file() and not p.name.startswith("unins"))

    def reset(self, previous: str | None, entry: str | None) -> None:
        """Start from: nothing installed, a v1-style install or a v2 install, with the Run value as given."""
        self.cleanup()
        if previous is not None:
            code = self.run(self.v1 if previous == "v1" else self.v2, V1_SWITCHES, "prepare " + previous)
            assert code == 0, f"could not prepare a {previous} install (exit code {code})"
        self.set_run_value(entry)

    def cleanup(self) -> None:
        uninstaller = self.app / "unins000.exe"
        if uninstaller.is_file():
            subprocess.run(f'"{uninstaller}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART', stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=240, creationflags=NO_WINDOW)
            deadline = time.monotonic() + 90
            while self.display_version() is not None and time.monotonic() < deadline:
                time.sleep(0.3)
        self.set_run_value(None)
        shutil.rmtree(self.app, ignore_errors=True)


def real_install_state() -> dict[str, Any]:
    """What the owner's real install looks like: its Run value, uninstall key and program file. Read-only, so a run
    that touched it could be told apart afterwards."""
    import winreg

    state: dict[str, Any] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            state["run"] = winreg.QueryValueEx(key, "ClaudeUsageMonitor")[0]
    except FileNotFoundError:
        state["run"] = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{UNINSTALL_ROOT}\Claude Usage Monitor_is1") as key:
            state["version"] = winreg.QueryValueEx(key, "DisplayVersion")[0]
    except FileNotFoundError:
        state["version"] = None
    exe = Path(os.environ.get("LOCALAPPDATA", "")) / "ClaudeUsageMonitor" / "ClaudeUsageMonitor.exe"
    state["exe"] = (exe.stat().st_size, exe.stat().st_mtime_ns) if exe.is_file() else None
    return state


@UI
@needs_iscc
def test_the_sandboxed_installers_build_and_hold_none_of_the_real_names(tmp_path: Path) -> None:
    """The safety net of the sandbox runs below, checked on its own: both installers compile (nothing is run) and the
    renamed script carries the sandbox's names and none of the real ones."""
    box = Sandbox(tmp_path)
    assert box.v1.is_file() and box.v2.is_file()
    text = (tmp_path / "scripts" / "v2.iss").read_text(encoding="ascii")
    for name in (SANDBOX_RUN_VALUE, SANDBOX_MUTEX, SANDBOX_DATA, f"DefaultDirName={box.app}"):
        assert name in text, name
    assert not any(name in text for name in REAL_IDENTITY)
    assert str(box.app).startswith(str(tmp_path))          # the install folder is inside the test's own folder
    assert box.files() == []                               # nothing has been installed: this test never starts Setup
    assert set(real_install_state()) == {"run", "version", "exe"}   # the read-only snapshot of the real install works


@SANDBOX_RUNS
def test_sandboxed_silent_upgrades_keep_each_users_start_with_windows_choice(tmp_path: Path) -> None:
    """Runs the renamed installer the way each updater runs it, over each kind of existing install, and reads the Run value."""
    box = Sandbox(tmp_path)
    exe = f'"{box.app / "ClaudeUsageMonitor.exe"}"'
    v1_two_part = exe + r' "C:\Users\Someone\AppData\Local\Temp\_MEI123456\main.py"'    # what v1's own switch wrote
    cases = [
        # label,                                  existing install, Run value before, switches,   Run value after
        ("v1 updater, Start with Windows off",    "v1", None,        V1_SWITCHES, None),
        ("v1 updater, on (v1's two parts)",       "v1", v1_two_part, V1_SWITCHES, exe),
        ("v2 updater, on",                        "v2", exe,         V2_SWITCHES, exe),
        ("v2 updater, off",                       "v2", None,        V2_SWITCHES, None),
        ("first install, silent",                 None, None,        V1_SWITCHES, exe),
    ]
    before = real_install_state()
    try:
        for label, previous, entry, switches, expected in cases:
            box.reset(previous, entry)
            assert box.run(box.v2, switches, label) == 0, label
            assert (box.run_value() or "").lower() == (expected or "").lower(), f"{label}: the Run value is {box.run_value()!r}"
            assert box.display_version() == "2.0.0", label
            assert not (box.app / "_internal.probe").exists(), label
    finally:
        box.cleanup()
    assert real_install_state() == before


@SANDBOX_RUNS
def test_a_sandboxed_update_with_a_file_held_open_stops_and_changes_nothing(tmp_path: Path) -> None:
    """A virus scanner, a backup agent or an indexer can hold a file open without delete sharing (Python's open() does
    the same). Without the probe, [InstallDelete] would stop half way and leave no program. With it, Setup exits with
    an error and the old install is whole; once the file is free the same Setup succeeds."""
    box = Sandbox(tmp_path)
    before = real_install_state()
    try:
        box.reset("v1", None)
        listing = box.files()
        with open(box.app / "_internal" / "base_library.zip", "rb"):
            assert box.run(box.v2, V1_SWITCHES, "file held open") != 0
        log = (box.logs / "file_held_open.log").read_text(encoding="utf-8", errors="replace")
        assert "stopping before anything is changed" in log, "Setup stopped for another reason"
        assert box.files() == listing, "the update changed the install although it stopped"
        assert box.display_version() == "1.1.5"
        assert not (box.app / "_internal.probe").exists()
        assert box.run(box.v2, V1_SWITCHES, "after the file was released") == 0
        assert box.display_version() == "2.0.0"
    finally:
        box.cleanup()
    assert real_install_state() == before


# ── The dashboard's Install update button ─────────────────────────────────────

def test_the_install_update_button_reports_what_the_monitor_actually_did() -> None:
    """/api/update/apply answers {"started", "mode"}: "browser" (the release page was opened), "installer" (the checked
    silent update started, and the monitor restarts only if the download passes the check) or "none". The status line
    must follow that, and must not promise an install that may never happen."""
    import inspect

    from usage_monitor import app as app_mod

    script = (ROOT / "usage_monitor" / "webui" / "app.js").read_text(encoding="utf-8")
    # The code that answers the apply call: from the request to wherever the skip code starts. (The Settings button and
    # the Overview banner's Update now both end up there.)
    start = script.index('api("POST", "/api/update/apply")')
    handler = script[start:min(i for i in (script.find(marker, start) for marker in (
        "function skipUpdate", 'action === "skip-update"', '"/api/update/skip"')) if i > 0)]
    answered = set(re.findall(r'"mode": "(\w+)"', inspect.getsource(app_mod.MonitorApp._apply_update)))
    assert answered == {"none", "installer", "browser"}
    for mode in ("browser", "installer"):
        assert f'mode === "{mode}"' in handler, mode
    assert "THE UPDATE WILL INSTALL WHEN THE MONITOR RESTARTS" not in script
    assert re.search(r'mode === "browser"\)\s*\{\s*text = "[^"]*RELEASE PAGE', handler)
    assert re.search(r'mode === "installer"\)\s*\{\s*text = "[^"]*IF IT CHECKS OUT[^"]*RESTARTS', handler)
    # One sentence each: the Gates theme writes sentence case and capitalises only the first letter of a string, so a
    # second sentence would come out as "... the update. if it checks out ...".
    texts = re.findall(r'text = "([^"]+)";', handler)
    assert len(texts) == 3 and all(". " not in text for text in texts), texts


def test_the_dashboard_reads_only_what_the_server_sends_about_an_update() -> None:
    """The Overview banner reads /api/state's ``update`` block in pendingUpdate(). A key the page reads and the server
    never sends would be a banner that never shows, or an Update now that always says it opens the release page."""
    from usage_monitor.app import SmokeTest

    script = (ROOT / "usage_monitor" / "webui" / "app.js").read_text(encoding="utf-8")
    assert "function pendingUpdate" in script, "the banner's reader moved: point this test at the new one"
    start = script.index("function pendingUpdate")
    body = script[start:script.index("function updateBanner", start)]
    block_keys = set(re.findall(r"\bu\.([a-z_]+)", body))
    info_keys = set(re.findall(r"\binfo\.([a-z_]+)", body))
    assert {"available", "version"} <= block_keys, block_keys
    assert block_keys <= set(SmokeTest.UPDATE_KEYS), f"the page reads {block_keys - set(SmokeTest.UPDATE_KEYS)}, the server sends none of it"
    # And inside ``info`` (the check's own answer, plus the two keys the app adds), only keys that exist.
    assert info_keys <= {"version", "tag", "url", "notes", "asset_url", "asset_name", "asset_digest", "asset_size",
                         "can_self_update"}, info_keys


# ── ClaudeUsageMonitor.spec: what the bundle must hold ────────────────────────

def _collect_submodules(package: str, filter: Callable[[str], bool] = lambda name: True, **_kw: Any) -> list[str]:
    names = [package]
    base = ROOT / package.replace(".", "/")
    for path in sorted(base.rglob("*.py")):
        relative = path.relative_to(ROOT).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts.pop()
        names.append(".".join(parts))
    return [name for name in dict.fromkeys(names) if filter(name)]


def _collect_all(_name: str) -> tuple[list[Any], list[Any], list[str]]:
    return (
        [("/fake-site-packages/webview/js", "webview/js")],
        [("/fake-site-packages/webview/lib/WebView2Loader.dll", "webview/lib")],
        ["webview.platforms.winforms", "webview.platforms.edgechromium"],
    )


class _Block:
    """Stands in for Analysis, PYZ, EXE and COLLECT: remembers its arguments."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args, self.kw = args, kwargs
        self.pure, self.scripts, self.binaries, self.datas = ["pure"], ["scripts"], ["binaries"], ["datas"]


@pytest.fixture(scope="module")
def spec() -> types.SimpleNamespace:
    """The spec, run against stand-ins for PyInstaller: what it hands to Analysis, EXE and COLLECT."""
    with pytest.MonkeyPatch.context() as mp:
        for name in ("PyInstaller", "PyInstaller.utils"):
            mp.setitem(sys.modules, name, types.ModuleType(name))
        hooks = types.ModuleType("PyInstaller.utils.hooks")
        hooks.collect_all = _collect_all  # type: ignore[attr-defined]
        hooks.collect_submodules = _collect_submodules  # type: ignore[attr-defined]
        mp.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
        namespace: dict[str, Any] = {"SPECPATH": str(ROOT), "Analysis": _Block, "PYZ": _Block, "EXE": _Block, "COLLECT": _Block}
        exec(compile(SPEC_TEXT, "ClaudeUsageMonitor.spec", "exec"), namespace)  # noqa: S102
    return types.SimpleNamespace(analysis=namespace["a"], exe=namespace["exe"], coll=namespace["coll"])


def bundled_files(datas: list[tuple[str, str]]) -> dict[str, Path]:
    """Bundle path -> source file for every (source, destination) pair of the repository itself."""
    bundled: dict[str, Path] = {}
    for source, destination in datas:
        for match in map(Path, glob.glob(source)):      # the stand-in pywebview data matches nothing
            if match.is_dir():
                for file in sorted(match.rglob("*")):
                    if file.is_file():
                        bundled[(Path(destination) / file.relative_to(match)).as_posix()] = file
            else:
                bundled[(Path(destination) / match.name).as_posix()] = match
    return bundled


def runtime_resources() -> list[Path]:
    """Every file in the package that is not Python: what the app reads at run time through resource_dir()."""
    package = ROOT / "usage_monitor"
    return sorted(p for p in package.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".py", ".pyc"))


def test_the_bundle_lands_every_resource_where_the_app_looks_for_it(spec: types.SimpleNamespace) -> None:
    """paths.resource_dir() is the bundle root, so a resource must sit at the same relative path as in the repository."""
    bundled = bundled_files(spec.analysis.kw["datas"])
    assert runtime_resources(), "the package has no resource files?"
    for resource in runtime_resources():
        relative = resource.relative_to(ROOT).as_posix()
        assert bundled.get(relative) == resource, f"{relative} is not bundled at its own path"
    assert bundled["VERSION"] == ROOT / "VERSION"
    assert bundled["usage_monitor.ico"] == ROOT / "usage_monitor.ico"


def test_the_bundle_holds_the_dashboard_the_fonts_the_themes_and_the_prices(spec: types.SimpleNamespace) -> None:
    bundled = set(bundled_files(spec.analysis.kw["datas"]))
    expected = {
        "usage_monitor/webui/index.html", "usage_monitor/webui/app.js", "usage_monitor/webui/charts.js",
        "usage_monitor/data/pricing.json",
        *(f"usage_monitor/themes/{theme}.json" for theme in ("nostromo", "night_city", "gates")),
        *(f"usage_monitor/webui/themes/{theme}.{ext}" for theme in ("nostromo", "night_city", "gates") for ext in ("css", "js")),
    }
    assert expected <= bundled
    assert any(name.startswith("usage_monitor/webui/fonts/") and name.endswith(".ttf") for name in bundled)
    assert any(name.endswith("OFL.txt") for name in bundled)    # the font licences travel with the fonts


def test_nothing_dev_only_is_bundled(spec: types.SimpleNamespace) -> None:
    sources = [file.relative_to(ROOT).parts[0] for file in bundled_files(spec.analysis.kw["datas"]).values()]
    assert set(sources) <= {"usage_monitor", "VERSION", "usage_monitor.ico"}
    hidden = set(spec.analysis.kw["hiddenimports"])
    assert "usage_monitor.ui.devserver" not in hidden and "usage_monitor.ui.devserver" in spec.analysis.kw["excludes"]
    assert {"tkinter", "pytest", "numpy"} <= set(spec.analysis.kw["excludes"])   # numpy: a dev machine's copy must not ride along


def test_the_app_never_imports_the_dev_server() -> None:
    """The spec leaves devserver.py out of the bundle, so nothing that ships may import it."""
    offenders = []
    for path in [ROOT / "main.py", ROOT / "version.py", ROOT / "update_check.py", *(ROOT / "usage_monitor").rglob("*.py")]:
        if path.name == "devserver.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "") + "." + alias.name for alias in node.names]
            elif isinstance(node, ast.Call):   # importlib.import_module("...") and __import__("...")
                called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if called in ("import_module", "__import__") and node.args and isinstance(node.args[0], ast.Constant):
                    names = [str(node.args[0].value)]
            if any(name.split(".")[-1] == "devserver" or ".devserver." in name for name in names):
                offenders.append(f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', '?')}")
    assert offenders == []


def test_every_module_of_the_package_is_a_hidden_import(spec: types.SimpleNamespace) -> None:
    """The ui and render packages import their modules by name, which PyInstaller's scan does not see."""
    hidden = set(spec.analysis.kw["hiddenimports"])
    for module in ("usage_monitor.ui.server", "usage_monitor.ui.dashboard", "usage_monitor.ui.tray",
                   "usage_monitor.render.skins.nostromo", "usage_monitor.render.skins.night_city",
                   "usage_monitor.render.skins.gates", "usage_monitor.themes.colour"):
        assert module in hidden, module
    assert {"webview.platforms.winforms", "webview.platforms.edgechromium"} <= hidden   # pywebview's own, from collect_all
    assert ("/fake-site-packages/webview/js", "webview/js") in spec.analysis.kw["datas"]


def test_the_exe_is_windowed_onedir_and_unpacked(spec: types.SimpleNamespace) -> None:
    kw = spec.exe.kw
    assert kw["console"] is False and kw["upx"] is False and kw["exclude_binaries"] is True
    assert kw["name"] == "ClaudeUsageMonitor" and spec.coll.kw["name"] == "ClaudeUsageMonitor" and spec.coll.kw["upx"] is False
    assert Path(kw["icon"]) == ROOT / "usage_monitor.ico" and Path(kw["icon"]).is_file()


# ── build.bat ─────────────────────────────────────────────────────────────────

def test_build_bat_is_plain_ascii_and_builds_what_the_workflow_builds() -> None:
    """cmd reads a batch file in the console code page, so a stray dash or accent garbles a message or a path."""
    text = (ROOT / "build.bat").read_bytes()
    assert all(byte < 128 for byte in text)
    script = text.decode("ascii")
    assert "ClaudeUsageMonitor.spec" in script and "installer.iss" in script and "/DAppVer=%APPVER%" in script
    assert "--print-version" in script                      # the frozen exe must report the version it was built with
    assert "set /p APPVER=<VERSION" in script               # the same VERSION file release.yml reads
    assert "Toolchain" in script and "importlib.metadata" in script   # CI does not pin its dependencies: the local build says what it used


# ── docs/RELEASING.md ─────────────────────────────────────────────────────────

def test_releasing_md_describes_the_updater_and_the_release_workflow_as_they_are() -> None:
    """The release checklist is only useful while it matches what the workflow and the updater really do."""
    releasing = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert uc.SILENT_ARGS in releasing                                    # the switches update_check.py passes
    assert "gh release view" in workflow and "git tag" not in workflow    # the workflow looks for a release, not a tag...
    assert "already exists it stops" in releasing and "not the tag" in releasing   # ...and the doc says so
    assert "python-version: '3.14'" in workflow and "Python 3.14" in releasing
    for step in ("pip install -r requirements.txt pyinstaller -c constraints-build.txt", "pyinstaller --noconfirm ClaudeUsageMonitor.spec"):
        assert step in workflow, step
    constraints = (ROOT / "constraints-build.txt").read_text(encoding="utf-8")
    for name in ("Pillow", "pywebview", "pyinstaller", "pythonnet"):
        assert re.search(rf"^{name}==\d", constraints, re.M), name      # the tested versions, exactly
    assert "-c constraints-build.txt" in (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "--print-version" in workflow and "--print-version" in releasing
    assert "pyinstaller --noconfirm ClaudeUsageMonitor.spec" in releasing and "ISCC /DAppVer=<VERSION> installer.iss" in releasing
    assert "workflow_dispatch" in workflow and "workflow_dispatch" in releasing
    # v1.1.5's updater, which the first release depends on: its helper is what the checklist reproduces.
    for line in ("ping 127.0.0.1 -n 4 >nul", "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART", "ping 127.0.0.1 -n 8 >nul"):
        assert line in releasing, line
    assert "/MERGETASKS" not in releasing.split("ping 127.0.0.1 -n 4 >nul")[1].split("ping 127.0.0.1 -n 8 >nul")[0]


def _workflow_step(workflow: str, name: str) -> str:
    """The text of the step called ``name`` in a workflow file (up to the next step)."""
    start = workflow.index(f"- name: {name}")
    following = re.search(r"\n      - ", workflow[start + 1:])
    return workflow[start:start + 1 + following.start()] if following else workflow[start:]


def test_the_release_workflow_can_be_rehearsed_gates_on_tests_and_smoke_and_only_ever_drafts() -> None:
    """The first run of this pipeline used to be the live release that v1.1.5's updater installs silently on every v1
    install: a hand-run on a branch was a no-op (VERSION is already released there) or a public release, the smoke
    step could fail without stopping anything, no tests ran, and nothing had a time limit."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    releasing = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    # A rehearsal: a hand-run input that defaults to off, which skips the "already exists" stop and publishes nothing.
    assert re.search(r"publish:\s*\n(?:\s+.*\n)*?\s+type: boolean\s*\n\s+default: false", workflow)
    exists = _workflow_step(workflow, "Does this release already exist?")
    assert "github.event_name == 'push' || inputs.publish" in exists
    rehearsal_branch = exists.split('[ "$PUBLISH" != "true" ]')[1].split("elif")[0]
    assert "skip=false" in rehearsal_branch
    kept = _workflow_step(workflow, "Keep the installer (rehearsal)")
    assert "steps.exists.outputs.publish != 'true'" in kept and "actions/upload-artifact" in kept
    # What publishes: a draft only, pinned to the commit that was built, and never promoted by the workflow itself.
    release = _workflow_step(workflow, "Create the draft release")
    assert "steps.exists.outputs.publish == 'true'" in release
    creates = [line for line in release.splitlines() if "gh release create" in line]
    assert len(creates) == 2 and all("--draft" in line and '--target "$GITHUB_SHA"' in line for line in creates)
    verbs = set(re.findall(r"^\s*(?:elif |if )?gh release (\w+)", workflow, re.M))
    assert verbs == {"view", "create"}                      # the workflow looks and creates; publishing a draft is yours
    # The tests run in the release job, and the smoke test gates it.
    assert "run: python -m pytest" in workflow and "tests/test_webui.py tests/test_motion.py" in workflow
    assert "continue-on-error" not in workflow
    smoke = _workflow_step(workflow, "Smoke-test the built exe")
    assert "throw 'the frozen smoke test failed'" in smoke and "'--smoke-test', '45'" in smoke
    assert "if: always()" in _workflow_step(workflow, "Keep the smoke report and logs")
    # Nothing can hang for the default six hours, and two runs cannot race to create the same release.
    assert re.search(r"^    timeout-minutes: \d+$", workflow, re.M)
    for name in ("Install dependencies", "Run the tests", "Build the exe", "Smoke-test the built exe", "Install Inno Setup",
                 "Build the installer"):
        assert re.search(r"timeout-minutes: \d+", _workflow_step(workflow, name)), name
    assert "concurrency:" in workflow and "cancel-in-progress: false" in workflow
    # And the checklist says how to use all of it.
    assert "rehearsal" in releasing.lower() and "publish left off" in releasing
    assert "--draft" in releasing and "gh release edit" in releasing and "Before you publish the draft" in releasing


# ── No test opens a console window ────────────────────────────────────────────
#
# A console program started by a process that has no console of its own gets a new terminal window (on this PC's
# Windows Terminal, a window per process). pytest has no console when an editor, a task runner or an agent runs it,
# so every test that starts ping, cmd, git, node or python has to say CREATE_NO_WINDOW. Two things keep it that way:
#   * tests/console_guard.py, installed for the whole run by tests/conftest.py: every subprocess.Popen refuses to start
#     without the flag, whoever calls it (a test, a helper, the app code a test runs). run, call, check_output, os.popen and
#     asyncio's subprocesses all go through Popen.
#   * the scan below, which reads every file under tests/ for the starts the wrapper cannot see (os.system, os.startfile,
#     multiprocessing, ShellExecute, os.spawn*) and for a missing flag it can name before a test ever runs. It follows
#     `import subprocess as sp` and `from subprocess import run as go`.
# The scan's own cases and the guard's are tested after it.

PROCESS_CALLS = {("subprocess", name) for name in ("run", "Popen", "call", "check_call", "check_output")} | {
    ("asyncio", "create_subprocess_exec"), ("asyncio", "create_subprocess_shell")}        # these take creationflags=
# Starts with no flag to pass (cmd.exe, a spawned interpreter): always an offender.
SHELL_CALLS = {("os", "system"), ("os", "popen"), ("os", "startfile"), ("subprocess", "getoutput"),
               ("subprocess", "getstatusoutput"), ("multiprocessing", "Process"), ("multiprocessing", "Pool"),
               ("concurrent.futures", "ProcessPoolExecutor")}
SHELL_FUNCTIONS = {"ShellExecuteW", "ShellExecuteExW", "ShellExecuteA", "WinExec", "CreateProcessW", "CreateProcessA"}
SPAWNERS = ("spawn", "exec")                                                              # os.spawnv, os.execv, ...


def _imports(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """(modules, objects): the local name of every ``import x [as y]`` -> the module, and of every ``from m import n [as k]``
    -> "m.n"."""
    modules: dict[str, str] = {}
    objects: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    modules[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                objects[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return modules, objects


def _called(func: ast.expr, modules: dict[str, str], objects: dict[str, str]) -> tuple[str, str] | None:
    """What a call really calls, as (module path, name), with the imports of the file resolved: ``sp.run`` is
    ("subprocess", "run") after ``import subprocess as sp``, ``go`` is too after ``from subprocess import run as go``."""
    chain: list[str] = []
    node = func
    while isinstance(node, ast.Attribute):
        chain.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    root = modules.get(node.id) or objects.get(node.id)
    if root is None:
        return None                                   # a local name (self.run, process.start): not an import
    full = ".".join([root, *reversed(chain)])
    module, _, name = full.rpartition(".")
    return module, name


def process_calls_without_the_flag(path: Path) -> list[str]:
    """``file:line`` of every call in ``path`` that starts a program and does not pass creationflags=...NO_WINDOW."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules, objects = _imports(tree)
    sites: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _called(node.func, modules, objects)
        if target is None:
            continue
        module, name = target
        if target in SHELL_CALLS or name in SHELL_FUNCTIONS or (module == "os" and name.startswith(SPAWNERS)):
            sites.append(f"{path.name}:{node.lineno}")
        elif target in PROCESS_CALLS:
            flags = [keyword.value for keyword in node.keywords if keyword.arg == "creationflags"]
            if not flags or "NO_WINDOW" not in ast.unparse(flags[0]):
                sites.append(f"{path.name}:{node.lineno}")
    return sites


def test_no_test_starts_a_console_program_without_the_flag() -> None:
    """Every file under tests/, not only test_*.py: a conftest.py or a helper module starts processes too."""
    files = sorted(path for path in (ROOT / "tests").rglob("*.py") if "__pycache__" not in path.parts)
    assert {"conftest.py", "console_guard.py", "test_update_check.py"} <= {path.name for path in files}
    offenders = [site for path in files for site in process_calls_without_the_flag(path)]
    assert offenders == [], "these calls would open a console window; add creationflags=NO_WINDOW: " + ", ".join(offenders)


@pytest.mark.parametrize(("source", "clean"), [
    ("subprocess.run(['x'], creationflags=NO_WINDOW)", True),
    ("subprocess.Popen(['x'], stdout=1, creationflags=subprocess.CREATE_NO_WINDOW | 0x200)", True),
    ("subprocess.check_output(['x'], creationflags=_NO_WINDOW)", True),
    ("subprocess.run(['x'])", False),
    ("subprocess.Popen(['x'], stdout=subprocess.PIPE)", False),
    ("subprocess.call(['x'], creationflags=0)", False),                     # a flag that is not the one
    ("subprocess.check_call(['x'], **options)", False),                      # a flag nobody can see
    ("os.system('x')", False),
    ("os.popen('x')", False),
    ("os.startfile('x')", False),
    ("subprocess.getoutput('x')", False),                                    # runs through cmd.exe, no flag to pass
    ("monkeypatch.setattr(subprocess, 'Popen', fake)", True),                # naming Popen is not starting one
    ("process = subprocess.CompletedProcess([], 0)", True),
    # a start the name-pattern scan used to miss: imports renamed, or from-imports
    ("import subprocess as sp\nsp.run(['x'])", False),
    ("import subprocess as sp\nsp.run(['x'], creationflags=sp.CREATE_NO_WINDOW)", True),
    ("from subprocess import run\nrun(['x'])", False),
    ("from subprocess import run as go\ngo(['x'])", False),
    ("from subprocess import run as go\ngo(['x'], creationflags=NO_WINDOW)", True),
    ("from subprocess import Popen, PIPE\nPopen(['x'], stdout=PIPE)", False),
    ("from subprocess import Popen\nPopen(['x'], creationflags=NO_WINDOW)", True),
    ("import os as operating_system\noperating_system.system('x')", False),
    ("from os import system\nsystem('x')", False),
    ("from os import popen as shell\nshell('x')", False),
    ("import asyncio\nasyncio.create_subprocess_exec('x')", False),
    ("import asyncio\nasyncio.create_subprocess_exec('x', creationflags=NO_WINDOW)", True),
    ("import asyncio as aio\naio.create_subprocess_shell('x')", False),
    ("import multiprocessing\nmultiprocessing.Process(target=f)", False),
    ("import multiprocessing as mp\nmp.Pool(2)", False),
    ("from concurrent.futures import ProcessPoolExecutor\nProcessPoolExecutor(2)", False),
    ("import os\nos.spawnv(os.P_NOWAIT, 'x', ['x'])", False),
    ("import os\nos.execv('x', ['x'])", False),
    ("import ctypes\nctypes.windll.shell32.ShellExecuteW(None, 'open', 'x', None, None, 1)", False),
    ("from ctypes import windll\nwindll.shell32.ShellExecuteW(None, 'open', 'x', None, None, 1)", False),
    ("from usage_monitor.ui import win32\nwin32.ShellExecuteW(None, 'open', 'x', None, None, 1)", False),
    # the names of another object are not the standard library's
    ("process.run(['x'])\nself.system('x')\nmy_os.popen('x')", True),
    ("import tempfile\ntempfile.mkdtemp()\nimport shutil\nshutil.which('x')", True),
])
def test_the_console_window_check_notices_a_call_without_the_flag(tmp_path: Path, source: str, clean: bool) -> None:
    sample = tmp_path / "test_sample.py"
    sample.write_text(f"import os, subprocess\n{source}\n", encoding="utf-8")
    assert (process_calls_without_the_flag(sample) == []) is clean


def test_the_scan_reads_a_helper_module_and_a_conftest_like_any_test(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text("import subprocess as sp\n\ndef pytest_configure():\n    sp.run(['git', 'status'])\n",
                                          encoding="utf-8")
    assert process_calls_without_the_flag(tmp_path / "conftest.py") == ["conftest.py:4"]


# The run-time half. console_guard is installed for the whole run (tests/conftest.py); these show what it does without
# starting a process: a start without the flag is refused before anything is created, a start with it reaches Popen.


def start(name: str, *args: Any, **kwargs: Any) -> Any:
    """``subprocess.<name>(...)`` through getattr. The scan above cannot see a call made this way, which is what the run-time guard
    is for: the starts in this section are refused (nothing is created) or handed to a recorder, never to a real Popen."""
    return getattr(subprocess, name)(*args, **kwargs)


def test_the_run_time_guard_is_installed_for_the_whole_run() -> None:
    import console_guard
    assert console_guard._installed and subprocess.Popen.__init__ is console_guard._guarded_init


@pytest.mark.parametrize(("name", "args", "kwargs"), [
    ("Popen", (["git", "status"],), {}),
    ("run", (["git", "status"],), {}),
    ("run", (["git", "status"],), {"creationflags": 0}),
    ("run", (["git", "status"],), {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}),     # a flag, not the one
    ("check_output", ("echo x",), {"shell": True}),
    ("call", (["git", "status"],), {}),
])
def test_a_start_without_the_flag_is_refused_before_the_process_exists(name: str, args: tuple[Any, ...],
                                                                       kwargs: dict[str, Any]) -> None:
    import console_guard
    with console_guard.refusals() as refused:
        with pytest.raises(AssertionError, match="console window"):
            start(name, *args, **kwargs)
    assert len(refused) == 1 and "without creationflags=CREATE_NO_WINDOW" in refused[0]
    assert "test_update_check.py:" in refused[0]                            # it names the test line, not the subprocess module


def test_a_start_with_the_flag_reaches_popen(monkeypatch: pytest.MonkeyPatch) -> None:
    import console_guard
    reached: list[dict[str, Any]] = []
    monkeypatch.setattr(console_guard, "_original_init", lambda self, *args, **kwargs: reached.append(kwargs))
    start("Popen", ["git", "status"], creationflags=NO_WINDOW)
    start("Popen", ["git", "status"], creationflags=NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    assert [call["creationflags"] for call in reached] == [NO_WINDOW, NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP]


def test_the_flag_counts_when_it_is_passed_by_position(monkeypatch: pytest.MonkeyPatch) -> None:
    """Popen's 14th parameter: (args, bufsize, executable, stdin, stdout, stderr, preexec_fn, close_fds, shell, cwd, env,
    universal_newlines, startupinfo, creationflags)."""
    import console_guard
    reached: list[tuple[Any, ...]] = []
    monkeypatch.setattr(console_guard, "_original_init", lambda self, *args, **kwargs: reached.append(args))
    start("Popen", ["git"], -1, None, None, None, None, None, True, False, None, None, None, None, NO_WINDOW)
    assert len(reached) == 1
    with console_guard.refusals() as refused:
        with pytest.raises(AssertionError):
            start("Popen", ["git"], -1, None, None, None, None, None, True, False, None, None, None, None, 0)
    assert len(refused) == 1


def test_a_refusal_that_the_code_under_test_swallows_still_fails_the_test() -> None:
    """The launcher in the app treats any error as "could not start". The refusal is recorded either way, and conftest.py calls
    fail_if_refused_since() after every test, so a start that was swallowed still fails the test that made it."""
    import console_guard
    mark = len(console_guard.violations)
    try:
        try:
            start("Popen", ["git", "status"])
        except Exception:
            pass                                                              # what a broad except in the app does
        with pytest.raises(pytest.fail.Exception, match="CREATE_NO_WINDOW"):
            console_guard.fail_if_refused_since(mark)
        console_guard.fail_if_refused_since(len(console_guard.violations))   # nothing refused since: nothing to fail
    finally:
        del console_guard.violations[mark:]                                    # this test refused on purpose


def test_the_guard_leaves_an_argument_error_to_popen() -> None:
    """Popen reports a bad call itself (TypeError); the guard does not turn it into a refusal."""
    import console_guard
    with console_guard.refusals() as refused:
        with pytest.raises(TypeError):
            start("Popen", ["git"], no_such_option=1)
    assert refused == []
