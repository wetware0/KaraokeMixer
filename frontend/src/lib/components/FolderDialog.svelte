<script lang="ts">
  import { onMount, tick } from "svelte";
  import { createLibraryFolder, renameLibraryFolder } from "../api";
  import type { LibraryFolder } from "../types";

  let {
    mode,
    folder = null,
    folders,
    defaultParent = "",
    onSaved = () => {},
    onClose = () => {},
  }: {
    mode: "create" | "rename";
    folder?: LibraryFolder | null;
    folders: LibraryFolder[];
    defaultParent?: string;
    onSaved?: (folder: LibraryFolder) => void;
    onClose?: () => void;
  } = $props();

  let name = $state("");
  let parentPath = $state("");
  let saving = $state(false);
  let error = $state<string | null>(null);
  let nameInput = $state<HTMLInputElement | undefined>();

  onMount(async () => {
    name = mode === "rename" ? folder?.name ?? "" : "";
    parentPath = defaultParent || folders[0]?.path || "";
    await tick();
    nameInput?.focus();
    nameInput?.select();
  });

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (!name.trim() || saving) return;
    saving = true;
    error = null;
    try {
      const saved = mode === "create"
        ? await createLibraryFolder(parentPath, name)
        : await renameLibraryFolder(folder!.path, name);
      onSaved(saved);
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      saving = false;
    }
  }
</script>

<div class="media-location-dialog-overlay" role="dialog" aria-modal="true" aria-labelledby="folder-dialog-title">
  <form class="media-location-dialog" onsubmit={(event) => void save(event)}>
    <header>
      <div>
        <p class="media-location-dialog-eyebrow">Library folders</p>
        <h2 id="folder-dialog-title">{mode === "create" ? "Create folder" : "Rename folder"}</h2>
      </div>
      <button type="button" class="media-location-dialog-close" aria-label="Close" onclick={onClose} disabled={saving}>×</button>
    </header>

    {#if mode === "create"}
      <label>
        <span>Location</span>
        <select bind:value={parentPath} disabled={saving}>
          {#each folders as candidate (candidate.path)}
            <option value={candidate.path}>{candidate.path}</option>
          {/each}
        </select>
      </label>
    {:else if folder}
      <p class="media-location-dialog-path">In {folder.path.slice(0, Math.max(0, folder.path.length - folder.name.length)).replace(/\/$/, "")}</p>
    {/if}

    <label>
      <span>Folder name</span>
      <input bind:this={nameInput} bind:value={name} maxlength="180" disabled={saving} autocomplete="off" />
    </label>

    {#if error}<p class="media-location-dialog-error" role="alert">{error}</p>{/if}

    <footer>
      <button type="button" onclick={onClose} disabled={saving}>Cancel</button>
      <button type="submit" class="media-location-dialog-primary" disabled={saving || !name.trim() || (mode === "create" && !parentPath)}>
        {saving ? "Saving…" : mode === "create" ? "Create folder" : "Rename folder"}
      </button>
    </footer>
  </form>
</div>
