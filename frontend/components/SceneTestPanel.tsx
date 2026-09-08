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
const LEASE_WATCHDOG_MS = 100; // An independent local deadline also catches a hung heartbeat.
const SCENE_FRAME_TIMEOUT_MS = 2000; // A stalled frame must not stop subsequent camera polling.
const RESULT_LABELS: Record<string, string> = {
  idle: "Idle", selected: "Selected", moving: "Moving", reached: "Completed", blocked: "Blocked", cancelled: "Cancelled",
  occluded_or_out_of_reach: "Occluded or out of reach", lease_expired: "Scene control expired", robot_fallen: "The robot fell; scene control ended",
  open_door: "Open the refrigerator door", place_object: "Place the can on the target shelf", release_object: "Release the can",
  wait_until_settled: "Wait for the can to settle", close_door: "Close the refrigerator door", complete: "Task completed",
  dawn: "Dawn", morning: "Dawn", day: "Day", dusk: "Dusk", night: "Night",
  opened: "Door opened", inside: "Fully inside", released: "Released", settled: "Settled", closed: "Door closed",
  supported: "Supported by the shelf", contact_valid: "No excessive penetration",
  invalid_contact: "Contact exceeded the tolerance. Reset the scene to retry.",
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
  const [resetPending, setResetPending] = useState(false);
  const [facility, setFacility] = useState("");
  const [target, setTarget] = useState(0);
  const [jointChoice, setJointChoice] = useState("");
  const [depth, setDepth] = useState(1);
  const mounted = useRef(true);
  const leaseRef = useRef<SceneLease | null>(null);
  const stateRef = useRef<SceneState | null>(null);
  const confirmedView = useRef<SceneView | null>(null);
  const owner = useRef("");
  const lifecycle = useRef(0);
  const commandSequence = useRef(0);
  const commandTail = useRef<Promise<unknown>>(Promise.resolve());
  const commandGeneration = useRef(0);
  const commandsPending = useRef(0);
  const leaseRenewedAt = useRef(0);
  const lastXY = useRef<[number, number]>([0, 0]);
  const commandBusy = useRef(false);
  const dragging = useRef(false);
  const latestDrag = useRef<{ xy: [number, number]; distance: number } | null>(null);
  const changed = useRef(onStateChanged);
  changed.current = onStateChanged;

  const update = useCallback((next: SceneState) => {
    if (!mounted.current) return;
    stateRef.current = next;
    confirmedView.current = next.view;
    setState(next);
    if (!next.active && leaseRef.current) {
      leaseRef.current = null;
      setLease(null);
      dragging.current = false;
      latestDrag.current = null;
    }
  }, []);
  const relinquish = useCallback(() => {
    lifecycle.current += 1;
    const current = leaseRef.current;
    leaseRef.current = null;
    latestDrag.current = null;
    dragging.current = false;
    if (current) abandonScene(sessionId, current);
  }, [sessionId]);

  useEffect(() => {
    mounted.current = true;
    owner.current = crypto.randomUUID();
    const load = () => {
      const generation = lifecycle.current;
      void Promise.all([getSceneCatalogue(sessionId), getSceneState(sessionId)])
        .then(([cat, next]) => { if (mounted.current && generation === lifecycle.current) { setCatalogue(cat); update(next); } })
        .catch((e: Error) => mounted.current && generation === lifecycle.current && setError(e.message));
    };
    load();
    const pagehide = () => { relinquish(); if (mounted.current) { setLease(null); setBusy(false); } };
    const pageshow = (event: PageTransitionEvent) => { if (event.persisted) load(); };
    window.addEventListener("pagehide", pagehide);
    window.addEventListener("pageshow", pageshow);
    return () => {
      mounted.current = false;
      window.removeEventListener("pagehide", pagehide);
      window.removeEventListener("pageshow", pageshow);
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
      const sequence = commandSequence.current;
      const idleAtStart = commandsPending.current === 0;
      const started = performance.now();
      try {
        const next = await sceneRequest(sessionId, "heartbeat", lease);
        if (!stopped && leaseRef.current?.token === lease.token) {
          leaseRenewedAt.current = started;
          if (idleAtStart && commandsPending.current === 0 && sequence === commandSequence.current) update(next);
        }
      } catch (e) {
        if (!stopped) { relinquish(); setLease(null); setError((e as Error).message); changed.current(); }
      } finally { pending = false; }
    };
    const timer = setInterval(tick, SCENE_HEARTBEAT_MS);
    const watchdog = setInterval(() => {
      if (!stopped && leaseRef.current?.token === lease.token && performance.now() - leaseRenewedAt.current >= lease.lease_seconds * 1000) {
        relinquish(); setLease(null); setError("Scene control expired"); changed.current();
      }
    }, LEASE_WATCHDOG_MS);
    return () => { stopped = true; clearInterval(timer); clearInterval(watchdog); };
  }, [lease, sessionId, update, relinquish]);

  const command = useCallback((action: string, values: Record<string, unknown> | (() => Record<string, unknown>) = {}) => {
    const current = leaseRef.current;
    if (!current) return Promise.resolve(false);
    const sequence = ++commandSequence.current;
    // Camera moves and their following drag rays must reach physics in order.
    // Cancel/release bypass pending requests, invalidate queued work and rely on
    // the world's sequence watermark to reject any older request already in flight.
    const urgent = action === "cancel" || action === "release";
    if (urgent) commandGeneration.current += 1;
    const generation = commandGeneration.current;
    commandsPending.current += 1;
    const send = async () => {
      try {
        if (leaseRef.current?.token !== current.token || generation !== commandGeneration.current) return false;
        const payload = typeof values === "function" ? values() : values;
        const next = await sceneRequest(sessionId, "command", { ...current, action, ...payload, sequence });
        if (leaseRef.current?.token === current.token && generation === commandGeneration.current) confirmedView.current = next.view;
        if (leaseRef.current?.token === current.token && sequence === commandSequence.current) { update(next); setError(""); }
        return true;
      } catch (e) {
        if (mounted.current && leaseRef.current?.token === current.token && sequence === commandSequence.current) setError((e as Error).message);
        return false;
      } finally { commandsPending.current -= 1; }
    };
    const result = urgent ? send() : commandTail.current.then(send, send);
    commandTail.current = result;
    return result;
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
    const generation = ++lifecycle.current;
    try {
      const next = await acquireScene(sessionId, owner.current);
      const acquired = { owner: next.owner, token: next.token, epoch: next.epoch, lease_seconds: next.lease_seconds } as SceneLease;
      if (!mounted.current || lifecycle.current !== generation) { abandonScene(sessionId, acquired); return; }
      leaseRenewedAt.current = performance.now();
      leaseRef.current = acquired;
      setLease(acquired);
      update(next);
      changed.current();
    } catch (e) { if (mounted.current && generation === lifecycle.current) setError((e as Error).message); }
    finally { if (mounted.current && generation === lifecycle.current) setBusy(false); }
  }
  async function leave() {
    lifecycle.current += 1;
    const current = leaseRef.current;
    leaseRef.current = null; latestDrag.current = null; dragging.current = false; setLease(null);
    changed.current();
    onClose();
    // UI exit never waits on network; only pagehide/unmount use the beacon path.
    try { if (current) await sceneRequest(sessionId, "release", current); } catch { /* The server deadline revokes a disconnected owner. */ }
  }
  async function reset() {
    setResetPending(false);
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
  function view(change: (current: SceneView) => Partial<SceneView>) {
    void command("view", () => {
      const current = confirmedView.current;
      if (!current) throw new Error("Scene camera is unavailable");
      return change(current);
    });
  }
  function pan(horizontal: number, vertical: number) {
    view((current) => {
      const az = current.azimuth * Math.PI / 180, el = current.elevation * Math.PI / 180;
      const right = [Math.sin(az), -Math.cos(az), 0];
      const up = [-Math.sin(el) * Math.cos(az), -Math.sin(el) * Math.sin(az), Math.cos(el)];
      return { lookat: current.lookat.map((value, i) => value + VIEW_PAN_M * (horizontal * right[i] + vertical * up[i])) as [number, number, number] };
    });
  }
  const items = catalogue?.facilities ?? [];
  const selectedNames = state?.selection?.names ?? [];
  const joints = items.filter((f) => f.joint && selectedNames.includes(f.joint));
  const focused = items.find((f) => f.id === facility);
  const selectedJoint = joints.find((f) => f.joint === jointChoice)?.joint
    ?? joints.find((f) => f.joint === focused?.joint)?.joint ?? joints[0]?.joint;
  const selectedState = selectedJoint ? state?.joints[selectedJoint] : null;
  const isMovable = selectedNames.some((name) => state?.joints[name]?.position);
  const inspectedMovables = Object.entries(state?.joints ?? {}).filter(([name, value]) =>
    value.position && (selectedNames.includes(name) || value.held || value.body === focused?.body));
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
          <p className="max-w-md text-xs leading-relaxed text-neutral-400">{t("Scene testing stops the current task and disarms the robot after it settles. Inspect facilities and use the controls supported by this world.")}</p>
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
                {items.map((f) => <option key={f.id} value={f.id}>{localized(f.label, lang)}{f.room ? ` · ${localized(catalogue?.rooms.find((r) => r.id === f.room)?.label, lang)}` : ""}</option>)}
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
                { label: "Orbit left", text: "◀", patch: (v: SceneView) => ({ azimuth: v.azimuth - VIEW_ORBIT_DEG }) },
                { label: "Orbit right", text: "▶", patch: (v: SceneView) => ({ azimuth: v.azimuth + VIEW_ORBIT_DEG }) },
                { label: "Tilt up", text: "▲", patch: (v: SceneView) => ({ elevation: v.elevation + VIEW_TILT_DEG }) },
                { label: "Tilt down", text: "▼", patch: (v: SceneView) => ({ elevation: v.elevation - VIEW_TILT_DEG }) },
                { label: "Zoom in", text: "+", patch: (v: SceneView) => ({ distance: v.distance / VIEW_ZOOM_FACTOR }) },
                { label: "Zoom out", text: "−", patch: (v: SceneView) => ({ distance: v.distance * VIEW_ZOOM_FACTOR }) },
              ].map((c) => <button key={c.label} className={btn} title={t(c.label)} aria-label={t(c.label)} onClick={() => view(c.patch)}>{c.text}</button>)}
              <span className="mx-0.5 border-l border-neutral-600" />
              <button className={btn} title={t("Pan left")} aria-label={t("Pan left")} onClick={() => pan(-1, 0)}>←</button>
              <button className={btn} title={t("Pan right")} aria-label={t("Pan right")} onClick={() => pan(1, 0)}>→</button>
              <button className={btn} title={t("Pan up")} aria-label={t("Pan up")} onClick={() => pan(0, 1)}>↑</button>
              <button className={btn} title={t("Pan down")} aria-label={t("Pan down")} onClick={() => pan(0, -1)}>↓</button>
            </div>
          </div>
          <div className="max-h-[42%] shrink-0 space-y-2 overflow-auto p-3 text-[11px]">
            <p className="text-neutral-400">{state?.held != null ? t("Drag the object in the image. Releasing the pointer releases the object. Adjust depth to move it closer or farther.") : items.some((item) => item.kind === "joint" || item.kind === "movable") ? t("Click the visible moving part to select it. Selection checks occlusion and a two-metre reach.") : t("Select a facility to inspect its location and test instructions. Furniture in this world is fixed.")}</p>
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
            </div>}
            {inspectedMovables.map(([name, value]) => <p key={name} className="tabular-nums text-neutral-400">{value.position?.map((v) => v.toFixed(2)).join(", ")} m · {statusLabel(value.result ?? "idle")}</p>)}
            <div className="flex flex-wrap items-center gap-2">
              <span role="status" className="min-w-0 flex-1 text-neutral-500">{statusLabel(state?.reason ?? "idle")}</span>
              <button className={btn} onClick={() => { latestDrag.current = null; dragging.current = false; void command("cancel"); }}>{t("Cancel interaction")}</button>
              <button className={btn} onClick={() => setResetPending(true)} disabled={busy}>{t("Reset whole scene")}</button>
            </div>
            {resetPending && <div role="alertdialog" aria-modal="false" aria-label={t("Reset whole scene")} className="space-y-2 rounded-lg border border-amber-700 bg-neutral-950 p-3">
              <p>{t("Reset the whole scene? The robot, furniture and props return to their initial positions. The current time of day is kept.")}</p>
              <div className="flex gap-2">
                <button className={btn} onClick={() => void reset()} disabled={busy}>{t("Reset whole scene")}</button>
                <button className={btn} onClick={() => setResetPending(false)}>{t("Cancel")}</button>
              </div>
            </div>}
            {error && <p role="alert" className="text-red-400">{t(error)}</p>}
            {state?.task.supported && <div className="rounded-lg border border-neutral-700 p-2" aria-label={t("Task progress")}>
              <p className="font-medium">{localized(state.task.label, lang)}</p>
              {state.task.target && <p className="mt-1 text-blue-400">{localized(state.task.target.label, lang)}</p>}
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
      const request = new AbortController();
      const cancel = () => request.abort();
      abort.signal.addEventListener("abort", cancel, { once: true });
      const deadline = setTimeout(cancel, SCENE_FRAME_TIMEOUT_MS);
      try {
        const response = await fetch(sceneFrameUrl(sessionId, lease), { cache: "no-store", signal: request.signal });
        if (!response.ok) throw new Error("The inspection camera is unavailable");
        const blob = await response.blob();
        if (abort.signal.aborted || !image.current) return;
        const previous = objectUrl;
        objectUrl = URL.createObjectURL(blob);
        image.current.src = objectUrl;
        image.current.dataset.simTime = response.headers.get("X-Sim-Time") ?? "";
        if (previous) URL.revokeObjectURL(previous);
      } catch (e) {
        if (!abort.signal.aborted) reportError.current(request.signal.aborted ? "The inspection camera is unavailable" : (e as Error).message);
      } finally {
        clearTimeout(deadline);
        abort.signal.removeEventListener("abort", cancel);
      }
      if (!abort.signal.aborted) timer = setTimeout(tick, Math.max(0, 1000 / SCENE_FRAME_FPS - (performance.now() - start)));
    };
    void tick();
    return () => { abort.abort(); clearTimeout(timer); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [sessionId, lease]);
  return <img ref={image} alt={t("Inspection camera")} draggable={false} className="max-h-full max-w-full touch-none select-none object-contain"
    onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} />;
}
