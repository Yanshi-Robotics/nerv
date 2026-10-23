import assert from "node:assert/strict";
import {mkdtempSync, mkdirSync, writeFileSync, symlinkSync} from "node:fs";
import {join, resolve} from "node:path";
import {spawnSync} from "node:child_process";
import {test} from "node:test";
import {fingerprint, home, pythonAt, uvEnvironment, root} from "./nerv.mjs";

const temporary = join(root, "temp/launcher-tests");
mkdirSync(temporary, {recursive: true});
writeFileSync(join(temporary, "README.md"), "2026-10-23: launcher tests; disposable after verification. Contains explicit fixtures only.\n");
test("all runtime data roots derive from the selected home", () => {
  const selected = home({NERV_HOME: join(temporary, "中文 space")});
  const env = uvEnvironment(selected, {});
  assert.equal(env.NERV_HOME, selected);
  assert.equal(env.UV_CACHE_DIR, join(selected, ".cache/uv"));
  assert.equal(pythonAt(selected, "win32"), join(selected, "Scripts/python.exe"));
  assert.equal(pythonAt(selected, "linux"), join(selected, "bin/python"));
});
test("installed environment identity follows source content, not directory location or bytecode", () => {
  const base = mkdtempSync(join(temporary, "identity-"));
  mkdirSync(join(base, "src/__pycache__"), {recursive: true});
  writeFileSync(join(base, "src/app.py"), "original");
  const before = fingerprint(base, ["src"]);
  writeFileSync(join(base, "src/__pycache__/app.pyc"), "cache");
  assert.equal(fingerprint(base, ["src"]), before);
  writeFileSync(join(base, "src/app.py"), "changed");
  assert.notEqual(fingerprint(base, ["src"]), before);
});
test("CLI entry works through the symlink installed by npm", {skip: process.platform === "win32"}, () => {
  const base = mkdtempSync(join(temporary, "bin-"));
  const executable = join(base, "nerv");
  symlinkSync(resolve(root, "launcher/nerv.mjs"), executable);
  const result = spawnSync(process.execPath, [executable, "launcher-help"], {encoding: "utf8"});
  assert.equal(result.status, 0);
  assert.match(result.stdout, /nerv runtime install/);
});
test("invalid provider fails before installing dependencies or opening hardware", () => {
  const result = spawnSync(process.execPath, [join(root, "launcher/nerv.mjs"), "runtime", "install", "unknown"], {encoding: "utf8"});
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Available: lerobot0.6.1/);
});
test("distribution contains built UI and Python sources without private or generated files", () => {
  const result = spawnSync(process.execPath, [process.env.npm_execpath, "pack", "--dry-run",
    "--ignore-scripts", "--json", "--cache", join(temporary, "npm-cache")], {
    cwd: root, encoding: "utf8", maxBuffer: 5 * 1024 * 1024,
  });
  assert.equal(result.status, 0, result.stderr);
  const files = JSON.parse(result.stdout)[0].files.map(item => item.path);
  for (const expected of ["frontend/out/index.html", "launcher/nerv.mjs", "src/nerv/vlm/catalog.json",
    "packages/nerv-runtime-sdk/src/nerv_runtime_sdk/__init__.py",
    "packages/nerv-runtime-lerobot/src/nerv_runtime_lerobot/product.json"]) {
    assert.ok(files.includes(expected), `Missing package resource: ${expected}; build the UI before testing`);
  }
  assert.deepEqual(files.filter(path => /__pycache__|\.pyc$|\.egg-info\/|\.test\.mjs$|(^|\/)(temp|docs|memory)\/|(^|\/)\.env$/.test(path)), []);
});
