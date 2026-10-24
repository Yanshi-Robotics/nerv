#!/usr/bin/env node
/** Small transport wrapper; uv owns Python discovery, installation and environments. */
import {createHash} from "node:crypto";
import {existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, statSync, writeFileSync, rmSync} from "node:fs";
import {homedir} from "node:os";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";
import {spawn, spawnSync} from "node:child_process";

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

// The long-lived host must own the terminal until its cleanup is complete.
export async function runHost(command, args, options = {}) {
  if (process.platform !== "win32" && typeof process.execve === "function") {
    process.execve(command, [command, ...args], options.env ?? process.env);
    return;
  }
  // Older Node: isolate POSIX terminal signals and forward them once. Windows
  // console children receive Ctrl+C themselves; child.kill would terminate them.
  const isolated = process.platform !== "win32";
  const child = spawn(command, args, {...options, stdio: "inherit", detached: isolated});
  const interrupt = () => { if (isolated) child.kill("SIGINT"); };
  const terminate = () => { if (isolated) child.kill("SIGTERM"); };
  process.on("SIGINT", interrupt);
  process.on("SIGTERM", terminate);
  try {
    process.exitCode = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", (code, signal) => resolve(code ?? (signal === "SIGINT" ? 130 : 1)));
    });
  } finally {
    process.removeListener("SIGINT", interrupt);
    process.removeListener("SIGTERM", terminate);
  }
}

export const releaseBase = "https://github.com/Yanshi-Robotics/nerv/releases/latest/download";
export function verifyReleaseAsset(bytes, receipt) {
  if (!receipt?.sha256 || createHash("sha256").update(bytes).digest("hex") !== receipt.sha256) {
    throw new Error("Release checksum mismatch; the installed version has not changed.");
  }
}

async function legacyUpdate(args, dataHome, env, products) {
  if (args.some(arg => !["update", "--check"].includes(arg))) throw new Error("Use nerv update [--check]");
  const response = await fetch(`${releaseBase}/release.json`, {signal: AbortSignal.timeout(60000)});
  if (!response.ok) throw new Error(`Release check failed: HTTP ${response.status}`);
  const manifest = await response.json();
  if (manifest.schema !== 1 || !manifest.version || !manifest.products?.host) throw new Error("Unsupported release manifest");
  const inputs = ["launcher/products.json", `${products.sdk}/pyproject.toml`, `${products.sdk}/src`,
    "pyproject.toml", "setup.py", "src/nerv", "frontend/out", "packages/nerv-runtime-lerobot/src/nerv_runtime_lerobot/product.json"];
  const python = pythonAt(join(dataHome, "envs/host", fingerprint(root, inputs)));
  const version = existsSync(python) ? spawnSync(python, ["-m", "nerv.platform.cli", "--version"], {encoding: "utf8", env}) : null;
  if (args.includes("--check")) {
    console.log(JSON.stringify({available_version: manifest.version, installed_version: version?.stdout?.trim().replace(/^nerv /, "") || null,
      update_available: true, migration_required: true}, null, 2));
    return;
  }
  const cache = join(dataHome, ".cache/releases", `migration-${Date.now()}`);
  mkdirSync(cache, {recursive: true});
  writeFileSync(join(cache, "release.json"), JSON.stringify(manifest));
  const url = new URL(manifest.base_url);
  if (url.origin !== "https://github.com" || !url.pathname.startsWith("/Yanshi-Robotics/nerv/releases/download/")) throw new Error("Invalid official release origin");
  const download = await fetch(`${url.href}/bootstrap.py`, {signal: AbortSignal.timeout(60000)});
  if (!download.ok) throw new Error(`Installer download failed: HTTP ${download.status}`);
  const bytes = Buffer.from(await download.arrayBuffer());
  verifyReleaseAsset(bytes, manifest.files["bootstrap.py"]);
  const bootstrap = join(cache, "bootstrap.py");
  writeFileSync(bootstrap, bytes);
  const launcherResponse = await fetch(`${url.href}/launch.py`, {signal: AbortSignal.timeout(60000)});
  if (!launcherResponse.ok) throw new Error(`Launcher download failed: HTTP ${launcherResponse.status}`);
  const launcherBytes = Buffer.from(await launcherResponse.arrayBuffer());
  verifyReleaseAsset(launcherBytes, manifest.files["launch.py"]);
  mkdirSync(join(dataHome, "bootstrap"), {recursive: true});
  writeFileSync(join(dataHome, "bootstrap/install.py"), bytes);
  writeFileSync(join(dataHome, "bootstrap/launch.py"), launcherBytes);
  const installArgs = [bootstrap, "--update", "--manifest", join(cache, "release.json")];
  if (existsSync(python)) run(python, [...installArgs, "--legacy-python", python], {env});
  else run("uv", ["run", "--no-project", "--managed-python", "--python", products.python, ...installArgs], {env});
  console.log("Update complete. Run nerv --version, then nerv.");
}

export async function main(args = process.argv.slice(2)) {
  const products = JSON.parse(readFileSync(join(root, "launcher/products.json"), "utf8"));
  if (args[0] === "launcher-help") {
    console.log("nerv [serve options via: serve --port PORT] | doctor | --version\nnerv runtime install <provider>\nnerv runtime list\nRequires Node.js 22+ and uv. Default data: ~/.nerv. No command connects hardware automatically.");
    return;
  }
  const dataHome = home(), env = uvEnvironment(dataHome);
  const pending = join(dataHome, "update-pending.json");
  if (existsSync(pending)) {
    if (args.length !== 1 || args[0] !== "rollback") throw new Error("An update was interrupted. Run nerv rollback before starting NERV.");
    const recovery = JSON.parse(readFileSync(pending, "utf8"));
    const previous = JSON.parse(readFileSync(join(recovery.snapshot, "active.json"), "utf8"));
    run(previous.python, [join(dataHome, "bootstrap/install.py"), "--rollback"], {env});
    return;
  }
  // Release installations have one stable pointer, shared with Shell/PowerShell.
  const active = join(dataHome, "active.json");
  if (existsSync(active)) {
    const installed = JSON.parse(readFileSync(active, "utf8"));
    if (installed.legacy && args[0] === "update") {
      await legacyUpdate(args, dataHome, env, products);
      return;
    }
    const sharedLauncher = join(dataHome, "bootstrap", "launch.py");
    await runHost(installed.python, existsSync(sharedLauncher) ? [sharedLauncher, ...args] : ["-m", "nerv.platform.cli", ...args], {env});
    return;
  }
  if (args[0] === "update") {
    await legacyUpdate(args, dataHome, env, products);
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
  await runHost(python, ["-m", "nerv.platform.cli", ...(args.length ? args : ["serve"])], options);
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  try { await main(); } catch (error) { console.error(`NERV: ${error.message}`); process.exitCode = 1; }
}
