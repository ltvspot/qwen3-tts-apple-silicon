import React from "react";
import { NavLink } from "react-router-dom";

const navigationItems = [
  { label: "Catalog", to: "/catalog" },
  { label: "Batch Production", to: "/batch-production" },
  { label: "Overseer", to: "/overseer" },
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
              <NavLink
                key={item.to}
                className={({ isActive }) =>
                  [
                    "rounded-full border px-4 py-2 text-sm font-medium transition",
                    isActive
                      ? "border-slate-900 bg-slate-900 text-white"
                      : "border-slate-300 text-slate-700 hover:border-slate-900 hover:text-slate-900",
                  ].join(" ")
                }
                to={item.to}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-10">{children}</main>
    </div>
  );
}
