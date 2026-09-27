"""Controlled extra_options probes for findings D7 and B4.

Every probe follows snapshot → POST → verify → restore → verify. Writes
happen ONLY when --confirm is passed; the default is a dry run that prints
the exact payloads it would send.

Probes:
  show   Print the node's raw setup JSON (read-only).
  b4     POST setup with ONLY {"extra_options": {"boost_temp": X}} to test
         whether the server replaces or merges the extra_options object.
  d7     POST extra_options with an out-of-slider-range boost_time (or
         boost_temp) to observe server-side validation/clamping.

Usage:
  uv run python tools/probe_extra_options.py --addr 5 show
  uv run python tools/probe_extra_options.py --addr 5 b4 --boost-temp 25.0
  uv run python tools/probe_extra_options.py --addr 5 --confirm b4 --boost-temp 25.0
  uv run python tools/probe_extra_options.py --addr 5 d7 --boost-time 999
"""

import argparse
import asyncio
import json
import sys
from typing import Any, cast

from live_check import ENV_PATH, load_env

from smartbox import AsyncSmartboxSession


def dump(label: str, payload: Any) -> None:
    """Pretty-print a labeled JSON payload."""
    print(f"== {label} ==")
    print(json.dumps(payload, indent=2, sort_keys=True))


def find_node(
    nodes: list[dict[str, Any]], addr: int
) -> dict[str, Any] | None:
    """Return the first node whose addr matches."""
    for node in nodes:
        if int(node["addr"]) == addr:
            return node
    return None


async def run_probe(args: argparse.Namespace) -> int:
    """Execute the requested probe mode."""
    env = load_env(ENV_PATH)
    session = AsyncSmartboxSession(
        api_name=env["SMARTBOX_API_NAME"],
        username=env["SMARTBOX_USERNAME"],
        password=env["SMARTBOX_PASSWORD"],
    )
    async with session:
        devices = cast("list[dict[str, Any]]", await session.get_devices())
        if not devices:
            print("no devices on this account", file=sys.stderr)
            return 1
        dev_id = devices[0]["dev_id"]
        nodes = cast("list[dict[str, Any]]", await session.get_nodes(dev_id))
        node = find_node(nodes, args.addr)
        if node is None:
            print(f"no node with addr={args.addr}", file=sys.stderr)
            return 1

        before = cast(
            "dict[str, Any]", await session.get_node_setup(dev_id, node)
        )
        dump(f"current setup addr={args.addr}", before)

        if args.command == "show":
            return 0

        if args.command == "b4":
            payload = {"extra_options": {"boost_temp": args.boost_temp}}
        else:  # d7
            payload = {
                "extra_options": {
                    "boost_temp": str(
                        (before.get("extra_options") or {}).get(
                            "boost_temp", "21.0"
                        )
                    ),
                    "boost_time": args.boost_time,
                },
            }

        dump("would POST", payload)
        if not args.confirm:
            print("DRY RUN — re-run with --confirm to send.")
            return 0

        print(f">>> POST setup payload={payload}")
        await session.set_node_setup(dev_id, node, payload)
        await asyncio.sleep(2)
        after = cast(
            "dict[str, Any]", await session.get_node_setup(dev_id, node)
        )
        dump("setup after POST", after)

        print(">>> POST restore setup snapshot")
        await session.set_node_setup(dev_id, node, before)
        await asyncio.sleep(2)
        restored = cast(
            "dict[str, Any]", await session.get_node_setup(dev_id, node)
        )
        dump("setup after restore", restored)
        if restored.get("extra_options") == (before.get("extra_options")):
            print("RESTORE OK — extra_options match the original snapshot.")
            return 0
        print(
            "RESTORE MISMATCH — verify manually!",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    """Parse arguments and run the probe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--addr", type=int, required=True, help="node addr to probe"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="actually send the probe POSTs (default: dry run)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="print raw setup JSON (read-only)")
    p_b4 = sub.add_parser("b4", help="partial extra_options replace/merge test")
    p_b4.add_argument("--boost-temp", default="25.0", help="boost_temp value")
    p_b4.add_argument("--confirm", action="store_true", help="send POSTs")
    p_d7 = sub.add_parser("d7", help="out-of-range boost_time test")
    p_d7.add_argument("--boost-time", type=int, default=999)
    p_d7.add_argument("--confirm", action="store_true", help="send POSTs")
    args = parser.parse_args()
    return asyncio.run(run_probe(args))


if __name__ == "__main__":
    sys.exit(main())
