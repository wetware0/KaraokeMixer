import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import TrackRow from "./TrackRow.svelte";
import type { Track } from "../types";
import { defaultLibraryColumnsState, visibleColumnsInOrder } from "../libraryColumns";

// Only the create-lyrics flow (added in this task) calls the API; every
// other TrackRow test in this file never touches it. clearAllMocks (not
// restoreAllMocks) between tests keeps this mockResolvedValue configured
// for every test rather than wiping it after the first - same reasoning as
// LyricEditor.test.ts's module-level vi.mock.
vi.mock("../api", () => ({
  artworkUrl: vi.fn((id: number) => `/api/tracks/${id}/artwork`),
  saveLrc: vi.fn().mockResolvedValue({ path: "D:/Media/Song.lrc" }),
  deleteTrack: vi.fn().mockResolvedValue({ track_id: 1, moved_to_recycle_bin: [] }),
}));

import { saveLrc } from "../api";

function track(overrides: Partial<Track> = {}): Track {
  return {
    id: 1,
    media_root: "D:/Media",
    relative_path: "Song.flac",
    artist: "ABBA",
    title: "Dancing Queen",
    outputs: {
      instrumental: false,
      vocals: false,
      lead_vocals: false,
      backing_vocals: false,
      drums: false,
      bass: false,
      guitar: false,
      piano: false,
      other: false,
      lrc: false,
    },
    lrc_state: null,
    stem_count: 0,
    album: null,
    year: null,
    duration_seconds: null,
    ...overrides,
  };
}

function defaultColumns() {
  return visibleColumnsInOrder(defaultLibraryColumnsState());
}

afterEach(() => vi.clearAllMocks());

describe("TrackRow", () => {
  it("shows a lazy artwork thumbnail and a quiet fallback when none is embedded", async () => {
    render(TrackRow, { props: { track: track(), columns: defaultColumns() } });
    const image = screen.getByAltText("Dancing Queen artwork") as HTMLImageElement;
    expect(image.src).toContain("/api/tracks/1/artwork");
    expect(image.getAttribute("loading")).toBe("lazy");

    await fireEvent(image, new Event("error"));
    expect(screen.getByLabelText("No artwork for Dancing Queen")).toBeTruthy();
  });

  it("retries artwork with a fresh cache key when the track revision changes", async () => {
    const view = render(TrackRow, { props: { track: track(), columns: defaultColumns(), revision: 3 } });
    const image = screen.getByAltText("Dancing Queen artwork") as HTMLImageElement;
    expect(image.getAttribute("src")).toBe("/api/tracks/1/artwork?v=3");
    await fireEvent(image, new Event("error"));
    expect(screen.getByLabelText("No artwork for Dancing Queen")).toBeTruthy();

    await view.rerender({ track: track(), columns: defaultColumns(), revision: 4 });

    const retried = await screen.findByAltText("Dancing Queen artwork") as HTMLImageElement;
    expect(retried.getAttribute("src")).toBe("/api/tracks/1/artwork?v=4");
    expect(retried).not.toBe(image);
  });

  it("shows title, artist, and separate missing-output columns", () => {
    render(TrackRow, { props: { track: track(), columns: defaultColumns() } });

    expect(screen.getByText("Dancing Queen")).toBeTruthy();
    expect(screen.getByText("ABBA")).toBeTruthy();
    expect(screen.getByText("0 stems")).toBeTruthy();
    expect(screen.getAllByText("Missing")).toHaveLength(2);
  });

  it("offers a direct tag editor action", async () => {
    const onEditTags = vi.fn();
    render(TrackRow, { props: { track: track(), columns: defaultColumns(), onEditTags } });

    await fireEvent.click(screen.getByRole("button", { name: "Tags" }));

    expect(onEditTags).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));
  });

  it("opens a confirmation before deleting and disables delete while processing", async () => {
    const onRequestDelete = vi.fn();
    const view = render(TrackRow, { props: { track: track(), columns: defaultColumns(), onRequestDelete } });

    await fireEvent.click(screen.getByRole("button", { name: "Delete…" }));
    expect(onRequestDelete).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));

    await view.rerender({ track: track(), columns: defaultColumns(), onRequestDelete, processingStatus: "running" });
    expect(screen.getByRole("button", { name: "Delete…" })).toBeDisabled();
  });

  it("disables delete during a rescan", async () => {
    const view = render(TrackRow, {
      props: { track: track(), columns: defaultColumns() },
    });
    await view.rerender({
      track: track(), columns: defaultColumns(),
      deleteDisabledReason: "Wait for the library rescan to finish before deleting",
    });
    const button = screen.getByRole("button", { name: "Delete…" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Wait for the library rescan to finish before deleting");
  });

  it("shows instrumental, lyric timing, and stem values in their own columns", () => {
    render(TrackRow, {
      props: {
        track: track({
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
        }),
        columns: defaultColumns(),
      },
    });

    expect(screen.getByText("Ready")).toBeTruthy();
    expect(screen.getByText("Enhanced")).toBeTruthy();
    expect(screen.getByText("1 stem")).toBeTruthy();
  });

  it("renders an empty artist cell when the artist is unknown", () => {
    // Artist used to be a fixed cell with its own "Unknown artist" fallback;
    // now it's rendered via the shared displayValue() (see libraryColumns.ts),
    // which maps a null artist to "" - consistent with how every other
    // missing column value (album, year, duration) displays blank rather
    // than a bespoke per-field label.
    const { container } = render(TrackRow, {
      props: { track: track({ artist: null }), columns: defaultColumns() },
    });
    const artistCell = container.querySelector(".track-row-cell-artist");
    expect(artistCell?.textContent).toBe("");
  });

  it("calls onToggle when the selection checkbox is clicked", async () => {
    const { fireEvent } = await import("@testing-library/svelte");
    const onToggle = vi.fn();
    render(TrackRow, { props: { track: track(), selected: false, onToggle, columns: defaultColumns() } });

    await fireEvent.click(screen.getByRole("checkbox"));

    expect(onToggle).toHaveBeenCalled();
  });
});

