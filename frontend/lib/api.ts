// NERV/Operator client. Everything the web app knows about the backend lives here;
// the contract is src/nerv/platform/server.py in this repo.
//
// ⭐ 空串是有意义的一档，不是"没设"：`npm run build:static` 会用 NEXT_PUBLIC_API="" 构建，
//    于是下面每一处 `${BASE}/api/...` 都变成 `/api/...`——相对于当前页面的源。
//    这正是把网页交给 `nerv serve` 自己端出来时需要的：换个端口，页面照样连得上。
//    注意用的是 `??` 不是 `||`：空串必须能穿过去。
const BASE = process.env.NEXT_PUBLIC_API ?? "http://localhost:8170";

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
  efforts?: string[]; effort?: string | null;
};

// ---- registry (bodies / worlds / tools declared on disk) ---------------------------------
export type SensorDecl = { name: string; description: string; camera: string; width: number; height: number };
export type BusEndpoint = { provider?: string; enabled: boolean; kind: string; python: string; settings: Record<string, unknown>; verified: boolean };
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
  configuration?: {schema: PropertySchema};
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
  agent_tools: ToolSpec[];
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
  local_simulation?: boolean;
  log: string;
  python: string;
  meta: Record<string, unknown>;
  online?: boolean;
  trust?: NodeTrust; // body / tool 才有（world 节点不进信任门：大脑从不读它的文字）
  robot_actions?: string[];
  agent_tools?: string[];
  family?: string;
  sensors?: string[];
};

// 一个节点**原样**声明的东西——审批界面看的就是它。
// ⛔ 这是未经信任过滤的原文：给人看的必须和被批准的是同一份，否则这次审批毫无意义。
export type ManifestTool = { name: string; kind: string; description: string; parameters: unknown };
export type NodeManifest = {
  hash: string;
  key: string;
  url: string;
  manifest: { url: string; guidance: string; tools: ManifestTool[] };
  trust: NodeTrust;
  family: string;
  sensors: string[];
};

const nodePath = (key: string) => `${BASE}/api/nodes/${encodeURI(key)}`; // key 里的 ":" 和 "/" 都要原样穿过去

export async function stopSimulation(node: NodeInfo, sessionId?: string): Promise<void> {
  const r = await fetch(`${nodePath(node.key)}/stop-simulation`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_world: node.meta.world, expected_epoch: node.meta.epoch, session_id: sessionId }),
  });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
}

export async function getNodes(): Promise<NodeInfo[]> {
  const r = await fetch(`${BASE}/api/nodes`);
  return (await r.json()) as NodeInfo[];
}

export async function getNodeManifest(key: string): Promise<NodeManifest> {
  const r = await fetch(`${nodePath(key)}/manifest`);
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  return (await r.json()) as NodeManifest;
}

export async function approveNode(key: string, expectedHash: string): Promise<{ ok: boolean; hash: string }> {
  const r = await fetch(`${nodePath(key)}/approve`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({expected_hash: expectedHash}) });
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
  control_scheme?: string; control_scheme_revision?: number;
  task_model?: string; task_effort?: string | null;
  id: string;
  brain: string;
  body: string | null; // null = conversation only
  world: string | null;
  status: SessionStatus;
  mode: "sim" | "real";
  binding: string;
  created_at: string;
  title: string;
  sensors: string[]; // ambient streams the brain also sees
  agent_tools: string[]; // auxiliary nodes mounted
  armed: boolean; // hardware may move only when true
  epoch: string;
  core_task: string; // 一句话：我在干什么（大脑自己写）
  notes: string[]; // 一条条：我发现了什么
};

export type ToolCall = { id: string; name: string; domain?: string; capability_name?: string; arguments: Record<string, unknown> };

// 会话记录里的一条（后端落盘格式）
export type RecMsg =
  | { role: "error"; code: string; message: string; brain?: string }
  | { role: "user"; text: string; ts?: string }
  | { role: "perception"; image_ref: string | null; images?: { name: string; ref: string }[]; state: Record<string, unknown>; ts?: string }
  | { role: "assistant"; text: string; tool_calls?: ToolCall[]; brain?: string; ts?: string }
  | { role: "tool"; id: string; name: string; domain?: string; capability_name?: string; content: string; ts?: string }
  | { role: "brain_divider"; brain: string; ts?: string };

export type SessionFull = SessionSummary & { messages: RecMsg[] };

