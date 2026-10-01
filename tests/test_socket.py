"""Tests for SocketSession run-loop and teardown behaviour.

Covers: bounded cancel on a wedged websocket, idempotent single
disconnect, exit-flag checks (before the REST auth refresh, during
reconnect backoff, after a late connect), transient refresh failures,
keepalive resilience, token redaction, and awaited dangling tasks.
"""

import asyncio
import logging
import signal
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import socketio

from smartbox.error import APIUnavailableError, InvalidAuthError, SmartboxError
from smartbox.session import AsyncSmartboxSession
import smartbox.socket as socket_mod
from smartbox.socket import SocketSession


@pytest.fixture
async def socket_session(mocker, reseller):
    session = AsyncSmartboxSession(
        api_name="test_api",
        username="test_user",
        password="test_password",
    )
    mocker.patch("smartbox.socket.socketio.AsyncClient", autospec=True)
    socket_session = SocketSession(session, "device_id")
    # autospec only sees class attributes: stub the engineio client that
    # the real AsyncClient.__init__ creates as an instance attribute.
    socket_session._sio.eio = MagicMock()
    yield socket_session
    await session.aclose_owned_session()


@pytest.mark.asyncio
async def test_cancel_is_idempotent(socket_session):
    """cancel() must not disconnect twice (races on the AsyncClient)."""
    socket_session._sio.disconnect = AsyncMock()
    await socket_session.cancel()
    await socket_session.cancel()
    assert socket_session._sio.disconnect.await_count == 1
    assert socket_session._loop_should_exit is True


@pytest.mark.asyncio
async def test_cancel_is_bounded_on_wedged_socket(socket_session, monkeypatch):
    """A hanging disconnect must not make cancel() wait forever."""
    monkeypatch.setattr(socket_mod, "_DISCONNECT_TIMEOUT", 0.05)
    hang_task = asyncio.create_task(asyncio.sleep(30))

    async def hang():
        await hang_task

    socket_session._sio.disconnect = hang
    try:
        start = time.monotonic()
        await asyncio.wait_for(socket_session.cancel(), 2)
        elapsed = time.monotonic() - start
    finally:
        hang_task.cancel()
    assert elapsed < 1


@pytest.mark.asyncio
async def test_run_refreshes_auth_once_before_connect_loop(socket_session):
    """run() authenticates up front; the exit flag prevents further refreshes."""
    socket_session._sio.disconnect = AsyncMock()
    check_refresh_auth = AsyncMock()

    async def fake_attempt(url):
        socket_session._loop_should_exit = True
        return True

    with (
        patch.object(
            socket_session._session, "check_refresh_auth", check_refresh_auth
        ),
        patch.object(socket_session, "_attempt_connection", fake_attempt),
    ):
        await socket_session.run()
    # Exactly one refresh: the pre-loop one. The exit flag short-circuits
    # the per-cycle refresh after the (fake) connect.
    assert check_refresh_auth.await_count == 1


@pytest.mark.asyncio
async def test_run_skips_refresh_when_already_exiting(socket_session):
    """A pre-set exit flag must skip even the initial auth refresh."""
    socket_session._loop_should_exit = True
    socket_session._sio.disconnect = AsyncMock()
    check_refresh_auth = AsyncMock()
    with patch.object(
        socket_session._session, "check_refresh_auth", check_refresh_auth
    ):
        await socket_session.run()
    assert check_refresh_auth.await_count == 0


@pytest.mark.asyncio
async def test_shutdown_removes_installed_sigint_handler(socket_session):
    """Shutdown must drop the SIGINT handler this session installed."""
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: None)
    socket_session._sigint_loop = loop
    socket_session._sio.disconnect = AsyncMock()
    await socket_session.shutdown()
    assert socket_session._sigint_loop is None
    # Handler is really gone: a second remove reports nothing was set.
    assert loop.remove_signal_handler(signal.SIGINT) is False


@pytest.mark.asyncio
async def test_connect_event_tolerates_no_signal_handler_support(
    socket_session,
    mocker,
):
    """Platforms without signal-handler support must not kill the connect."""
    # The fixture already mocks socketio.AsyncClient: no nested patching.
    session = AsyncSmartboxSession(
        api_name="test_api",
        username="test_user",
        password="test_password",
    )
    ss = SocketSession(session, "device_id", add_sigint_handler=True)
    try:
        connect_cb = ss._sio.event.call_args[0][0]
        mock_loop = MagicMock()
        mock_loop.add_signal_handler.side_effect = NotImplementedError
        mocker.patch("asyncio.get_running_loop", return_value=mock_loop)
        await connect_cb()
        # Nothing installed -> nothing to remove at shutdown.
        assert ss._sigint_loop is None
    finally:
        await session.aclose_owned_session()


