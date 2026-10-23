"use client";
import {useCallback, useEffect, useState} from "react";
import {getRobotTask, listControlSchemes, saveControlScheme, selectControlScheme, robotTaskOperation, POLL_NODES_MS,
  type NodeInfo, type Brain, type ControlScheme, type RobotTask, type SchemeOptions, type SessionSummary} from "@/lib/api";
import {NodeTrust} from "./NodeTrust";
import {useI18n} from "@/lib/i18n";

const labels = {fixed_policy: "Fixed policy", policy_group: "Policy group", visual_direct: "Visual direct control"};
const inputClass = "w-full rounded-lg border border-neutral-600 bg-neutral-900 p-2 text-sm";

export default function ControlSchemes({session, bodyNode, brains, compact = false, onChanged, onSetup, onModels}: {
  session: SessionSummary | null; bodyNode?: NodeInfo | null; brains: Brain[]; compact?: boolean; onChanged: () => void; onSetup?: () => void; onModels?: () => void;
}) {
  const {t} = useI18n();
  const [options, setOptions] = useState<SchemeOptions>({schemes: [], actions: []});
  const [task, setTask] = useState<RobotTask | null>(null);
  const [open, setOpen] = useState(!compact);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Partial<ControlScheme>>({template: "fixed_policy", actions: [], name: "", max_runtime_s: 120, action_budget: 20});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    setOptions(await listControlSchemes(session?.id));
    if (session?.id) setTask((await getRobotTask(session.id)).task);
  }, [session?.id]);
  useEffect(() => {
    refresh().catch(e => setError(e.message));
    const timer = setInterval(() => refresh().catch(e => setError(e.message)), POLL_NODES_MS);
    return () => clearInterval(timer);
  }, [refresh]);
  const selected = options.selected;
  const latest = options.schemes.find(s => s.id === session?.control_scheme);
  const running = !!task && ["starting", "running", "updating", "stopping"].includes(task.status);
  const candidates = options.schemes.filter(s => !session?.body || s.robot === session.body && s.mode === session.mode);
  const modelLabel = (id: string | null | undefined) => brains.find(b => b.name === id)?.label || id || t("Decision layer not enabled");
  async function perform(action: () => Promise<unknown>) {
    setBusy(true); setError("");
    try {await action(); await refresh(); onChanged();} catch(e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  async function save() {
    if (!session?.body) return;
    const fixed = draft.template === "fixed_policy";
    const result = await saveControlScheme({...draft, robot: session.body, mode: session.mode,
      decision_model: fixed ? null : draft.decision_model, effort: fixed ? null : draft.effort || null});
    if (!running) await selectControlScheme(session.id, result);
    setEditing(false);
  }
  const graph = editing ? draft : selected;
  return <section className={compact ? "shrink-0 border-b border-neutral-700 p-3 space-y-2" : "col-span-2 overflow-auto p-6 space-y-5"} aria-label={t("Control schemes")}>
    <div className="flex items-center gap-2"><h2 className={compact ? "text-xs font-medium" : "text-xl"}>{t("Control schemes")}</h2>
      {compact && <button className="nerv-control ml-auto text-xs underline" onClick={() => setOpen(!open)} aria-expanded={open}>{t(open ? "Close details" : "View structure")}</button>}
    </div>
    {session?.body ? <>
      {bodyNode && bodyNode.trust?.state !== "trusted" && <NodeTrust node={bodyNode} onChanged={() => {refresh(); onChanged();}} />}
      <select aria-label={t("Control scheme")} className={inputClass} disabled={busy || running || session.status !== "active"} value={selected?.id || ""}
        onChange={e => {const scheme = candidates.find(s => s.id === e.target.value); if (scheme) perform(() => selectControlScheme(session.id, scheme));}}>
        <option value="">{t("Choose a control scheme")}</option>
        {candidates.map(s => <option key={s.id} value={s.id}>{s.name} · {t(labels[s.template])}{s.ready ? "" : ` · ${t("Needs setup")}`}</option>)}
      </select>
      {selected && <p className="text-xs text-neutral-400">{modelLabel(selected.decision_model)} · {selected.actions.join(", ")}</p>}
      {selected && !selected.ready && <div role="status" className="text-xs text-amber-300"><p>{selected.reason || t("Connect and check the device first")}</p><div className="mt-2 flex gap-3">{onSetup && <button className="nerv-control underline" onClick={onSetup}>{t("Device and policy setup")}</button>}{selected.decision_model && onModels && <button className="nerv-control underline" onClick={onModels}>{t("Visual models")}</button>}</div></div>}
      {latest && latest.revision !== session.control_scheme_revision && <button disabled={running || busy} className="nerv-control text-xs underline" onClick={() => perform(() => selectControlScheme(session.id, latest))}>{t("Use latest saved revision")}</button>}
      {selected?.template === "fixed_policy" && <button className="nerv-control nerv-primary rounded-lg px-3 py-2 text-sm" disabled={busy || running || !selected.ready || !session.armed} onClick={() => perform(() => robotTaskOperation(session.id, "start", {goal: selected.name}))}>{t("Run selected policy")}</button>}
      {selected && !session.armed && <p className="text-xs text-neutral-400">{t("Authorize motion in Toolbox before running")}</p>}
    </> : <p className="text-sm text-neutral-400">{t("Open a robot workspace to create a compatible scheme")}</p>}
    {task && <div role="status" className="rounded-lg border border-neutral-700 p-2 text-xs space-y-1"><p>{task.goal}</p><p>{t(task.status)} · {task.message}</p>{running && session && <button className="nerv-control nerv-warning rounded border px-2 py-1" onClick={() => perform(() => robotTaskOperation(session.id, "cancel"))}>{t("Stop task")}</button>}</div>}
    {open && <div className={compact ? "max-h-64 overflow-auto space-y-3" : "space-y-3"}>
      {!editing && <div className="flex gap-2"><button className="nerv-control rounded border px-3 py-1 text-xs" disabled={!session?.body || busy} onClick={() => {setDraft({template: "fixed_policy", actions: [], name: "", max_runtime_s: 120, action_budget: 20}); setEditing(true);}}>{t("Create control scheme")}</button>
        {selected && <button className="nerv-control text-xs underline" onClick={() => {const {ready, reason, structure, ...value} = latest || selected; void ready; void reason; void structure; setDraft(value); setEditing(true);}}>{t("Edit next revision")}</button>}</div>}
      {editing && <form className="space-y-3" onSubmit={e => {e.preventDefault(); perform(save);}}>
        <label className="block text-xs">{t("Name")}<input required className={inputClass} value={draft.name || ""} onChange={e => setDraft({...draft, name: e.target.value})} /></label>
        <label className="block text-xs">{t("Control route")}<select className={inputClass} value={draft.template} onChange={e => setDraft({...draft, template: e.target.value as ControlScheme["template"], actions: []})}>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{t(label)}</option>)}</select></label>
        {draft.template !== "fixed_policy" && <label className="block text-xs">{t("Decision model")}<select required className={inputClass} value={draft.decision_model || ""} onChange={e => setDraft({...draft, decision_model: e.target.value})}><option value="">{t("Choose a model")}</option>{brains.filter(b => b.name !== "operator").map(b => <option key={b.name} value={b.name}>{b.label}{b.available ? "" : ` · ${t("Needs setup")}`}</option>)}</select></label>}
        <fieldset className="space-y-2"><legend className="text-xs mb-2">{t("Allowed execution skills")}</legend>
          {options.actions.filter(a => a.kind === (draft.template === "visual_direct" ? "primitive" : "skill")).map(a => <label key={a.name} className="flex gap-2 text-xs" title={a.description}><input type={draft.template === "fixed_policy" ? "radio" : "checkbox"} name="actions" checked={draft.actions?.includes(a.name) || false} onChange={e => setDraft({...draft, actions: draft.template === "fixed_policy" ? [a.name] : e.target.checked ? [...(draft.actions || []), a.name] : draft.actions?.filter(x => x !== a.name)})} />{a.name}</label>)}
          {!options.actions.some(a => a.kind === (draft.template === "visual_direct" ? "primitive" : "skill")) && <p className="text-xs text-amber-300">{t("Connect the runtime and import a compatible policy first")}</p>}
        </fieldset>
        <div className="grid grid-cols-2 gap-2"><label className="text-xs">{t("Time limit (seconds)")}<input className={inputClass} type="number" min="1" max="3600" required value={draft.max_runtime_s} onChange={e => setDraft({...draft, max_runtime_s: Number(e.target.value)})} /></label>
          {draft.template !== "fixed_policy" && <label className="text-xs">{t("Action budget")}<input className={inputClass} type="number" min="1" max="1000" required value={draft.action_budget} onChange={e => setDraft({...draft, action_budget: Number(e.target.value)})} /></label>}</div>
        <div className="flex gap-3"><button disabled={busy || !draft.actions?.length} className="nerv-control nerv-primary rounded px-3 py-2 text-xs" type="submit">{t("Save scheme")}</button><button type="button" className="nerv-control text-xs underline" onClick={() => setEditing(false)}>{t("Cancel")}</button></div>
      </form>}
      {graph && <figure className="space-y-1 rounded-lg bg-neutral-950 p-3 text-center text-xs" aria-label={t("Control scheme structure")}>
        <div className="rounded border border-neutral-700 p-2">{t("Interaction layer")}<br />{t("NERV Agent")}{graph.template === "fixed_policy" ? ` / ${t("Run selected policy")}` : ""}</div><div aria-hidden>↓</div>
        {graph.template !== "fixed_policy" && <><div className="rounded border border-neutral-700 p-2">{t("Decision layer")} · System 2<br />{modelLabel(graph.decision_model)}</div><div aria-hidden>↓</div></>}
        {graph.template === "fixed_policy" && <p className="text-neutral-400">{t("Decision layer not enabled")}</p>}
        <div className="rounded border border-[var(--control-focus)] p-2">{t("Execution layer")} · System 1<br />{graph.actions?.join(" / ") || t("Choose execution skills")}</div><div aria-hidden>↓</div>
        <div className="rounded border border-neutral-700 p-2">{t("Runtime and device")} · {session?.mode === "real" ? t("Real hardware") : t("Simulation")}</div>
        <figcaption className="pt-2 text-neutral-400">{t("Observations and execution feedback return to the workspace")}</figcaption>
      </figure>}
    </div>}
    {error && <p role="alert" className="text-xs text-[var(--control-danger-text)]">{error}</p>}
  </section>;
}
