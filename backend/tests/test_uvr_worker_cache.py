from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path
from types import SimpleNamespace


WORKER_PATH = Path(__file__).resolve().parents[1] / "workers" / "uvr_worker.py"
SPEC = importlib.util.spec_from_file_location("uvr_worker", WORKER_PATH)
assert SPEC is not None and SPEC.loader is not None
uvr_worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(uvr_worker)


def test_corrupt_cached_karaoke_model_is_removed_without_touching_valid_models(tmp_path):
    corrupt_name, valid_name, *_ = uvr_worker.KARAOKE_MODEL_NAMES
    corrupt = tmp_path / corrupt_name
    corrupt.write_bytes(b"PK\x03\x04 interrupted download without a central directory")
    valid = tmp_path / valid_name
    with zipfile.ZipFile(valid, "w") as archive:
        archive.writestr("archive/data.pkl", b"checkpoint")
    unrelated = tmp_path / "other.ckpt"
    unrelated.write_bytes(b"not managed by the karaoke preset")

    removed = uvr_worker.remove_corrupt_cached_models(tmp_path)

    assert removed == [corrupt_name]
    assert not corrupt.exists()
    assert valid.is_file()
    assert unrelated.is_file()


def test_missing_model_cache_is_a_clean_noop(tmp_path):
    assert uvr_worker.remove_corrupt_cached_models(tmp_path) == []


def test_stereo_uvr_input_is_used_without_conversion(tmp_path):
    source = tmp_path / "song.flac"
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="2\n", stderr="")

    assert uvr_worker.prepare_separator_input(source, tmp_path / "out", runner) == source
    assert len(calls) == 1
    assert calls[0][0] == "ffprobe"


def test_surround_uvr_input_is_downmixed_to_temporary_stereo_wav(tmp_path):
    source = tmp_path / "surround.flac"
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="6\n", stderr="")
        Path(command[-1]).write_bytes(b"stereo wav")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = uvr_worker.prepare_separator_input(source, output_dir, runner)

    assert result == output_dir / "stereo-input.wav"
    assert result.is_file()
    assert calls[1][0] == "ffmpeg"
    assert calls[1][calls[1].index("-ac") + 1] == "2"


def test_surround_uvr_input_reports_downmix_failure(tmp_path):
    responses = iter([
        SimpleNamespace(returncode=0, stdout="6\n", stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr="unsupported channel layout"),
    ])

    try:
        uvr_worker.prepare_separator_input(
            tmp_path / "surround.flac",
            tmp_path,
            lambda *args, **kwargs: next(responses),
        )
        raise AssertionError("expected downmix failure")
    except RuntimeError as exc:
        assert "unsupported channel layout" in str(exc)
