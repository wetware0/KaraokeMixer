from app.scanner import find_outputs


def test_find_outputs_detects_instrumental_beside_original(tmp_path):
    media_root = tmp_path / "Media"
    media_root.mkdir()
    audio = media_root / "Song.flac"
    audio.write_bytes(b"")
    (media_root / "Song.instrumental.mp3").write_bytes(b"")

    outputs, lrc_state = find_outputs(audio, media_root, mirror_roots=[])

    assert outputs.instrumental is True
    assert outputs.vocals is False
    assert outputs.lrc is False
    assert lrc_state is None


def test_find_outputs_detects_and_classifies_lrc_beside_original(tmp_path):
    media_root = tmp_path / "Media"
    media_root.mkdir()
    audio = media_root / "Song.flac"
    audio.write_bytes(b"")
    (media_root / "Song.lrc").write_text("[00:01.00]Hello\n", encoding="utf-8")

    outputs, lrc_state = find_outputs(audio, media_root, mirror_roots=[])

    assert outputs.lrc is True
    assert lrc_state == "line_timed"


def test_find_outputs_checks_mirror_root_when_not_beside_original(tmp_path):
    media_root = tmp_path / "Media"
    (media_root / "ABBA").mkdir(parents=True)
    mirror_root = tmp_path / "Stems"
    (mirror_root / "ABBA").mkdir(parents=True)
    audio = media_root / "ABBA" / "Song.flac"
    audio.write_bytes(b"")
    (mirror_root / "ABBA" / "Song.drums.mp3").write_bytes(b"")

    outputs, _ = find_outputs(audio, media_root, mirror_roots=[mirror_root])

    assert outputs.drums is True
    assert outputs.bass is False


def test_find_outputs_prefers_beside_original_over_mirror_root(tmp_path):
    media_root = tmp_path / "Media"
    media_root.mkdir()
    mirror_root = tmp_path / "Stems"
    mirror_root.mkdir()
    audio = media_root / "Song.flac"
    audio.write_bytes(b"")
    (media_root / "Song.bass.mp3").write_bytes(b"")

    outputs, _ = find_outputs(audio, media_root, mirror_roots=[mirror_root])

    assert outputs.bass is True
