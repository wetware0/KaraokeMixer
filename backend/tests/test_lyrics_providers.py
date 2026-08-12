from app.lyrics.providers import LrclibProvider, MusixmatchProvider, NetEaseProvider, search_providers


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self, responses):
        self._responses = responses  # url -> _FakeResponse
        self.calls = []

    def get(self, url, *, params=None, timeout=10.0):
        self.calls.append((url, params))
        return self._responses[url]


def test_lrclib_provider_returns_synced_lyrics_when_available():
    client = _FakeHttpClient({
        LrclibProvider.BASE_URL: _FakeResponse(200, [{"syncedLyrics": "[00:01.00]la la", "plainLyrics": None}]),
    })
    provider = LrclibProvider(http_client=client)

    result = provider.search("ABBA", "Chiquitita")

    assert result == ("[00:01.00]la la", True)
    assert client.calls[0][1] == {"track_name": "Chiquitita", "artist_name": "ABBA"}


def test_lrclib_provider_falls_back_to_plain_lyrics():
    client = _FakeHttpClient({
        LrclibProvider.BASE_URL: _FakeResponse(200, [{"syncedLyrics": None, "plainLyrics": "la la la"}]),
    })
    provider = LrclibProvider(http_client=client)

    assert provider.search("ABBA", "Chiquitita") == ("la la la", False)


def test_lrclib_provider_returns_none_when_no_results():
    client = _FakeHttpClient({LrclibProvider.BASE_URL: _FakeResponse(200, [])})
    provider = LrclibProvider(http_client=client)

    assert provider.search("Nobody", "Nothing") is None


def test_lrclib_provider_returns_none_on_non_200():
    client = _FakeHttpClient({LrclibProvider.BASE_URL: _FakeResponse(500, [])})
    provider = LrclibProvider(http_client=client)

    assert provider.search("ABBA", "Chiquitita") is None


def test_musixmatch_provider_extracts_subtitle_body():
    client = _FakeHttpClient({
        MusixmatchProvider.TOKEN_URL: _FakeResponse(200, {"message": {"body": {"user_token": "tok"}}}),
        MusixmatchProvider.SUBTITLE_URL: _FakeResponse(200, {
            "message": {"body": {"macro_calls": {"track.subtitles.get": {"message": {"body": {
                "subtitle_list": [{"subtitle": {"subtitle_body": "[00:01.00]la"}}]
            }}}}}}
        }),
    })
    provider = MusixmatchProvider(http_client=client)

    assert provider.search("ABBA", "Chiquitita") == ("[00:01.00]la", True)


def test_musixmatch_provider_returns_none_on_unexpected_response_shape():
    client = _FakeHttpClient({
        MusixmatchProvider.TOKEN_URL: _FakeResponse(200, {"message": {"body": {}}}),
        MusixmatchProvider.SUBTITLE_URL: _FakeResponse(200, {}),
    })
    provider = MusixmatchProvider(http_client=client)

    assert provider.search("ABBA", "Chiquitita") is None


def test_netease_provider_fetches_lyric_by_song_id():
    client = _FakeHttpClient({
        NetEaseProvider.SEARCH_URL: _FakeResponse(200, {"result": {"songs": [{"id": 42}]}}),
        NetEaseProvider.LYRIC_URL: _FakeResponse(200, {"lrc": {"lyric": "[00:01.00]la la"}}),
    })
    provider = NetEaseProvider(http_client=client)

    assert provider.search("ABBA", "Chiquitita") == ("[00:01.00]la la", True)


def test_netease_provider_returns_none_when_search_finds_nothing():
    client = _FakeHttpClient({NetEaseProvider.SEARCH_URL: _FakeResponse(200, {"result": {"songs": []}})})
    provider = NetEaseProvider(http_client=client)

    assert provider.search("Nobody", "Nothing") is None


def test_search_providers_tries_in_order_and_returns_first_hit():
    class _EmptyProvider:
        name = "empty"

        def search(self, artist, title):
            return None

    class _HitProvider:
        name = "hit"

        def search(self, artist, title):
            return "[00:01.00]hit", True

    result = search_providers("A", "B", [_EmptyProvider(), _HitProvider()])

    assert result == ("[00:01.00]hit", True, "hit")


def test_search_providers_returns_none_when_all_fail_or_error():
    class _RaisingProvider:
        name = "raising"

        def search(self, artist, title):
            raise RuntimeError("boom")

    class _EmptyProvider:
        name = "empty"

        def search(self, artist, title):
            return None

    assert search_providers("A", "B", [_RaisingProvider(), _EmptyProvider()]) is None
