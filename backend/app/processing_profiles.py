from __future__ import annotations

PROCESSING_PROFILES = ("fast", "balanced", "high_quality")

PROFILE_OPTION = {
    "type": "select",
    "choices": list(PROCESSING_PROFILES),
    "default": "balanced",
    "description": "Choose throughput or maximum separation and transcription quality.",
}

PROFILE_DEFAULTS: dict[str, dict[str, object]] = {
    "fast": {
        "model": "mdx",
        "backing_vocal_mode": "stripped",
        "asr_model": "base.en",
        "split": False,
    },
    "balanced": {
        "model": "htdemucs",
        "backing_vocal_mode": "stripped",
        "asr_model": "small.en",
        "split": False,
    },
    "high_quality": {
        "model": "htdemucs_ft",
        "backing_vocal_mode": "best",
        "asr_model": "medium",
        "split": True,
    },
}


def profile_option(options: dict, name: str, fallback: object) -> object:
    """Resolve a profile default while allowing an explicit expert override."""
    if name in options:
        return options[name]
    profile = str(options.get("processing_profile", "balanced"))
    return PROFILE_DEFAULTS.get(profile, PROFILE_DEFAULTS["balanced"]).get(name, fallback)
