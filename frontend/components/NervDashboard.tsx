"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useI18n } from "@/lib/i18n";
import LangToggle from "./LangToggle";
import { NodeTrust, TrustBadge } from "./NodeTrust";
import { StatusBadge } from "./ChatPanel";
import {
  getNerv, getNodeStatus, nervEventsUrl, POLL_NODES_MS, SIGNAL_LOG_SHOWN,
  type BodySpec, type MatrixCell, type NervOverview, type NodeInfo, type SignalEntry, type ToolSpec, type WorldSpec,
} from "@/lib/api";

const OVERVIEW_POLL_MS = POLL_NODES_MS;

const KIND_COLOR: Record<string, string> = {
  llm_call: "text-purple-400",
  body_call: "text-sky-400",
  world_call: "text-teal-400",
  tool_call: "text-emerald-400",
  gate: "text-amber-400",
  operator: "text-neutral-300",
};

function Json({ value }: { value: unknown }) {
  return (
    <pre className="mt-1 overflow-x-auto rounded-md border border-neutral-800 bg-black/50 p-2 text-[10px] leading-relaxed text-neutral-400">
      {JSON.stringify(value ?? {}, null, 2)}
    </pre>
  );
}

function Region({ title, color, sub, children }: { title: string; color: string; sub?: string; children: ReactNode }) {
  return (
    <div className="border-l-2 pl-3" style={{ borderColor: color }}>
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide" style={{ color }}>
        {title} {sub && <span className="font-normal normal-case tracking-normal text-neutral-600">· {sub}</span>}
      </div>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-3">
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-xs text-neutral-500">{label}</div>
    </div>
  );
}

function Online({ node }: { node: NodeInfo | null }) {
  const { t } = useI18n();
  if (!node) return <span className="text-xs text-neutral-500">○ {t("not launched")}</span>;
  return <span className={`text-xs ${node.online ? "text-green-400" : "text-red-400"}`}>● {node.online ? t("online") : node.alive ? t("starting / unreachable") : t("exited")}</span>;
}

// 节点自己的真实状态（人的上帝视角）。按需拉，不轮询——它可能很大。
function StatusPeek({ nodeKey }: { nodeKey: string }) {
  const { t } = useI18n();
  const [st, setSt] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState("");
  return (
    <div>
      <button onClick={() => getNodeStatus(nodeKey).then((s) => { setSt(s); setErr(""); }).catch((e) => setErr((e as Error).message))}
        className="rounded border border-neutral-700 px-2 py-0.5 text-[11px] text-neutral-300 hover:bg-neutral-800">
        {st ? t("Refresh status") : t("Peek at /status (god view, the brain cannot see this)")}
      </button>
      {err && <span className="ml-2 text-[11px] text-red-400">{err}</span>}
      {st && <Json value={st} />}
    </div>
  );
}

// 🤖 身体卡：注册表里写了什么 + 起来了没有 + 信任门。
function BodyCard({ spec, node, onChanged }: { spec: BodySpec; node: NodeInfo | null; onChanged: () => void }) {
  const { t } = useI18n();
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">
          🤖 {spec.label || spec.name} <span className="text-xs text-neutral-500">{spec.name} · {spec.family} · v{spec.version}</span>
        </span>
        <span className="flex items-center gap-2">
          {node?.trust && <TrustBadge trust={node.trust} />}
          <Online node={node} />
        </span>
      </div>
      <div className="mt-3 space-y-3">
        <Region title={t("Sensors")} color="#58a6ff" sub={t("what this body itself perceives")}>
          {spec.sensors.length ? spec.sensors.map((s) => (
            <div key={s.name} className="text-[12px] text-neutral-300"><span className="font-mono">{s.name}</span> <span className="text-neutral-500">{s.description} · {s.width}×{s.height}</span></div>
          )) : <div className="text-xs text-neutral-500">{t("(none declared)")}</div>}
        </Region>
        <Region title={t("Skills")} color="#3fb950" sub={t("policies this body can run")}>
          {Object.keys(spec.skills).length ? Object.entries(spec.skills).map(([k, v]) => (
            <div key={k} className="text-[12px] text-neutral-300"><span className="font-mono">{k}</span> <span className="text-neutral-500">{v.description}</span></div>
          )) : <div className="text-xs text-neutral-500">{t("(none declared)")}</div>}
        </Region>
        <Region title={t("Buses")} color="#a78bfa" sub={t("how it is driven in a sim world / on real hardware")}>
          {Object.keys(spec.buses).length ? Object.entries(spec.buses).map(([k, v]) => (
            <div key={k} className="text-[12px] text-neutral-300">
              <span className="font-mono">{k}</span> → {v.kind}
              {!v.verified && <span className="ml-1 text-amber-400">{t("(unverified — never run on hardware)")}</span>}
            </div>
          )) : <div className="text-xs text-neutral-500">{t("(none declared)")}</div>}
        </Region>
        {node && (
          <Region title={t("Node")} color="#f59e0b" sub={node.url}>
            {node.tools && node.tools.length > 0 && (
              <div className="text-[12px] text-neutral-300">{t("tools")}: <span className="font-mono">{node.tools.join(", ")}</span></div>
            )}
            <NodeTrust node={node} onChanged={onChanged} />
            {node.online && <StatusPeek nodeKey={node.key} />}
          </Region>
        )}
      </div>
    </div>
  );
}

