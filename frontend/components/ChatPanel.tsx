"use client";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { useI18n } from "@/lib/i18n";
import {
  armSession,
  getSession,
  imgUrl,
  interruptSession,
  setSessionBrain,
  streamChat,
  type Brain,
  type ChatEvent,
  type NodeInfo,
  type RecMsg,
  type SessionSummary,
} from "@/lib/api";
import TeleopPanel from "./TeleopPanel";

// 思考区的最大高度：长回合可能几十步，不限高的话思考会把最终回复顶出屏幕。
const THINKING_MAX_H = "max-h-72";

type ThinkStep = { text: string; tool_calls: { name: string; args: Record<string, unknown> }[]; tool_results: string[] };
type Input = { images: { name: string; src: string }[]; state: Record<string, unknown> };
type Turn = { user?: string; inputs: Input[]; thinking: ThinkStep[]; reply: string; brain?: string };
type Item = { kind: "turn"; turn: Turn } | { kind: "divider"; text: string };

// 会话记录（逐条）→ 回合 + 大脑切换分隔线。
// 分隔线两个来源：后端落盘的 brain_divider（切换时写的）和 assistant.brain 的变化（老记录没有 divider）。
// 同一次切换两边都会触发，所以用 lastBrain 去重。
function groupItems(msgs: RecMsg[], brainLabel: (n: string) => string, tt: (k: string, v?: Record<string, string | number>) => string): Item[] {
  const items: Item[] = [];
  let lastBrain: string | undefined;
  const divider = (brain: string) => {
    if (brain === lastBrain) return;
    items.push({
      kind: "divider",
      text: lastBrain === undefined ? tt("Session started with {brain}", { brain: brainLabel(brain) }) : tt("Switched to {brain}", { brain: brainLabel(brain) }),
    });
    lastBrain = brain;
  };
  const lastTurn = (): Turn => {
    const last = items[items.length - 1];
    if (last && last.kind === "turn" && !last.turn.reply) return last.turn;
    const turn: Turn = { inputs: [], thinking: [], reply: "" };
    items.push({ kind: "turn", turn });
    return turn;
  };
  for (const m of msgs) {
    if (m.role === "brain_divider") {
      divider(m.brain);
    } else if (m.role === "user") {
      items.push({ kind: "turn", turn: { user: m.text, inputs: [], thinking: [], reply: "" } });
    } else if (m.role === "perception") {
      const refs = m.images && m.images.length ? m.images : m.image_ref ? [{ name: "", ref: m.image_ref }] : [];
      lastTurn().inputs.push({ images: refs.map((r) => ({ name: r.name, src: imgUrl(r.ref) })), state: m.state });
    } else if (m.role === "assistant") {
      if (m.brain) divider(m.brain);
      const turn = lastTurn();
      if (m.brain) turn.brain = m.brain;
      if (m.tool_calls && m.tool_calls.length) {
        turn.thinking.push({ text: m.text, tool_calls: m.tool_calls.map((tc) => ({ name: tc.name, args: tc.arguments })), tool_results: [] });
      } else {
        turn.reply = m.text;
      }
    } else if (m.role === "tool") {
      const turn = lastTurn();
      const last = turn.thinking[turn.thinking.length - 1];
      if (last) last.tool_results.push(`${m.name}: ${m.content}`);
    }
  }
  return items;
}

const REPLY_CLASS =
  "inline-block max-w-[88%] rounded-2xl bg-neutral-800 px-3 py-2 text-sm " +
  "[&_p]:my-1 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0 [&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-5 " +
  "[&_ol]:my-1 [&_ol]:list-decimal [&_ol]:pl-5 [&_code]:rounded [&_code]:bg-neutral-900 [&_code]:px-1";

// 闸门拒绝行在结果区里的前缀：渲染时据此上色，别的都是普通结果。
const GATE_PREFIX = "⛔ ";

// 后端把操作员遥控的那几步记成这个 brain 名（hub.teleop_stream）。
const TELEOP_BRAIN = "operator";

