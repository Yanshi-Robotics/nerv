"use client";
import { useCallback, useEffect, useState } from "react";

import ChatPanel from "@/components/ChatPanel";
import SensingArea from "@/components/SensingArea";
import SessionSidebar from "@/components/SessionSidebar";
import { useI18n } from "@/lib/i18n";
import { getNodes, getRegistry, listSessions, POLL_NODES_MS, type NodeInfo, type Registry, type SessionSummary } from "@/lib/api";

// 左侧栏折叠状态的持久化键；值 "1" = 收起
const SIDEBAR_KEY = "nerv-sidebar";

export default function Home() {
  const { t } = useI18n();
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentId, setCurrentId] = useState("");
  const [collapsed, setCollapsed] = useState(false);

  // 预渲染阶段拿不到 localStorage，挂载后再对齐一次；此后每次切换都存回去。
  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(SIDEBAR_KEY) === "1");
    } catch {
      /* 隐私模式等读不了就按展开 */
    }
  }, []);
  const toggleCollapsed = useCallback(() => {
    setCollapsed((v) => {
      const next = !v;
      try {
        localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0");
      } catch {
        /* 存不了也照样切 */
      }
      return next;
    });
  }, []);

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

  return (
    <main
      className={`grid h-screen bg-neutral-950 text-neutral-100 transition-[grid-template-columns] duration-200 ease-out ${
        collapsed ? "grid-cols-[56px_minmax(0,1fr)_440px]" : "grid-cols-[240px_minmax(0,1fr)_440px]"
      }`}
    >
      <SessionSidebar
        sessions={sessions}
        registry={registry}
        currentId={currentId}
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        onSelect={setCurrentId}
        onChanged={async (id) => {
          const s = await refreshSessions();
          await refreshNodes(); // 新会话可能刚起了节点
          if (id) setCurrentId(id);
          else setCurrentId((cur) => (s.find((x) => x.id === cur) ? cur : s[0]?.id ?? ""));
        }}
      />

      {current ? (
        <SensingArea session={current} bodyNode={bodyNode} worldNode={worldNode} />
      ) : (
        <div className="flex min-w-0 items-center justify-center overflow-hidden bg-neutral-950 p-8 text-center text-sm text-neutral-600">
          {t("Pick a session on the left, or create one.")}
        </div>
      )}

      <ChatPanel session={current} brains={registry?.brains ?? []} bodyNode={bodyNode} onSessionsChanged={refreshSessions} />
    </main>
  );
}
