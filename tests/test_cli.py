"""Tests for recap CLI."""

import subprocess
from pathlib import Path
from unittest import mock

import click.testing
import pytest

from recap.cli import main
from recap.plan import Plan, Assignment


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


# ---------------------------------------------------------------------------
# Synthetic data fixtures for create pipeline tests
# ---------------------------------------------------------------------------


def _make_assignment_plan(bpm=120.0, clips_dir="/fake"):
    """Return a synthetic assignment Plan with 3 clips."""
    return Plan(
        bpm=bpm,
        assignments=[
            Assignment(
                clip=f"{clips_dir}/clip_0.mp4",
                source_start=0.75,
                source_end=3.25,
                target_start=0.0,
                beat_index=0,
                beat_count=5,
                beat_energy=0.55,
                motion_score=0.9,
            ),
            Assignment(
                clip=f"{clips_dir}/clip_1.mp4",
                source_start=0.75,
                source_end=3.25,
                target_start=2.5,
                beat_index=5,
                beat_count=5,
                beat_energy=0.55,
                motion_score=0.7,
            ),
            Assignment(
                clip=f"{clips_dir}/clip_2.mp4",
                source_start=0.75,
                source_end=3.25,
                target_start=5.0,
                beat_index=10,
                beat_count=5,
                beat_energy=0.55,
                motion_score=0.5,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# End-to-end pipeline tests
# ---------------------------------------------------------------------------


class TestCreatePipeline:
    """Integration tests for `recap create` — the full end-to-end pipeline."""

    def test_help_lists_create(self):
        """`recap --help` lists the create subcommand."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "create" in result.output

    def test_create_help(self):
        """`recap create --help` shows usage with all options."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["create", "--help"])
        assert result.exit_code == 0
        assert "CLIPS_DIR" in result.output
        assert "MUSIC_FILE" in result.output
        assert "--mode" in result.output
        assert "--ratio" in result.output
        assert "--force" in result.output

    def test_missing_args_exits_with_error(self):
        """`recap create` with no args exits with error."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["create"])
        assert result.exit_code != 0

    def test_pipeline_run_called_with_config(self, tmp_path, monkeypatch):
        """create unpacks flags into PipelineConfig and calls pipeline.run."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")
        output_path = tmp_path / "recap.kdenlive"

        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        run_calls = []

        def fake_run(config, stop_after=None):
            run_calls.append((config, stop_after))
            from recap.pipeline import PipelineResult
            return PipelineResult(
                plan=assign_plan,
                kdenlive_path=config.output_path,
                warnings=[],
            )

        monkeypatch.setattr("recap.pipeline.run", fake_run)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "create",
            str(clips_dir),
            str(music_file),
            "-o", str(output_path),
            "--mode", "best-match",
            "--ratio", "9:16",
            "--force",
            "--no-transcode",
        ])

        assert result.exit_code == 0, f"CLI failed: {result.output}\nstderr: {result.exc_info}"

        # Verify pipeline.run was called once
        assert len(run_calls) == 1
        config, stop_after = run_calls[0]
        assert stop_after is None  # create runs full pipeline

        # Verify PipelineConfig was built correctly from CLI flags
        from pathlib import Path
        assert config.clips_dir == clips_dir
        assert config.music_path == music_file
        assert config.mode == "best-match"
        assert config.ratio == "9:16"
        assert config.seed == 42  # default
        assert config.force is True
        assert config.fps == 25.0  # default
        assert config.transcode is False  # --no-transcode
        assert config.output_path == output_path
        assert config.min_beats == 4  # default
        assert config.max_beats == 8  # default

        # Verify success output
        assert "Done!" in result.output
        assert "Wrote" in result.output

    def test_pipeline_defaults_with_transcode(self, tmp_path, monkeypatch):
        """create default (transcode on) passes transcode=True to PipelineConfig."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")

        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        run_calls = []

        def fake_run(config, stop_after=None):
            run_calls.append((config, stop_after))
            from recap.pipeline import PipelineResult
            return PipelineResult(
                plan=assign_plan,
                kdenlive_path=config.output_path,
                warnings=[],
            )

        monkeypatch.setattr("recap.pipeline.run", fake_run)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "create",
            str(clips_dir),
            str(music_file),
            "--mode", "best-match",
        ])

        assert result.exit_code == 0, f"CLI failed: {result.output}\nstderr: {result.exc_info}"

        assert len(run_calls) == 1
        config, stop_after = run_calls[0]
        assert config.transcode is True  # default, no --no-transcode
        assert config.mode == "best-match"
        assert stop_after is None

        assert "Done!" in result.output

    def test_pipeline_defaults_no_transcode(self, tmp_path, monkeypatch):
        """End-to-end --no-transcode: uses correct defaults (shuffled-tiers, 16:9)."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")

        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        run_calls = []

        def fake_run(config, stop_after=None):
            run_calls.append(config)
            from recap.pipeline import PipelineResult
            return PipelineResult(plan=assign_plan, warnings=[])

        monkeypatch.setattr("recap.pipeline.run", fake_run)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "create", str(clips_dir), str(music_file), "--no-transcode",
        ])

        assert result.exit_code == 0
        assert len(run_calls) == 1
        config = run_calls[0]
        assert config.transcode is False
        assert config.mode == "shuffled-tiers"
        assert config.ratio == "16:9"

    def test_no_clips_aborts_with_error(self, tmp_path, monkeypatch):
        """When no clips are found, exits non-zero."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")

        def fake_run(config, stop_after=None):
            raise ValueError("No clips found")

        monkeypatch.setattr("recap.pipeline.run", fake_run)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["create", str(clips_dir), str(music_file)])
        assert result.exit_code == 1
        assert "No clips found" in result.output

    def test_trim_removed_from_pipeline(self, tmp_path, monkeypatch):
        """create no longer calls trim_plan (timeline trimming used instead)."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")

        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))
        trim_called = []

        def fake_run(config, stop_after=None):
            from recap.pipeline import PipelineResult
            return PipelineResult(plan=assign_plan, warnings=[])

        def fake_trim(*a, **kw):
            trim_called.append(True)
            return assign_plan

        monkeypatch.setattr("recap.pipeline.run", fake_run)
        monkeypatch.setattr("recap.trim.trim_plan", fake_trim)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "create", str(clips_dir), str(music_file), "--no-transcode",
        ])
        assert result.exit_code == 0
        assert len(trim_called) == 0, "trim_plan should not be called"

    def test_warns_on_clip_analysis_errors(self, tmp_path, monkeypatch):
        """When pipeline returns warnings, they are printed."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")

        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        from recap.pipeline import PipelineResult, PipelineWarning

        def fake_run(config, stop_after=None):
            return PipelineResult(
                plan=assign_plan,
                warnings=[
                    PipelineWarning(
                        stage="analyze_clips",
                        message="Cannot decode",
                        source="broken.mp4",
                    ),
                ],
            )

        monkeypatch.setattr("recap.pipeline.run", fake_run)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["create", str(clips_dir), str(music_file)])
        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "broken.mp4" in result.output
        assert "Cannot decode" in result.output
