import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { JobSummary } from "../types";

vi.mock("../api", () => ({
  fetchJobHistory: vi.fn(),
  fetchJobItems: vi.fn(),
}));

import { fetchJobHistory, fetchJobItems } from "../api";
import ProcessingHistory from "./ProcessingHistory.svelte";

const jobs: JobSummary[] = [
  {
    id: 83,
    recipe: "karaoke",
    options: { processing_profile: "high_quality" },
    status: "completed",
    created_at: "2026-08-08T08:00:00+10:00",
    started_at: "2026-08-08T08:04:00+10:00",
    finished_at: "2026-08-08T21:07:00+10:00",
    item_counts: { queued: 0, running: 0, completed: 306, failed: 0, skipped: 0, cancelled: 0 },
  },
  {
    id: 82,
    recipe: "karaoke",
    options: { processing_profile: "high_quality" },
    status: "failed",
    created_at: "2026-08-07T19:00:00+10:00",
    started_at: "2026-08-07T19:07:00+10:00",
    finished_at: "2026-08-08T08:04:00+10:00",
    item_counts: { queued: 0, running: 0, completed: 301, failed: 3, skipped: 0, cancelled: 0 },
  },
];

describe("ProcessingHistory", () => {
  beforeEach(() => {
    vi.mocked(fetchJobHistory).mockReset().mockResolvedValue({ jobs, total: 2, limit: 25, offset: 0 });
    vi.mocked(fetchJobItems).mockReset().mockResolvedValue({
      items: [
        {
          id: 820,
          track_id: 11468,
          source_path: "D:\\Music\\The Beatles\\Eleanor Rigby (2022 mix).flac",
          status: "failed",
          current_stage: "karaoke_instrumental",
          stages: [
            { name: "karaoke_instrumental", status: "failed", started_at: "t1", finished_at: "t2", error: "assertion" },
          ],
          error_text: "AssertionError: stereo needs to be set to True if passing in audio signal that is stereo",
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });
  });

  it("shows creator-facing run summaries with outcome and duration", async () => {
    render(ProcessingHistory);

    await waitFor(() => expect(screen.getByText("Job 83", { exact: false })).toBeTruthy());
    expect(screen.getAllByText("Karaoke instrumental")).toHaveLength(2);
    expect(screen.getByText("306 of 306")).toBeTruthy();
    expect(screen.getByText(/301 completed.*3 failed/)).toBeTruthy();
    expect(screen.getAllByText("High Quality")).toHaveLength(2);
    expect(screen.getByText("Showing 1–2 of 2 runs")).toBeTruthy();
  });

  it("loads track and phase details only when a run is expanded", async () => {
    render(ProcessingHistory);
    const failedJob = await screen.findByRole("button", { name: /Job 82/ });

    expect(fetchJobItems).not.toHaveBeenCalled();
    await fireEvent.click(failedJob);

    await waitFor(() => expect(screen.getByText("Eleanor Rigby (2022 mix).flac")).toBeTruthy());
    expect(fetchJobItems).toHaveBeenCalledWith(82, { status: "failed", query: "", limit: 50, offset: 0 });
    expect(screen.getByText("Create karaoke instrumental")).toBeTruthy();
    expect(screen.getByText(/Surround audio was not accepted/)).toBeTruthy();
  });

  it("filters and searches history without loading all job details", async () => {
    render(ProcessingHistory);
    await screen.findByText("306 of 306");

    await fireEvent.change(screen.getByLabelText("Status"), { target: { value: "failed" } });
    await waitFor(() => expect(fetchJobHistory).toHaveBeenLastCalledWith({
      status: "failed", query: "", limit: 25, offset: 0,
    }));

    const search = screen.getByPlaceholderText("Workflow, job number, or filename");
    await fireEvent.input(search, { target: { value: "Eleanor Rigby" } });
    await fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(fetchJobHistory).toHaveBeenLastCalledWith({
      status: "failed", query: "Eleanor Rigby", limit: 25, offset: 0,
    }));
    expect(fetchJobItems).not.toHaveBeenCalled();
  });

  it("returns to the preserved Library through the explicit Back action", async () => {
    const onBack = vi.fn();
    render(ProcessingHistory, { props: { onBack } });

    await fireEvent.click(screen.getByRole("button", { name: "← Library" }));

    expect(onBack).toHaveBeenCalledTimes(1);
  });
});
