"use client";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useI18n } from "@/lib/i18n";
import {
  getPerception,
  getSessionTools,
  interruptSession,
  streamTeleop,
  type ChatEvent,
  type JsonSchema,
  type ToolSheetEntry,
} from "@/lib/api";

/**
 * Remote control. The operator drives the body (or a tool node) directly, one call at a time,
 * through exactly the path the brain would take: same gate, same nodes, same log. Every step is
 * recorded in the session as the operator's, so the brain sees it next turn.
 *
 * ⛔ The panel knows no body. Every card and every field is generated from the JSON schema the
 *    tool sheet carries (GET /api/sessions/{sid}/tools) — a body that declares a new verb shows up
 *    here with no change on this side.
 *
 * 遥控面板。操作员直接调身体的动词（或工具节点的函数），一次一个，走的和大脑完全同一条路：
 * 同一道闸门、同一批节点、同一份日志；每一步都记进会话，大脑下一回合看得到。
 * ⛔ 面板不认识任何身体：每张卡、每个字段都从工具单带的 JSON Schema 生成。
 */

// 人形三个大键对应的动词名——不是硬编码身体，只是"工具单里若有这三个就给大键"的约定；
// 走多远 / 转多少全部取各自 schema 的 default。
const QUICK = { forward: "move_forward", left: "turn_left", right: "turn_right" } as const;

// 观察状态里关节角字典的键名（arm 家族给的是 joints_deg）；有它就用它的键当 move_joints.targets 的候选。
const JOINTS_STATE_KEY = "joints_deg";

// 卡片下面的一行输出
type Line = { kind: "ok" | "bad" | "info" | "progress"; text: string };

type Group = { key: string; label: string; tools: ToolSheetEntry[] };

