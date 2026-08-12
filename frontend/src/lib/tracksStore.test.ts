import { afterEach, describe, expect, it, vi } from "vitest";
import type { JobDetail, JobSummary, LibraryScanStatus, Track } from "./types";

vi.mock("./api", () => ({
  fetchJobs: vi.fn(),
  fetchJob: vi.fn(),
  cancelJob: vi.fn(),
  fetchTracks: vi.fn(),
  reconcileTrackLyrics: vi.fn(),
  fetchRescanStatus: vi.fn(),
  rescan: vi.fn(),
}));
vi.mock("./jobsSocket", () => ({
  connectJobsSocket: vi.fn(),
}));

import { fetchJob, fetchJobs, fetchRescanStatus, fetchTracks, reconcileTrackLyrics, rescan } from "./api";
import { connectJobsSocket } from "./jobsSocket";
import { jobsStore } from "./jobsStore.svelte";
import { tracksStore } from "./tracksStore.svelte";

afterEach(() => {
  jobsStore.stop();
  tracksStore.stopRescanMonitoring();
  vi.clearAllMocks();
});

const sampleTrack: Track = {
  id: 1,
  media_root: "D:/Media",
  relative_path: "ABBA/Dancing Queen.flac",
  artist: "ABBA",
  title: "Dancing Queen",
  outputs: {
    instrumental: false, vocals: false, lead_vocals: false, backing_vocals: false,
    drums: false, bass: false, guitar: false, piano: false, other: false, lrc: false,
  },
  lrc_state: null,
  stem_count: 0,
};

function scanStatus(overrides: Partial<LibraryScanStatus> = {}): LibraryScanStatus {
  return {
    scan_id: 1,
    status: "completed" as const,
    tracks_found: 1,
    media_roots_scanned: 1,
    media_roots_total: 1,
    current_root: null,
    unavailable_roots: [],
    tracks_purged: 0,
    error: null,
    updated_at: "2026-08-05T00:00:00Z",
    ...overrides,
  };
}

// Deferred promise helper for exercising out-of-order resolution.
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

// Wires up jobsStore's socket mock and returns a function to fire a JobEvent
// through it, mirroring the pattern already used in jobsStore.test.ts.
function startJobsStoreAndCaptureOnEvent(): (event: unknown) => void {
  let captured: ((event: unknown) => void) | undefined;
  vi.mocked(connectJobsSocket).mockImplementation((opts) => {
    captured = opts.onEvent as (event: unknown) => void;
    return { close: vi.fn() };
  });
  jobsStore.start();
  return (event: unknown) => captured?.(event);
}

