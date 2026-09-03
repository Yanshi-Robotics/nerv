"use client";

import { useSyncExternalStore } from "react";

import { LOCALES, SOURCE_LANG, type LocaleMeta } from "./locales.generated";

/** 界面语言。**英文是正典——词条以英文原文为 key。**
 *
 *  为什么用原文当 key，而不是 `sidebar.newSession` 这种命名 key：
 *  - 代码里读得懂：`t("New session")` 一眼知道显示什么，不用来回翻词表；
 *  - **漏翻自动回落成英文**，而不是在界面上露出 `sidebar.newSession` 这种事故；
 *  - 省掉"给两百条文案起名字"这一步——那一步既费时又容易起重名。
 *
 *  ⛔ **加一门语言 = 往 `locales/` 丢一个 JSON，再跑 `npm run gen:locales`。不改这个文件。**
 *     这里没有任何语言清单、没有任何 `lang === "xx"` 的分支——那些以前都有，
 *     加第三门语言时会一个个变成要改的地方，所以全拆了。
 *
 *  ⚠️ 唯一保留的语言字面量是生成文件里的 `SOURCE_LANG`（正典语言 = 域常量）。
 *     别再往代码里加第二个。
 *
 *  ⛔ 只翻**用户看得见**的东西。类型定义上的行尾注释、开发者注释一律不进词条。
 *
 *  Interface language. **English is canonical — entries are keyed by the English source string.**
 *  Adding a language means dropping a JSON into `locales/`; nothing in this file changes.
 */
export type Lang = string;

const STORAGE_KEY = "nerv-lang";

/** 可用语言，由 `locales/` 目录扫描得出（见 scripts/gen-locales.mjs）。 */
export function availableLangs(): Lang[] {
  return Object.keys(LOCALES);
}

export function localeMeta(lang: Lang): LocaleMeta | undefined {
  return LOCALES[lang]?.meta;
}

// ─────────────────────────────────────────────────────── 极简语言 store（无第三方库）
// 为什么自己写：整个应用只有一个全局标量（当前语言），用 useSyncExternalStore 订阅足够了。
// 引一个 i18n 框架反而要装 provider、配 namespace、处理 SSR 水合——不值当。
//
// 预渲染阶段（SSR / 静态导出）拿不到 localStorage 和 navigator，只能先按一个值渲染，
// 而那个值就是正典语言：静态导出的 HTML 是随 wheel 分发、也是别人截图会看到的那一份。
// 用户自己选过语言之后就再也不闪（走 localStorage）。
const INITIAL_LANG: Lang = SOURCE_LANG;
let current: Lang = INITIAL_LANG;
const listeners = new Set<() => void>();

function read(): Lang {
  if (typeof window === "undefined") return INITIAL_LANG;
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    // ⛔ 校验的是"这门语言现在还在不在"，不是硬编码的白名单——
    //    删掉一个语言文件之后，存着它的浏览器也要能优雅退回。
    if (saved && LOCALES[saved]) return saved;
  } catch {
    /* 隐私模式等读不了，往下走浏览器语言 */
  }
  // 没选过 → 跟随浏览器语言：每个语言文件自己声明 `meta.match` 前缀，这里只做匹配。
  // 取**最长**的匹配前缀，这样将来加了 zh-TW 之类的细分语言时，它会赢过泛化的 zh。
  const nav = (typeof navigator !== "undefined" ? navigator.language : "").toLowerCase();
  let best: Lang = INITIAL_LANG;
  let bestLen = 0;
  for (const { meta } of Object.values(LOCALES)) {
    for (const prefix of meta.match) {
      const p = prefix.toLowerCase();
      if (nav.startsWith(p) && p.length > bestLen) {
        best = meta.code;
        bestLen = p.length;
      }
    }
  }
  return best;
}

// 首次在浏览器里加载时对齐一次真实偏好（SSR 阶段拿不到 localStorage，先按 INITIAL_LANG 渲染）
if (typeof window !== "undefined") current = read();

export function getLang(): Lang {
  return current;
}

export function setLang(next: Lang): void {
  if (next === current || !LOCALES[next]) return;
  current = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* 存不了也照样切，只是刷新后回到默认 */
  }
  // html lang 由各语言文件自己声明（zh 要的是 zh-CN，不是 zh）——不在这里做映射表。
  document.documentElement.lang = LOCALES[next].meta.html;
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** 翻译一条文案。`vars` 用来填 `{name}` 这类占位符。
 *  查不到就原样返回 key——**漏翻只是显示英文原文，不会显示 key**（因为 key 就是英文原文）。
 *
 *  ⚠️ 这里也是**后端文案**的翻译口：后端一律说英文正典，前端把收到的字符串过一次这个函数，
 *  命中就是框架自己的话、显示译文；不命中就原样输出（模型的回复走的就是这条）。 */
export function translate(key: string, lang: Lang, vars?: Record<string, string | number>): string {
  let out = LOCALES[lang]?.strings[key] ?? key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) out = out.replaceAll(`{${k}}`, String(v));
  }
  return out;
}

/** 给**非组件**代码用的翻译（普通函数、事件回调里没法调 hook）。
 *  ⚠️ 它不订阅语言变化——但调用它的那个组件会因为自己用了 useI18n 而重渲染，
 *  重渲染时这个函数被重新执行、读到的就是新语言。所以实际表现是对的。
 *  能用 useI18n 的地方就别用它。 */
export function tt(key: string, vars?: Record<string, string | number>): string {
  return translate(key, getLang(), vars);
}

/** 组件里这样用：`const { t, lang, setLang } = useI18n();` 然后 `t("New session")`。
 *  语言一变，所有用到这个 hook 的组件自动重渲染。 */
export function useI18n() {
  // ⚠️ 第三个参数是**服务端快照**，预渲染（SSR / 静态导出）用的就是它。
  //    它必须和文件上方那个 `current` 的初值一致——两处都是"还不知道用户偏好时用什么"。
  //    v1.1 改默认语言时只改了上面那一处、漏了这里，结果静态导出的 HTML 仍是中文：
  //    一个默认值散在两个地方，就一定会有人只改一个。现在两处共用 INITIAL_LANG。
  const lang = useSyncExternalStore(subscribe, getLang, () => INITIAL_LANG);
  return {
    lang,
    setLang,
    availableLangs,
    localeMeta,
    t: (key: string, vars?: Record<string, string | number>) => translate(key, lang, vars),
  };
}
