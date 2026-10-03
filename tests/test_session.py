import asyncio
import datetime
import gc
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import math
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, PropertyMock, patch

import aiohttp
from aiohttp import ClientSession
import pydantic
import pytest
import time_machine

from smartbox import (
    APIUnavailableError,
    InvalidAuthError,
    ResellerNotExistError,
    SmartboxError,
    SmartboxValidationError,
)
from smartbox.models import DefaultNodeSetup
from smartbox.session import (
    _DEFAULT_BACKOFF_FACTOR,
    _DEFAULT_RETRY_ATTEMPTS,
    AsyncSession,
    Session,
    _redact_body,
    _redacted_url,
)
from tests.common import fake_get_request, mock_api_response


@pytest.mark.asyncio
async def test_get_grouped_devices(async_smartbox_session):
    with patch.object(
        async_smartbox_session,
        "_api_request",
        new_callable=AsyncMock,
    ) as mock_api_request:
        url = "grouped_devs"
        mock_api_request.return_value = await fake_get_request(
            mock_api_request,
            url,
        )
        grouped_devices = await async_smartbox_session.get_grouped_devices()
        assert grouped_devices == mock_api_request.return_value
        mock_api_request.assert_called_once_with(url)
        async_smartbox_session.raw_response = False
        grouped_devices_model = (
            await async_smartbox_session.get_grouped_devices()
        )
        assert grouped_devices_model.root[0].name == grouped_devices[0]["name"]


@pytest.mark.asyncio
async def test_get_nodes(async_smartbox_session):
    for mock_device in await async_smartbox_session.get_devices():
        with patch.object(
            async_smartbox_session,
            "_api_request",
            new_callable=AsyncMock,
        ) as mock_api_request:
            url = f"devs/{mock_device['dev_id']}/mgr/nodes"
            mock_api_request.return_value = await fake_get_request(
                mock_api_request,
                url,
            )
            async_smartbox_session.raw_response = True
            nodes = await async_smartbox_session.get_nodes(
                device_id=mock_device["dev_id"],
            )
            assert nodes == mock_api_request.return_value["nodes"]
            mock_api_request.assert_called_with(url)
            async_smartbox_session.raw_response = False
            nodes_model = await async_smartbox_session.get_nodes(
                device_id=mock_device["dev_id"],
            )
            assert nodes_model[0].addr == nodes[0]["addr"]


@pytest.mark.asyncio
async def test_get_node_status(async_smartbox_session):
    for mock_device in await async_smartbox_session.get_devices():
        for mock_node in await async_smartbox_session.get_nodes(
            mock_device["dev_id"],
        ):
            with patch.object(
                async_smartbox_session,
                "_api_request",
                new_callable=AsyncMock,
            ) as mock_api_request:
                url = f"devs/{mock_device['dev_id']}/{mock_node['type']}/{mock_node['addr']}/status"
                mock_api_request.return_value = await fake_get_request(
                    mock_api_request,
                    url,
                )
                status = await async_smartbox_session.get_node_status(
                    mock_device["dev_id"],
                    mock_node,
                )
                assert status == mock_api_request.return_value
                mock_api_request.assert_called_with(url)

                async_smartbox_session.raw_response = False
                if mock_node["type"] != "pmo":
                    status_model = await async_smartbox_session.get_node_status(
                        mock_device["dev_id"],
                        mock_node,
                    )
                    assert status_model.act_duty == status["act_duty"]
                with pytest.raises(SmartboxValidationError) as exc_info:
                    mock_api_request.return_value = {
                        "sync_status": "synced",
                        "mode": "auto",
                    }
                    await async_smartbox_session.get_node_status(
                        mock_device["dev_id"],
                        mock_node,
                    )
                async_smartbox_session.raw_response = True
                assert isinstance(
                    exc_info.value.__cause__, pydantic.ValidationError
                )


@pytest.mark.asyncio
async def test_get_node_samples(async_smartbox_session):
    for mock_device in await async_smartbox_session.get_devices():
        for mock_node in await async_smartbox_session.get_nodes(
            mock_device["dev_id"],
        ):
            with patch.object(
                async_smartbox_session,
                "_api_request",
                new_callable=AsyncMock,
            ) as mock_api_request:
                start_time = 1737722209
                end_time = 1737729409
                url = f"devs/{mock_device['dev_id']}/{mock_node['type']}/{mock_node['addr']}/samples"

                mock_api_request.return_value = await fake_get_request(
                    mock_api_request,
                    url,
                )
                samples = await async_smartbox_session.get_node_samples(
                    mock_device["dev_id"],
                    mock_node,
                    start_time=start_time,
                    end_time=end_time,
                )
                assert samples == mock_api_request.return_value
                mock_api_request.assert_called_with(
                    url,
                    params={"start": start_time, "end": end_time},
                )

                async_smartbox_session.raw_response = False
                samples_model = await async_smartbox_session.get_node_samples(
                    mock_device["dev_id"],
                    mock_node,
                    start_time=start_time,
                    end_time=end_time,
                )
                assert samples_model.samples[0].counter == float(
                    samples["samples"][0]["counter"]
                )
                async_smartbox_session.raw_response = True


@pytest.mark.asyncio
async def test_get_node_samples_default_times(async_smartbox_session):
    """Default start and end times should be 1 hour before and after current time."""
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 1,
        "type": "htr",
        "installed": True,
        "lost": False,
    }

    with patch.object(
        async_smartbox_session,
        "_api_request",
        new_callable=AsyncMock,
    ) as mock_api_request:
        mock_api_request.return_value = {"samples": []}
        now = int(time.time())
        await async_smartbox_session.get_node_samples(
            device_id=mock_device_id,
            node=mock_node,
        )
        called_url = mock_api_request.call_args[0][0]
        called_params = mock_api_request.call_args[1]["params"]

        assert called_url == (
            f"devs/{mock_device_id}/{mock_node['type']}/{mock_node['addr']}/samples"
        )

        assert abs(called_params["start"] - (now - 3600)) <= 20
        assert abs(called_params["end"] - (now + 3600)) <= 20


@pytest.mark.asyncio
async def test_get_device_away_status(async_smartbox_session):
    for mock_device in await async_smartbox_session.get_devices():
        with patch.object(
            async_smartbox_session,
            "_api_request",
            new_callable=AsyncMock,
        ) as mock_api_request:
            url = f"devs/{mock_device['dev_id']}/mgr/away_status"
            mock_api_request.return_value = await fake_get_request(
                mock_api_request,
                url,
            )
            nodes = await async_smartbox_session.get_device_away_status(
                device_id=mock_device["dev_id"],
            )
            assert nodes == mock_api_request.return_value
            mock_api_request.assert_called_with(url)

            async_smartbox_session.raw_response = False
            nodes_model = await async_smartbox_session.get_device_away_status(
                device_id=mock_device["dev_id"],
            )
            assert nodes_model.away == nodes["away"]
            async_smartbox_session.raw_response = True


@pytest.mark.asyncio
async def test_set_device_away_status(async_smartbox_session):
    with patch.object(
        async_smartbox_session,
        "_api_post",
        new_callable=AsyncMock,
    ) as mock_api_post:
        mock_api_post.return_value = {}
        status_args = {"status": "away"}
        result = await async_smartbox_session.set_device_away_status(
            device_id="test_device",
            status_args=status_args,
        )
        assert result is None
        # The vendor app body shape {away, enabled}: enabled is defaulted
        # to true when the caller omits it.
        mock_api_post.assert_called_once_with(
            data={"status": "away", "enabled": True},
            path="devs/test_device/mgr/away_status",
        )
        # An explicit enabled is passed through untouched.
        result = await async_smartbox_session.set_device_away_status(
            device_id="test_device",
            status_args={"away": True, "enabled": False},
        )
        mock_api_post.assert_called_with(
            data={"away": True, "enabled": False},
            path="devs/test_device/mgr/away_status",
        )


@pytest.mark.asyncio
async def test_get_device_power_limit(async_smartbox_session):
    power = 100
    mock_node = {
        "name": "Living Room",
        "addr": 1,
        "type": "pmo",
        "installed": True,
        "lost": False,
    }
    with patch.object(
        async_smartbox_session,
        "_api_request",
        new_callable=AsyncMock,
    ) as mock_api_request:
        mock_api_request.return_value = {"power_limit": "100"}
        power_limit = await async_smartbox_session.get_device_power_limit(
            device_id="test_device",
        )
        assert power_limit == power
        mock_api_request.assert_called_once_with(
            "devs/test_device/htr_system/power_limit",
        )

        mock_api_request.return_value = {"power": "100"}
        power_limit = await async_smartbox_session.get_device_power_limit(
            device_id="test_device", node=mock_node
        )
        assert power_limit == power
        mock_api_request.assert_called_with(
            "devs/test_device/pmo/1/power",
        )


