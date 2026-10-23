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
  focus,
}: {
  registry: Registry | null;
  robot: RobotDisplay | null;
  sessions: SessionSummary[];
  draft: SessionDraft;
  setDraft: (v: SessionDraft) => void;
  navigate: (route: string) => void;
  onCreated: (id: string, text: string) => void;
  focus: boolean;
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
    if (focus) input.current?.focus();
  }, [focus]);
  const configured = (registry?.brains || []).filter(
    (b) => b.available && b.name !== "operator",
  );
  useEffect(() => {
    if (!draft.brain && configured.length)
      setDraft({
        ...draft,
        brain:
          configured.find((b) => b.name === registry?.default_brain)?.name ||
          configured[0].name,
      });
  }, [registry]);
  const product = registry?.bodies.find((b) => b.buses.real?.provider);
  const simBody = registry?.bodies.find(
    (b) =>
      b.buses.sim?.enabled &&
      registry.worlds.some(
        (w) =>
          w.name.startsWith("so101-") &&
          registry.matrix.some(
            (m) => m.body === b.name && m.world === w.name && m.ok,
          ),
      ),
  );
  const ready =
    !!configured.find((b) => b.name === draft.brain) &&
    (!draft.body || draft.mode === "real" || !!draft.world);
  async function start() {
    if (!ready || busy) return;
    setBusy(true);
    setError("");
    try {
      const session = await createSession({
        brain: draft.brain,
        body: draft.body || null,
        mode: draft.mode,
        world: draft.mode === "sim" ? draft.world || null : null,
        sensors: [],
      });
      const scheme = schemes.find((s) => s.id === draft.scheme);
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
          {!configured.length && (
            <button onClick={() => navigate("settings/models")}>
              <span className="status-dot" />
              {t("Configure a chat model to start a session")}
            </button>
          )}
          {!robot?.registered && (
            <button onClick={() => navigate("robots")}>
              <span className="status-dot" />
              {t("Register your robot or explore simulation")}
            </button>
          )}
          {robot?.registered && (
            <button onClick={() => navigate("robots")}>
              <span className="status-dot" />
              {t("Robot configuration saved. Check devices before connecting.")}
            </button>
          )}
          {configured.length > 0 && !lastTask && (
            <button onClick={() => navigate("settings/models")}>
              {t("Chat model ready")}
            </button>
          )}
        </div>
        <div className="home-grid">
          <div className="home-composer">
            <p className="text-sm text-neutral-400">NERV</p>
            <h1>{t("What would you like to do?")}</h1>
            <p className="muted">
              {t("Start a conversation or work with your robot.")}
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
                <label className="form-label">
                  {t("Use with")}
                  <select
                    value={!draft.body ? "chat" : draft.mode}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        body:
                          e.target.value === "chat"
                            ? ""
                            : e.target.value === "real"
                              ? product?.name || ""
                              : simBody?.name || "",
                        mode: e.target.value === "real" ? "real" : "sim",
                        world: "",
                        scheme: "",
                      })
                    }
                  >
                    <option value="chat">{t("Conversation only")}</option>
                    {robot?.registered && product && (
                      <option value="real">{robot.name} · Real</option>
                    )}
                    {simBody && <option value="sim">SO-101 · Sim</option>}
                  </select>
                </label>
              </div>
              {draft.body && draft.mode === "sim" && (
                <label className="form-label">
                  {t("Simulation world")}
                  <select
                    className="form-field"
                    value={draft.world}
                    onChange={(e) =>
                      setDraft({ ...draft, world: e.target.value })
                    }
                  >
                    <option value="">{t("Select a simulation world")}</option>
                    {registry?.worlds
                      .filter((w) =>
                        registry.matrix.some(
                          (m) =>
                            m.body === draft.body && m.world === w.name && m.ok,
                        ),
                      )
                      .map((w) => (
                        <option key={w.name} value={w.name}>
                          {w.label || w.name}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              {draft.body && (
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
            {draft.body && (
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
          <aside className="home-robot">
            <RobotPreview image={robot?.image} />
            <h2>{robot?.registered ? robot.name : "SO-101"}</h2>
            <p className="muted">
              {t(
                robot?.registered
                  ? "Saved robot · check connection in workspace"
                  : "Standard follower · register to get started",
              )}
            </p>
            <button
              className="nerv-control action mt-4"
              onClick={() => navigate("robots")}
            >
              {t(robot?.registered ? "View robot" : "Register SO-101")}
            </button>
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
