// NERV/Operator client. Everything the web app knows about the backend lives here;
// the contract is src/nerv/platform/server.py in this repo.
//
// ⭐ 空串是有意义的一档，不是"没设"：`npm run build:static` 会用 NEXT_PUBLIC_API="" 构建，
//    于是下面每一处 `${BASE}/api/...` 都变成 `/api/...`——相对于当前页面的源。
//    这正是把网页交给 `nerv serve` 自己端出来时需要的：换个端口，页面照样连得上。
//    注意用的是 `??` 不是 `||`：空串必须能穿过去。
const BASE = process.env.NEXT_PUBLIC_API ?? "http://localhost:8000";

// 前端轮询 / 显示上限：集中此处、可用 NEXT_PUBLIC_* env 覆盖，不在各组件内联写死。
export const SIGNAL_LOG_SHOWN = Number(process.env.NEXT_PUBLIC_SIGNAL_LOG_SHOWN) || 300; // ≤ 后端 NERV_SIGNAL_LOG_MAXLEN
export const POLL_NODES_MS = Number(process.env.NEXT_PUBLIC_POLL_NODES_MS) || 3000;
export const POLL_PERCEIVE_MS = Number(process.env.NEXT_PUBLIC_POLL_PERCEIVE_MS) || 3000;
export const POLL_SESSION_LOGS_MS = Number(process.env.NEXT_PUBLIC_POLL_SESSION_LOGS_MS) || 2000;

// ---- brains ------------------------------------------------------------------------------
export type Brain = {
  name: string;
  vendor: string;
  label: string;
  model: string;
  hosting: "api" | "local";
  available: boolean;
};

// ---- registry (bodies / worlds / tools declared on disk) ---------------------------------
export type SensorDecl = { name: string; description: string; camera: string; width: number; height: number };
export type BusEndpoint = { kind: string; python: string; settings: Record<string, unknown>; verified: boolean };
export type BodySpec = {
  name: string;
  family: string;
  label: string;
  version: string;
  sensors: SensorDecl[];
  skills: Record<string, { policy: string; description: string }>;
  actuators: Record<string, unknown>;
  buses: Record<string, BusEndpoint>;
  url: string;
  dir: string;
};
export type WorldSpec = {
  name: string;
  kind: "sim" | "real";
  label: string;
  engine: string;
  assets_root: string;
  supports: Record<string, string>;
  spawn: Record<string, unknown>;
  ambient: SensorDecl[];
  physics: Record<string, unknown>;
  url: string;
  dir: string;
};
export type ToolSpec = { name: string; label: string; module: string; python: string; url: string; dir: string };
export type MatrixCell = { world: string; body: string; ok: boolean; reason: string };
export type Registry = {
  bodies: BodySpec[];
  worlds: WorldSpec[];
  tools: ToolSpec[];
  matrix: MatrixCell[];
  brains: Brain[];
  default_brain: string;
};

export async function getRegistry(): Promise<Registry> {
  const r = await fetch(`${BASE}/api/registry`);
  return (await r.json()) as Registry;
}

export async function getBrains(): Promise<Brain[]> {
  const r = await fetch(`${BASE}/api/brains`);
  return (await r.json()) as Brain[];
}

export async function checkBrain(brain: string): Promise<{ ok: boolean; message?: string; model?: string; vision?: boolean }> {
  const r = await fetch(`${BASE}/api/check?brain=${encodeURIComponent(brain)}`);
  return await r.json();
}

// ---- nodes (control plane) --------------------------------------------------------------
// unknown = 没审批过；changed = 批准过但清单变了；trusted = 可用；offline = 问不到。
export type TrustState = "unknown" | "changed" | "trusted" | "offline";
export type NodeTrust = { state: TrustState; reason: string; changes?: string[] };
export type NodeKind = "body" | "world" | "tool";
export type NodeInfo = {
  key: string; // "body:<name>" | "world:<world>/<body>" | "tool:<name>"
  kind: NodeKind;
  url: string;
  bus_url: string;
  alive: boolean;
  attached: boolean;
  log: string;
  python: string;
  meta: Record<string, unknown>;
  online?: boolean;
  trust?: NodeTrust; // body / tool 才有（world 节点不进信任门：大脑从不读它的文字）
  tools?: string[];
  family?: string;
  sensors?: string[];
};

