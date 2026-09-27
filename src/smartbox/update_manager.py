"""Smartbox socket update manager."""

from collections.abc import Callable, Iterable
import logging
import re
from typing import Any, ClassVar

import jq

from smartbox.session import AsyncSmartboxSession
from smartbox.socket import SocketSession

_LOGGER = logging.getLogger(__name__)

_SIMPLE_JQ_RE = re.compile(r"^\.(\w+)$")


class OptimisedJQMatcher:
    """jq matcher that doesn't bother with jq for simple one-level element queries."""

    _jq_cache: ClassVar[dict[str, Any]] = {}

    def __init__(self, jq_expr: str) -> None:
        """Create an OptimisedJQMatcher for any jq expression."""
        m = _SIMPLE_JQ_RE.match(jq_expr)
        self._fast_path = False
        if m:
            self._fast_path = True
            self._simple_elem = m.group(1)
        else:
            if jq_expr not in self._jq_cache:
                self._jq_cache[jq_expr] = jq.compile(jq_expr)
            self._compiled_jq = self._jq_cache[jq_expr]

    def match(self, input_data: dict[str, Any]) -> Iterable[Any]:
        """Return matches for the given dev data."""
        if self._fast_path:
            return [input_data.get(self._simple_elem)]
        return self._compiled_jq.input(input_data)

    def __repr__(self) -> str:
        """Printable representation."""
        if self._fast_path:
            return str(self)
        return repr(self._compiled_jq)

    def __str__(self) -> str:
        """Str representation."""
        if self._fast_path:
            return f"OptimisedJQMatcher('.{self._simple_elem}', fast_path=True)"
        return str(self._compiled_jq)


