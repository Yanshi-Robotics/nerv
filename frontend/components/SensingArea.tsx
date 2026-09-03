"use client";
import { useCallback, useEffect, useState } from "react";

import { useI18n } from "@/lib/i18n";
import { getPerception, POLL_PERCEIVE_MS, type NodeInfo, type Perception, type SessionSummary } from "@/lib/api";
import RuntimeParamsBar from "./RuntimeParamsBar";

// 中间感知区，两块、各有各的标题，⛔ 不许混在一起：
//   1. 大脑看到的：GET /api/perceive 的结果——身体自己的感官 + 会话声明的环境流，每一路带名字。
//      这就是下一回合送进模型的东西（离散快照），所以它是主角。
//   2. 只给人看的连续视频（MJPEG）：身体节点的 /stream，和世界节点的追拍 /stream。
//      后者是上帝视角，**大脑从来看不到**——标题必须写明，否则看的人会以为模型有全局视野。
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

  const sid = session?.id ?? "";
  const hasBody = !!session?.body;
  const active = session?.status === "active";

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

  useEffect(() => {
    setPerc(null);
    setErr("");
    if (!sid || !hasBody) return;
    load();
    if (!live || !active) return;
    const id = setInterval(load, POLL_PERCEIVE_MS);
    return () => clearInterval(id);
  }, [sid, hasBody, live, active, load]);

  const bodyOnline = !!bodyNode?.online;
  const worldOnline = !!worldNode?.online;
  const cameras = perc?.images ?? [];
  const stateKeys = perc ? Object.keys(perc.state ?? {}) : [];

  return (
    <section className="flex min-w-0 flex-col overflow-hidden">
      <RuntimeParamsBar />
      <div className="flex min-w-0 flex-1 flex-col gap-3 overflow-y-auto p-6">
        {!session || !hasBody ? (
          <div className="flex h-full items-center justify-center rounded-2xl border border-neutral-800 bg-neutral-900">
            <span className="text-sm text-neutral-500">
              {session ? t("Conversation only — no body, no world") : t("No session selected")}
            </span>
          </div>
        ) : (
          <>
            {/* ---- 1. 大脑看到的 ---- */}
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-medium text-neutral-300">
                {t("👁 What the brain sees")}
                <span className="ml-2 text-xs font-normal text-neutral-500">
                  {session.body} · {session.world}
                  {session.sensors.length ? ` · ${t("ambient")}: ${session.sensors.join(", ")}` : ""}
                </span>
              </h2>
              <div className="ml-auto flex items-center gap-1.5">
                <label className="flex items-center gap-1 text-[11px] text-neutral-400">
                  <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
                  {t("auto-refresh")}
                </label>
                <button onClick={load} disabled={busy || !active}
                  className="rounded-md border border-neutral-700 bg-neutral-800 px-2 py-1 text-[11px] text-neutral-300 hover:bg-neutral-700 disabled:opacity-50">
                  {busy ? t("Perceiving…") : t("Perceive now")}
                </button>
              </div>
            </div>
            {err && (
              <div className="rounded-lg border border-amber-700/60 bg-amber-950/30 p-3 text-xs text-amber-300">
                {t("Could not perceive:")} {err}
              </div>
            )}
            {!active && (
              <div className="text-[11px] text-neutral-500">{t("This session is not active; showing the last observation only.")}</div>
            )}
            {perc && (
              <div className="space-y-2">
                {cameras.length === 0 ? (
                  <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4 text-xs text-neutral-500">
                    {t("(no images in this observation — the body declares no camera and no ambient stream was selected)")}
                  </div>
                ) : (
                  <div className={`grid gap-3 ${cameras.length > 1 ? "grid-cols-2" : "grid-cols-1"}`}>
                    {cameras.map((im) => (
                      <figure key={im.name} className="relative overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={`data:image/png;base64,${im.b64}`} alt={im.name} className="mx-auto max-h-[46vh] w-auto" />
                        <figcaption className="absolute left-2 top-2 rounded bg-neutral-950/70 px-1.5 py-0.5 font-mono text-[10px] text-neutral-300">
                          {im.name}
                        </figcaption>
                      </figure>
                    ))}
                  </div>
                )}
                <details className="rounded-lg bg-neutral-900/60 text-xs">
                  <summary className="cursor-pointer px-3 py-1.5 text-neutral-400">
                    {t("state the brain is told")} · {stateKeys.length} {t("keys")}
                  </summary>
                  <pre className="overflow-x-auto px-3 pb-2 text-[10px] leading-relaxed text-neutral-500">
                    {JSON.stringify(perc.state ?? {}, null, 2)}
                  </pre>
                </details>
              </div>
            )}

            {/* ---- 2. 只给人看的连续视频 ---- */}
            <div className="mt-2 flex items-center gap-2">
              <h3 className="text-[11px] uppercase tracking-wide text-neutral-500">{t("🎥 Continuous video — for you, not the brain")}</h3>
              <button onClick={() => setNonce((n) => n + 1)}
                className="rounded border border-neutral-800 px-1.5 py-0.5 text-[10px] text-neutral-500 hover:text-neutral-300">
                {t("reconnect")}
              </button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Stream
                nonce={nonce}
                url={bodyOnline && bodyNode ? `${bodyNode.url}/stream` : null}
                title={t("Body stream · {key}", { key: bodyNode?.key ?? `body:${session.body}` })}
                sub={t("what the body's own camera is streaming; the brain gets one snapshot per turn, not this")}
                missing={bodyNode ? t("body node offline") : t("body node not launched")}
              />
              <Stream
                nonce={nonce}
                url={worldOnline && worldNode ? `${worldNode.url}/stream` : null}
                title={t("World chase camera · {key}", { key: worldNode?.key ?? `world:${session.world}/${session.body}` })}
                sub={t("operator only — the brain never sees this")}
                missing={worldNode ? t("world node offline") : t("world node not launched")}
                muted
              />
            </div>
          </>
        )}
      </div>
    </section>
  );
}

// 一路 MJPEG。<img> 加载失败 = 这个节点没有这路流（世界的 third_person 关着、真机没相机），
// 不是故障——显示一句话，不盖告警。
function Stream({ url, title, sub, missing, nonce, muted = false }: {
  url: string | null; title: string; sub: string; missing: string; nonce: number; muted?: boolean;
}) {
  const { t } = useI18n();
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url, nonce]);
  return (
    <div className={`flex flex-col gap-1 rounded-2xl border p-2 ${muted ? "border-neutral-800/60" : "border-neutral-800"} bg-neutral-900`}>
      <div className={`text-[11px] ${muted ? "text-neutral-500" : "text-neutral-300"}`}>{title}</div>
      <div className="text-[10px] text-neutral-600">{sub}</div>
      <div className="flex min-h-32 items-center justify-center overflow-hidden rounded-xl bg-neutral-950">
        {url && !failed ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={`${url}#${nonce}`} src={url} alt={title} onError={() => setFailed(true)}
            className={`max-h-64 max-w-full ${muted ? "opacity-80" : ""}`} />
        ) : (
          <span className="p-4 text-[11px] text-neutral-600">{url ? t("(this node offers no video stream)") : missing}</span>
        )}
      </div>
    </div>
  );
}