// 一个节点**原样**声明的东西——审批界面看的就是它。
// ⛔ 这是未经信任过滤的原文：给人看的必须和被批准的是同一份，否则这次审批毫无意义。
export type ManifestTool = { name: string; kind: string; description: string; parameters: unknown };
export type NodeManifest = {
  key: string;
  url: string;
  manifest: { url: string; guidance: string; tools: ManifestTool[] };
  trust: NodeTrust;
  family: string;
  sensors: string[];
};

const nodePath = (key: string) => `${BASE}/api/nodes/${encodeURI(key)}`; // key 里的 ":" 和 "/" 都要原样穿过去

export async function getNodes(): Promise<NodeInfo[]> {
  const r = await fetch(`${BASE}/api/nodes`);
  return (await r.json()) as NodeInfo[];
}

export async function getNodeManifest(key: string): Promise<NodeManifest> {
  const r = await fetch(`${nodePath(key)}/manifest`);
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return (await r.json()) as NodeManifest;
}

export async function approveNode(key: string): Promise<{ ok: boolean; hash: string }> {
  const r = await fetch(`${nodePath(key)}/approve`, { method: "POST" });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return await r.json();
}

// 让 NERV 重新问一遍这个节点有哪些工具（能力清单在首次握手时被缓存）。
export async function refreshNode(key: string): Promise<{ ok: boolean }> {
  const r = await fetch(`${nodePath(key)}/refresh`, { method: "POST" });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return await r.json();
}

export async function setNodeConfig(key: string, k: string, value: string): Promise<Record<string, unknown>> {
  const r = await fetch(`${nodePath(key)}/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: k, value }),
  });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return await r.json();
}

// 节点自己的真实状态（人的上帝视角，大脑看不到）
export async function getNodeStatus(key: string): Promise<Record<string, unknown>> {
  const r = await fetch(`${nodePath(key)}/status`);
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return await r.json();
}

// ---- sessions ---------------------------------------------------------------------------
export type SessionStatus = "active" | "frozen" | "reconnect_required";
export type SessionSummary = {
  id: string;
  brain: string;
  body: string | null; // null = conversation only
  world: string | null;
  status: SessionStatus;
  created_at: string;
  title: string;
  sensors: string[]; // ambient streams the brain also sees
  tools: string[]; // tool nodes mounted
  armed: boolean; // hardware may move only when true
  epoch: string;
  core_task: string; // 一句话：我在干什么（大脑自己写）
  notes: string[]; // 一条条：我发现了什么
};

export type ToolCall = { id: string; name: string; arguments: Record<string, unknown> };

// 会话记录里的一条（后端落盘格式）
export type RecMsg =
  | { role: "user"; text: string; ts?: string }
  | { role: "perception"; image_ref: string | null; images?: { name: string; ref: string }[]; state: Record<string, unknown>; ts?: string }
  | { role: "assistant"; text: string; tool_calls?: ToolCall[]; brain?: string; ts?: string }
  | { role: "tool"; id: string; name: string; content: string; ts?: string }
  | { role: "brain_divider"; brain: string; ts?: string };

export type SessionFull = SessionSummary & { messages: RecMsg[] };

export type NewSession = {
  brain?: string;
  body?: string | null;
  world?: string | null;
  sensors: string[];
  tools?: string[];
};

/** 后端用 4xx + {detail} 说"不行"（注册表拒绝这对组合、节点起不来、会话不存在）。
 *  把 detail 原样带出来，界面直接显示——那句话就是给人看的。 */
export class ApiError extends Error {
  status: number;
  constructor(detail: string, status: number) {
    super(detail);
    this.status = status;
  }
}

async function detailOf(r: Response): Promise<string> {
  try {
    const j = await r.json();
    // 后端把 str(KeyError) 直接当 detail，外面会多一层引号——剥掉，别把引号显示给人。
    if (typeof j?.detail === "string") return j.detail.replace(/^"([\s\S]*)"$/, "$1");
    return JSON.stringify(j);
  } catch {
    return `${r.status} ${r.statusText}`;
  }
}

export async function createSession(inp: NewSession): Promise<SessionSummary> {
  const r = await fetch(`${BASE}/api/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(inp),
  });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return (await r.json()) as SessionSummary;
}

export async function listSessions(): Promise<SessionSummary[]> {
  const r = await fetch(`${BASE}/api/sessions`);
  return (await r.json()) as SessionSummary[];
}

