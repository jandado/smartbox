"""Core layer - domain state.

The versioned state store, pending-write confirmation, availability-window
logic and the reconnection policy, as HA-agnostic asyncio driven by typed
protocol events (``smartbox.protocol.Event``), exposing typed change
notifications. Zero ``homeassistant`` imports (enforced by the library
tests); it must stay testable against the fake transport without the HA
test harness.
"""

from .engine import SmartboxEngine
from .state import (
    Change,
    DeviceChange,
    DeviceRemoved,
    DeviceState,
    FeedChange,
    NodeAdded,
    NodeChange,
    NodeRemoved,
    NodeState,
    WorldState,
)

__all__ = [
    "Change",
    "DeviceChange",
    "DeviceRemoved",
    "DeviceState",
    "FeedChange",
    "NodeAdded",
    "NodeChange",
    "NodeRemoved",
    "NodeState",
    "SmartboxEngine",
    "WorldState",
]
