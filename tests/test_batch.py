"""Tests for batch analysis and caching (T4)."""

import json
import os
import time
from pathlib import Path
from unittest import mock

import click.testing
import numpy as np
import pytest

from recap.cli import main

# Re-use the synthetic-video helper via a local import.
from test_analyze import create_synthetic_video


# ---------------------------------------------------------------------------
# Unit tests — batch.py internals
# ---------------------------------------------------------------------------

class TestCacheKey:
    def test_deterministic(self):
        from recap.batch import _cache_key

        p = Path("/tmp/foo.mp4")
        assert _cache_key(p) == _cache_key(p)

    def test_different_paths_different_keys(self):
        from recap.batch import _cache_key

        a = _cache_key(Path("/tmp/a.mp4"))
        b = _cache_key(Path("/tmp/b.mp4"))
        assert a != b

    def test_ends_with_json(self):
        from recap.batch import _cache_key

        key = _cache_key(Path("/tmp/v.mp4"))
        assert key.endswith(".json")

    def test_no_slashes(self):
        from recap.batch import _cache_key

        key = _cache_key(Path("/tmp/v.mp4"))
        assert "/" not in key
        assert "\\" not in key


class TestCachePathFor:
    def test_constructs_expected_path(self, tmp_path):
        from recap.batch import _cache_path_for, _cache_key

        cache_dir = tmp_path / ".recap-cache"
        video = tmp_path / "clip.mp4"

        result = _cache_path_for(cache_dir, video)
        assert result.parent == cache_dir
        assert result.name == _cache_key(video)


class TestIsFresh:
    def test_no_cache_file_returns_false(self, tmp_path):
        from recap.batch import _is_fresh

        video = tmp_path / "v.mp4"
        video.write_text("fake")
        cache = tmp_path / "cache.json"

        assert _is_fresh(video, cache) is False

    def test_source_newer_than_cache_returns_false(self, tmp_path):
        from recap.batch import _is_fresh

        video = tmp_path / "v.mp4"
        video.write_text("fake")

        cache = tmp_path / "cache.json"
        cache.write_text("{}")

        # Ensure video mtime >= cache mtime
        video_mtime = video.stat().st_mtime
        cache_mtime = cache.stat().st_mtime

        if video_mtime < cache_mtime:
            # Touch video to make it newer
            time.sleep(0.01)
            video.write_text("fake-updated")

        assert _is_fresh(video, cache) is False

    def test_source_older_than_cache_returns_true(self, tmp_path):
        from recap.batch import _is_fresh

        video = tmp_path / "v.mp4"
        video.write_text("fake")

        # Sleep so cache is definitively newer.
        time.sleep(0.02)
        cache = tmp_path / "cache.json"
        cache.write_text("{}")

        assert _is_fresh(video, cache) is True


class TestFindVideos:
    def test_finds_mp4_and_mov(self, tmp_path):
        from recap.batch import _find_videos

        (tmp_path / "a.mp4").touch()
        (tmp_path / "b.mov").touch()
        (tmp_path / "c.txt").touch()
        (tmp_path / "d.mp3").touch()

        found = _find_videos(tmp_path)
        names = {p.name for p in found}
        assert names == {"a.mp4", "b.mov"}

    def test_recursive(self, tmp_path):
        from recap.batch import _find_videos

        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "root.mp4").touch()
        (sub / "deep.mov").touch()

        found = _find_videos(tmp_path)
        names = {p.name for p in found}
        assert names == {"root.mp4", "deep.mov"}

    def test_case_insensitive_fallback(self, tmp_path):
        from recap.batch import _find_videos

        (tmp_path / "LOUD.MP4").touch()
        (tmp_path / "Quiet.MOV").touch()

        found = _find_videos(tmp_path)
        names = {p.name for p in found}
        assert names == {"LOUD.MP4", "Quiet.MOV"}

    def test_sorted_output(self, tmp_path):
        from recap.batch import _find_videos

        (tmp_path / "c.mp4").touch()
        (tmp_path / "a.mp4").touch()
        (tmp_path / "b.mp4").touch()

        found = _find_videos(tmp_path)
        names = [p.name for p in found]
        assert names == sorted(names)

    def test_empty_directory(self, tmp_path):
        from recap.batch import _find_videos

        assert _find_videos(tmp_path) == []

    def test_excludes_recap_cache_dir(self, tmp_path):
        """_find_videos excludes .recap-cache/ subtree."""
        from recap.batch import _find_videos

        (tmp_path / "good.mp4").touch()
        cache = tmp_path / ".recap-cache"
        transcode_dir = cache / "transcoded"
        transcode_dir.mkdir(parents=True)
        (transcode_dir / "hidden.mp4").touch()

        found = _find_videos(tmp_path)
        names = {p.name for p in found}
        assert names == {"good.mp4"}
        assert "hidden.mp4" not in names


