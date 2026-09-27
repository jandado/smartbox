"""Smartbox specific Errors."""

import aiohttp


class SmartboxError(Exception):
    """General errors from smartbox API."""


class SmartboxValidationError(SmartboxError):
    """A response payload did not match its model (``raw_response=False``).

    Chained from the underlying ``pydantic.ValidationError``. Raw-mode
    (``raw_response=True``) calls never raise this: they return the wire
    payload unvalidated.
    """


class InvalidAuthError(Exception):
    """Authentication failed."""


class APIUnavailableError(aiohttp.ClientConnectionError):
    """API is unavailable."""


class ResellerNotExistError(Exception):
    """Reseller is not known."""
