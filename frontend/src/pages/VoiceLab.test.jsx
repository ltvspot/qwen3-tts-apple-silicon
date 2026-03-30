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

function createVoiceListPayload(voices, overrides = {}) {
  return {
    engine: "qwen3_tts",
    voices,
    ...overrides,
  };
}

function createEngineListPayload(overrides = {}) {
  return {
    engines: [
      {
        available: true,
        description:
          "Alibaba's Qwen3-TTS with emotion control and VoiceDesign.",
        display_name: "Qwen3 TTS",
        download_command: null,
        name: "qwen3_tts",
      },
      {
        available: true,
        description:
          "Mistral's Voxtral TTS with 20 built-in voices across 9 languages.",
        display_name: "Voxtral TTS",
        download_command: null,
        name: "voxtral_tts",
      },
    ],
    ...overrides,
  };
}

function createClonedVoicesPayload(clonedVoices) {
  return {
    cloned_voices: clonedVoices,
  };
}

function createVoiceDesignStatusPayload(overrides = {}) {
  return {
    available: true,
    download_command: null,
    model_name: "Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit",
    ...overrides,
  };
}

function createDeferredResponse() {
  let resolve;

  return {
    promise: new Promise((resolver) => {
      resolve = resolver;
    }),
    resolve,
  };
}

class MockXMLHttpRequest {
  static latest = null;
  static onSend = null;

