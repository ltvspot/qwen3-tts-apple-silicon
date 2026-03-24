import React from "react";
import AppShell from "../components/AppShell";

export default function Queue() {
  return (
    <AppShell
      title="Production Queue"
      description="Generation jobs, progress, and throughput estimates will be managed here."
    >
      <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-sm text-slate-600">
        Page stub
      </div>
    </AppShell>
  );
}
