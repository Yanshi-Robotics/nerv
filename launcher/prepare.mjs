/** GitHub source archives build the UI once; packed distributions carry it already. */
import {existsSync} from "node:fs";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import {spawnSync} from "node:child_process";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
if (!existsSync(join(root, "frontend/out/index.html")) || process.argv.includes("--force")) {
  if (!existsSync(join(root, "frontend/package-lock.json"))) throw new Error("Package has no built UI or frontend sources");
  // npm_execpath avoids invoking a .cmd file through a shell on Windows.
  const npm = process.env.npm_execpath;
  if (!npm) throw new Error("Build the UI through npm install or npm pack");
  for (const args of [["ci", "--include=dev", "--no-audit", "--no-fund"], ["run", "build"]]) {
    const result = spawnSync(process.execPath, [npm, "--global=false", "--prefix", join(root, "frontend"), ...args], {
      cwd: root, stdio: "inherit", env: {...process.env, NERV_STATIC_EXPORT: "1", NEXT_PUBLIC_API: ""},
    });
    if (result.error || result.status !== 0) throw result.error || new Error(`Frontend build failed (${result.status})`);
  }
}
