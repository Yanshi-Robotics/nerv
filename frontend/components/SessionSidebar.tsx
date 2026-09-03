"use client";
import { useMemo, useState } from "react";

import { createSession, deleteSession, type Registry, type SessionSummary } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { StatusBadge } from "./ChatPanel";
import LangToggle from "./LangToggle";
import ThemeToggle from "./ThemeToggle";

const CONVERSATION_ONLY = ""; // world 选中值空串 = 只聊天，不起身体、不起世界

export default function SessionSidebar({
  sessions,
  registry,
  currentId,
  onSelect,
  onChanged,
  onHome,
  onOpenPanel,
}: {
  sessions: SessionSummary[];
  registry: Registry | null;
  currentId: string;
  onSelect: (id: string) => void;
  onChanged: (id?: string) => void;
  onHome: () => void;
  onOpenPanel: (p: "nerv" | "logs") => void;
}) {
  const { t } = useI18n();
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [world, setWorld] = useState(CONVERSATION_ONLY);
  const [body, setBody] = useState("");
  const [brain, setBrain] = useState("");
  const [sensors, setSensors] = useState<string[]>([]);

  const worlds = registry?.worlds ?? [];
  const bodies = registry?.bodies ?? [];
  const brains = registry?.brains ?? [];
  const worldSpec = worlds.find((w) => w.name === world) ?? null;

  // 这个世界容得下哪些身体：注册表的兼容矩阵说了算——界面灰掉的，就是后端会拒绝的。
  const compat = useMemo(() => {
    const m = new Map<string, { ok: boolean; reason: string }>();
    for (const c of registry?.matrix ?? []) if (c.world === world) m.set(c.body, { ok: c.ok, reason: c.reason });
    return m;
  }, [registry, world]);

  function openForm() {
    setWorld(CONVERSATION_ONLY);
    setBody("");
    setSensors([]);
    setBrain(registry?.default_brain ?? brains.find((b) => b.available)?.name ?? brains[0]?.name ?? "");
    setError("");
    setCreating(true);
  }

  function pickWorld(name: string) {
    setWorld(name);
    setSensors([]);
    // 默认选第一个这个世界支持的身体；没有就留空，让用户看到全灰的下拉和原因。
    const m = new Map<string, boolean>();
    for (const c of registry?.matrix ?? []) if (c.world === name) m.set(c.body, c.ok);
    setBody(bodies.find((b) => m.get(b.name))?.name ?? "");
  }

  async function doCreate() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await createSession({
        brain: brain || undefined,
        body: world ? body || null : null,
        world: world || null,
        sensors: world ? sensors : [],
      });
      setCreating(false);
      onChanged(created.id);
    } catch (e) {
      // 后端的 400 detail 就是给人看的那句话（注册表拒绝这对组合 / 节点起不来）：原样显示。
      setError((e as Error).message);
    }
    setBusy(false);
  }

  async function doDelete(id: string) {
    if (!confirm(t("Delete this session? This cannot be undone."))) return;
    await deleteSession(id);
    onChanged();
  }

  const selectCls = "w-full rounded-md border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs text-neutral-200 disabled:opacity-50";

  return (
    <aside className="flex h-screen flex-col border-r border-neutral-800 bg-neutral-900">
      <button onClick={onHome} title={t("Back to home")}
        className="flex items-center gap-2 border-b border-neutral-800 px-3 py-2.5 text-sm font-semibold text-neutral-200 transition-colors hover:bg-neutral-800">
        <HomeIcon />
        <span>NERV</span>
        <span className="ml-auto text-[11px] font-normal text-neutral-500">{t("Home")}</span>
      </button>
      <div className="border-b border-neutral-800 p-3">
        <button onClick={openForm} className="w-full rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium">
          + {t("New session")}
        </button>
      </div>

      {creating && (
        <div className="space-y-3 border-b border-neutral-800 p-3 text-xs">
          <div className="text-[11px] text-neutral-500">{t("A session is brain × body × world.")}</div>

          <label className="block">
            <div className="mb-1 text-neutral-400">{t("World")}</div>
            <select value={world} onChange={(e) => pickWorld(e.target.value)} className={selectCls}>
              <option value={CONVERSATION_ONLY}>{t("— conversation only (no body, no world) —")}</option>
              {worlds.map((w) => (
                <option key={w.name} value={w.name}>{w.label || w.name} · {w.kind}</option>
              ))}
            </select>
          </label>

          {world && (
            <label className="block">
              <div className="mb-1 text-neutral-400">{t("Body")}</div>
              <select value={body} onChange={(e) => setBody(e.target.value)} className={selectCls}>
                {!body && <option value="">{t("— pick a body —")}</option>}
                {bodies.map((b) => {
                  const c = compat.get(b.name);
                  const ok = c?.ok ?? false;
                  return (
                    <option key={b.name} value={b.name} disabled={!ok} title={ok ? undefined : c?.reason}>
                      {b.label || b.name} · {b.family}{ok ? "" : ` — ${t("not supported here")}`}
                    </option>
                  );
                })}
              </select>
              {body && !compat.get(body)?.ok && <div className="mt-1 text-[11px] text-amber-400">{compat.get(body)?.reason}</div>}
              {/* 灰掉的原因：<option> 的 title 大多数浏览器不显示，所以把原因也列在下面。 */}
              {bodies.some((b) => !compat.get(b.name)?.ok) && (
                <details className="mt-1 text-[10px] text-neutral-500">
                  <summary className="cursor-pointer">{t("why some bodies are greyed out")}</summary>
                  <ul className="mt-0.5 space-y-0.5">
                    {bodies.filter((b) => !compat.get(b.name)?.ok).map((b) => (
                      <li key={b.name}><span className="font-mono">{b.name}</span>: {compat.get(b.name)?.reason ?? t("(not in the matrix)")}</li>
                    ))}
                  </ul>
                </details>
              )}
            </label>
          )}

          {worldSpec && worldSpec.ambient.length > 0 && (
            <div>
              <div className="mb-1 text-neutral-400">{t("Ambient sensors the brain also sees")}</div>
              <div className="space-y-0.5">
                {worldSpec.ambient.map((s) => (
                  <label key={s.name} className="flex items-center gap-1.5 text-neutral-300" title={s.description || undefined}>
                    <input type="checkbox" checked={sensors.includes(s.name)}
                      onChange={(e) => setSensors((cur) => (e.target.checked ? [...cur, s.name] : cur.filter((x) => x !== s.name)))} />
                    <span className="font-mono">{s.name}</span>
                    {s.description && <span className="truncate text-[10px] text-neutral-500">{s.description}</span>}
                  </label>
                ))}
              </div>
            </div>
          )}

          <label className="block">
            <div className="mb-1 text-neutral-400">{t("Brain")}</div>
            <select value={brain} onChange={(e) => setBrain(e.target.value)} className={selectCls}>
              {brains.map((b) => (
                <option key={b.name} value={b.name}>
                  {t(b.label)}{b.available ? "" : ` (${t("not configured")})`}
                </option>
              ))}
            </select>
          </label>

          {error && (
            <div className="rounded-md border border-red-700/60 bg-red-950/40 p-2 text-[11px] leading-relaxed text-red-300">
              {t("Refused:")} {error}
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <button onClick={doCreate} disabled={busy || (!!world && !body)} className="rounded-lg bg-blue-600 px-3 py-1.5 disabled:opacity-50">
              {busy ? (world ? t("Launching nodes…") : t("Creating…")) : t("Create session")}
            </button>
            <button onClick={() => setCreating(false)} disabled={busy} className="rounded-lg bg-neutral-700 px-3 py-1.5">
              {t("Cancel")}
            </button>
          </div>
        </div>
      )}

      <div className="min-h-24 flex-1 overflow-y-auto p-2">
        {sessions.length === 0 && (
          <div className="p-3 text-xs text-neutral-500">{t("No sessions yet — create one above.")}</div>
        )}
        {sessions.map((s) => (
          <div key={s.id} className={`group mb-1 flex items-start rounded-lg ${s.id === currentId ? "bg-neutral-800" : "hover:bg-neutral-800/50"}`}>
            <button onClick={() => onSelect(s.id)} className="min-w-0 flex-1 rounded-lg p-2 text-left text-xs">
              <div className="flex items-center justify-between gap-1">
                <span className="truncate font-medium">{s.title}</span>
                {s.status !== "active" && <StatusBadge status={s.status} />}
              </div>
              <div className="mt-0.5 flex items-center gap-1 text-[10px] text-neutral-500">
                {s.armed && <span className="text-red-400" title={t("armed")}>●</span>}
                <span className="truncate">{s.world ? `${s.world} / ${s.body}` : t("Conversation only")} · {s.brain}</span>
              </div>
            </button>
            <button onClick={() => doDelete(s.id)} title={t("Delete session")}
              className="mr-1 mt-1 shrink-0 rounded px-1.5 py-1 text-[11px] text-neutral-600 opacity-0 hover:bg-neutral-700 hover:text-red-400 group-hover:opacity-100">
              ✕
            </button>
          </div>
        ))}
      </div>

      <div className="shrink-0 space-y-0.5 border-t border-neutral-800 p-2">
        <button onClick={() => onOpenPanel("nerv")}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs text-neutral-300 transition-colors hover:bg-neutral-800 hover:text-neutral-100">
          <DashboardIcon />
          <span>{t("NERV dashboard")}</span>
          <span className="ml-auto text-neutral-600">›</span>
        </button>
        <button onClick={() => onOpenPanel("logs")}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs text-neutral-300 transition-colors hover:bg-neutral-800 hover:text-neutral-100">
          <LogsIcon />
          <span>Session Logs</span>
          <span className="ml-auto text-neutral-600">›</span>
        </button>
        <div className="mt-1 flex items-center justify-end gap-1.5 border-t border-neutral-800 px-2 pt-2">
          <LangToggle />
          <ThemeToggle />
        </div>
      </div>
    </aside>
  );
}

function HomeIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 9.5V20h14V9.5" />
    </svg>
  );
}

function DashboardIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="7" height="9" rx="1" />
      <rect x="14" y="3" width="7" height="5" rx="1" />
      <rect x="14" y="12" width="7" height="9" rx="1" />
      <rect x="3" y="16" width="7" height="5" rx="1" />
    </svg>
  );
}

function LogsIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 6h16M4 12h16M4 18h10" />
    </svg>
  );
}
