import React, { act } from "react";
import ReactDOM from "react-dom/client";
import ProgressHeartbeat from "./ProgressHeartbeat";

describe("ProgressHeartbeat", () => {
  let container;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-03-25T12:00:00Z"));
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    jest.useRealTimers();
    container.remove();
  });

  async function renderHeartbeat(props) {
    await act(async () => {
      root.render(<ProgressHeartbeat {...props} />);
    });
  }

  test("renders elapsed time and updates every second while active", async () => {
    await renderHeartbeat({
      isActive: true,
      progressPercent: 35,
      stage: "Synthesizing audio...",
      startTime: "2026-03-25T11:59:55Z",
    });

    expect(container.textContent).toContain("Elapsed: 0:05");
    expect(container.textContent).toContain("35%");

    await act(async () => {
      jest.advanceTimersByTime(2000);
    });

    expect(container.textContent).toContain("Elapsed: 0:07");
  });

  test("shows determinate and indeterminate progress bars", async () => {
    await renderHeartbeat({
      isActive: true,
      progressPercent: 45,
      stage: "Exporting...",
      startTime: "2026-03-25T11:59:50Z",
    });

    const determinateBar = container.querySelector('[role="progressbar"]');
    expect(determinateBar.getAttribute("aria-valuenow")).toBe("45");

    await renderHeartbeat({
      isActive: true,
      progressPercent: null,
      stage: "Processing...",
      startTime: "2026-03-25T11:59:50Z",
    });

    const indeterminateBar = container.querySelector('[role="progressbar"]');
    expect(indeterminateBar.getAttribute("aria-valuenow")).toBeNull();
    expect(container.textContent).toContain("Live");
  });

  test("stops updating elapsed time when inactive", async () => {
    await renderHeartbeat({
      isActive: true,
      progressPercent: null,
      stage: "Waiting...",
      startTime: "2026-03-25T11:59:58Z",
    });

    expect(container.textContent).toContain("Elapsed: 0:02");

    await renderHeartbeat({
      isActive: false,
      progressPercent: null,
      stage: "Waiting...",
      startTime: "2026-03-25T11:59:58Z",
    });

    await act(async () => {
      jest.advanceTimersByTime(3000);
    });

    expect(container.textContent).toContain("Elapsed: 0:02");
  });
});