describe("tracksStore", () => {
  it("refetches database rows without a filesystem rescan when a processing job completes", async () => {
    vi.mocked(fetchTracks).mockResolvedValue([sampleTrack]);
    vi.mocked(rescan).mockResolvedValue(scanStatus());
    vi.mocked(fetchJobs).mockResolvedValue([]);

    const fireEvent = startJobsStoreAndCaptureOnEvent();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    const fetchTracksCallsBefore = vi.mocked(fetchTracks).mock.calls.length;
    const rescanCallsBefore = vi.mocked(rescan).mock.calls.length;

    fireEvent({ type: "job", job_id: 1, status: "completed" });

    await vi.waitFor(() =>
      expect(vi.mocked(fetchTracks).mock.calls.length).toBe(fetchTracksCallsBefore + 1),
    );
    expect(vi.mocked(rescan).mock.calls.length).toBe(rescanCallsBefore);
  });

  it("does not rescan or refetch tracks for unrelated events (progress ticks, queued->running)", async () => {
    vi.mocked(fetchTracks).mockResolvedValue([sampleTrack]);
    vi.mocked(rescan).mockResolvedValue(scanStatus({ tracks_found: 0 }));
    vi.mocked(fetchJobs).mockResolvedValue([]);

    const fireEvent = startJobsStoreAndCaptureOnEvent();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));

    fireEvent({
      type: "stage_progress", job_id: 1, item_id: 10, stage: "demucs_separate", detail: "loading htdemucs",
    });
    fireEvent({ type: "job", job_id: 1, status: "queued" });
    fireEvent({ type: "job", job_id: 1, status: "running" });
    fireEvent({ type: "item", job_id: 1, item_id: 10, status: "running", current_stage: "demucs_separate" });

    // Give applyEvent's debounced refreshList() (fetchJobs) a chance to fire
    // so we know these events were actually processed, not just still
    // pending - a genuine negative assertion needs to wait for the
    // observable side effect these DO cause before trusting the ones they
    // must NOT cause.
    await vi.waitFor(() => expect(vi.mocked(fetchJobs).mock.calls.length).toBeGreaterThan(1));

    expect(fetchTracks).not.toHaveBeenCalled();
    expect(rescan).not.toHaveBeenCalled();
  });

  it("replaces and re-sorts each row as a metadata batch item completes", async () => {
    const second = { ...sampleTrack, id: 2, artist: "ZZ Top", title: "Legs" };
    vi.mocked(fetchTracks).mockResolvedValue([sampleTrack, second]);
    vi.mocked(fetchJobs).mockResolvedValue([]);
    await tracksStore.refresh("");
    const revisionBefore = tracksStore.revisionFor(second.id);
    const fireEvent = startJobsStoreAndCaptureOnEvent();
    await vi.waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(1));
    const updated = { ...second, artist: "A-ha", album: "Hunting High and Low", year: 1985 };

    fireEvent({ type: "track_updated", track_id: second.id, track: updated });

    expect(tracksStore.tracks).toEqual([updated, sampleTrack]);
    expect(tracksStore.revisionFor(second.id)).toBe(revisionBefore + 1);
  });

  it("does a final list reconciliation without a rescan when a metadata job finishes", async () => {
    const running: JobSummary = {
      id: 42, recipe: "fetch_tags", options: {}, status: "running",
      created_at: "t0", started_at: "t1", finished_at: null,
      item_counts: { queued: 0, running: 1, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
    };
    const completed: JobSummary = {
      ...running, status: "completed", finished_at: "t2",
      item_counts: { queued: 0, running: 0, completed: 1, failed: 0, skipped: 0, cancelled: 0 },
    };
    vi.mocked(fetchTracks).mockResolvedValue([sampleTrack]);
    vi.mocked(fetchJobs).mockResolvedValueOnce([running]).mockResolvedValue([completed]);
    vi.mocked(fetchJob).mockResolvedValue({
      ...completed,
      items: [{
        id: 9, track_id: sampleTrack.id, source_path: "D:/Media/ABBA/Dancing Queen.flac",
        status: "completed", current_stage: null, stages: [], error_text: null,
      }],
    } satisfies JobDetail);
    const fireEvent = startJobsStoreAndCaptureOnEvent();
    await vi.waitFor(() => expect(jobsStore.jobs[0]?.status).toBe("running"));
    const fetchBefore = vi.mocked(fetchTracks).mock.calls.length;
    const rescanBefore = vi.mocked(rescan).mock.calls.length;
    const revisionBefore = tracksStore.revisionFor(sampleTrack.id);

    fireEvent({ type: "job", job_id: 42, status: "completed" });

    await vi.waitFor(() => expect(vi.mocked(fetchTracks).mock.calls.length).toBe(fetchBefore + 1));
    await vi.waitFor(() => expect(tracksStore.revisionFor(sampleTrack.id)).toBe(revisionBefore + 1));
    expect(fetchJob).toHaveBeenCalledWith(42);
    expect(vi.mocked(rescan).mock.calls.length).toBe(rescanBefore);
  });

  it("keeps the result of the newer refresh when an older, slower fetch resolves later", async () => {
    const trackA: Track = { ...sampleTrack, id: 1 };
    const trackB: Track = { ...sampleTrack, id: 2 };
    const first = deferred<Track[]>();
    const second = deferred<Track[]>();
    vi.mocked(fetchTracks).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    const p1 = tracksStore.refresh("a");
    const p2 = tracksStore.refresh("b");

    second.resolve([trackB]);
    await p2;
    expect(tracksStore.tracks).toEqual([trackB]);

    first.resolve([trackA]);
    await p1;
    // The stale (first, older) response must not clobber the newer result.
    expect(tracksStore.tracks).toEqual([trackB]);
  });

  it("reuses the last explicit query on a no-argument refresh (completion-triggered refetch)", async () => {
    vi.mocked(fetchTracks).mockResolvedValue([sampleTrack]);

    await tracksStore.refresh("dancing queen");
    await tracksStore.refresh();

    expect(vi.mocked(fetchTracks).mock.calls.at(-1)).toEqual(["dancing queen"]);
  });

  it("refreshes incrementally as a background scan publishes new batches", async () => {
    vi.mocked(fetchTracks).mockResolvedValue([sampleTrack]);
    vi.mocked(rescan).mockResolvedValue(scanStatus({ scan_id: 99, status: "running", tracks_found: 0, media_roots_scanned: 0 }));
    vi.mocked(fetchRescanStatus).mockResolvedValue(scanStatus({ scan_id: 99, tracks_found: 40 }));

    const refreshCallsBefore = vi.mocked(fetchTracks).mock.calls.length;
    await tracksStore.startRescan();

    await vi.waitFor(() => expect(fetchRescanStatus).toHaveBeenCalled());
    await vi.waitFor(() => expect(fetchTracks).toHaveBeenCalledTimes(refreshCallsBefore + 1));
    expect(tracksStore.scanStatus?.status).toBe("completed");
    expect(tracksStore.scanStatus?.tracks_found).toBe(40);
  });

  it("replaces one track in place after a tag edit", async () => {
    vi.mocked(fetchTracks).mockResolvedValue([sampleTrack]);
    await tracksStore.refresh("");
    const updated = { ...sampleTrack, title: "Updated title" };

    const revisionBefore = tracksStore.revisionFor(updated.id);
    tracksStore.replaceTrack(updated);

    expect(tracksStore.tracks).toEqual([updated]);
    expect(tracksStore.revisionFor(updated.id)).toBe(revisionBefore + 1);
    expect(fetchTracks).toHaveBeenCalledTimes(1);
  });

  it("replaces only rows whose live lyric state changed", async () => {
    const stale = { ...sampleTrack, outputs: { ...sampleTrack.outputs, lrc: true }, lrc_state: "line_timed" as const };
    const enhanced = { ...stale, lrc_state: "enhanced" as const };
    vi.mocked(fetchTracks).mockResolvedValue([stale]);
    vi.mocked(reconcileTrackLyrics).mockResolvedValue([enhanced]);
    await tracksStore.refresh("");

    await tracksStore.reconcileLrcStates([stale.id, stale.id]);

    expect(reconcileTrackLyrics).toHaveBeenCalledWith([stale.id]);
    expect(tracksStore.tracks).toEqual([enhanced]);
  });

  it("marks only changed tracks for artwork and row refresh after a refetch", async () => {
    const unchanged = { ...sampleTrack, id: 2, title: "Unchanged" };
    vi.mocked(fetchTracks).mockResolvedValueOnce([sampleTrack, unchanged]);
    await tracksStore.refresh("");
    const changedRevision = tracksStore.revisionFor(sampleTrack.id);
    const unchangedRevision = tracksStore.revisionFor(unchanged.id);

    vi.mocked(fetchTracks).mockResolvedValueOnce([
      { ...sampleTrack, outputs: { ...sampleTrack.outputs, instrumental: true }, stem_count: 1 },
      unchanged,
    ]);
    await tracksStore.refresh();

    expect(tracksStore.revisionFor(sampleTrack.id)).toBe(changedRevision + 1);
    expect(tracksStore.revisionFor(unchanged.id)).toBe(unchangedRevision);
  });

  it("marks a row changed when reprocessing only changes instrumental quality", async () => {
    const balanced: Track = {
      ...sampleTrack,
      outputs: { ...sampleTrack.outputs, instrumental: true },
      instrumental_provenance: {
        schema_version: 1,
        part: "instrumental",
        quality: "balanced",
        engine: "demucs",
        engine_version: null,
        model: "htdemucs",
        models: ["htdemucs"],
        backing_vocal_mode: "stripped",
        device: "cuda",
        job_id: 106,
        stage: "karaoke_instrumental",
        attribution: "confirmed",
        recorded_at: "2026-08-12T01:00:00Z",
      },
    };
    const highQuality: Track = {
      ...balanced,
      instrumental_provenance: {
        ...balanced.instrumental_provenance!,
        quality: "high_quality",
        model: "htdemucs_ft",
        models: ["htdemucs_ft"],
        backing_vocal_mode: "best",
        job_id: 108,
        recorded_at: "2026-08-12T02:00:00Z",
      },
    };
    vi.mocked(fetchTracks).mockResolvedValueOnce([balanced]);
    await tracksStore.refresh("");
    const revisionBefore = tracksStore.revisionFor(balanced.id);

    vi.mocked(fetchTracks).mockResolvedValueOnce([highQuality]);
    await tracksStore.refresh();

    expect(tracksStore.tracks[0]?.instrumental_provenance?.quality).toBe("high_quality");
    expect(tracksStore.revisionFor(balanced.id)).toBe(revisionBefore + 1);
  });
});
