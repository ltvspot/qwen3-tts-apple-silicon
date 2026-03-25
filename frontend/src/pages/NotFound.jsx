import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import AppShell from "../components/AppShell";

export default function NotFound() {
  useEffect(() => {
    document.title = "Page Not Found | Alexandria Audiobook Narrator";
  }, []);

  return (
    <AppShell
      title="Page Not Found"
      description="The route you requested does not exist in the Alexandria Audiobook Narrator."
    >
      <div className="flex min-h-[50vh] flex-col items-center justify-center rounded-[2rem] border border-slate-200 bg-white px-6 py-12 text-center shadow-sm">
        <p className="text-sm font-semibold uppercase tracking-[0.3em] text-slate-500">404 Error</p>
        <h2 className="mt-4 text-4xl font-semibold text-slate-950">Page not found</h2>
        <p className="mt-3 max-w-xl text-sm leading-6 text-slate-600">
          Check the URL or return to the library to continue managing narration jobs.
        </p>
        <Link
          className="mt-8 rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800"
          to="/"
        >
          Back to Library
        </Link>
      </div>
    </AppShell>
  );
}