@pytest.mark.asyncio
async def test_shutdown_awaits_dangling_cleanup_tasks(socket_session):
    """Pending background tasks must be awaited (bounded), not destroyed."""
    socket_session._sio.disconnect = AsyncMock()
    done = False

    async def cleanup():
        nonlocal done
        await asyncio.sleep(0.01)
        done = True

    task = asyncio.create_task(cleanup())
    socket_session._background_tasks.add(task)
    await socket_session.shutdown()
    assert done


@pytest.mark.asyncio
async def test_dev_data_skipped_when_namespace_disconnected(socket_session):
    """_dev_data must not emit on a disconnected namespace."""
    socket_session._sio.emit = AsyncMock()
    socket_session._api_v2_ns._namespace_connected = False
    await socket_session._dev_data()
    assert socket_session._sio.emit.await_count == 0

    socket_session._api_v2_ns._namespace_connected = True
    await socket_session._dev_data()
    socket_session._sio.emit.assert_awaited_once_with(
        "dev_data",
        namespace=socket_mod._API_V2_NAMESPACE,
    )


@pytest.mark.asyncio
async def test_disconnect_once_reraises_cancellation(
    socket_session, monkeypatch
):
    """Cancellation of the disconnect wait must propagate, not be swallowed."""
    monkeypatch.setattr(socket_mod, "_DISCONNECT_TIMEOUT", 30)

    async def hang():
        await asyncio.sleep(30)

    socket_session._sio.disconnect = hang
    task = asyncio.create_task(socket_session._disconnect_once())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _stub_run_io(socket_session):
    """Stub the network edges of run(): ping task and disconnect."""
    socket_session._sio.disconnect = AsyncMock()
    socket_session._sio.start_background_task = MagicMock(
        side_effect=lambda _target: asyncio.ensure_future(asyncio.sleep(30)),
    )


@pytest.mark.asyncio
async def test_cancel_during_backoff_stops_reconnecting(socket_session):
    """Regression: cancel() mid-backoff must not trigger further attempts."""
    _stub_run_io(socket_session)
    socket_session._reconnect_attempts = 3
    socket_session._backoff_factor = 0.2
    attempts = []

    async def failing_attempt(url):
        attempts.append(socket_session._loop_should_exit)
        return False

    with (
        patch.object(
            socket_session._session,
            "check_refresh_auth",
            AsyncMock(),
        ),
        patch.object(socket_session, "_attempt_connection", failing_attempt),
    ):
        run_task = asyncio.create_task(socket_session.run())
        await asyncio.sleep(0.05)  # first attempt failed; now in backoff
        start = time.monotonic()
        await socket_session.cancel()
        await asyncio.wait_for(run_task, 2)
    assert attempts == [False]
    # The backoff sleep is woken by cancel(), not waited out.
    assert time.monotonic() - start < 0.15


@pytest.mark.asyncio
async def test_connect_completing_after_cancel_is_disconnected(
    socket_session,
    monkeypatch,
):
    """Regression: a connect that lands after cancel() must not leak.

    cancel()'s one-shot disconnect already ran, so without the post-connect
    exit check run() parked on the fresh socket forever.
    """
    monkeypatch.setattr(socket_mod, "_DISCONNECT_TIMEOUT", 1)
    _stub_run_io(socket_session)
    socket_session._sio.wait = AsyncMock()  # must never be reached
    connect_gate = asyncio.Event()

    async def slow_connect(*args, **kwargs):
        await connect_gate.wait()

    socket_session._sio.connect = slow_connect
    with patch.object(
        socket_session._session,
        "check_refresh_auth",
        AsyncMock(),
    ):
        run_task = asyncio.create_task(socket_session.run())
        await asyncio.sleep(0.02)  # parked inside connect
        await socket_session.cancel()
        connect_gate.set()  # the connect succeeds only now
        done, _ = await asyncio.wait({run_task}, timeout=2)
    assert run_task in done
    # cancel()'s disconnect + the post-connect one for the late socket.
    assert socket_session._sio.disconnect.await_count == 2
    socket_session._sio.wait.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [APIUnavailableError("net blip"), SmartboxError("bad token body")],
)
@pytest.mark.asyncio
async def test_run_survives_transient_refresh_failure(socket_session, error):
    """Regression: a transient refresh failure must not end run() for good."""
    _stub_run_io(socket_session)
    socket_session._backoff_factor = 0.01
    refreshes = 0

    async def flaky_refresh():
        nonlocal refreshes
        refreshes += 1
        if refreshes == 2:  # the between-cycles refresh
            raise error

    connects = 0

    async def fake_attempt(url):
        nonlocal connects
        connects += 1
        if connects == 2:
            socket_session._loop_should_exit = True
        return True

    with (
        patch.object(
            socket_session._session,
            "check_refresh_auth",
            flaky_refresh,
        ),
        patch.object(socket_session, "_attempt_connection", fake_attempt),
    ):
        await asyncio.wait_for(socket_session.run(), 2)
    # Pre-loop refresh, failed refresh, its successful retry; then the
    # loop reconnected instead of dying.
    assert refreshes == 3
    assert connects == 2


