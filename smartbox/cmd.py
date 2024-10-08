import asyncio
import click
import logging
import pprint

from .session import Session
from .socket import SocketSession

_LOGGER = logging.getLogger(__name__)


@click.group(chain=True)
@click.option('-a', '--api-name', required=True, help='API name')
@click.option('-b', '--basic-auth-creds', required=True, help='API basic auth credentials')
@click.option('-xr', '--x-referer', required=True, help='API X-Referer')
@click.option('-xsid', '--x-serialid', required=True, help='API X-Serialid')
@click.option('-u', '--username', required=True, help='API username')
@click.option('-p', '--password', required=True, help='API password')
@click.option('-v', '--verbose/--no-verbose', default=False, help='Enable verbose logging')
@click.pass_context
def smartbox(ctx, api_name, basic_auth_creds, x_referer, x_serialid, username, password, verbose):
    ctx.ensure_object(dict)
    logging.basicConfig(format='%(asctime)s %(levelname)-8s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
                        level=logging.DEBUG if verbose else logging.INFO,
                        datefmt='%Y-%m-%d %H:%M:%S')
    session = Session(api_name, basic_auth_creds, x_referer, x_serialid, username, password)
    ctx.obj['session'] = session
    ctx.obj['verbose'] = verbose


@smartbox.command(help='Show devices')
@click.pass_context
def devices(ctx):
    session = ctx.obj['session']
    devices = session.get_devices()
    pp = pprint.PrettyPrinter(indent=4)
    pp.pprint(devices)


@smartbox.command(help='Show nodes')
@click.pass_context
def nodes(ctx):
    session = ctx.obj['session']
    devices = session.get_devices()
    pp = pprint.PrettyPrinter(indent=4)

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        nodes = session.get_nodes(device['dev_id'])
        pp.pprint(nodes)


@smartbox.command(help='Show node status')
@click.pass_context
def status(ctx):
    session = ctx.obj['session']
    devices = session.get_devices()
    pp = pprint.PrettyPrinter(indent=4)

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        nodes = session.get_nodes(device['dev_id'])

        for node in nodes:
            print(f"{node['name']} (addr: {node['addr']})")
            status = session.get_status(device['dev_id'], node)
            pp.pprint(status)


@smartbox.command(help='Set node status (pass settings as extra args, e.g. mode=auto)')
@click.option('-d', '--device-id', required=True, help='Device ID for node to set status on')
@click.option('-n', '--node-addr', type=int, required=True, help='Address of node to set status on')
@click.option('--locked', type=bool)
@click.option('--mode')
@click.option('--stemp')
@click.option('--units')
# TODO: other options
@click.pass_context
def set_status(ctx, device_id, node_addr, **kwargs):
    session = ctx.obj['session']
    devices = session.get_devices()
    device = next(d for d in devices if d['dev_id'] == device_id)
    nodes = session.get_nodes(device['dev_id'])
    node = next(n for n in nodes if n['addr'] == node_addr)

    session.set_status(device['dev_id'], node, kwargs)


@smartbox.command(help='Show node setup')
@click.pass_context
def setup(ctx):
    session = ctx.obj['session']
    devices = session.get_devices()
    pp = pprint.PrettyPrinter(indent=4)

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        nodes = session.get_nodes(device['dev_id'])

        for node in nodes:
            print(f"{node['name']} (addr: {node['addr']})")
            setup = session.get_setup(device['dev_id'], node)
            pp.pprint(setup)


@smartbox.command(help='Set node setup (pass settings as extra args, e.g. mode=auto)')
@click.option('-d', '--device-id', required=True, help='Device ID for node to set setup on')
@click.option('-n', '--node-addr', type=int, required=True, help='Address of node to set setup on')
@click.option('--true-radiant-enabled', type=bool)
# TODO: other options
@click.pass_context
def set_setup(ctx, device_id, node_addr, **kwargs):
    session = ctx.obj['session']
    devices = session.get_devices()
    device = next(d for d in devices if d['dev_id'] == device_id)
    nodes = session.get_nodes(device['dev_id'])
    node = next(n for n in nodes if n['addr'] == node_addr)

    session.set_setup(device['dev_id'], node, kwargs)


@smartbox.command(help='Show device away_status')
@click.pass_context
def device_away_status(ctx):
    session = ctx.obj['session']
    devices = session.get_devices()
    pp = pprint.PrettyPrinter(indent=4)

    for device in devices:
        print(f"{device['name']} (dev_id: {device['dev_id']})")
        device_away_status = session.get_device_away_status(device['dev_id'])
        pp.pprint(device_away_status)


@smartbox.command(help='Set device away_status (pass settings as extra args, e.g. mode=auto)')
@click.option('-d', '--device-id', required=True, help='Device ID to set away_status on')
@click.option('--away', type=bool)
@click.option('--enabled', type=bool)
@click.option('--forced', type=bool)
@click.pass_context
def set_device_away_status(ctx, device_id, **kwargs):
    session = ctx.obj['session']
    devices = session.get_devices()
    device = next(d for d in devices if d['dev_id'] == device_id)

    session.set_device_away_status(device['dev_id'], kwargs)


@smartbox.command(help='Open socket.io connection to device.')
@click.option('-d', '--device-id', required=True, help='Device ID to open socket for')
@click.pass_context
def socket(ctx, device_id):
    session = ctx.obj['session']
    verbose = ctx.obj['verbose']
    pp = pprint.PrettyPrinter(indent=4)

    def on_dev_data(data):
        _LOGGER.info("Received dev_data:")
        pp.pprint(data)

    def on_update(data):
        _LOGGER.info("Received update:")
        pp.pprint(data)

    socket_session = SocketSession(session, device_id, on_dev_data, on_update, verbose, add_sigint_handler=True)
    event_loop = asyncio.get_event_loop()
    task = event_loop.create_task(socket_session.run())
    event_loop.run_until_complete(task)
