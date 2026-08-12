from __future__ import annotations

import numpy as np

FAINT_VOCAL_GAIN_DB = -18.0
STEREO_SIDE_GAIN_DB = -9.0
PEAK_CEILING = 0.99


def db_to_amplitude(decibels: float) -> float:
    return 10.0 ** (decibels / 20.0)


def apply_peak_protection(audio: np.ndarray, ceiling: float = PEAK_CEILING) -> np.ndarray:
    """Scale `audio` down (never up) so its absolute peak does not exceed
    `ceiling`, avoiding hard clipping after mixing vocal content back in."""
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > ceiling:
        return audio * (ceiling / peak)
    return audio


def combine_stems(*stems: np.ndarray) -> np.ndarray:
    """Sum any number of same-shape [channels, samples] stems - the demucs
    'drums'/'bass'/'other' (and, for 6-stem models, 'guitar'/'piano') stems
    combined make the vocal-free instrumental bed.

    Callers always pass at least one stem; `combine_stems()` with no
    arguments has no shape to return and would otherwise silently produce
    `sum(()) == 0` (a bare int, not an array), so it raises instead.
    """
    if not stems:
        raise ValueError("combine_stems() requires at least one stem")
    return sum(stems)


def apply_backing_vocal_mix(instrumental: np.ndarray, vocals: np.ndarray, mode: str) -> np.ndarray:
    """Blend `vocals` back into `instrumental` per one of TrackSeparator's
    three fast modes (ported from TrackSeparator/src/core/backing_vocals.py,
    numpy instead of torch tensors). The 'best' UVR ensemble mode is a
    different code path entirely (Task 7) and does not go through this
    function - passing it here raises ValueError. `instrumental`/`vocals`
    are [channels, samples] float32 arrays at the same sample rate and
    length.
    """
    if mode == "stripped":
        return instrumental
    if mode == "faint":
        return apply_peak_protection(instrumental + vocals * db_to_amplitude(FAINT_VOCAL_GAIN_DB))
    if mode == "stereo_mix":
        if vocals.shape[0] < 2 or instrumental.shape[0] < 2:
            return apply_peak_protection(instrumental + vocals * db_to_amplitude(FAINT_VOCAL_GAIN_DB))
        side = (vocals[0] - vocals[1]) * 0.5
        side_stereo = np.zeros_like(vocals)
        side_stereo[0] = side
        side_stereo[1] = -side
        return apply_peak_protection(instrumental + side_stereo * db_to_amplitude(STEREO_SIDE_GAIN_DB))
    raise ValueError(f"Mode '{mode}' is not a fast backing-vocal mix mode")