@pytest.mark.asyncio
async def test_run_propagates_rejected_credentials(socket_session):
    """InvalidAuthError (after the password fallback) still ends run()."""
    _stub_run_io(socket_session)
    with (
        patch.object(
            socket_session._session,
            "check_refresh_auth",
            AsyncMock(side_effect=InvalidAuthError("rejected")),
        ),
        pytest.raises(InvalidAuthError),
    ):
        await asyncio.wait_for(socket_session.run(), 2)


@pytest.mark.asyncio
async def test_ping_task_survives_failed_send(socket_session):
    """Regression: one failed ping send must not end the keepalive."""
    socket_session._ping_interval = 0
    socket_session._api_v2_ns._namespace_connected = True
    sends = 0

    async def flaky_send(*args, **kwargs):
        nonlocal sends
        sends += 1
        if sends == 1:
            msg = "/api/v2/socket_io is not a connected namespace."
            raise socketio.exceptions.BadNamespaceError(msg)

    socket_session._sio.sleep = asyncio.sleep
    socket_session._sio.send = flaky_send
    ping_task = asyncio.create_task(socket_session._send_ping())
    try:
        await asyncio.sleep(0.05)
        assert not ping_task.done()
        assert sends > 1
    finally:
        ping_task.cancel()


@pytest.mark.asyncio
async def test_connected_log_does_not_leak_token(socket_session, caplog):
    """Regression: the INFO 'connected' line logged the raw ?token= URL."""
    _stub_run_io(socket_session)
    socket_session._sio.connect = AsyncMock()
    socket_session._sio.wait = AsyncMock()
    socket_session._sio.emit = AsyncMock()
    url = "https://api.example/?token=SUPERSECRETTOKEN&dev_id=dev"
    with caplog.at_level(logging.DEBUG, logger="smartbox.socket"):
        assert await socket_session._attempt_connection(url) is True
    assert "Successfully connected" in caplog.text
    assert "SUPERSECRETTOKEN" not in caplog.text


@pytest.mark.asyncio
async def test_shutdown_reaps_pending_ping_task(socket_session, monkeypatch):
    """A pending ping task must be cancelled and awaited on shutdown."""
    monkeypatch.setattr(socket_mod, "_DISCONNECT_TIMEOUT", 1)
    socket_session._sio.disconnect = AsyncMock()

    async def ping():
        await asyncio.sleep(30)

    ping_task = asyncio.create_task(ping())
    socket_session._ping_task = ping_task
    await asyncio.sleep(0.01)  # let the ping task start
    await socket_session.shutdown()
    assert ping_task.cancelled()


@pytest.mark.asyncio
async def test_socket_knobs_are_validated(socket_session):
    """ping_interval<=0 and reconnect_attempts<1 are rejected up front."""
    with pytest.raises(ValueError, match="ping_interval"):
        SocketSession(socket_session._session, "device_id", ping_interval=0)
    with pytest.raises(ValueError, match="ping_interval"):
        SocketSession(socket_session._session, "device_id", ping_interval=-5)
    with pytest.raises(ValueError, match="reconnect_attempts"):
        SocketSession(
            socket_session._session,
            "device_id",
            reconnect_attempts=0,
        )
    with pytest.raises(ValueError, match="reconnect_attempts"):
        SocketSession(
            socket_session._session,
            "device_id",
            reconnect_attempts=-3,
        )


