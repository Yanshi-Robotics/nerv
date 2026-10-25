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

export default function InstallJobCard({job, label, root, onCancel, onPause, onRetry, retryDisabled}: {
  job: InstallJob; label: string; root?: string; onCancel: () => void;
  onPause: () => void; onRetry: () => void; retryDisabled: boolean;
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
    {(running || ["paused", "waiting"].includes(job.state)) && <>
      <progress className="install-progress" max={100} value={percent} aria-label={t("Installation progress")} />
      <p className="muted text-xs">{percent !== undefined
        ? `${t("Dependency downloads")}: ${percent}% · ${((job.downloaded || 0) / 1048576).toFixed(1)} / ${((job.download_total || 0) / 1048576).toFixed(1)} MiB`
        : t("Working on this stage; total progress is not available.")}</p>
      {job.phase === "download" && job.asset && <p className="text-xs break-all">{job.asset}</p>}
    </>}
    {job.files && <>
      <p className="muted text-xs">{t("Cached")}: {((job.cached_bytes || 0) / 1048576).toFixed(1)} MiB · {((running && job.phase === 'download' ? job.speed_bytes_s || 0 : 0) / 1048576).toFixed(2)} MiB/s{running && job.phase === 'download' && job.eta_seconds != null ? ` · ${t("Estimated seconds remaining")}: ${job.eta_seconds}` : ""}</p>
      <p className="muted text-xs">{t("Concurrent downloads")}: {job.concurrency} · {t("Bandwidth limit")}: {job.bandwidth_bytes_s ? `${job.bandwidth_bytes_s} B/s` : t("Unlimited")}</p>
      <details><summary className="nerv-control cursor-pointer text-sm">{t("Download files")}</summary><div className="max-h-64 overflow-auto space-y-3 py-3">{job.files.map(file => <div key={file.name}>
        <p className="text-xs break-all">{file.name} · {t(file.state)}</p>
        <progress className="install-progress" max={file.bytes} value={file.received} aria-label={file.name}/>
        <p className="muted text-xs">{(file.received / 1048576).toFixed(1)} / {(file.bytes / 1048576).toFixed(1)} MiB{file.attempt ? ` · ${t("Retry")}: ${file.attempt}` : ""}</p>
        {file.next_retry_at && <p className="text-xs">{t("Next retry")}: {new Date(file.next_retry_at * 1000).toLocaleTimeString()}</p>}
        {file.reason && <p className="text-xs">{file.reason}</p>}
      </div>)}</div></details>
    </>}
    <InstallationLocation path={job.installation_path} root={root} />
    {job.error_code && <p className="error-text text-sm">{t(job.error_code === "download_dns"
      ? "Download failed because the package server name could not be resolved. Check your network or proxy, then retry. Verified cached packages are kept."
      : "A dependency download failed. Check the log and your network, then retry. Verified cached packages are kept.")}</p>}
    {job.message && <p className="break-words text-sm">{job.message}</p>}
    <div className="flex flex-wrap gap-3">
      <button className="nerv-control underline text-xs" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        {t(expanded ? "Hide installation log" : "Show installation log")}
      </button>
      {running && <button className="nerv-control underline text-xs" onClick={onPause}>{t("Pause download")}</button>}
      {running ? <button className="nerv-control underline text-xs" onClick={onCancel}>{t("Cancel installation")}</button>
        : ["failed", "cancelled", "paused", "waiting"].includes(job.state) && <button className="nerv-control underline text-xs" disabled={retryDisabled} onClick={onRetry}>{t("Continue installation")}</button>}
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
