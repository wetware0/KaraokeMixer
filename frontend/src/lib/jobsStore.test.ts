import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JobSummary, Track, TrackProcessingFailure } from "./types";

vi.mock("./api", () => ({
  fetchJobs: vi.fn(),
  fetchTrackFailures: vi.fn(),
  cancelJob: vi.fn(),
}));
vi.mock("./jobsSocket", () => ({
  connectJobsSocket: vi.fn(),
}));

import { cancelJob, fetchJobs, fetchTrackFailures } from "./api";
import { connectJobsSocket } from "./jobsSocket";
import { createJobsStore } from "./jobsStore.svelte";

beforeEach(() => {
  vi.mocked(fetchTrackFailures).mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
});

const sampleJob: JobSummary = {
  id: 1, recipe: "fake", options: {}, status: "completed",
  created_at: "t0", started_at: "t1", finished_at: "t2",
  item_counts: { queued: 0, running: 0, completed: 1, failed: 0, skipped: 0, cancelled: 0 },
};

const updatedTrack: Track = {
  id: 7, media_root: "D:/Media", relative_path: "Song.flac", artist: "ABBA", title: "Dancing Queen",
  outputs: {
    instrumental: false, vocals: false, lead_vocals: false, backing_vocals: false,
    drums: false, bass: false, guitar: false, piano: false, other: false, lrc: false,
  },
  lrc_state: null, stem_count: 0, album: "Arrival", year: 1976, duration_seconds: 210,
};

