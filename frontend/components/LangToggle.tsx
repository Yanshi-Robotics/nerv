"use client";

import { useI18n } from "@/lib/i18n";

/** 侧栏底部的语言切换：一个标准方形图标按钮，点一下切到下一门语言。
 *
 *  ⛔ **这里不认识任何具体语言。** 它遍历 `locales/` 里有什么就在什么之间循环；
 *  每个语言的显示名来自它自己文件里的 `meta.label`。只有一门语言时不渲染。
 *  Knows about no specific language: it cycles whatever is in `locales/`.
 */
export default function LangToggle() {
  const { lang, setLang, availableLangs, localeMeta, t } = useI18n();
  const langs = availableLangs();
  if (langs.length < 2) return null;
  const idx = Math.max(0, langs.indexOf(lang));
  const next = langs[(idx + 1) % langs.length];
  const nextLabel = localeMeta(next)?.label ?? next;
  const curLabel = localeMeta(lang)?.label ?? lang;
  return (
    <button
      onClick={() => setLang(next)}
      title={`${t("Interface language")}: ${curLabel} → ${nextLabel}`}
      aria-label={t("Interface language")}
      className="flex h-9 w-9 items-center justify-center rounded-lg border border-neutral-700 text-neutral-400 transition-colors hover:bg-neutral-800 hover:text-neutral-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
    >
      <GlobeIcon />
      <span className="sr-only">{curLabel}</span>
    </button>
  );
}

function GlobeIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  );
}
