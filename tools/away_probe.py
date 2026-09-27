#!/usr/bin/env python
"""D1 probe: away_status write shape ({away, enabled} vs {away} alone).

The vendor app sends {"away": bool, "enabled": bool}; the integration
sends {"away"} only. This probe establishes which bodies the server
accepts and what they actually do, then restores the baseline:

  1. GET away_status -> baseline
  2. POST {"away": true, "enabled": true}  -> GET-verify
  3. POST {"away": false, "enabled": true} -> GET-verify
  4. POST {"away": true} (no enabled)      -> GET-verify (D1 question)
  5. restore baseline {"away", "enabled"}  -> GET-verify

Writes happen ONLY with --confirm; the default is a dry run. Changing
away mode may physically affect heaters (away offsets apply) — run it
and expect the restore to bring everything back.

Usage:
  uv run python tools/away_probe.py [--confirm]
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

SCRUB_KEYS = {"uid", "devid", "dev_id", "serial_id"}


def log(msg: str) -> None:
    """Timestamped progress line."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def scrub(data: dict[str, Any]) -> dict[str, Any]:
    """Drop identifying keys before printing."""
    return {k: v for k, v in data.items() if k not in SCRUB_KEYS}


async def probe(confirm: bool) -> int:
    """Execute the away-status probe."""
    env = load_env(ENV_PATH)
    session = AsyncSmartboxSession(
        api_name=env["SMARTBOX_API_NAME"],
        username=env["SMARTBOX_USERNAME"],
        password=env["SMARTBOX_PASSWORD"],
    )
    async with session:
        devices = cast("list[dict[str, Any]]", await session.get_devices())
        dev_id = devices[0]["dev_id"]
        baseline = cast(
            "dict[str, Any]",
            await session.get_device_away_status(dev_id),
        )
        log(f"baseline: {json.dumps(scrub(baseline), sort_keys=True)}")
        steps: list[tuple[str, dict[str, Any]]] = [
            ("away=true, enabled=true", {"away": True, "enabled": True}),
            ("away=false, enabled=true (reset)", {"away": False, "enabled": True}),
            (
                "away=true, enabled OMITTED (D1 question)",
                {"away": True},
            ),
            ("away=false, enabled=true (reset)", {"away": False, "enabled": True}),
            (
                "restore baseline",
                {k: baseline[k] for k in ("away", "enabled") if k in baseline},
            ),
        ]
        if not confirm:
            log("dry run plan:")
            for label, body in steps:
                log(f"  POST {json.dumps(body)}  ({label})")
            log("dry run: no writes issued (pass --confirm to execute)")
            return 0

        failures = 0
        for label, body in steps:
            try:
                await session.set_device_away_status(dev_id, body)
            except SmartboxError as err:
                log(f"FAIL: {label}: POST rejected: "
                    f"{type(err).__name__}: {err}")
                failures += 1
                continue
            await asyncio.sleep(3)  # let the server settle before the GET
            current = cast(
                "dict[str, Any]",
                await session.get_device_away_status(dev_id),
            )
            log(
                f"{label}: accepted; now "
                f"{json.dumps(scrub(current), sort_keys=True)}"
            )
    log(f"done failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(probe(args.confirm)))
