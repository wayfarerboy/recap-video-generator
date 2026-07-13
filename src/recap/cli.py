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
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.option(
    "--window",
    default=3.0,
    show_default=True,
    help="Duration of the most-exciting segment (seconds).",
)
def analyze(video_path, window):
    """Score a video clip for visual excitement.

    Outputs JSON with the most-exciting segment, per-frame motion scores,
    and orientation metadata to stdout.
    """
    from recap.video import analyze_video

    try:
        result = analyze_video(video_path, window_seconds=window)
        click.echo(json.dumps(result))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
