"""Bounded reads of an HTTP response body: a size cap and one time budget for the whole body.

The socket timeout applies to each read, so a server that trickles bytes can keep a plain read()
going for as long as it likes. read_capped gives the whole body a deadline as well.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

_CHUNK = 64 * 1024


def read_capped(response: Any, limit: int, budget_s: float, *,
                clock: Callable[[], float] | None = None) -> bytes:
    """Up to ``limit`` bytes of the body, all read within ``budget_s`` seconds in total.

    The caller passes ``limit`` one byte above its size cap and compares the length, as it did
    with a plain read(). With read1 the reads return whatever has arrived, so the deadline is
    checked between reads; one read that blocks is still bounded by the socket timeout. Without
    read1, read(n) returns fewer than n bytes only at the end of the body.
    Raises TimeoutError when the budget is spent before ``limit`` bytes or the end of the body.
    """
    now = clock if clock is not None else time.monotonic
    read1 = getattr(response, "read1", None)
    deadline = now() + budget_s
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        if now() >= deadline:
            raise TimeoutError("response body did not arrive within its time budget")
        want = min(_CHUNK, limit - total)
        chunk = read1(want) if read1 is not None else response.read(want)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if read1 is None and len(chunk) < want:
            break
    return b"".join(chunks)
