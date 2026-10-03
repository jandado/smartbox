"""Expose submodules."""

import importlib.metadata

from .error import (
    APIUnavailableError,
    InvalidAuthError,
    ResellerNotExistError,
    SmartboxError,
    SmartboxValidationError,
    WsUserUnsupportedError,
)
from .models import (
    AcmNodeStatus,
    DefaultNodeStatus,
    Device,
    DeviceAwayStatus,
    DeviceConnected,
    Devices,
    Guests,
    GuestUser,
    Home,
    Homes,
    HtrModNodeStatus,
    HtrNodeStatus,
    Node,
    NodeExtraOptions,
    NodeFactoryOptions,
    NodeProg,
    Nodes,
    NodeSetup,
    NodeStatus,
    NodeVersion,
    PmoSample,
    Sample,
    Samples,
    SmartboxNodeType,
    Token,
)
from .reseller import AvailableResellers, SmartboxReseller
from .session import AsyncSmartboxSession, Session
from .socket import SocketSession
from .update_manager import UpdateManager
from .ws_user import WsUserSocketSession, check_ws_user_support

try:
    __version__ = importlib.metadata.version("smartbox")
except importlib.metadata.PackageNotFoundError:  # pragma: no cover
    # E.g. vendored/undist import without a dist-info.
    __version__ = "0.0.0"


__all__ = [
    "APIUnavailableError",
    "AcmNodeStatus",
    "AsyncSmartboxSession",
    "AvailableResellers",
    "DefaultNodeStatus",
    "Device",
    "DeviceAwayStatus",
    "DeviceConnected",
    "Devices",
    "GuestUser",
    "Guests",
    "Home",
    "Homes",
    "HtrModNodeStatus",
    "HtrNodeStatus",
    "InvalidAuthError",
    "Node",
    "NodeExtraOptions",
    "NodeFactoryOptions",
    "NodeProg",
    "NodeSetup",
    "NodeStatus",
    "NodeVersion",
    "Nodes",
    "PmoSample",
    "ResellerNotExistError",
    "Sample",
    "Samples",
    "Session",
    "SmartboxError",
    "SmartboxNodeType",
    "SmartboxReseller",
    "SmartboxValidationError",
    "SocketSession",
    "Token",
    "UpdateManager",
    "WsUserSocketSession",
    "WsUserUnsupportedError",
    "check_ws_user_support",
]
