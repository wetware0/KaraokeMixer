<script lang="ts">
  import { onMount } from "svelte";
  import { browseForFolder, fetchSettings, updateSettings } from "../api";
  import { loadThemePreference, setThemePreference, type ThemePreference } from "../theme";
  import { tracksStore } from "../tracksStore.svelte";
  import type { Settings } from "../types";

  let { onClose }: { onClose: () => void } = $props();

  let mediaRoots = $state<string[]>([]);
  let mirrorRoots = $state<string[]>([]);
  let devicePreference = $state<"auto" | "cuda" | "cpu">("auto");
  let downloadsRoot = $state("");
  let cookieMode = $state<"none" | "browser" | "file">("none");
  let cookieBrowser = $state("");
  let cookieFile = $state("");
  let themePreference = $state<ThemePreference>("system");
  let newMediaRoot = $state("");
  let newMirrorRoot = $state("");
  let loading = $state(true);
  let saving = $state(false);
  let saveError = $state<string | null>(null);
  let browsingFor = $state<"media" | "mirror" | "downloads" | null>(null);
  let rescanError = $state<string | null>(null);
  const scanStatus = $derived(tracksStore.scanStatus);
  const rescanning = $derived(scanStatus?.status === "queued" || scanStatus?.status === "running");

  // Mirrors the backend's PUT /api/settings validation (routes/settings.py):
  // a cookie mode of "browser"/"file" is meaningless without its companion
  // field filled in, so disable Save client-side rather than let the user
  // discover the 422 only after clicking it.
  const cookieFieldMissing = $derived(
    (cookieMode === "browser" && !cookieBrowser.trim()) ||
      (cookieMode === "file" && !cookieFile.trim()),
  );

  onMount(async () => {
    themePreference = loadThemePreference();
    try {
      const settings = await fetchSettings();
      mediaRoots = settings.media_roots;
      mirrorRoots = settings.mirror_roots;
      devicePreference = settings.device_preference;
      downloadsRoot = settings.downloads_root ?? "";
      cookieMode = settings.youtube_cookies?.mode ?? "none";
      cookieBrowser = settings.youtube_cookies?.browser ?? "";
      cookieFile = settings.youtube_cookies?.cookies_file ?? "";
    } catch (error) {
      saveError = error instanceof Error ? error.message : "Failed to load settings";
    } finally {
      loading = false;
    }
  });

  function addMediaRoot() {
    const value = newMediaRoot.trim();
    if (value && !mediaRoots.includes(value)) mediaRoots = [...mediaRoots, value];
    newMediaRoot = "";
  }

  function removeMediaRoot(root: string) {
    mediaRoots = mediaRoots.filter((existing) => existing !== root);
  }

  function addMirrorRoot() {
    const value = newMirrorRoot.trim();
    if (value && !mirrorRoots.includes(value)) mirrorRoots = [...mirrorRoots, value];
    newMirrorRoot = "";
  }

  function removeMirrorRoot(root: string) {
    mirrorRoots = mirrorRoots.filter((existing) => existing !== root);
  }

  function changeTheme(): void {
    setThemePreference(themePreference);
  }

  async function browse(kind: "media" | "mirror" | "downloads"): Promise<void> {
    browsingFor = kind;
    saveError = null;
    try {
      const current = kind === "media" ? newMediaRoot : kind === "mirror" ? newMirrorRoot : downloadsRoot;
      const selected = await browseForFolder(current);
      if (!selected) return;
      if (kind === "media") newMediaRoot = selected;
      else if (kind === "mirror") newMirrorRoot = selected;
      else downloadsRoot = selected;
    } catch (error) {
      saveError = error instanceof Error ? error.message : "Folder picker is unavailable";
    } finally {
      browsingFor = null;
    }
  }

  async function save() {
    saving = true;
    saveError = null;
    try {
      const settings: Settings = {
        media_roots: mediaRoots,
        mirror_roots: mirrorRoots,
        device_preference: devicePreference,
        downloads_root: downloadsRoot.trim() || null,
        youtube_cookies:
          cookieMode === "browser"
            ? { mode: "browser", browser: cookieBrowser.trim() }
            : cookieMode === "file"
              ? { mode: "file", cookies_file: cookieFile.trim() }
              : { mode: "none" },
      };
      await updateSettings(settings);
    } catch (error) {
      saveError = error instanceof Error ? error.message : "Failed to save settings";
    } finally {
      saving = false;
    }
  }

  async function runRescan() {
    rescanError = null;
    try {
      await tracksStore.startRescan();
    } catch (error) {
      rescanError = error instanceof Error ? error.message : "Library rescan failed";
    }
  }

  function onOverlayKeydown(event: KeyboardEvent) {
    if (event.key === "Escape") onClose();
  }
</script>

