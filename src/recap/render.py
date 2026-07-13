"""Kdenlive project XML generation (MLT-based, doc version 1.1).

Generates a valid ``.kdenlive`` project file from an assignment plan
(produced by ``recap assign`` and optionally updated by ``recap trim``).
Per-clip MLT transforms handle rotation and centre-crop for mixed
orientation footage.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_kdenlive(
    plan: dict[str, Any],
    music_path: str,
    output_ratio: str = "16:9",
    *,
    fps: float = 30.0,
    output_dir: str | Path | None = None,
    orientation_cache: dict[str, str] | None = None,
) -> str:
    """Generate a ``.kdenlive`` project XML string.

    Parameters
    ----------
    plan : dict
        Assignment plan with an ``assignments`` list.  Each entry must
        have ``clip`` (original path), optionally ``trim`` (trimmed path),
        ``source_start``, and ``source_end``.
    music_path : str
        Path to the music file used for the audio track.
    output_ratio : str
        ``"16:9"`` (default) or ``"9:16"``.
    fps : float
        Timeline frame rate (default 30).
    output_dir : str or Path or None
        If given, resource paths in the XML are made relative to this
        directory for portability.
    orientation_cache : dict[str, str] or None
        Mapping of original clip paths → ``"portrait"`` or ``"landscape"``.
        When ``None``, orientation is probed from ``.recap-cache/`` or
        the trimmed file's pixel dimensions.

    Returns
    -------
    str
        Complete XML document as a string (UTF-8).
    """
    if output_ratio not in ("16:9", "9:16"):
        raise ValueError(f"Unknown output ratio: {output_ratio!r}. Use '16:9' or '9:16'.")

    assignments: list[dict[str, Any]] = plan.get("assignments", [])

    # Resolve output dimensions
    if output_ratio == "16:9":
        out_w, out_h = 1920, 1080
    else:
        out_w, out_h = 1080, 1920

    # Choose fps from plan if present
    plan_fps = plan.get("fps")
    if plan_fps is not None:
        fps = float(plan_fps)

    # ------------------------------------------------------------------
    # Build XML tree
    # ------------------------------------------------------------------

    mlt = ET.Element("mlt")
    mlt.set("version", "1.1")
    mlt.set("title", "Recap Video")
    mlt.set("producer", "main_bin")

    # Profile
    _add_profile(mlt, out_w, out_h, fps)

    # Main bin playlist (kdenlive convention)
    main_bin = ET.SubElement(mlt, "playlist")
    main_bin.set("id", "main_bin")

    # Pre-declare playlists
    video_playlist = ET.SubElement(mlt, "playlist")
    video_playlist.set("id", "playlist0")

    audio_playlist = ET.SubElement(mlt, "playlist")
    audio_playlist.set("id", "playlist1")

    # Tractor (timeline)
    tractor = ET.SubElement(mlt, "tractor")
    tractor.set("id", "tractor0")
    tractor.set("title", "Timeline 1")
    ET.SubElement(tractor, "property", {"name": "kdenlive:trackheight"}).text = "69"

    # Video track
    ET.SubElement(tractor, "track", {"producer": "playlist0"})
    # Audio track
    ET.SubElement(tractor, "track", {"producer": "playlist1", "hide": "video"})

    # ------------------------------------------------------------------
    # Producers + entries for each clip
    # ------------------------------------------------------------------

    for i, a in enumerate(assignments):
        prod_id = f"producer{i}"

        # Use trimmed path when available, fall back to original clip
        resource_path = a.get("trim", a["clip"])

        # Make relative to output_dir for portability
        if output_dir is not None:
            resource_path = _relative_path(resource_path, output_dir)

        # Duration from plan (trimmed extract = source_end - source_start)
        clip_duration = a.get("source_end", 0) - a.get("source_start", 0)
        clip_frames = max(1, int(clip_duration * fps))

        # Create producer
        producer = ET.SubElement(mlt, "producer")
        producer.set("id", prod_id)
        producer.set("in", "0")
        producer.set("out", str(clip_frames - 1))

        ET.SubElement(producer, "property", {"name": "resource"}).text = resource_path
        ET.SubElement(producer, "property", {"name": "mlt_type"}).text = "producer"
        ET.SubElement(producer, "property", {"name": "mlt_service"}).text = "avformat-novalidate"
        ET.SubElement(producer, "property", {"name": "length"}).text = str(clip_frames)

        # Add to main bin
        ET.SubElement(main_bin, "entry", {
            "producer": prod_id,
            "in": "0",
            "out": str(clip_frames - 1),
        })

        # Add entry to video playlist
        entry = ET.SubElement(video_playlist, "entry")
        entry.set("producer", prod_id)
        entry.set("in", "0")
        entry.set("out", str(clip_frames - 1))

        # ------------------------------------------------------------------
        # Orientation and transform filters
        # ------------------------------------------------------------------
        orientation = _resolve_orientation(a.get("clip", ""), orientation_cache)
        clip_w, clip_h = _probe_dimensions(a.get("trim", a["clip"]))

        # Apply per-clip transforms
        _add_transforms(entry, orientation, clip_w, clip_h, out_w, out_h, output_ratio)

    # ------------------------------------------------------------------
    # Music producer + audio track entry
    # ------------------------------------------------------------------

    music_resource = music_path
    if output_dir is not None:
        music_resource = _relative_path(music_path, output_dir)

    music_duration = _probe_duration(music_path)
    music_frames = max(1, int(music_duration * fps))

    music_producer = ET.SubElement(mlt, "producer")
    music_producer.set("id", "producer_music")
    music_producer.set("in", "0")
    music_producer.set("out", str(music_frames - 1))

    ET.SubElement(music_producer, "property", {"name": "resource"}).text = music_resource
    ET.SubElement(music_producer, "property", {"name": "mlt_type"}).text = "producer"
    ET.SubElement(music_producer, "property", {"name": "mlt_service"}).text = "avformat-novalidate"

    # Music entry in audio playlist (starts at 0, plays full duration)
    ET.SubElement(audio_playlist, "entry", {
        "producer": "producer_music",
        "in": "0",
        "out": str(music_frames - 1),
    })

    # Music in main bin
    ET.SubElement(main_bin, "entry", {
        "producer": "producer_music",
        "in": "0",
        "out": str(music_frames - 1),
    })

    # ------------------------------------------------------------------
    # Serialize
    # ------------------------------------------------------------------
    ET.indent(mlt, space="  ")
    xml_body = ET.tostring(mlt, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + xml_body


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _add_profile(
    mlt: ET.Element,
    width: int,
    height: int,
    fps: float,
) -> None:
    """Add an MLT ``<profile>`` element with the given dimensions."""
    # Derive display aspect as reduced fraction
    da_num, da_den = _aspect_fraction(width, height)

    fps_num, fps_den = _float_to_fraction(fps)

    profile = ET.SubElement(mlt, "profile")
    profile.set("description", f"HD {width}x{height} {fps}fps")
    profile.set("width", str(width))
    profile.set("height", str(height))
    profile.set("display_aspect_num", str(da_num))
    profile.set("display_aspect_den", str(da_den))
    profile.set("sample_aspect_num", "1")
    profile.set("sample_aspect_den", "1")
    profile.set("frame_rate_num", str(fps_num))
    profile.set("frame_rate_den", str(fps_den))
    profile.set("progressive", "1")
    profile.set("colorspace", "709")


def _add_transforms(
    entry: ET.Element,
    orientation: str,
    clip_w: int,
    clip_h: int,
    out_w: int,
    out_h: int,
    output_ratio: str,
) -> None:
    """Attach MLT filter elements to *entry* for rotation and crop/scale.

    Rotation is applied when orientation metadata reports ``portrait``.
    Centre-crop is applied when clip and output aspect ratios differ
    (e.g. portrait clip in a 16:9 output timeline).
    """
    # Rotation: applied based on orientation metadata alone (not gated on
    # dimension probing, since files may not exist at render time).
    if orientation == "portrait":
        _add_filter(entry, "affine", {"transition.rotate": "90"})

    # Centre-crop: only when we have valid dimensions to compute with.
    if clip_w <= 0 or clip_h <= 0:
        return

    out_aspect = out_w / out_h
    clip_aspect = clip_w / clip_h

    # If clip orientation says portrait but dimensions are landscape
    # (rotation tag present), swap for aspect comparison.
    if orientation == "portrait" and clip_w > clip_h:
        clip_aspect = clip_h / clip_w

    # Tolerance for aspect ratio matching
    if abs(clip_aspect - out_aspect) < 0.02:
        return  # same aspect, no crop needed

    # Compute normalized crop rect: take the centre of the clip
    # that matches the output aspect ratio.
    if clip_aspect > out_aspect:
        # Clip is wider → crop horizontally (pillarbox → fill)
        target_w = clip_h * out_aspect
        norm_x = (clip_w - target_w) / (2 * clip_w)
        norm_w = target_w / clip_w
        rect = f"{norm_x:.6f} 0 {norm_w:.6f} 1"
    else:
        # Clip is taller → crop vertically (letterbox → fill)
        target_h = clip_w / out_aspect
        norm_y = (clip_h - target_h) / (2 * clip_h)
        norm_h = target_h / clip_h
        rect = f"0 {norm_y:.6f} 1 {norm_h:.6f}"

    # The rect is in normalized source coordinates, fill=1 means scale to
    # fill output frame, distort=0 preserves aspect (no stretch).
    _add_filter(entry, "affine", {
        "transition.rect": rect,
        "transition.fill": "1",
        "transition.distort": "0",
    })


def _add_filter(
    parent: ET.Element,
    service: str,
    properties: dict[str, str],
) -> ET.Element:
    """Create an MLT ``<filter>`` element with a service name and properties."""
    filt = ET.SubElement(parent, "filter")
    ET.SubElement(filt, "property", {"name": "mlt_service"}).text = service
    for name, value in properties.items():
        ET.SubElement(filt, "property", {"name": name}).text = value
    return filt


# ---------------------------------------------------------------------------
# Orientation resolution
# ---------------------------------------------------------------------------

def _resolve_orientation(
    clip_path: str,
    orientation_cache: dict[str, str] | None,
) -> str:
    """Return ``"portrait"`` or ``"landscape"`` for *clip_path*.

    Checks *orientation_cache* first, then tries the ``.recap-cache/``
    analysis cache, then falls back to pixel dimensions.
    """
    if not clip_path:
        return "landscape"

    # 1. Explicit cache
    if orientation_cache and clip_path in orientation_cache:
        return orientation_cache[clip_path]

    # 2. Batch analysis cache
    cached = _read_orientation_from_cache(clip_path)
    if cached:
        return cached

    # 3. Probe pixel dimensions
    w, h = _probe_dimensions(clip_path)
    if w > 0 and h > 0:
        return "portrait" if h > w else "landscape"

    return "landscape"


def _read_orientation_from_cache(clip_path: str) -> str | None:
    """Try to read orientation from the batch analysis cache (``.recap-cache/``)."""
    try:
        clip = Path(clip_path).resolve()
        cache_dir = clip.parent / ".recap-cache"
        if not cache_dir.is_dir():
            return None

        digest = hashlib.sha256(str(clip).encode()).hexdigest()
        cache_file = cache_dir / f"{digest}.json"
        if not cache_file.exists():
            return None

        data = json.loads(cache_file.read_text())
        return data.get("orientation")
    except (OSError, json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# ffprobe helpers
# ---------------------------------------------------------------------------

def _probe_dimensions(video_path: str) -> tuple[int, int]:
    """Return ``(width, height)`` of the first video stream, or ``(0, 0)``."""
    if not video_path or not Path(video_path).exists():
        return (0, 0)

    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode == 0 and proc.stdout.strip():
            parts = proc.stdout.strip().split(",")
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired, ValueError):
        pass
    return (0, 0)


def _probe_duration(audio_path: str) -> float:
    """Return duration in seconds of *audio_path*, or 10.0 on failure."""
    if not audio_path or not Path(audio_path).exists():
        return 10.0

    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode == 0 and proc.stdout.strip():
            return float(proc.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired, ValueError):
        pass
    return 10.0


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _relative_path(abs_path: str, base_dir: str | Path) -> str:
    """Convert *abs_path* to a path relative to *base_dir*.

    If the path cannot be made relative (different drive on Windows, etc.),
    returns *abs_path* unchanged.
    """
    try:
        return str(Path(os.path.relpath(abs_path, base_dir)))
    except ValueError:
        return abs_path


def _aspect_fraction(w: int, h: int) -> tuple[int, int]:
    """Reduce width×height to a numerator/denominator pair."""
    from math import gcd
    g = gcd(w, h)
    return w // g, h // g


def _float_to_fraction(f: float, max_den: int = 100000) -> tuple[int, int]:
    """Convert a float frame rate to a numerator/denominator pair.

    Common NTSC rates are handled explicitly; other rates use
    ``Fraction.limit_denominator`` for a close rational approximation.
    """
    from fractions import Fraction

    # Common NTSC frame rates — exact rational representations
    known: dict[float, tuple[int, int]] = {
        23.976: (24000, 1001),
        29.97: (30000, 1001),
        59.94: (60000, 1001),
    }
    # Match with a small tolerance for floating-point drift
    for rate, frac in known.items():
        if abs(f - rate) < 0.001:
            return frac

    frac = Fraction(f).limit_denominator(max_den)
    return frac.numerator, frac.denominator
