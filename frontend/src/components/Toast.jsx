import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";

const TOAST_STYLES = {
  error: {
    accent: "text-rose-200",
    border: "border-rose-300/30",
    button: "text-rose-100/80 hover:text-rose-50",
    panel: "bg-slate-950/95 text-rose-50 shadow-rose-950/40",
    symbol: "!",
  },
  info: {
    accent: "text-sky-200",
    border: "border-sky-300/30",
    button: "text-sky-100/80 hover:text-sky-50",
    panel: "bg-slate-950/95 text-sky-50 shadow-slate-950/30",
    symbol: "i",
  },
  success: {
    accent: "text-emerald-200",
    border: "border-emerald-300/30",
    button: "text-emerald-100/80 hover:text-emerald-50",
    panel: "bg-slate-950/95 text-emerald-50 shadow-slate-950/30",
    symbol: "✓",
  },
};

export default function Toast({
  message,
  onClose,
  type = "info",
  visible,
}) {
  const [isVisible, setIsVisible] = useState(false);
  const toastStyle = TOAST_STYLES[type] ?? TOAST_STYLES.info;

  useEffect(() => {
    if (!visible) {
      setIsVisible(false);
      return undefined;
    }

    const frameId = window.requestAnimationFrame(() => {
      setIsVisible(true);
    });
    const timeoutId = window.setTimeout(() => {
      onClose();
    }, 4000);

    return () => {
      window.cancelAnimationFrame(frameId);
      window.clearTimeout(timeoutId);
    };
  }, [onClose, visible]);

  if (!visible) {
    return null;
  }

  return createPortal(
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-6 right-6 z-50"
    >
      <div
        className={`pointer-events-auto flex max-w-sm items-start gap-4 rounded-2xl border px-6 py-4 shadow-xl transition duration-200 ease-out ${
          toastStyle.border
        } ${toastStyle.panel} ${
          isVisible ? "translate-x-0 opacity-100" : "translate-x-8 opacity-0"
        }`}
        role="status"
      >
        <div
          aria-hidden="true"
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-current/20 text-sm font-semibold ${toastStyle.accent}`}
        >
          {toastStyle.symbol}
        </div>
        <div className="min-w-0 flex-1 text-sm leading-6">{message}</div>
        <button
          aria-label="Close notification"
          className={`shrink-0 text-sm transition ${toastStyle.button}`}
          onClick={onClose}
          type="button"
        >
          ×
        </button>
      </div>
    </div>,
    document.body,
  );
}

Toast.propTypes = {
  message: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
  type: PropTypes.oneOf(["success", "error", "info"]),
  visible: PropTypes.bool.isRequired,
};
