"""Tests for constant frame-rate transcode (ticket 07)."""

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from recap.transcode import transcode_clip, _content_hash


# ---------------------------------------------------------------------------
# _content_hash tests
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_deterministic(self, tmp_path):
        f = tmp_path / "test.mp4"
        f.write_bytes(b"a" * 2000)
        assert _content_hash(f) == _content_hash(f)

    def test_different_content_different_hash(self, tmp_path):
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.write_bytes(b"a" * 2000)
        b.write_bytes(b"b" * 2000)
        assert _content_hash(a) != _content_hash(b)

    def test_same_content_different_names_same_hash(self, tmp_path):
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.write_bytes(b"same content")
        b.write_bytes(b"same content")
        assert _content_hash(a) == _content_hash(b)

    def test_returns_12_char_hex(self, tmp_path):
        f = tmp_path / "test.mp4"
        f.write_bytes(b"data")
        h = _content_hash(f)
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.mp4"
        f.write_bytes(b"")
        h = _content_hash(f)
        assert len(h) == 12

    def test_large_file_first_mb_only(self, tmp_path):
        """Content hash only reads first 1MB, so a change past 1MB doesn't alter it."""
        f = tmp_path / "big.mp4"
        # Write 1MB + 100KB
        f.write_bytes(b"a" * 1_048_576 + b"x" * 100_000)
        h1 = _content_hash(f)
        # Rewrite with different trailing bytes
        f.write_bytes(b"a" * 1_048_576 + b"y" * 100_000)
        h2 = _content_hash(f)
        assert h1 == h2


# ---------------------------------------------------------------------------
# transcode_clip tests
# ---------------------------------------------------------------------------

class TestTranscodeClip:
    def test_missing_file_returns_original(self, tmp_path):
        result = transcode_clip(str(tmp_path / "nonexistent.mp4"), str(tmp_path))
        assert result == str(tmp_path / "nonexistent.mp4")

    def test_ffmpeg_success(self, tmp_path):
        """Mocked ffmpeg: returns transcoded path, creates directory."""
        source = tmp_path / "source.mp4"
        source.write_bytes(b"fake video data")

        def fake_run(cmd, **kwargs):
            # Simulate ffmpeg creating output
            # The output path is the last arg before -y
            out_path = Path(cmd[-1])
            out_path.write_bytes(b"transcoded data")
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.stderr = ""
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = transcode_clip(str(source), str(tmp_path), fps=25)

        assert result != str(source)
        assert result.endswith(".mp4")
        assert Path(result).exists()

    def test_skips_if_already_transcoded(self, tmp_path):
        """Second call to transcode_clip returns cached path without running ffmpeg."""
        source = tmp_path / "source.mp4"
        source.write_bytes(b"fake video data")

        run_count = [0]

        def fake_run(cmd, **kwargs):
            run_count[0] += 1
            out_path = Path(cmd[-1])
            out_path.write_bytes(b"transcoded data")
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.stderr = ""
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            first = transcode_clip(str(source), str(tmp_path), fps=25)
            second = transcode_clip(str(source), str(tmp_path), fps=25)

        assert first == second
        assert run_count[0] == 1  # ffmpeg only called once

    def test_ffmpeg_nonzero_exit(self, tmp_path):
        source = tmp_path / "source.mp4"
        source.write_bytes(b"fake video data")

        def fake_run(cmd, **kwargs):
            proc = mock.MagicMock()
            proc.returncode = 1
            proc.stderr = "Error while decoding stream"
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="Error while decoding"):
                transcode_clip(str(source), str(tmp_path), fps=25)

    def test_ffmpeg_timeout(self, tmp_path):
        source = tmp_path / "source.mp4"
        source.write_bytes(b"fake video data")

        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 600)):
            with pytest.raises(RuntimeError, match="Transcode failed"):
                transcode_clip(str(source), str(tmp_path), fps=25)

    def test_ffmpeg_not_found(self, tmp_path):
        source = tmp_path / "source.mp4"
        source.write_bytes(b"fake video data")

        with mock.patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg")):
            with pytest.raises(RuntimeError, match="Transcode failed"):
                transcode_clip(str(source), str(tmp_path), fps=25)

    def test_creates_transcode_subdir(self, tmp_path):
        source = tmp_path / "source.mp4"
        source.write_bytes(b"fake video data")

        def fake_run(cmd, **kwargs):
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"transcoded data")
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.stderr = ""
            return proc

        cache_dir = tmp_path / "cache"
        with mock.patch("subprocess.run", side_effect=fake_run):
            result = transcode_clip(str(source), str(cache_dir), fps=25)

        assert "transcoded" in result
        assert (cache_dir / "transcoded").is_dir()

    def test_uses_specified_fps(self, tmp_path):
        source = tmp_path / "source.mp4"
        source.write_bytes(b"fake video data")

        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.append(cmd)
            out_path = Path(cmd[-1])
            out_path.write_bytes(b"transcoded data")
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.stderr = ""
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            transcode_clip(str(source), str(tmp_path), fps=30)

        # Find -r flag value
        r_idx = captured_cmd[0].index("-r")
        assert captured_cmd[0][r_idx + 1] == "30"

        # Check -vsync cfr
        vsync_idx = captured_cmd[0].index("-vsync")
        assert captured_cmd[0][vsync_idx + 1] == "cfr"

    def test_ffmpeg_exit_zero_but_no_file(self, tmp_path):
        source = tmp_path / "source.mp4"
        source.write_bytes(b"fake video data")

        def fake_run(cmd, **kwargs):
            # Don't create the output file
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.stderr = ""
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="output file not found"):
                transcode_clip(str(source), str(tmp_path), fps=25)


# ---------------------------------------------------------------------------
# Integration test (requires ffmpeg)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestTranscodeIntegration:
    @pytest.fixture
    def check_ffmpeg(self):
        try:
            subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, timeout=10, check=True
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pytest.skip("ffmpeg not available")

    def test_transcode_real_clip(self, tmp_path, check_ffmpeg):
        """Generate synthetic 30fps video, transcode to 25fps, verify output."""
        source = tmp_path / "source.mp4"
        gen_cmd = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", "testsrc=duration=2:size=320x240:rate=30",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-y",
            str(source),
        ]
        subprocess.run(gen_cmd, capture_output=True, check=True)
        assert source.exists()

        cache_dir = tmp_path / ".recap-cache"
        result = transcode_clip(str(source), str(cache_dir), fps=25)

        assert result != str(source)
        out_path = Path(result)
        assert out_path.exists()

        # Probe frame rate
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(out_path),
        ]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        fps_str = probe.stdout.strip()
        # Should be 25/1
        assert "25/1" in fps_str

    def test_transcode_is_idempotent(self, tmp_path, check_ffmpeg):
        source = tmp_path / "source.mp4"
        gen_cmd = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", "testsrc=duration=1:size=320x240:rate=30",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-y",
            str(source),
        ]
        subprocess.run(gen_cmd, capture_output=True, check=True)

        cache_dir = tmp_path / ".recap-cache"
        first = transcode_clip(str(source), str(cache_dir), fps=25)
        second = transcode_clip(str(source), str(cache_dir), fps=25)

        assert first == second
