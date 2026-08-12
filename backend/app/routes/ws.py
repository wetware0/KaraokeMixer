from __future__ import annotations

import asyncio
import queue

from fastapi import APIRouter, WebSocket
from starlette.concurrency import run_in_threadpool

router = APIRouter()


@router.websocket("/api/ws/jobs")
async def jobs_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    bus = websocket.app.state.event_bus
    subscriber = bus.subscribe()
    closing = False

    def poll_one() -> dict | None:
        # Bounded get(): an unbounded subscriber.get() blocks the worker
        # thread run_in_threadpool hands it to until an event arrives - and
        # asyncio cancelling the *task* wrapping that call cannot interrupt
        # the underlying OS thread, so it would sit blocked (leaked) forever
        # for a disconnected/idle subscriber. Bounding it to 1s means the
        # thread always returns control and the `while not closing` loop
        # below can exit within ~1s of disconnect.
        try:
            return subscriber.get(timeout=1.0)
        except queue.Empty:
            return None

    async def sender() -> None:
        while not closing:
            event = await run_in_threadpool(poll_one)
            if event is not None:
                await websocket.send_json(event)

    async def receiver() -> None:
        # The client never sends anything on this channel; awaiting receive()
        # is how we notice a disconnect promptly instead of only failing on
        # the next send().
        while True:
            await websocket.receive_text()

    sender_task = asyncio.ensure_future(sender())
    receiver_task = asyncio.ensure_future(receiver())
    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED
        )
        closing = True
        for task in pending:
            task.cancel()
        for task in done:
            task.exception()  # retrieve to avoid "exception was never retrieved" warnings
    finally:
        closing = True
        bus.unsubscribe(subscriber)
