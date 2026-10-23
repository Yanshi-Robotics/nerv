/** GitHub source archives build the UI once; packed distributions carry it already. */
import {cpSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync} from "node:fs";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import {spawnSync} from "node:child_process";
import {home} from "./nerv.mjs";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
if (!existsSync(join(root, "frontend/out/index.html")) || process.argv.includes("--force")) {
  if (!existsSync(join(root, "frontend/package-lock.json"))) throw new Error("Package has no built UI or frontend sources");
  // npm_execpath avoids invoking a .cmd file through a shell on Windows.
  const npm = process.env.npm_execpath;
  if (!npm) throw new Error("Build the UI through npm install or npm pack");
  // Next skips TypeScript project detection under node_modules. Build a private
  // copy outside the npm installation, then return only its exported web assets.
  const scratch = join(home(), "temp/frontend-builds");
  mkdirSync(scratch, {recursive: true});
  const build = mkdtempSync(join(scratch, "build-"));
  try {
    writeFileSync(join(build, "README.md"), `${new Date().toISOString()}: NERV installation UI build; disposable after this installer ends.\n`);
    const frontend = join(build, "frontend");
    cpSync(join(root, "frontend"), frontend, {recursive: true, filter: source =>
      !source.slice(join(root, "frontend").length).split(/[\\/]/).some(part =>
        ["node_modules", ".next", "out", "tsconfig.tsbuildinfo"].includes(part))});
    for (const args of [["ci", "--include=dev", "--no-audit", "--no-fund"], ["run", "build", "--", "--webpack"]]) {
      const result = spawnSync(process.execPath, [npm, "--global=false", "--prefix", frontend, ...args], {
        cwd: build, stdio: "inherit", env: {...process.env, NERV_STATIC_EXPORT: "1", NEXT_PUBLIC_API: ""},
      });
      if (result.error || result.status !== 0) throw result.error || new Error(`Frontend build failed (${result.status})`);
    }
    cpSync(join(frontend, "out"), join(root, "frontend/out"), {recursive: true});
  } finally {
    rmSync(build, {recursive: true, force: true});
  }
}
