import type { JobEvent } from "./types";

export interface JobsSocketOptions {
  url?: string;
  onEvent: (event: JobEvent) => void;
  onPollFallback: () => void;
  WebSocketImpl?: new (url: string) => WebSocket;
  reconnectDelayMs?: number;
  pollIntervalMs?: number;
  reconcileIntervalMs?: number;
}

export interface JobsSocketHandle {
  close: () => void;
}

export function connectJobsSocket(options: JobsSocketOptions): JobsSocketHandle {
  const {
    url = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/ws/jobs`,
    onEvent,
    onPollFallback,
    WebSocketImpl = WebSocket,
    reconnectDelayMs = 3000,
    pollIntervalMs = 5000,
    reconcileIntervalMs = 30_000,
  } = options;

  let closed = false;
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let pollTimer: ReturnType<typeof setInterval> | undefined;
  let reconcileTimer: ReturnType<typeof setInterval> | undefined;

  function startPolling(): void {
    if (pollTimer !== undefined) return;
    pollTimer = setInterval(onPollFallback, pollIntervalMs);
  }

  function stopPolling(): void {
    if (pollTimer !== undefined) {
      clearInterval(pollTimer);
      pollTimer = undefined;
    }
  }

  // A browser can sleep through a terminal WebSocket event while the TCP
  // connection still appears open afterwards. A slower reconciliation poll
  // keeps the socket as the fast path but prevents a stale Processing state
  // from surviving indefinitely.
  function startReconciliation(): void {
    if (reconcileTimer !== undefined) return;
    reconcileTimer = setInterval(onPollFallback, reconcileIntervalMs);
  }

  function stopReconciliation(): void {
    if (reconcileTimer !== undefined) {
      clearInterval(reconcileTimer);
      reconcileTimer = undefined;
    }
  }

  function scheduleReconnect(): void {
    if (closed || reconnectTimer !== undefined) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = undefined;
      connect();
    }, reconnectDelayMs);
  }

  function connect(): void {
    if (closed) return;
    socket = new WebSocketImpl(url);
    socket.onopen = () => {
      stopPolling();
      startReconciliation();
    };
    socket.onmessage = (event: MessageEvent) => {
      let parsed: JobEvent;
      try {
        parsed = JSON.parse(event.data as string) as JobEvent;
      } catch {
        return;
      }
      onEvent(parsed);
    };
    socket.onerror = () => {
      stopReconciliation();
      startPolling();
    };
    socket.onclose = () => {
      stopReconciliation();
      startPolling();
      scheduleReconnect();
    };
  }

  connect();

  return {
    close(): void {
      closed = true;
      if (reconnectTimer !== undefined) clearTimeout(reconnectTimer);
      stopPolling();
      stopReconciliation();
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        socket.close();
      }
    },
  };
}
