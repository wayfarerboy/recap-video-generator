"""Tests for plan.py — Plan and Assignment dataclasses."""

import json

import pytest

from recap.plan import Assignment, Plan


# ---------------------------------------------------------------------------
# Assignment tests
# ---------------------------------------------------------------------------

class TestAssignment:
    def test_construction(self):
        a = Assignment(
            clip="/tmp/c.mp4",
            source_start=1.0,
            source_end=3.0,
            target_start=0.0,
            beat_index=0,
            beat_count=4,
            beat_energy=0.5,
            motion_score=0.8,
        )
        assert a.clip == "/tmp/c.mp4"
        assert a.source_start == 1.0
        assert a.trim is None

    def test_trim_optional(self):
        a = Assignment(
            clip="/tmp/c.mp4",
            source_start=1.0,
            source_end=3.0,
            target_start=0.0,
            beat_index=0,
            beat_count=4,
            beat_energy=0.5,
            motion_score=0.8,
            trim="/tmp/trim.mp4",
        )
        assert a.trim == "/tmp/trim.mp4"

    def test_to_dict_without_trim(self):
        a = Assignment(
            clip="/tmp/c.mp4",
            source_start=1.0,
            source_end=3.0,
            target_start=0.0,
            beat_index=0,
            beat_count=4,
            beat_energy=0.5,
            motion_score=0.8,
        )
        d = a.to_dict()
        assert d["clip"] == "/tmp/c.mp4"
        assert "trim" not in d

    def test_to_dict_with_trim(self):
        a = Assignment(
            clip="/tmp/c.mp4",
            source_start=1.0,
            source_end=3.0,
            target_start=0.0,
            beat_index=0,
            beat_count=4,
            beat_energy=0.5,
            motion_score=0.8,
            trim="/tmp/trim.mp4",
        )
        d = a.to_dict()
        assert d["trim"] == "/tmp/trim.mp4"

    def test_from_dict_without_trim(self):
        d = {
            "clip": "/tmp/c.mp4",
            "source_start": 1.0,
            "source_end": 3.0,
            "target_start": 0.0,
            "beat_index": 0,
            "beat_count": 4,
            "beat_energy": 0.5,
            "motion_score": 0.8,
        }
        a = Assignment.from_dict(d)
        assert a.clip == "/tmp/c.mp4"
        assert a.trim is None

    def test_from_dict_with_trim(self):
        d = {
            "clip": "/tmp/c.mp4",
            "source_start": 1.0,
            "source_end": 3.0,
            "target_start": 0.0,
            "beat_index": 0,
            "beat_count": 4,
            "beat_energy": 0.5,
            "motion_score": 0.8,
            "trim": "/tmp/trim.mp4",
        }
        a = Assignment.from_dict(d)
        assert a.trim == "/tmp/trim.mp4"

    def test_roundtrip(self):
        a = Assignment(
            clip="/tmp/c.mp4",
            source_start=1.5,
            source_end=3.5,
            target_start=2.0,
            beat_index=4,
            beat_count=6,
            beat_energy=0.75,
            motion_score=0.9,
            trim="/tmp/trim.mp4",
        )
        b = Assignment.from_dict(a.to_dict())
        assert b == a


# ---------------------------------------------------------------------------
# Plan tests
# ---------------------------------------------------------------------------

class TestPlan:
    def test_construction_empty(self):
        p = Plan(bpm=120.0)
        assert p.bpm == 120.0
        assert p.assignments == []

    def test_construction_with_assignments(self):
        a = Assignment(
            clip="/tmp/c.mp4",
            source_start=1.0,
            source_end=3.0,
            target_start=0.0,
            beat_index=0,
            beat_count=4,
            beat_energy=0.5,
            motion_score=0.8,
        )
        p = Plan(bpm=120.0, assignments=[a])
        assert len(p.assignments) == 1
        assert p.assignments[0].clip == "/tmp/c.mp4"

    def test_to_dict(self):
        a = Assignment(
            clip="/tmp/c.mp4",
            source_start=1.0,
            source_end=3.0,
            target_start=0.0,
            beat_index=0,
            beat_count=4,
            beat_energy=0.5,
            motion_score=0.8,
        )
        p = Plan(bpm=120.0, assignments=[a])
        d = p.to_dict()
        assert d["bpm"] == 120.0
        assert len(d["assignments"]) == 1
        assert d["assignments"][0]["clip"] == "/tmp/c.mp4"

    def test_to_dict_empty(self):
        p = Plan(bpm=140.0)
        d = p.to_dict()
        assert d == {"bpm": 140.0, "assignments": []}

    def test_from_dict(self):
        d = {
            "bpm": 120.0,
            "assignments": [
                {
                    "clip": "/tmp/c.mp4",
                    "source_start": 1.0,
                    "source_end": 3.0,
                    "target_start": 0.0,
                    "beat_index": 0,
                    "beat_count": 4,
                    "beat_energy": 0.5,
                    "motion_score": 0.8,
                }
            ],
        }
        p = Plan.from_dict(d)
        assert p.bpm == 120.0
        assert len(p.assignments) == 1
        assert p.assignments[0].clip == "/tmp/c.mp4"
        assert p.assignments[0].source_start == 1.0

    def test_from_dict_empty_assignments(self):
        d = {"bpm": 140.0, "assignments": []}
        p = Plan.from_dict(d)
        assert p.bpm == 140.0
        assert p.assignments == []

    def test_from_dict_no_assignments_key(self):
        d = {"bpm": 140.0}
        p = Plan.from_dict(d)
        assert p.bpm == 140.0
        assert p.assignments == []

    def test_to_dict_json_roundtrip(self):
        a = Assignment(
            clip="/tmp/c.mp4",
            source_start=1.5,
            source_end=3.5,
            target_start=2.0,
            beat_index=4,
            beat_count=6,
            beat_energy=0.75,
            motion_score=0.9,
            trim="/tmp/trim.mp4",
        )
        p = Plan(bpm=120.0, assignments=[a])
        json_str = json.dumps(p.to_dict())
        reloaded = Plan.from_dict(json.loads(json_str))
        assert reloaded.bpm == p.bpm
        assert len(reloaded.assignments) == 1
        assert reloaded.assignments[0] == a

    def test_from_dict_preserves_types(self):
        d = {
            "bpm": 120.0,
            "assignments": [
                {
                    "clip": "/tmp/c.mp4",
                    "source_start": 1,
                    "source_end": 3,
                    "target_start": 0,
                    "beat_index": 0,
                    "beat_count": 4,
                    "beat_energy": 0.5,
                    "motion_score": 0.8,
                }
            ],
        }
        p = Plan.from_dict(d)
        a = p.assignments[0]
        assert isinstance(a.source_start, float) or isinstance(a.source_start, int)
        assert isinstance(a.clip, str)
