import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LibraryFolder, Track } from "../types";

vi.mock("../api", () => ({
  createLibraryFolder: vi.fn(),
  renameLibraryFolder: vi.fn(),
  deleteLibraryFolder: vi.fn(),
  moveTrack: vi.fn(),
}));

import { createLibraryFolder, deleteLibraryFolder, moveTrack, renameLibraryFolder } from "../api";
import DeleteFolderDialog from "./DeleteFolderDialog.svelte";
import FolderDialog from "./FolderDialog.svelte";
import RenameTrackDialog from "./RenameTrackDialog.svelte";

const root: LibraryFolder = { path: "D:/Media", media_root: "D:/Media", relative_path: "", name: "D:/Media" };
const artist: LibraryFolder = { path: "D:/Media/Artist", media_root: "D:/Media", relative_path: "Artist", name: "Artist" };
const track: Track = {
  id: 4,
  media_root: "D:/Media",
  relative_path: "Artist/Old.flac",
  artist: "Artist",
  title: "Old",
  outputs: {
    instrumental: true, vocals: true, lead_vocals: false, backing_vocals: false,
    drums: false, bass: false, guitar: false, piano: false, other: false, lrc: true,
  },
  lrc_state: "enhanced",
  stem_count: 1,
  album: null,
  year: null,
  duration_seconds: 180,
};

afterEach(() => vi.clearAllMocks());

describe("media location dialogs", () => {
  it("creates a folder in the chosen parent and ignores backdrop clicks", async () => {
    vi.mocked(createLibraryFolder).mockResolvedValue(artist);
    const onSaved = vi.fn();
    const onClose = vi.fn();
    render(FolderDialog, { props: { mode: "create", folders: [root], defaultParent: root.path, onSaved, onClose } });

    await fireEvent.click(screen.getByRole("dialog", { name: "Create folder" }));
    expect(onClose).not.toHaveBeenCalled();
    await fireEvent.input(screen.getByLabelText("Folder name"), { target: { value: "Artist" } });
    await fireEvent.click(screen.getByRole("button", { name: "Create folder" }));

    await waitFor(() => expect(createLibraryFolder).toHaveBeenCalledWith(root.path, "Artist"));
    expect(onSaved).toHaveBeenCalledWith(artist);
  });

  it("renames a folder without changing its parent", async () => {
    const renamed = { ...artist, path: "D:/Media/Singer", relative_path: "Singer", name: "Singer" };
    vi.mocked(renameLibraryFolder).mockResolvedValue(renamed);
    render(FolderDialog, { props: { mode: "rename", folder: artist, folders: [root, artist] } });

    const input = screen.getByLabelText("Folder name");
    await waitFor(() => expect(input).toHaveValue("Artist"));
    await fireEvent.input(input, { target: { value: "Singer" } });
    await fireEvent.click(screen.getByRole("button", { name: "Rename folder" }));

    await waitFor(() => expect(renameLibraryFolder).toHaveBeenCalledWith(artist.path, "Singer"));
  });

  it("moves and renames a track together while keeping the extension fixed", async () => {
    const moved = { ...track, relative_path: "Old.flac" };
    vi.mocked(moveTrack).mockResolvedValue(moved);
    render(RenameTrackDialog, {
      props: { track, currentFolder: artist.path, folders: [root, artist] },
    });

    const filenameInput = screen.getByLabelText("Filename");
    await waitFor(() => expect(filenameInput).toHaveValue("Old"));
    expect(screen.getByText(".flac")).toBeTruthy();
    await fireEvent.change(screen.getByLabelText("Location"), { target: { value: root.path } });
    await fireEvent.input(filenameInput, { target: { value: "New" } });
    await fireEvent.click(screen.getByRole("button", { name: "Move / rename" }));

    await waitFor(() => expect(moveTrack).toHaveBeenCalledWith(track.id, root.path, "New"));
  });

  it("requires explicit confirmation before recycling a folder", async () => {
    vi.mocked(deleteLibraryFolder).mockResolvedValue({ deleted_track_ids: [4], moved_to_recycle_bin: [artist.path] });
    const onDeleted = vi.fn();
    const onClose = vi.fn();
    render(DeleteFolderDialog, { props: { folder: artist, trackCount: 1, onDeleted, onClose } });

    await fireEvent.click(screen.getByRole("dialog", { name: "Move folder to Recycle Bin?" }));
    expect(onClose).not.toHaveBeenCalled();
    expect(deleteLibraryFolder).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole("button", { name: "Move to Recycle Bin" }));

    await waitFor(() => expect(deleteLibraryFolder).toHaveBeenCalledWith(artist.path));
    expect(onDeleted).toHaveBeenCalledWith([4]);
  });
});

