"use client";
import { useState } from "react";
import { stopSimulation, type NodeInfo } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function StopSimulation({ node, sessionId, onChanged }: { node: NodeInfo; sessionId?: string; onChanged: () => void }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  if (!node.local_simulation) return null;
  async function stop() {
    setBusy(true);
    setError("");
    try {
      await stopSimulation(node, sessionId);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return <div className="mt-2 text-xs">
    <button disabled={busy} onClick={() => setConfirming(true)}
      className="nerv-control nerv-warning rounded border border-amber-800 px-2 py-1 text-amber-300 hover:bg-amber-950">
      {busy ? t("Stopping simulation…") : t("Stop simulation / change world")}
    </button>
    {confirming && <div role="group" aria-label={t("End instance confirmation")} className="mt-2 space-y-2 rounded border border-amber-800 p-2"><p>{t("Stop this body and its simulated world? Session history will be kept. Create a new session to choose another world.")}</p><button disabled={busy} onClick={stop} className="nerv-control mr-2 rounded nerv-warning bg-amber-800 px-2 py-1">{t("End instance")}</button><button disabled={busy} onClick={() => setConfirming(false)} className="nerv-control">{t("Cancel")}</button></div>}
    {error && <p role="alert" className="mt-1 text-[var(--control-danger-text)]">{error}</p>}
  </div>;
}
