"""CLI entry point for recap."""

import json
import subprocess
import sys

import click

from recap.audio import detect_beats
from recap.plan import Plan


@click.group()
def main():
    """recap — Auto-generate music-synced recap videos from phone footage."""
    pass


@main.command()
def check():
    """Verify FFmpeg and kdenlive are installed."""
    missing = []

    # Check ffmpeg
    try:
        proc = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        missing.append("ffmpeg")
    else:
        version_line = proc.stdout.splitlines()[0] if proc.stdout.strip() else "ffmpeg (unknown version)"
        click.echo(f"ffmpeg: {version_line}")

    # Check kdenlive
    try:
        proc = subprocess.run(
            ["kdenlive", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # fallback: try which kdenlive
        try:
            proc = subprocess.run(
                ["which", "kdenlive"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            missing.append("kdenlive")
        else:
            if proc.returncode == 0 and proc.stdout.strip():
                click.echo(f"kdenlive: {proc.stdout.strip()}")
            else:
                missing.append("kdenlive")
    else:
        version_line = proc.stdout.splitlines()[0] if proc.stdout.strip() else "kdenlive (unknown version)"
        click.echo(f"kdenlive: {version_line}")

    if missing:
        click.echo(f"Missing: {', '.join(missing)}", err=True)
        sys.exit(1)


@main.command()
@click.argument("filepath", type=click.Path(exists=False))
def beats(filepath):
    """Detect beats and energy in an audio file. Outputs JSON."""
    try:
        result = detect_beats(filepath)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    click.echo(json.dumps(result))


@main.command()
@click.option(
    "--clips",
    type=click.Path(exists=True, file_okay=False, readable=True),
    required=True,
    help="Directory containing source video clips.",
)
@click.option(
    "--music",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
    help="Music audio file (MP3, WAV, etc.).",
)
@click.option(
    "--mode",
    type=click.Choice(["shuffled-tiers", "best-match"]),
    default="shuffled-tiers",
    show_default=True,
    help="Assignment strategy.",
)
@click.option(
    "--min-beats",
    type=int,
    default=4,
    show_default=True,
    help="Minimum beats per clip.",
)
@click.option(
    "--max-beats",
    type=int,
    default=8,
    show_default=True,
    help="Maximum beats per clip.",
)
@click.option(
    "--seed",
    type=int,
    default=42,
    show_default=True,
    help="Random seed for shuffled-tiers mode reproducibility.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-analyze all clips and music, ignoring any cached results.",
)
def assign(clips, music, mode, min_beats, max_beats, seed, force):
    """Assign video clips to beat slots on the music timeline.

    Outputs a JSON assignment plan to stdout.
    """
    from pathlib import Path

    from recap.audio import detect_beats
    from recap.pipeline import PipelineConfig, run

    config = PipelineConfig(
        clips_dir=Path(clips),
        music_path=Path(music),
        mode=mode,
        seed=seed,
        force=force,
        transcode=False,
        min_beats=min_beats,
        max_beats=max_beats,
    )

    try:
        result = run(config, stop_after="assign")
    except ValueError as exc:
        if "No clips found" in str(exc):
            # Output an empty plan with the music BPM.
            music_path = Path(music)
            beat_data = detect_beats(str(music_path))
            empty_plan = Plan(bpm=beat_data["bpm"])
            click.echo(json.dumps(empty_plan.to_dict(), indent=2))
            return
        raise

    for w in result.warnings:
        label = w.source or "unknown"
        click.echo(f"WARNING: {label} — {w.message}", err=True)

    click.echo(json.dumps(result.plan.to_dict(), indent=2))


@main.command()
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
    help="Path to the assignment plan JSON (output of `recap assign`).",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False),
    default="recap-trims",
    show_default=True,
    help="Directory for trimmed MP4 files.",
)
def trim(plan_path, output_dir):
    """Trim the exciting segment from each clip in the assignment plan.

    Reads the assignment plan (JSON from ``recap assign``) and runs FFmpeg
    to extract each clip's source_start–source_end segment.  Trims run in
    parallel and produce frame-accurate H.264/AAC MP4 files.

    Outputs the updated plan JSON with ``trim`` paths to stdout.
    """
    import json
    import sys
    from pathlib import Path

    from recap.trim import trim_plan

    plan_dict = json.loads(Path(plan_path).read_text())
    plan = Plan.from_dict(plan_dict)
    trimmed_plan, summary = trim_plan(plan, output_dir=output_dir, verbose=True, progress_file=sys.stderr)

    click.echo(json.dumps(trimmed_plan.to_dict(), indent=2))
    if summary["failed"] > 0:
        click.echo(
            f"\nTrim summary: {summary['succeeded']} succeeded, "
            f"{summary['failed']} failed",
            err=True,
        )
        for err_item in summary["errors"]:
            click.echo(f"  FAILED: {err_item['clip']} — {err_item['error']}", err=True)
        sys.exit(1)
    else:
        click.echo(f"\nAll {summary['succeeded']} clips trimmed successfully.", err=True)


@main.command()
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
    help="Path to the assignment plan JSON (output of `recap assign` or `recap trim`).",
)
@click.option(
    "--music",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
    help="Music audio file for the audio track.",
)
@click.option(
    "-o",
    "--output",
    "output_path",
    type=click.Path(dir_okay=False),
    default="recap.kdenlive",
    show_default=True,
    help="Path to write the .kdenlive project file.",
)
@click.option(
    "--ratio",
    type=click.Choice(["16:9", "9:16"]),
    default="16:9",
    show_default=True,
    help="Output aspect ratio.",
)
@click.option(
    "--fps",
    type=float,
    default=25.0,
    show_default=True,
    help="Timeline frame rate.",
)
def render(plan_path, music, output_path, ratio, fps):
    """Generate a .kdenlive project file from the assignment plan.

    Reads the assignment plan (JSON from ``recap assign``, optionally
    updated by ``recap trim``) and writes a valid .kdenlive project
    file with per-clip MLT transforms for rotation and centre-crop.
    """
    from pathlib import Path

    from recap.render import render_kdenlive

    plan_dict = json.loads(Path(plan_path).read_text())
    plan = Plan.from_dict(plan_dict)

    # Use the output file's parent as the base for relative paths
    output_dir = Path(output_path).resolve().parent

    xml = render_kdenlive(
        plan,
        music_path=music,
        output_ratio=ratio,
        fps=fps,
        output_dir=str(output_dir),
    )

    Path(output_path).write_text(xml, encoding="utf-8")
    click.echo(f"Wrote {output_path}")


@main.command()
@click.argument(
    "video_path",
    type=click.Path(exists=True, readable=True),
)
@click.option(
    "--window",
    default=3.0,
    show_default=True,
    help="Duration of the most-exciting segment (seconds).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-analyze all clips, ignoring any cached results.",
)
def analyze(video_path, window, force):
    """Score video clip(s) for visual excitement.

    When VIDEO_PATH is a single file, outputs JSON with the most-exciting
    segment, per-frame motion scores, and orientation metadata to stdout.

    When VIDEO_PATH is a directory, recursively analyses all .mp4/.mov
    files, caches per-clip results in a .recap-cache/ subdirectory, and
    prints a summary of processed / skipped / errored clips.
    """
    from pathlib import Path

    p = Path(video_path)

    if p.is_dir():
        from recap.batch import analyze_directory

        summary = analyze_directory(video_path, window_seconds=window, force=force)
        processed = summary["processed"]
        skipped = summary["skipped"]
        errors = summary["errors"]

        click.echo(f"Processed: {processed}, Skipped (cached): {skipped}, Errors: {len(errors)}")
        for err in errors:
            click.echo(f"  ERROR: {err['file']} — {err['error']}", err=True)

        if errors:
            sys.exit(1)
        return

    # Single-file path.
    from recap.video import analyze_video

    try:
        result = analyze_video(video_path, window_seconds=window)
        click.echo(json.dumps(result))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@main.command()
@click.argument(
    "clips_dir",
    type=click.Path(exists=True, file_okay=False, readable=True),
)
@click.argument(
    "music_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.option(
    "-o",
    "--output",
    "output_path",
    type=click.Path(dir_okay=False),
    default="recap.kdenlive",
    show_default=True,
    help="Path to write the .kdenlive project file.",
)
@click.option(
    "--mode",
    type=click.Choice(["shuffled-tiers", "best-match"]),
    default="shuffled-tiers",
    show_default=True,
    help="Assignment strategy.",
)
@click.option(
    "--ratio",
    type=click.Choice(["16:9", "9:16"]),
    default="16:9",
    show_default=True,
    help="Output aspect ratio.",
)
@click.option(
    "--seed",
    type=int,
    default=42,
    show_default=True,
    help="Random seed for shuffled-tiers mode reproducibility.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-analyze all clips and music, ignoring any cached results.",
)
@click.option(
    "--fps",
    type=float,
    default=25.0,
    show_default=True,
    help="Timeline frame rate (also used for transcoding).",
)
@click.option(
    "--no-transcode",
    is_flag=True,
    default=False,
    help="Skip the transcode step (use original VFR sources directly).",
)
def create(clips_dir, music_file, output_path, mode, ratio, seed, force, fps, no_transcode):
    """Run the full recap pipeline: transcode → analyze → assign → render.

    CLIPS_DIR is a directory of source video clips (.mp4/.mov).
    MUSIC_FILE is an audio file (MP3, WAV, etc.).
    Clips are transcoded to constant 25fps by default; timeline trimming
    uses Kdenlive in/out points — no pre-trimmed media files are produced.
    """
    from pathlib import Path

    from recap.pipeline import PipelineConfig, run

    config = PipelineConfig(
        clips_dir=Path(clips_dir),
        music_path=Path(music_file),
        mode=mode,
        ratio=ratio,
        seed=seed,
        force=force,
        fps=float(fps),
        transcode=not no_transcode,
        output_path=Path(output_path),
    )

    try:
        result = run(config)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    for w in result.warnings:
        label = w.source or "unknown"
        click.echo(f"WARNING: {label} — {w.message}", err=True)

    click.echo(f"Wrote {output_path}")
    click.echo("\nDone! Open the project in kdenlive:")
    click.echo(f"  kdenlive {output_path}")
