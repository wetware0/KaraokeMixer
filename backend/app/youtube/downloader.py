from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

DEFAULT_DURATION_CAP_SECONDS = 600.0
PLAYLIST_ENTRY_CAP = 100


class YoutubeDownloadError(RuntimeError):
    def __init__(self, message: str, *, age_restricted: bool = False) -> None:
        super().__init__(message)
        self.age_restricted = age_restricted


@dataclass
class DownloadResult:
    path: Path
    title: str
    duration: float
    uploader: str


def _is_age_restricted_error(message: str) -> bool:
    lowered = message.lower()
    return "sign in to confirm your age" in lowered or "age-restricted" in lowered


def _ydl_opts(cookies: dict | None) -> dict:
    opts: dict = {"quiet": True, "no_warnings": True}
    mode = (cookies or {}).get("mode", "none")
    if mode == "browser" and (cookies or {}).get("browser"):
        opts["cookiesfrombrowser"] = (cookies["browser"],)
    elif mode == "file" and (cookies or {}).get("cookies_file"):
        opts["cookiefile"] = cookies["cookies_file"]
    return opts


def _watch_url(video_id: str | None) -> str:
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""


def _watch_video_id(url: str) -> str | None:
    """Returns the watch video id embedded in `url`, if any - via the `v=`
    query parameter (the standard youtube.com/watch form) or the path
    segment of a youtu.be short link. Deliberately uses urllib.parse rather
    than a regex over the whole URL, so query-parameter ordering or extra
    params (like a tagged-along `&list=...`) never matter."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname == "youtu.be" or hostname.endswith(".youtu.be"):
        video_id = parsed.path.strip("/")
        return video_id or None
    if hostname != "youtube.com" and not hostname.endswith(".youtube.com"):
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live"}:
        return path_parts[1]
    query_video_id = parse_qs(parsed.query).get("v")
    if query_video_id and query_video_id[0]:
        return query_video_id[0]
    return None


def probe_youtube_url(url: str, *, cookies: dict | None = None) -> dict:
    """Metadata-only probe (extract_info(download=False)) - used both by the
    duration-cap check before a real download and by POST /api/youtube/probe
    for the frontend's artist/title prefill (single video) or playlist entry
    picker (playlist URL).

    Ruling on the `watch?v=X&list=Y` ambiguity: pasting a video's URL while
    it happens to be playing inside a playlist produces exactly this form,
    and it's the most common paste shape - yt-dlp's own default (follow the
    `list=` param) would silently resolve it as the whole playlist, which
    surprises someone who only meant to grab one video. So: any URL that
    names a watch video id - a `v=` query param, or a youtu.be/<id> path -
    is probed with `noplaylist=True` and always returns the single-video
    shape, even if a `list=` param is also present. Only a URL with NO watch
    video id at all (e.g. youtube.com/playlist?list=...) is probed as a
    playlist, via `extract_flat="in_playlist"` - which also keeps that probe
    fast, since yt-dlp does not recursively resolve every entry's full
    metadata, just the lightweight id/url/title/duration already visible on
    the playlist page. Never touched by a test with a real network call -
    tests monkeypatch this module's `yt_dlp` attribute."""
    opts = {**_ydl_opts(cookies), "skip_download": True}
    if _watch_video_id(url) is not None:
        opts["noplaylist"] = True
    else:
        opts["extract_flat"] = "in_playlist"

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get("entries")
    if entries is not None:
        entries = list(entries)
        capped = entries[:PLAYLIST_ENTRY_CAP]
        playlist_entries = [
            {
                "url": entry.get("url") or entry.get("webpage_url") or _watch_url(entry.get("id")),
                "title": entry.get("title") or "Unknown",
                "duration": float(entry.get("duration") or 0.0),
            }
            for entry in capped
        ]
        return {
            "is_playlist": True,
            "entries": playlist_entries,
            "count": len(playlist_entries),
            "total": len(entries),
        }

    return {
        "is_playlist": False,
        "title": info.get("title", "Unknown"),
        "duration": float(info.get("duration") or 0.0),
        "uploader": info.get("uploader", "Unknown"),
    }


def download_youtube_audio(url: str, destination: Path, *, cookies: dict | None = None) -> DownloadResult:
    """Downloads bestaudio and transcodes to m4a at exactly `destination` via
    yt-dlp's FFmpegExtractAudio postprocessor. Raises
    YoutubeDownloadError(age_restricted=True) when yt-dlp's error message
    indicates YouTube's age gate blocked the download without cookies
    configured."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    opts = {
        **_ydl_opts(cookies),
        "format": "bestaudio/best",
        "outtmpl": str(destination.with_suffix("")) + ".%(ext)s",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        message = str(exc)
        raise YoutubeDownloadError(message, age_restricted=_is_age_restricted_error(message)) from exc

    produced = destination.with_suffix(".m4a")
    if produced != destination and produced.exists():
        produced.replace(destination)
    return DownloadResult(
        path=destination,
        title=info.get("title", "Unknown"),
        duration=float(info.get("duration") or 0.0),
        uploader=info.get("uploader", "Unknown"),
    )
