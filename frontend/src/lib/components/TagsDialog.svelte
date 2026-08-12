<script lang="ts">
  import { onMount } from "svelte";
  import { artworkUrl, fetchTagSuggestion, saveTrackTags, uploadTrackArtwork } from "../api";
  import type { Track } from "../types";

  let { track, onSaved, onClose }: { track: Track; onSaved: (track: Track) => void; onClose: () => void } = $props();

  function initialFields() {
    return {
      artist: track.artist ?? "",
      title: track.title,
      album: track.album ?? "",
      year: track.year === null ? "" : String(track.year),
    };
  }

  const initial = initialFields();
  let artist = $state(initial.artist);
  let title = $state(initial.title);
  let album = $state(initial.album);
  let year = $state(initial.year);
  let artworkFile = $state<File | null>(null);
  let artworkMissing = $state(false);
  let fetchedArtworkDataUrl = $state<string | null>(null);
  let lookupKind = $state<"tags" | "artwork" | null>(null);
  let lookupStatus = $state<string | null>(null);
  let saving = $state(false);
  let error = $state<string | null>(null);
  let dialogEl = $state<HTMLDivElement | undefined>();
  let artistInputEl = $state<HTMLInputElement | undefined>();

  const MIN_RELEASE_YEAR = 1860;
  const MAX_RELEASE_YEAR = new Date().getFullYear() + 1;

  const artworkSrc = $derived(fetchedArtworkDataUrl ?? `${artworkUrl(track.id)}?t=${Date.now()}`);
  const displayName = $derived([track.artist, track.title].filter(Boolean).join(" — "));

  onMount(() => {
    dialogEl?.focus();
    artistInputEl?.select();
  });

  function onArtworkFileSelected(event: Event): void {
    const selected = (event.target as HTMLInputElement).files?.[0] ?? null;
    if (selected && !["image/jpeg", "image/png"].includes(selected.type)) {
      artworkFile = null;
      error = "Artwork must be a JPEG or PNG image";
    } else if (selected && selected.size > 20 * 1024 * 1024) {
      artworkFile = null;
      error = "Artwork must be 20 MB or smaller";
    } else {
      artworkFile = selected;
      fetchedArtworkDataUrl = null;
      error = null;
    }
  }

  function fileFromDataUrl(dataUrl: string): File {
    const match = /^data:(image\/(?:jpeg|png));base64,(.+)$/.exec(dataUrl);
    if (!match) throw new Error("The artwork service returned an invalid image");
    const binary = atob(match[2]);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
    const extension = match[1] === "image/png" ? "png" : "jpg";
    return new File([bytes], `fetched-cover.${extension}`, { type: match[1] });
  }

  async function lookup(kind: "tags" | "artwork"): Promise<void> {
    lookupKind = kind;
    error = null;
    lookupStatus = null;
    try {
      const suggestion = await fetchTagSuggestion(track.id, {
        artist: artist.trim() || null,
        title: title.trim(),
        include_artwork: kind === "artwork",
      });
      if (kind === "tags") {
        if (suggestion.artist) artist = suggestion.artist;
        if (suggestion.title) title = suggestion.title;
        if (suggestion.album) album = suggestion.album;
        if (suggestion.year !== null) year = String(suggestion.year);
        lookupStatus = `Matched via ${suggestion.provider}. Review the corrected fields, then save.`;
      } else if (suggestion.artwork_data_url) {
        artworkFile = fileFromDataUrl(suggestion.artwork_data_url);
        fetchedArtworkDataUrl = suggestion.artwork_data_url;
        artworkMissing = false;
        lookupStatus = `Artwork found via ${suggestion.provider}. Review it, then save.`;
      } else {
        error = `A tag match was found via ${suggestion.provider}, but it has no usable artwork.`;
      }
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "Lookup failed";
    } finally {
      lookupKind = null;
    }
  }

  function normalizedYear(): number | null {
    const trimmed = year.trim();
    if (!trimmed) return null;
    const value = Number(trimmed);
    if (!Number.isInteger(value) || value < MIN_RELEASE_YEAR || value > MAX_RELEASE_YEAR) {
      throw new Error(`Year must be between ${MIN_RELEASE_YEAR} and ${MAX_RELEASE_YEAR}`);
    }
    return value;
  }

  async function save(): Promise<void> {
    const cleanTitle = title.trim();
    if (!cleanTitle) {
      error = "Title is required";
      return;
    }

    saving = true;
    error = null;
    try {
      const cleanYear = normalizedYear();
      if (artworkFile) await uploadTrackArtwork(track.id, artworkFile);
      const updated = await saveTrackTags(track.id, {
        artist: artist.trim() || null,
        title: cleanTitle,
        album: album.trim() || null,
        year: cleanYear,
      });
      onSaved(updated);
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "Failed to save track details";
    } finally {
      saving = false;
    }
  }

  function closeIfIdle(): void {
    if (!saving) onClose();
  }

  function onDialogKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") closeIfIdle();
  }