@pytest.mark.asyncio
async def test_disconnect_timeout_reaps_inner_task(
    socket_session,
    monkeypatch,
):
    """A timed-out disconnect task must be cancelled and reaped."""
    monkeypatch.setattr(socket_mod, "_DISCONNECT_TIMEOUT", 0.05)

    async def hang():
        await asyncio.sleep(30)

    socket_session._sio.disconnect = hang
    await socket_session._disconnect_now()
    lingering = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
    ]
    assert not lingering


@pytest.mark.asyncio
async def test_shutdown_reaps_cancelled_background_tasks(
    socket_session,
    monkeypatch,
):
    """Cancelled still-pending background tasks are reaped on shutdown."""
    monkeypatch.setattr(socket_mod, "_DISCONNECT_TIMEOUT", 0.5)
    socket_session._sio.disconnect = AsyncMock()

    async def slow():
        await asyncio.sleep(30)

    task = asyncio.create_task(slow())
    socket_session._background_tasks.add(task)
    await asyncio.sleep(0.01)  # let the task start
    await socket_session.shutdown()
    assert task.cancelled()


def test_constructed_outside_running_loop(reseller):
    """Regression: construction must not need a running event loop.

    The websession binding is deferred to ``run()``; eagerly touching
    ``session.client`` in ``__init__`` crashed with
    "RuntimeError: no running event loop" (aiohttp needs a loop).
    """
    session = AsyncSmartboxSession(
        api_name="test_api",
        username="test_user",
        password="test_password",
    )
    with patch("smartbox.socket.socketio.AsyncClient", autospec=True):
        SocketSession(session, "device_id")
    # The REST session's websession was not materialised at construction.
    assert session._client_session is None


@pytest.mark.asyncio
async def test_run_binds_shared_http_session(socket_session):
    """run() binds the REST websession before connecting, engineio-owned."""
    socket_session._sio.disconnect = AsyncMock()

    async def fake_attempt(url):
        socket_session._loop_should_exit = True
        return True

    with (
        patch.object(
            socket_session._session, "check_refresh_auth", AsyncMock()
        ),
        patch.object(socket_session, "_attempt_connection", fake_attempt),
    ):
        await socket_session.run()
    assert socket_session._sio.eio.http is socket_session._session.client
    assert socket_session._sio.eio.external_http is True


@pytest.mark.asyncio
async def test_dev_data_emit_race_is_not_fatal(socket_session):
    """Regression: BadNamespaceError from the first dev_data emit.

    The emit races the server drop (connected check then send) and
    surfaced as a BadNamespaceError — a sibling of ConnectionError —
    escaping _attempt_connection and killing run() for good.
    """
    socket_session._sio.connect = AsyncMock()
    socket_session._sio.wait = AsyncMock()
    socket_session._sio.disconnect = AsyncMock()
    with patch.object(
        socket_session,
        "_dev_data",
        AsyncMock(side_effect=socketio.exceptions.BadNamespaceError("gone")),
    ):
        assert await socket_session._attempt_connection("http://host") is False


@pytest.mark.asyncio
async def test_bad_namespace_error_on_connect_is_not_fatal(socket_session):
    """A connect-time BadNamespaceError goes to the reconnect cycle."""
    socket_session._sio.connect = AsyncMock(
        side_effect=socketio.exceptions.BadNamespaceError("no ns")
    )
    assert await socket_session._attempt_connection("http://host") is False


@pytest.mark.asyncio
async def test_namespace_connect_and_disconnect_track_state(socket_session):
    """on_connect/on_disconnect drive ``connected`` and reset the flags."""
    ns = socket_session.namespace
    assert ns.connected is False
    await ns.on_connect()
    assert ns.connected is True
    ns._received_message = True
    ns._received_dev_data = True
    await ns.on_disconnect("transport close")
    assert ns.connected is False
    assert ns._received_message is False
    assert ns._received_dev_data is False


@pytest.mark.asyncio
async def test_namespace_dev_data_invokes_callback(socket_session):
    """dev_data marks the namespace ready and forwards the payload."""
    callback = MagicMock()
    ns = socket_module_namespace(socket_session, dev_data_callback=callback)
    await ns.on_dev_data({"nodes": []})
    callback.assert_called_once_with({"nodes": []})
    assert ns._received_dev_data is True


@pytest.mark.asyncio
async def test_namespace_dev_data_without_callback_is_ok(socket_session):
    ns = socket_module_namespace(socket_session)
    await ns.on_dev_data({"nodes": []})
    assert ns._received_message is True


