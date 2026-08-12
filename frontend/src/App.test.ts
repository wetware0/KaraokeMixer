import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockStart, mockJobs } = vi.hoisted(() => ({ mockStart: vi.fn(), mockJobs: [] as unknown[] }));

vi.mock("./lib/jobsStore.svelte", () => ({
  jobsStore: {
    jobs: mockJobs,
    stageDetails: {},
    trackFailures: {},
    start: mockStart,
    stop: vi.fn(),
    cancel: vi.fn(),
    onJobCompleted: vi.fn(() => () => {}),
    onJobCompletedFor: vi.fn(() => () => {}),
    onTrackChanged: vi.fn(() => () => {}),
  },
}));

vi.mock("./lib/api", () => ({
  artworkUrl: vi.fn((id: number) => `/api/tracks/${id}/artwork`),
  fetchSystem: vi.fn().mockRejectedValue(new Error("backend down")),
  fetchTracks: vi.fn().mockResolvedValue([]),
  fetchRescanStatus: vi.fn().mockResolvedValue({
    scan_id: 0, status: "idle", tracks_found: 0, media_roots_scanned: 0, media_roots_total: 0,
    current_root: null, unavailable_roots: [], tracks_purged: 0, error: null, updated_at: "2026-08-05T00:00:00Z",
  }),
  rescan: vi.fn(),
  fetchSettings: vi.fn().mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" }),
  fetchTrackParts: vi.fn().mockResolvedValue([]),
  fetchJobHistory: vi.fn().mockResolvedValue({ jobs: [], total: 0, limit: 25, offset: 0 }),
  fetchJobItems: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 }),
  fetchJob: vi.fn().mockResolvedValue({
    id: 94, recipe: "karaoke", options: {}, status: "queued",
    created_at: "2026-08-10T02:00:00Z", started_at: null, finished_at: null, items: [],
  }),
  fetchLrc: vi.fn().mockResolvedValue({ exists: false, content: "", state: null }),
}));

import App from "./App.svelte";

describe("App", () => {
  beforeEach(() => mockJobs.splice(0));

  it("starts the jobs store even when the device probe rejects", async () => {
    render(App);

    expect(mockStart).toHaveBeenCalled();

    // Let the rejected fetchSystem() promise settle; it must not throw an
    // unhandled rejection or prevent jobsStore.start() from having run.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("places processing progress in the app layout after the workspace instead of over it", () => {
    mockJobs.push({
      id: 94,
      recipe: "karaoke",
      options: {},
      status: "queued",
      created_at: "2026-08-10T02:00:00Z",
      started_at: null,
      finished_at: null,
      item_counts: { queued: 1, running: 0, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
    });

    const { container } = render(App);
    const shell = container.querySelector(".app-shell")!;
    const workspace = container.querySelector(".app-body")!;
    const tray = container.querySelector(".job-tray")!;

    expect(tray.parentElement).toBe(shell);
    expect(workspace.nextElementSibling).toBe(tray);
  });

  it("activates the browser close confirmation while a job is active", () => {
    render(App);
    const safeEvent = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(safeEvent);
    expect(safeEvent.defaultPrevented).toBe(false);

    mockJobs.push({ status: "running", recipe: "youtube_import" });
    const protectedEvent = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(protectedEvent);
    expect(protectedEvent.defaultPrevented).toBe(true);
  });

  it("double-clicking a track row switches to the Mixer view for that track", async () => {
    const { fetchTracks } = await import("./lib/api");
    vi.mocked(fetchTracks).mockResolvedValue([
      {
        id: 1, media_root: "D:/Media", relative_path: "Song.flac", artist: "ABBA", title: "Dancing Queen",
        outputs: {
          instrumental: false, vocals: false, lead_vocals: false, backing_vocals: false,
          drums: false, bass: false, guitar: false, piano: false, other: false, lrc: false,
        },
        lrc_state: null, stem_count: 0,
      },
    ]);

    render(App);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());

    await fireEvent.dblClick(screen.getByText("Dancing Queen"));

    await waitFor(() => expect(screen.getByText("← Back")).toBeTruthy());
    expect(screen.getByRole("heading", { name: "Dancing Queen" })).toBeTruthy();
    expect(screen.getByPlaceholderText("Search tracks...").closest("[hidden]")).toBeTruthy();
  });

  it("the back button instantly restores the same mounted Library state without refetching", async () => {
    const { fetchTracks } = await import("./lib/api");
    vi.mocked(fetchTracks).mockResolvedValue([
      {
        id: 1, media_root: "D:/Media", relative_path: "Song.flac", artist: "ABBA", title: "Dancing Queen",
        outputs: {
          instrumental: false, vocals: false, lead_vocals: false, backing_vocals: false,
          drums: false, bass: false, guitar: false, piano: false, other: false, lrc: false,
        },
        lrc_state: null, stem_count: 0,
      },
    ]);

    render(App);
    await waitFor(() => expect(screen.getByText("Dancing Queen")).toBeTruthy());
    const search = screen.getByPlaceholderText("Search tracks...") as HTMLInputElement;
    await fireEvent.input(search, { target: { value: "Dancing" } });
    await waitFor(() => expect(vi.mocked(fetchTracks).mock.calls.at(-1)).toEqual(["Dancing"]));
    const fetchCountBeforeNavigation = vi.mocked(fetchTracks).mock.calls.length;
    await fireEvent.dblClick(screen.getByText("Dancing Queen"));
    await waitFor(() => expect(screen.getByText("← Back")).toBeTruthy());

    await fireEvent.click(screen.getByText("← Back"));

    const restoredSearch = screen.getByPlaceholderText("Search tracks...") as HTMLInputElement;
    expect(restoredSearch).toBe(search);
    expect(restoredSearch.value).toBe("Dancing");
    expect(restoredSearch.closest("[hidden]")).toBeNull();
    expect(vi.mocked(fetchTracks).mock.calls.length).toBe(fetchCountBeforeNavigation);
  });

  it("updates the open lyric editor when the refreshed library record changes", async () => {
    const { fetchTracks } = await import("./lib/api");
    const { tracksStore } = await import("./lib/tracksStore.svelte");
    const original = {
      id: 41, media_root: "D:/Media", relative_path: "Song.flac", artist: "ABBA", title: "Old title",
      outputs: {
        instrumental: false, vocals: false, lead_vocals: false, backing_vocals: false,
        drums: false, bass: false, guitar: false, piano: false, other: false, lrc: true,
      },
      lrc_state: "line_timed" as const, stem_count: 0,
    };
    vi.mocked(fetchTracks).mockResolvedValue([original]);

    render(App);
    await waitFor(() => expect(screen.getByText("Old title")).toBeTruthy());
    await fireEvent.click(screen.getByRole("button", { name: "Edit lyrics" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Old title" })).toBeTruthy());

    vi.mocked(fetchTracks).mockResolvedValue([{ ...original, title: "Refreshed title", year: 2026 }]);
    await tracksStore.refresh("");

    await waitFor(() => expect(screen.getByRole("heading", { name: "Refreshed title" })).toBeTruthy());
  });

  it("opens Processing history and returns to the same mounted Library", async () => {
    const { fetchTracks } = await import("./lib/api");
    vi.mocked(fetchTracks).mockResolvedValue([]);

    render(App);
    const librarySearch = screen.getByPlaceholderText("Search tracks...");
    await fireEvent.click(screen.getByRole("button", { name: "Processing history" }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "Processing history" })).toBeTruthy());
    expect(librarySearch.closest("[hidden]")).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "← Library" }));

    expect(librarySearch.closest("[hidden]")).toBeNull();
  });
});
