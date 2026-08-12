from __future__ import annotations

from typing import Protocol

import httpx


class HttpClient(Protocol):
    def get(self, url: str, *, params: dict | None = None, timeout: float = 10.0): ...


class LyricsProvider(Protocol):
    name: str

    def search(self, artist: str, title: str) -> tuple[str, bool] | None:
        """Return (lrc_or_plain_text, synced) or None if nothing was found."""
        ...


def _default_http_client() -> httpx.Client:
    return httpx.Client()


class LrclibProvider:
    """LRCLIB (https://lrclib.net) - the primary provider: free, public, no
    API key. GET /api/search?track_name=&artist_name= returns a JSON array
    of candidates, each with syncedLyrics/plainLyrics (str | None)."""

    name = "lrclib"
    BASE_URL = "https://lrclib.net/api/search"

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http_client = http_client

    @property
    def _client(self) -> HttpClient:
        if self._http_client is None:
            self._http_client = _default_http_client()
        return self._http_client

    def search(self, artist: str, title: str) -> tuple[str, bool] | None:
        response = self._client.get(
            self.BASE_URL, params={"track_name": title, "artist_name": artist}, timeout=10.0
        )
        if response.status_code != 200:
            return None
        results = response.json()
        for result in results:
            synced = result.get("syncedLyrics")
            if synced:
                return synced, True
        for result in results:
            plain = result.get("plainLyrics")
            if plain:
                return plain, False
        return None


class MusixmatchProvider:
    """Musixmatch has no free public API; this ports the unofficial
    apic-desktop token + macro.subtitles.get flow used by community lyrics
    tools (e.g. syncedlyrics) - undocumented and best-effort. Any failure
    (network, auth, response-shape drift) returns None rather than raising,
    matching spec section 7 ("no lyrics found" is not a job failure)."""

    name = "musixmatch"
    TOKEN_URL = "https://apic-desktop.musixmatch.com/ws/1.1/token.get"
    SUBTITLE_URL = "https://apic-desktop.musixmatch.com/ws/1.1/macro.subtitles.get"
    APP_ID = "web-desktop-app-v1.0"

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http_client = http_client

    @property
    def _client(self) -> HttpClient:
        if self._http_client is None:
            self._http_client = _default_http_client()
        return self._http_client

    def search(self, artist: str, title: str) -> tuple[str, bool] | None:
        try:
            token_response = self._client.get(self.TOKEN_URL, params={"app_id": self.APP_ID}, timeout=10.0)
            token = token_response.json()["message"]["body"]["user_token"]
            response = self._client.get(
                self.SUBTITLE_URL,
                params={
                    "app_id": self.APP_ID,
                    "usertoken": token,
                    "q_track": title,
                    "q_artist": artist,
                    "subtitle_format": "lrc",
                },
                timeout=10.0,
            )
            body = response.json()["message"]["body"]
            subtitle_list = body["macro_calls"]["track.subtitles.get"]["message"]["body"]["subtitle_list"]
            lrc_body = subtitle_list[0]["subtitle"]["subtitle_body"]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if not lrc_body:
            return None
        return lrc_body, True


class NetEaseProvider:
    """NetEase Cloud Music's unofficial public endpoints (widely used by
    open-source lyric tools): search/get/web to find a song id, then
    song/lyric for its LRC-formatted lyric text."""

    name = "netease"
    SEARCH_URL = "https://music.163.com/api/search/get/web"
    LYRIC_URL = "https://music.163.com/api/song/lyric"

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http_client = http_client

    @property
    def _client(self) -> HttpClient:
        if self._http_client is None:
            self._http_client = _default_http_client()
        return self._http_client

    def search(self, artist: str, title: str) -> tuple[str, bool] | None:
        query = f"{title} {artist}".strip()
        search_response = self._client.get(self.SEARCH_URL, params={"s": query, "type": 1, "limit": 5}, timeout=10.0)
        songs = search_response.json().get("result", {}).get("songs", [])
        if not songs:
            return None
        song_id = songs[0]["id"]
        lyric_response = self._client.get(
            self.LYRIC_URL, params={"id": song_id, "lv": 1, "kv": 1, "tv": -1}, timeout=10.0
        )
        lrc_text = lyric_response.json().get("lrc", {}).get("lyric")
        if not lrc_text:
            return None
        return lrc_text, True


DEFAULT_PROVIDERS: list[LyricsProvider] = [LrclibProvider(), MusixmatchProvider(), NetEaseProvider()]


def search_providers(artist: str, title: str, providers: list[LyricsProvider]) -> tuple[str, bool, str] | None:
    """Try each provider in order; returns (text, synced, provider_name) from
    the first hit, or None if every provider came up empty or raised."""
    for provider in providers:
        try:
            result = provider.search(artist, title)
        except Exception:
            continue
        if result is not None:
            text, synced = result
            return text, synced, provider.name
    return None
