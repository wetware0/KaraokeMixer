import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({ fetchRecipes: vi.fn(), submitJob: vi.fn() }));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

describe("ProcessDialog", () => {
  it("defaults to karaoke instrumental regardless of registry order", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([
      { name: "fetch_tags", lane: "cpu", options_schema: null },
      { name: "full_prep", lane: "gpu", options_schema: null },
      { name: "karaoke", lane: "gpu", options_schema: null },
    ]);
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1], device: "auto", onSubmitted: vi.fn(), onClose: vi.fn() } });

    await waitFor(() => expect((screen.getByLabelText("Recipe") as HTMLSelectElement).value).toBe("karaoke"));
    expect(screen.getByText("Karaoke instrumental")).toBeTruthy();
  });

  it("fetches recipes and renders karaoke's options from its schema", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([
      {
        name: "karaoke", lane: "gpu",
        options_schema: {
          model: { type: "select", choices: ["htdemucs", "mdx"], default: "htdemucs" },
          backing_vocal_mode: { type: "select", choices: ["stripped", "faint", "stereo_mix", "best"], default: "stripped" },
        },
      },
    ]);
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1], device: "cpu", onSubmitted: vi.fn(), onClose: vi.fn() } });

    await waitFor(() => expect(screen.getByText("Backing-vocal treatment")).toBeTruthy());
    expect(screen.getByText("Separation model")).toBeTruthy();
  });

  it("presents isolated-vocal timing as the recommended review profile", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([
      {
        name: "improve_lyrics", lane: "gpu",
        options_schema: {
          timing_review_profile: {
            type: "select",
            choices: ["high_accuracy", "deep", "quick"],
            default: "high_accuracy",
          },
        },
      },
    ]);
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1], device: "cuda", onSubmitted: vi.fn(), onClose: vi.fn() } });

    await waitFor(() => expect(screen.getByText("High Accuracy — isolated vocal (recommended)")).toBeTruthy());
    expect(screen.getByText("Legacy deep review")).toBeTruthy();
    expect(screen.getByText("Quick dual-audio review")).toBeTruthy();
  });

  it("submits the selected recipe's options merged with device/overwrite/output_mode", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes, submitJob } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([
      {
        name: "karaoke", lane: "gpu",
        options_schema: { backing_vocal_mode: { type: "select", choices: ["stripped", "faint"], default: "stripped" } },
      },
    ]);
    vi.mocked(submitJob).mockResolvedValue({ job_id: 5 });
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");
    const onSubmitted = vi.fn();

    render(ProcessDialog, { props: { trackIds: [1, 2], device: "cpu", onSubmitted, onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByText("Backing-vocal treatment")).toBeTruthy());

    await fireEvent.click(screen.getByText("Start preparation"));

    expect(submitJob).toHaveBeenCalledWith({
      recipe: "karaoke",
      track_ids: [1, 2],
      options: { device: "cpu", overwrite: false, output_mode: "beside", backing_vocal_mode: "stripped" },
    });
    expect(onSubmitted).toHaveBeenCalledWith(5);
  });

  it("renders and submits a number-typed option", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes, submitJob } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([
      { name: "karaoke", lane: "gpu", options_schema: { retries: { type: "number", default: 2 } } },
    ]);
    vi.mocked(submitJob).mockResolvedValue({ job_id: 9 });
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1], device: "cpu", onSubmitted: vi.fn(), onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByText("Retry attempts")).toBeTruthy());

    const numberInput = screen.getByDisplayValue("2") as HTMLInputElement;
    await fireEvent.input(numberInput, { target: { value: "5" } });
    await fireEvent.click(screen.getByText("Start preparation"));

    expect(submitJob).toHaveBeenCalledWith({
      recipe: "karaoke", track_ids: [1],
      options: { device: "cpu", overwrite: false, output_mode: "beside", retries: 5 },
    });
  });

  it("resets options when switching recipes, leaving no stale values from the previous schema", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes, submitJob } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([
      {
        name: "karaoke", lane: "gpu",
        options_schema: { backing_vocal_mode: { type: "select", choices: ["stripped", "faint"], default: "stripped" } },
      },
      {
        name: "full_stems", lane: "cpu",
        options_schema: { split: { type: "checkbox", default: false } },
      },
    ]);
    vi.mocked(submitJob).mockResolvedValue({ job_id: 7 });
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1], device: "cpu", onSubmitted: vi.fn(), onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByText("Backing-vocal treatment")).toBeTruthy());

    await fireEvent.change(screen.getByText("Backing-vocal treatment").closest("label")!.querySelector("select")!, {
      target: { value: "faint" },
    });

    await fireEvent.change(screen.getByLabelText("Recipe"), { target: { value: "full_stems" } });
    await waitFor(() => expect(screen.getByText("Split combined vocals")).toBeTruthy());
    expect(screen.queryByText("Backing-vocal treatment")).toBeNull();

    await fireEvent.click(screen.getByText("Start preparation"));

    expect(submitJob).toHaveBeenCalledWith({
      recipe: "full_stems",
      track_ids: [1],
      options: { device: "cpu", overwrite: false, output_mode: "beside", split: false },
    });
  });

  it("renders and submits a checkbox-typed option", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes, submitJob } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([
      { name: "full_stems", lane: "cpu", options_schema: { split: { type: "checkbox", default: false } } },
    ]);
    vi.mocked(submitJob).mockResolvedValue({ job_id: 11 });
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1], device: "cpu", onSubmitted: vi.fn(), onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByText("Split combined vocals")).toBeTruthy());

    await fireEvent.click(screen.getByLabelText("Split combined vocals"));
    await fireEvent.click(screen.getByText("Start preparation"));

    expect(submitJob).toHaveBeenCalledWith({
      recipe: "full_stems",
      track_ids: [1],
      options: { device: "cpu", overwrite: false, output_mode: "beside", split: true },
    });
  });

  it("blocks requested enhanced timing when WhisperX is unavailable but permits lyrics-only download", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes, submitJob } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([
      {
        name: "lyrics_only", lane: "gpu",
        options_schema: {
          fetch: { type: "checkbox", default: true },
          align: { type: "checkbox", default: true },
        },
      },
    ]);
    vi.mocked(submitJob).mockResolvedValue({ job_id: 12 });
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, {
      props: {
        trackIds: [1], device: "cpu", whisperxAvailable: false,
        onSubmitted: vi.fn(), onClose: vi.fn(),
      },
    });

    await waitFor(() => expect(screen.getByText(/Enhanced per-word timing is unavailable/)).toBeTruthy());
    const submit = screen.getByText("Start preparation") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    await fireEvent.click(screen.getByLabelText("Create enhanced per-word timing"));
    expect(screen.queryByText(/Enhanced per-word timing is unavailable/)).toBeNull();
    expect(submit.disabled).toBe(false);
    await fireEvent.click(submit);

    expect(submitJob).toHaveBeenCalledWith({
      recipe: "lyrics_only",
      track_ids: [1],
      options: { device: "cpu", overwrite: false, output_mode: "beside", fetch: true, align: false },
    });
  });

  it("applies a clear bulk profile and keeps expert model controls advanced", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes, submitJob } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([
      {
        name: "full_prep", lane: "gpu",
        options_schema: {
          processing_profile: { type: "select", choices: ["fast", "balanced", "high_quality"], default: "balanced" },
          model: { type: "select", choices: ["mdx", "htdemucs", "htdemucs_ft"], default: "htdemucs", advanced: true },
          backing_vocal_mode: { type: "select", choices: ["stripped", "best"], default: "stripped", advanced: true },
          asr_model: { type: "select", choices: ["base.en", "small.en", "medium"], default: "small.en", advanced: true },
          fetch_lyrics: { type: "checkbox", default: true },
          align_lyrics: { type: "checkbox", default: true },
        },
      },
    ]);
    vi.mocked(submitJob).mockResolvedValue({ job_id: 31 });
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1, 2, 3], device: "cuda", onSubmitted: vi.fn(), onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByText("Fast bulk")).toBeTruthy());
    expect(screen.getByText(/Models stay loaded/)).toBeTruthy();

    await fireEvent.click(screen.getByText("High quality"));
    await fireEvent.click(screen.getByText("Advanced options"));
    expect((screen.getByLabelText("Separation model") as HTMLSelectElement).value).toBe("htdemucs_ft");
    expect((screen.getByLabelText("Speech recognition model") as HTMLSelectElement).value).toBe("medium");
    await fireEvent.click(screen.getByText("Start preparation"));

    expect(submitJob).toHaveBeenCalledWith({
      recipe: "full_prep",
      track_ids: [1, 2, 3],
      options: expect.objectContaining({
        device: "cuda",
        processing_profile: "high_quality",
        model: "htdemucs_ft",
        backing_vocal_mode: "best",
        asr_model: "medium",
      }),
    });
  });

  it("keeps an explicitly selected quality when switching processing workflows", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes, submitJob } = await import("../api");
    const profile = { type: "select" as const, choices: ["fast", "balanced", "high_quality"], default: "balanced" };
    vi.mocked(fetchRecipes).mockResolvedValue([
      {
        name: "full_prep", lane: "gpu",
        options_schema: {
          processing_profile: profile,
          model: { type: "select", choices: ["mdx", "htdemucs", "htdemucs_ft"], default: "htdemucs", advanced: true },
          backing_vocal_mode: { type: "select", choices: ["stripped", "best"], default: "stripped", advanced: true },
        },
      },
      {
        name: "karaoke", lane: "gpu",
        options_schema: {
          processing_profile: profile,
          model: { type: "select", choices: ["mdx", "htdemucs", "htdemucs_ft"], default: "htdemucs", advanced: true },
          backing_vocal_mode: { type: "select", choices: ["stripped", "best"], default: "stripped", advanced: true },
        },
      },
      {
        name: "lyrics_only", lane: "gpu",
        options_schema: {
          processing_profile: profile,
          asr_model: { type: "select", choices: ["base.en", "small.en", "medium"], default: "small.en", advanced: true },
          fetch: { type: "checkbox", default: true },
          align: { type: "checkbox", default: true },
        },
      },
    ]);
    vi.mocked(submitJob).mockResolvedValue({ job_id: 32 });
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1], device: "cuda", onSubmitted: vi.fn(), onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByText("High quality")).toBeTruthy());

    await fireEvent.change(screen.getByLabelText("Recipe"), { target: { value: "full_prep" } });
    await fireEvent.click(screen.getByText("High quality"));
    await fireEvent.change(screen.getByLabelText("Recipe"), { target: { value: "karaoke" } });
    expect((screen.getByLabelText(/High quality/) as HTMLInputElement).checked).toBe(true);

    await fireEvent.change(screen.getByLabelText("Recipe"), { target: { value: "lyrics_only" } });
    expect((screen.getByLabelText(/High quality/) as HTMLInputElement).checked).toBe(true);
    await fireEvent.click(screen.getByText("Start preparation"));

    expect(submitJob).toHaveBeenCalledWith({
      recipe: "lyrics_only",
      track_ids: [1],
      options: expect.objectContaining({
        device: "cuda",
        processing_profile: "high_quality",
        asr_model: "medium",
      }),
    });
  });

  it("shows the fake recipe option only when the dev flag is enabled", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "true");
    const { fetchRecipes } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([]);
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1], device: "cpu", onSubmitted: vi.fn(), onClose: vi.fn() } });

    await waitFor(() => expect(screen.getByText("fake (dev only)")).toBeTruthy());
  });

  it("does not show the fake recipe option when the dev flag is disabled", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([{ name: "karaoke", lane: "gpu", options_schema: null }]);
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1], device: "cpu", onSubmitted: vi.fn(), onClose: vi.fn() } });
    await waitFor(() => expect(screen.getByText("What would you like to make?")).toBeTruthy());

    expect(screen.queryByText("fake (dev only)")).toBeNull();
  });

  it("preselects the device select to the given device prop", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([{ name: "karaoke", lane: "gpu", options_schema: null }]);
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");

    render(ProcessDialog, { props: { trackIds: [1], device: "auto", onSubmitted: vi.fn(), onClose: vi.fn() } });

    expect((screen.getByLabelText("Device") as HTMLSelectElement).value).toBe("auto");
  });

  it("stays open when the backdrop is clicked", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([{ name: "karaoke", lane: "gpu", options_schema: null }]);
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");
    const onClose = vi.fn();
    const { container } = render(ProcessDialog, { props: { trackIds: [1], device: "cpu", onSubmitted: vi.fn(), onClose } });

    await fireEvent.click(container.querySelector(".process-dialog-overlay")!);

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("closes on Escape", async () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    const { fetchRecipes } = await import("../api");
    vi.mocked(fetchRecipes).mockResolvedValue([{ name: "karaoke", lane: "gpu", options_schema: null }]);
    const { default: ProcessDialog } = await import("./ProcessDialog.svelte");
    const onClose = vi.fn();

    const { container } = render(ProcessDialog, { props: { trackIds: [1], device: "cpu", onSubmitted: vi.fn(), onClose } });

    await fireEvent.keyDown(container.querySelector(".process-dialog-overlay")!, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });
});
