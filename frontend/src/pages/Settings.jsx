import React from "react";
import AppShell from "../components/AppShell";
import SettingsForm from "../components/SettingsForm";

export default function Settings() {
  return (
    <AppShell
      title="Settings"
      description="Manage the global narrator, manuscript path, default voice, engine visibility, and export preferences that drive the production pipeline."
    >
      <SettingsForm />
    </AppShell>
  );
}
