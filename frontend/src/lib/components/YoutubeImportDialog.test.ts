import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  fetchRecipes: vi.fn(),
  probeYoutube: vi.fn(),
  importFromYoutube: vi.fn(),
}));

import { fetchRecipes, importFromYoutube, probeYoutube } from "../api";
import YoutubeImportDialog from "./YoutubeImportDialog.svelte";

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

const URL_PLACEHOLDER = "https://www.youtube.com/watch?v=...";

describe("YoutubeImportDialog", () => {
  it("prefills artist and title from the probe when the URL field loses focus", async () => {
    vi.mocked(fetchRecipes).mockResolvedValue([]);
    vi.mocked(probeYoutube).mockResolvedValue({ is_playlist: false, title: "Chiquitita", duration: 218, uploader: "ABBA" });

    render(YoutubeImportDialog, { props: { onClose: vi.fn() } });
    await fireEvent.input(screen.getByPlaceholderText(URL_PLACEHOLDER), {
      target: { value: "https://youtube.com/watch?v=abc" },
    });
    await fireEvent.blur(screen.getByPlaceholderText(URL_PLACEHOLDER));

    await waitFor(() => expect((screen.getByLabelText("Artist") as HTMLInputElement).value).toBe("ABBA"));
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Chiquitita");
  });

  it("submits the import request with the entered url/artist/title and closes on success", async () => {
    vi.mocked(fetchRecipes).mockResolvedValue([]);
    vi.mocked(importFromYoutube).mockResolvedValue({ job_id: 5 });
    const onClose = vi.fn();

    render(YoutubeImportDialog, { props: { onClose } });
    await fireEvent.input(screen.getByPlaceholderText(URL_PLACEHOLDER), {
      target: { value: "https://youtube.com/watch?v=abc" },
    });
    await fireEvent.click(screen.getByText("Import track"));

    await waitFor(() =>
      expect(importFromYoutube).toHaveBeenCalledWith({
        url: "https://youtube.com/watch?v=abc", artist: undefined, title: undefined, process_after: undefined,
      })
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("includes process_after when the checkbox is checked and a recipe is selected", async () => {
    vi.mocked(fetchRecipes).mockResolvedValue([{ name: "karaoke", lane: "gpu", options_schema: null }]);
    vi.mocked(importFromYoutube).mockResolvedValue({ job_id: 6 });

    render(YoutubeImportDialog, { props: { onClose: vi.fn() } });
    await fireEvent.input(screen.getByPlaceholderText(URL_PLACEHOLDER), {
      target: { value: "https://youtube.com/watch?v=abc" },
    });
    // The recipe <select> (and its "karaoke" option) only renders once
    // "Process after import" is checked - check the checkbox first, THEN
    // wait for "karaoke" to appear, not the other way around.
    await fireEvent.click(screen.getByLabelText("Process after import"));
    await waitFor(() => expect(screen.getByText("Karaoke instrumental")).toBeTruthy());
    await fireEvent.click(screen.getByText("Import track"));

    await waitFor(() =>
      expect(importFromYoutube).toHaveBeenCalledWith(
        expect.objectContaining({ process_after: { recipe: "karaoke", options: {} } })
      )
    );
  });

  it("shows the backend's detail message when the import request fails, and does not close", async () => {
    // Exercises the REAL importFromYoutube (not a mocked rejection) against a
    // mocked fetch, so this proves api.ts's error-detail extraction actually
    // reaches the dialog - a generic "500" status message would hide the
    // actionable guidance (e.g. "Configure a downloads root").
    vi.mocked(fetchRecipes).mockResolvedValue([]);
    const actualApi = await vi.importActual<typeof import("../api")>("../api");
    vi.mocked(importFromYoutube).mockImplementation(actualApi.importFromYoutube);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: "Configure a downloads root" }),
      } as Response)
    );
    const onClose = vi.fn();

    render(YoutubeImportDialog, { props: { onClose } });
    await fireEvent.input(screen.getByPlaceholderText(URL_PLACEHOLDER), {
      target: { value: "https://youtube.com/watch?v=abc" },
    });
    await fireEvent.click(screen.getByText("Import track"));

    await waitFor(() => expect(screen.getByText("Configure a downloads root")).toBeTruthy());
    expect(onClose).not.toHaveBeenCalled();
  });

  it("disables Import until a URL is entered", async () => {
    vi.mocked(fetchRecipes).mockResolvedValue([]);

    render(YoutubeImportDialog, { props: { onClose: vi.fn() } });

    expect((screen.getByText("Import track") as HTMLButtonElement).disabled).toBe(true);
  });

  it("closes when Escape is pressed", async () => {
    vi.mocked(fetchRecipes).mockResolvedValue([]);
    const onClose = vi.fn();

    render(YoutubeImportDialog, { props: { onClose } });
    await fireEvent.keyDown(screen.getByRole("dialog", { name: "Import from YouTube" }), { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });

  it("stays open when the backdrop is clicked", async () => {
    vi.mocked(fetchRecipes).mockResolvedValue([]);
    const onClose = vi.fn();
    const { container } = render(YoutubeImportDialog, { props: { onClose } });

    await fireEvent.click(container.querySelector(".process-dialog-overlay")!);

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Import from YouTube" })).toBeTruthy();
  });
});

