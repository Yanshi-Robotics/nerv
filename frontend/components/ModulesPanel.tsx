"use client";
import {useEffect, useState} from "react";
import {moduleCatalog, installModule, cancelModuleInstall, installationLog, checkRelease, POLL_NODES_MS} from "@/lib/api";
import {useI18n} from "@/lib/i18n";

type Module = {id: string; label: string; installed: boolean; download_bytes: number};
type Job = {id: string; module: string; state: string; phase: string; message?: string; downloaded?: number; download_total?: number};
export default function ModulesPanel({only, onChanged}: {only?: string; onChanged: () => void}) {
  const {t} = useI18n();
  const [data, setData] = useState<{modules: Module[]; jobs: Job[]}>({modules: [], jobs: []});
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [release, setRelease] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const load = () => moduleCatalog().then(setData).catch(e => setError(e.message));
    load(); const timer = setInterval(load, POLL_NODES_MS);
    return () => clearInterval(timer);
  }, []);
  async function install(id: string) {
    try {setError(""); await installModule(id); setData(await moduleCatalog()); onChanged();} catch(e) {setError((e as Error).message);}
  }
  return <section className={only ? "space-y-3" : "col-span-2 overflow-auto p-6 space-y-5"}>
    {!only && <><h1 className="text-xl">{t("Simulation and components")}</h1><p className="text-sm text-neutral-400">{t("Install only the components you need. Training environments remain separate.")}</p></>}
    {!only && <div className="space-y-2"><button className="nerv-control rounded border px-3 py-2 text-sm" onClick={() => checkRelease().then(v => setRelease(`${v.installed_version || "—"} → ${v.available_version}`)).catch(e => setError(e.message))}>{t("Check for updates")}</button>{release && <p className="text-sm">{release} · {t("Stop tasks, disconnect and exit NERV before running nerv update")}</p>}</div>}
    {[false, true].map(installed => <div key={String(installed)} className="space-y-3">
      {!only && <h2 className="text-sm text-neutral-400">{t(installed ? "Installed" : "Available to install")}</h2>}
      {data.modules.filter(m => (!only || m.id === only) && m.installed === installed).map(m => {
        const job = data.jobs.filter(j => j.module === m.id).at(-1);
        const active = job && ["queued", "running"].includes(job.state);
        return <article key={m.id} className="rounded-xl border border-neutral-700 p-4 space-y-2">
          <h3 className="font-medium">{m.label}</h3>
          <p className="text-xs text-neutral-400">{t(m.id === "so101-sim" ? "Pick and place, snack sorting and Tower of Hanoi. No trained policy included." : "LeRobot 0.6.1 and Feetech driver. Connecting the arm is a separate step.")}</p>
          <p className="text-xs">{t(m.installed ? "Installed" : "Not installed")}</p><p className="text-xs text-neutral-400">{(m.download_bytes / 1048576).toFixed(1)} MB · {t("Bundle only; Python dependencies download separately")}</p>
          {<button disabled={!!active} className="nerv-control nerv-primary rounded-lg px-3 py-2 text-sm" onClick={() => install(m.id)}>{t(active ? "Installing…" : m.installed ? "Reinstall component" : "Install component")}</button>}
          {job && <div role="status" className="text-xs space-y-1"><p>{t(job.state)} · {t(job.phase)}</p>{job.downloaded !== undefined && <p>{(job.downloaded / 1048576).toFixed(1)} MB {job.download_total ? `/ ${(job.download_total / 1048576).toFixed(1)} MB` : ""}</p>}{job.message && <p>{job.message}</p>}<button className="nerv-control underline" onClick={() => installationLog(job.id).then(v => setLogs({...logs, [job.id]: v.log})).catch(e => setError(e.message))}>{t("Show installation log")}</button>{logs[job.id] && <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-neutral-950 p-2">{logs[job.id]}</pre>}{active && <button className="nerv-control underline" onClick={() => cancelModuleInstall(job.id).catch(e => setError(e.message))}>{t("Cancel installation")}</button>}</div>}
        </article>;
      })}
    </div>)}
    {error && <p role="alert" className="text-sm text-[var(--control-danger-text)]">{error}</p>}
  </section>;
}
