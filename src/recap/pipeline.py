"""Pipeline orchestration for the recap video generation workflow.

Owns all five pipeline stages: transcode, analyze clips, analyze music,
assign, and render.  The CLI becomes a thin adapter that unpacks flags into
:class:`PipelineConfig` and calls :func:`run`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from recap.plan import Plan


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PipelineWarning:
    """Non-fatal warning collected during pipeline execution."""

    stage: str
    message: str
    source: str | None = None


@dataclass
class PipelineConfig:
    """All parameters needed to run the recap pipeline."""

    clips_dir: Path
    music_path: Path
    mode: str = "shuffled-tiers"
    ratio: str = "16:9"
    seed: int = 42
    force: bool = False
    fps: float = 25.0
    transcode: bool = True
    min_beats: int = 4
    max_beats: int = 8
    output_path: Path | None = None


@dataclass
class PipelineResult:
    """Result of a pipeline run."""

    plan: Plan
    kdenlive_path: Path | None = None
    warnings: list[PipelineWarning] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Music beat caching
# ---------------------------------------------------------------------------

def _load_or_cache_beats(
    music_path: Path,
    cache_dir: Path,
    force: bool = False,
) -> dict:
    """Return beat analysis for *music_path*, using and updating a JSON cache.

    Parameters
    ----------
    music_path : Path
        Path to the music audio file.
    cache_dir : Path
        Directory containing the ``.recap-cache/`` subdirectory.
    force : bool
        When ``True``, re-analyse even if a cache file exists.

    Returns
    -------
    dict
        Beat analysis dict with keys ``bpm``, ``beats``, ``energy``.
    """
    from recap.audio import detect_beats

    cache_parent = cache_dir / ".recap-cache"
    cache_name = music_path.stem + "_beats.json"
    cache_path = cache_parent / cache_name

    if not force and cache_path.exists():
        return json.loads(cache_path.read_text())

    beat_data = detect_beats(str(music_path))
    cache_parent.mkdir(exist_ok=True)
    cache_path.write_text(json.dumps(beat_data, indent=2))
    return beat_data


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run(
    config: PipelineConfig,
    stop_after: str | None = None,
) -> PipelineResult:
    """Run the recap pipeline.

    Stages (when *config.transcode* is ``True``, 5 stages; otherwise 4):

    1. Transcode + analyze clips
    2. Analyze music
    3. Assign clips to beat slots
    4. Render kdenlive project (unless *stop_after* is ``"assign"``)

    Parameters
    ----------
    config : PipelineConfig
        All pipeline parameters.
    stop_after : str or None
        If ``"assign"``, stops after the assign stage and returns the plan
        without rendering a kdenlive project.

    Returns
    -------
    PipelineResult
        The assignment plan, optional kdenlive output path, and any warnings.

    Raises
    ------
    ValueError
        If no clips are found or the music file cannot be decoded.
    """
    from recap.assign import assign_clips
    from recap.batch import analyze_directory
    from recap.render import render_kdenlive

    warnings: list[PipelineWarning] = []

    # ------------------------------------------------------------------
    # Stage 1: Transcode + analyze clips
    # ------------------------------------------------------------------
    batch = analyze_directory(
        str(config.clips_dir),
        force=config.force,
        transcode=config.transcode,
    )

    # Collect clip analysis errors as warnings
    for err in batch["errors"]:
        warnings.append(PipelineWarning(
            stage="analyze_clips",
            message=err["error"],
            source=err["file"],
        ))

    # Collect transcode errors as warnings
    tc = batch.get("transcode")
    if tc:
        for err in tc["errors"]:
            warnings.append(PipelineWarning(
                stage="transcode",
                message=err["error"],
                source=err["file"],
            ))

    clip_data = batch["results"]

    # ------------------------------------------------------------------
    # Stage 2: Analyze music
    # ------------------------------------------------------------------
    beat_data = _load_or_cache_beats(
        config.music_path,
        config.clips_dir,
        force=config.force,
    )

    # ------------------------------------------------------------------
    # Fatal: no clips
    # ------------------------------------------------------------------
    if not clip_data:
        raise ValueError("No clips found")

    # ------------------------------------------------------------------
    # Stage 3 (or 4): Assign clips to beats
    # ------------------------------------------------------------------
    plan = assign_clips(
        beat_analysis=beat_data,
        clip_analyses=clip_data,
        mode=config.mode,
        min_beats=config.min_beats,
        max_beats=config.max_beats,
        seed=config.seed,
    )

    if stop_after == "assign":
        return PipelineResult(plan=plan, kdenlive_path=None, warnings=warnings)

    # ------------------------------------------------------------------
    # Stage 4 (or 5): Render kdenlive project
    # ------------------------------------------------------------------
    output_dir = (
        config.output_path.resolve().parent
        if config.output_path
        else Path.cwd()
    )
    xml = render_kdenlive(
        plan,
        music_path=str(config.music_path),
        output_ratio=config.ratio,
        fps=config.fps,
        output_dir=str(output_dir),
    )

    if config.output_path:
        config.output_path.write_text(xml, encoding="utf-8")

    return PipelineResult(
        plan=plan,
        kdenlive_path=config.output_path,
        warnings=warnings,
    )
