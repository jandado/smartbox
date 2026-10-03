import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smartbox.error import APIUnavailableError, SmartboxValidationError
from smartbox.models import HtrNodeStatus
from smartbox.session import AsyncSmartboxSession
from smartbox.socket import SocketSession
from smartbox.update_manager import (
    DevDataSubscription,
    OptimisedJQMatcher,
    UpdateManager,
    UpdateSubscription,
    _written_applied,
)
from smartbox.ws_user import WsUserSocketSession


@pytest.fixture
def mock_session():
    return MagicMock(spec=AsyncSmartboxSession)


def test_optimised_jq_matcher_simple():
    matcher = OptimisedJQMatcher(".simple")
    input_data = {"simple": "value"}
    assert list(matcher.match(input_data)) == ["value"]


def test_optimised_jq_matcher_complex():
    matcher = OptimisedJQMatcher(".complex | .nested")
    input_data = {"complex": {"nested": "value"}}
    assert list(matcher.match(input_data)) == ["value"]


def test_dev_data_subscription():
    callback = MagicMock()
    subscription = DevDataSubscription(".data", callback)
    input_data = {"data": "value"}
    subscription.match(input_data)
    callback.assert_called_once_with("value")


def test_update_subscription():
    callback = MagicMock()
    subscription = UpdateSubscription(r"^/path", ".data", callback)
    input_data = {"path": "/path", "data": "value"}
    assert subscription.match(input_data)
    callback.assert_called_once_with("value")


