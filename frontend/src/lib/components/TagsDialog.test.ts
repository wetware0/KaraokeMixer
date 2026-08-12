import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Track } from "../types";

vi.mock("../api", () => ({
  artworkUrl: vi.fn((id: number) => `/api/tracks/${id}/artwork`),
  fetchTagSuggestion: vi.fn(),
  saveTrackTags: vi.fn(),
  uploadTrackArtwork: vi.fn(),
}));

import { fetchTagSuggestion, saveTrackTags, uploadTrackArtwork } from "../api";
import TagsDialog from "./TagsDialog.svelte";

function track(overrides: Partial<Track> = {}): Track {
  return {
    id: 1,
    media_root: "D:/Media",
    relative_path: "Song.flac",
    artist: "ABBA",
    title: "Dancing Queen",
    outputs: {
      instrumental: false, vocals: false, lead_vocals: false, backing_vocals: false,
      drums: false, bass: false, guitar: false, piano: false, other: false, lrc: false,
    },
    lrc_state: null,
    stem_count: 0,
    album: "Arrival",
    year: 1976,
    duration_seconds: 213,
    ...overrides,
  };
}

afterEach(() => vi.clearAllMocks());

describe("TagsDialog", () => {
  it("prefills the track fields and shows the embedded artwork endpoint", () => {
    render(TagsDialog, { props: { track: track(), onSaved: vi.fn(), onClose: vi.fn() } });

    expect((screen.getByLabelText("Artist") as HTMLInputElement).value).toBe("ABBA");
    expect((screen.getByLabelText(/Title/) as HTMLInputElement).value).toBe("Dancing Queen");
    expect((screen.getByLabelText("Album") as HTMLInputElement).value).toBe("Arrival");
    expect((screen.getByLabelText("Year") as HTMLInputElement).value).toBe("1976");
    expect((screen.getByAltText("Cover artwork") as HTMLImageElement).src).toContain("/api/tracks/1/artwork");
  });

  it("shows an empty-artwork state when the artwork endpoint has no image", async () => {
    render(TagsDialog, { props: { track: track(), onSaved: vi.fn(), onClose: vi.fn() } });
    await fireEvent(screen.getByAltText("Cover artwork"), new Event("error"));
    expect(screen.getByLabelText("No artwork")).toBeTruthy();
  });

  it("saves normalized fields and returns the fresh backend track", async () => {
    const updated = track({ artist: "New Artist", title: "New Title", album: null, year: 2001 });
    vi.mocked(saveTrackTags).mockResolvedValue(updated);
    const onSaved = vi.fn();
    render(TagsDialog, { props: { track: track(), onSaved, onClose: vi.fn() } });

    await fireEvent.input(screen.getByLabelText("Artist"), { target: { value: " New Artist " } });
    await fireEvent.input(screen.getByLabelText(/Title/), { target: { value: "New Title" } });
    await fireEvent.input(screen.getByLabelText("Album"), { target: { value: "" } });
    await fireEvent.input(screen.getByLabelText("Year"), { target: { value: "2001" } });
    await fireEvent.click(screen.getByText("Save changes"));

    await waitFor(() => expect(saveTrackTags).toHaveBeenCalledWith(1, {
      artist: "New Artist", title: "New Title", album: null, year: 2001,
    }));
    expect(onSaved).toHaveBeenCalledWith(updated);
  });

  it("uploads selected JPEG bytes before saving fields", async () => {
    vi.mocked(uploadTrackArtwork).mockResolvedValue(undefined);
    vi.mocked(saveTrackTags).mockResolvedValue(track());
    const file = new File(["bytes"], "cover.jpg", { type: "image/jpeg" });
    render(TagsDialog, { props: { track: track(), onSaved: vi.fn(), onClose: vi.fn() } });

    await fireEvent.change(screen.getByLabelText("Replace artwork"), { target: { files: [file] } });
    expect(screen.getByText("Selected: cover.jpg")).toBeTruthy();
    await fireEvent.click(screen.getByText("Save changes"));

    await waitFor(() => expect(uploadTrackArtwork).toHaveBeenCalledWith(1, file));
    expect(vi.mocked(uploadTrackArtwork).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(saveTrackTags).mock.invocationCallOrder[0]);
  });

  it("rejects a non-image selection before upload", async () => {
    const file = new File(["not art"], "notes.txt", { type: "text/plain" });
    render(TagsDialog, { props: { track: track(), onSaved: vi.fn(), onClose: vi.fn() } });

    await fireEvent.change(screen.getByLabelText("Replace artwork"), { target: { files: [file] } });

    expect(screen.getByRole("alert").textContent).toContain("JPEG or PNG");
    expect(uploadTrackArtwork).not.toHaveBeenCalled();
  });

  it("keeps the dialog open and surfaces backend validation errors", async () => {
    vi.mocked(saveTrackTags).mockRejectedValue(new Error("title is required"));
    const onClose = vi.fn();
    render(TagsDialog, { props: { track: track(), onSaved: vi.fn(), onClose } });
    await fireEvent.click(screen.getByText("Save changes"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("title is required"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("stages auto-corrected tags for review without saving them", async () => {
    vi.mocked(fetchTagSuggestion).mockResolvedValue({
      artist: "ABBA", title: "Dancing Queen", album: "Arrival", year: 1976,
      provider: "itunes", artwork_data_url: null,
    });
    render(TagsDialog, { props: { track: track({ artist: "Abba", title: "dancing queen" }), onSaved: vi.fn(), onClose: vi.fn() } });

    await fireEvent.click(screen.getByText("Auto-correct tags"));
    await waitFor(() => expect((screen.getByLabelText("Artist") as HTMLInputElement).value).toBe("ABBA"));
    expect((screen.getByLabelText(/Title/) as HTMLInputElement).value).toBe("Dancing Queen");
    expect(screen.getByText(/Review the corrected fields/)).toBeTruthy();
    expect(saveTrackTags).not.toHaveBeenCalled();
  });

  it("stages fetched artwork for preview and only uploads it on Save", async () => {
    vi.mocked(fetchTagSuggestion).mockResolvedValue({
      artist: "ABBA", title: "Dancing Queen", album: "Arrival", year: 1976,
      provider: "itunes", artwork_data_url: "data:image/jpeg;base64,/9jg",
    });
    vi.mocked(saveTrackTags).mockResolvedValue(track());
    render(TagsDialog, { props: { track: track(), onSaved: vi.fn(), onClose: vi.fn() } });

    await fireEvent.click(screen.getByText("Fetch artwork"));
    await waitFor(() => expect((screen.getByAltText("Cover artwork") as HTMLImageElement).src).toContain("data:image/jpeg;base64,/9jg"));
    expect(uploadTrackArtwork).not.toHaveBeenCalled();

    await fireEvent.click(screen.getByText("Save changes"));
    await waitFor(() => expect(uploadTrackArtwork).toHaveBeenCalledWith(1, expect.any(File)));
  });

  it("stays open when the backdrop is clicked", async () => {
    const onClose = vi.fn();
    const { container } = render(TagsDialog, { props: { track: track(), onSaved: vi.fn(), onClose } });

    await fireEvent.click(container.querySelector(".process-dialog-overlay")!);

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Fix tags & artwork" })).toBeTruthy();
  });

  it("validates the year before writing and closes on Escape", async () => {
    const onClose = vi.fn();
    render(TagsDialog, { props: { track: track(), onSaved: vi.fn(), onClose } });
    await fireEvent.input(screen.getByLabelText("Year"), { target: { value: "76" } });
    await fireEvent.click(screen.getByText("Save changes"));
    expect(await screen.findByText(/Year must be between 1860 and/)).toBeTruthy();
    expect(saveTrackTags).not.toHaveBeenCalled();

    await fireEvent.keyDown(screen.getByRole("dialog", { name: "Fix tags & artwork" }), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
