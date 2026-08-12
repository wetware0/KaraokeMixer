from __future__ import annotations

from pathlib import Path


def resolve_output_path(source_path: Path, part: str, options: dict) -> Path:
    """Where a stage should publish `{source_path.stem}.{part}.mp3`.

    "beside" (default): next to the source file, matching the discovery
    convention in `app.scanner`. "mirror": under the first configured
    mirror root, at the same path relative to whichever configured media
    root contains `source_path` - the inverse of `scanner.locate_output`,
    which only searches for outputs that already exist. Falls back to
    "beside" when output_mode is "mirror" but no mirror root is configured,
    or the source path is not under any configured media root - a
    resolvable destination beats a lost output.
    """
    filename = f"{source_path.stem}.{part}.mp3"
    if options.get("output_mode") != "mirror":
        return source_path.with_name(filename)

    mirror_roots = [Path(root) for root in options.get("mirror_roots", [])]
    if not mirror_roots:
        return source_path.with_name(filename)

    media_roots = [Path(root) for root in options.get("media_roots", [])]
    resolved_source = source_path.resolve()
    for media_root in media_roots:
        resolved_root = media_root.resolve()
        if resolved_source.is_relative_to(resolved_root):
            relative_dir = resolved_source.parent.relative_to(resolved_root)
            return mirror_roots[0] / relative_dir / filename
    return source_path.with_name(filename)
