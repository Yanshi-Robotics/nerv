"use client";

import { useEffect, useState } from "react";

import { useI18n } from "@/lib/i18n";

type Theme = "dark" | "light";

const STORAGE_KEY = "nerv-theme";

/** 左下角的深/浅主题切换：当前深色显月亮、浅色显太阳，点一下在两者间切并记住偏好。
 *  主题挂在 <html data-theme> 上（首屏由 layout 的内联脚本设好），这里只负责切换 + 持久化。 */
export default function ThemeToggle() {
  const { t } = useI18n();
  const [theme, setTheme] = useState<Theme>("dark");

  // 挂载后从真实 DOM 读主题（内联脚本已按 localStorage 设好），避免和 SSR 默认值不一致
  useEffect(() => {
    const t = document.documentElement.dataset.theme;
    setTheme(t === "light" ? "light" : "dark");
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* 隐私模式等存不了就算了，本次仍然切换 */
    }
    setTheme(next);
  }

  const isDark = theme === "dark";
  return (
    <button
      onClick={toggle}
      title={isDark ? t("Switch to light theme") : t("Switch to dark theme")}
      aria-label={t("Toggle dark / light theme")}
      className="flex h-9 w-9 items-center justify-center rounded-lg border border-neutral-700 text-neutral-400 transition-colors hover:bg-neutral-800 hover:text-neutral-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
    >
      <span className="transition-transform duration-200">
        {isDark ? <MoonIcon /> : <SunIcon />}
      </span>
    </button>
  );
}

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}
