"""Constant frame-rate transcode step.

Provides :func:`transcode_clip` which uses ``ffmpeg`` to produce a
constant-25fps copy of a source file.  Already-transcoded files are
skipped via a content-hash cache.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def transcode_clip(
    source_path: str | Path,
    cache_dir: str | Path,
    fps: int = 25,
) -> str:
    """Transcode *source_path* to a constant frame-rate MP4.

    Parameters
    ----------
    source_path : str or Path
        Path to the source video file.
    cache_dir : str or Path
        Directory for transcoded output.  The file is written to
        ``<cache_dir>/transcoded/<hash>.mp4``.
    fps : int
        Target constant frame rate (default 25).

    Returns
    -------
    str
        Path to the transcoded file on success.  Returns the original
        *source_path* as a fallback on failure.

    Notes
    -----
    Uses ``ffmpeg -r <fps> -vsync cfr`` for constant frame-rate output.
    The cache key is an MD5 of the first 1 MB + file size, so identical
    files that are renamed still hit the cache.
    """
    source_path = Path(source_path)
    cache_dir = Path(cache_dir)

    if not source_path.exists():
        return str(source_path)

    transcode_dir = cache_dir / "transcoded"
    transcode_dir.mkdir(parents=True, exist_ok=True)

    # Content hash: MD5 of first 1 MB + file size
    cache_key = _content_hash(source_path)
    output_path = transcode_dir / f"{cache_key}.mp4"

    # Skip if already transcoded
    if output_path.exists():
        return str(output_path)

    cmd = [
        "ffmpeg",
        "-i", str(source_path),
        "-r", str(fps),
        "-vsync", "cfr",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-y",
        str(output_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # generous for long clips
        )
        if proc.returncode != 0:
            stderr_tail = (
                proc.stderr.strip().splitlines()[-5:]
                if proc.stderr
                else ["(no stderr)"]
            )
            raise RuntimeError(
                f"ffmpeg exited {proc.returncode}: " + "; ".join(stderr_tail)
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"Transcode failed for {source_path}: {exc}") from exc

    if not output_path.exists():
        raise RuntimeError(
            f"ffmpeg exited 0 but output file not found: {output_path}"
        )

    return str(output_path)


def _content_hash(file_path: Path) -> str:
    """Return an MD5 hex digest of the first 1 MB + file size."""
    hasher = hashlib.md5()
    try:
        with open(file_path, "rb") as fh:
            chunk = fh.read(1_048_576)  # 1 MB
            hasher.update(chunk)
    except OSError:
        pass
    size = file_path.stat().st_size
    hasher.update(str(size).encode("utf-8"))
    return hasher.hexdigest()[:12]
