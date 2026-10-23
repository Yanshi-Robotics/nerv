"use client";
import { useCallback, useEffect, useState } from "react";

import OperatorToolbox from "@/components/OperatorToolbox";
import ControlSchemes from "@/components/ControlSchemes";
import ModulesPanel from "@/components/ModulesPanel";
import RuntimeSetup from "@/components/RuntimeSetup";
import VlmPanel from "@/components/VlmPanel";
import ChatPanel from "@/components/ChatPanel";
import NervDashboard from "@/components/NervDashboard";
import SensingArea from "@/components/SensingArea";
import SessionLogsView from "@/components/SessionLogsView";
import SessionSidebar, { type Panel } from "@/components/SessionSidebar";
import WorldExplore from "@/components/world-explore/WorldExplore";
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
  // 右边那两栏显示什么：会话（感知区 + 对话）、Dashboard、Logs。侧栏一直在，不跳页。
  const [panel, setPanel] = useState<Panel>("session");

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
    setRegistry(await getRegistry().catch(() => null));
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
    const id = setInterval(() => { refreshNodes(); refreshSessions(); }, POLL_NODES_MS);
    return () => clearInterval(id);
  }, [refreshNodes, refreshSessions]);

  useEffect(() => {
    const created = (event: Event) => {setCurrentId((event as CustomEvent<string>).detail); refreshSessions(); refreshNodes();};
    window.addEventListener("nerv-instance-created", created);
    return () => window.removeEventListener("nerv-instance-created", created);
  }, [refreshSessions, refreshNodes]);

  const current = sessions.find((x) => x.id === currentId) || null;
  const bodyNode = current?.body ? nodes.find((n) => n.key === `body:${current.body}` && (n.meta.world || null) === current.world) ?? null : null;
  const worldNode = current?.world && current.body ? nodes.find((n) => n.key === `world:${current.world}/${current.body}`) ?? null : null;

  return (
    <main
      className={`nerv-shell grid h-screen bg-neutral-950 text-neutral-100 transition-[grid-template-columns] duration-200 ease-out ${
        collapsed ? "grid-cols-[56px_minmax(0,1fr)_440px]" : "grid-cols-[240px_minmax(0,1fr)_440px]"
      }`}
    >
      <SessionSidebar
        sessions={sessions}
        registry={registry}
        currentId={currentId}
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        panel={panel}
        onPanel={setPanel}
        onSelect={(id) => {
          setCurrentId(id);
          setPanel("session");
        }}
        onChanged={async (id) => {
          const s = await refreshSessions();
          await refreshNodes(); // 新会话可能刚起了节点
          if (id) {
            setCurrentId(id);
            setPanel("session"); // a freshly created session is what you want to look at
          } else setCurrentId((cur) => (s.find((x) => x.id === cur) ? cur : s[0]?.id ?? ""));
        }}
      />

      {panel === "schemes" && <ControlSchemes bodyNode={bodyNode} onSetup={() => setPanel("setup")} onModels={() => setPanel("models")} session={current} brains={registry?.brains ?? []} onChanged={() => {refreshSessions();}} />}
      {panel === "modules" && <ModulesPanel onChanged={() => getRegistry().then(setRegistry)} />}
      {panel === "setup" && <RuntimeSetup registry={registry} onChanged={() => getRegistry().then(setRegistry)} onCreated={id => {setCurrentId(id); setPanel("session"); refreshSessions(); refreshNodes();}} />}
      {panel === "models" && <div className="col-span-2 min-h-0 overflow-hidden"><VlmPanel onChanged={() => getRegistry().then(setRegistry)} /></div>}
      {panel === "dashboard" && (
        <div className="col-span-2 min-h-0 min-w-0 overflow-hidden">
          <NervDashboard embedded onOpenLogs={() => setPanel("logs")} onNodesChanged={() => { refreshNodes(); refreshSessions(); }} />
        </div>
      )}
      {panel === "logs" && (
        <div className="col-span-2 min-h-0 min-w-0 overflow-hidden">
          <SessionLogsView embedded sessionId={current?.id ?? ""} />
        </div>
      )}
      {panel === "explore" && (
        <div className="col-span-2 min-h-0 min-w-0 overflow-hidden">
          <WorldExplore worlds={registry?.worlds ?? []} defaultWorld={current?.world ?? null} />
        </div>
      )}
      {panel === "session" && (
        <OperatorToolbox key={`${current?.id}:${current?.epoch}`} session={current} bodyNode={bodyNode} nodes={nodes} onChanged={() => {refreshSessions(); refreshNodes();}}
          chat={<ChatPanel onSetup={() => setPanel("setup")} session={current} brains={registry?.brains ?? []} bodyNode={bodyNode} onModels={() => setPanel("models")} onSessionsChanged={() => { refreshSessions(); refreshNodes(); }} />}>
          {current ? (
            <SensingArea key={`${current.id}:${current.status}`} session={current} bodyNode={bodyNode} worldNode={worldNode} robotSensors={registry?.bodies.find(body => body.name === current.body)?.sensors} onStateChanged={() => { refreshSessions(); refreshNodes(); }} />
          ) : (
            <section className="flex min-w-0 flex-col items-center justify-center gap-5 p-8">
              <h1 className="text-2xl">{t("Start with your robot")}</h1>
              <p className="max-w-md text-center text-sm text-neutral-400">{t("Choose a real arm or install a simulation. You can add an API key later.")}</p>
              <button className="nerv-control w-full max-w-md rounded-xl border border-neutral-600 p-5 text-left" onClick={() => setPanel("setup")}><span className="block text-lg">{t("Connect a real SO-101")}</span><span className="text-sm text-neutral-400">{t("Arm, calibration and two cameras")}</span></button>
              <button className="nerv-control w-full max-w-md rounded-xl border border-neutral-600 p-5 text-left" onClick={() => setPanel("modules")}><span className="block text-lg">{t("Use simulation")}</span><span className="text-sm text-neutral-400">{t("Install the SO-101 workbench")}</span></button>
              <button className="nerv-control text-sm underline" onClick={() => setPanel("models")}>{t("Set up an AI model")}</button>
            </section>
          )}
        </OperatorToolbox>
      )}
    </main>
  );
}
