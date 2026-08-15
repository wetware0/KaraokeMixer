import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Library from "./Library.svelte";
import type { Track } from "../types";
import { jobsStore } from "../jobsStore.svelte";
import { LIBRARY_COLUMNS_STORAGE_KEY } from "../libraryColumns";
import { tracksStore } from "../tracksStore.svelte";

vi.mock("../jobsSocket", () => ({
  connectJobsSocket: vi.fn(() => ({ close: vi.fn() })),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    artworkUrl: vi.fn((id: number) => `/api/tracks/${id}/artwork`),
    saveTrackTags: vi.fn(),
    uploadTrackArtwork: vi.fn(),
  };
});

// tracksStore (see tracksStore.svelte.ts) is a module-level singleton by
// design - that's the whole point of the Bug 1 fix (it must keep refreshing
// in the background across Library mount/unmount cycles). But that means it
// also persists across *tests* in this file, unlike the old per-instance
// `let tracks = $state([])` this replaced. Without resetting it, a later
// test's first render can transiently show a previous test's leftover track
// list before its own fetch resolves - and if that stale data happens to
// contain matching text (e.g. both fixtures include "Dancing Queen"), a
// `waitFor` can pass against the wrong data entirely. Force it back to an
// empty, query-less state before every test, using only tracksStore's public
// refresh() - the test's own fetch stub is set up after this, inside the
// test body, so it never sees this reset call.
beforeEach(async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => Promise.resolve(
      url.toString() === "/api/rescan"
        ? jsonResponse(idleScanStatus())
        : jsonResponse({ tracks: [] }),
    )),
  );
  await Promise.all([tracksStore.refresh(""), tracksStore.resumeRescan()]);
  vi.unstubAllGlobals();
});

// Minimal stand-in for HTMLAudioElement - jsdom's real implementation logs
// "Not implemented" console errors for play()/pause(), so preview tests stub
// the global constructor entirely, in the same vi.stubGlobal style used for
// `fetch` above and for `WebSocket` in jobsSocket.test.ts.
class FakeAudio {
  static instances: FakeAudio[] = [];
  // Controls what play() resolves/rejects with for every instance created
  // after it's set - tests that need a rejecting play() set this before
  // clicking the preview button, matching how a real play() promise's
  // outcome isn't known until called.
  static nextPlayResult: () => Promise<void> = () => Promise.resolve();

  src: string;
  onended: (() => void) | null = null;
  play = vi.fn(() => FakeAudio.nextPlayResult());
  pause = vi.fn();
  load = vi.fn();

  constructor(src: string) {
    this.src = src;
    FakeAudio.instances.push(this);
  }

  // Real HTMLMediaElement only stops fetching the underlying stream once
  // both the src is cleared and load() is called - mirrored here so tests
  // can assert stopPreview() actually releases the stream, not just pauses.
  removeAttribute(name: string): void {
    if (name === "src") this.src = "";
  }
}

afterEach(() => {
  tracksStore.stopRescanMonitoring();
  vi.unstubAllGlobals();
  FakeAudio.instances = [];
  FakeAudio.nextPlayResult = () => Promise.resolve();
});

// Minimal in-memory Storage stand-in for localStorage - libraryColumns.ts
// reads/writes window.localStorage directly, so column-state tests stub the
// global the same way FakeAudio stubs the global Audio constructor above.
class FakeStorage implements Storage {
  private data = new Map<string, string>();
  get length() { return this.data.size; }
  clear(): void { this.data.clear(); }
  getItem(key: string): string | null { return this.data.get(key) ?? null; }
  key(index: number): string | null { return [...this.data.keys()][index] ?? null; }
  removeItem(key: string): void { this.data.delete(key); }
  setItem(key: string, value: string): void { this.data.set(key, value); }
}

let fakeStorage: FakeStorage;

beforeEach(() => {
  fakeStorage = new FakeStorage();
  vi.stubGlobal("localStorage", fakeStorage);
});

const sampleTracks: Track[] = [
  {
    id: 1,
    media_root: "D:/Media",
    relative_path: "ABBA/Dancing Queen.flac",
    artist: "ABBA",
    title: "Dancing Queen",
    outputs: {
      instrumental: true,
      vocals: false,
      lead_vocals: false,
      backing_vocals: false,
      drums: false,
      bass: false,
      guitar: false,
      piano: false,
      other: false,
      lrc: true,
    },
    lrc_state: "enhanced",
    stem_count: 1,
    album: "Arrival",
    year: 1976,
    duration_seconds: 213,
  },
];

function stubFetchTracks(tracks: Track[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ tracks }) })
  );
}

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

