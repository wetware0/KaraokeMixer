from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..release_year import is_plausible_release_year

MATCH_THRESHOLD = 0.6
RETRY_BACKOFF_SECONDS = 1.0
MAX_ARTWORK_BYTES = 20 * 1024 * 1024
DENYLIST_KEYWORDS = {"karaoke", "tribute", "cover", "instrumental", "backing track", "originally performed", "in the style of", "live"}


class HttpClient(Protocol):
    def get(self, url: str, *, params: dict | None = None, timeout: float = 10.0): ...


@dataclass
class TagsMatch:
    artist: str | None
    title: str | None
    album: str | None
    year: int | None
    artwork_url: str | None


class TagsProvider(Protocol):
    name: str

    def search(self, artist: str, title: str) -> TagsMatch | None: ...


def _default_http_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        headers={"User-Agent": "KaraokeMediaManager/1.0 (https://github.com/wetware0/KaraokeMixer)"}
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _parse_year(date_value: str | None) -> int | None:
    if not date_value or len(date_value) < 4 or not date_value[:4].isdigit():
        return None
    year = int(date_value[:4])
    return year if is_plausible_release_year(year) else None


_ARTWORK_SIZE_RE = re.compile(r"\d+x\d+(?=bb\.\w+$)")


def _should_reject_candidate(candidate_title: str, candidate_album: str, candidate_artist: str, query: str) -> bool:
    """Reject candidates that contain denylist keywords absent from the original query.
    Prevents matching karaoke versions, tributes, covers, etc. when searching for the original.
    If the query itself contains a denylist keyword (user is genuinely searching for e.g., a live version),
    allow candidates with that keyword."""
    query_normalized = _normalize(query)
    candidate_text = f"{candidate_title} {candidate_album} {candidate_artist}"
    candidate_normalized = _normalize(candidate_text)

    def contains_keyword(text: str, keyword: str) -> bool:
        # Both values are normalized to space-separated words. Explicit
        # boundaries prevent false positives such as "cover" in "uncovered".
        return re.search(rf"(?:^|\s){re.escape(keyword)}(?:$|\s)", text) is not None

    for keyword in DENYLIST_KEYWORDS:
        if contains_keyword(candidate_normalized, keyword) and not contains_keyword(query_normalized, keyword):
            return True
    return False


def _upscale_itunes_artwork(url: str) -> str:
    """artworkUrl100 -> 600x600, per Apple's documented URL-substitution
    trick (there is no "give me the big one" endpoint)."""
    return _ARTWORK_SIZE_RE.sub("600x600", url)


class ItunesProvider:
    """https://itunes.apple.com/search - no API key. One request per
    search() call; on a 429 it sleeps RETRY_BACKOFF_SECONDS once and retries
    exactly once more, then gives up (treated as no match, never raised)."""

    name = "itunes"
    BASE_URL = "https://itunes.apple.com/search"

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http_client = http_client

    @property
    def _client(self) -> HttpClient:
        if self._http_client is None:
            self._http_client = _default_http_client()
        return self._http_client

    def _get(self, term: str):
        response = self._client.get(
            self.BASE_URL, params={"term": term, "media": "music", "limit": 5}, timeout=10.0
        )
        if response.status_code == 429:
            time.sleep(RETRY_BACKOFF_SECONDS)
            response = self._client.get(
                self.BASE_URL, params={"term": term, "media": "music", "limit": 5}, timeout=10.0
            )
        return response

    def search(self, artist: str, title: str) -> TagsMatch | None:
        term = f"{artist} {title}".strip()
        if not term:
            return None
        response = self._get(term)
        if response.status_code != 200:
            return None

        results = response.json().get("results", [])
        best_result = None
        best_score = 0.0
        for result in results:
            candidate_title = result.get("trackName", "")
            candidate_album = result.get("collectionName", "")
            candidate_artist = result.get("artistName", "")
            if _should_reject_candidate(candidate_title, candidate_album, candidate_artist, f"{artist} {title}"):
                continue
            score = (
                _similarity(artist, candidate_artist)
                + _similarity(title, candidate_title)
            ) / 2
            if score > best_score:
                best_score = score
                best_result = result
        if best_result is None or best_score < MATCH_THRESHOLD:
            return None

        artwork_url = best_result.get("artworkUrl100")
        return TagsMatch(
            artist=best_result.get("artistName"),
            title=best_result.get("trackName"),
            album=best_result.get("collectionName"),
            year=_parse_year(best_result.get("releaseDate")),
            artwork_url=_upscale_itunes_artwork(artwork_url) if artwork_url else None,
        )


