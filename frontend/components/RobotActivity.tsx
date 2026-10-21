"use client";
import { useEffect, useState } from "react";
import { estopSession, getBrains, robotEventUrl, robotTaskOperation, setTaskModel, type Brain, type RobotEvent, type RobotSnapshot, type RobotTask, type SessionSummary } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

// The overlay is DOM-only. No screenshot/canvas is used by any sensor endpoint.
const RECENT_EVENTS = 3;
const HISTORY_LIMIT = 100;
export default function RobotActivity({session, onChanged, children}: {session: SessionSummary; onChanged?: () => void; children: React.ReactNode}) {
  const {t} = useI18n();
  const [task, setTask] = useState<RobotTask | null>(null);
  const [events, setEvents] = useState<RobotEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [updated, setUpdated] = useState<number>();
  const [collapsed, setCollapsed] = useState(false);
  const [history, setHistory] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [models, setModels] = useState<Brain[]>([]);
  useEffect(() => {getBrains().then(setModels).catch(() => {});}, []);
  useEffect(() => {
    setEvents([]); setTask(null); setConnected(false); setUpdated(undefined);
    let cursor = 0;
    const stream = new EventSource(robotEventUrl(session.id));
    stream.onmessage = event => {
      const data: RobotSnapshot = JSON.parse(event.data);
      if (data.cursor < cursor) return;
      cursor = data.cursor;
      setConnected(session.status === "active");
      const latest = data.events.filter(item => item.instance_id === session.epoch).at(-1);
      if (latest) setUpdated(latest.time * 1000);
      if (!data.task || data.task.instance_id === session.epoch) setTask(data.task);
      setEvents(previous => {
        const merged = new Map(previous.map(item => [item.seq, item]));
        for (const item of data.events) if (item.instance_id === session.epoch && (item.text || item.message)) merged.set(item.seq, item);
        return [...merged.values()].sort((a,b) => a.seq-b.seq).slice(-HISTORY_LIMIT);
      });
    };
    stream.onerror = () => setConnected(false);
    return () => stream.close();
  }, [session.id, session.epoch, session.status]);
  const running = !!task && ["starting", "running", "updating", "stopping"].includes(task.status);
  const chosen = session.task_model || session.brain;
  const model = models.find(m => m.name === chosen);
  const lastFault = [...events].reverse().find(e => e.ok === false || e.status === "failed");
  const visible = history ? events : events.slice(-RECENT_EVENTS);
  async function stop(emergency = false) {
    setPending(true); setError("");
    try { const result = emergency ? await estopSession(session.id) : await robotTaskOperation(session.id, "cancel"); if (result.ok === false) throw new Error(result.message); onChanged?.(); }
    catch(e) {setError((e as Error).message);} finally {setPending(false);}
  }
  async function configure(name: string, effort: string | null) {
    try { await setTaskModel(session.id, name, effort); onChanged?.(); } catch(e) {setError((e as Error).message);}
  }
  return <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-neutral-700 bg-neutral-950">
    <div className="z-10 flex shrink-0 flex-wrap items-center gap-2 border-b border-neutral-700 bg-neutral-900 px-3 py-2 text-xs">
      <div className="min-w-0 flex-1"><div className="font-medium text-neutral-100">{t("Robot workspace")} · {t(task?.status || "Standby")}</div><p className="truncate text-[11px] text-neutral-400" title={task?.goal}>{task?.goal || t("No active robot task")}</p></div>
      <button onClick={() => stop()} disabled={pending || session.status !== "active"} className="nerv-control nerv-warning rounded border border-amber-700 px-2 py-1.5 text-amber-200">{t(pending ? "Stopping…" : "Stop task")}</button>
      <button onClick={() => stop(true)} disabled={session.status !== "active"} className="nerv-control rounded nerv-danger bg-red-700 px-2 py-1.5 text-white">{t("Emergency stop")}</button>
      <details className="w-full text-neutral-400"><summary className="cursor-pointer">System 2 · {model?.label || chosen}</summary><div className="mt-2 flex gap-2">
        <select aria-label={t("Robot task model")} disabled={running || session.status !== "active"} value={chosen} onChange={e => configure(e.target.value, null)} className="min-w-0 flex-1 rounded bg-neutral-800 p-1">{models.map(m => <option key={m.name} value={m.name} disabled={!m.available}>{m.label}</option>)}</select>
        <select aria-label={t("Robot reasoning effort")} disabled={running || session.status !== "active"} value={session.task_effort ?? ""} onChange={e => configure(chosen, e.target.value || null)} className="rounded bg-neutral-800 p-1"><option value="">{t("Provider default")}</option>{model?.efforts?.map(e => <option key={e}>{e}</option>)}</select>
      </div></details>
      {error && <p role="alert" className="w-full text-[var(--control-danger-text)]">{error}</p>}
    </div>
    <div className="relative flex min-h-0 flex-1 flex-col">{children}
      <aside className="pointer-events-none absolute right-2 top-2 z-10 flex max-h-[75%] w-[min(20rem,75%)] flex-col items-end gap-2" aria-label={t("Robot activity")}>
        <button onClick={() => setCollapsed(!collapsed)} aria-expanded={!collapsed} className="nerv-control pointer-events-auto rounded-full border border-neutral-600 bg-neutral-950/90 px-3 py-1 text-[11px] text-neutral-300">{t(collapsed ? "Show robot activity" : "Collapse robot activity")}</button>
        {!collapsed && <div className="flex max-h-full w-full flex-col gap-2 overflow-y-auto">
          <div className="self-end rounded bg-neutral-950/90 px-2 text-[10px] text-neutral-400">{t(session.status !== "active" ? "Historical session" : connected ? "Connected" : "Disconnected")}{updated && ` · ${new Date(updated).toLocaleTimeString()}`}</div>
          {lastFault && !visible.includes(lastFault) && <p role="alert" className="rounded-xl border border-red-700 bg-neutral-950/95 p-3 text-xs text-[var(--control-danger-text)]">{lastFault.text || lastFault.message}</p>}
          {visible.map((event, i) => <article key={event.seq} className={`rounded-2xl rounded-tr-sm border bg-neutral-950/95 p-3 shadow-lg ${event.ok === false || event.status === "failed" ? "border-red-700 text-red-200" : i === visible.length-1 ? "border-blue-700 text-neutral-100" : "border-neutral-700 text-neutral-400"}`}>
            <div className="mb-1 text-[10px] text-neutral-400">{event.source === "system2" ? "System 2" : event.source === "system1" ? "System 1" : t("Host")} · {new Date(event.time*1000).toLocaleTimeString()}</div>
            <details className="pointer-events-auto text-xs leading-relaxed"><summary className="line-clamp-3 cursor-pointer">{event.text || event.message}</summary><p className="mt-2 whitespace-pre-wrap">{event.text || event.message}</p></details>
          </article>)}
          {events.length > RECENT_EVENTS && <button onClick={() => setHistory(!history)} className="nerv-control pointer-events-auto self-end rounded bg-neutral-950/90 px-2 py-1 text-[11px] text-neutral-300">{t(history ? "Recent activity" : "Activity history")}</button>}
        </div>}
      </aside>
    </div>
  </div>;
}
