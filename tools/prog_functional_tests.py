#!/usr/bin/env python
"""Round-trip prog (schedule) test against live hardware (D2/D6 verification).

Sequence (single target node): GET prog (snapshot) -> edit one slot on day
"0" -> POST the merged schedule via set_node_prog (body {"prog": ...} only)
-> GET-verify the edit landed -> restore the snapshot -> GET-verify the
restore. Shape validated against tests/fixtures/live expectations (7 string
day keys, 24 hourly slots at prog_resolution 0; profiles 0=ICE, 1=ECO,
2=COMF). Unlike stemp, a schedule does not self-change, so any mismatch is
a real failure.

Writes happen ONLY with --confirm; the default is a dry run that prints
the plan. Run tools/watch_status.py concurrently to capture the websocket
/htr/<addr>/prog frame for the write.

Usage:
  uv run python tools/prog_functional_tests.py [--addr 5] [--confirm]
"""

import argparse
import asyncio
import json
import sys
import time
from typing import Any, cast

from live_check import ENV_PATH, load_env

from smartbox import AsyncSmartboxSession
from smartbox.error import SmartboxError

DAYS = tuple(str(day) for day in range(7))
PROFILES = {0, 1, 2}
EDIT_SLOT = 6  # 07:00 hourly slot on day "0"


def log(msg: str) -> None:
    """Timestamped progress line."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def validate_shape(prog: dict[str, Any]) -> None:
    """Assert the pinned prog shape (raise on any deviation)."""
    if set(prog) < {"prog", "sync_status"}:
        raise ValueError(f"unexpected prog payload keys: {sorted(prog)}")
    if set(prog["prog"]) != set(DAYS):
        raise ValueError(f"day keys not 0..6: {sorted(prog['prog'])}")
    for day, slots in prog["prog"].items():
        if len(slots) != 24:
            raise ValueError(f"day {day}: {len(slots)} slots (expected 24)")
        bad = set(slots) - PROFILES
        if bad:
            raise ValueError(f"day {day}: unexpected profile values {bad}")


async def get_prog(
    session: AsyncSmartboxSession, dev_id: str, node: dict[str, Any]
) -> dict[str, Any]:
    """Raw node prog."""
    return cast("dict[str, Any]", await session.get_node_prog(dev_id, node))


async def run_tests(addr: int, confirm: bool) -> int:
    """Execute the edit + restore cycle."""
    env = load_env(ENV_PATH)
    session = AsyncSmartboxSession(
        api_name=env["SMARTBOX_API_NAME"],
        username=env["SMARTBOX_USERNAME"],
        password=env["SMARTBOX_PASSWORD"],
    )
    async with session:
        devices = cast("list[dict[str, Any]]", await session.get_devices())
        dev_id = devices[0]["dev_id"]
        nodes = cast("list[dict[str, Any]]", await session.get_nodes(dev_id))
        target = next((n for n in nodes if int(n["addr"]) == addr), None)
        if target is None:
            print(f"no node with addr={addr}", file=sys.stderr)
            return 1
        log(f"target: {target['name']} (htr addr={addr})")

        baseline = await get_prog(session, dev_id, target)
        validate_shape(baseline)
        original_day = baseline["prog"]["0"]
        edited_day = list(original_day)
        edited_day[EDIT_SLOT] = next(
            p for p in sorted(PROFILES) if p != original_day[EDIT_SLOT]
        )
        log(
            f"plan: day '0' slot {EDIT_SLOT} "
            f"{original_day[EDIT_SLOT]} -> {edited_day[EDIT_SLOT]} "
            f"(profile: 0=ICE, 1=ECO, 2=COMF)"
        )
        if not confirm:
            log("dry run: no writes issued (pass --confirm to execute)")
            return 0

        failures = 0
        try:
            await session.set_node_prog(
                dev_id, target, {"prog": {"0": edited_day}}
            )
        except SmartboxError as err:
            log(f"FAIL: POST rejected: {type(err).__name__}: {err}")
            return 1
        await asyncio.sleep(5)  # GET reads device state with a settle lag
        after = await get_prog(session, dev_id, target)
        if after["prog"]["0"] == edited_day:
            log(f"PASS: edit GET-verified (slot {EDIT_SLOT} now "
                f"{edited_day[EDIT_SLOT]})")
        else:
            log(f"FAIL: edit not reflected: {after['prog']['0']}")
            failures += 1

        try:
            await session.set_node_prog(
                dev_id, target, {"prog": {"0": original_day}}
            )
        except SmartboxError as err:
            log(f"FAIL: restore POST rejected: {type(err).__name__}: {err}")
            return 1
        await asyncio.sleep(5)
        restored = await get_prog(session, dev_id, target)
        if restored["prog"] == baseline["prog"]:
            log("PASS: restore verified (all 7 days match snapshot)")
        else:
            diff = {
                day: (baseline["prog"][day], restored["prog"].get(day))
                for day in baseline["prog"]
                if restored["prog"].get(day) != baseline["prog"][day]
            }
            log(f"FAIL: restore mismatch: {json.dumps(diff)}")
            failures += 1

    log(f"done failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--addr", type=int, default=5)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(run_tests(args.addr, args.confirm)))
