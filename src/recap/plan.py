"""Typed plan and assignment dataclasses for the recap pipeline.

Replaces the bare ``dict`` previously passed between pipeline stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Assignment:
    """A single clip assignment to a beat slot on the music timeline."""

    clip: str
    source_start: float
    source_end: float
    target_start: float
    beat_index: int
    beat_count: int
    beat_energy: float
    motion_score: float
    trim: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "clip": self.clip,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "target_start": self.target_start,
            "beat_index": self.beat_index,
            "beat_count": self.beat_count,
            "beat_energy": self.beat_energy,
            "motion_score": self.motion_score,
        }
        if self.trim is not None:
            d["trim"] = self.trim
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Assignment:
        return cls(
            clip=d["clip"],
            source_start=d["source_start"],
            source_end=d["source_end"],
            target_start=d["target_start"],
            beat_index=d["beat_index"],
            beat_count=d["beat_count"],
            beat_energy=d["beat_energy"],
            motion_score=d["motion_score"],
            trim=d.get("trim"),
        )


@dataclass
class Plan:
    """An assignment plan mapping video clips to beat slots."""

    bpm: float
    assignments: list[Assignment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bpm": self.bpm,
            "assignments": [a.to_dict() for a in self.assignments],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        return cls(
            bpm=d["bpm"],
            assignments=[Assignment.from_dict(a) for a in d.get("assignments", [])],
        )