@pytest.mark.asyncio
async def test_set_device_power_limit(async_smartbox_session):
    mock_node = {
        "name": "Living Room",
        "addr": 1,
        "type": "pmo",
        "installed": True,
        "lost": False,
    }
    with patch.object(
        async_smartbox_session,
        "_api_post",
        new_callable=AsyncMock,
    ) as mock_api_post:
        mock_api_post.return_value = {}
        power_limit = 100
        await async_smartbox_session.set_device_power_limit(
            device_id="test_device",
            power_limit=power_limit,
        )
        mock_api_post.assert_called_once_with(
            data={"power_limit": str(power_limit)},
            path="devs/test_device/htr_system/power_limit",
        )

        await async_smartbox_session.set_device_power_limit(
            device_id="test_device",
            node=mock_node,
            power_limit=power_limit,
        )
        mock_api_post.assert_called_with(
            data={"power_limit": str(power_limit)},
            path="devs/test_device/pmo/1/power_limit",
        )


def test_session_get_homes(session):
    with patch.object(
        session._async,
        "get_homes",
        new_callable=AsyncMock,
    ) as mock_get_homes:
        mock_get_homes.return_value = []
        homes = session.get_homes()
        assert homes == []
        mock_get_homes.assert_called_once()


def test_session_get_grouped_devices(session):
    with patch.object(
        session._async,
        "get_grouped_devices",
        new_callable=AsyncMock,
    ) as mock_get_grouped_devices:
        mock_get_grouped_devices.return_value = []
        grouped_devices = session.get_grouped_devices()
        assert grouped_devices == []
        mock_get_grouped_devices.assert_called_once()


def test_session_get_nodes(session):
    with patch.object(
        session._async,
        "get_nodes",
        new_callable=AsyncMock,
    ) as mock_get_nodes:
        mock_get_nodes.return_value = []
        nodes = session.get_nodes(device_id="test_device")
        assert nodes == []
        mock_get_nodes.assert_called_once_with(device_id="test_device")


def test_session_get_device_away_status(session):
    with patch.object(
        session._async,
        "get_device_away_status",
        new_callable=AsyncMock,
    ) as mock_get_device_away_status:
        mock_get_device_away_status.return_value = {}
        away_status = session.get_device_away_status(device_id="test_device")
        assert away_status == {}
        mock_get_device_away_status.assert_called_once_with(
            device_id="test_device",
        )


def test_session_set_device_away_status(session):
    with patch.object(
        session._async,
        "set_device_away_status",
        new_callable=AsyncMock,
    ) as mock_set_device_away_status:
        mock_set_device_away_status.return_value = {}
        status_args = {"status": "away"}
        result = session.set_device_away_status(
            device_id="test_device",
            status_args=status_args,
        )
        assert result == {}
        mock_set_device_away_status.assert_called_once_with(
            device_id="test_device",
            status_args=status_args,
        )


def test_session_get_device_power_limit(session):
    power = 100
    with patch.object(
        session._async,
        "get_device_power_limit",
        new_callable=AsyncMock,
    ) as mock_get_device_power_limit:
        mock_get_device_power_limit.return_value = power
        power_limit = session.get_device_power_limit(device_id="test_device")
        assert power_limit == power
        mock_get_device_power_limit.assert_called_once_with(
            device_id="test_device",
            node=None,
        )


def test_session_set_device_power_limit(session):
    with patch.object(
        session._async,
        "set_device_power_limit",
        new_callable=AsyncMock,
    ) as mock_set_device_power_limit:
        mock_set_device_power_limit.return_value = None
        power_limit = 100
        session.set_device_power_limit(
            device_id="test_device",
            power_limit=power_limit,
        )
        mock_set_device_power_limit.assert_called_once_with(
            device_id="test_device",
            power_limit=power_limit,
            node=None,
        )


def test_session_get_devices(session):
    with patch.object(
        session._async,
        "get_devices",
        new_callable=AsyncMock,
    ) as mock_get_devices:
        mock_get_devices.return_value = []
        devices = session.get_devices()
        assert devices == []
        mock_get_devices.assert_called_once()

    with patch.object(
        session._async,
        "get_devices",
        new_callable=AsyncMock,
    ) as mock_get_devices:
        mock_get_devices.return_value = [
            {"id": "device1", "name": "Device 1"},
            {"id": "device2", "name": "Device 2"},
        ]
        devices = session.get_devices()
        assert devices == [
            {"id": "device1", "name": "Device 1"},
            {"id": "device2", "name": "Device 2"},
        ]
        mock_get_devices.assert_called_once()


@pytest.mark.asyncio
async def test_set_node_status(async_smartbox_session):
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 1,
        "type": "htr",
        "installed": True,
        "lost": False,
    }
    status_args = {"status": "active"}

    with patch.object(
        async_smartbox_session,
        "_api_post",
        new_callable=AsyncMock,
    ) as mock_api_post:
        mock_api_post.return_value = None
        result = await async_smartbox_session.set_node_status(
            device_id=mock_device_id,
            node=mock_node,
            status_args=status_args,
        )
        assert result is None
        mock_api_post.assert_called_once_with(
            data=status_args,
            path=f"devs/{mock_device_id}/{mock_node['type']}/{mock_node['addr']}/status",
        )

    # Test with temperature field without units
    status_args = {"stemp": 25}
    with pytest.raises(
        ValueError,
        match="Must supply unit with temperature fields",
    ):
        await async_smartbox_session.set_node_status(
            device_id=mock_device_id,
            node=mock_node,
            status_args=status_args,
        )


def test_session_set_node_status(session):
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 1,
        "type": "thermostat",
        "installed": True,
        "lost": False,
    }
    status_args = {"status": "active"}

    with patch.object(
        session._async,
        "set_node_status",
        new_callable=AsyncMock,
    ) as mock_set_node_status:
        mock_set_node_status.return_value = {}
        result = session.set_status(
            device_id=mock_device_id,
            node=mock_node,
            status_args=status_args,
        )
        assert result == {}
        mock_set_node_status.assert_called_once_with(
            device_id=mock_device_id,
            node=mock_node,
            status_args=status_args,
        )

    # Test with temperature field without units
    status_args = {"stemp": 25}
    with patch.object(
        session._async,
        "set_node_status",
        new_callable=AsyncMock,
    ) as mock_set_node_status:
        mock_set_node_status.side_effect = ValueError(
            "Must supply unit with temperature fields",
        )
        with pytest.raises(
            ValueError,
            match="Must supply unit with temperature fields",
        ):
            session.set_status(
                device_id=mock_device_id,
                node=mock_node,
                status_args=status_args,
            )


def test_session_get_setup(session):
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 1,
        "type": "thermostat",
        "installed": True,
        "lost": False,
    }

    with patch.object(
        session._async,
        "get_node_setup",
        new_callable=AsyncMock,
    ) as mock_get_node_setup:
        mock_get_node_setup.return_value = {}
        setup = session.get_setup(device_id=mock_device_id, node=mock_node)
        assert setup == {}
        mock_get_node_setup.assert_called_once_with(
            device_id=mock_device_id,
            node=mock_node,
        )


def test_session_set_status(session):
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 1,
        "type": "thermostat",
        "installed": True,
        "lost": False,
    }
    status_args = {"status": "active"}

    with patch.object(
        session._async,
        "set_node_status",
        new_callable=AsyncMock,
    ) as mock_set_node_status:
        mock_set_node_status.return_value = {"status": "active"}
        result = session.set_status(
            device_id=mock_device_id,
            node=mock_node,
            status_args=status_args,
        )
        assert result == {"status": "active"}
        mock_set_node_status.assert_called_once_with(
            device_id=mock_device_id,
            node=mock_node,
            status_args=status_args,
        )

    # Test with temperature field without units
    status_args = {"stemp": 25}
    with patch.object(
        session._async,
        "set_node_status",
        new_callable=AsyncMock,
    ) as mock_set_node_status:
        mock_set_node_status.side_effect = ValueError(
            "Must supply unit with temperature fields",
        )
        with pytest.raises(
            ValueError,
            match="Must supply unit with temperature fields",
        ):
            session.set_status(
                device_id=mock_device_id,
                node=mock_node,
                status_args=status_args,
            )


