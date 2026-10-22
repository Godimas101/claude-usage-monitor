"""Tests for usage_monitor.paths, and for usage_monitor.log (which has no test module of its own).

Every location is redirected into tmp_path, so the real profile and %APPDATA% are never touched.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from usage_monitor import log, paths


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Send every location into tmp_path. Returns the fake home folder."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.delenv("CUM_APPDATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


@pytest.fixture
def logging_sandbox() -> Iterator[logging.Logger]:
    """Yield the usage_monitor logger, then remove the handlers and level a test left behind."""
    logger = logging.getLogger(log.ROOT_NAME)
    yield logger
    for handler in _managed(logger):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.NOTSET)


def _managed(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if isinstance(h, (RotatingFileHandler, logging.NullHandler))]


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ── paths.py ──────────────────────────────────────────────────────────────────

def test_app_data_dir_uses_appdata_and_creates_it(tmp_path: Path) -> None:
    folder = paths.app_data_dir()
    assert folder == tmp_path / "roaming" / "ClaudeUsageMonitor"
    assert folder.is_dir()


def test_cum_appdata_dir_replaces_the_app_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "override"
    monkeypatch.setenv("CUM_APPDATA_DIR", str(target))
    assert paths.app_data_dir() == target
    assert target.is_dir()


def test_app_data_dir_returns_its_path_when_it_cannot_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("a file, not a folder", encoding="utf-8")
    monkeypatch.setenv("CUM_APPDATA_DIR", str(blocker / "sub"))
    with caplog.at_level(logging.WARNING, logger="usage_monitor"):
        assert paths.app_data_dir() == blocker / "sub"
    assert any("cannot create app data folder" in r.getMessage() for r in caplog.records)


def test_file_locations_sit_in_the_documented_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-cfg"))
    monkeypatch.setenv("CUM_APPDATA_DIR", str(tmp_path / "app"))
    assert paths.credentials_path() == tmp_path / "claude-cfg" / ".credentials.json"
    assert paths.settings_path() == tmp_path / "app" / "settings.json"
    assert paths.db_path() == tmp_path / "app" / "usage.db"
    assert paths.log_path() == tmp_path / "app" / "monitor.log"


def test_claude_dir_defaults_to_dot_claude_under_home(isolated_env: Path) -> None:
    assert paths.claude_dir() == isolated_env / ".claude"


def test_claude_config_dir_overrides_claude_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert paths.claude_dir() == tmp_path / "cfg"


def test_only_app_data_dir_creates_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("CUM_APPDATA_DIR", str(tmp_path / "app"))
    paths.claude_dir()
    paths.credentials_path()
    paths.projects_dirs()
    assert not (tmp_path / "cfg").exists()
    assert not (tmp_path / "app").exists()
    assert paths.db_path().parent.is_dir()


def test_projects_dirs_lists_only_roots_that_exist(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert paths.projects_dirs() == []
    cfg_projects = tmp_path / "cfg" / "projects"
    xdg_projects = isolated_env / ".config" / "claude" / "projects"
    cfg_projects.mkdir(parents=True)
    xdg_projects.mkdir(parents=True)
    assert paths.projects_dirs() == [cfg_projects, xdg_projects]


def test_projects_dirs_never_lists_one_root_twice(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = isolated_env / ".config" / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    (config / "projects").mkdir(parents=True)
    assert paths.projects_dirs() == [config / "projects"]


def test_resource_dir_is_the_repo_root_when_run_from_source() -> None:
    assert paths.resource_dir() == Path(paths.__file__).resolve().parents[1]
    assert (paths.resource_dir() / "usage_monitor" / "paths.py").is_file()


def test_resource_dir_follows_pyinstaller_when_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.resource_dir() == tmp_path


def test_resource_dir_falls_back_to_the_executable_folder_when_frozen_without_meipass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert paths.resource_dir() == Path(sys.executable).resolve().parent


# ── log.py ────────────────────────────────────────────────────────────────────

def test_get_logger_nests_names_under_usage_monitor() -> None:
    assert log.get_logger("usage_monitor.data.store").name == "usage_monitor.data.store"
    assert log.get_logger("widget").name == "usage_monitor.widget"


def test_setup_logging_writes_formatted_utf8_to_a_rotating_file(
    tmp_path: Path, logging_sandbox: logging.Logger
) -> None:
    target = tmp_path / "logs" / "monitor.log"
    log.setup_logging(logging.INFO, target)
    (handler,) = _managed(logging_sandbox)
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 1024 * 1024
    assert handler.backupCount == 3
    log.get_logger("usage_monitor.tests").warning("ünïcode ok")
    line = target.read_text(encoding="utf-8").strip()
    pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} WARNING usage_monitor\.tests: ünïcode ok$"
    assert re.match(pattern, line)


def test_setup_logging_is_idempotent_and_can_switch_files(
    tmp_path: Path, logging_sandbox: logging.Logger
) -> None:
    first, second = tmp_path / "a.log", tmp_path / "b.log"
    log.setup_logging(logging.INFO, first)
    log.setup_logging(logging.DEBUG, first)
    (handler,) = _managed(logging_sandbox)
    assert _same_path(handler.baseFilename, first)
    assert logging_sandbox.level == logging.DEBUG
    log.setup_logging(logging.INFO, second)
    (handler,) = _managed(logging_sandbox)
    assert _same_path(handler.baseFilename, second)


def test_setup_logging_defaults_to_the_log_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logging_sandbox: logging.Logger
) -> None:
    monkeypatch.setenv("CUM_APPDATA_DIR", str(tmp_path / "app"))
    log.setup_logging()
    (handler,) = _managed(logging_sandbox)
    assert _same_path(handler.baseFilename, paths.log_path())


def test_unwritable_log_location_falls_back_to_a_null_handler(
    tmp_path: Path, logging_sandbox: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("a file, not a folder", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="usage_monitor"):
        log.setup_logging(logging.INFO, blocker / "monitor.log")  # must not raise
        log.setup_logging(logging.INFO, blocker / "monitor.log")  # and must not duplicate
    (handler,) = _managed(logging_sandbox)
    assert isinstance(handler, logging.NullHandler)
    assert not isinstance(handler, RotatingFileHandler)
    log.get_logger("usage_monitor.tests").error("dropped quietly")  # must not raise
    assert any("file logging disabled" in r.getMessage() for r in caplog.records)
