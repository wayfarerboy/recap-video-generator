"""Video analysis utilities for recap.

Frame-differencing motion scoring at 480p, sliding-window exciting-segment
extraction, and ffprobe-based orientation detection.
"""

import json
import subprocess
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Orientation detection via ffprobe
# ---------------------------------------------------------------------------

def get_orientation(video_path: str) -> str:
    """Return ``"portrait"`` or ``"landscape"`` by inspecting the first video
    stream with ffprobe.

    Respects the ``rotation`` tag and ``side_data_list`` rotation entries;
    falls back to raw width/height comparison when no rotation is present.
    """
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        proc.check_returncode()
        data = json.loads(proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"ffprobe failed for {video_path}: {exc}") from exc
    except FileNotFoundError:
        raise RuntimeError("ffprobe not found – is ffmpeg installed?")

    streams = data.get("streams", [])
    if not streams:
        raise RuntimeError(f"No video stream found in {video_path}")

    stream = streams[0]
    width = int(stream.get("width", 0))
    height = int(stream.get("height", 0))

    # Collect rotation from tags …
    rotation = 0
    tags = stream.get("tags", {})
    rot_str = tags.get("rotation", "0")
    try:
        rotation = int(rot_str)
    except (ValueError, TypeError):
        pass

    # … and from side_data_list.
    for sd in stream.get("side_data_list", []):
        if sd.get("rotation") is not None:
            try:
                rotation = int(sd["rotation"])
            except (ValueError, TypeError):
                pass

    rotation = rotation % 360

    # Rotations that swap the effective dimensions.
    if rotation in (90, 270):
        width, height = height, width

    return "portrait" if height > width else "landscape"


# ---------------------------------------------------------------------------
# Frame-differencing motion scoring
# ---------------------------------------------------------------------------

def _resize_frame(frame: np.ndarray, target_height: int = 480) -> np.ndarray:
    """Resize *frame* so its height equals *target_height*, keeping aspect ratio."""
    import cv2

    h, w = frame.shape[:2]
    if h == target_height:
        return frame
    scale = target_height / h
    new_w = int(w * scale)
    return cv2.resize(frame, (new_w, target_height))


def _frame_diff_score(prev_frame: np.ndarray, curr_frame: np.ndarray) -> float:
    """Return the mean absolute pixel difference between two frames.

    Parameters
    ----------
    prev_frame, curr_frame : np.ndarray
        Frames of identical shape (H, W, 3) in uint8 or float.

    Returns
    -------
    float
        Mean absolute difference across all channels.
    """
    diff = np.abs(curr_frame.astype(np.float64) - prev_frame.astype(np.float64))
    return float(diff.mean())


def compute_motion_scores(video_path: str) -> tuple[list[float], float]:
    """Compute per-frame motion scores using frame differencing at 480p.

    Parameters
    ----------
    video_path : str
        Path to a video file readable by OpenCV.

    Returns
    -------
    tuple[list[float], float]
        ``(scores, fps)`` where ``scores[i]`` is the inter-frame difference
        between frame *i-1* and frame *i* (``scores[0]`` is always 0.0).
    """
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0  # sensible fallback

        scores: list[float] = []
        prev_frame = None

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame = _resize_frame(frame)

            if prev_frame is None:
                scores.append(0.0)
            else:
                scores.append(_frame_diff_score(prev_frame, frame))

            prev_frame = frame

        return scores, fps
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Sliding-window exciting-segment extraction
# ---------------------------------------------------------------------------

def find_most_exciting(
    motion_scores: list[float],
    fps: float,
    window_seconds: float = 3.0,
) -> tuple[float, float, float]:
    """Find the contiguous *window_seconds* segment with the highest mean motion.

    Uses ``np.convolve`` with a boxcar window for O(n) sliding-window sum.

    Parameters
    ----------
    motion_scores : list[float]
        Per-frame scores from :func:`compute_motion_scores`.
    fps : float
        Frames per second of the source video.
    window_seconds : float
        Desired segment duration (default 3.0).

    Returns
    -------
    tuple[float, float, float]
        ``(start_time, end_time, mean_score)`` in seconds.
    """
    scores = np.array(motion_scores, dtype=np.float64)
    window_frames = max(1, int(window_seconds * fps))

    if len(scores) <= window_frames:
        start_time = 0.0
        end_time = len(scores) / fps if fps > 0 else 0.0
        mean_score = float(scores.mean()) if len(scores) > 0 else 0.0
        return start_time, end_time, mean_score

    window = np.ones(window_frames)
    conv = np.convolve(scores, window, mode="valid")

    best_idx = int(np.argmax(conv))
    start_time = best_idx / fps
    end_time = (best_idx + window_frames) / fps
    mean_score = float(conv[best_idx] / window_frames)

    return start_time, end_time, mean_score


# ---------------------------------------------------------------------------
# Top-level analysis entry point
# ---------------------------------------------------------------------------

def analyze_video(
    video_path: str,
    window_seconds: float = 3.0,
) -> dict:
    """Analyze a single video clip for visual excitement.

    Returns a dict with keys ``most_exciting``, ``motion_scores``, and
    ``orientation``.

    Parameters
    ----------
    video_path : str
        Path to the video file.
    window_seconds : float
        Duration of the most-exciting segment to extract (default 3.0).

    Returns
    -------
    dict
        See :ref:`recap-analyze-output` for the JSON schema.
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {video_path}")

    orientation = get_orientation(str(path))
    motion_scores, fps = compute_motion_scores(str(path))
    start, end, score = find_most_exciting(motion_scores, fps, window_seconds)

    return {
        "most_exciting": {
            "start": round(start, 2),
            "end": round(end, 2),
            "score": round(score, 2),
        },
        "motion_scores": [round(s, 2) for s in motion_scores],
        "orientation": orientation,
    }
