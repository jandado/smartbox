"""Tests for the reseller table and convenience accessors."""

from __future__ import annotations

import pytest

from smartbox.protocol.errors import ResellerNotExistError
from smartbox.protocol.reseller import API_HOST, AvailableResellers, api_base_url


def test_api_base_url_uses_the_current_host():
    assert API_HOST == "users-api.helki.com"
    assert api_base_url() == f"https://{API_HOST}"


def test_known_reseller():
    reseller = AvailableResellers("api")
    assert reseller.reseller.name == "Helki"
    assert reseller.api_url == "api"
    assert reseller.name == "Helki"
    assert reseller.web_url == "https://app.helki.com/"


def test_unknown_reseller_needs_all_fields():
    with pytest.raises(ResellerNotExistError):
        _ = AvailableResellers("api-unknown").reseller


def test_unknown_reseller_can_be_created():
    reseller = AvailableResellers(
        "api-unknown",
        basic_auth="secret",
        web_url="https://example.test/",
        serial_id=99,
        name="Example",
    ).reseller
    assert reseller.api_url == "api-unknown"
    assert reseller.name == "Example"
    assert reseller.web_url == "https://example.test/"
    assert reseller.serial_id == 99
