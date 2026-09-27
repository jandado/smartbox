import json
import pathlib
from unittest.mock import AsyncMock, MagicMock


def get_fixture_path(filename: str) -> pathlib.Path:
    """Get path of fixture."""
    return pathlib.Path(__file__).parent.joinpath("fixtures", filename)


def load_fixture(filename):
    """Load a fixture."""
    return get_fixture_path(filename).read_text(encoding="utf-8")


async def fake_get_request(*args, **kwargs):
    """Return fake data."""
    return json.loads(load_fixture(f"{args[1]}.json"))


def mock_api_response(payload):
    """Build an async-context-manager response returning ``payload``."""
    response = AsyncMock()
    response.__aenter__.return_value = response
    response.__aexit__.return_value = None
    response.json = AsyncMock(return_value=payload)
    response.raise_for_status = MagicMock()
    return response
