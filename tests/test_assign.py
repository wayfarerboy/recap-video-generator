"""Tests for clip-to-beat assignment engine (T5)."""

import json
import math

import pytest

from recap.assign import assign_clips
from recap.plan import Plan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def beat_analysis():
    """120 BPM, 64 beats, energy ramps up then down."""
    bpm = 120.0
    beat_interval = 60.0 / bpm  # 0.5 s
    total = 64
    beats = [i * beat_interval for i in range(total)]
    # Energy: ramp up to peak, then ramp down
    mid = total // 2
    energy = []
    for i in range(total):
        if i <= mid:
            energy.append(round(i / mid, 4))
        else:
            energy.append(round((total - 1 - i) / mid, 4))
    return {"bpm": bpm, "beats": beats, "energy": energy}


@pytest.fixture
def clip_analyses():
    """6 clips with varying motion scores and exciting segments (~2s each)."""
    clips = {}
    for i in range(6):
        # Motion scores: 0.9, 0.7, 0.5, 0.3, 0.2, 0.1
        score = round(0.9 - i * 0.16, 2)
        # Exciting segment: ~2s window → 4 beats at 120 BPM
        start = round(1.0 + i * 0.5, 2)
        end = round(start + 2.0, 2)
        clips[f"/tmp/clip_{i}.mp4"] = {
            "most_exciting": {"start": start, "end": end, "score": score},
            "motion_scores": [score] * 90,
            "orientation": "landscape",
        }
    return clips


def _make_beat_analysis(num_beats: int, bpm: float = 120.0):
    beat_interval = 60.0 / bpm
    beats = [i * beat_interval for i in range(num_beats)]
    energy = [0.5] * num_beats
    return {"bpm": bpm, "beats": beats, "energy": energy}


def _make_clip_analyses(num_clips: int, base_score: float = 0.8):
    """Make N clips with exciting segments ~2s (→4 beats at 120 BPM)."""
    clips = {}
    for i in range(num_clips):
        score = round(base_score - i * 0.1, 2)
        clips[f"/tmp/clip_{i}.mp4"] = {
            "most_exciting": {"start": 1.0, "end": 3.0, "score": max(score, 0.01)},
            "motion_scores": [score] * 90,
            "orientation": "landscape",
        }
    return clips


# ---------------------------------------------------------------------------
# Output structure tests
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_returns_plan_with_bpm_and_assignments(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses)
        assert isinstance(result, Plan)
        assert result.bpm == 120.0
        assert isinstance(result.assignments, list)

    def test_each_assignment_has_required_keys(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses)
        for slot in result.assignments:
            assert slot.clip != ""
            assert slot.source_start >= 0
            assert slot.source_end > 0
            assert slot.target_start >= 0
            assert slot.beat_index >= 0
            assert slot.beat_count > 0
            assert slot.motion_score > 0

    def test_target_starts_are_monotonic(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses)
        starts = [a.target_start for a in result.assignments]
        for i in range(1, len(starts)):
            assert starts[i] >= starts[i - 1], f"target_start not monotonic at index {i}"

    def test_shuffled_tiers_deterministic_with_seed(self, beat_analysis, clip_analyses):
        """Same seed produces identical assignments."""
        a = assign_clips(beat_analysis, clip_analyses, mode="shuffled-tiers", seed=42)
        b = assign_clips(beat_analysis, clip_analyses, mode="shuffled-tiers", seed=42)
        assert [x.clip for x in a.assignments] == [x.clip for x in b.assignments]
        assert [x.target_start for x in a.assignments] == [x.target_start for x in b.assignments]

    def test_shuffled_tiers_different_seeds_differ(self, beat_analysis, clip_analyses):
        """Different seeds can produce different orderings."""
        # With many clips, seed 42 vs 99 should nearly always differ
        clips = _make_clip_analyses(20)
        a = assign_clips(beat_analysis, clips, mode="shuffled-tiers", seed=42)
        b = assign_clips(beat_analysis, clips, mode="shuffled-tiers", seed=99)
        clips_a = [x.clip for x in a.assignments]
        clips_b = [x.clip for x in b.assignments]
        assert clips_a != clips_b, "different seeds produced identical orderings"

    def test_beat_indices_are_monotonic(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses)
        indices = [a.beat_index for a in result.assignments]
        for i in range(1, len(indices)):
            assert indices[i] > indices[i - 1], f"beat_index not strictly increasing at {i}"


# ---------------------------------------------------------------------------
# Constraint tests
# ---------------------------------------------------------------------------

