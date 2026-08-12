<script lang="ts">
  import { onMount, tick } from "svelte";
  import { moveTrack } from "../api";
  import type { LibraryFolder, Track } from "../types";

  let {
    track,
    currentFolder,
    folders,
    onRenamed = () => {},
    onClose = () => {},
  }: {
    track: Track;
    currentFolder: string;
    folders: LibraryFolder[];
    onRenamed?: (track: Track) => void;
    onClose?: () => void;
  } = $props();

  let extension = $state("");
  let stem = $state("");
  let destinationFolder = $state("");
  let saving = $state(false);
  let error = $state<string | null>(null);
  let nameInput = $state<HTMLInputElement | undefined>();

  onMount(async () => {
    const filename = track.relative_path.split(/[\\/]/).at(-1) ?? track.title;
    const extensionIndex = filename.lastIndexOf(".");
    extension = extensionIndex > 0 ? filename.slice(extensionIndex) : "";
    stem = extensionIndex > 0 ? filename.slice(0, extensionIndex) : filename;
    destinationFolder = currentFolder;
    await tick();
    nameInput?.focus();
    nameInput?.select();
  });

  async function rename(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (!stem.trim() || saving) return;
    saving = true;
    error = null;
    try {
      onRenamed(await moveTrack(track.id, destinationFolder, stem));
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      saving = false;
    }
  }
</script>

<div class="media-location-dialog-overlay" role="dialog" aria-modal="true" aria-labelledby="rename-track-title">
  <form class="media-location-dialog" onsubmit={(event) => void rename(event)}>
    <header>
      <div>
        <p class="media-location-dialog-eyebrow">Track file</p>
        <h2 id="rename-track-title">Move or rename file</h2>
      </div>
      <button type="button" class="media-location-dialog-close" aria-label="Close" onclick={onClose} disabled={saving}>×</button>
    </header>

    <p class="media-location-dialog-path">Lyrics and generated stems will move with the original and keep matching its filename.</p>
    <label>
      <span>Location</span>
      <select bind:value={destinationFolder} disabled={saving}>
        {#each folders as folder (folder.path)}
          <option value={folder.path}>{folder.path}</option>
        {/each}
      </select>
    </label>
    <label for="track-filename-stem">
      <span>Filename</span>
      <div class="media-location-filename-field">
        <input id="track-filename-stem" aria-label="Filename" bind:this={nameInput} bind:value={stem} maxlength="180" disabled={saving} autocomplete="off" />
        <span>{extension}</span>
      </div>
    </label>

    {#if error}<p class="media-location-dialog-error" role="alert">{error}</p>{/if}

    <footer>
      <button type="button" onclick={onClose} disabled={saving}>Cancel</button>
      <button type="submit" class="media-location-dialog-primary" disabled={saving || !stem.trim()}>
        {saving ? "Saving…" : "Move / rename"}
      </button>
    </footer>
  </form>
</div>
