"use client";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { armSession, estopSession, robotTaskOperation, type NodeInfo, type SessionSummary } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import RuntimeControls from "./RuntimeControls";
import { NodeTrust } from "./NodeTrust";
import TeleopPanel from "./TeleopPanel";
import WorldControlPanel from "./WorldControlPanel";
import StopSimulation from "./StopSimulation";
import SceneTestPanel from "./SceneTestPanel";

type Slot = "observation" | "task";
type ToolboxContext = { open: () => void; targets: Record<Slot, HTMLDivElement | null> };
const Context = createContext<ToolboxContext>({open: () => {}, targets: {observation: null, task: null}});
export const useToolbox = () => useContext(Context);
export function ToolboxSlot({name, children}: {name: Slot; children: ReactNode}) {
  const target = useToolbox().targets[name];
  return target ? createPortal(children, target) : null;
}

const GROUPS = ["Device and access", "Robot controls", "Simulation world", "Observation and task settings", "Scene test"] as const;
export default function OperatorToolbox({session, bodyNode, nodes, onChanged, children}: {
  session: SessionSummary | null; bodyNode: NodeInfo | null; nodes: NodeInfo[];
  onChanged: () => void; children: ReactNode;
}) {
  const {t} = useI18n();
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState<string>(GROUPS[0]);
  const [observation, setObservation] = useState<HTMLDivElement | null>(null);
  const [task, setTask] = useState<HTMLDivElement | null>(null);
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [stopMessage, setStopMessage] = useState("");
  const active = session?.status === "active";
  const robot = active && !!session?.body;
  const sim = robot && session?.mode === "sim" && !!session.world;
  useEffect(() => {setOpen(false); setError(""); setStopMessage(""); setGroup(GROUPS[0]);}, [session?.id]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {if (event.key === "Escape") setOpen(false);};
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  async function perform(kind: string, action: () => Promise<{ok?: boolean; message?: string}>) {
    setPending(previous => ({...previous, [kind]: true})); setError("");
    try {
      const result = await action();
      if (result.ok === false) throw new Error(result.message || t("Operation failed"));
      if (kind === "stop") setStopMessage(result.message || t("Stop confirmed"));
      onChanged();
    } catch(e) {setError((e as Error).message);} finally {setPending(previous => ({...previous, [kind]: false}));}
  }
  function arm() {
    if (!session) return;
    if (!session.armed && session.mode === "real" && !confirm(t("Arming permits real hardware motion. Confirm the work area is ready."))) return;
    perform("arm", () => armSession(session.id, !session.armed));
  }
  return <Context.Provider value={{open: () => setOpen(true), targets: {observation, task}}}>
    <div className="nerv-workspace col-span-2 flex min-h-0 min-w-0 flex-col overflow-hidden">
      <header className="nerv-operator-bar z-30 flex shrink-0 flex-wrap items-center gap-2 border-b border-neutral-800 bg-neutral-900 px-3 py-2 text-xs">
        <span className="mr-auto truncate text-neutral-400">{session?.body || t("Conversation only")} {session?.world ? `· ${session.world}` : ""}</span>
        {stopMessage && <span role="status">{t(stopMessage)}</span>}
        {robot && <><button className="nerv-control nerv-warning rounded border px-3 py-1.5" disabled={pending.stop} onClick={() => perform("stop", () => robotTaskOperation(session!.id, "cancel"))}>{t(pending.stop ? "Stopping…" : "Stop task")}</button>
        <button className="nerv-control nerv-danger rounded border px-3 py-1.5 font-semibold" onClick={() => perform("emergency", () => estopSession(session!.id))}>{t("Emergency stop")}</button></>}
        <button className="nerv-control rounded border px-3 py-1.5" aria-expanded={open} aria-controls="operator-toolbox" onClick={() => setOpen(!open)}>{t("Toolbox")}</button>
        {error && <p role="alert" className="w-full text-[var(--control-danger-text)]">{t(error)}</p>}
      </header>
      <div className="nerv-workspace-panes grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_440px]">{children}</div>
      <section id="operator-toolbox" aria-label={t("Toolbox")} hidden={!open} className="nerv-toolbox shrink-0 border-t border-neutral-700 bg-neutral-900">
        <div className="flex items-start gap-2 border-b border-neutral-800 p-2">
          <nav className="flex flex-1 flex-wrap gap-2" aria-label={t("Manual controls")}>
            {GROUPS.filter(name => !["Simulation world", "Scene test"].includes(name) || sim).map(name => <button key={name} aria-pressed={group === name} onClick={() => setGroup(name)} className="nerv-control rounded border px-3 py-1 text-xs">{t(name)}</button>)}
          </nav>
          <button className="nerv-control rounded border px-3 py-1 text-xs" onClick={() => setOpen(false)}>{t("Close toolbox")}</button>
        </div>
        <div className="max-h-[32dvh] overflow-auto p-3">
          {open && group === "Device and access" && <div className="space-y-3">
            <p className="text-xs text-neutral-400">{t("Access permission exposes plugin capabilities. It does not prepare devices or permit motion.")}</p>
            {nodes.filter(node => node.key === bodyNode?.key || (node.kind === "tool" && session?.agent_tools?.includes(node.key.replace(/^tool:/, "")))).map(node => <NodeTrust key={node.key} node={node} onChanged={onChanged} />)}
            {robot && <RuntimeControls session={session!} online={!!bodyNode?.online} onChanged={onChanged} />}
          </div>}
          {open && group === "Robot controls" && robot && <div className="space-y-3">
            <button role="switch" aria-checked={session!.armed} disabled={pending.arm} onClick={arm} className={`nerv-control rounded border px-3 py-2 text-xs ${session!.armed ? "nerv-danger" : ""}`}>{t(session!.armed ? "Disarm robot" : "Authorize robot motion")}</button>
            <TeleopPanel sessionId={session!.id} armed={session!.armed} hasWorld={false} bodyTrust={bodyNode?.online ? bodyNode.trust?.state ?? null : "offline"} />
          </div>}
          {open && group === "Simulation world" && sim && <><WorldControlPanel session={session!} onChanged={onChanged} />{bodyNode && <StopSimulation node={bodyNode} sessionId={session!.id} onChanged={onChanged} />}</>}
          <div hidden={group !== "Observation and task settings"} className="space-y-3"><div ref={setTask} /><div ref={setObservation} /></div>
          {open && group === "Scene test" && sim && <SceneTestPanel sessionId={session!.id} onClose={() => setOpen(false)} onStateChanged={onChanged} />}
          {!robot && group !== "Observation and task settings" && <p className="text-xs text-neutral-400">{t("No active robot instance")}</p>}
        </div>
      </section>
    </div>
  </Context.Provider>;
}
