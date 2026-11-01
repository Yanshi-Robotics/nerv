"use client";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { armSession, runtimeOperation, type NodeInfo, type SessionSummary } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { projectRequest, type AgentConnection } from "@/lib/projects";
import NavigationIcon from "./NavigationIcon";
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
export default function OperatorToolbox({session, bodyNode, nodes, onChanged, children, chat, onConsole, projectId}: {
  session: SessionSummary | null; bodyNode: NodeInfo | null; nodes: NodeInfo[];
  onChanged: () => void; children: ReactNode; chat: ReactNode; onConsole?: () => void; projectId?: string;
}) {
  const {t} = useI18n();
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState<string>(GROUPS[0]);
  const [observation, setObservation] = useState<HTMLDivElement | null>(null);
  const [task, setTask] = useState<HTMLDivElement | null>(null);
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [stopMessage, setStopMessage] = useState("");
  const [clients,setClients] = useState<AgentConnection[]>([]);
  const [owner,setOwner] = useState("");
  const [manualReady,setManualReady] = useState(false);
  useEffect(()=>{setManualReady(false);setOwner("");},[projectId]);
  useEffect(()=>{if(!projectId)return;projectRequest<AgentConnection[]>("agent-connections").then(items=>setClients(items.filter(item=>!item.revoked&&item.projects.includes(projectId)&&item.permissions.includes("control")))).catch(e=>setError(e.message));},[projectId,open]);
  async function changeControl(action:string) {
    if (!projectId) return;
    const result=await projectRequest(`projects/${projectId}/control`,"POST",{action,owner_id:owner||undefined});
    if(result.ok!==false){const manual=action==="takeover";setManualReady(manual);window.dispatchEvent(new CustomEvent("nerv-manual-control",{detail:{sessionId:session?.id,manual}}));}
    return result;
  }
  const active = session?.status === "active";
  const robot = active && !!session?.body;
  const sim = robot && session?.mode === "sim" && !!session.world;
  // Supervision is owned by GlobalTaskBar across all application pages.
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
    <div className="world-workbench">
      <nav className="world-toolrail" aria-label={t("World tools")}>
        {([
          ["Observation and task settings", "panel", "Views"],
          ["Execution", "robot", "Execution structure"],
          ["Robot controls", "control", "Manual controls"],
          ["Activity", "sessions", "Activity history"],
          ["Device and access", "settings", "Device and access"],
          ...(sim ? [["Simulation world", "world", "Simulation world"], ["Scene test", "check", "Scene test"]] : []),
        ] as const).map(([key, icon, label]) => <button key={key} className="nerv-control icon-button" title={t(label)} aria-label={t(label)} aria-pressed={open && group === key} onClick={() => {
          if (key === "Activity") {window.dispatchEvent(new CustomEvent("nerv-show-activity")); return;}
          setGroup(key);setOpen(!(open && group === key));
        }}><NavigationIcon name={icon as import('./NavigationIcon').NavigationIconName}/></button>)}
        <button className="nerv-control icon-button world-console-button" onClick={onConsole} title={t("Open console")} aria-label={t("Open console")}><NavigationIcon name="console"/></button>
      </nav>
      <div className="world-stage">{children}</div>
      {error && <p role="alert" className="project-error error-text">{error}</p>}
      {stopMessage && <p role="status" className="project-error">{stopMessage}</p>}
      <section id="operator-toolbox" aria-label={t("Toolbox")} hidden={!open} className="world-tool-popover">
        <header className="project-section-heading"><h2>{t(group)}</h2><button className="nerv-control icon-button" onClick={()=>setOpen(false)} aria-label={t("Close")}><NavigationIcon name="close"/></button></header>
            <div className="min-h-0 flex-1 overflow-auto p-3">
              {open && group === "Device and access" && <div className="space-y-3">
                <p className="text-xs text-neutral-400">{t("Access permission exposes plugin capabilities. It does not prepare devices or permit motion.")}</p>
                {nodes.filter(node => node.key === bodyNode?.key || (node.kind === "tool" && session?.agent_tools?.includes(node.key.replace(/^tool:/, "")))).map(node => <NodeTrust key={node.key} node={node} onChanged={onChanged} />)}
                {robot && <RuntimeControls session={session!} online={!!bodyNode?.online} onChanged={onChanged} />}
              </div>}
              {open && group === "Execution" && chat}
              {open && group === "Robot controls" && robot && <div className="space-y-3">
                {projectId && <div className="project-takeover"><label className="form-label">{t("Controlling Agent")}<select className="nerv-input" value={owner} onChange={e=>setOwner(e.target.value)}><option value="">{t("Choose an Agent")}</option>{clients.map(client=><option key={client.id} value={client.id}>{client.name}</option>)}</select></label><p>{t("Take control before using manual actions. The current Agent task will be stopped.")}</p><button className="nerv-control action" onClick={()=>perform("takeover",()=>changeControl("takeover"))}>{t("Take manual control")}</button><button disabled={!owner} className="nerv-control action" onClick={()=>perform("release",()=>changeControl("release"))}>{t("Return control to Agent")}</button><button className="nerv-control action" onClick={()=>perform("pause",()=>changeControl("pause"))}>{t("Pause Agent requests")}</button><button disabled={!owner} className="nerv-control action" onClick={()=>perform("resume",()=>changeControl("resume"))}>{t("Accept Agent requests")}</button></div>}
                <button role="switch" aria-checked={session!.armed} disabled={pending.arm} onClick={arm} className={`nerv-control rounded border px-3 py-2 text-xs ${session!.armed ? "nerv-danger" : ""}`}>{t(session!.armed ? "Disarm robot" : "Authorize robot motion")}</button>
                <TeleopPanel sessionId={session!.id} armed={session!.armed && (!projectId || manualReady)} hasWorld={false} bodyTrust={bodyNode?.online ? bodyNode.trust?.state ?? null : "offline"} />
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
