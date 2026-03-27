import React, { act } from "react";
import ReactDOM from "react-dom/client";
import ConfirmDialog from "./ConfirmDialog";

function getButtonByText(label, rootNode = document.body) {
  return Array.from(rootNode.querySelectorAll("button")).find((button) =>
    button.textContent.includes(label),
  );
}

function setFormValue(element, value, eventName) {
  const prototype = Object.getPrototypeOf(element);
  const valueSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;

  valueSetter.call(element, value);
  element.dispatchEvent(new Event(eventName, { bubbles: true }));
}

describe("ConfirmDialog", () => {
  let container;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    container.remove();
  });

  test("renders in confirm mode", async () => {
    const onCancel = jest.fn();
    const onConfirm = jest.fn();

    await act(async () => {
      root.render(
        <ConfirmDialog
          confirmLabel="Delete Voice"
          message="Delete this cloned voice permanently?"
          onCancel={onCancel}
          onConfirm={onConfirm}
          open
          title="Delete Cloned Voice"
        />,
      );
    });

    expect(document.body.textContent).toContain("Delete Cloned Voice");
    expect(document.body.textContent).toContain(
      "Delete this cloned voice permanently?",
    );
    expect(getButtonByText("Cancel")).toBeTruthy();
    expect(getButtonByText("Delete Voice")).toBeTruthy();

    await act(async () => {
      getButtonByText("Delete Voice").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  test("renders in prompt mode with an input", async () => {
    const onCancel = jest.fn();
    const onConfirm = jest.fn();

    await act(async () => {
      root.render(
        <ConfirmDialog
          confirmLabel="Save Preset"
          message="Enter a name for this voice configuration preset."
          onCancel={onCancel}
          onConfirm={onConfirm}
          open
          promptDefault="Warm Narrator"
          promptLabel="Preset name"
          promptMode
          theme="light"
          title="Save Voice Preset"
        />,
      );
    });

    const promptInput = document.body.querySelector(
      'input[aria-label="Preset name"]',
    );
    expect(promptInput).not.toBeNull();
    expect(promptInput.value).toBe("Warm Narrator");

    await act(async () => {
      setFormValue(promptInput, "Evening Read", "input");
    });

    await act(async () => {
      getButtonByText("Save Preset").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(onConfirm).toHaveBeenCalledWith("Evening Read");
  });

  test("renders in alert mode with a single button", async () => {
    const onCancel = jest.fn();
    const onConfirm = jest.fn();

    await act(async () => {
      root.render(
        <ConfirmDialog
          alertMode
          confirmLabel="OK"
          message="Please select a chapter from the list before generating audio."
          onCancel={onCancel}
          onConfirm={onConfirm}
          open
          title="No Chapter Selected"
        />,
      );
    });

    const buttons = Array.from(document.body.querySelectorAll("button")).filter(
      (button) => button.textContent.trim() === "OK",
    );
    expect(buttons).toHaveLength(1);
    expect(document.body.textContent).not.toContain("Cancel");

    await act(async () => {
      buttons[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
