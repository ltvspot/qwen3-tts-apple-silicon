import React, { useEffect, useMemo, useState } from "react";
import useAsyncData from "../hooks/useAsyncData";

function cloneSettings(payload) {
  return JSON.parse(JSON.stringify(payload));
}

function deepEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function deepSet(target, path, value) {
  const keys = path.split(".");
  const next = cloneSettings(target);
  let current = next;

  for (let index = 0; index < keys.length - 1; index += 1) {
    const key = keys[index];
    current[key] = current[key] ?? {};
    current = current[key];
  }

  current[keys[keys.length - 1]] = value;
  return next;
}

function resolveSchemaNode(schema, reference) {
  if (!reference?.startsWith("#/$defs/")) {
    return {};
  }

  const definitionName = reference.replace("#/$defs/", "");
  return schema?.$defs?.[definitionName] ?? {};
}

function getSchemaProperty(schema, reference, property) {
  const node = resolveSchemaNode(schema, reference);
  return node?.properties?.[property] ?? {};
}

function schemaDefaultsFromNode(schema, node) {
  if (!node) {
    return undefined;
  }

  if (node.$ref) {
    return schemaDefaultsFromNode(schema, resolveSchemaNode(schema, node.$ref));
  }

  if (Object.prototype.hasOwnProperty.call(node, "default")) {
    return node.default;
  }

  if (node.type === "object" || node.properties) {
    return Object.fromEntries(
      Object.entries(node.properties ?? {}).map(([key, value]) => [key, schemaDefaultsFromNode(schema, value)]),
    );
  }

  return undefined;
}

function buildDefaultSettings(schema) {
  return schemaDefaultsFromNode(schema, schema);
}

function fieldLabel(text, helpText = null, htmlFor = null) {
  return (
    <div>
      <label className="text-sm font-semibold text-slate-950" htmlFor={htmlFor}>
        {text}
      </label>
      {helpText ? (
        <p className="mt-1 text-sm leading-6 text-slate-500">{helpText}</p>
      ) : null}
    </div>
  );
}

function readValidationMessage(schema, path, value) {
  const [section, key] = path.split(".");
  let node;

  if (key) {
    const sectionProperty = schema?.properties?.[section] ?? {};
    node = getSchemaProperty(schema, sectionProperty.$ref, key);
  } else {
    node = schema?.properties?.[section] ?? {};
  }

  if (!node || value === "" || value === null || value === undefined) {
    return "";
  }

  if (node.enum && !node.enum.includes(value)) {
    return `Value must be one of: ${node.enum.join(", ")}.`;
  }

  if (typeof value === "number") {
    if (typeof node.minimum === "number" && value < node.minimum) {
      return `Value must be at least ${node.minimum}.`;
    }
    if (typeof node.maximum === "number" && value > node.maximum) {
      return `Value must be at most ${node.maximum}.`;
    }
  }

  return "";
}

