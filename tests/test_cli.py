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


# ---------------------------------------------------------------------------
# Synthetic data fixtures for create pipeline tests
# ---------------------------------------------------------------------------


def _make_beat_analysis(bpm=120.0, num_beats=32):
    """Return a synthetic beat analysis dict."""
    beat_interval = 60.0 / bpm
    beats = [i * beat_interval for i in range(num_beats)]
    energy = [round(0.5 + 0.3 * (i % 4) / 4, 4) for i in range(num_beats)]
    return {"bpm": bpm, "beats": beats, "energy": energy}


def _make_clip_analyses(num_clips=3):
    """Return synthetic batch analysis results dict."""
    results = {}
    for i in range(num_clips):
        score = round(0.9 - i * 0.2, 2)
        results[f"/fake/clip_{i}.mp4"] = {
            "most_exciting": {"start": 1.0, "end": 3.0, "score": score},
            "motion_scores": [score] * 90,
            "orientation": "landscape",
        }
    return {
        "processed": num_clips,
        "skipped": 0,
        "errors": [],
        "results": results,
    }


def _make_assignment_plan(bpm=120.0, clips_dir="/fake"):
    """Return a synthetic assignment plan with 3 clips."""
    return {
        "bpm": bpm,
        "assignments": [
            {
                "clip": f"{clips_dir}/clip_0.mp4",
                "source_start": 0.75,
                "source_end": 3.25,
                "target_start": 0.0,
                "beat_index": 0,
                "beat_count": 5,
                "beat_energy": 0.55,
                "motion_score": 0.9,
            },
            {
                "clip": f"{clips_dir}/clip_1.mp4",
                "source_start": 0.75,
                "source_end": 3.25,
                "target_start": 2.5,
                "beat_index": 5,
                "beat_count": 5,
                "beat_energy": 0.55,
                "motion_score": 0.7,
            },
            {
                "clip": f"{clips_dir}/clip_2.mp4",
                "source_start": 0.75,
                "source_end": 3.25,
                "target_start": 5.0,
                "beat_index": 10,
                "beat_count": 5,
                "beat_energy": 0.55,
                "motion_score": 0.5,
            },
        ],
    }


