import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectJobsSocket } from "./jobsSocket";
import type { JobEvent } from "./types";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {}

  triggerOpen(): void {
    this.onopen?.();
  }

  triggerMessage(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  triggerClose(): void {
    this.onclose?.();
  }
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("connectJobsSocket", () => {
  it("forwards parsed events from the socket", () => {
    const events: JobEvent[] = [];
    connectJobsSocket({
      onEvent: (event) => events.push(event),
      onPollFallback: () => {},
      WebSocketImpl: FakeWebSocket as unknown as new (url: string) => WebSocket,
    });

    const socket = FakeWebSocket.instances[0];
    socket.triggerOpen();
    socket.triggerMessage({ type: "job", job_id: 1, status: "completed" });

    expect(events).toEqual([{ type: "job", job_id: 1, status: "completed" }]);
  });

  it("drops malformed message frames without throwing and keeps forwarding later events", () => {
    const events: JobEvent[] = [];
    connectJobsSocket({
      onEvent: (event) => events.push(event),
      onPollFallback: () => {},
      WebSocketImpl: FakeWebSocket as unknown as new (url: string) => WebSocket,
    });

    const socket = FakeWebSocket.instances[0];
    socket.triggerOpen();

    // Bypass triggerMessage's JSON.stringify so the raw payload is malformed.
    expect(() => socket.onmessage?.({ data: "not json{" } as MessageEvent)).not.toThrow();
    expect(events).toEqual([]);

    socket.triggerMessage({ type: "job", job_id: 2, status: "completed" });
    expect(events).toEqual([{ type: "job", job_id: 2, status: "completed" }]);
  });

  it("falls back to polling and reconnects after the socket closes", () => {
    // reconnectDelayMs (1000) and pollIntervalMs (700) are deliberately
    // non-multiples of each other so their timers never fire on the same
    // tick - a collision would make it ambiguous how many poll calls to
    // expect once the reconnect timer also fires.
    const pollCalls: number[] = [];
    connectJobsSocket({
      onEvent: () => {},
      onPollFallback: () => pollCalls.push(1),
      WebSocketImpl: FakeWebSocket as unknown as new (url: string) => WebSocket,
      reconnectDelayMs: 1000,
      pollIntervalMs: 700,
    });

    FakeWebSocket.instances[0].triggerClose();

    vi.advanceTimersByTime(700); // first poll tick
    expect(pollCalls.length).toBe(1);

    vi.advanceTimersByTime(300); // total 1000ms since close: reconnect timer fires
    expect(FakeWebSocket.instances.length).toBe(2);
    expect(pollCalls.length).toBe(1); // next poll tick isn't due until 1400ms

    FakeWebSocket.instances[1].triggerOpen();
    vi.advanceTimersByTime(700); // would have been the next poll tick (1400ms) had polling not stopped
    expect(pollCalls.length).toBe(1); // polling stopped once reconnected - no new poll calls
  });

  it("periodically reconciles even while the socket appears healthy", () => {
    const pollCalls: number[] = [];
    const handle = connectJobsSocket({
      onEvent: () => {},
      onPollFallback: () => pollCalls.push(1),
      WebSocketImpl: FakeWebSocket as unknown as new (url: string) => WebSocket,
      reconcileIntervalMs: 1000,
    });

    FakeWebSocket.instances[0].triggerOpen();
    vi.advanceTimersByTime(999);
    expect(pollCalls).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(pollCalls).toHaveLength(1);

    handle.close();
    vi.advanceTimersByTime(5000);
    expect(pollCalls).toHaveLength(1);
  });

  it("stops all timers and does not reconnect once closed", () => {
    const pollCalls: number[] = [];
    const handle = connectJobsSocket({
      onEvent: () => {},
      onPollFallback: () => pollCalls.push(1),
      WebSocketImpl: FakeWebSocket as unknown as new (url: string) => WebSocket,
      reconnectDelayMs: 1000,
      pollIntervalMs: 500,
    });

    FakeWebSocket.instances[0].triggerClose();
    handle.close();

    vi.advanceTimersByTime(5000);

    expect(pollCalls.length).toBe(0);
    expect(FakeWebSocket.instances.length).toBe(1);
  });
});
