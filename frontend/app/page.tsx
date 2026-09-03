"use client";
import { useCallback, useEffect, useState } from "react";

import ChatPanel from "@/components/ChatPanel";
import NervDashboard from "@/components/NervDashboard";
import SensingArea from "@/components/SensingArea";
import SessionLogsView from "@/components/SessionLogsView";
import SessionSidebar from "@/components/SessionSidebar";
import { useI18n } from "@/lib/i18n";
import { getNodes, getRegistry, listSessions, POLL_NODES_MS, type NodeInfo, type Registry, type SessionSummary } from "@/lib/api";

export default function Home() {
  const { t } = useI18n();
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentId, setCurrentId] = useState("");
  // 中间区当前看什么：会话视图（默认）/ 留白主页 / 内嵌 NERV 仪表盘 / 内嵌 Session Logs
  const [view, setView] = useState<"session" | "home" | "nerv" | "logs">("session");

  const refreshSessions = useCallback(async () => {
    const s = await listSessions().catch(() => []);
    setSessions(s);
    return s;
  }, []);
  const refreshNodes = useCallback(() => getNodes().then(setNodes).catch(() => {}), []);

  useEffect(() => {
    (async () => {
      setRegistry(await getRegistry().catch(() => null));
      await refreshNodes();
      const s = await refreshSessions();
      if (s.length) setCurrentId((id) => id || s[0].id);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 周期性刷新节点在线状态：节点中途挂了也能反映在感知区和侧栏上。
  useEffect(() => {
    const id = setInterval(refreshNodes, POLL_NODES_MS);
    return () => clearInterval(id);
  }, [refreshNodes]);

  const current = sessions.find((x) => x.id === currentId) || null;
  const bodyNode = current?.body ? nodes.find((n) => n.key === `body:${current.body}`) ?? null : null;
  const worldNode = current?.world && current.body ? nodes.find((n) => n.key === `world:${current.world}/${current.body}`) ?? null : null;
  const inSession = view === "session";

  return (
    <main className="grid h-screen grid-cols-[240px_minmax(0,1fr)_440px] bg-neutral-950 text-neutral-100">
      <SessionSidebar
        sessions={sessions}
        registry={registry}
        currentId={currentId}
        onSelect={(id) => {
          setCurrentId(id);
          setView("session");
        }}
        onChanged={async (id) => {
          const s = await refreshSessions();
          await refreshNodes(); // 新会话可能刚起了节点
          if (id) setCurrentId(id);
          else setCurrentId((cur) => (s.find((x) => x.id === cur) ? cur : s[0]?.id ?? ""));
        }}
        onHome={() => setView("home")}
        onOpenPanel={(p) => setView(p)}
      />

      {view === "nerv" ? (
        <NervDashboard embedded onOpenLogs={() => setView("logs")} />
      ) : view === "logs" ? (
        <SessionLogsView embedded sessionId={currentId} />
      ) : view === "home" ? (
        <div className="flex min-w-0 items-center justify-center overflow-hidden bg-neutral-950 p-8 text-center text-sm text-neutral-600">
          {t("NERV · pick a session on the left or start a new one. The NERV dashboard and Session Logs are at the bottom left.")}
        </div>
      ) : (
        <SensingArea session={current} bodyNode={bodyNode} worldNode={worldNode} />
      )}

      <ChatPanel session={inSession ? current : null} brains={registry?.brains ?? []} onSessionsChanged={refreshSessions} paused={!inSession} />
    </main>
  );
}
