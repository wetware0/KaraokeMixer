from fastapi.testclient import TestClient

from app.main import create_app


def test_get_system_route_reports_probed_device_and_worker_venv_status(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.probe_device", lambda: "cpu")
    app = create_app(db_path=tmp_path / "library.db", worker_venv_base=tmp_path / "workers")
    client = TestClient(app)
    response = client.get("/api/system")
    assert response.status_code == 200
    assert response.json() == {"device": "cpu", "workers": {"demucs": False, "uvr": False, "whisperx": False}}
