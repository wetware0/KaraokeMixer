import { fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import LineBandStrip from "./LineBandStrip.svelte";
import { parseLrc } from "../lrcModel";

afterEach(() => {
  vi.restoreAllMocks();
});

// A width deliberately different from any internal fallback constant
// (800) - proves the drag/loop math tracks the container's ACTUAL
// rendered width rather than an assumed fixed pixel space.
function stubRect(width = 1200): void {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0, top: 0, right: width, bottom: 28, width, height: 28, x: 0, y: 0, toJSON: () => ({}),
  });
}

const model = parseLrc(
  "[00:01.00]<00:01.00>Hello<00:01.50> world\n[00:05.00]<00:05.00>Second<00:05.50> line\n",
);

describe("LineBandStrip", () => {
  it("renders one band per timed lyric line, labeled with its text", () => {
    render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10 } });
    expect(screen.getByText("Hello world")).toBeTruthy();
    expect(screen.getByText("Second line")).toBeTruthy();
  });

  it("renders no band for an untimed line", () => {
    const untimed = parseLrc("Hi there\n");
    const { container } = render(LineBandStrip, { props: { model: untimed, viewStart: 0, viewEnd: 10, duration: 10 } });
    expect(container.querySelectorAll(".line-band")).toHaveLength(0);
  });

  it("positions bands as a PERCENTAGE of the view window, not a pixel offset against any fixed width", () => {
    const { container } = render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10 } });
    const bands = container.querySelectorAll(".line-band") as NodeListOf<HTMLElement>;
    expect(bands[0].style.left).toBe("10%"); // 1.0 / 10 * 100
    expect(bands[1].style.left).toBe("50%"); // 5.0 / 10 * 100
  });

  it("band positions are identical regardless of the container's actual rendered width (percentage-based, no measurement needed)", () => {
    const narrow = render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10 } });
    const wide = render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10 } });
    const narrowBand = narrow.container.querySelector(".line-band") as HTMLElement;
    const wideBand = wide.container.querySelector(".line-band") as HTMLElement;
    // Same percentage-based style regardless of any assumed rendered width -
    // this IS the fix: no clientWidth/getBoundingClientRect measurement is
    // even needed to get this right, unlike the old fixed-800px math.
    expect(narrowBand.style.left).toBe(wideBand.style.left);
    expect(narrowBand.style.width).toBe(wideBand.style.width);
  });

  it("clicking a band calls onSelectLine with that line's index", async () => {
    const onSelectLine = vi.fn();
    render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10, onSelectLine } });

    await fireEvent.click(screen.getByText("Hello world"));
    expect(onSelectLine).toHaveBeenCalledWith(0);
  });

  it("marks the currently-selected line's band", () => {
    const { container } = render(LineBandStrip, {
      props: { model, viewStart: 0, viewEnd: 10, duration: 10, selectedLineIndex: 1 },
    });
    const bands = container.querySelectorAll(".line-band") as NodeListOf<HTMLElement>;
    expect(bands[0].className).not.toContain("line-band-selected");
    expect(bands[1].className).toContain("line-band-selected");
  });

  it("alternates tint by RENDERED band position (array index), not raw lineIndex - interleaved untimed lines still alternate consecutively", () => {
    // Line 0 is untimed (no band); lines 1 and 3 are timed (bands); line 2
    // is untimed (no band). If alternation used raw lineIndex, the two
    // rendered bands (lineIndex 1 and 3, both odd) would incorrectly get
    // the SAME tint. Using the rendered array index (0 and 1) instead
    // makes them alternate correctly.
    const interleaved = parseLrc(
      "Untimed intro\n[00:01.00]<00:01.00>First\nUntimed middle\n[00:05.00]<00:05.00>Second\n",
    );
    const { container } = render(LineBandStrip, { props: { model: interleaved, viewStart: 0, viewEnd: 10, duration: 10 } });
    const bands = container.querySelectorAll(".line-band") as NodeListOf<HTMLElement>;
    expect(bands).toHaveLength(2);
    expect(bands[0].className).not.toContain("line-band-odd");
    expect(bands[1].className).toContain("line-band-odd");
  });

  it("renders an instrumental band with a distinct [break] label (not the raw ♪ glyph) - legacy ♪ form", () => {
    const instrumentalModel = parseLrc("[00:03.00]<00:03.00>♪\n");
    const { container } = render(LineBandStrip, {
      props: { model: instrumentalModel, viewStart: 0, viewEnd: 10, duration: 10 },
    });
    const band = container.querySelector(".line-band") as HTMLElement;
    expect(band.className).toContain("line-band-instrumental");
    expect(band.textContent).toContain("[break]");
    expect(band.querySelector(".line-band-label")?.textContent).not.toContain("♪");
  });

  it("renders an instrumental band with a [break] label for a bare-timestamp break line (the canonical on-disk form)", () => {
    const instrumentalModel = parseLrc("[00:03.00]\n");
    const { container } = render(LineBandStrip, {
      props: { model: instrumentalModel, viewStart: 0, viewEnd: 10, duration: 10 },
    });
    const band = container.querySelector(".line-band") as HTMLElement;
    expect(band.className).toContain("line-band-instrumental");
    expect(band.textContent).toContain("[break]");
  });

  it("double-clicking a band calls onLoopChange with the band's span", async () => {
    stubRect();
    const onLoopChange = vi.fn();
    render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10, onLoopChange } });

    await fireEvent.dblClick(screen.getByText("Hello world"));
    expect(onLoopChange).toHaveBeenCalledWith({ start: 1.0, end: 1.5 + 0.4 });
  });

  it("renders a translucent loop overlay across the active loop region (percentage-positioned)", () => {
    const { container } = render(LineBandStrip, {
      props: { model, viewStart: 0, viewEnd: 10, duration: 10, loop: { start: 2, end: 4 } },
    });
    const overlay = container.querySelector(".line-band-strip-loop") as HTMLElement;
    expect(overlay).toBeTruthy();
    expect(overlay.style.left).toBe("20%"); // 2/10*100
    expect(overlay.style.width).toBe("20%"); // (4-2)/10*100
  });

  it("does not render a loop overlay when no loop is set, or when it's entirely outside the view", () => {
    const { container: noLoop } = render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10 } });
    expect(noLoop.querySelector(".line-band-strip-loop")).toBeNull();

    const { container: outsideLoop } = render(LineBandStrip, {
      props: { model, viewStart: 0, viewEnd: 10, duration: 10, loop: { start: 15, end: 18 } },
    });
    expect(outsideLoop.querySelector(".line-band-strip-loop")).toBeNull();
  });

  it("dragging on the background beyond the threshold calls onLoopChange with a span computed from the ACTUAL measured (non-800) width", async () => {
    stubRect(1200);
    const onLoopChange = vi.fn();
    const { container } = render(LineBandStrip, {
      props: { model, viewStart: 0, viewEnd: 10, duration: 10, onLoopChange },
    });
    const strip = container.querySelector(".line-band-strip") as HTMLElement;

    await fireEvent(strip, new MouseEvent("pointerdown", { clientX: 700, bubbles: true }));
    await fireEvent(strip, new MouseEvent("pointermove", { clientX: 750, bubbles: true }));

    const [loopArg] = onLoopChange.mock.calls[0];
    expect(loopArg.start).toBeCloseTo(700 / 1200 * 10, 5);
    expect(loopArg.end).toBeCloseTo(750 / 1200 * 10, 5);
  });

  it("the same drag distance produces a DIFFERENT loop span at a different measured width, proving the math tracks the live width", async () => {
    const onLoopChangeNarrow = vi.fn();
    stubRect(800);
    const narrow = render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10, onLoopChange: onLoopChangeNarrow } });
    const narrowStrip = narrow.container.querySelector(".line-band-strip") as HTMLElement;
    await fireEvent(narrowStrip, new MouseEvent("pointerdown", { clientX: 100, bubbles: true }));
    await fireEvent(narrowStrip, new MouseEvent("pointermove", { clientX: 300, bubbles: true }));

    const onLoopChangeWide = vi.fn();
    stubRect(1200);
    const wide = render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10, onLoopChange: onLoopChangeWide } });
    const wideStrip = wide.container.querySelector(".line-band-strip") as HTMLElement;
    await fireEvent(wideStrip, new MouseEvent("pointerdown", { clientX: 100, bubbles: true }));
    await fireEvent(wideStrip, new MouseEvent("pointermove", { clientX: 300, bubbles: true }));

    const [narrowLoop] = onLoopChangeNarrow.mock.calls[0];
    const [wideLoop] = onLoopChangeWide.mock.calls[0];
    expect(narrowLoop).not.toEqual(wideLoop);
    expect(narrowLoop.end).toBeCloseTo(300 / 800 * 10, 5);
    expect(wideLoop.end).toBeCloseTo(300 / 1200 * 10, 5);
  });

  it("a small movement below the drag threshold does not call onLoopChange", async () => {
    stubRect();
    const onLoopChange = vi.fn();
    const { container } = render(LineBandStrip, {
      props: { model, viewStart: 0, viewEnd: 10, duration: 10, onLoopChange },
    });
    const strip = container.querySelector(".line-band-strip") as HTMLElement;

    await fireEvent(strip, new MouseEvent("pointerdown", { clientX: 700, bubbles: true }));
    await fireEvent(strip, new MouseEvent("pointermove", { clientX: 702, bubbles: true }));

    expect(onLoopChange).not.toHaveBeenCalled();
  });

  it("renders an instrumental band with a remove control that calls onRemoveInstrumental (and does not also select the line)", async () => {
    const instrumentalModel = parseLrc("[00:03.00]<00:03.00>♪\n");
    const onRemoveInstrumental = vi.fn();
    const onSelectLine = vi.fn();
    render(LineBandStrip, {
      props: { model: instrumentalModel, viewStart: 0, viewEnd: 10, duration: 10, onRemoveInstrumental, onSelectLine },
    });

    const removeControl = screen.getByLabelText("Remove instrumental section at line 0");
    await fireEvent.click(removeControl);

    expect(onRemoveInstrumental).toHaveBeenCalledWith(0);
    expect(onSelectLine).not.toHaveBeenCalled();
  });

  it("a non-instrumental band has no remove control", () => {
    const { container } = render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10 } });
    expect(container.querySelector(".line-band-remove")).toBeNull();
  });

  it("each band has an aria-label describing its content", () => {
    const { container } = render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10 } });
    const bands = container.querySelectorAll(".line-band") as NodeListOf<HTMLElement>;
    expect(bands[0].getAttribute("aria-label")).toBe("Line: Hello world");
    expect(bands[1].getAttribute("aria-label")).toBe("Line: Second line");
  });

  it("an instrumental band's aria-label reads 'Instrumental section'", () => {
    const instrumentalModel = parseLrc("[00:03.00]<00:03.00>♪\n");
    const { container } = render(LineBandStrip, {
      props: { model: instrumentalModel, viewStart: 0, viewEnd: 10, duration: 10 },
    });
    expect((container.querySelector(".line-band") as HTMLElement).getAttribute("aria-label")).toBe("Instrumental section");
  });

  it("pressing Enter on a focused band calls onSelectLine once and prevents the native button default", async () => {
    const onSelectLine = vi.fn();
    render(LineBandStrip, { props: { model, viewStart: 0, viewEnd: 10, duration: 10, onSelectLine } });

    const band = screen.getByText("Hello world");
    const event = new KeyboardEvent("keydown", { key: "Enter", bubbles: true });
    const preventDefaultSpy = vi.spyOn(event, "preventDefault");
    band.dispatchEvent(event);

    expect(onSelectLine).toHaveBeenCalledTimes(1);
    expect(onSelectLine).toHaveBeenCalledWith(0);
    expect(preventDefaultSpy).toHaveBeenCalled();
  });
});