@pytest.mark.asyncio
async def test_set_node_setup(async_smartbox_session):
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 1,
        "type": "acm",
        "installed": True,
        "lost": False,
    }
    setup_args = {"setting1": "value1"}

    with (
        patch.object(
            async_smartbox_session,
            "_api_request",
            new_callable=AsyncMock,
        ) as mock_api_request,
        patch.object(
            async_smartbox_session,
            "_api_post",
            new_callable=AsyncMock,
        ) as mock_api_post,
    ):
        # The raw setup carries a key the Pydantic models do not declare
        # (``counter_offset``). It must survive the read-modify-write
        # cycle and be re-posted unchanged, otherwise it would be wiped
        # on the device.
        mock_api_request.return_value = {
            "setting2": "value2",
            "counter_offset": 42,
        }
        mock_api_post.return_value = None

        result = await async_smartbox_session.set_node_setup(
            device_id=mock_device_id,
            node=mock_node,
            setup_args=setup_args,
        )
        assert result is None
        setup_path = (
            f"devs/{mock_device_id}/{mock_node['type']}/"
            f"{mock_node['addr']}/setup"
        )
        mock_api_request.assert_awaited_once_with(setup_path)
        mock_api_post.assert_called_once_with(
            data={
                "setting1": "value1",
                "setting2": "value2",
                "counter_offset": 42,
            },
            path=setup_path,
        )

        # The internal read stays raw even when the session is in typed
        # mode: the round-trip must never serialize through the model.
        mock_api_request.reset_mock()
        mock_api_post.reset_mock()
        async_smartbox_session.raw_response = False
        mock_api_request.return_value = {"counter_offset": 7}

        result = await async_smartbox_session.set_node_setup(
            device_id=mock_device_id,
            node=mock_node,
            setup_args=setup_args,
        )
        assert result is None
        mock_api_post.assert_called_once_with(
            data={"setting1": "value1", "counter_offset": 7},
            path=setup_path,
        )
        async_smartbox_session.raw_response = True


@pytest.mark.asyncio
async def test_get_node_prog(async_smartbox_session):
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 5,
        "type": "htr",
        "installed": True,
        "lost": False,
    }
    raw_prog = json.loads(
        (
            Path(__file__).parent / "fixtures/live/htr_prog_addr5.json"
        ).read_text()
    )
    url = f"devs/{mock_device_id}/htr/{mock_node['addr']}/prog"
    with patch.object(
        async_smartbox_session,
        "_api_request",
        new_callable=AsyncMock,
    ) as mock_api_request:
        mock_api_request.return_value = raw_prog
        prog = await async_smartbox_session.get_node_prog(
            device_id=mock_device_id,
            node=mock_node,
        )
        assert prog == raw_prog
        mock_api_request.assert_called_once_with(url)
        async_smartbox_session.raw_response = False
        prog_model = await async_smartbox_session.get_node_prog(
            device_id=mock_device_id,
            node=mock_node,
        )
        assert prog_model.prog == raw_prog["prog"]
        assert prog_model.sync_status == raw_prog["sync_status"]


@pytest.mark.asyncio
async def test_set_node_prog(async_smartbox_session):
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 5,
        "type": "htr",
        "installed": True,
        "lost": False,
    }
    raw_prog = json.loads(
        (
            Path(__file__).parent / "fixtures/live/htr_prog_addr5.json"
        ).read_text()
    )
    prog_args = {"prog": {**raw_prog["prog"], "0": [2] * 24}}
    with (
        patch.object(
            async_smartbox_session,
            "get_node_prog",
            new_callable=AsyncMock,
        ) as mock_get_node_prog,
        patch.object(
            async_smartbox_session,
            "_api_post",
            new_callable=AsyncMock,
        ) as mock_api_post,
    ):
        mock_get_node_prog.return_value = raw_prog
        result = await async_smartbox_session.set_node_prog(
            device_id=mock_device_id,
            node=mock_node,
            prog_args=prog_args,
        )
        assert result is None
        mock_api_post.assert_called_once_with(
            data={"prog": {**raw_prog["prog"], "0": [2] * 24}},
            path=f"devs/{mock_device_id}/htr/{mock_node['addr']}/prog",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kwargs_name", "suffix"),
    [
        ("set_node_mode", "mode_args", "mode"),
        ("set_node_lock", "lock_args", "lock"),
        ("set_node_boost", "boost_args", "boost"),
        ("set_node_prog_temps", "temps_args", "prog_temps"),
    ],
)
async def test_dedicated_endpoint_wrappers(
    async_smartbox_session, method, kwargs_name, suffix
):
    mock_node = {
        "name": "Living Room",
        "addr": 5,
        "type": "htr",
        "installed": True,
        "lost": False,
    }
    payload = {"x": 1}
    with patch.object(
        async_smartbox_session,
        "_api_post",
        new_callable=AsyncMock,
    ) as mock_api_post:
        result = await getattr(async_smartbox_session, method)(
            device_id="test_device",
            node=mock_node,
            **{kwargs_name: payload},
        )
        assert result is None
        mock_api_post.assert_called_once_with(
            data=payload,
            path=f"devs/test_device/htr/{mock_node['addr']}/{suffix}",
        )


_HTR_NODE = {
    "name": "Living Room",
    "addr": 5,
    "type": "htr",
    "installed": True,
    "lost": False,
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kwargs", "path"),
    [
        (
            "get_device_discovery",
            {"device_id": "test_device"},
            "devs/test_device/mgr/discovery",
        ),
        (
            "get_device_rtc_time",
            {"device_id": "test_device"},
            "devs/test_device/mgr/rtc/time",
        ),
        (
            "get_node_power",
            {"device_id": "test_device", "node": _HTR_NODE},
            "devs/test_device/htr/5/power",
        ),
        ("get_group_geo_data", {"group_id": "home1"}, "groups/home1/geo_data"),
        (
            "get_group_extra_data",
            {"group_id": "home1"},
            "groups/home1/extra_data",
        ),
    ],
)
async def test_management_get_endpoints(
    async_smartbox_session, method, kwargs, path
):
    with patch.object(
        async_smartbox_session,
        "_api_request",
        new_callable=AsyncMock,
    ) as mock_api_request:
        mock_api_request.return_value = {}
        result = await getattr(async_smartbox_session, method)(**kwargs)
        assert result == {}
        mock_api_request.assert_called_once_with(path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kwargs", "data", "path"),
    [
        (
            "set_device_discovery",
            {
                "device_id": "test_device",
                "discovery_args": {"discovery": "on"},
            },
            {"discovery": "on"},
            "devs/test_device/mgr/discovery",
        ),
        (
            "set_device_name",
            {"device_id": "test_device", "name": "New name"},
            {"name": "New name"},
            "devs/test_device/name",
        ),
        (
            "move_device_to_group",
            {"device_id": "test_device", "group_args": {"groupid": "home1"}},
            {"groupid": "home1"},
            "devs/test_device/group",
        ),
        (
            "set_node_name",
            {"device_id": "test_device", "node": _HTR_NODE, "name": "New"},
            {"name": "New"},
            "devs/test_device/htr/5/name",
        ),
        (
            "set_node_select",
            {
                "device_id": "test_device",
                "node": _HTR_NODE,
                "select_args": {"select": True},
            },
            {"select": True},
            "devs/test_device/htr/5/select",
        ),
        (
            "invite_user",
            {
                "user_id": "u1",
                "home_id": "home1",
                "email": "a@b.c",
                "confirmation_url": "https://x/invite-confirm/nserie17",
            },
            {
                "email": "a@b.c",
                "groupid": "home1",
                "confirmation_url": "https://x/invite-confirm/nserie17",
            },
            "users/u1/invite",
        ),
    ],
)
async def test_management_post_endpoints(
    async_smartbox_session, method, kwargs, data, path
):
    with patch.object(
        async_smartbox_session,
        "_api_post",
        new_callable=AsyncMock,
    ) as mock_api_post:
        mock_api_post.return_value = {}
        await getattr(async_smartbox_session, method)(**kwargs)
        mock_api_post.assert_called_once_with(data=data, path=path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kwargs", "path", "data"),
    [
        (
            "delete_device",
            {"device_id": "test_device"},
            "devs/test_device",
            None,
        ),
        (
            "delete_node",
            {"device_id": "test_device", "node": _HTR_NODE},
            "devs/test_device/htr/5",
            {"purge": False},
        ),
        (
            "revoke_invite",
            {"user_id": "u1", "home_id": "home1", "email": "a@b.c"},
            "users/u1/invite",
            {"groupid": "home1", "email": "a@b.c"},
        ),
    ],
)
async def test_delete_endpoints(
    async_smartbox_session, method, kwargs, path, data
):
    with patch.object(
        async_smartbox_session,
        "_api_delete",
        new_callable=AsyncMock,
    ) as mock_api_delete:
        await getattr(async_smartbox_session, method)(**kwargs)
        if data is None:
            mock_api_delete.assert_called_once_with(path)
        else:
            mock_api_delete.assert_called_once_with(path, data=data)