function completedScanStatus(tracksFound = 1, unavailableRoots: string[] = []) {
  return {
    scan_id: 1, status: "completed", tracks_found: tracksFound, media_roots_scanned: 1, media_roots_total: 1,
    current_root: null, unavailable_roots: unavailableRoots, tracks_purged: 0, error: null,
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function idleScanStatus() {
  return {
    scan_id: 0, status: "idle", tracks_found: 0, media_roots_scanned: 0, media_roots_total: 0,
    current_root: null, unavailable_roots: [], tracks_purged: 0, error: null,
    updated_at: "2026-08-05T00:00:00Z",
  };
}

describe("Library", () => {
  it("confirms a row deletion, stops its preview, and removes it immediately", async () => {
    stubFetchTracks(sampleTracks);
    vi.stubGlobal("Audio", FakeAudio);
    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Play preview"));
    await fireEvent.click(screen.getByRole("button", { name: "Delete…" }));
    expect(FakeAudio.instances[0].pause).toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Move track to Recycle Bin?" })).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "Move to Recycle Bin" }));
    await waitFor(() => expect(screen.queryByText("Dancing Queen")).toBeNull());
  });

  it("rescans and then refreshes the visible library while the app is running", async () => {
    const refreshed = [{ ...sampleTracks[0], id: 2, title: "Newly Added" }];
    let rescanned = false;
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      calls.push(url.toString());
      if (url.toString() === "/api/rescan") {
        rescanned = true;
        return Promise.resolve(jsonResponse(completedScanStatus()));
      }
      return Promise.resolve(jsonResponse({ tracks: rescanned ? refreshed : sampleTracks }));
    }));

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());
    await fireEvent.click(screen.getByText("Rescan library"));

    await waitFor(() => expect(screen.getByText("Newly Added")).toBeTruthy());
    expect(screen.getByText(/Found 1 tracks across 1 media root/)).toBeTruthy();
    const rescanIndex = calls.indexOf("/api/rescan");
    expect(calls.indexOf("/api/tracks", rescanIndex)).toBeGreaterThan(rescanIndex);
  });

  it("shows background progress and publishes newly scanned tracks before the scan finishes", async () => {
    const newlyFound = [{ ...sampleTracks[0], id: 22, title: "Found Mid Scan" }];
    let statusPolls = 0;
    const runningAt = (tracksFound: number) => ({
      ...completedScanStatus(tracksFound), status: "running", media_roots_scanned: 0, current_root: "D:/Large Library",
    });
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string, options?: RequestInit) => {
      const path = url.toString();
      if (path === "/api/rescan" && options?.method === "POST") {
        return Promise.resolve(jsonResponse(runningAt(0)));
      }
      if (path === "/api/rescan") {
        statusPolls += 1;
        return Promise.resolve(jsonResponse(statusPolls === 1 ? runningAt(40) : completedScanStatus(40)));
      }
      return Promise.resolve(jsonResponse({ tracks: statusPolls > 0 ? newlyFound : sampleTracks }));
    }));

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());
    await fireEvent.click(screen.getByText("Rescan library"));

    await waitFor(() => expect(screen.getByText("Scanning in background…")).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/40 tracks found so far/)).toBeTruthy(), { timeout: 2000 });
    await waitFor(() => expect(screen.getByText("Found Mid Scan")).toBeTruthy());
  });

  it("loads and displays tracks on mount", async () => {
    stubFetchTracks(sampleTracks);

    render(Library);

    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());
    expect(screen.getAllByText("ABBA").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Ready")).toBeTruthy();
  });

  it("virtualizes a very large library instead of mounting every track row", async () => {
    const largeLibrary = Array.from({ length: 80_000 }, (_, index): Track => ({
      ...sampleTracks[0],
      id: index + 1,
      relative_path: `Artist ${Math.floor(index / 20)}/Track ${index}.flac`,
      artist: `Artist ${Math.floor(index / 20)}`,
      title: `Track ${index}`,
    }));
    stubFetchTracks(largeLibrary);
    const { container } = render(Library);

    await waitFor(() => expect(screen.getByText("80000 tracks ready to review")).toBeTruthy(), { timeout: 10_000 });

    const mountedRows = container.querySelectorAll("tbody .track-row");
    expect(mountedRows.length).toBeGreaterThan(0);
    expect(mountedRows.length).toBeLessThan(40);
    expect(container.querySelector(".library-table")?.getAttribute("aria-rowcount")).toBe("80001");
    expect(container.querySelectorAll(".library-virtual-spacer").length).toBeGreaterThan(0);

    const scrollContainer = container.querySelector(".library-table-scroll") as HTMLDivElement;
    scrollContainer.scrollTop = 58_000;
    await fireEvent.scroll(scrollContainer);
    await waitFor(() => {
      const firstRendered = container.querySelector("tbody .track-row");
      expect(Number(firstRendered?.getAttribute("aria-rowindex"))).toBeGreaterThan(900);
    });
    expect(container.querySelectorAll("tbody .track-row").length).toBeLessThan(40);
  }, 15_000);

  it("highlights queued and running track items and clears them when jobs finish", async () => {
    const tracks: Track[] = [
      sampleTracks[0],
      { ...sampleTracks[0], id: 2, title: "Waterloo", relative_path: "ABBA/Waterloo.flac" },
    ];
    let active = true;
    const counts = (queued: number, running: number, completed = 0) => ({
      queued, running, completed, failed: 0, skipped: 0, cancelled: 0,
    });
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const path = url.toString();
      if (path === "/api/jobs") {
        return Promise.resolve(jsonResponse({ jobs: active ? [
          {
            id: 21, recipe: "lyrics_only", options: {}, status: "queued",
            created_at: "now", started_at: null, finished_at: null, item_counts: counts(1, 0),
          },
          {
            id: 22, recipe: "karaoke", options: {}, status: "running",
            created_at: "now", started_at: "now", finished_at: null, item_counts: counts(0, 1),
          },
        ] : [] }));
      }
      if (path === "/api/jobs/21") {
        return Promise.resolve(jsonResponse({
          id: 21, recipe: "lyrics_only", options: {}, status: "queued",
          created_at: "now", started_at: null, finished_at: null,
          items: [{ id: 210, track_id: 1, source_path: "Dancing Queen.flac", status: "queued", current_stage: null, stages: [], error_text: null }],
        }));
      }
      if (path === "/api/jobs/22") {
        return Promise.resolve(jsonResponse({
          id: 22, recipe: "karaoke", options: {}, status: "running",
          created_at: "now", started_at: "now", finished_at: null,
          items: [{ id: 220, track_id: 2, source_path: "Waterloo.flac", status: "running", current_stage: "demucs_separate", stages: [], error_text: null }],
        }));
      }
      if (path.startsWith("/api/rescan")) return Promise.resolve(jsonResponse(completedScanStatus()));
      return Promise.resolve(jsonResponse({ tracks }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(Library);
    jobsStore.start();

    await waitFor(() => expect(screen.getByLabelText("Select Dancing Queen")).toBeTruthy());
    const queuedRow = screen.getByLabelText("Select Dancing Queen").closest("tr")!;
    const runningRow = screen.getByLabelText("Select Waterloo").closest("tr")!;
    await waitFor(() => expect(queuedRow.classList.contains("track-row-queued")).toBe(true));
    await waitFor(() => expect(runningRow.classList.contains("track-row-running")).toBe(true));
    expect(screen.getByText("Queued")).toBeTruthy();
    expect(screen.getByText("Processing")).toBeTruthy();

    active = false;
    const { connectJobsSocket } = await import("../jobsSocket");
    const onEvent = vi.mocked(connectJobsSocket).mock.calls[vi.mocked(connectJobsSocket).mock.calls.length - 1][0].onEvent;
    onEvent({ type: "job", job_id: 22, status: "completed" });

    await waitFor(() => expect(runningRow.classList.contains("track-row-running")).toBe(false));
    expect(queuedRow.classList.contains("track-row-queued")).toBe(false);
    expect(screen.queryByText("Queued")).toBeNull();
    expect(screen.queryByText("Processing")).toBeNull();

    jobsStore.stop();
  });

  it("keeps a resolved phase row highlighted as waiting while later rows remain queued", async () => {
    const tracks: Track[] = [
      sampleTracks[0],
      { ...sampleTracks[0], id: 2, title: "Waterloo", relative_path: "ABBA/Waterloo.flac" },
      { ...sampleTracks[0], id: 3, title: "Fernando", relative_path: "ABBA/Fernando.flac" },
    ];
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      const path = url.toString();
      if (path === "/api/jobs") {
        return Promise.resolve(jsonResponse({ jobs: [{
          id: 23, recipe: "karaoke", options: {}, status: "running",
          created_at: "now", started_at: "now", finished_at: null,
          item_counts: { queued: 2, running: 1, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
        }] }));
      }
      if (path === "/api/jobs/23") {
        const stage = (status: "pending" | "running" | "completed") => ({
          name: "demucs_separate", status, started_at: status === "pending" ? null : "now",
          finished_at: status === "completed" ? "later" : null, error: null,
        });
        return Promise.resolve(jsonResponse({
          id: 23, recipe: "karaoke", options: {}, status: "running",
          created_at: "now", started_at: "now", finished_at: null,
          items: [
            { id: 230, track_id: 1, source_path: "Dancing Queen.flac", status: "queued", current_stage: null, stages: [stage("completed"), { name: "karaoke_instrumental", status: "pending", started_at: null, finished_at: null, error: null }], error_text: null },
            { id: 231, track_id: 2, source_path: "Waterloo.flac", status: "running", current_stage: "demucs_separate", stages: [stage("running"), { name: "karaoke_instrumental", status: "pending", started_at: null, finished_at: null, error: null }], error_text: null },
            { id: 232, track_id: 3, source_path: "Fernando.flac", status: "queued", current_stage: null, stages: [stage("pending"), { name: "karaoke_instrumental", status: "pending", started_at: null, finished_at: null, error: null }], error_text: null },
          ],
        }));
      }
      if (path.startsWith("/api/rescan")) return Promise.resolve(jsonResponse(completedScanStatus()));
      return Promise.resolve(jsonResponse({ tracks }));
    }));

    render(Library);
    jobsStore.start();

    await waitFor(() => expect(screen.getByLabelText("Select Dancing Queen")).toBeTruthy());
    const finishedRow = screen.getByLabelText("Select Dancing Queen").closest("tr")!;
    const runningRow = screen.getByLabelText("Select Waterloo").closest("tr")!;
    const queuedRow = screen.getByLabelText("Select Fernando").closest("tr")!;
    await waitFor(() => expect(runningRow.classList.contains("track-row-running")).toBe(true));
    expect(finishedRow.classList.contains("track-row-waiting")).toBe(true);
    expect(finishedRow.classList.contains("track-row-queued")).toBe(false);
    expect(finishedRow.classList.contains("track-row-running")).toBe(false);
    expect(queuedRow.classList.contains("track-row-queued")).toBe(true);
    expect(screen.getByText("Waiting for next phase")).toBeTruthy();

    jobsStore.stop();
  });

  it("keeps an unresolved processing error visible on the failed track row", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      const path = url.toString();
      if (path === "/api/jobs") return Promise.resolve(jsonResponse({ jobs: [] }));
      if (path === "/api/jobs/track-failures") {
        return Promise.resolve(jsonResponse({ failures: [{
          track_id: 1,
          job_id: 82,
          stage: "karaoke_instrumental",
          message: "Surround audio could not be processed by the stereo separation model",
        }] }));
      }
      if (path.startsWith("/api/rescan")) return Promise.resolve(jsonResponse(completedScanStatus()));
      return Promise.resolve(jsonResponse({ tracks: sampleTracks }));
    }));

    render(Library);
    jobsStore.start();

    await waitFor(() => expect(screen.getByLabelText(/Processing error: Creating karaoke instrumental/)).toBeTruthy());
    const failedRow = screen.getByLabelText("Select Dancing Queen").closest("tr")!;
    expect(failedRow.classList.contains("track-row-failed")).toBe(true);

    jobsStore.stop();
  });

  it("re-fetches tracks with the search query on input", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, status: 200, json: async () => ({ tracks: sampleTracks }) });
    vi.stubGlobal("fetch", fetchMock);

    render(Library);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/tracks"));

    const input = screen.getByPlaceholderText("Search tracks...") as HTMLInputElement;
    input.value = "queen";
    input.dispatchEvent(new Event("input", { bubbles: true }));

    // Search is debounced by 200ms; wait past that before expecting the fetch.
    await new Promise((resolve) => setTimeout(resolve, 250));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/tracks?query=queen")
    );
  });

  it("filters tracks to the selected folder, including Windows-style paths", async () => {
    const winTracks: Track[] = [
      { ...sampleTracks[0], id: 1, media_root: "D:\\Media", relative_path: "ABBA\\Dancing Queen.flac" },
      {
        ...sampleTracks[0],
        id: 2,
        media_root: "D:\\Media",
        relative_path: "Queen\\Bohemian Rhapsody.flac",
        artist: "Queen",
        title: "Bohemian Rhapsody",
      },
    ];
    stubFetchTracks(winTracks);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByRole("button", { name: "Expand D:/Media" }));

    const abbaFolderButton = screen
      .getAllByText("ABBA")
      .find((el) => el.tagName === "BUTTON") as HTMLElement;
    abbaFolderButton.click();

    await waitFor(() => expect(screen.queryByText("Bohemian Rhapsody")).toBeNull());
    expect(screen.getByText("Dancing Queen")).toBeTruthy();
  });

  it("collapses and expands individual folder branches without changing the selected folder", async () => {
    const nestedTracks: Track[] = [
      { ...sampleTracks[0], id: 1, relative_path: "ABBA/Arrival/Dancing Queen.flac" },
      {
        ...sampleTracks[0],
        id: 2,
        relative_path: "Queen/Greatest Hits/Somebody to Love.flac",
        artist: "Queen",
        title: "Somebody to Love",
      },
    ];
    stubFetchTracks(nestedTracks);

    render(Library);
    await waitFor(() => expect(screen.getByRole("button", { name: "Expand D:/Media" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Arrival" })).toBeNull();

    await fireEvent.click(screen.getByRole("button", { name: "Expand D:/Media" }));
    await fireEvent.click(screen.getByRole("button", { name: "Expand ABBA" }));
    expect(screen.getByRole("button", { name: "Arrival" })).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "ABBA" }));
    expect(screen.queryByText("Somebody to Love")).toBeNull();

    await fireEvent.click(screen.getByRole("button", { name: "Collapse ABBA" }));
    expect(screen.queryByRole("button", { name: "Arrival" })).toBeNull();
    expect(screen.getByRole("button", { name: "ABBA" }).classList.contains("selected")).toBe(true);
    expect(screen.getByText("Dancing Queen")).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "Expand ABBA" }));
    expect(screen.getByRole("button", { name: "Arrival" })).toBeTruthy();
  });

  it("collapses and expands all folder branches from the folder heading", async () => {
    const nestedTracks: Track[] = [
      { ...sampleTracks[0], id: 1, relative_path: "ABBA/Arrival/Dancing Queen.flac" },
      {
        ...sampleTracks[0],
        id: 2,
        relative_path: "Queen/Greatest Hits/Somebody to Love.flac",
        title: "Somebody to Love",
      },
    ];
    stubFetchTracks(nestedTracks);

    render(Library);
    await waitFor(() => expect(screen.getByRole("button", { name: "Expand all" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "ABBA" })).toBeNull();
    expect(screen.getByLabelText("Folder list")).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getByRole("button", { name: "ABBA" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Arrival" })).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.queryByRole("button", { name: "ABBA" })).toBeNull();
    expect(screen.getByRole("button", { name: "Expand all" })).toBeTruthy();
  });

  it("expands a collapsed folder on drag hover and moves the dropped track", async () => {
    const nestedTracks: Track[] = [
      { ...sampleTracks[0], id: 1, relative_path: "ABBA/Arrival/Dancing Queen.flac" },
      {
        ...sampleTracks[0], id: 2, relative_path: "Queen/Greatest Hits/Somebody to Love.flac",
        artist: "Queen", title: "Somebody to Love",
      },
    ];
    const moved = { ...nestedTracks[0], media_root: "D:/Media", relative_path: "Queen/Dancing Queen.flac" };
    const folders = [
      { path: "D:/Media", media_root: "D:/Media", relative_path: "", name: "D:/Media" },
      { path: "D:/Media/ABBA", media_root: "D:/Media", relative_path: "ABBA", name: "ABBA" },
      { path: "D:/Media/ABBA/Arrival", media_root: "D:/Media", relative_path: "ABBA/Arrival", name: "Arrival" },
      { path: "D:/Media/Queen", media_root: "D:/Media", relative_path: "Queen", name: "Queen" },
      { path: "D:/Media/Queen/Greatest Hits", media_root: "D:/Media", relative_path: "Queen/Greatest Hits", name: "Greatest Hits" },
    ];
    const fetchMock = vi.fn().mockImplementation((url: string, options?: RequestInit) => {
      const path = url.toString();
      if (path === "/api/folders") return Promise.resolve(jsonResponse({ folders }));
      if (path === "/api/tracks/1/location" && options?.method === "PUT") {
        return Promise.resolve(jsonResponse({ track: moved }));
      }
      if (path.startsWith("/api/rescan")) return Promise.resolve(jsonResponse(idleScanStatus()));
      return Promise.resolve(jsonResponse({ tracks: nestedTracks }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Queen" })).toBeNull();

    const row = screen.getByText("Dancing Queen").closest("tr")!;
    const transfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" };
    await fireEvent.dragStart(row, { dataTransfer: transfer });
    await fireEvent.dragOver(screen.getByRole("group", { name: "D:/Media" }), { dataTransfer: transfer });

    await waitFor(() => expect(screen.getByRole("button", { name: "Queen" })).toBeTruthy(), { timeout: 1200 });
    await fireEvent.drop(screen.getByRole("group", { name: "D:/Media/Queen" }), { dataTransfer: transfer });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/tracks/1/location",
      expect.objectContaining({ method: "PUT" }),
    ));
    const moveCall = fetchMock.mock.calls.find((call) => call[0] === "/api/tracks/1/location")!;
    expect(JSON.parse(moveCall[1].body)).toEqual({ destination_folder: "D:/Media/Queen", filename_stem: null });
  });

  it("clears the selection when Clear is clicked", async () => {
    stubFetchTracks(sampleTracks);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Select Dancing Queen"));
    expect((screen.getByText(/^Prepare selected/) as HTMLButtonElement).disabled).toBe(false);

    await fireEvent.click(screen.getByText("Clear"));

    expect((screen.getByText(/^Prepare selected/) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Prepare selected (0)")).toBeTruthy();
    expect(screen.queryByText("Clear")).toBeNull();
  });

  it("refetches tracks when a job completes", async () => {
    // A single blanket mockResolvedValue would also answer Library's own
    // /api/tracks refetch once we start reassigning it below for /api/jobs,
    // making fetchTracks() resolve to `undefined` and crash buildFolderTree.
    // Discriminate by URL instead so /api/tracks always returns sampleTracks
    // and /api/jobs always returns an empty job list, no matter which fires
    // first or how many times either is called.
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const path = url.toString();
      if (path.startsWith("/api/jobs")) return Promise.resolve(jsonResponse({ jobs: [] }));
      if (path.startsWith("/api/rescan")) return Promise.resolve(jsonResponse(completedScanStatus()));
      return Promise.resolve(jsonResponse({ tracks: sampleTracks }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(Library);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/tracks"));
    const tracksCallsBefore = fetchMock.mock.calls.filter((call) => call[0] === "/api/tracks").length;

    // jobsStore.start() (called from App.svelte in real usage) is what wires
    // the socket up; this test drives the store directly through its public
    // onJobCompleted callback instead, which Library.svelte already subscribes
    // to on mount - proving the wiring without needing a real WebSocket.
    jobsStore.start();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/jobs"));

    const { connectJobsSocket } = await import("../jobsSocket");
    const onEvent = vi.mocked(connectJobsSocket).mock.calls[0][0].onEvent;
    onEvent({ type: "job", job_id: 1, status: "completed" });

    await waitFor(() => {
      const tracksCallsAfter = fetchMock.mock.calls.filter((call) => call[0] === "/api/tracks").length;
      expect(tracksCallsAfter).toBeGreaterThan(tracksCallsBefore);
    });

    jobsStore.stop();
  });

  it("preserves the current search query when refetching after a job completes", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const path = url.toString();
      if (path.startsWith("/api/jobs")) return Promise.resolve(jsonResponse({ jobs: [] }));
      if (path.startsWith("/api/rescan")) return Promise.resolve(jsonResponse(completedScanStatus()));
      return Promise.resolve(jsonResponse({ tracks: sampleTracks }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(Library);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/tracks"));

    const input = screen.getByPlaceholderText("Search tracks...") as HTMLInputElement;
    input.value = "queen";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 250)); // past the 200ms search debounce
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/tracks?query=queen"));

    jobsStore.start();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/jobs"));
    const { connectJobsSocket } = await import("../jobsSocket");
    const onEvent = vi.mocked(connectJobsSocket).mock.calls[0][0].onEvent;
    onEvent({ type: "job", job_id: 1, status: "completed" });

    await waitFor(() => {
      const queryCalls = fetchMock.mock.calls.filter((call) => call[0] === "/api/tracks?query=queen").length;
      expect(queryCalls).toBeGreaterThanOrEqual(2); // once from typing, once from the completion refetch
    });

    jobsStore.stop();
  });

  it("refetches current database rows without starting a library scan when a job completes", async () => {
    // The queue updates each affected row at the stage boundary. Terminal
    // handling is therefore only a missed-WebSocket database reconciliation;
    // it must never walk the complete media library.
    const calledUrls: string[] = [];
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      calledUrls.push(url.toString());
      if (url.toString().startsWith("/api/jobs")) return Promise.resolve(jsonResponse({ jobs: [] }));
      if (url.toString().startsWith("/api/rescan")) {
        return Promise.resolve(jsonResponse(completedScanStatus()));
      }
      return Promise.resolve(jsonResponse({ tracks: sampleTracks }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(Library);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/tracks"));

    jobsStore.start();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/jobs"));

    const { connectJobsSocket } = await import("../jobsSocket");
    const onEvent = vi.mocked(connectJobsSocket).mock.calls[0][0].onEvent;
    const trackCallsBefore = calledUrls.filter((url) => url === "/api/tracks").length;
    onEvent({ type: "job", job_id: 1, status: "completed" });

    await waitFor(() => {
      expect(calledUrls.filter((url) => url === "/api/tracks").length).toBeGreaterThan(trackCallsBefore);
    });
    expect(calledUrls).not.toContain("/api/rescan");

    jobsStore.stop();
  });

  it("shows refreshed badges on remount after a job completed while Library was unmounted", async () => {
    // Regression test for the "Library may be unmounted when the job
    // finishes" case: the completion-triggered database refresh must happen
    // at the tracksStore module level, not just while Library happens to be
    // mounted - otherwise the user comes back from the Mixer/Editor to a
    // freshly-mounted Library that still shows stale badges.
    let tracksToServe = [
      { ...sampleTracks[0], outputs: { ...sampleTracks[0].outputs, instrumental: false } },
    ];
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const u = url.toString();
      if (u.startsWith("/api/jobs")) return Promise.resolve(jsonResponse({ jobs: [] }));
      return Promise.resolve(jsonResponse({ tracks: tracksToServe }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());
    expect(screen.getByText("Missing")).toBeTruthy();

    // Simulate the user navigating away to the Mixer/Editor - Library
    // unmounts, and its own onJobCompleted-style wiring (if it had any) would
    // go with it.
    unmount();

    jobsStore.start();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/jobs"));
    const { connectJobsSocket } = await import("../jobsSocket");
    const onEvent = vi.mocked(connectJobsSocket).mock.calls[vi.mocked(connectJobsSocket).mock.calls.length - 1][0].onEvent;
    // Simulate the queue's targeted stage-boundary database update. Even if
    // its track_updated event was missed, the terminal database refresh sees
    // the current row without a filesystem scan.
    tracksToServe = [
      { ...sampleTracks[0], outputs: { ...sampleTracks[0].outputs, instrumental: true } },
    ];
    const tracksCallsBefore = fetchMock.mock.calls.filter((call) => call[0] === "/api/tracks").length;
    onEvent({ type: "job", job_id: 2, status: "completed" });

    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter((call) => call[0] === "/api/tracks").length;
      expect(calls).toBeGreaterThan(tracksCallsBefore);
    });
    expect(fetchMock.mock.calls.some((call) => call[0] === "/api/rescan")).toBe(false);

    // Now the user comes back to Library - it should render the already-
    // fresh badge immediately, not the stale one it started with.
    render(Library);
    await waitFor(() => expect(screen.getByText("Ready")).toBeTruthy());

    jobsStore.stop();
  });

  it("passes onOpenMixer/onOpenEditor through to TrackRow", async () => {
    stubFetchTracks(sampleTracks);
    const onOpenMixer = vi.fn();
    const onOpenEditor = vi.fn();

    render(Library, { props: { onOpenMixer, onOpenEditor } });
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.dblClick(screen.getByText("Dancing Queen"));
    expect(onOpenMixer).toHaveBeenCalledWith(sampleTracks[0]);

    await fireEvent.click(screen.getByText("Edit lyrics"));
    expect(onOpenEditor).toHaveBeenCalledWith(sampleTracks[0]);
  });
});

const twoTracks: Track[] = [
  sampleTracks[0],
  {
    ...sampleTracks[0],
    id: 2,
    relative_path: "Queen/Bohemian Rhapsody.flac",
    artist: "Queen",
    title: "Bohemian Rhapsody",
  },
];

describe("Library preview playback", () => {
  it("plays the original track when the preview button is clicked", async () => {
    stubFetchTracks(sampleTracks);
    vi.stubGlobal("Audio", FakeAudio);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Play preview"));

    expect(FakeAudio.instances).toHaveLength(1);
    expect(FakeAudio.instances[0].src).toBe("/api/audio/1/part/original");
    expect(FakeAudio.instances[0].play).toHaveBeenCalled();
    expect(screen.getByLabelText("Pause preview")).toBeTruthy();
  });

  it("reports preview playback start and stop to the app close guard", async () => {
    stubFetchTracks(sampleTracks);
    vi.stubGlobal("Audio", FakeAudio);
    const onPlaybackChange = vi.fn();

    render(Library, { props: { onPlaybackChange } });
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());
    await fireEvent.click(screen.getByLabelText("Play preview"));
    await waitFor(() => expect(onPlaybackChange).toHaveBeenCalledWith(true));
    await fireEvent.click(screen.getByLabelText("Pause preview"));
    expect(onPlaybackChange).toHaveBeenLastCalledWith(false);
  });

  it("pauses when the same row's button is clicked again", async () => {
    stubFetchTracks(sampleTracks);
    vi.stubGlobal("Audio", FakeAudio);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Play preview"));
    await fireEvent.click(screen.getByLabelText("Pause preview"));

    expect(FakeAudio.instances[0].pause).toHaveBeenCalled();
    expect(screen.getByLabelText("Play preview")).toBeTruthy();
  });

  it("stops the previous preview when starting a preview on another row", async () => {
    stubFetchTracks(twoTracks);
    vi.stubGlobal("Audio", FakeAudio);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    const buttons = screen.getAllByLabelText("Play preview");
    await fireEvent.click(buttons[0]);
    expect(FakeAudio.instances).toHaveLength(1);

    // Row 0 is now showing "Pause preview"; re-query for the still-"Play
    // preview" button, which belongs to row 1.
    const remainingPlayButton = screen.getByLabelText("Play preview");
    await fireEvent.click(remainingPlayButton);

    expect(FakeAudio.instances[0].pause).toHaveBeenCalled();
    expect(FakeAudio.instances).toHaveLength(2);
    expect(FakeAudio.instances[1].src).toBe("/api/audio/2/part/original");
    expect(screen.getAllByLabelText("Pause preview")).toHaveLength(1);
  });

  it("stops the preview when navigating away by double-clicking a row to open the mixer", async () => {
    stubFetchTracks(sampleTracks);
    vi.stubGlobal("Audio", FakeAudio);
    const onOpenMixer = vi.fn();

    render(Library, { props: { onOpenMixer } });
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Play preview"));
    await fireEvent.dblClick(screen.getByText("Dancing Queen"));

    expect(FakeAudio.instances[0].pause).toHaveBeenCalled();
    expect(onOpenMixer).toHaveBeenCalledWith(sampleTracks[0]);
    expect(screen.getByLabelText("Play preview")).toBeTruthy();
  });

  it("stops the preview when navigating away via Edit lyrics", async () => {
    stubFetchTracks(sampleTracks);
    vi.stubGlobal("Audio", FakeAudio);
    const onOpenEditor = vi.fn();

    render(Library, { props: { onOpenEditor } });
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Play preview"));
    await fireEvent.click(screen.getByText("Edit lyrics"));

    expect(FakeAudio.instances[0].pause).toHaveBeenCalled();
    expect(onOpenEditor).toHaveBeenCalledWith(sampleTracks[0]);
  });

  it("stops any preview when the Library component is destroyed", async () => {
    stubFetchTracks(sampleTracks);
    vi.stubGlobal("Audio", FakeAudio);

    const { unmount } = render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Play preview"));
    unmount();

    expect(FakeAudio.instances[0].pause).toHaveBeenCalled();
  });

  it("releases the underlying stream (clears src, calls load) when a preview is stopped", async () => {
    stubFetchTracks(sampleTracks);
    vi.stubGlobal("Audio", FakeAudio);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Play preview"));
    expect(FakeAudio.instances[0].src).toBe("/api/audio/1/part/original");

    await fireEvent.click(screen.getByLabelText("Pause preview"));

    expect(FakeAudio.instances[0].src).toBe("");
    expect(FakeAudio.instances[0].load).toHaveBeenCalled();
  });

  it("recovers to the non-playing state (no stuck Pause button) when play() rejects", async () => {
    stubFetchTracks(sampleTracks);
    FakeAudio.nextPlayResult = () => Promise.reject(new Error("playback blocked"));
    vi.stubGlobal("Audio", FakeAudio);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Play preview"));

    // The rejection is asynchronous (a real play() promise settles on a
    // microtask), so the UI only recovers once it's handled.
    await waitFor(() => expect(screen.getByLabelText("Play preview")).toBeTruthy());
    expect(screen.queryByLabelText("Pause preview")).toBeNull();
  });
});

describe("Library select-all control", () => {
  it("is unchecked and not indeterminate when nothing is selected", async () => {
    stubFetchTracks(twoTracks);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    const selectAll = screen.getByLabelText("Select all tracks") as HTMLInputElement;
    expect(selectAll.checked).toBe(false);
    expect(selectAll.indeterminate).toBe(false);
  });

  it("is indeterminate when some but not all visible tracks are selected", async () => {
    stubFetchTracks(twoTracks);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Select Dancing Queen"));

    const selectAll = screen.getByLabelText("Select all tracks") as HTMLInputElement;
    expect(selectAll.checked).toBe(false);
    expect(selectAll.indeterminate).toBe(true);
  });

  it("is checked (and its label reflects deselect) once all visible tracks are selected", async () => {
    stubFetchTracks(twoTracks);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Select Dancing Queen"));
    await fireEvent.click(screen.getByLabelText("Select Bohemian Rhapsody"));

    const selectAll = screen.getByLabelText("Deselect all tracks") as HTMLInputElement;
    expect(selectAll.checked).toBe(true);
    expect(selectAll.indeterminate).toBe(false);
  });

  it("selects all visible tracks when clicked from the none/some-selected state", async () => {
    stubFetchTracks(twoTracks);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Select all tracks"));

    expect(screen.getByText("Prepare selected (2)")).toBeTruthy();
  });

  it("deselects all visible tracks when clicked from the all-selected state", async () => {
    stubFetchTracks(twoTracks);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Select all tracks"));
    expect(screen.getByText("Prepare selected (2)")).toBeTruthy();

    await fireEvent.click(screen.getByLabelText("Deselect all tracks"));

    expect(screen.getByText("Prepare selected (0)")).toBeTruthy();
  });

  it("scopes select-all/deselect-all to tracks visible after a search filter", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, status: 200, json: async () => ({ tracks: twoTracks }) });
    vi.stubGlobal("fetch", fetchMock);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    // Select everything while both tracks are visible.
    await fireEvent.click(screen.getByLabelText("Select all tracks"));
    expect(screen.getByText("Prepare selected (2)")).toBeTruthy();

    // Narrow the visible set via search - tracksStore.refresh() (debounced)
    // re-fetches and replaces the store's tracks with just the match.
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ tracks: [twoTracks[0]] }),
    });
    const input = screen.getByPlaceholderText("Search tracks...") as HTMLInputElement;
    input.value = "dancing";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 250)); // past the 200ms search debounce
    await waitFor(() => expect(screen.queryByText("Bohemian Rhapsody")).toBeNull());

    // Both tracks are still individually selected (selections persist across
    // search - see clearSelection's comment), but only one is now visible,
    // so the header checkbox reads "all VISIBLE selected", not "all ever
    // selected".
    const selectAll = screen.getByLabelText("Deselect all tracks") as HTMLInputElement;
    expect(selectAll.checked).toBe(true);
    expect(selectAll.indeterminate).toBe(false);

    await fireEvent.click(selectAll);

    // Deselecting while filtered removes only the visible track from the
    // selection - the hidden, previously-selected Bohemian Rhapsody survives.
    expect(screen.getByText("Prepare selected (1)")).toBeTruthy();
  });
});

