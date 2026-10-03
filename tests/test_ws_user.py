"""Tests for the per-user ``ws_user`` websocket transport.

Covers: per-device fan-out (all_data/update routing), unknown-device
frames ignored, should_sync debounce, consumer-exception isolation,
deterministic 404/410 → WsUserUnsupportedError vs retriable 401,
heartbeat knob passed through, cycle URLs, bounded close, construction
outside a running loop, and credential rejection propagation.
"""

import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from smartbox.error import (
    APIUnavailableError,
    InvalidAuthError,
    SmartboxError,
    WsUserUnsupportedError,
)
from smartbox.session import AsyncSmartboxSession
from smartbox.update_manager import UpdateManager
import smartbox.ws_user as ws_user_mod
from smartbox.ws_user import WsUserSocketSession, check_ws_user_support

# A minimal JWT whose payload decodes to {"userId": "user_under_test"}.
_PAYLOAD = json.dumps({"userId": "user_under_test"}).encode()
_B64 = base64.urlsafe_b64encode(_PAYLOAD).rstrip(b"=").decode()
FAKE_JWT = f"hdr.{_B64}.sig"
_WS_URL = "wss://host/api/v2/ws_user?token=x&user_id=user_under_test"


class FakeWs:
    """Minimal fake aiohttp websocket with a scripted receive queue."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []
        self.closed_flag = False
        self.close_code = 1006
        self._closed_event = asyncio.Event()
        self.send_json = AsyncMock(side_effect=self._send_json)
        self.close = AsyncMock(side_effect=self._close)

    async def _send_json(self, payload):
        self.sent.append(payload)

    async def _close(self):
        self.closed_flag = True
        self._closed_event.set()

    @property
    def closed(self):
        return self.closed_flag

    async def receive(self):
        if self._frames:
            frame = self._frames.pop(0)
            if isinstance(frame, Exception):
                raise frame
            return frame
        # Park on the close event (as a live receive() parks on data):
        # close() wakes this, mirroring aiohttp's CLOSED-delivery.
        await self._closed_event.wait()
        return aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)


class FakeWsCtx:
    """Async context manager returning the fake websocket."""

    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *exc):
        return False


class HangingWsCtx:
    """Async context manager whose enter parks (a live connection)."""

    async def __aenter__(self):
        await asyncio.sleep(30)

    async def __aexit__(self, *exc):
        return False


def closed_message():
    return aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)


def text_frame(payload):
    return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps(payload), None)


@pytest.fixture
async def ws_user_session(mocker, reseller):
    """Return a real session with the auth refresh stubbed for ws_user.

    Named ``ws_user_session`` so it cannot be confused with conftest's
    sync ``session`` fixture (a different type with a different API).
    """
    session = AsyncSmartboxSession(
        api_name="test_api",
        username="test_user",
        password="test_password",
    )
    session._access_token = FAKE_JWT
    mocker.patch.object(session, "check_refresh_auth", AsyncMock())
    yield session
    await session.aclose_owned_session()


def make_ws_socket(
    mocker, ws_user_session, frames, ws=None, connect_error=None
):
    """Create a WsUserSocketSession with ws_connect patched to a fake.

    ``connect_error`` makes the (first and every) connect attempt raise
    instead, simulating a handshake rejection. The patch is managed by
    ``mocker`` (auto-reverted) so it cannot leak across tests.
    """
    if ws is None:
        ws = FakeWs(frames)
    captured = {}

    def fake_ws_connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        if connect_error is not None:
            raise connect_error
        return FakeWsCtx(ws)

    mocker.patch.object(
        aiohttp.ClientSession,
        "ws_connect",
        MagicMock(side_effect=fake_ws_connect),
        create=True,
    )
    sock = WsUserSocketSession(ws_user_session)
    return sock, ws, captured


@pytest.mark.asyncio
async def test_all_data_fans_out_to_registered_devices(ws_user_session, mocker):
    """Each registered device gets its own devData payload; unknown ignored."""
    snapshot = {
        "event": "all_data",
        "data": [
            {
                "id": "h1",
                "name": "home",
                "owner": True,
                "extraData": None,
                "devs": [
                    {
                        "dev_id": "dev_a",
                        "name": "a",
                        "devData": {"connected": True, "nodes": []},
                    },
                    {
                        "dev_id": "dev_b",
                        "name": "b",
                        "devData": {"connected": False, "nodes": []},
                    },
                    {
                        "dev_id": "dev_ghost",
                        "name": "g",
                        "devData": {"connected": False},
                    },
                ],
            }
        ],
    }
    sock, ws, captured = make_ws_socket(
        mocker, ws_user_session, [text_frame(snapshot), closed_message()]
    )
    got_a, got_b = [], []
    sock.add_device("dev_a", got_a.append, lambda _d: None)
    sock.add_device("dev_b", got_b.append, lambda _d: None)
    served = await sock._attempt_cycle(_WS_URL)
    assert served is True
    assert got_a == [{"connected": True, "nodes": []}]
    assert got_b == [{"connected": False, "nodes": []}]
    # the all_data request was sent on open
    assert ws.sent == [{"event": "all_data"}]
    # url carries the ws_user path plus the user_id claim from the token
    assert "/api/v2/ws_user" in captured["url"]
    assert "user_id=user_under_test" in captured["url"]


@pytest.mark.asyncio
async def test_update_routed_by_devid(ws_user_session, mocker):
    """Update frames go to the right device's update callback with {path, body}."""
    update = {
        "event": "update",
        "devid": "dev_a",
        "data": {"path": "/htr/1/status", "body": {"active": True}},
    }
    sock, _, _ = make_ws_socket(
        mocker, ws_user_session, [text_frame(update), closed_message()]
    )
    got_updates, other_updates = [], []
    sock.add_device("dev_a", lambda _d: None, got_updates.append)
    sock.add_device("dev_b", lambda _d: None, other_updates.append)
    served = await sock._attempt_cycle(_WS_URL)
    assert served is True
    assert got_updates == [{"path": "/htr/1/status", "body": {"active": True}}]
    assert other_updates == []


