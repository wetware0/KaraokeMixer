import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockCancel, defaultJobDetail } = vi.hoisted(() => ({
  mockCancel: vi.fn().mockResolvedValue(undefined),
  defaultJobDetail: {
    id: 1, recipe: "fake", options: {}, status: "running",
    created_at: "t0", started_at: "t1", finished_at: null,
    items: [
      {
        id: 10, track_id: 3, source_path: "a.flac", status: "running",
        current_stage: "fake_publish",
        stages: [{ name: "fake_publish", status: "running", started_at: "t1", finished_at: null, error: null }],
        error_text: null,
      },
    ],
  },
}));

// A real reactive double (backed by $state in mockJobsStore.svelte.ts), not a
// plain object - see that file's comment for why: reassigning `jobsStore.jobs`
// on a plain object never re-triggers a mounted component's $effect, which
// would make it impossible to regression-test the runaway-refetch bug (the
// bug only manifests once the effect actually re-runs on external updates).
vi.mock("../jobsStore.svelte", async () => {
  const { createMockJobsStore } = await import("./mockJobsStore.svelte");
  return { jobsStore: createMockJobsStore(mockCancel) };
});

vi.mock("../api", () => ({
  fetchJob: vi.fn().mockResolvedValue(defaultJobDetail),
}));

import { fetchJob } from "../api";
import { jobsStore } from "../jobsStore.svelte";
import JobTray from "./JobTray.svelte";

const DISMISSED_JOBS_STORAGE_KEY = "karaoke-mm.dismissedJobs";

