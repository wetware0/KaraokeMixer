<script lang="ts">
  import { onMount } from "svelte";
  import { saveLrc } from "../api";
  import { tracksStore } from "../tracksStore.svelte";
  import type { Track } from "../types";

  let { track, onCreated, onClose }: {
    track: Track;
    onCreated: (track: Track) => void;
    onClose: () => void;
  } = $props();

  let text = $state("");
  let submitting = $state(false);
  let errorMessage = $state<string | null>(null);
  let textareaEl: HTMLTextAreaElement | undefined;

  onMount(() => {
    textareaEl?.focus();
  });

  async function create(): Promise<void> {
    if (!text.trim()) return;
    submitting = true;
    errorMessage = null;
    try {
      const result = await saveLrc(track.id, text, { create: "beside" });
      const createdTrack = result.track ?? track;
      if (result.track) tracksStore.replaceTrack(result.track);
      onCreated(createdTrack);
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : String(err);
    } finally {
      submitting = false;
    }
  }

  function onOverlayKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") onClose();
  }
</script>

<div class="process-dialog-overlay" role="presentation" onkeydown={onOverlayKeydown}>
  <div
    class="process-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="create-lyrics-dialog-title"
    tabindex="-1"
    onkeydown={(event) => {
      if (event.key !== "Escape") event.stopPropagation();
    }}
  >
    <div class="process-dialog-header">
      <h2 id="create-lyrics-dialog-title">Create lyrics for {track.title}</h2>
      <button class="process-dialog-close" onclick={onClose} aria-label="Close">×</button>
    </div>

    <div class="process-dialog-body">
      <label class="process-dialog-field">
        <span class="process-dialog-label">Lyrics</span>
        <textarea
          class="process-dialog-textarea"
          rows="10"
          placeholder="Paste plain lyric lines…"
          bind:value={text}
          bind:this={textareaEl}
        ></textarea>
      </label>

      {#if errorMessage}
        <p class="process-dialog-error">{errorMessage}</p>
      {/if}
    </div>

    <div class="process-dialog-actions">
      <button class="process-dialog-cancel" onclick={onClose}>Cancel</button>
      <button class="process-dialog-submit" onclick={create} disabled={submitting || !text.trim()}>
        {submitting ? "Creating…" : "Create"}
      </button>
    </div>
  </div>
</div>