@pytest.mark.asyncio
async def test_get_quiet_home_notifications(async_smartbox_session):
    with patch.object(
        async_smartbox_session,
        "_api_get",
        new_callable=AsyncMock,
    ) as mock_api_get:
        mock_api_get.return_value = {}
        result = await async_smartbox_session.get_quiet_home_notifications(
            "home1",
        )
        assert result == {}
        mock_api_get.assert_called_once_with(
            "/api/notifications/v1/home1/presence/config",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kwargs", "api_path", "data", "auth"),
    [
        (
            "set_quiet_home_notifications",
            {
                "group_id": "home1",
                "notification_data": {"enabled": True},
            },
            "/api/notifications/v1/home1/presence/config",
            {"enabled": True},
            None,
        ),
        (
            "test_quiet_home_notifications",
            {"group_id": "home1"},
            "/api/notifications/v1/home1/test",
            None,
            None,
        ),
        (
            "confirm_invite",
            {"user_id": "u1", "password": "pw", "code": "c"},
            "/api/v2/users/u1/invite_confirmation",
            {"pass": "pw", "code": "c"},
            False,
        ),
    ],
)
async def test_host_relative_post_endpoints(
    async_smartbox_session, method, kwargs, api_path, data, auth
):
    with patch.object(
        async_smartbox_session,
        "_api_post_path",
        new_callable=AsyncMock,
    ) as mock_api_post_path:
        mock_api_post_path.return_value = {}
        result = await getattr(async_smartbox_session, method)(**kwargs)
        assert result == {}
        if auth is None:
            if data is None:
                mock_api_post_path.assert_called_once_with(api_path)
            else:
                mock_api_post_path.assert_called_once_with(api_path, data=data)
        else:
            mock_api_post_path.assert_called_once_with(
                api_path, data=data, auth=auth
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kwargs", "api_path", "params"),
    [
        (
            "get_encrypted_wifi_credentials",
            {"ssid": "s", "wifi_password": "p"},
            "/api/v2/encrypted_wifi_credentials",
            {"ssid": "s", "pass": "p"},
        ),
        (
            "get_coordinates",
            {
                "country": "c",
                "state": "st",
                "city": "ci",
                "zip_code": "z",
            },
            "/api/location/v1/coordinates",
            {"country": "c", "state": "st", "city": "ci", "zip": "z"},
        ),
    ],
)
async def test_param_get_endpoints(
    async_smartbox_session, method, kwargs, api_path, params
):
    with patch.object(
        async_smartbox_session,
        "_api_get",
        new_callable=AsyncMock,
    ) as mock_api_get:
        mock_api_get.return_value = {}
        result = await getattr(async_smartbox_session, method)(**kwargs)
        assert result == {}
        mock_api_get.assert_called_once_with(api_path, params=params)


def test_session_set_prog(session):
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 5,
        "type": "htr",
        "installed": True,
        "lost": False,
    }
    prog_args = {"prog": {"0": [0] * 24}}

    with patch.object(
        session._async,
        "set_node_prog",
        new_callable=AsyncMock,
    ) as mock_set_node_prog:
        mock_set_node_prog.return_value = prog_args
        result = session.set_prog(
            device_id=mock_device_id,
            node=mock_node,
            prog_args=prog_args,
        )
        assert result == prog_args
        mock_set_node_prog.assert_called_once_with(
            device_id=mock_device_id,
            node=mock_node,
            prog_args=prog_args,
        )


def test_session_set_node_setup(session):
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 1,
        "type": "thermostat",
        "installed": True,
        "lost": False,
    }
    setup_args = {"setting1": "value1"}

    with patch.object(
        session._async,
        "set_node_setup",
        new_callable=AsyncMock,
    ) as mock_set_node_setup:
        mock_set_node_setup.return_value = {
            "setting1": "value1",
            "setting2": "value2",
        }
        result = session.set_setup(
            device_id=mock_device_id,
            node=mock_node,
            setup_args=setup_args,
        )
        assert result == {"setting1": "value1", "setting2": "value2"}
        mock_set_node_setup.assert_called_once_with(
            device_id=mock_device_id,
            node=mock_node,
            setup_args=setup_args,
        )


@pytest.mark.asyncio
async def test_async_session_init():
    api_name = "test_api"
    basic_auth_credentials = "test_credentials"
    username = "test_user"
    password = "test_password"
    retry_attempts = 3
    backoff_factor = 0.2
    serial_id = 10
    referer = "http"
    websession = ClientSession()

    session = AsyncSession(
        api_name=api_name,
        basic_auth_credentials=basic_auth_credentials,
        username=username,
        password=password,
        websession=websession,
        retry_attempts=retry_attempts,
        backoff_factor=backoff_factor,
        x_serial_id=serial_id,
        x_referer=referer,
    )

    assert session.api_name == api_name
    assert session.api_host == f"https://{api_name}.helki.com"
    assert session._retry_attempts == retry_attempts
    assert math.isclose(session._backoff_factor, backoff_factor)
    assert session._username == username
    assert session._password == password
    assert session._access_token == ""
    assert session._client_session == websession
    assert session._headers["x-serialid"] == str(serial_id)
    assert session._headers["x-referer"] == referer
    await websession.close()


@pytest.mark.asyncio
async def test_async_session_init_defaults(reseller):
    api_name = "test_api"
    username = "test_user"
    password = "test_password"

    session = AsyncSession(
        api_name=api_name,
        username=username,
        password=password,
    )

    assert session.api_name == api_name
    assert session._api_host == f"https://{api_name}.helki.com"
    assert session._retry_attempts == _DEFAULT_RETRY_ATTEMPTS
    assert session._backoff_factor == _DEFAULT_BACKOFF_FACTOR
    assert session._username == username
    assert session._password == password
    assert session._access_token == ""
    assert session._client_session is None
    assert "x-serialid" in session._headers
    assert "x-referer" in session._headers


@pytest.mark.asyncio
async def test_negative_backoff_factor_rejected():
    """A negative backoff_factor would silently disable backoff (clamped sleep)."""
    with pytest.raises(ValueError, match="backoff_factor"):
        AsyncSession(
            api_name="test_api",
            username="test_user",
            password="test_password",
            backoff_factor=-0.5,
        )


@pytest.mark.asyncio
async def test_authentication_success(async_session, caplog):
    credentials = {
        "grant_type": "password",
        "username": "test_user",
        "password": "test_password",
    }
    token_response = {
        "access_token": "test_access_token",
        "refresh_token": "test_refresh_token",
        "expires_in": 5,
        "token_type": "test_token_type",
    }

    with patch.object(
        async_session.client,
        "post",
    ) as mock_post:
        mock_response = MagicMock()
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None

        mock_response.json = AsyncMock(return_value=token_response)
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        with caplog.at_level(logging.WARNING, logger="smartbox.session"):
            await async_session._authentication(credentials)

        assert async_session._access_token == "test_access_token"
        assert async_session.access_token == "test_access_token"
        assert async_session._refresh_token == "test_refresh_token"
        assert async_session.refresh_token == "test_refresh_token"
        assert async_session._expires_at > datetime.datetime.now(datetime.UTC)

        assert "below minimum lifetime" in caplog.text
        assert "will refresh again on next operation" in caplog.text

        mock_post.assert_called_once_with(
            url=f"{async_session._api_host}/client/token",
            headers={
                "authorization": f"Basic {async_session.reseller.basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
                "x-referer": "http",
                "x-serialid": "10",
            },
            data=credentials,
        )


@pytest.mark.asyncio
async def test_authentication_invalid_response(async_session):
    credentials = {
        "grant_type": "password",
        "username": "test_user",
        "password": "test_password",
    }
    invalid_response = {"invalid_key": "invalid_value"}

    with patch.object(
        async_session.client,
        "post",
    ) as mock_post:
        mock_response = MagicMock()
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None

        mock_response.json = AsyncMock(return_value=invalid_response)
        mock_response.raise_for_status = MagicMock()

        mock_post.return_value = mock_response

        with pytest.raises(
            SmartboxError,
            match="malformed token payload",
        ):
            await async_session._authentication(credentials)

        mock_post.assert_called_once_with(
            url=f"{async_session._api_host}/client/token",
            headers={
                "authorization": f"Basic {async_session.reseller.basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
                "x-serialid": f"{async_session.reseller.serial_id}",
                "x-referer": f"{async_session.reseller.web_url}",
            },
            data=credentials,
        )


@pytest.mark.asyncio
async def test_authentication_client_response_error(async_session):
    credentials = {
        "grant_type": "password",
        "username": "test_user",
        "password": "test_password",
    }

    with patch.object(
        async_session.client,
        "post",
    ) as mock_post:
        mock_post.side_effect = aiohttp.ClientResponseError(
            request_info=None,
            history=None,
            status=401,
            message="Unauthorized",
        )

        with pytest.raises(InvalidAuthError):
            await async_session._authentication(credentials)

        mock_post.assert_called_once_with(
            url=f"{async_session._api_host}/client/token",
            headers={
                "authorization": f"Basic {async_session.reseller.basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
                "x-serialid": f"{async_session.reseller.serial_id}",
                "x-referer": f"{async_session.reseller.web_url}",
            },
            data=credentials,
        )


