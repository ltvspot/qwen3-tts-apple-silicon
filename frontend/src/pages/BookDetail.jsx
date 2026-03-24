import React from "react";
import { useParams } from "react-router-dom";
import AppShell from "../components/AppShell";

export default function BookDetail() {
  const { id } = useParams();

  return (
    <AppShell
      title="Book Detail"
      description="Detailed manuscript, chapter, and generation controls will land in Prompt 05."
    >
      <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-sm text-slate-600">
        Page stub for book <span className="font-semibold text-slate-900">{id}</span>.
      </div>
    </AppShell>
  );
}
