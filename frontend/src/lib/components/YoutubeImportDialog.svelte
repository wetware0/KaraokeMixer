<script lang="ts">
  import { onMount } from "svelte";
  import { fetchRecipes, importFromYoutube, probeYoutube } from "../api";
  import type { RecipeInfo, YoutubePlaylistEntry } from "../types";

  let { onClose }: { onClose: () => void } = $props();

  let url = $state("");
  let artist = $state("");
  let title = $state("");
  let probing = $state(false);
  let lastProbedUrl = $state("");
  let probeError = $state<string | null>(null);
  let processAfter = $state(false);
  let recipes = $state<RecipeInfo[]>([]);
  let selectedRecipe = $state("");
  let submitting = $state(false);
  let submitError = $state<string | null>(null);
  let dialogEl = $state<HTMLDivElement | undefined>();

  let playlistEntries = $state<YoutubePlaylistEntry[] | null>(null);
  let playlistTotal = $state(0);
  let checkedEntryUrls = $state<Set<string>>(new Set());

  const selectedCount = $derived(checkedEntryUrls.size);
  const allEntriesSelected = $derived(Boolean(playlistEntries?.length) && checkedEntryUrls.size === playlistEntries?.length);

  const recipeLabels: Record<string, string> = {
    karaoke: "Karaoke instrumental",
    full_stems: "All editable stems",
    lyrics_only: "Lyrics and timing",
    fetch_tags: "Tags and artwork",
    full_prep: "Complete karaoke preparation",
  };

  function recipeLabel(name: string): string {
    return recipeLabels[name] ?? name.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  onMount(async () => {
    dialogEl?.focus();
    try {
      recipes = await fetchRecipes();
      if (recipes.length > 0) selectedRecipe = recipes[0].name;
    } catch {
      // Recipe discovery is optional for a download-only import.
    }
  });

  function formatEntryDuration(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds < 0) return "—";
    const total = Math.round(seconds);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  function onUrlInput(): void {
    if (url.trim() !== lastProbedUrl) {
      playlistEntries = null;
      playlistTotal = 0;
      checkedEntryUrls = new Set();
      probeError = null;
    }
  }

  async function probe(): Promise<void> {
    const trimmed = url.trim();
    if (!trimmed || probing || trimmed === lastProbedUrl) return;
    probing = true;
    probeError = null;
    submitError = null;
    playlistEntries = null;
    playlistTotal = 0;
    checkedEntryUrls = new Set();
    try {
      const info = await probeYoutube(trimmed);
      lastProbedUrl = trimmed;
      if (info.is_playlist) {
        playlistEntries = info.entries;
        playlistTotal = info.total;
        checkedEntryUrls = new Set(info.entries.map((entry) => entry.url));
      } else {
        if (!artist) artist = info.uploader;
        if (!title) title = info.title;
      }
    } catch (cause) {
      probeError = cause instanceof Error ? cause.message : "Could not inspect this YouTube link";
    } finally {
      probing = false;
    }
  }

  function toggleEntry(entryUrl: string): void {
    const next = new Set(checkedEntryUrls);
    if (next.has(entryUrl)) next.delete(entryUrl);
    else next.add(entryUrl);
    checkedEntryUrls = next;
  }

  function toggleSelectAll(): void {
    checkedEntryUrls = allEntriesSelected
      ? new Set()
      : new Set((playlistEntries ?? []).map((entry) => entry.url));
  }

  function currentProcessAfter() {
    return processAfter && selectedRecipe ? { recipe: selectedRecipe, options: {} } : undefined;
  }

  async function submitSingle(): Promise<void> {
    await importFromYoutube({
      url: url.trim(),
      artist: artist.trim() || undefined,
      title: title.trim() || undefined,
      process_after: currentProcessAfter(),
    });
  }

  async function submitPlaylist(): Promise<void> {
    const entries = (playlistEntries ?? []).filter((entry) => checkedEntryUrls.has(entry.url));
    const results = await Promise.allSettled(
      entries.map((entry) => importFromYoutube({
        url: entry.url,
        title: entry.title,
        process_after: currentProcessAfter(),
      })),
    );
    const failed = results.filter((result) => result.status === "rejected");
    if (failed.length > 0) {
      const started = results.length - failed.length;
      const firstFailure = failed[0] as PromiseRejectedResult;
      const detail = firstFailure.reason instanceof Error ? firstFailure.reason.message : "Unknown error";
      throw new Error(`${started} import${started === 1 ? "" : "s"} started; ${failed.length} failed. ${detail}`);
    }
  }

  async function submit(): Promise<void> {
    submitting = true;
    submitError = null;
    try {
      if (playlistEntries) await submitPlaylist();
      else await submitSingle();
      onClose();
    } catch (cause) {
      submitError = cause instanceof Error ? cause.message : "Failed to start the import";
    } finally {
      submitting = false;
    }
  }

  function closeIfIdle(): void {
    if (!submitting) onClose();
  }

  function onDialogKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") closeIfIdle();
  }
