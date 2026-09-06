"use client";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { useI18n } from "@/lib/i18n";
import {
  getPerception,
  getWorldSensors,
  POLL_PERCEIVE_MS,
  worldCameraStreamUrl,
  type NodeInfo,
  type Perception,
  type SessionSummary,
} from "@/lib/api";

// 中间区：可选视图，多选，按会话记在 localStorage 里。
//   observation   大脑看到的：GET /api/perceive 的结果，每一路一格、带名字。这就是下一回合送进模型的东西。
//   body          身体节点的连续视频（MJPEG /stream）——给人看的，大脑一回合只拿一张快照。
//   world         世界节点的追拍相机（/stream）——上帝视角，**大脑从来看不到**，标题必须写明。
//   cam:<name>    世界节点 /sensors 列出的每一路相机（/stream/<去前缀名>），同样只给人看。
// 默认勾 observation + body。
type ViewId = string;
const VIEW_OBSERVATION: ViewId = "observation";
const VIEW_BODY: ViewId = "body";
const VIEW_WORLD: ViewId = "world";
const CAM_VIEW_PREFIX = "cam:";
const DEFAULT_VIEWS: ViewId[] = [VIEW_OBSERVATION, VIEW_BODY];
const viewsKey = (sid: string) => `nerv-views:${sid}`;

function readViews(sid: string): ViewId[] {
  try {
    const raw = localStorage.getItem(viewsKey(sid));
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) return arr.filter((x) => typeof x === "string");
    }
  } catch {
    /* 读不了就用默认 */
  }
  return DEFAULT_VIEWS;
}

