"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";

type Theme = "dark" | "light" | "system";

export default function ThemeToggle() {
  const { t } = useI18n();
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    const read = () => {
      const value = document.documentElement.dataset.themePreference;
      setTheme(value === "dark" || value === "system" ? value : "light");
    };
    read();
    window.addEventListener("storage", read);
    return () => window.removeEventListener("storage", read);
  }, []);

  function select(next: Theme) {
    document.documentElement.dataset.themePreference = next;
    document.documentElement.dataset.theme = next === "system"
      ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : next;
    try { localStorage.setItem("nerv-theme", next); } catch { /* Apply for this visit. */ }
    setTheme(next);
  }

  return (
    <select value={theme} onChange={e => select(e.target.value as Theme)}
      aria-label={t("Appearance")} className="nerv-control h-11 w-44 max-w-full rounded-lg border border-neutral-700 bg-neutral-900 px-3 text-sm">
      <option value="light">{t("Light")}</option>
      <option value="dark">{t("Dark")}</option>
      <option value="system">{t("Follow system")}</option>
    </select>
  );
}