</script>

<div class="process-dialog-overlay" role="presentation">
  <div
    class="process-dialog youtube-import-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="youtube-import-dialog-title"
    tabindex="-1"
    bind:this={dialogEl}
    onkeydown={onDialogKeydown}
  >
    <div class="process-dialog-header">
      <div>
        <p class="dialog-eyebrow">Bring music into your workspace</p>
        <h2 id="youtube-import-dialog-title">Import from YouTube</h2>
      </div>
      <button class="process-dialog-close" type="button" onclick={closeIfIdle} aria-label="Close YouTube import">×</button>
    </div>

    <div class="process-dialog-body">
      <div class="youtube-url-row">
        <label class="process-dialog-field">
          <span class="process-dialog-label">Video or playlist URL</span>
          <input
            class="process-dialog-text"
            type="url"
            placeholder="https://www.youtube.com/watch?v=..."
            bind:value={url}
            oninput={onUrlInput}
            onblur={() => void probe()}
          />
        </label>
        <button type="button" class="youtube-check-button" onclick={() => void probe()} disabled={!url.trim() || probing}>
          {probing ? "Checking…" : "Check link"}
        </button>
      </div>

      {#if probeError}
        <p class="process-dialog-error" role="alert">{probeError}</p>
      {/if}

      {#if playlistEntries}
        <section class="youtube-playlist-panel" aria-labelledby="youtube-playlist-title">
          <div class="youtube-playlist-summary">
            <div>
              <h3 id="youtube-playlist-title">Choose tracks</h3>
              <p>
                {#if playlistTotal > playlistEntries.length}
                  Showing the first {playlistEntries.length} of {playlistTotal} videos
                {:else}
                  {playlistEntries.length} video{playlistEntries.length === 1 ? "" : "s"} found
                {/if}
              </p>
            </div>
            <label class="youtube-select-all">
              <input type="checkbox" aria-label="Select all" checked={allEntriesSelected} onchange={toggleSelectAll} />
              <span>{allEntriesSelected ? "Clear all" : "Select all"}</span>
            </label>
          </div>
          <ul class="youtube-playlist-entries">
            {#each playlistEntries as entry (entry.url)}
              <li class:youtube-playlist-entry-selected={checkedEntryUrls.has(entry.url)}>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`Import ${entry.title}`}
                    checked={checkedEntryUrls.has(entry.url)}
                    onchange={() => toggleEntry(entry.url)}
                  />
                  <span class="youtube-playlist-entry-title">{entry.title}</span>
                  <span class="youtube-playlist-entry-duration">{formatEntryDuration(entry.duration)}</span>
                </label>
              </li>
            {/each}
          </ul>
        </section>
      {:else}
        <div class="youtube-single-fields">
          <label class="process-dialog-field">
            <span class="process-dialog-label">Artist</span>
            <input class="process-dialog-text" type="text" bind:value={artist} autocomplete="off" />
          </label>
          <label class="process-dialog-field">
            <span class="process-dialog-label">Title</span>
            <input class="process-dialog-text" type="text" bind:value={title} autocomplete="off" />
          </label>
        </div>
      {/if}

      <section class="youtube-after-import">
        <label class="process-dialog-checkbox-row">
          <input class="process-dialog-checkbox" type="checkbox" bind:checked={processAfter} aria-label="Process after import" />
          <span>
            <strong>Prepare after download</strong>
            <small>Automatically run a recipe when each track arrives.</small>
          </span>
        </label>
        {#if processAfter}
          <label class="process-dialog-field">
            <span class="process-dialog-label">Preparation recipe</span>
            <select class="process-dialog-select" bind:value={selectedRecipe}>
              {#each recipes as recipe (recipe.name)}
                <option value={recipe.name}>{recipeLabel(recipe.name)}</option>
              {/each}
            </select>
          </label>
        {/if}
      </section>

      {#if submitError}
        <p class="process-dialog-error" role="alert">{submitError}</p>
      {/if}
    </div>

    <div class="process-dialog-actions">
      <button type="button" class="process-dialog-cancel" onclick={closeIfIdle} disabled={submitting}>Cancel</button>
      <button
        type="button"
        class="process-dialog-submit"
        onclick={() => void submit()}
        disabled={submitting || (playlistEntries ? selectedCount === 0 : !url.trim())}
      >
        {#if submitting}
          Starting…
        {:else if playlistEntries}
          Import {selectedCount} selected
        {:else}
          Import track
        {/if}
      </button>
    </div>
  </div>
</div>