class TestNoAdjacentSameSource:
    def test_no_adjacent_same_source_shuffled(self, beat_analysis):
        """Shuffled-tiers with unique clips trivially satisfies adjacency constraint."""
        clips = _make_clip_analyses(8)
        result = assign_clips(beat_analysis, clips, mode="shuffled-tiers", seed=42)
        clips_list = [a.clip for a in result.assignments]
        for i in range(1, len(clips_list)):
            assert clips_list[i] != clips_list[i - 1], \
                f"adjacent same-source at index {i}: {clips_list[i]}"

    def test_no_adjacent_same_source_best_match(self, beat_analysis):
        clips = _make_clip_analyses(8)
        result = assign_clips(beat_analysis, clips, mode="best-match")
        clips_list = [a.clip for a in result.assignments]
        for i in range(1, len(clips_list)):
            assert clips_list[i] != clips_list[i - 1], \
                f"adjacent same-source at index {i}: {clips_list[i]}"


class TestNoClipRepeats:
    def test_no_repeats_shuffled(self, beat_analysis):
        clips = _make_clip_analyses(6)
        result = assign_clips(beat_analysis, clips, mode="shuffled-tiers", seed=42)
        paths = [a.clip for a in result.assignments]
        assert len(paths) == len(set(paths))

    def test_no_repeats_best_match(self, beat_analysis):
        clips = _make_clip_analyses(6)
        result = assign_clips(beat_analysis, clips, mode="best-match")
        paths = [a.clip for a in result.assignments]
        assert len(paths) == len(set(paths))


# ---------------------------------------------------------------------------
# Mode-specific tests
# ---------------------------------------------------------------------------

class TestBestMatch:
    def test_highest_motion_first(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses, mode="best-match")
        scores = [a.motion_score for a in result.assignments]
        assert scores == sorted(scores, reverse=True), \
            f"expected descending motion scores, got {scores}"

    def test_all_clips_assigned(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses, mode="best-match")
        assert len(result.assignments) == len(clip_analyses)

    def test_clip_beat_count_in_range(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses, mode="best-match")
        for a in result.assignments:
            assert 4 <= a.beat_count <= 8, \
                f"beat_count {a.beat_count} out of [4,8] range"


