import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";
import KaraokeDisplay from "./KaraokeDisplay.svelte";
import { parseLrc, renderLrc, setWordTime } from "../lrcModel";

describe("KaraokeDisplay", () => {
  it("renders only lyric lines, skipping blank/metadata lines", () => {
    const model = parseLrc("[ar: ABBA]\n\nHi there\n");
    render(KaraokeDisplay, { props: { model, currentTime: 0 } });
    expect(screen.getByText("Hi")).toBeTruthy();
    expect(screen.queryByText("ABBA")).toBeNull();
  });

  it("highlights the currently active word", () => {
    let model = parseLrc("Hi there\n");
    model = setWordTime(model, 0, 0, 1.0);
    model = setWordTime(model, 0, 1, 2.0);

    render(KaraokeDisplay, { props: { model, currentTime: 1.5 } });

    expect(screen.getByText("Hi").className).toContain("karaoke-word-active");
    expect(screen.getByText("there").className).not.toContain("karaoke-word-active");
  });

  it("marks the selected line and word independently from playback", () => {
    const model = parseLrc("Hi there\n");
    render(KaraokeDisplay, {
      props: {
        model,
        currentTime: 0,
        selectedLineIndex: 0,
        selectedWord: { lineIndex: 0, wordIndex: 1 },
      },
    });

    expect(screen.getByText("there").className).toContain("karaoke-word-selected");
    expect(screen.getByText("Hi").className).not.toContain("karaoke-word-selected");
    expect(screen.getByText("there").closest(".karaoke-line")?.className).toContain("karaoke-line-selected");
  });

  it("softly marks only words whose timing evidence needs review", () => {
    const model = parseLrc("Hi there\n");
    render(KaraokeDisplay, {
      props: { model, currentTime: 0, wordConfidence: { "0:0": 42, "0:1": 91 } },
    });

    expect(screen.getByText("Hi").className).toContain("karaoke-word-review");
    expect(screen.getByText("Hi").getAttribute("title")).toBe("Timing confidence 42/100");
    expect(screen.getByText("there").className).not.toContain("karaoke-word-review");
  });

  it("highlights the active whole line when only line timestamps exist", () => {
    const model = parseLrc("[00:01.00]First line\n[00:05.00]Second line\n");
    render(KaraokeDisplay, { props: { model, currentTime: 5.5 } });

    expect(screen.getByText("Second").closest(".karaoke-line")?.className).toContain("karaoke-line-active");
    expect(screen.getByText("First").closest(".karaoke-line")?.className).not.toContain("karaoke-line-active");
    expect(screen.getByText("Second").className).not.toContain("karaoke-word-active");
  });

  it("auto-scrolls as line-timed playback crosses line starts", async () => {
    const model = parseLrc("[00:01.00]First line\n[00:05.00]Second line\n");
    const scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy;
    const { rerender } = render(KaraokeDisplay, { props: { model, currentTime: 0 } });

    await rerender({ model, currentTime: 1.2 });
    await rerender({ model, currentTime: 2.5 });
    expect(scrollSpy).toHaveBeenCalledTimes(1);

    await rerender({ model, currentTime: 5.2 });
    expect(scrollSpy).toHaveBeenCalledTimes(2);
  });

  it("calls onWordClick with the clicked word's line/word index", async () => {
    const model = parseLrc("Hi there\n");
    const onWordClick = vi.fn();
    render(KaraokeDisplay, { props: { model, currentTime: 0, onWordClick } });

    await fireEvent.click(screen.getByText("there"));

    expect(onWordClick).toHaveBeenCalledWith({ lineIndex: 0, wordIndex: 1 });
  });

  it("auto-scrolls the newly active line into view when the active line changes, but not on every tick within the same line", async () => {
    let model = parseLrc("Hi there\nBye now\n");
    model = setWordTime(model, 0, 0, 1.0);
    model = setWordTime(model, 0, 1, 1.5);
    model = setWordTime(model, 1, 0, 5.0);
    model = setWordTime(model, 1, 1, 5.5);

    const scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy;

    const { rerender } = render(KaraokeDisplay, { props: { model, currentTime: 0 } });
    expect(scrollSpy).not.toHaveBeenCalled();

    // Still line 0 active - ticking within the same line must not re-scroll.
    await rerender({ model, currentTime: 1.2 });
    await rerender({ model, currentTime: 1.4 });
    expect(scrollSpy).toHaveBeenCalledTimes(1);
    expect(scrollSpy).toHaveBeenCalledWith({ block: "nearest" });

    // Active line changes to line 1 - exactly one more scroll call.
    await rerender({ model, currentTime: 5.2 });
    expect(scrollSpy).toHaveBeenCalledTimes(2);
  });

  it("without breakLabel (Mixer's usage), an instrumental line still renders as the plain ♪ word button - current behavior is unchanged", () => {
    const model = parseLrc("[00:03.00]<00:03.00>♪\n");
    const { container } = render(KaraokeDisplay, { props: { model, currentTime: 0 } });

    const word = screen.getByText("♪");
    expect(word.tagName).toBe("BUTTON");
    expect(word.className).toContain("karaoke-word");
    expect(container.querySelector(".karaoke-break-label")).toBeNull();
  });

  it("with breakLabel set, an instrumental line renders the label instead of the raw ♪ word", () => {
    const model = parseLrc("[00:03.00]<00:03.00>♪\n");
    const { container } = render(KaraokeDisplay, { props: { model, currentTime: 0, breakLabel: "[break]" } });

    expect(screen.getByText("[break]")).toBeTruthy();
    expect(screen.queryByText("♪")).toBeNull();
    expect(container.querySelector(".karaoke-break-label")).toBeTruthy();
  });

  it("lets a selected break use the same selected-line treatment", async () => {
    const model = parseLrc("[00:03.00]<00:03.00>♪\n");
    const onLineClick = vi.fn();
    render(KaraokeDisplay, {
      props: { model, currentTime: 0, breakLabel: "[break]", selectedLineIndex: 0, onLineClick },
    });

    const label = screen.getByText("[break]");
    expect(label.closest(".karaoke-line")?.className).toContain("karaoke-line-selected");
    await fireEvent.click(label);
    expect(onLineClick).toHaveBeenCalledWith(0);
  });

  it("with breakLabel set but no onRemoveBreak, no remove control is rendered", () => {
    const model = parseLrc("[00:03.00]<00:03.00>♪\n");
    render(KaraokeDisplay, { props: { model, currentTime: 0, breakLabel: "[break]" } });

    expect(screen.queryByLabelText("Remove break")).toBeNull();
  });

  it("with breakLabel and onRemoveBreak set, a × control calls onRemoveBreak with the line's index", async () => {
    const model = parseLrc("Hi there\n[00:03.00]<00:03.00>♪\n");
    const onRemoveBreak = vi.fn();
    render(KaraokeDisplay, { props: { model, currentTime: 0, breakLabel: "[break]", onRemoveBreak } });

    const removeControl = screen.getByLabelText("Remove break");
    await fireEvent.click(removeControl);

    expect(onRemoveBreak).toHaveBeenCalledWith(1);
  });

  it("without breakLabel (Mixer's usage), a bare-timestamp break line renders NO element at all - matching pre-existing behavior for non-lyric lines", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hi\n[00:03.00]\n");
    const { container } = render(KaraokeDisplay, { props: { model, currentTime: 0 } });

    const lines = container.querySelectorAll(".karaoke-line");
    expect(lines).toHaveLength(1); // only the real lyric line - no empty gap for the break
    expect(container.querySelector(".karaoke-break-label")).toBeNull();
  });

  it("with breakLabel set, a bare-timestamp break line renders the [break] label (same as the legacy ♪ form)", () => {
    const model = parseLrc("Hi there\n[00:03.00]\n");
    const onRemoveBreak = vi.fn();
    render(KaraokeDisplay, { props: { model, currentTime: 0, breakLabel: "[break]", onRemoveBreak } });

    expect(screen.getByText("[break]")).toBeTruthy();
    const removeControl = screen.getByLabelText("Remove break");
    expect(removeControl).toBeTruthy();
  });

  it("clicking × on a bare-timestamp break line calls onRemoveBreak with the line's index", async () => {
    const model = parseLrc("Hi there\n[00:03.00]\n");
    const onRemoveBreak = vi.fn();
    render(KaraokeDisplay, { props: { model, currentTime: 0, breakLabel: "[break]", onRemoveBreak } });

    await fireEvent.click(screen.getByLabelText("Remove break"));
    expect(onRemoveBreak).toHaveBeenCalledWith(1);
  });

  it("rendering an instrumental line with breakLabel is purely a display substitution - the model's rendered LRC text is unaffected", () => {
    const content = "[00:00.10]<00:00.10>Hi<00:00.50> there\n[00:03.00]<00:03.00>♪\n";
    const model = parseLrc(content);
    const before = renderLrc(model);

    render(KaraokeDisplay, { props: { model, currentTime: 0, breakLabel: "[break]", onRemoveBreak: vi.fn() } });

    const after = renderLrc(model);
    expect(after).toBe(before);
    expect(after).toBe(content);
  });
});