@pytest.mark.asyncio
async def test_namespace_first_update_requests_dev_data(socket_session):
    """The first update triggers the dev_data request and is then dropped."""
    callback = MagicMock()
    ns = socket_module_namespace(socket_session, node_update_callback=callback)
    ns.emit = AsyncMock()
    await ns.on_update({"path": "/htr/1/status"})
    ns.emit.assert_awaited_once_with("dev_data", namespace=ns._namespace)
    callback.assert_not_called()


@pytest.mark.asyncio
async def test_namespace_update_forwarded_after_dev_data(socket_session):
    callback = MagicMock()
    ns = socket_module_namespace(socket_session, node_update_callback=callback)
    await ns.on_dev_data({})
    await ns.on_update({"path": "/htr/1/status"})
    callback.assert_called_once_with({"path": "/htr/1/status"})


def socket_module_namespace(socket_session, **callbacks):
    return socket_mod.SmartboxAPIV2Namespace(
        socket_session._session, "/api/v2/socket_io", **callbacks
    )


@pytest.mark.asyncio
async def test_cleanup_websocket_closes_open_ws(socket_session):
    ws = MagicMock(closed=False)
    ws.close = AsyncMock()
    socket_session._sio.eio.ws = ws
    await socket_session._cleanup_websocket()
    ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_websocket_skips_closed_ws(socket_session):
    ws = MagicMock(closed=True)
    ws.close = AsyncMock()
    socket_session._sio.eio.ws = ws
    await socket_session._cleanup_websocket()
    ws.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_websocket_swallows_close_errors(socket_session):
    ws = MagicMock(closed=False)
    ws.close = AsyncMock(side_effect=OSError("boom"))
    socket_session._sio.eio.ws = ws
    await socket_session._cleanup_websocket()  # must not raise


@pytest.mark.asyncio
async def test_disconnect_now_swallows_oserror(socket_session):
    socket_session._sio.disconnect = AsyncMock(side_effect=OSError("boom"))
    await socket_session._disconnect_now()  # must not raise


@pytest.mark.asyncio
async def test_remove_sigint_handler_tolerates_failure(socket_session):
    loop = MagicMock()
    loop.remove_signal_handler.side_effect = ValueError("bad")
    socket_session._sigint_loop = loop
    socket_session._remove_sigint_handler()
    assert socket_session._sigint_loop is None


@pytest.mark.asyncio
async def test_connect_event_installs_sigint_handler_that_cancels(
    socket_session, mocker
):
    """The installed SIGINT callback schedules cancel() as a tracked task."""
    session = AsyncSmartboxSession(
        api_name="test_api",
        username="test_user",
        password="test_password",
    )
    ss = SocketSession(session, "device_id", add_sigint_handler=True)
    try:
        ss.cancel = AsyncMock()
        connect_cb = ss._sio.event.call_args[0][0]
        mock_loop = MagicMock()
        mocker.patch("asyncio.get_running_loop", return_value=mock_loop)
        await connect_cb()
        handler = mock_loop.add_signal_handler.call_args[0][1]
        handler()
        (task,) = ss._background_tasks
        await task
        ss.cancel.assert_awaited_once()
        assert not ss._background_tasks
        assert ss._sigint_loop is mock_loop
    finally:
        await session.aclose_owned_session()


@pytest.mark.asyncio
async def test_cancel_during_connect_schedules_cleanup(socket_session):
    """A cancel mid-connect disconnects the dangling socket once it lands."""
    release = asyncio.Event()

    async def slow_connect(*args, **kwargs):
        await release.wait()

    socket_session._sio.connect = slow_connect
    socket_session._sio.disconnect = AsyncMock()
    attempt = asyncio.create_task(
        socket_session._attempt_connection("wss://example/socket")
    )
    await asyncio.sleep(0)
    attempt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attempt
    (cleanup,) = socket_session._background_tasks
    release.set()
    await cleanup
    socket_session._sio.disconnect.assert_awaited_once()
    assert not socket_session._background_tasks


@pytest.mark.asyncio
async def test_cancel_during_connect_cleanup_swallows_errors(socket_session):
    release = asyncio.Event()

    async def slow_connect(*args, **kwargs):
        await release.wait()

    socket_session._sio.connect = slow_connect
    socket_session._sio.disconnect = AsyncMock(side_effect=RuntimeError("x"))
    attempt = asyncio.create_task(
        socket_session._attempt_connection("wss://example/socket")
    )
    await asyncio.sleep(0)
    attempt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attempt
    (cleanup,) = socket_session._background_tasks
    release.set()
    await cleanup  # the RuntimeError must not escape