// 🌍 世界卡：注册表 + 兼容矩阵 + 起来的实例（一个世界按身体各起一个节点）。
function WorldCard({ spec, nodes, matrix }: { spec: WorldSpec; nodes: NodeInfo[]; matrix: MatrixCell[] }) {
  const { t } = useI18n();
  const cells = matrix.filter((c) => c.world === spec.name);
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">
          🌍 {spec.label || spec.name} <span className="text-xs text-neutral-500">{spec.name} · {spec.kind}{spec.engine ? ` · ${spec.engine}` : ""}</span>
        </span>
        <span className="text-xs text-neutral-500">{nodes.length} {t("instance(s) launched")}</span>
      </div>
      <div className="mt-3 space-y-3">
        <Region title={t("Bodies")} color="#3fb950" sub={t("who can stand here (the registry's compatibility matrix)")}>
          {cells.map((c) => (
            <div key={c.body} className="text-[12px]">
              <span className={c.ok ? "text-green-300" : "text-neutral-500 line-through"}>{c.body}</span>
              {!c.ok && <span className="ml-2 text-[11px] text-neutral-500">{c.reason}</span>}
            </div>
          ))}
          {cells.length === 0 && <div className="text-xs text-neutral-500">{t("(no bodies in the registry)")}</div>}
        </Region>
        <Region title={t("Ambient sensors")} color="#58a6ff" sub={t("streams the world publishes; a session opts in to them")}>
          {spec.ambient.length ? spec.ambient.map((s) => (
            <div key={s.name} className="text-[12px] text-neutral-300"><span className="font-mono">{s.name}</span> <span className="text-neutral-500">{s.description}</span></div>
          )) : <div className="text-xs text-neutral-500">{t("(none declared)")}</div>}
        </Region>
        {nodes.map((n) => (
          <Region key={n.key} title={n.key} color="#f59e0b" sub={n.url}>
            <div className="flex items-center gap-3 text-[12px]">
              <Online node={n} />
              {n.sensors && n.sensors.length > 0 && <span className="text-neutral-400">{t("sensors")}: <span className="font-mono">{n.sensors.join(", ")}</span></span>}
              {n.online && (
                <a href={`${n.url}/stream`} target="_blank" rel="noreferrer" className="text-blue-400 hover:underline">
                  {t("chase camera")} <span className="text-neutral-600">({t("operator only — the brain never sees this")})</span>
                </a>
              )}
            </div>
            {n.online && <StatusPeek nodeKey={n.key} />}
          </Region>
        ))}
      </div>
    </div>
  );
}

// 🧰 工具节点卡：纯计算的顾问，只有工具、没有画面。
function ToolCard({ spec, node, onChanged }: { spec: ToolSpec; node: NodeInfo | null; onChanged: () => void }) {
  const { t } = useI18n();
  return (
    <div className="rounded-xl border border-emerald-900/60 bg-neutral-900 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">🧰 {spec.label || spec.name} <span className="text-xs text-neutral-500">{spec.name}</span></span>
        <span className="flex items-center gap-2">
          {node?.trust && <TrustBadge trust={node.trust} />}
          <Online node={node} />
        </span>
      </div>
      {node ? (
        <div className="mt-3 space-y-2">
          {node.tools && node.tools.length > 0 && (
            <div className="text-[12px] text-neutral-300">{t("tools")}: <span className="font-mono">{node.tools.join(", ")}</span></div>
          )}
          <NodeTrust node={node} onChanged={onChanged} />
        </div>
      ) : (
        <div className="mt-2 text-xs text-neutral-500">{t("(launches with the next session that mounts it)")}</div>
      )}
    </div>
  );
}

