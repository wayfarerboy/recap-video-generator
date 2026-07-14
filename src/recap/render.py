"""Kdenlive project XML generation (MLT-based, doc version 1.1).

Generates a valid ``.kdenlive`` project file from an assignment plan.
Matches kdenlive 23.04+ structure with chains, control_uuid, docproperties.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


def _frames_to_timecode(frames: int, fps: float) -> str:
    """Convert frame count to HH:MM:SS.fff timecode string."""
    seconds = frames / fps
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _add_prop(parent: ET.Element, name: str, value: str) -> ET.Element:
    el = ET.SubElement(parent, "property", {"name": name})
    el.text = value
    return el


def _make_chain(
    mlt: ET.Element,
    chain_id: int,
    resource: str,
    duration_frames: int,
    fps: float,
    is_audio: bool = False,
    video_meta: dict[str, Any] | None = None,
) -> ET.Element:
    """Create a <chain> element for a media source."""
    chain = ET.SubElement(mlt, "chain")
    chain.set("id", f"chain{chain_id}")
    chain.set("out", _frames_to_timecode(duration_frames - 1, fps))

    _add_prop(chain, "length", str(duration_frames))
    _add_prop(chain, "eof", "pause")
    _add_prop(chain, "resource", resource)

    if is_audio:
        _add_prop(chain, "mlt_service", "avformat")
        _add_prop(chain, "audio_index", "0")
        _add_prop(chain, "video_index", "-1")
        _add_prop(chain, "astream", "0")
    else:
        _add_prop(chain, "mlt_service", "avformat-novalidate")
        _add_prop(chain, "audio_index", "1")
        _add_prop(chain, "video_index", "0")
        _add_prop(chain, "vstream", "0")
        _add_prop(chain, "format", "3")
        if video_meta:
            if video_meta.get("width"):
                _add_prop(chain, "meta.media.0.codec.width", str(video_meta["width"]))
            if video_meta.get("height"):
                _add_prop(chain, "meta.media.0.codec.height", str(video_meta["height"]))
            if video_meta.get("fps"):
                _add_prop(chain, "meta.media.0.stream.frame_rate", str(video_meta["fps"]))
            if video_meta.get("rotate") is not None:
                _add_prop(chain, "meta.media.0.codec.rotate", str(video_meta["rotate"]))
            _add_prop(chain, "meta.media.0.codec.pix_fmt", "yuv420p")
            _add_prop(chain, "meta.media.0.codec.colorspace", "709")
            _add_prop(chain, "meta.media.0.codec.color_trc", "1")
            _add_prop(chain, "meta.media.0.codec.name", "h264")
        _add_prop(chain, "meta.media.frame_rate_num", str(int(fps)))
        _add_prop(chain, "meta.media.frame_rate_den", "1")
        _add_prop(chain, "meta.media.colorspace", "709")
        _add_prop(chain, "meta.media.sample_aspect_num", "1")
        _add_prop(chain, "meta.media.sample_aspect_den", "1")

    _add_prop(chain, "seekable", "1")

    # kdenlive-specific metadata
    _add_prop(chain, "kdenlive:folderid", "-1")
    _add_prop(chain, "kdenlive:id", str(chain_id))
    _add_prop(chain, "kdenlive:control_uuid", f"{{{uuid.uuid4()}}}")
    _add_prop(chain, "kdenlive:clip_type", "0" if is_audio else "1")
    _add_prop(chain, "kdenlive:file_size", str(os.path.getsize(resource) if os.path.exists(resource) else 0))
    _add_prop(chain, "kdenlive:file_hash", _file_hash(resource))
    _add_prop(chain, "kdenlive:monitorPosition", "0")

    return chain


def _file_hash(path: str) -> str:
    """Return md5 hex digest of file, or '0'*32 if not found."""
    try:
        if os.path.exists(path):
            return hashlib.md5(Path(path).read_bytes()).hexdigest()
    except OSError:
        pass
    return "0" * 32


def render_kdenlive(
    plan: dict[str, Any],
    music_path: str,
    output_ratio: str = "16:9",
    *,
    fps: float = 30.0,
    output_dir: str | Path | None = None,
    orientation_cache: dict[str, str] | None = None,
    root_dir: str | Path = "/",
) -> str:
    """Generate a ``.kdenlive`` project XML string.

    Parameters
    ----------
    plan : dict
        Assignment plan with an ``assignments`` list.
    music_path : str
        Path to the music file.
    output_ratio : str
        ``"16:9"`` (default) or ``"9:16"``.
    fps : float
        Timeline frame rate (default 30).
    output_dir : str or Path or None
        If given, resource paths are made relative to this directory.
    orientation_cache : dict[str, str] or None
        Mapping of original clip paths → ``"portrait"`` or ``"landscape"``.
    root_dir : str or Path
        Value for the mlt ``root`` attribute (default ``"/"``).

    Returns
    -------
    str
        Complete XML document as a string (UTF-8).
    """
    if output_ratio not in ("16:9", "9:16"):
        raise ValueError(f"Unknown output ratio: {output_ratio!r}. Use '16:9' or '9:16'.")

    assignments: list[dict[str, Any]] = plan.get("assignments", [])

    if output_ratio == "16:9":
        out_w, out_h = 1920, 1080
    else:
        out_w, out_h = 1080, 1920

    plan_fps = plan.get("fps")
    if plan_fps is not None:
        fps = float(plan_fps)

    seq_uuid = f"{{{uuid.uuid4()}}}"
    now_ms = str(int(time.time() * 1000))
    profile_name = f"HD {out_w}x{out_h} {fps}fps"
    da_num, da_den = _aspect_fraction(out_w, out_h)
    fps_num, fps_den = _float_to_fraction(fps)

    # ---- Root -----------------------------------------------------------
    mlt = ET.Element("mlt")
    mlt.set("LC_NUMERIC", "C")
    mlt.set("producer", "main_bin")
    mlt.set("root", "")
    mlt.set("version", "7.37.0")

    # Profile
    profile = ET.SubElement(mlt, "profile")
    profile.set("description", profile_name)
    profile.set("width", str(out_w))
    profile.set("height", str(out_h))
    profile.set("display_aspect_num", str(da_num))
    profile.set("display_aspect_den", str(da_den))
    profile.set("sample_aspect_num", "1")
    profile.set("sample_aspect_den", "1")
    profile.set("frame_rate_num", str(fps_num))
    profile.set("frame_rate_den", str(fps_den))
    profile.set("progressive", "1")
    profile.set("colorspace", "709")

    # ---- Chains (media sources) ------------------------------------------
    next_chain_id = 0

    # Video clips
    for i, a in enumerate(assignments):
        resource = a.get("trim", a["clip"])
        if output_dir is not None:
            resource = str(Path(os.path.relpath(resource, output_dir)))

        duration_s = a.get("source_end", 0) - a.get("source_start", 0)
        dur_frames = max(1, int(duration_s * fps))

        clip_w, clip_h = _probe_dimensions(a.get("trim", a["clip"]))
        orientation = _resolve_orientation(a.get("clip", ""), orientation_cache)

        video_meta = {
            "width": clip_w if clip_w else out_w,
            "height": clip_h if clip_h else out_h,
            "fps": fps,
            "rotate": 90 if orientation == "portrait" else 0,
        }

        _make_chain(mlt, next_chain_id, resource, dur_frames, fps, is_audio=False, video_meta=video_meta)
        a["_chain_id"] = next_chain_id
        a["_dur_frames"] = dur_frames
        a["_orientation"] = orientation
        a["_clip_w"] = clip_w
        a["_clip_h"] = clip_h
        next_chain_id += 1

    # Music chain
    music_resource = music_path
    if output_dir is not None:
        music_resource = str(Path(os.path.relpath(music_path, output_dir)))
    music_duration = _probe_duration(music_path)
    music_frames = max(1, int(music_duration * fps))
    music_chain_id = next_chain_id
    _make_chain(mlt, music_chain_id, music_resource, music_frames, fps, is_audio=True)
    next_chain_id += 1

    # ---- Playlists -------------------------------------------------------
    video_playlist = ET.SubElement(mlt, "playlist")
    video_playlist.set("id", "playlist0")

    audio_playlist = ET.SubElement(mlt, "playlist")
    audio_playlist.set("id", "playlist1")

    # Fill video playlist entries (continuous, no blanks)
    for a in assignments:
        ET.SubElement(video_playlist, "entry", {
            "in": "00:00:00.000",
            "out": _frames_to_timecode(a["_dur_frames"] - 1, fps),
            "producer": f"chain{a['_chain_id']}",
        })

    # Audio entry
    ET.SubElement(audio_playlist, "entry", {
        "in": "00:00:00.000",
        "out": _frames_to_timecode(music_frames - 1, fps),
        "producer": f"chain{music_chain_id}",
    })

    # ---- Tractor ---------------------------------------------------------
    total_frames = sum(a["_dur_frames"] for a in assignments)
    tractor = ET.SubElement(mlt, "tractor")
    tractor.set("id", "tractor0")
    tractor.set("in", "00:00:00.000")
    tractor.set("out", _frames_to_timecode(total_frames - 1, fps))

    _add_prop(tractor, "kdenlive:trackheight", "69")

    # Video track
    ET.SubElement(tractor, "track", {"hide": "video", "producer": "playlist0"})
    # Audio track
    ET.SubElement(tractor, "track", {"hide": "video", "producer": "playlist1"})

    # ---- Sequence properties on tractor ----------------------------------
    _add_prop(tractor, "kdenlive:duration", _frames_to_timecode(total_frames - 1, fps))
    _add_prop(tractor, "kdenlive:maxduration", str(total_frames))
    _add_prop(tractor, "kdenlive:clipname", "Sequence 1")
    _add_prop(tractor, "kdenlive:description", "")
    _add_prop(tractor, "kdenlive:uuid", seq_uuid)
    _add_prop(tractor, "kdenlive:producer_type", "17")
    _add_prop(tractor, "kdenlive:control_uuid", seq_uuid)
    _add_prop(tractor, "kdenlive:id", str(next_chain_id))
    _add_prop(tractor, "kdenlive:clip_type", "0")
    _add_prop(tractor, "kdenlive:file_hash", "0" * 32)
    _add_prop(tractor, "kdenlive:folderid", "-1")
    _add_prop(tractor, "kdenlive:sequenceproperties.activeTrack", "3")
    _add_prop(tractor, "kdenlive:sequenceproperties.audioTarget", "1")
    _add_prop(tractor, "kdenlive:sequenceproperties.disablepreview", "0")
    _add_prop(tractor, "kdenlive:sequenceproperties.documentuuid", seq_uuid)
    _add_prop(tractor, "kdenlive:sequenceproperties.hasAudio", "1")
    _add_prop(tractor, "kdenlive:sequenceproperties.hasVideo", "1")
    _add_prop(tractor, "kdenlive:sequenceproperties.position", "0")
    _add_prop(tractor, "kdenlive:sequenceproperties.scrollPos", "0")
    _add_prop(tractor, "kdenlive:sequenceproperties.tracks", "4")
    _add_prop(tractor, "kdenlive:sequenceproperties.tracksCount", "2")
    _add_prop(tractor, "kdenlive:sequenceproperties.verticalzoom", "1")
    _add_prop(tractor, "kdenlive:sequenceproperties.videoTarget", "2")
    _add_prop(tractor, "kdenlive:sequenceproperties.zonein", "0")
    _add_prop(tractor, "kdenlive:sequenceproperties.zoneout", str(total_frames))
    _add_prop(tractor, "kdenlive:sequenceproperties.zoom", "8")
    _add_prop(tractor, "kdenlive:sequenceproperties.groups", "[\n]\n")

    # ---- Wrapper tractor -------------------------------------------------
    wrapper = ET.SubElement(mlt, "tractor")
    wrapper.set("id", "tractor1")
    wrapper.set("in", "00:00:00.000")
    wrapper.set("out", _frames_to_timecode(total_frames - 1, fps))
    _add_prop(wrapper, "kdenlive:uuid", f"{{{uuid.uuid4()}}}")

    ET.SubElement(wrapper, "track", {"producer": "tractor0"})

    # ---- Main bin playlist ------------------------------------------------
    main_bin = ET.SubElement(mlt, "playlist")
    main_bin.set("id", "main_bin")

    # Entry for the sequence (wrapper tractor)
    ET.SubElement(main_bin, "entry", {
        "producer": "tractor1",
        "in": "00:00:00.000",
        "out": _frames_to_timecode(total_frames - 1, fps),
    })

    # Doc properties
    _add_prop(main_bin, "kdenlive:folder.-1.2", "Sequences")
    _add_prop(main_bin, "kdenlive:sequenceFolder", "2")
    _add_prop(main_bin, "kdenlive:docproperties.activetimeline", seq_uuid)
    _add_prop(main_bin, "kdenlive:docproperties.audioChannels", "2")
    _add_prop(main_bin, "kdenlive:docproperties.binsort", "0")
    _add_prop(main_bin, "kdenlive:docproperties.browserurl", "")
    _add_prop(main_bin, "kdenlive:docproperties.documentid", now_ms)
    _add_prop(main_bin, "kdenlive:docproperties.enableTimelineZone", "0")
    _add_prop(main_bin, "kdenlive:docproperties.enableexternalproxy", "0")
    _add_prop(main_bin, "kdenlive:docproperties.enableproxy", "0")
    _add_prop(main_bin, "kdenlive:docproperties.externalproxyparams", "")
    _add_prop(main_bin, "kdenlive:docproperties.generateimageproxy", "0")
    _add_prop(main_bin, "kdenlive:docproperties.generateproxy", "0")
    _add_prop(main_bin, "kdenlive:docproperties.kdenliveversion", "23.04.0")
    _add_prop(main_bin, "kdenlive:docproperties.opensequences", seq_uuid)
    _add_prop(main_bin, "kdenlive:docproperties.previewextension", "")
    _add_prop(main_bin, "kdenlive:docproperties.previewparameters", "")
    _add_prop(main_bin, "kdenlive:docproperties.profile", profile_name.lower().replace(" ", "_"))
    _add_prop(main_bin, "kdenlive:docproperties.proxyextension", "")
    _add_prop(main_bin, "kdenlive:docproperties.proxyimageminsize", "2000")
    _add_prop(main_bin, "kdenlive:docproperties.proxyimagesize", "800")
    _add_prop(main_bin, "kdenlive:docproperties.proxyminsize", "1000")
    _add_prop(main_bin, "kdenlive:docproperties.proxyparams", "")
    _add_prop(main_bin, "kdenlive:docproperties.proxyresize", "640")
    _add_prop(main_bin, "kdenlive:docproperties.renderexportaudio", "0")
    _add_prop(main_bin, "kdenlive:docproperties.renderfullcolorrange", "0")
    _add_prop(main_bin, "kdenlive:docproperties.rendermode", "0")
    _add_prop(main_bin, "kdenlive:docproperties.renderplay", "0")

    # ---- Serialize -------------------------------------------------------
    ET.indent(mlt, space=" ")
    xml_body = ET.tostring(mlt, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + xml_body


# ---------------------------------------------------------------------------
# Helpers (from original, mostly unchanged)
# ---------------------------------------------------------------------------

def _resolve_orientation(
    clip_path: str,
    orientation_cache: dict[str, str] | None,
) -> str:
    if not clip_path:
        return "landscape"
    if orientation_cache and clip_path in orientation_cache:
        return orientation_cache[clip_path]
    cached = _read_orientation_from_cache(clip_path)
    if cached:
        return cached
    w, h = _probe_dimensions(clip_path)
    if w > 0 and h > 0:
        return "portrait" if h > w else "landscape"
    return "landscape"


def _read_orientation_from_cache(clip_path: str) -> str | None:
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


def _probe_dimensions(video_path: str) -> tuple[int, int]:
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
    except Exception:
        pass
    return (0, 0)


def _probe_duration(audio_path: str) -> float:
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
    except Exception:
        pass
    return 10.0


def _aspect_fraction(w: int, h: int) -> tuple[int, int]:
    from math import gcd
    g = gcd(w, h)
    return w // g, h // g


def _float_to_fraction(f: float, max_den: int = 100000) -> tuple[int, int]:
    from fractions import Fraction
    known: dict[float, tuple[int, int]] = {
        23.976: (24000, 1001),
        29.97: (30000, 1001),
        59.94: (60000, 1001),
    }
    for rate, frac in known.items():
        if abs(f - rate) < 0.001:
            return frac
    frac = Fraction(f).limit_denominator(max_den)
    return frac.numerator, frac.denominator
