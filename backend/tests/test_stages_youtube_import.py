from pathlib import Path

import app.stages.youtube_import as youtube_import_module
from app.pipeline import StageContext, StageStatus
from app.stages.youtube_import import YoutubeImportStage
from app.youtube.downloader import DownloadResult, YoutubeDownloadError


def _make_stage(prober_result, downloader_side_effect=None, submit_calls=None):
    submit_calls = submit_calls if submit_calls is not None else []

    def prober(url, cookies=None):
        return prober_result

    def downloader(url, destination, cookies=None):
        if downloader_side_effect is not None:
            raise downloader_side_effect
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-m4a")
        return DownloadResult(
            path=destination, title=prober_result["title"], duration=prober_result["duration"],
            uploader=prober_result["uploader"],
        )

    def submit_followup(recipe, options, items):
        submit_calls.append((recipe, options, items))
        return 99

    return YoutubeImportStage(downloader=downloader, prober=prober, submit_followup_fn=submit_followup)


def _ctx(tmp_path, downloads_root, **extra_options):
    return StageContext(
        source_path=tmp_path / "unused",
        overwrite=False,
        options={
            "youtube_url": "https://youtube.com/watch?v=abc",
            "downloads_root": str(downloads_root),
            "db_path": str(tmp_path / "library.db"),
            **extra_options,
        },
    )


def test_declared_outputs_is_always_empty(tmp_path):
    stage = _make_stage({"title": "T", "duration": 10.0, "uploader": "U"})
    ctx = _ctx(tmp_path, tmp_path / "Downloads")

    assert stage.declared_outputs(ctx) == []


def test_downloads_and_publishes_into_the_downloads_root(tmp_path):
    downloads_root = tmp_path / "Downloads"
    stage = _make_stage({"title": "Chiquitita", "duration": 200.0, "uploader": "ABBA"})
    ctx = _ctx(tmp_path, downloads_root)

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert (downloads_root / "ABBA - Chiquitita.m4a").exists()


def test_prefers_explicit_artist_and_title_over_probed_metadata(tmp_path):
    downloads_root = tmp_path / "Downloads"
    stage = _make_stage({"title": "Probed Title", "duration": 200.0, "uploader": "Probed Uploader"})
    ctx = _ctx(tmp_path, downloads_root, youtube_artist="My Artist", youtube_title="My Title")

    stage.run(ctx)

    assert (downloads_root / "My Artist - My Title.m4a").exists()


def test_fails_when_video_exceeds_the_duration_cap(tmp_path):
    stage = _make_stage({"title": "Long", "duration": 9999.0, "uploader": "X"})
    ctx = _ctx(tmp_path, tmp_path / "Downloads")

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "duration cap" in result.detail
    assert not (tmp_path / "Downloads").exists()


def test_age_restriction_failure_names_the_cookies_setting(tmp_path):
    stage = _make_stage(
        {"title": "Explicit", "duration": 200.0, "uploader": "X"},
        downloader_side_effect=YoutubeDownloadError("age gate", age_restricted=True),
    )
    ctx = _ctx(tmp_path, tmp_path / "Downloads")

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "cookies" in result.detail.lower()


def test_a_non_age_restricted_download_failure_reports_its_own_message(tmp_path):
    stage = _make_stage(
        {"title": "Gone", "duration": 200.0, "uploader": "X"},
        downloader_side_effect=YoutubeDownloadError("Video unavailable", age_restricted=False),
    )
    ctx = _ctx(tmp_path, tmp_path / "Downloads")

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "Video unavailable" in result.detail


def test_submits_a_followup_job_when_process_after_is_requested(tmp_path):
    submit_calls = []
    downloads_root = tmp_path / "Downloads"
    stage = _make_stage({"title": "Chiquitita", "duration": 200.0, "uploader": "ABBA"}, submit_calls=submit_calls)
    ctx = _ctx(
        tmp_path, downloads_root,
        process_after={"recipe": "karaoke", "options": {"model": "htdemucs"}},
    )

    stage.run(ctx)

    assert len(submit_calls) == 1
    recipe, options, items = submit_calls[0]
    assert recipe == "karaoke"
    assert options["model"] == "htdemucs"
    assert items[0]["source_path"] == str(downloads_root / "ABBA - Chiquitita.m4a")


def test_does_not_submit_a_followup_job_when_not_requested(tmp_path):
    submit_calls = []
    stage = _make_stage({"title": "Chiquitita", "duration": 200.0, "uploader": "ABBA"}, submit_calls=submit_calls)
    ctx = _ctx(tmp_path, tmp_path / "Downloads")

    stage.run(ctx)

    assert submit_calls == []


def test_rescans_the_downloads_root_into_the_library(tmp_path):
    from app.db import get_connection, list_tracks

    downloads_root = tmp_path / "Downloads"
    db_path = tmp_path / "library.db"
    stage = _make_stage({"title": "Chiquitita", "duration": 200.0, "uploader": "ABBA"})
    ctx = _ctx(tmp_path, downloads_root)

    stage.run(ctx)

    conn = get_connection(db_path)
    titles = [track["title"] for track in list_tracks(conn)]
    # The fake downloaded file is raw bytes (b"fake-m4a"), not a real M4A with
    # tags, so scanner.read_tags() falls back to the filename stem - the full
    # "{artist} - {title}" name this stage constructed, not just the title.
    assert "ABBA - Chiquitita" in titles