describe("JobTray", () => {
  beforeEach(() => {
    mockCancel.mockClear();
    // Test hygiene: a prior test may have overridden fetchJob's
    // implementation (via .mockImplementation(...)) - reset fully and
    // restore the shared default so tests don't leak mock state into each
    // other, then clear call history so call-count assertions start fresh.
    vi.mocked(fetchJob).mockReset().mockResolvedValue(defaultJobDetail);
    jobsStore.jobs = [];
    jobsStore.stageDetails = {};
    // jsdom's localStorage is real and persists across tests in the same
    // file - without clearing it, a dismissal persisted by one test would
    // leak into the next test's fresh JobTray instance (loadDismissedIds()
    // reads real localStorage on mount).
    localStorage.clear();
  });

  it("shows running jobs with item progress and a cancel button", () => {
    jobsStore.jobs = [
      {
        id: 1, recipe: "fake", options: {}, status: "running",
        created_at: "t0", started_at: "t1", finished_at: null,
        item_counts: { queued: 1, running: 1, completed: 2, failed: 0, skipped: 0, cancelled: 0 },
      },
    ];

    render(JobTray);

    expect(screen.getByText("fake")).toBeTruthy();
    expect(screen.getByText("running")).toBeTruthy();
    expect(screen.getByText("2 of 4 items")).toBeTruthy();
    expect(screen.getByText("Cancel")).toBeTruthy();
  });

  it("renders nothing when the store has no queued/running jobs", () => {
    jobsStore.jobs = [];

    const { container } = render(JobTray);

    expect(container.querySelector(".job-tray")).toBeNull();
  });

  it("can hide historical failures while retaining active jobs", () => {
    jobsStore.jobs = [
      {
        id: 1, recipe: "fake", options: {}, status: "running",
        created_at: "t0", started_at: "t1", finished_at: null,
        item_counts: { queued: 0, running: 1, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
      },
      {
        id: 4, recipe: "karaoke", options: {}, status: "failed",
        created_at: "t0", started_at: "t1", finished_at: "t2",
        item_counts: { queued: 0, running: 0, completed: 1, failed: 1, skipped: 0, cancelled: 0 },
      },
    ];

    render(JobTray, { props: { showFailed: false } });

    expect(screen.getByText("fake")).toBeTruthy();
    expect(screen.queryByText("karaoke")).toBeNull();
  });

  it("shows a queued job with a Cancel button that calls cancel and disables on click", async () => {
    jobsStore.jobs = [
      {
        id: 2, recipe: "queued-recipe", options: {}, status: "queued",
        created_at: "t0", started_at: null, finished_at: null,
        item_counts: { queued: 4, running: 0, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
      },
    ];

    render(JobTray);

    const button = screen.getByText("Cancel") as HTMLButtonElement;
    expect(button).toBeTruthy();
    expect(button.disabled).toBe(false);

    await fireEvent.click(button);

    expect(mockCancel).toHaveBeenCalledWith(2);
    expect(button.disabled).toBe(true);
  });

  it("re-enables the cancel button if the cancel call fails", async () => {
    mockCancel.mockRejectedValueOnce(new Error("network error"));
    jobsStore.jobs = [
      {
        id: 3, recipe: "queued-recipe", options: {}, status: "queued",
        created_at: "t0", started_at: null, finished_at: null,
        item_counts: { queued: 4, running: 0, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
      },
    ];

    render(JobTray);

    const button = screen.getByText("Cancel") as HTMLButtonElement;
    await fireEvent.click(button);

    expect(mockCancel).toHaveBeenCalledWith(3);
    expect(button.disabled).toBe(false);
  });

  it("shows the current phase as a friendly numbered step", async () => {
    jobsStore.jobs = [
      {
        id: 1, recipe: "fake", options: {}, status: "running",
        created_at: "t0", started_at: "t1", finished_at: null,
        item_counts: { queued: 0, running: 1, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
      },
    ];

    render(JobTray);

    await waitFor(() => expect(screen.getByText("Step 1 of 1 · Fake publish")).toBeTruthy());
  });

  it("updates progress and clearly labels stage-major phase transitions", async () => {
    vi.mocked(fetchJob).mockResolvedValue({
      id: 8, recipe: "karaoke", options: {}, status: "running",
      created_at: "t0", started_at: "t1", finished_at: null,
      items: [
        {
          id: 80, track_id: 1, source_path: "one.flac", status: "queued", current_stage: null,
          stages: [
            { name: "demucs_separate", status: "completed", started_at: "t1", finished_at: "t2", error: null },
            { name: "karaoke_instrumental", status: "pending", started_at: null, finished_at: null, error: null },
          ],
          error_text: null,
        },
        {
          id: 81, track_id: 2, source_path: "two.flac", status: "running", current_stage: "demucs_separate",
          stages: [
            { name: "demucs_separate", status: "running", started_at: "t2", finished_at: null, error: null },
            { name: "karaoke_instrumental", status: "pending", started_at: null, finished_at: null, error: null },
          ],
          error_text: null,
        },
        {
          id: 82, track_id: 3, source_path: "three.flac", status: "queued", current_stage: null,
          stages: [
            { name: "demucs_separate", status: "pending", started_at: null, finished_at: null, error: null },
            { name: "karaoke_instrumental", status: "pending", started_at: null, finished_at: null, error: null },
          ],
          error_text: null,
        },
      ],
    });
    jobsStore.jobs = [
      {
        id: 8, recipe: "karaoke", options: {}, status: "running",
        created_at: "t0", started_at: "t1", finished_at: null,
        item_counts: { queued: 2, running: 1, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
      },
    ];

    render(JobTray);

    await waitFor(() => expect(screen.getByText("1 of 3 tracks")).toBeTruthy());
    expect(screen.getByText("Step 1 of 2 · Separating stems")).toBeTruthy();

    vi.mocked(fetchJob).mockResolvedValue({
      id: 8, recipe: "karaoke", options: {}, status: "running",
      created_at: "t0", started_at: "t1", finished_at: null,
      items: [
        {
          id: 80, track_id: 1, source_path: "one.flac", status: "queued", current_stage: null,
          stages: [{ name: "demucs_separate", status: "completed", started_at: "t1", finished_at: "t2", error: null }],
          error_text: null,
        },
        {
          id: 81, track_id: 2, source_path: "two.flac", status: "queued", current_stage: null,
          stages: [{ name: "demucs_separate", status: "completed", started_at: "t2", finished_at: "t3", error: null }],
          error_text: null,
        },
        {
          id: 82, track_id: 3, source_path: "three.flac", status: "running", current_stage: "demucs_separate",
          stages: [{ name: "demucs_separate", status: "running", started_at: "t3", finished_at: null, error: null }],
          error_text: null,
        },
      ],
    });
    // Socket item events refresh the summary with a fresh array even though
    // its terminal counts stay unchanged during a stage-major phase.
    jobsStore.jobs = jobsStore.jobs.map((job) => ({ ...job }));

    await waitFor(() => expect(screen.getByText("2 of 3 tracks")).toBeTruthy());

    vi.mocked(fetchJob).mockResolvedValue({
      id: 8, recipe: "karaoke", options: {}, status: "running",
      created_at: "t0", started_at: "t1", finished_at: null,
      items: [
        {
          id: 80, track_id: 1, source_path: "one.flac", status: "running", current_stage: "karaoke_instrumental",
          stages: [
            { name: "demucs_separate", status: "completed", started_at: "t1", finished_at: "t2", error: null },
            { name: "karaoke_instrumental", status: "running", started_at: "t4", finished_at: null, error: null },
          ],
          error_text: null,
        },
        ...[81, 82].map((id) => ({
          id, track_id: id - 79, source_path: `${id}.flac`, status: "queued" as const, current_stage: null,
          stages: [
            { name: "demucs_separate", status: "completed" as const, started_at: "t2", finished_at: "t4", error: null },
            { name: "karaoke_instrumental", status: "pending" as const, started_at: null, finished_at: null, error: null },
          ],
          error_text: null,
        })),
      ],
    });
    jobsStore.jobs = jobsStore.jobs.map((job) => ({ ...job }));

    await waitFor(() => expect(screen.getByText("Step 2 of 2 · Creating karaoke instrumental")).toBeTruthy());
    expect(screen.getByText("0 of 3 tracks")).toBeTruthy();
  });

  it("shows the live stage_progress detail line for a running job", () => {
    jobsStore.jobs = [
      {
        id: 1, recipe: "karaoke", options: {}, status: "running",
        created_at: "t0", started_at: "t1", finished_at: null,
        item_counts: { queued: 0, running: 1, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
      },
    ];
    jobsStore.stageDetails = { 1: "loading htdemucs" };

    render(JobTray);

    expect(screen.getByText("loading htdemucs")).toBeTruthy();
  });

  it("shows a failed job's error_text, and dismissing it hides the tray when nothing else is active", async () => {
    vi.mocked(fetchJob).mockImplementation((jobId: number) =>
      Promise.resolve({
        id: jobId, recipe: "youtube_import", options: {}, status: "failed",
        created_at: "t0", started_at: "t1", finished_at: "t2",
        items: [
          {
            id: 40, track_id: null, source_path: "https://youtube.com/watch?v=abc", status: "failed",
            current_stage: "youtube_import",
            stages: [{ name: "youtube_import", status: "failed", started_at: "t1", finished_at: "t2", error: "boom" }],
            error_text: "Video is age-restricted; configure YouTube cookies in Settings",
          },
        ],
      })
    );
    jobsStore.jobs = [
      {
        id: 4, recipe: "youtube_import", options: {}, status: "failed",
        created_at: "t0", started_at: "t1", finished_at: "t2",
        item_counts: { queued: 0, running: 0, completed: 0, failed: 1, skipped: 0, cancelled: 0 },
      },
    ];

    const { container } = render(JobTray);

    await waitFor(() =>
      expect(screen.getByText(/Video is age-restricted; configure YouTube cookies in Settings/)).toBeTruthy()
    );
    expect(container.querySelector(".job-tray")).not.toBeNull();

    await fireEvent.click(screen.getByLabelText("Dismiss youtube_import"));

    expect(screen.queryByText(/Video is age-restricted; configure YouTube cookies in Settings/)).toBeNull();
    expect(container.querySelector(".job-tray")).toBeNull();
  });

  it("identifies every failed track and stage in a failed batch", async () => {
    vi.mocked(fetchJob).mockResolvedValue({
      id: 82, recipe: "karaoke", options: {}, status: "failed",
      created_at: "t0", started_at: "t1", finished_at: "t2",
      items: [
        {
          id: 820, track_id: 11468, source_path: "D:\\Music\\Eleanor Rigby (2022 mix).flac", status: "failed",
          current_stage: "karaoke_instrumental",
          stages: [{ name: "karaoke_instrumental", status: "failed", started_at: "t1", finished_at: "t2", error: "assertion" }],
          error_text: "AssertionError: stereo needs to be set to True if passing in audio signal that is stereo",
        },
        {
          id: 821, track_id: 11539, source_path: "D:\\Music\\Paperback Writer (2022 stereo mix).flac", status: "failed",
          current_stage: "karaoke_instrumental",
          stages: [{ name: "karaoke_instrumental", status: "failed", started_at: "t1", finished_at: "t2", error: "assertion" }],
          error_text: "AssertionError: stereo needs to be set to True if passing in audio signal that is stereo",
        },
      ],
    });
    jobsStore.jobs = [{
      id: 82, recipe: "karaoke", options: {}, status: "failed",
      created_at: "t0", started_at: "t1", finished_at: "t2",
      item_counts: { queued: 0, running: 0, completed: 301, failed: 2, skipped: 0, cancelled: 0 },
    }];

    render(JobTray);

    await waitFor(() => expect(screen.getByText("2 tracks failed")).toBeTruthy());
    expect(screen.getByText("Eleanor Rigby (2022 mix).flac")).toBeTruthy();
    expect(screen.getByText("Paperback Writer (2022 stereo mix).flac")).toBeTruthy();
    expect(screen.getAllByText(/Creating karaoke instrumental: Surround audio was not accepted/)).toHaveLength(2);
  });

  it("keeps the tray visible for an active job even after a failed job is dismissed", async () => {
    vi.mocked(fetchJob).mockImplementation((jobId: number) =>
      Promise.resolve({
        id: jobId, recipe: jobId === 4 ? "youtube_import" : "fake", options: {},
        status: jobId === 4 ? "failed" : "running",
        created_at: "t0", started_at: "t1", finished_at: jobId === 4 ? "t2" : null,
        items:
          jobId === 4
            ? [
                {
                  id: 40, track_id: null, source_path: "x", status: "failed",
                  current_stage: "youtube_import", stages: [], error_text: "boom",
                },
              ]
            : [],
      })
    );
    jobsStore.jobs = [
      {
        id: 1, recipe: "fake", options: {}, status: "running",
        created_at: "t0", started_at: "t1", finished_at: null,
        item_counts: { queued: 0, running: 1, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
      },
      {
        id: 4, recipe: "youtube_import", options: {}, status: "failed",
        created_at: "t0", started_at: "t1", finished_at: "t2",
        item_counts: { queued: 0, running: 0, completed: 0, failed: 1, skipped: 0, cancelled: 0 },
      },
    ];

    const { container } = render(JobTray);

    await waitFor(() => expect(screen.getByLabelText("Dismiss youtube_import")).toBeTruthy());
    await fireEvent.click(screen.getByLabelText("Dismiss youtube_import"));

    expect(container.querySelector(".job-tray")).not.toBeNull();
    expect(screen.getByText("fake")).toBeTruthy();
  });

  it("regression: does not runaway-refetch a failed job's detail across store update cycles", async () => {
    // Before the fix, the effect's failed-job guard read `details` (a
    // $state), making `details` a tracked dependency of the effect. Because
    // the running-job branch of the SAME effect reassigns `details` on every
    // fetch resolution, the effect would then re-run itself, forever, at
    // network speed - entirely independent of any real store update. This
    // test drives the (now genuinely reactive, see mockJobsStore.svelte.ts)
    // jobsStore.jobs setter through several update cycles and asserts the
    // failed job (id 4) is fetched exactly once, while the running job (id 1)
    // is fetched at most once per cycle - never runaway.
    vi.mocked(fetchJob).mockImplementation((jobId: number) =>
      Promise.resolve({
        ...defaultJobDetail,
        id: jobId,
        recipe: jobId === 4 ? "youtube_import" : "fake",
        status: jobId === 4 ? "failed" : "running",
      })
    );

    const runningJob = {
      id: 1, recipe: "fake", options: {}, status: "running" as const,
      created_at: "t0", started_at: "t1", finished_at: null,
      item_counts: { queued: 0, running: 1, completed: 0, failed: 0, skipped: 0, cancelled: 0 },
    };
    const failedJob = {
      id: 4, recipe: "youtube_import", options: {}, status: "failed" as const,
      created_at: "t0", started_at: "t1", finished_at: "t2",
      item_counts: { queued: 0, running: 0, completed: 0, failed: 1, skipped: 0, cancelled: 0 },
    };

    jobsStore.jobs = [runningJob, failedJob];
    render(JobTray);

    await waitFor(() => expect(vi.mocked(fetchJob).mock.calls.length).toBeGreaterThan(0));

    // Give the runaway-loop bug a real window to manifest: if the effect
    // were (incorrectly) re-triggering itself off of `details` writes, many
    // more calls than 2 would land well within this pause.
    await new Promise((resolve) => setTimeout(resolve, 100));
    const callsAfterSettling = vi.mocked(fetchJob).mock.calls.length;
    expect(callsAfterSettling).toBeLessThan(10);

    // Drive a second store update cycle (e.g. a poll refresh landing new
    // job objects with the same ids/statuses) - the effect legitimately
    // re-runs here because `jobsStore.jobs` itself changed.
    jobsStore.jobs = [{ ...runningJob }, { ...failedJob }];
    await new Promise((resolve) => setTimeout(resolve, 100));

    // And a third cycle.
    jobsStore.jobs = [{ ...runningJob }, { ...failedJob }];
    await new Promise((resolve) => setTimeout(resolve, 100));

    const failedJobCalls = vi.mocked(fetchJob).mock.calls.filter(([id]) => id === 4).length;
    const runningJobCalls = vi.mocked(fetchJob).mock.calls.filter(([id]) => id === 1).length;

    // The failed job's detail is fetched exactly once, ever - never again,
    // no matter how many store update cycles follow.
    expect(failedJobCalls).toBe(1);
    // The running job may legitimately refetch once per store-update cycle
    // (3 cycles here) - bounded, not runaway.
    expect(runningJobCalls).toBeGreaterThanOrEqual(1);
    expect(runningJobCalls).toBeLessThanOrEqual(3);
  });

  describe("dismissal persistence", () => {
    const failedJob = {
      id: 4, recipe: "youtube_import", options: {}, status: "failed" as const,
      created_at: "t0", started_at: "t1", finished_at: "t2",
      item_counts: { queued: 0, running: 0, completed: 0, failed: 1, skipped: 0, cancelled: 0 },
    };

    it("persists a dismissal to localStorage under the karaoke-mm.dismissedJobs key", async () => {
      jobsStore.jobs = [failedJob];

      render(JobTray);
      await waitFor(() => expect(screen.getByLabelText("Dismiss youtube_import")).toBeTruthy());

      await fireEvent.click(screen.getByLabelText("Dismiss youtube_import"));

      expect(JSON.parse(localStorage.getItem(DISMISSED_JOBS_STORAGE_KEY) ?? "[]")).toEqual([4]);
    });

    it("does not show a failed job on a fresh mount when its id is already in localStorage", async () => {
      localStorage.setItem(DISMISSED_JOBS_STORAGE_KEY, JSON.stringify([4]));
      jobsStore.jobs = [failedJob];

      const { container } = render(JobTray);
      // Give the (mocked) fetchJob detail lookup and any effects a chance to
      // run - the failed job must stay hidden throughout, not just at the
      // instant of mount.
      await new Promise((resolve) => setTimeout(resolve, 50));

      expect(container.querySelector(".job-tray")).toBeNull();
      expect(screen.queryByLabelText("Dismiss youtube_import")).toBeNull();
    });

    it("prunes ids no longer present in jobsStore.jobs out of the persisted set", async () => {
      localStorage.setItem(DISMISSED_JOBS_STORAGE_KEY, JSON.stringify([4, 999]));
      jobsStore.jobs = [failedJob]; // id 999 no longer exists anywhere in the store

      render(JobTray);

      await vi.waitFor(() => {
        const stored = JSON.parse(localStorage.getItem(DISMISSED_JOBS_STORAGE_KEY) ?? "[]");
        expect(stored).toEqual([4]);
      });
    });

    it("does not prune persisted dismissals while jobsStore.jobs is still empty (not loaded yet)", async () => {
      // Regression guard: jobsStore.jobs starts empty until its initial
      // /api/jobs fetch resolves. The pruning effect runs immediately on
      // mount - without an explicit guard, that transient empty list looks
      // exactly like "every dismissed id is stale" and would wipe out a
      // perfectly valid persisted set before the real job list ever arrives.
      localStorage.setItem(DISMISSED_JOBS_STORAGE_KEY, JSON.stringify([4]));
      jobsStore.jobs = [];

      render(JobTray);
      await new Promise((resolve) => setTimeout(resolve, 50));

      expect(JSON.parse(localStorage.getItem(DISMISSED_JOBS_STORAGE_KEY) ?? "[]")).toEqual([4]);
    });
  });
});
