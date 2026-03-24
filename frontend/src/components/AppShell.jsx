import React from "react";
import { Link } from "react-router-dom";

const navigationItems = [
  { label: "Library", to: "/" },
  { label: "Voice Lab", to: "/voice-lab" },
  { label: "Queue", to: "/queue" },
  { label: "QA", to: "/qa" },
  { label: "Settings", to: "/settings" },
];

export default function AppShell({ title, description, children }) {
  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-6 py-6 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-500">
              Alexandria Audiobook Narrator
            </p>
            <h1 className="mt-2 text-3xl font-semibold text-slate-950">{title}</h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-600">{description}</p>
          </div>
          <nav className="flex flex-wrap gap-3">
            {navigationItems.map((item) => (
              <Link
                key={item.to}
                className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-900 hover:text-slate-900"
                to={item.to}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-10">{children}</main>
    </div>
  );
}
