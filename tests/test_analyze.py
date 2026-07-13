"""Tests for recap analyze command and video analysis logic."""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import click.testing
import numpy as np
import pytest

from recap.cli import main


# ---------------------------------------------------------------------------
# Synthetic video helpers
# ---------------------------------------------------------------------------

def create_synthetic_video(path, num_frames=90, fps=30, width=640, height=480):
    """Create a small synthetic .mp4 with a white bar moving rightward."""
    import cv2

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(path), fourcc, fps, (width, height))
    for i in range(num_frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        x = i * 3  # bar shifts right 3 px / frame
        x = min(x, width - 50)
        frame[:, x : x + 50] = 255
        out.write(frame)
    out.release()
    return path


# ---------------------------------------------------------------------------
# CLI-level tests
# ---------------------------------------------------------------------------

class TestAnalyzeCLI:
    def test_help(self):
        """`recap analyze --help` prints usage."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "analyze" in result.output
        assert "VIDEO_PATH" in result.output

    def test_missing_file(self):
        """`recap analyze nonexistent.mp4` exits non-zero."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["analyze", "/nonexistent/path/video.mp4"])
        assert result.exit_code != 0

    def test_corrupt_file(self, tmp_path):
        """`recap analyze` on a corrupt file exits non-zero."""
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"not-a-valid-video")
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["analyze", str(corrupt)])
        assert result.exit_code != 0

    def test_valid_video_json_shape(self, tmp_path):
        """Output is valid JSON with required top-level keys."""
        video_path = tmp_path / "test.mp4"
        create_synthetic_video(video_path, num_frames=90, fps=30)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["analyze", str(video_path)])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert "most_exciting" in data
        assert "start" in data["most_exciting"]
        assert "end" in data["most_exciting"]
        assert "score" in data["most_exciting"]
        assert "motion_scores" in data
        assert isinstance(data["motion_scores"], list)
        assert len(data["motion_scores"]) > 0
        assert "orientation" in data
        assert data["orientation"] in ("portrait", "landscape")

    def test_valid_video_values_are_numeric(self, tmp_path):
        """start, end, score are floats; motion_scores are floats."""
        video_path = tmp_path / "test.mp4"
        create_synthetic_video(video_path, num_frames=90, fps=30)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["analyze", str(video_path)])
        data = json.loads(result.output)

        me = data["most_exciting"]
        assert isinstance(me["start"], (int, float))
        assert isinstance(me["end"], (int, float))
        assert isinstance(me["score"], (int, float))
        assert me["start"] < me["end"]
        assert all(isinstance(s, (int, float)) for s in data["motion_scores"])

    def test_custom_window_option(self, tmp_path):
        """--window N changes the most_exciting segment length."""
        video_path = tmp_path / "test.mp4"
        create_synthetic_video(video_path, num_frames=90, fps=30)

        runner = click.testing.CliRunner()
        result_default = runner.invoke(main, ["analyze", str(video_path)])
        result_custom = runner.invoke(main, ["analyze", "--window", "1.0", str(video_path)])

        d1 = json.loads(result_default.output)
        d2 = json.loads(result_custom.output)

        dur_default = d1["most_exciting"]["end"] - d1["most_exciting"]["start"]
        dur_custom = d2["most_exciting"]["end"] - d2["most_exciting"]["start"]
        assert dur_default >= dur_custom  # 3s default >= 1s custom
        assert dur_custom >= 0.9  # roughly 1 second


# ---------------------------------------------------------------------------
# Unit tests for video.py internals
# ---------------------------------------------------------------------------

class TestFrameDiffScore:
    """Tests for _frame_diff_score (imported from video module)."""

    def test_identical_frames_zero(self):
        from recap.video import _frame_diff_score

        frame = np.ones((100, 100, 3), dtype=np.uint8) * 128
        assert _frame_diff_score(frame, frame) == 0.0

    def test_different_frames_positive(self):
        from recap.video import _frame_diff_score

        f1 = np.zeros((100, 100, 3), dtype=np.uint8)
        f2 = np.ones((100, 100, 3), dtype=np.uint8) * 255
        assert _frame_diff_score(f1, f2) > 0

    def test_returns_float(self):
        from recap.video import _frame_diff_score

        f1 = np.zeros((100, 100, 3), dtype=np.uint8)
        f2 = np.ones((100, 100, 3), dtype=np.uint8) * 128
        assert isinstance(_frame_diff_score(f1, f2), float)


