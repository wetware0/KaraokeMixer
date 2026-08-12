from app.metadata.providers import (
    DEFAULT_TAGS_PROVIDERS,
    ItunesProvider,
    MusicBrainzProvider,
    TagsMatch,
    download_artwork,
    search_tags_providers,
)


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b"", headers=None):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._json_body


class _FakeHttpClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, *, params=None, timeout=10.0):
        self.calls.append((url, params))
        return self._responses.pop(0)


def test_itunes_provider_picks_the_best_scoring_result():
    client = _FakeHttpClient([
        _FakeResponse(json_body={"results": [
            {"artistName": "ABBA Tribute Band", "trackName": "Dancing Queen (Cover)",
             "collectionName": "Covers", "releaseDate": "2010-01-01T00:00:00Z",
             "artworkUrl100": "https://example.com/art/100x100bb.jpg"},
            {"artistName": "ABBA", "trackName": "Dancing Queen",
             "collectionName": "Arrival", "releaseDate": "1976-04-05T00:00:00Z",
             "artworkUrl100": "https://example.com/art2/100x100bb.jpg"},
        ]})
    ])
    provider = ItunesProvider(http_client=client)

    match = provider.search("ABBA", "Dancing Queen")

    assert match == TagsMatch(
        artist="ABBA", title="Dancing Queen", album="Arrival", year=1976,
        artwork_url="https://example.com/art2/600x600bb.jpg",
    )


def test_itunes_provider_returns_none_when_no_result_meets_the_match_threshold():
    client = _FakeHttpClient([
        _FakeResponse(json_body={"results": [
            {"artistName": "Someone Else", "trackName": "Completely Different Song"},
        ]})
    ])
    provider = ItunesProvider(http_client=client)

    assert provider.search("ABBA", "Dancing Queen") is None


def test_itunes_provider_discards_an_implausible_release_year():
    client = _FakeHttpClient([
        _FakeResponse(json_body={"results": [
            {"artistName": "ABBA", "trackName": "Dancing Queen",
             "collectionName": "Arrival", "releaseDate": "1125-01-01T00:00:00Z"},
        ]})
    ])

    match = ItunesProvider(http_client=client).search("ABBA", "Dancing Queen")

    assert match is not None
    assert match.year is None


def test_itunes_provider_returns_none_on_a_non_200_response():
    client = _FakeHttpClient([_FakeResponse(status_code=500)])
    provider = ItunesProvider(http_client=client)

    assert provider.search("ABBA", "Dancing Queen") is None


def test_itunes_provider_retries_once_after_a_429_then_succeeds(monkeypatch):
    import app.metadata.providers as providers_module
    monkeypatch.setattr(providers_module.time, "sleep", lambda seconds: None)
    client = _FakeHttpClient([
        _FakeResponse(status_code=429),
        _FakeResponse(json_body={"results": [
            {"artistName": "ABBA", "trackName": "Dancing Queen", "collectionName": "Arrival",
             "releaseDate": "1976-01-01T00:00:00Z", "artworkUrl100": "https://x/100x100bb.jpg"},
        ]}),
    ])
    provider = ItunesProvider(http_client=client)

    match = provider.search("ABBA", "Dancing Queen")

    assert match is not None
    assert len(client.calls) == 2


def test_itunes_provider_gives_up_after_a_second_429(monkeypatch):
    import app.metadata.providers as providers_module
    monkeypatch.setattr(providers_module.time, "sleep", lambda seconds: None)
    client = _FakeHttpClient([_FakeResponse(status_code=429), _FakeResponse(status_code=429)])
    provider = ItunesProvider(http_client=client)

    assert provider.search("ABBA", "Dancing Queen") is None
    assert len(client.calls) == 2


