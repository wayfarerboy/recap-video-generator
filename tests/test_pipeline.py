"""Tests for the pipeline orchestration module."""

import json
from pathlib import Path
from unittest import mock

import pytest

from recap.pipeline import (
    PipelineConfig,
    PipelineResult,
    PipelineWarning,
    _load_or_cache_beats,
    run,
)
from recap.plan import Plan, Assignment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_beat_analysis(bpm=120.0, num_beats=32):
    beat_interval = 60.0 / bpm
    beats = [i * beat_interval for i in range(num_beats)]
    energy = [round(0.5 + 0.3 * (i % 4) / 4, 4) for i in range(num_beats)]
    return {"bpm": bpm, "beats": beats, "energy": energy}


def _make_clip_analyses(num_clips=3):
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
        ],
    )


# ---------------------------------------------------------------------------
# PipelineConfig tests
# ---------------------------------------------------------------------------

class TestPipelineConfig:
    def test_all_11_parameters(self):
        """PipelineConfig accepts all 11 parameters."""
        config = PipelineConfig(
            clips_dir=Path("/tmp/clips"),
            music_path=Path("/tmp/music.mp3"),
            mode="best-match",
            ratio="9:16",
            seed=99,
            force=True,
            fps=30.0,
            transcode=False,
            min_beats=6,
            max_beats=10,
            output_path=Path("/tmp/out.kdenlive"),
        )
        assert config.clips_dir == Path("/tmp/clips")
        assert config.music_path == Path("/tmp/music.mp3")
        assert config.mode == "best-match"
        assert config.ratio == "9:16"
        assert config.seed == 99
        assert config.force is True
        assert config.fps == 30.0
        assert config.transcode is False
        assert config.min_beats == 6
        assert config.max_beats == 10
        assert config.output_path == Path("/tmp/out.kdenlive")

    def test_defaults(self):
        """PipelineConfig has sensible defaults."""
        config = PipelineConfig(
            clips_dir=Path("/tmp/clips"),
            music_path=Path("/tmp/music.mp3"),
        )
        assert config.mode == "shuffled-tiers"
        assert config.ratio == "16:9"
        assert config.seed == 42
        assert config.force is False
        assert config.fps == 25.0
        assert config.transcode is True
        assert config.min_beats == 4
        assert config.max_beats == 8
        assert config.output_path is None


# ---------------------------------------------------------------------------
# PipelineResult tests
# ---------------------------------------------------------------------------

class TestPipelineResult:
    def test_plan_required(self):
        plan = Plan(bpm=120.0)
        result = PipelineResult(plan=plan)
        assert result.plan == plan
        assert result.kdenlive_path is None
        assert result.warnings == []

    def test_with_kdenlive_path(self):
        plan = Plan(bpm=120.0)
        result = PipelineResult(
            plan=plan,
            kdenlive_path=Path("/tmp/out.kdenlive"),
        )
        assert result.kdenlive_path == Path("/tmp/out.kdenlive")

    def test_with_warnings(self):
        plan = Plan(bpm=120.0)
        w = PipelineWarning(stage="analyze_clips", message="err", source="f.mp4")
        result = PipelineResult(plan=plan, warnings=[w])
        assert len(result.warnings) == 1
        assert result.warnings[0].stage == "analyze_clips"


# ---------------------------------------------------------------------------
# PipelineWarning tests
# ---------------------------------------------------------------------------

class TestPipelineWarning:
    def test_construction(self):
        w = PipelineWarning(stage="transcode", message="fail", source="clip.mp4")
        assert w.stage == "transcode"
        assert w.message == "fail"
        assert w.source == "clip.mp4"

    def test_source_optional(self):
        w = PipelineWarning(stage="analyze_clips", message="oops")
        assert w.source is None


# ---------------------------------------------------------------------------
# Music beat caching tests
# ---------------------------------------------------------------------------