export type NewSession = {
  world_configuration?: Record<string, unknown>;
  brain?: string;
  mode?: "sim" | "real";
  body?: string | null;
  world?: string | null;
  sensors: string[];
  agent_tools?: string[];
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
    if (typeof j?.detail?.message === "string") return j.detail.message;
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
  const r = await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}/brain`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ brain }),
  });
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
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
  | {type: "model_error"; code: string; message: string}
  | {type: "instance_created"; session_id: string}
  | {type: "capability_call"; domain: string; name: string; args: Record<string, unknown>}
  | {type: "capability_result"; domain: string; name: string; ok: boolean; message: string}
  | { type: "start"; brain: string; model: string }
  | { type: "perception"; image_b64: string | null; state: Record<string, unknown>; n_images: number; cameras: string[]; images?: {name: string; b64: string}[] }
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
export type ToolKind = "read" | "primitive" | "skill" | "control";
export type ToolSheetEntry = {
  domain: "robot_actions" | "agent_tools";
  name: string;
  kind: ToolKind;
  origin: "body" | "tool" | "memory";
  node: string;
  description: string;
  parameters: JsonSchema;
};

// 工具参数的 JSON Schema（只列表单生成用得到的那几个字段）
export type JsonSchema = {
  "x-observation-field"?: string;
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
  const r = await fetch(`${BASE}/api/sessions/${encodeURIComponent(id)}/capabilities`);
  if (!r.ok) throw new ApiError(await detailOf(r), r.status);
  const domains = await r.json();
  return [...domains.robot_actions, ...domains.agent_tools] as ToolSheetEntry[];
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

// 追拍相机的手动视角：拉远/拉近、左右旋、上下旋。发给世界节点，视频流不重连、下一帧起平滑过渡。
// 「关掉再打开回默认」靠前端在打开和关闭视图时各发一次默认值。
export type ChaseView = { zoom: number; yaw: number; pitch: number };
export const DEFAULT_CHASE_VIEW: ChaseView = { zoom: 1, yaw: 0, pitch: 0 };

export async function setWorldChaseView(worldUrl: string, v: ChaseView): Promise<void> {
  await fetch(`${worldUrl}/view`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) });
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

export type VlmModel = {
  id: string; provider: string; label: string; model: string; base_url: string;
  protocol: string; credential_ref: string; input_modalities: string[];
  supports_tools: boolean; efforts: string[]; evidence: string;
  context_window: number | null; max_output_tokens: number; request_timeout_s: number; enabled: boolean;
  credential: { configured: boolean; source: string | null; writable: boolean };
  checks: Record<string, string>; effort: string | null; available: boolean;
  physical?: { status: string };
  provider_default_effort?: string | null;
};
export type VlmProvider = {
  id: string; label: string; credential_ref: string; protocol: string;
  credential: VlmModel["credential"];
};
async function modelRequest(path: string, method = "GET", body?: unknown) {
  const response = await fetch(`${BASE}/api/${path}`, { method, headers: {"Content-Type":"application/json"}, ...(body === undefined ? {} : {body: JSON.stringify(body)}) });
  if (!response.ok) throw new ApiError(await detailOf(response), response.status);
  return response.json();
}
export const getVlm = (): Promise<{models: VlmModel[]; providers: VlmProvider[]; credential_path: string; selection: string; task_locked: boolean}> => modelRequest("vlm");
export type ConfigurationResult = {ok: boolean; saved: boolean; error?: {code: string; message: string}};
export async function configureVlm(id: string, value: string, signal: AbortSignal): Promise<ConfigurationResult> {
  const response = await fetch(`${BASE}/api/vlm/${encodeURIComponent(id)}/configure`, {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({value}), signal});
  if (!response.ok) throw new ApiError(await detailOf(response), response.status);
  return response.json();
}
export const putVlm = (model: Pick<VlmModel, "id"> & Record<string, unknown>) => modelRequest(`vlm/${encodeURIComponent(model.id)}`, "PUT", model);
export const selectVlm = (id: string, effort: string | null) => modelRequest(`vlm/${encodeURIComponent(id)}/select`, "POST", {effort});
export const validateVlm = (id: string) => modelRequest(`vlm/${encodeURIComponent(id)}/validate`, "POST", {});
export const saveCredential = (reference: string, value: string | null) => modelRequest(`credentials/${encodeURIComponent(reference)}`, "PUT", {value});
export const runtimeOperation = (session: string, operation: string, payload = {}) => modelRequest(`sessions/${encodeURIComponent(session)}/runtime/${operation}`, "POST", payload);
export const runtimeStatus = (session: string) => modelRequest(`sessions/${encodeURIComponent(session)}/runtime`);

// Business domains stay distinct even when provider APIs encode calls as functions.
export type PropertyField = { type: string; title?: string; description?: string; default?: unknown; enum?: string[]; oneOf?: { const: string; title: string; title_locales?: Record<string, string>; description_locales?: Record<string, string>; description?: string }[] };
export type PropertySchema = { properties?: Record<string, PropertyField> };
export type WorldDescriptor = { instance_id: string; revision: number; configuration: PropertySchema; state: {configuration?: Record<string, unknown>}; commands: { name: string; description: string; parameters: PropertySchema; effect: string }[] };
export type RobotEvent = { seq: number; time: number; instance_id: string; task_id?: string; version?: number; source?: string; type?: string; text?: string; message?: string; status?: string; ok?: boolean };
export type RobotTask = { task_id: string; instance_id: string; version: number; status: string; goal: string; model: string | null; effort?: string; message?: string; scheme?: ControlScheme };
export type RobotSnapshot = { task: RobotTask | null; events: RobotEvent[]; cursor: number };
export function robotEventUrl(sid: string, cursor = 0) { return `${BASE}/api/sessions/${encodeURIComponent(sid)}/robot-events?after=${cursor}`; }
async function domainRequest(path: string, payload?: unknown) {
  const response = await fetch(`${BASE}${path}`, payload === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  if (!response.ok) throw new ApiError(await detailOf(response), response.status);
  const data = await response.json();
  if (data.ok === false) throw new Error(data.message || "Operation failed");
  return data;
}
export const robotTaskOperation = (sid: string, operation: string, arguments_: Record<string, unknown> = {}) => domainRequest(`/api/sessions/${sid}/robot-tasks`, {operation, arguments: arguments_});
export const setTaskModel = (sid: string, model: string, effort: string | null) => domainRequest(`/api/sessions/${sid}/task-model`, {model, effort});
export const getWorldControl = (sid: string): Promise<WorldDescriptor> => domainRequest(`/api/sessions/${sid}/world-control`);
export const changeWorld = (sid: string, descriptor: WorldDescriptor, command: string, arguments_: Record<string, unknown>) => domainRequest(`/api/sessions/${sid}/world-control`, {instance_id: descriptor.instance_id, revision: descriptor.revision, command, arguments: arguments_});

export async function manageRuntime(python: string, provider: string, operation: string, payload: Record<string, unknown>) {
  const response = await fetch(`${BASE}/api/runtime/manage`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({python, provider, operation, payload})});
  if (!response.ok) throw new ApiError(await detailOf(response), response.status);
  return response.json();
}

export async function runtimeInstallations(): Promise<Array<{provider: string; python: string; label: string}>> {
  const response = await fetch(`${BASE}/api/runtime/installations`);
  if (!response.ok) throw new Error("Cannot read installed runtimes");
  return response.json();
}

export type ControlScheme = {
  id: string; revision: number; name: string; robot: string; mode: "sim" | "real";
  template: "fixed_policy" | "policy_group" | "visual_direct";
  decision_model: string | null; effort: string | null; actions: string[];
  max_runtime_s: number; action_budget: number; ready?: boolean; reason?: string; structure?: unknown;
};
export type SchemeOptions = {selected?: ControlScheme | null; schemes: ControlScheme[]; actions: {name: string; description: string; kind: string}[]};
export const listControlSchemes = (sid?: string): Promise<SchemeOptions> => domainRequest(`/api/control-schemes${sid ? `?sid=${encodeURIComponent(sid)}` : ""}`);
export const saveControlScheme = (scheme: Partial<ControlScheme>): Promise<ControlScheme> => domainRequest("/api/control-schemes", scheme);
export const selectControlScheme = (sid: string, scheme: ControlScheme) => domainRequest(`/api/sessions/${sid}/control-scheme`, {id: scheme.id, revision: scheme.revision});
export const getRobotTask = (sid: string): Promise<RobotSnapshot> => domainRequest(`/api/sessions/${sid}/robot-tasks`);
export const setupDevice = (operation: string, payload: Record<string, unknown> = {}) => domainRequest(`/api/setup/lerobot0.6.1/${operation}`, payload);
export const browseDirectories = (path?: string, offset = 0) => domainRequest("/api/operator/directories", {path, offset});
export const setupCamera = (operation: string, payload: Record<string, unknown> = {}) => domainRequest(`/api/setup-camera/lerobot0.6.1/${operation}`, payload);
export const moduleCatalog = () => domainRequest("/api/modules");
export const installModule = (id: string) => domainRequest(`/api/modules/${id}/install`, {});
export const cancelModuleInstall = (id: string) => domainRequest(`/api/modules/${id}/cancel`, {});

// A hung request must not freeze the live installer log indefinitely.
const INSTALL_LOG_TIMEOUT_MS = 15000;
export type InstallationLog = {log: string; bytes?: number; modified_at?: number | null; truncated?: boolean};
export async function installationLog(id: string, signal?: AbortSignal): Promise<InstallationLog> {
  const request = new AbortController();
  const abort = () => request.abort();
  if (signal?.aborted) request.abort();
  signal?.addEventListener("abort", abort, {once: true});
  const timeout = setTimeout(abort, INSTALL_LOG_TIMEOUT_MS);
  try {
    const response = await fetch(`${BASE}/api/modules/jobs/${id}/log`, {signal: request.signal, cache: "no-store"});
    if (!response.ok) throw new Error(`Installation log unavailable (${response.status})`);
    return await response.json();
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}
export const checkRelease = (): Promise<{available_version: string; installed_version: string | null; update_available: boolean}> => domainRequest("/api/releases/check");

// Operator settings and robot identity use the same local host as the workspace.
export async function userApi<T = any>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`${BASE}/api/${path}`, {method, headers: body instanceof Blob ? {} : {"Content-Type": "application/json"}, body: body === undefined ? undefined : body instanceof Blob ? body : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail || result));
  return result;
}
export const assetUrl = (path: string) => `${BASE}${path}`;
export type RobotDisplay = {name: string; registered: boolean; image: string | null; last_check?: {at: number; repair_step?: number | null} | null; device: {id?: string; port?: string; calibration_dir?: string; cameras?: Record<string, unknown>}};
