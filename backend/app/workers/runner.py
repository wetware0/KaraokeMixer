from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class WorkerResult:
    status: str  # "completed" | "failed" | "cancelled"
    payload: Optional[dict]
    error_text: Optional[str]


PERSISTENT_WORKER_SCRIPTS = {"demucs_worker.py", "whisperx_worker.py"}


class PersistentWorkerPool:
    """Job-scoped pool for GPU workers whose models are expensive to load.

    Demucs and WhisperX speak the same line-delimited protocol as the regular
    one-shot runner when started with ``--persistent``.  A process is reused
    for every compatible stage in the job and closed when the job reaches a
    terminal state.  Other worker scripts continue through ``run_worker`` so
    this optimisation cannot accidentally change their lifecycle.
    """

    def __init__(self) -> None:
        self._workers: dict[tuple[Path, Path], _PersistentWorker] = {}

    def run(
        self,
        python_executable: Path,
        script_path: Path,
        args: dict,
        *,
        on_progress: Optional[Callable[[dict], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        timeout_seconds: Optional[float] = None,
    ) -> WorkerResult:
        if script_path.name not in PERSISTENT_WORKER_SCRIPTS:
            return run_worker(
                python_executable,
                script_path,
                args,
                on_progress=on_progress,
                cancel_event=cancel_event,
                timeout_seconds=timeout_seconds,
            )

        key = (python_executable.resolve(), script_path.resolve())
        worker = self._workers.get(key)
        if worker is None or not worker.is_running:
            worker = _PersistentWorker(*key)
            self._workers[key] = worker
        result = worker.run(
            args,
            on_progress=on_progress,
            cancel_event=cancel_event,
            timeout_seconds=timeout_seconds,
        )
        if not worker.is_running:
            self._workers.pop(key, None)
        return result

    def close(self) -> None:
        for worker in list(self._workers.values()):
            worker.close()
        self._workers.clear()

    def __enter__(self) -> "PersistentWorkerPool":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()


class _PersistentWorker:
    def __init__(self, python_executable: Path, script_path: Path) -> None:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [str(python_executable), str(script_path), "--persistent"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        self._stdout_lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stderr_chunks: list[str] = []
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    @property
    def is_running(self) -> bool:
        return self._process.poll() is None

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            self._stdout_lines.put(line)
        self._stdout_lines.put(None)

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            self._stderr_chunks.append(line)

    def run(
        self,
        args: dict,
        *,
        on_progress: Optional[Callable[[dict], None]],
        cancel_event: Optional[threading.Event],
        timeout_seconds: Optional[float],
    ) -> WorkerResult:
        if not self.is_running or self._process.stdin is None:
            return WorkerResult(status="failed", payload=None, error_text="persistent worker is not running")
        try:
            self._process.stdin.write(json.dumps(args) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.close()
            return WorkerResult(status="failed", payload=None, error_text=str(exc))

        started = time.monotonic()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                self.close(force=True)
                return WorkerResult(status="cancelled", payload=None, error_text=None)
            if timeout_seconds is not None and time.monotonic() - started > timeout_seconds:
                self.close(force=True)
                return WorkerResult(
                    status="failed",
                    payload=None,
                    error_text=f"worker timed out after {timeout_seconds}s",
                )
            try:
                line = self._stdout_lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                detail = self._stderr_tail() or f"worker exited with code {self._process.returncode}"
                self.close()
                return WorkerResult(status="failed", payload=None, error_text=detail)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "progress":
                if on_progress is not None:
                    on_progress(event)
                continue
            if event.get("type") != "result":
                continue
            if event.get("status") == "completed":
                return WorkerResult(status="completed", payload=event.get("payload"), error_text=None)
            return WorkerResult(
                status="failed",
                payload=event.get("payload"),
                error_text=event.get("error") or self._stderr_tail() or "worker reported failure",
            )

    def _stderr_tail(self) -> str:
        text = "".join(self._stderr_chunks).strip()
        return "\n".join(text.splitlines()[-10:]) if text else ""

    def close(self, *, force: bool = False) -> None:
        if self._process.poll() is None and not force and self._process.stdin is not None:
            try:
                self._process.stdin.close()
                self._process.wait(timeout=10)
            except Exception:
                force = True
        if self._process.poll() is None and force:
            try:
                _terminate(self._process)
                self._process.wait(timeout=5)
            except Exception:
                pass
        self._stdout_thread.join(timeout=2)
        self._stderr_thread.join(timeout=2)


def run_worker(
    python_executable: Path,
    script_path: Path,
    args: dict,
    *,
    on_progress: Optional[Callable[[dict], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    timeout_seconds: Optional[float] = None,
) -> WorkerResult:
    """Run `script_path` under `python_executable` as a subprocess and collect
    its JSON-over-stdio result.

    Protocol: `args` is written to the child's stdin as one JSON line, then
    stdin is closed. The child prints one JSON object per stdout line: zero
    or more `{"type": "progress", "message": str}` lines, forwarded live to
    `on_progress`, followed by exactly one final line -
    `{"type": "result", "status": "completed", "payload": {...}}` or
    `{"type": "result", "status": "failed", "error": str}`. stderr is
    captured in full and used as `error_text` when no final line ever
    arrives (a crash) or the final line carries no `error`.

    This is the one place in the app allowed to terminate a subprocess, and
    only ever a subprocess this call itself spawned - via `cancel_event` or
    `timeout_seconds`.
    """
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(python_executable), str(script_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    process.stdin.write(json.dumps(args) + "\n")
    process.stdin.close()

    stdout_lines: "queue.Queue[Optional[str]]" = queue.Queue()
    stderr_chunks: list[str] = []

    def read_stdout() -> None:
        for line in process.stdout:
            stdout_lines.put(line)
        stdout_lines.put(None)

    def read_stderr() -> None:
        for line in process.stderr:
            stderr_chunks.append(line)

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    final_result: Optional[dict] = None
    started = time.monotonic()
    outcome: Optional[str] = None  # "cancelled" | "timed_out" when triggered early, before a result arrived
    while True:
        # Once a result line has been parsed, the child has already committed
        # to an outcome - stop evaluating cancel/timeout entirely so a
        # cancellation or timeout that lands afterward (while we're merely
        # waiting for the process to exit) can never override a result that
        # was genuinely received.
        if final_result is None:
            if cancel_event is not None and cancel_event.is_set():
                outcome = "cancelled"
                break
            if timeout_seconds is not None and time.monotonic() - started > timeout_seconds:
                outcome = "timed_out"
                break
        try:
            line = stdout_lines.get(timeout=0.2)
        except queue.Empty:
            continue
        if line is None:
            break
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "progress" and on_progress is not None:
            on_progress(event)
        elif event.get("type") == "result":
            final_result = event
            # Stop polling immediately - see comment above.
            break

    if final_result is None and outcome in ("cancelled", "timed_out"):
        try:
            _terminate(process)
            process.wait(timeout=5)
        except Exception:
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
            return WorkerResult(
                status="failed",
                payload=None,
                error_text="worker process could not be terminated cleanly",
            )
    else:
        try:
            process.wait(timeout=5)
        except Exception:
            pass

    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    stderr_text = "".join(stderr_chunks).strip()

    # A received result always wins, regardless of what outcome flag (if any)
    # got set - see the loop-exit comment above.
    if final_result is not None:
        if final_result.get("status") == "completed":
            return WorkerResult(status="completed", payload=final_result.get("payload"), error_text=None)
        error_text = final_result.get("error") or stderr_text or "worker reported failure"
        return WorkerResult(status="failed", payload=final_result.get("payload"), error_text=error_text)

    if outcome == "cancelled":
        return WorkerResult(status="cancelled", payload=None, error_text=None)
    if outcome == "timed_out":
        return WorkerResult(
            status="failed", payload=None, error_text=f"worker timed out after {timeout_seconds}s"
        )
    detail = "\n".join(stderr_text.splitlines()[-10:]) if stderr_text else f"worker exited with code {process.returncode}"
    return WorkerResult(status="failed", payload=None, error_text=detail)


def _terminate(process: subprocess.Popen) -> None:
    """Terminate `process`'s whole tree. Windows: `taskkill /T /F`, matching
    TrackSeparator/src/core/backing_vocals.py::_terminate_process_tree -
    plain `Popen.terminate()` only signals the launcher, not children it may
    have spawned (audio-separator does)."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    else:
        process.terminate()
