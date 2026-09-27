#!/usr/bin/env python
"""Read-only live capture of node prog (schedule) and setup payloads.

D6 blocker: no live `GET .../prog` sample existed. This tool fetches the
prog for every node on the first device (plus a fresh setup per htr node,
for D5 prog_resolution) and writes raw fixtures to tests/fixtures/live/
for pinning in test_live_payloads.py. Read-only: only GETs are issued.
Identifying fields (uid/devid/dev_id) are scrubbed from fixtures.

Usage:
    uv run python tools/capture_prog.py
"""

import asyncio
import json
from pathlib import Path
import sys
from typing import Any, cast

from live_check import ENV_PATH, load_env

from smartbox import AsyncSmartboxSession

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "live"
SCRUB_KEYS = {"uid", "devid", "dev_id", "serial_id"}


def scrub(data: Any) -> Any:
    """Recursively drop identifying keys from a payload."""
    if isinstance(data, dict):
        return {
            k: scrub(v) for k, v in data.items() if k not in SCRUB_KEYS
        }
    if isinstance(data, list):
        return [scrub(item) for item in data]
    return data


def write(name: str, payload: dict[str, Any]) -> None:
    """Scrub and write a fixture."""
    path = FIXTURES / name
    path.write_text(
        json.dumps(scrub(payload), indent=2, sort_keys=True) + "\n"
    )
    print(f"captured {name} ({len(json.dumps(payload))} bytes)")


async def capture() -> int:
    """Fetch prog + setup payloads and write fixtures."""
    env = load_env(ENV_PATH)
    session = AsyncSmartboxSession(
        api_name=env["SMARTBOX_API_NAME"],
        username=env["SMARTBOX_USERNAME"],
        password=env["SMARTBOX_PASSWORD"],
    )
    async with session:
        devices = cast("list[dict[str, Any]]", await session.get_devices())
        if not devices:
            print("FAIL: no devices on this account", file=sys.stderr)
            return 1
        dev_id = devices[0]["dev_id"]
        nodes = cast("list[dict[str, Any]]", await session.get_nodes(dev_id))
        FIXTURES.mkdir(parents=True, exist_ok=True)
        count = 0
        for node in nodes:
            node_type = node["type"]
            addr = int(node["addr"])
            prog = cast(
                "dict[str, Any]", await session.get_node_prog(dev_id, node)
            )
            write(f"{node_type}_prog_addr{addr}.json", prog)
            count += 1
            if node_type == "htr":
                setup = cast(
                    "dict[str, Any]", await session.get_node_setup(dev_id, node)
                )
                write(f"{node_type}_setup_addr{addr}.json", setup)
                count += 1
        print(f"OK: {count} fixtures written to {FIXTURES}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(capture()))