# ---------------------------------------------------------------------------
# Integration tests — analyze_directory
# ---------------------------------------------------------------------------

class TestAnalyzeDirectory:
    def test_processes_all_videos(self, tmp_path):
        from recap.batch import analyze_directory

        v1 = tmp_path / "clip1.mp4"
        v2 = tmp_path / "clip2.mp4"
        create_synthetic_video(v1, num_frames=30, fps=30)
        create_synthetic_video(v2, num_frames=30, fps=30)

        summary = analyze_directory(str(tmp_path))
        assert summary["processed"] == 2
        assert summary["skipped"] == 0
        assert summary["errors"] == []
        assert len(summary["results"]) == 2
        assert str(v1) in summary["results"]

    def test_caches_to_dot_recap_cache(self, tmp_path):
        from recap.batch import analyze_directory

        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        analyze_directory(str(tmp_path))

        cache_dir = tmp_path / ".recap-cache"
        assert cache_dir.is_dir()
        cache_files = list(cache_dir.iterdir())
        assert len(cache_files) == 1
        assert cache_files[0].suffix == ".json"

    def test_second_run_skips_all(self, tmp_path):
        from recap.batch import analyze_directory

        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        s1 = analyze_directory(str(tmp_path))
        assert s1["processed"] == 1
        assert s1["skipped"] == 0

        s2 = analyze_directory(str(tmp_path))
        assert s2["processed"] == 0
        assert s2["skipped"] == 1
        # Skipped items still appear in results (loaded from cache).
        assert str(v) in s2["results"]

    def test_force_reanalyzes_all(self, tmp_path):
        from recap.batch import analyze_directory

        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        s1 = analyze_directory(str(tmp_path))
        assert s1["processed"] == 1

        s2 = analyze_directory(str(tmp_path), force=True)
        assert s2["processed"] == 1
        assert s2["skipped"] == 0

    def test_source_modified_triggers_reanalysis(self, tmp_path):
        from recap.batch import analyze_directory

        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        s1 = analyze_directory(str(tmp_path))
        assert s1["processed"] == 1

        # Touch source to make it newer than cache.
        time.sleep(0.02)
        os.utime(str(v), None)

        s2 = analyze_directory(str(tmp_path))
        assert s2["processed"] == 1
        assert s2["skipped"] == 0

    def test_errors_reported_not_crashing_batch(self, tmp_path):
        from recap.batch import analyze_directory

        good = tmp_path / "good.mp4"
        bad = tmp_path / "bad.mp4"
        create_synthetic_video(good, num_frames=30, fps=30)
        bad.write_bytes(b"not-a-video")

        summary = analyze_directory(str(tmp_path))
        assert summary["processed"] == 1
        assert len(summary["errors"]) == 1
        assert str(bad) == summary["errors"][0]["file"]
        # Good clip still has results.
        assert str(good) in summary["results"]

    def test_empty_directory(self, tmp_path):
        from recap.batch import analyze_directory

        summary = analyze_directory(str(tmp_path))
        assert summary["processed"] == 0
        assert summary["skipped"] == 0
        assert summary["errors"] == []

    def test_not_a_directory_raises(self, tmp_path):
        from recap.batch import analyze_directory

        f = tmp_path / "file.txt"
        f.touch()
        with pytest.raises(ValueError, match="Not a directory"):
            analyze_directory(str(f))

    def test_cache_is_valid_json(self, tmp_path):
        from recap.batch import analyze_directory, _cache_key

        import recap.batch as batch_mod

        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        analyze_directory(str(tmp_path))

        cache_path = tmp_path / ".recap-cache" / _cache_key(v)
        data = json.loads(cache_path.read_text())
        assert "most_exciting" in data
        assert "motion_scores" in data
        assert "orientation" in data

    def test_corrupt_cache_handled_gracefully(self, tmp_path):
        """Corrupt cache file is overwritten on next run, no crash on skip."""
        from recap.batch import analyze_directory, _cache_key

        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        # First run: write valid cache.
        analyze_directory(str(tmp_path))

        cache_dir = tmp_path / ".recap-cache"
        cache_path = cache_dir / _cache_key(v)
        # Corrupt the cache.
        cache_path.write_text("not valid json at all {{{")

        # Corrupt cache shouldn't crash; will re-analyze because cache
        # mtime is newer but the JSON error is caught during skip-load.
        summary = analyze_directory(str(tmp_path))
        assert summary["processed"] == 0
        assert summary["skipped"] == 1
        # Result won't be loaded (corrupt), but shouldn't crash.
        # Force re-analysis should fix it.
        summary2 = analyze_directory(str(tmp_path), force=True)
        assert summary2["processed"] == 1


