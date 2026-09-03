"use client";
import { useEffect, useRef, useState } from "react";

import { useI18n } from "@/lib/i18n";
import LangToggle from "./LangToggle";
import { getSessionLogs, listSessions, POLL_SESSION_LOGS_MS, type SessionSummary, type SignalEntry } from "@/lib/api";

// 六类信号的标签与色点。⛔ 返回的是英文原文（= 词条 key），显示处再过一次 t()。
function kindTag(kind: string): { tag: string; dot: string } {
  if (kind === "llm_call") return { tag: "LLM", dot: "bg-purple-400" };
  if (kind === "body_call") return { tag: "Body", dot: "bg-sky-400" };
  if (kind === "world_call") return { tag: "World", dot: "bg-teal-400" };
  if (kind === "tool_call") return { tag: "Tool", dot: "bg-emerald-400" };
  if (kind === "gate") return { tag: "Gate", dot: "bg-amber-400" };
  if (kind === "operator") return { tag: "Operator", dot: "bg-neutral-300" };
  return { tag: kind || "Other", dot: "bg-neutral-500" };
}

// 把一条流水拍平成可读文本（一键复制用）。
// ⛔ 这一份有意保持英文正典、不跟界面语言走：它是要被复制出去的诊断文本。
function fmtEntry(e: SignalEntry): string {
  const head = `#${e.id}  ${e.ts}  [${kindTag(e.kind).tag}]`;
  const sess = `session: ${e.session || "(none)"}`;
  if (e.kind === "llm_call") {
    const tok = e.tokens ? `in ${e.tokens.input} / out ${e.tokens.output} / total ${e.tokens.total}` : "(none)";
    return [
      `${head}  ${e.model}`, sess,
      `context ${e.n_history} · tools ${e.n_tools} · ${e.has_image ? "with image" : "no image"} · ${e.ms}ms`,
      `tokens: ${tok}`, `user: ${e.last_user || "(none)"}`, `reply: ${e.reply || "(none)"}`,
      `tool calls: ${e.tool_calls.length ? e.tool_calls.join(", ") : "(none)"}`,
      e.error ? `error: ${e.error}` : "", `system prompt:\n${e.system}`,
    ].filter(Boolean).join("\n");
  }
  if (e.kind === "gate") {
    return [`${head}  ${e.tool} · origin ${e.origin} · ${e.armed ? "armed" : "disarmed"}`, sess,
      `decision: ${e.allowed ? "allowed" : "refused"}${e.reason ? ` — ${e.reason}` : ""}`].join("\n");
  }
  if (e.kind === "operator") {
    const { id: _i, t: _t, ts: _ts, session: _s, kind: _k, event, ...rest } = e;
    return [`${head}  ${event}`, sess, JSON.stringify(rest)].join("\n");
  }
  return [`${head}  ${e.node} · ${e.method} · ${e.ms}ms`, sess, `sent: ${e.summary}`, `returned: ${JSON.stringify(e.resp)}`].join("\n");
}

const ALL = "";

