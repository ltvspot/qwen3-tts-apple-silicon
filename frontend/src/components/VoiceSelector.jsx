import React from "react";

function optionLabel(voice) {
  return voice.display_name || voice.name;
}

export default function VoiceSelector({
  ariaLabel,
  className,
  disabled = false,
  emptyLabel = "No voices available",
  id,
  onChange,
  value,
  voices,
}) {
  const builtInVoices = voices.filter((voice) => !voice.is_cloned);
  const clonedVoices = voices.filter((voice) => voice.is_cloned);

  return (
    <select
      aria-label={ariaLabel}
      className={className}
      disabled={disabled || voices.length === 0}
      id={id}
      onChange={(event) => onChange(event.target.value)}
      value={voices.length === 0 ? "" : value}
    >
      {voices.length === 0 ? <option value="">{emptyLabel}</option> : null}

      {builtInVoices.length > 0 ? (
        <optgroup label="Built-in Voices">
          {builtInVoices.map((voice) => (
            <option key={voice.name} value={voice.name}>
              {optionLabel(voice)}
            </option>
          ))}
        </optgroup>
      ) : null}

      {clonedVoices.length > 0 ? (
        <optgroup label="Cloned Voices">
          {clonedVoices.map((voice) => (
            <option key={voice.name} value={voice.name}>
              {optionLabel(voice)}
            </option>
          ))}
        </optgroup>
      ) : null}
    </select>
  );
}