</script>

<div class="process-dialog-overlay" role="presentation">
  <div
    class="process-dialog tags-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="tags-dialog-title"
    tabindex="-1"
    bind:this={dialogEl}
    onkeydown={onDialogKeydown}
  >
    <div class="process-dialog-header">
      <div>
        <p class="dialog-eyebrow">Track details</p>
        <h2 id="tags-dialog-title">Fix tags &amp; artwork</h2>
        <p class="tags-dialog-track-name">{displayName}</p>
      </div>
      <button class="process-dialog-close" type="button" onclick={closeIfIdle} aria-label="Close tag editor">×</button>
    </div>

    <div class="tags-dialog-content">
      <section class="tags-dialog-artwork-panel" aria-label="Artwork">
        <div class="tags-dialog-artwork-frame">
          {#if !artworkMissing}
            <img src={artworkSrc} alt="Cover artwork" onerror={() => (artworkMissing = true)} />
          {:else}
            <div class="tags-dialog-no-artwork" aria-label="No artwork">
              <span aria-hidden="true">♪</span>
              <strong>No artwork</strong>
            </div>
          {/if}
        </div>
        <label class="tags-dialog-file-button">
          <span>{artworkFile ? "Choose a different image" : "Replace artwork"}</span>
          <input type="file" accept="image/jpeg,image/png" onchange={onArtworkFileSelected} aria-label="Replace artwork" />
        </label>
        <button type="button" class="tags-dialog-fetch-button" onclick={() => void lookup("artwork")} disabled={saving || lookupKind !== null}>
          {lookupKind === "artwork" ? "Finding artwork…" : "Fetch artwork"}
        </button>
        {#if artworkFile}
          <p class="tags-dialog-artwork-selected">Selected: {artworkFile.name}</p>
        {:else}
          <p class="tags-dialog-artwork-help">JPEG or PNG embedded in the audio file.</p>
        {/if}
      </section>

      <form class="tags-dialog-fields" onsubmit={(event) => { event.preventDefault(); void save(); }}>
        <div class="tags-dialog-lookup-row">
          <button type="button" onclick={() => void lookup("tags")} disabled={saving || lookupKind !== null}>
            {lookupKind === "tags" ? "Finding match…" : "Auto-correct tags"}
          </button>
          <span>Uses the current Artist and Title as search hints.</span>
        </div>
        <label class="process-dialog-field tags-dialog-wide-field">
          <span class="process-dialog-label">Artist</span>
          <input class="process-dialog-text" type="text" bind:this={artistInputEl} bind:value={artist} autocomplete="off" />
        </label>
        <label class="process-dialog-field tags-dialog-wide-field">
          <span class="process-dialog-label">Title <span aria-hidden="true">*</span></span>
          <input class="process-dialog-text" type="text" bind:value={title} required autocomplete="off" />
        </label>
        <label class="process-dialog-field tags-dialog-album-field">
          <span class="process-dialog-label">Album</span>
          <input class="process-dialog-text" type="text" bind:value={album} autocomplete="off" />
        </label>
        <label class="process-dialog-field tags-dialog-year-field">
          <span class="process-dialog-label">Year</span>
          <input class="process-dialog-text" type="text" inputmode="numeric" maxlength="4" bind:value={year} autocomplete="off" />
        </label>

        <p class="tags-dialog-safety-note">Only metadata blocks are updated. The encoded audio stream is left unchanged.</p>

        {#if lookupStatus}
          <p class="tags-dialog-lookup-status" aria-live="polite">{lookupStatus}</p>
        {/if}

        {#if error}
          <p class="process-dialog-error" role="alert">{error}</p>
        {/if}

        <div class="process-dialog-actions tags-dialog-actions">
          <button type="button" class="process-dialog-cancel" onclick={closeIfIdle} disabled={saving}>Cancel</button>
          <button type="submit" class="process-dialog-submit" disabled={saving}>
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </form>
    </div>
  </div>
</div>
