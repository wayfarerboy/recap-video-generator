"""Tests for the trim engine and CLI (T6)."""

import json
import subprocess
from pathlib import Path
from unittest import mock

import click.testing
import pytest

from recap.cli import main
from recap.trim import _deterministic_name, trim_plan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_plan():
    """A minimal assignment plan with two clips."""
    return {
        "bpm": 120.0,
        "assignments": [
            {
                "clip": "/fake/clips/ski.mp4",
                "source_start": 2.5,
                "source_end": 5.5,
                "target_start": 0.0,
                "beat_index": 0,
                "beat_count": 6,
                "beat_energy": 0.4,
                "motion_score": 0.9,
            },
            {
                "clip": "/fake/clips/jump.mp4",
                "source_start": 1.0,
                "source_end": 3.5,
                "target_start": 3.0,
                "beat_index": 6,
                "beat_count": 5,
                "beat_energy": 0.7,
                "motion_score": 0.7,
            },
        ],
    }


@pytest.fixture
def single_clip_plan():
    """Plan with a single clip."""
    return {
        "bpm": 120.0,
        "assignments": [
            {
                "clip": "/fake/clips/lone.mp4",
                "source_start": 3.0,
                "source_end": 6.0,
                "target_start": 0.0,
                "beat_index": 0,
                "beat_count": 6,
                "beat_energy": 0.5,
                "motion_score": 0.8,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Deterministic name tests
# ---------------------------------------------------------------------------

class TestDeterministicName:
    def test_same_inputs_same_output(self):
        name1 = _deterministic_name("/a.mp4", 1.0, 3.0)
        name2 = _deterministic_name("/a.mp4", 1.0, 3.0)
        assert name1 == name2

    def test_different_path_different_name(self):
        name1 = _deterministic_name("/a.mp4", 1.0, 3.0)
        name2 = _deterministic_name("/b.mp4", 1.0, 3.0)
        assert name1 != name2

    def test_different_start_different_name(self):
        name1 = _deterministic_name("/a.mp4", 1.0, 3.0)
        name2 = _deterministic_name("/a.mp4", 2.0, 3.0)
        assert name1 != name2

    def test_different_end_different_name(self):
        name1 = _deterministic_name("/a.mp4", 1.0, 3.0)
        name2 = _deterministic_name("/a.mp4", 1.0, 4.0)
        assert name1 != name2

    def test_ends_with_mp4(self):
        name = _deterministic_name("/a.mp4", 1.0, 3.0)
        assert name.endswith(".mp4")

    def test_is_12_hex_chars_before_ext(self):
        name = _deterministic_name("/a.mp4", 1.0, 3.0)
        stem = name[: -len(".mp4")]
        assert len(stem) == 12
        # all hex
        assert all(c in "0123456789abcdef" for c in stem)


# ---------------------------------------------------------------------------
# trim_plan unit tests (mocked ffmpeg)
# ---------------------------------------------------------------------------

class TestTrimPlanUnit:
    def test_empty_plan_returns_summary(self, tmp_path):
        plan = {"bpm": 120.0, "assignments": []}
        result = trim_plan(plan, output_dir=str(tmp_path))
        assert result["_trim_summary"]["succeeded"] == 0
        assert result["_trim_summary"]["failed"] == 0
        assert result["_trim_summary"]["errors"] == []

    def test_adds_trim_key_on_success(self, tmp_path, sample_plan):
        out = tmp_path / "trims"
        # Mock _trim_one to simulate creating the output file
        def fake_trim_one(task):
            # Create the file so it "exists"
            Path(task["trim_path"]).write_text("fake mp4")
            return task["trim_path"]

        with mock.patch("recap.trim._trim_one", side_effect=fake_trim_one):
            result = trim_plan(sample_plan, output_dir=str(out))
        assert result["_trim_summary"]["succeeded"] == 2
        assert result["_trim_summary"]["failed"] == 0
        assert len(result["_trim_summary"]["errors"]) == 0
        for a in result["assignments"]:
            assert "trim" in a
            assert a["trim"].endswith(".mp4")

    def test_single_clip_failure_does_not_block_others(self, tmp_path, sample_plan):
        out = tmp_path / "trims"
        call_count = [0]

        def fake_trim_one(task):
            call_count[0] += 1
            if "ski" in task["source_path"]:
                raise RuntimeError("ffmpeg exploded")
            Path(task["trim_path"]).write_text("fake mp4")
            return task["trim_path"]

        with mock.patch("recap.trim._trim_one", side_effect=fake_trim_one):
            result = trim_plan(sample_plan, output_dir=str(out))
        assert result["_trim_summary"]["succeeded"] == 1
        assert result["_trim_summary"]["failed"] == 1
        assert len(result["_trim_summary"]["errors"]) == 1
        assert result["_trim_summary"]["errors"][0]["clip"] == "/fake/clips/ski.mp4"
        # ski.mp4 failed → no trim key
        assert "trim" not in result["assignments"][0]
        # jump.mp4 succeeded → has trim key
        assert "trim" in result["assignments"][1]
        assert call_count[0] == 2  # both clips attempted

    def test_trim_paths_are_inside_output_dir(self, tmp_path, sample_plan):
        out = tmp_path / "my-trims"
        def fake_trim_one(task):
            Path(task["trim_path"]).write_text("fake mp4")
            return task["trim_path"]

        with mock.patch("recap.trim._trim_one", side_effect=fake_trim_one):
            result = trim_plan(sample_plan, output_dir=str(out))
        for a in result["assignments"]:
            assert str(out) in a["trim"]

    def test_trim_paths_are_deterministic(self, tmp_path, sample_plan):
        out1 = tmp_path / "trims1"
        out2 = tmp_path / "trims2"
        def fake_trim_one(task):
            Path(task["trim_path"]).write_text("fake mp4")
            return task["trim_path"]

        with mock.patch("recap.trim._trim_one", side_effect=fake_trim_one):
            r1 = trim_plan(sample_plan, output_dir=str(out1))
            r2 = trim_plan(sample_plan, output_dir=str(out2))
        paths1 = [a["trim"] for a in r1["assignments"]]
        paths2 = [a["trim"] for a in r2["assignments"]]

        # Relative filenames (last component) should be identical
        for p1, p2 in zip(paths1, paths2):
            assert Path(p1).name == Path(p2).name

    def test_plan_keys_preserved(self, tmp_path, single_clip_plan):
        out = tmp_path / "trims"
        def fake_trim_one(task):
            Path(task["trim_path"]).write_text("fake mp4")
            return task["trim_path"]

        with mock.patch("recap.trim._trim_one", side_effect=fake_trim_one):
            result = trim_plan(single_clip_plan, output_dir=str(out))
        assert result["bpm"] == 120.0
        a = result["assignments"][0]
        assert a["clip"] == "/fake/clips/lone.mp4"
        assert a["source_start"] == 3.0
        assert a["source_end"] == 6.0
        assert a["target_start"] == 0.0

    def test_creates_output_dir_if_missing(self, tmp_path, single_clip_plan):
        out = tmp_path / "nonexistent" / "trims"
        def fake_trim_one(task):
            Path(task["trim_path"]).write_text("fake mp4")
            return task["trim_path"]

        with mock.patch("recap.trim._trim_one", side_effect=fake_trim_one):
            result = trim_plan(single_clip_plan, output_dir=str(out))
        assert out.exists()
        assert result["_trim_summary"]["succeeded"] == 1

    def test_all_failures_summary(self, tmp_path, sample_plan):
        out = tmp_path / "trims"
        def fake_trim_one(task):
            raise RuntimeError("boom")

        with mock.patch("recap.trim._trim_one", side_effect=fake_trim_one):
            result = trim_plan(sample_plan, output_dir=str(out))
        assert result["_trim_summary"]["succeeded"] == 0
        assert result["_trim_summary"]["failed"] == 2
        assert len(result["_trim_summary"]["errors"]) == 2


# ---------------------------------------------------------------------------
# _trim_one unit tests (mocked subprocess)
# ---------------------------------------------------------------------------

class TestTrimOne:
    def test_ffmpeg_success(self, tmp_path):
        out_path = tmp_path / "trim.mp4"
        task = {
            "source_path": "/fake/clips/test.mp4",
            "trim_path": str(out_path),
            "start": 1.5,
            "duration": 3.0,
        }

        def fake_run(cmd, **kwargs):
            # Simulate ffmpeg creating the output file
            out_path.write_text("fake video data")
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.stderr = ""
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            from recap.trim import _trim_one
            result = _trim_one(task)
            assert result == str(out_path)

    def test_ffmpeg_nonzero_exit(self, tmp_path):
        out_path = tmp_path / "trim.mp4"
        task = {
            "source_path": "/fake/clips/bad.mp4",
            "trim_path": str(out_path),
            "start": 0.0,
            "duration": 5.0,
        }

        def fake_run(cmd, **kwargs):
            proc = mock.MagicMock()
            proc.returncode = 1
            proc.stderr = "Invalid data found when processing input"
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            from recap.trim import _trim_one
            with pytest.raises(RuntimeError, match="Invalid data"):
                _trim_one(task)

    def test_ffmpeg_exit_zero_but_no_file(self, tmp_path):
        out_path = tmp_path / "missing.mp4"
        task = {
            "source_path": "/fake/clips/test.mp4",
            "trim_path": str(out_path),
            "start": 0.0,
            "duration": 2.0,
        }

        def fake_run(cmd, **kwargs):
            # ffmpeg says ok but doesn't write the file
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.stderr = ""
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            from recap.trim import _trim_one
            with pytest.raises(RuntimeError, match="output file not found"):
                _trim_one(task)

    def test_ffmpeg_not_installed(self, tmp_path):
        out_path = tmp_path / "trim.mp4"
        task = {
            "source_path": "/fake/clips/test.mp4",
            "trim_path": str(out_path),
            "start": 1.0,
            "duration": 2.0,
        }

        with mock.patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg")):
            from recap.trim import _trim_one
            with pytest.raises(FileNotFoundError):
                _trim_one(task)


# ---------------------------------------------------------------------------
# CLI integration tests (mocked)
# ---------------------------------------------------------------------------

class TestCLITrim:
    def test_trim_command_in_help(self):
        """`recap --help` lists the trim subcommand."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "trim" in result.output

    def test_trim_requires_plan_option(self):
        """`recap trim` without --plan shows error."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["trim"])
        assert result.exit_code != 0

    def test_trim_with_valid_plan(self, tmp_path, sample_plan):
        """End-to-end mock: write plan file, run trim, check stdout."""
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(sample_plan))
        out_dir = tmp_path / "trims"

        def fake_trim_one(task):
            Path(task["trim_path"]).write_text("fake mp4")
            return task["trim_path"]

        with mock.patch("recap.trim._trim_one", side_effect=fake_trim_one):
            runner = click.testing.CliRunner()
            result = runner.invoke(main, [
                "trim", "--plan", str(plan_file),
                "--output-dir", str(out_dir),
            ])
            assert result.exit_code == 0
            output = _extract_json(result.output)
            assert output["_trim_summary"]["succeeded"] == 2
            assert output["_trim_summary"]["failed"] == 0
            for a in output["assignments"]:
                assert "trim" in a

    def test_trim_default_output_dir(self, tmp_path, sample_plan):
        """Default --output-dir is recap-trims/ relative to CWD."""
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(sample_plan))

        def fake_trim_one(task):
            Path(task["trim_path"]).write_text("fake mp4")
            return task["trim_path"]

        with mock.patch("recap.trim._trim_one", side_effect=fake_trim_one):
            runner = click.testing.CliRunner()
            result = runner.invoke(main, [
                "trim", "--plan", str(plan_file),
            ])
            assert result.exit_code == 0
            output = _extract_json(result.output)
            # trim files created under default "recap-trims" dir
            for a in output["assignments"]:
                assert "recap-trims" in a["trim"]

    def test_trim_missing_plan_file(self, tmp_path):
        """Missing plan file gives error."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "trim", "--plan", str(tmp_path / "nonexistent.json"),
        ])
        assert result.exit_code != 0

    def test_trim_invalid_json_plan(self, tmp_path):
        """Malformed JSON in plan file gives error."""
        plan_file = tmp_path / "bad.json"
        plan_file.write_text("not json {{{")
        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "trim", "--plan", str(plan_file),
        ])
        assert result.exit_code != 0

    def test_trim_plan_missing_assignments_key(self, tmp_path):
        """Plan JSON without 'assignments' key is handled gracefully."""
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps({"bpm": 120.0}))

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "trim", "--plan", str(plan_file),
        ])
        assert result.exit_code == 0
        output = _extract_json(result.output)
        assert output["_trim_summary"]["succeeded"] == 0


def _extract_json(mixed_output: str) -> dict:
    """Extract the first JSON object from mixed stdout/stderr output."""
    # Find the outermost JSON object by matching braces
    start = mixed_output.find("{")
    if start == -1:
        raise ValueError("No JSON object found in output")
    # Count braces to find the matching closing brace
    depth = 0
    for i in range(start, len(mixed_output)):
        if mixed_output[i] == "{":
            depth += 1
        elif mixed_output[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(mixed_output[start : i + 1])
    raise ValueError("Unterminated JSON object in output")


# ---------------------------------------------------------------------------
# Integration test (requires ffmpeg)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestTrimIntegration:
    """Integration tests that actually run ffmpeg on a known input."""

    @pytest.fixture
    def check_ffmpeg(self):
        """Skip if ffmpeg not available."""
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pytest.skip("ffmpeg not available")

    def test_trim_real_clip(self, tmp_path, check_ffmpeg):
        """Generate a small synthetic MP4, trim it, verify output exists."""
        # Generate a 3-second test video (solid color + test pattern)
        source = tmp_path / "source.mp4"
        gen_cmd = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", "testsrc=duration=3:size=320x240:rate=30",
            "-f", "lavfi",
            "-i", "sine=frequency=440:duration=3",
            "-shortest",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-y",
            str(source),
        ]
        subprocess.run(gen_cmd, capture_output=True, check=True)
        assert source.exists()

        plan = {
            "bpm": 120.0,
            "assignments": [
                {
                    "clip": str(source),
                    "source_start": 0.5,
                    "source_end": 2.0,
                    "target_start": 0.0,
                    "beat_index": 0,
                    "beat_count": 3,
                    "beat_energy": 0.5,
                    "motion_score": 0.8,
                }
            ],
        }

        out_dir = tmp_path / "trims"
        result = trim_plan(plan, output_dir=str(out_dir), verbose=False)

        assert result["_trim_summary"]["succeeded"] == 1
        assert result["_trim_summary"]["failed"] == 0

        trim_path = result["assignments"][0]["trim"]
        assert Path(trim_path).exists()

        # Verify trimmed file is valid MP4 with expected duration (~1.5s)
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            trim_path,
        ]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        actual_duration = float(probe.stdout.strip())
        assert 1.3 <= actual_duration <= 1.7, f"expected ~1.5s, got {actual_duration}s"

    def test_trim_with_failure(self, tmp_path, check_ffmpeg):
        """A nonexistent source file is reported as failure without crashing."""
        plan = {
            "bpm": 120.0,
            "assignments": [
                {
                    "clip": str(tmp_path / "does_not_exist.mp4"),
                    "source_start": 0.5,
                    "source_end": 2.0,
                    "target_start": 0.0,
                    "beat_index": 0,
                    "beat_count": 3,
                    "beat_energy": 0.5,
                    "motion_score": 0.8,
                },
                {
                    "clip": str(tmp_path / "also_missing.mp4"),
                    "source_start": 1.0,
                    "source_end": 3.0,
                    "target_start": 3.0,
                    "beat_index": 3,
                    "beat_count": 4,
                    "beat_energy": 0.5,
                    "motion_score": 0.6,
                },
            ],
        }

        out_dir = tmp_path / "trims"
        result = trim_plan(plan, output_dir=str(out_dir), verbose=False)

        assert result["_trim_summary"]["succeeded"] == 0
        assert result["_trim_summary"]["failed"] == 2
        assert len(result["_trim_summary"]["errors"]) == 2
        # Neither has trim key
        for a in result["assignments"]:
            assert "trim" not in a
