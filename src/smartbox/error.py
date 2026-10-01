"""Smartbox specific Errors."""

import aiohttp


class SmartboxError(Exception):
    """Base class for every error raised by this library."""


class SmartboxValidationError(SmartboxError):
    """A response payload did not match its model (``raw_response=False``).

    Chained from the underlying ``pydantic.ValidationError``. Raw-mode
    (``raw_response=True``) calls never raise this: they return the wire
    payload unvalidated.

    The offending wire payload is attached as ``payload`` so a caller can
    inspect the actual shape (e.g. distinguish the documented bare
    ``{"sync_status": "lost"}`` dead-node status from other drift).
    """

    def __init__(self, msg: str, payload: object = None) -> None:
        """Attach the offending wire payload for programmatic inspection."""
        super().__init__(msg)
        self.payload = payload


class InvalidAuthError(SmartboxError):
    """Authentication failed (bad credentials or rejected/expired token)."""


class APIUnavailableError(SmartboxError, aiohttp.ClientConnectionError):
    """API is unavailable.

    Also inherits from ``aiohttp.ClientConnectionError`` for backwards
    compatibility with consumers that catch that type.
    """


class ResellerNotExistError(SmartboxError):
    """Reseller is not known."""
