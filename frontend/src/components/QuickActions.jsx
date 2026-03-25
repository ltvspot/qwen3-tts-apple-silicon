import React from "react";
import { Link } from "react-router-dom";

function ActionButton({ disabled, label, onClick }) {
  return (
    <button
      className="rounded-2xl bg-slate-950 px-4 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  );
}

function ActionLink({ label, to }) {
  return (
    <Link
      className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-900 transition hover:bg-slate-50"
      to={to}
    >
      {label}
    </Link>
  );
}

export default function QuickActions({ actionLoading, onAction }) {
  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4">
        <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
          Quick Actions
        </p>
        <h2 className="mt-2 text-2xl font-semibold text-slate-950">Bulk QA and export shortcuts</h2>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <ActionButton
          disabled={actionLoading}
          label="Batch Approve All Passing"
          onClick={() => onAction("approve")}
        />
        <ActionButton
          disabled={actionLoading}
          label="Batch Export All Ready"
          onClick={() => onAction("export")}
        />
        <ActionLink label="Retry All Failed in Queue" to="/queue" />
        <ActionLink label="View Flagged Books" to="/qa" />
      </div>
    </section>
  );
}
