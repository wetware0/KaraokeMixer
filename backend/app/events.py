from __future__ import annotations

import queue
import threading


class EventBus:
    """In-process pub/sub: the job queue publishes; the WebSocket route and
    tests subscribe. Each subscriber gets its own thread-safe queue.Queue so a
    slow or disconnected subscriber never blocks another subscriber or the
    publisher."""

    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> "queue.Queue[dict]":
        subscriber: "queue.Queue[dict]" = queue.Queue()
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: "queue.Queue[dict]") -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def publish(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.put(event)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