  constructor() {
    this.open = jest.fn();
    this.send = jest.fn((body) => {
      const formData = body;
      MockXMLHttpRequest.onSend?.(formData);
      expect(formData.get("voice_name")).toBe("james-mitchell");
      expect(formData.get("display_name")).toBe("James Mitchell Clone");
      expect(formData.get("transcript")).toBe(
        "This is the exact reference transcript.",
      );
      expect(formData.get("notes")).toBe("Clean studio sample.");
      expect(formData.get("reference_audio").name).toBe("kent.wav");

      this.status = 200;
      this.response = {
        audio_duration_seconds: 2.5,
        display_name: "James Mitchell Clone",
        message: "Voice cloned successfully",
        success: true,
        voice_name: "james-mitchell",
      };
      this.responseText = JSON.stringify(this.response);
      this.onload?.();
    });
    this.upload = {};
    MockXMLHttpRequest.latest = this;
  }
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
  let originalXHR;
  let promptMock;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;

    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);

    fetchMock = jest.fn();
    global.fetch = fetchMock;
    originalXHR = global.XMLHttpRequest;

    window.localStorage.clear();
    confirmMock = jest.spyOn(window, "confirm").mockImplementation(() => {
      throw new Error("Native confirm should not be used.");
    });
    promptMock = jest.spyOn(window, "prompt").mockImplementation(() => {
      throw new Error("Native prompt should not be used.");
    });
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
    global.XMLHttpRequest = originalXHR;
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
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
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
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
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

    expect(
      container.querySelector('select[aria-label="Primary voice"]').value,
    ).toBe("Nova");
    expect(
      container.querySelector('input[aria-label="Primary emotion"]').value,
    ).toBe("warm");
    expect(
      container.querySelector('input[aria-label="Primary speed"]').value,
    ).toBe("0.95");

    const testText = container.querySelector(
      'textarea[aria-label="Test text"]',
    );
    await act(async () => {
      setFormValue(
        testText,
        "A quieter sentence for a more intimate audition.",
        "input",
      );
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

    expect(fetchMock.mock.calls[0][0]).toBe("/api/voice-lab/engines");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/voice-lab/voices?engine=qwen3_tts",
    );
    expect(fetchMock.mock.calls[2][0]).toBe("/api/voice-lab/cloned-voices");
    expect(fetchMock.mock.calls[3][0]).toBe(
      "/api/voice-lab/voice-design/status",
    );
    expect(fetchMock.mock.calls[4][0]).toBe("/api/voice-lab/test");
    expect(JSON.parse(fetchMock.mock.calls[4][1].body)).toEqual({
      engine: "qwen3_tts",
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
      expect(document.body.textContent).toContain("Save Voice Preset");
    });

    const presetDialog = document.body.querySelector('[role="dialog"]');
    const presetNameInput = document.body.querySelector(
      'input[aria-label="Preset name"]',
    );
    await act(async () => {
      setFormValue(presetNameInput, "Evening Read", "input");
    });

    await act(async () => {
      getButtonByText(presetDialog, "Save Preset").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Evening Read");
    });

    expect(window.prompt).not.toHaveBeenCalled();

    const storedPresets = JSON.parse(
      window.localStorage.getItem("voicePresets"),
    );
    expect(storedPresets).toHaveLength(2);

    const deleteButton = presetCard.querySelector(
      'button[data-action="delete"]',
    );
    await act(async () => {
      deleteButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).not.toContain("Warm Narrator");
    });
  });

  test("shows the selected voice description below the voice selector", async () => {
    fetchMock.mockImplementation((url) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload([
              {
                description:
                  "Bright, clear American male. Energetic midrange with sunny tone. Great for contemporary fiction and young adult narration.",
                display_name: "Ethan",
                is_cloned: false,
                name: "Ethan",
              },
              {
                description:
                  "Warm male with slightly husky brightness. Lively personality with natural warmth. Excellent for character-driven stories and dialogue-heavy books.",
                display_name: "Marcus",
                is_cloned: false,
                name: "Marcus",
              },
            ]),
          ),
        );
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain(
        "Bright, clear American male. Energetic midrange with sunny tone. Great for contemporary fiction and young adult narration.",
      );
    });

    const primaryVoiceSelect = container.querySelector(
      'select[aria-label="Primary voice"]',
    );
    await act(async () => {
      setFormValue(primaryVoiceSelect, "Marcus", "change");
    });

    await waitFor(() => {
      expect(container.textContent).toContain(
        "Warm male with slightly husky brightness. Lively personality with natural warmth. Excellent for character-driven stories and dialogue-heavy books.",
      );
    });
  });

  test("switches to Voxtral, groups voices by language, and hides emotion controls", async () => {
    fetchMock.mockImplementation((url, options = {}) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
      }

      if (url === "/api/voice-lab/voices?engine=qwen3_tts") {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload([
              { display_name: "Ethan", is_cloned: false, name: "Ethan" },
              { display_name: "Nova", is_cloned: false, name: "Nova" },
            ]),
          ),
        );
      }

      if (url === "/api/voice-lab/voices?engine=voxtral_tts") {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload(
              [
                {
                  display_name: "Casual Male",
                  language: "en-US",
                  name: "Casual Male",
                  voice_type: "built_in",
                },
                {
                  display_name: "Cheerful Female",
                  language: "en-US",
                  name: "Cheerful Female",
                  voice_type: "built_in",
                },
                {
                  display_name: "French Male",
                  language: "fr-FR",
                  name: "French Male",
                  voice_type: "built_in",
                },
              ],
              { engine: "voxtral_tts" },
            ),
          ),
        );
      }

      if (url === "/api/voice-lab/test") {
        return Promise.resolve(
          createJsonResponse({
            audio_url: "/audio/voices/voxtral-preview.wav",
            duration_seconds: 3.2,
          }),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain("Qwen3 TTS");
      expect(container.textContent).toContain("Voxtral TTS");
    });

    await act(async () => {
      getButtonByText(container, "Voxtral TTS").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(
        container.querySelector('select[aria-label="Primary voice"]').value,
      ).toBe("Casual Male");
      expect(container.textContent).toContain(
        "Voxtral TTS does not support emotion/style control.",
      );
    });

    expect(
      container.querySelector('input[aria-label="Primary emotion"]'),
    ).toBeNull();
    expect(container.textContent).not.toContain(
      "Create Custom Voices from Text Descriptions",
    );

    const primaryVoiceSelect = container.querySelector(
      'select[aria-label="Primary voice"]',
    );
    expect(
      primaryVoiceSelect.querySelector('optgroup[label="English"]'),
    ).toBeTruthy();
    expect(
      primaryVoiceSelect.querySelector('optgroup[label="French"]'),
    ).toBeTruthy();

    await act(async () => {
      getButtonByText(container, "Generate Preview").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Generated Preview");
    });

    expect(JSON.parse(fetchMock.mock.calls[5][1].body)).toEqual({
      engine: "voxtral_tts",
      emotion: "neutral",
      speed: 1,
      text: "This is the Alexandria Audiobook Narrator. Test your voice settings here with any text you like.",
      voice: "Casual Male",
    });
  });

  test("shows install instructions when Voxtral is unavailable", async () => {
    fetchMock.mockImplementation((url) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(
          createJsonResponse(
            createEngineListPayload({
              engines: [
                createEngineListPayload().engines[0],
                {
                  available: false,
                  description:
                    "Mistral's Voxtral TTS with 20 built-in voices across 9 languages.",
                  display_name: "Voxtral TTS",
                  download_command:
                    "huggingface-cli download mlx-community/Voxtral-4B-TTS-2603-mlx-bf16 --local-dir models/Voxtral-4B-TTS-2603-mlx-bf16",
                  name: "voxtral_tts",
                },
              ],
            }),
          ),
        );
      }

      if (url.startsWith("/api/voice-lab/voices")) {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload([
              { display_name: "Ethan", is_cloned: false, name: "Ethan" },
            ]),
          ),
        );
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain("Voxtral TTS");
    });

    await act(async () => {
      getButtonByText(container, "Voxtral TTS").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain(
        "Voxtral TTS is not installed yet.",
      );
      expect(container.textContent).toContain(
        "huggingface-cli download mlx-community/Voxtral-4B-TTS-2603-mlx-bf16 --local-dir models/Voxtral-4B-TTS-2603-mlx-bf16",
      );
    });
  });

  test("validates empty input and supports compare mode generation for both clips", async () => {
    fetchMock.mockImplementation((url, options = {}) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
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
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
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

    const testText = container.querySelector(
      'textarea[aria-label="Test text"]',
    );
    await act(async () => {
      setFormValue(testText, "   ", "input");
    });

    await act(async () => {
      getButtonByText(container, "Generate Preview").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(container.textContent).toContain(
      "Please enter text to generate audio.",
    );

    await act(async () => {
      setFormValue(
        testText,
        "Two nearby deliveries make the best A/B test.",
        "input",
      );
    });

    await act(async () => {
      getButtonByText(container, "Compare Voices").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    const compareVoiceSelect = container.querySelector(
      'select[aria-label="Compare voice"]',
    );
    await act(async () => {
      setFormValue(compareVoiceSelect, "Aria", "change");
    });

    const compareEmotionInput = container.querySelector(
      'input[aria-label="Compare emotion"]',
    );
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

    expect(JSON.parse(fetchMock.mock.calls[4][1].body)).toEqual({
      engine: "qwen3_tts",
      emotion: "neutral",
      speed: 1,
      text: "Two nearby deliveries make the best A/B test.",
      voice: "Ethan",
    });
    expect(JSON.parse(fetchMock.mock.calls[5][1].body)).toEqual({
      engine: "qwen3_tts",
      emotion: "dramatic",
      speed: 1,
      text: "Two nearby deliveries make the best A/B test.",
      voice: "Aria",
    });
  });

  test("shows a helpful busy message when preview generation returns 503", async () => {
    fetchMock.mockImplementation((url) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload([
              { display_name: "Ethan", is_cloned: false, name: "Ethan" },
            ]),
          ),
        );
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
      }

      if (url === "/api/voice-lab/test") {
        return Promise.resolve(
          createJsonResponse(
            {
              detail:
                "Voice preview is temporarily unavailable — audiobook generation is using the GPU. Please try again in a moment or pause generation first.",
            },
            {
              ok: false,
              status: 503,
            },
          ),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain("1 available voice");
    });

    await act(async () => {
      getButtonByText(container, "Generate Preview").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain(
        "Voice preview is temporarily unavailable — audiobook generation is using the GPU. Please try again in a moment or pause generation first.",
      );
      expect(container.textContent).toContain("Retry");
    });
  });

  test("shows a reconnect message when the preview request cannot reach the server", async () => {
    fetchMock.mockImplementation((url) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload([
              { display_name: "Ethan", is_cloned: false, name: "Ethan" },
            ]),
          ),
        );
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
      }

      if (url === "/api/voice-lab/test") {
        return Promise.reject(new TypeError("Failed to fetch"));
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain("1 available voice");
    });

    await act(async () => {
      getButtonByText(container, "Generate Preview").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain(
        "Could not connect to the server. It may have restarted — please refresh the page and try again.",
      );
    });
  });

  test("renders the voice designer, applies a preset chip, and saves a designed voice", async () => {
    let designedVoiceSaved = false;

    fetchMock.mockImplementation((url, options = {}) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
        const voices = [
          { display_name: "Ethan", is_cloned: false, name: "Ethan" },
          { display_name: "Nova", is_cloned: false, name: "Nova" },
        ];
        if (designedVoiceSaved) {
          voices.push({
            display_name: "Narrator One",
            is_cloned: false,
            name: "narrator-one",
            voice_type: "designed",
          });
        }
        return Promise.resolve(
          createJsonResponse(createVoiceListPayload(voices)),
        );
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
      }

      if (url === "/api/voice-lab/voice-design/test") {
        return Promise.resolve(
          createJsonResponse({
            audio_url: "/audio/voices/designed-preview.wav",
            duration_seconds: 3.6,
          }),
        );
      }

      if (url === "/api/voice-lab/voice-design/save") {
        designedVoiceSaved = true;
        expect(JSON.parse(options.body)).toEqual({
          display_name: "Narrator One",
          voice_description:
            "A deep, authoritative American male narrator with a warm baritone, clear diction, and a steady measured pace",
          voice_name: "Narrator One",
        });
        return Promise.resolve(
          createJsonResponse({
            success: true,
            voice_name: "narrator-one",
            display_name: "Narrator One",
            message: "Designed voice saved: Narrator One",
          }),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain(
        "Create Custom Voices from Text Descriptions",
      );
    });

    await act(async () => {
      getButtonByText(
        container,
        "A deep, authoritative American male narrator with a warm baritone, clear diction, and a steady measured pace",
      ).dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(
      container.querySelector('textarea[aria-label="Voice description"]').value,
    ).toBe(
      "A deep, authoritative American male narrator with a warm baritone, clear diction, and a steady measured pace",
    );

    const designerButtons = Array.from(
      container.querySelectorAll("button"),
    ).filter((button) => button.textContent.includes("Generate Preview"));
    await act(async () => {
      designerButtons[1].dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Voice Designer Preview");
      expect(container.textContent).toContain("Save This Voice");
    });

    await act(async () => {
      getButtonByText(container, "Save This Voice").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(document.body.textContent).toContain("Save Designed Voice");
    });

    const voiceNameInput = document.body.querySelector(
      'input[aria-label="Voice name"]',
    );
    await act(async () => {
      setFormValue(voiceNameInput, "Narrator One", "input");
    });

    await act(async () => {
      getButtonByText(document.body, "Save Voice").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain(
        "Narrator One is ready in the Designed Voices list.",
      );
      expect(
        container.querySelector('select[aria-label="Primary voice"]').value,
      ).toBe("narrator-one");
      expect(
        container.querySelector(
          'select[aria-label="Primary voice"] optgroup[label="Designed Voices"]',
        ),
      ).not.toBeNull();
    });
  });

  test("locks and unlocks a designed voice from the designed voice library", async () => {
    const clonedVoices = [];

    fetchMock.mockImplementation((url, options = {}) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload([
              { display_name: "Ethan", is_cloned: false, name: "Ethan" },
              {
                description: "A steady, resonant command voice.",
                display_name: "Commander",
                is_cloned: false,
                name: "commander",
                voice_type: "designed",
              },
            ]),
          ),
        );
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload(clonedVoices)),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
      }

      if (url === "/api/voice-lab/voice-design/commander/lock") {
        if (options.method === "DELETE") {
          clonedVoices.splice(0, clonedVoices.length);
          return Promise.resolve(
            createJsonResponse({
              success: true,
              message: "Voice unlocked: commander",
            }),
          );
        }

        clonedVoices.splice(0, clonedVoices.length, {
          audio_duration_seconds: 2.5,
          created_at: "2026-03-30T00:00:00+00:00",
          created_by: "Tim",
          display_name: "Commander (Locked)",
          is_enabled: true,
          notes: "Auto-locked sample.",
          voice_name: "commander",
        });
        return Promise.resolve(
          createJsonResponse({
            success: true,
            voice_name: "commander",
            display_name: "Commander (Locked)",
            audio_duration_seconds: 2.5,
            message: "Voice locked: Commander",
          }),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain("Designed Voice Library");
      expect(
        container.querySelector('[data-designed-voice-name="commander"]')
          .textContent,
      ).toContain("Unlocked");
    });

    await act(async () => {
      getButtonByText(
        container.querySelector('[data-designed-voice-name="commander"]'),
        "Lock Voice",
      ).dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain(
        "Commander (Locked) is now locked for production generation.",
      );
      expect(
        container.querySelector('[data-designed-voice-name="commander"]')
          .textContent,
      ).toContain("Locked");
      expect(
        container.querySelector('[data-designed-voice-name="commander"]')
          .textContent,
      ).toContain("Unlock Voice");
    });

    await act(async () => {
      getButtonByText(
        container.querySelector('[data-designed-voice-name="commander"]'),
        "Unlock Voice",
      ).dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain(
        "commander now uses its saved text description again.",
      );
      expect(
        container.querySelector('[data-designed-voice-name="commander"]')
          .textContent,
      ).toContain("Unlocked");
      expect(
        container.querySelector('[data-designed-voice-name="commander"]')
          .textContent,
      ).toContain("Lock Voice");
    });
  });

  test("shows the install banner when the VoiceDesign model is not available", async () => {
    fetchMock.mockImplementation((url) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload([
              { display_name: "Ethan", is_cloned: false, name: "Ethan" },
            ]),
          ),
        );
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(
            createVoiceDesignStatusPayload({
              available: false,
              download_command:
                "huggingface-cli download mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit --local-dir /tmp/models/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit",
            }),
          ),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();

    await waitFor(() => {
      expect(container.textContent).toContain(
        "VoiceDesign model not installed.",
      );
      expect(container.textContent).toContain(
        "huggingface-cli download mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit --local-dir /tmp/models/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit",
      );
    });
  });

  test("clones a voice, refreshes the clone list, and exposes it in the audition selector", async () => {
    global.XMLHttpRequest = MockXMLHttpRequest;
    const voicePayload = createVoiceListPayload([
      { display_name: "Ethan", is_cloned: false, name: "Ethan" },
      { display_name: "Nova", is_cloned: false, name: "Nova" },
    ]);
    const clonedVoices = [];
    let cloneCreated = false;
    MockXMLHttpRequest.onSend = () => {
      cloneCreated = true;
    };

    fetchMock.mockImplementation((url, options = {}) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
        if (
          cloneCreated &&
          !voicePayload.voices.some((voice) => voice.name === "james-mitchell")
        ) {
          voicePayload.voices = [
            ...voicePayload.voices,
            {
              display_name: "James Mitchell Clone",
              is_cloned: true,
              name: "james-mitchell",
            },
          ];
        }
        return Promise.resolve(createJsonResponse(voicePayload));
      }

      if (url === "/api/voice-lab/cloned-voices") {
        if (cloneCreated && clonedVoices.length === 0) {
          clonedVoices.splice(0, clonedVoices.length, {
            audio_duration_seconds: 2.5,
            created_at: "2026-03-24T00:00:00+00:00",
            created_by: "Tim",
            display_name: "James Mitchell Clone",
            is_enabled: true,
            notes: "Clean studio sample.",
            voice_name: "james-mitchell",
          });
        }
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload(clonedVoices)),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
      }

      if (url === "/api/voice-lab/clone") {
        throw new Error("Clone requests should use XMLHttpRequest.");
      }

      if (url === "/api/voice-lab/cloned-voices/james-mitchell") {
        cloneCreated = false;
        voicePayload.voices = voicePayload.voices.filter(
          (voice) => voice.name !== "james-mitchell",
        );
        clonedVoices.splice(0, clonedVoices.length);
        return Promise.resolve(
          createJsonResponse({
            success: true,
            message: "Voice deleted: james-mitchell",
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
      expect(container.textContent).toContain(
        "Create a reusable voice reference",
      );
    });

    await act(async () => {
      container
        .querySelector('button[type="submit"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(container.textContent).toContain(
      "Please fill in all required fields.",
    );

    const voiceIdInput = container.querySelector(
      'input[aria-label="Voice ID"]',
    );
    const displayNameInput = container.querySelector(
      'input[aria-label="Display Name"]',
    );
    const fileInput = container.querySelector(
      'input[aria-label="Reference Audio"]',
    );
    const transcriptInput = container.querySelector(
      'textarea[aria-label="Transcript"]',
    );
    const notesInput = container.querySelector('textarea[aria-label="Notes"]');
    const file = new File(["fake audio"], "kent.wav", { type: "audio/wav" });

    await act(async () => {
      setFormValue(voiceIdInput, "james-mitchell", "input");
      setFormValue(displayNameInput, "James Mitchell Clone", "input");
      setFileInput(fileInput, [file]);
      setFormValue(
        transcriptInput,
        "This is the exact reference transcript.",
        "input",
      );
      setFormValue(notesInput, "Clean studio sample.", "input");
    });

    await act(async () => {
      container
        .querySelector('button[type="submit"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain(
        "James Mitchell Clone is ready for audition and generation.",
      );
      expect(container.textContent).toContain("james-mitchell");
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
        Array.from(
          container.querySelector('select[aria-label="Primary voice"]').options,
        ).map((option) => option.textContent),
      ).toContain("James Mitchell Clone");
    });

    const primaryVoiceSelect = container.querySelector(
      'select[aria-label="Primary voice"]',
    );
    await act(async () => {
      setFormValue(primaryVoiceSelect, "james-mitchell", "change");
    });

    expect(primaryVoiceSelect.value).toBe("james-mitchell");

    await act(async () => {
      getButtonByText(container, "Clone Voice").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Reference library");
    });

    const deleteButton = container.querySelector(
      '[data-voice-name="james-mitchell"] button',
    );
    await act(async () => {
      deleteButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(document.body.textContent).toContain("Delete Cloned Voice");
      expect(document.body.textContent).toContain(
        'Are you sure you want to delete "james-mitchell"? This cannot be undone.',
      );
    });

    expect(window.confirm).not.toHaveBeenCalled();

    await act(async () => {
      getButtonByText(document.body, "Delete Voice").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).not.toContain("James Mitchell Clone");
      expect(container.textContent).toContain("No cloned voices yet");
    });
    MockXMLHttpRequest.onSend = null;
  });

  test("shows a preview heartbeat during generation and hides it after completion", async () => {
    jest.useFakeTimers();
    const deferredPreview = createDeferredResponse();

    fetchMock.mockImplementation((url) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
        return Promise.resolve(
          createJsonResponse(
            createVoiceListPayload([
              { display_name: "Ethan", is_cloned: false, name: "Ethan" },
            ]),
          ),
        );
      }

      if (url === "/api/voice-lab/cloned-voices") {
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
      }

      if (url === "/api/voice-lab/test") {
        return deferredPreview.promise;
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).toContain("1 available voice");

    await act(async () => {
      getButtonByText(container, "Generate Preview").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(container.textContent).toContain("Synthesizing audio...");
    expect(container.textContent).toContain("Elapsed:");

    await act(async () => {
      jest.advanceTimersByTime(1500);
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Processing...");

    await act(async () => {
      deferredPreview.resolve(
        createJsonResponse({
          audio_url: "/audio/voices/heartbeat.wav",
          duration_seconds: 2.8,
        }),
      );
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Generated Preview");
    expect(container.textContent).not.toContain("Synthesizing audio...");

    jest.useRealTimers();
  });

  test("retries voice loading when the engine reports loading state", async () => {
    jest.useFakeTimers();
    let voiceRequests = 0;

    fetchMock.mockImplementation((url) => {
      if (url === "/api/voice-lab/engines") {
        return Promise.resolve(createJsonResponse(createEngineListPayload()));
      }

      if (url.startsWith("/api/voice-lab/voices")) {
        voiceRequests += 1;
        if (voiceRequests === 1) {
          return Promise.resolve(
            createJsonResponse({
              engine: "qwen3_tts",
              loading: true,
              message:
                "TTS engine is loading. Voices will be available shortly.",
              voices: [],
            }),
          );
        }

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
        return Promise.resolve(
          createJsonResponse(createClonedVoicesPayload([])),
        );
      }

      if (url === "/api/voice-lab/voice-design/status") {
        return Promise.resolve(
          createJsonResponse(createVoiceDesignStatusPayload()),
        );
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderVoiceLab();
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).toContain("retrying in 3s (attempt 1/20)");

    await act(async () => {
      jest.advanceTimersByTime(3000);
      await Promise.resolve();
    });

    expect(container.textContent).toContain("2 available voices");

    expect(voiceRequests).toBe(2);
    jest.useRealTimers();
  });
});
