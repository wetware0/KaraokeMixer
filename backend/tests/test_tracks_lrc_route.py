from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.scan_test_helpers import run_rescan
from app.pipeline import atomic_publish
from app.lyrics.provenance import lyric_timing_sidecar_path, write_lyric_timing_report


def _seed_track(tmp_path, lrc_content: str | None = None) -> TestClient:
    media_root = tmp_path / "Media"
    media_root.mkdir()
    (media_root / "Song.flac").write_bytes(b"0123456789")
    if lrc_content is not None:
        # write_bytes, not write_text: write_text opens in text mode, which
        # on Windows translates embedded "\n" to "\r\n" on write. The route
        # under test reads the file back via lrc.read_lrc_text (a raw bytes
        # read + manual decode, no newline translation), so a write_text
        # fixture would silently corrupt the "\n" the test asserts against.
        (media_root / "Song.lrc").write_bytes(lrc_content.encode("utf-8"))

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)
    return client


def _seed_track_with_mirror_lrc(tmp_path, lrc_content: str) -> TestClient:
    """A track whose .lrc resolves ONLY under a configured mirror root -
    nothing beside the source audio file."""
    media_root = tmp_path / "Media"
    media_root.mkdir()
    (media_root / "Song.flac").write_bytes(b"0123456789")

    mirror_root = tmp_path / "Mirror"
    mirror_root.mkdir()
    # write_bytes for the same reason as _seed_track above: no newline
    # translation on write, so the fixture's bytes match what the route's
    # byte-level reads/writes are asserted against.
    (mirror_root / "Song.lrc").write_bytes(lrc_content.encode("utf-8"))

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={
            "media_roots": [str(media_root)],
            "mirror_roots": [str(mirror_root)],
            "device_preference": "auto",
        },
    )
    run_rescan(client)
    return client


def test_get_lrc_returns_content_and_state_when_lrc_exists(tmp_path):
    client = _seed_track(tmp_path, lrc_content="[00:01.00]Hello world\n")

    response = client.get("/api/tracks/1/lrc")

    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True
    assert body["content"] == "[00:01.00]Hello world\n"
    assert body["state"] == "line_timed"


def test_get_lrc_returns_exists_false_when_no_lrc_resolves(tmp_path):
    client = _seed_track(tmp_path, lrc_content=None)

    response = client.get("/api/tracks/1/lrc")

    assert response.status_code == 200
    body = response.json()
    assert body == {"exists": False, "content": "", "state": None, "timing_report": None}


def test_get_lrc_returns_hash_bound_timing_report(tmp_path):
    content = "[00:01.00]<00:01.00>Hello <00:01.50>world\n"
    client = _seed_track(tmp_path, lrc_content=content)
    lrc_path = tmp_path / "Media" / "Song.lrc"
    write_lyric_timing_report(lrc_path, {
        "quality": "review", "engine": "whisperx", "model": "align",
        "method": "dual_audio_consensus_v1", "device": "cuda",
        "confidence_score": 84, "verified_words": 1, "review_words": 1,
        "corrected_words": 1, "review_lines": 1, "agreement_within_0_25": 1,
        "median_agreement_seconds": 0.08, "attribution": "automatic",
        "confirmed_by": None,
    }, [{
        "line_index": 0, "word_index": 1, "word": "world", "confidence": 42,
        "status": "review", "corrected": False,
    }])

    body = client.get("/api/tracks/1/lrc").json()

    assert body["timing_report"]["summary"]["confidence_score"] == 84
    assert body["timing_report"]["words"][0]["word"] == "world"


def test_get_lrc_returns_404_for_unknown_track(tmp_path):
    client = _seed_track(tmp_path)

    response = client.get("/api/tracks/999/lrc")

    assert response.status_code == 404


