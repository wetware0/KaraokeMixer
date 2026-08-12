<script lang="ts">
  import { deleteLibraryFolder } from "../api";
  import type { LibraryFolder } from "../types";

  let {
    folder,
    trackCount,
    onDeleted = () => {},
    onClose = () => {},
  }: {
    folder: LibraryFolder;
    trackCount: number;
    onDeleted?: (trackIds: number[]) => void;
    onClose?: () => void;
  } = $props();

  let deleting = $state(false);
  let error = $state<string | null>(null);

  async function remove(): Promise<void> {
    if (deleting) return;
    deleting = true;
    error = null;
    try {
      const result = await deleteLibraryFolder(folder.path);
      onDeleted(result.deleted_track_ids);
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      deleting = false;
    }
  }
</script>

<div class="media-location-dialog-overlay" role="dialog" aria-modal="true" aria-labelledby="delete-folder-title">
  <section class="media-location-dialog">
    <header>
      <div>
        <p class="media-location-dialog-eyebrow">Library folders</p>
        <h2 id="delete-folder-title">Move folder to Recycle Bin?</h2>
      </div>
      <button type="button" class="media-location-dialog-close" aria-label="Close" onclick={onClose} disabled={deleting}>×</button>
    </header>

    <p>
      <strong>{folder.name}</strong> and everything inside it will be moved to the Windows Recycle Bin.
      {trackCount} library track{trackCount === 1 ? "" : "s"} will be removed from the list.
    </p>
    <p class="media-location-dialog-path">Matching generated lyrics and stems in mirror folders will also be recycled. Files can normally be restored from the Recycle Bin.</p>

    {#if error}<p class="media-location-dialog-error" role="alert">{error}</p>{/if}

    <footer>
      <button type="button" onclick={onClose} disabled={deleting}>Cancel</button>
      <button type="button" class="media-location-dialog-danger" onclick={() => void remove()} disabled={deleting}>
        {deleting ? "Moving…" : "Move to Recycle Bin"}
      </button>
    </footer>
  </section>
</div>
