#!/usr/bin/env python
"""Live connectivity check against the real Smartbox API.

Reads credentials from a local `.env` file at the repo root (user-authored,
gitignored). NEVER prints credentials or tokens, and masks device IDs in
output, so the printed summary is safe to share. Used before hardware
validation sessions (D6/D7/B4 semantics pinning).

Usage:
    uv run python tools/live_check.py
"""

import asyncio
import json
from pathlib import Path
import sys
from typing import Any, cast

from smartbox import AsyncSmartboxSession
from smartbox.error import (
    APIUnavailableError,
    InvalidAuthError,
    ResellerNotExistError,
    SmartboxError,
)
from smartbox.reseller import AvailableResellers

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
REQUIRED_KEYS = ("SMARTBOX_API_NAME", "SMARTBOX_USERNAME", "SMARTBOX_PASSWORD")


def load_env(path: Path) -> dict[str, str]:
    """Parse a minimal KEY=VALUE .env file (no third-party dependency)."""
    env: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def mask(value: str, keep: int = 4) -> str:
    """Mask an identifier, keeping only a short prefix."""
    return (value[:keep] + "…") if len(value) > keep else "…"


async def check() -> int:
    """Run the read-only connectivity checks. Returns an exit code."""
    env = load_env(ENV_PATH)
    missing = [key for key in REQUIRED_KEYS if not env.get(key)]
    if missing:
        print(f"FAIL: missing keys in {ENV_PATH.name}: {', '.join(missing)}")
        return 1

    try:
        session = AsyncSmartboxSession(
            api_name=env["SMARTBOX_API_NAME"],
            username=env["SMARTBOX_USERNAME"],
            password=env["SMARTBOX_PASSWORD"],
        )
    except ResellerNotExistError as e:
        known = ", ".join(sorted(AvailableResellers.resellers))
        print(f"FAIL: unknown reseller key. Valid keys: {known}\n({e})")
        return 1

    fail: str | None = None
    async with session:
        # The reseller key is a public brand identifier (embedded in the
        # library) — safe to display, and vital for diagnosing auth failures.
        print(f"== reseller: {session.reseller.name} (key: {env['SMARTBOX_API_NAME']}) ==")
        print(f"== api host: {session.api_host} ==")
        try:
            print("== api_version ==")
            print(json.dumps(await session.api_version(), indent=2, sort_keys=True))
            print("== health_check ==")
            print(json.dumps(await session.health_check(), indent=2, sort_keys=True))

            devices = cast(
                "list[dict[str, Any]]", await session.get_devices()
            )
            print(f"== devices: {len(devices)} found ==")
            for device in devices:
                print(f"  dev_id={mask(device['dev_id'])} name={device['name']!r}")
            if not devices:
                fail = "no devices on this account"

            if devices:
                device = devices[0]
                dev_id = device["dev_id"]
                nodes = cast(
                    "list[dict[str, Any]]", await session.get_nodes(dev_id)
                )
                print(f"== nodes of first device ({len(nodes)}) ==")
                for node in nodes:
                    print(
                        f"  type={node['type']} addr={node['addr']}"
                        f" name={node['name']!r}"
                    )

                preferred = [
                    node
                    for node in nodes
                    if node.get("type") in {"htr", "htr_mod", "thm"}
                ]
                node = (preferred or nodes)[0]
                print(
                    f"== status payload of node type={node['type']}"
                    f" addr={node['addr']} =="
                )
                status = cast(
                    "dict[str, Any] | None",
                    await session.get_node_status(dev_id, node),
                )
                print(json.dumps(status, indent=2, sort_keys=True))
        except InvalidAuthError:
            fail = (
                "authentication rejected — check SMARTBOX_USERNAME / "
                f"SMARTBOX_PASSWORD, and that SMARTBOX_API_NAME="
                f"{env['SMARTBOX_API_NAME']!r} is the brand your account "
                "was created with"
            )
        except APIUnavailableError:
            fail = "could not reach the API host — check network/DNS"
        except SmartboxError as e:
            fail = f"API error: {e!r}"

    if fail:
        print(f"FAIL: {fail}")
        return 1
    print("OK: connectivity check passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(check()))