@pytest.mark.asyncio
async def test_authentication_client_response_unavailable(async_session):
    credentials = {
        "grant_type": "password",
        "username": "test_user",
        "password": "test_password",
    }

    with patch.object(
        async_session.client,
        "post",
    ) as mock_post:
        mock_post.side_effect = aiohttp.ClientConnectionError()

        with pytest.raises(APIUnavailableError):
            await async_session._authentication(credentials)

        mock_post.assert_called_once_with(
            url=f"{async_session._api_host}/client/token",
            headers={
                "authorization": f"Basic {async_session.reseller.basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
                "x-serialid": f"{async_session.reseller.serial_id}",
                "x-referer": f"{async_session.reseller.web_url}",
            },
            data=credentials,
        )


@pytest.mark.asyncio
async def test_health_check_success(async_session):
    with patch.object(
        async_session.client,
        "request",
    ) as mock_request:
        mock_request.return_value = mock_api_response({"status": "ok"})

        result = await async_session.health_check()
        assert result == {"status": "ok"}
        expected_headers = {
            k: v
            for k, v in async_session._headers.items()
            if k != "Authorization"
        }
        mock_request.assert_called_once_with(
            "get",
            f"{async_session._api_host}/health_check",
            headers=expected_headers,
            params=None,
            data=None,
        )


@pytest.mark.asyncio
async def test_health_check_api_unavailable(async_session):
    with patch.object(
        async_session.client,
        "request",
    ) as mock_request:
        mock_request.side_effect = aiohttp.ClientConnectionError()

        with pytest.raises(APIUnavailableError):
            await async_session.health_check()

        mock_request.assert_called_once_with(
            "get",
            f"{async_session._api_host}/health_check",
            headers=ANY,
            params=None,
            data=None,
        )


@pytest.mark.asyncio
async def test_api_version_success(async_session):
    version_response = {
        "major": "1",
        "minor": "53",
        "subminor": "2",
        "commit": "NULL",
    }
    with patch.object(
        async_session.client,
        "request",
    ) as mock_request:
        mock_request.return_value = mock_api_response(version_response)

        result = await async_session.api_version()
        assert result == version_response
        mock_request.assert_called_once_with(
            "get",
            f"{async_session._api_host}/version",
            headers=ANY,
            params=None,
            data=None,
        )


@pytest.mark.asyncio
async def test_api_version_unavailable(async_session):
    with patch.object(
        async_session.client,
        "request",
    ) as mock_request:
        mock_request.side_effect = aiohttp.ClientConnectionError()

        with pytest.raises(APIUnavailableError):
            await async_session.api_version()

        mock_request.assert_called_once_with(
            "get",
            f"{async_session._api_host}/version",
            headers=ANY,
            params=None,
            data=None,
        )


@pytest.mark.asyncio
async def test_api_request_success(async_session):
    path = "test_path"
    expected_response = {"key": "value"}

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ) as mock_check_refresh_auth,
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.return_value = mock_api_response(expected_response)

        result = await async_session._api_request(path)
        mock_check_refresh_auth.assert_called_once()
        assert result == expected_response
        mock_request.assert_called_once_with(
            "get",
            f"{async_session._api_host}/api/v2/{path}",
            headers=async_session._headers,
            params=None,
            data=None,
        )


@pytest.mark.asyncio
async def test_api_request_check_refresh_auth_called(async_session):
    path = "test_path"

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ) as mock_check_refresh_auth,
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.return_value = mock_api_response({})

        await async_session._api_request(path)
        mock_check_refresh_auth.assert_called_once()
        mock_request.assert_called_once_with(
            "get",
            f"{async_session._api_host}/api/v2/{path}",
            headers=async_session._headers,
            params=None,
            data=None,
        )


@pytest.mark.asyncio
async def test_api_request_client_connection_error(async_session):
    path = "test_path"
    async_session._retry_attempts = 1

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ) as mock_check_refresh_auth,
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.side_effect = aiohttp.ClientConnectionError()

        with pytest.raises(APIUnavailableError):
            await async_session._api_request(path)

        mock_check_refresh_auth.assert_called_once()
        assert mock_request.call_count == 1


@pytest.mark.asyncio
async def test_api_request_retries_on_connection_error(async_session):
    path = "test_path"
    async_session._backoff_factor = 0.01

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ),
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.side_effect = [
            aiohttp.ClientConnectionError(),
            mock_api_response({"ok": 1}),
        ]

        result = await async_session._api_request(path)
        assert result == {"ok": 1}
        assert mock_request.call_count == 2


def test_error_hierarchy_has_common_root():
    """Every public error is catchable as ``SmartboxError``.

    ``APIUnavailableError`` keeps the ``aiohttp.ClientConnectionError``
    base for backwards compatibility (consumers and the token path
    catch that type directly).
    """
    assert issubclass(InvalidAuthError, SmartboxError)
    assert issubclass(APIUnavailableError, SmartboxError)
    assert issubclass(ResellerNotExistError, SmartboxError)
    assert issubclass(SmartboxValidationError, SmartboxError)
    assert issubclass(APIUnavailableError, aiohttp.ClientConnectionError)


@pytest.mark.asyncio
async def test_api_request_client_response_error(async_session):
    path = "test_path"

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ) as mock_check_refresh_auth,
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.side_effect = aiohttp.ClientResponseError(
            request_info=None,
            history=None,
            status=400,
            message="Bad Request",
        )

        with pytest.raises(SmartboxError):
            await async_session._api_request(path)

        mock_check_refresh_auth.assert_called_once()
        assert mock_request.call_count == 1


@pytest.mark.asyncio
async def test_api_request_5xx_raises_api_unavailable_and_retries(
    async_session,
):
    """5xx is transient unavailability: mapped and retried on GETs."""
    path = "test_path"
    async_session._backoff_factor = 0.01

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ),
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.side_effect = [
            aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=None,
                status=503,
                message="Service Unavailable",
            ),
            mock_api_response({"ok": 1}),
        ]

        result = await async_session._api_request(path)
        assert result == {"ok": 1}
        assert mock_request.call_count == 2


@pytest.mark.asyncio
async def test_api_request_timeout_raises_api_unavailable(async_session):
    """A wedged request's TimeoutError must not leak raw to consumers.

    aiohttp's total-timeout raises a plain TimeoutError, which is NOT an
    aiohttp.ClientConnectionError — without this mapping it would escape
    the uniform error handling and skip the GET retry entirely.
    """
    path = "test_path"
    async_session._retry_attempts = 1

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ),
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.side_effect = TimeoutError()

        with pytest.raises(APIUnavailableError):
            await async_session._api_request(path)


@pytest.mark.asyncio
async def test_api_request_401_raises_invalid_auth_error(async_session):
    path = "test_path"

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ) as mock_check_refresh_auth,
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.side_effect = aiohttp.ClientResponseError(
            request_info=None,
            history=None,
            status=401,
            message="Unauthorized",
        )

        with pytest.raises(InvalidAuthError):
            await async_session._api_request(path)

        # One re-auth + resend, then the persistent 401 surfaces.
        assert mock_request.call_count == 2
        assert mock_check_refresh_auth.await_count == 2


@pytest.mark.asyncio
async def test_api_post_success(async_session):
    path = "test_path"
    data = {"key": "value"}
    expected_response = {"response_key": "response_value"}

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ) as mock_check_refresh_auth,
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.return_value = mock_api_response(expected_response)

        result = await async_session._api_post(data, path)
        mock_check_refresh_auth.assert_called_once()
        assert result == expected_response
        mock_request.assert_called_once_with(
            "post",
            f"{async_session._api_host}/api/v2/{path}",
            data=json.dumps(data),
            headers=async_session._headers,
            params=None,
        )


@pytest.mark.asyncio
async def test_api_post_check_refresh_auth_called(async_session):
    path = "test_path"
    data = {"key": "value"}

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ) as mock_check_refresh_auth,
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.return_value = mock_api_response({})

        await async_session._api_post(data, path)
        mock_check_refresh_auth.assert_called_once()
        mock_request.assert_called_once_with(
            "post",
            f"{async_session._api_host}/api/v2/{path}",
            data=json.dumps(data),
            headers=async_session._headers,
            params=None,
        )


@pytest.mark.asyncio
async def test_api_post_client_connection_error(async_session):
    path = "test_path"
    data = {"key": "value"}
    async_session._retry_attempts = 1

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ) as mock_check_refresh_auth,
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.side_effect = aiohttp.ClientConnectionError()

        with pytest.raises(APIUnavailableError):
            await async_session._api_post(data, path)

        mock_check_refresh_auth.assert_called_once()
        assert mock_request.call_count == 1


@pytest.mark.asyncio
async def test_api_post_client_response_error(async_session):
    path = "test_path"
    data = {"key": "value"}

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ) as mock_check_refresh_auth,
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.side_effect = aiohttp.ClientResponseError(
            request_info=None,
            history=None,
            status=400,
            message="Bad Request",
        )

        with pytest.raises(SmartboxError):
            await async_session._api_post(data, path)

        mock_check_refresh_auth.assert_called_once()
        assert mock_request.call_count == 1