<div class="settings-dialog-overlay" role="presentation" onkeydown={onOverlayKeydown}>
  <div
    class="settings-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="settings-dialog-title"
    tabindex="-1"
    onkeydown={(event) => event.stopPropagation()}
  >
    <div class="settings-dialog-header">
      <h2 id="settings-dialog-title">Settings</h2>
      <button class="settings-dialog-close" onclick={onClose} aria-label="Close">×</button>
    </div>

    {#if loading}
      <p class="settings-dialog-loading">Loading…</p>
    {:else}
      <div class="settings-dialog-body">
        <section class="settings-section">
          <h3>Appearance</h3>
          <label class="settings-field">
            <span>Theme</span>
            <select aria-label="Theme" bind:value={themePreference} onchange={changeTheme}>
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
          <p class="settings-hint">System follows your Windows or browser appearance.</p>
        </section>

        <section class="settings-section">
          <h3>Media roots</h3>
          <ul class="settings-root-list">
            {#each mediaRoots as root (root)}
              <li>
                <span>{root}</span>
                <button onclick={() => removeMediaRoot(root)} aria-label={`Remove ${root}`}>×</button>
              </li>
            {/each}
          </ul>
          <div class="settings-root-add">
            <input type="text" placeholder="D:\Media\..." bind:value={newMediaRoot} />
            <button type="button" aria-label="Browse for media root" onclick={() => void browse("media")} disabled={browsingFor !== null}>Browse…</button>
            <button onclick={addMediaRoot} aria-label="Add media root">Add</button>
          </div>
        </section>

        <section class="settings-section">
          <h3>Mirror roots</h3>
          <ul class="settings-root-list">
            {#each mirrorRoots as root (root)}
              <li>
                <span>{root}</span>
                <button onclick={() => removeMirrorRoot(root)} aria-label={`Remove ${root}`}>×</button>
              </li>
            {/each}
          </ul>
          <div class="settings-root-add">
            <input type="text" placeholder="D:\Stems\..." bind:value={newMirrorRoot} />
            <button type="button" aria-label="Browse for mirror root" onclick={() => void browse("mirror")} disabled={browsingFor !== null}>Browse…</button>
            <button onclick={addMirrorRoot} aria-label="Add mirror root">Add</button>
          </div>
        </section>

        <label class="settings-field">
          <span>Device preference</span>
          <select bind:value={devicePreference}>
            <option value="auto">auto</option>
            <option value="cuda">cuda</option>
            <option value="cpu">cpu</option>
          </select>
        </label>

        <div class="settings-field">
          <span>Downloads root</span>
          <div class="settings-path-row">
            <input aria-label="Downloads root" class="process-dialog-text" type="text" placeholder="D:\Downloads\..." bind:value={downloadsRoot} />
            <button type="button" aria-label="Browse for downloads root" onclick={() => void browse("downloads")} disabled={browsingFor !== null}>Browse…</button>
          </div>
        </div>

        <label class="settings-field">
          <span>YouTube cookies</span>
          <select bind:value={cookieMode}>
            <option value="none">none</option>
            <option value="browser">browser</option>
            <option value="file">cookies.txt file</option>
          </select>
        </label>

        {#if cookieMode === "browser"}
          <label class="settings-field">
            <span>Browser</span>
            <input class="process-dialog-text" type="text" placeholder="chrome" bind:value={cookieBrowser} />
          </label>
        {:else if cookieMode === "file"}
          <label class="settings-field">
            <span>Cookies file</span>
            <input class="process-dialog-text" type="text" placeholder="C:\cookies.txt" bind:value={cookieFile} />
          </label>
        {/if}

        {#if cookieFieldMissing}
          <p class="settings-hint">
            {cookieMode === "browser" ? "Enter a browser name" : "Enter a cookies file path"} to save.
          </p>
        {/if}

        {#if saveError}
          <p class="settings-error">{saveError}</p>
        {/if}

        <div class="settings-actions">
          <button onclick={save} disabled={saving || cookieFieldMissing}>{saving ? "Saving…" : "Save"}</button>
          <button onclick={runRescan} disabled={rescanning}>{rescanning ? "Scanning in background…" : "Rescan"}</button>
        </div>

        {#if scanStatus && scanStatus.scan_id > 0}
          <p class="settings-rescan-result">
            {#if rescanning}
              Scanning in the background — {scanStatus.tracks_found} track{scanStatus.tracks_found === 1 ? "" : "s"} found so far.
              {scanStatus.media_roots_scanned} of {scanStatus.media_roots_total} folders complete.
            {:else if scanStatus.status === "completed"}
              Found {scanStatus.tracks_found} tracks across {scanStatus.media_roots_scanned} media root{scanStatus.media_roots_scanned === 1 ? "" : "s"}.
              {#if scanStatus.unavailable_roots.length > 0}
                Unavailable: {scanStatus.unavailable_roots.join(", ")}
              {/if}
            {:else if scanStatus.status === "failed"}
              Scan failed: {scanStatus.error ?? "Unknown error"}
            {/if}
          </p>
        {/if}
        {#if rescanError}<p class="settings-error" role="alert">{rescanError}</p>{/if}
      </div>
    {/if}
  </div>
</div>