class TestFindMostExciting:
    """Tests for find_most_exciting."""

    def test_peak_in_middle(self):
        from recap.video import find_most_exciting

        # 90 frames at 30 fps: first 30 low, middle 30 high, last 30 low
        scores = [0.0] * 30 + [10.0] * 30 + [0.0] * 30
        start, end, score = find_most_exciting(scores, fps=30.0, window_seconds=1.0)
        # 1s window = 30 frames. Best window should be fully inside the 10.0 block.
        assert start >= 25 / 30.0 - 0.1  # allow minor boundary imprecision
        assert end <= 65 / 30.0 + 0.1
        assert score > 5.0

    def test_window_longer_than_clip(self):
        from recap.video import find_most_exciting

        scores = [1.0, 2.0, 3.0]
        start, end, score = find_most_exciting(scores, fps=10.0, window_seconds=10.0)
        assert start == 0.0
        assert end == pytest.approx(0.3)
        assert score == pytest.approx(2.0)

    def test_constant_scores(self):
        from recap.video import find_most_exciting

        scores = [5.0] * 60
        start, end, score = find_most_exciting(scores, fps=30.0, window_seconds=1.0)
        assert start == 0.0
        assert end == pytest.approx(1.0)
        assert score == pytest.approx(5.0)


class TestGetOrientation:
    """Tests for get_orientation with mocked ffprobe."""

    def test_landscape_no_rotation(self):
        from recap.video import get_orientation

        fake_output = json.dumps({
            "streams": [{"width": 1920, "height": 1080}]
        })
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0, stdout=fake_output, stderr=""
            )
            assert get_orientation("dummy.mp4") == "landscape"

    def test_portrait_no_rotation(self):
        from recap.video import get_orientation

        fake_output = json.dumps({
            "streams": [{"width": 1080, "height": 1920}]
        })
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0, stdout=fake_output, stderr=""
            )
            assert get_orientation("dummy.mp4") == "portrait"

    def test_portrait_rotation_90(self):
        from recap.video import get_orientation

        # 1920x1080 with rotation=90 → effectively portrait
        fake_output = json.dumps({
            "streams": [{
                "width": 1920, "height": 1080,
                "tags": {"rotation": "90"}
            }]
        })
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0, stdout=fake_output, stderr=""
            )
            assert get_orientation("dummy.mp4") == "portrait"

    def test_landscape_rotation_180(self):
        from recap.video import get_orientation

        fake_output = json.dumps({
            "streams": [{
                "width": 1920, "height": 1080,
                "tags": {"rotation": "180"}
            }]
        })
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0, stdout=fake_output, stderr=""
            )
            assert get_orientation("dummy.mp4") == "landscape"

    def test_ffprobe_failure_raises(self):
        from recap.video import get_orientation

        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "ffprobe")
            with pytest.raises(RuntimeError):
                get_orientation("dummy.mp4")


class TestAnalyzeVideo:
    """Tests for analyze_video top-level function."""

    def test_missing_file_raises(self):
        from recap.video import analyze_video

        with pytest.raises(FileNotFoundError):
            analyze_video("/nonexistent/video.mp4")

    def test_returns_correct_keys(self, tmp_path, monkeypatch):
        """End-to-end test with a real synthetic video."""
        from recap.video import analyze_video

        video_path = tmp_path / "test.mp4"
        create_synthetic_video(video_path, num_frames=60, fps=30)

        result = analyze_video(str(video_path), window_seconds=1.0)
        assert set(result.keys()) == {"most_exciting", "motion_scores", "orientation"}
        me = result["most_exciting"]
        assert all(k in me for k in ("start", "end", "score"))
        assert isinstance(result["motion_scores"], list)
        assert len(result["motion_scores"]) == 60  # one per frame
        assert result["motion_scores"][0] == 0.0  # first frame has no prev

    def test_motion_scores_monotonic_for_moving_bar(self, tmp_path):
        """A monotonically moving bar produces non-zero motion scores."""
        from recap.video import analyze_video

        video_path = tmp_path / "test.mp4"
        create_synthetic_video(video_path, num_frames=30, fps=30)

        result = analyze_video(str(video_path), window_seconds=0.5)
        scores = result["motion_scores"]
        # After first frame (which is 0), scores should be positive
        positive_scores = scores[1:]
        assert any(s > 0 for s in positive_scores), "expected some motion"
