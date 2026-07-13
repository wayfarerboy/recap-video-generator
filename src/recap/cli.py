"""CLI entry point for recap."""

import json
import subprocess
import sys

import click

from recap.audio import detect_beats


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