describe("TrackRow navigation", () => {
  it("calls onOpenMixer with the track on double-click", async () => {
    const { fireEvent } = await import("@testing-library/svelte");
    const onOpenMixer = vi.fn();
    const t = track();
    render(TrackRow, { props: { track: t, onOpenMixer, columns: defaultColumns() } });

    await fireEvent.dblClick(screen.getByText("Dancing Queen"));

    expect(onOpenMixer).toHaveBeenCalledWith(t);
  });

  it("shows an Edit lyrics button only when lrc_state is set, and it calls onOpenEditor without also triggering onOpenMixer", async () => {
    const { fireEvent } = await import("@testing-library/svelte");
    const onOpenEditor = vi.fn();
    const onOpenMixer = vi.fn();
    const t = track({ lrc_state: "enhanced" });
    render(TrackRow, { props: { track: t, onOpenEditor, onOpenMixer, columns: defaultColumns() } });

    const editButton = screen.getByText("Edit lyrics");
    await fireEvent.click(editButton);

    expect(onOpenEditor).toHaveBeenCalledWith(t);
    expect(onOpenMixer).not.toHaveBeenCalled();
  });

  it("does not show Edit lyrics when there is no lrc", () => {
    render(TrackRow, { props: { track: track({ lrc_state: null }), columns: defaultColumns() } });
    expect(screen.queryByText("Edit lyrics")).toBeNull();
  });

  it("shows an explicit Mixer button regardless of lrc state, and clicking it calls onOpenMixer without onOpenEditor", async () => {
    const { fireEvent } = await import("@testing-library/svelte");
    const onOpenMixer = vi.fn();
    const onOpenEditor = vi.fn();
    const t = track({ lrc_state: null });
    render(TrackRow, { props: { track: t, onOpenMixer, onOpenEditor, columns: defaultColumns() } });

    const mixerButton = screen.getByText("Mixer");
    await fireEvent.click(mixerButton);

    expect(onOpenMixer).toHaveBeenCalledWith(t);
    expect(onOpenEditor).not.toHaveBeenCalled();
  });
});

