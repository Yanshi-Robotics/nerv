#!/usr/bin/env node
/**
 * 词条守卫。CI 每次跑。
 *
 * ⛔ **它守的不是"词条文件长得对不对"，而是"这次修的东西不会再坏"。**
 *    第一版守卫只查文件结构，四条全绿——而那时候停顿语和运行参数标签的翻译已经可以被
 *    任何一次改动静默弄断，没有任何东西会红。这个项目在 v1.1 抓到过四条那样的守卫，
 *    所以下面每一条都对着一个**具体会坏的东西**。
 *
 * Each check guards a specific thing that can break, not the shape of the files.
 */
import fs from "node:fs";
import path from "node:path";

import { scanKeys } from "./scan-keys.mjs";

const HERE = path.dirname(new URL(import.meta.url).pathname);
const FRONTEND = path.resolve(HERE, "..");
const REPO = path.resolve(FRONTEND, "..");
const LOCALES_DIR = path.join(FRONTEND, "locales");
const GENERATED = path.join(FRONTEND, "lib", "locales.generated.ts");
const SOURCE_LANG = "en";

const problems = [];
const fail = (check, detail) => problems.push(`✗ ${check}\n    ${detail}`);
const ok = (check, note = "") => console.log(`  ✓ ${check}${note ? `  ${note}` : ""}`);

// ── 读词条 ────────────────────────────────────────────────────────────────
const files = fs.readdirSync(LOCALES_DIR).filter((f) => f.endsWith(".json")).sort();
const catalogues = {};
for (const file of files) {
  const code = path.basename(file, ".json");
  catalogues[code] = JSON.parse(fs.readFileSync(path.join(LOCALES_DIR, file), "utf8"));
}
const registry = catalogues[SOURCE_LANG]?.strings ?? {};

// ── G1 · 加一门语言不改任何逻辑 ────────────────────────────────────────────
// 这是整件事的核心主张，所以它是**用行为**验的：往目录里丢一个合成语言，
// 跑一遍真正的生成器，看它有没有自己出现。不是查代码里有没有硬编码——
// 那种查法查不出"我忘了某处还写着 if (lang === 'zh')"。
{
  const probe = path.join(LOCALES_DIR, "zz.json");
  const backup = fs.existsSync(GENERATED) ? fs.readFileSync(GENERATED, "utf8") : null;
  try {
    fs.writeFileSync(probe, JSON.stringify({
      meta: { code: "zz", label: "Probe", html: "zz", match: [] },
      strings: { "New session": "PROBE" },
    }));
    const { execFileSync } = await import("node:child_process");
    execFileSync(process.execPath, [path.join(HERE, "gen-locales.mjs")], { stdio: "pipe" });
    const out = fs.readFileSync(GENERATED, "utf8");
    if (!out.includes('"zz"') || !out.includes("PROBE")) {
      fail("G1 加一门语言不改任何逻辑", "丢进去的合成语言没有出现在生成的索引里");
    } else {
      ok("G1 加一门语言不改任何逻辑", "(丢一个 zz.json → 自动出现)");
    }
  } finally {
    fs.rmSync(probe, { force: true });
    if (backup !== null) fs.writeFileSync(GENERATED, backup);
  }
}

// ── G2 · 每个 t("字面量") 都在登记表里 ──────────────────────────────────────
// 打错一个字 → 那处永远显示 key 本身。以前那张表是 zh→en，打错了会静默回落中文；
// 现在 key 就是英文原文，打错了看起来"像正常英文"，更难发现。所以这条必须机器查。
{
  const keys = scanKeys(FRONTEND);
  const missing = [...keys].filter(([k]) => !(k in registry));
  if (missing.length) {
    fail("G2 调用点的 key 都在登记表里",
      missing.map(([k, f]) => `${f}: ${JSON.stringify(k.slice(0, 60))}`).join("\n    "));
  } else {
    ok("G2 调用点的 key 都在登记表里", `(${keys.size} 个)`);
  }
}

// ── G3 · 各语言的 key ⊆ 登记表 ──────────────────────────────────────────────
// 搬运时掉条目、或译文里留了个过时的 key，都会静默。
for (const [code, cat] of Object.entries(catalogues)) {
  if (code === SOURCE_LANG) continue;
  const orphans = Object.keys(cat.strings).filter((k) => !(k in registry));
  if (orphans.length) {
    fail(`G3 ${code}.json 里没有孤儿 key`,
      orphans.slice(0, 8).map((k) => JSON.stringify(k.slice(0, 60))).join("\n    "));
  }
}
if (!problems.some((p) => p.startsWith("✗ G3"))) ok("G3 各语言无孤儿 key");

// ── G4/G5 去哪了 ────────────────────────────────────────────────────────────
// "后端送的文案必须在登记表里"这两条守卫**在 Python 那边**（tests/test_locales.py）。
//
// 一开始写在这里，用正则去抠 Python 的字典——结果第一次跑就抓错了：`STOP_REPLIES` 的值是
// **常量引用**不是字面量，正则只看得到 key，于是把 reason code "time" 当成了要翻译的文案。
// 与其把 Python 的语法猜得更准，不如**让 Python 自己回答**：那边 import 一下就拿到真值，
// 再读这个目录下的 en.json 比对即可。
//
// 教训值得留在这里：一个需要解析另一门语言的守卫，应该住在那门语言里。

// ── G6 · 生成物是最新的 ────────────────────────────────────────────────────
// 产物**入库**（不 gitignore），所以它可能过时。过时了就该 CI 红，而不是发出一个
// 词条不对的包——这个仓已经吃过两次"静默用旧产物"的亏。
{
  const before = fs.readFileSync(GENERATED, "utf8");
  const { execFileSync } = await import("node:child_process");
  execFileSync(process.execPath, [path.join(HERE, "gen-locales.mjs")], { stdio: "pipe" });
  const after = fs.readFileSync(GENERATED, "utf8");
  if (before !== after) {
    fs.writeFileSync(GENERATED, after);
    fail("G6 生成物是最新的", "lib/locales.generated.ts 与 locales/ 对不上（已就地重新生成，把它一起提交）");
  } else {
    ok("G6 生成物是最新的");
  }
}

// ── 报告 ───────────────────────────────────────────────────────────────────
console.log("");
for (const code of Object.keys(catalogues)) {
  const n = Object.keys(catalogues[code].strings).length;
  const missing = Object.keys(registry).filter((k) => !(k in catalogues[code].strings)).length;
  const tag = code === SOURCE_LANG ? "registry" : missing ? `${missing} untranslated → English` : "complete";
  console.log(`  ${code.padEnd(4)} ${String(n).padStart(4)}  ${catalogues[code].meta.label.padEnd(10)} ${tag}`);
}

if (problems.length) {
  console.error(`\n${problems.length} 项不过 / ${problems.length} check(s) failed\n`);
  for (const p of problems) console.error(p);
  process.exit(1);
}
console.log("\n词条守卫全部通过 / all locale checks passed");