def test_replace_tracks_is_called_with_the_raw_downloads_root_string_when_explicit(tmp_path, monkeypatch):
    # Regression: replace_tracks used to be keyed by str(Path(downloads_root))
    # (backslashes on Windows), while /api/rescan (routes/tracks.py) keys the
    # tracks table by the RAW settings string. If the raw string uses forward
    # slashes, Path() normalization silently mints a second, differently-
    # spelled root for the same directory, duplicating the whole library
    # under it on the next rescan. The exact string passed to replace_tracks
    # must be the raw settings-string spelling, untouched by Path().
    replace_tracks_calls = []
    monkeypatch.setattr(
        youtube_import_module, "replace_tracks",
        lambda conn, media_root, records: replace_tracks_calls.append(media_root),
    )
    monkeypatch.setattr(youtube_import_module.scanner, "scan_media_root", lambda root, mirror_roots: [])

    downloads_root_dir = tmp_path / "Downloads"
    downloads_root_dir.mkdir()
    raw_downloads_root = str(downloads_root_dir).replace("\\", "/")
    assert raw_downloads_root != str(Path(raw_downloads_root))  # sanity: Path() would normalize this

    stage = _make_stage({"title": "Chiquitita", "duration": 200.0, "uploader": "ABBA"})
    ctx = StageContext(
        source_path=tmp_path / "unused", overwrite=False,
        options={
            "youtube_url": "https://youtube.com/watch?v=abc",
            "downloads_root": raw_downloads_root,
            "db_path": str(tmp_path / "library.db"),
        },
    )

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert replace_tracks_calls == [raw_downloads_root]


def test_replace_tracks_is_called_with_the_raw_media_root_string_on_the_fallback_path(tmp_path, monkeypatch):
    # Same regression as above, but exercising the media-root-fallback path
    # (no explicit downloads_root - falls back to media_roots[0], exactly as
    # routes/youtube.py's own downloads_root fallback does before submitting
    # the job). The fallback must also preserve the raw string spelling.
    replace_tracks_calls = []
    monkeypatch.setattr(
        youtube_import_module, "replace_tracks",
        lambda conn, media_root, records: replace_tracks_calls.append(media_root),
    )
    monkeypatch.setattr(youtube_import_module.scanner, "scan_media_root", lambda root, mirror_roots: [])

    media_root_dir = tmp_path / "Media"
    media_root_dir.mkdir()
    raw_media_root = str(media_root_dir).replace("\\", "/")
    assert raw_media_root != str(Path(raw_media_root))  # sanity: Path() would normalize this

    stage = _make_stage({"title": "Chiquitita", "duration": 200.0, "uploader": "ABBA"})
    ctx = StageContext(
        source_path=tmp_path / "unused", overwrite=False,
        options={
            "youtube_url": "https://youtube.com/watch?v=abc",
            "media_roots": [raw_media_root],
            "db_path": str(tmp_path / "library.db"),
        },
    )

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert replace_tracks_calls == [raw_media_root]


def test_scan_media_root_receives_the_mirror_roots(tmp_path, monkeypatch):
    scan_calls = []
    monkeypatch.setattr(
        youtube_import_module.scanner, "scan_media_root",
        lambda root, mirror_roots: scan_calls.append(mirror_roots) or [],
    )
    monkeypatch.setattr(youtube_import_module, "replace_tracks", lambda conn, media_root, records: None)

    downloads_root = tmp_path / "Downloads"
    stage = _make_stage({"title": "Chiquitita", "duration": 200.0, "uploader": "ABBA"})
    ctx = _ctx(tmp_path, downloads_root, mirror_roots=["D:/Stems"])

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert scan_calls == [[Path("D:/Stems")]]


def test_fails_when_the_probed_duration_is_unknown(tmp_path):
    # Ruling: an unknown/absent/zero duration must FAIL the pre-download
    # check rather than slip past the duration cap (0.0 is never > any
    # positive cap) - previously this let unknown-duration videos through
    # to a real download attempt.
    downloader_calls = []

    def prober(url, cookies=None):
        return {"title": "T", "duration": None, "uploader": "U"}

    def downloader(url, destination, cookies=None):
        downloader_calls.append(url)
        raise AssertionError("downloader must not be called when duration is unknown")

    stage = YoutubeImportStage(downloader=downloader, prober=prober)
    ctx = _ctx(tmp_path, tmp_path / "Downloads")

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "duration" in result.detail.lower()
    assert downloader_calls == []
    assert not (tmp_path / "Downloads").exists()


def test_fails_when_the_probed_duration_is_zero(tmp_path):
    def prober(url, cookies=None):
        return {"title": "T", "duration": 0.0, "uploader": "U"}

    def downloader(url, destination, cookies=None):
        raise AssertionError("downloader must not be called when duration is unknown")

    stage = YoutubeImportStage(downloader=downloader, prober=prober)
    ctx = _ctx(tmp_path, tmp_path / "Downloads")

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "duration" in result.detail.lower()