describe("TrackRow keyboard accessibility", () => {
  it("the row is keyboard-focusable with native row semantics and tabindex 0", () => {
    const { container } = render(TrackRow, { props: { track: track(), columns: defaultColumns() } });
    const row = container.querySelector("tr.track-row") as HTMLElement;
    expect(row.getAttribute("role")).toBeNull();
    expect(row.getAttribute("tabindex")).toBe("0");
  });

  it("pressing Enter on the row calls onOpenEditor with the track", async () => {
    const onOpenEditor = vi.fn();
    const t = track();
    const { container } = render(TrackRow, { props: { track: t, onOpenEditor, columns: defaultColumns() } });
    const row = container.querySelector(".track-row") as HTMLElement;

    await fireEvent.keyDown(row, { key: "Enter" });

    expect(onOpenEditor).toHaveBeenCalledWith(t);
  });

  it("pressing Enter on the row does not also trigger onOpenMixer", async () => {
    const onOpenMixer = vi.fn();
    const { container } = render(TrackRow, { props: { track: track(), onOpenMixer, columns: defaultColumns() } });
    const row = container.querySelector(".track-row") as HTMLElement;

    await fireEvent.keyDown(row, { key: "Enter" });

    expect(onOpenMixer).not.toHaveBeenCalled();
  });

  it("pressing Enter on the nested Create lyrics button does not hijack navigation to the editor", async () => {
    const onOpenEditor = vi.fn();
    render(TrackRow, { props: { track: track({ lrc_state: null }), onOpenEditor, columns: defaultColumns() } });

    const createButton = screen.getByText("Create lyrics");
    const notPrevented = await fireEvent.keyDown(createButton, { key: "Enter" });

    expect(onOpenEditor).not.toHaveBeenCalled();
    expect(notPrevented).toBe(true);
  });

  it("pressing Enter on the Mixer button does not trigger row navigation (onOpenEditor)", async () => {
    const onOpenMixer = vi.fn();
    const onOpenEditor = vi.fn();
    render(TrackRow, { props: { track: track(), onOpenMixer, onOpenEditor, columns: defaultColumns() } });

    const mixerButton = screen.getByText("Mixer");
    // As with the preview button above, jsdom's keydown doesn't synthesize
    // the browser's native button-activation-on-Enter behavior; what matters
    // here is that the row's keydown guard (event.target !== currentTarget)
    // doesn't hijack the bubbling keydown into opening the editor.
    const notPrevented = await fireEvent.keyDown(mixerButton, { key: "Enter" });

    expect(notPrevented).toBe(true);
    expect(onOpenEditor).not.toHaveBeenCalled();
  });
});

describe("TrackRow preview button", () => {
  it("shows a Play preview button that is not the Pause state by default", () => {
    render(TrackRow, { props: { track: track(), columns: defaultColumns() } });

    const button = screen.getByLabelText("Play preview") as HTMLButtonElement;
    expect(button).toBeTruthy();
    expect(screen.queryByLabelText("Pause preview")).toBeNull();
  });

  it("shows Pause preview and a playing indicator when previewing is true", () => {
    const { container } = render(TrackRow, {
      props: { track: track(), previewing: true, columns: defaultColumns() },
    });

    expect(screen.getByLabelText("Pause preview")).toBeTruthy();
    expect(screen.queryByLabelText("Play preview")).toBeNull();
    const row = container.querySelector(".track-row") as HTMLElement;
    expect(row.classList.contains("track-row-playing")).toBe(true);
  });

  it("calls onTogglePreview with the track when the preview button is clicked, without opening the mixer", async () => {
    const onTogglePreview = vi.fn();
    const onOpenMixer = vi.fn();
    const t = track();
    render(TrackRow, { props: { track: t, onTogglePreview, onOpenMixer, columns: defaultColumns() } });

    await fireEvent.click(screen.getByLabelText("Play preview"));

    expect(onTogglePreview).toHaveBeenCalledWith(t);
    expect(onOpenMixer).not.toHaveBeenCalled();
  });

  it("pressing Enter on the preview button toggles preview and does not open the editor", async () => {
    const onTogglePreview = vi.fn();
    const onOpenEditor = vi.fn();
    const t = track();
    render(TrackRow, { props: { track: t, onTogglePreview, onOpenEditor, columns: defaultColumns() } });

    const button = screen.getByLabelText("Play preview");
    // jsdom doesn't synthesize the browser's native "Enter activates a
    // focused button" behavior (that's real-browser default action, not
    // something a dispatched keydown triggers on its own), so the keydown
    // followed by a click stands in for what a browser actually fires. The
    // assertion that matters is that the row's keydown handler (bound
    // above, guarded by event.target !== event.currentTarget) doesn't
    // hijack this into opening the editor as the event bubbles past it -
    // mirroring the existing "nested Create lyrics button" test.
    const notPrevented = await fireEvent.keyDown(button, { key: "Enter" });
    await fireEvent.click(button);

    expect(notPrevented).toBe(true);
    expect(onTogglePreview).toHaveBeenCalledWith(t);
    expect(onOpenEditor).not.toHaveBeenCalled();
  });
});