def test_musicbrainz_provider_returns_a_match_with_cover_art_archive_url():
    client = _FakeHttpClient([
        _FakeResponse(json_body={"recordings": [
            {
                "title": "Dancing Queen",
                "artist-credit": [{"name": "ABBA"}],
                "releases": [
                    {"id": "mbid-123", "title": "Arrival", "date": "1976-04-05",
                     "release-group": {"first-release-date": "1976-04-05"}}
                ],
            }
        ]})
    ])
    provider = MusicBrainzProvider(http_client=client)

    match = provider.search("ABBA", "Dancing Queen")

    assert match == TagsMatch(
        artist="ABBA", title="Dancing Queen", album="Arrival", year=1976,
        artwork_url="https://coverartarchive.org/release/mbid-123/front",
    )


def test_musicbrainz_provider_returns_none_when_nothing_scores_high_enough():
    client = _FakeHttpClient([
        _FakeResponse(json_body={"recordings": [
            {"title": "Totally Different", "artist-credit": [{"name": "Nobody"}], "releases": []}
        ]})
    ])
    provider = MusicBrainzProvider(http_client=client)

    assert provider.search("ABBA", "Dancing Queen") is None


def test_search_tags_providers_returns_the_first_hit_and_its_provider_name():
    class _StubProvider:
        name = "stub"

        def search(self, artist, title):
            return TagsMatch(artist="A", title="T", album=None, year=None, artwork_url=None)

    result = search_tags_providers("A", "T", [_StubProvider()])

    assert result[0] == TagsMatch(artist="A", title="T", album=None, year=None, artwork_url=None)
    assert result[1] == "stub"


def test_search_tags_providers_falls_through_to_the_next_provider_on_none_or_exception():
    class _RaisingProvider:
        name = "raiser"

        def search(self, artist, title):
            raise RuntimeError("network error")

    class _EmptyProvider:
        name = "empty"

        def search(self, artist, title):
            return None

    class _HitProvider:
        name = "hit"

        def search(self, artist, title):
            return TagsMatch(artist="A", title="T", album=None, year=None, artwork_url=None)

    result = search_tags_providers("A", "T", [_RaisingProvider(), _EmptyProvider(), _HitProvider()])

    assert result[1] == "hit"


def test_search_tags_providers_returns_none_when_every_provider_misses():
    class _EmptyProvider:
        name = "empty"

        def search(self, artist, title):
            return None

    assert search_tags_providers("A", "T", [_EmptyProvider()]) is None


def test_default_tags_providers_are_itunes_then_musicbrainz():
    assert [type(p) for p in DEFAULT_TAGS_PROVIDERS] == [ItunesProvider, MusicBrainzProvider]


def test_download_artwork_returns_bytes_and_content_type():
    jpeg_bytes = b"\xff\xd8" + b"jpeg-data"
    client = _FakeHttpClient([
        _FakeResponse(content=jpeg_bytes, headers={"content-type": "image/jpeg"})
    ])

    result = download_artwork("https://example.com/art.jpg", http_client=client)

    assert result == (jpeg_bytes, "image/jpeg")


def test_download_artwork_returns_none_on_a_non_200_response():
    client = _FakeHttpClient([_FakeResponse(status_code=404)])

    assert download_artwork("https://example.com/missing.jpg", http_client=client) is None


def test_default_http_client_follows_redirects():
    """Verify that the default HTTP client follows redirects for CoverArtArchive."""
    from app.metadata.providers import _default_http_client
    client = _default_http_client()
    # httpx.Client stores follow_redirects as an attribute
    assert client.follow_redirects is True


def test_default_http_client_has_user_agent_header():
    """Verify that the default HTTP client includes a User-Agent header for MusicBrainz."""
    from app.metadata.providers import _default_http_client
    client = _default_http_client()
    # Check if the User-Agent header is set
    assert "User-Agent" in client.headers
    assert "KaraokeMediaManager" in client.headers["User-Agent"]