// 一条信号 → 一行人话。⛔ 不猜字段：每种 kind 用后端真有的字段。
function fmtSignal(e: SignalEntry, tt: (k: string) => string): { head: string; tail: string; warn: boolean } {
  if (e.kind === "llm_call") {
    return {
      head: `${e.model} · ${e.n_history} ${tt("msgs")} · ${e.n_tools} ${tt("tools")}${e.has_image ? ` · ${tt("with image")}` : ""} (${e.ms}ms)`,
      tail: e.error ? `✗ ${e.error}` : `${e.tool_calls.length ? `→ ${e.tool_calls.join(", ")}` : ""} ${e.reply ? `“${e.reply.slice(0, 120)}${e.reply.length > 120 ? "…" : ""}”` : ""}`.trim(),
      warn: !!e.error,
    };
  }
  if (e.kind === "gate") {
    return {
      head: `${e.tool} · ${e.origin} · ${e.armed ? tt("armed") : tt("disarmed")}`,
      tail: e.allowed ? "✓ " + tt("allowed") : `✗ ${tt("refused")}: ${tt(e.reason)}`,
      warn: !e.allowed,
    };
  }
  if (e.kind === "operator") {
    const { id: _i, t: _t, ts: _ts, session: _s, kind: _k, event, ...rest } = e;
    return { head: event, tail: JSON.stringify(rest), warn: false };
  }
  const r = e.resp ?? {};
  const ok = (r as { ok?: boolean }).ok;
  return {
    head: `${e.node} ${e.method} → ${e.summary} (${e.ms}ms)`,
    tail: `← ${JSON.stringify(r).slice(0, 160)}`,
    warn: ok === false,
  };
}