def test_get_lrc_returns_content_and_state_for_mirror_only_lrc(tmp_path):
    client = _seed_track_with_mirror_lrc(tmp_path, "[00:01.00]Mirror lyrics\n")

    response = client.get("/api/tracks/1/lrc")

    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True
    assert body["content"] == "[00:01.00]Mirror lyrics\n"
    assert body["state"] == "line_timed"


def test_put_lrc_overwrites_an_existing_resolved_lrc_in_place(tmp_path):
    client = _seed_track(tmp_path, lrc_content="[00:01.00]Old lyrics\n")

    response = client.put("/api/tracks/1/lrc", json={"content": "[00:02.00]New lyrics\n"})

    assert response.status_code == 200
    lrc_path = Path(response.json()["path"])
    assert lrc_path.name == "Song.lrc"
    # read_bytes + exact-bytes comparison, not read_text: read_text applies
    # universal-newline translation on read, which would silently mask a
    # write path that corrupted "\n" into "\r\n" on Windows (as write_text
    # on the write side previously did). Comparing raw bytes against the
    # exact utf-8 encoding of the expected content is the only check that
    # actually proves no newline translation happened.
    assert lrc_path.read_bytes() == "[00:02.00]New lyrics\n".encode("utf-8")
    assert response.json()["track"]["outputs"]["lrc"] is True
    assert response.json()["track"]["lrc_state"] == "line_timed"


def test_put_lrc_immediately_publishes_enhanced_state_to_the_library(tmp_path):
    client = _seed_track(tmp_path, lrc_content="[00:01.00]Old lyrics\n")

    response = client.put(
        "/api/tracks/1/lrc",
        json={"content": "[00:02.00]<00:02.00>New <00:02.40>lyrics\n"},
    )

    assert response.status_code == 200
    assert response.json()["track"]["lrc_state"] == "enhanced"
    listed_track = client.get("/api/tracks").json()["tracks"][0]
    assert listed_track["outputs"]["lrc"] is True
    assert listed_track["lrc_state"] == "enhanced"


def test_confirm_high_quality_timing_is_persisted_and_an_edit_invalidates_it(tmp_path):
    client = _seed_track(tmp_path, lrc_content="[00:01.00]<00:01.00>Hello <00:01.50>world\n")

    confirmed = client.post(
        "/api/tracks/1/lrc/confirm-quality",
        json={"confirmed_by": "Peter"},
    )

    assert confirmed.status_code == 200
    provenance = confirmed.json()["lyric_timing_provenance"]
    assert provenance["quality"] == "high_quality"
    assert provenance["attribution"] == "manual"
    assert provenance["confirmed_by"] == "Peter"
    lrc_path = tmp_path / "Media" / "Song.lrc"
    assert lyric_timing_sidecar_path(lrc_path).exists()
    assert client.get("/api/tracks").json()["tracks"][0]["lyric_timing_provenance"] == provenance

    saved = client.put(
        "/api/tracks/1/lrc",
        json={"content": "[00:02.00]<00:02.00>Changed\n"},
    )

    assert saved.status_code == 200
    assert saved.json()["track"]["lyric_timing_provenance"] is None
    assert not lyric_timing_sidecar_path(lrc_path).exists()


def test_confirm_high_quality_timing_requires_enhanced_words(tmp_path):
    client = _seed_track(tmp_path, lrc_content="[00:01.00]Line timed only\n")

    response = client.post(
        "/api/tracks/1/lrc/confirm-quality",
        json={"confirmed_by": "Peter"},
    )

    assert response.status_code == 409
    assert "enhanced" in response.json()["detail"]


def test_put_lrc_without_existing_file_and_no_create_flag_returns_409(tmp_path):
    client = _seed_track(tmp_path, lrc_content=None)

    response = client.put("/api/tracks/1/lrc", json={"content": "[00:01.00]Hi\n"})

    assert response.status_code == 409


