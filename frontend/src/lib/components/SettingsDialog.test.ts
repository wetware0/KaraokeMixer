import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  browseForFolder: vi.fn(),
  fetchSettings: vi.fn(),
  fetchRescanStatus: vi.fn(),
  updateSettings: vi.fn(),
  rescan: vi.fn(),
}));

import { browseForFolder, fetchSettings, rescan, updateSettings } from "../api";
import SettingsDialog from "./SettingsDialog.svelte";
import { THEME_STORAGE_KEY } from "../theme";

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("SettingsDialog", () => {
  it("applies and persists System, Light, and Dark appearance choices", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });
    render(SettingsDialog, { props: { onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByLabelText("Theme")).toBeTruthy());

    await fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "dark" } });
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    await fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "system" } });
    expect(document.documentElement.dataset.theme).toBe("system");
  });

  it("loads and displays existing media roots, mirror roots, and device preference", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({
      media_roots: ["D:/Media/ABBA"], mirror_roots: ["D:/Stems"], device_preference: "cuda",
    });

    render(SettingsDialog, { props: { onClose: vi.fn() } });

    await waitFor(() => expect(screen.getByText("D:/Media/ABBA")).toBeTruthy());
    expect(screen.getByText("D:/Stems")).toBeTruthy();
    expect((screen.getByLabelText("Device preference") as HTMLSelectElement).value).toBe("cuda");
  });

  it("adds a media root and saves the updated settings", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });
    vi.mocked(updateSettings).mockResolvedValue({ media_roots: ["D:/New"], mirror_roots: [], device_preference: "auto" });

    render(SettingsDialog, { props: { onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByPlaceholderText("D:\\Media\\...")).toBeTruthy());

    await fireEvent.input(screen.getByPlaceholderText("D:\\Media\\..."), { target: { value: "D:/New" } });
    await fireEvent.click(screen.getByLabelText("Add media root"));
    await fireEvent.click(screen.getByText("Save"));

    expect(updateSettings).toHaveBeenCalledWith({
      media_roots: ["D:/New"], mirror_roots: [], device_preference: "auto",
      downloads_root: null, youtube_cookies: { mode: "none" },
    });
  });

  it("adds a mirror root using its own distinctly-labeled Add button", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });
    vi.mocked(updateSettings).mockResolvedValue({ media_roots: [], mirror_roots: ["D:/NewStems"], device_preference: "auto" });

    render(SettingsDialog, { props: { onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByPlaceholderText("D:\\Stems\\...")).toBeTruthy());

    await fireEvent.input(screen.getByPlaceholderText("D:\\Stems\\..."), { target: { value: "D:/NewStems" } });
    await fireEvent.click(screen.getByLabelText("Add mirror root"));
    await fireEvent.click(screen.getByText("Save"));

    expect(updateSettings).toHaveBeenCalledWith({
      media_roots: [], mirror_roots: ["D:/NewStems"], device_preference: "auto",
      downloads_root: null, youtube_cookies: { mode: "none" },
    });
  });

  it("removes a media root", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: ["D:/Media/ABBA"], mirror_roots: [], device_preference: "auto" });

    render(SettingsDialog, { props: { onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByText("D:/Media/ABBA")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Remove D:/Media/ABBA"));

    expect(screen.queryByText("D:/Media/ABBA")).toBeNull();
  });

  it("runs a rescan and displays the result counts including unavailable roots", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });
    vi.mocked(rescan).mockResolvedValue({
      scan_id: 1, status: "completed", tracks_found: 12, media_roots_scanned: 2, media_roots_total: 2,
      current_root: null, unavailable_roots: ["D:/Missing"], tracks_purged: 0, error: null,
      updated_at: "2026-08-05T00:00:00Z",
    });

    render(SettingsDialog, { props: { onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByText("Rescan")).toBeTruthy());

    await fireEvent.click(screen.getByText("Rescan"));

    await waitFor(() => expect(screen.getByText(/Found 12 tracks/)).toBeTruthy());
    expect(screen.getByText(/D:\/Missing/)).toBeTruthy();
  });

  it("closes when Escape is pressed", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });
    const onClose = vi.fn();

    const { container } = render(SettingsDialog, { props: { onClose } });
    await waitFor(() => expect(screen.getByText("Settings")).toBeTruthy());

    await fireEvent.keyDown(container.querySelector(".settings-dialog-overlay")!, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });

  it("uses the folder picker to populate path fields without adding or saving automatically", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });
    vi.mocked(browseForFolder).mockResolvedValueOnce("D:\\Music").mockResolvedValueOnce("D:\\Downloads");
    render(SettingsDialog, { props: { onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByLabelText("Browse for media root")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Browse for media root"));
    await waitFor(() => expect((screen.getByPlaceholderText("D:\\Media\\...") as HTMLInputElement).value).toBe("D:\\Music"));
    expect(screen.queryByText("D:\\Music")).toBeNull();

    await fireEvent.click(screen.getByLabelText("Browse for downloads root"));
    await waitFor(() => expect((screen.getByLabelText("Downloads root") as HTMLInputElement).value).toBe("D:\\Downloads"));
    expect(updateSettings).not.toHaveBeenCalled();
  });

  it("stays open when the backdrop is clicked", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });
    const onClose = vi.fn();
    const { container } = render(SettingsDialog, { props: { onClose } });
    await waitFor(() => expect(screen.getByText("Settings")).toBeTruthy());

    await fireEvent.click(container.querySelector(".settings-dialog-overlay")!);

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Settings" })).toBeTruthy();
  });

  it("loads and displays the downloads root and cookie mode", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({
      media_roots: [], mirror_roots: [], device_preference: "auto",
      downloads_root: "D:/Downloads", youtube_cookies: { mode: "browser", browser: "chrome" },
    });

    render(SettingsDialog, { props: { onClose: vi.fn() } });

    await waitFor(() => expect((screen.getByLabelText("Downloads root") as HTMLInputElement).value).toBe("D:/Downloads"));
    expect((screen.getByLabelText("YouTube cookies") as HTMLSelectElement).value).toBe("browser");
    expect((screen.getByLabelText("Browser") as HTMLInputElement).value).toBe("chrome");
  });

  it("defaults to no downloads root and cookie mode none when unset", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });

    render(SettingsDialog, { props: { onClose: vi.fn() } });

    await waitFor(() => expect((screen.getByLabelText("Downloads root") as HTMLInputElement).value).toBe(""));
    expect((screen.getByLabelText("YouTube cookies") as HTMLSelectElement).value).toBe("none");
  });

  it("shows the cookies-file field instead of the browser field when mode is file", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({
      media_roots: [], mirror_roots: [], device_preference: "auto",
      downloads_root: null, youtube_cookies: { mode: "file", cookies_file: "C:/cookies.txt" },
    });

    render(SettingsDialog, { props: { onClose: vi.fn() } });

    await waitFor(() => expect((screen.getByLabelText("Cookies file") as HTMLInputElement).value).toBe("C:/cookies.txt"));
    expect(screen.queryByLabelText("Browser")).toBeNull();
  });

  it("disables Save when cookie mode is browser but the browser field is empty", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });

    render(SettingsDialog, { props: { onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByLabelText("YouTube cookies")).toBeTruthy());

    await fireEvent.change(screen.getByLabelText("YouTube cookies"), { target: { value: "browser" } });

    const saveButton = screen.getByText("Save") as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);

    await fireEvent.input(screen.getByLabelText("Browser"), { target: { value: "chrome" } });
    expect((screen.getByText("Save") as HTMLButtonElement).disabled).toBe(false);
  });

  it("disables Save when cookie mode is file but the cookies file field is empty", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });

    render(SettingsDialog, { props: { onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByLabelText("YouTube cookies")).toBeTruthy());

    await fireEvent.change(screen.getByLabelText("YouTube cookies"), { target: { value: "file" } });

    const saveButton = screen.getByText("Save") as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);

    await fireEvent.input(screen.getByLabelText("Cookies file"), { target: { value: "C:/cookies.txt" } });
    expect((screen.getByText("Save") as HTMLButtonElement).disabled).toBe(false);
  });

  it("saves the downloads root and cookie settings", async () => {
    vi.mocked(fetchSettings).mockResolvedValue({ media_roots: [], mirror_roots: [], device_preference: "auto" });
    vi.mocked(updateSettings).mockResolvedValue({
      media_roots: [], mirror_roots: [], device_preference: "auto",
      downloads_root: "D:/Downloads", youtube_cookies: { mode: "none" },
    });

    render(SettingsDialog, { props: { onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByLabelText("Downloads root")).toBeTruthy());

    await fireEvent.input(screen.getByLabelText("Downloads root"), { target: { value: "D:/Downloads" } });
    await fireEvent.click(screen.getByText("Save"));

    expect(updateSettings).toHaveBeenCalledWith({
      media_roots: [], mirror_roots: [], device_preference: "auto",
      downloads_root: "D:/Downloads", youtube_cookies: { mode: "none" },
    });
  });
});
