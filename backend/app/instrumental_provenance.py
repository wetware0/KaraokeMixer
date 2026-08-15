from __future__ import annotations

import json
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX
from mutagen.mp4 import MP4

from .processing_profiles import PROCESSING_PROFILES

UVR_KARAOKE_MODELS = (
    "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
    "mel_band_roformer_karaoke_gabox_v2.ckpt",
    "mel_band_roformer_karaoke_becruily.ckpt",
)

PROVENANCE_TAG_DESCRIPTION = "KARAOKE_MIXER_INSTRUMENTAL_PROVENANCE"
_FLAC_PROVENANCE_KEY = "karaoke_mixer_instrumental_provenance"
_MP4_PROVENANCE_KEY = "----:com.karaokemixer:instrumental_provenance"
_MAX_PROVENANCE_TAG_BYTES = 32 * 1024
_PORTABLE_PROVENANCE_FIELDS = (
    "schema_version",
    "part",
    "quality",
    "engine",
    "engine_version",
    "model",
    "models",
    "backing_vocal_mode",
    "device",
    "output_mode",
    "stage",
    "attribution",
    "confirmed_by",
    "recorded_at",
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


def portable_instrumental_provenance(provenance: dict) -> dict | None:
    """Return the path/database-independent portion safe to embed in audio.

    File paths, signatures, and job ids are intentionally excluded. They are
    valid only inside one catalogue, whereas the embedded record must remain
    meaningful when an instrumental is copied or imported into a new library.
    """
    if provenance.get("schema_version") != 1 or provenance.get("part") != "instrumental":
        return None
    quality = provenance.get("quality")
    if quality is not None and quality not in PROCESSING_PROFILES:
        return None
    models = provenance.get("models")
    if (
        not isinstance(models, list)
        or len(models) > 32
        or not all(isinstance(model, str) and len(model) <= 1024 for model in models)
    ):
        return None
    if not isinstance(provenance.get("engine"), str) or not isinstance(provenance.get("model"), str):
        return None
    for key in (
        "engine_version",
        "backing_vocal_mode",
        "device",
        "output_mode",
        "stage",
        "attribution",
        "confirmed_by",
        "recorded_at",
    ):
        value = provenance.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 4096):
            return None

    portable = {key: provenance.get(key) for key in _PORTABLE_PROVENANCE_FIELDS}
    portable["models"] = list(models)
    return portable


def read_instrumental_provenance_tag(path: Path) -> dict | None:
    """Read a Karaoke Mixer provenance tag, returning None for invalid data."""
    try:
        suffix = path.suffix.lower()
        raw: str | None = None
        if suffix == ".mp3":
            tags = ID3(path)
            frame = next(
                (item for item in tags.getall("TXXX") if item.desc == PROVENANCE_TAG_DESCRIPTION),
                None,
            )
            if frame is not None and frame.text:
                raw = str(frame.text[0])
        elif suffix == ".flac":
            values = FLAC(path).get(_FLAC_PROVENANCE_KEY)
            if values:
                raw = str(values[0])
        elif suffix == ".m4a":
            values = MP4(path).get(_MP4_PROVENANCE_KEY)
            if values:
                value = values[0]
                raw = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        if raw is None or len(raw.encode("utf-8")) > _MAX_PROVENANCE_TAG_BYTES:
            return None
        decoded = json.loads(raw)
        return portable_instrumental_provenance(decoded) if isinstance(decoded, dict) else None
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError, ID3NoHeaderError):
        return None


def write_instrumental_provenance_tag(path: Path, provenance: dict) -> bool:
    """Persist portable provenance in the audio metadata container.

    Returns True when the file changed and False when the same tag was already
    present. Other metadata and the compressed audio stream are preserved.
    """
    portable = portable_instrumental_provenance(provenance)
    if portable is None:
        raise ValueError("invalid instrumental provenance")
    if read_instrumental_provenance_tag(path) == portable:
        return False
    encoded = json.dumps(portable, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_PROVENANCE_TAG_BYTES:
        raise ValueError("instrumental provenance tag is too large")

    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall(f"TXXX:{PROVENANCE_TAG_DESCRIPTION}")
        tags.add(TXXX(encoding=3, desc=PROVENANCE_TAG_DESCRIPTION, text=[encoded]))
        tags.save(path)
        return True
    if suffix == ".flac":
        audio = FLAC(path)
        audio[_FLAC_PROVENANCE_KEY] = [encoded]
        audio.save()
        return True
    if suffix == ".m4a":
        audio = MP4(path)
        audio[_MP4_PROVENANCE_KEY] = [encoded.encode("utf-8")]
        audio.save()
        return True
    raise ValueError(f"unsupported instrumental provenance tag format: {suffix}")
