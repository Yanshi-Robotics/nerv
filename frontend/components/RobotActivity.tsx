"use client";
import { useEffect, useState } from "react";
import { robotEventUrl, type RobotEvent, type RobotSnapshot, type RobotTask, type SessionSummary } from "@/lib/api";
import { ToolboxSlot } from "./OperatorToolbox";
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
  const lastFault = [...events].reverse().find(e => e.ok === false || e.status === "failed");
  const visible = history ? events : events.slice(-RECENT_EVENTS);
  return <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-neutral-700 bg-neutral-950">
    <ToolboxSlot name="task"><div className="z-10 flex shrink-0 flex-wrap items-center gap-2 border-b border-neutral-700 bg-neutral-900 px-3 py-2 text-xs">
      <div className="min-w-0 flex-1"><div className="font-medium text-neutral-100">{t("Robot workspace")} · {t(task?.status || "Standby")}</div><p className="truncate text-[11px] text-neutral-400" title={task?.goal}>{task?.goal || t("No active robot task")}</p></div>
      <p className="text-neutral-400">{t("Choose the decision model in Control schemes")}</p>
    </div>
    </ToolboxSlot>
    <div className="relative flex min-h-0 flex-1 flex-col">{children}
      <aside className="pointer-events-none absolute right-2 top-2 z-10 flex max-h-[75%] w-[min(20rem,75%)] flex-col items-end gap-2" aria-label={t("Robot activity")}>
        <button onClick={() => setCollapsed(!collapsed)} aria-expanded={!collapsed} className="nerv-control pointer-events-auto rounded-full border border-neutral-600 bg-neutral-950/90 px-3 py-1 text-[11px] text-neutral-300">{t(collapsed ? "Show robot activity" : "Collapse robot activity")}</button>
        {!collapsed && <div className="flex max-h-full w-full flex-col gap-2 overflow-y-auto">
          <div className="self-end rounded bg-neutral-950/90 px-2 text-[10px] text-neutral-400">{t(session.status !== "active" ? "Historical session" : connected ? "Task event stream connected" : "Task event stream disconnected")}{updated && ` · ${new Date(updated).toLocaleTimeString()}`}</div>
          {lastFault && !visible.includes(lastFault) && <p role="alert" className="rounded-xl border border-red-700 bg-neutral-950/95 p-3 text-xs text-[var(--control-danger-text)]">{t(lastFault.text || lastFault.message || "")}</p>}
          {visible.map((event, i) => <article key={event.seq} className={`nerv-activity-bubble rounded-2xl rounded-tr-sm border bg-neutral-950/95 p-3 shadow-lg ${event.ok === false || event.status === "failed" ? "border-red-700 text-[var(--control-danger-text)]" : i === visible.length-1 ? "border-[var(--control-focus)] text-neutral-100" : "border-neutral-700 text-neutral-400"}`}>
            <div className="mb-1 text-[10px] text-neutral-400">{event.source === "system2" ? t("Decision layer") : event.source === "system1" ? t("Execution layer") : t("Host")} · {new Date(event.time*1000).toLocaleTimeString()}</div>
            <details className="pointer-events-auto text-xs leading-relaxed"><summary className="line-clamp-3 cursor-pointer">{t(event.text || event.message || "")}</summary><p className="mt-2 whitespace-pre-wrap">{t(event.text || event.message || "")}</p></details>
          </article>)}
          {events.length > RECENT_EVENTS && <button onClick={() => setHistory(!history)} className="nerv-control pointer-events-auto self-end rounded bg-neutral-950/90 px-2 py-1 text-[11px] text-neutral-300">{t(history ? "Recent activity" : "Activity history")}</button>}
        </div>}
      </aside>
    </div>
  </div>;
}
