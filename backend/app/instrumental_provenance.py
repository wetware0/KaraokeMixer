from __future__ import annotations

from pathlib import Path

from .processing_profiles import PROCESSING_PROFILES

UVR_KARAOKE_MODELS = (
    "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
    "mel_band_roformer_karaoke_gabox_v2.ckpt",
    "mel_band_roformer_karaoke_becruily.ckpt",
)


def build_instrumental_provenance(
    options: dict,
    output_path: Path,
    *,
    engine: str,
    model: str,
    backing_vocal_mode: str,
    engine_version: str | None = None,
    models: tuple[str, ...] | None = None,
) -> dict:
    """Describe the effective process which wrote one instrumental.

    Job identity and the final file signature are attached by the database at
    publication time. Keeping this stage-owned portion close to the actual
    engine choice prevents a selected Demucs option from being mistaken for
    the UVR ensemble used by the High Quality ``best`` path.
    """
    selected_profile = options.get("processing_profile")
    quality = selected_profile if selected_profile in PROCESSING_PROFILES else None
    return {
        "schema_version": 1,
        "part": "instrumental",
        "quality": quality,
        "engine": engine,
        "engine_version": engine_version,
        "model": model,
        "models": list(models or ()),
        "backing_vocal_mode": backing_vocal_mode,
        "device": options.get("device"),
        "output_mode": options.get("output_mode", "beside"),
        "output_path": str(output_path),
    }