class TestLoadOrCacheBeats:
    def test_creates_cache_on_first_call(self, tmp_path):
        """First call detects beats, writes cache file."""
        beat_data = _make_beat_analysis()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        with mock.patch("recap.audio.detect_beats", return_value=beat_data):
            result = _load_or_cache_beats(music_path, tmp_path, force=False)

        assert result == beat_data
        cache_path = tmp_path / ".recap-cache" / "song_beats.json"
        assert cache_path.exists()
        cached = json.loads(cache_path.read_text())
        assert cached == beat_data

    def test_uses_cache_on_second_call(self, tmp_path):
        """Second call reads from cache without calling detect_beats."""
        # Pre-populate cache
        cache_dir = tmp_path / ".recap-cache"
        cache_dir.mkdir()
        cache_path = cache_dir / "song_beats.json"
        cached_data = {"bpm": 140.0, "beats": [0.0, 0.43], "energy": [0.5, 0.5]}
        cache_path.write_text(json.dumps(cached_data))

        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        detect_calls = []

        def fake_detect(fp):
            detect_calls.append(fp)
            return {"bpm": 120.0, "beats": [0.0, 0.5], "energy": [0.5]}

        with mock.patch("recap.audio.detect_beats", side_effect=fake_detect):
            result = _load_or_cache_beats(music_path, tmp_path, force=False)

        assert len(detect_calls) == 0  # cached
        assert result == cached_data

    def test_force_ignores_cache(self, tmp_path):
        """force=True re-detects even when cache exists."""
        # Pre-populate cache
        cache_dir = tmp_path / ".recap-cache"
        cache_dir.mkdir()
        cache_path = cache_dir / "song_beats.json"
        cache_path.write_text(json.dumps({"bpm": 140.0, "beats": [], "energy": []}))

        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        beat_data = _make_beat_analysis()

        with mock.patch("recap.audio.detect_beats", return_value=beat_data):
            result = _load_or_cache_beats(music_path, tmp_path, force=True)

        assert result == beat_data

    def test_cache_key_uses_stem(self, tmp_path):
        """Cache filename is based on music file stem."""
        music_path = tmp_path / "my-song.mp3"
        music_path.write_text("fake")

        beat_data = _make_beat_analysis()

        with mock.patch("recap.audio.detect_beats", return_value=beat_data):
            _load_or_cache_beats(music_path, tmp_path, force=False)

        cache_path = tmp_path / ".recap-cache" / "my-song_beats.json"
        assert cache_path.exists()


# ---------------------------------------------------------------------------
# run() tests
# ---------------------------------------------------------------------------

