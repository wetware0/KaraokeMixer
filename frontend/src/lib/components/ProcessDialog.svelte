<script lang="ts">
  import { onMount } from "svelte";
  import { fetchRecipes, submitJob } from "../api";
  import { isFakeRecipeEnabled } from "../devFlags";
  import type { JobOptions, RecipeInfo } from "../types";

  let {
    trackIds,
    device,
    whisperxAvailable = null,
    onSubmitted,
    onClose,
  }: {
    trackIds: number[];
    device: "auto" | "cuda" | "cpu";
    whisperxAvailable?: boolean | null;
    onSubmitted: (jobId: number) => void;
    onClose: () => void;
  } = $props();

  const fakeEnabled = isFakeRecipeEnabled();
  const recipeCopy: Record<string, { label: string; description: string }> = {
    karaoke: { label: "Karaoke instrumental", description: "Separate the lead vocal and keep a ready-to-sing backing track." },
    full_stems: { label: "All editable stems", description: "Separate vocals, drums, bass and other parts for detailed mixing." },
    lyrics_only: { label: "Lyrics and enhanced timing", description: "Find lyrics and create a fresh timestamp for every word without separating audio." },
    fetch_tags: { label: "Tags and artwork", description: "Look up missing artist, title, album, year and cover art." },
    full_prep: { label: "Complete karaoke preparation", description: "Create stems, fetch metadata and prepare timed lyrics in one job." },
    improve_lyrics: { label: "Improve lyric timing", description: "Deep review adds independent song transcription to the original and vocal-residual alignments, while keeping uncertain or large timing changes flagged." },
  };
  const optionLabels: Record<string, string> = {
    processing_profile: "Processing profile",
    model: "Separation model",
    backing_vocal_mode: "Backing-vocal treatment",
    asr_model: "Speech recognition model",
    fetch: "Download lyrics",
    fetch_lyrics: "Download lyrics",
    align: "Create enhanced per-word timing",
    align_lyrics: "Create enhanced per-word timing",
    lyrics_source: "Lyrics source",
    split: "Split combined vocals",
    retries: "Retry attempts",
    timing_review_profile: "Timing review",
  };
  const profileCopy: Record<string, { label: string; description: string }> = {
    fast: { label: "Fast bulk", description: "Maximum throughput for large libraries." },
    balanced: { label: "Balanced", description: "Strong quality at practical batch speed." },
    high_quality: { label: "High quality", description: "Best models; substantially slower." },
  };
  const profileDefaults: Record<string, Record<string, unknown>> = {
    fast: { model: "mdx", backing_vocal_mode: "stripped", asr_model: "base.en", split: false },
    balanced: { model: "htdemucs", backing_vocal_mode: "stripped", asr_model: "small.en", split: false },
    high_quality: { model: "htdemucs_ft", backing_vocal_mode: "best", asr_model: "medium", split: true },
  };

  function humanize(value: string): string {
    return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function recipeLabel(name: string): string {
    return recipeCopy[name]?.label ?? humanize(name);
  }

  function optionLabel(name: string): string {
    return optionLabels[name] ?? humanize(name);
  }

  function choiceLabel(key: string, value: string): string {
    if (key === "timing_review_profile") return value === "deep" ? "Deep review (recommended)" : "Quick dual-audio review";
    if (key === "processing_profile") return profileCopy[value]?.label ?? humanize(value);
    if (key === "asr_model") return value === "base.en" ? "Base (fastest)" : value === "small.en" ? "Small (balanced)" : "Medium (highest accuracy)";
    if (key === "backing_vocal_mode") {
      return ({ stripped: "Remove all vocals", faint: "Keep faint backing vocals", stereo_mix: "Keep wide backing vocals", best: "Best lead-vocal removal (UVR)" } as Record<string, string>)[value] ?? humanize(value);
    }
    return value;
  }

  let recipes = $state<RecipeInfo[]>([]);
  let recipe = $state("");
  let recipeOptions = $state<Record<string, unknown>>({});
  // A creator commonly chooses a quality level and then explores which
  // workflow to run. Keep that explicit quality choice when the destination
  // recipe supports the same profile instead of silently reverting to its
  // default (normally Balanced).
  let preferredProcessingProfile = $state<string | null>(null);
  function initialDevice(): "auto" | "cuda" | "cpu" { return device; }
  let selectedDevice = $state(initialDevice());
  let overwrite = $state(false);
  let outputMode = $state<"beside" | "mirror">("beside");
  let submitting = $state(false);
  let loading = $state(true);
  let errorMessage = $state<string | null>(null);
  let dialogEl: HTMLDivElement | undefined;

  const currentSchema = $derived(recipes.find((candidate) => candidate.name === recipe)?.options_schema ?? null);
  const regularOptions = $derived(
    Object.entries(currentSchema ?? {}).filter(([key, spec]) => key !== "processing_profile" && !spec.advanced)
  );
  const advancedRecipeOptions = $derived(
    Object.entries(currentSchema ?? {}).filter(([, spec]) => spec.advanced)
  );
  const enhancedTimingRequested = $derived(
    recipeOptions.align === true || recipeOptions.align_lyrics === true
  );
  const enhancedTimingUnavailable = $derived(whisperxAvailable === false && enhancedTimingRequested);

  function defaultsFor(schema: RecipeInfo["options_schema"]): Record<string, unknown> {
    const defaults: Record<string, unknown> = {};
    if (schema) {
      for (const [key, spec] of Object.entries(schema)) defaults[key] = spec.default;
    }
    return defaults;
  }

  function selectRecipe(name: string) {
    const schema = recipes.find((candidate) => candidate.name === name)?.options_schema ?? null;
    const next = defaultsFor(schema);
    const profileSpec = schema?.processing_profile;
    if (
      preferredProcessingProfile !== null
      && profileSpec?.choices?.includes(preferredProcessingProfile)
    ) {
      next.processing_profile = preferredProcessingProfile;
      for (const [profileKey, profileValue] of Object.entries(profileDefaults[preferredProcessingProfile] ?? {})) {
        if (profileKey in (schema ?? {})) next[profileKey] = profileValue;
      }
    }
    recipe = name;
    recipeOptions = next;
  }

  function setRecipeOption(key: string, value: unknown) {
    let next = { ...recipeOptions, [key]: value };
    if (key === "processing_profile" && typeof value === "string") {
      preferredProcessingProfile = value;
      const schema = currentSchema ?? {};
      for (const [profileKey, profileValue] of Object.entries(profileDefaults[value] ?? {})) {
        if (profileKey in schema) next[profileKey] = profileValue;
      }
    }
    recipeOptions = next;
  }

  onMount(async () => {
    dialogEl?.focus();
    try {
      recipes = await fetchRecipes();
      const preferred = recipes.find((candidate) => candidate.name === "karaoke")
        ?? recipes.find((candidate) => candidate.name === "full_prep")
        ?? recipes[0];
      if (preferred) selectRecipe(preferred.name);
      else if (fakeEnabled) selectRecipe("fake");
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to load recipes";
    } finally {
      loading = false;
    }
  });

  async function submit() {
    submitting = true;
    errorMessage = null;
    try {
      const options: JobOptions = {
        device: selectedDevice,
        overwrite,
        output_mode: outputMode,
        ...recipeOptions,
      };
      const { job_id } = await submitJob({ recipe, track_ids: trackIds, options });
      onSubmitted(job_id);
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to submit job";
    } finally {
      submitting = false;
    }
  }

  function onOverlayKeydown(event: KeyboardEvent) {
    if (event.key === "Escape") onClose();
  }

  function onDialogKeydown(event: KeyboardEvent) {
    if (event.key === "Escape") onClose();
    event.stopPropagation();
  }
</script>

<div class="process-dialog-overlay" role="presentation" onkeydown={onOverlayKeydown}>
  <div
    class="process-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="process-dialog-title"
    tabindex="-1"
    bind:this={dialogEl}
    onkeydown={onDialogKeydown}
  >
    <div class="process-dialog-header">
      <div>
        <p class="dialog-eyebrow">CREATOR WORKFLOW</p>
        <h2 id="process-dialog-title">Prepare {trackIds.length} track{trackIds.length === 1 ? "" : "s"}</h2>
      </div>
      <button class="process-dialog-close" onclick={onClose} aria-label="Close">×</button>
    </div>

    <div class="process-dialog-body">
      {#if loading}
        <p>Loading recipes…</p>
      {:else}
        <label class="process-dialog-field">
          <span class="process-dialog-label">What would you like to make?</span>
          <select
            class="process-dialog-select"
            aria-label="Recipe"
            value={recipe}
            onchange={(event) => selectRecipe((event.target as HTMLSelectElement).value)}
          >
            {#each recipes as candidate (candidate.name)}
              <option value={candidate.name}>{recipeLabel(candidate.name)}</option>
            {/each}
            {#if fakeEnabled}
              <option value="fake">fake (dev only)</option>
            {/if}
          </select>
        </label>
        {#if recipe}
          <p class="process-dialog-description">{recipeCopy[recipe]?.description ?? "Run this preparation workflow for the selected tracks."}</p>
        {/if}

        {#if currentSchema?.processing_profile}
          <fieldset class="processing-profile-picker">
            <legend>How should this batch run?</legend>
            <div class="processing-profile-options">
              {#each currentSchema.processing_profile.choices ?? [] as profile (profile)}
                <label class:processing-profile-selected={recipeOptions.processing_profile === profile}>
                  <input
                    type="radio"
                    name="processing-profile"
                    value={profile}
                    checked={recipeOptions.processing_profile === profile}
                    onchange={() => setRecipeOption("processing_profile", profile)}
                  />
                  <span>
                    <strong>{profileCopy[profile]?.label ?? humanize(profile)}</strong>
                    <small>{profileCopy[profile]?.description ?? "Custom processing profile."}</small>
                  </span>
                </label>
              {/each}
            </div>
            {#if trackIds.length > 1}
              <p class="bulk-acceleration-note">Models stay loaded while this batch runs, avoiding repeated startup time between tracks.</p>
            {/if}
          </fieldset>
        {/if}

        {#if currentSchema}
          {#each regularOptions as [key, spec] (key)}
            <label class="process-dialog-field">
              <span class="process-dialog-label">{optionLabel(key)}</span>
              {#if spec.type === "select"}
                <select
                  class="process-dialog-select"
                  value={recipeOptions[key]}
                  onchange={(event) => setRecipeOption(key, (event.target as HTMLSelectElement).value)}
                >
                  {#each spec.choices ?? [] as choice (choice)}
                    <option value={choice}>{choiceLabel(key, choice)}</option>
                  {/each}
                </select>
              {:else if spec.type === "checkbox"}
                <input
                  class="process-dialog-checkbox"
                  type="checkbox"
                  aria-label={optionLabel(key)}
                  checked={Boolean(recipeOptions[key])}
                  onchange={(event) => setRecipeOption(key, (event.target as HTMLInputElement).checked)}
                />
              {:else if spec.type === "number"}
                <input
                  class="process-dialog-number"
                  type="number"
                  value={recipeOptions[key]}
                  oninput={(event) => setRecipeOption(key, Number((event.target as HTMLInputElement).value))}
                />
              {/if}
            </label>
          {/each}
        {/if}
      {/if}

      <details class="process-dialog-advanced">
        <summary>Advanced options</summary>
        <div class="process-dialog-advanced-fields">
          {#each advancedRecipeOptions as [key, spec] (key)}
            <label class="process-dialog-field">
              <span class="process-dialog-label">{optionLabel(key)}</span>
              {#if spec.type === "select"}
                <select
                  class="process-dialog-select"
                  value={recipeOptions[key]}
                  onchange={(event) => setRecipeOption(key, (event.target as HTMLSelectElement).value)}
                >
                  {#each spec.choices ?? [] as choice (choice)}
                    <option value={choice}>{choiceLabel(key, choice)}</option>
                  {/each}
                </select>
              {:else if spec.type === "checkbox"}
                <label class="process-dialog-checkbox-row">
                  <input
                    type="checkbox"
                    checked={Boolean(recipeOptions[key])}
                    onchange={(event) => setRecipeOption(key, (event.target as HTMLInputElement).checked)}
                  />
                  <span>{optionLabel(key)}</span>
                </label>
              {/if}
              {#if spec.description}<small class="process-dialog-help">{spec.description}</small>{/if}
            </label>
          {/each}

          <label class="process-dialog-field">
            <span class="process-dialog-label">Processing device</span>
            <select aria-label="Device" class="process-dialog-select" bind:value={selectedDevice}>
              <option value="auto">Automatic</option>
              <option value="cuda">GPU (CUDA)</option>
              <option value="cpu">CPU</option>
            </select>
          </label>

          <label class="process-dialog-checkbox-row">
            <input type="checkbox" bind:checked={overwrite} />
            <span>Replace outputs that already exist</span>
          </label>

          <label class="process-dialog-field">
            <span class="process-dialog-label">Save files</span>
            <select aria-label="Output mode" class="process-dialog-select" bind:value={outputMode}>
              <option value="beside">Beside each original</option>
              <option value="mirror">In the mirror library</option>
            </select>
          </label>
        </div>
      </details>

      {#if errorMessage}
        <p class="process-dialog-error">{errorMessage}</p>
      {/if}
      {#if enhancedTimingUnavailable}
        <p class="process-dialog-warning" role="alert">
          Enhanced per-word timing is unavailable because the WhisperX worker is not installed. Turn off
          <strong>Create enhanced per-word timing</strong> to download lyrics only, or complete worker setup first.
        </p>
      {/if}
    </div>

    <div class="process-dialog-actions">
      <button class="process-dialog-cancel" onclick={onClose}>Cancel</button>
      <button
        class="process-dialog-submit"
        onclick={submit}
        disabled={submitting || !recipe || trackIds.length === 0 || enhancedTimingUnavailable}
      >
        {submitting ? "Starting…" : "Start preparation"}
      </button>
    </div>
  </div>
</div>
