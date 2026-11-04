"""Smoke tests for the optional CLI surface."""

from __future__ import annotations

from asyncclick.testing import CliRunner

from smartbox.cli import smartbox


async def test_cli_help_lists_commands():
    result = await CliRunner().invoke(smartbox, ["--help"])
    assert result.exit_code == 0
    assert "listen" in result.output
    assert "set-boost-mode" in result.output


async def test_cli_resellers_does_not_need_credentials():
    result = await CliRunner().invoke(smartbox, ["resellers"])
    assert result.exit_code == 0
    assert "Helki" in result.output
    assert "Haverland" in result.output
