import React, { act } from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import VoiceLab from "./VoiceLab";

function createJsonResponse(payload, options = {}) {
  return {
    json: async () => payload,
    ok: options.ok ?? true,
    status: options.status ?? 200,
  };
}

function getButtonByText(container, label) {
  return Array.from(container.querySelectorAll("button")).find((button) =>
    button.textContent.includes(label),
  );
}

function setFormValue(element, value, eventName) {
  const prototype = Object.getPrototypeOf(element);
  const valueSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;

  valueSetter.call(element, value);
  element.dispatchEvent(new Event(eventName, { bubbles: true }));
}

async function waitFor(assertion, timeout = 2000) {
  const startTime = Date.now();

  while (true) {
    try {
      assertion();
      return;
    } catch (error) {
      if (Date.now() - startTime > timeout) {
        throw error;
      }

      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 20);
        });
      });
    }
  }
}

describe("VoiceLab page", () => {
  let container;
  let fetchMock;
  let promptMock;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;

    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);

    fetchMock = jest.fn();
    global.fetch = fetchMock;

    window.localStorage.clear();
    promptMock = jest.spyOn(window, "prompt").mockReturnValue(null);
  });

  afterEach(async () => {
    promptMock.mockRestore();
    window.localStorage.clear();

    await act(async () => {
      root.unmount();
    });

    container.remove();
    delete global.fetch;
  });

  async function renderVoiceLab() {
    await act(async () => {
      root.render(
        <MemoryRouter
          future={{
            v7_relativeSplatPath: true,
            v7_startTransition: true,
          }}
        >
          <VoiceLab />
        </MemoryRouter>,
      );
    });
  }

  test("loads voices, generates a preview, and manages saved presets", async () => {
    window.localStorage.setItem(
      "voicePresets",
      JSON.stringify([
        {
          emotion: "warm",
          id: "preset-1",
          name: "Warm Narrator",
          speed: 0.95,
          voice: "Nova",
        },
      ]),
    );

    fetchMock
      .mockResolvedValueOnce(
        createJsonResponse({
          engine: "qwen3_tts",
          voices: [{ name: "Ethan" }, { name: "Nova" }],
        }),
      )
      .mockResolvedValueOnce(
        createJsonResponse({
          audio_url: "/audio/voices/test-primary.wav",
          duration_seconds: 4.25,
        }),
      );

    promptMock.mockReturnValue("Evening Read");

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain("2 available voices");
      expect(container.textContent).toContain("Warm Narrator");
    });

    const presetCard = container.querySelector('[data-preset-id="preset-1"]');
    const loadButton = presetCard.querySelector('button[data-action="load"]');

    await act(async () => {
      loadButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(container.querySelector('select[aria-label="Primary voice"]').value).toBe("Nova");
    expect(container.querySelector('input[aria-label="Primary emotion"]').value).toBe("warm");
    expect(container.querySelector('input[aria-label="Primary speed"]').value).toBe("0.95");

    const testText = container.querySelector('textarea[aria-label="Test text"]');
    await act(async () => {
      setFormValue(testText, "A quieter sentence for a more intimate audition.", "input");
    });

    await act(async () => {
      getButtonByText(container, "Generate Preview").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(container.textContent).toContain("Generated Preview");
      expect(container.textContent).toContain("Loading preview");
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/voice-lab/voices");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/voice-lab/test");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      emotion: "warm",
      speed: 0.95,
      text: "A quieter sentence for a more intimate audition.",
      voice: "Nova",
    });

    await act(async () => {
      getButtonByText(container, "Save Preset").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Evening Read");
    });

    const storedPresets = JSON.parse(window.localStorage.getItem("voicePresets"));
    expect(storedPresets).toHaveLength(2);

    const deleteButton = presetCard.querySelector('button[data-action="delete"]');
    await act(async () => {
      deleteButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).not.toContain("Warm Narrator");
    });
  });

  test("validates empty input and supports compare mode generation for both clips", async () => {
    fetchMock
      .mockResolvedValueOnce(
        createJsonResponse({
          engine: "qwen3_tts",
          voices: [{ name: "Ethan" }, { name: "Nova" }, { name: "Aria" }],
        }),
      )
      .mockResolvedValueOnce(
        createJsonResponse({
          audio_url: "/audio/voices/voice-a.wav",
          duration_seconds: 3.8,
        }),
      )
      .mockResolvedValueOnce(
        createJsonResponse({
          audio_url: "/audio/voices/voice-b.wav",
          duration_seconds: 3.2,
        }),
      );

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain("3 available voices");
    });

    const testText = container.querySelector('textarea[aria-label="Test text"]');
    await act(async () => {
      setFormValue(testText, "   ", "input");
    });

    await act(async () => {
      getButtonByText(container, "Generate Preview").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("Please enter text to generate audio.");

    await act(async () => {
      setFormValue(testText, "Two nearby deliveries make the best A/B test.", "input");
    });

    await act(async () => {
      getButtonByText(container, "Compare Voices").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    const compareVoiceSelect = container.querySelector('select[aria-label="Compare voice"]');
    await act(async () => {
      setFormValue(compareVoiceSelect, "Aria", "change");
    });

    const compareEmotionInput = container.querySelector('input[aria-label="Compare emotion"]');
    await act(async () => {
      setFormValue(compareEmotionInput, "dramatic", "input");
    });

    await act(async () => {
      getButtonByText(container, "Generate Voice A").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await act(async () => {
      getButtonByText(container, "Generate Voice B").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3);
      expect(container.textContent).toContain("Voice A Preview");
      expect(container.textContent).toContain("Voice B Preview");
    });

    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      emotion: "neutral",
      speed: 1,
      text: "Two nearby deliveries make the best A/B test.",
      voice: "Ethan",
    });
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({
      emotion: "dramatic",
      speed: 1,
      text: "Two nearby deliveries make the best A/B test.",
      voice: "Aria",
    });
  });
});