class DevDataSubscription:
    """Subscription for dev data callbacks."""

    def __init__(
        self,
        jq_expr: str,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        """Create a dev data subscription for the given jq expression."""
        self._jq_matcher = OptimisedJQMatcher(jq_expr)
        self._callback = callback

    def match(self, input_data: dict[str, Any]) -> None:
        """Return matches for this subscription for the given dev data."""
        _LOGGER.debug("Matching jq %s", self._jq_matcher)
        try:
            # The ValueError guard covers jq evaluation only: exceptions
            # from the user callback must not be misattributed to jq.
            matches = list(self._jq_matcher.match(input_data))
        except ValueError:
            _LOGGER.exception("Error evaluating jq on dev data %s", input_data)
            return
        for match in matches:
            if match is None:
                continue
            try:
                # Isolate user callbacks: one raising callback must not
                # starve the other subscriptions or kill the socket loop.
                self._callback(match)
            except Exception:
                _LOGGER.exception(
                    "Error in dev_data callback for %s",
                    self._jq_matcher,
                )


class UpdateSubscription:
    """Subscription for updates."""

    def __init__(
        self,
        path_regex: str,
        jq_expr: str,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        """Create an update subscription for the given path regex and body jq expression."""
        self._path_regex = re.compile(path_regex)
        self._jq_matcher = OptimisedJQMatcher(jq_expr)
        self._callback = callback

    def match(self, input_data: dict[str, Any]) -> bool:
        """Return matches for this subscription for the given update."""
        path_match = self._path_regex.search(input_data.get("path", ""))
        if not path_match:
            return False
        path_match_kwargs = path_match.groupdict()
        matched = False
        _LOGGER.debug("Matching jq %s", self._jq_matcher)
        try:
            # The ValueError guard covers jq evaluation only: exceptions
            # from the user callback must not be misattributed to jq.
            data_matches = [
                data_match
                for data_match in self._jq_matcher.match(input_data)
                if data_match is not None
            ]
        except ValueError:
            _LOGGER.exception("Error evaluating jq on update %s", input_data)
            return False
        for data_match in data_matches:
            matched = True
            try:
                # Isolate user callbacks: one raising callback must not
                # starve the other subscriptions or kill the socket loop.
                self._callback(data_match, **path_match_kwargs)
            except Exception:
                _LOGGER.exception(
                    "Error in update callback for %s",
                    self._jq_matcher,
                )
        return matched


class UpdateManager:
    """Manages subscription callbacks to receive updates from a Smartbox socket."""

    BODY_PATH = ".body"

    def __init__(
        self,
        session: AsyncSmartboxSession,
        device_id: str,
        **kwargs: Any,  # noqa: ANN401  # pass-through SocketSession knobs
    ) -> None:
        """Create an UpdateManager for a smartbox socket."""
        self._socket_session = SocketSession(
            session,
            device_id,
            self._dev_data_cb,
            self._update_cb,
            **kwargs,
        )
        self._dev_data_subscriptions: list[DevDataSubscription] = []
        self._update_subscriptions: list[UpdateSubscription] = []

    @property
    def socket_session(self) -> SocketSession:
        """Get the underlying socket session."""
        return self._socket_session

    async def run(self) -> None:
        """Run the socket session asynchronously, waiting for updates."""
        await self._socket_session.run()

    async def cancel(self) -> None:
        """Disconnecting and cancelling tasks."""
        await self._socket_session.cancel()

    def subscribe_to_dev_data(self, jq_expr: str, callback: Callable) -> None:
        """Subscribe to receive device data."""
        sub = DevDataSubscription(jq_expr, callback)
        self._dev_data_subscriptions.append(sub)

    def subscribe_to_updates(
        self,
        path_regex: str,
        jq_expr: str,
        callback: Callable[..., None],
    ) -> None:
        """Subscribe to receive device and node data updates.

        Named groups in path_regex are passed as kwargs to callback.
        """
        sub = UpdateSubscription(path_regex, jq_expr, callback)
        self._update_subscriptions.append(sub)

    def subscribe_to_device_away_status(
        self,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        """Subscribe to device away status updates."""
        self.subscribe_to_dev_data(".away_status", callback)
        self.subscribe_to_updates(
            r"^/mgr/away_status",
            self.BODY_PATH,
            callback,
        )

    def subscribe_to_device_connected(
        self,
        callback: Callable[[bool], None],
    ) -> None:
        """Subscribe to device connected updates."""
        self.subscribe_to_dev_data(
            ".connected",
            lambda p: callback(bool(p)),
        )
        self.subscribe_to_updates(
            r"^/connected",
            f"{self.BODY_PATH}.connected",
            lambda p: callback(bool(p)),
        )

    def subscribe_to_device_power_limit(
        self,
        callback: Callable[[int], None],
    ) -> None:
        """Subscribe to device power limit updates."""
        self.subscribe_to_dev_data(
            ".htr_system.setup.power_limit",
            lambda p: callback(int(p)),
        )
        self.subscribe_to_updates(
            r"^/htr_system/(setup|power_limit)",
            f"{self.BODY_PATH}.power_limit",
            lambda p: callback(int(p)),
        )

    def subscribe_to_node_status(
        self,
        callback: Callable[[str, int, dict[str, Any]], None],
    ) -> None:
        """Subscribe to node status updates."""

        def dev_data_wrapper(data: dict[str, Any]) -> None:
            status = data.get("status")
            if status is None:
                # jq's {addr, type, status} emits status: null for nodes
                # that don't carry the key — nothing to report.
                _LOGGER.debug("Status absent in dev data node, ignoring")
                return
            callback(data["type"], int(data["addr"]), status)

        self.subscribe_to_dev_data(
            "(.nodes[] | {addr, type, status})?",
            dev_data_wrapper,
        )

        def update_wrapper(
            data: dict[str, Any],
            node_type: str,
            addr: str,
        ) -> None:
            callback(node_type, int(addr), data)

        self.subscribe_to_updates(
            r"^/(?P<node_type>[^/]+)/(?P<addr>\d+)/status$",
            self.BODY_PATH,
            update_wrapper,
        )

    def subscribe_to_node_setup(
        self,
        callback: Callable[[str, int, dict[str, Any]], None],
    ) -> None:
        """Subscribe to node setup updates."""

        def dev_data_wrapper(data: dict[str, Any]) -> None:
            setup = data.get("setup")
            if setup is None:
                # jq's {addr, type, setup} emits setup: null for nodes
                # that don't carry the key — nothing to report.
                _LOGGER.debug("Setup absent in dev data node, ignoring")
                return
            callback(data["type"], int(data["addr"]), setup)

        self.subscribe_to_dev_data(
            "(.nodes[] | {addr, type, setup})?",
            dev_data_wrapper,
        )

        def update_wrapper(
            data: dict[str, Any],
            node_type: str,
            addr: str,
        ) -> None:
            callback(node_type, int(addr), data)

        self.subscribe_to_updates(
            r"^/(?P<node_type>[^/]+)/(?P<addr>\d+)/setup$",
            self.BODY_PATH,
            update_wrapper,
        )

    def subscribe_to_node_version(
        self,
        callback: Callable[[str, int, dict[str, Any]], None],
    ) -> None:
        """Subscribe to node version updates."""

        def dev_data_wrapper(data: dict[str, Any]) -> None:
            version = data.get("version")
            if version is None:
                # jq's {addr, type, version} emits version: null for nodes
                # that don't carry the key — nothing to report.
                _LOGGER.debug("Version absent in dev data node, ignoring")
                return
            callback(data["type"], int(data["addr"]), version)

        self.subscribe_to_dev_data(
            "(.nodes[] | {addr, type, version})?",
            dev_data_wrapper,
        )

        def update_wrapper(
            data: dict[str, Any],
            node_type: str,
            addr: str,
        ) -> None:
            callback(node_type, int(addr), data)

        self.subscribe_to_updates(
            r"^/(?P<node_type>[^/]+)/(?P<addr>\d+)/version$",
            self.BODY_PATH,
            update_wrapper,
        )

    def subscribe_to_node_prog(
        self,
        callback: Callable[[str, int, dict[str, Any]], None],
    ) -> None:
        """Subscribe to node prog (schedule) updates."""

        def dev_data_wrapper(data: dict[str, Any]) -> None:
            prog = data.get("prog")
            if prog is None:
                # jq's {addr, type, prog} emits prog: null for nodes
                # that don't carry the key — nothing to report.
                _LOGGER.debug("Prog absent in dev data node, ignoring")
                return
            callback(data["type"], int(data["addr"]), prog)

        self.subscribe_to_dev_data(
            "(.nodes[] | {addr, type, prog})?",
            dev_data_wrapper,
        )

        def update_wrapper(
            data: dict[str, Any],
            node_type: str,
            addr: str,
        ) -> None:
            callback(node_type, int(addr), data)

        self.subscribe_to_updates(
            r"^/(?P<node_type>[^/]+)/(?P<addr>\d+)/prog$",
            self.BODY_PATH,
            update_wrapper,
        )

    def _dev_data_cb(self, data: dict[str, Any]) -> None:
        for sub in self._dev_data_subscriptions:
            sub.match(data)

    def _update_cb(self, data: dict[str, Any]) -> None:
        if "path" not in data:
            # Checked once, not per subscription: with the check inside
            # the loop a malformed update was logged once per subscription.
            _LOGGER.error("Path not found in update data: %s", data)
            return
        matched = False
        for sub in self._update_subscriptions:
            if sub.match(data):
                matched = True
        if not matched:
            _LOGGER.debug("No matches for update %s", data)
