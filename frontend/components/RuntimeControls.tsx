"use client";
import { useEffect, useState } from "react";
import { releaseSession, runtimeOperation, runtimeStatus, POLL_NODES_MS, type SessionSummary } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function RuntimeControls({session, online, onChanged}: {session: SessionSummary; online: boolean; onChanged?: () => void}) {
  const {t} = useI18n();
  const [status, setStatus] = useState<Record<string, unknown> | null>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState<Record<string, unknown> | null>(null);
  const load = () => !online ? Promise.resolve(setStatus(null)) : runtimeStatus(session.id).then(value => setStatus(value && Object.keys(value).length ? value : null)).catch(() => setStatus(null));
  useEffect(() => { load(); const timer = setInterval(load, POLL_NODES_MS); return () => clearInterval(timer); }, [session.id, online]);
  async function perform(action: () => Promise<Record<string, unknown>>) {
    setBusy(true); setError("");
    try { const result = await action(); setReceipt(result); if (result.ok === false) throw new Error(String(result.message)); await load(); onChanged?.(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const policies = (status?.policies || []) as {id: string; loaded: boolean}[];
  const execution = status?.execution as {running?: boolean; state?: string} | undefined;
  const button = "rounded-md border border-neutral-600 px-3 py-1.5 text-xs";
  return <div className="border-b border-neutral-800 bg-neutral-900 px-3 py-2">
    <div className="flex flex-wrap items-center gap-2">
      <span className="mr-auto text-xs text-neutral-400">{status === undefined ? t("Loading…") : status === null ? t("body node offline") : status.prepared ? t("Device prepared") : t("Device not prepared")}{execution?.state ? ` · ${execution.state}` : ""}</span>
      {!status?.prepared && <button className={`nerv-control ${button}`} disabled={busy || !status} onClick={() => {
        if (session.mode === "real" && !confirm(t("Preparing connects the device and may enable motor torque. Confirm the work area and hardware are ready."))) return;
        perform(() => runtimeOperation(session.id, "prepare"));
      }}>{t("Prepare device")}</button>}
      {policies.map(policy => <button key={policy.id} className={`nerv-control ${button}`} disabled={busy || !!execution?.running || policy.loaded} onClick={() => perform(() => runtimeOperation(session.id, "policies/load", {id:policy.id}))}>{policy.loaded ? t("Loaded") : t("Load policy")}: {policy.id}</button>)}
      {!!status?.prepared && <>
        <button className={`nerv-control ${button}`} disabled={busy || session.armed || !!execution?.running} onClick={() => perform(() => runtimeOperation(session.id, "policies/check"))}>{t("Check policy without motion")}</button>
        <button className={`nerv-control ${button}`} disabled={busy} onClick={() => perform(() => runtimeOperation(session.id, "device/close"))}>{t("Disconnect, keep torque")}</button>
        <button className={`nerv-control ${button}`} disabled={busy} onClick={() => { if (confirm(t("Support the arm before releasing torque. Disconnect with torque disabled?"))) perform(() => runtimeOperation(session.id, "device/torque-off")); }}>{t("Release torque and disconnect")}</button>
      </>}
      {!!status?.held && <button className={`nerv-control ${button}`} disabled={busy || !status} onClick={() => perform(() => releaseSession(session.id))}>{t("Release to standby")}</button>}
    </div>
    {receipt && <details className="mt-2 text-xs"><summary>{t("Operation result")}</summary><pre className="overflow-auto">{JSON.stringify(receipt, null, 2)}</pre></details>}
    {!!status?.last_action && <details className="mt-2 text-xs"><summary>{t("Requested, sent and measured positions")}</summary><pre className="overflow-auto">{JSON.stringify(status.last_action, null, 2)}</pre></details>}
    {error && <p className="mt-2 text-xs text-[var(--control-danger-text)]" role="alert">{error}</p>}
  </div>;
}
