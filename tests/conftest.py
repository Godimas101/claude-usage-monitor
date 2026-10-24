"""Test-wide setup. Today that is one thing: the run-time guard against a console window (tests/console_guard.py).

Every child process a test starts, or the code under test starts, carries creationflags=CREATE_NO_WINDOW. This installs the
check on ``subprocess.Popen`` for the whole run and fails the test that was running when a start was refused, even when the
code under test swallowed the exception.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

import console_guard


def pytest_configure(config: pytest.Config) -> None:
    console_guard.install()


def pytest_unconfigure(config: pytest.Config) -> None:
    console_guard.uninstall()


@pytest.fixture(autouse=True)
def no_console_window_was_opened() -> Iterator[None]:
    mark = len(console_guard.violations)
    yield
    console_guard.fail_if_refused_since(mark)
