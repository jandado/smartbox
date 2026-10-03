"""Socket integration for smartbox."""

import asyncio
from collections.abc import Callable
import contextlib
import logging
import signal
from typing import Any
import urllib.parse

import socketio

from smartbox.retry import (
    backoff_delay,
    refresh_auth_with_retry,
    sleep_unless_exiting,
)
from smartbox.session import AsyncSmartboxSession, _redacted_url

_API_V2_NAMESPACE = "/api/v2/socket_io"
# We most commonly get disconnected when the session
# expires, so we don't want to try many times
_DEFAULT_RECONNECT_ATTEMPTS = 10
_DEFAULT_BACKOFF_FACTOR = 1.0
# Upper bound on a single disconnect attempt during teardown: on a wedged
# or half-open websocket the engineio disconnect can block indefinitely.
_DISCONNECT_TIMEOUT = 5.0
# Cap on a single reconnect backoff sleep: without it, 10 attempts at
# backoff_factor 1.0 stall the loop up to ~8.5 min before falling through
# to the token refresh that usually fixes the disconnect.
_MAX_RECONNECT_SLEEP = 30.0

_LOGGER = logging.getLogger(__name__)


class SmartboxAPIV2Namespace(socketio.AsyncClientNamespace):
    """Smartbox Namespace for socket.io."""

    def __init__(
        self,
        session: AsyncSmartboxSession,
        namespace: str,
        dev_data_callback: Callable | None = None,
        node_update_callback: Callable | None = None,
    ) -> None:
        """Init of a async namespace."""
        super().__init__(namespace)
        self._session = session
        self._namespace = namespace
        self._dev_data_callback = dev_data_callback
        self._node_update_callback = node_update_callback
        self._namespace_connected = False
        self._received_message = False
        self._received_dev_data = False

    async def on_connect(self) -> None:
        """Namespace connected."""
        _LOGGER.debug("Namespace %s connected", self._namespace)
        self._namespace_connected = True

    async def on_disconnect(self, reason: str) -> None:
        """Disconnection of namespace."""
        _LOGGER.debug(
            "Namespace %s disconnected, disconnecting socket. Reason: %s",
            self._namespace,
            reason,
        )
        self._namespace_connected = False
        self._received_message = False
        self._received_dev_data = False

    @property
    def connected(self) -> bool:
        """Are we connected."""
        return self._namespace_connected

    async def on_dev_data(self, data: dict[str, Any]) -> None:
        """Received dev data."""
        _LOGGER.debug("Received dev_data: %s", data)
        self._received_message = True
        self._received_dev_data = True
        if self._dev_data_callback is not None:
            self._dev_data_callback(data)

    async def on_update(self, data: dict[str, Any]) -> None:
        """Received update."""
        _LOGGER.debug("Received update: %s", data)
        if not self._received_message:
            # The connection is only usable once we've received a message from
            # the server (not on the connect event!!!), so we wait to receive
            # something before sending our first message
            await self.emit("dev_data", namespace=self._namespace)
            self._received_message = True
        if not self._received_dev_data:
            _LOGGER.debug("Dev data not received yet, ignoring update")
            return
        if self._node_update_callback is not None:
            self._node_update_callback(data)


