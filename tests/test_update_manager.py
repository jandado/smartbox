import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smartbox.session import AsyncSmartboxSession
from smartbox.socket import SocketSession
from smartbox.update_manager import (
    DevDataSubscription,
    OptimisedJQMatcher,
    UpdateSubscription,
)


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
