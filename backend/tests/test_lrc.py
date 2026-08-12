from app.lrc import LrcDocument, TimingState, classify_lrc_file


def test_classifies_untimed_lyrics():
    assert LrcDocument.parse("hello\nworld\n").state == TimingState.UNTIMED


def test_classifies_line_timed_lyrics():
    assert LrcDocument.parse("[00:01.00]hello\n").state == TimingState.LINE_TIMED


def test_classifies_enhanced_lyrics():
    document = LrcDocument.parse("[00:01.00]<00:01.00>hello\n")
    assert document.state == TimingState.ENHANCED


def test_classifies_empty_lyrics_with_only_metadata():
    assert LrcDocument.parse("[ar:Artist]\n\n").state == TimingState.EMPTY


def test_classifies_orphaned_word_tag_as_unknown():
    document = LrcDocument.parse("Hello <00:01.00> world\n")
    assert document.state == TimingState.UNKNOWN


def test_classify_lrc_file_empty_content_is_empty_state(tmp_path):
    path = tmp_path / "empty.lrc"
    path.write_text("", encoding="utf-8")
    assert classify_lrc_file(path) == TimingState.EMPTY


def test_classify_lrc_file_reads_real_content(tmp_path):
    path = tmp_path / "song.lrc"
    path.write_text("[00:01.00]hello\n", encoding="utf-8")
    assert classify_lrc_file(path) == TimingState.LINE_TIMED


def test_classify_lrc_file_returns_unknown_when_file_cannot_be_read(tmp_path):
    broken = tmp_path / "broken.lrc"
    broken.mkdir()
    assert classify_lrc_file(broken) == TimingState.UNKNOWN


def test_classify_lrc_file_reads_utf16_with_bom(tmp_path):
    path = tmp_path / "utf16.lrc"
    path.write_bytes("[00:01.00]hello\n".encode("utf-16"))  # writes BOM + UTF-16-LE
    assert classify_lrc_file(path) == TimingState.LINE_TIMED


def test_classify_lrc_file_falls_back_to_cp1252(tmp_path):
    path = tmp_path / "cp1252.lrc"
    path.write_bytes("[00:01.00]don't stop\n".encode("cp1252"))  # 0x92 is invalid UTF-8
    assert classify_lrc_file(path) == TimingState.LINE_TIMED


def test_classify_lrc_file_metadata_only_file_is_empty_state(tmp_path):
    path = tmp_path / "meta.lrc"
    path.write_text("[ar:Artist]\n[ti:Title]\n", encoding="utf-8")
    assert classify_lrc_file(path) == TimingState.EMPTY