@pytest.mark.asyncio
async def test_api_post_5xx_raises_api_unavailable(async_session):
    """POSTs are mapped like GETs but never retried (no double-write)."""
    path = "test_path"
    data = {"key": "value"}

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ),
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.side_effect = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=None,
            status=503,
            message="Service Unavailable",
        )

        with pytest.raises(APIUnavailableError):
            await async_session._api_post(data, path)

        assert mock_request.call_count == 1


@pytest.mark.asyncio
async def test_api_post_401_raises_invalid_auth_error(async_session):
    path = "test_path"
    data = {"key": "value"}

    with (
        patch.object(
            async_session,
            "check_refresh_auth",
            new_callable=AsyncMock,
        ) as mock_check_refresh_auth,
        patch.object(
            async_session.client,
            "request",
        ) as mock_request,
    ):
        mock_request.side_effect = aiohttp.ClientResponseError(
            request_info=None,
            history=None,
            status=401,
            message="Unauthorized",
        )

        with pytest.raises(InvalidAuthError):
            await async_session._api_post(data, path)

        # One re-auth + resend, then the persistent 401 surfaces.
        assert mock_request.call_count == 2
        assert mock_check_refresh_auth.await_count == 2


@pytest.mark.asyncio
async def test_check_refresh_auth_token_expired(async_session):
    async_session._access_token = "test_access_token"
    async_session._expires_at = datetime.datetime.now(
        datetime.UTC
    ) - datetime.timedelta(
        seconds=10,
    )
    async_session._refresh_token = "test_refresh_token"

    with patch.object(
        async_session,
        "_authentication",
        new_callable=AsyncMock,
    ) as mock_authentication:
        await async_session.check_refresh_auth()
        mock_authentication.assert_called_once_with(
            {
                "grant_type": "refresh_token",
                "refresh_token": async_session._refresh_token,
            },
        )


@pytest.mark.asyncio
async def test_get_devices_raw_response_false(async_smartbox_session):
    with patch.object(
        async_smartbox_session,
        "_api_request",
        new_callable=AsyncMock,
    ) as mock_api_request:
        mock_api_request.return_value = {
            "invited_to": [],
            "devs": [
                {
                    "dev_id": "device1",
                    "name": "Device 1",
                    "product_id": "prod123",
                    "fw_version": "1.0.0",
                    "serial_id": "serial123",
                },
                {
                    "dev_id": "device2",
                    "name": "Device 2",
                    "product_id": "prod123",
                    "fw_version": "1.0.0",
                    "serial_id": "serial123",
                },
            ],
        }
        async_smartbox_session.raw_response = False
        devices = await async_smartbox_session.get_devices()
        assert devices.devs[0].dev_id == "device1"
        assert devices.devs[1].dev_id == "device2"
        mock_api_request.assert_called_once_with("devs")


@pytest.mark.asyncio
async def test_get_node_setup(async_smartbox_session):
    for mock_device in await async_smartbox_session.get_devices():
        mock_device_id = mock_device["dev_id"]
        for mock_node in await async_smartbox_session.get_nodes(mock_device_id):
            with patch.object(
                async_smartbox_session,
                "_api_request",
                new_callable=AsyncMock,
            ) as mock_api_request:
                url = f"devs/{mock_device_id}/{mock_node['type']}/{mock_node['addr']}/setup"
                mock_api_request.return_value = await fake_get_request(
                    mock_api_request,
                    url,
                )

                async_smartbox_session.raw_response = True
                setup = await async_smartbox_session.get_node_setup(
                    device_id=mock_device_id,
                    node=mock_node,
                )
                assert setup == mock_api_request.return_value
                mock_api_request.assert_called_with(url)

                async_smartbox_session.raw_response = False
                setup_model = await async_smartbox_session.get_node_setup(
                    device_id=mock_device_id,
                    node=mock_node,
                )
                if isinstance(setup_model, DefaultNodeSetup):
                    assert setup_model.away_mode == setup["away_mode"]
                with pytest.raises(SmartboxValidationError) as exc_info:
                    mock_api_request.return_value = {
                        "sync_status": "synced",
                        "control_mode": 1,
                    }
                    await async_smartbox_session.get_node_setup(
                        device_id=mock_device_id, node=mock_node
                    )
                assert isinstance(
                    exc_info.value.__cause__, pydantic.ValidationError
                )
                async_smartbox_session.raw_response = True


@pytest.mark.asyncio
async def test_get_node_version(async_smartbox_session):
    for mock_device in await async_smartbox_session.get_devices():
        mock_device_id = mock_device["dev_id"]
        for mock_node in await async_smartbox_session.get_nodes(mock_device_id):
            with patch.object(
                async_smartbox_session,
                "_api_request",
                new_callable=AsyncMock,
            ) as mock_api_request:
                url = f"devs/{mock_device_id}/{mock_node['type']}/{mock_node['addr']}/version"
                mock_api_request.return_value = await fake_get_request(
                    mock_api_request,
                    url,
                )

                async_smartbox_session.raw_response = True
                version = await async_smartbox_session.get_node_version(
                    device_id=mock_device_id,
                    node=mock_node,
                )
                assert version == mock_api_request.return_value
                mock_api_request.assert_called_with(url)

                async_smartbox_session.raw_response = False
                version_model = await async_smartbox_session.get_node_version(
                    device_id=mock_device_id,
                    node=mock_node,
                )
                assert version_model.fw_version == version["fw_version"]
                assert version_model.hw_version == version["hw_version"]
                assert version_model.uid == version["uid"]
                assert version_model.pid == version["pid"]

                with pytest.raises(SmartboxValidationError) as exc_info:
                    mock_api_request.return_value = {"fw_version": "1.0.0"}
                    await async_smartbox_session.get_node_version(
                        device_id=mock_device_id,
                        node=mock_node,
                    )
                assert isinstance(
                    exc_info.value.__cause__, pydantic.ValidationError
                )
                async_smartbox_session.raw_response = True


@pytest.mark.asyncio
async def test_get_homes(async_smartbox_session):
    with patch.object(
        async_smartbox_session,
        "_api_request",
        new_callable=AsyncMock,
    ) as mock_api_request:
        url = "grouped_devs"
        mock_api_request.return_value = await fake_get_request(
            mock_api_request,
            url,
        )
        homes = await async_smartbox_session.get_homes()
        assert homes == mock_api_request.return_value
        mock_api_request.assert_called_once_with(url)
        async_smartbox_session.raw_response = False
        homes_model = await async_smartbox_session.get_homes()
        assert homes_model[0].name == homes[0]["name"]


def test_session_get_status(session):
    mock_device_id = "test_device"
    mock_node = {
        "name": "Living Room",
        "addr": 1,
        "type": "thermostat",
        "installed": True,
        "lost": False,
    }

    with patch.object(
        session._async,
        "get_node_status",
        new_callable=AsyncMock,
    ) as mock_get_node_status:
        mock_get_node_status.return_value = {"status": "active"}
        status = session.get_status(device_id=mock_device_id, node=mock_node)
        assert status == {"status": "active"}
        mock_get_node_status.assert_called_once_with(
            device_id=mock_device_id,
            node=mock_node,
        )


def test_client_with_existing_session():
    mock_session = AsyncMock(spec=ClientSession)
    async_smartbox_session = AsyncSession(
        username="test_user",
        password="test_password",
        websession=mock_session,
    )

    client = async_smartbox_session.client
    assert client == mock_session


@pytest.mark.asyncio
async def test_client_without_existing_session():
    async_smartbox_session = AsyncSession(
        username="test_user",
        password="test_password",
    )
    with patch("smartbox.session.ClientSession") as mock_client_session:
        client = async_smartbox_session.client
        assert client == mock_client_session.return_value


@pytest.mark.asyncio
async def test_check_refresh_auth_no_access_token(async_session):
    async_session._access_token = ""

    with patch.object(
        async_session,
        "_authentication",
        new_callable=AsyncMock,
    ) as mock_authentication:
        await async_session.check_refresh_auth()
        mock_authentication.assert_called_once_with(
            {
                "grant_type": "password",
                "username": async_session._username,
                "password": async_session._password,
            },
        )


@pytest.mark.asyncio
async def test_check_refresh_auth_token_valid(async_session):
    async_session._access_token = "test_access_token"
    async_session._expires_at = datetime.datetime.now(
        datetime.UTC
    ) + datetime.timedelta(seconds=3600)

    with patch.object(
        async_session,
        "_authentication",
        new_callable=AsyncMock,
    ) as mock_authentication:
        await async_session.check_refresh_auth()
        mock_authentication.assert_not_called()


@pytest.mark.asyncio
async def test_get_home_guests(async_smartbox_session):
    mock_home_id = "test_home"
    with patch.object(
        async_smartbox_session,
        "_api_request",
        new_callable=AsyncMock,
    ) as mock_api_request:
        url = f"groups/{mock_home_id}/guest_users"
        mock_api_request.return_value = await fake_get_request(
            mock_api_request,
            url,
        )
        guests = await async_smartbox_session.get_home_guests(
            home_id=mock_home_id,
        )
        assert guests == mock_api_request.return_value["guest_users"]
        mock_api_request.assert_called_once_with(url)

        async_smartbox_session.raw_response = False
        guests_model = await async_smartbox_session.get_home_guests(
            home_id=mock_home_id,
        )
        assert guests_model.guest_users[0].email == guests[0]["email"]
        async_smartbox_session.raw_response = True


