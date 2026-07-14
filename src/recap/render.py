"""Kdenlive project XML generation matching kdenlive's native format.

Generates a valid ``.kdenlive`` project file that mirrors the structure
kdenlive produces when saving: ``<chain>`` elements for all bin items,
separate sub-tractors for video/audio, a black background producer, and
``qtblend`` transitions for video compositing.
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

from recap.plan import Plan


def _add_prop(parent: ET.Element, name: str, value: str) -> ET.Element:
    el = ET.SubElement(parent, "property", {"name": name})
    el.text = value
    return el


def _file_hash(path: str) -> str:
    """Return md5 hex digest of file, or '0'*32 if not found."""
    try:
        if os.path.exists(path):
            return hashlib.md5(Path(path).read_bytes()).hexdigest()
    except OSError:
        pass
    return "0" * 32


def _seconds_to_timecode(seconds: float) -> str:
    """Convert seconds to HH:MM:SS.fff timecode string."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def render_kdenlive(
    plan: Plan,
    music_path: str,
    output_ratio: str = "16:9",
    *,
    fps: float = 25.0,
    output_dir: str | Path | None = None,
    orientation_cache: dict[str, str] | None = None,
    root_dir: str | Path = "/",
) -> str:
    """Generate a ``.kdenlive`` project XML string."""
    if output_ratio not in ("16:9", "9:16"):
        raise ValueError(f"Unknown output ratio: {output_ratio!r}. Use '16:9' or '9:16'.")

    assignments = plan.assignments

    if output_ratio == "16:9":
        out_w, out_h = 1920, 1080
    else:
        out_w, out_h = 1080, 1920

    output_dir_path = Path(output_dir).resolve() if output_dir else Path.cwd()
    root_dir_str = str(output_dir_path)
    seq_uuid = f"{{{uuid.uuid4()}}}"
    master_uuid = f"{{{uuid.uuid4()}}}"
    now_ms = str(int(time.time() * 1000))
    profile_name = f"HD {out_h}p {fps:.0f} fps"
    da_num, da_den = _aspect_fraction(out_w, out_h)
    fps_num, fps_den = _float_to_fraction(fps)

    # ---- Root -----------------------------------------------------------
    mlt = ET.Element("mlt", {
        "LC_NUMERIC": "C",
        "producer": "main_bin",
        "root": root_dir_str,
        "version": "7.39.0",
    })

    ET.SubElement(mlt, "profile", {
        "colorspace": "709",
        "description": profile_name,
        "display_aspect_den": str(da_den),
        "display_aspect_num": str(da_num),
        "frame_rate_den": str(fps_den),
        "frame_rate_num": str(fps_num),
        "height": str(out_h),
        "progressive": "1",
        "sample_aspect_den": "1",
        "sample_aspect_num": "1",
        "width": str(out_w),
    })

    # ---- Music duration (needed early for timing) -----------------------
    music_dur = _probe_duration(music_path)
    # MLT treats 'out' as inclusive: the frame at 'out' is played.
    # We want the sequence to last exactly music_dur seconds, so the last
    # frame is at music_dur - 1/fps.  Without this, every tractor and
    # chain is one frame too long, accumulating drift across the project.
    mlt_fps_inv = 1.0 / fps
    music_mlt_out = music_dur - mlt_fps_inv
    music_tc = _seconds_to_timecode(music_mlt_out)
    total_frames = max(1, int(music_dur * fps))

    # ---- Video chains ---------------------------------------------------
    chain_id = 0
    chain_dur_seconds: list[float] = []
    chain_kdenlive_ids: list[int] = []
    for a in assignments:
        resource = a.clip

        # Use relative paths; resolve for probe/hash
        res_path = Path(resource)
        res_abs = str(res_path)
        full_path = res_path if res_path.is_absolute() else (output_dir_path / res_path)
        file_h = _file_hash(str(full_path.resolve()))
        file_size = "0"
        if full_path.exists():
            file_size = str(full_path.stat().st_size)

        # Full source duration (probed via ffprobe, fallback to 10.0)
        full_dur_s = _probe_duration(str(full_path))
        chain_dur_seconds.append(full_dur_s)
        # MLT inclusive out: the chain's last frame is at full_dur_s - 1/fps
        full_dur_tc = _seconds_to_timecode(full_dur_s - mlt_fps_inv)

        chain = ET.SubElement(mlt, "chain", {"id": f"chain{chain_id}", "out": full_dur_tc})
        _add_prop(chain, "length", str(max(1, int(full_dur_s * fps))))
        _add_prop(chain, "eof", "pause")
        _add_prop(chain, "resource", res_abs)
        _add_prop(chain, "mlt_service", "avformat-novalidate")
        _add_prop(chain, "seekable", "1")
        _add_prop(chain, "audio_index", "1")
        _add_prop(chain, "video_index", "0")
        _add_prop(chain, "astream", "0")
        _add_prop(chain, "kdenlive:folderid", "-1")
        _add_prop(chain, "kdenlive:id", str(chain_id))
        _add_prop(chain, "kdenlive:control_uuid", f"{{{uuid.uuid4()}}}")
        _add_prop(chain, "kdenlive:clip_type", "0")
        _add_prop(chain, "kdenlive:file_size", file_size)
        _add_prop(chain, "kdenlive:file_hash", file_h)
        _add_prop(chain, "kdenlive:monitorPosition", "0")
        chain_kdenlive_ids.append(chain_id)
        chain_id += 1

    num_video_clips = chain_id

    # ---- Black background producer --------------------------------------
    black_prod = ET.SubElement(mlt, "producer", {
        "id": "producer0",
        "in": "00:00:00.000",
        "out": music_tc,
    })
    _add_prop(black_prod, "length", str(total_frames))
    _add_prop(black_prod, "eof", "pause")
    _add_prop(black_prod, "resource", "black")
    _add_prop(black_prod, "mlt_service", "color")
    _add_prop(black_prod, "mlt_image_format", "rgba")
    _add_prop(black_prod, "kdenlive:id", str(chain_id + 1))

    # ---- Music chain ----------------------------------------------------
    music_path_obj = Path(music_path)
    if not music_path_obj.is_absolute():
        music_path_obj = output_dir_path / music_path_obj
    music_abs = str(music_path_obj.resolve())
    music_file_h = _file_hash(music_abs)
    music_file_size = str(music_path_obj.stat().st_size) if music_path_obj.exists() else "0"

    music_chain = ET.SubElement(mlt, "chain", {"id": f"chain{chain_id}", "out": music_tc})
    _add_prop(music_chain, "length", str(total_frames))
    _add_prop(music_chain, "eof", "pause")
    _add_prop(music_chain, "resource", music_abs)
    _add_prop(music_chain, "mlt_service", "avformat-novalidate")
    _add_prop(music_chain, "seekable", "1")
    _add_prop(music_chain, "audio_index", "0")
    _add_prop(music_chain, "video_index", "-1")
    _add_prop(music_chain, "astream", "0")
    _add_prop(music_chain, "kdenlive:folderid", "-1")
    _add_prop(music_chain, "kdenlive:id", str(chain_id))
    _add_prop(music_chain, "kdenlive:control_uuid", f"{{{uuid.uuid4()}}}")
    _add_prop(music_chain, "kdenlive:clip_type", "1")
    _add_prop(music_chain, "kdenlive:file_size", music_file_size)
    _add_prop(music_chain, "kdenlive:file_hash", music_file_h)
    _add_prop(music_chain, "kdenlive:kextractor", "1")
    _add_prop(music_chain, "kdenlive:monitorPosition", "0")
    music_chain_id = chain_id
    chain_id += 1

    # ---- tractor0: music sub-tractor (hide="video") ---------------------
    pl_a_music = ET.SubElement(mlt, "playlist", {"id": "playlist0"})
    ET.SubElement(pl_a_music, "entry", {
        "producer": f"chain{music_chain_id}",
        "in": "00:00:00.000",
        "out": music_tc,
    })
    pl_a_empty = ET.SubElement(mlt, "playlist", {"id": "playlist1"})
    tractor0 = ET.SubElement(mlt, "tractor", {
        "id": "tractor0",
        "in": "00:00:00.000",
        "out": music_tc,
    })
    ET.SubElement(tractor0, "track", {"producer": "playlist0", "hide": "video"})
    ET.SubElement(tractor0, "track", {"producer": "playlist1", "hide": "video"})
    _add_prop(tractor0, "kdenlive:audio_track", "1")
    _add_prop(tractor0, "kdenlive:trackheight", "62")
    _add_prop(tractor0, "kdenlive:timeline_active", "1")

    # ---- tractor1: video sub-tractor (hide="audio") ---------------------
    pl_video = ET.SubElement(mlt, "playlist", {"id": "playlist2"})

    # No blank entries in playlist2 — Kdenlive may misrender colour
    # producers mixed with chain entries.  Instead, fill gaps (beat-offset
    # at start, or duration loss from clamped clips) by pulling the
    # *following* clip's source in-point backward so the gap disappears.
    timeline_pos = 0.0
    for i in range(num_video_clips):
        a = assignments[i]
        target = a.target_start

        # Beat slot duration: how long this clip occupies on the timeline
        if i + 1 < num_video_clips:
            next_target = assignments[i + 1].target_start
            slot_dur = next_target - target
        else:
            slot_dur = music_dur - target

        source_start = a.source_start
        # Absorb any gap before this clip by pulling its in-point back
        # and extending its slot duration so the clip fills the gap
        # on the timeline.  This avoids blank entries in playlist2 while
        # keeping beat-aligned content at the correct timeline position.
        gap = max(0.0, target - timeline_pos)
        if gap > 0.001:
            source_start = max(0.0, source_start - gap)
            slot_dur = slot_dur + gap

        in_tc = _seconds_to_timecode(source_start)
        # Use source_start + slot_dur so clip exactly fills the beat slot,
        # but clamp to the source file's probed duration so we never ask
        # MLT / Kdenlive to play past end-of-file (would cause silent
        # duration loss and cumulative beat drift).
        desired_out = min(source_start + slot_dur, chain_dur_seconds[i])
        # MLT treats playlist entry 'out' as INCLUSIVE: the frame at
        # 'out' is played.  Without this correction, every clip plays
        # one extra frame (1/fps seconds), accumulating drift across
        # the timeline.
        out_seconds = max(source_start, desired_out - mlt_fps_inv)
        out_tc = _seconds_to_timecode(out_seconds)
        # MLT timeline duration with inclusive out = out - in + 1/fps
        actual_dur = out_seconds - source_start + mlt_fps_inv
        entry = ET.SubElement(pl_video, "entry", {
            "producer": f"chain{i}",
            "in": in_tc,
            "out": out_tc,
        })
        _add_prop(entry, "kdenlive:id", str(chain_kdenlive_ids[i]))
        timeline_pos = timeline_pos + actual_dur
    pl_v_empty = ET.SubElement(mlt, "playlist", {"id": "playlist3"})
    tractor1 = ET.SubElement(mlt, "tractor", {
        "id": "tractor1",
        "in": "00:00:00.000",
        "out": music_tc,
    })
    ET.SubElement(tractor1, "track", {"producer": "playlist2", "hide": "audio"})
    ET.SubElement(tractor1, "track", {"producer": "playlist3", "hide": "audio"})
    _add_prop(tractor1, "kdenlive:trackheight", "62")
    _add_prop(tractor1, "kdenlive:timeline_active", "")
    _add_prop(tractor1, "kdenlive:collapsed", "0")

    # ---- tractor2: empty (hide="audio") ---------------------------------
    pl_empty0 = ET.SubElement(mlt, "playlist", {"id": "playlist4"})
    pl_empty1 = ET.SubElement(mlt, "playlist", {"id": "playlist5"})
    tractor2 = ET.SubElement(mlt, "tractor", {
        "id": "tractor2",
        "in": "00:00:00.000",
        "out": music_tc,
    })
    ET.SubElement(tractor2, "track", {"producer": "playlist4", "hide": "audio"})
    ET.SubElement(tractor2, "track", {"producer": "playlist5", "hide": "audio"})
    _add_prop(tractor2, "kdenlive:trackheight", "62")
    _add_prop(tractor2, "kdenlive:timeline_active", "")

    # ---- Master tractor (4 tracks: black + tractor0 + tractor1 + tractor2)
    master = ET.SubElement(mlt, "tractor", {
        "id": master_uuid,
        "in": "00:00:00.000",
        "out": music_tc,
    })
    ET.SubElement(master, "track", {"producer": "producer0"})
    ET.SubElement(master, "track", {"producer": "tractor0"})
    ET.SubElement(master, "track", {"producer": "tractor1"})
    ET.SubElement(master, "track", {"producer": "tractor2"})

    # Transitions: mix for empty, qtblend for music and video
    t0 = ET.SubElement(master, "transition", {"id": "transition0"})
    _add_prop(t0, "a_track", "0")
    _add_prop(t0, "b_track", "1")
    _add_prop(t0, "mlt_service", "mix")
    _add_prop(t0, "kdenlive_id", "mix")
    _add_prop(t0, "internal_added", "237")
    _add_prop(t0, "always_active", "1")
    _add_prop(t0, "accepts_blanks", "1")
    _add_prop(t0, "sum", "1")

    t1 = ET.SubElement(master, "transition", {"id": "transition1"})
    _add_prop(t1, "a_track", "0")
    _add_prop(t1, "b_track", "2")
    _add_prop(t1, "compositing", "0")
    _add_prop(t1, "distort", "0")
    _add_prop(t1, "rotate_center", "0")
    _add_prop(t1, "mlt_service", "qtblend")
    _add_prop(t1, "kdenlive_id", "qtblend")
    _add_prop(t1, "internal_added", "237")
    _add_prop(t1, "always_active", "1")

    t2 = ET.SubElement(master, "transition", {"id": "transition2"})
    _add_prop(t2, "a_track", "0")
    _add_prop(t2, "b_track", "3")
    _add_prop(t2, "compositing", "0")
    _add_prop(t2, "distort", "0")
    _add_prop(t2, "rotate_center", "0")
    _add_prop(t2, "mlt_service", "qtblend")
    _add_prop(t2, "kdenlive_id", "qtblend")
    _add_prop(t2, "internal_added", "237")
    _add_prop(t2, "always_active", "1")

    # Sequence properties on master tractor
    _add_prop(master, "kdenlive:duration", music_tc)
    _add_prop(master, "kdenlive:maxduration", str(total_frames))
    _add_prop(master, "kdenlive:clipname", "Sequence 1")
    _add_prop(master, "kdenlive:description", "")
    _add_prop(master, "kdenlive:uuid", master_uuid)
    _add_prop(master, "kdenlive:id", str(chain_id + 1))
    _add_prop(master, "kdenlive:producer_type", "17")
    _add_prop(master, "kdenlive:control_uuid", master_uuid)
    _add_prop(master, "kdenlive:clip_type", "0")
    _add_prop(master, "kdenlive:file_hash", "0" * 32)
    _add_prop(master, "kdenlive:folderid", "-1")
    _add_prop(master, "kdenlive:trackheight", "69")
    _add_prop(master, "kdenlive:sequenceproperties.activeTrack", "0")
    _add_prop(master, "kdenlive:sequenceproperties.audioTarget", "1")
    _add_prop(master, "kdenlive:sequenceproperties.disablepreview", "0")
    _add_prop(master, "kdenlive:sequenceproperties.documentuuid", seq_uuid)
    _add_prop(master, "kdenlive:sequenceproperties.hasAudio", "1")
    _add_prop(master, "kdenlive:sequenceproperties.hasVideo", "1")
    _add_prop(master, "kdenlive:sequenceproperties.position", "0")
    _add_prop(master, "kdenlive:sequenceproperties.scrollPos", "0")
    _add_prop(master, "kdenlive:sequenceproperties.tracks", "4")
    _add_prop(master, "kdenlive:sequenceproperties.tracksCount", "3")
    _add_prop(master, "kdenlive:sequenceproperties.verticalzoom", "1")
    _add_prop(master, "kdenlive:sequenceproperties.videoTarget", "2")
    _add_prop(master, "kdenlive:sequenceproperties.zonein", "0")
    _add_prop(master, "kdenlive:sequenceproperties.zoneout", str(total_frames))
    _add_prop(master, "kdenlive:sequenceproperties.zoom", "8")
    _add_prop(master, "kdenlive:sequenceproperties.groups", "[\n]\n")
    _add_prop(master, "kdenlive:sequenceproperties.guides", "[\n]\n")

    # Music chain entry in main_bin — use a separate chain49 (duplicate ref)
    # The timeline uses chain48; the bin reference uses chain49.
    chain49 = ET.SubElement(mlt, "chain", {"id": "chain49", "out": music_tc})
    _add_prop(chain49, "resource", music_abs)

    # ---- Main bin playlist ----------------------------------------------
    main_bin = ET.SubElement(mlt, "playlist", {"id": "main_bin"})

    # Video chains
    for i in range(num_video_clips):
        dur = _seconds_to_timecode(chain_dur_seconds[i])
        ET.SubElement(main_bin, "entry", {
            "producer": f"chain{i}",
            "in": "00:00:00.000",
            "out": dur,
        })

    # Sequence entry
    ET.SubElement(main_bin, "entry", {
        "producer": master_uuid,
        "in": "00:00:00.000",
        "out": music_tc,
    })

    # Music bin entry (chain49, not chain48)
    ET.SubElement(main_bin, "entry", {
        "producer": "chain49",
        "in": "00:00:00.000",
        "out": music_tc,
    })

    # Doc properties
    _add_prop(main_bin, "kdenlive:folder.-1.2", "Sequences")
    _add_prop(main_bin, "kdenlive:sequenceFolder", "2")
    _add_prop(main_bin, "kdenlive:docproperties.activetimeline", master_uuid)
    _add_prop(main_bin, "kdenlive:docproperties.audioChannels", "2")
    _add_prop(main_bin, "kdenlive:docproperties.binsort", "0")
    _add_prop(main_bin, "kdenlive:docproperties.browserurl", "")
    _add_prop(main_bin, "kdenlive:docproperties.documentid", now_ms)
    _add_prop(main_bin, "kdenlive:docproperties.enableTimelineZone", "0")
    _add_prop(main_bin, "kdenlive:docproperties.enableexternalproxy", "0")
    _add_prop(main_bin, "kdenlive:docproperties.enableproxy", "0")
    _add_prop(main_bin, "kdenlive:docproperties.generateimageproxy", "0")
    _add_prop(main_bin, "kdenlive:docproperties.generateproxy", "0")
    _add_prop(main_bin, "kdenlive:docproperties.kdenliveversion", "23.04.0")
    _add_prop(main_bin, "kdenlive:docproperties.opensequences", master_uuid)
    _add_prop(main_bin, "kdenlive:docproperties.profile", profile_name.lower().replace(" ", "_"))
    _add_prop(main_bin, "kdenlive:docproperties.proxyimageminsize", "2000")
    _add_prop(main_bin, "kdenlive:docproperties.proxyimagesize", "800")
    _add_prop(main_bin, "kdenlive:docproperties.proxyminsize", "1000")
    _add_prop(main_bin, "kdenlive:docproperties.proxyresize", "640")
    _add_prop(main_bin, "kdenlive:docproperties.renderexportaudio", "0")
    _add_prop(main_bin, "kdenlive:docproperties.renderfullcolorrange", "0")
    _add_prop(main_bin, "kdenlive:docproperties.rendermode", "0")
    _add_prop(main_bin, "kdenlive:docproperties.renderplay", "0")
    _add_prop(main_bin, "kdenlive:docproperties.version", "1.1")
    _add_prop(main_bin, "xml_retain", "1")

    # ---- Wrapper tractor ------------------------------------------------
    wrapper = ET.SubElement(mlt, "tractor", {
        "id": "tractor3",
        "in": "00:00:00.000",
        "out": music_tc,
    })
    _add_prop(wrapper, "kdenlive:projectTractor", "1")
    _add_prop(wrapper, "kdenlive:uuid", f"{{{uuid.uuid4()}}}")
    ET.SubElement(wrapper, "track", {
        "producer": master_uuid,
        "in": "00:00:00.000",
        "out": music_tc,
    })

    # ---- Serialize ------------------------------------------------------
    ET.indent(mlt, space=" ")
    xml_body = ET.tostring(mlt, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + xml_body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _probe_dimensions(video_path: str) -> tuple[int, int]:
    """Return (width, height) of the first video stream, or (0, 0) on failure."""
    if not video_path or not Path(video_path).exists():
        return 0, 0
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return 0, 0
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        if streams:
            return streams[0].get("width", 0), streams[0].get("height", 0)
    except Exception:
        pass
    return 0, 0


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
