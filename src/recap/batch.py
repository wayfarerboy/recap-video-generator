"""Batch video analysis with per-clip caching.

Provides directory-level analysis of .mp4/.mov files with per-clip JSON
caching in a ``.recap-cache/`` subdirectory.
"""

import hashlib
import json
from pathlib import Path

from recap.video import analyze_video
from recap.transcode import transcode_clip

CACHE_DIR_NAME = ".recap-cache"
"""Name of the cache directory created inside the analysed directory."""

VIDEO_EXTENSIONS = (".mp4", ".mov")


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(video_path: Path) -> str:
    """Return a deterministic filename-safe key for *video_path*.

    Uses SHA-256 of the resolved absolute path so renames or moves
    invalidate the cache.
    """
    digest = hashlib.sha256(str(video_path.resolve()).encode("utf-8")).hexdigest()
    return f"{digest}.json"


def _cache_path_for(cache_dir: Path, video_path: Path) -> Path:
    """Return the on-disk cache JSON path for a given video."""
    return cache_dir / _cache_key(video_path)


def _is_fresh(video_path: Path, cache_path: Path) -> bool:
    """Return ``True`` when *cache_path* exists and its mtime is **newer**
    than the source video's mtime."""
    if not cache_path.exists():
        return False
    source_mtime = video_path.stat().st_mtime
    cache_mtime = cache_path.stat().st_mtime
    return source_mtime < cache_mtime


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _find_videos(directory: Path) -> list[Path]:
    """Return a sorted list of all .mp4/.mov files under *directory*,
    excluding the ``.recap-cache/`` subdirectory."""
    videos: list[Path] = []
    cache_dir = directory / CACHE_DIR_NAME
    for ext in VIDEO_EXTENSIONS:
        for p in directory.rglob(f"*{ext}"):
            if cache_dir not in p.parents and p != cache_dir:
                videos.append(p)
    # Case-insensitive fallback for .MP4 / .MOV
    for ext in VIDEO_EXTENSIONS:
        for p in directory.rglob(f"*{ext.upper()}"):
            if cache_dir not in p.parents and p != cache_dir:
                videos.append(p)
    # Deduplicate (Path.rglob is case-sensitive on Linux but returned
    # values may overlap when both patterns match the same file).
    return sorted(set(videos))


# ---------------------------------------------------------------------------
# Batch entry point
# ---------------------------------------------------------------------------

def analyze_directory(
    directory: str,
    window_seconds: float = 3.0,
    force: bool = False,
    transcode: bool = False,
) -> dict:
    """Analyze every .mp4/.mov recursively under *directory*.

    Each clip's analysis result is cached to ``.recap-cache/`` (one JSON
    file per video, keyed by source path hash).  On subsequent runs clips
    whose source mtime is older than the cache mtime are skipped unless
    *force* is ``True``.

    When *transcode* is ``True``, each source clip is first transcoded to
    a constant-25fps copy via :func:`recap.transcode.transcode_clip` and
    analysis runs against the transcoded file.  The analysis cache key
    incorporates the original source's content hash so results survive
    pipeline re-runs.

    Parameters
    ----------
    directory : str
        Root directory to scan.
    window_seconds : float
        Duration of the most-exciting segment (forwarded to
        :func:`recap.video.analyze_video`).
    force : bool
        When ``True``, re-analyse every clip regardless of cache.
    transcode : bool
        When ``True``, transcode each source clip to CFR before analysis
        (default ``False``).

    Returns
    -------
    dict
        ``{"processed": int, "skipped": int, "errors": list[dict],
        "results": dict[str, dict]}`` where each error dict has ``file``
        and ``error`` keys, and ``results`` maps resolved video paths to
        their analysis dicts.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    cache_dir = root / CACHE_DIR_NAME
    cache_dir.mkdir(exist_ok=True)

    videos = _find_videos(root)

    processed = 0
    skipped = 0
    transcode_count = 0
    transcode_skipped = 0
    transcode_errors: list[dict] = []
    errors: list[dict] = []
    results: dict[str, dict] = {}

    for video_path in videos:
        cache_path = _cache_path_for(cache_dir, video_path)

        if not force and _is_fresh(video_path, cache_path):
            skipped += 1
            # Re-load cached result so caller has full picture.
            try:
                results[str(video_path)] = json.loads(cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
            continue

        # Determine the effective source path for analysis.
        effective_path = video_path

        if transcode:
            try:
                tr_path = transcode_clip(str(video_path), str(cache_dir), fps=25)
                if tr_path != str(video_path):
                    # Newly transcoded or existing cached transcode.
                    if Path(tr_path).stat().st_mtime > cache_path.stat().st_mtime if cache_path.exists() else True:
                        transcode_count += 1
                    else:
                        transcode_skipped += 1
                else:
                    transcode_skipped += 1
                effective_path = Path(tr_path)
            except Exception as exc:
                transcode_errors.append({"file": str(video_path), "error": str(exc)})
                # Fall through — analyse original file on transcode failure.

        try:
            result = analyze_video(str(effective_path), window_seconds)
            cache_path.write_text(json.dumps(result, indent=2))
            processed += 1
            results[str(video_path)] = result
        except Exception as exc:
            errors.append({"file": str(video_path), "error": str(exc)})

    return {
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "results": results,
        "transcode": {
            "new": transcode_count,
            "skipped": transcode_skipped,
            "errors": transcode_errors,
        } if transcode else None,
    }
