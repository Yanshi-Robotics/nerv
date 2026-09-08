"use client";

import { useCallback, useEffect, useRef, useState, type PointerEvent } from "react";

import { resetSessionWorld } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  abandonScene, acquireScene, getSceneCatalogue, getSceneState, localized, sceneFrameUrl, sceneRequest,
  SCENE_FRAME_FPS, SCENE_HEARTBEAT_MS, type SceneCatalogue, type SceneLease, type SceneState, type SceneView,
} from "@/lib/world-explore";

const DRAG_REACH_M = 2; // Matches the scene operator's physical selection range.
const DRAG_MIN_M = 0.15;
const DRAG_DEPTH_STEP_M = 0.08;
const VIEW_ORBIT_DEG = 15;
const VIEW_TILT_DEG = 10;
const VIEW_ZOOM_FACTOR = 1.2;
const VIEW_PAN_M = 0.2; // Small camera translations make close-range object placement controllable.
const DRAG_SEND_MS = 50; // At most one pointer command in flight, with a latest-target update.
const RESULT_LABELS: Record<string, string> = {
  idle: "Idle", selected: "Selected", moving: "Moving", reached: "Completed", blocked: "Blocked", cancelled: "Cancelled",
  occluded_or_out_of_reach: "Occluded or out of reach", lease_expired: "Scene control expired", robot_fallen: "The robot fell; scene control ended",
  open_door: "Open the refrigerator door", place_object: "Place the can on the target shelf", release_object: "Release the can",
  wait_until_settled: "Wait for the can to settle", close_door: "Close the refrigerator door", complete: "Task completed",
  dawn: "Dawn", morning: "Dawn", day: "Day", dusk: "Dusk", night: "Night",
  opened: "Door opened", inside: "Fully inside", released: "Released", settled: "Settled", closed: "Door closed",
  supported: "Supported by the shelf", no_penetration: "No excessive penetration",
};