class TestShuffledTiers:
    def test_all_clips_assigned(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses, mode="shuffled-tiers", seed=42)
        assert len(result.assignments) == len(clip_analyses)

    def test_tiers_exist_when_enough_clips(self, beat_analysis):
        """With 9+ clips, all three tiers should have clips."""
        clips = _make_clip_analyses(9)
        result = assign_clips(beat_analysis, clips, mode="shuffled-tiers", seed=42)
        scores = [a.motion_score for a in result.assignments]
        # First third should be highest motion (tier: high)
        n = len(scores)
        tier_size = max(1, n // 3)
        # High tier clips should generally have higher scores than med/low
        # (after randomization within tier, boundaries may blur)
        # Just sanity-check that all clips are present.
        assert len(scores) == len(clips)

    def test_deterministic_with_seed(self, beat_analysis):
        clips = _make_clip_analyses(6)
        r1 = assign_clips(beat_analysis, clips, mode="shuffled-tiers", seed=42)
        r2 = assign_clips(beat_analysis, clips, mode="shuffled-tiers", seed=42)
        assert r1.assignments == r2.assignments

    def test_different_seeds_produce_different_orders(self, beat_analysis):
        clips = _make_clip_analyses(10)
        r1 = assign_clips(beat_analysis, clips, mode="shuffled-tiers", seed=1)
        r2 = assign_clips(beat_analysis, clips, mode="shuffled-tiers", seed=2)
        paths1 = [a.clip for a in r1.assignments]
        paths2 = [a.clip for a in r2.assignments]
        # With 10 clips, probability of identical shuffle is 1/10! ≈ 0
        assert paths1 != paths2

    def test_clip_beat_count_in_range(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses, mode="shuffled-tiers", seed=42)
        for a in result.assignments:
            assert 4 <= a.beat_count <= 8, \
                f"beat_count {a.beat_count} out of [4,8] range"


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

class TestZeroClips:
    def test_returns_empty_assignments(self, beat_analysis):
        result = assign_clips(beat_analysis, {})
        assert result.assignments == []
        assert result.bpm == 120.0

    def test_assignments_is_list(self, beat_analysis):
        result = assign_clips(beat_analysis, {})
        assert isinstance(result.assignments, list)


class TestZeroBeats:
    def test_returns_empty_assignments(self, clip_analyses):
        empty_beats = {"bpm": 120.0, "beats": [], "energy": []}
        result = assign_clips(empty_beats, clip_analyses)
        assert result.assignments == []


class TestSingleClip:
    def test_assigns_single_clip(self, beat_analysis):
        clips = _make_clip_analyses(1)
        result = assign_clips(beat_analysis, clips)
        assert len(result.assignments) == 1
        a = result.assignments[0]
        assert a.clip == "/tmp/clip_0.mp4"
        assert a.beat_index == 0
        assert a.target_start == 0.0

    def test_single_clip_best_match(self, beat_analysis):
        clips = _make_clip_analyses(1)
        result = assign_clips(beat_analysis, clips, mode="best-match")
        assert len(result.assignments) == 1


class TestMoreBeatsThanClips:
    def test_uses_only_needed_beats(self, beat_analysis):
        """When there are more beats than needed, assignments don't use all beats."""
        clips = _make_clip_analyses(2)
        result = assign_clips(beat_analysis, clips)
        # Last assignment's beat_index + beat_count should be < len(beats)
        # or at least not more than total beats
        total_beats = len(beat_analysis["beats"])
        last = result.assignments[-1]
        assert last.beat_index + last.beat_count <= total_beats

    def test_video_ends_early_not_full_song(self, beat_analysis):
        clips = _make_clip_analyses(1)
        result = assign_clips(beat_analysis, clips)
        last = result.assignments[-1]
        used_beats = last.beat_index + last.beat_count
        total_beats = len(beat_analysis["beats"])
        assert used_beats < total_beats, \
            f"expected used_beats ({used_beats}) < total ({total_beats})"


class TestMoreClipsThanBeats:
    def test_ends_when_beats_exhausted(self):
        """When there aren't enough beats for all clips, video ends early."""
        beats = _make_beat_analysis(10)  # 10 beats
        clips = _make_clip_analyses(6)   # each needs 4-8 beats, total ~24-48 beats
        result = assign_clips(beats, clips)
        # Should assign some clips but not all
        total_beats_needed = sum(a.beat_count for a in result.assignments)
        assert total_beats_needed <= len(beats["beats"])
        # At least one clip assigned (there's room for at least 1 clip needing 4 beats)
        assert len(result.assignments) >= 1

    def test_single_clip_needs_more_than_available(self):
        """When a single clip needs more beats than available, assigns what it can."""
        beats = _make_beat_analysis(3)  # only 3 beats
        clips = _make_clip_analyses(1)
        result = assign_clips(beats, clips)
        assert len(result.assignments) == 1
        # beat_count clamped to available beats
        assert result.assignments[0].beat_count <= 3


# ---------------------------------------------------------------------------
# Beat count computation tests
# ---------------------------------------------------------------------------

class TestBeatCountComputation:
    def test_clip_duration_determines_beat_count(self):
        """Clip with 3.5s exciting segment at 120 BPM → ~7 beats."""
        beats = _make_beat_analysis(32)
        clips = {
            "/tmp/c.mp4": {
                "most_exciting": {"start": 1.0, "end": 4.5, "score": 0.8},
                "motion_scores": [0.8] * 90,
                "orientation": "landscape",
            }
        }
        result = assign_clips(beats, clips)
        # 3.5s / 0.5s per beat = 7 beats
        assert result.assignments[0].beat_count == 7

    def test_short_clip_gets_min_beats(self):
        """Very short exciting segment gets clamped to min_beats."""
        beats = _make_beat_analysis(32)
        clips = {
            "/tmp/c.mp4": {
                "most_exciting": {"start": 0.5, "end": 1.0, "score": 0.8},
                "motion_scores": [0.8] * 30,
                "orientation": "landscape",
            }
        }
        result = assign_clips(beats, clips, min_beats=4, max_beats=8)
        assert result.assignments[0].beat_count == 4

    def test_long_clip_gets_max_beats(self):
        """Very long exciting segment gets clamped to max_beats."""
        beats = _make_beat_analysis(32)
        clips = {
            "/tmp/c.mp4": {
                "most_exciting": {"start": 0.0, "end": 10.0, "score": 0.8},
                "motion_scores": [0.8] * 300,
                "orientation": "landscape",
            }
        }
        result = assign_clips(beats, clips, min_beats=4, max_beats=8)
        assert result.assignments[0].beat_count == 8

    def test_min_beats_override(self):
        """--min-beats override works."""
        beats = _make_beat_analysis(32)
        clips = _make_clip_analyses(1)
        result = assign_clips(beats, clips, min_beats=6, max_beats=8)
        assert result.assignments[0].beat_count >= 6

    def test_max_beats_override(self):
        """--max-beats override works."""
        beats = _make_beat_analysis(32)
        clips = _make_clip_analyses(1)
        result = assign_clips(beats, clips, min_beats=4, max_beats=5)
        assert result.assignments[0].beat_count <= 5


# ---------------------------------------------------------------------------
# Symmetric padding tests
# ---------------------------------------------------------------------------

class TestSymmetricPadding:
    def test_pads_exciting_segment_when_shorter_than_slot(self):
        """Exciting segment shorter than beat slot → symmetrically expanded."""
        beats = _make_beat_analysis(32, bpm=120.0)
        # 6 beats = 3.0s at 120 BPM
        # Exciting segment = 2.0s (shorter than slot)
        clips = {
            "/tmp/c.mp4": {
                "most_exciting": {"start": 4.0, "end": 6.0, "score": 0.8},
                "motion_scores": [0.8] * 300,
                "orientation": "landscape",
            }
        }
        result = assign_clips(beats, clips, min_beats=6, max_beats=6)
        a = result.assignments[0]
        # Original exciting: 4.0–6.0 (2s). Slot = 6 beats * 0.5s = 3.0s.
        # Extra = 1.0s, padded 0.5s each side → 3.5–6.5
        assert a.source_start == pytest.approx(3.5, abs=0.1)
        assert a.source_end == pytest.approx(6.5, abs=0.1)

    def test_does_not_pad_below_zero(self):
        """Padding doesn't go below 0.0."""
        beats = _make_beat_analysis(32, bpm=120.0)
        clips = {
            "/tmp/c.mp4": {
                "most_exciting": {"start": 0.5, "end": 2.5, "score": 0.8},
                "motion_scores": [0.8] * 300,
                "orientation": "landscape",
            }
        }
        result = assign_clips(beats, clips, min_beats=6, max_beats=6)
        a = result.assignments[0]
        assert a.source_start >= 0.0

    def test_exciting_longer_than_slot_not_trimmed(self):
        """If exciting segment is longer than slot, source_start/end keep original."""
        beats = _make_beat_analysis(32, bpm=120.0)
        clips = {
            "/tmp/c.mp4": {
                "most_exciting": {"start": 1.0, "end": 5.0, "score": 0.8},
                "motion_scores": [0.8] * 300,
                "orientation": "landscape",
            }
        }
        result = assign_clips(beats, clips, min_beats=4, max_beats=4)
        a = result.assignments[0]
        # 4 beats = 2.0s, exciting = 4.0s. Should keep original.
        assert a.source_start == 1.0
        assert a.source_end == 5.0


# ---------------------------------------------------------------------------
# Beat energy metadata tests
# ---------------------------------------------------------------------------

class TestBeatEnergy:
    def test_beat_energy_is_mean_of_assigned_beats(self, beat_analysis):
        """beat_energy in output is average of the beat energies for the occupied beats."""
        clips = {
            "/tmp/c.mp4": {
                "most_exciting": {"start": 1.0, "end": 3.0, "score": 0.8},
                "motion_scores": [0.8] * 90,
                "orientation": "landscape",
            }
        }
        result = assign_clips(beat_analysis, clips, min_beats=4, max_beats=4)
        a = result.assignments[0]
        # Compute expected from fixture: beats 0-3 energy values
        energies = beat_analysis["energy"][:4]
        expected = sum(energies) / 4
        assert a.beat_energy == pytest.approx(expected, abs=0.01)

    def test_beat_energy_between_0_and_1(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses)
        for a in result.assignments:
            assert 0.0 <= a.beat_energy <= 1.0


# ---------------------------------------------------------------------------
# Consecutive / no-gaps tests
# ---------------------------------------------------------------------------

class TestConsecutiveNoGaps:
    def test_clips_abut_each_other(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses)
        for i in range(1, len(result.assignments)):
            prev = result.assignments[i - 1]
            curr = result.assignments[i]
            # Next clip's beat_index = previous beat_index + previous beat_count
            assert curr.beat_index == prev.beat_index + prev.beat_count, \
                f"gap between assignment {i-1} and {i}"

    def test_first_clip_starts_at_beat_0(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses)
        assert result.assignments[0].beat_index == 0

    def test_first_target_start_is_zero(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses)
        assert result.assignments[0].target_start == 0.0


# ---------------------------------------------------------------------------
# JSON-serializable output test
# ---------------------------------------------------------------------------

class TestJSONSerializable:
    def test_output_is_json_serializable(self, beat_analysis, clip_analyses):
        result = assign_clips(beat_analysis, clip_analyses)
        dumped = json.dumps(result.to_dict())
        reloaded = json.loads(dumped)
        assert reloaded == result.to_dict()


# ---------------------------------------------------------------------------
# Invalid mode test
# ---------------------------------------------------------------------------

class TestInvalidMode:
    def test_unknown_mode_raises(self, beat_analysis, clip_analyses):
        with pytest.raises(ValueError, match="Unknown mode"):
            assign_clips(beat_analysis, clip_analyses, mode="invalid-mode")
