"use client";
import { useState } from "react";

import { useI18n } from "@/lib/i18n";
import { approveNode, getNodeManifest, refreshNode, type NodeInfo, type NodeManifest, type NodeTrust as Trust, type TrustState } from "@/lib/api";

/**
 * Approving a node, in the browser.
 *
 * A body or tool node is a separate process reached over a URL. Its guidance is concatenated
 * into the brain's system prompt and its tool descriptions go into the model's tool sheet —
 * all of it written by whoever runs that URL. So none of it reaches the brain until a person
 * has looked at it and said yes; the approval is bound to a hash of what was reviewed, and
 * if the node comes back different the operator is asked again and shown what changed.
 *
 * The design rule here is one thing: **show the whole text, not a summary.** An approval
 * dialog that displays an abridged version approves something that was never read.
 *
 * 在浏览器里批准一个节点。规矩只有一条：**把全文摊出来，不给摘要。**
 * 一个显示删节版的审批对话框，批准的是一个从没被读过的东西——那比没有对话框更糟。
 *
 * World nodes never go through this gate: the brain reads no text from a world, only sensor streams.
 */

export const TRUST_STYLE: Record<TrustState, string> = {
  trusted: "border-green-700/60 bg-green-950/40 text-green-300",
  unknown: "border-amber-700/60 bg-amber-950/40 text-amber-300",
  changed: "border-red-700/60 bg-red-950/40 text-red-300",
  offline: "border-neutral-700 bg-neutral-900 text-neutral-500",
};

