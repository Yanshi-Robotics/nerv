#!/usr/bin/env node
/** Small transport wrapper; uv owns Python discovery, installation and environments. */
import {createHash} from "node:crypto";
import {existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, statSync, writeFileSync, rmSync} from "node:fs";
import {homedir} from "node:os";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";
import {spawnSync} from "node:child_process";

export const root = dirname(dirname(fileURLToPath(import.meta.url)));
export function home(env = process.env) { return resolve(env.NERV_HOME || join(homedir(), ".nerv")); }
export function pythonAt(directory, platform = process.platform) {
  return join(directory, platform === "win32" ? "Scripts/python.exe" : "bin/python");
}
export function uvEnvironment(dataHome, env = process.env) {
  return {...env, NERV_HOME: dataHome, PYTHONUTF8: "1", UV_CACHE_DIR: join(dataHome, ".cache/uv"),
    UV_PYTHON_INSTALL_DIR: join(dataHome, "python")};
}
export function fingerprint(base, paths) {
  const hash = createHash("sha256");
  function visit(relative) {
    if (relative.split(/[\\/]/).some(p => p === "__pycache__" || p.endsWith(".egg-info"))) return;
    const full = join(base, relative);
    if (statSync(full).isDirectory()) {
      for (const name of readdirSync(full).sort()) visit(join(relative, name));
    } else { hash.update(relative.replaceAll("\\", "/")); hash.update(readFileSync(full)); }
  }
  paths.forEach(visit);
  return hash.digest("hex").slice(0, 20);
}
function run(command, args, options) {
  const result = spawnSync(command, args, {stdio: "inherit", ...options});
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited ${result.status ?? result.signal}`);
}

export function main(args = process.argv.slice(2)) {
  const products = JSON.parse(readFileSync(join(root, "launcher/products.json"), "utf8"));
  if (args[0] === "launcher-help") {
    console.log("nerv [serve options via: serve --port PORT] | doctor | --version\nnerv runtime install <provider>\nnerv runtime list\nRequires Node.js 22+ and uv. Default data: ~/.nerv. No command connects hardware automatically.");
    return;
  }
  const dataHome = home(), env = uvEnvironment(dataHome);
  // Release installations have one stable pointer, shared with Shell/PowerShell.
  const active = join(dataHome, "active.json");
  if (existsSync(active)) {
    const installed = JSON.parse(readFileSync(active, "utf8"));
    run(installed.python, ["-m", "nerv.platform.cli", ...args], {env});
    return;
  }
  if (args[0] === "runtime" && args[1] === "list") {
    const directory = join(dataHome, "runtimes");
    const records = existsSync(directory) ? readdirSync(directory).filter(n => n.endsWith(".json")) : [];
    console.log(JSON.stringify(records.map(n => JSON.parse(readFileSync(join(directory, n), "utf8"))), null, 2));
    return;
  }
  const device = args[0] === "runtime";
  const provider = device ? args[2] : "host";
  if (device && (args[1] !== "install" || args.length !== 3 || !products.runtimes[provider])) {
    throw new Error(`Use nerv runtime install <provider>. Available: ${Object.keys(products.runtimes).join(", ")}`);
  }
  const probe = spawnSync("uv", ["--version"], {encoding: "utf8", env});
  if (probe.error || probe.status !== 0) throw new Error("Install uv first: https://docs.astral.sh/uv/getting-started/installation/ . No system Python environment was modified.");
  const source = device ? products.runtimes[provider].package : products.host.package;
  const inputs = ["launcher/products.json", `${products.sdk}/pyproject.toml`, `${products.sdk}/src`,
    ...(device ? [`${source}/pyproject.toml`, `${source}/src`] : ["pyproject.toml", "setup.py", "src/nerv", "frontend/out", "packages/nerv-runtime-lerobot/src/nerv_runtime_lerobot/product.json"])];
  const digest = fingerprint(root, inputs);
  const directory = join(dataHome, "envs", provider, digest);
  const python = pythonAt(directory);
  const receipt = join(directory, "nerv-install.json");
  mkdirSync(dataHome, {recursive: true});
  const options = {cwd: dataHome, env};
  if (!existsSync(receipt) || !existsSync(python)) {
    // A failed install has no receipt and can be retried. Environments are never
    // deleted or upgraded in place while another version may be running.
    mkdirSync(dirname(directory), {recursive: true});
    const lock = `${directory}.installing`;
    try { mkdirSync(lock); } catch { throw new Error(`Installation locked: ${lock}. Wait for the other installer. After a crashed installer, remove only this empty lock directory before retrying.`); }
    try {
      if (!existsSync(python)) run("uv", ["venv", "--python", products.python, directory], options);
      const target = resolve(root, source) + (device ? "" : `[${products.host.extras}]`);
      run("uv", ["pip", "install", "--python", python, resolve(root, products.sdk), target], options);
      run("uv", ["pip", "check", "--python", python], options);
      writeFileSync(receipt, JSON.stringify({digest, provider, python, installed_at: new Date().toISOString()}));
    } finally { rmSync(lock, {recursive: true}); }
  }
  if (device) {
    const registry = join(dataHome, "runtimes");
    mkdirSync(registry, {recursive: true});
    const record = {provider, python, digest, label: products.runtimes[provider].label};
    writeFileSync(join(registry, `${provider}.json`), JSON.stringify(record));
    console.log(`Runtime installed without connecting hardware.\n${python}`);
    return;
  }
  const result = spawnSync(python, ["-m", "nerv.platform.cli", ...(args.length ? args : ["serve"])], {stdio: "inherit", ...options});
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  try { main(); } catch (error) { console.error(`NERV: ${error.message}`); process.exitCode = 1; }
}
