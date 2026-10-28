"use client";
import { useI18n } from "@/lib/i18n";

export default function EnvironmentDisclosure() {
  const { t } = useI18n();
  return <div className="space-y-2 text-sm muted">
    <p>{t("NERV uses independent Python virtual environments (venv), created and maintained with uv. Docker is not required.")}</p>
    <p>{t("The host, SO-101 runtime and simulation have separate dependencies. Installing a component does not install packages into your system Python, Conda or training environments.")}</p>
    <p>{t("Environments, managed Python and download caches live under the current NERV_HOME (default: ~/.nerv). A preview uses a separate home for its settings, keys, sessions and environments.")}</p>
    <p>{t("NERV verifies downloads and checks a new environment before activating it. Updates retain the previous environment for rollback. Do not move or edit environment folders manually.")}</p>
    <p>{t("Models and calibration files stay at your selected locations. Python isolation does not isolate hardware: cameras, serial ports, GPU memory and system drivers are shared with other applications.")}</p>
  </div>;
}
