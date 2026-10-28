"use client";

import { useI18n } from "@/lib/i18n";

/** Options come from the locale registry, including future languages. */
export default function LangToggle() {
  const { lang, setLang, availableLangs, localeMeta, t } = useI18n();
  const langs = availableLangs();
  if (langs.length < 2) return null;
  return (
    <select value={lang} onChange={e => setLang(e.target.value)}
      aria-label={t("Interface language")} className="nerv-control h-11 w-44 max-w-full rounded-lg border border-neutral-700 bg-neutral-900 px-3 text-sm">
      {langs.map(code => <option key={code} value={code}>{localeMeta(code)?.label ?? code}</option>)}
    </select>
  );
}
