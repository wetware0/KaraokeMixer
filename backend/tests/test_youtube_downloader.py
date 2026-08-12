from pathlib import Path

import pytest

from app.youtube.downloader import (
    DEFAULT_DURATION_CAP_SECONDS,
    PLAYLIST_ENTRY_CAP,
    YoutubeDownloadError,
    download_youtube_audio,
    probe_youtube_url,
)


class _FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL - a context manager whose extract_info()
    returns canned info or raises, exactly like the real thing does on
    failure. No network call ever happens in this test."""

    last_opts: dict | None = None
    info: dict = {}
    raise_message: str | None = None

    def __init__(self, opts):
        type(self).last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download):
        if type(self).raise_message is not None:
            raise RuntimeError(type(self).raise_message)
        return type(self).info


class _FakeYtDlpModule:
    YoutubeDL = _FakeYoutubeDL


@pytest.fixture(autouse=True)
def _reset_fake_state():
    _FakeYoutubeDL.last_opts = None
    _FakeYoutubeDL.info = {}
    _FakeYoutubeDL.raise_message = None


def test_probe_returns_title_duration_uploader(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {"title": "Chiquitita", "duration": 218, "uploader": "ABBA"}

    info = probe_youtube_url("https://youtube.com/watch?v=abc")

    assert info == {"is_playlist": False, "title": "Chiquitita", "duration": 218.0, "uploader": "ABBA"}


def test_probe_maps_browser_cookies_into_ydl_opts(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {"title": "T", "duration": 10, "uploader": "U"}

    probe_youtube_url("https://youtube.com/watch?v=abc", cookies={"mode": "browser", "browser": "chrome"})

    assert _FakeYoutubeDL.last_opts["cookiesfrombrowser"] == ("chrome",)


def test_probe_maps_cookies_file_into_ydl_opts(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {"title": "T", "duration": 10, "uploader": "U"}

    probe_youtube_url("https://youtube.com/watch?v=abc", cookies={"mode": "file", "cookies_file": "C:/cookies.txt"})

    assert _FakeYoutubeDL.last_opts["cookiefile"] == "C:/cookies.txt"


def test_probe_with_no_cookies_configured_sets_neither_option(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {"title": "T", "duration": 10, "uploader": "U"}

    probe_youtube_url("https://youtube.com/watch?v=abc", cookies={"mode": "none"})

    assert "cookiesfrombrowser" not in _FakeYoutubeDL.last_opts
    assert "cookiefile" not in _FakeYoutubeDL.last_opts


def test_download_writes_to_the_exact_destination(monkeypatch, tmp_path):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    destination = tmp_path / "ABBA - Chiquitita.m4a"

    def fake_extract_info(self, url, download):
        Path(str(destination.with_suffix(""))+".m4a").write_bytes(b"fake-m4a")
        return {"title": "Chiquitita", "duration": 218, "uploader": "ABBA"}

    monkeypatch.setattr(_FakeYoutubeDL, "extract_info", fake_extract_info)

    result = download_youtube_audio("https://youtube.com/watch?v=abc", destination)

    assert result.path == destination
    assert result.title == "Chiquitita"
    assert destination.exists()


def test_download_raises_age_restricted_error_on_the_known_message(monkeypatch, tmp_path):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.raise_message = "Sign in to confirm your age. This video may be inappropriate for some users."

    with pytest.raises(YoutubeDownloadError) as excinfo:
        download_youtube_audio("https://youtube.com/watch?v=abc", tmp_path / "song.m4a")

    assert excinfo.value.age_restricted is True


def test_download_raises_a_non_age_restricted_error_for_other_failures(monkeypatch, tmp_path):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.raise_message = "Video unavailable"

    with pytest.raises(YoutubeDownloadError) as excinfo:
        download_youtube_audio("https://youtube.com/watch?v=abc", tmp_path / "song.m4a")

    assert excinfo.value.age_restricted is False


def test_default_duration_cap_is_ten_minutes():
    assert DEFAULT_DURATION_CAP_SECONDS == 600.0


def test_probe_uses_extract_flat_for_a_pure_playlist_url(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {"entries": [{"url": "https://youtu.be/vid1", "title": "Song", "duration": 10}]}

    info = probe_youtube_url("https://youtube.com/playlist?list=abc")

    assert _FakeYoutubeDL.last_opts["extract_flat"] == "in_playlist"
    assert "noplaylist" not in _FakeYoutubeDL.last_opts
    assert info["is_playlist"] is True


def test_probe_resolves_a_watch_url_with_a_list_param_as_a_single_video(monkeypatch):
    # The most common paste form: a video's URL copied while it happened to
    # be playing inside a playlist. yt-dlp's own default (follow `list=`)
    # would resolve this as the whole playlist - the ruling this test
    # enforces is that it must resolve as exactly the one video instead.
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {"title": "Chiquitita", "duration": 218, "uploader": "ABBA"}

    info = probe_youtube_url("https://youtube.com/watch?v=abc&list=PL123")

    assert info == {"is_playlist": False, "title": "Chiquitita", "duration": 218.0, "uploader": "ABBA"}
    assert _FakeYoutubeDL.last_opts["noplaylist"] is True
    assert "extract_flat" not in _FakeYoutubeDL.last_opts


def test_probe_resolves_a_youtu_be_short_link_with_a_list_param_as_a_single_video(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {"title": "Chiquitita", "duration": 218, "uploader": "ABBA"}

    info = probe_youtube_url("https://youtu.be/abc123?list=PL123")

    assert info == {"is_playlist": False, "title": "Chiquitita", "duration": 218.0, "uploader": "ABBA"}
    assert _FakeYoutubeDL.last_opts["noplaylist"] is True
    assert "extract_flat" not in _FakeYoutubeDL.last_opts


def test_probe_resolves_shorts_and_embed_links_with_list_params_as_single_videos(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {"title": "Chiquitita", "duration": 218, "uploader": "ABBA"}

    for url in (
        "https://youtube.com/shorts/abc123?list=PL123",
        "https://www.youtube.com/embed/abc123?list=PL123",
    ):
        info = probe_youtube_url(url)
        assert info["is_playlist"] is False
        assert _FakeYoutubeDL.last_opts["noplaylist"] is True
        assert "extract_flat" not in _FakeYoutubeDL.last_opts


def test_probe_detects_a_playlist_and_returns_its_entries(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {
        "entries": [
            {"url": "https://youtu.be/vid1", "title": "Song One", "duration": 120},
            {"url": "https://youtu.be/vid2", "title": "Song Two", "duration": 180},
        ]
    }

    info = probe_youtube_url("https://youtube.com/playlist?list=abc")

    assert info == {
        "is_playlist": True,
        "count": 2,
        "total": 2,
        "entries": [
            {"url": "https://youtu.be/vid1", "title": "Song One", "duration": 120.0},
            {"url": "https://youtu.be/vid2", "title": "Song Two", "duration": 180.0},
        ],
    }


def test_probe_caps_playlist_entries_at_the_configured_limit(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {
        "entries": [
            {"url": f"https://youtu.be/vid{i}", "title": f"Song {i}", "duration": 100 + i}
            for i in range(150)
        ]
    }

    info = probe_youtube_url("https://youtube.com/playlist?list=abc")

    assert info["count"] == PLAYLIST_ENTRY_CAP == 100
    assert info["total"] == 150
    assert len(info["entries"]) == 100
    assert info["entries"][0]["title"] == "Song 0"
    assert info["entries"][-1]["title"] == "Song 99"


def test_probe_playlist_entry_falls_back_to_a_constructed_watch_url_when_no_url_is_present(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {"entries": [{"id": "abc123", "title": "Song", "duration": 90}]}

    info = probe_youtube_url("https://youtube.com/playlist?list=abc")

    assert info["entries"][0]["url"] == "https://www.youtube.com/watch?v=abc123"


def test_probe_playlist_entry_defaults_missing_title_and_duration(monkeypatch):
    monkeypatch.setattr("app.youtube.downloader.yt_dlp", _FakeYtDlpModule)
    _FakeYoutubeDL.info = {"entries": [{"id": "abc123"}]}

    info = probe_youtube_url("https://youtube.com/playlist?list=abc")

    assert info["entries"][0] == {"url": "https://www.youtube.com/watch?v=abc123", "title": "Unknown", "duration": 0.0}
