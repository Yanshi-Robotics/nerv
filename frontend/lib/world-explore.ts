// Operator-only scene interfaces. These never enter a brain's tool sheet.
import { ApiError } from "./api";

const BASE = process.env.NEXT_PUBLIC_API ?? "http://localhost:8000";
export const SCENE_HEARTBEAT_MS = 500; // Refresh the two-second server lease independently of images.
export const SCENE_FRAME_FPS = Number(process.env.NEXT_PUBLIC_SCENE_FRAME_FPS) || 12;
const SCENE_REQUEST_TIMEOUT_MS = 5000; // Bound normal operator requests independently of camera frames.
const SCENE_HEARTBEAT_TIMEOUT_MS = 1000; // Fail before the server's two-second lease expires.
const SCENE_ACQUIRE_TIMEOUT_MS = 8000; // Includes the six-second standing stabilization deadline.
const EXPLORE_MANIFEST_TIMEOUT_MS = 15000; // Source verification may read the full asset inventory.
export type XYZ = [number, number, number];
export type Bounds = [XYZ, XYZ];
export type Localized = Record<string, string>;
export type ExploreFloor = { id: number; label: Localized; z: number };
export type ExploreRoom = { id: string; label: Localized; floor: number; bounds: Bounds; exterior?: boolean };
export type ExploreFacility = {
  id: string; label: Localized; room: string | null; floor: number;
  kind: "joint" | "movable" | "boundary" | "pose" | "static";
  body?: string; joint?: string; anchor: XYZ; bounds?: Bounds; operations: string[];
  description: Localized; test: Localized; points?: XYZ[];
};
export type SceneCatalogue = {
  scene: string; label: Localized; floors: ExploreFloor[]; rooms: ExploreRoom[]; facilities: ExploreFacility[];
};
export type ExploreManifest = SceneCatalogue & {
  schema_version: number;
  assets: { path: string; hash: string; bytes: number }[];
  coordinate_system: { units: string; up_axis: "Z"; glb_up_axis: "Y"; to_glb: number[] };
};
export type SceneView = { lookat: XYZ; azimuth: number; elevation: number; distance: number };
export type SceneTask = {
  supported?: boolean; label?: Localized; stage?: string; success?: boolean;
  checks?: Record<string, boolean>; reason?: string;
  target?: { label: Localized; world_bounds: Bounds; world_anchor: XYZ };
};
export type SceneState = {
  ok?: boolean; available: boolean; active: boolean; epoch: string; session: string | null; owner: string | null;
  phase: string; phases: string[]; held: number | null; reason: string; view: SceneView;
  selection: { names: string[]; body: number; point: XYZ; distance: number } | null;
  joints: Record<string, { value?: number; fraction?: number; position?: XYZ; held?: boolean; result?: string }>;
  task: SceneTask;
};
export type SceneLease = { owner: string; token: string; epoch: string; lease_seconds: number };

export function localized(value: Localized | undefined, lang: string): string {
  return value?.[lang] ?? value?.en ?? Object.values(value ?? {})[0] ?? "";
}

async function json<T>(url: string, init?: RequestInit, timeoutMs = SCENE_REQUEST_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  init?.signal?.addEventListener("abort", cancel, { once: true });
  if (init?.signal?.aborted) cancel();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; cancel(); }, timeoutMs);
  try {
    const response = await fetch(url, { cache: "no-store", ...init, signal: controller.signal });
    const result = await response.json();
    if (!response.ok || result?.ok === false) {
      throw new ApiError(result?.detail ?? result?.message ?? response.statusText, response.status);
    }
    return result as T;
  } catch (error) {
    if (timedOut) throw new ApiError("Scene request timed out", 408);
    throw error;
  } finally {
    clearTimeout(timer);
    init?.signal?.removeEventListener("abort", cancel);
  }
}
export function getExplore(world: string, signal?: AbortSignal): Promise<ExploreManifest> {
  return json(`${BASE}/api/worlds/${encodeURIComponent(world)}/explore`, { signal }, EXPLORE_MANIFEST_TIMEOUT_MS);
}
export function exploreAsset(world: string, filename: string): string {
  return `${BASE}/api/worlds/${encodeURIComponent(world)}/explore/assets/${encodeURIComponent(filename)}`;
}
const scenePath = (sid: string) => `${BASE}/api/sessions/${encodeURIComponent(sid)}/scene`;
export function getSceneCatalogue(sid: string): Promise<SceneCatalogue> {
  return json(`${scenePath(sid)}/catalogue`);
}
export function getSceneState(sid: string): Promise<SceneState> {
  return json(`${scenePath(sid)}/state`);
}
export function sceneRequest<T = SceneState>(sid: string, operation: string, payload: Record<string, unknown>): Promise<T> {
  return json(`${scenePath(sid)}/${operation}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }, operation === "heartbeat" ? SCENE_HEARTBEAT_TIMEOUT_MS : operation === "acquire" ? SCENE_ACQUIRE_TIMEOUT_MS : SCENE_REQUEST_TIMEOUT_MS);
}
export function acquireScene(sid: string, owner: string): Promise<SceneState & SceneLease> {
  return sceneRequest(sid, "acquire", { owner });
}
export function sceneFrameUrl(sid: string, lease: SceneLease): string {
  const query = new URLSearchParams({ owner: lease.owner, token: lease.token, epoch: lease.epoch });
  return `${scenePath(sid)}/frame?${query}`;
}
export function abandonScene(sid: string, lease: SceneLease): void {
  // pagehide cannot wait for a promise. The server timeout remains the final safety net.
  const url = `${scenePath(sid)}/release`;
  const body = JSON.stringify(lease);
  if (navigator.sendBeacon?.(url, new Blob([body], { type: "application/json" }))) return;
  void fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true }).catch(() => {});
}