describe("Library table columns", () => {
  it("reorders columns by dragging a handle in the Columns panel and persists the order", async () => {
    stubFetchTracks(sampleTracks);
    const { container } = render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());
    await fireEvent.click(screen.getByRole("button", { name: "Columns" }));

    const filenameHandle = container.querySelector('[title="Drag Filename to reorder"]') as HTMLElement;
    const titleRow = container.querySelector('[data-column-key="title"]') as HTMLElement;
    const dataTransfer = {
      effectAllowed: "none",
      dropEffect: "none",
      setData: vi.fn(),
      getData: vi.fn(() => "filename"),
    };

    await fireEvent.dragStart(filenameHandle, { dataTransfer });
    await fireEvent.dragOver(titleRow, { dataTransfer });
    expect(titleRow.classList.contains("drag-over")).toBe(true);
    await fireEvent.drop(titleRow, { dataTransfer });

    const stored = JSON.parse(fakeStorage.getItem(LIBRARY_COLUMNS_STORAGE_KEY)!);
    const orderedKeys = stored.columns
      .slice()
      .sort((a: { order: number }, b: { order: number }) => a.order - b.order)
      .map((column: { key: string }) => column.key);
    expect(orderedKeys.indexOf("filename")).toBeGreaterThan(orderedKeys.indexOf("title"));

    const headers = [...container.querySelectorAll("thead th")];
    const filenameHeader = screen.getByLabelText("Sort by Filename, currently unsorted").closest("th")!;
    const titleHeader = screen.getByLabelText("Sort by Title, currently unsorted").closest("th")!;
    expect(headers.indexOf(filenameHeader)).toBeGreaterThan(headers.indexOf(titleHeader));
  });

  it("resizes a column by dragging its header edge and persists the width", async () => {
    stubFetchTracks(sampleTracks);
    const { container } = render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    const handle = screen.getByRole("button", { name: "Resize Folder column" });
    const header = handle.closest("th") as HTMLElement;
    expect(header.style.width).toBe("240px");

    await fireEvent(handle, new MouseEvent("pointerdown", { bubbles: true, clientX: 200 }));
    await fireEvent(handle, new MouseEvent("pointermove", { bubbles: true, clientX: 280 }));
    await fireEvent(handle, new MouseEvent("pointerup", { bubbles: true, clientX: 280 }));

    expect(header.style.width).toBe("320px");
    expect((container.querySelector(".track-row-cell-folder") as HTMLElement).style.width).toBe("320px");
    const stored = JSON.parse(fakeStorage.getItem(LIBRARY_COLUMNS_STORAGE_KEY)!);
    expect(stored.columns.find((column: { key: string }) => column.key === "folder").width).toBe(320);
    expect(screen.getByLabelText("Sort by Folder, currently unsorted")).toBeTruthy();
  });

  it("supports keyboard resizing and offers a reset-widths action", async () => {
    stubFetchTracks(sampleTracks);
    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    const handle = screen.getByRole("button", { name: "Resize Title column" });
    await fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect((handle.closest("th") as HTMLElement).style.width).toBe("270px");

    await fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    await fireEvent.click(screen.getByRole("button", { name: "Reset widths" }));
    expect((screen.getByRole("button", { name: "Resize Title column" }).closest("th") as HTMLElement).style.width).toBe("260px");
  });

  it("renders source folder and core track facts by default while keeping Album available in Columns", async () => {
    stubFetchTracks(sampleTracks);

    const { container } = render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    expect(container.querySelector("table.library-table")).toBeTruthy();
    for (const label of ["Artwork", "Folder", "Filename", "Artist", "Title", "Instrumental", "Lyrics", "Stems", "Year", "Duration"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByText("D:/Media/ABBA")).toBeTruthy();
    expect(screen.getByText("Dancing Queen.flac")).toBeTruthy();
    expect(screen.queryByText("Arrival")).toBeNull();
    expect(screen.getByText("1976")).toBeTruthy();
    expect(screen.getByText("3:33")).toBeTruthy();

    await fireEvent.click(screen.getByText("Columns"));
    expect(screen.getByRole("dialog", { name: "Columns" })).toBeTruthy();
    expect(screen.getAllByText("Folder").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Album")).toBeTruthy();
  });

  it("opens the column menu on right-clicking the header row and closes it via its close button", async () => {
    stubFetchTracks(sampleTracks);
    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.contextMenu(screen.getByText("Title"));
    expect(screen.getByRole("dialog", { name: "Columns" })).toBeTruthy();

    await fireEvent.click(screen.getByLabelText("Close columns"));
    expect(screen.queryByRole("dialog", { name: "Columns" })).toBeNull();
  });

  it("keeps the column dialog open when the backdrop is clicked", async () => {
    stubFetchTracks(sampleTracks);
    const { container } = render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());
    await fireEvent.click(screen.getByRole("button", { name: "Columns" }));

    await fireEvent.click(container.querySelector(".library-column-menu-overlay")!);

    expect(screen.getByRole("dialog", { name: "Columns" })).toBeTruthy();
  });

  it("hides a column when its checkbox is unchecked, and persists the change", async () => {
    stubFetchTracks(sampleTracks);
    const { container } = render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.contextMenu(container.querySelector(".library-table-header-row")!);
    // "Year" appears in both the header and menu - disambiguate
    // by finding the menu row via its container class, not getByText.
    const menuRows = [...container.querySelectorAll(".library-column-menu-row")];
    const yearRow = menuRows.find((row) => row.textContent?.includes("Year")) as HTMLElement;
    const yearCheckbox = yearRow.querySelector('input[type="checkbox"]') as HTMLInputElement;

    await fireEvent.click(yearCheckbox);

    expect(screen.queryByText("1976")).toBeNull();
    const stored = JSON.parse(fakeStorage.getItem(LIBRARY_COLUMNS_STORAGE_KEY)!);
    expect(stored.columns.find((c: { key: string }) => c.key === "year").visible).toBe(false);
  });

  it("sorts by a column when its Sort button is clicked", async () => {
    const twoTracksUnsorted: Track[] = [
      sampleTracks[0],
      { ...sampleTracks[0], id: 2, title: "Bohemian Rhapsody", artist: "Queen", year: 1975 },
    ];
    stubFetchTracks(twoTracksUnsorted);
    const { container } = render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.contextMenu(container.querySelector(".library-table-header-row")!);
    const menuRows = [...container.querySelectorAll(".library-column-menu-row")];
    const titleRow = menuRows.find((row) => row.textContent?.includes("Title")) as HTMLElement;
    await fireEvent.click(titleRow.querySelector('[aria-label="Sort ascending"]') as HTMLElement);

    const bodyRows = [...container.querySelectorAll("tbody tr")];
    expect(bodyRows[0].textContent).toContain("Bohemian Rhapsody");
  });

  it("filters rows via a column's filter text input", async () => {
    const twoTracksForFilter: Track[] = [
      sampleTracks[0],
      { ...sampleTracks[0], id: 2, title: "Bohemian Rhapsody", artist: "Queen" },
    ];
    stubFetchTracks(twoTracksForFilter);
    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.contextMenu(screen.getByText("Title"));
    await fireEvent.input(screen.getByLabelText("Filter Title"), { target: { value: "bohemian" } });

    await waitFor(() => expect(screen.queryByText("Dancing Queen")).toBeNull());
    expect(screen.getByText("Bohemian Rhapsody")).toBeTruthy();
  });

  it("filters production-state columns with explicit choices", async () => {
    const twoTracksForFilter: Track[] = [
      sampleTracks[0],
      {
        ...sampleTracks[0], id: 2, title: "Needs preparation", artist: "New Artist",
        outputs: { ...sampleTracks[0].outputs, instrumental: false, lrc: false },
        lrc_state: null,
        stem_count: 0,
      },
    ];
    stubFetchTracks(twoTracksForFilter);
    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    await fireEvent.change(screen.getByLabelText("Filter Instrumental"), { target: { value: "missing" } });

    await waitFor(() => expect(screen.queryByText("Dancing Queen")).toBeNull());
    expect(screen.getByText("Needs preparation")).toBeTruthy();
    expect(screen.getByLabelText("Filter Lyrics")).toBeTruthy();
    expect(screen.getByLabelText("Filter Stems")).toBeTruthy();
  });

  it("filters directly from headings, combines filters, and clears them together", async () => {
    const filterTracks: Track[] = [
      { ...sampleTracks[0], has_artwork: true },
      {
        ...sampleTracks[0], id: 2, title: "Bohemian Rhapsody", artist: "Queen", has_artwork: false,
      },
      {
        ...sampleTracks[0], id: 3, title: "Waterloo", artist: "ABBA", has_artwork: false,
      },
    ];
    stubFetchTracks(filterTracks);
    render(Library);
    await waitFor(() => expect(screen.getByText("Bohemian Rhapsody")).toBeTruthy());

    await fireEvent.click(screen.getByRole("button", { name: "Open Artwork filter" }));
    await fireEvent.change(screen.getByLabelText("Artwork filter value"), { target: { value: "missing" } });
    await fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.queryByText("Dancing Queen")).toBeNull());
    expect(screen.getByText("Bohemian Rhapsody")).toBeTruthy();
    expect(screen.getByText("Waterloo")).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "Open Artist filter" }));
    await fireEvent.input(screen.getByLabelText("Artist filter value"), { target: { value: "ABBA" } });
    await fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.queryByText("Bohemian Rhapsody")).toBeNull());
    expect(screen.getByText("Waterloo")).toBeTruthy();
    expect(screen.getByText("1 of 3 shown", { exact: false })).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "Clear filters (2)" }));
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());
    expect(screen.getByText("Bohemian Rhapsody")).toBeTruthy();
  });

  it("loads a persisted column state on mount", async () => {
    fakeStorage.setItem(
      LIBRARY_COLUMNS_STORAGE_KEY,
      JSON.stringify({
        columns: [
          { key: "folder", label: "Folder", visible: false, order: 0, filter: "" },
          { key: "artist", label: "Artist", visible: true, order: 1, filter: "" },
          { key: "title", label: "Title", visible: true, order: 2, filter: "" },
          { key: "album", label: "Album", visible: false, order: 3, filter: "" },
          { key: "year", label: "Year", visible: true, order: 4, filter: "" },
          { key: "duration", label: "Duration", visible: true, order: 5, filter: "" },
        ],
        version: 2,
        sortKey: null,
        sortDirection: "asc",
      })
    );
    stubFetchTracks(sampleTracks);

    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    expect(screen.queryByText("Folder")).toBeNull();
    expect(screen.queryByText("Album")).toBeNull();
    expect(screen.getByText("Artist")).toBeTruthy();
  });

  it("cycles a column header through ascending, descending, and unsorted", async () => {
    const twoTracksUnsorted: Track[] = [
      sampleTracks[0],
      { ...sampleTracks[0], id: 2, title: "Bohemian Rhapsody", artist: "Queen" },
    ];
    stubFetchTracks(twoTracksUnsorted);
    const { container } = render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    const header = screen.getByLabelText("Sort by Title, currently unsorted");
    await fireEvent.click(header);
    expect([...container.querySelectorAll("tbody tr")][0].textContent).toContain("Bohemian Rhapsody");

    await fireEvent.click(screen.getByLabelText("Sort by Title, currently ascending"));
    expect([...container.querySelectorAll("tbody tr")][0].textContent).toContain("Dancing Queen");

    await fireEvent.click(screen.getByLabelText("Sort by Title, currently descending"));
    expect([...container.querySelectorAll("tbody tr")][0].textContent).toContain("Dancing Queen");
    expect(screen.getByLabelText("Sort by Title, currently unsorted")).toBeTruthy();
  });

  it("opens Columns from a visible button, traps Escape, and restores focus", async () => {
    stubFetchTracks(sampleTracks);
    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    const opener = screen.getByRole("button", { name: "Columns" });
    await fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Columns" });
    expect(document.activeElement).toBe(dialog);

    await fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Columns" })).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});

describe("Library tag editing", () => {
  it("opens the selected track's editor and updates the row after save", async () => {
    stubFetchTracks(sampleTracks);
    const { saveTrackTags } = await import("../api");
    vi.mocked(saveTrackTags).mockResolvedValue({ ...sampleTracks[0], title: "Dancing Queen (Remastered)" });
    render(Library);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.click(screen.getByRole("button", { name: "Tags" }));
    expect(screen.getByRole("dialog", { name: "Fix tags & artwork" })).toBeTruthy();
    await fireEvent.click(screen.getByText("Save changes"));

    await waitFor(() => expect(screen.getByText("Dancing Queen (Remastered)")).toBeTruthy());
    expect(screen.queryByRole("dialog", { name: "Fix tags & artwork" })).toBeNull();
  });
});