// Deferred promise helper for exercising out-of-order resolution: lets a test
// control exactly when a mocked fetchJobs() call settles, independent of
// when it was invoked.
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("jobsStore", () => {
  it("loads unresolved track failures for Library row status", async () => {
    const failure: TrackProcessingFailure = {
      track_id: 7, job_id: 82, stage: "karaoke_instrumental", message: "Surround audio could not be processed",
    };
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    vi.mocked(fetchTrackFailures).mockResolvedValue([failure]);
    vi.mocked(connectJobsSocket).mockReturnValue({ close: vi.fn() });

    const store = createJobsStore();
    store.start();

    await vi.waitFor(() => expect(store.trackFailures[7]).toEqual(failure));
  });

  it("refreshes track failures immediately when an item fails", async () => {
    const failure: TrackProcessingFailure = {
      track_id: 7, job_id: 82, stage: "karaoke_instrumental", message: "Processing failed",
    };
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    vi.mocked(fetchTrackFailures).mockResolvedValueOnce([]).mockResolvedValueOnce([failure]);
    let capturedOnEvent: ((event: unknown) => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnEvent = opts.onEvent as (event: unknown) => void;
      return { close: vi.fn() };
    });

    const store = createJobsStore();
    store.start();
    await vi.waitFor(() => expect(fetchTrackFailures).toHaveBeenCalledTimes(1));

    capturedOnEvent?.({ type: "item", job_id: 82, item_id: 820, status: "failed" });

    await vi.waitFor(() => expect(store.trackFailures[7]).toEqual(failure));
    store.stop();
  });

  it("fetches the job list on start", async () => {
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    vi.mocked(connectJobsSocket).mockReturnValue({ close: vi.fn() });

    const store = createJobsStore();
    store.start();

    // start() -> refreshList() -> await fetchJobs() resolves over several
    // microtask hops; vi.waitFor polls until the assertion holds instead of
    // guessing how many `await Promise.resolve()` ticks are enough (a fixed
    // tick count is exactly what made this test flaky before).
    await vi.waitFor(() => expect(store.jobs).toEqual([sampleJob]));
  });

  it("re-fetches and notifies completion listeners when a job-completed event arrives", async () => {
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    let capturedOnEvent: ((event: unknown) => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnEvent = opts.onEvent as (event: unknown) => void;
      return { close: vi.fn() };
    });

    const store = createJobsStore();
    const completed = vi.fn();
    store.onJobCompleted(completed);
    store.start();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    capturedOnEvent?.({ type: "job", job_id: 1, status: "completed" });

    // applyEvent()'s `await refreshList()` -> `await fetchJobs()` chain needs
    // more microtask flushes than a couple of manual `await Promise.resolve()`
    // calls reliably provide; vi.waitFor polls (with real bounded retries)
    // until the listener has actually fired.
    await vi.waitFor(() => expect(completed).toHaveBeenCalledTimes(1));
    expect(completed).toHaveBeenCalledWith([1]);
    expect(fetchJobs).toHaveBeenCalledTimes(2);
  });

  it("does not notify completion listeners for non-completed job events", async () => {
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    let capturedOnEvent: ((event: unknown) => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnEvent = opts.onEvent as (event: unknown) => void;
      return { close: vi.fn() };
    });

    const store = createJobsStore();
    const completed = vi.fn();
    store.onJobCompleted(completed);
    store.start();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    capturedOnEvent?.({ type: "job", job_id: 1, status: "running" });
    // Wait for the observable side effect of applyEvent() finishing (the
    // refetch it always does) before asserting on the negative case -
    // otherwise this could pass only because we didn't wait long enough.
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(2));

    expect(completed).not.toHaveBeenCalled();
  });

  it("ignores library scan progress instead of refetching the jobs list for every batch", async () => {
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    let capturedOnEvent: ((event: unknown) => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnEvent = opts.onEvent as (event: unknown) => void;
      return { close: vi.fn() };
    });

    const store = createJobsStore();
    store.start();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    capturedOnEvent?.({ type: "library_scan", scan_id: 8, status: "running", tracks_found: 40 });
    await new Promise((resolve) => setTimeout(resolve, 300)); // beyond the store's 200ms refresh debounce

    expect(fetchJobs).toHaveBeenCalledTimes(1);
    store.stop();
  });

  it("publishes a fresh metadata track immediately without refetching the jobs list", async () => {
    vi.mocked(fetchJobs).mockResolvedValue([]);
    let capturedOnEvent: ((event: unknown) => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnEvent = opts.onEvent as (event: unknown) => void;
      return { close: vi.fn() };
    });
    const store = createJobsStore();
    const changed = vi.fn();
    store.onTrackChanged(changed);
    store.start();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    capturedOnEvent?.({ type: "track_updated", track_id: 7, track: updatedTrack });

    expect(changed).toHaveBeenCalledWith(updatedTrack);
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(fetchJobs).toHaveBeenCalledTimes(1);
  });

  it("stop() closes the socket handle", () => {
    const close = vi.fn();
    vi.mocked(fetchJobs).mockResolvedValue([]);
    vi.mocked(connectJobsSocket).mockReturnValue({ close });

    const store = createJobsStore();
    store.start();
    store.stop();

    expect(close).toHaveBeenCalled();
  });

  it("cancel() delegates to the API client", async () => {
    vi.mocked(cancelJob).mockResolvedValue(undefined);
    const store = createJobsStore();

    await store.cancel(42);

    expect(cancelJob).toHaveBeenCalledWith(42);
  });

  it("notifies completion listeners for a failed job event (terminal, not just completed)", async () => {
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    let capturedOnEvent: ((event: unknown) => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnEvent = opts.onEvent as (event: unknown) => void;
      return { close: vi.fn() };
    });

    const store = createJobsStore();
    const completed = vi.fn();
    store.onJobCompleted(completed);
    store.start();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    capturedOnEvent?.({ type: "job", job_id: 1, status: "failed" });

    await vi.waitFor(() => expect(completed).toHaveBeenCalledTimes(1));
  });

  it("onJobCompleted() returns an unsubscribe function that stops future notifications", async () => {
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    let capturedOnEvent: ((event: unknown) => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnEvent = opts.onEvent as (event: unknown) => void;
      return { close: vi.fn() };
    });

    const store = createJobsStore();
    const completed = vi.fn();
    const unsubscribe = store.onJobCompleted(completed);
    unsubscribe();
    store.start();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    capturedOnEvent?.({ type: "job", job_id: 1, status: "completed" });
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(2));

    expect(completed).not.toHaveBeenCalled();
  });

  it("notifies a one-job listener only for its matching terminal job", async () => {
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    let capturedOnEvent: ((event: unknown) => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnEvent = opts.onEvent as (event: unknown) => void;
      return { close: vi.fn() };
    });
    const store = createJobsStore();
    const completed = vi.fn();
    store.onJobCompletedFor(42, completed);
    store.start();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    capturedOnEvent?.({ type: "job", job_id: 7, status: "completed" });
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(2));
    expect(completed).not.toHaveBeenCalled();

    capturedOnEvent?.({ type: "job", job_id: 42, status: "failed" });
    await vi.waitFor(() => expect(completed).toHaveBeenCalledTimes(1));
  });

  it("swallows a fetchJobs rejection instead of raising an unhandled rejection, and later refreshes still work", async () => {
    vi.mocked(fetchJobs)
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValue([sampleJob]);
    let capturedOnPollFallback: (() => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnPollFallback = opts.onPollFallback;
      return { close: vi.fn() };
    });

    const store = createJobsStore();
    store.start();

    // The initial refreshList() call rejects. The store's internal swallow()
    // helper must prevent this from becoming an unhandled promise rejection
    // (which would otherwise fail this test under vitest's rejection
    // tracking) - the list simply stays empty and a later refresh retries.
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));
    expect(store.jobs).toEqual([]);

    capturedOnPollFallback?.();
    await vi.waitFor(() => expect(store.jobs).toEqual([sampleJob]));
  });

  it("detects a terminal transition during polling and notifies completion listeners", async () => {
    const runningJob: JobSummary = { ...sampleJob, status: "running", finished_at: null };
    vi.mocked(fetchJobs)
      .mockResolvedValueOnce([runningJob])
      .mockResolvedValueOnce([sampleJob]);
    let capturedOnPollFallback: (() => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnPollFallback = opts.onPollFallback;
      return { close: vi.fn() };
    });

    const store = createJobsStore();
    const completed = vi.fn();
    store.onJobCompleted(completed);
    store.start();
    await vi.waitFor(() => expect(store.jobs[0]?.status).toBe("running"));

    capturedOnPollFallback?.();

    await vi.waitFor(() => expect(completed).toHaveBeenCalledWith([1]));
    expect(store.jobs[0]?.status).toBe("completed");
  });

  it("does not announce historical completed jobs during the initial load", async () => {
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    vi.mocked(connectJobsSocket).mockReturnValue({ close: vi.fn() });
    const store = createJobsStore();
    const completed = vi.fn();
    store.onJobCompleted(completed);

    store.start();

    await vi.waitFor(() => expect(store.jobs).toEqual([sampleJob]));
    expect(completed).not.toHaveBeenCalled();
  });

  it("keeps the result of the newer refresh when an older, slower fetch resolves later (stale-response guard)", async () => {
    const firstJob: JobSummary = { ...sampleJob, id: 1 };
    const secondJob: JobSummary = { ...sampleJob, id: 2 };
    const first = deferred<JobSummary[]>();
    const second = deferred<JobSummary[]>();
    vi.mocked(fetchJobs).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    let capturedOnEvent: ((event: unknown) => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnEvent = opts.onEvent as (event: unknown) => void;
      return { close: vi.fn() };
    });

    const store = createJobsStore();
    store.start(); // triggers refreshList() call #1 (pending on `first`)
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    // Trigger a second, overlapping refresh before the first one resolves.
    capturedOnEvent?.({ type: "job", job_id: 2, status: "running" });
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(2));

    // Resolve out of order: the newer (second) request settles first...
    second.resolve([secondJob]);
    await vi.waitFor(() => expect(store.jobs).toEqual([secondJob]));

    // ...then the older (first) request finally resolves. Its stale result
    // must be discarded because a newer refresh has already completed.
    first.resolve([firstJob]);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(store.jobs).toEqual([secondJob]);
  });

  it("coalesces rapid successive events into a single trailing refresh (200ms debounce)", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
      let capturedOnEvent: ((event: unknown) => void) | undefined;
      vi.mocked(connectJobsSocket).mockImplementation((opts) => {
        capturedOnEvent = opts.onEvent as (event: unknown) => void;
        return { close: vi.fn() };
      });

      const store = createJobsStore();
      store.start();
      await vi.advanceTimersByTimeAsync(0);
      expect(fetchJobs).toHaveBeenCalledTimes(1); // initial start() refresh, not debounced

      capturedOnEvent?.({ type: "stage", job_id: 1, item_id: 10, stage: "demucs_separate", status: "running" });
      capturedOnEvent?.({ type: "stage", job_id: 1, item_id: 10, stage: "demucs_separate", status: "completed" });
      capturedOnEvent?.({ type: "item", job_id: 1, item_id: 10, status: "running", current_stage: "karaoke_instrumental" });

      await vi.advanceTimersByTimeAsync(199);
      expect(fetchJobs).toHaveBeenCalledTimes(1); // still debounced - trailing edge not reached yet

      await vi.advanceTimersByTimeAsync(2);
      expect(fetchJobs).toHaveBeenCalledTimes(2); // exactly one coalesced refresh for all three events
    } finally {
      vi.useRealTimers();
    }
  });

  it("tracks the latest stage_progress detail per job without triggering a list refresh", async () => {
    vi.mocked(fetchJobs).mockResolvedValue([sampleJob]);
    let capturedOnEvent: ((event: unknown) => void) | undefined;
    vi.mocked(connectJobsSocket).mockImplementation((opts) => {
      capturedOnEvent = opts.onEvent as (event: unknown) => void;
      return { close: vi.fn() };
    });

    const store = createJobsStore();
    store.start();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    capturedOnEvent?.({
      type: "stage_progress", job_id: 1, item_id: 10, stage: "demucs_separate", detail: "loading htdemucs",
    });

    await vi.waitFor(() => expect(store.stageDetails[1]).toBe("loading htdemucs"));
    // stage_progress must not trigger the debounced list refresh - only
    // job/item/stage events do (see the existing debounce test above).
    expect(fetchJobs).toHaveBeenCalledTimes(1);
  });
});