function TurnView({ turn, open, live = false }: { turn: Turn; open: boolean; live?: boolean }) {
  const { t } = useI18n();
  const hasBody = turn.inputs.length > 0 || turn.thinking.length > 0 || turn.reply;
  const thinkRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (live && thinkRef.current) thinkRef.current.scrollTop = thinkRef.current.scrollHeight;
  }, [live, turn.thinking.length, turn.thinking[turn.thinking.length - 1]?.tool_results.length]);
  return (
    <div className="space-y-2">
      {turn.user && (
        <div className="text-right">
          <span className="inline-block max-w-[85%] rounded-2xl bg-blue-600 px-3 py-2 text-sm">{turn.user}</span>
        </div>
      )}
      {hasBody && (
        <div className="space-y-1 text-left">
          {turn.inputs.length > 0 && (
            <details open={open} className="rounded-lg bg-neutral-800/50 text-xs">
              <summary className="cursor-pointer px-3 py-1.5 text-neutral-400">{t("👁 Observation the brain received")}</summary>
              <div className="space-y-2 px-3 pb-2">
                {turn.inputs.map((inp, j) => (
                  <div key={j}>
                    <div className="flex flex-wrap gap-2">
                      {inp.images.map((im, k) => (
                        <figure key={k} className="relative">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={im.src} alt={im.name || t("perception frame")} className="max-h-40 rounded" />
                          {im.name && <figcaption className="absolute left-1 top-1 rounded bg-neutral-950/70 px-1 font-mono text-[9px] text-neutral-300">{im.name}</figcaption>}
                        </figure>
                      ))}
                    </div>
                    <pre className="mt-1 overflow-x-auto text-[10px] text-neutral-500">{JSON.stringify(inp.state)}</pre>
                  </div>
                ))}
              </div>
            </details>
          )}
          {turn.thinking.length > 0 && (
            <details open={open} className="rounded-lg bg-neutral-800/50 text-xs">
              <summary className="cursor-pointer px-3 py-1.5 text-neutral-400">
                {t("💭 Reasoning")} · {turn.thinking.length} {t("steps")}
              </summary>
              <div ref={thinkRef} className={`space-y-1 overflow-y-auto px-3 pb-2 text-neutral-400 ${THINKING_MAX_H}`}>
                {turn.thinking.map((th, j) => (
                  <div key={j} className="flex gap-1.5">
                    <span className="shrink-0 tabular-nums text-neutral-600">{j + 1}.</span>
                    <div className="min-w-0 flex-1">
                      {th.text && <div className="text-neutral-300">{th.text}</div>}
                      {th.tool_calls.map((tc, k) => (
                        <div key={k} className="text-[11px]">
                          {t("→ calls")} <code>{tc.name}</code>({JSON.stringify(tc.args)})
                        </div>
                      ))}
                      {th.tool_results.map((tr, k) => (
                        <div key={k} className={`text-[11px] ${tr.startsWith(GATE_PREFIX) ? "text-red-400" : "text-neutral-500"}`}>
                          　{tr.startsWith(GATE_PREFIX) ? "" : t("result: ")}{tr}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </details>
          )}
          {turn.reply && (
            <div className={REPLY_CLASS}>
              {/* ⭐ 回复过一次 t()：命中词条 = 这句是框架自己吐的（撞步数/时间上限、被叫停、大脑没配好），
                  显示译文；没命中 = 模型的回复，原样输出。 */}
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{t(turn.reply)}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StopIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor" aria-hidden="true">
      <rect width="10" height="10" rx="1.5" />
    </svg>
  );
}

function Divider({ text }: { text: string }) {
  return (
    <div className="my-1 flex items-center gap-2 text-[10px] text-neutral-500">
      <div className="h-px flex-1 bg-neutral-800" />
      <span className="shrink-0">{text}</span>
      <div className="h-px flex-1 bg-neutral-800" />
    </div>
  );
}

// 会话状态徽章。三个状态三种颜色，一眼分清。
export function StatusBadge({ status }: { status: SessionSummary["status"] }) {
  const { t } = useI18n();
  const cls =
    status === "active" ? "border-green-700/60 bg-green-950/40 text-green-300"
    : status === "frozen" ? "border-amber-700/60 bg-amber-950/40 text-amber-300"
    : "border-red-700/60 bg-red-950/40 text-red-300";
  const label = status === "active" ? t("active") : status === "frozen" ? t("frozen") : t("reconnect required");
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] ${cls}`}>{label}</span>;
}

const NOTES_MAX_H = "max-h-40";

// 大脑自己的两个状态寄存器，钉在对话顶上。⛔ 网页只做显示器、不提供编辑。
function Notebook({ session }: { session: SessionSummary | null }) {
  const { t } = useI18n();
  const task = session?.core_task ?? "";
  const notes = session?.notes ?? [];
  const [open, setOpen] = useState(false);
  if (!task && !notes.length) return null;
  return (
    <div className="border-b border-neutral-800 bg-neutral-900/40 px-3 py-1.5 text-[11px]">
      <button onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left text-neutral-500 hover:text-neutral-300"
        title={t("The brain writes these itself; they survive a long conversation. Read-only here — click to expand/collapse.")}>
        <span className={`transition-transform ${open ? "rotate-90" : ""}`}>›</span>
        {task ? (
          <span className="min-w-0 flex-1 truncate">
            <span className="text-neutral-600">{t("Doing")} </span>
            <span className="text-neutral-300">{task}</span>
          </span>
        ) : (
          <span className="flex-1 text-neutral-600">{t("Notebook")}</span>
        )}
        {!!notes.length && <span className="shrink-0 tabular-nums text-neutral-600">📓 {notes.length}</span>}
      </button>
      {open && (
        <div className={`mt-1.5 space-y-1 overflow-y-auto ${NOTES_MAX_H}`}>
          {task && (
            <div className="rounded bg-neutral-800/60 px-2 py-1 leading-snug text-neutral-300">
              <span className="text-neutral-600">{t("Core task")} </span>{task}
            </div>
          )}
          {!notes.length && <div className="text-neutral-600">{t("(no notes yet)")}</div>}
          {notes.map((n, i) => (
            <div key={i} className="flex gap-1.5 leading-snug">
              <span className="shrink-0 tabular-nums text-neutral-600">{i + 1}.</span>
              <span className="text-neutral-400">{n}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ChatPanel({
  session,
  brains,
  bodyNode,
  onSessionsChanged,
}: {
  session: SessionSummary | null;
  brains: Brain[];
  bodyNode: NodeInfo | null;
  onSessionsChanged: () => void;
}) {
  const { t } = useI18n();
  const [items, setItems] = useState<Item[]>([]);
  const [live, setLive] = useState<Turn | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [armBusy, setArmBusy] = useState(false);
  const [armErr, setArmErr] = useState("");
  const [teleop, setTeleop] = useState(false);
  const [expand, setExpand] = useState<"auto" | "all" | "none">(() => {
    if (typeof window === "undefined") return "auto";
    const v = new URLSearchParams(window.location.search).get("expand");
    return v === "all" || v === "none" ? v : "auto";
  });
  const bottomRef = useRef<HTMLDivElement>(null);
  const openFor = (isLive: boolean) => (expand === "all" ? true : expand === "none" ? false : isLive);

  // 遥控那几步在记录里的 brain 是 "operator"（后端约定），不是注册表里的大脑：给它一个人话标签。
  const brainLabel = useCallback(
    (n: string) => (n === TELEOP_BRAIN ? t("operator (teleop)") : brains.find((b) => b.name === n)?.label ?? n),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [brains],
  );

  const reload = useCallback(async () => {
    if (!session) {
      setItems([]);
      return;
    }
    const full = await getSession(session.id).catch(() => null);
    setItems(full && full.messages ? groupItems(full.messages, brainLabel, t) : []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id, brainLabel]);

  useEffect(() => {
    reload();
    setTeleop(false); // 换会话就退出遥控
  }, [reload]);
  useEffect(() => {
    if (!teleop) bottomRef.current?.scrollIntoView();
  }, [items, live, busy, teleop]);

  const active = session?.status === "active";
  const hasBody = !!session?.body;
  const curBrain = brains.find((b) => b.name === session?.brain);

  async function switchBrain(name: string) {
    if (!session) return;
    await setSessionBrain(session.id, name);
    onSessionsChanged();
  }

  async function toggleArm() {
    if (!session || armBusy || !hasBody || !active) return;
    if (!session.armed) {
      // 身体节点报的总线不是 zmq（仿真）= 后面是真机：上膛前问一句。
      const bus = bodyNode?.meta?.bus;
      if (bus !== "zmq" && !confirm(t("This body node reports a real bus ({bus}) — arming lets the hardware move. Arm it?", { bus: String(bus ?? "unknown") }))) return;
    }
    setArmBusy(true);
    setArmErr("");
    try {
      await armSession(session.id, !session.armed);
      onSessionsChanged();
    } catch (e) {
      setArmErr((e as Error).message);
    }
    setArmBusy(false);
  }

  // 遥控开关。关掉时刷新会话：操作员那几步已经记进会话，历史里要看得到。
  async function toggleTeleop() {
    if (!session || !hasBody || !active || busy) return;
    const next = !teleop;
    setTeleop(next);
    if (!next) {
      await reload();
      onSessionsChanged();
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || !session || !active || busy || teleop) return;
    setInput("");
    setBusy(true);
    const base: Turn = { user: text, inputs: [], thinking: [], reply: "" };
    setLive(base);
    // 不可变更新：每次都基于 prev 返回新 Turn，绝不在 updater 里 mutate（严格模式会跑两遍）。
    const upd = (fn: (t: Turn) => Turn) => setLive((prev) => fn(prev ?? base));
    const appendResult = (line: string) =>
      upd((turn) => {
        if (!turn.thinking.length) return { ...turn, thinking: [{ text: "", tool_calls: [], tool_results: [line] }] };
        const i = turn.thinking.length - 1;
        return { ...turn, thinking: turn.thinking.map((th, j) => (j === i ? { ...th, tool_results: [...th.tool_results, line] } : th)) };
      });
    try {
      await streamChat(session.id, text, (e: ChatEvent) => {
        if (e.type === "perception")
          upd((turn) => ({
            ...turn,
            inputs: [...turn.inputs, {
              images: e.image_b64 ? [{ name: (e.cameras ?? [])[0] ?? "", src: `data:image/png;base64,${e.image_b64}` }] : [],
              state: e.state,
            }],
          }));
        else if (e.type === "thinking")
          upd((turn) => ({ ...turn, thinking: [...turn.thinking, { text: e.text, tool_calls: [], tool_results: [] }] }));
        else if (e.type === "tool_call")
          upd((turn) => ({ ...turn, thinking: [...turn.thinking, { text: "", tool_calls: [{ name: e.name, args: e.args }], tool_results: [] }] }));
        else if (e.type === "gate") {
          // 安全门：只有拒绝才值得一行。放行是常态，刷一行"ok"只会淹没真正的信息。
          if (!e.allowed) appendResult(`${GATE_PREFIX}${t("gate refused {name}", { name: e.name })}: ${t(e.reason)}`);
        } else if (e.type === "progress") appendResult(`⏳ ${e.message}`);
        else if (e.type === "tool_result") appendResult(`${e.name}: ${e.message}`);
        else if (e.type === "reply") upd((turn) => ({ ...turn, reply: e.text }));
      });
    } catch (e) {
      upd((turn) => ({ ...turn, reply: `${t("(cannot reach the backend)")} ${(e as Error).message ?? ""}` }));
    } finally {
      await reload();
      setLive(null);
      setBusy(false);
      setStopping(false);
      onSessionsChanged();
    }
  }

  // 停止这一轮。只是给后端置个叫停旗标——它会把当前这一步做完再礼貌收尾。
  async function stop() {
    if (!session || !busy || stopping) return;
    setStopping(true);
    await interruptSession(session.id).catch(() => setStopping(false));
  }

  return (
    <aside className="flex h-screen flex-col border-l border-neutral-800 bg-neutral-900">
      <header className="border-b border-neutral-800 p-3">
        <div className="flex items-center justify-between text-xs">
          <span className="font-medium text-neutral-200">{t("Talk to the brain")}</span>
          <span className="flex items-center gap-2">
            {(items.length > 0 || live) && !teleop && (
              <button onClick={() => setExpand(expand === "all" ? "none" : "all")}
                title={t("Expand / collapse the reasoning of every turn")}
                className="rounded border border-neutral-700 px-1.5 py-0.5 text-[10px] text-neutral-400 hover:border-neutral-500">
                {expand === "all" ? t("Collapse reasoning") : t("Expand reasoning")}
              </button>
            )}
            {session && <StatusBadge status={session.status} />}
          </span>
        </div>
        {session && (
          <div className="mt-1.5 truncate text-[11px] text-neutral-400" title={curBrain ? `${t(curBrain.vendor)} · ${curBrain.model}` : undefined}>
            {/* 会话 = 大脑 × 身体 × 世界。三样都在这一行。 */}
            {session.world ? `🌍 ${session.world} · 🤖 ${session.body}` : t("Conversation only")} · 🧠 {t(brainLabel(session.brain))}
          </div>
        )}
      </header>

      <Notebook session={session} />

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {!session && (
          <div className="p-4 text-center text-xs text-neutral-500">{t("Create or pick a session on the left")}</div>
        )}
        {session && teleop ? (
          <TeleopPanel sessionId={session.id} armed={session.armed} hasWorld={!!session.world} />
        ) : (
          <>
            {items.map((it, i) => (
              <Fragment key={i}>
                {it.kind === "divider" ? <Divider text={it.text} /> : <TurnView turn={it.turn} open={openFor(false)} />}
              </Fragment>
            ))}
            {live && <TurnView turn={live} open={openFor(true)} live />}
            {busy && !live?.reply && (
              <div className="text-xs text-neutral-500">
                {stopping ? t("Wrapping up — it will stop once this step finishes…") : t("The brain is thinking…")}
              </div>
            )}
            <div ref={bottomRef} />
          </>
        )}
      </div>

      {session && (
        <div className="border-t border-neutral-800">
          {/* ---- 一行三个控件：大脑 · 上膛 · 遥控 ---- */}
          <div className="flex items-center gap-2 px-3 pt-2">
            <BrainPicker brains={brains} current={session.brain} disabled={!active || busy} onPick={switchBrain} />
            {hasBody && (
              <button onClick={toggleArm} disabled={armBusy || !active} role="switch" aria-checked={session.armed}
                title={session.armed
                  ? t("Armed: the body may move. Click to disarm.")
                  : t("Disarmed: every world-changing action is refused at the gate. Click to arm — only you can.")}
                className={`rounded-full border px-2.5 py-0.5 text-[10px] font-semibold tracking-wide transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                  session.armed
                    ? "border-red-500 bg-red-600 text-white hover:bg-red-500"
                    : "border-neutral-600 bg-neutral-800 text-neutral-400 hover:border-neutral-400"
                }`}>
                {session.armed ? `● ${t("ARMED")}` : `○ ${t("DISARMED")}`}
              </button>
            )}
            {hasBody && (
              <button onClick={toggleTeleop} disabled={!active || busy} aria-pressed={teleop}
                title={teleop ? t("Leave remote control (the brain resumes; your steps are in the history)") : t("Remote control: drive the body yourself; the brain is paused")}
                className={`ml-auto flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[10px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                  teleop ? "border-blue-500 bg-blue-600 text-white" : "border-neutral-600 bg-neutral-800 text-neutral-300 hover:border-neutral-400"
                }`}>
                🎮 {t("Teleop")}
              </button>
            )}
          </div>
          {armErr && <div className="px-3 pt-1 text-[11px] text-red-400">{armErr}</div>}

          {!active ? (
            <div className="p-4 text-center text-xs text-neutral-500">
              {session.status === "frozen"
                ? t("🔒 This session is frozen and read-only. Create a new one to continue.")
                : t("⚠ The world node restarted under this session (different physics now). Start a new session to continue.")}
              {session.status === "frozen" && (
                <span className="group relative ml-1 cursor-help text-neutral-400">
                  ❓
                  <span className="pointer-events-none invisible absolute bottom-full left-1/2 z-10 mb-1 w-64 -translate-x-1/2 rounded-lg bg-neutral-800 p-2 text-left text-[11px] leading-relaxed text-neutral-300 opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100">
                    {t("A safety rule for hardware: one active session per body. Opening a new session on the same body locks the previous one — you can still read its history, but it no longer perceives and cannot command the body.")}
                  </span>
                </span>
              )}
            </div>
          ) : (
            <div className="p-3">
              {teleop && (
                <div className="mb-1.5 text-[10px] leading-snug text-neutral-500">
                  {t("teleop mode — the brain is paused; your actions are recorded in the session so the brain sees them next turn")}
                </div>
              )}
              <div className="flex gap-2">
                <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} disabled={teleop}
                  placeholder={teleop ? t("teleop mode — chat is paused") : busy ? t("Wait for this turn to finish…") : t("Give the brain an instruction…")}
                  className="flex-1 rounded-xl bg-neutral-800 px-3 py-2 text-sm outline-none placeholder:text-neutral-500 disabled:opacity-50" />
                {busy ? (
                  <button onClick={stop} disabled={stopping}
                    title={t("Stop this turn (it finishes the current step; say “continue” to resume)")}
                    className="flex items-center gap-1.5 rounded-xl bg-neutral-700 px-4 py-2 text-sm font-medium disabled:opacity-60">
                    <StopIcon />
                    {stopping ? t("Stopping…") : t("Stop")}
                  </button>
                ) : (
                  <button onClick={send} disabled={teleop} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{t("Send")}</button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

// 🧠 + 当前大脑；点开一个小浮层列出所有大脑，没配好的灰掉。
function BrainPicker({ brains, current, disabled, onPick }: { brains: Brain[]; current: string; disabled: boolean; onPick: (name: string) => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const cur = brains.find((b) => b.name === current);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative min-w-0">
      <button onClick={() => setOpen((v) => !v)} disabled={disabled} aria-haspopup="listbox" aria-expanded={open}
        title={cur ? `${t(cur.vendor)} · ${cur.model}` : t("Model")}
        className="flex max-w-44 items-center gap-1 rounded-full border border-neutral-600 bg-neutral-800 px-2.5 py-0.5 text-[10px] text-neutral-300 hover:border-neutral-400 disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
        🧠 <span className="truncate">{cur ? t(cur.label) : current}</span>
        <span aria-hidden="true" className="text-neutral-500">▾</span>
      </button>
      {open && (
        <ul role="listbox" aria-label={t("Model")}
          className="absolute bottom-full left-0 z-30 mb-1 w-56 rounded-lg border border-neutral-700 bg-neutral-900 p-1 shadow-xl">
          {brains.map((b) => (
            <li key={b.name} role="option" aria-selected={b.name === current}>
              <button disabled={!b.available} onClick={() => { setOpen(false); if (b.name !== current) onPick(b.name); }}
                title={b.available ? `${t(b.vendor)} · ${b.model}` : t("not configured")}
                className={`flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[11px] hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40 ${
                  b.name === current ? "text-blue-300" : "text-neutral-200"
                }`}>
                <span className="w-3 shrink-0">{b.name === current ? "✓" : ""}</span>
                <span className="min-w-0 flex-1 truncate">{t(b.label)}</span>
                <span className="shrink-0 text-[9px] text-neutral-500">{b.available ? b.hosting : t("not configured")}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