// embedded=true：内嵌在主页中间区；false：/nerv 整页独立版。
export default function NervDashboard({ embedded = false, onOpenLogs }: { embedded?: boolean; onOpenLogs?: () => void }) {
  const { t } = useI18n();
  const [data, setData] = useState<NervOverview | null>(null);
  const [events, setEvents] = useState<SignalEntry[]>([]);
  const termRef = useRef<HTMLDivElement>(null);
  const [tick, setTick] = useState(0);

  // ?live=0：不开实时信号那条 SSE 长连接（嵌进文档 / headless 截图时用）。
  const liveSignals = typeof window === "undefined" || new URLSearchParams(window.location.search).get("live") !== "0";

  useEffect(() => {
    const load = () => getNerv().then(setData).catch(() => {});
    load();
    const id = setInterval(load, OVERVIEW_POLL_MS);
    if (!liveSignals) return () => clearInterval(id);
    const es = new EventSource(nervEventsUrl(0));
    es.onmessage = (e) => setEvents((prev) => [...prev.slice(-SIGNAL_LOG_SHOWN), JSON.parse(e.data) as SignalEntry]);
    return () => {
      clearInterval(id);
      es.close();
    };
  }, [liveSignals, tick]);
  useEffect(() => {
    termRef.current?.scrollTo(0, termRef.current.scrollHeight);
  }, [events]);

  const nodes = data?.nodes ?? [];
  const reg = data?.registry;
  const nodeFor = (key: string) => nodes.find((n) => n.key === key) ?? null;
  const onChanged = () => setTick((x) => x + 1);

  return (
    <main className={`${embedded ? "h-full min-w-0 overflow-y-auto" : "min-h-screen"} bg-neutral-950 p-6 text-neutral-200`}>
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold">{t("NERV dashboard · nodes, brains, sessions, signals")}</h1>
          {!embedded && (
            <div className="flex items-center gap-3 text-sm">
              <LangToggle />
              <a href="/session-logs" className="text-blue-400 hover:underline">{t("Session Logs (activity trace)")}</a>
              <a href="/" className="text-blue-400 hover:underline">{t("← Back to the app")}</a>
            </div>
          )}
        </div>

        <p className="text-sm leading-relaxed text-neutral-400">
          {t("A session is brain × body × world. NERV launches one body node per body, one world node per (world, body) pair and one tool node per tool; the brain talks to bodies and tools only, and a world reaches the brain only through the sensor streams a session opts into.")}{" "}
          {t("This page shows what the registry declares, which nodes are up, where each stands with you (trust), and every signal that crosses NERV. For one session's full chain, see")}{" "}
          {embedded && onOpenLogs ? (
            <button onClick={onOpenLogs} className="text-blue-400 hover:underline">Session Logs</button>
          ) : (
            <a href="/session-logs" className="text-blue-400 hover:underline">Session Logs</a>
          )}.
        </p>

        {data && reg && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <Stat label={t("Bodies")} value={reg.bodies.length} />
            <Stat label={t("Worlds")} value={reg.worlds.length} />
            <Stat label={t("Tools")} value={reg.tools.length} />
            <Stat label={t("nodes up")} value={`${nodes.filter((n) => n.online).length} / ${nodes.length}`} />
            <Stat label={t("Sessions")} value={data.sessions.length} />
          </div>
        )}

        <section>
          <h2 className="mb-2 text-sm font-medium text-neutral-400">🤖 {t("Bodies")} <span className="text-xs font-normal text-neutral-600">{t("· what the brain drives — its text passes the trust gate before the brain reads it")}</span></h2>
          <div className="space-y-3">
            {reg?.bodies.map((b) => <BodyCard key={b.name} spec={b} node={nodeFor(`body:${b.name}`)} onChanged={onChanged} />)}
            {reg && reg.bodies.length === 0 && <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4 text-xs text-neutral-500">{t("(no bodies in the registry)")}</div>}
          </div>
        </section>

        <section>
          <h2 className="mb-2 text-sm font-medium text-neutral-400">🌍 {t("Worlds")} <span className="text-xs font-normal text-neutral-600">{t("· where a body stands — physics and ambient sensors, never a voice to the brain")}</span></h2>
          <div className="space-y-3">
            {reg?.worlds.map((w) => (
              <WorldCard key={w.name} spec={w} matrix={reg.matrix} nodes={nodes.filter((n) => n.kind === "world" && n.key.startsWith(`world:${w.name}/`))} />
            ))}
            {reg && reg.worlds.length === 0 && <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4 text-xs text-neutral-500">{t("(no worlds in the registry)")}</div>}
          </div>
        </section>

        <section>
          <h2 className="mb-2 text-sm font-medium text-neutral-400">🧰 {t("Tools")} <span className="text-xs font-normal text-neutral-600">{t("· pure-computation advisors the brain consults (question in, answer out)")}</span></h2>
          <div className="space-y-3">
            {reg?.tools.map((x) => <ToolCard key={x.name} spec={x} node={nodeFor(`tool:${x.name}`)} onChanged={onChanged} />)}
            {reg && reg.tools.length === 0 && <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4 text-xs text-neutral-500">{t("(no tools in the registry)")}</div>}
          </div>
        </section>

        <section>
          <h2 className="mb-2 text-sm font-medium text-neutral-400">🧠 {t("Brains")} <span className="text-xs font-normal text-neutral-600">{t("· the model behind a session; swappable mid-session")}</span></h2>
          <div className="grid gap-1.5 md:grid-cols-2">
            {data?.brains.map((b) => (
              <div key={b.name} className="flex items-center justify-between rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-xs">
                <span>{t(b.vendor)} · <b>{t(b.label)}</b> <span className="text-neutral-500">({b.model})</span></span>
                <span className={b.available ? "text-green-400" : "text-neutral-500"}>{b.available ? t("configured") : t("not configured")}</span>
              </div>
            ))}
          </div>
        </section>

        <section>
          <h2 className="mb-2 text-sm font-medium text-neutral-400">{t("Live signals")} <span className="text-xs font-normal text-neutral-600">{t("· every call that crosses NERV: brain ↔ model, body, world, tool; gate decisions; operator actions")}</span></h2>
          <div ref={termRef} className="h-80 overflow-y-auto rounded-xl border border-neutral-800 bg-black p-3 font-mono text-xs leading-relaxed">
            {events.length === 0 && <div className="text-neutral-600">{t("(no signals yet — send a message in the app)")}</div>}
            {events.map((e) => {
              const f = fmtSignal(e, t);
              return (
                <div key={e.id} className="mb-1">
                  <div>
                    <span className="text-neutral-600">[{e.ts}]</span>{" "}
                    <span className={KIND_COLOR[e.kind] ?? "text-neutral-300"}>{e.kind}</span>{" "}
                    <span className="text-neutral-300">{f.head}</span>
                    {e.session && <span className="text-neutral-700"> ·{e.session.slice(0, 12)}</span>}
                  </div>
                  {f.tail && <div className={`pl-14 ${f.warn ? "text-amber-400" : "text-neutral-500"}`}>{f.tail}</div>}
                </div>
              );
            })}
          </div>
        </section>

        <section>
          <h2 className="mb-2 text-sm font-medium text-neutral-400">{t("Sessions")}</h2>
          <div className="space-y-1.5">
            {data?.sessions.length === 0 && <div className="text-xs text-neutral-500">{t("(no sessions yet)")}</div>}
            {data?.sessions.map((s) => (
              <div key={s.id} className="flex items-center justify-between rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-xs">
                <span className="truncate">
                  {s.title}　<span className="text-neutral-500">{s.world ? `${s.world} / ${s.body}` : t("Conversation only")} · {s.brain}</span>
                </span>
                <span className="flex items-center gap-2">
                  {s.armed && <span className="rounded border border-red-700/60 bg-red-950/40 px-1.5 py-0.5 text-[10px] text-red-300">{t("ARMED")}</span>}
                  <StatusBadge status={s.status} />
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
