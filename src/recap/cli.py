"""CLI entry point for recap."""

import subprocess
import sys

import click


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
