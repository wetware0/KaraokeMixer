from fastapi.testclient import TestClient

from app.main import create_app


def test_health_check_returns_ok(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_create_app_serves_spa_when_dist_dir_is_provided(tmp_path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<h1>Karaoke MM</h1>", encoding="utf-8")

    app = create_app(db_path=tmp_path / "library.db", dist_dir=dist_dir)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Karaoke MM" in response.text


def test_create_app_skips_static_mount_when_dist_dir_is_missing(tmp_path):
    app = create_app(db_path=tmp_path / "library.db", dist_dir=tmp_path / "does-not-exist")
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200


def test_create_app_rejects_non_loopback_clients(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app, client=("203.0.113.10", 50000))

    response = client.get("/api/health")

    assert response.status_code == 403
    assert response.json() == {"detail": "Karaoke Media Manager accepts local requests only"}


def test_create_app_rejects_untrusted_host_headers(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)

    response = client.get("/api/health", headers={"host": "attacker.example"})

    assert response.status_code == 403


def test_create_app_rejects_cross_origin_localhost_requests(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)

    response = client.post(
        "/api/rescan",
        headers={"origin": "https://attacker.example"},
    )

    assert response.status_code == 403


def test_create_app_can_explicitly_allow_reverse_proxy_clients(tmp_path):
    app = create_app(db_path=tmp_path / "library.db", allow_remote_clients=True)
    client = TestClient(app, client=("203.0.113.10", 50000))

    response = client.get("/api/health", headers={"host": "karaoke.example"})

    assert response.status_code == 200