// Session Logs：一个会话的全部信号流水，按时间合并。默认选当前会话，没日志就退到第一个有日志的。
export default function SessionLogsView({ embedded = false, sessionId = "" }: { embedded?: boolean; sessionId?: string }) {
  const { t } = useI18n();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [logged, setLogged] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string>(sessionId || ALL);
  const [entries, setEntries] = useState<SignalEntry[]>([]);
  const termRef = useRef<HTMLDivElement>(null);
  const resolvedRef = useRef(false);
  const [copied, setCopied] = useState(false);

  const copyAll = async () => {
    if (entries.length === 0) return;
    const text = entries.map(fmtEntry).join("\n\n" + "─".repeat(40) + "\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 非 https / 旧浏览器无 clipboard 权限：不崩 */
    }
  };

  useEffect(() => {
    const load = async () => {
      const [ss, logs] = await Promise.all([
        listSessions().catch(() => [] as SessionSummary[]),
        getSessionLogs(500, selected).catch(() => ({ entries: [], sessions: [] as string[] })),
      ]);
      setSessions(ss);
      setLogged(new Set(logs.sessions));
      setEntries(logs.entries);
    };
    load();
    const id = setInterval(load, POLL_SESSION_LOGS_MS);
    return () => clearInterval(id);
  }, [selected]);

  useEffect(() => {
    if (resolvedRef.current) return;
    if (sessions.length === 0 && logged.size === 0) return;
    resolvedRef.current = true;
    const has = (id: string) => logged.has(id);
    const def = sessionId && has(sessionId) ? sessionId : sessions.find((s) => has(s.id))?.id ?? [...logged][0] ?? sessionId ?? ALL;
    if (def !== selected) setSelected(def);
  }, [sessions, logged, sessionId, selected]);

  useEffect(() => {
    termRef.current?.scrollTo(0, termRef.current.scrollHeight);
  }, [entries]);

  const cur = sessions.find((s) => s.id === selected);
  const orphanLogged = [...logged].filter((id) => !sessions.some((s) => s.id === id));
  const where = (s: SessionSummary) => (s.world ? `${s.world} / ${s.body}` : t("Conversation only"));

  return (
    <main className={`flex min-h-0 min-w-0 flex-col ${embedded ? "h-full" : "h-screen"} bg-neutral-950 text-neutral-200`}>
      <div className="shrink-0 border-b border-neutral-800 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-neutral-500">Session Logs · {t("Session: ")}</span>
          <select value={selected} onChange={(e) => { resolvedRef.current = true; setSelected(e.target.value); }}
            className="rounded-md border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs text-neutral-200">
            {sessions.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
            {orphanLogged.map((id) => <option key={id} value={id}>{t("deleted")} {id.slice(0, 12)}…</option>)}
            <option value={ALL}>{t("All (every session merged)")}</option>
          </select>
          <span className="text-[11px] text-neutral-500">
            {selected === ALL ? `${entries.length} ${t("entries")}` : (cur ? `${where(cur)} · ${cur.brain} · ` : "") + `${entries.length} ${t("entries")}`}
          </span>
          <button onClick={copyAll} disabled={entries.length === 0} title={t("Copy every listed log entry (all fields) to the clipboard")}
            className="ml-auto rounded-md border border-neutral-700 bg-neutral-800 px-2 py-1 text-[11px] text-neutral-200 hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-40">
            {copied ? t("Copied ✓") : t("Copy all logs")}
          </button>
          {!embedded && <LangToggle />}
        </div>
        <div className="flex items-baseline gap-2">
          <h1 className="truncate text-sm font-semibold">
            {selected === ALL ? t("All activity (every session merged)") : cur ? cur.title : selected ? `${t("Session: ")}${selected.slice(0, 12)}…` : t("(no session selected)")}
          </h1>
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-neutral-500">
          {t("Everything this session did, merged by time:")}
          <span className="mx-1 inline-block h-2 w-2 rounded-full bg-purple-400" />{t("LLM calls (thinking)")} ·
          <span className="mx-1 inline-block h-2 w-2 rounded-full bg-sky-400" />{t("body calls (perceive / act)")} ·
          <span className="mx-1 inline-block h-2 w-2 rounded-full bg-teal-400" />{t("world calls (sensors / status)")} ·
          <span className="mx-1 inline-block h-2 w-2 rounded-full bg-emerald-400" />{t("tool calls (asking an advisor)")} ·
          <span className="mx-1 inline-block h-2 w-2 rounded-full bg-amber-400" />{t("gate decisions")} ·
          <span className="mx-1 inline-block h-2 w-2 rounded-full bg-neutral-300" />{t("operator actions")}
          {t(" — one chain showing what the brain saw, thought, and called.")}
        </p>
      </div>

      <div ref={termRef} className="min-h-0 flex-1 overflow-y-auto bg-neutral-950 p-3">
        {entries.length === 0 ? (
          <div className="text-xs text-neutral-600">
            {selected === ALL ? t("(No signals yet. Send a message on the main screen and the whole chain shows up here.)") : t("(This session has no signals yet.)")}
          </div>
        ) : (
          entries.map((e) => {
            const src = kindTag(e.kind);
            const key = `${e.kind}-${e.id}-${e.t ?? e.ts}`;
            if (e.kind === "gate") {
              return (
                <div key={key} className={`mb-1.5 rounded-md border px-2.5 py-1.5 ${e.allowed ? "border-neutral-800/70 bg-neutral-900/30" : "border-red-900/60 bg-red-950/20"}`}>
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
                    <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${src.dot}`} />
                    <span className="font-medium text-neutral-300">{t(src.tag)}</span>
                    <span className="text-neutral-600">#{e.id}</span>
                    <span className="text-neutral-500">{e.ts}</span>
                    <span className="font-mono text-neutral-300">{e.tool}</span>
                    <span className="text-neutral-500">{e.origin} · {e.armed ? t("armed") : t("disarmed")}</span>
                    <span className={e.allowed ? "text-green-400" : "text-red-400"}>{e.allowed ? `✓ ${t("allowed")}` : `✗ ${t("refused")}`}</span>
                    {e.reason && <span className="text-neutral-400">{t(e.reason)}</span>}
                    {selected === ALL && e.session && <span className="text-neutral-600">·{e.session.slice(0, 8)}</span>}
                  </div>
                </div>
              );
            }
            if (e.kind === "operator") {
              const { id: _i, t: _t, ts: _ts, session: _s, kind: _k, event, ...rest } = e;
              return (
                <div key={key} className="mb-1.5 rounded-md border border-neutral-800/70 bg-neutral-900/30 px-2.5 py-1.5">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
                    <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${src.dot}`} />
                    <span className="font-medium text-neutral-300">{t(src.tag)}</span>
                    <span className="text-neutral-600">#{e.id}</span>
                    <span className="text-neutral-500">{e.ts}</span>
                    <span className="font-mono text-neutral-300">{event}</span>
                    <span className="truncate text-neutral-500">{JSON.stringify(rest)}</span>
                  </div>
                </div>
              );
            }
            if (e.kind !== "llm_call") {
              return (
                <div key={key} className="mb-1.5 rounded-md border border-neutral-800/70 bg-neutral-900/30 px-2.5 py-1.5">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
                    <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${src.dot}`} />
                    <span className="font-medium text-neutral-300">{t(src.tag)}</span>
                    <span className="text-neutral-600">#{e.id}</span>
                    <span className="text-neutral-500">{e.ts}</span>
                    <span className="text-neutral-400">{e.node}</span>
                    <span className="font-mono text-neutral-300">{e.method} {e.summary}</span>
                    {selected === ALL && e.session && <span className="text-neutral-600">·{e.session.slice(0, 8)}</span>}
                    <span className="ml-auto text-neutral-600">{e.ms}ms</span>
                  </div>
                  <details className="mt-0.5">
                    <summary className="cursor-pointer text-[10px] text-neutral-600">{t("returned")}</summary>
                    <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded border border-neutral-800 bg-neutral-950 p-2 font-mono text-[10px] leading-relaxed text-neutral-500">
                      {JSON.stringify(e.resp, null, 2)}
                    </pre>
                  </details>
                </div>
              );
            }
            return (
              <div key={key} className="mb-2 rounded-lg border border-neutral-800 bg-neutral-900/50 p-2.5">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
                  <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${src.dot}`} />
                  <span className="font-medium text-neutral-300">{src.tag}</span>
                  <span className="text-neutral-600">#{e.id}</span>
                  <span className="text-neutral-500">{e.ts}</span>
                  <span className="text-neutral-400">{e.model}</span>
                  {selected === ALL && e.session && <span className="text-neutral-600">·{e.session.slice(0, 8)}</span>}
                  <span className="ml-auto text-neutral-600">
                    {t("context")} {e.n_history} · {t("tools")} {e.n_tools}{e.has_image ? ` · ${t("with image")}` : ""} · {e.ms}ms
                  </span>
                  {e.error && <span className="w-full text-rose-500">✗ {e.error}</span>}
                </div>
                {e.last_user && (
                  <div className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed">
                    <span className="text-neutral-500">{t("user:")}</span> <span className="text-neutral-300">{e.last_user}</span>
                  </div>
                )}
                {e.reply && (
                  <div className="mt-1 whitespace-pre-wrap text-xs leading-relaxed">
                    <span className="text-neutral-500">{t("reply:")}</span> <span className="text-neutral-100">{e.reply}</span>
                  </div>
                )}
                {e.tool_calls.length > 0 && (
                  <div className="mt-1 text-xs leading-relaxed">
                    <span className="text-neutral-500">{t("tool calls:")}</span> <span className="font-mono text-neutral-200">{e.tool_calls.join(", ")}</span>
                  </div>
                )}
                {e.tokens && (
                  <div className="mt-1 text-[11px] text-neutral-500">
                    tokens: {t("in")} {e.tokens.input} · {t("out")} {e.tokens.output} · {t("total")} {e.tokens.total}
                  </div>
                )}
                <details className="mt-1.5">
                  <summary className="cursor-pointer text-[10px] text-neutral-500">{t("system prompt (full)")}</summary>
                  <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded border border-neutral-800 bg-neutral-950 p-2 font-mono text-[10px] leading-relaxed text-neutral-500">
                    {e.system}
                  </pre>
                </details>
              </div>
            );
          })
        )}
      </div>
    </main>
  );
}