export default function TeleopPanel({ sessionId, armed }: { sessionId: string; armed: boolean }) {
  const { t } = useI18n();
  const [tools, setTools] = useState<ToolSheetEntry[] | null>(null);
  const [err, setErr] = useState("");
  const [jointKeys, setJointKeys] = useState<string[]>([]);
  const [running, setRunning] = useState<string | null>(null); // 正在跑的工具名；一次只跑一个
  const [lines, setLines] = useState<Record<string, Line[]>>({});
  const [stopping, setStopping] = useState(false);

  // 工具单 + 一次观察（拿关节名做 targets 的候选键）
  useEffect(() => {
    let gone = false;
    setTools(null);
    setErr("");
    setLines({});
    getSessionTools(sessionId)
      .then((ts) => !gone && setTools(ts))
      .catch((e) => !gone && setErr((e as Error).message));
    getPerception(sessionId)
      .then((p) => {
        const j = p.state?.[JOINTS_STATE_KEY];
        if (!gone && j && typeof j === "object" && !Array.isArray(j)) setJointKeys(Object.keys(j as Record<string, unknown>));
      })
      .catch(() => {});
    return () => {
      gone = true;
    };
  }, [sessionId]);

  const groups = useMemo<Group[]>(() => {
    const ts = tools ?? [];
    const body = (kind: string) => ts.filter((x) => x.origin === "body" && x.kind === kind);
    return [
      { key: "skills", label: t("Body · skills"), tools: body("skill") },
      { key: "primitives", label: t("Body · primitives"), tools: body("primitive") },
      { key: "read", label: t("Body · read"), tools: body("read") },
      { key: "tools", label: t("Tools"), tools: ts.filter((x) => x.origin === "tool") },
    ].filter((g) => g.tools.length > 0);
  }, [tools, t]);

  const byName = useCallback((name: string) => tools?.find((x) => x.name === name) ?? null, [tools]);

  const run = useCallback(
    async (name: string, args: Record<string, unknown>) => {
      if (running) return;
      setRunning(name);
      setStopping(false);
      // 遥控器只显示一行状态；逐条进度与结果都在 Session Logs 里，这里不重复。
      const push = (line: Line) => setLines((cur) => ({ ...cur, [name]: [line] }));
      push({ kind: "progress", text: `⏳ ${t("running…")}` });
      try {
        await streamTeleop(sessionId, name, args, (e: ChatEvent) => {
          if (e.type === "gate") {
            // 闸门：只有拒绝才值得一行（没上膛、参数越界）。放行是常态。
            if (!e.allowed) push({ kind: "bad", text: `⛔ ${t("gate refused {name}", { name: e.name })}: ${t(e.reason)}` });
          } else if (e.type === "progress") push({ kind: "progress", text: `⏳ ${e.message || t("running…")}` });
          else if (e.type === "tool_result") push({ kind: e.ok ? "ok" : "bad", text: `${e.ok ? "✓" : "✗"} ${t(e.message)}` });
        });
      } catch (e) {
        push({ kind: "bad", text: `${t("(cannot reach the backend)")} ${(e as Error).message ?? ""}` });
      } finally {
        setRunning(null);
        setStopping(false);
      }
    },
    [running, sessionId, t],
  );

  async function stop() {
    setStopping(true);
    await interruptSession(sessionId).catch(() => {});
  }

  // 人形的三个大键：工具单里有这三个动词才出现，数值取各自 schema 的 default
  const quick = useMemo(() => {
    const f = byName(QUICK.forward), l = byName(QUICK.left), r = byName(QUICK.right);
    if (!f || !l || !r) return null;
    const def = (tool: ToolSheetEntry) => {
      const props = tool.parameters?.properties ?? {};
      const key = Object.keys(props).find((k) => props[k].type === "number");
      return key ? { key, value: Number(props[key].default ?? props[key].minimum ?? 0) } : null;
    };
    const fd = def(f), ld = def(l), rd = def(r);
    if (!fd || !ld || !rd) return null;
    return { forward: fd, left: ld, right: rd };
  }, [byName]);

  const quickBtn =
    "flex flex-col items-center justify-center gap-0.5 rounded-xl border border-neutral-700 bg-neutral-800 px-2 py-3 text-neutral-100 transition-colors hover:border-blue-500 hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500";

  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center gap-2">
        <span className="font-medium text-neutral-200">🎮 {t("Remote control")}</span>
        {!armed && <span className="rounded border border-amber-700/60 bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300">{t("disarmed — moving verbs will be refused at the gate")}</span>}
        <button onClick={stop} disabled={!running || stopping}
          className="ml-auto rounded-md bg-red-700 px-3 py-1 text-[11px] font-semibold text-white hover:bg-red-600 disabled:opacity-40">
          ■ {stopping ? t("Stopping…") : t("Stop")}
        </button>
      </div>

      {err && <div className="rounded-md border border-red-700/60 bg-red-950/40 p-2 text-[11px] text-red-300">{err}</div>}
      {tools === null && !err && <div className="text-neutral-500">{t("Loading the tool sheet…")}</div>}
      {tools !== null && tools.length === 0 && (
        <div className="rounded-lg border border-neutral-800 bg-neutral-900 p-3 text-neutral-500">
          {t("(no tools in this session — the body node may be unapproved or offline, or the session is not active)")}
        </div>
      )}

      {quick && (
        <div className="grid grid-cols-3 gap-2" role="group" aria-label={t("Quick moves")}>
          <button onClick={() => run(QUICK.left, { [quick.left.key]: quick.left.value })} disabled={!!running} className={quickBtn}
            title={`${QUICK.left}(${quick.left.key}=${quick.left.value})`}>
            <span className="text-xl">◀</span>
            <span className="text-[10px] text-neutral-400">{t("left")} {quick.left.value}°</span>
          </button>
          <button onClick={() => run(QUICK.forward, { [quick.forward.key]: quick.forward.value })} disabled={!!running} className={quickBtn}
            title={`${QUICK.forward}(${quick.forward.key}=${quick.forward.value})`}>
            <span className="text-xl">▲</span>
            <span className="text-[10px] text-neutral-400">{t("forward")} {quick.forward.value} m</span>
          </button>
          <button onClick={() => run(QUICK.right, { [quick.right.key]: quick.right.value })} disabled={!!running} className={quickBtn}
            title={`${QUICK.right}(${quick.right.key}=${quick.right.value})`}>
            <span className="text-xl">▶</span>
            <span className="text-[10px] text-neutral-400">{t("right")} {quick.right.value}°</span>
          </button>
        </div>
      )}

      {groups.map((g) => (
        <section key={g.key}>
          <h3 className="mb-1.5 text-[10px] uppercase tracking-wide text-neutral-500">{g.label}</h3>
          <div className="space-y-2">
            {g.tools.map((tool) => (
              <ToolCard key={tool.name} tool={tool} jointKeys={jointKeys} lines={lines[tool.name] ?? []}
                running={running === tool.name} disabled={!!running} onRun={(args) => run(tool.name, args)} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- 一张卡 = 一个工具 + 按 schema 生成的表单
type KV = { key: string; value: string };
type FieldValue = string | number | boolean | KV[] | "";

function isKvObject(s: JsonSchema): boolean {
  const ap = s.additionalProperties;
  return s.type === "object" && !!ap && typeof ap === "object" && ap.type === "number";
}

function initialValues(schema: JsonSchema): Record<string, FieldValue> {
  const out: Record<string, FieldValue> = {};
  for (const [k, p] of Object.entries(schema.properties ?? {})) {
    if (isKvObject(p)) out[k] = [];
    else if (p.type === "boolean") out[k] = typeof p.default === "boolean" ? p.default : false;
    else if (p.default !== undefined && p.default !== null) out[k] = p.default as string | number;
    else if (p.enum && p.enum.length) out[k] = p.enum[0];
    else out[k] = "";
  }
  return out;
}

function ToolCard({ tool, jointKeys, lines, running, disabled, onRun }: {
  tool: ToolSheetEntry; jointKeys: string[]; lines: Line[]; running: boolean; disabled: boolean; onRun: (args: Record<string, unknown>) => void;
}) {
  const { t } = useI18n();
  const schema = tool.parameters ?? {};
  const props = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const [vals, setVals] = useState<Record<string, FieldValue>>(() => initialValues(schema));
  const [open, setOpen] = useState(false);
  const set = (k: string, v: FieldValue) => setVals((cur) => ({ ...cur, [k]: v }));

  // 表单值 → 调用参数。空着的非必填字段不送；数字字段送数字。
  function buildArgs(): { args: Record<string, unknown>; missing: string[] } {
    const args: Record<string, unknown> = {};
    const missing: string[] = [];
    for (const [k, p] of Object.entries(props)) {
      const v = vals[k];
      if (isKvObject(p)) {
        const obj: Record<string, number> = {};
        for (const { key, value } of (v as KV[]) ?? []) if (key.trim() && value !== "" && !Number.isNaN(Number(value))) obj[key.trim()] = Number(value);
        if (Object.keys(obj).length) args[k] = obj;
        else if (required.has(k)) missing.push(k);
      } else if (p.type === "boolean") args[k] = !!v;
      else if (p.type === "number" || p.type === "integer") {
        if (v === "" || v === undefined || Number.isNaN(Number(v))) {
          if (required.has(k)) missing.push(k);
        } else args[k] = Number(v);
      } else if (p.type === "string" || p.enum) {
        if (v === "" || v === undefined) {
          if (required.has(k)) missing.push(k);
        } else args[k] = v;
      } else if (v !== "" && v !== undefined) {
        // 别的复杂类型：当 JSON 文本
        try {
          args[k] = JSON.parse(String(v));
        } catch {
          args[k] = v;
        }
      } else if (required.has(k)) missing.push(k);
    }
    return { args, missing };
  }

  const { args, missing } = buildArgs();
  const mutating = tool.kind !== "read";
  const inputCls = "w-full rounded-md border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs text-neutral-200 focus:border-blue-500 focus:outline-none";

  return (
    <div className={`rounded-lg border p-2.5 ${running ? "border-blue-600/60 bg-blue-950/20" : "border-neutral-800 bg-neutral-900"}`}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[12px] text-neutral-100">{tool.name}</span>
            <span className={`rounded border px-1 text-[9px] ${mutating ? "border-amber-700/60 text-amber-400" : "border-neutral-700 text-neutral-500"}`}>{tool.kind}</span>
            <span className="text-[10px] text-neutral-600">{tool.node}</span>
          </div>
          {tool.description && (
            <button onClick={() => setOpen((v) => !v)} className="mt-0.5 text-left text-[10px] leading-snug text-neutral-500 hover:text-neutral-300" aria-expanded={open}>
              {open ? tool.description : `${tool.description.slice(0, 90)}${tool.description.length > 90 ? "…" : ""}`}
            </button>
          )}
        </div>
        <button onClick={() => onRun(args)} disabled={disabled || missing.length > 0}
          title={missing.length ? t("required: {names}", { names: missing.join(", ") }) : undefined}
          className="shrink-0 rounded-md bg-blue-600 px-3 py-1 text-[11px] font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-40">
          {running ? t("Running…") : t("Run")}
        </button>
      </div>

      {Object.keys(props).length > 0 && (
        <div className="mt-2 space-y-2">
          {Object.entries(props).map(([k, p]) => (
            <Field key={k} id={`${tool.name}-${k}`} name={k} schema={p} required={required.has(k)} value={vals[k]} onChange={(v) => set(k, v)}
              jointKeys={jointKeys} inputCls={inputCls} />
          ))}
        </div>
      )}

      {lines.length > 0 && (
        <div className="mt-2 space-y-0.5 border-t border-neutral-800 pt-1.5 font-mono text-[10px] leading-snug" aria-live="polite">
          {lines.map((l, i) => (
            <div key={i} className={l.kind === "bad" ? "text-red-400" : l.kind === "ok" ? "text-green-400" : l.kind === "progress" ? "text-neutral-400" : "text-neutral-600"}>
              {l.text}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// 滑杆步长取 10 的整数次幂（量程 3 → 0.01，量程 180 → 1），这样 schema 的 default（1.0、45）都落在刻度上；
// 用「量程/100」当步长的话 1.0 会被 0.03 的刻度吸到 0.99。
const SLIDER_DIVISIONS = 100;
function niceStep(range: number): number {
  if (!(range > 0)) return 1;
  return 10 ** Math.floor(Math.log10(range / SLIDER_DIVISIONS));
}

// 一个字段：number → 滑杆 + 数字框（守 min/max/default）；enum → 下拉；boolean → 勾选；string → 文本；
// additionalProperties number 的 object → 键值编辑器（键从身体观察的 joints_deg 里挑，没有就自由输入）。
function Field({ id, name, schema, required, value, onChange, jointKeys, inputCls }: {
  id: string; name: string; schema: JsonSchema; required: boolean; value: FieldValue; onChange: (v: FieldValue) => void; jointKeys: string[]; inputCls: string;
}) {
  const { t } = useI18n();
  const label = (
    <label htmlFor={id} className="flex items-baseline gap-1 text-[10px] text-neutral-400">
      <span className="font-mono text-neutral-300">{name}</span>
      {required && <span className="text-amber-500" title={t("required")}>*</span>}
      {schema.description && <span className="truncate text-neutral-600">{schema.description}</span>}
    </label>
  );

  if (isKvObject(schema)) {
    const rows = (Array.isArray(value) ? value : []) as KV[];
    const setRows = (r: KV[]) => onChange(r);
    const unused = jointKeys.filter((j) => !rows.some((r) => r.key === j));
    return (
      <div>
        {label}
        <div className="mt-1 space-y-1">
          {rows.map((r, i) => (
            <div key={i} className="flex items-center gap-1">
              {jointKeys.length ? (
                <select value={r.key} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} className={`${inputCls} flex-1`} aria-label={t("key")}>
                  {!jointKeys.includes(r.key) && <option value={r.key}>{r.key || t("— pick —")}</option>}
                  {jointKeys.map((j) => <option key={j} value={j}>{j}</option>)}
                </select>
              ) : (
                <input value={r.key} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} placeholder={t("key")} className={`${inputCls} flex-1`} aria-label={t("key")} />
              )}
              <input type="number" step="any" value={r.value} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}
                placeholder={t("value")} className={`${inputCls} w-24`} aria-label={t("value")} />
              <button onClick={() => setRows(rows.filter((_, j) => j !== i))} className="rounded px-1.5 py-1 text-neutral-500 hover:bg-neutral-800 hover:text-red-400" title={t("remove")} aria-label={t("remove")}>✕</button>
            </div>
          ))}
          <button id={id} onClick={() => setRows([...rows, { key: unused[0] ?? "", value: "" }])}
            className="rounded-md border border-dashed border-neutral-700 px-2 py-0.5 text-[10px] text-neutral-400 hover:border-neutral-500 hover:text-neutral-200">
            + {t("add {what}", { what: jointKeys.length ? t("joint") : t("key") })}
          </button>
        </div>
      </div>
    );
  }

  if (schema.enum && schema.enum.length) {
    return (
      <div>
        {label}
        <select id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} className={`${inputCls} mt-1`}>
          {schema.enum.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
        </select>
      </div>
    );
  }

  if (schema.type === "boolean") {
    return (
      <label htmlFor={id} className="flex items-center gap-1.5 text-[11px] text-neutral-300">
        <input id={id} type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
        <span className="font-mono">{name}</span>
        {schema.description && <span className="truncate text-[10px] text-neutral-600">{schema.description}</span>}
      </label>
    );
  }

  if (schema.type === "number" || schema.type === "integer") {
    const hasRange = typeof schema.minimum === "number" && typeof schema.maximum === "number";
    const step = schema.type === "integer" ? 1 : hasRange ? niceStep(schema.maximum! - schema.minimum!) : "any";
    const num = value === "" || value === undefined ? "" : String(value);
    return (
      <div>
        {label}
        <div className="mt-1 flex items-center gap-2">
          {hasRange && (
            <input type="range" min={schema.minimum} max={schema.maximum} step={step} value={num === "" ? schema.minimum : Number(num)}
              onChange={(e) => onChange(Number(e.target.value))} className="flex-1 accent-blue-500" aria-label={`${name} (${t("slider")})`} />
          )}
          <input id={id} type="number" min={schema.minimum} max={schema.maximum} step={step} value={num} placeholder={schema.default !== undefined ? String(schema.default) : ""}
            onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} className={`${inputCls} ${hasRange ? "w-24" : ""}`} />
        </div>
        {(schema.minimum !== undefined || schema.maximum !== undefined) && (
          <div className="mt-0.5 text-[9px] text-neutral-600">
            {schema.minimum ?? "−∞"} … {schema.maximum ?? "∞"}{schema.default !== undefined ? ` · ${t("default")} ${String(schema.default)}` : ""}
          </div>
        )}
      </div>
    );
  }

  // string 与其它（复杂类型当 JSON 文本）
  return (
    <div>
      {label}
      <input id={id} value={typeof value === "string" ? value : value === undefined ? "" : JSON.stringify(value)} onChange={(e) => onChange(e.target.value)}
        placeholder={schema.type && schema.type !== "string" ? "JSON" : ""} className={`${inputCls} mt-1`} />
    </div>
  );
}
