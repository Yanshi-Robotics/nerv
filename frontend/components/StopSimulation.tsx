"use client";
import { useState } from "react";
import { stopSimulation, type NodeInfo } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function StopSimulation({ node, sessionId, onChanged }: { node: NodeInfo; sessionId?: string; onChanged: () => void }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!node.local_simulation) return null;
  async function stop() {
    if (!window.confirm(t("Stop this body and its simulated world? Session history will be kept. Create a new session to choose another world."))) return;
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
    <button disabled={busy} onClick={stop}
      className="rounded border border-amber-800 px-2 py-1 text-amber-300 hover:bg-amber-950 disabled:opacity-50">
      {busy ? t("Stopping simulation…") : t("Stop simulation / change world")}
    </button>
    {error && <p role="alert" className="mt-1 text-red-400">{error}</p>}
  </div>;
}
