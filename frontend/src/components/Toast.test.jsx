import React, { act } from "react";
import ReactDOM from "react-dom/client";
import Toast from "./Toast";

describe("Toast", () => {
  let container;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    container.remove();
    jest.useRealTimers();
  });

  test("auto-dismisses after the timeout", async () => {
    const onClose = jest.fn();

    await act(async () => {
      root.render(
        <Toast
          message="Settings saved"
          onClose={onClose}
          type="success"
          visible
        />,
      );
    });

    expect(document.body.textContent).toContain("Settings saved");
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => {
      jest.advanceTimersByTime(3999);
    });

    expect(onClose).not.toHaveBeenCalled();

    await act(async () => {
      jest.advanceTimersByTime(1);
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