export async function getSession(id: string): Promise<SessionFull> {
  const r = await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}`);
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return (await r.json()) as SessionFull;
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// 叫停这个会话正在跑的那一轮。**立刻返回、不等它真的停**：身体那边正在做的那一步还得做完，
// 所以按钮此后显示「停止中…」，直到流自己以停顿语收尾。
// ⛔ 不要改成 AbortController 掐掉 fetch——那只是前端自己捂住眼睛，后端还在跑、还在花钱。
export async function interruptSession(id: string): Promise<void> {
  await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}/interrupt`, { method: "POST" });
}

// 急停：身体锁住当前姿态、停止思考，直到操作员放开。⛔ 不是断电——断电舵机会卸力倒下。
export type HoldResult = { ok: boolean; held: boolean; message: string };

export async function estopSession(id: string): Promise<HoldResult> {
  const r = await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}/estop`, { method: "POST" });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return (await r.json()) as HoldResult;
}

export async function releaseSession(id: string): Promise<HoldResult> {
  const r = await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}/release`, { method: "POST" });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return (await r.json()) as HoldResult;
}

// 复位：身体回到出生姿态（只有仿真世界会答应），同时解除锁姿。
export async function resetSessionWorld(id: string): Promise<{ ok: boolean; message: string }> {
  const r = await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}/reset`, { method: "POST" });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return (await r.json()) as { ok: boolean; message: string };
}

export async function setSessionBrain(id: string, brain: string): Promise<void> {
  await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}/brain`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ brain }),
  });
}

// ARM / DISARM：硬件只有在 armed 时才许动。只有人能按这个键；大脑没有这个工具。
export async function armSession(id: string, armed: boolean): Promise<{ ok: boolean; armed: boolean; body: unknown }> {
  const r = await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}/arm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ armed }),
  });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return await r.json();
}

// ---- chat -------------------------------------------------------------------------------
// 流式聊天的事件（SSE），一轮按这个顺序产生：start · perception · thinking · tool_call · gate ·
// progress · tool_result · reply · done。stop_reason 只在这一轮被闸门收尾时才有。
export type ChatEvent =
  | { type: "start"; brain: string; model: string }
  | { type: "perception"; image_b64: string | null; state: Record<string, unknown>; n_images: number; cameras: string[] }
  | { type: "thinking"; text: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "gate"; name: string; allowed: boolean; reason: string } // 安全门的裁决；allowed=false 时这一步不会执行
  | { type: "progress"; name: string; message: string; progress: number }
  | { type: "tool_result"; name: string; ok: boolean; message: string }
  | { type: "reply"; text: string; stop_reason?: "steps" | "time" | "interrupt" }
  | { type: "done" };

export async function sendChat(session: string, text: string): Promise<{ reply: string; stop_reason: string | null }> {
  const r = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session, text }),
  });
  return await r.json();
}

// 读一条 SSE 流（`data: {...}\n\n` 分帧），每帧解析成一个事件交给回调。
// chat 与 teleop 两个端点吐的是同一种事件，所以解析只写一份。
async function readSse(r: Response, onEvent: (e: ChatEvent) => void): Promise<void> {
  const reader = r.body?.getReader();
  if (!reader) return;
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(6)) as ChatEvent);
        } catch {
          /* ignore */
        }
      }
    }
  }
}

export async function streamChat(session: string, text: string, onEvent: (e: ChatEvent) => void): Promise<void> {
  const r = await fetch(`${BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session, text }),
  });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  await readSse(r, onEvent);
}

// ---- teleop (operator remote control) -----------------------------------------------------
// 这个会话里大脑会看到的工具单：身体的动词（read / primitive / skill）+ 工具节点的函数。
// 遥控面板照着它生成表单——⛔ 前端不知道任何具体身体有哪些动词，全从这里读。
export type ToolKind = "read" | "primitive" | "skill";
export type ToolSheetEntry = {
  name: string;
  kind: ToolKind;
  origin: "body" | "tool";
  node: string;
  description: string;
  parameters: JsonSchema;
};

// 工具参数的 JSON Schema（只列表单生成用得到的那几个字段）
export type JsonSchema = {
  type?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: (string | number)[];
  minimum?: number;
  maximum?: number;
  default?: unknown;
  additionalProperties?: JsonSchema | boolean;
};

export async function getSessionTools(id: string): Promise<ToolSheetEntry[]> {
  const r = await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}/tools`);
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return (await r.json()) as ToolSheetEntry[];
}

// 操作员直接调一个工具。同一道闸门、同一批节点、同一份日志，事件与 chat 流完全一样
// （start · gate · progress · tool_result · done）；这一步会记进会话，大脑下一回合看得到。
export async function streamTeleop(
  id: string,
  name: string,
  args: Record<string, unknown>,
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const r = await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}/teleop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, arguments: args }),
  });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  await readSse(r, onEvent);
}

