"""Tests for usage_monitor.data.netread: a size cap and a time budget for a whole response body.

Fake responses and a fake clock only, so nothing touches the network.
"""
from __future__ import annotations

import pytest

from usage_monitor.data.netread import read_capped


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Dripping:
    """Hands out at most ``size`` bytes per read and takes ``step`` seconds of fake time per read."""

    def __init__(self, body: bytes, clock: FakeClock, *, size: int = 1, step: float = 0.5) -> None:
        self._body = body
        self._clock = clock
        self._size = size
        self._step = step
        self._pos = 0

    def read1(self, amount: int = -1) -> bytes:
        self._clock.now += self._step
        take = self._size if amount < 0 else min(self._size, amount)
        chunk = self._body[self._pos:self._pos + take]
        self._pos += len(chunk)
        return chunk


class PlainResponse:
    """Only read(), as a response object without read1 would offer."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, amount: int | None = None) -> bytes:
        return self._body if amount is None else self._body[:amount]


def test_a_body_that_arrives_within_the_budget_is_returned_whole():
    clock = FakeClock()
    assert read_capped(Dripping(b"x" * 100, clock, size=10), 1000, 60, clock=clock) == b"x" * 100


def test_the_read_never_returns_more_than_the_limit():
    clock = FakeClock()
    assert len(read_capped(Dripping(b"y" * 5000, clock, size=4096, step=0), 100, 60, clock=clock)) == 100


def test_a_body_that_trickles_past_the_budget_is_cut_off_with_a_timeout():
    clock = FakeClock()
    with pytest.raises(TimeoutError):
        read_capped(Dripping(b"z" * 10_000, clock, size=1, step=0.5), 20_000, 3, clock=clock)
    assert clock.now <= 3.5  # stopped at the budget, not after the whole body


def test_a_response_without_read1_is_read_with_read():
    assert read_capped(PlainResponse(b"abc"), 10, 60) == b"abc"
