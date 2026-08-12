import queue

from app.events import EventBus


def test_subscribe_returns_a_queue_that_receives_published_events():
    bus = EventBus()
    subscriber = bus.subscribe()

    bus.publish({"type": "job", "job_id": 1, "status": "queued"})

    assert subscriber.get(timeout=1) == {"type": "job", "job_id": 1, "status": "queued"}


def test_multiple_subscribers_each_receive_the_same_event():
    bus = EventBus()
    first = bus.subscribe()
    second = bus.subscribe()

    bus.publish({"type": "job", "job_id": 1, "status": "running"})

    assert first.get(timeout=1) == {"type": "job", "job_id": 1, "status": "running"}
    assert second.get(timeout=1) == {"type": "job", "job_id": 1, "status": "running"}


def test_unsubscribe_stops_further_delivery():
    bus = EventBus()
    subscriber = bus.subscribe()
    bus.unsubscribe(subscriber)

    bus.publish({"type": "job", "job_id": 1, "status": "completed"})

    assert subscriber.empty()


def test_subscriber_count_reflects_active_subscribers():
    bus = EventBus()
    assert bus.subscriber_count() == 0
    subscriber = bus.subscribe()
    assert bus.subscriber_count() == 1
    bus.unsubscribe(subscriber)
    assert bus.subscriber_count() == 0
