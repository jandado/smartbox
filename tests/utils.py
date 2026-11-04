"""Shared helpers for the library test suite."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


async def until(
    pred: Callable[[], bool],
    budget: float = 10.0,
) -> None:
    """Bounded poll for a condition (the established sleep replacement)."""
    try:
        async with asyncio.timeout(budget):
            while not pred():  # noqa: ASYNC110 - intentional bounded poll
                await asyncio.sleep(0.01)
    except TimeoutError:
        msg = f"condition not met within {budget}s"
        raise TimeoutError(msg) from None


def assert_backoff_delays(delays: list[float], bases: list[float]) -> None:
    """Assert each delay is a base plus up to 20 % one-sided jitter."""
    assert len(delays) == len(bases)
    for delay, base in zip(delays, bases, strict=True):
        # The epsilon absorbs float rounding at the 20 % upper bound.
        assert base <= delay <= base * 1.2 + 1e-9


def armed_deadlines(engine, kind: str | None = None) -> list[tuple]:
    """Return the identity part of the engine's armed node deadlines."""
    return [
        (deadline.dev_id, deadline.node_key, deadline.kind, deadline.slot)
        for deadline in engine.deadlines
        if kind is None or deadline.kind == kind
    ]


_LIVE_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "live"


def live_fixture(name: str) -> dict:
    """Load a captured live wire payload from the vendored fixtures."""
    path = _LIVE_FIXTURES / name
    if not path.exists():
        msg = f"vendored live fixture {name} is missing from {_LIVE_FIXTURES}"
        raise FileNotFoundError(msg)
    return json.loads(path.read_text())
