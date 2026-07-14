"""Tests for kdenlive XML generation (T7)."""

import json
import subprocess
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import click.testing
import pytest

from recap.cli import main
from recap.render import render_kdenlive, _probe_duration, _probe_dimensions
from recap.render import _float_to_fraction, _aspect_fraction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def landscape_plan():
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
def single_clip_plan():
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
    def test_well_formed_xml(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        assert tree.tag == "mlt"

    def test_mlt_root_attributes(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        assert tree.get("LC_NUMERIC") == "C"
        assert tree.get("producer") == "main_bin"
        assert tree.get("version") == "7.37.0"

    def test_profile_present(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        profile = tree.find("profile")
        assert profile is not None
        assert profile.get("width") == "1920"
        assert profile.get("height") == "1080"

    def test_profile_9_16(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3", output_ratio="9:16")
        tree = ET.fromstring(xml)
        profile = tree.find("profile")
        assert profile.get("width") == "1080"
        assert profile.get("height") == "1920"

    def test_has_tractors(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        tractors = tree.findall("tractor")
        # Should have at least tractor0 (timeline) and tractor1 (wrapper)
        assert len(tractors) >= 2

    def test_main_bin_present(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        main_bin = tree.find("playlist[@id='main_bin']")
        assert main_bin is not None

    def test_docproperties_present(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        main_bin = tree.find("playlist[@id='main_bin']")
        dp = main_bin.find("property[@name='kdenlive:docproperties.kdenliveversion']")
        assert dp is not None
        assert dp.text == "23.04.0"


class TestChainElements:
    """Verify chain elements (kdenlive media sources)."""

    def test_chains_for_all_clips_plus_music(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        chains = tree.findall("chain")
        assert len(chains) == 3  # 2 clips + music

    def test_chains_have_control_uuid(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        for chain in ET.fromstring(xml).findall("chain"):
            cu = chain.find("property[@name='kdenlive:control_uuid']")
            assert cu is not None
            assert cu.text.startswith("{") and cu.text.endswith("}")

    def test_chains_have_kdenlive_id(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        ids = []
        for chain in ET.fromstring(xml).findall("chain"):
            kid = chain.find("property[@name='kdenlive:id']")
            assert kid is not None
            ids.append(int(kid.text))
        assert ids == sorted(ids)  # sequential

    def test_clip_chains_have_resource(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        chains = ET.fromstring(xml).findall("chain")
        video_chains = [c for c in chains if "avformat-novalidate" in
                        (c.find("property[@name='mlt_service']").text or "")]
        assert len(video_chains) == 2

    def test_music_chain_has_audio_service(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        chains = ET.fromstring(xml).findall("chain")
        audio_chains = [c for c in chains if
                        c.find("property[@name='mlt_service']").text == "avformat"]
        assert len(audio_chains) == 1

    def test_out_is_timecode_format(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        for chain in ET.fromstring(xml).findall("chain"):
            out = chain.get("out")
            assert ":" in out  # HH:MM:SS.fff
            assert "." in out


class TestTimelineEntries:
    """Verify video and audio playlist entries."""

    def test_video_playlist_entries(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        p0 = tree.find("playlist[@id='playlist0']")
        entries = p0.findall("entry")
        assert len(entries) == 2

    def test_entry_in_out_are_timecodes(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        p0 = tree.find("playlist[@id='playlist0']")
        for entry in p0.findall("entry"):
            assert ":" in entry.get("in")
            assert ":" in entry.get("out")
            assert "." in entry.get("out")

    def test_entries_reference_chain_producers(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        p0 = tree.find("playlist[@id='playlist0']")
        for entry in p0.findall("entry"):
            assert entry.get("producer", "").startswith("chain")

    def test_audio_playlist_has_entry(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        p1 = tree.find("playlist[@id='playlist1']")
        entries = p1.findall("entry")
        assert len(entries) == 1

    def test_tractor_tracks_hide(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        tractor0 = tree.find("tractor[@id='tractor0']")
        tracks = tractor0.findall("track")
        assert len(tracks) == 2
        # Video track hides audio, audio track hides video (kdenlive convention)
        assert tracks[0].get("hide") == "audio"
        assert tracks[1].get("hide") == "video"

    def test_wrapper_tractor_has_track(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        wrapper = tree.find("tractor[@id='tractor1']")
        assert wrapper is not None
        wrapper_tracks = wrapper.findall("track")
        assert len(wrapper_tracks) == 1
        assert wrapper_tracks[0].get("producer") == "tractor0"


class TestTractorProperties:
    """Verify sequence properties on tractor0."""

    def test_tractor_has_uuid(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        tractor = tree.find("tractor[@id='tractor0']")
        uuid_prop = tractor.find("property[@name='kdenlive:uuid']")
        assert uuid_prop is not None
        assert uuid_prop.text.startswith("{")

    def test_tractor_has_sequence_properties(self, landscape_plan):
        xml = render_kdenlive(landscape_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        tractor = tree.find("tractor[@id='tractor0']")
        assert tractor.find("property[@name='kdenlive:sequenceproperties.hasVideo']") is not None
        assert tractor.find("property[@name='kdenlive:sequenceproperties.hasAudio']") is not None


class TestEmptyPlan:
    def test_empty_assignments_valid_xml(self):
        plan = {"bpm": 120.0, "assignments": []}
        xml = render_kdenlive(plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        assert tree.tag == "mlt"
        # Should still have music chain and audio playlist
        assert tree.find("playlist[@id='playlist1']") is not None


class TestRatioFlag:
    def test_16_9_default(self, single_clip_plan):
        xml = render_kdenlive(single_clip_plan, music_path="/fake/music.mp3")
        tree = ET.fromstring(xml)
        profile = tree.find("profile")
        assert profile.get("width") == "1920"
        assert profile.get("height") == "1080"

    def test_9_16_explicit(self, single_clip_plan):
        xml = render_kdenlive(single_clip_plan, music_path="/fake/music.mp3", output_ratio="9:16")
        tree = ET.fromstring(xml)
        profile = tree.find("profile")
        assert profile.get("width") == "1080"
        assert profile.get("height") == "1920"

    def test_invalid_ratio_raises(self, single_clip_plan):
        with pytest.raises(ValueError, match="Unknown output ratio"):
            render_kdenlive(single_clip_plan, music_path="/fake/music.mp3", output_ratio="4:3")


class TestUtilityFunctions:
    def test_aspect_fraction_16_9(self):
        num, den = _aspect_fraction(1920, 1080)
        assert num == 16
        assert den == 9

    def test_float_to_fraction_30(self):
        num, den = _float_to_fraction(30.0)
        assert num == 30
        assert den == 1

    def test_float_to_fraction_2997(self):
        num, den = _float_to_fraction(29.97)
        assert num == 30000
        assert den == 1001

    def test_probe_dimensions_nonexistent(self):
        w, h = _probe_dimensions("/nonexistent/file.mp4")
        assert w == 0
        assert h == 0

    def test_probe_duration_nonexistent(self):
        dur = _probe_duration("/nonexistent/audio.mp3")
        assert dur == 10.0


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLIRender:
    def test_render_command_in_help(self):
        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "render" in result.output

    def test_render_writes_output(self, tmp_path):
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
        music_file = tmp_path / "music.wav"
        _generate_silent_wav(music_file)
        out_file = tmp_path / "recap.kdenlive"

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "render", "--plan", str(plan_file), "--music", str(music_file),
            "-o", str(out_file),
        ])
        assert result.exit_code == 0, f"stderr: {result.output}"
        assert out_file.exists()
        content = out_file.read_text()
        assert "control_uuid" in content
        assert "kdenlive:docproperties" in content

    def test_render_with_ratio(self, tmp_path):
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps({
            "bpm": 120,
            "assignments": [
                {"clip": str(tmp_path / "c.mp4"), "source_start": 0, "source_end": 3,
                 "target_start": 0, "beat_index": 0, "beat_count": 6,
                 "beat_energy": 0.5, "motion_score": 0.8},
            ],
        }))
        music_file = tmp_path / "m.wav"
        _generate_silent_wav(music_file)
        out_file = tmp_path / "out.kdenlive"

        runner = click.testing.CliRunner()
        result = runner.invoke(main, [
            "render", "--plan", str(plan_file), "--music", str(music_file),
            "-o", str(out_file), "--ratio", "9:16",
        ])
        assert result.exit_code == 0
        content = out_file.read_text()
        assert 'width="1080"' in content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_silent_wav(path: Path, duration: float = 1.0):
    try:
        subprocess.run(
            ["ffmpeg", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
             "-y", str(path)],
            capture_output=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        import struct
        sample_rate = 44100
        num_samples = int(sample_rate * duration)
        data_size = num_samples * 2
        with open(path, "wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + data_size))
            f.write(b"WAVE")
            f.write(b"fmt ")
            f.write(struct.pack("<I", 16))
            f.write(struct.pack("<H", 1))
            f.write(struct.pack("<H", 1))
            f.write(struct.pack("<I", sample_rate))
            f.write(struct.pack("<I", sample_rate * 2))
            f.write(struct.pack("<H", 2))
            f.write(struct.pack("<H", 16))
            f.write(b"data")
            f.write(struct.pack("<I", data_size))
            f.write(b"\x00" * data_size)
