import type { JobSummary } from "../types";

// Test-only helper. The real jobsStore (jobsStore.svelte.ts) backs `jobs`
// with a genuine $state rune so external updates (poll refreshes, socket
// events) trigger Svelte's reactivity. A plain mocked object (`{ jobs: [] }`)
// does NOT reproduce that: reassigning a property on a non-reactive object
// never re-runs a component's $derived/$effect. This factory - in a
// `.svelte.ts` file so the rune compiles - gives JobTray.test.ts a jobsStore
// double whose `jobs`/`stageDetails` getters are real reactive sources, so a
// test can drive multiple "store update cycles" and observe how many times
// JobTray's $effect actually re-runs (the JobTray runaway-fetch regression
// can only be exercised with real reactivity backing the store).
export function createMockJobsStore(cancel: (jobId: number) => Promise<void>) {
  let jobs = $state<JobSummary[]>([]);
  let stageDetails = $state<Record<number, string>>({});

  return {
    get jobs(): JobSummary[] {
      return jobs;
    },
    set jobs(value: JobSummary[]) {
      jobs = value;
    },
    get stageDetails(): Record<number, string> {
      return stageDetails;
    },
    set stageDetails(value: Record<number, string>) {
      stageDetails = value;
    },
    cancel,
  };
}