@pytest.mark.asyncio
async def test_consumer_exceptions_are_isolated(ws_user_session, mocker):
    """A raising consumer must not starve the others or kill the cycle."""
    snapshot = {
        "event": "all_data",
        "data": [
            {
                "id": "h1",
                "name": "home",
                "owner": True,
                "extraData": None,
                "devs": [
                    {"dev_id": "dev_a", "name": "a", "devData": {}},
                    {"dev_id": "dev_b", "name": "b", "devData": {}},
                ],
            }
        ],
    }
    bug = RuntimeError("consumer bug")

    def boom(_data):
        raise bug

    sock, _, _ = make_ws_socket(
        mocker, ws_user_session, [text_frame(snapshot), closed_message()]
    )
    got_b = []
    sock.add_device("dev_a", boom, boom)
    sock.add_device("dev_b", got_b.append, got_b.append)
    served = await sock._attempt_cycle(_WS_URL)
    assert served is True
    assert got_b == [{}]


@pytest.mark.asyncio
async def test_should_sync_schedules_debounced_all_data(
    ws_user_session, mocker
):
    """should_sync re-requests the snapshot after the debounce window."""
    sock, ws, _ = make_ws_socket(mocker, ws_user_session, [])
    # Seed a per-cycle websocket as _attempt_cycle would; shrink the
    # debounce knob (the sibling add_device test's pattern) instead of
    # patching global asyncio.sleep.
    sock._ws = ws
    sock._should_sync_debounce = 0.01
    sock._schedule_all_data()
    task = sock._should_sync_task
    assert task is not None
    await task
    assert ws.sent == [{"event": "all_data"}]


@pytest.mark.asyncio
async def test_deterministic_404_raises_unsupported(ws_user_session, mocker):
    """A 404 handshake rejection must surface as WsUserUnsupportedError."""
    err = aiohttp.ClientResponseError(
        MagicMock(), (), status=404, message="Not Found"
    )
    sock, _, _ = make_ws_socket(mocker, ws_user_session, [], connect_error=err)
    with pytest.raises(WsUserUnsupportedError, match="404"):
        await sock._attempt_cycle(_WS_URL)


@pytest.mark.asyncio
async def test_expired_token_401_signals_auth_rejected(ws_user_session, mocker):
    """A 401 handshake rejection is NOT a plain failure: run() must refresh.

    _attempt_cycle raises _HandshakeAuthRejectedError; run() turns it
    into a forced token invalidation + refresh (retrying the same URL
    would no-op under clock skew — the verified mid-run-401 finding).
    """
    err = aiohttp.ClientResponseError(
        MagicMock(), (), status=401, message="Unauthorized"
    )
    sock, _, _ = make_ws_socket(mocker, ws_user_session, [], connect_error=err)
    with pytest.raises(ws_user_mod._HandshakeAuthRejectedError):
        await sock._attempt_cycle(_WS_URL)


