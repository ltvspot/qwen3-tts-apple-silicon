import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";

export default function AudioPlayerQuick({ audioUrl, chapterName }) {
  const audioRef = useRef(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
      }
    };
  }, []);

  if (!audioUrl) {
    return (
      <span className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">
        No Audio
      </span>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <button
        className="inline-flex items-center justify-center rounded-full border border-sky-200 bg-sky-50 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-sky-700 transition hover:bg-sky-100"
        onClick={() => setOpen((current) => !current)}
        type="button"
      >
        {open ? "Hide Player" : "Listen"}
      </button>

      {open ? (
        <audio
          aria-label={`Audio preview for ${chapterName}`}
          className="w-full"
          controls
          preload="none"
          ref={audioRef}
          src={audioUrl}
        />
      ) : null}
    </div>
  );
}

AudioPlayerQuick.propTypes = {
  audioUrl: PropTypes.string,
  chapterName: PropTypes.string.isRequired,
};
