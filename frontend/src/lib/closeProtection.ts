import type { JobSummary, LibraryScanStatus } from "./types";

export interface CloseProtectionState {
  jobs: readonly JobSummary[];
  scanStatus: LibraryScanStatus | null;
  trackPlaying: boolean;
}

export function shouldConfirmClose(state: CloseProtectionState): boolean {
  const activeJob = state.jobs.some((job) => job.status === "queued" || job.status === "running");
  const activeScan = state.scanStatus?.status === "queued" || state.scanStatus?.status === "running";
  return activeJob || activeScan || state.trackPlaying;
}

export function protectWindowClose(event: BeforeUnloadEvent, state: CloseProtectionState): void {
  if (!shouldConfirmClose(state)) return;

  // Modern browsers intentionally ignore application-provided wording and
  // display their own standard leave-site confirmation. Both calls are kept:
  // preventDefault() is the current API, while returnValue covers older
  // beforeunload implementations still in use.
  event.preventDefault();
  event.returnValue = "";
}