@pytest.mark.asyncio
async def test_update_manager_cancel_stops_run_in_ws_mode(
    ws_user_session, mocker
):
    """cancel() must stop a waiting run() (socket_io contract) in ws mode.

    run() parks on its release event only (it never consults
    wait_closed), so the release must be what wakes it — promptly.
    """
    shared, _, _ = make_ws_socket(mocker, ws_user_session, [])
    manager = UpdateManager(ws_user_session, "dev_x", ws_user_socket=shared)
    run_task = asyncio.create_task(manager.run())
    await asyncio.sleep(0.02)
    start = time.monotonic()
    await manager.cancel()
    await asyncio.wait_for(run_task, 2)
    assert time.monotonic() - start < 1
    assert "dev_x" not in shared._devices


@pytest.mark.asyncio
async def test_manager_run_parks_through_shared_crash_exit(
    ws_user_session, mocker
):
    """A REAL shared-socket crash exit must not wake UpdateManager.run().

    End-to-end pin of the seam contract: the shared
    session's run() raises; the manager stays parked (registrations
    survive restarts), a restarted shared run serves it, and only
    manager.cancel() releases it.
    """
    attempts = {"n": 0}
    shared_crash = RuntimeError("unexpected shared-socket crash")

    def fake_ws_connect(_self, _url, **_kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise shared_crash
        return HangingWsCtx()

    mocker.patch.object(
        aiohttp.ClientSession, "ws_connect", fake_ws_connect, create=True
    )
    sock = WsUserSocketSession(ws_user_session)
    manager = UpdateManager(ws_user_session, "dev_x", ws_user_socket=sock)
    manager_task = asyncio.create_task(manager.run())
    await asyncio.sleep(0.02)
    assert "dev_x" in sock._devices

    # First shared run() crashes; the manager must stay parked.
    with pytest.raises(RuntimeError, match="unexpected shared-socket crash"):
        await asyncio.wait_for(sock.run(), 2)
    await asyncio.sleep(0.02)
    assert not manager_task.done(), "manager woke on a shared-socket exit"
    assert "dev_x" in sock._devices, "registration lost across the exit"

    # Supervisor-style restart: the shared run parks again; teardown
    # (manager.cancel()) is what releases the manager.
    shared_run = asyncio.create_task(sock.run())
    await asyncio.sleep(0.02)
    assert not manager_task.done()
    await manager.cancel()
    await asyncio.wait_for(manager_task, 1)
    assert "dev_x" not in sock._devices
    shared_run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(shared_run, 1)


@pytest.mark.asyncio
async def test_heartbeat_knob_is_passed_through(ws_user_session, mocker):
    """The heartbeat parameter must reach ws_connect."""
    sock, _, captured = make_ws_socket(
        mocker, ws_user_session, [closed_message()]
    )
    sock._heartbeat = 37.5
    await sock._attempt_cycle(_WS_URL)
    assert captured["kwargs"]["heartbeat"] == 37.5


@pytest.mark.asyncio
async def test_run_second_cycle_uses_session_token(ws_user_session, mocker):
    """Every cycle builds its URL from the session's current token."""
    frames = [text_frame({"event": "all_data", "data": []}), closed_message()]
    sock, _, _ = make_ws_socket(mocker, ws_user_session, frames)
    seen_urls = []

    def fake_ws_connect(url, **kwargs):
        seen_urls.append(url)
        return FakeWsCtx(FakeWs(frames))

    with patch.object(
        aiohttp.ClientSession,
        "ws_connect",
        MagicMock(side_effect=fake_ws_connect),
        create=True,
    ):
        task = asyncio.create_task(sock.run())
        await asyncio.sleep(0.1)
        await sock.cancel()
        await asyncio.wait_for(task, 2)
    assert seen_urls, "no connect attempts recorded"
    assert all("/api/v2/ws_user" in u for u in seen_urls)
    assert all("user_id=user_under_test" in u for u in seen_urls)


@pytest.mark.asyncio
async def test_cancel_is_bounded_on_hanging_close(
    ws_user_session, mocker, monkeypatch
):
    """close() hanging must not make cancel() wait forever."""
    monkeypatch.setattr(ws_user_mod, "_DISCONNECT_TIMEOUT", 0.05)

    async def hang_close():
        await asyncio.sleep(30)

    ws = FakeWs([])
    ws.close = hang_close
    sock, _, _ = make_ws_socket(mocker, ws_user_session, [], ws=ws)
    sock._ws = ws
    start = time.monotonic()
    await asyncio.wait_for(sock.cancel(), 2)
    assert time.monotonic() - start < 1


def test_constructed_outside_running_loop(reseller):
    """Construction outside a running loop must not fail (HA setup pattern)."""
    session = AsyncSmartboxSession(
        api_name="test_api",
        username="test_user",
        password="test_password",
    )
    sock = WsUserSocketSession(session)
    assert sock.closed is False


@pytest.mark.asyncio
async def test_run_propagates_rejected_credentials(ws_user_session, mocker):
    """InvalidAuthError from the auth refresh must propagate out of run()."""
    sock, _, _ = make_ws_socket(mocker, ws_user_session, [])
    mocker.patch.object(
        ws_user_session,
        "check_refresh_auth",
        AsyncMock(side_effect=InvalidAuthError("bad credentials")),
    )
    with pytest.raises(InvalidAuthError):
        await asyncio.wait_for(sock.run(), 2)


@pytest.mark.asyncio
async def test_run_survives_transient_refresh_failure(ws_user_session, mocker):
    """A transient auth-refresh failure must be waited out, not fatal."""
    sock, _, _ = make_ws_socket(mocker, ws_user_session, [closed_message()])
    sock._backoff_factor = 0.01
    state = {"calls": 0}

    down = APIUnavailableError("down")

    async def flaky_refresh():
        state["calls"] += 1
        if state["calls"] == 1:
            raise down

    mocker.patch.object(ws_user_session, "check_refresh_auth", flaky_refresh)
    task = asyncio.create_task(sock.run())
    await asyncio.sleep(0.05)
    await sock.cancel()
    await asyncio.wait_for(task, 2)
    assert state["calls"] >= 2


@pytest.mark.asyncio
async def test_knobs_are_validated(ws_user_session):
    """Invalid construction knobs must raise ValueError."""
    for kwargs in (
        {"heartbeat": 0.0},
        {"reconnect_attempts": 0},
        {"backoff_factor": 0.0},
        {"should_sync_debounce": -1.0},
    ):
        with pytest.raises(ValueError, match="must be"):
            WsUserSocketSession(ws_user_session, **kwargs)


@pytest.mark.asyncio
async def test_malformed_frames_are_ignored(ws_user_session, mocker, caplog):
    """Unparseable/malformed frames must be logged and skipped."""
    sock, _, _ = make_ws_socket(mocker, ws_user_session, [closed_message()])
    with caplog.at_level("WARNING"):
        sock._dispatch("not json")
        sock._dispatch('{"event": "update"}')
        sock._dispatch('{"event": "update", "devid": "d", "data": {"x": 1}}')
        # Parseable JSON that is not an object must be dropped too: it
        # would otherwise AttributeError out of run() on every resend.
        sock._dispatch("[1, 2]")
        sock._dispatch("null")
    assert any(
        "Unparseable ws_user frame" in r.getMessage() for r in caplog.records
    )
    assert any(
        "Malformed update frame" in r.getMessage() for r in caplog.records
    )
    assert any("not an object" in r.getMessage() for r in caplog.records)
    # Unknown event types are degraded to a debug log, not a warning.
    assert not any("future_kind" in r.getMessage() for r in caplog.records)
    with caplog.at_level("DEBUG"):
        sock._dispatch('{"event": "future_kind", "data": []}')
    assert any("future_kind" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_check_ws_user_support_ok(ws_user_session, mocker):
    """A successful handshake returns quietly."""
    mocker.patch.object(
        aiohttp.ClientSession,
        "ws_connect",
        MagicMock(return_value=FakeWsCtx(FakeWs([]))),
        create=True,
    )
    await check_ws_user_support(ws_user_session)


@pytest.mark.asyncio
async def test_check_ws_user_support_deterministic_rejections(
    ws_user_session, mocker
):
    """401/403/404/410 all demote in the fresh-token probe context."""
    for status in (401, 403, 404, 410):
        err = aiohttp.ClientResponseError(
            MagicMock(), (), status=status, message="no"
        )
        mocker.patch.object(
            aiohttp.ClientSession,
            "ws_connect",
            MagicMock(side_effect=err),
            create=True,
        )
        with pytest.raises(WsUserUnsupportedError, match=str(status)):
            await check_ws_user_support(ws_user_session)


@pytest.mark.asyncio
async def test_check_ws_user_support_transient_propagates(
    ws_user_session, mocker
):
    """Transient transport trouble must NOT demote (retried by caller)."""
    mocker.patch.object(
        aiohttp.ClientSession,
        "ws_connect",
        MagicMock(side_effect=OSError("network unreachable")),
        create=True,
    )
    with pytest.raises(OSError, match="network unreachable"):
        await check_ws_user_support(ws_user_session)


@pytest.mark.asyncio
async def test_check_ws_user_support_probe_timeout_is_bounded(
    ws_user_session, mocker, monkeypatch
):
    """A hung handshake must abort within _PROBE_TIMEOUT, not the 300 s default.

    The ws_connect handshake is not bounded by ClientWSTimeout (in
    aiohttp 3.14 that knob is a post-handshake read timeout only), so
    the probe wraps the whole handshake in asyncio.timeout() and lets
    the TimeoutError propagate as transient trouble (doctrine: retried
    by the caller, never treated as "unsupported").
    """
    monkeypatch.setattr(ws_user_mod, "_PROBE_TIMEOUT", 0.05)

    def hang(_self, _url, **_kwargs):
        # Regular def: ws_connect's return value IS the context manager.
        return HangingWsCtx()

    mocker.patch.object(aiohttp.ClientSession, "ws_connect", hang, create=True)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await check_ws_user_support(ws_user_session)
    assert time.monotonic() - start < 1


@pytest.mark.asyncio
async def test_check_ws_user_support_empty_token_is_transient(
    ws_user_session, mocker
):
    """An empty/never-fetched token raises SmartboxError — NOT unsupported.

    Doctrine-critical split: a missing token is a startup race (auth not
    yet awaited) and must retry, while a non-JWT token is a permanent
    demotion. Note SmartboxError is the base of WsUserUnsupportedError,
    so the negative assert must check the exact type.
    """
    ws_user_session._access_token = ""
    with pytest.raises(SmartboxError) as excinfo:
        await check_ws_user_support(ws_user_session)
    assert not isinstance(excinfo.value, WsUserUnsupportedError)


@pytest.mark.asyncio
async def test_check_ws_user_support_non_jwt_token_demotes(
    ws_user_session, mocker
):
    """A non-JWT access token cannot address the per-user endpoint."""
    ws_user_session._access_token = "opaque-token"
    mocker.patch.object(
        aiohttp.ClientSession,
        "ws_connect",
        MagicMock(return_value=FakeWsCtx(FakeWs([]))),
        create=True,
    )
    with pytest.raises(WsUserUnsupportedError):
        await check_ws_user_support(ws_user_session)


@pytest.mark.asyncio
async def test_run_is_reunnable_and_cancel_is_terminal(ws_user_session, mocker):
    """Pin the run model: exit → restart parks → cancel() makes run() a no-op.

    The supervisor restarts run() after any unexpected exit; only
    cancel() is terminal. A one-shot run() would leave the transport
    permanently dead after the first exception exit.
    """
    attempts = {"n": 0}
    internal_error = RuntimeError("unexpected internal error")

    def fake_ws_connect(_self, _url, **_kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise internal_error
        return HangingWsCtx()

    mocker.patch.object(
        aiohttp.ClientSession, "ws_connect", fake_ws_connect, create=True
    )

    # 1) The first run() propagates the unexpected connect error...
    sock = WsUserSocketSession(ws_user_session)
    with pytest.raises(RuntimeError, match="unexpected internal error"):
        await asyncio.wait_for(sock.run(), 2)
    assert attempts["n"] == 1
    assert sock.closed is True  # wait_closed() subscribers were woken

    # 2) ...a restart connects again and parks (re-runnable).
    restart_task = asyncio.create_task(sock.run())
    await asyncio.sleep(0.05)
    assert attempts["n"] == 2
    assert not sock.closed

    # 3) cancel() is terminal: bounded and complete (it cannot unblock a
    #    run parked in an in-flight handshake — that needs task cancel,
    #    which is how the integration's supervisor tears down). Then a
    #    later run() is a no-op.
    await asyncio.wait_for(sock.cancel(), 1)
    restart_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(restart_task, 1)
    assert sock.closed is True
    before = attempts["n"]
    await asyncio.wait_for(sock.run(), 0.5)
    assert attempts["n"] == before


@pytest.mark.asyncio
async def test_add_device_on_live_socket_schedules_all_data(
    ws_user_session, mocker
):
    """A device registered on a live socket triggers a snapshot re-request.

    add_device() while connected schedules the debounced all_data so a
    late registration (or a restart-path re-registration racing the
    snapshot) cannot sit silent until the next update frame.
    """
    sock, ws, _ = make_ws_socket(mocker, ws_user_session, [])
    sock._ws = ws
    sock._should_sync_debounce = 0.01
    done = asyncio.Event()

    def on_dev_data(_data):
        done.set()

    sock.add_device("dev_late", on_dev_data, lambda _d: None)
    task = sock._should_sync_task
    assert task is not None
    await asyncio.wait_for(task, 1)
    assert ws.sent == [{"event": "all_data"}]
    assert not done.is_set()  # the snapshot itself arrives via the wire


@pytest.mark.asyncio
async def test_attempts_exhaustion_falls_through_to_refresh(
    ws_user_session, mocker
):
    """After N failed attempts run() refreshes auth and cycles again."""
    err = OSError("connection refused")
    sock, _, _ = make_ws_socket(mocker, ws_user_session, [], connect_error=err)
    sock._reconnect_attempts = 2
    sock._backoff_factor = 0.001  # fast attempts; real tiny backoff sleeps
    refresh = AsyncMock()
    mocker.patch.object(ws_user_session, "check_refresh_auth", refresh)
    attempts = {"n": 0}

    def counting_connect(_url, **_kwargs):
        # MagicMock side_effect: no self binding (the mock replaces the
        # class attribute).
        attempts["n"] += 1
        raise err

    mocker.patch.object(
        aiohttp.ClientSession,
        "ws_connect",
        MagicMock(side_effect=counting_connect),
        create=True,
    )

    task = asyncio.create_task(sock.run())
    await asyncio.sleep(0.1)
    # Several failed attempts per cycle + refreshed cycles after.
    assert attempts["n"] >= 4
    await sock.cancel()
    await asyncio.wait_for(task, 2)
    assert refresh.await_count >= 2


@pytest.mark.asyncio
async def test_heartbeat_death_is_cycle_over(ws_user_session, mocker):
    """A ServerTimeoutError from receive() must end the cycle, not run().

    The supervisor's correctness depends on run() never returning on
    transient failure: the heartbeat death is an in-loop cycle-over
    (backoff + refresh + reconnect).
    """
    sock, _ws, _ = make_ws_socket(
        mocker, ws_user_session, [aiohttp.ServerTimeoutError("heartbeat died")]
    )
    refresh = AsyncMock()
    mocker.patch.object(ws_user_session, "check_refresh_auth", refresh)
    task = asyncio.create_task(sock.run())
    await asyncio.sleep(0.05)
    await sock.cancel()
    await asyncio.wait_for(task, 2)
    # run() was still alive after the heartbeat death (refresh re-armed
    # inside the loop) — it exited only via cancel().
    assert refresh.await_count >= 1


@pytest.mark.asyncio
async def test_dispatch_all_data_shape_guards(ws_user_session, mocker, caplog):
    """Malformed all_data shapes are logged and skipped, never raised."""
    sock, _, _ = make_ws_socket(mocker, ws_user_session, [])
    sock.add_device("dev_a", lambda _d: None, lambda _d: None)
    with caplog.at_level("WARNING"):
        sock._dispatch_all_data("not a list")
        sock._dispatch_all_data([{"no": "devs"}])
        sock._dispatch_all_data([{"devs": [{"dev_id": "x"}]}])  # no devData
        sock._dispatch_all_data([{"devs": [{"dev_id": None, "devData": {}}]}])
    assert any(
        "all_data snapshot is not a list" in r.getMessage()
        for r in caplog.records
    )
    assert any(
        "missing dev_id/devData" in r.getMessage() for r in caplog.records
    )


def test_backoff_formula():
    """Backoff doubles with ±20% jitter, capped, and saturates the exponent."""
    for attempt in range(20):
        for _ in range(50):
            delay = WsUserSocketSession._backoff_delay(
                WsUserSocketSession(session=None), attempt
            )
            base = min(1.0 * (2**attempt), 30.0)
            assert base <= delay <= base * 1.2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [502, 503, 429, 405])
async def test_probe_transient_status_is_retriable(
    ws_user_session, mocker, status
):
    """Non-doctrine handshake statuses are APIUnavailableError (retriable)."""
    err = aiohttp.ClientResponseError(
        MagicMock(), (), status=status, message="x"
    )
    mocker.patch.object(
        aiohttp.ClientSession,
        "ws_connect",
        MagicMock(side_effect=err),
        create=True,
    )
    with pytest.raises(APIUnavailableError):
        await check_ws_user_support(ws_user_session)


async def test_backoff_delay_saturates_before_overflow(ws_user_session):
    """A multi-day failure count must sleep the cap, not OverflowError."""
    sock = WsUserSocketSession(ws_user_session)
    for attempt in (100, 1024, 10_000):
        delay = sock._backoff_delay(attempt)
        base = min(1.0 * (2 ** min(attempt, 6)), 30.0)
        assert base <= delay <= base * 1.2


@pytest.mark.asyncio
async def test_run_demotes_after_persistent_auth_rejections(
    ws_user_session, mocker, monkeypatch
):
    """Handshake 401/403s of fresh tokens must eventually demote.

    Each cycle mints a NEW token (forced refresh) and the endpoint
    still 401s: without the ratchet the loop would grant tokens every
    second forever. Past _MAX_AUTH_REJECTED_CYCLES, run() raises
    WsUserUnsupportedError so the supervisor re-probes and falls back —
    the same verdict the one-shot probe returns for these statuses.
    """
    err = aiohttp.ClientResponseError(
        MagicMock(), (), status=401, message="Unauthorized"
    )
    mocker.patch.object(
        aiohttp.ClientSession,
        "ws_connect",
        MagicMock(side_effect=err),
        create=True,
    )
    sock = WsUserSocketSession(ws_user_session, backoff_factor=0.001)
    invalidated = []
    mocker.patch.object(
        ws_user_session,
        "_invalidate_access_token",
        invalidated.append,
        create=True,
    )
    with pytest.raises(WsUserUnsupportedError, match="fresh tokens"):
        await asyncio.wait_for(sock.run(), 2)
    assert invalidated  # every rejected token was invalidated
    assert not sock._devices  # nothing else to clean up


async def wait_until(pred, message, budget=5.0):
    """Poll ``pred`` until true, bounded by a wall-clock budget.

    Replaces fixed sleeps: a loop's cycle count depends on scheduler
    progress, so waiting on the condition (generously bounded) is
    deterministic under load, unlike guessing a duration. The polled
    counter has no associated event to await, hence the poll.
    """
    try:
        async with asyncio.timeout(budget):
            while not pred():  # noqa: ASYNC110 -- no event to await
                await asyncio.sleep(0.005)
    except TimeoutError:
        pytest.fail(message)


@pytest.mark.asyncio
async def test_short_cycles_escalate_then_reset(
    ws_user_session, mocker, monkeypatch
):
    """Accept-then-kill cycles escalate the between-cycle pause.

    One long-lived cycle resets the counter (backoff, refresh,
    reconnect). Every cycle here closes immediately after connect;
    shrinking _SHORT_CYCLE_SECONDS mid-run flips them from
    accepted-then-killed to long-lived.
    """
    short_ws = FakeWs([closed_message()])  # closes on the first receive
    short_ws._closed_event.set()  # later receives report CLOSED at once

    def fake_ws_connect(_self, _url, **_kwargs):
        return FakeWsCtx(short_ws)

    mocker.patch.object(
        aiohttp.ClientSession, "ws_connect", fake_ws_connect, create=True
    )
    sock = WsUserSocketSession(ws_user_session, backoff_factor=0.001)
    task = asyncio.create_task(sock.run())
    try:
        await wait_until(
            lambda: sock._short_cycles >= 2,
            "short cycles did not accumulate",
        )
        # Every cycle now counts as long-lived: the counter resets to 0.
        monkeypatch.setattr(ws_user_mod, "_SHORT_CYCLE_SECONDS", -1.0)
        await wait_until(
            lambda: sock._short_cycles == 0,
            "long-lived cycle did not reset the counter",
        )
    finally:
        await sock.cancel()
        await asyncio.wait_for(task, 2)