@pytest.mark.asyncio
async def test_get_deviceconnected_status(async_smartbox_session):
    for mock_device in await async_smartbox_session.get_devices():
        with patch.object(
            async_smartbox_session,
            "_api_request",
            new_callable=AsyncMock,
        ) as mock_api_request:
            url = f"devs/{mock_device['dev_id']}/connected"
            mock_api_request.return_value = await fake_get_request(
                mock_api_request,
                url,
            )
            nodes = await async_smartbox_session.get_device_connected(
                device_id=mock_device["dev_id"],
            )
            assert nodes == mock_api_request.return_value
            mock_api_request.assert_called_with(url)

            async_smartbox_session.raw_response = False
            nodes_model = await async_smartbox_session.get_device_connected(
                device_id=mock_device["dev_id"],
            )
            assert nodes_model.connected == nodes["connected"]
            async_smartbox_session.raw_response = True


@pytest.mark.asyncio
async def test_async_session_context_manager_success():
    """Testing __aenter__ and __aexit__.

    A caller-provided websession is owned by the caller: __aexit__ must
    NOT close it.
    """
    mock_client = AsyncMock(spec=ClientSession)
    session = AsyncSession(
        username="test_user",
        password="test_password",
        websession=mock_client,
    )

    async with session as s:
        assert s is session
        mock_client.close.assert_not_called()
    mock_client.close.assert_not_called()


@pytest.mark.asyncio
async def test_async_session_context_manager_closes_owned_session():
    """Testing that __aexit__ closes the session the library created."""
    session = AsyncSession(username="test_user", password="test_password")

    async with session:
        client = session.client  # force lazy creation
        assert session._owns_client_session is True

    assert client.closed
    # The closed session is forgotten so a later sync call lazily
    # recreates a fresh one instead of reusing the closed object.
    assert session._client_session is None


@pytest.mark.asyncio
async def test_async_session_context_manager_with_exception():
    """Testing that __aexit__ cleans up the owned session even on a crash."""
    session = AsyncSession(username="test_user", password="test_password")

    class DummyError(Exception):
        """Dummy exception for testing context manager error handling."""

    with pytest.raises(DummyError):
        async with session:
            client = session.client  # force lazy creation
            msg = "This is a test error to check context manager exception handling."
            raise DummyError(msg)

    assert client.closed
    assert session._client_session is None


@pytest.mark.asyncio
async def test_get_node_samples_defaults_use_call_time(async_smartbox_session):
    """Regression pin: the default sample window is anchored at call time.

    Default arguments are evaluated at import, so the old
    ``int(time.time() - 3600)`` defaults were frozen at process start.
    """
    node = {
        "name": "n",
        "addr": 5,
        "type": "htr",
        "installed": True,
        "lost": False,
    }
    with patch.object(
        async_smartbox_session,
        "_api_request",
        new_callable=AsyncMock,
    ) as mock_api_request:
        mock_api_request.return_value = {"samples": []}
        with time_machine.travel("2026-09-27 12:00:00+00:00"):
            await async_smartbox_session.get_node_samples("test_device", node)
            first_params = mock_api_request.call_args[1]["params"]
        with time_machine.travel("2026-09-27 13:00:00+00:00"):
            await async_smartbox_session.get_node_samples("test_device", node)
            second_params = mock_api_request.call_args[1]["params"]
    assert first_params["start"] != second_params["start"]
    assert first_params["end"] != second_params["end"]


@pytest.mark.asyncio
async def test_check_refresh_auth_is_serialized(async_session):
    """Concurrent callers must not race two token refreshes.

    The stub simulates a *complete* successful auth: it must set the
    token AND the expiry (the real ``_authentication`` does both), so
    the second caller re-checks and skips.
    """

    async def auth_side_effect(credentials):
        async_session._access_token = "tok"
        async_session._expires_at = datetime.datetime.now(datetime.UTC) + (
            datetime.timedelta(seconds=3600)
        )

    with patch.object(
        async_session,
        "_authentication",
        new_callable=AsyncMock,
        side_effect=auth_side_effect,
    ) as mock_authentication:
        await asyncio.gather(
            async_session.check_refresh_auth(),
            async_session.check_refresh_auth(),
        )
    assert mock_authentication.call_count == 1


@pytest.mark.asyncio
async def test_authentication_does_not_log_full_token(async_session, caplog):
    """Regression pin: the access token must be masked in logs."""
    credentials = {"grant_type": "password", "username": "u", "password": "p"}
    token_response = {
        "access_token": "SUPERSECRETACCESSTOKEN123",
        "refresh_token": "r",
        "expires_in": 3600,
        "token_type": "Bearer",
    }
    with patch.object(
        async_session.client,
        "post",
    ) as mock_post:
        mock_post.return_value = mock_api_response(token_response)
        with caplog.at_level(logging.DEBUG, logger="smartbox.session"):
            await async_session._authentication(credentials)
    assert "SUPERSECRETACCESSTOKEN123" not in caplog.text


@pytest.mark.asyncio
async def test_authentication_server_error_is_unavailable(async_session):
    """A 5xx on the token endpoint must NOT surface as InvalidAuthError.

    Transient server trouble would otherwise push consumers into a reauth
    flow (as if the credentials were wrong).
    """
    credentials = {
        "grant_type": "password",
        "username": "test_user",
        "password": "test_password",
    }
    with patch.object(
        async_session.client,
        "post",
    ) as mock_post:
        mock_post.side_effect = aiohttp.ClientResponseError(
            request_info=None,
            history=None,
            status=503,
            message="Service Unavailable",
        )
        with pytest.raises(APIUnavailableError):
            await async_session._authentication(credentials)


@pytest.mark.asyncio
async def test_request_empty_body_returns_empty_dict(async_session):
    """Empty-body success (204) must not fail as a ContentTypeError."""
    async_session._access_token = "tok"
    async_session._expires_at = datetime.datetime.now(
        datetime.UTC
    ) + datetime.timedelta(hours=1)
    mock_response = MagicMock()
    mock_response.status = 204
    mock_response.raise_for_status = MagicMock()
    mock_response.__aenter__.return_value = mock_response
    mock_response.__aexit__.return_value = None
    mock_client = MagicMock()
    mock_client.request.return_value = mock_response
    with patch.object(
        AsyncSession,
        "client",
        new_callable=PropertyMock,
        return_value=mock_client,
    ):
        result = await async_session._request(
            "delete",
            f"{async_session._api_host}/api/v2/devs/test_device",
        )
    assert result == {}


@pytest.mark.asyncio
async def test_request_non_json_body_returns_empty_dict(async_session):
    """A 200 without a JSON body must not fail as a ContentTypeError."""
    async_session._access_token = "tok"
    async_session._expires_at = datetime.datetime.now(
        datetime.UTC
    ) + datetime.timedelta(hours=1)
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.content_length = None
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(
        side_effect=aiohttp.ContentTypeError(
            request_info=None,
            history=None,
            message="not json",
        )
    )
    mock_response.__aenter__.return_value = mock_response
    mock_response.__aexit__.return_value = None
    mock_client = MagicMock()
    mock_client.request.return_value = mock_response
    with patch.object(
        AsyncSession,
        "client",
        new_callable=PropertyMock,
        return_value=mock_client,
    ):
        result = await async_session._request(
            "post",
            f"{async_session._api_host}/api/v2/devs/x/status",
        )
    assert result == {}


@pytest.mark.asyncio
async def test_request_does_not_log_sensitive_query_params(
    async_session, caplog
):
    """Regression pin: secrets in query strings must not reach the logs."""
    async_session._access_token = "tok"
    async_session._expires_at = datetime.datetime.now(
        datetime.UTC
    ) + datetime.timedelta(hours=1)
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.content_length = 25
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={"encrypted_pass": "x"})
    mock_response.__aenter__.return_value = mock_response
    mock_response.__aexit__.return_value = None
    mock_client = MagicMock()
    mock_client.request.return_value = mock_response
    with (
        patch.object(
            AsyncSession,
            "client",
            new_callable=PropertyMock,
            return_value=mock_client,
        ),
        caplog.at_level(logging.DEBUG, logger="smartbox.session"),
    ):
        await async_session._request(
            "get",
            f"{async_session._api_host}/api/v2/encrypted_wifi_credentials"
            "?ssid=home&pass=supersecretpass",
        )
    assert "supersecretpass" not in caplog.text
    assert "ssid=home" in caplog.text