// ---- world node (direct) ------------------------------------------------------------------
// 世界节点自己的传感器清单（GET <worldurl>/sensors）。相机名形如 "camera:<mujoco 相机名>"；
// 对应的连续视频在 <worldurl>/stream/<去掉 camera: 前缀的名字>。这些都是给人看的，大脑看不到。
export const WORLD_CAMERA_PREFIX = "camera:";

export async function getWorldSensors(worldUrl: string): Promise<string[]> {
  const r = await fetch(`${worldUrl}/sensors`);
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  const j = (await r.json()) as { sensors?: string[] };
  return j.sensors ?? [];
}

export const worldCameraStreamUrl = (worldUrl: string, sensor: string) =>
  `${worldUrl}/stream/${encodeURIComponent(sensor.startsWith(WORLD_CAMERA_PREFIX) ? sensor.slice(WORLD_CAMERA_PREFIX.length) : sensor)}`;

// ---- perception --------------------------------------------------------------------------
// 大脑此刻会看到的东西：身体自己的感官 + 会话声明的环境流。每一路都带名字。
export type Perception = { state: Record<string, unknown>; images: { name: string; b64: string }[] };

export async function getPerception(session: string): Promise<Perception> {
  const r = await fetch(`${BASE}/api/perceive?session=${encodeURIComponent(session)}`);
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return (await r.json()) as Perception;
}

export const imgUrl = (ref: string) => `${BASE}/api/imgfile?ref=${encodeURIComponent(ref)}`;

// ---- status / config ---------------------------------------------------------------------
export async function getStatus(): Promise<{ version: string; nodes: number; data_root: string }> {
  const r = await fetch(`${BASE}/api/status`);
  return await r.json();
}

// 核心运行参数。值 / env 名 / 说明都来自后端的 config.Settings，前端不写死任何数字。
export type RuntimeParam = { key: string; label: string; value: number; env: string; description: string };
export type RuntimeConfig = { params: RuntimeParam[]; trust_all: boolean };

export async function getRuntimeConfig(): Promise<RuntimeConfig> {
  const r = await fetch(`${BASE}/api/config`);
  return (await r.json()) as RuntimeConfig;
}

// ---- signals (one log; every call that crosses NERV) --------------------------------------
type SignalBase = {
  id: number;
  t?: number; // unix 秒（合并排序键）
  ts: string;
  session: string; // 空 = 非会话场景（如连通自检 / 操作员动作）
};
export type LlmCallEntry = SignalBase & {
  kind: "llm_call";
  model: string;
  system: string;
  last_user: string;
  n_history: number;
  n_tools: number;
  has_image: boolean;
  reply: string;
  tool_calls: string[];
  tokens: { input: number; output: number; total: number } | null;
  ms: number;
  error: string;
};
export type NodeCallEntry = SignalBase & {
  kind: "body_call" | "tool_call" | "world_call";
  node: string;
  method: string;
  summary: string;
  resp: Record<string, unknown>;
  ms: number;
};
export type GateEntry = SignalBase & {
  kind: "gate";
  tool: string;
  origin: string;
  armed: boolean;
  allowed: boolean;
  reason: string;
};
export type OperatorEntry = SignalBase & { kind: "operator"; event: string } & Record<string, unknown>;
export type SignalEntry = LlmCallEntry | NodeCallEntry | GateEntry | OperatorEntry;

export type NervOverview = {
  nodes: NodeInfo[];
  sessions: SessionSummary[];
  brains: Brain[];
  registry: Omit<Registry, "brains" | "default_brain">;
  recent: SignalEntry[];
};

export async function getNerv(): Promise<NervOverview> {
  const r = await fetch(`${BASE}/api/nerv`);
  return (await r.json()) as NervOverview;
}

export const nervEventsUrl = (since = 0) => `${BASE}/api/nerv/events?since=${since}`;

export async function getSessionLogs(limit = 500, session = ""): Promise<{ entries: SignalEntry[]; sessions: string[] }> {
  const q = `limit=${limit}` + (session ? `&session=${encodeURIComponent(session)}` : "");
  const r = await fetch(`${BASE}/api/session-logs?${q}`);
  const j = (await r.json()) as { entries: SignalEntry[]; sessions?: string[] };
  return { entries: j.entries, sessions: j.sessions ?? [] };
}
