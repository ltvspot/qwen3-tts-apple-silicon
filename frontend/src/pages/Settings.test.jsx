import React, { act } from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import Settings from "./Settings";

function createJsonResponse(payload, options = {}) {
  return {
    json: async () => payload,
    ok: options.ok ?? true,
    status: options.status ?? 200,
  };
}

function createSettingsPayload(overrides = {}) {
  return {
    default_voice: {
      emotion: "neutral",
      name: "Ethan",
      speed: 1.0,
    },
    engine_config: {
      model_path: "models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
    },
    manuscript_source_folder: "./Formatted Manuscripts/",
    narrator_name: "Kent Zimering",
    output_preferences: {
      include_album_art: true,
      mp3_bitrate: 192,
      sample_rate: 44100,
      silence_duration_chapters: 2.0,
      silence_duration_closing: 3.0,
      silence_duration_opening: 3.0,
    },
    ...overrides,
  };
}

function createSettingsSchema() {
  return {
    $defs: {
      EngineSettings: {
        properties: {
          model_path: {
            default: "models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
            readOnly: true,
            type: "string",
          },
        },
        type: "object",
      },
      OutputSettings: {
        properties: {
          include_album_art: {
            default: true,
            type: "boolean",
          },
          mp3_bitrate: {
            default: 192,
            enum: [128, 192, 256, 320],
            type: "integer",
          },
          sample_rate: {
            default: 44100,
            enum: [44100, 48000],
            type: "integer",
          },
          silence_duration_chapters: {
            default: 2.0,
            maximum: 10,
            minimum: 0.5,
            type: "number",
          },
          silence_duration_closing: {
            default: 3.0,
            maximum: 10,
            minimum: 0.5,
            type: "number",
          },
          silence_duration_opening: {
            default: 3.0,
            maximum: 10,
            minimum: 0.5,
            type: "number",
          },
        },
        type: "object",
      },
      VoiceSettings: {
        properties: {
          emotion: {
            default: "neutral",
            enum: ["neutral", "calm", "happy", "sad", "angry"],
            type: "string",
          },
          name: {
            default: "Ethan",
            enum: ["Ethan", "Nova", "Aria"],
            type: "string",
          },
          speed: {
            default: 1.0,
            maximum: 2.0,
            minimum: 0.5,
            type: "number",
          },
        },
        type: "object",
      },
    },
    properties: {
      default_voice: {
        $ref: "#/$defs/VoiceSettings",
      },
      engine_config: {
        $ref: "#/$defs/EngineSettings",
      },
      manuscript_source_folder: {
        default: "./Formatted Manuscripts/",
        type: "string",
      },
      narrator_name: {
        default: "Kent Zimering",
        type: "string",
      },
      output_preferences: {
        $ref: "#/$defs/OutputSettings",
      },
    },
    type: "object",
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

describe("Settings page", () => {
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

    await act(async () => {
      root.unmount();
    });

    container.remove();
    delete global.fetch;
  });

  async function renderSettingsPage() {
    await act(async () => {
      root.render(
        <MemoryRouter
          initialEntries={["/settings"]}
          future={{
            v7_relativeSplatPath: true,
            v7_startTransition: true,
          }}
        >
          <Settings />
        </MemoryRouter>,
      );
    });
  }

  test("loads settings, tracks unsaved changes, and saves the updated payload", async () => {
    const initialSettings = createSettingsPayload();
    const savedSettings = createSettingsPayload({
      default_voice: {
        emotion: "calm",
        name: "Nova",
        speed: 1.2,
      },
      narrator_name: "Morgan Vale",
    });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(initialSettings))
      .mockResolvedValueOnce(createJsonResponse(createSettingsSchema()))
      .mockResolvedValueOnce(
        createJsonResponse({
          message: "Settings updated successfully",
          settings: savedSettings,
          success: true,
          updated_fields: ["narrator_name", "default_voice.name", "default_voice.emotion", "default_voice.speed"],
        }),
      );

    await renderSettingsPage();

    await waitFor(() => {
      expect(container.textContent).toContain("Manage the global narrator");
      expect(container.textContent).toContain("All Changes Saved");
      expect(container.querySelector("#narrator-name").value).toBe("Kent Zimering");
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/settings");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/settings/schema");
    expect(document.title).toBe("Settings | Alexandria Audiobook Narrator");

    await act(async () => {
      setFormValue(container.querySelector("#narrator-name"), "Morgan Vale", "input");
      setFormValue(container.querySelector("#default-voice"), "Nova", "change");
      setFormValue(container.querySelector("#voice-emotion"), "calm", "change");
      setFormValue(container.querySelector('input[aria-label="Speech Speed Value"]'), "1.2", "input");
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Unsaved Changes");
      expect(document.title).toBe("* Settings | Alexandria Audiobook Narrator");
    });

    await act(async () => {
      getButtonByText(container, "Save Settings").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3);
      expect(container.textContent).toContain("Settings saved successfully.");
      expect(container.textContent).toContain("All Changes Saved");
      expect(document.title).toBe("Settings | Alexandria Audiobook Narrator");
    });

    expect(fetchMock.mock.calls[2][0]).toBe("/api/settings");
    expect(fetchMock.mock.calls[2][1]).toMatchObject({
      headers: {
        "Content-Type": "application/json",
      },
      method: "PUT",
    });
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual(savedSettings);
  });

  test("discards local edits and resets back to schema defaults", async () => {
    const initialSettings = createSettingsPayload({
      default_voice: {
        emotion: "happy",
        name: "Nova",
        speed: 1.4,
      },
      manuscript_source_folder: "/tmp/custom-manuscripts",
      narrator_name: "Avery Stone",
      output_preferences: {
        include_album_art: false,
        mp3_bitrate: 320,
        sample_rate: 48000,
        silence_duration_chapters: 4.0,
        silence_duration_closing: 4.5,
        silence_duration_opening: 4.5,
      },
    });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(initialSettings))
      .mockResolvedValueOnce(createJsonResponse(createSettingsSchema()));

    await renderSettingsPage();

    await waitFor(() => {
      expect(container.querySelector("#narrator-name").value).toBe("Avery Stone");
      expect(container.querySelector("#manuscript-folder").value).toBe("/tmp/custom-manuscripts");
    });

    await act(async () => {
      setFormValue(container.querySelector("#narrator-name"), "Draft Narrator", "input");
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Unsaved Changes");
    });

    await act(async () => {
      getButtonByText(container, "Discard Changes").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.querySelector("#narrator-name").value).toBe("Avery Stone");
      expect(container.textContent).toContain("All Changes Saved");
    });

    await act(async () => {
      getButtonByText(container, "Reset to Defaults").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(document.body.textContent).toContain("Reset to Defaults");
      expect(document.body.textContent).toContain(
        "This will revert all settings to their factory defaults. Any custom configuration will be lost.",
      );
    });

    expect(window.confirm).not.toHaveBeenCalled();

    await act(async () => {
      getButtonByText(document.body, "Reset All").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.querySelector("#narrator-name").value).toBe("Kent Zimering");
      expect(container.querySelector("#manuscript-folder").value).toBe("./Formatted Manuscripts/");
      expect(container.querySelector("#default-voice").value).toBe("Ethan");
      expect(container.textContent).toContain("Unsaved Changes");
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  test("shows a retry action when the initial settings load fails and recovers on retry", async () => {
    fetchMock
      .mockResolvedValueOnce(createJsonResponse({}, { ok: false, status: 500 }))
      .mockResolvedValueOnce(createJsonResponse(createSettingsSchema()))
      .mockResolvedValueOnce(createJsonResponse(createSettingsPayload()))
      .mockResolvedValueOnce(createJsonResponse(createSettingsSchema()));

    await renderSettingsPage();

    await waitFor(() => {
      expect(container.textContent).toContain("Failed to load settings.");
      expect(getButtonByText(container, "Retry Load")).toBeTruthy();
    });

    await act(async () => {
      getButtonByText(container, "Retry Load").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Manage the global narrator");
      expect(container.querySelector("#narrator-name").value).toBe("Kent Zimering");
    });

    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  test("switches to the pronunciation tab and loads pronunciation controls", async () => {
    fetchMock.mockImplementation((url) => {
      if (url === "/api/settings") {
        return Promise.resolve(createJsonResponse(createSettingsPayload()));
      }
      if (url === "/api/settings/schema") {
        return Promise.resolve(createJsonResponse(createSettingsSchema()));
      }
      if (url === "/api/pronunciation") {
        return Promise.resolve(createJsonResponse({ global: { Thoreau: "thuh-ROH" }, per_book: {} }));
      }
      if (url === "/api/pronunciation/suggestions") {
        return Promise.resolve(createJsonResponse([]));
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    await renderSettingsPage();

    await waitFor(() => {
      expect(container.textContent).toContain("Production Defaults");
      expect(container.textContent).toContain("Pronunciation");
    });

    await act(async () => {
      getButtonByText(container, "Pronunciation").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Shared pronunciations");
      expect(container.textContent).toContain("Thoreau");
    });
  });
});
