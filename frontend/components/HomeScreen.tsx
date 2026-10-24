"use client";
import { useEffect, useRef, useState } from "react";
import {
  createSession,
  getRobotTask,
  POLL_NODES_MS,
  listControlSchemes,
  selectControlScheme,
  type Registry,
  type RobotDisplay,
  type SessionSummary,
  type ControlScheme,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import RobotPreview from "./RobotPreview";
import {
  sessionTargets,
  selectedTarget,
  adjacentTarget,
  type SessionTarget,
} from "@/lib/session-targets";
export type SessionDraft = {
  text: string;
  brain: string;
  body: string;
  mode: "sim" | "real";
  world: string;
  scheme: string;
};
export default function HomeScreen({
  registry,
  robot,
  sessions,
  draft,
  setDraft,
  navigate,
  onCreated,
  focusRequest,
}: {
  registry: Registry | null;
  robot: RobotDisplay | null;
  sessions: SessionSummary[];
  draft: SessionDraft;
  setDraft: (v: SessionDraft) => void;
  navigate: (route: string) => void;
  onCreated: (id: string, text: string) => void;
  focusRequest: number;
}) {
  const { t } = useI18n();
  const input = useRef<HTMLTextAreaElement>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [schemes, setSchemes] = useState<ControlScheme[]>([]);
  useEffect(() => {
    listControlSchemes()
      .then((v) => setSchemes(v.schemes))
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (focusRequest > 0) input.current?.focus();
  }, [focusRequest]);
  const configured = (registry?.brains || []).filter(
    (b) => b.available && b.name !== "operator",
  );
  const targets = sessionTargets(registry, robot);
  const selected = selectedTarget(targets, draft);
  useEffect(() => {
    const brain =
      draft.brain ||
      configured.find((b) => b.name === registry?.default_brain)?.name ||
      configured[0]?.name ||
      "";
    const target = !draft.body ? targets[0] : undefined;
    if (brain !== draft.brain || target)
      setDraft({
        ...draft,
        brain,
        ...(target
          ? {
              body: target.body,
              mode: target.mode,
              world: target.world,
              scheme: "",
            }
          : {}),
      });
  }, [registry, robot?.registered, draft]);
  function choose(target?: SessionTarget) {
    if (target && !busy)
      setDraft({
        ...draft,
        body: target.body,
        mode: target.mode,
        world: target.world,
        scheme: "",
      });
  }
  const ready = !!selected && !!configured.find((b) => b.name === draft.brain);
  async function start() {
    if (!ready || !selected || busy) return;
    setBusy(true);
    setError("");
    try {
      const session = await createSession({
        brain: draft.brain,
        body: selected.body,
        mode: selected.mode,
        world: selected.world || null,
        sensors: [],
      });
      const scheme = schemes.find(
        (s) =>
          s.id === draft.scheme &&
          s.robot === selected.body &&
          s.mode === selected.mode,
      );
      if (scheme) await selectControlScheme(session.id, scheme);
      onCreated(session.id, draft.text);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const [lastTask, setLastTask] = useState<any>(null);
  const lastRobotSession = sessions.find((s) => !!s.body);
  useEffect(() => {
    let cancelled = false;
    const read = () => {
      if (lastRobotSession)
        getRobotTask(lastRobotSession.id)
          .then((v) => {
            if (!cancelled) setLastTask(v.task);
          })
          .catch(() => {});
    };
    read();
    const timer = setInterval(read, POLL_NODES_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [lastRobotSession?.id]);
  const recent = sessions.filter((s) => s.brain !== "operator").slice(0, 3);
  return (
    <section className="page-scroll">
      <div className="home-content">
        <div className="home-insights" aria-label={t("Next steps")}>
          {lastTask && (
            <button onClick={() => navigate(`session/${lastRobotSession?.id}`)}>
              {t(lastTask.status)} · {lastTask.goal}
            </button>
          )}
          {selected && (
            <span>
              {t("Selected scene")}: {t(selected.sceneLabel)}
            </span>
          )}
        </div>
        {registry &&
          robot &&
          (!configured.length || !robot.registered || !targets.length) && (
            <section className="home-todos" aria-label={t("Setup checklist")}>
              <h2>{t("Setup checklist")}</h2>
              {!configured.length && (
                <div className="home-todo">
                  <div>
                    <b>{t("Configure a chat model")}</b>
                    <p>
                      {t("Save a model key before starting an Agent session.")}
                    </p>
                  </div>
                  <button
                    className="nerv-control action"
                    onClick={() => navigate("settings/models")}
                  >
                    {t("Model settings")}
                  </button>
                </div>
              )}
              {!robot.registered && (
                <div className="home-todo">
                  <div>
                    <b>{t("Register your robot")}</b>
                    <p>
                      {t(
                        "Save your real robot profile. Installed simulation scenes can be used without it.",
                      )}
                    </p>
                  </div>
                  <button
                    className="nerv-control action"
                    onClick={() => navigate("robots")}
                  >
                    {t("Register SO-101")}
                  </button>
                </div>
              )}
              {!targets.length && (
                <div className="home-todo">
                  <div>
                    <b>{t("Add a scene to get started")}</b>
                    <p>
                      {t(
                        "Register a real robot or install a simulation component.",
                      )}
                    </p>
                  </div>
                  <button
                    className="nerv-control action"
                    onClick={() => navigate("settings/components")}
                  >
                    {t("Open components")}
                  </button>
                </div>
              )}
            </section>
          )}
        <div className="home-grid">
          <div className="home-composer">
            <p className="text-sm text-neutral-400">NERV</p>
            <h1>{t("What would you like to do?")}</h1>
            <p className="muted">
              {t("Choose a scene and describe what you want the robot to do.")}
            </p>
            <div className="composer-surface">
              <textarea
                ref={input}
                value={draft.text}
                onChange={(e) => setDraft({ ...draft, text: e.target.value })}
                placeholder={t("Describe a task or ask a question…")}
                aria-label={t("Task or question")}
              />
              <div className="composer-options">
                <label className="form-label">
                  {t("Chat model")}
                  <select
                    value={draft.brain}
                    onChange={(e) =>
                      setDraft({ ...draft, brain: e.target.value })
                    }
                  >
                    <option value="">{t("Choose a configured model")}</option>
                    {configured.map((b) => (
                      <option key={b.name} value={b.name}>
                        {b.label}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="form-label">
                  {t("Selected scene")}
                  <p className="selected-scene-summary" aria-live="polite">
                    {selected
                      ? `${selected.mode === "real" ? "Real" : "Sim"} · ${selected.robotLabel} · ${t(selected.sceneLabel)}`
                      : t("Choose a robot and scene")}
                  </p>
                </div>
              </div>
              {selected && (
                <label className="form-label">
                  {t("Control scheme")}
                  <select
                    className="form-field"
                    value={draft.scheme}
                    onChange={(e) =>
                      setDraft({ ...draft, scheme: e.target.value })
                    }
                  >
                    <option value="">
                      {t("Choose later; motion stays disabled")}
                    </option>
                    {schemes
                      .filter(
                        (s) => s.robot === draft.body && s.mode === draft.mode,
                      )
                      .map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              <div className="flex items-center justify-between gap-3 pt-4">
                <button
                  className="nerv-control text-sm underline"
                  onClick={() => navigate("settings/models")}
                >
                  {t("Model settings")}
                </button>
                <button
                  className="nerv-control nerv-primary action"
                  disabled={!ready || busy}
                  onClick={start}
                >
                  {t(busy ? "Creating…" : "Start session")}
                </button>
              </div>
            </div>
            {selected && (
              <p className="mt-3 text-xs text-neutral-400">
                {t(
                  "Creating a session does not connect motors or enable motion.",
                )}
              </p>
            )}
            {error && (
              <p role="alert" className="error-text">
                {error}
              </p>
            )}
          </div>
          <aside
            className="home-robot"
            aria-label={t("Session scene")}
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.target !== event.currentTarget) return;
              if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                choose(
                  adjacentTarget(
                    targets,
                    draft,
                    event.key === "ArrowLeft" ? -1 : 1,
                  ),
                );
              }
            }}
          >
            <div className="scene-switcher">
              <button
                className="nerv-control scene-arrow"
                aria-label={t("Previous scene")}
                disabled={targets.length < 2 || busy}
                onClick={() => choose(adjacentTarget(targets, draft, -1))}
              >
                ‹
              </button>
              <span>
                {selected
                  ? `${selected.mode === "real" ? "Real" : "Sim"} · ${targets.indexOf(selected) + 1} / ${targets.length}`
                  : t("No scene available")}
              </span>
              <button
                className="nerv-control scene-arrow"
                aria-label={t("Next scene")}
                disabled={targets.length < 2 || busy}
                onClick={() => choose(adjacentTarget(targets, draft, 1))}
              >
                ›
              </button>
            </div>
            {selected ? (
              <>
                {selected.body === "arm-lerobot-so101" ? (
                  <RobotPreview key={selected.id} image={selected.image} />
                ) : (
                  <div
                    className="robot-preview-stage flex items-center justify-center text-5xl"
                    aria-hidden="true"
                  >
                    ◇
                  </div>
                )}
                <div aria-live="polite">
                  <h2>{selected.robotLabel}</h2>
                  <p className="muted mt-2">{t(selected.sceneLabel)}</p>
                </div>
                <label className="form-label mt-4 text-left">
                  {t("Session scene")}
                  <select
                    className="form-field"
                    value={selected.id}
                    disabled={busy}
                    onChange={(event) =>
                      choose(
                        targets.find(
                          (target) => target.id === event.target.value,
                        ),
                      )
                    }
                  >
                    {targets.map((target) => (
                      <option key={target.id} value={target.id}>
                        {target.mode === "real" ? "Real" : "Sim"} ·{" "}
                        {target.robotLabel} · {t(target.sceneLabel)}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="nerv-control action mt-4"
                  onClick={() =>
                    navigate(
                      selected.mode === "real" ? "robots" : "settings/explore",
                    )
                  }
                >
                  {t(
                    selected.mode === "real"
                      ? "View robot"
                      : "Installed simulation scenes",
                  )}
                </button>
              </>
            ) : (
              <div className="scene-empty">
                <p>{t("Choose a robot and scene")}</p>
                <p className="muted mt-2">
                  {t(
                    "Register a real robot or install a simulation component.",
                  )}
                </p>
              </div>
            )}
          </aside>
        </div>
        <div className="home-secondary">
          <div>
            <h2>{t("Recent sessions")}</h2>
            {recent.length ? (
              recent.map((s) => (
                <button
                  className="recent-item"
                  key={s.id}
                  onClick={() => navigate(`session/${s.id}`)}
                >
                  <span>{s.title}</span>
                  <span className="text-xs text-neutral-500">
                    {s.created_at}
                  </span>
                </button>
              ))
            ) : (
              <p className="muted mt-3">
                {t("Your conversations will appear here.")}
              </p>
            )}
          </div>
          <div>
            <h2>{t("Use simulation")}</h2>
            <p className="muted mt-3">
              {t("Install a workspace and explore without a physical arm.")}
            </p>
            <button
              className="nerv-control action mt-4"
              onClick={() => navigate("settings/components")}
            >
              {t("Open components")}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