def test_put_lrc_creates_beside_when_create_flag_given(tmp_path):
    client = _seed_track(tmp_path, lrc_content=None)

    response = client.put("/api/tracks/1/lrc?create=beside", json={"content": "[00:01.00]Hi\n"})

    assert response.status_code == 200
    lrc_path = Path(response.json()["path"])
    assert lrc_path == tmp_path / "Media" / "Song.lrc"
    assert lrc_path.read_bytes() == "[00:01.00]Hi\n".encode("utf-8")
    assert response.json()["track"]["outputs"]["lrc"] is True
    assert client.get("/api/tracks").json()["tracks"][0]["lrc_state"] == "line_timed"


def test_put_lrc_with_suffix_writes_a_variant_file_beside_the_source(tmp_path):
    client = _seed_track(tmp_path, lrc_content="[00:01.00]Original\n")

    response = client.put("/api/tracks/1/lrc?suffix=alt", json={"content": "[00:02.00]Variant\n"})

    assert response.status_code == 200
    variant_path = Path(response.json()["path"])
    assert variant_path == tmp_path / "Media" / "Song.alt.lrc"
    assert variant_path.read_bytes() == "[00:02.00]Variant\n".encode("utf-8")
    assert "track" not in response.json()
    # the original .lrc is untouched
    assert (tmp_path / "Media" / "Song.lrc").read_bytes() == "[00:01.00]Original\n".encode("utf-8")


def test_put_lrc_rejects_a_path_traversal_suffix(tmp_path):
    client = _seed_track(tmp_path, lrc_content="[00:01.00]Original\n")

    response = client.put("/api/tracks/1/lrc?suffix=../escape", json={"content": "x"})

    assert response.status_code == 422


def test_put_lrc_rejects_a_suffix_containing_a_colon(tmp_path):
    # A colon is how NTFS names an Alternate Data Stream (path:stream) - the
    # allowlist regex (SUFFIX_RE) has no ":" in its character class, so this
    # is rejected the same way traversal segments are, without needing a
    # separate special case for it.
    client = _seed_track(tmp_path, lrc_content="[00:01.00]Original\n")

    response = client.put("/api/tracks/1/lrc?suffix=alt:stream", json={"content": "x"})

    assert response.status_code == 422


def test_put_lrc_never_touches_the_source_audio_file(tmp_path):
    client = _seed_track(tmp_path, lrc_content=None)
    original_bytes = (tmp_path / "Media" / "Song.flac").read_bytes()

    client.put("/api/tracks/1/lrc?create=beside", json={"content": "[00:01.00]Hi\n"})

    assert (tmp_path / "Media" / "Song.flac").read_bytes() == original_bytes


def test_put_lrc_without_create_flag_writes_to_the_mirror_location_it_resolved_from(tmp_path):
    client = _seed_track_with_mirror_lrc(tmp_path, "[00:01.00]Old\n")
    mirror_lrc_path = tmp_path / "Mirror" / "Song.lrc"
    beside_lrc_path = tmp_path / "Media" / "Song.lrc"

    response = client.put("/api/tracks/1/lrc", json={"content": "[00:02.00]New\n"})

    assert response.status_code == 200
    assert Path(response.json()["path"]) == mirror_lrc_path
    assert mirror_lrc_path.read_bytes() == "[00:02.00]New\n".encode("utf-8")
    assert not beside_lrc_path.exists()


# Note on concurrency (FIX 2 in the review): write_track_lrc now serializes
# its resolve-then-atomic_publish section behind a module-level
# threading.Lock (see app.routes.tracks._lrc_write_lock) to close a race
# where two concurrent PUTs for the same track could interleave writes to
# the same `<target>.part` sibling that atomic_publish creates. A
# deterministic test for that interleaving would need to pause one request
# mid-write (e.g. patching atomic_publish/write_bytes with a barrier) while
# a second request runs concurrently on a background thread - doable, but
# a lot of test-only synchronization machinery to prove a single `with
# lock:` block is correctly placed. Given the brief's own guidance that a
# deterministic race test is not required, this is left as a documented
# design decision rather than a flaky/complex timing-dependent test.