def test_itunes_provider_rejects_karaoke_versions():
    """Ensure karaoke versions are rejected even with exact artist match."""
    client = _FakeHttpClient([
        _FakeResponse(json_body={"results": [
            {"artistName": "ABBA", "trackName": "Dancing Queen (Karaoke Version)",
             "collectionName": "Karaoke Collection", "releaseDate": "2020-01-01T00:00:00Z",
             "artworkUrl100": "https://example.com/art/100x100bb.jpg"},
        ]})
    ])
    provider = ItunesProvider(http_client=client)

    assert provider.search("ABBA", "Dancing Queen") is None


def test_itunes_provider_allows_karaoke_when_querying_for_karaoke():
    """If the user is searching for a karaoke version, allow it."""
    client = _FakeHttpClient([
        _FakeResponse(json_body={"results": [
            {"artistName": "ABBA", "trackName": "Dancing Queen (Karaoke Version)",
             "collectionName": "Karaoke Collection", "releaseDate": "2020-01-01T00:00:00Z",
             "artworkUrl100": "https://example.com/art/100x100bb.jpg"},
        ]})
    ])
    provider = ItunesProvider(http_client=client)

    match = provider.search("ABBA", "Dancing Queen Karaoke")
    assert match is not None


def test_itunes_provider_denylist_uses_whole_words_not_substrings():
    client = _FakeHttpClient([_FakeResponse(json_body={"results": [
        {"artistName": "The Uncovered", "trackName": "Dancing Queen",
         "collectionName": "Gold", "releaseDate": "2020-01-01T00:00:00Z"},
    ]})])

    match = ItunesProvider(http_client=client).search("The Uncovered", "Dancing Queen")

    assert match is not None


def test_download_artwork_rejects_non_image_content_type():
    """Artwork downloads with non-image MIME types should be rejected."""
    client = _FakeHttpClient([
        _FakeResponse(status_code=200, content=b"html", headers={"content-type": "text/html"})
    ])

    assert download_artwork("https://example.com/notimage.html", http_client=client) is None


def test_download_artwork_rejects_an_unreasonably_large_response(monkeypatch):
    monkeypatch.setattr("app.metadata.providers.MAX_ARTWORK_BYTES", 8)
    client = _FakeHttpClient([_FakeResponse(
        content=b"\xff\xd8" + b"x" * 7, headers={"content-type": "image/jpeg"}
    )])

    assert download_artwork("https://example.com/huge.jpg", http_client=client) is None


def test_download_artwork_validates_jpeg_magic_bytes():
    """Verify JPEG magic bytes (FFD8) and return sniffed MIME type."""
    jpeg_bytes = b"\xff\xd8" + b"jpeg-data"
    client = _FakeHttpClient([
        _FakeResponse(content=jpeg_bytes, headers={"content-type": "image/jpeg"})
    ])

    result = download_artwork("https://example.com/art.jpg", http_client=client)
    assert result == (jpeg_bytes, "image/jpeg")


def test_download_artwork_validates_png_magic_bytes():
    """Verify PNG magic bytes (89504E47) and return sniffed MIME type."""
    png_bytes = b"\x89PNG" + b"png-data"
    client = _FakeHttpClient([
        _FakeResponse(content=png_bytes, headers={"content-type": "image/png"})
    ])

    result = download_artwork("https://example.com/art.png", http_client=client)
    assert result == (png_bytes, "image/png")


def test_download_artwork_rejects_invalid_magic_bytes():
    """Artwork with invalid image magic bytes should be rejected."""
    fake_image = b"not-an-image-really"
    client = _FakeHttpClient([
        _FakeResponse(content=fake_image, headers={"content-type": "image/jpeg"})
    ])

    assert download_artwork("https://example.com/fake.jpg", http_client=client) is None


def test_download_artwork_trusts_sniffed_mime_over_header():
    """MIME type should come from magic bytes, not the header."""
    jpeg_bytes = b"\xff\xd8" + b"jpeg-data"
    client = _FakeHttpClient([
        _FakeResponse(content=jpeg_bytes, headers={"content-type": "image/png; charset=utf-8"})
    ])

    result = download_artwork("https://example.com/art.jpg", http_client=client)
    assert result == (jpeg_bytes, "image/jpeg")
