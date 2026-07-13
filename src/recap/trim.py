"""FFmpeg-based video trim step.

Extracts the exciting segment from each clip in an assignment plan using
FFmpeg subprocess with frame-accurate re-encoding (libx264 veryfast).
Trims are raw extracts — no crop, rotation, or scale baked in.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, TextIO


def trim_plan(
    plan: dict[str, Any],
    output_dir: str | Path = "recap-trims",
    *,
    max_workers: int | None = None,
    verbose: bool = False,
    progress_file: TextIO | None = None,
) -> dict[str, Any]:
    """Trim every clip in the assignment plan and update with trim paths.

    Parameters
    ----------
    plan : dict
        Assignment plan (output of ``recap assign``). Must have an
        ``assignments`` key with a list of clip entries.
    output_dir : str or Path
        Directory to write trimmed MP4 files.  Created if missing.
    max_workers : int or None
        Maximum number of parallel ffmpeg workers.  Defaults to
        ``ThreadPoolExecutor`` default.
    verbose : bool
        When ``True``, prints progress lines to *progress_file*.
    progress_file : TextIO or None
        Where to send verbose progress lines.  Defaults to ``sys.stderr``.

    Returns
    -------
    dict
        The updated plan with a ``trim`` key in each assignment pointing
        to the trimmed file path.  Also includes a ``_trim_summary`` key
        with ``succeeded``, ``failed``, and list of ``errors``.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if progress_file is None:
        progress_file = sys.stderr

    assignments: list[dict[str, Any]] = plan.get("assignments", [])
    if not assignments:
        return {**plan, "_trim_summary": {"succeeded": 0, "failed": 0, "errors": []}}

    # Build task list
    tasks: list[dict[str, Any]] = []
    for i, entry in enumerate(assignments):
        source_path = entry["clip"]
        start = entry["source_start"]
        end = entry["source_end"]
        duration = round(end - start, 2)
        trim_filename = _deterministic_name(source_path, start, end)
        trim_path = output_dir / trim_filename
        tasks.append({
            "index": i,
            "source_path": source_path,
            "trim_path": str(trim_path),
            "start": start,
            "duration": duration,
        })

    total = len(tasks)
    succeeded = 0
    errors: list[dict[str, Any]] = []

    if verbose and total > 0:
        print(f"Trimming {total} clip(s) concurrently...", file=progress_file)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_trim_one, t): t
            for t in tasks
        }
        for future in as_completed(future_map):
            task = future_map[future]
            idx = task["index"]
            try:
                trim_path = future.result()
                assignments[idx]["trim"] = trim_path
                succeeded += 1
                if verbose:
                    print(f"  {succeeded}/{total} clips trimmed", file=progress_file)
            except Exception as exc:
                errors.append({
                    "clip": task["source_path"],
                    "error": str(exc),
                })
                if verbose:
                    print(f"  FAILED: {task['source_path']} — {exc}", file=progress_file)

    summary: dict[str, Any] = {
        "succeeded": succeeded,
        "failed": len(errors),
        "errors": errors,
    }
    result = dict(plan)
    result["_trim_summary"] = summary
    return result


def _trim_one(task: dict[str, Any]) -> str:
    """Run a single ffmpeg trim.  Returns the output path on success."""
    trim_path = task["trim_path"]

    # Skip if already trimmed (idempotent)
    if Path(trim_path).exists():
        return trim_path

    cmd = [
        "ffmpeg",
        "-ss", str(task["start"]),
        "-i", task["source_path"],
        "-t", str(task["duration"]),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-y",  # overwrite without asking
        trim_path,
    ]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,  # generous timeout for long clips
    )

    if proc.returncode != 0:
        stderr_tail = proc.stderr.strip().splitlines()[-5:] if proc.stderr else ["(no stderr)"]
        raise RuntimeError(
            f"ffmpeg exited {proc.returncode}: " + "; ".join(stderr_tail)
        )

    if not Path(trim_path).exists():
        raise RuntimeError("ffmpeg exited 0 but output file not found")

    return trim_path


def _deterministic_name(source_path: str, start: float, end: float) -> str:
    """Derive a deterministic filename from source path and in/out points."""
    key = f"{source_path}:{start}:{end}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"{digest}.mp4"
