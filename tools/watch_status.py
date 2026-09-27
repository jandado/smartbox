"""Watch REST-polled statuses and websocket frames around boost toggles.

Read-only observation for finding D6 (raw `boost_end_min` semantics when
boost is on). Never writes to the API. Prints JSONL event lines to stdout;
human-readable headers go to stderr, so stdout stays machine-parseable.

Usage:
    uv run python tools/watch_status.py [--duration 300] [--interval 2]
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from typing import Any, cast

from live_check import ENV_PATH, load_env

from smartbox import AsyncSmartboxSession
from smartbox.error import SmartboxError
from smartbox.socket import SocketSession


def emit(src: str, payload: Any, **extra: Any) -> None:
    """Print one JSONL event line (flush immediately for tailing)."""
    record = {
        "t": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
        "src": src,
        **extra,
        "payload": payload,
    }
    print(json.dumps(record, sort_keys=True), flush=True)


async def poll_loop(
    session: AsyncSmartboxSession,
    dev_id: str,
    nodes: list[dict[str, Any]],
    interval: float,
    duration: float,
) -> None:
    """Poll every node's status, emitting only changed payloads."""
    last: dict[tuple[str, int], dict[str, Any]] = {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    while loop.time() < deadline:
        for node in nodes:
            try:
                status = cast(
                    "dict[str, Any] | None",
                    await session.get_node_status(dev_id, node),
                )
            except SmartboxError:
                continue
            key = (node["type"], int(node["addr"]))
            if status is not None and last.get(key) != status:
                last[key] = status
                emit("poll", status, type=node["type"], addr=node["addr"])
        await asyncio.sleep(interval)


async def watch(duration: float, interval: float) -> int:
    """Run the capture window. Returns an exit code."""
    env = load_env(ENV_PATH)
    missing = [
        key
        for key in ("SMARTBOX_API_NAME", "SMARTBOX_USERNAME", "SMARTBOX_PASSWORD")
        if not env.get(key)
    ]
    if missing:
        print(f"missing .env keys: {missing}", file=sys.stderr)
        return 1

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
        print(
            f"watching dev_id={dev_id[:4]}… nodes="
            f"{[(n['type'], n['addr']) for n in nodes]} duration={duration}s",
            file=sys.stderr,
        )

        def on_dev_data(data: dict[str, Any]) -> None:
            emit("socket_dev_data", data)

        def on_update(data: dict[str, Any]) -> None:
            emit("socket_update", data)

        socket_session = SocketSession(session, dev_id, on_dev_data, on_update)
        socket_task = asyncio.create_task(socket_session.run())
        try:
            await poll_loop(session, dev_id, nodes, interval, duration)
        finally:
            socket_task.cancel()
            await asyncio.gather(socket_task, return_exceptions=True)
    return 0


def main() -> int:
    """Parse arguments and run the watcher."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duration", type=float, default=300.0, help="capture window seconds"
    )
    parser.add_argument(
        "--interval", type=float, default=2.0, help="REST poll interval seconds"
    )
    args = parser.parse_args()
    return asyncio.run(watch(args.duration, args.interval))


if __name__ == "__main__":
    sys.exit(main())
