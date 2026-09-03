"use client";

import { useI18n } from "@/lib/i18n";

/** 侧栏底部的语言切换，和主题按钮并排。
 *
 *  ⛔ **这里不认识任何具体语言。** 上一版是个二态开关（`isEn ? "zh" : "en"`，显示写死的
 *  `"EN"/"中"`），加第三门语言时它是必改的地方之一——所以改成遍历 `locales/` 里有什么就给什么。
 *  每个语言的显示名来自它自己文件里的 `meta.label`（用它自己的语言写的）。
 *
 *  只有一门语言时不渲染：一个没得选的选择器只是噪音。
 *
 *  Knows about no specific language: it lists whatever is in `locales/`.
 */
export default function LangToggle() {
  const { lang, setLang, availableLangs, localeMeta, t } = useI18n();
  const langs = availableLangs();
  if (langs.length < 2) return null;

  const current = localeMeta(lang);
  return (
    <label className="relative flex h-7 items-center">
      <span className="sr-only">{t("Interface language")}</span>
      <select
        value={lang}
        onChange={(e) => setLang(e.target.value)}
        title={t("Interface language")}
        // appearance-none + 自绘：原生 select 的箭头在深浅主题下都很难看齐
        className="h-7 cursor-pointer appearance-none rounded-md border border-neutral-700 bg-transparent pl-2 pr-5 text-[11px] font-medium text-neutral-400 transition-colors hover:border-neutral-500 hover:text-neutral-100 focus:outline-none"
      >
        {langs.map((code) => {
          const meta = localeMeta(code);
          return (
            <option key={code} value={code} className="bg-neutral-900 text-neutral-100">
              {meta?.label ?? code}
            </option>
          );
        })}
      </select>
      <svg
        aria-hidden="true"
        viewBox="0 0 8 5"
        className="pointer-events-none absolute right-1.5 h-[5px] w-2 fill-current text-neutral-500"
      >
        <path d="M0 0h8L4 5z" />
      </svg>
      <span className="sr-only">{current?.label ?? lang}</span>
    </label>
  );
}
