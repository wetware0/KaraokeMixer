import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app


def test_ws_receives_events_published_after_connecting(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)

    with client.websocket_connect("/api/ws/jobs") as ws:
        # websocket_connect()'s __enter__ only guarantees the transport-level
        # accept() has happened; the route's own bus.subscribe() runs on the
        # next line of its coroutine, which can still be pending. Without
        # this bounded wait, publish() can run before subscribe() and the
        # event is silently dropped, then receive_json() blocks forever.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and app.state.event_bus.subscriber_count() == 0:
            time.sleep(0.01)
        assert app.state.event_bus.subscriber_count() == 1

        app.state.event_bus.publish({"type": "job", "job_id": 1, "status": "queued"})
        received = ws.receive_json()

    assert received == {"type": "job", "job_id": 1, "status": "queued"}


def test_ws_unsubscribes_when_client_disconnects(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)

    with client.websocket_connect("/api/ws/jobs"):
        assert app.state.event_bus.subscriber_count() == 1

    # Bounded poll for the server-side cleanup task to run after the client
    # disconnects; not a fixed synchronization sleep.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and app.state.event_bus.subscriber_count() != 0:
        time.sleep(0.02)

    assert app.state.event_bus.subscriber_count() == 0


def test_ws_rejects_cross_origin_localhost_connections(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(
            "/api/ws/jobs",
            headers={"origin": "https://attacker.example"},
        ):
            pass

    assert error.value.code == 1008