def test_update_manager_subscribe_to_dev_data(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_dev_data(".data", callback)
    assert len(update_manager._dev_data_subscriptions) == 1


def test_update_manager_subscribe_to_updates(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_updates(r"^/path", ".data", callback)
    assert len(update_manager._update_subscriptions) == 1


def test_update_manager_dev_data_cb(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_dev_data(".data", callback)
    input_data = {"data": "value"}
    update_manager._dev_data_cb(input_data)
    callback.assert_called_once_with("value")


def test_update_manager_update_cb(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_updates(r"^/path", ".data", callback)
    input_data = {"path": "/path", "data": "value"}
    update_manager._update_cb(input_data)
    callback.assert_called_once_with("value")


def test_update_manager_socket_session(update_manager):
    assert isinstance(update_manager.socket_session, SocketSession)


def test_optimised_jq_matcher_repr_simple():
    matcher = OptimisedJQMatcher(".simple")
    assert repr(matcher) == "OptimisedJQMatcher('.simple', fast_path=True)"


def test_optimised_jq_matcher_repr_complex():
    matcher = OptimisedJQMatcher(".complex | .nested")
    assert repr(matcher) == repr(matcher._compiled_jq)


def test_optimised_jq_matcher_str_simple():
    matcher = OptimisedJQMatcher(".simple")
    assert str(matcher) == "OptimisedJQMatcher('.simple', fast_path=True)"


def test_optimised_jq_matcher_str_complex():
    matcher = OptimisedJQMatcher(".complex | .nested")
    assert str(matcher) == str(matcher._compiled_jq)


def test_dev_data_subscription_match():
    callback = MagicMock()
    subscription = DevDataSubscription(".data", callback)
    input_data = {"data": "value"}
    subscription.match(input_data)
    callback.assert_called_once_with("value")


def test_dev_data_subscription_match_no_match():
    callback = MagicMock()
    subscription = DevDataSubscription(".data", callback)
    input_data = {"other_data": "value"}
    subscription.match(input_data)
    callback.assert_not_called()


def test_update_manager_subscribe_to_device_away_status(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_device_away_status(callback)
    assert len(update_manager._dev_data_subscriptions) == 1
    assert len(update_manager._update_subscriptions) == 1

    # Test dev data callback
    dev_data = {"away_status": "away"}
    update_manager._dev_data_cb(dev_data)
    callback.assert_called_once_with("away")

    # Test update callback
    callback.reset_mock()
    update_data = {"path": "/mgr/away_status", "body": "away"}
    update_manager._update_cb(update_data)
    callback.assert_called_once_with("away")


def test_update_manager_subscribe_to_node_status(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_node_status(callback)
    assert len(update_manager._dev_data_subscriptions) == 1
    assert len(update_manager._update_subscriptions) == 1

    # Test dev data callback
    dev_data = {
        "nodes": [{"type": "node_type", "addr": 1, "status": {"key": "value"}}],
    }
    update_manager._dev_data_cb(dev_data)
    callback.assert_called_once_with("node_type", 1, {"key": "value"})

    # Test update callback
    callback.reset_mock()
    update_data = {"path": "/node_type/1/status", "body": {"key": "value"}}
    update_manager._update_cb(update_data)
    callback.assert_called_once_with("node_type", 1, {"key": "value"})


def test_update_manager_subscribe_to_node_setup(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_node_setup(callback)
    assert len(update_manager._dev_data_subscriptions) == 1
    assert len(update_manager._update_subscriptions) == 1

    # Test dev data callback
    dev_data = {
        "nodes": [{"type": "node_type", "addr": 1, "setup": {"key": "value"}}],
    }
    update_manager._dev_data_cb(dev_data)
    callback.assert_called_once_with("node_type", 1, {"key": "value"})

    # Test update callback
    callback.reset_mock()
    update_data = {"path": "/node_type/1/setup", "body": {"key": "value"}}
    update_manager._update_cb(update_data)
    callback.assert_called_once_with("node_type", 1, {"key": "value"})


def test_update_manager_subscribe_to_device_power_limit(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_device_power_limit(callback)
    assert len(update_manager._dev_data_subscriptions) == 1
    assert len(update_manager._update_subscriptions) == 1

    # Test dev data callback
    dev_data = {"htr_system": {"setup": {"power_limit": 100}}}
    update_manager._dev_data_cb(dev_data)
    callback.assert_called_once_with(100)

    # Test update callback
    callback.reset_mock()
    update_data = {"path": "/htr_system/setup", "body": {"power_limit": 200}}
    update_manager._update_cb(update_data)
    callback.assert_called_once_with(200)

    # Test update callback with different path
    callback.reset_mock()
    update_data = {
        "path": "/htr_system/power_limit",
        "body": {"power_limit": 300},
    }
    update_manager._update_cb(update_data)
    callback.assert_called_once_with(300)


@pytest.mark.asyncio
async def test_update_manager_run(update_manager):
    with patch.object(
        update_manager.socket_session,
        "run",
        new_callable=AsyncMock,
    ) as mock_run:
        await update_manager.run()
        mock_run.assert_awaited_once()


def test_update_manager_subscribe_to_device_connected(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_device_connected(callback)
    assert len(update_manager._dev_data_subscriptions) == 1
    assert len(update_manager._update_subscriptions) == 1

    # Test dev data callback
    dev_data = {"connected": True}
    update_manager._dev_data_cb(dev_data)
    callback.assert_called_once_with(dev_data["connected"])

    # Test update callback
    callback.reset_mock()
    update_data = {"path": "/connected", "body": {"connected": True}}
    update_manager._update_cb(update_data)
    callback.assert_called_once_with(update_data["body"]["connected"])


def test_dev_data_subscription_isolates_callback_exceptions():
    """A raising callback must not starve the other subscriptions."""

    def bad(_data):
        msg = "boom"
        raise RuntimeError(msg)

    ok = MagicMock()
    DevDataSubscription(".data", bad).match({"data": "value"})
    DevDataSubscription(".data", ok).match({"data": "value"})
    ok.assert_called_once_with("value")


def test_update_subscription_isolates_callback_exceptions():
    """A raising update callback must not starve the other subscriptions."""
    calls = []

    def bad(data, **kwargs):
        msg = "boom"
        raise RuntimeError(msg)

    UpdateSubscription(r"^/path", ".data", bad).match(
        {"path": "/path", "data": "value"},
    )
    UpdateSubscription(
        r"^/path",
        ".data",
        lambda d, **_k: calls.append(d),
    ).match({"path": "/path", "data": "value"})
    assert calls == ["value"]


def test_update_subscription_missing_path_is_safe():
    """A missing ``path`` key must not raise KeyError when matched."""
    callback = MagicMock()
    assert not UpdateSubscription(r"^/path", ".data", callback).match({})
    callback.assert_not_called()


def test_update_manager_node_status_wrapper_skips_null_status(update_manager):
    """Jq emits status: null for key-less nodes — callback must not fire."""
    callback = MagicMock()
    update_manager.subscribe_to_node_status(callback)
    update_manager._dev_data_cb(
        {"nodes": [{"type": "htr", "addr": 1, "status": None}]},
    )
    callback.assert_not_called()


def test_update_manager_subscribe_to_node_version(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_node_version(callback)
    assert len(update_manager._dev_data_subscriptions) == 1
    assert len(update_manager._update_subscriptions) == 1

    # Test dev data callback
    dev_data = {
        "nodes": [
            {
                "type": "acm",
                "addr": 2,
                "version": {
                    "pid": "081c",
                    "fw_version": "1.6",
                    "hw_version": "1.0",
                    "uid": "test123",
                },
            }
        ],
    }
    update_manager._dev_data_cb(dev_data)
    callback.assert_called_once_with(
        "acm",
        2,
        {
            "pid": "081c",
            "fw_version": "1.6",
            "hw_version": "1.0",
            "uid": "test123",
        },
    )

    # Test update callback
    callback.reset_mock()
    update_data = {
        "path": "/acm/2/version",
        "body": {
            "pid": "081c",
            "fw_version": "1.7",
            "hw_version": "1.0",
            "uid": "test123",
        },
    }
    update_manager._update_cb(update_data)
    callback.assert_called_once_with(
        "acm",
        2,
        {
            "pid": "081c",
            "fw_version": "1.7",
            "hw_version": "1.0",
            "uid": "test123",
        },
    )


def test_update_manager_subscribe_to_node_prog(update_manager):
    callback = MagicMock()
    update_manager.subscribe_to_node_prog(callback)
    assert len(update_manager._dev_data_subscriptions) == 1
    assert len(update_manager._update_subscriptions) == 1

    # Test dev data callback
    day_prog = [0] * 24
    dev_data = {
        "nodes": [
            {"type": "htr", "addr": 5, "prog": {"0": day_prog}},
        ],
    }
    update_manager._dev_data_cb(dev_data)
    callback.assert_called_once_with("htr", 5, {"0": day_prog})

    # Test update callback
    callback.reset_mock()
    update_data = {
        "path": "/htr/5/prog",
        "body": {"0": day_prog},
    }
    update_manager._update_cb(update_data)
    callback.assert_called_once_with("htr", 5, {"0": day_prog})


def test_node_prog_subscription_ignores_prog_temps_path(update_manager):
    """Regression: unanchored ``/prog`` also matched ``/prog_temps``."""
    callback = MagicMock()
    update_manager.subscribe_to_node_prog(callback)
    update_manager._update_cb(
        {"path": "/htr/5/prog_temps", "body": {"comf_temp": "20.0"}},
    )
    callback.assert_not_called()


def test_update_cb_path_error_logged_once(update_manager, caplog):
    """A malformed update is logged once, not once per subscription."""
    for _ in range(3):
        update_manager.subscribe_to_updates(r"^/x", ".body", MagicMock())
    with caplog.at_level(logging.ERROR, logger="smartbox.update_manager"):
        update_manager._update_cb({"no": "path"})
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1


# --- node availability ------------------------------------------------------


@pytest.fixture
def fast_update_manager(mock_session):
    """UpdateManager with tiny windows so timers expire within the test."""
    return UpdateManager(
        mock_session,
        "device_id",
        write_confirm_timeout=0.05,
        unavailable_delay=0.05,
    )


@pytest.fixture
def events():
    return []


@pytest.fixture
def tracked(fast_update_manager, events):
    fast_update_manager.subscribe_to_node_availability(
        lambda node_type, addr, available: events.append(
            (node_type, addr, available),
        ),
    )
    return fast_update_manager


@pytest.mark.asyncio
async def test_availability_disabled_by_default_does_not_tap(update_manager):
    """Without a subscriber, the internal frame taps stay unregistered."""
    assert update_manager._availability_taps_registered is False
    update_manager._dev_data_cb(
        {"nodes": [{"addr": 3, "type": "htr", "status": {"mode": "off"}}]},
    )
    assert update_manager._node_availability == {}


@pytest.mark.asyncio
async def test_ok_frame_reports_available(tracked, events):
    tracked._availability_frame("htr", 3, {"mode": "off"})
    assert events == [("htr", 3, True)]
    assert tracked.get_node_availability("htr", 3) is True


@pytest.mark.asyncio
async def test_lost_frame_reports_unavailable_after_delay(tracked, events):
    tracked._availability_frame("htr", 3, {"sync_status": "lost"})
    assert events == []  # not immediate
    await asyncio.sleep(0.15)
    assert events == [("htr", 3, False)]


@pytest.mark.asyncio
async def test_lost_then_ok_stays_available(tracked, events):
    tracked._availability_frame("htr", 3, {"sync_status": "lost"})
    tracked._availability_frame("htr", 3, {"mode": "off"})
    assert events == [("htr", 3, True)]
    await asyncio.sleep(0.15)
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_setup_frames_tracked(tracked, events):
    tracked._availability_frame("htr", 3, {"sync_status": "lost"})
    await asyncio.sleep(0.15)
    assert events == [("htr", 3, False)]
    tracked._availability_frame("htr", 3, {"sync_status": "ok"})
    assert events == [("htr", 3, False), ("htr", 3, True)]


@pytest.mark.asyncio
async def test_availability_deduplicated(tracked, events):
    for _ in range(3):
        tracked._availability_frame("htr", 3, {"mode": "off"})
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_availability_via_dev_data_tap(tracked, events):
    """The taps also see frames delivered through the public dev_data path."""
    tracked._dev_data_cb(
        {
            "nodes": [
                {"addr": 3, "type": "htr", "status": {"sync_status": "ok"}}
            ]
        },
    )
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_write_confirmed_by_ok_frame_no_flip(tracked, events):
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    tracked.expect_write_confirmation(node, {"mode": "auto"})
    tracked._availability_frame("htr", 3, {"sync_status": "lost"})
    tracked._availability_frame(
        "htr",
        3,
        {"sync_status": "ok", "mode": "auto"},
    )
    await asyncio.sleep(0.15)
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_write_to_dead_node_get_discriminates_lost(
    tracked, events, mock_session
):
    """Confirm window expires; GET returns bare lost -> unavailable."""
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(
        return_value={"sync_status": "lost"}
    )
    tracked.expect_write_confirmation(node, {"mode": "auto"})
    await asyncio.sleep(0.15)
    mock_session.get_node_status.assert_awaited_once()
    assert events == [("htr", 3, False)]


@pytest.mark.asyncio
async def test_rearmed_write_during_get_own_verdict(
    tracked, events, mock_session
):
    """A write armed while an older confirm-GET is in flight owns the verdict.

    Regression (review finding 1): the stale task's GET result must not
    override the newer write's pending window.
    """
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    gate = asyncio.Event()
    calls = []

    async def gated_get(dev_id, n):
        calls.append(dev_id)
        await gate.wait()
        # The newer write IS applied (node alive) -> verdict True.
        return {"sync_status": "ok", "mode": "auto"}

    mock_session.get_node_status = AsyncMock(side_effect=gated_get)

    tracked.expect_write_confirmation(node, {"mode": "heat"})
    # Old task: sleep expired, GET parked on the gate.
    await asyncio.sleep(0.15)
    # Newer write replaces the old pending; its task also parks on the gate.
    tracked.expect_write_confirmation(node, {"mode": "auto"})
    await asyncio.sleep(0.15)
    gate.set()
    await asyncio.sleep(0.15)
    # Both tasks GET (await_count 2), but only the newer write may emit.
    assert mock_session.get_node_status.await_count == 2
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_confirm_verdict_cancels_pending_lost_timer(events, mock_session):
    """The confirm GET's 'applied' verdict owns the state.

    Regression (review round 2, finding 1): a bare lost frame armed during
    a pending write confirmation must not emit False *after* the
    discriminating GET proved the node alive — the vendor-app-parity
    scenario where the ok push never comes and the GET is the only verdict.
    """
    manager = UpdateManager(
        mock_session,
        "device_id",
        write_confirm_timeout=0.05,
        unavailable_delay=0.4,
    )
    manager.subscribe_to_node_availability(
        lambda node_type, addr, available: events.append(
            (node_type, addr, available),
        ),
    )
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(
        return_value={"sync_status": "ok", "mode": "auto"},
    )
    manager.expect_write_confirmation(node, {"mode": "auto"})
    # Lost frame arrives while the confirmation is pending (arms the lost
    # timer, fires later than the confirm task).
    manager._availability_frame("htr", 3, {"sync_status": "lost"})
    await asyncio.sleep(0.3)  # confirm task fired + verdict; lost timer pending
    assert events == [("htr", 3, True)]
    # Past the lost timer's horizon: the verdict must have cancelled it.
    await asyncio.sleep(0.3)
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_duplicate_subscriber_deduplicated(tracked, events):
    """Re-registering the same callback must not double events."""
    first = tracked._availability_callbacks[0]
    tracked.subscribe_to_node_availability(first)
    assert len(tracked._availability_callbacks) == 1
    tracked._availability_frame("htr", 3, {"mode": "off"})
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_write_confirm_invalid_kind_rejected(tracked, mock_session):
    """An unknown kind must fail loudly, not silently behave as "status"."""
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock()
    with pytest.raises(ValueError, match="kind"):
        tracked.expect_write_confirmation(node, {"mode": "off"}, kind="prog")
    assert not tracked._pending_confirms
    mock_session.get_node_status.assert_not_called()


@pytest.mark.asyncio
async def test_arm_lost_timer_replaces_existing(tracked):
    """Re-arming a node's lost timer cancels the replaced task."""
    tracked._arm_lost_timer("htr", 3)
    first = tracked._lost_timers[("htr", 3)]
    tracked._arm_lost_timer("htr", 3)
    await asyncio.sleep(0.05)
    assert first.cancelled()
    assert tracked._lost_timers[("htr", 3)] is not first


def _htr_status_data(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    """Complete htr status payload (mirrors test_models.py's fixture)."""
    data: dict[str, Any] = {
        "mtemp": "20.0",
        "units": "C",
        "sync_status": "synced",
        "locked": False,
        "mode": "auto",
        "error_code": "none",
        "eco_temp": "18.0",
        "comf_temp": "22.0",
        "act_duty": 45,
        "pcb_temp": "30.0",
        "power_pcb_temp": "35.0",
        "presence": True,
        "window_open": False,
        "true_radiant_active": True,
        "boost": False,
        "boost_end_min": 0,
        "boost_end_day": 0,
        "stemp": "21.0",
        "power": "on",
        "duty": 50,
        "ice_temp": "5.0",
        "active": True,
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_write_confirm_typed_model_applied(mock_session, events):
    """Typed mode (raw_response=False) verdict adapts the pydantic model.

    The confirming GET returns a pydantic model — the verdict must adapt
    it, not invert it (review round 3).
    """
    manager = UpdateManager(
        mock_session,
        "device_id",
        write_confirm_timeout=0.05,
        unavailable_delay=0.4,
    )
    manager.subscribe_to_node_availability(
        lambda node_type, addr, available: events.append(
            (node_type, addr, available),
        ),
    )
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(
        return_value=HtrNodeStatus(**_htr_status_data()),
    )
    manager.expect_write_confirmation(node, {"stemp": "21.0", "units": "C"})
    await asyncio.sleep(0.15)
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_write_confirm_typed_model_not_applied(mock_session, events):
    """Typed mode, write dropped: model values differing → unavailable."""
    manager = UpdateManager(
        mock_session,
        "device_id",
        write_confirm_timeout=0.05,
        unavailable_delay=0.4,
    )
    manager.subscribe_to_node_availability(
        lambda node_type, addr, available: events.append(
            (node_type, addr, available),
        ),
    )
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(
        return_value=HtrNodeStatus(**_htr_status_data(stemp="22.5")),
    )
    manager.expect_write_confirmation(node, {"stemp": "21.0", "units": "C"})
    await asyncio.sleep(0.15)
    assert events == [("htr", 3, False)]


@pytest.mark.asyncio
async def test_write_confirm_empty_written_not_armed(
    tracked, events, mock_session
):
    """Only-None written values arm nothing (review round 3, finding 2)."""
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock()
    tracked.expect_write_confirmation(node, {"mode": None})
    assert not tracked._pending_confirms
    await asyncio.sleep(0.15)  # past the confirm-timeout horizon
    mock_session.get_node_status.assert_not_called()
    assert tracked.get_node_availability("htr", 3) is None
    assert events == []


@pytest.mark.asyncio
async def test_write_confirm_node_dict_snapshot(tracked, events, mock_session):
    """Caller-owned node dict is snapshotted at arm time."""
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(
        return_value={"sync_status": "ok", "mode": "auto"},
    )
    tracked.expect_write_confirmation(node, {"mode": "auto"})
    # Caller mutates its dict while the window is pending (6 s in prod).
    node["addr"] = 9
    node["type"] = "acm"
    await asyncio.sleep(0.15)
    mock_session.get_node_status.assert_awaited_once()
    awaited_node = mock_session.get_node_status.await_args.args[1]
    assert awaited_node["type"] == "htr"
    assert awaited_node["addr"] == 3
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_write_confirm_unexpected_exception_no_flip(
    tracked,
    events,
    mock_session,
    caplog,
):
    """Non-SmartboxError GET failures (pydantic, TimeoutError) are isolated.

    Regression (review finding 2): the confirm task must not die
    un-retrieved; no evidence, no emit.
    """
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(side_effect=TimeoutError())
    tracked.expect_write_confirmation(node, {"mode": "auto"})
    await asyncio.sleep(0.15)
    assert events == []
    assert any(
        "failed unexpectedly" in str(r.getMessage())
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


@pytest.mark.asyncio
async def test_write_confirm_get_shows_applied(tracked, events, mock_session):
    """Confirm window expires; GET shows the written value -> available."""
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(
        return_value={"sync_status": "ok", "stemp": "20.0", "units": "C"},
    )
    tracked.expect_write_confirmation(node, {"stemp": "20.0", "units": "C"})
    await asyncio.sleep(0.15)
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_write_confirm_get_error_no_flip(tracked, events, mock_session):
    """API trouble at discriminator time is not node death: no event."""
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(
        side_effect=APIUnavailableError("api down"),
    )
    tracked.expect_write_confirmation(node, {"mode": "auto"})
    await asyncio.sleep(0.15)
    assert events == []
    # The finished task retires its entry: the dict means "currently armed".
    assert not tracked._pending_confirms


@pytest.mark.asyncio
async def test_ok_frame_cancels_pending_across_kinds(
    tracked, events, mock_session
):
    """An ok frame cancels pending confirmations of every kind.

    Behavior note (review round 4), not an accident: the ok frame itself
    reports Available — the verdict a surviving setup discriminator would
    reach on an alive node — and on a dead node no ok frames arrive, so a
    needed setup GET is never disturbed.
    """
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    # GET (if it ran) would say NOT applied — pins the conservative outcome.
    mock_session.get_node_setup = AsyncMock(
        return_value={"sync_status": "ok", "offset": "0.0"},
    )
    tracked.expect_write_confirmation(node, {"offset": "-1.0"}, kind="setup")
    tracked._availability_frame("htr", 3, {"mode": "auto"})
    await asyncio.sleep(0.15)
    mock_session.get_node_setup.assert_not_awaited()
    assert events == [("htr", 3, True)]
    assert not tracked._pending_confirms


@pytest.mark.asyncio
async def test_lost_timer_no_emit_after_cancel_window(tracked, events):
    """Timer task must not emit once its key is gone (cancel window)."""
    tracked._availability_frame("htr", 3, {"sync_status": "lost"})
    # Simulate the window: the key is popped without the task stopping.
    tracked._lost_timers.clear()
    await asyncio.sleep(0.15)  # past unavailable_delay
    assert events == []


@pytest.mark.asyncio
async def test_write_confirm_typed_bare_lost_unavailable(
    tracked, events, mock_session
):
    """Typed mode, dead node: the validation error's payload IS the verdict.

    The bare lost status payload FAILS NodeStatus validation in typed
    mode; without inspecting the attached payload it would be swallowed
    as "no evidence" and the node would stay Available forever
    (review round 6, finding 1).
    """
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(
        side_effect=SmartboxValidationError(
            "Unexpected node status payload",
            {"sync_status": "lost"},
        ),
    )
    tracked.expect_write_confirmation(node, {"mode": "auto"})
    await asyncio.sleep(0.15)
    assert events == [("htr", 3, False)]
    assert not tracked._pending_confirms


@pytest.mark.asyncio
async def test_write_confirm_typed_drift_no_evidence(
    tracked, events, mock_session
):
    """Typed mode, unrelated payload drift is not node death: no event."""
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(
        side_effect=SmartboxValidationError(
            "Unexpected node status payload",
            {"unexpected": "shape"},
        ),
    )
    tracked.expect_write_confirmation(node, {"mode": "auto"})
    await asyncio.sleep(0.15)
    assert events == []
    assert not tracked._pending_confirms


@pytest.mark.asyncio
async def test_write_confirm_setup_kind(tracked, events, mock_session):
    """kind="setup" discriminates using the setup endpoint."""
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_setup = AsyncMock(
        return_value={"sync_status": "ok", "offset": "0.0"},
    )
    tracked.expect_write_confirmation(node, {"offset": "0.0"}, kind="setup")
    await asyncio.sleep(0.15)
    mock_session.get_node_setup.assert_awaited_once()
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_second_write_replaces_first_pending(
    tracked, events, mock_session
):
    """A later write supersedes the earlier pending confirmation."""
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    mock_session.get_node_status = AsyncMock(
        return_value={"sync_status": "ok", "mode": "auto"},
    )
    tracked.expect_write_confirmation(node, {"mode": "heat"})
    tracked.expect_write_confirmation(node, {"mode": "auto"})
    await asyncio.sleep(0.15)
    mock_session.get_node_status.assert_awaited_once()
    assert events == [("htr", 3, True)]


@pytest.mark.asyncio
async def test_cancel_clears_timers_and_tasks(mock_session, events):
    manager = UpdateManager(
        mock_session,
        "device_id",
        write_confirm_timeout=10,
        unavailable_delay=10,
    )
    manager.subscribe_to_node_availability(lambda *a: events.append(a))
    node = {"type": "htr", "addr": 3, "name": "n", "installed": True}
    manager.expect_write_confirmation(node, {"mode": "auto"})
    manager._availability_frame("htr", 3, {"sync_status": "lost"})
    await manager.cancel()
    assert not manager._lost_timers
    assert not manager._pending_confirms
    await asyncio.sleep(0.05)
    assert events == []


@pytest.mark.asyncio
async def test_availability_callback_exception_isolated(
    tracked, events, caplog
):
    """One raising callback must not starve the others."""
    tracked.subscribe_to_node_availability(
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tracked._availability_frame("htr", 3, {"mode": "off"})
    assert events == [("htr", 3, True)]
    assert any(
        "availability callback" in str(r.getMessage())
        for r in caplog.records
        if r.levelno == logging.ERROR
    )


def test_written_applied_helper():
    assert _written_applied({"stemp": "20.0"}, {"stemp": "20.00"})
    assert _written_applied({"mode": "off"}, {"mode": "off", "mtemp": "17.7"})
    assert not _written_applied({"stemp": "20.0"}, {"sync_status": "lost"})
    assert not _written_applied({"stemp": "20.0"}, {"stemp": "21.0"})
    assert not _written_applied({"mode": "off"}, "not a dict")
    # A lost-marked payload is never evidence of an applied write, even
    # with fully matching last-known values (dead-node /setup GET shape).
    assert not _written_applied(
        {"stemp": "20.0"},
        {"sync_status": "lost", "stemp": "20.0", "units": "C"},
    )
    assert _written_applied(
        {"stemp": "20.0"},
        {"sync_status": "ok", "stemp": "20.0", "units": "C"},
    )


@pytest.mark.asyncio
async def test_availability_frame_unknown_sync_status_no_timer(tracked, events):
    """Only "lost" arms the unavailable timer; unknown values are no-ops."""
    tracked._availability_frame("htr", 3, {"sync_status": "syncing"})
    tracked._availability_frame("htr", 4, {"sync_status": 42})
    assert not tracked._lost_timers
    await asyncio.sleep(0.15)
    assert events == []


@pytest.mark.asyncio
async def test_update_manager_ws_user_mode_registers_and_unregisters(
    mock_session,
):
    """ws_user mode: run() registers with the shared socket; cancel unregisters."""
    shared = MagicMock(spec=WsUserSocketSession)
    manager = UpdateManager(
        mock_session, "dev_id_under_test", ws_user_socket=shared
    )
    assert manager.socket_session is None
    assert manager.ws_user_socket is shared

    task = asyncio.create_task(manager.run())
    await asyncio.sleep(0)
    # Observable contract: register the manager's device with two
    # callbacks (the internal dispatcher wiring is not part of it).
    args, _ = shared.add_device.call_args
    assert args[0] == "dev_id_under_test"
    assert callable(args[1])
    assert callable(args[2])
    await manager.cancel()
    await asyncio.wait_for(task, 1)
    shared.remove_device.assert_called_with("dev_id_under_test")


def test_update_manager_default_mode_still_owns_socket(mock_session, mocker):
    """Without ws_user_socket the manager owns a SocketSession (unchanged)."""
    mocker.patch("smartbox.update_manager.SocketSession", autospec=True)
    manager = UpdateManager(mock_session, "dev_id_under_test")
    assert manager.socket_session is not None
    assert manager._ws_user_socket is None