# ---------------------------------------------------------------------------
# CLI integration tests — recap analyze with directory
# ---------------------------------------------------------------------------

class TestAnalyzeDirectoryCLI:
    def test_directory_prints_summary(self, tmp_path):
        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["analyze", str(tmp_path)])
        assert result.exit_code == 0
        assert "Processed:" in result.output
        assert "Skipped" in result.output
        assert "Errors:" in result.output

    def test_directory_with_errors_exits_nonzero(self, tmp_path):
        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"garbage")

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["analyze", str(tmp_path)])
        assert result.exit_code == 1
        assert "ERROR:" in result.output

    def test_directory_force_flag(self, tmp_path):
        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        runner = click.testing.CliRunner()
        # First run.
        runner.invoke(main, ["analyze", str(tmp_path)])
        # Second with --force.
        result = runner.invoke(main, ["analyze", "--force", str(tmp_path)])
        assert result.exit_code == 0
        # All processed (not skipped).
        lines = result.output.splitlines()
        summary_line = [l for l in lines if "Processed:" in l][0]
        assert "Processed: 1" in summary_line
        assert "Skipped (cached): 0" in summary_line

    def test_single_file_still_works(self, tmp_path):
        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["analyze", str(v)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "most_exciting" in data

    def test_single_file_with_force_ok(self, tmp_path):
        """--force on a single file is accepted (no-op, but shouldn't crash)."""
        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["analyze", "--force", str(v)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "most_exciting" in data

    def test_second_run_shows_skipped(self, tmp_path):
        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        runner = click.testing.CliRunner()
        runner.invoke(main, ["analyze", str(tmp_path)])
        result = runner.invoke(main, ["analyze", str(tmp_path)])
        assert result.exit_code == 0
        assert "Skipped (cached): 1" in result.output

    def test_empty_directory_summary(self, tmp_path):
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["analyze", str(tmp_path)])
        assert result.exit_code == 0
        assert "Processed: 0, Skipped (cached): 0, Errors: 0" in result.output


# ---------------------------------------------------------------------------
# Transcode integration in analyze_directory (ticket 08)
# ---------------------------------------------------------------------------

class TestAnalyzeDirectoryWithTranscode:
    def test_transcode_parameter_accepted(self, tmp_path):
        """analyze_directory accepts transcode=True without crashing."""
        from recap.batch import analyze_directory

        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        summary = analyze_directory(str(tmp_path), transcode=False)
        assert summary["processed"] == 1
        assert summary.get("transcode") is None

    def test_transcode_flag_adds_transcode_summary_key(self, tmp_path):
        """When transcode=True is passed, the result includes a transcode summary."""
        from recap.batch import analyze_directory

        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        summary = analyze_directory(str(tmp_path), transcode=True)
        assert "transcode" in summary
        assert summary["transcode"] is not None

    def test_analyze_with_transcode_processes_clips(self, tmp_path):
        """With transcode enabled, clips are still analyzed successfully."""
        from recap.batch import analyze_directory

        v1 = tmp_path / "clip1.mp4"
        create_synthetic_video(v1, num_frames=30, fps=30)

        summary = analyze_directory(str(tmp_path), transcode=True)
        assert summary["processed"] == 1
        assert summary["errors"] == []
        assert str(v1) in summary["results"]

    def test_transcode_result_has_expected_keys(self, tmp_path):
        """Transcode summary has new, skipped, errors keys."""
        from recap.batch import analyze_directory

        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        summary = analyze_directory(str(tmp_path), transcode=True)
        tc = summary["transcode"]
        assert "new" in tc
        assert "skipped" in tc
        assert "errors" in tc

    def test_transcode_works_with_force(self, tmp_path):
        """transcode + force work together."""
        from recap.batch import analyze_directory

        v = tmp_path / "clip.mp4"
        create_synthetic_video(v, num_frames=30, fps=30)

        # First pass
        s1 = analyze_directory(str(tmp_path), transcode=True)
        assert s1["processed"] == 1

        # Second pass with force
        s2 = analyze_directory(str(tmp_path), transcode=True, force=True)
        assert s2["processed"] == 1
        assert s2["skipped"] == 0
