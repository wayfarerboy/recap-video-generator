"""Audio analysis: beat detection via Essentia, energy scoring via librosa."""

from pathlib import Path

import essentia.standard as es
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

    # Beat tracking via Essentia RhythmExtractor2013 (more accurate than librosa)
    try:
        audio = es.MonoLoader(filename=str(path), sampleRate=44100)()
        rhythm_extractor = es.RhythmExtractor2013(method="multifeature")
        bpm, beat_positions, _confidence, _, _ = rhythm_extractor(audio)
        beat_times = beat_positions.tolist() if hasattr(beat_positions, "tolist") else list(beat_positions)
        bpm_val = float(bpm)
    except Exception as exc:
        # Fallback to librosa if Essentia fails
        import warnings
        warnings.warn("Essentia beat detection failed, falling back to librosa")
        try:
            y, sr = librosa.load(str(path), sr=22050, mono=True)
        except Exception as inner_exc:
            raise ValueError(f"Cannot decode audio file: {filepath}") from inner_exc
        tempo, beat_frames_raw = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames_raw, sr=sr).tolist()
        bpm_val = float(tempo.item() if hasattr(tempo, "item") else tempo)
        # Skip the separate librosa load below
        y_loaded = y
    else:
        y_loaded = None

    # Energy scoring via librosa (onset strength + RMS)
    # Load at 22050 for librosa processing
    if y_loaded is None:
        try:
            y, sr = librosa.load(str(path), sr=22050, mono=True)
        except Exception as exc:
            raise ValueError(f"Cannot decode audio file: {filepath}") from exc
    else:
        y = y_loaded

    # Convert beat times to frame indices for librosa frame-aligned features
    hop_length = 512
    beat_frames = librosa.time_to_frames(np.array(beat_times), sr=sr, hop_length=hop_length)

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
        "bpm": bpm_val,
        "beats": beat_times,
        "energy": energy.tolist(),
    }


def _normalize(arr: np.ndarray) -> np.ndarray:
    """Scale array values to [0, 1]. Returns all zeros if constant."""
    rng = arr.max() - arr.min()
    if rng < 1e-12:
        return np.zeros_like(arr)
    return (arr - arr.min()) / rng
