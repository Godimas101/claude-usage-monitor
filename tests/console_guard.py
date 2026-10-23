"""The run-time half of "no test opens a console window" (AGENTS.md, Tests).

A console program started by a process that has no console of its own (pytest run from an editor, a task runner or an agent)
gets a terminal window on the desktop, one per process. tests/test_update_check.py reads the test files for a call that
forgets ``creationflags=subprocess.CREATE_NO_WINDOW``; it cannot see a call it does not recognise, and it cannot read the
code under test. This module can: while it is installed (tests/conftest.py does it for the whole run) every
``subprocess.Popen`` refuses to start unless it carries that flag. ``subprocess.run``, ``call``, ``check_call``,
``check_output``, ``os.popen`` and asyncio's subprocesses all start their process through ``Popen``, so one wrapper covers
them, whoever calls: a test, a helper module, a fixture, or the app code a test runs.

A refused start raises ``AssertionError`` (the process is never created, so nothing flashes up) and is also recorded in
``violations``, which conftest.py turns into a failure of the test that was running: code under test that catches the
exception (a launcher that treats any error as "could not start") would otherwise hide it. What the wrapper cannot see
(os.system, os.startfile, multiprocessing, ShellExecute) is what the test-file scan covers.
"""
from __future__ import annotations

import contextlib
import inspect
import subprocess
import sys
import traceback
from collections.abc import Iterator
from typing import Any

import pytest

CREATE_NO_WINDOW = 0x08000000

# One line per refused start, in order: "tests/test_x.py:12: subprocess.Popen(['git', ...]) without CREATE_NO_WINDOW".
violations: list[str] = []

_original_init = subprocess.Popen.__init__
_parameters = inspect.signature(_original_init)
_installed = False


def _where() -> str:
    """The first frame of the caller that is not the subprocess machinery, this module or asyncio."""
    for frame in reversed(traceback.extract_stack()[:-2]):
        name = frame.filename.replace("\\", "/")
        if not (name.endswith(("/subprocess.py", "/console_guard.py")) or "/asyncio/" in name):
            return f"{name.rsplit('/tests/', 1)[-1]}:{frame.lineno}"
    return "?"


def _program(arguments: dict[str, Any]) -> str:
    command = arguments.get("args")
    if isinstance(command, (list, tuple)) and command:
        command = command[0]
    return str(command).strip()[:60] if command else "?"


def _guarded_init(self: subprocess.Popen[Any], *args: Any, **kwargs: Any) -> None:
    try:
        bound = _parameters.bind(self, *args, **kwargs).arguments
    except TypeError:
        bound = None                           # a call Popen itself will refuse for its arguments
    if bound is not None:
        flags = bound.get("creationflags", 0)
        if not isinstance(flags, int) or not flags & CREATE_NO_WINDOW:
            message = f"{_where()}: subprocess.Popen({_program(bound)!r}, ...) without creationflags=CREATE_NO_WINDOW"
            violations.append(message)
            raise AssertionError("a test run must not open a console window: " + message)
    _original_init(self, *args, **kwargs)


def install() -> None:
    """Start refusing a Popen without the flag (Windows only: the flag does not exist elsewhere). Safe to repeat."""
    global _installed
    if sys.platform == "win32" and not _installed:
        subprocess.Popen.__init__ = _guarded_init       # type: ignore[method-assign,assignment]
        _installed = True


def uninstall() -> None:
    global _installed
    if _installed:
        subprocess.Popen.__init__ = _original_init      # type: ignore[method-assign]
        _installed = False


def fail_if_refused_since(mark: int) -> None:
    """Fail the running test when a start was refused after ``violations`` had ``mark`` entries (conftest.py, after each test)."""
    refused = violations[mark:]
    if refused:
        pytest.fail("a process was started without CREATE_NO_WINDOW (a console window would open on the desktop):\n  "
                    + "\n  ".join(refused), pytrace=False)


@contextlib.contextmanager
def refusals() -> Iterator[list[str]]:
    """For the tests of this guard: the starts refused inside the block are handed back and do not count against the test."""
    start = len(violations)
    caught: list[str] = []
    try:
        yield caught
    finally:
        caught.extend(violations[start:])
        del violations[start:]
