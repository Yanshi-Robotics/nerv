"use client";
import { useEffect, useRef, useState } from "react";
import { installationLog, POLL_SESSION_LOGS_MS, type InstallationLog } from "@/lib/api";
import { downloadPercent, isInstalling, watchInstallLog, type InstallJob } from "@/lib/install-progress";
import { useI18n } from "@/lib/i18n";

// Allow a small rounding margin when testing whether a user is at the log's bottom.
const LOG_BOTTOM_MARGIN_PX = 24;

export function InstallationLocation({ path, root }: {path?: string | null; root?: string}) {
  const { t } = useI18n();
  if (!path && !root) return null;
  return <details className="install-location">
    <summary className="nerv-control cursor-pointer py-2 text-xs">{t("Installation location")}</summary>
    <p className="muted text-xs">{t(path ? "Component environment" : "Component environments folder")}</p>
    <code className="block text-xs break-all select-all">{path || root}</code>
    <p className="muted text-xs">{t("Managed by this NERV instance. Your training environment is unchanged.")}</p>
  </details>;
}

export default function InstallJobCard({job, label, root, onCancel, onRetry, retryDisabled}: {
  job: InstallJob; label: string; root?: string; onCancel: () => void;
  onRetry: () => void; retryDisabled: boolean;
}) {
  const { t } = useI18n();
  const running = isInstalling(job);
  const [expanded, setExpanded] = useState(running);
  const [log, setLog] = useState<InstallationLog | null>(null);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [error, setError] = useState(false);
  const [following, setFollowing] = useState(true);
  const viewport = useRef<HTMLPreElement>(null);
  const percent = downloadPercent(job);
  useEffect(() => {
    if (!expanded) return;
    return watchInstallLog(signal => installationLog(job.id, signal), value => {
      setLog(value); setCheckedAt(Date.now()); setError(false);
    }, () => setError(true), POLL_SESSION_LOGS_MS, running);
  }, [job.id, running, expanded]);
  useEffect(() => {
    if (following && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [log?.log, following, expanded]);
  const secondsQuiet = log?.modified_at && checkedAt
    ? Math.max(0, Math.floor(checkedAt / 1000 - log.modified_at)) : null;
  return <article className="install-job rounded-xl border border-neutral-700 p-4 space-y-3">
    <h3 className="font-medium">{label}</h3>
    <p className="text-sm" role="status">{t(job.state)} · {t(job.phase)}</p>
    {running && <>
      <progress className="install-progress" max={100} value={percent} aria-label={t("Installation progress")} />
      <p className="muted text-xs">{percent !== undefined
        ? `${t("Current file download")}: ${percent}% · ${((job.downloaded || 0) / 1048576).toFixed(1)} / ${((job.download_total || 0) / 1048576).toFixed(1)} MiB`
        : t("Working on this stage; total progress is not available.")}</p>
      {job.phase === "download" && job.asset && <p className="text-xs break-all">{job.asset}</p>}
    </>}
    <InstallationLocation path={job.installation_path} root={root} />
    {job.message && <p className="break-words text-sm">{job.message}</p>}
    <div className="flex flex-wrap gap-3">
      <button className="nerv-control underline text-xs" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        {t(expanded ? "Hide installation log" : "Show installation log")}
      </button>
      {running ? <button className="nerv-control underline text-xs" onClick={onCancel}>{t("Cancel installation")}</button>
        : ["failed", "cancelled"].includes(job.state) && <button className="nerv-control underline text-xs" disabled={retryDisabled} onClick={onRetry}>{t("Retry")}</button>}
    </div>
    {expanded && <div className="install-log-panel">
      <div className="install-log-heading">
        <span>{t(running ? "Live installation log" : "Installation log")}</span>
        <button className="nerv-control underline" onClick={() => setFollowing(!following)} aria-pressed={following}>
          {t(following ? "Following latest output" : "Follow latest output")}
        </button>
      </div>
      <pre ref={viewport} tabIndex={0} aria-label={t("Installation log")} className="install-log-output"
        onScroll={() => {const el = viewport.current; if (el) setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < LOG_BOTTOM_MARGIN_PX);}}>
        {log?.log || t("Waiting for installer output…")}
      </pre>
      <div className="install-log-meta">
        {error ? <p role="status" className="error-text">{t(running
          ? "Log connection interrupted. Retrying automatically; the last output is kept."
          : "Could not read the log. Close and reopen it to retry.")}</p>
          : checkedAt && <p>{t("Last checked")}: {new Date(checkedAt).toLocaleTimeString()}</p>}
        {secondsQuiet !== null && <p>{t("Time since last output")}: {secondsQuiet} {t("seconds")}</p>}
        {running && <p>{t("Downloads and dependency resolution can be quiet. No new log output does not confirm a stalled installation.")}</p>}
        {log?.truncated && <p>{t("Showing the latest output; the full log is retained locally.")}</p>}
      </div>
    </div>}
  </article>;
}
