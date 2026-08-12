import { describe, expect, it, vi } from "vitest";
import { protectWindowClose, shouldConfirmClose } from "./closeProtection";
import type { JobSummary, LibraryScanStatus } from "./types";

function job(status: JobSummary["status"], recipe = "karaoke"): JobSummary {
  return {
    id: 1, recipe, options: {}, status, created_at: "now", started_at: null, finished_at: null,
    item_counts: { queued: 0, running: 0, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
  };
}

function scan(status: LibraryScanStatus["status"]): LibraryScanStatus {
  return {
    scan_id: 1, status, tracks_found: 0, media_roots_scanned: 0, media_roots_total: 1,
    current_root: null, unavailable_roots: [], tracks_purged: 0, error: null, updated_at: "now",
  };
}

describe("close protection", () => {
  it("warns for queued/running work, downloads, scans, and playback", () => {
    expect(shouldConfirmClose({ jobs: [job("queued")], scanStatus: null, trackPlaying: false })).toBe(true);
    expect(shouldConfirmClose({ jobs: [job("running", "youtube_import")], scanStatus: null, trackPlaying: false })).toBe(true);
    expect(shouldConfirmClose({ jobs: [], scanStatus: scan("running"), trackPlaying: false })).toBe(true);
    expect(shouldConfirmClose({ jobs: [], scanStatus: null, trackPlaying: true })).toBe(true);
  });

  it("does not warn when work is terminal and audio is paused", () => {
    expect(shouldConfirmClose({ jobs: [job("completed"), job("failed"), job("cancelled")], scanStatus: scan("completed"), trackPlaying: false })).toBe(false);
  });

  it("activates the browser confirmation only for an unsafe close", () => {
    const preventDefault = vi.fn();
    const event = { preventDefault, returnValue: undefined } as unknown as BeforeUnloadEvent;
    protectWindowClose(event, { jobs: [job("running")], scanStatus: null, trackPlaying: false });
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(event.returnValue).toBe("");

    preventDefault.mockClear();
    const safeEvent = { preventDefault, returnValue: undefined } as unknown as BeforeUnloadEvent;
    protectWindowClose(safeEvent, { jobs: [], scanStatus: null, trackPlaying: false });
    expect(preventDefault).not.toHaveBeenCalled();
    expect(safeEvent.returnValue).toBeUndefined();
  });
});
