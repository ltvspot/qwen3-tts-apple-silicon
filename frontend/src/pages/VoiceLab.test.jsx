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

function createVoiceListPayload(voices) {
  return {
    engine: "qwen3_tts",
    voices,
  };
}

function createClonedVoicesPayload(clonedVoices) {
  return {
    cloned_voices: clonedVoices,
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

function setFileInput(element, files) {
  Object.defineProperty(element, "files", {
    configurable: true,
    value: files,
  });
  element.dispatchEvent(new Event("change", { bubbles: true }));
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
  let confirmMock;
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
    confirmMock = jest.spyOn(window, "confirm").mockReturnValue(true);
    promptMock = jest.spyOn(window, "prompt").mockReturnValue(null);
  });

  afterEach(async () => {
    confirmMock.mockRestore();
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

    fetchMock.mockImplementation((url, options = {}) => {
      if (url === "/api/voice-lab/voices") {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload([
              { display_name: "Ethan", is_cloned: false, name: "Ethan" },
              { display_name: "Nova", is_cloned: false, name: "Nova" },
            ]),
          ),
        );
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(createJsonResponse(createClonedVoicesPayload([])));
      }

      if (url === "/api/voice-lab/test") {
        return Promise.resolve(
          createJsonResponse({
            audio_url: "/audio/voices/test-primary.wav",
            duration_seconds: 4.25,
          }),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

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
      expect(container.textContent).toContain("Generated Preview");
      expect(container.textContent).toContain("Loading preview");
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/voice-lab/voices");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/voice-lab/cloned-voices");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/voice-lab/test");
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({
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
    fetchMock.mockImplementation((url, options = {}) => {
      if (url === "/api/voice-lab/voices") {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload([
              { display_name: "Ethan", is_cloned: false, name: "Ethan" },
              { display_name: "Nova", is_cloned: false, name: "Nova" },
              { display_name: "Aria", is_cloned: false, name: "Aria" },
            ]),
          ),
        );
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(createJsonResponse(createClonedVoicesPayload([])));
      }

      if (url === "/api/voice-lab/test") {
        const parsedBody = JSON.parse(options.body);
        if (parsedBody.voice === "Ethan") {
          return Promise.resolve(
            createJsonResponse({
              audio_url: "/audio/voices/voice-a.wav",
              duration_seconds: 3.8,
            }),
          );
        }

        return Promise.resolve(
          createJsonResponse({
            audio_url: "/audio/voices/voice-b.wav",
            duration_seconds: 3.2,
          }),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

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

    expect(fetchMock).toHaveBeenCalledTimes(2);
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
      expect(container.textContent).toContain("Voice A Preview");
      expect(container.textContent).toContain("Voice B Preview");
    });

    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({
      emotion: "neutral",
      speed: 1,
      text: "Two nearby deliveries make the best A/B test.",
      voice: "Ethan",
    });
    expect(JSON.parse(fetchMock.mock.calls[3][1].body)).toEqual({
      emotion: "dramatic",
      speed: 1,
      text: "Two nearby deliveries make the best A/B test.",
      voice: "Aria",
    });
  });

  test("clones a voice, refreshes the clone list, and exposes it in the audition selector", async () => {
    const voicePayload = createVoiceListPayload([
      { display_name: "Ethan", is_cloned: false, name: "Ethan" },
      { display_name: "Nova", is_cloned: false, name: "Nova" },
    ]);
    const clonedVoices = [];

    fetchMock.mockImplementation((url, options = {}) => {
      if (url === "/api/voice-lab/voices") {
        return Promise.resolve(createJsonResponse(voicePayload));
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(createJsonResponse(createClonedVoicesPayload(clonedVoices)));
      }

      if (url === "/api/voice-lab/clone") {
        const formData = options.body;
        expect(formData.get("voice_name")).toBe("kent-zimering");
        expect(formData.get("display_name")).toBe("Kent Zimering Clone");
        expect(formData.get("transcript")).toBe("This is the exact reference transcript.");
        expect(formData.get("notes")).toBe("Clean studio sample.");
        expect(formData.get("reference_audio").name).toBe("kent.wav");

        voicePayload.voices = [
          ...voicePayload.voices,
          { display_name: "Kent Zimering Clone", is_cloned: true, name: "kent-zimering" },
        ];
        clonedVoices.splice(0, clonedVoices.length, {
          audio_duration_seconds: 2.5,
          created_at: "2026-03-24T00:00:00+00:00",
          created_by: "Tim",
          display_name: "Kent Zimering Clone",
          is_enabled: true,
          notes: "Clean studio sample.",
          voice_name: "kent-zimering",
        });

        return Promise.resolve(
          createJsonResponse({
            audio_duration_seconds: 2.5,
            display_name: "Kent Zimering Clone",
            message: "Voice cloned successfully",
            success: true,
            voice_name: "kent-zimering",
          }),
        );
      }

      if (url === "/api/voice-lab/cloned-voices/kent-zimering") {
        voicePayload.voices = voicePayload.voices.filter((voice) => voice.name !== "kent-zimering");
        clonedVoices.splice(0, clonedVoices.length);
        return Promise.resolve(
          createJsonResponse({
            success: true,
            message: "Voice deleted: kent-zimering",
          }),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain("2 available voices");
    });

    await act(async () => {
      getButtonByText(container, "Clone Voice").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Create a reusable voice reference");
    });

    await act(async () => {
      container.querySelector('button[type="submit"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(container.textContent).toContain("Please fill in all required fields.");

    const voiceIdInput = container.querySelector('input[aria-label="Voice ID"]');
    const displayNameInput = container.querySelector('input[aria-label="Display Name"]');
    const fileInput = container.querySelector('input[aria-label="Reference Audio"]');
    const transcriptInput = container.querySelector('textarea[aria-label="Transcript"]');
    const notesInput = container.querySelector('textarea[aria-label="Notes"]');
    const file = new File(["fake audio"], "kent.wav", { type: "audio/wav" });

    await act(async () => {
      setFormValue(voiceIdInput, "kent-zimering", "input");
      setFormValue(displayNameInput, "Kent Zimering Clone", "input");
      setFileInput(fileInput, [file]);
      setFormValue(transcriptInput, "This is the exact reference transcript.", "input");
      setFormValue(notesInput, "Clean studio sample.", "input");
    });

    await act(async () => {
      container.querySelector('button[type="submit"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Kent Zimering Clone is ready for audition and generation.");
      expect(container.textContent).toContain("kent-zimering");
      expect(container.textContent).toContain("Clean studio sample.");
    });

    await act(async () => {
      getButtonByText(container, "Audition Voices").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("3 available voices");
      expect(
        Array.from(container.querySelector('select[aria-label="Primary voice"]').options).map(
          (option) => option.textContent,
        ),
      ).toContain("Kent Zimering Clone");
    });

    const primaryVoiceSelect = container.querySelector('select[aria-label="Primary voice"]');
    await act(async () => {
      setFormValue(primaryVoiceSelect, "kent-zimering", "change");
    });

    expect(primaryVoiceSelect.value).toBe("kent-zimering");

    await act(async () => {
      getButtonByText(container, "Clone Voice").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Reference library");
    });

    const deleteButton = container.querySelector('[data-voice-name="kent-zimering"] button');
    await act(async () => {
      deleteButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).not.toContain("Kent Zimering Clone");
      expect(container.textContent).toContain("No cloned voices yet");
    });

    expect(window.confirm).toHaveBeenCalledWith('Delete voice "kent-zimering"?');
  });
});
