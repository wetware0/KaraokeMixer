from __future__ import annotations

from pathlib import Path


def resolve_lrc_path(source_path: Path, options: dict) -> Path:
    """Where the fetch/align lyrics stages read and write `{stem}.lrc`.
    Mirrors app.output_paths.resolve_output_path's beside/mirror rules for
    the `.lrc` extension - duplicated rather than shared because
    output_paths.py is not touched this milestone (see Global Constraints)."""
    filename = f"{source_path.stem}.lrc"
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
