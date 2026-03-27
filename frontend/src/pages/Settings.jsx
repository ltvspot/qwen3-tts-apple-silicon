import React, { useState } from "react";
import AppShell from "../components/AppShell";
import PronunciationSettings from "../components/PronunciationSettings";
import SettingsForm from "../components/SettingsForm";

export default function Settings() {
  const [activeTab, setActiveTab] = useState("defaults");

  const tabs = [
    {
      id: "defaults",
      label: "Production Defaults",
      description: "Narrator identity, voice behavior, and export defaults.",
    },
    {
      id: "pronunciation",
      label: "Pronunciation",
      description: "Global and book-specific phonetic overrides plus QA-based suggestions.",
    },
  ];

  return (
    <AppShell
      title="Settings"
      description="Manage the global narrator, manuscript path, default voice, engine visibility, and export preferences that drive the production pipeline."
    >
      <div className="space-y-6">
        <section className="rounded-[2rem] border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-col gap-3 lg:flex-row">
            {tabs.map((tab) => {
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  className={[
                    "flex-1 rounded-[1.5rem] border px-5 py-4 text-left transition",
                    isActive
                      ? "border-slate-950 bg-slate-950 text-white"
                      : "border-slate-200 bg-slate-50 text-slate-700 hover:border-slate-400 hover:bg-white",
                  ].join(" ")}
                  onClick={() => {
                    setActiveTab(tab.id);
                  }}
                  type="button"
                >
                  <div className="text-sm font-semibold">{tab.label}</div>
                  <div className={`mt-1 text-sm ${isActive ? "text-slate-200" : "text-slate-500"}`}>
                    {tab.description}
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        {activeTab === "defaults" ? <SettingsForm /> : <PronunciationSettings />}
      </div>
    </AppShell>
  );
}
