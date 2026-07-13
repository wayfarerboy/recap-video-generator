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


class TestBeatsCommand:
    """Integration tests for `recap beats`."""

    def test_beats_in_help(self):
        """`recap --help` lists the beats subcommand."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "beats" in result.output

    def test_beats_missing_file(self):
        """`recap beats nonexistent.mp3` exits with error."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["beats", "/nonexistent/audio.mp3"])
        assert result.exit_code != 0

    def test_beats_produces_valid_json(self, tmp_path):
        """`recap beats` on a valid audio file outputs JSON with correct keys."""
        import json

        import numpy as np
        from scipy.io import wavfile

        # Generate a simple click track
        sr = 22050
        duration = 2.0
        n_samples = int(duration * sr)
        y = np.zeros(n_samples, dtype=np.float32)
        click_samples = int(0.01 * sr)
        click_env = np.sin(np.linspace(0, np.pi, click_samples)).astype(np.float32)
        for beat in range(4):
            pos = beat * int(0.5 * sr)
            if pos + click_samples < n_samples:
                y[pos : pos + click_samples] = 0.8 * click_env

        wav_path = tmp_path / "test.wav"
        y_int16 = (y * 32767).astype(np.int16)
        wavfile.write(str(wav_path), sr, y_int16)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["beats", str(wav_path)])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert "bpm" in data
        assert "beats" in data
        assert "energy" in data
        assert isinstance(data["beats"], list)
        assert isinstance(data["energy"], list)
