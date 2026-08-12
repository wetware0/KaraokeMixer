from app.workers.venvs import detect_worker_venvs, venv_python_path


def test_detect_worker_venvs_false_when_absent(tmp_path):
    assert detect_worker_venvs(tmp_path) == {"demucs": False, "uvr": False, "whisperx": False}


def test_detect_worker_venvs_true_when_python_executable_exists(tmp_path):
    demucs_python = venv_python_path(tmp_path, "demucs")
    demucs_python.parent.mkdir(parents=True)
    demucs_python.write_text("", encoding="utf-8")

    result = detect_worker_venvs(tmp_path)

    assert result == {"demucs": True, "uvr": False, "whisperx": False}


def test_detect_worker_venvs_true_for_all_three_when_all_present(tmp_path):
    for worker in ("demucs", "uvr", "whisperx"):
        python_path = venv_python_path(tmp_path, worker)
        python_path.parent.mkdir(parents=True)
        python_path.write_text("", encoding="utf-8")

    assert detect_worker_venvs(tmp_path) == {"demucs": True, "uvr": True, "whisperx": True}
