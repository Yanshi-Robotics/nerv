#!/usr/bin/env node
/**
 * 扫出代码里所有 `t("…")` / `tt("…")` 的**字面量** key。
 *
 * 它是 `check-locales.mjs` 和迁移工具共用的那一步，单独放一个文件，免得两处各写一份扫描逻辑
 * ——两份扫描逻辑迟早会对不上，而对不上的表现是「守卫说没问题」。
 *
 * ⚠️ 只能扫到**字面量**。动态查表（`t(p.label)`，后端送来的字符串）扫不到，
 *    那种由 check-locales.mjs 里针对后端常量的专门检查兜住。
 *
 * Scans literal `t("…")` keys. Dynamic lookups are covered by the backend-constant checks
 * in check-locales.mjs instead.
 */
import fs from "node:fs";
import path from "node:path";

/** 把 TS 字符串字面量里的转义还原成运行时真正的那个串。
 *  ⚠️ 必须处理 `\uXXXX`：源码里写 `’` 和写 `’` 是同一个 key，
 *  不还原的话登记表里会存进一串字面反斜杠，而界面永远查不中它。 */
export function unescapeTs(raw) {
  return raw.replace(/\\u\{([0-9a-fA-F]+)\}|\\u([0-9a-fA-F]{4})|\\(.)/g, (_m, brace, four, ch) => {
    if (brace) return String.fromCodePoint(parseInt(brace, 16));
    if (four) return String.fromCharCode(parseInt(four, 16));
    return { n: "\n", t: "\t", r: "\r" }[ch] ?? ch;
  });
}

export function scanKeys(root) {
  const SKIP = new Set(["node_modules", ".next", "out", "locales", "scripts"]);
  const files = [];
  (function walk(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (SKIP.has(e.name)) continue;
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.tsx?$/.test(e.name)) files.push(p);
    }
  })(root);

  const keys = new Map(); // key → 第一次出现的文件
  for (const file of files) {
    if (file.endsWith(path.join("lib", "i18n.ts"))) continue; // 词典自己不算调用点
    const text = fs.readFileSync(file, "utf8");
    for (const m of text.matchAll(/\b(?:t|tt)\(\s*"((?:[^"\\]|\\.)*)"/g)) {
      const key = unescapeTs(m[1]);
      if (!keys.has(key)) keys.set(key, path.relative(root, file));
    }
  }
  return keys;
}
