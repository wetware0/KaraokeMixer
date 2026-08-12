import { fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import StemLane from "./StemLane.svelte";

const fakeCtx = { clearRect: vi.fn(), fillRect: vi.fn(), fillStyle: "" };

afterEach(() => {
  vi.restoreAllMocks();
});

function renderLane(overrides: Partial<Record<string, unknown>> = {}) {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
  return render(StemLane, {
    props: {
      id: "vocals", label: "Vocals", peaks: [{ min: -1, max: 1 }], gain: 0.8, muted: false, solo: false,
      ...overrides,
    },
  });
}

describe("StemLane", () => {
  it("shows the lane label", () => {
    renderLane();
    expect(screen.getByText("Vocals")).toBeTruthy();
  });

  it("calls onGainChange with the new numeric value when the slider moves", async () => {
    const onGainChange = vi.fn();
    renderLane({ onGainChange });

    const slider = screen.getByLabelText("Vocals volume") as HTMLInputElement;
    slider.value = "0.3";
    await fireEvent.input(slider);

    expect(onGainChange).toHaveBeenCalledWith(0.3);
  });

  it("calls onMuteToggle when the mute button is clicked", async () => {
    const onMuteToggle = vi.fn();
    renderLane({ onMuteToggle });

    await fireEvent.click(screen.getByText("M"));

    expect(onMuteToggle).toHaveBeenCalled();
  });

  it("calls onSoloToggle when the solo button is clicked", async () => {
    const onSoloToggle = vi.fn();
    renderLane({ onSoloToggle });

    await fireEvent.click(screen.getByText("S"));

    expect(onSoloToggle).toHaveBeenCalled();
  });

  it("marks the mute button active when muted is true", () => {
    renderLane({ muted: true });
    expect(screen.getByText("M").className).toContain("active");
  });

  it("explains mute and solo actions on hover and to assistive technology", () => {
    renderLane();

    const mute = screen.getByRole("button", { name: "Mute Vocals" });
    const solo = screen.getByRole("button", { name: "Solo Vocals" });
    expect(mute.getAttribute("title")).toBe("Mute Vocals — silence this track");
    expect(solo.getAttribute("title")).toBe("Solo Vocals — hear this track by itself");
    expect(mute.getAttribute("aria-pressed")).toBe("false");
    expect(solo.getAttribute("aria-pressed")).toBe("false");
  });

  it("changes the hover explanation when mute and solo are active", () => {
    renderLane({ muted: true, solo: true });

    const mute = screen.getByRole("button", { name: "Unmute Vocals" });
    const solo = screen.getByRole("button", { name: "Unsolo Vocals" });
    expect(mute.getAttribute("title")).toBe("Unmute Vocals — restore this track to the mix");
    expect(solo.getAttribute("title")).toBe("Unsolo Vocals — return to the full mix");
    expect(mute.getAttribute("aria-pressed")).toBe("true");
    expect(solo.getAttribute("aria-pressed")).toBe("true");
  });
});
