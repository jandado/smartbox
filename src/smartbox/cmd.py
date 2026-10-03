"""Command line interaction with Smartbox system."""

import json
import logging
import os
from pathlib import Path
import re
from typing import Any, cast

import asyncclick as click

from smartbox.error import APIUnavailableError, InvalidAuthError, SmartboxError
from smartbox.reseller import AvailableResellers, ResellerNotExistError
from smartbox.session import AsyncSmartboxSession
from smartbox.socket import SocketSession

_LOGGER = logging.getLogger(__name__)


def _pretty_print(data: dict[str, Any]) -> None:
    """Pretty print json."""
    print(json.dumps(data, indent=4, sort_keys=True))


def _find_dotenv() -> Path | None:
    """Return the nearest ``.env`` file, searching upward from the CWD."""
    cwd = Path.cwd()
    for directory in (cwd, *cwd.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def _load_env_file(path: Path) -> None:
    """Load ``KEY=VALUE`` lines from ``path`` into the process environment.

    Variables already set in the real environment take precedence, so an
    explicit shell variable still wins over the file. Blank lines, ``#``
    comments and lines without ``=`` are ignored; an optional ``export``
    prefix is dropped. Values follow python-dotenv's parsing: a ``#``
    starts an inline comment only when it is preceded by whitespace and
    lies outside a quoted region, and one pair of surrounding matching
    quotes is stripped — so ``pass="a #b"`` keeps its hash while
    ``pass="a" # comment`` unquotes cleanly.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        _LOGGER.warning("Could not read env file %s: %s", path, exc)
        return
    quotes = {'"', "'"}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if value[:1] in quotes:
            # Drop anything after the closing quote (typically a comment);
            # a ``#`` inside the quoted region belongs to the value.
            closing = value.find(value[0], 1)
            if closing != -1:
                value = value[: closing + 1]
        else:
            value = re.split(r"(?<=\s)#", value, maxsplit=1)[0]
        value = value.strip()
        if value[:1] in quotes and value[-1:] == value[:1]:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _load_dotenv() -> None:
    """Load the nearest ``.env`` file, if any, before the CLI parses options."""
    dotenv_path = _find_dotenv()
    if dotenv_path is not None:
        _LOGGER.debug("Loading environment from %s", dotenv_path)
        _load_env_file(dotenv_path)


async def _resolve_device(
    session: AsyncSmartboxSession, device_id: str
) -> dict[str, Any]:
    """Find a device by id, with a clean error instead of a traceback."""
    devices = cast("list[dict[str, Any]]", await session.get_devices())
    device = next((d for d in devices if d["dev_id"] == device_id), None)
    if device is None:
        msg = f"no device with dev_id {device_id!r}"
        raise click.BadParameter(msg, param_hint="'-d/--device-id'")
    return device


async def _resolve_node(
    session: AsyncSmartboxSession, device_id: str, node_addr: int
) -> dict[str, Any]:
    """Find a node by address, with a clean error instead of a traceback.

    Node dicts carry an int addr on the wire; coerce defensively since
    raw-response payloads are unvalidated.
    """
    nodes = cast(
        "list[dict[str, Any]]",
        await session.get_nodes(device_id),
    )

    def _wire_addr(node: dict[str, Any]) -> int:
        try:
            return int(node["addr"])
        except (KeyError, TypeError, ValueError) as e:
            # Missing key (KeyError), non-numeric value (ValueError) or
            # non-scalar (TypeError) on the unvalidated wire payload.
            msg = f"malformed node payload (bad addr): {node!r}"
            raise click.ClickException(msg) from e

    node = next((n for n in nodes if _wire_addr(n) == node_addr), None)
    if node is None:
        msg = f"no node with addr {node_addr} on device {device_id!r}"
        raise click.BadParameter(msg, param_hint="'-n/--node-addr'")
    return node


class SmartboxGroup(click.Group):
    """Group mapping session/API failures to clean CLI errors.

    Without this, auth/network/reseller failures surfaced as raw
    tracebacks for every command.
    """

    async def invoke(self, ctx: click.Context) -> Any:  # noqa: ANN401
        """Invoke the chain, mapping session errors to clean CLI errors."""
        try:
            return await super().invoke(ctx)
        except (
            SmartboxError,
            InvalidAuthError,
            APIUnavailableError,
            ResellerNotExistError,
        ) as e:
            raise click.ClickException(str(e)) from e


@click.group(chain=True, cls=SmartboxGroup)
@click.option(
    "-a",
    "--api-name",
    default="api",
    show_default=True,
    help="API name (see the ``resellers`` command)",
    envvar="SMARTBOX_API_NAME",
    show_envvar=True,
)
@click.option(
    "-b",
    "--basic-auth-creds",
    required=False,
    help="API basic auth credentials",
    envvar="SMARTBOX_BASIC_AUTH_CREDS",
    show_envvar=True,
)
@click.option(
    "-u",
    "--username",
    required=True,
    help="API username",
    envvar="SMARTBOX_USERNAME",
    show_envvar=True,
)
@click.option(
    "-p",
    "--password",
    required=True,
    help="API password",
    envvar="SMARTBOX_PASSWORD",
    show_envvar=True,
)
@click.option(
    "-v",
    "--verbose/--no-verbose",
    default=False,
    help="Enable verbose logging",
)
@click.option(
    "-r",
    "--x-referer",
    required=False,
    envvar="SMARTBOX_X_REFERER",
    show_envvar=True,
    help="Refere of API",
)
@click.option(
    "-i",
    "--x-serial-id",
    required=False,
    type=int,
    envvar="SMARTBOX_X_SERIAL_ID",
    show_envvar=True,
    help="Serial id of API",
)
@click.pass_context
async def smartbox(
    ctx,
    api_name: str,
    basic_auth_creds: str,
    username: str,
    password: str,
    verbose: bool,
    x_serial_id: int,
    x_referer: str,
) -> None:
    """Set default options for smartbox."""
    ctx.ensure_object(dict)
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s "
        "[%(name)s.%(funcName)s:%(lineno)d] %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    session = AsyncSmartboxSession(
        api_name=api_name,
        basic_auth_credentials=basic_auth_creds,
        username=username,
        password=password,
        x_referer=x_referer,
        x_serial_id=x_serial_id,
    )
    ctx.obj["session"] = session
    ctx.obj["verbose"] = verbose
    # The library creates (and owns) its client session lazily; close it
    # when the CLI context tears down.
    ctx.call_on_close(session.aclose_owned_session)


@smartbox.command(help="Show devices")
@click.pass_context
async def devices(ctx) -> None:
    """Show devices."""
    session = ctx.obj["session"]
    devices = await session.get_devices()
    _pretty_print(devices)


@smartbox.command(help="Show Homes")
@click.pass_context
async def homes(ctx) -> None:
    """Show homes."""
    session = ctx.obj["session"]
    devices = await session.get_homes()
    _pretty_print(devices)


@smartbox.command(help="Show nodes")
@click.pass_context
async def nodes(ctx) -> None:
    """Show nodes."""
    session = ctx.obj["session"]
    devices = await session.get_devices()

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        nodes = await session.get_nodes(device["dev_id"])
        _pretty_print(nodes)


@smartbox.command(help="Show node status")
@click.pass_context
async def status(ctx) -> None:
    """Show node status."""
    session = ctx.obj["session"]
    devices = await session.get_devices()

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        nodes = await session.get_nodes(device["dev_id"])

        for node in nodes:
            print(f"{node['name']} (addr: {node['addr']})")
            status = await session.get_node_status(device["dev_id"], node)
            _pretty_print(status)


@smartbox.command(help="Show node power and temperature records (aka samples)")
@click.option(
    "-d",
    "--device-id",
    required=True,
    help="Device ID for node to set status on",
)
@click.option(
    "-n",
    "--node-addr",
    type=int,
    required=True,
    help="Address of node to set status on",
)
@click.option(
    "-s",
    "--start-time",
    type=int,
    required=False,
    help="Default now - 1 hour",
)
@click.option(
    "-e",
    "--end-time",
    type=int,
    required=False,
    help="Default now + 1 hour",
)
@click.pass_context
async def node_samples(
    ctx,
    device_id: str,
    node_addr: int,
    start_time: int,
    end_time: int,
) -> None:
    """Show node temperatures and consumption history."""
    session = ctx.obj["session"]
    node = await _resolve_node(session, device_id, node_addr)

    node_samples = await session.get_node_samples(
        device_id,
        node,
        start_time,
        end_time,
    )
    _pretty_print(node_samples)


@smartbox.command(
    help="Set node status (pass settings as extra args, e.g. mode=auto)",
)
@click.option(
    "-d",
    "--device-id",
    required=True,
    help="Device ID for node to set status on",
)
@click.option(
    "-n",
    "--node-addr",
    type=int,
    required=True,
    help="Address of node to set status on",
)
@click.option("--locked", type=bool)
@click.option("--mode")
@click.option("--stemp")
@click.option("--units")
@click.pass_context
async def set_status(
    ctx,
    device_id: str,
    node_addr: int,
    **kwargs: dict[str, Any],
) -> None:
    """Set node status."""
    session = ctx.obj["session"]
    device = await _resolve_device(session, device_id)
    node = await _resolve_node(session, device_id, node_addr)

    if kwargs.get("stemp") is not None and kwargs.get("units") is None:
        # The library raises ValueError here; map it to a clean CLI error.
        msg = "must supply --units with --stemp"
        raise click.ClickException(msg)

    await session.set_node_status(device["dev_id"], node, kwargs)


@smartbox.command(help="Show node setup")
@click.pass_context
async def setup(ctx) -> None:
    """Show node setup."""
    session = ctx.obj["session"]
    devices = await session.get_devices()

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        nodes = await session.get_nodes(device["dev_id"])

        for node in nodes:
            print(f"{node['name']} (addr: {node['addr']})")
            setup = await session.get_node_setup(device["dev_id"], node)
            _pretty_print(setup)


@smartbox.command(help="Set node setup options")
@click.option(
    "-d",
    "--device-id",
    required=True,
    help="Device ID for node to set setup on",
)
@click.option(
    "-n",
    "--node-addr",
    type=int,
    required=True,
    help="Address of node to set setup on",
)
@click.option("--control-mode", type=int, default=None)
@click.option("--offset", type=str, default=None)
@click.option("--priority", type=str, default=None)
@click.option("--true-radiant-enabled", type=bool, default=None)
@click.option("--units", type=str, default=None)
@click.option("--window-mode-enabled", type=bool, default=None)
@click.pass_context
async def set_setup(
    ctx,
    device_id: str,
    node_addr: int,
    **kwargs: dict[str, Any],
) -> None:
    """Set node setup options."""
    session = ctx.obj["session"]
    device = await _resolve_device(session, device_id)
    node = await _resolve_node(session, device_id, node_addr)

    # Only pass specified options
    setup_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    await session.set_node_setup(device["dev_id"], node, setup_kwargs)


@smartbox.command(help="Show node prog")
@click.pass_context
async def prog(ctx) -> None:
    """Show node prog."""
    session = ctx.obj["session"]
    devices = await session.get_devices()

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        nodes = await session.get_nodes(device["dev_id"])

        for node in nodes:
            print(f"{node['name']} (addr: {node['addr']})")
            prog = await session.get_node_prog(device["dev_id"], node)
            _pretty_print(prog)


@smartbox.command(
    help=(
        "Set node prog from a JSON string, e.g. "
        '\'{"prog": {"0": [2, 2, ...], ...}}\''
    ),
)
@click.option(
    "-d",
    "--device-id",
    required=True,
    help="Device ID for node to set prog on",
)
@click.option(
    "-n",
    "--node-addr",
    type=int,
    required=True,
    help="Address of node to set prog on",
)
@click.argument("prog-json", type=str)
@click.pass_context
async def set_prog(
    ctx,
    device_id: str,
    node_addr: int,
    prog_json: str,
) -> None:
    """Set node prog."""
    session = ctx.obj["session"]
    device = await _resolve_device(session, device_id)
    node = await _resolve_node(session, device_id, node_addr)

    try:
        prog_args = json.loads(prog_json)
    except json.JSONDecodeError as err:
        msg = f"invalid prog JSON: {err}"
        raise click.ClickException(msg) from err
    if not isinstance(prog_args, dict) or not isinstance(
        prog_args.get("prog"),
        dict,
    ):
        # Reject array shapes / day-keyed-top-level bodies with a clean
        # error instead of an AttributeError or a silent no-op POST.
        msg = (
            "prog payload must be a JSON object with a 'prog' object "
            'mapping day keys, e.g. {"prog": {"0": [2, 2, ...]}}'
        )
        raise click.ClickException(msg)
    await session.set_node_prog(device["dev_id"], node, prog_args)


@smartbox.command(help="Show device away_status")
@click.pass_context
async def device_away_status(ctx) -> None:
    """Show device away status."""
    session = ctx.obj["session"]
    devices = await session.get_devices()

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        device_away_status = await session.get_device_away_status(
            device["dev_id"],
        )
        _pretty_print(device_away_status)


@smartbox.command(help="Show device connected status")
@click.pass_context
async def device_connected_status(ctx) -> None:
    """Show device connected status."""
    session = ctx.obj["session"]
    devices = await session.get_devices()

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        device_away_status = await session.get_device_connected(
            device["dev_id"],
        )
        _pretty_print(device_away_status)


@smartbox.command(
    help="Set device away_status (pass settings as extra args, e.g. away=true)",
)
@click.option(
    "-d",
    "--device-id",
    required=True,
    help="Device ID to set away_status on",
)
@click.option("--away", type=bool)
@click.option("--enabled", type=bool)
@click.option("--forced", type=bool)
@click.pass_context
async def set_device_away_status(
    ctx,
    device_id: str,
    **kwargs: dict[str, Any],
) -> None:
    """Set device away status."""
    session = ctx.obj["session"]
    device = await _resolve_device(session, device_id)

    await session.set_device_away_status(device["dev_id"], kwargs)


@smartbox.command(help="Show device power_limit")
@click.pass_context
async def device_power_limit(ctx) -> None:
    """Show device power limit."""
    session = ctx.obj["session"]
    devices = await session.get_devices()

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        device_power_limit = await session.get_device_power_limit(
            device["dev_id"],
        )
        _pretty_print(device_power_limit)


@smartbox.command(help="Set device power_limit")
@click.option(
    "-d",
    "--device-id",
    required=True,
    help="Device ID to set power_limit on",
)
@click.argument("power-limit", type=int)
@click.pass_context
async def set_device_power_limit(ctx, device_id: str, power_limit: int) -> None:
    """Set device power limit."""
    session = ctx.obj["session"]
    device = await _resolve_device(session, device_id)

    await session.set_device_power_limit(device["dev_id"], power_limit)


@smartbox.command(help="Open socket.io connection to device.")
@click.option(
    "-d",
    "--device-id",
    required=True,
    help="Device ID to open socket for",
)
@click.pass_context
async def socket(ctx, device_id: str) -> None:
    """Open socket.io connection to device."""
    session = ctx.obj["session"]
    verbose = ctx.obj["verbose"]

    def on_dev_data(data) -> None:
        """Received dev_data."""
        _LOGGER.info("Received dev_data:")
        _pretty_print(data)

    def on_update(data) -> None:
        """Received update."""
        _LOGGER.info("Received update:")
        _pretty_print(data)

    socket_session = SocketSession(
        session,
        device_id,
        on_dev_data,
        on_update,
        verbose,
        add_sigint_handler=True,
    )
    await socket_session.run()


@smartbox.command(help="Get status of the API.")
@click.pass_context
async def health_check(ctx) -> None:
    """Get the status of the API."""
    session = ctx.obj["session"]
    health = await session.health_check()
    _pretty_print(health)


@smartbox.command(help="Get version of the API.")
@click.pass_context
async def api_version(ctx) -> None:
    """Get the version of the API."""
    session = ctx.obj["session"]
    version = await session.api_version()
    _pretty_print(version)


@smartbox.command(help="Get the availables resellers.")
def resellers() -> None:
    """Get the availables resellers."""
    for name, reseller in AvailableResellers.resellers.items():
        print(f"{name}: {reseller.name} (api_url: {reseller.api_url})")


@smartbox.command(help="Get the home guest")
@click.option(
    "-h",
    "--home-id",
    required=True,
    help="Home ID to get the guests.",
)
@click.pass_context
async def guests(ctx, home_id: str) -> None:
    """Get the home guests."""
    session = ctx.obj["session"]
    guests = await session.get_home_guests(home_id=home_id)
    _pretty_print(guests)


def cli() -> None:
    """Console-script entry point: load ``.env`` then run the CLI."""
    _load_dotenv()
    smartbox()


# For debugging
if __name__ == "__main__":
    cli()
