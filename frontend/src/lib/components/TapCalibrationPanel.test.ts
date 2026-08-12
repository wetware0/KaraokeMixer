import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";
import TapCalibrationPanel from "./TapCalibrationPanel.svelte";
import type { BeepScheduler, BeepSchedulerOptions } from "../audio/calibration";

// A fake createScheduler: start() is a no-op (nothing fires until the test
// calls fireLatest()), and cancel() is independently spy-able per created
// scheduler instance so Redo (which cancels the old one and creates a new
// one) can be told apart from Cancel.
function fakeSchedulerFactory(beepTimes: number[]) {
  let created = 0;
  const cancelSpies: ReturnType<typeof vi.fn>[] = [];
  let fireLatest = () => {};
  const createScheduler = (
    onBeep: (beepIndex: number, beepTime: number) => void,
    onComplete: () => void,
  ): BeepScheduler => {
    created++;
    const cancelSpy = vi.fn();
    cancelSpies.push(cancelSpy);
    fireLatest = () => {
      beepTimes.forEach((t, i) => onBeep(i, t));
      onComplete();
    };
    return { start: () => {}, cancel: cancelSpy };
  };
  return {
    createScheduler,
    fireLatest: () => fireLatest(),
    get createdCount() { return created; },
    get lastCancelSpy() { return cancelSpies[cancelSpies.length - 1]; },
  };
}