def _first_artist_name(recording: dict) -> str:
    credits = recording.get("artist-credit", [])
    return credits[0].get("name", "") if credits else ""


class MusicBrainzProvider:
    """Fallback when iTunes has no confident match. MusicBrainz's public
    recording search (no key) for artist/title/album/year, then the Cover
    Art Archive's release-front image (also no key, no extra request - the
    URL is deterministic from the release mbid) for artwork. See
    https://musicbrainz.org/doc/MusicBrainz_API and
    https://coverartarchive.org."""

    name = "musicbrainz"
    SEARCH_URL = "https://musicbrainz.org/ws/2/recording"
    COVER_ART_URL = "https://coverartarchive.org/release/{mbid}/front"

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http_client = http_client

    @property
    def _client(self) -> HttpClient:
        if self._http_client is None:
            self._http_client = _default_http_client()
        return self._http_client

    def search(self, artist: str, title: str) -> TagsMatch | None:
        query = f'artist:"{artist}" AND recording:"{title}"'
        response = self._client.get(
            self.SEARCH_URL, params={"query": query, "fmt": "json", "limit": 5}, timeout=10.0
        )
        if response.status_code != 200:
            return None

        recordings = response.json().get("recordings", [])
        best_recording = None
        best_score = 0.0
        for recording in recordings:
            candidate_title = recording.get("title", "")
            candidate_artist = _first_artist_name(recording)
            releases = recording.get("releases", [])
            candidate_album = releases[0].get("title", "") if releases else ""
            if _should_reject_candidate(candidate_title, candidate_album, candidate_artist, f"{artist} {title}"):
                continue
            score = (
                _similarity(artist, candidate_artist)
                + _similarity(title, candidate_title)
            ) / 2
            if score > best_score:
                best_score = score
                best_recording = recording
        if best_recording is None or best_score < MATCH_THRESHOLD:
            return None

        releases = best_recording.get("releases", [])
        release = releases[0] if releases else {}
        release_group = release.get("release-group", {})
        year = _parse_year(release.get("date") or release_group.get("first-release-date"))
        mbid = release.get("id")

        return TagsMatch(
            artist=_first_artist_name(best_recording) or artist,
            title=best_recording.get("title") or title,
            album=release.get("title"),
            year=year,
            artwork_url=self.COVER_ART_URL.format(mbid=mbid) if mbid else None,
        )


DEFAULT_TAGS_PROVIDERS: list[TagsProvider] = [ItunesProvider(), MusicBrainzProvider()]


def search_tags_providers(
    artist: str, title: str, providers: list[TagsProvider]
) -> tuple[TagsMatch, str] | None:
    """Tries each provider in order; returns (match, provider_name) from the
    first hit, or None if every provider came up empty or raised."""
    for provider in providers:
        try:
            result = provider.search(artist, title)
        except Exception:
            continue
        if result is not None:
            return result, provider.name
    return None


def _sniff_image_mime(data: bytes) -> str | None:
    """Detect image type from magic bytes and return MIME type."""
    if data.startswith(b"\xff\xd8"):  # JPEG magic bytes (FFD8)
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):  # PNG magic bytes (89504E47)
        return "image/png"
    return None


def download_artwork(url: str, http_client: HttpClient | None = None) -> tuple[bytes, str] | None:
    client = http_client or _default_http_client()
    try:
        response = client.get(url, timeout=10.0)
    except Exception:
        return None
    if response.status_code != 200:
        return None
    if len(response.content) > MAX_ARTWORK_BYTES:
        return None

    # Validate content type header (strip parameters like ;charset=utf-8)
    header_mime = response.headers.get("content-type", "").split(";")[0].strip()
    if not header_mime.startswith("image/"):
        return None

    # Verify actual image magic bytes
    sniffed_mime = _sniff_image_mime(response.content)
    if sniffed_mime is None:
        return None

    return response.content, sniffed_mime
