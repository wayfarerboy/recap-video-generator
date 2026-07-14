"""Clip-to-beat assignment engine.

Produces an assignment plan mapping video clips to beat slots on the
music timeline.  Supports two modes:

- ``shuffled-tiers`` (default): clips bucketed into high/med/low motion,
  beats bucketed into high/med/low energy, randomised within tiers.
- ``best-match``: strict descending match — highest-motion clip → first
  (earliest) position.
"""

from __future__ import annotations

import math
import random
from typing import Any

from recap.plan import Assignment, Plan


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assign_clips(
    beat_analysis: dict[str, Any],
    clip_analyses: dict[str, dict[str, Any]],
    mode: str = "shuffled-tiers",
    min_beats: int = 4,
    max_beats: int = 8,
    seed: int | None = 42,
) -> Plan:
    """Assign video clips to beat slots on the music timeline.

    Parameters
    ----------
    beat_analysis : dict
        Output of :func:`recap.audio.detect_beats` — must have keys
        ``bpm``, ``beats`` (list[float]), ``energy`` (list[float]).
    clip_analyses : dict[str, dict]
        Mapping of clip path → analysis dict (from
        :func:`recap.video.analyze_video`).  Each value must contain a
        ``most_exciting`` sub-dict with ``start``, ``end``, and ``score``.
    mode : str
        ``"shuffled-tiers"`` or ``"best-match"``.
    min_beats : int
        Minimum number of beats a single clip may occupy  (default 4).
    max_beats : int
        Maximum number of beats a single clip may occupy  (default 8).
    seed : int or None
        Random seed for shuffled-tiers reproducibility (default 42).
        Pass ``None`` for system-random shuffle each run.

    Returns
    -------
    Plan
        A :class:`Plan` with ``bpm`` and ``assignments`` list.
    """
    if mode not in ("shuffled-tiers", "best-match"):
        raise ValueError(f"Unknown mode: {mode!r}. Use 'shuffled-tiers' or 'best-match'.")

    bpm: float = beat_analysis["bpm"]
    beats: list[float] = beat_analysis["beats"]
    energy: list[float] = beat_analysis["energy"]

    if not beats or not clip_analyses:
        return Plan(bpm=bpm)

    beat_interval = 60.0 / bpm if bpm > 0 else 0.5

    # ------------------------------------------------------------------
    # 1. Compute per-clip beat count and padded source window
    # ------------------------------------------------------------------
    clip_entries: list[dict[str, Any]] = []
    for path, analysis in clip_analyses.items():
        exciting = analysis["most_exciting"]
        clip_duration = exciting["end"] - exciting["start"]
        desired_beats = max(1, round(clip_duration / beat_interval))
        n_beats = max(min_beats, min(max_beats, desired_beats))
        slot_duration = n_beats * beat_interval

        # Symmetric padding if exciting segment is shorter than slot
        if clip_duration < slot_duration:
            extra = slot_duration - clip_duration
            pad = extra / 2.0
            source_start = max(0.0, exciting["start"] - pad)
            source_end = exciting["end"] + pad
        else:
            source_start = exciting["start"]
            source_end = exciting["end"]

        clip_entries.append({
            "path": path,
            "source_start": round(source_start, 2),
            "source_end": round(source_end, 2),
            "motion_score": exciting["score"],
            "beat_count": n_beats,
        })

    # ------------------------------------------------------------------
    # 2. Order clips according to mode
    # ------------------------------------------------------------------
    if mode == "best-match":
        clip_entries.sort(key=lambda c: c["motion_score"], reverse=True)
    else:  # shuffled-tiers
        clip_entries.sort(key=lambda c: c["motion_score"], reverse=True)
        rng = random.Random(seed)
        # Split into three equal(-ish) tiers
        n = len(clip_entries)
        tier_size = max(1, math.ceil(n / 3))
        high = clip_entries[:tier_size]
        med = clip_entries[tier_size: 2 * tier_size] if n > tier_size else []
        low = clip_entries[2 * tier_size:] if n > 2 * tier_size else []
        rng.shuffle(high)
        rng.shuffle(med)
        rng.shuffle(low)
        clip_entries = high + med + low

    # ------------------------------------------------------------------
    # 3. Lay clips out consecutively on the beat timeline
    # ------------------------------------------------------------------
    assignments: list[Assignment] = []
    beat_idx = 0
    total_beats = len(beats)

    for entry in clip_entries:
        n_beats = entry["beat_count"]
        # Clamp to available beats
        if beat_idx + n_beats > total_beats:
            n_beats = total_beats - beat_idx
            if n_beats < 1:
                break  # no more room

        # Average energy over the assigned beat window
        beat_energy = (
            sum(energy[beat_idx : beat_idx + n_beats]) / n_beats
        )

        target_start = beats[beat_idx] if beat_idx < total_beats else 0.0

        assignments.append(Assignment(
            clip=entry["path"],
            source_start=entry["source_start"],
            source_end=entry["source_end"],
            target_start=target_start,
            beat_index=beat_idx,
            beat_count=n_beats,
            beat_energy=round(beat_energy, 4),
            motion_score=entry["motion_score"],
        ))

        beat_idx += n_beats

    return Plan(bpm=bpm, assignments=assignments)
