from unittest.mock import MagicMock

from app.scanner import read_tags


def test_read_tags_returns_artist_and_title_from_easy_tags(tmp_path, monkeypatch):
    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {
        "artist": ["ABBA"],
        "title": ["Dancing Queen"],
    }.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    path = tmp_path / "song.flac"
    path.write_bytes(b"not-real-audio")

    artist, title = read_tags(path)

    assert artist == "ABBA"
    assert title == "Dancing Queen"


def test_read_tags_falls_back_to_filename_when_tags_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: None)

    path = tmp_path / "Unknown Artist - Mystery Track.mp3"
    path.write_bytes(b"not-real-audio")

    artist, title = read_tags(path)

    assert artist is None
    assert title == "Unknown Artist - Mystery Track"


def test_read_tags_falls_back_when_mutagen_raises(tmp_path, monkeypatch):
    def raise_error(path, easy=True):
        raise ValueError("corrupt header")

    monkeypatch.setattr("app.scanner.mutagen.File", raise_error)

    path = tmp_path / "Broken Track.wav"
    path.write_bytes(b"not-real-audio")

    artist, title = read_tags(path)

    assert artist is None
    assert title == "Broken Track"


from app.scanner import ExtendedTags, read_extended_tags


def test_read_extended_tags_returns_artist_title_album_year(tmp_path, monkeypatch):
    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {
        "artist": ["ABBA"],
        "title": ["Dancing Queen"],
        "album": ["Arrival"],
        "date": ["1976-04-05"],
    }.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    path = tmp_path / "song.flac"
    path.write_bytes(b"not-real-audio")

    extended = read_extended_tags(path)

    assert extended == ExtendedTags(artist="ABBA", title="Dancing Queen", album="Arrival", year=1976)


def test_read_extended_tags_preserves_contributing_artists(tmp_path, monkeypatch):
    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {
        "artist": ["David Bowie", "Queen", "David Bowie"],
        "title": ["Under Pressure"],
    }.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    extended = read_extended_tags(tmp_path / "song.flac")

    assert extended.artist == "David Bowie; Queen"


def test_read_extended_tags_falls_back_to_album_artist(tmp_path, monkeypatch):
    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {
        "albumartist": ["The Supremes"],
        "title": ["You Can't Hurry Love"],
    }.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    extended = read_extended_tags(tmp_path / "song.flac")

    assert extended.artist == "The Supremes"


def test_read_extended_tags_falls_back_to_original_date_for_year(tmp_path, monkeypatch):
    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {"original date": ["1980-04-01"]}.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    assert read_extended_tags(tmp_path / "song.flac").year == 1980


def test_read_extended_tags_handles_a_bare_year_date_tag(tmp_path, monkeypatch):
    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {"date": ["1999"]}.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    extended = read_extended_tags(tmp_path / "song.mp3")

    assert extended.year == 1999


def test_read_extended_tags_year_is_none_when_date_tag_is_missing_or_unparseable(tmp_path, monkeypatch):
    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {"date": ["not-a-date"]}.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    assert read_extended_tags(tmp_path / "song.mp3").year is None

    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: None)
    assert read_extended_tags(tmp_path / "song2.mp3").year is None


def test_read_extended_tags_treats_implausible_four_digit_dates_as_missing(tmp_path, monkeypatch):
    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {"date": ["1125"]}.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    assert read_extended_tags(tmp_path / "song.mp3").year is None


def test_read_extended_tags_falls_back_to_filename_stem_for_title(tmp_path, monkeypatch):
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: None)

    extended = read_extended_tags(tmp_path / "Unknown Artist - Mystery Track.mp3")

    assert extended == ExtendedTags(artist=None, title="Unknown Artist - Mystery Track", album=None, year=None)


def test_read_tags_still_returns_the_plain_two_tuple(tmp_path, monkeypatch):
    # read_tags is used as-is by app.stages.fetch_lyrics - this pins its
    # signature/behavior now that it delegates to read_extended_tags.
    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {"artist": ["ABBA"], "title": ["Chiquitita"]}.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    artist, title = read_tags(tmp_path / "song.flac")

    assert (artist, title) == ("ABBA", "Chiquitita")


def test_read_extended_tags_survives_tag_access_exception(tmp_path, monkeypatch):
    # Malformed tag structure that raises on .get access
    fake_tags = MagicMock()
    fake_tags.get.side_effect = TypeError("tag access failed")
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    extended = read_extended_tags(tmp_path / "broken.flac")

    assert extended == ExtendedTags(artist=None, title="broken", album=None, year=None)


def test_read_extended_tags_survives_non_string_date_tag(tmp_path, monkeypatch):
    # Non-string date tag value (an object) that would crash _parse_year without str() coercion
    class WeirdObj:
        def __str__(self):
            return "no-year-here"

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {
        "artist": ["Artist"],
        "title": ["Title"],
        "date": [WeirdObj()],  # non-string object instead of string
    }.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)

    extended = read_extended_tags(tmp_path / "weird.flac")

    # Should not raise, year parsing handled non-string gracefully via str() coercion
    assert extended.artist == "Artist"
    assert extended.title == "Title"
    assert extended.year is None  # object has no 4-digit year