export function TrustBadge({ trust }: { trust?: Trust }) {
  const { t } = useI18n();
  const state: TrustState = trust?.state ?? "offline";
  const label =
    state === "trusted" ? t("trusted") : state === "unknown" ? t("not approved") : state === "changed" ? t("changed since approval") : t("unreachable");
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${TRUST_STYLE[state]}`} title={trust?.reason ? t(trust.reason) : undefined}>
      {label}
    </span>
  );
}

/** One node's trust row: state, manifest viewer, approve. */
export function NodeTrust({ node, onChanged }: { node: NodeInfo; onChanged?: () => void }) {
  const { t } = useI18n();
  const [manifest, setManifest] = useState<NodeManifest | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");

  const state: TrustState = node.trust?.state ?? "offline";
  const needsApproval = state === "unknown" || state === "changed";

  async function open() {
    setBusy(true);
    setErr("");
    try {
      setManifest(await getNodeManifest(node.key));
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(false);
  }

  async function approve() {
    setBusy(true);
    setErr("");
    try {
      const r = await approveNode(node.key);
      setNote(t("Approved (hash {hash}…)", { hash: r.hash.slice(0, 12) }));
      setManifest(null);
      onChanged?.();
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(false);
  }

  // 「重新握手」：能力清单在首次握手时被缓存，节点加了新工具而 NERV 没重启的话，
  // 新工具永远上不了工具单——所以给一个按钮。
  async function refresh() {
    setBusy(true);
    setErr("");
    try {
      await refreshNode(node.key);
      setNote(t("Re-handshake done"));
      onChanged?.();
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(false);
  }

  const m = manifest?.manifest;
  const changes = manifest?.trust.changes ?? node.trust?.changes ?? [];

  return (
    <div className={`rounded-lg border p-3 ${needsApproval ? TRUST_STYLE[state] : "border-neutral-800 bg-neutral-950/40"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-neutral-200">{node.key}</span>
        <span className={`text-[10px] ${node.online ? "text-green-400" : "text-red-400"}`}>● {node.online ? t("online") : t("offline")}</span>
        <TrustBadge trust={node.trust} />
        <span className="ml-auto flex items-center gap-1">
          {manifest === null ? (
            <button onClick={open} disabled={busy || !node.online}
              className="rounded border border-neutral-700 px-2 py-0.5 text-[11px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-50">
              {busy ? t("Loading…") : t("See what it declares")}
            </button>
          ) : (
            <button onClick={() => setManifest(null)} disabled={busy}
              className="rounded border border-neutral-700 px-2 py-0.5 text-[11px] text-neutral-400 hover:bg-neutral-800">
              {t("Collapse")}
            </button>
          )}
          <button onClick={refresh} disabled={busy || !node.online}
            title={t("If the node changed its tools or restarted, make NERV ask again (the capability list is cached at handshake)")}
            className="rounded border border-neutral-700 px-2 py-0.5 text-[11px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-50">
            {t("Re-handshake")}
          </button>
        </span>
      </div>
      <div className="mt-1 text-[11px] text-neutral-500">{node.url}</div>

      {needsApproval && (
        <p className="mt-1.5 text-xs leading-relaxed text-neutral-300">
          {state === "changed"
            ? t("Its manifest has changed since you approved it. What changed is below; approve again once you are satisfied.")
            : t("Until you approve it, its tools and guidance do not reach the brain — so it is launched, but nothing can drive it.")}
        </p>
      )}
      {node.trust?.reason && state !== "trusted" && (
        <div className="mt-1 text-[11px] text-neutral-500">{t(node.trust.reason)}</div>
      )}

      {m && (
        <div className="mt-3 space-y-3">
          {changes.length > 0 && (
            <div>
              <div className="text-xs font-medium text-amber-300">{t("What changed")}</div>
              <ul className="mt-1 space-y-0.5">
                {changes.map((c, i) => (
                  <li key={i} className="text-xs text-neutral-300">· {c}</li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <div className="text-xs font-medium text-neutral-300">
              {t("The tools it declares")} ({m.tools.length})
            </div>
            <div className="mt-1 space-y-2">
              {m.tools.map((tool) => (
                <div key={tool.name} className="rounded border border-neutral-800 bg-neutral-950 p-2">
                  <div className="text-xs">
                    <span className="font-mono text-neutral-200">{tool.name}</span>{" "}
                    <span className={tool.kind === "read" || tool.kind === "judge" ? "text-neutral-500" : "text-amber-400"}>
                      [{tool.kind}] {tool.kind === "read" || tool.kind === "judge" ? t("read-only") : t("acts on the body / the world")}
                    </span>
                  </div>
                  {/* 描述原样显示、不截断：它会原样进模型的工具单，那就该原样给人看。 */}
                  <p className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-neutral-400">
                    {tool.description || t("(no description)")}
                  </p>
                  <details className="mt-0.5">
                    <summary className="cursor-pointer text-[10px] text-neutral-600">{t("parameter schema")}</summary>
                    <pre className="mt-1 overflow-x-auto rounded border border-neutral-800 bg-black/40 p-2 text-[10px] text-neutral-500">
                      {JSON.stringify(tool.parameters ?? {}, null, 2)}
                    </pre>
                  </details>
                </div>
              ))}
              {m.tools.length === 0 && <div className="text-[11px] text-neutral-500">{t("(no tools declared)")}</div>}
            </div>
          </div>

          <div>
            <div className="text-xs font-medium text-neutral-300">
              {t("Its guidance")} {t("(goes into the brain’s system prompt, {n} characters)", { n: m.guidance.length })}
            </div>
            {/* 全文，不折叠、不摘要——见本文件顶部的说明。 */}
            <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-neutral-800 bg-neutral-950 p-2 text-[11px] leading-relaxed text-neutral-400">
              {m.guidance || t("(no guidance)")}
            </pre>
          </div>

          {(manifest.family || manifest.sensors.length > 0) && (
            <div className="text-[11px] text-neutral-500">
              {manifest.family && <span>{t("family")}: {manifest.family} · </span>}
              {t("sensors")}: {manifest.sensors.length ? manifest.sensors.join(", ") : t("(none)")}
            </div>
          )}

          {needsApproval && (
            <div className="flex items-center gap-2">
              <button onClick={approve} disabled={busy}
                className="rounded bg-amber-700 px-3 py-1 text-xs text-white hover:bg-amber-600 disabled:opacity-50">
                {busy ? t("Working…") : state === "changed" ? t("I have read it — approve again") : t("I have read it — approve")}
              </button>
              <span className="text-[11px] text-neutral-500">{t("Only approve nodes you trust")}</span>
            </div>
          )}
        </div>
      )}

      {note && <div className="mt-2 text-xs text-green-400">{note}</div>}
      {err && <div className="mt-2 text-xs text-red-400">{err}</div>}
    </div>
  );
}

/** Every launched body / tool node and where it stands with the operator. */
export function NodeTrustList({ nodes, onChanged }: { nodes: NodeInfo[]; onChanged?: () => void }) {
  const { t } = useI18n();
  const gated = nodes.filter((n) => n.kind === "body" || n.kind === "tool");
  return (
    <div className="space-y-2">
      {gated.map((n) => <NodeTrust key={n.key} node={n} onChanged={onChanged} />)}
      {gated.length === 0 && (
        <div className="rounded-lg border border-neutral-800 bg-neutral-900 p-3 text-xs text-neutral-500">
          {t("(no body or tool node launched yet — nodes start when a session needs them)")}
        </div>
      )}
    </div>
  );
}
