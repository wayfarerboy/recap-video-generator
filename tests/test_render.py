"""Tests for kdenlive XML generation (T7)."""

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

import click.testing
import pytest

from recap.cli import main
from recap.render import render_kdenlive, _probe_duration, _probe_dimensions
from recap.render import _relative_path, _float_to_fraction, _aspect_fraction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def landscape_plan():
    """Plan with two landscape clips."""
    return {
        "bpm": 120.0,
        "fps": 30.0,
        "assignments": [
            {
                "clip": "/fake/clips/ski.mp4",
                "trim": "/fake/trims/abc123.mp4",
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
                "trim": "/fake/trims/def456.mp4",
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
def portrait_plan():
    """Plan with one portrait clip."""
    return {
        "bpm": 120.0,
        "assignments": [
            {
                "clip": "/fake/clips/portrait.mp4",
                "trim": "/fake/trims/port.mp4",
                "source_start": 0.0,
                "source_end": 3.0,
                "target_start": 0.0,
                "beat_index": 0,
                "beat_count": 6,
                "beat_energy": 0.5,
                "motion_score": 0.9,
            },
        ],
    }


@pytest.fixture
def single_clip_plan():
    """Plan with a single landscape clip."""
    return {
        "bpm": 120.0,
        "assignments": [
            {
                "clip": "/fake/clips/lone.mp4",
                "trim": "/fake/trims/lone.mp4",
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
# XML structure tests
# ---------------------------------------------------------------------------

class TestXMLStructure:
    """Verify the generated XML conforms to the expected structure."""

    def test_well_formed_xml(self, landscape_plan):
        """Generated output is well-formed XML."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        assert tree.tag == "mlt"

    def test_document_version_1_1(self, landscape_plan):
        """The mlt root element has version="1.1"."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        assert tree.get("version") == "1.1"

    def test_profile_present_with_correct_dimensions(self, landscape_plan):
        """Profile has 1920x1080 for 16:9 output."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        profile = tree.find("profile")
        assert profile is not None
        assert profile.get("width") == "1920"
        assert profile.get("height") == "1080"
        assert profile.get("display_aspect_num") == "16"
        assert profile.get("display_aspect_den") == "9"

    def test_profile_9_16_dimensions(self, landscape_plan):
        """Profile has 1080x1920 for 9:16 output."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              output_ratio="9:16",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        profile = tree.find("profile")
        assert profile.get("width") == "1080"
        assert profile.get("height") == "1920"

    def test_has_tractor_with_two_tracks(self, landscape_plan):
        """Tractor has one video track and one audio track."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        tractor = tree.find("tractor")
        assert tractor is not None
        tracks = tractor.findall("track")
        assert len(tracks) == 2
        # First track references video playlist
        assert tracks[0].get("producer") == "playlist0"
        # Second track references audio playlist
        assert tracks[1].get("producer") == "playlist1"

    def test_main_bin_present(self, landscape_plan):
        """kdenlive main_bin playlist is present."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        main_bin = tree.find("playlist[@id='main_bin']")
        assert main_bin is not None


class TestProducerElements:
    """Verify producer elements are generated correctly."""

    def test_one_producer_per_clip_plus_music(self, landscape_plan):
        """Produces N+1 producers: N clips + 1 music."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        producers = tree.findall("producer")
        assert len(producers) == 3  # 2 clips + music

    def test_producers_have_resource_property(self, landscape_plan):
        """Each producer has a resource property pointing to the media file."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        producers = tree.findall("producer")
        for prod in producers:
            resource = prod.find("property[@name='resource']")
            assert resource is not None, f"Producer {prod.get('id')} missing resource"
            assert resource.text is not None

    def test_clip_producers_use_trim_path_when_available(self, landscape_plan):
        """Producers reference the trimmed file path."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        res0 = tree.find("producer[@id='producer0']/property[@name='resource']")
        assert res0 is not None
        assert "abc123.mp4" in res0.text

    def test_music_producer_has_id_producer_music(self, landscape_plan):
        """Music producer is named producer_music."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        music = tree.find("producer[@id='producer_music']")
        assert music is not None
        resource = music.find("property[@name='resource']")
        assert resource is not None
        assert "music" in resource.text


class TestTimelineEntries:
    """Verify video and audio playlist entries."""

    def test_video_playlist_has_correct_entry_count(self, landscape_plan):
        """Video playlist has one entry per clip."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        playlist0 = tree.find("playlist[@id='playlist0']")
        assert playlist0 is not None
        entries = playlist0.findall("entry")
        assert len(entries) == 2

    def test_video_entries_reference_correct_producers(self, landscape_plan):
        """Video entries point to the right producer IDs."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        playlist0 = tree.find("playlist[@id='playlist0']")
        entries = playlist0.findall("entry")
        assert entries[0].get("producer") == "producer0"
        assert entries[1].get("producer") == "producer1"

    def test_audio_playlist_has_music_entry(self, landscape_plan):
        """Audio playlist has one entry for the music producer."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        playlist1 = tree.find("playlist[@id='playlist1']")
        assert playlist1 is not None
        entries = playlist1.findall("entry")
        assert len(entries) == 1
        assert entries[0].get("producer") == "producer_music"

    def test_entry_in_out_are_integer_frame_numbers(self, landscape_plan):
        """Entry in/out attributes are integer frame numbers."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        playlist0 = tree.find("playlist[@id='playlist0']")
        for entry in playlist0.findall("entry"):
            in_val = int(entry.get("in", "-1"))
            out_val = int(entry.get("out", "-1"))
            assert in_val >= 0
            assert out_val >= in_val

    def test_video_entry_in_is_always_zero(self, landscape_plan):
        """Trimmed clips always start at source in=0."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        playlist0 = tree.find("playlist[@id='playlist0']")
        for entry in playlist0.findall("entry"):
            assert entry.get("in") == "0"


class TestTransforms:
    """Verify per-clip MLT transform filters."""

    def test_rotation_added_for_portrait_clip(self, portrait_plan):
        """Portrait orientation → affine filter with rotate=90."""
        xml = render_kdenlive(portrait_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/portrait.mp4": "portrait"})
        tree = ET.fromstring(xml)
        # Find the video entry for producer0
        playlist0 = tree.find("playlist[@id='playlist0']")
        entry = playlist0.find("entry")
        filters = entry.findall("filter")
        filter_texts = [ET.tostring(f, encoding="unicode") for f in filters]

        has_rotate = any("transition.rotate" in ft and "90" in ft for ft in filter_texts)
        assert has_rotate, f"Expected rotation filter, got: {filter_texts}"

    def test_no_rotation_for_landscape_clip(self, landscape_plan):
        """Landscape orientation → no rotation filter."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        playlist0 = tree.find("playlist[@id='playlist0']")
        entries = playlist0.findall("entry")
        for entry in entries:
            filters = entry.findall("filter")
            # No rotation filters
            for f in filters:
                props = f.findall("property")
                for p in props:
                    if p.get("name") == "transition.rotate":
                        pytest.fail(f"Unexpected rotation filter: {ET.tostring(f, encoding='unicode')}")

    def test_rotation_applied_even_without_dimension_probe(self):
        """Rotation is gated on orientation metadata, not dimension probe."""
        plan = {
            "bpm": 120,
            "assignments": [
                {
                    "clip": "/nonexistent/clip.mp4",
                    "trim": "/nonexistent/trim.mp4",
                    "source_start": 0.0,
                    "source_end": 3.0,
                    "target_start": 0.0,
                    "beat_index": 0,
                    "beat_count": 6,
                    "beat_energy": 0.5,
                    "motion_score": 0.9,
                },
            ],
        }
        xml = render_kdenlive(plan, music_path="/fake/music.mp3",
                              orientation_cache={"/nonexistent/clip.mp4": "portrait"})
        assert "transition.rotate" in xml
        assert "90" in xml


class TestEmptyPlan:
    """Empty / edge-case plans."""

    def test_empty_assignments_produces_valid_xml(self):
        """Plan with no assignments still produces valid XML with audio track."""
        plan = {"bpm": 120.0, "assignments": []}
        xml = render_kdenlive(plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        assert tree.tag == "mlt"
        # Should have music producer
        music = tree.find("producer[@id='producer_music']")
        assert music is not None
        # Should have audio playlist
        playlist1 = tree.find("playlist[@id='playlist1']")
        assert playlist1 is not None


class TestRatioFlag:
    """Output ratio handling."""

    def test_16_9_default(self, single_clip_plan):
        """Default is 16:9 → 1920x1080 profile."""
        xml = render_kdenlive(single_clip_plan, music_path="/fake/music.mp3",
                              orientation_cache={"/fake/clips/lone.mp4": "landscape"})
        tree = ET.fromstring(xml)
        profile = tree.find("profile")
        assert profile.get("width") == "1920"
        assert profile.get("height") == "1080"

    def test_9_16_explicit(self, single_clip_plan):
        """Explicit 9:16 → 1080x1920 profile."""
        xml = render_kdenlive(single_clip_plan, music_path="/fake/music.mp3",
                              output_ratio="9:16",
                              orientation_cache={"/fake/clips/lone.mp4": "landscape"})
        tree = ET.fromstring(xml)
        profile = tree.find("profile")
        assert profile.get("width") == "1080"
        assert profile.get("height") == "1920"

    def test_invalid_ratio_raises(self, single_clip_plan):
        """Unknown ratio raises ValueError."""
        with pytest.raises(ValueError, match="Unknown output ratio"):
            render_kdenlive(single_clip_plan, music_path="/fake/music.mp3",
                            output_ratio="4:3")


class TestRelativePaths:
    """Portable (relative) resource paths."""

    def test_relative_paths_when_output_dir_given(self, landscape_plan):
        """Resource paths are relative when output_dir is provided."""
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3",
                              output_dir="/fake/project",
                              orientation_cache={"/fake/clips/ski.mp4": "landscape",
                                                 "/fake/clips/jump.mp4": "landscape"})
        tree = ET.fromstring(xml)
        prod0 = tree.find("producer[@id='producer0']")
        resource = prod0.find("property[@name='resource']")
        # Path should be relative (no leading /)
        path = resource.text
        assert not path.startswith("/"), f"Expected relative path, got {path}"
        assert ".." in path or "trims" in path or "fake" in path

    def test_relative_path_helper(self):
        """_relative_path converts correctly."""
        result = _relative_path("/a/b/c.mp4", "/a")
        assert result == "b/c.mp4"

    def test_relative_path_sibling(self):
        """_relative_path handles sibling directories."""
        result = _relative_path("/a/b/c.mp4", "/a/d")
        assert result == "../b/c.mp4"


class TestUtilityFunctions:
    """Unit tests for utility helpers."""

    def test_aspect_fraction_16_9(self):
        num, den = _aspect_fraction(1920, 1080)
        assert num == 16
        assert den == 9

    def test_aspect_fraction_9_16(self):
        num, den = _aspect_fraction(1080, 1920)
        assert num == 9
        assert den == 16

    def test_float_to_fraction_30(self):
        num, den = _float_to_fraction(30.0)
        assert num == 30
        assert den == 1

    def test_float_to_fraction_2997(self):
        """29.97 → 30000/1001."""
        num, den = _float_to_fraction(29.97)
        assert num == 30000
        assert den == 1001

    def test_float_to_fraction_23976(self):
        """23.976 → 24000/1001."""
        num, den = _float_to_fraction(23.976)
        assert num == 24000
        assert den == 1001

    def test_probe_dimensions_nonexistent(self):
        """_probe_dimensions returns (0,0) for nonexistent files."""
        w, h = _probe_dimensions("/nonexistent/file.mp4")
        assert w == 0
        assert h == 0

    def test_probe_duration_nonexistent(self):
        """_probe_duration returns fallback for nonexistent files."""
        dur = _probe_duration("/nonexistent/audio.mp3")
        assert dur == 10.0


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

class TestCLIRender:
    """Integration tests for `recap render` CLI subcommand."""

    def test_render_command_in_help(self):
        """`recap --help` lists the render subcommand."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "render" in result.output

    def test_render_requires_plan(self):
        """`recap render` without --plan shows error."""
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["render"])
        assert result.exit_code != 0

    def test_render_requires_music(self):
        """`recap render --plan ...` without --music shows error."""
        with tempfile.TemporaryDirectory() as td:
            plan_file = Path(td) / "plan.json"
            plan_file.write_text(json.dumps({"bpm": 120, "assignments": []}))
            runner = click.testing.CliRunner()
            result = runner.invoke(main, ["render", "--plan", str(plan_file)])
            assert result.exit_code != 0

    def test_render_writes_output_file(self, tmp_path):
        """End-to-end: render writes a .kdenlive file."""
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps({
            "bpm": 120,
            "assignments": [
                {
                    "clip": str(tmp_path / "clip0.mp4"),
                    "trim": str(tmp_path / "trim0.mp4"),
                    "source_start": 2.5,
                    "source_end": 5.5,
                    "target_start": 0.0,
                    "beat_index": 0,
                    "beat_count": 6,
                    "beat_energy": 0.5,
                    "motion_score": 0.9,
                },
            ],
        }))

        # Create a dummy music file
        music_file = tmp_path / "music.wav"
        _generate_silent_wav(music_file, duration=1.0)

        out_file = tmp_path / "recap.kdenlive"
        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "render",
            "--plan", str(plan_file),
            "--music", str(music_file),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0, f"stderr: {result.output}"
        assert out_file.exists()

        # Verify content
        content = out_file.read_text()
        tree = ET.fromstring(content)
        assert tree.get("version") == "1.1"
        assert tree.find("producer[@id='producer_music']") is not None

    def test_render_with_ratio_flag(self, tmp_path):
        """Render respects --ratio flag."""
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps({
            "bpm": 120,
            "assignments": [
                {
                    "clip": str(tmp_path / "clip0.mp4"),
                    "source_start": 0,
                    "source_end": 3,
                    "target_start": 0,
                    "beat_index": 0,
                    "beat_count": 6,
                    "beat_energy": 0.5,
                    "motion_score": 0.8,
                },
            ],
        }))
        music_file = tmp_path / "music.wav"
        _generate_silent_wav(music_file, duration=1.0)
        out_file = tmp_path / "out.kdenlive"

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "render", "--plan", str(plan_file), "--music", str(music_file),
            "-o", str(out_file), "--ratio", "9:16",
        ])
        assert result.exit_code == 0
        content = out_file.read_text()
        tree = ET.fromstring(content)
        profile = tree.find("profile")
        assert profile.get("width") == "1080"
        assert profile.get("height") == "1920"

    def test_render_default_output_path(self, tmp_path):
        """Default -o is recap.kdenlive in CWD."""
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps({
            "bpm": 120,
            "assignments": [
                {
                    "clip": str(tmp_path / "c.mp4"),
                    "source_start": 0,
                    "source_end": 2,
                    "target_start": 0,
                    "beat_index": 0,
                    "beat_count": 4,
                    "beat_energy": 0.5,
                    "motion_score": 0.8,
                },
            ],
        }))
        music_file = tmp_path / "m.wav"
        _generate_silent_wav(music_file, duration=1.0)

        import os
        cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            runner = click.testing.CliRunner()
            result = runner.invoke(main, [
                "render", "--plan", str(plan_file), "--music", str(music_file),
            ])
            assert result.exit_code == 0
            out = tmp_path / "recap.kdenlive"
            assert out.exists()
        finally:
            os.chdir(cwd)

    def test_render_invalid_plan_json(self, tmp_path):
        """Malformed JSON in plan file gives error."""
        plan_file = tmp_path / "bad.json"
        plan_file.write_text("not json {{{")
        music_file = tmp_path / "m.wav"
        _generate_silent_wav(music_file, duration=1.0)

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "render", "--plan", str(plan_file), "--music", str(music_file),
        ])
        assert result.exit_code != 0

    def test_render_plan_missing_assignments(self, tmp_path):
        """Plan without assignments key is handled gracefully."""
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps({"bpm": 120}))
        music_file = tmp_path / "m.wav"
        _generate_silent_wav(music_file, duration=1.0)

        out_file = tmp_path / "out.kdenlive"
        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "render", "--plan", str(plan_file), "--music", str(music_file),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0
        assert out_file.exists()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_silent_wav(path: Path, duration: float = 1.0):
    """Generate a tiny silent WAV file for CLI tests."""
    cmd = [
        "ffmpeg", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-y", str(path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=10)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        # Fallback: write a minimal valid WAV header + silence
        import struct
        sample_rate = 44100
        num_samples = int(sample_rate * duration)
        data_size = num_samples * 2  # 16-bit mono
        with open(path, "wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + data_size))
            f.write(b"WAVE")
            f.write(b"fmt ")
            f.write(struct.pack("<I", 16))       # chunk size
            f.write(struct.pack("<H", 1))        # PCM
            f.write(struct.pack("<H", 1))        # mono
            f.write(struct.pack("<I", sample_rate))
            f.write(struct.pack("<I", sample_rate * 2))  # byte rate
            f.write(struct.pack("<H", 2))        # block align
            f.write(struct.pack("<H", 16))       # bits per sample
            f.write(b"data")
            f.write(struct.pack("<I", data_size))
            f.write(b"\x00" * data_size)
