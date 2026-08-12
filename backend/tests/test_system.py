import subprocess

from app.system import probe_device


def test_probe_device_returns_cpu_when_nvidia_smi_is_absent(monkeypatch):
    monkeypatch.setattr("app.system.shutil.which", lambda name: None)
    assert probe_device() == "cpu"


def test_probe_device_returns_cuda_when_nvidia_smi_succeeds(monkeypatch):
    monkeypatch.setattr("app.system.shutil.which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        "app.system.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode=0),
    )
    assert probe_device() == "cuda"


def test_probe_device_returns_cpu_when_nvidia_smi_exits_nonzero(monkeypatch):
    monkeypatch.setattr("app.system.shutil.which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        "app.system.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode=1),
    )
    assert probe_device() == "cpu"


def test_probe_device_returns_cpu_when_nvidia_smi_times_out(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=5)

    monkeypatch.setattr("app.system.shutil.which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr("app.system.subprocess.run", raise_timeout)
    assert probe_device() == "cpu"
