import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import CreateLyricsDialog from "./CreateLyricsDialog.svelte";
import type { Track } from "../types";

vi.mock("../api", () => ({
  saveLrc: vi.fn(),
}));

import { saveLrc } from "../api";

const track: Track = {
  id: 1, media_root: "D:/Media", relative_path: "Song.flac", artist: "ABBA", title: "Dancing Queen",
  outputs: {
    instrumental: false, vocals: false, lead_vocals: false, backing_vocals: false,
    drums: false, bass: false, guitar: false, piano: false, other: false, lrc: false,
  },
  lrc_state: null, stem_count: 0,
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("CreateLyricsDialog", () => {
  it("focuses the textarea on mount so Escape works", () => {
    const { container } = render(CreateLyricsDialog, { props: { track, onCreated: vi.fn(), onClose: vi.fn() } });
    const textarea = container.querySelector(".process-dialog-textarea") as HTMLElement;
    expect(document.activeElement === textarea).toBe(true);
  });

  it("disables Create when the textarea is empty", () => {
    render(CreateLyricsDialog, { props: { track, onCreated: vi.fn(), onClose: vi.fn() } });
    expect((screen.getByText("Create") as HTMLButtonElement).disabled).toBe(true);
  });

  it("enables Create once text is entered, saves with create=beside, and calls onCreated", async () => {
    const createdTrack = {
      ...track,
      outputs: { ...track.outputs, lrc: true },
      lrc_state: "untimed" as const,
    };
    vi.mocked(saveLrc).mockResolvedValue({ path: "D:/Media/Song.lrc", track: createdTrack });
    const onCreated = vi.fn();
    render(CreateLyricsDialog, { props: { track, onCreated, onClose: vi.fn() } });

    await fireEvent.input(screen.getByPlaceholderText("Paste plain lyric lines…"), {
      target: { value: "Hello world\nSecond line" },
    });
    expect((screen.getByText("Create") as HTMLButtonElement).disabled).toBe(false);

    await fireEvent.click(screen.getByText("Create"));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(createdTrack));
    expect(saveLrc).toHaveBeenCalledWith(1, "Hello world\nSecond line", { create: "beside" });
  });

  it("shows the backend's error message and does not call onCreated when the save fails", async () => {
    vi.mocked(saveLrc).mockRejectedValue(new Error("No .lrc file resolved for this track"));
    const onCreated = vi.fn();
    render(CreateLyricsDialog, { props: { track, onCreated, onClose: vi.fn() } });

    await fireEvent.input(screen.getByPlaceholderText("Paste plain lyric lines…"), {
      target: { value: "Hello world" },
    });
    await fireEvent.click(screen.getByText("Create"));

    await waitFor(() => expect(screen.getByText("No .lrc file resolved for this track")).toBeTruthy());
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("calls onClose when Cancel is clicked, and never calls saveLrc", async () => {
    const onClose = vi.fn();
    render(CreateLyricsDialog, { props: { track, onCreated: vi.fn(), onClose } });

    await fireEvent.click(screen.getByText("Cancel"));

    expect(onClose).toHaveBeenCalled();
    expect(saveLrc).not.toHaveBeenCalled();
  });

  it("stays open when the backdrop is clicked", async () => {
    const onClose = vi.fn();
    const { container } = render(CreateLyricsDialog, { props: { track, onCreated: vi.fn(), onClose } });

    await fireEvent.click(container.querySelector(".process-dialog-overlay")!);

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("calls onClose on Escape", async () => {
    const onClose = vi.fn();
    render(CreateLyricsDialog, { props: { track, onCreated: vi.fn(), onClose } });

    await fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });
});