describe("TrackRow processing state", () => {
  it("marks a queued track with a visible label and queued row treatment", () => {
    const { container } = render(TrackRow, {
      props: { track: track(), processingStatus: "queued", columns: defaultColumns() },
    });

    expect(container.querySelector(".track-row")?.classList.contains("track-row-queued")).toBe(true);
    expect(screen.getByText("Queued")).toBeTruthy();
  });

  it("marks a running track with a visible label and stronger running treatment", () => {
    const { container } = render(TrackRow, {
      props: { track: track(), processingStatus: "running", columns: defaultColumns() },
    });

    expect(container.querySelector(".track-row")?.classList.contains("track-row-running")).toBe(true);
    expect(screen.getByText("Processing")).toBeTruthy();
  });

  it("keeps a completed-phase track visibly waiting for the next phase", () => {
    const { container } = render(TrackRow, {
      props: { track: track(), processingStatus: "waiting", columns: defaultColumns() },
    });

    expect(container.querySelector(".track-row")?.classList.contains("track-row-waiting")).toBe(true);
    expect(screen.getByText("Waiting for next phase")).toBeTruthy();
  });

  it("marks a failed track with its actionable error available on the badge", () => {
    const { container } = render(TrackRow, {
      props: {
        track: track(),
        processingStatus: "failed",
        processingError: "Creating karaoke instrumental: Surround audio could not be processed",
        columns: defaultColumns(),
      },
    });

    expect(container.querySelector(".track-row")?.classList.contains("track-row-failed")).toBe(true);
    expect(screen.getByLabelText(/Processing error: Creating karaoke instrumental/)).toBeTruthy();
  });
});

describe("TrackRow create-lyrics flow", () => {
  it("shows a Create lyrics button when there is no lrc, and not an Edit lyrics button", () => {
    render(TrackRow, { props: { track: track({ lrc_state: null }), columns: defaultColumns() } });
    expect(screen.getByText("Create lyrics")).toBeTruthy();
    expect(screen.queryByText("Edit lyrics")).toBeNull();
  });

  it("does not show Create lyrics once an lrc exists", () => {
    render(TrackRow, { props: { track: track({ lrc_state: "untimed" }), columns: defaultColumns() } });
    expect(screen.queryByText("Create lyrics")).toBeNull();
  });

  it("clicking Create lyrics opens the dialog without triggering onOpenMixer", async () => {
    const onOpenMixer = vi.fn();
    render(TrackRow, {
      props: { track: track({ lrc_state: null }), onOpenMixer, columns: defaultColumns() },
    });

    await fireEvent.click(screen.getByText("Create lyrics"));

    expect(screen.getByText(/Create lyrics for/)).toBeTruthy();
    expect(onOpenMixer).not.toHaveBeenCalled();
  });

  it("navigates to the editor via onOpenEditor once lyrics are successfully created", async () => {
    const onOpenEditor = vi.fn();
    const t = track({ lrc_state: null });
    render(TrackRow, { props: { track: t, onOpenEditor, columns: defaultColumns() } });

    await fireEvent.click(screen.getByText("Create lyrics"));
    await fireEvent.input(screen.getByPlaceholderText("Paste plain lyric lines…"), {
      target: { value: "Hello world" },
    });
    await fireEvent.click(screen.getByText("Create"));

    await waitFor(() => expect(onOpenEditor).toHaveBeenCalledWith(t));
    expect(saveLrc).toHaveBeenCalledWith(t.id, "Hello world", { create: "beside" });
  });
});

describe("TrackRow configurable columns", () => {
  it("renders one cell per visible column, in order, using displayValue", () => {
    const t = track({ album: "Arrival", year: 1976, duration_seconds: 65 });
    const { container } = render(TrackRow, { props: { track: t, columns: defaultColumns() } });

    const cells = container.querySelectorAll(".track-row-cell");
    const cellTexts = [...cells].map((cell) => cell.textContent);
    // Source provenance and core track facts are visible; Album remains optional.
    expect(cellTexts).toEqual([
      "", "D:/Media", "Song.flac", "ABBA", "Dancing Queen", "Missing", "Missing", "0 stems", "1976", "1:05",
    ]);
  });

  it("omits a cell for a column not present in the columns prop (hidden column)", () => {
    const t = track({ album: "Arrival" });
    const columns = defaultColumns().filter((column) => column.key !== "album");

    const { container } = render(TrackRow, { props: { track: t, columns } });

    expect(container.querySelectorAll(".track-row-cell")).toHaveLength(columns.length);
    expect(screen.queryByText("Arrival")).toBeNull();
  });

  it("renders as a table row element", () => {
    const { container } = render(TrackRow, { props: { track: track(), columns: defaultColumns() } });
    expect(container.querySelector("tr.track-row")).toBeTruthy();
  });
});
