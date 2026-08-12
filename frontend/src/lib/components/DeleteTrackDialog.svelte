<script lang="ts">
  import { onMount } from "svelte";
  import { deleteTrack } from "../api";
  import type { Track } from "../types";

  let {
    track,
    onDeleted = () => {},
    onClose = () => {},
  }: {
    track: Track;
    onDeleted?: (trackId: number) => void;
    onClose?: () => void;
  } = $props();

  let includeOutputs = $state(true);
  let deleting = $state(false);
  let error = $state<string | null>(null);
  let dialogEl = $state<HTMLDivElement | undefined>();

  onMount(() => dialogEl?.focus());

  async function confirmDelete(): Promise<void> {
    deleting = true;
    error = null;
    try {
      await deleteTrack(track.id, includeOutputs);
      onDeleted(track.id);
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      deleting = false;
    }
  }

  function closeIfIdle(): void {
    if (!deleting) onClose();
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") closeIfIdle();
  }
</script>

<div
  class="delete-track-dialog-overlay"
  role="dialog"
  aria-modal="true"
  aria-labelledby="delete-track-title"
  tabindex="-1"
  bind:this={dialogEl}
  onkeydown={onKeydown}
>
  <section class="delete-track-dialog">
    <header>
      <h2 id="delete-track-title">Move track to Recycle Bin?</h2>
      <button type="button" class="delete-track-dialog-close" aria-label="Close" onclick={closeIfIdle} disabled={deleting}>×</button>
    </header>

    <p>
      The original audio for <strong>{track.artist ? `${track.artist} — ` : ""}{track.title}</strong>
      will be removed from the Library and moved to the Windows Recycle Bin.
    </p>
    <label class="delete-track-outputs-option">
      <input type="checkbox" bind:checked={includeOutputs} disabled={deleting} />
      Also recycle generated stems and lyric files
    </label>
    <p class="delete-track-dialog-note">Files can normally be restored from the Recycle Bin.</p>

    {#if error}<p class="delete-track-dialog-error" role="alert">{error}</p>{/if}

    <footer>
      <button type="button" onclick={closeIfIdle} disabled={deleting}>Cancel</button>
      <button type="button" class="danger-button" onclick={() => void confirmDelete()} disabled={deleting}>
        {deleting ? "Moving…" : "Move to Recycle Bin"}
      </button>
    </footer>
  </section>
</div>
