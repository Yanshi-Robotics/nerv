"use client";
import { useEffect, useState } from "react";
import {
  userApi,
  runtimeOperation,
  runtimeStatus,
  POLL_NODES_MS,
  setupDevice,
  type RobotDisplay,
  type Registry,
  type SessionSummary,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import RobotPreview from "./RobotPreview";
import RuntimeSetup from "./RuntimeSetup";
export default function MyRobot({
  robot,
  registry,
  sessions,
  onChanged,
  navigate,
  openWorkspace,
}: {
  robot: RobotDisplay | null;
  registry: Registry | null;
  sessions: SessionSummary[];
  onChanged: () => void;
  navigate: (route: string) => void;
  openWorkspace: (id: string) => void;
}) {
  const { t } = useI18n();
  const [name, setName] = useState(robot?.name || "SO-101"),
    [editing, setEditing] = useState(false),
    [setup, setSetup] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [acceptance, setAcceptance] = useState(false);
  const checked = robot?.last_check;
  const [connection, setConnection] = useState<boolean | null>(null);
  useEffect(() => {
    if (robot) setName(robot.name);
  }, [robot?.name]);
  const [pendingImage, setPendingImage] = useState<File | null>(null);
  const [previewImage, setPreviewImage] = useState("");
  useEffect(() => {
    if (!pendingImage) {
      setPreviewImage("");
      return;
    }
    const url = URL.createObjectURL(pendingImage);
    setPreviewImage(url);
    return () => URL.revokeObjectURL(url);
  }, [pendingImage]);
  const product = registry?.bodies.find((b) => b.buses.real?.provider);
  const active = sessions.find(
    (s) =>
      s.body === product?.name && s.mode === "real" && s.status === "active",
  );
  useEffect(() => {
    let cancelled = false;
    setConnection(null);
    if (!active) return;
    const read = () => runtimeStatus(active.id)
      .then((status) => { if (!cancelled) setConnection(typeof status.prepared === "boolean" ? status.prepared : null); })
      .catch(() => { if (!cancelled) setConnection(null); });
    read();
    const timer = setInterval(read, POLL_NODES_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [active?.id]);
  async function perform(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function workspace(connect = false) {
    if (!product) throw new Error(t("Install the robot runtime first."));
    if (!product.buses.real.verified && !acceptance)
      throw new Error(
        t(
          "Confirm supervised acceptance before opening this unverified runtime.",
        ),
      );
    if (acceptance)
      await setupDevice("acceptance", { accepted: true, robot: product.name });
    const session: SessionSummary =
      active ||
      (await userApi<SessionSummary>("operator/workspace", "POST", {
        body: product.name,
        mode: "real",
      }));
    if (connect) await runtimeOperation(session.id, "prepare");
    openWorkspace(session.id);
  }
  return (
    <section className="page-scroll">
      <div className="page-content">
        <header className="page-heading">
          <div>
            <h1>{t("My robot")}</h1>
            <p className="muted">
              {t("Your device, cameras and saved configuration.")}
            </p>
          </div>
          <span className="environment-badge">Real</span>
        </header>
        {setup ? (
          <>
            <button
              className="nerv-control underline"
              onClick={() => setSetup(false)}
            >
              {t("Back to robot")}
            </button>
            <RuntimeSetup
              registry={registry}
              onChanged={onChanged}
              onCreated={() => {
                setSetup(false);
                onChanged();
              }}
            />
          </>
        ) : (
          <>
            <div className="robot-detail-grid">
              <RobotPreview image={robot?.image} autoOpen />
              <div className="space-y-5">
                <h2 className="text-2xl">{robot?.name || "SO-101"}</h2>
                <p className="muted">
                  {t(
                    robot?.registered
                      ? "Configuration saved; connection is checked in the workspace."
                      : "Register your SO-101 to save its connection and camera setup.",
                  )}
                </p>
                <div className="flex flex-wrap gap-3">
                  <button
                    className="nerv-control nerv-primary action"
                    onClick={() => {
                      if (robot?.registered) setSetup(true);
                      else setEditing(true);
                    }}
                  >
                    {t(
                      robot?.registered
                        ? "Repair configuration"
                        : "Register SO-101",
                    )}
                  </button>
                  <button
                    className="nerv-control action"
                    onClick={() => setEditing(!editing)}
                  >
                    {t("Name and image")}
                  </button>
                </div>
                {active && <p role="status">{t(connection === true ? "Device prepared" : connection === false ? "Device not prepared" : "body node offline")}</p>}
                {editing && (
                  <div className="space-y-3">
                    <label className="form-label">
                      {t("Robot name")}
                      <input
                        className="form-field"
                        maxLength={80}
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                      />
                    </label>
                    <p className="muted">
                      {t(
                        "This name does not change the LeRobot calibration ID.",
                      )}
                    </p>
                    <button
                      className="nerv-control action"
                      disabled={busy || !name.trim()}
                      onClick={() =>
                        perform(async () => {
                          await userApi("robot", "PUT", { name });
                          setEditing(false);
                          if (!robot?.registered) setSetup(true);
                        })
                      }
                    >
                      {t("Save name")}
                    </button>
                    <label className="form-label">
                      {t("Robot image")}
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        disabled={busy}
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (file) {
                            if (file.size > 5 * 1024 * 1024)
                              setError(t("Image must be at most 5 MB"));
                            else setPendingImage(file);
                          }
                        }}
                      />
                    </label>
                    <p className="muted">
                      {t(
                        "JPEG, PNG or WebP, up to 5 MB. The image is cropped to a square.",
                      )}
                    </p>
                    {pendingImage && (
                      <div className="space-y-2">
                        <img
                          className="h-40 w-40 rounded-xl object-cover"
                          src={previewImage}
                          alt={t("Image preview")}
                        />
                        <button
                          className="nerv-control action"
                          disabled={busy}
                          onClick={() =>
                            perform(async () => {
                              await userApi("robot/image", "PUT", pendingImage);
                              setPendingImage(null);
                            })
                          }
                        >
                          {t("Use this image")}
                        </button>
                        <button
                          className="nerv-control action"
                          onClick={() => setPendingImage(null)}
                        >
                          {t("Cancel")}
                        </button>
                      </div>
                    )}
                    <button
                      className="nerv-control underline"
                      disabled={busy}
                      onClick={() =>
                        perform(() => userApi("robot/image", "DELETE"))
                      }
                    >
                      {t("Restore default image")}
                    </button>
                  </div>
                )}
                {robot?.registered && (
                  <>
                    <dl className="detail-list">
                      {[
                        ["Calibration ID", robot.device.id],
                        ["Serial port", robot.device.port],
                        [
                          "Top camera",
                          robot.device.cameras?.top
                            ? t("Configured")
                            : t("Missing"),
                        ],
                        [
                          "Wrist camera",
                          robot.device.cameras?.wrist
                            ? t("Configured")
                            : t("Missing"),
                        ],
                      ].map(([k, v]) => (
                        <div key={k}>
                          <dt>{t(k || "")}</dt>
                          <dd>{v}</dd>
                        </div>
                      ))}
                    </dl>
                    <button
                      className="nerv-control action"
                      disabled={busy}
                      onClick={() =>
                        perform(async () => {
                          await setupDevice("discover");
                        })
                      }
                    >
                      {t("Check saved devices")}
                    </button>
                    {checked && (
                      <p className="muted">
                        {t("Last device check")} · {new Date(checked.at * 1000).toLocaleString()} ·{" "}
                        {t(
                          checked.repair_step !== null &&
                            checked.repair_step !== undefined
                            ? "Needs setup"
                            : "Saved devices found. Connect to verify calibration and cameras.",
                        )}
                      </p>
                    )}
                    {!product?.buses.real?.verified && (
                      <label className="flex gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={acceptance}
                          onChange={(e) => setAcceptance(e.target.checked)}
                        />
                        {t(
                          "I am supervising an unverified hardware acceptance test.",
                        )}
                      </label>
                    )}
                    <p className="text-sm text-amber-300">
                      {t(
                        "Connecting may enable motor torque. Support the arm and clear the work area. Motion remains disabled.",
                      )}
                    </p>
                    <div className="flex flex-wrap gap-3">
                      <button
                        className="nerv-control nerv-primary action"
                        disabled={busy}
                        onClick={() => perform(() => workspace(true))}
                      >
                        {t("Connect robot")}
                      </button>
                      <button
                        className="nerv-control action"
                        disabled={busy}
                        onClick={() => perform(() => workspace())}
                      >
                        {t("Device and fixed policy workspace")}
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
            <div className="settings-row">
              <div>
                <h2>{t("Policies and control schemes")}</h2>
                <p className="muted">
                  {t(
                    "Choose how the robot executes a task. Training stays outside NERV.",
                  )}
                </p>
              </div>
              <div className="flex gap-3">
                <button
                  className="nerv-control action"
                  onClick={() => navigate("settings/policies")}
                >
                  {t("Policy library")}
                </button>
                <button
                  className="nerv-control action"
                  onClick={() => navigate("settings/schemes")}
                >
                  {t("Control schemes")}
                </button>
              </div>
            </div>
            <div className="settings-row">
              <div>
                <h2>{t("Start an Agent session")}</h2>
                <p className="muted">
                  {t(
                    "A configured chat model is required. Fixed policies do not need a key.",
                  )}
                </p>
              </div>
              <button
                className="nerv-control action"
                onClick={() => navigate("new")}
              >
                {t("New session")}
              </button>
            </div>
            <details>
              <summary className="cursor-pointer py-3">
                {t("Device history")}
              </summary>
              {sessions
                .filter((s) => s.brain === "operator" && s.mode === "real" && s.body === product?.name)
                .map((s) => (
                  <button
                    key={s.id}
                    className="nerv-nav"
                    onClick={() => openWorkspace(s.id)}
                  >
                    {s.title} · {s.created_at}
                  </button>
                ))}
            </details>
          </>
        )}
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
      </div>
    </section>
  );
}
