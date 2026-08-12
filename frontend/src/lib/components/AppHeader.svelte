<script lang="ts">
  import SettingsDialog from "./SettingsDialog.svelte";
  import YoutubeImportDialog from "./YoutubeImportDialog.svelte";

  let {
    device,
    showHistoryAction = true,
    historyActive = false,
    onOpenHistory = () => {},
  }: {
    device: "cuda" | "cpu" | null;
    showHistoryAction?: boolean;
    historyActive?: boolean;
    onOpenHistory?: () => void;
  } = $props();
  let showSettings = $state(false);
  let showYoutubeImport = $state(false);
</script>

<header class="app-header">
  <span class="app-header-title">Karaoke Media Manager</span>
  <div class="app-header-actions">
    {#if device}
      <span class="device-indicator device-indicator-{device}">
        <span class="device-indicator-dot"></span>{device}
      </span>
    {/if}
    {#if showHistoryAction}
      <button
        class="app-header-history-button"
        class:active={historyActive}
        aria-current={historyActive ? "page" : undefined}
        onclick={onOpenHistory}
      >Processing history</button>
    {/if}
    <button class="app-header-youtube-button" onclick={() => (showYoutubeImport = true)}>Add from YouTube</button>
    <button class="app-header-settings-button" onclick={() => (showSettings = true)} aria-label="Settings">⚙</button>
  </div>
</header>

{#if showYoutubeImport}
  <YoutubeImportDialog onClose={() => (showYoutubeImport = false)} />
{/if}
{#if showSettings}
  <SettingsDialog onClose={() => (showSettings = false)} />
{/if}
