"use client";
import { useEffect, useState } from "react";
import {
  moduleCatalog,
  installModule,
  cancelModuleInstall,
  installationLog,
  POLL_NODES_MS,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
type Module = {
  id: string;
  label: string;
  installed: boolean;
  needs_repair?: boolean;
  download_bytes: number;
};
type Job = {
  id: string;
  module: string;
  state: string;
  phase: string;
  message?: string;
  downloaded?: number;
  download_total?: number;
};
export default function ModulesPanel({
  only,
  onChanged,
  onOpen,
}: {
  only?: string;
  onChanged: () => void;
  onOpen?: (id: string) => void;
}) {
  const { t } = useI18n();
  const [data, setData] = useState<{ modules: Module[]; jobs: Job[] }>({
      modules: [],
      jobs: [],
    }),
    [error, setError] = useState(""),
    [logs, setLogs] = useState<Record<string, string>>({});
  useEffect(() => {
    const read = () =>
      moduleCatalog()
        .then(setData)
        .catch((e) => setError(e.message));
    read();
    const timer = setInterval(read, POLL_NODES_MS);
    return () => clearInterval(timer);
  }, []);
  async function install(id: string) {
    try {
      setError("");
      await installModule(id);
      setData(await moduleCatalog());
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const modules = data.modules.filter((m) => !only || m.id === only);
  const jobs = data.jobs.filter((j) => !only || j.module === only);
  const latest = jobs.filter(
    (j, i) => !jobs.slice(i + 1).some((v) => v.module === j.module),
  );
  const active = latest.filter((j) => ["queued", "running"].includes(j.state));
  const card = (m: Module) => (
    <article
      className="rounded-xl border border-neutral-700 p-4 space-y-3"
      key={m.id}
    >
      <h3 className="font-medium">{m.label}</h3>
      <p className="muted">
        {t(
          m.id === "so101-sim"
            ? "Pick and place, snack sorting and Tower of Hanoi. No trained policy included."
            : "LeRobot 0.6.1 and Feetech driver. Connecting the arm is a separate step.",
        )}
      </p>
      <p className={m.needs_repair ? "error-text" : "text-xs"}>
        {t(
          m.needs_repair
            ? "Needs repair"
            : m.installed
              ? "Installed"
              : "Not installed",
        )}
      </p>
      {m.installed && !m.needs_repair ? (
        <>
          <button
            className="nerv-control nerv-primary action"
            onClick={() => onOpen?.(m.id)}
            disabled={!onOpen}
          >
            {t("Open")}
          </button>
          <details>
            <summary className="cursor-pointer text-xs py-2">
              {t("Maintenance")}
            </summary>
            <button
              className="nerv-control action"
              disabled={!!active.length}
              onClick={() => install(m.id)}
            >
              {t("Reinstall component")}
            </button>
          </details>
        </>
      ) : (
        <>
          <p className="text-xs text-neutral-400">
            {(m.download_bytes / 1048576).toFixed(1)} MB ·{" "}
            {t("Bundle only; Python dependencies download separately")}
          </p>
          <button
            className="nerv-control nerv-primary action"
            disabled={!!active.length}
            onClick={() => install(m.id)}
          >
            {t(m.needs_repair ? "Repair component" : "Install component")}
          </button>
        </>
      )}
    </article>
  );
  const jobCard = (job: Job) => (
    <article
      key={job.id}
      role="status"
      className="rounded-xl border border-neutral-700 p-4 space-y-2"
    >
      <p>
        {data.modules.find((m) => m.id === job.module)?.label || job.module}
      </p>
      <p className="text-sm">
        {t(job.state)} · {t(job.phase)}
      </p>
      {job.downloaded !== undefined && (
        <p className="text-xs">
          {(job.downloaded / 1048576).toFixed(1)} MB{" "}
          {job.download_total
            ? `/ ${(job.download_total / 1048576).toFixed(1)} MB`
            : ""}
        </p>
      )}
      {job.message && <p className="break-words text-sm">{job.message}</p>}
      <div className="flex gap-3">
        <button
          className="nerv-control underline text-xs"
          onClick={() =>
            installationLog(job.id)
              .then((v) => setLogs({ ...logs, [job.id]: v.log }))
              .catch((e) => setError(e.message))
          }
        >
          {t("Show installation log")}
        </button>
        {["queued", "running"].includes(job.state) ? (
          <button
            className="nerv-control underline text-xs"
            onClick={() =>
              cancelModuleInstall(job.id).catch((e) => setError(e.message))
            }
          >
            {t("Cancel installation")}
          </button>
        ) : (
          ["failed", "cancelled"].includes(job.state) && (
            <button
              className="nerv-control underline text-xs"
              disabled={!!active.length}
              onClick={() => install(job.module)}
            >
              {t("Retry")}
            </button>
          )
        )}
      </div>
      {logs[job.id] && (
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs">
          {logs[job.id]}
        </pre>
      )}
    </article>
  );
  return (
    <section className={only ? "space-y-4" : "settings-content space-y-6"}>
      {!only && (
        <>
          <h1>{t("Components and simulation")}</h1>
          <p className="muted">
            {t(
              "Install only the components you need. Training environments remain separate.",
            )}
          </p>
        </>
      )}
      <div className="space-y-3">
        <h2>{t("Installed")}</h2>
        {modules.filter((m) => m.installed || m.needs_repair).map(card)}
        {!modules.some((m) => m.installed || m.needs_repair) && (
          <p className="muted">{t("No components installed yet.")}</p>
        )}
      </div>
      {active.length > 0 && (
        <div className="space-y-3">
          <h2>{t("Installation progress")}</h2>
          {active.map(jobCard)}
        </div>
      )}
      <div className="space-y-3">
        <h2>{t("Available to install")}</h2>
        {modules
          .filter(
            (m) =>
              !m.installed &&
              !m.needs_repair &&
              !active.some((j) => j.module === m.id),
          )
          .map(card)}
      </div>
      {latest.some((j) => !["queued", "running"].includes(j.state)) && (
        <details>
          <summary className="cursor-pointer">
            {t("Installation history")}
          </summary>
          <div className="space-y-3 py-3">
            {latest
              .filter((j) => !["queued", "running"].includes(j.state))
              .map(jobCard)}
          </div>
        </details>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
    </section>
  );
}
