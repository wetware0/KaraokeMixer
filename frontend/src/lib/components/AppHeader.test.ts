import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import AppHeader from "./AppHeader.svelte";

describe("AppHeader", () => {
  it("shows the app title and device indicator", () => {
    render(AppHeader, { props: { device: "cpu" } });
    expect(screen.getByText("Karaoke Media Manager")).toBeTruthy();
    expect(screen.getByText("cpu")).toBeTruthy();
  });

  it("omits the device indicator until the device is known", () => {
    render(AppHeader, { props: { device: null } });
    expect(screen.queryByText("cpu")).toBeNull();
    expect(screen.queryByText("cuda")).toBeNull();
  });

  it("opens the settings dialog when the gear button is clicked", async () => {
    render(AppHeader, { props: { device: "cpu" } });

    await fireEvent.click(screen.getByLabelText("Settings"));

    expect(screen.getByText("Settings")).toBeTruthy();
  });

  it("opens the YouTube import dialog when 'Add from YouTube' is clicked", async () => {
    render(AppHeader, { props: { device: "cpu" } });

    await fireEvent.click(screen.getByText("Add from YouTube"));

    expect(screen.getByText("Import from YouTube")).toBeTruthy();
  });

  it("opens Processing history from the header action", async () => {
    const onOpenHistory = vi.fn();
    render(AppHeader, { props: { device: "cpu", onOpenHistory } });

    await fireEvent.click(screen.getByRole("button", { name: "Processing history" }));

    expect(onOpenHistory).toHaveBeenCalledTimes(1);
  });
});
