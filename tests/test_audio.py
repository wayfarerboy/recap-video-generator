"""Tests for recap audio analysis."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from recap.audio import detect_beats


def make_click_track(duration=4.0, bpm=120, sr=22050):
    """Generate a click track with known tempo.

    Short sine pulses at each beat (10 ms clicks).
    """
    beat_interval = 60.0 / bpm
    n_samples = int(duration * sr)
    y = np.zeros(n_samples, dtype=np.float32)
    click_samples = int(0.01 * sr)  # 10 ms click
    click_env = np.sin(np.linspace(0, np.pi, click_samples)).astype(np.float32)
    for beat in range(int(duration / beat_interval)):
        pos = int(beat * beat_interval * sr)
        if pos + click_samples < n_samples:
            y[pos : pos + click_samples] = 0.8 * click_env
    return y, sr


def _write_wav(y, sr):
    """Write float32 audio to a temporary WAV file, return path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    # Scale to int16 for WAV
    y_int16 = (y * 32767).astype(np.int16)
    wavfile.write(tmp.name, sr, y_int16)
    return tmp.name


class TestDetectBeats:
    """Unit tests for detect_beats()."""

    def test_returns_expected_keys(self):
        """Result dict contains bpm, beats, energy keys."""
        y, sr = make_click_track(bpm=120, duration=4.0)
        path = _write_wav(y, sr)
        try:
            result = detect_beats(path)
            assert "bpm" in result
            assert "beats" in result
            assert "energy" in result
        finally:
            Path(path).unlink()

    def test_bpm_detected_reasonably(self):
        """BPM should be close to 120 for a click track."""
        y, sr = make_click_track(bpm=120, duration=8.0)
        path = _write_wav(y, sr)
        try:
            result = detect_beats(path)
            # librosa may return double or half tempo; allow wide range
            bpm = result["bpm"]
            assert isinstance(bpm, float)
            assert 50 <= bpm <= 260
        finally:
            Path(path).unlink()

    def test_beats_are_seconds_and_monotonic(self):
        """Beat entries are floats in seconds, strictly increasing."""
        y, sr = make_click_track(bpm=120, duration=4.0)
        path = _write_wav(y, sr)
        try:
            result = detect_beats(path)
            beats = result["beats"]
            assert len(beats) > 0
            assert all(isinstance(b, float) for b in beats)
            assert all(beats[i] < beats[i + 1] for i in range(len(beats) - 1))
        finally:
            Path(path).unlink()

    def test_energy_scores_in_range(self):
        """Energy scores are floats between 0 and 1."""
        y, sr = make_click_track(bpm=120, duration=4.0)
        path = _write_wav(y, sr)
        try:
            result = detect_beats(path)
            energy = result["energy"]
            assert len(energy) == len(result["beats"])
            assert all(isinstance(e, float) for e in energy)
            assert all(0.0 <= e <= 1.0 for e in energy)
        finally:
            Path(path).unlink()

    def test_missing_file_raises(self):
        """detect_beats raises FileNotFoundError for nonexistent path."""
        with pytest.raises(FileNotFoundError):
            detect_beats("/nonexistent/path/audio.mp3")

    def test_invalid_file_raises(self):
        """detect_beats raises ValueError for non-audio file content."""
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp.write(b"not audio data")
        tmp.close()
        try:
            with pytest.raises(ValueError):
                detect_beats(tmp.name)
        finally:
            Path(tmp.name).unlink()