def _make_trimmed_plan(plan):
    """Add trim paths and summary to an assignment plan."""
    p = dict(plan)
    for a in p["assignments"]:
        a["trim"] = a["clip"].replace(".mp4", "_trim.mp4")
    p["_trim_summary"] = {
        "succeeded": len(p["assignments"]),
        "failed": 0,
        "errors": [],
    }
    return p


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

    def test_pipeline_runs_all_stages_no_transcode(self, tmp_path, monkeypatch):
        """End-to-end: create --no-transcode calls 4 stages in order."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")
        output_path = tmp_path / "recap.kdenlive"

        beat_data = _make_beat_analysis()
        clip_results = _make_clip_analyses(3)
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        kdenlive_xml = '<?xml version="1.0"?>\n<mlt version="1.1"></mlt>'

        call_order = []

        def fake_analyze_directory(directory, force=False, transcode=False):
            call_order.append(("analyze_directory", directory, force, transcode))
            return dict(clip_results)

        def fake_detect_beats(filepath):
            call_order.append(("detect_beats", filepath))
            return dict(beat_data)

        def fake_assign_clips(beat_analysis, clip_analyses, mode="shuffled-tiers", **kwargs):
            call_order.append(("assign_clips", mode))
            return dict(assign_plan)

        def fake_render_kdenlive(plan, music_path, output_ratio="16:9", **kwargs):
            call_order.append(("render_kdenlive", music_path, output_ratio))
            return kdenlive_xml

        monkeypatch.setattr("recap.batch.analyze_directory", fake_analyze_directory)
        monkeypatch.setattr("recap.audio.detect_beats", fake_detect_beats)
        monkeypatch.setattr("recap.assign.assign_clips", fake_assign_clips)
        monkeypatch.setattr("recap.render.render_kdenlive", fake_render_kdenlive)

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

        # Verify each stage ran (4 stages, no transcode)
        assert len(call_order) == 4
        assert call_order[0] == ("analyze_directory", str(clips_dir), True, False)
        assert call_order[1] == ("detect_beats", str(music_file))
        assert call_order[2] == ("assign_clips", "best-match")
        assert call_order[3] == ("render_kdenlive", str(music_file), "9:16")

        # Verify output file was written
        assert output_path.exists()
        assert output_path.read_text() == kdenlive_xml

        # Verify progress messages
        assert "Stage 1/4" in result.output
        assert "Stage 2/4" in result.output
        assert "Stage 3/4" in result.output
        assert "Stage 4/4" in result.output
        assert "Done!" in result.output

    def test_pipeline_runs_all_five_stages_with_transcode(self, tmp_path, monkeypatch):
        """End-to-end: create (default, transcode on) calls 5 stages."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")
        output_path = tmp_path / "recap.kdenlive"

        beat_data = _make_beat_analysis()
        clip_results = _make_clip_analyses(3)
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        kdenlive_xml = '<?xml version="1.0"?>\n<mlt version="1.1"></mlt>'

        call_order = []

        def fake_analyze_directory(directory, force=False, transcode=False):
            call_order.append(("analyze_directory", directory, force, transcode))
            r = dict(clip_results)
            r["transcode"] = {"new": 3, "skipped": 0, "errors": []}
            return r

        def fake_detect_beats(filepath):
            call_order.append(("detect_beats", filepath))
            return dict(beat_data)

        def fake_assign_clips(beat_analysis, clip_analyses, mode="shuffled-tiers", **kwargs):
            call_order.append(("assign_clips", mode))
            return dict(assign_plan)

        def fake_render_kdenlive(plan, music_path, output_ratio="16:9", **kwargs):
            call_order.append(("render_kdenlive", music_path, output_ratio))
            return kdenlive_xml

        monkeypatch.setattr("recap.batch.analyze_directory", fake_analyze_directory)
        monkeypatch.setattr("recap.audio.detect_beats", fake_detect_beats)
        monkeypatch.setattr("recap.assign.assign_clips", fake_assign_clips)
        monkeypatch.setattr("recap.render.render_kdenlive", fake_render_kdenlive)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "create",
            str(clips_dir),
            str(music_file),
            "-o", str(output_path),
            "--mode", "best-match",
        ])

        assert result.exit_code == 0, f"CLI failed: {result.output}\nstderr: {result.exc_info}"

        # Verify each stage ran (5 stages with transcode)
        assert len(call_order) == 4
        assert call_order[0] == ("analyze_directory", str(clips_dir), False, True)
        assert call_order[1] == ("detect_beats", str(music_file))
        assert call_order[2] == ("assign_clips", "best-match")

        # Verify progress messages (5-stage numbering)
        assert "Stage 1/5" in result.output
        assert "Stage 3/5" in result.output
        assert "Stage 4/5" in result.output
        assert "Stage 5/5" in result.output
        assert "Transcoded:" in result.output
        assert "Done!" in result.output

    def test_pipeline_defaults_no_transcode(self, tmp_path, monkeypatch):
        """End-to-end --no-transcode: uses correct defaults (shuffled-tiers, 16:9)."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")

        beat_data = _make_beat_analysis()
        clip_results = _make_clip_analyses(1)
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        call_order = []

        monkeypatch.setattr("recap.batch.analyze_directory",
                           lambda directory, force=False, transcode=False: (
                               call_order.append(("analyze", force, transcode)) or dict(clip_results)))
        monkeypatch.setattr("recap.audio.detect_beats",
                           lambda filepath: (call_order.append(("beats",)) or dict(beat_data)))
        monkeypatch.setattr("recap.assign.assign_clips",
                           lambda beat_analysis, clip_analyses, mode="shuffled-tiers", **kw:
                           (call_order.append(("assign", mode)) or dict(assign_plan)))
        monkeypatch.setattr("recap.render.render_kdenlive",
                           lambda plan, music_path, output_ratio="16:9", **kw:
                           (call_order.append(("render", output_ratio)) or "<xml/>"))

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "create", str(clips_dir), str(music_file), "--no-transcode",
        ])

        assert result.exit_code == 0
        assert call_order[0] == ("analyze", False, False)
        # shuffled-tiers is default mode
        assert ("assign", "shuffled-tiers") in call_order
        # 16:9 is default ratio
        assert ("render", "16:9") in call_order
        # trim is no longer called
        assert not any(c[0] == "trim" for c in call_order)

    def test_no_clips_aborts_with_error(self, tmp_path, monkeypatch):
        """When no clips are found, exits non-zero."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")

        empty_batch = {"processed": 0, "skipped": 0, "errors": [], "results": {}, "transcode": None}
        monkeypatch.setattr("recap.batch.analyze_directory", lambda *a, **kw: empty_batch)

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

        beat_data = _make_beat_analysis()
        clip_results = _make_clip_analyses(1)
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        trim_called = []

        def fake_trim(*a, **kw):
            trim_called.append(True)
            return assign_plan

        monkeypatch.setattr("recap.batch.analyze_directory", lambda *a, **kw: dict(clip_results))
        monkeypatch.setattr("recap.audio.detect_beats", lambda *a, **kw: dict(beat_data))
        monkeypatch.setattr("recap.assign.assign_clips", lambda *a, **kw: dict(assign_plan))
        monkeypatch.setattr("recap.trim.trim_plan", fake_trim)
        monkeypatch.setattr("recap.render.render_kdenlive", lambda *a, **kw: "<xml/>")

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "create", str(clips_dir), str(music_file), "--no-transcode",
        ])
        assert result.exit_code == 0
        assert len(trim_called) == 0, "trim_plan should not be called"

    def test_music_caching_used_when_not_forced(self, tmp_path, monkeypatch):
        """When --force is not set and music cache exists, detect_beats is not called."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")

        # Pre-populate music cache
        cache_dir = clips_dir / ".recap-cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "song_beats.json"
        cache_file.write_text('{"bpm": 140.0, "beats": [0.0, 0.43], "energy": [0.5, 0.5]}')

        beat_calls = []

        def fake_detect_beats(fp):
            beat_calls.append(fp)
            return {"bpm": 120.0, "beats": [0.0, 0.5], "energy": [0.5, 0.5]}

        clip_results = _make_clip_analyses(1)
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        monkeypatch.setattr("recap.batch.analyze_directory", lambda *a, **kw: dict(clip_results))
        monkeypatch.setattr("recap.audio.detect_beats", fake_detect_beats)
        monkeypatch.setattr("recap.assign.assign_clips", lambda *a, **kw: dict(assign_plan))
        monkeypatch.setattr("recap.render.render_kdenlive", lambda *a, **kw: "<xml/>")

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["create", str(clips_dir), str(music_file)])
        assert result.exit_code == 0
        assert len(beat_calls) == 0  # cached, not called
        assert "Using cached beat analysis" in result.output

    def test_force_reanalyzes_music(self, tmp_path, monkeypatch):
        """When --force is set, music cache is ignored."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")

        # Pre-populate music cache
        cache_dir = clips_dir / ".recap-cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "song_beats.json"
        cache_file.write_text('{"bpm": 140.0, "beats": [0.0], "energy": [0.5]}')

        beat_calls = []
        beat_data = _make_beat_analysis()

        def fake_detect_beats(fp):
            beat_calls.append(fp)
            return dict(beat_data)

        clip_results = _make_clip_analyses(1)
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        monkeypatch.setattr("recap.batch.analyze_directory", lambda *a, **kw: dict(clip_results))
        monkeypatch.setattr("recap.audio.detect_beats", fake_detect_beats)
        monkeypatch.setattr("recap.assign.assign_clips", lambda *a, **kw: dict(assign_plan))
        monkeypatch.setattr("recap.render.render_kdenlive", lambda *a, **kw: "<xml/>")

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "create", str(clips_dir), str(music_file), "--force",
        ])
        assert result.exit_code == 0
        assert len(beat_calls) == 1  # force bypassed cache
        assert "Using cached beat analysis" not in result.output

    def test_warns_on_clip_analysis_errors(self, tmp_path, monkeypatch):
        """When batch analysis has errors, they are printed as warnings but pipeline continues."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_file = tmp_path / "song.mp3"
        music_file.write_text("fake audio")

        batch_with_errors = {
            "processed": 2,
            "skipped": 0,
            "errors": [
                {"file": "broken.mp4", "error": "Cannot decode"},
            ],
            "results": {
                "/good1.mp4": _make_clip_analyses(1)["results"]["/fake/clip_0.mp4"],
                "/good2.mp4": _make_clip_analyses(2)["results"]["/fake/clip_1.mp4"],
            },
        }

        beat_data = _make_beat_analysis()
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        monkeypatch.setattr("recap.batch.analyze_directory", lambda *a, **kw: dict(batch_with_errors))
        monkeypatch.setattr("recap.audio.detect_beats", lambda *a, **kw: dict(beat_data))
        monkeypatch.setattr("recap.assign.assign_clips", lambda *a, **kw: dict(assign_plan))
        monkeypatch.setattr("recap.render.render_kdenlive", lambda *a, **kw: "<xml/>")

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["create", str(clips_dir), str(music_file)])
        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "broken.mp4" in result.output
        assert "Cannot decode" in result.output
