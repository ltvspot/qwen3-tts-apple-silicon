import PropTypes from "prop-types";
import React, {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

const CONFIRM_BUTTON_TONES = {
  amber: {
    dark:
      "border-amber-300/25 bg-amber-400/10 text-amber-100 hover:bg-amber-400/20",
    light:
      "border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100",
  },
  emerald: {
    dark:
      "border-emerald-300/25 bg-emerald-400/10 text-emerald-100 hover:bg-emerald-400/20",
    light:
      "border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100",
  },
  rose: {
    dark:
      "border-rose-300/25 bg-rose-400/10 text-rose-100 hover:bg-rose-400/20",
    light: "border-rose-300 bg-rose-50 text-rose-800 hover:bg-rose-100",
  },
  sky: {
    dark:
      "border-sky-300/25 bg-sky-400/10 text-sky-100 hover:bg-sky-400/20",
    light: "border-sky-300 bg-sky-50 text-sky-800 hover:bg-sky-100",
  },
};

const THEME_STYLES = {
  dark: {
    cancelButton:
      "border-white/10 text-slate-300 hover:border-white/20 hover:text-white",
    input:
      "border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus:border-amber-300/40",
    label: "text-slate-200",
    message: "text-slate-400",
    panel: "border-white/10 bg-slate-900 text-white shadow-2xl shadow-slate-950/50",
  },
  light: {
    cancelButton:
      "border-slate-200 text-slate-700 hover:border-slate-400 hover:text-slate-950",
    input:
      "border-slate-200 bg-slate-50 text-slate-950 placeholder:text-slate-400 focus:border-amber-300 focus:bg-white focus:ring-2 focus:ring-amber-300/25",
    label: "text-slate-700",
    message: "text-slate-600",
    panel: "border-slate-200 bg-white text-slate-950 shadow-2xl shadow-slate-900/10",
  },
};

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export default function ConfirmDialog({
  alertMode = false,
  cancelLabel = "Cancel",
  confirmColor = "amber",
  confirmLabel = "Confirm",
  message,
  onCancel,
  onConfirm,
  open,
  promptDefault = "",
  promptLabel = "Value",
  promptMode = false,
  theme = "dark",
  title,
}) {
  const [isVisible, setIsVisible] = useState(false);
  const [promptValue, setPromptValue] = useState(promptDefault ?? "");
  const panelRef = useRef(null);
  const confirmButtonRef = useRef(null);
  const inputRef = useRef(null);
  const titleId = useId();
  const descriptionId = useId();
  const promptId = useId();

  const themeStyles = THEME_STYLES[theme] ?? THEME_STYLES.dark;
  const confirmTone =
    CONFIRM_BUTTON_TONES[confirmColor]?.[theme] ??
    CONFIRM_BUTTON_TONES.amber[theme];

  const focusableElements = useMemo(
    () =>
      open && panelRef.current
        ? Array.from(panelRef.current.querySelectorAll(FOCUSABLE_SELECTOR))
        : [],
    [open, isVisible],
  );

  useEffect(() => {
    if (!open) {
      setIsVisible(false);
      return undefined;
    }

    setPromptValue(promptDefault ?? "");
    const frameId = window.requestAnimationFrame(() => {
      setIsVisible(true);
    });
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      window.cancelAnimationFrame(frameId);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, promptDefault]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const focusTarget =
      (promptMode ? inputRef.current : null) ?? confirmButtonRef.current;
    const focusTimeoutId = window.setTimeout(() => {
      focusTarget?.focus();
    }, 0);

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }

      if (event.key !== "Tab" || focusableElements.length === 0) {
        return;
      }

      const firstElement = focusableElements[0];
      const lastElement = focusableElements[focusableElements.length - 1];
      const activeElement = document.activeElement;

      if (event.shiftKey) {
        if (activeElement === firstElement || !panelRef.current?.contains(activeElement)) {
          event.preventDefault();
          lastElement.focus();
        }
        return;
      }

      if (activeElement === lastElement) {
        event.preventDefault();
        firstElement.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      window.clearTimeout(focusTimeoutId);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [focusableElements, onCancel, open, promptMode]);

  if (!open) {
    return null;
  }

  function handleSubmit(event) {
    event.preventDefault();
    if (promptMode) {
      onConfirm(promptValue);
      return;
    }

    onConfirm();
  }

  return createPortal(
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 px-4 backdrop-blur-sm transition duration-150 ${
        isVisible ? "opacity-100" : "opacity-0"
      }`}
    >
      <form
        aria-describedby={descriptionId}
        aria-labelledby={titleId}
        aria-modal="true"
        className={`w-full max-w-md rounded-[2rem] border p-8 transition duration-150 ${
          themeStyles.panel
        } ${isVisible ? "scale-100 opacity-100" : "scale-95 opacity-0"}`}
        onSubmit={handleSubmit}
        ref={panelRef}
        role={alertMode ? "alertdialog" : "dialog"}
      >
        <h2 className="mb-3 text-xl font-semibold" id={titleId}>
          {title}
        </h2>
        <p className={`mb-6 text-sm leading-7 ${themeStyles.message}`} id={descriptionId}>
          {message}
        </p>

        {promptMode ? (
          <div className="mb-6 space-y-2">
            <label
              className={`block text-sm font-semibold ${themeStyles.label}`}
              htmlFor={promptId}
            >
              {promptLabel}
            </label>
            <input
              aria-label={promptLabel}
              className={`w-full rounded-2xl border px-4 py-3 outline-none transition ${themeStyles.input}`}
              id={promptId}
              onChange={(event) => setPromptValue(event.target.value)}
              ref={inputRef}
              type="text"
              value={promptValue}
            />
          </div>
        ) : null}

        <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          {alertMode ? null : (
            <button
              className={`inline-flex items-center justify-center rounded-full border px-5 py-3 text-sm font-semibold transition ${themeStyles.cancelButton}`}
              onClick={onCancel}
              type="button"
            >
              {cancelLabel}
            </button>
          )}
          <button
            className={`inline-flex items-center justify-center rounded-full border px-5 py-3 text-sm font-semibold transition ${confirmTone}`}
            ref={confirmButtonRef}
            type="submit"
          >
            {confirmLabel}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

ConfirmDialog.propTypes = {
  alertMode: PropTypes.bool,
  cancelLabel: PropTypes.string,
  confirmColor: PropTypes.string,
  confirmLabel: PropTypes.string,
  message: PropTypes.string.isRequired,
  onCancel: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
  open: PropTypes.bool.isRequired,
  promptDefault: PropTypes.string,
  promptLabel: PropTypes.string,
  promptMode: PropTypes.bool,
  theme: PropTypes.oneOf(["dark", "light"]),
  title: PropTypes.string.isRequired,
};
