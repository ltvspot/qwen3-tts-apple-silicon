import React, { act } from "react";
import ReactDOM from "react-dom/client";
import VoiceCloneForm from "./VoiceCloneForm";

function setFormValue(element, value, eventName) {
  const prototype = Object.getPrototypeOf(element);
  const valueSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;

  valueSetter.call(element, value);
  element.dispatchEvent(new Event(eventName, { bubbles: true }));
}

function setFileInput(element, files) {
  Object.defineProperty(element, "files", {
    configurable: true,
    value: files,
  });
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

class MockXMLHttpRequest {
  static latest = null;

  constructor() {
    this.open = jest.fn();
    this.send = jest.fn();
    this.upload = {};
    MockXMLHttpRequest.latest = this;
  }

  triggerUploadProgress(loaded, total) {
    this.upload.onprogress?.({
      lengthComputable: true,
      loaded,
      total,
    });
  }

  triggerUploadComplete() {
    this.upload.onloadend?.();
  }

  triggerSuccess(payload) {
    this.status = 200;
    this.response = payload;
    this.responseText = JSON.stringify(payload);
    this.onload?.();
  }
}

describe("VoiceCloneForm", () => {
  let container;
  let onCloned;
  let originalXHR;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
    onCloned = jest.fn().mockResolvedValue(undefined);
    originalXHR = global.XMLHttpRequest;
    global.XMLHttpRequest = MockXMLHttpRequest;
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    global.XMLHttpRequest = originalXHR;
    container.remove();
  });

  async function renderForm() {
    await act(async () => {
      root.render(<VoiceCloneForm onCloned={onCloned} />);
    });
  }

  test("shows upload and processing progress during voice cloning", async () => {
    await renderForm();

    const voiceIdInput = container.querySelector('input[aria-label="Voice ID"]');
    const displayNameInput = container.querySelector('input[aria-label="Display Name"]');
    const fileInput = container.querySelector('input[aria-label="Reference Audio"]');
    const transcriptInput = container.querySelector('textarea[aria-label="Transcript"]');
    const file = new File(["fake audio"], "kent.wav", { type: "audio/wav" });

    await act(async () => {
      setFormValue(voiceIdInput, "kent-zimering", "input");
      setFormValue(displayNameInput, "Kent Zimering Clone", "input");
      setFileInput(fileInput, [file]);
      setFormValue(transcriptInput, "Exact transcript.", "input");
    });

    await act(async () => {
      container.querySelector('button[type="submit"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    const request = MockXMLHttpRequest.latest;
    expect(request.open).toHaveBeenCalledWith("POST", "/api/voice-lab/clone");

    await act(async () => {
      request.triggerUploadProgress(50, 100);
    });

    expect(container.textContent).toContain("Uploading reference audio...");
    expect(container.textContent).toContain("50%");

    await act(async () => {
      request.triggerUploadComplete();
    });

    expect(container.textContent).toContain("Processing voice clone...");

    await act(async () => {
      request.triggerSuccess({
        audio_duration_seconds: 2.5,
        display_name: "Kent Zimering Clone",
        message: "Voice cloned successfully",
        success: true,
        voice_name: "kent-zimering",
      });
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Kent Zimering Clone is ready for audition and generation.");
    expect(onCloned).toHaveBeenCalledWith({
      audio_duration_seconds: 2.5,
      display_name: "Kent Zimering Clone",
      message: "Voice cloned successfully",
      success: true,
      voice_name: "kent-zimering",
    });
  });
});