describe("TapCalibrationPanel", () => {
  it("starts idle, and Start calibration begins the running phase", async () => {
    const { createScheduler } = fakeSchedulerFactory([0, 1]);
    render(TapCalibrationPanel, { props: { onApply: vi.fn(), onCancel: vi.fn(), createScheduler } });

    expect(screen.getByText("Start calibration")).toBeTruthy();
    await fireEvent.click(screen.getByText("Start calibration"));

    expect(screen.getByText("Tap")).toBeTruthy();
  });

  it("computes and shows the offset once all beeps complete, using recorded taps", async () => {
    const { createScheduler, fireLatest } = fakeSchedulerFactory([0, 1, 2]);
    let tapCall = 0;
    const schedulerOptions: BeepSchedulerOptions = { now: () => [0.1, 1.2, 2.15][tapCall++] };
    render(TapCalibrationPanel, {
      props: { onApply: vi.fn(), onCancel: vi.fn(), createScheduler, schedulerOptions },
    });

    await fireEvent.click(screen.getByText("Start calibration"));
    await fireEvent.click(screen.getByText("Tap")); // tap 1 at 0.1 -> delta 0.1
    await fireEvent.click(screen.getByText("Tap")); // tap 2 at 1.2 -> delta 0.2
    await fireEvent.click(screen.getByText("Tap")); // tap 3 at 2.15 -> delta 0.15
    fireLatest();

    await waitFor(() => expect(screen.getByText("Computed offset: 0.150s")).toBeTruthy()); // median of 0.1, 0.2, 0.15
  });

  it("Apply calls onApply with the computed offset", async () => {
    const { createScheduler, fireLatest } = fakeSchedulerFactory([0, 1, 2]);
    const onApply = vi.fn();
    let tapCall = 0;
    const schedulerOptions: BeepSchedulerOptions = { now: () => [0.2, 1.2, 2.2][tapCall++] };
    render(TapCalibrationPanel, { props: { onApply, onCancel: vi.fn(), createScheduler, schedulerOptions } });

    await fireEvent.click(screen.getByText("Start calibration"));
    await fireEvent.click(screen.getByText("Tap"));
    await fireEvent.click(screen.getByText("Tap"));
    await fireEvent.click(screen.getByText("Tap"));
    fireLatest();
    await waitFor(() => expect(screen.getByText("Apply")).toBeTruthy());

    await fireEvent.click(screen.getByText("Apply"));

    expect(onApply).toHaveBeenCalledWith(0.2); // median of [0.2, 0.2, 0.2]
  });

  it("disables Apply and shows a retry message when no taps were recorded", async () => {
    const { createScheduler, fireLatest } = fakeSchedulerFactory([0, 1]);
    render(TapCalibrationPanel, { props: { onApply: vi.fn(), onCancel: vi.fn(), createScheduler } });

    await fireEvent.click(screen.getByText("Start calibration"));
    fireLatest(); // no taps recorded

    await waitFor(() => expect(screen.getByText("No taps recorded — try again.")).toBeTruthy());
    expect((screen.getByText("Apply") as HTMLButtonElement).disabled).toBe(true);
  });

  it("Redo cancels the in-progress scheduler and starts a fresh run, mid-calibration", async () => {
    // NOTE: `fake.lastCancelSpy`/`fake.createdCount` are getters - they must
    // be read fresh via `fake.___` at each assertion point, never
    // destructured up front (destructuring a getter reads it exactly once,
    // producing a stale snapshot instead of a live value).
    const fake = fakeSchedulerFactory([0, 1]);
    render(TapCalibrationPanel, { props: { onApply: vi.fn(), onCancel: vi.fn(), createScheduler: fake.createScheduler } });

    await fireEvent.click(screen.getByText("Start calibration"));
    expect(screen.getByText("Tap")).toBeTruthy();
    const firstCancelSpy = fake.lastCancelSpy;

    await fireEvent.click(screen.getByText("Redo"));

    expect(firstCancelSpy).toHaveBeenCalled();
    expect(fake.createdCount).toBe(2);
    expect(screen.getByText("Tap")).toBeTruthy(); // fresh running phase again
  });

  it("Redo also works after completion (not just mid-run)", async () => {
    const fake = fakeSchedulerFactory([0, 1]);
    render(TapCalibrationPanel, { props: { onApply: vi.fn(), onCancel: vi.fn(), createScheduler: fake.createScheduler } });

    await fireEvent.click(screen.getByText("Start calibration"));
    fake.fireLatest();
    await waitFor(() => expect(screen.getByText("Redo")).toBeTruthy());
    const firstCancelSpy = fake.lastCancelSpy;

    await fireEvent.click(screen.getByText("Redo"));

    expect(firstCancelSpy).toHaveBeenCalled();
    expect(fake.createdCount).toBe(2);
    expect(screen.getByText("Tap")).toBeTruthy();
  });

  it("Cancel cancels the scheduler and calls onCancel, from any phase", async () => {
    const fake = fakeSchedulerFactory([0]);
    const onCancel = vi.fn();
    render(TapCalibrationPanel, { props: { onApply: vi.fn(), onCancel, createScheduler: fake.createScheduler } });

    await fireEvent.click(screen.getByText("Start calibration"));
    await fireEvent.click(screen.getByText("Cancel"));

    expect(fake.lastCancelSpy).toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalled();
  });

  it("pressing Space while running records a tap", async () => {
    const { createScheduler, fireLatest } = fakeSchedulerFactory([0, 1, 2]);
    let tapCall = 0;
    const schedulerOptions: BeepSchedulerOptions = { now: () => [0.05, 1.05, 2.05][tapCall++] };
    render(TapCalibrationPanel, { props: { onApply: vi.fn(), onCancel: vi.fn(), createScheduler, schedulerOptions } });

    await fireEvent.click(screen.getByText("Start calibration"));
    await fireEvent.keyDown(window, { code: "Space" });
    await fireEvent.keyDown(window, { code: "Space" });
    await fireEvent.keyDown(window, { code: "Space" });
    fireLatest();

    await waitFor(() => expect(screen.getByText("Computed offset: 0.050s")).toBeTruthy());
  });

  it("pressing Space while idle does nothing (no tap recorded before Start)", async () => {
    const { createScheduler, fireLatest } = fakeSchedulerFactory([0]);
    render(TapCalibrationPanel, { props: { onApply: vi.fn(), onCancel: vi.fn(), createScheduler } });

    await fireEvent.keyDown(window, { code: "Space" });
    await fireEvent.click(screen.getByText("Start calibration"));
    fireLatest(); // zero taps were recorded, even though Space fired once before Start

    await waitFor(() => expect(screen.getByText("No taps recorded — try again.")).toBeTruthy());
  });

  it("unmounting mid-calibration cancels the scheduler so no further beeps fire", async () => {
    const fake = fakeSchedulerFactory([0, 1]);
    const { unmount } = render(TapCalibrationPanel, { props: { onApply: vi.fn(), onCancel: vi.fn(), createScheduler: fake.createScheduler } });

    await fireEvent.click(screen.getByText("Start calibration"));
    expect(screen.getByText("Tap")).toBeTruthy();
    const runningCancelSpy = fake.lastCancelSpy;

    unmount();

    expect(runningCancelSpy).toHaveBeenCalled();
  });
});