export default function SensingArea({
  session,
  bodyNode,
  worldNode,
}: {
  session: SessionSummary | null;
  bodyNode: NodeInfo | null;
  worldNode: NodeInfo | null;
}) {
  const { t } = useI18n();
  const [perc, setPerc] = useState<Perception | null>(null);
  const [err, setErr] = useState("");
  const [live, setLive] = useState(true); // 自动刷新开关；真机上每次 perceive 都是一次真实采样，所以能关
  const [busy, setBusy] = useState(false);
  const [nonce, setNonce] = useState(0); // 视频流重连
  const [views, setViews] = useState<ViewId[]>(DEFAULT_VIEWS);
  const [worldSensors, setWorldSensors] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<ViewId | null>(null); // 放大到整块中间区的那一格

  const sid = session?.id ?? "";
  const hasBody = !!session?.body;
  const active = session?.status === "active";
  const bodyOnline = !!bodyNode?.online;
  const worldOnline = !!worldNode?.online;
  const worldUrl = worldNode?.url ?? "";

  // 视图选择按会话持久化
  useEffect(() => {
    if (!sid) return;
    setViews(readViews(sid));
    setExpanded(null);
  }, [sid]);
  const toggleView = (id: ViewId) =>
    setViews((cur) => {
      const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
      try {
        localStorage.setItem(viewsKey(sid), JSON.stringify(next));
      } catch {
        /* 存不了也照样切 */
      }
      return next;
    });

  // 大脑看到的
  const load = useCallback(async () => {
    if (!sid || !hasBody) return;
    setBusy(true);
    try {
      setPerc(await getPerception(sid));
      setErr("");
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(false);
  }, [sid, hasBody]);

  const wantObservation = views.includes(VIEW_OBSERVATION);
  useEffect(() => {
    setPerc(null);
    setErr("");
    if (!sid || !hasBody || !wantObservation) return;
    load();
    if (!live || !active) return;
    const id = setInterval(load, POLL_PERCEIVE_MS);
    return () => clearInterval(id);
  }, [sid, hasBody, live, active, load, wantObservation]);

  // 世界节点的相机清单：直接问节点 GET <worldurl>/sensors；问不到（节点的 CORS 白名单默认只有
  // 开发端口 :8100，网页由 `nerv serve` 端出来时源不在里面）就退回 /api/nodes 已经转述的同一份清单。
  const relayed = worldNode?.sensors;
  useEffect(() => {
    if (!worldUrl || !worldOnline) {
      setWorldSensors([]);
      return;
    }
    let gone = false;
    getWorldSensors(worldUrl)
      .then((s) => !gone && setWorldSensors(s))
      .catch(() => !gone && setWorldSensors(relayed ?? []));
    return () => {
      gone = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [worldUrl, worldOnline, (relayed ?? []).join("|")]);

  // 可选的视图清单
  const chips = useMemo(() => {
    const out: { id: ViewId; label: string; title?: string; group: "robot" | "third" }[] = [
      { id: VIEW_OBSERVATION, label: `👁 ${t("Observation")}`, title: t("what the brain sees — one snapshot per turn"), group: "robot" },
      { id: VIEW_BODY, label: `🎥 ${t("Body live")}`, title: t("the body's own camera, continuous — for you, not the brain"), group: "robot" },
    ];
    if (session?.world) {
      out.push({ id: VIEW_WORLD, label: `🛰 ${t("World chase (operator only)")}`, title: t("the brain never sees this"), group: "third" });
      for (const s of worldSensors) out.push({ id: CAM_VIEW_PREFIX + s, label: `📷 ${s}`, title: t("world camera — the brain never sees this"), group: "third" });
    }
    return out;
  }, [session?.world, worldSensors, t]);

  // 选中的视图 → 一格格瓦片
  const tiles = useMemo(() => {
    if (!session || !hasBody) return [];
    const out: { id: ViewId; label: string; sub?: string; muted?: boolean; body: ReactNode }[] = [];
    for (const v of views) {
      if (v === VIEW_OBSERVATION) {
        const images = perc?.images ?? [];
        if (!perc || images.length === 0) {
          out.push({
            id: v,
            label: `👁 ${t("Observation")}`,
            sub: t("what the brain sees"),
            body: (
              <span className="p-4 text-[11px] text-neutral-500">
                {perc ? t("(no images in this observation — the body declares no camera and no ambient stream was selected)") : err ? "" : t("Perceiving…")}
              </span>
            ),
          });
        } else {
          for (const im of images)
            out.push({
              id: `${v}/${im.name}`,
              label: `👁 ${t("Observation")} · ${im.name}`,
              sub: t("what the brain sees"),
              // eslint-disable-next-line @next/next/no-img-element
              body: <img src={`data:image/png;base64,${im.b64}`} alt={im.name} className="max-h-full max-w-full object-contain" />,
            });
        }
      } else if (v === VIEW_BODY) {
        out.push({
          id: v,
          label: `🎥 ${t("Body live")} · ${bodyNode?.key ?? `body:${session.body}`}`,
          sub: t("what the body's own camera is streaming; the brain gets one snapshot per turn, not this"),
          body: (
            <Stream nonce={nonce} url={bodyOnline && bodyNode ? `${bodyNode.url}/stream` : null} alt={t("Body live")}
              missing={bodyNode ? t("body node offline") : t("body node not launched")} />
          ),
        });
      } else if (v === VIEW_WORLD) {
        if (!session.world) continue;
        out.push({
          id: v,
          label: `🛰 ${t("World chase (operator only)")} · ${worldNode?.key ?? `world:${session.world}/${session.body}`}`,
          sub: t("the brain never sees this"),
          muted: true,
          body: (
            <Stream nonce={nonce} url={worldOnline && worldNode ? `${worldNode.url}/stream` : null} alt={t("World chase (operator only)")}
              missing={worldNode ? t("world node offline") : t("world node not launched")} muted />
          ),
        });
      } else if (v.startsWith(CAM_VIEW_PREFIX)) {
        const name = v.slice(CAM_VIEW_PREFIX.length);
        if (!worldSensors.includes(name)) continue; // 这个世界（已经）没有这路相机
        out.push({
          id: v,
          label: `📷 ${name}`,
          sub: t("world camera — the brain never sees this"),
          muted: true,
          body: (
            <Stream nonce={nonce} url={worldOnline && worldNode ? worldCameraStreamUrl(worldNode.url, name) : null} alt={name}
              missing={worldNode ? t("world node offline") : t("world node not launched")} muted />
          ),
        });
      }
    }
    return out;
  }, [session, hasBody, views, perc, err, bodyNode, worldNode, bodyOnline, worldOnline, worldSensors, nonce, t]);

  // Esc 关掉放大的那一格
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setExpanded(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const stateKeys = perc ? Object.keys(perc.state ?? {}) : [];
  const expandedTile = expanded ? tiles.find((x) => x.id === expanded) ?? null : null;

  if (!session || !hasBody) {
    return (
      <section className="flex min-w-0 flex-col overflow-hidden p-6">
        <div className="flex h-full items-center justify-center rounded-2xl border border-neutral-800 bg-neutral-900">
          <span className="text-sm text-neutral-500">{t("Conversation only — no body, no world")}</span>
        </div>
      </section>
    );
  }

  return (
    <section className="relative flex min-w-0 flex-col overflow-hidden">
      {/* ---- 视图选择 ---- */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-neutral-800 bg-neutral-900/60 px-3 py-2">
        <div className="flex flex-wrap items-center gap-3" role="group" aria-label={t("Views")}>
        {([["robot", t("Robot's own view")], ["third", t("Third-person view")]] as const).map(([g, caption]) => {
          const items = chips.filter((c) => c.group === g);
          if (!items.length) return null;
          return (
            <div key={g} className="flex flex-wrap items-center gap-1.5" role="group" aria-label={caption}>
              <span className="text-[10px] uppercase tracking-wide text-neutral-500">{caption}</span>
              {items.map((c) => {
                const on = views.includes(c.id);
                return (
                  <button key={c.id} onClick={() => toggleView(c.id)} aria-pressed={on} title={c.title}
                    className={`rounded-full border px-2.5 py-0.5 text-[11px] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                      on ? "border-blue-600 bg-blue-600 text-white" : "border-neutral-700 text-neutral-400 hover:border-neutral-500 hover:text-neutral-200"
                    }`}>
                    {c.label}
                  </button>
                );
              })}
            </div>
          );
        })}
        </div>
        <span className="ml-auto flex items-center gap-1.5">
          {wantObservation && (
            <>
              <label className="flex items-center gap-1 text-[11px] text-neutral-400">
                <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
                {t("auto-refresh")}
              </label>
              <button onClick={load} disabled={busy || !active}
                className="rounded-md border border-neutral-700 bg-neutral-800 px-2 py-1 text-[11px] text-neutral-300 hover:bg-neutral-700 disabled:opacity-50">
                {busy ? t("Perceiving…") : t("Perceive now")}
              </button>
            </>
          )}
          <button onClick={() => setNonce((n) => n + 1)} title={t("reconnect the video streams")}
            className="rounded border border-neutral-800 px-1.5 py-1 text-[10px] text-neutral-500 hover:text-neutral-300">
            {t("reconnect")}
          </button>
        </span>
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
        <div className="text-[11px] text-neutral-500">
          {session.body} · {session.world}
          {session.sensors.length ? ` · ${t("ambient")}: ${session.sensors.join(", ")}` : ""}
          {!active && <span className="ml-2 text-amber-400">{t("This session is not active; showing the last observation only.")}</span>}
        </div>
        {wantObservation && err && (
          <div className="rounded-lg border border-amber-700/60 bg-amber-950/30 p-3 text-xs text-amber-300">
            {t("Could not perceive:")} {err}
          </div>
        )}

        {tiles.length === 0 ? (
          <div className="flex flex-1 items-center justify-center rounded-2xl border border-dashed border-neutral-800 text-xs text-neutral-500">
            {t("No view selected — pick one above.")}
          </div>
        ) : (
          <div className={`grid gap-3 ${tiles.length > 1 ? "grid-cols-2" : "grid-cols-1"}`}>
            {tiles.map((tile) => (
              <Tile key={tile.id} label={tile.label} sub={tile.sub} muted={tile.muted} onExpand={() => setExpanded(tile.id)}>
                {tile.body}
              </Tile>
            ))}
          </div>
        )}

        {wantObservation && perc && (
          <details className="rounded-lg bg-neutral-900/60 text-xs">
            <summary className="cursor-pointer px-3 py-1.5 text-neutral-400">
              {t("state the brain is told")} · {stateKeys.length} {t("keys")}
            </summary>
            <pre className="overflow-x-auto px-3 pb-2 text-[10px] leading-relaxed text-neutral-500">
              {JSON.stringify(perc.state ?? {}, null, 2)}
            </pre>
          </details>
        )}
      </div>

      {/* ---- 放大：盖住整块中间区 ---- */}
      {expandedTile && (
        <div className="absolute inset-0 z-20 flex flex-col bg-neutral-950 p-3" role="dialog" aria-modal="true" aria-label={expandedTile.label}>
          <Tile label={expandedTile.label} sub={expandedTile.sub} muted={expandedTile.muted} expanded onExpand={() => setExpanded(null)} fill>
            {expandedTile.body}
          </Tile>
        </div>
      )}
    </section>
  );
}

// 一格：标题 + 放大键 + 内容
function Tile({ label, sub, muted = false, expanded = false, fill = false, onExpand, children }: {
  label: string; sub?: string; muted?: boolean; expanded?: boolean; fill?: boolean; onExpand: () => void; children: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <div className={`flex min-w-0 flex-col gap-1 rounded-2xl border bg-neutral-900 p-2 ${muted ? "border-neutral-800/60" : "border-neutral-800"} ${fill ? "h-full" : ""}`}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className={`truncate text-[11px] ${muted ? "text-neutral-500" : "text-neutral-300"}`} title={label}>{label}</div>
          {sub && <div className="truncate text-[10px] text-neutral-600">{sub}</div>}
        </div>
        <button onClick={onExpand} title={expanded ? t("Close (Esc)") : t("Expand")} aria-label={expanded ? t("Close (Esc)") : t("Expand")}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-neutral-500 hover:bg-neutral-800 hover:text-neutral-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          autoFocus={expanded}>
          {expanded ? <CloseIcon /> : <ExpandIcon />}
        </button>
      </div>
      <div className={`flex items-center justify-center overflow-hidden rounded-xl bg-neutral-950 ${fill ? "min-h-0 flex-1" : "min-h-32 max-h-[46vh]"}`}>
        {children}
      </div>
    </div>
  );
}

// 一路 MJPEG。<img> 加载失败 = 这个节点没有这路流（世界的 third_person 关着、真机没相机），
// 不是故障——显示一句话，不盖告警。
function Stream({ url, alt, missing, nonce, muted = false }: { url: string | null; alt: string; missing: string; nonce: number; muted?: boolean }) {
  const { t } = useI18n();
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url, nonce]);
  if (url && !failed)
    // eslint-disable-next-line @next/next/no-img-element
    return <img key={`${url}#${nonce}`} src={url} alt={alt} onError={() => setFailed(true)} className={`max-h-full max-w-full object-contain ${muted ? "opacity-80" : ""}`} />;
  return <span className="p-4 text-[11px] text-neutral-600">{url ? t("(this node offers no video stream)") : missing}</span>;
}

function ExpandIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}