class SocketSession:
    """Smartbox SocketSession class."""

    def __init__(
        self,
        session: AsyncSmartboxSession,
        device_id: str,
        dev_data_callback: Callable | None = None,
        node_update_callback: Callable | None = None,
        verbose: bool = False,
        add_sigint_handler: bool = False,
        ping_interval: int = 20,
        reconnect_attempts: int = _DEFAULT_RECONNECT_ATTEMPTS,
        backoff_factor: float = _DEFAULT_BACKOFF_FACTOR,
    ) -> None:
        """Init socket session to smartbox."""
        if ping_interval <= 0:
            msg = f"ping_interval must be >= 1 second (got {ping_interval})"
            raise ValueError(msg)
        if reconnect_attempts < 1:
            msg = f"reconnect_attempts must be >= 1 (got {reconnect_attempts})"
            raise ValueError(msg)
        self._session = session
        self._device_id = device_id
        self._ping_interval = ping_interval
        self._reconnect_attempts = reconnect_attempts
        self._backoff_factor = backoff_factor
        self._background_tasks: set[asyncio.Task] = set()
        self._disconnect_done = False
        self._loop_should_exit = False
        # Set together with _loop_should_exit; wakes backoff sleeps so
        # cancel() takes effect immediately instead of after the sleep.
        self._exit_event = asyncio.Event()
        # Loop on which this session installed a SIGINT handler, if any
        # (so shutdown can drop it again; the handler must not outlive
        # the session it was installed for).
        self._sigint_loop: asyncio.AbstractEventLoop | None = None

        # Without ``verbose`` the socketio/engineio loggers stay under the
        # host application's logging configuration.
        # The REST session's websession is bound to the socketio client in
        # ``run()`` (``_bind_http_session``), not here: materialising it
        # eagerly would make construction outside a running event loop
        # fail ("no running event loop" — aiohttp.ClientSession needs one).
        self._sio = socketio.AsyncClient(
            logger=verbose,
            engineio_logger=verbose,
            http_session=None,
            reconnection=False,
        )

        self._api_v2_ns = SmartboxAPIV2Namespace(
            session,
            _API_V2_NAMESPACE,
            dev_data_callback,
            node_update_callback,
        )
        self._sio.register_namespace(self._api_v2_ns)

        @self._sio.event
        async def connect() -> None:
            _LOGGER.debug("Received connect socket event")
            if add_sigint_handler:
                # engineio sets a signal handler on connect, which means we
                # have to set our own in the connect callback if we want to
                # override it
                _LOGGER.debug("Adding signal handler")
                event_loop = asyncio.get_running_loop()

                def sigint_handler() -> None:
                    _LOGGER.debug("Caught SIGINT, cancelling loop")
                    task = asyncio.ensure_future(self.cancel())
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)

                try:
                    event_loop.add_signal_handler(signal.SIGINT, sigint_handler)
                    self._sigint_loop = event_loop
                except NotImplementedError:
                    # No signal-handler support (e.g. some Windows event
                    # loops): keep the connection alive without it.
                    _LOGGER.debug("SIGINT handler not supported; skipping")

    def _bind_http_session(self) -> None:
        """Bind the REST session's websession to the socketio client.

        Deferred from ``__init__`` so a SocketSession can be built outside
        a running event loop (aiohttp.ClientSession needs one); called at
        the start of ``run()``. ``external_http=True`` marks the session
        as caller-owned so engineio's disconnect never closes it — it is
        shared with the REST traffic.
        """
        eio = self._sio.eio
        eio.http = self._session.client
        eio.external_http = True

    async def _dev_data(self) -> None:
        """Send first dev data."""
        if not self._api_v2_ns.connected:
            _LOGGER.debug("Namespace disconnected, not sending dev_data")
            return
        _LOGGER.debug("Sending dev_data event")
        await self._sio.emit("dev_data", namespace=_API_V2_NAMESPACE)

    async def _send_ping(self) -> None:
        """Send keepalive pings for the lifetime of ``run()``.

        The task spans every reconnect cycle, so a failed send (e.g. the
        namespace dropping between the ``connected`` check and the send)
        is logged and skipped — it must not end the keepalive for good.
        """
        _LOGGER.debug("Starting ping task every %ss", self._ping_interval)
        while True:
            await self._sio.sleep(self._ping_interval)
            if not self._api_v2_ns.connected:
                _LOGGER.debug("Namespace disconnected, not sending ping")
                continue
            _LOGGER.debug("Sending ping")
            try:
                await self._sio.send("ping", namespace=_API_V2_NAMESPACE)
            except (socketio.exceptions.SocketIOError, OSError) as e:
                _LOGGER.debug("Ping not sent: %s", e)

    async def _attempt_connection(self, url: str) -> bool:
        """Attempt to connect to the websocket URL.

        Returns True if connection was successful, False otherwise.
        """
        _LOGGER.debug("Connecting to %s", _redacted_url(url))
        try:
            connect_task = asyncio.create_task(
                self._sio.connect(url, transports=["websocket"])
            )
            try:
                await asyncio.shield(connect_task)
            except asyncio.CancelledError:
                _LOGGER.debug(
                    "HA stops during connection, scheduled cleaning..."
                )
                if not connect_task.done():

                    async def _cleanup_dangling_socket(
                        task_to_cleanup: asyncio.Task = connect_task,
                    ) -> None:
                        try:
                            await task_to_cleanup
                            await self._sio.disconnect()
                        except (
                            AttributeError,
                            RuntimeError,
                            OSError,
                        ) as e:
                            _LOGGER.debug(
                                "Error occurred while _cleanup_dangling_socket: %s",
                                e,
                            )

                    task = asyncio.create_task(_cleanup_dangling_socket())
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)

                raise
            _LOGGER.info("Successfully connected to %s", _redacted_url(url))
            if self._loop_should_exit:
                # cancel() ran while we were connecting: its one-shot
                # disconnect already happened, so drop this fresh
                # connection here instead of parking on it forever.
                _LOGGER.debug("Exit requested during connect; disconnecting")
                await self._disconnect_now()
                return True
            await self._dev_data()
            await self._sio.wait()
            await self._cleanup_websocket()
            with contextlib.suppress(Exception):
                await self._sio.disconnect()
        except (
            socketio.exceptions.SocketIOError,
            TimeoutError,
            OSError,
        ) as e:
            # SocketIOError (not just its ConnectionError subclass): the
            # first dev_data emit races the server drop (connected check
            # then send), which surfaces as a BadNamespaceError — a
            # *sibling* of ConnectionError. Swallowing only ConnectionError
            # let it escape and end the run loop for good; all SocketIOError
            # variants go through the outer backoff/reconnect cycle instead.
            # TimeoutError/OSError can escape from the underlying websocket
            # transport on transient DNS/socket hiccups.
            _LOGGER.debug("Connection attempt failed: %s", e)
            return False
        return True

    async def _cleanup_websocket(self) -> None:
        """Clean up orphaned WebSocket connections."""
        _LOGGER.debug("Exiting wait(), forcing socketio cleanup...")
        try:
            if (
                hasattr(self._sio, "eio")
                and hasattr(self._sio.eio, "ws")
                and self._sio.eio.ws
            ) and not self._sio.eio.ws.closed:
                _LOGGER.debug("Manually closing the orphaned WebSocket")
                await self._sio.eio.ws.close()
        except (AttributeError, RuntimeError, OSError) as e:
            _LOGGER.debug(
                "Error occurred while manually closing the WebSocket: %s",
                e,
            )

    async def _disconnect_once(self) -> None:
        """Disconnect exactly once, bounded by a timeout.

        Concurrent/serialised disconnect attempts on the same AsyncClient
        can trip internal state races, and a wedged websocket can make the
        engineio disconnect block indefinitely — both callers (cancel and
        shutdown) funnel through here.
        """
        if self._disconnect_done:
            return
        self._disconnect_done = True
        await self._disconnect_now()

    async def _disconnect_now(self) -> None:
        """Disconnect the client, bounded by ``_DISCONNECT_TIMEOUT``."""
        inner = asyncio.ensure_future(self._sio.disconnect())
        try:
            await asyncio.wait_for(
                asyncio.shield(inner),
                timeout=_DISCONNECT_TIMEOUT,
            )
        except TimeoutError:
            _LOGGER.warning(
                "Timed out after %ss waiting for websocket disconnect",
                _DISCONNECT_TIMEOUT,
            )
            # Stop the wedged disconnect task and reap it (bounded) so it
            # cannot linger as "Task was destroyed but it is pending".
            inner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({inner}, timeout=_DISCONNECT_TIMEOUT)
        except asyncio.CancelledError:
            _LOGGER.debug("Disconnect interrupted; stopping disconnect task")
            inner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({inner}, timeout=_DISCONNECT_TIMEOUT)
            raise
        except OSError as e:
            _LOGGER.debug("Silent error on disconnect: %s", e)

    def _remove_sigint_handler(self) -> None:
        """Drop the SIGINT handler this session installed (if any)."""
        loop, self._sigint_loop = self._sigint_loop, None
        if loop is None:
            return
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except RuntimeError, ValueError:
            _LOGGER.debug("Could not remove SIGINT handler", exc_info=True)

    async def run(self) -> None:
        """Run the websocket."""
        self._bind_http_session()
        self._ping_task = self._sio.start_background_task(self._send_ping)

        _LOGGER.debug("Starting main loop")
        try:
            # Authenticate before the first connect attempt: without this,
            # a cold session (no prior REST call, e.g. the CLI ``socket``
            # command) burns every reconnect attempt on an empty/stale
            # token before the fall-through refresh below fixes it on the
            # next cycle.
            await self._refresh_auth()
            while not self._loop_should_exit:
                encoded_token = urllib.parse.quote(
                    self._session.access_token,
                    safe="~()*!.'",
                )
                url = f"{self._session.api_host}/?token={encoded_token}&dev_id={self._device_id}"
                redacted_url = _redacted_url(url)

                # Try to connect
                _LOGGER.debug(
                    "Connecting to %s (will try %s times)",
                    redacted_url,
                    self._reconnect_attempts,
                )
                for attempt in range(self._reconnect_attempts):
                    if self._loop_should_exit:
                        break
                    _LOGGER.debug(
                        "Connecting to %s (attempt #%s)", redacted_url, attempt
                    )

                    if await self._attempt_connection(url):
                        _LOGGER.debug("Breaking loop to refresh token")
                        break

                    remaining = self._reconnect_attempts - attempt - 1
                    sleep_time = backoff_delay(
                        attempt, self._backoff_factor, _MAX_RECONNECT_SLEEP
                    )
                    _LOGGER.warning(
                        "Received error on connection attempt, %s retries remaining, sleeping %ss",
                        remaining,
                        sleep_time,
                    )
                    if remaining > 0:
                        await self._sleep_unless_exiting(sleep_time)
                    else:
                        _LOGGER.warning(
                            "Failed to connect after %s attempts, falling through to refresh token",
                            self._reconnect_attempts,
                        )
                if self._loop_should_exit:
                    # Exit before touching the REST API so cancel is not
                    # delayed by a slow/unreachable auth refresh.
                    break
                await self._refresh_auth()
                # Small pause between connection cycles: the per-attempt
                # backoff above only covers failed connects, so a server
                # that accepts-then-drops would otherwise spin us in a hot
                # reconnect loop.
                await self._sleep_unless_exiting(self._backoff_factor)
        except asyncio.CancelledError:
            _LOGGER.debug("WebSocket loop cancelled by Home Assistant")
            raise
        finally:
            _LOGGER.debug("Cleaning up socketio...")
            await self.shutdown()

    async def _refresh_auth(self) -> None:
        """Refresh the REST token the socket URL carries.

        Transient failures (API unreachable, 5xx, malformed token
        response) are waited out with capped backoff: a network blip at
        refresh time must not end the websocket loop for good. Rejected
        credentials (``InvalidAuthError``, raised only after the session's
        password-login fallback also failed) propagate out of ``run()``.
        Returns early once an exit is requested.
        """
        await refresh_auth_with_retry(
            self._session.check_refresh_auth,
            exit_requested=lambda: self._loop_should_exit,
            sleep_unless_exiting=self._sleep_unless_exiting,
            backoff_factor=self._backoff_factor,
            max_sleep=_MAX_RECONNECT_SLEEP,
            log_context=f"device {self._device_id}",
        )

    async def _sleep_unless_exiting(self, delay: float) -> None:
        """Sleep up to ``delay`` seconds, waking early once exit is requested."""
        await sleep_unless_exiting(self._exit_event, delay)

    def _request_exit(self) -> None:
        """Flag the run loop to stop and wake any backoff sleep."""
        self._loop_should_exit = True
        self._exit_event.set()

    async def _stop_ping(self) -> None:
        """Cancel and reap the ping task (bounded) so it cannot linger."""
        if not hasattr(self, "_ping_task"):
            return
        task = self._ping_task
        if not isinstance(task, asyncio.Task) or task.done():
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=_DISCONNECT_TIMEOUT)
        except (asyncio.CancelledError, TimeoutError, OSError) as e:
            _LOGGER.debug("Ping task stopped: %s", e)

    async def cancel(self) -> None:
        """Disconnecting and cancelling tasks."""
        _LOGGER.debug("Disconnecting and cancelling tasks")
        self._request_exit()
        await self._stop_ping()
        await self._disconnect_once()

    async def shutdown(self) -> None:
        """Shutdown the socket session."""
        self._request_exit()
        self._remove_sigint_handler()
        await self._stop_ping()
        await self._disconnect_once()
        if self._background_tasks:
            pending = [
                task for task in self._background_tasks if not task.done()
            ]
            if pending:
                # e.g. _cleanup_dangling_socket tasks: await (bounded) so
                # they don't die as "Task was destroyed but it is pending"
                # noise at loop teardown.
                _, still_pending = await asyncio.wait(
                    pending, timeout=_DISCONNECT_TIMEOUT
                )
                for task in still_pending:
                    task.cancel()
                if still_pending:
                    # Reap the cancelled ones (bounded) so they cannot
                    # linger as "Task was destroyed but it is pending".
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.wait(
                            still_pending, timeout=_DISCONNECT_TIMEOUT
                        )

    @property
    def namespace(self) -> SmartboxAPIV2Namespace:
        """Namespace property."""
        return self._api_v2_ns
