import numpy as np
import pytest

from app.stages.mixing import (
    FAINT_VOCAL_GAIN_DB,
    STEREO_SIDE_GAIN_DB,
    apply_backing_vocal_mix,
    apply_peak_protection,
    combine_stems,
    db_to_amplitude,
)


def _sine(seconds=0.1, sample_rate=8000, freq=440.0, channels=2, amplitude=0.5):
    samples = int(seconds * sample_rate)
    t = np.arange(samples) / sample_rate
    mono = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.tile(mono, (channels, 1))


def test_db_to_amplitude_matches_known_values():
    assert db_to_amplitude(0.0) == 1.0
    assert abs(db_to_amplitude(-18.0) - 0.12589254) < 1e-6
    assert abs(db_to_amplitude(-9.0) - 0.35481339) < 1e-6


def test_combine_stems_sums_an_arbitrary_number_of_stems():
    drums = _sine(freq=100)
    bass = _sine(freq=60)
    other = _sine(freq=300)

    combined = combine_stems(drums, bass, other)

    assert np.allclose(combined, drums + bass + other)


def test_stripped_mode_returns_the_instrumental_bed_unchanged():
    instrumental = _sine(freq=100)
    vocals = _sine(freq=440)

    result = apply_backing_vocal_mix(instrumental, vocals, "stripped")

    assert np.array_equal(result, instrumental)


def test_faint_mode_adds_vocals_at_minus_18db():
    instrumental = np.zeros((2, 100), dtype=np.float32)
    vocals = np.full((2, 100), 0.5, dtype=np.float32)

    result = apply_backing_vocal_mix(instrumental, vocals, "faint")

    expected = 0.5 * db_to_amplitude(FAINT_VOCAL_GAIN_DB)
    assert np.allclose(result, expected, atol=1e-6)


def test_stereo_mix_mode_puts_vocals_in_the_side_channel_at_minus_9db():
    instrumental = np.zeros((2, 100), dtype=np.float32)
    vocals = np.zeros((2, 100), dtype=np.float32)
    vocals[0] = 0.5  # left-only vocal -> maximal side content

    result = apply_backing_vocal_mix(instrumental, vocals, "stereo_mix")

    side = (vocals[0] - vocals[1]) * 0.5
    expected_left = side * db_to_amplitude(STEREO_SIDE_GAIN_DB)
    expected_right = -side * db_to_amplitude(STEREO_SIDE_GAIN_DB)
    assert np.allclose(result[0], expected_left, atol=1e-6)
    assert np.allclose(result[1], expected_right, atol=1e-6)


def test_apply_peak_protection_scales_down_when_over_ceiling():
    audio = np.array([[1.5, -1.5]], dtype=np.float32)

    result = apply_peak_protection(audio, ceiling=0.99)

    assert abs(float(np.max(np.abs(result))) - 0.99) < 1e-6


def test_apply_peak_protection_leaves_quiet_audio_unchanged():
    audio = np.array([[0.2, -0.2]], dtype=np.float32)

    result = apply_peak_protection(audio, ceiling=0.99)

    assert np.array_equal(result, audio)


def test_unknown_mode_raises_value_error():
    instrumental = _sine()
    vocals = _sine()

    with pytest.raises(ValueError):
        apply_backing_vocal_mix(instrumental, vocals, "best")