describe("YoutubeImportDialog playlist import", () => {
  const playlistEntries = [
    { url: "https://youtu.be/vid1", title: "Song One", duration: 125 },
    { url: "https://youtu.be/vid2", title: "Song Two", duration: 65 },
  ];

  async function renderPlaylist(total = 2) {
    vi.mocked(fetchRecipes).mockResolvedValue([]);
    vi.mocked(probeYoutube).mockResolvedValue({
      is_playlist: true, entries: playlistEntries, count: playlistEntries.length, total,
    });
    render(YoutubeImportDialog, { props: { onClose: vi.fn() } });
    const input = screen.getByPlaceholderText(URL_PLACEHOLDER);
    await fireEvent.input(input, { target: { value: "https://youtube.com/playlist?list=abc" } });
    await fireEvent.blur(input);
    await waitFor(() => expect(screen.getByText("Song One")).toBeTruthy());
  }

  it("shows playlist entries checked by default and reports a truncated total", async () => {
    await renderPlaylist(250);

    expect(screen.getByText("Song Two")).toBeTruthy();
    expect(screen.getByText("Showing the first 2 of 250 videos")).toBeTruthy();
    expect((screen.getByLabelText("Import Song One") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Import Song Two") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("Import 2 selected")).toBeTruthy();
  });

  it("select-all toggles the complete detected set", async () => {
    await renderPlaylist();

    await fireEvent.click(screen.getByLabelText("Select all"));
    expect((screen.getByLabelText("Import Song One") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByText("Import 0 selected") as HTMLButtonElement).disabled).toBe(true);

    await fireEvent.click(screen.getByLabelText("Select all"));
    expect((screen.getByLabelText("Import Song Two") as HTMLInputElement).checked).toBe(true);
  });

  it("starts one import job per checked entry and closes after all succeed", async () => {
    vi.mocked(fetchRecipes).mockResolvedValue([]);
    vi.mocked(probeYoutube).mockResolvedValue({ is_playlist: true, entries: playlistEntries, count: 2, total: 2 });
    vi.mocked(importFromYoutube).mockResolvedValue({ job_id: 1 });
    const onClose = vi.fn();
    render(YoutubeImportDialog, { props: { onClose } });
    const input = screen.getByPlaceholderText(URL_PLACEHOLDER);
    await fireEvent.input(input, { target: { value: "https://youtube.com/playlist?list=abc" } });
    await fireEvent.blur(input);
    await waitFor(() => expect(screen.getByText("Song One")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Import Song Two"));
    await fireEvent.click(screen.getByText("Import 1 selected"));

    await waitFor(() => expect(importFromYoutube).toHaveBeenCalledTimes(1));
    expect(importFromYoutube).toHaveBeenCalledWith(expect.objectContaining({
      url: "https://youtu.be/vid1", title: "Song One",
    }));
    expect(onClose).toHaveBeenCalled();
  });

  it("reports partial failures without hiding already-started work", async () => {
    vi.mocked(fetchRecipes).mockResolvedValue([]);
    vi.mocked(probeYoutube).mockResolvedValue({ is_playlist: true, entries: playlistEntries, count: 2, total: 2 });
    vi.mocked(importFromYoutube)
      .mockResolvedValueOnce({ job_id: 1 })
      .mockRejectedValueOnce(new Error("Video unavailable"));
    const onClose = vi.fn();
    render(YoutubeImportDialog, { props: { onClose } });
    const input = screen.getByPlaceholderText(URL_PLACEHOLDER);
    await fireEvent.input(input, { target: { value: "https://youtube.com/playlist?list=abc" } });
    await fireEvent.blur(input);
    await waitFor(() => expect(screen.getByText("Song One")).toBeTruthy());
    await fireEvent.click(screen.getByText("Import 2 selected"));

    expect(await screen.findByText("1 import started; 1 failed. Video unavailable")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });
});