def test_redacted_url_masks_sensitive_query_params():
    url = "https://api.helki.com/?token=secret-token&dev_id=dev1"
    redacted = _redacted_url(url)
    assert "secret-token" not in redacted
    assert "token=***" in redacted
    assert "dev_id=dev1" in redacted


def test_redacted_url_masks_wifi_password():
    url = (
        "https://api.helki.com/api/v2/encrypted_wifi_credentials"
        "?ssid=home&pass=secret-pass"
    )
    redacted = _redacted_url(url)
    assert "secret-pass" not in redacted
    assert "pass=***" in redacted


def test_redacted_url_masks_user_id():
    url = "https://api.helki.com/api/v2/ws_user?token=secret&user_id=6962241835"
    redacted = _redacted_url(url)
    assert "6962241835" not in redacted
    assert "user_id=***" in redacted
    assert "token=***" in redacted


def test_redacted_url_passthrough_without_query():
    url = "https://api.helki.com/api/v2/devs"
    assert _redacted_url(url) == url


def test_redact_body_masks_sensitive_keys():
    """Body keys like the invite-confirmation ``pass`` must be masked."""
    assert _redact_body({"pass": "secret", "code": "1"}) == (
        '{"pass": "***", "code": "1"}'
    )
    assert _redact_body(None) is None


@pytest.mark.asyncio
async def test_api_post_path_does_not_log_password_body(async_session, caplog):
    """Regression pin: invite-confirmation passwords are masked in logs."""
    with patch.object(
        async_session.client,
        "request",
    ) as mock_request:
        mock_request.return_value = mock_api_response({})
        with caplog.at_level(logging.DEBUG, logger="smartbox.session"):
            await async_session._api_post_path(
                "/api/v2/users/u/invite_confirmation",
                data={"pass": "SUPERSECRETPASSWORD", "code": "1234"},
                auth=False,
            )
    assert "SUPERSECRETPASSWORD" not in caplog.text
    assert '"pass": "***"' in caplog.text


@pytest.mark.asyncio
async def test_get_nodes_unexpected_payload_raises_smartbox_error(
    async_smartbox_session,
):
    """A payload without a ``nodes`` key must raise SmartboxError, not KeyError."""
    with (
        patch.object(
            async_smartbox_session,
            "_api_request",
            new_callable=AsyncMock,
            return_value={"unexpected": "shape"},
        ),
        pytest.raises(SmartboxError, match="Unexpected nodes payload"),
    ):
        await async_smartbox_session.get_nodes("test_device")


def test_sync_session_multiple_calls(session):
    """Two sync calls (two event loops) must both work with per-call cleanup."""
    session._async._access_token = "tok"
    session._async._expires_at = datetime.datetime.now(
        datetime.UTC
    ) + datetime.timedelta(hours=1)
    with patch.object(
        session._async,
        "_api_request",
        new_callable=AsyncMock,
        return_value={"devs": [], "invited_to": []},
    ):
        assert session.get_devices() == []
        assert session.get_devices() == []


class _SyncSessionWireHandler(BaseHTTPRequestHandler):
    """Minimal wire for real-client sync tests: token + devs endpoints."""

    def _send_json(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/client/token":
            self._send_json(
                {
                    "access_token": "test_access_token",
                    "refresh_token": "test_refresh_token",
                    "expires_in": 3600,
                    "token_type": "bearer",
                }
            )
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/v2/devs"):
            self._send_json({"devs": [], "invited_to": []})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:
        pass


def test_sync_session_second_call_recreates_closed_client():
    """Regression: a second sync call must not reuse a closed ClientSession.

    aclose_owned_session() used to leave the closed ClientSession on
    ``_client_session``, so the next asyncio.run() raised
    "RuntimeError: Session is closed". Exercises the real client (auth
    POST + GET through a local HTTP server), not mocks.
    """
    server = HTTPServer(("127.0.0.1", 0), _SyncSessionWireHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        session = Session(username="test_user", password="test_password")
        session._async._api_host = (
            f"http://127.0.0.1:{server.server_address[1]}"
        )
        first = session.get_devices()
        second = session.get_devices()
        assert first == second == []
    finally:
        server.shutdown()


def test_session_power_limit_passes_node(session):
    """The sync power-limit getter exposes the async ``node`` parameter."""
    node = {"name": "PMO", "addr": 1, "type": "pmo", "installed": True}
    with patch.object(
        session._async,
        "get_device_power_limit",
        new_callable=AsyncMock,
    ) as mock_get:
        session.get_device_power_limit(device_id="test_device", node=node)
        mock_get.assert_awaited_once_with(device_id="test_device", node=node)


def test_session_set_power_limit_passes_node(session):
    """The sync power-limit setter exposes the async ``node`` parameter."""
    node = {"name": "PMO", "addr": 1, "type": "pmo", "installed": True}
    with patch.object(
        session._async,
        "set_device_power_limit",
        new_callable=AsyncMock,
    ) as mock_set:
        session.set_device_power_limit(
            device_id="test_device",
            power_limit=100,
            node=node,
        )
        mock_set.assert_awaited_once_with(
            device_id="test_device",
            power_limit=100,
            node=node,
        )


def test_session_health_check(session):
    with patch.object(
        session._async,
        "health_check",
        new_callable=AsyncMock,
    ) as mock_health:
        mock_health.return_value = {"status": "ok"}
        assert session.health_check() == {"status": "ok"}
        mock_health.assert_awaited_once()


def test_session_api_version(session):
    with patch.object(
        session._async,
        "api_version",
        new_callable=AsyncMock,
    ) as mock_version:
        mock_version.return_value = {"major": "1"}
        assert session.api_version() == {"major": "1"}
        mock_version.assert_awaited_once()


def test_session_get_device_connected(session):
    with patch.object(
        session._async,
        "get_device_connected",
        new_callable=AsyncMock,
    ) as mock_connected:
        mock_connected.return_value = {"connected": True}
        assert session.get_device_connected(device_id="test_device") == {
            "connected": True,
        }
        mock_connected.assert_awaited_once_with(device_id="test_device")


def test_session_get_node_samples(session):
    node = {"name": "Living Room", "addr": 5, "type": "htr", "installed": True}
    with patch.object(
        session._async,
        "get_node_samples",
        new_callable=AsyncMock,
    ) as mock_samples:
        mock_samples.return_value = {"samples": []}
        assert session.get_node_samples(
            device_id="test_device",
            node=node,
        ) == {"samples": []}
        mock_samples.assert_awaited_once_with(
            device_id="test_device",
            node=node,
            start_time=None,
            end_time=None,
        )


def test_session_get_node_version(session):
    node = {"name": "Living Room", "addr": 5, "type": "htr", "installed": True}
    with patch.object(
        session._async,
        "get_node_version",
        new_callable=AsyncMock,
    ) as mock_version:
        mock_version.return_value = {
            "hw_version": "1",
            "fw_version": "1",
            "uid": "u",
            "pid": "p",
        }
        result = session.get_node_version(device_id="test_device", node=node)
        assert result["fw_version"] == "1"
        mock_version.assert_awaited_once_with(
            device_id="test_device",
            node=node,
        )


@pytest.mark.asyncio
async def test_authentication_non_json_body_raises_smartbox_error(
    async_session,
):
    """A 200 with a non-JSON body is malformed success, not bad credentials."""
    credentials = {
        "grant_type": "password",
        "username": "test_user",
        "password": "test_password",
    }
    with patch.object(async_session.client, "post") as mock_post:
        mock_response = MagicMock()
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(
                request_info=SimpleNamespace(real_url="http://test"),
                history=(),
                status=200,
                message="bad",
            )
        )
        mock_post.return_value = mock_response
        with pytest.raises(SmartboxError, match="non-JSON"):
            await async_session._authentication(credentials)


@pytest.mark.asyncio
async def test_set_node_prog_rejects_payload_without_prog_key(
    async_smartbox_session,
):
    """A day-keyed-top-level payload is a user error, not a silent no-op."""
    node = {"name": "Living Room", "addr": 5, "type": "htr", "installed": True}
    with (
        patch.object(
            async_smartbox_session,
            "get_node_prog",
            new_callable=AsyncMock,
        ) as mock_get,
        patch.object(
            async_smartbox_session,
            "_api_post",
            new_callable=AsyncMock,
        ) as mock_post,
    ):
        with pytest.raises(ValueError, match="'prog'"):
            await async_smartbox_session.set_node_prog(
                "test_device",
                node,
                {"0": [2] * 24},
            )
        mock_get.assert_not_awaited()
        mock_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_call_in_running_loop_closes_coroutines(
    session,
    recwarn,
):
    """Misuse (sync Session inside a running loop) raises cleanly.

    Regression: the pre-built coroutine leaked a "never awaited"
    RuntimeWarning behind the RuntimeError.
    """
    with pytest.raises(RuntimeError, match="running event loop"):
        session.get_devices()
    gc.collect()
    never_awaited = [w for w in recwarn if "never awaited" in str(w.message)]
    assert not never_awaited
