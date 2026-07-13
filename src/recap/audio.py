"""Audio analysis: beat detection and energy scoring via librosa."""

from pathlib import Path

import librosa
import numpy as np


def detect_beats(filepath: str) -> dict:
    """Analyze an audio file for tempo, beat positions, and per-beat energy.

    Args:
        filepath: Path to an audio file (MP3, WAV, etc.).

    Returns:
        dict with keys:
            - bpm (float): detected tempo in beats per minute.
            - beats (list[float]): beat timestamps in seconds.
            - energy (list[float]): per-beat energy scores, 0.0 – 1.0.

    Raises:
        FileNotFoundError: If *filepath* does not exist.
        ValueError: If the file cannot be decoded as audio.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    # Load audio (mono, native sr resampled to 22050)
    try:
        y, sr = librosa.load(str(path), sr=22050, mono=True)
    except Exception as exc:
        raise ValueError(f"Cannot decode audio file: {filepath}") from exc

    # Beat tracking
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)

    # Onset strength envelope (same hop_length as beat_track default)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)

    # RMS energy per frame
    rms = librosa.feature.rms(y=y)[0]  # (n_frames,)

    # Align frame counts (they can differ by 1)
    min_len = min(len(onset_env), len(rms))
    onset_env = onset_env[:min_len]
    rms = rms[:min_len]

    # Normalize each feature to [0, 1]
    onset_norm = _normalize(onset_env)
    rms_norm = _normalize(rms)

    # Composite per-frame energy: mean of normalized onset + RMS
    composite = 0.5 * onset_norm + 0.5 * rms_norm

    # Per-beat energy: mean composite over [beat_i, beat_{i+1})
    energy = []
    for i in range(len(beat_frames)):
        start = int(beat_frames[i])
        end = int(beat_frames[i + 1]) if i + 1 < len(beat_frames) else len(composite)
        if start < len(composite) and end > start:
            segment = composite[start : min(end, len(composite))]
            energy.append(float(np.mean(segment)))
        else:
            energy.append(0.0)

    # Re-normalize per-beat energies to full [0, 1] range
    energy = _normalize(np.array(energy, dtype=np.float64))

    return {
        "bpm": float(tempo.item() if hasattr(tempo, "item") else tempo),
        "beats": beat_times.tolist(),
        "energy": energy.tolist(),
    }


def _normalize(arr: np.ndarray) -> np.ndarray:
    """Scale array values to [0, 1]. Returns all zeros if constant."""
    rng = arr.max() - arr.min()
    if rng < 1e-12:
        return np.zeros_like(arr)
    return (arr - arr.min()) / rng