export default function SettingsForm() {
  const [errorMessage, setErrorMessage] = useState("");
  const [formSettings, setFormSettings] = useState(null);
  const [initialSettings, setInitialSettings] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const [schema, setSchema] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [successMessage, setSuccessMessage] = useState("");
  const [validationMessage, setValidationMessage] = useState("");

  const {
    data: loadedSettings,
    error: loadError,
    loading: isLoading,
    retry: retryLoad,
  } = useAsyncData(async () => {
    const [settingsResponse, schemaResponse] = await Promise.all([
      fetch("/api/settings"),
      fetch("/api/settings/schema"),
    ]);

    if (!settingsResponse.ok || !schemaResponse.ok) {
      throw new Error("Failed to load settings.");
    }

    const [settingsPayload, schemaPayload] = await Promise.all([
      settingsResponse.json(),
      schemaResponse.json(),
    ]);

    return {
      schemaPayload,
      settingsPayload,
    };
  });

  useEffect(() => {
    if (!loadedSettings) {
      return;
    }

    setSchema(loadedSettings.schemaPayload);
    setFormSettings(cloneSettings(loadedSettings.settingsPayload));
    setInitialSettings(cloneSettings(loadedSettings.settingsPayload));
    setErrorMessage("");
  }, [loadedSettings]);

  const hasChanges = useMemo(() => {
    if (!formSettings || !initialSettings) {
      return false;
    }

    return !deepEqual(formSettings, initialSettings);
  }, [formSettings, initialSettings]);

  useEffect(() => {
    const baseTitle = "Settings | Alexandria Audiobook Narrator";
    document.title = hasChanges ? `* ${baseTitle}` : baseTitle;

    return () => {
      document.title = baseTitle;
    };
  }, [hasChanges]);

  if (isLoading) {
    return (
      <div className="rounded-[2rem] border border-slate-200 bg-white p-8 text-sm text-slate-600 shadow-sm">
        Loading settings...
      </div>
    );
  }

  if (!formSettings || !schema) {
    return (
      <div className="rounded-[2rem] border border-rose-200 bg-rose-50 p-8 text-sm text-rose-700">
        <p>{loadError || errorMessage || "Settings are unavailable right now."}</p>
        <button
          className="mt-4 rounded-full bg-rose-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-rose-500"
          onClick={retryLoad}
          type="button"
        >
          Retry Load
        </button>
      </div>
    );
  }

  const voiceDefinition = resolveSchemaNode(schema, schema?.properties?.default_voice?.$ref);
  const outputDefinition = resolveSchemaNode(schema, schema?.properties?.output_preferences?.$ref);
  const engineDefinition = resolveSchemaNode(schema, schema?.properties?.engine_config?.$ref);

  function handleChange(path, value) {
    setSuccessMessage("");
    setValidationMessage(readValidationMessage(schema, path, value));
    setFormSettings((current) => deepSet(current, path, value));
  }

  function discardChanges() {
    setFormSettings(cloneSettings(initialSettings));
    setErrorMessage("");
    setSuccessMessage("");
    setValidationMessage("");
  }

  function resetToDefaults() {
    const shouldReset = window.confirm("Reset all settings to defaults?");
    if (!shouldReset) {
      return;
    }

    const defaults = buildDefaultSettings(schema);
    setFormSettings(defaults);
    setSuccessMessage("");
    setErrorMessage("");
    setValidationMessage("");
  }

  async function handleSave() {
    setIsSaving(true);
    setErrorMessage("");
    setSuccessMessage("");

    try {
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(formSettings),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const detail = Array.isArray(payload?.detail)
          ? payload.detail.map((item) => item.msg ?? "Invalid setting").join(" ")
          : payload?.detail ?? "Failed to save settings.";
        throw new Error(detail);
      }

      const payload = await response.json();
      setFormSettings(cloneSettings(payload.settings));
      setInitialSettings(cloneSettings(payload.settings));
      setSuccessMessage("Settings saved successfully.");
      setValidationMessage("");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to save settings.");
    } finally {
      setIsSaving(false);
    }
  }

  const outputPrefs = formSettings.output_preferences;
  const defaultVoice = formSettings.default_voice;
  const engineConfig = formSettings.engine_config;

  return (
    <div className="space-y-6">
      <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.28em] text-amber-600">
              Application Settings
            </div>
            <h2 className="mt-3 text-2xl font-semibold text-slate-950">Production defaults</h2>
            <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-600">
              Configure narrator credits, default voice behavior, export packaging, and manuscript discovery without editing code.
            </p>
          </div>
          <div className={`inline-flex items-center rounded-full px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] ${
            hasChanges
              ? "border border-amber-200 bg-amber-50 text-amber-700"
              : "border border-emerald-200 bg-emerald-50 text-emerald-700"
          }`}
          >
            {hasChanges ? "Unsaved Changes" : "All Changes Saved"}
          </div>
        </div>
      </section>

      {successMessage ? (
        <div className="rounded-[1.75rem] border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-800">
          {successMessage}
        </div>
      ) : null}

      {errorMessage ? (
        <div className="rounded-[1.75rem] border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
          Failed to save settings: {errorMessage}
        </div>
      ) : null}

      {validationMessage ? (
        <div className="rounded-[1.75rem] border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-800">
          {validationMessage}
        </div>
      ) : null}

      <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="space-y-2">
            {fieldLabel("Narrator Name", "Name used in opening and closing audiobook credits.", "narrator-name")}
            <input
              aria-label="Narrator Name"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-amber-300 focus:bg-white focus:ring-2 focus:ring-amber-300/25"
              id="narrator-name"
              onChange={(event) => handleChange("narrator_name", event.target.value)}
              type="text"
              value={formSettings.narrator_name}
            />
          </div>

          <div className="space-y-2">
            {fieldLabel(
              "Formatted Manuscripts Folder",
              "Path to the folder containing manuscript subfolders.",
              "manuscript-folder",
            )}
            <div className="flex gap-3">
              <input
                aria-label="Formatted Manuscripts Folder"
                className="min-w-0 flex-1 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-amber-300 focus:bg-white focus:ring-2 focus:ring-amber-300/25"
                id="manuscript-folder"
                onChange={(event) => handleChange("manuscript_source_folder", event.target.value)}
                type="text"
                value={formSettings.manuscript_source_folder}
              />
              <button
                className="rounded-full border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-950 hover:text-slate-950"
                onClick={() => {
                  const nextPath = window.prompt(
                    "Enter the formatted manuscripts folder path",
                    formSettings.manuscript_source_folder,
                  );
                  if (nextPath !== null) {
                    handleChange("manuscript_source_folder", nextPath);
                  }
                }}
                type="button"
              >
                Browse...
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-2">
            {fieldLabel("Default Voice", null, "default-voice")}
            <select
              aria-label="Default Voice"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-amber-300 focus:bg-white focus:ring-2 focus:ring-amber-300/25"
              id="default-voice"
              onChange={(event) => handleChange("default_voice.name", event.target.value)}
              value={defaultVoice.name}
            >
              {(voiceDefinition?.properties?.name?.enum ?? ["Ethan"]).map((voice) => (
                <option key={voice} value={voice}>
                  {voice}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            {fieldLabel("Voice Emotion", null, "voice-emotion")}
            <select
              aria-label="Voice Emotion"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-amber-300 focus:bg-white focus:ring-2 focus:ring-amber-300/25"
              id="voice-emotion"
              onChange={(event) => handleChange("default_voice.emotion", event.target.value)}
              value={defaultVoice.emotion}
            >
              {(voiceDefinition?.properties?.emotion?.enum ?? []).map((emotion) => (
                <option key={emotion} value={emotion}>
                  {emotion}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            {fieldLabel("Speech Speed", `${defaultVoice.speed.toFixed(1)}x default delivery`, "voice-speed")}
            <div className="space-y-3 rounded-[1.5rem] border border-slate-200 bg-slate-50 p-4">
              <input
                aria-label="Speech Speed"
                className="w-full accent-amber-500"
                id="voice-speed"
                max={voiceDefinition?.properties?.speed?.maximum ?? 2}
                min={voiceDefinition?.properties?.speed?.minimum ?? 0.5}
                onChange={(event) => handleChange("default_voice.speed", Number.parseFloat(event.target.value))}
                step="0.1"
                type="range"
                value={defaultVoice.speed}
              />
              <input
                aria-label="Speech Speed Value"
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-amber-300 focus:ring-2 focus:ring-amber-300/25"
                onChange={(event) => handleChange("default_voice.speed", Number.parseFloat(event.target.value))}
                step="0.1"
                type="number"
                value={defaultVoice.speed}
              />
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="space-y-2">
            {fieldLabel(
              "MP3 Bitrate (kbps)",
              "Higher bitrate improves quality at the cost of larger export files.",
              "mp3-bitrate",
            )}
            <select
              aria-label="MP3 Bitrate"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-amber-300 focus:bg-white focus:ring-2 focus:ring-amber-300/25"
              id="mp3-bitrate"
              onChange={(event) => handleChange("output_preferences.mp3_bitrate", Number.parseInt(event.target.value, 10))}
              value={outputPrefs.mp3_bitrate}
            >
              {(outputDefinition?.properties?.mp3_bitrate?.enum ?? [128, 192, 256, 320]).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            {fieldLabel("Sample Rate (Hz)", null, "sample-rate")}
            <select
              aria-label="Sample Rate"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-amber-300 focus:bg-white focus:ring-2 focus:ring-amber-300/25"
              id="sample-rate"
              onChange={(event) => handleChange("output_preferences.sample_rate", Number.parseInt(event.target.value, 10))}
              value={outputPrefs.sample_rate}
            >
              {(outputDefinition?.properties?.sample_rate?.enum ?? [44100, 48000]).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>

          {[
            ["silence_duration_chapters", "Silence Between Chapters (seconds)"],
            ["silence_duration_opening", "Silence After Opening Credits (seconds)"],
            ["silence_duration_closing", "Silence Before Closing Credits (seconds)"],
          ].map(([key, label]) => (
            <div className="space-y-2" key={key}>
              {fieldLabel(label, null, key)}
              <div className="space-y-3 rounded-[1.5rem] border border-slate-200 bg-slate-50 p-4">
                <input
                  aria-label={label}
                  className="w-full accent-amber-500"
                  id={key}
                  max={outputDefinition?.properties?.[key]?.maximum ?? 10}
                  min={outputDefinition?.properties?.[key]?.minimum ?? 0.5}
                  onChange={(event) => handleChange(`output_preferences.${key}`, Number.parseFloat(event.target.value))}
                  step="0.1"
                  type="range"
                  value={outputPrefs[key]}
                />
                <input
                  aria-label={`${label} Value`}
                  className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-amber-300 focus:ring-2 focus:ring-amber-300/25"
                  onChange={(event) => handleChange(`output_preferences.${key}`, Number.parseFloat(event.target.value))}
                  step="0.1"
                  type="number"
                  value={outputPrefs[key]}
                />
              </div>
            </div>
          ))}

          <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 p-4">
            <label className="flex items-start gap-3">
              <input
                checked={outputPrefs.include_album_art}
                className="mt-1 h-4 w-4 rounded border-slate-300 text-amber-500 focus:ring-amber-400"
                onChange={(event) => handleChange("output_preferences.include_album_art", event.target.checked)}
                type="checkbox"
              />
              <span>
                <span className="block text-sm font-semibold text-slate-950">
                  Include album art in exported MP3
                </span>
                <span className="mt-1 block text-sm text-slate-500">
                  Embed the placeholder cover image in MP3 exports.
                </span>
              </span>
            </label>
          </div>
        </div>
      </section>

      <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
        <button
          className="flex w-full items-center justify-between text-left"
          onClick={() => setShowAdvanced((current) => !current)}
          type="button"
        >
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
              Advanced
            </div>
            <h3 className="mt-2 text-xl font-semibold text-slate-950">Engine Configuration</h3>
          </div>
          <span className="text-sm font-semibold text-slate-500">
            {showAdvanced ? "Hide" : "Show"}
          </span>
        </button>

        {showAdvanced ? (
          <div className="mt-5 space-y-2">
            {fieldLabel(
              "Model Path",
              "Displayed for reference. Model file management still happens on disk.",
              "model-path",
            )}
            <input
              aria-label="Model Path"
              className="w-full rounded-2xl border border-slate-200 bg-slate-100 px-4 py-3 text-sm text-slate-600"
              id="model-path"
              readOnly
              type="text"
              value={engineConfig.model_path}
            />
            {engineDefinition?.properties?.model_path?.readOnly ? (
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
                Read-only field
              </p>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-3 sm:flex-row sm:justify-end">
          <button
            className="rounded-full border border-slate-200 px-5 py-3 text-sm font-semibold text-slate-700 transition hover:border-slate-950 hover:text-slate-950"
            disabled={isSaving}
            onClick={resetToDefaults}
            type="button"
          >
            Reset to Defaults
          </button>
          <button
            className="rounded-full border border-slate-200 px-5 py-3 text-sm font-semibold text-slate-700 transition hover:border-slate-950 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!hasChanges || isSaving}
            onClick={discardChanges}
            type="button"
          >
            Discard Changes
          </button>
          <button
            className="rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            disabled={!hasChanges || isSaving}
            onClick={() => {
              void handleSave();
            }}
            type="button"
          >
            {isSaving ? "Saving..." : "Save Settings"}
          </button>
        </div>
      </section>
    </div>
  );
}