class TestRun:
    def test_orchestrates_all_stages(self, tmp_path):
        """run() calls analyze_directory, detect_beats, assign_clips, render_kdenlive."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")
        output_path = tmp_path / "out.kdenlive"

        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
            output_path=output_path,
            transcode=False,
        )

        beat_data = _make_beat_analysis()
        clip_data = _make_clip_analyses(2)
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        call_order = []

        def fake_analyze_directory(directory, force=False, transcode=False):
            call_order.append("analyze_directory")
            return clip_data

        def fake_detect_beats(fp):
            call_order.append("detect_beats")
            return beat_data

        def fake_assign_clips(beat_analysis, clip_analyses, **kwargs):
            call_order.append("assign_clips")
            return assign_plan

        def fake_render_kdenlive(plan, music_path, output_ratio="16:9", **kwargs):
            call_order.append("render_kdenlive")
            return "<xml/>"

        with mock.patch("recap.batch.analyze_directory", side_effect=fake_analyze_directory), \
             mock.patch("recap.audio.detect_beats", side_effect=fake_detect_beats), \
             mock.patch("recap.assign.assign_clips", side_effect=fake_assign_clips), \
             mock.patch("recap.render.render_kdenlive", side_effect=fake_render_kdenlive):
            result = run(config)

        assert result.plan == assign_plan
        assert result.kdenlive_path == output_path
        assert result.warnings == []
        assert output_path.exists()
        assert output_path.read_text() == "<xml/>"

        assert call_order == [
            "analyze_directory",
            "detect_beats",
            "assign_clips",
            "render_kdenlive",
        ]

    def test_stop_after_assign(self, tmp_path):
        """stop_after='assign' stops before render."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
            transcode=False,
        )

        beat_data = _make_beat_analysis()
        clip_data = _make_clip_analyses(2)
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        render_called = []

        def fake_analyze_directory(directory, force=False, transcode=False):
            return clip_data

        def fake_detect_beats(fp):
            return beat_data

        def fake_assign_clips(beat_analysis, clip_analyses, **kwargs):
            return assign_plan

        def fake_render_kdenlive(*args, **kwargs):
            render_called.append(True)
            return "<xml/>"

        with mock.patch("recap.batch.analyze_directory", side_effect=fake_analyze_directory), \
             mock.patch("recap.audio.detect_beats", side_effect=fake_detect_beats), \
             mock.patch("recap.assign.assign_clips", side_effect=fake_assign_clips), \
             mock.patch("recap.render.render_kdenlive", side_effect=fake_render_kdenlive):
            result = run(config, stop_after="assign")

        assert result.plan == assign_plan
        assert result.kdenlive_path is None
        assert len(render_called) == 0  # render not called

    def test_no_clips_raises(self, tmp_path):
        """No clips results raises ValueError."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
        )

        beat_data = _make_beat_analysis()

        with mock.patch("recap.batch.analyze_directory") as fake_analyze, \
             mock.patch("recap.audio.detect_beats") as fake_beats:
            fake_analyze.return_value = {
                "processed": 0,
                "skipped": 0,
                "errors": [],
                "results": {},
            }
            fake_beats.return_value = beat_data

            with pytest.raises(ValueError, match="No clips found"):
                run(config)

    def test_collects_clip_analysis_warnings(self, tmp_path):
        """Clip analysis errors appear in PipelineResult.warnings."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
            transcode=False,
        )

        beat_data = _make_beat_analysis()
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        with mock.patch("recap.batch.analyze_directory") as fake_analyze, \
             mock.patch("recap.audio.detect_beats") as fake_beats, \
             mock.patch("recap.assign.assign_clips") as fake_assign, \
             mock.patch("recap.render.render_kdenlive") as fake_render:
            fake_analyze.return_value = {
                "processed": 1,
                "skipped": 0,
                "errors": [
                    {"file": "broken.mp4", "error": "Cannot decode"},
                ],
                "results": _make_clip_analyses(1)["results"],
            }
            fake_beats.return_value = beat_data
            fake_assign.return_value = assign_plan
            fake_render.return_value = "<xml/>"

            result = run(config)

        assert len(result.warnings) == 1
        w = result.warnings[0]
        assert w.stage == "analyze_clips"
        assert w.message == "Cannot decode"
        assert w.source == "broken.mp4"

    def test_collects_transcode_warnings(self, tmp_path):
        """Transcode errors appear in PipelineResult.warnings."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
            transcode=True,
        )

        beat_data = _make_beat_analysis()
        assign_plan = _make_assignment_plan(clips_dir=str(clips_dir))

        with mock.patch("recap.batch.analyze_directory") as fake_analyze, \
             mock.patch("recap.audio.detect_beats") as fake_beats, \
             mock.patch("recap.assign.assign_clips") as fake_assign, \
             mock.patch("recap.render.render_kdenlive") as fake_render:
            fake_analyze.return_value = {
                "processed": 2,
                "skipped": 0,
                "errors": [],
                "results": _make_clip_analyses(2)["results"],
                "transcode": {
                    "new": 2,
                    "skipped": 0,
                    "errors": [
                        {"file": "bad.mp4", "error": "ffmpeg crashed"},
                    ],
                },
            }
            fake_beats.return_value = beat_data
            fake_assign.return_value = assign_plan
            fake_render.return_value = "<xml/>"

            result = run(config)

        transcode_warnings = [w for w in result.warnings if w.stage == "transcode"]
        assert len(transcode_warnings) == 1
        assert transcode_warnings[0].source == "bad.mp4"
        assert transcode_warnings[0].message == "ffmpeg crashed"

    def test_passes_config_to_assign(self, tmp_path):
        """PipelineConfig.mode, seed, min_beats, max_beats reach assign_clips."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
            mode="best-match",
            seed=123,
            min_beats=5,
            max_beats=7,
            transcode=False,
        )

        beat_data = _make_beat_analysis()
        clip_data = _make_clip_analyses(2)
        assign_plan = _make_assignment_plan()

        assign_kwargs = {}

        def fake_assign_clips(beat_analysis, clip_analyses, **kwargs):
            nonlocal assign_kwargs
            assign_kwargs = kwargs
            return assign_plan

        with mock.patch("recap.batch.analyze_directory", return_value=clip_data), \
             mock.patch("recap.audio.detect_beats", return_value=beat_data), \
             mock.patch("recap.assign.assign_clips", side_effect=fake_assign_clips), \
             mock.patch("recap.render.render_kdenlive", return_value="<xml/>"):
            run(config)

        assert assign_kwargs["mode"] == "best-match"
        assert assign_kwargs["seed"] == 123
        assert assign_kwargs["min_beats"] == 5
        assert assign_kwargs["max_beats"] == 7

    def test_transcode_flag_passed_to_analyze_directory(self, tmp_path):
        """config.transcode=True passes transcode=True to analyze_directory."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
            transcode=True,
        )

        analyze_kwargs = {}

        def fake_analyze_directory(directory, force=False, transcode=False):
            nonlocal analyze_kwargs
            analyze_kwargs = {"force": force, "transcode": transcode}
            return _make_clip_analyses(1)

        with mock.patch("recap.batch.analyze_directory", side_effect=fake_analyze_directory), \
             mock.patch("recap.audio.detect_beats", return_value=_make_beat_analysis()), \
             mock.patch("recap.assign.assign_clips", return_value=_make_assignment_plan()), \
             mock.patch("recap.render.render_kdenlive", return_value="<xml/>"):
            run(config)

        assert analyze_kwargs["transcode"] is True

    def test_transcode_false_passed_when_disabled(self, tmp_path):
        """config.transcode=False passes transcode=False to analyze_directory."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
            transcode=False,
        )

        analyze_kwargs = {}

        def fake_analyze_directory(directory, force=False, transcode=False):
            nonlocal analyze_kwargs
            analyze_kwargs = {"force": force, "transcode": transcode}
            return _make_clip_analyses(1)

        with mock.patch("recap.batch.analyze_directory", side_effect=fake_analyze_directory), \
             mock.patch("recap.audio.detect_beats", return_value=_make_beat_analysis()), \
             mock.patch("recap.assign.assign_clips", return_value=_make_assignment_plan()), \
             mock.patch("recap.render.render_kdenlive", return_value="<xml/>"):
            run(config)

        assert analyze_kwargs["transcode"] is False

    def test_passes_ratio_and_fps_to_render(self, tmp_path):
        """PipelineConfig.ratio and fps reach render_kdenlive."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
            ratio="9:16",
            fps=30.0,
            transcode=False,
        )

        render_kwargs = {}

        def fake_render_kdenlive(plan, music_path, output_ratio="16:9", **kwargs):
            nonlocal render_kwargs
            render_kwargs = {"output_ratio": output_ratio, **kwargs}
            return "<xml/>"

        with mock.patch("recap.batch.analyze_directory", return_value=_make_clip_analyses(1)), \
             mock.patch("recap.audio.detect_beats", return_value=_make_beat_analysis()), \
             mock.patch("recap.assign.assign_clips", return_value=_make_assignment_plan()), \
             mock.patch("recap.render.render_kdenlive", side_effect=fake_render_kdenlive):
            run(config)

        assert render_kwargs["output_ratio"] == "9:16"
        assert render_kwargs["fps"] == 30.0

    def test_force_passed_to_analyze_and_beats(self, tmp_path):
        """config.force=True propagates to both analyze_directory and beat caching."""
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        music_path = tmp_path / "song.mp3"
        music_path.write_text("fake")

        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
            force=True,
            transcode=False,
        )

        analyze_force = None
        beat_calls = []

        def fake_analyze_directory(directory, force=False, transcode=False):
            nonlocal analyze_force
            analyze_force = force
            return _make_clip_analyses(1)

        def fake_detect_beats(fp):
            beat_calls.append(fp)
            return _make_beat_analysis()

        with mock.patch("recap.batch.analyze_directory", side_effect=fake_analyze_directory), \
             mock.patch("recap.audio.detect_beats", side_effect=fake_detect_beats), \
             mock.patch("recap.assign.assign_clips", return_value=_make_assignment_plan()), \
             mock.patch("recap.render.render_kdenlive", return_value="<xml/>"):
            run(config)

        assert analyze_force is True
        assert len(beat_calls) == 1  # force=True so detect_beats is called
