# smartbox 3.0 (release candidate)

Python library for Smartbox heating systems (Helki and its resellers: Elnur,
Haverland, Climastar, Technotherm, HJM, …).

This is the 3.0 rewrite. It exposes the protocol and domain layers that the
Home Assistant integration uses, without importing Home Assistant. The 2.7
API remains available on the `main` branch and the `v2.7.0` tag.

## Install

From the tagged source:

```sh
pip install "smartbox @ https://github.com/jandado/smartbox/archive/refs/tags/v3.0.0-rc.1.tar.gz"
```

With the optional command line tool:

```sh
pip install "smartbox[cli] @ https://github.com/jandado/smartbox/archive/refs/tags/v3.0.0-rc.1.tar.gz"
```

For development:

```sh
uv sync --all-extras --dev
```

## Library

`SmartboxClient` owns the REST API and the token lifecycle. `smartbox.core`
contains the Home Assistant-free state engine, write confirmation and feed
supervision.

```python
import asyncio

from smartbox.core import SmartboxEngine
from smartbox.protocol import API_HOST, AuthConfig, ManagedTokenSource, SmartboxClient

CONFIG = AuthConfig(
    api_host=API_HOST,
    basic_auth_creds="<reseller Basic auth>",
    serial_id="1",
    username="you@example.com",
    password="secret",
    referer="https://app.helki.com/",
)

async def main() -> None:
    client = SmartboxClient(CONFIG)
    try:
        print(await client.get_devices())
    finally:
        await client.close()
```

The lower layers are Home Assistant-free and are tested without the HA test
harness.

## CLI

The CLI is an optional extra; the base package only depends on `aiohttp`.

Authentication options are `-a/--api-name`, `-b/--basic-auth-creds`,
`-u/--username`, `-p/--password`, `-r/--x-referer`, `-i/--x-serial-id`.
They also resolve from `SMARTBOX_*` environment variables, and the script
loads the nearest `.env` file. Command line values win over the environment,
which wins over `.env`.

```sh
smartbox -a api -u you@example.com -p secret devices
smartbox -a api -u you@example.com -p secret nodes
smartbox -a api -u you@example.com -p secret status
smartbox -a api -u you@example.com -p secret set-status -d DEV -n 0 --mode auto
smartbox -a api -u you@example.com -p secret set-boost-mode -d DEV -n 0 \
  --active --mode runback --temperature 21 --time-minutes 60
smartbox -a api -u you@example.com -p secret listen
```

`smartbox --help` lists every command. Reads print the raw wire payloads;
writes print the posted path and body. `listen` follows the shared `ws_user`
feed and can filter updates with `--device-id`.

## Layout

- `smartbox.protocol` — auth, REST client, `ws_user` transport, wire values
  and named writes.
- `smartbox.core` — versioned state store, merges, write confirmation,
  deadlines and feed supervision.
- `smartbox.cli` — the optional debugging CLI.

The canonical wire record lives in the sibling `hass-smartbox` repository
under `docs/` (`api-contract.md`, `api-ws-user.md`, `webapi-spec.md`);
`webapi-spec.md` in this repo is a pointer.

## Development

```sh
uv run pytest
uv run ruff check src tests
uv run mypy src
uv run tox
```

## Credits

The original library and the 2.7.x line are by Alberto Tudela, Delmael,
graham33 and the upstream contributors. This fork carries the 3.0 rewrite;
upstream collaboration is welcome.
