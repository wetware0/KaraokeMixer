import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Track } from "../types";

vi.mock("../api", () => ({ deleteTrack: vi.fn() }));

import { deleteTrack } from "../api";
import DeleteTrackDialog from "./DeleteTrackDialog.svelte";

const track: Track = {
  id: 7, media_root: "D:/Media", relative_path: "Song.flac", artist: "Artist", title: "Song",
  outputs: {
    instrumental: true, vocals: false, lead_vocals: false, backing_vocals: false,
    drums: false, bass: false, guitar: false, piano: false, other: false, lrc: true,
  },
  lrc_state: "enhanced", stem_count: 1, album: null, year: null, duration_seconds: 180,
};

afterEach(() => vi.clearAllMocks());

describe("DeleteTrackDialog", () => {
  it("requires a second explicit action and includes generated outputs by default", async () => {
    vi.mocked(deleteTrack).mockResolvedValue({ track_id: 7, moved_to_recycle_bin: [] });
    const onDeleted = vi.fn();
    render(DeleteTrackDialog, { props: { track, onDeleted } });

    expect(deleteTrack).not.toHaveBeenCalled();
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    await fireEvent.click(screen.getByRole("button", { name: "Move to Recycle Bin" }));

    await waitFor(() => expect(deleteTrack).toHaveBeenCalledWith(7, true));
    expect(onDeleted).toHaveBeenCalledWith(7);
  });

  it("stays open on backdrop click and closes with Cancel", async () => {
    const onClose = vi.fn();
    render(DeleteTrackDialog, { props: { track, onClose } });
    await fireEvent.click(screen.getByRole("dialog", { name: "Move track to Recycle Bin?" }));
    expect(onClose).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("keeps the dialog open and shows a backend safety error", async () => {
    vi.mocked(deleteTrack).mockRejectedValue(new Error("This track is queued or processing."));
    const onDeleted = vi.fn();
    render(DeleteTrackDialog, { props: { track, onDeleted } });
    await fireEvent.click(screen.getByRole("button", { name: "Move to Recycle Bin" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("queued or processing");
    expect(onDeleted).not.toHaveBeenCalled();
  });
});
