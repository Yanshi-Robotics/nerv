"use client";
import { useEffect, useState } from "react";
import { estopSession, releaseSession, runtimeOperation, runtimeStatus, POLL_NODES_MS, type SessionSummary } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function RuntimeControls({session, onChanged}: {session: SessionSummary; onChanged?: () => void}) {
  const {t} = useI18n();
  const [status, setStatus] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = () => runtimeStatus(session.id).then(setStatus).catch(() => {});
  useEffect(() => { load(); const timer = setInterval(load, POLL_NODES_MS); return () => clearInterval(timer); }, [session.id]);
  async function perform(action: () => Promise<Record<string, unknown>>) {
    setBusy(true); setError("");
    try { const result = await action(); if (result.ok === false) throw new Error(String(result.message)); await load(); onChanged?.(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const policies = (status.policies || []) as {id: string; loaded: boolean}[];
  const execution = status.execution as {running?: boolean; state?: string} | undefined;
  const button = "rounded-md border border-neutral-600 px-3 py-1.5 text-xs disabled:opacity-40";
  return <div className="border-b border-neutral-800 bg-neutral-900 px-3 py-2">
    <div className="flex flex-wrap items-center gap-2">
      <span className="mr-auto text-xs text-neutral-400">{status.prepared ? t("Device prepared") : t("Device not prepared")}{execution?.state ? ` · ${execution.state}` : ""}</span>
      {!status.prepared && <button className={button} disabled={busy} onClick={() => {
        if (session.mode === "real" && !confirm(t("Preparing connects the device and may enable motor torque. Confirm the work area and hardware are ready."))) return;
        perform(() => runtimeOperation(session.id, "prepare"));
      }}>{t("Prepare device")}</button>}
      {policies.map(policy => <button key={policy.id} className={button} disabled={busy || !!execution?.running || policy.loaded} onClick={() => perform(() => runtimeOperation(session.id, "policies/load", {id:policy.id}))}>{policy.loaded ? t("Loaded") : t("Load policy")}: {policy.id}</button>)}
      <button className="rounded-md bg-red-700 px-3 py-1.5 text-xs font-semibold text-white" onClick={() => perform(() => estopSession(session.id))}>{t("Emergency stop")}</button>
      {!!status.held && <button className={button} disabled={busy} onClick={() => perform(() => releaseSession(session.id))}>{t("Release to standby")}</button>}
    </div>
    {error && <p className="mt-2 text-xs text-red-400" role="alert">{error}</p>}
  </div>;
}
