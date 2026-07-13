"""Tests for recap CLI."""

import subprocess
from unittest import mock

import click.testing
import pytest

from recap.cli import main


def test_help_prints_usage():
    """`recap --help` prints usage with subcommands listed."""
    runner = click.testing.CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.output


def test_check_both_found():
    """`recap check` exits 0 when both ffmpeg and kdenlive found."""
    def fake_run(cmd, **kwargs):
        proc = mock.MagicMock()
        proc.returncode = 0
        if "ffmpeg" in cmd:
            proc.stdout = "ffmpeg version 4.4"
        else:
            proc.stdout = "kdenlive 22.04"
        return proc

    with mock.patch("subprocess.run", side_effect=fake_run):
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["check"])
        assert result.exit_code == 0
        assert "ffmpeg" in result.output
        assert "kdenlive" in result.output


def test_check_ffmpeg_missing():
    """`recap check` exits 1 when ffmpeg not found."""
    def fake_run(cmd, **kwargs):
        proc = mock.MagicMock()
        if "ffmpeg" in cmd:
            raise FileNotFoundError("ffmpeg not found")
        proc.returncode = 0
        proc.stdout = "kdenlive 22.04"
        return proc

    with mock.patch("subprocess.run", side_effect=fake_run):
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["check"])
        assert result.exit_code == 1
        assert "ffmpeg" in result.output.lower()


def test_check_kdenlive_missing():
    """`recap check` exits 1 when kdenlive not found."""
    def fake_run(cmd, **kwargs):
        proc = mock.MagicMock()
        if "kdenlive" in cmd:
            raise FileNotFoundError("kdenlive not found")
        proc.returncode = 0
        proc.stdout = "ffmpeg version 4.4"
        return proc

    with mock.patch("subprocess.run", side_effect=fake_run):
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["check"])
        assert result.exit_code == 1
        assert "kdenlive" in result.output.lower()