export default function SceneTestPanel({ sessionId, onClose, onStateChanged }: {
  sessionId: string; onClose: () => void; onStateChanged: () => void;
}) {
  const { t, lang } = useI18n();
  const [catalogue, setCatalogue] = useState<SceneCatalogue | null>(null);
  const [state, setState] = useState<SceneState | null>(null);
  const [lease, setLease] = useState<SceneLease | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [facility, setFacility] = useState("");
  const [target, setTarget] = useState(0);
  const [jointChoice, setJointChoice] = useState("");
  const [depth, setDepth] = useState(1);
  const mounted = useRef(true);
  const leaseRef = useRef<SceneLease | null>(null);
  const stateRef = useRef<SceneState | null>(null);
  const owner = useRef("");
  const lastXY = useRef<[number, number]>([0, 0]);
  const commandBusy = useRef(false);
  const dragging = useRef(false);
  const latestDrag = useRef<{ xy: [number, number]; distance: number } | null>(null);
  const changed = useRef(onStateChanged);
  changed.current = onStateChanged;

  const update = useCallback((next: SceneState) => {
    if (!mounted.current) return;
    stateRef.current = next;
    setState(next);
    if (!next.active && leaseRef.current) {
      leaseRef.current = null;
      setLease(null);
      dragging.current = false;
      latestDrag.current = null;
    }
  }, []);
  const relinquish = useCallback(() => {
    const current = leaseRef.current;
    leaseRef.current = null;
    latestDrag.current = null;
    dragging.current = false;
    if (current) abandonScene(sessionId, current);
  }, [sessionId]);

  useEffect(() => {
    mounted.current = true;
    owner.current = crypto.randomUUID();
    Promise.all([getSceneCatalogue(sessionId), getSceneState(sessionId)])
      .then(([cat, next]) => { if (mounted.current) { setCatalogue(cat); update(next); } })
      .catch((e: Error) => mounted.current && setError(e.message));
    const pagehide = () => { relinquish(); if (mounted.current) setLease(null); };
    window.addEventListener("pagehide", pagehide);
    return () => {
      mounted.current = false;
      window.removeEventListener("pagehide", pagehide);
      relinquish();
    };
  }, [sessionId, update, relinquish]);

  useEffect(() => {
    if (!lease) return;
    let stopped = false;
    let pending = false;
    const tick = async () => {
      if (pending || stopped || leaseRef.current?.token !== lease.token) return;
      pending = true;
      try {
        const next = await sceneRequest(sessionId, "heartbeat", lease);
        if (!stopped) update(next);
      } catch (e) {
        if (!stopped) { relinquish(); setLease(null); setError((e as Error).message); changed.current(); }
      } finally { pending = false; }
    };
    const timer = setInterval(tick, SCENE_HEARTBEAT_MS);
    return () => { stopped = true; clearInterval(timer); };
  }, [lease, sessionId, update, relinquish]);

  const command = useCallback(async (action: string, values: Record<string, unknown> = {}) => {
    const current = leaseRef.current;
    if (!current) return false;
    try {
      const next = await sceneRequest(sessionId, "command", { ...current, action, ...values });
      if (leaseRef.current?.token === current.token) { update(next); setError(""); }
      return true;
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
      return false;
    }
  }, [sessionId, update]);

  useEffect(() => {
    if (!lease) return;
    let stopped = false;
    const timer = setInterval(async () => {
      if (stopped || commandBusy.current || !latestDrag.current) return;
      const move = latestDrag.current;
      latestDrag.current = null;
      commandBusy.current = true;
      try { await command("drag", move); } finally { commandBusy.current = false; }
    }, DRAG_SEND_MS);
    return () => { stopped = true; clearInterval(timer); latestDrag.current = null; };
  }, [lease, command]);

  async function enter() {
    setBusy(true); setError("");
    try {
      const next = await acquireScene(sessionId, owner.current);
      const acquired = { owner: next.owner, token: next.token, epoch: next.epoch, lease_seconds: next.lease_seconds } as SceneLease;
      if (!mounted.current) { abandonScene(sessionId, acquired); return; }
      leaseRef.current = acquired;
      setLease(acquired);
      update(next);
      changed.current();
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function leave() {
    setBusy(true);
    const current = leaseRef.current;
    relinquish(); setLease(null);
    try { if (current) await sceneRequest(sessionId, "release", current); } catch { /* The timeout also revokes a disconnected owner. */ }
    changed.current();
    onClose();
  }
  async function reset() {
    if (!confirm(t("Reset the whole scene? The robot, furniture and props return to their initial positions. The current time of day is kept."))) return;
    setBusy(true);
    relinquish(); setLease(null);
    try {
      const result = await resetSessionWorld(sessionId);
      if (!result.ok) throw new Error(result.message);
      update(await getSceneState(sessionId));
      setError(""); changed.current();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function xyFromEvent(e: PointerEvent<HTMLImageElement>): [number, number] {
    const rect = e.currentTarget.getBoundingClientRect();
    return [Math.max(-1, Math.min(1, 2 * (e.clientX - rect.left) / rect.width - 1)),
      Math.max(-1, Math.min(1, 1 - 2 * (e.clientY - rect.top) / rect.height))];
  }
  function pointerDown(e: PointerEvent<HTMLImageElement>) {
    if (!lease || e.button !== 0) return;
    e.preventDefault();
    lastXY.current = xyFromEvent(e);
    if (stateRef.current?.held != null) {
      dragging.current = true;
      e.currentTarget.setPointerCapture(e.pointerId);
      latestDrag.current = { xy: lastXY.current, distance: depth };
    } else void command("select", { xy: lastXY.current });
  }
  function pointerMove(e: PointerEvent<HTMLImageElement>) {
    if (!dragging.current) return;
    lastXY.current = xyFromEvent(e);
    latestDrag.current = { xy: lastXY.current, distance: depth };
  }
  function pointerUp(e: PointerEvent<HTMLImageElement>) {
    if (!dragging.current) return;
    dragging.current = false; latestDrag.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    void command("release");
  }
  async function grab() {
    if (await command("grab", { xy: lastXY.current })) setDepth(stateRef.current?.selection?.distance ?? depth);
  }
  function view(patch: Partial<SceneView>) {
    void command("view", { ...state?.view, ...patch });
  }
  function pan(horizontal: number, vertical: number) {
    if (!state?.view) return;
    const az = state.view.azimuth * Math.PI / 180, el = state.view.elevation * Math.PI / 180;
    const right = [Math.sin(az), -Math.cos(az), 0];
    const up = [-Math.sin(el) * Math.cos(az), -Math.sin(el) * Math.sin(az), Math.cos(el)];
    view({ lookat: state.view.lookat.map((value, i) => value + VIEW_PAN_M * (horizontal * right[i] + vertical * up[i])) as [number, number, number] });
  }
  const items = catalogue?.facilities ?? [];
  const selectedNames = state?.selection?.names ?? [];
  const joints = items.filter((f) => f.joint && selectedNames.includes(f.joint));
  const focused = items.find((f) => f.id === facility);
  const selectedJoint = joints.find((f) => f.joint === jointChoice)?.joint
    ?? joints.find((f) => f.joint === focused?.joint)?.joint ?? joints[0]?.joint;
  const selectedState = selectedJoint ? state?.joints[selectedJoint] : null;
  const isMovable = selectedNames.some((name) => state?.joints[name]?.position);
  const statusLabel = (key: string) => t(RESULT_LABELS[key] ?? key);
  const btn = "rounded-md border border-neutral-700 bg-neutral-800 px-2 py-1.5 text-[11px] text-neutral-200 hover:bg-neutral-700 disabled:opacity-40";

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-xl border border-neutral-700 bg-neutral-900" aria-label={t("Scene test")}>
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-neutral-800 px-3 py-2">
        <span className="text-xs font-medium">{t("Scene test")}</span>
        <span className="text-[10px] text-neutral-500">{t("Operator only")}</span>
        <button className={`${btn} ml-auto`} onClick={leave} disabled={busy}>{t("Exit scene test")}</button>
      </header>
      {!lease ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-5 text-center">
          <p className="max-w-md text-xs leading-relaxed text-neutral-400">{t("Scene testing stops the current task and disarms the robot after it settles. You can then operate furniture from the inspection camera.")}</p>
          <button className={`${btn} border-blue-600 bg-blue-600 text-white`} disabled={busy || !state?.available} onClick={enter}>
            {busy ? t("Waiting for the robot to settle…") : t("Start scene test")}
          </button>
          {state && !state.available && <p className="text-xs text-neutral-500">{t("This world does not offer scene tests.")}</p>}
          {error && <p role="alert" className="max-w-md text-xs text-red-400">{t(error)}</p>}
        </div>
      ) : (
        <>
          <div className="flex shrink-0 flex-wrap gap-2 px-3 py-2">
            <label className="flex min-w-0 flex-1 items-center gap-2 text-[11px] text-neutral-400">{t("Facility")}
              <select aria-label={t("Facility")} className="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-800 p-1.5 text-neutral-200" value={facility} disabled={state?.held != null}
                onChange={(e) => { setFacility(e.target.value); setJointChoice(items.find((f) => f.id === e.target.value)?.joint ?? ""); lastXY.current = [0, 0]; void command("focus", { facility: e.target.value }); }}>
                <option value="" disabled>{t("Choose a facility")}</option>
                {items.map((f) => <option key={f.id} value={f.id}>{localized(f.label, lang)} · {f.room ? localized(catalogue?.rooms.find((r) => r.id === f.room)?.label, lang) : t("Outdoor")}</option>)}
              </select>
            </label>
            <select aria-label={t("Time of day")} value={state?.phase ?? "day"} onChange={(e) => void command("time", { phase: e.target.value })} className="rounded border border-neutral-700 bg-neutral-800 p-1.5 text-[11px]">
              {state?.phases.map((phase) => <option key={phase} value={phase}>{statusLabel(phase)}</option>)}
            </select>
          </div>
          <div className="relative flex min-h-24 flex-1 items-center justify-center overflow-hidden bg-black">
            <SceneFrame sessionId={sessionId} lease={lease} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onError={setError} />
            <div className="absolute bottom-2 right-2 flex max-w-[95%] flex-wrap justify-end gap-1 rounded-md bg-neutral-950/90 p-1" role="group" aria-label={t("Inspection camera")}>
              {[
                { label: "Orbit left", text: "◀", patch: { azimuth: (state?.view.azimuth ?? 0) - VIEW_ORBIT_DEG } },
                { label: "Orbit right", text: "▶", patch: { azimuth: (state?.view.azimuth ?? 0) + VIEW_ORBIT_DEG } },
                { label: "Tilt up", text: "▲", patch: { elevation: (state?.view.elevation ?? 0) + VIEW_TILT_DEG } },
                { label: "Tilt down", text: "▼", patch: { elevation: (state?.view.elevation ?? 0) - VIEW_TILT_DEG } },
                { label: "Zoom in", text: "+", patch: { distance: (state?.view.distance ?? 1) / VIEW_ZOOM_FACTOR } },
                { label: "Zoom out", text: "−", patch: { distance: (state?.view.distance ?? 1) * VIEW_ZOOM_FACTOR } },
              ].map((c) => <button key={c.label} className={btn} title={t(c.label)} aria-label={t(c.label)} onClick={() => view(c.patch)}>{c.text}</button>)}
              <span className="mx-0.5 border-l border-neutral-600" />
              <button className={btn} title={t("Pan left")} aria-label={t("Pan left")} onClick={() => pan(-1, 0)}>←</button>
              <button className={btn} title={t("Pan right")} aria-label={t("Pan right")} onClick={() => pan(1, 0)}>→</button>
              <button className={btn} title={t("Pan up")} aria-label={t("Pan up")} onClick={() => pan(0, 1)}>↑</button>
              <button className={btn} title={t("Pan down")} aria-label={t("Pan down")} onClick={() => pan(0, -1)}>↓</button>
            </div>
          </div>
          <div className="max-h-[42%] shrink-0 space-y-2 overflow-auto p-3 text-[11px]">
            <p className="text-neutral-400">{state?.held != null ? t("Drag the object in the image. Releasing the pointer releases the object. Adjust depth to move it closer or farther.") : t("Click the visible moving part to select it. Selection checks occlusion and a two-metre reach.")}</p>
            {focused && <p className="text-neutral-500">{localized(focused.test, lang)}</p>}
            {joints.length > 0 && <div className="flex flex-wrap items-center gap-2">
              <select aria-label={t("Selected joint")} value={selectedJoint} onChange={(e) => setJointChoice(e.target.value)} className="max-w-full rounded border border-neutral-700 bg-neutral-800 px-2 py-1.5">
                {joints.map((f) => <option key={f.joint} value={f.joint}>{localized(f.label, lang)}</option>)}
              </select>
              <input type="range" min="0" max="1" step="0.01" value={target} aria-label={t("Target opening")} onChange={(e) => setTarget(Number(e.target.value))} className="min-w-20 flex-1" />
              <button className={btn} onClick={() => void command("joint", { joint: selectedJoint, fraction: target, xy: lastXY.current })}>{t("Apply opening")}</button>
              <span className="tabular-nums text-neutral-400">{t("Measured")}: {Math.round((selectedState?.fraction ?? 0) * 100)}% · {statusLabel(selectedState?.result ?? "idle")}</span>
            </div>}
            {(isMovable || state?.held != null) && <div className="flex flex-wrap items-center gap-2">
              {state?.held == null ? <button className={btn} onClick={grab}>{t("Grab object")}</button> : <>
                <label className="flex items-center gap-2">{t("Depth")}
                  <input type="range" min={DRAG_MIN_M} max={DRAG_REACH_M} step={DRAG_DEPTH_STEP_M / 4} value={depth} aria-label={t("Drag depth")}
                    onChange={(e) => { const d = Number(e.target.value); setDepth(d); latestDrag.current = { xy: lastXY.current, distance: d }; }} />
                  <span className="tabular-nums">{depth.toFixed(2)} m</span>
                </label>
                <button className={btn} onClick={() => { latestDrag.current = null; void command("release"); }}>{t("Release object")}</button>
              </>}
              {selectedNames.map((name) => state?.joints[name]?.position && <span key={name} className="font-mono text-neutral-400">{state.joints[name].position?.map((v) => v.toFixed(2)).join(", ")} m</span>)}
            </div>}
            <div className="flex flex-wrap items-center gap-2">
              <span role="status" className="min-w-0 flex-1 text-neutral-500">{statusLabel(state?.reason ?? "idle")}</span>
              <button className={btn} onClick={() => { latestDrag.current = null; dragging.current = false; void command("cancel"); }}>{t("Cancel interaction")}</button>
              <button className={btn} onClick={reset} disabled={busy}>{t("Reset whole scene")}</button>
            </div>
            {error && <p role="alert" className="text-red-400">{t(error)}</p>}
            {state?.task.supported && <div className="rounded-lg border border-neutral-700 p-2" aria-label={t("Task progress")}>
              <p className="font-medium">{localized(state.task.label, lang)}</p>
              <p role="status" className={state.task.success ? "mt-1 text-green-400" : "mt-1 text-neutral-400"}>{statusLabel(state.task.stage ?? "idle")}</p>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-neutral-400">
                {Object.entries(state.task.checks ?? {}).map(([key, value]) => <span key={key}>{value ? "✓" : "○"} {statusLabel(key)}</span>)}
              </div>
              {state.task.reason && <p className="mt-1 text-neutral-500">{t(state.task.reason)}</p>}
            </div>}
          </div>
        </>
      )}
    </section>
  );
}

function SceneFrame({ sessionId, lease, onPointerDown, onPointerMove, onPointerUp, onError }: {
  sessionId: string; lease: SceneLease; onPointerDown: (e: PointerEvent<HTMLImageElement>) => void;
  onPointerMove: (e: PointerEvent<HTMLImageElement>) => void; onPointerUp: (e: PointerEvent<HTMLImageElement>) => void;
  onError: (message: string) => void;
}) {
  const { t } = useI18n();
  const image = useRef<HTMLImageElement>(null);
  const reportError = useRef(onError);
  reportError.current = onError;
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let objectUrl: string | null = null;
    const tick = async () => {
      const start = performance.now();
      try {
        const response = await fetch(sceneFrameUrl(sessionId, lease), { cache: "no-store", signal: abort.signal });
        if (!response.ok) throw new Error("The inspection camera is unavailable");
        const blob = await response.blob();
        if (abort.signal.aborted || !image.current) return;
        const previous = objectUrl;
        objectUrl = URL.createObjectURL(blob);
        image.current.src = objectUrl;
        if (previous) URL.revokeObjectURL(previous);
      } catch (e) {
        if (!abort.signal.aborted) reportError.current((e as Error).message);
      }
      if (!abort.signal.aborted) timer = setTimeout(tick, Math.max(0, 1000 / SCENE_FRAME_FPS - (performance.now() - start)));
    };
    void tick();
    return () => { abort.abort(); clearTimeout(timer); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [sessionId, lease]);
  return <img ref={image} alt={t("Inspection camera")} draggable={false} className="max-h-full max-w-full touch-none select-none object-contain"
    onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} />;
}
