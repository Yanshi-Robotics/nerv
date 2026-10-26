"use client";
import { useEffect, useState, useRef } from "react";
import {
  moduleCatalog,
  setupHealth, startSetupCheck, setupCheckJob,
  setupDevice,
  setupCamera,
  POLL_NODES_MS,
  type Registry,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import ModulesPanel from "./ModulesPanel";
import DirectoryPicker from "./DirectoryPicker";
import SetupReport, { SetupExplanation } from "./SetupReport";

type Camera = {
  type: string;
  index_or_path: number | string;
  width: number;
  height: number;
  fps: number;
  rotation: number;
  backend?: number;
};
type Profile = {
  port: string;
  id: string;
  calibration_dir: string;
  policy_device: string;
  cameras: Record<string, Camera>;
};
type Discovery = {
  ports: { port: string; label: string; serial?: string; vid?: number | null }[];
  cameras: {
    device: number | string;
    label: string;
    stable: boolean;
    backend?: number;
  }[];
  calibrations: { id: string; directory: string }[];
  saved?: Profile;
  repair_step?: number | null;
  warnings?: string[];
};
const steps = [
  "Check your computer",
  "Install runtime",
  "Choose the arm",
  "Check prerequisites",
  "Set up cameras",
  "Save configuration",
];
const cameraDefault = (device: number): Camera => ({
  type: "opencv",
  index_or_path: device,
  width: 640,
  height: 480,
  fps: 30,
  rotation: 0,
});
const field =
  "w-full rounded-lg border border-neutral-600 bg-neutral-900 p-2 text-sm";

export default function RuntimeSetup({
  registry,
  onChanged,
  onCreated,
}: {
  registry: Registry | null;
  onChanged: () => void;
  onCreated?: (id: string) => void;
}) {
  const { t } = useI18n();
  const [step, setStep] = useState(0);
  const [installed, setInstalled] = useState(false);
  const [data, setData] = useState<Discovery>({
    ports: [],
    cameras: [],
    calibrations: [],
  });
  const [profile, setProfile] = useState<Profile>({
    port: "",
    id: "",
    calibration_dir: "",
    policy_device: "",
    cameras: { top: cameraDefault(0), wrist: cameraDefault(1) },
  });
  const [health, setHealth] = useState<any>(null);
  const [check, setCheck] = useState<any>(null);
  const [job, setJob] = useState<string | null>(null);
  const profileKey = `${profile.port}|${profile.id}|${profile.calibration_dir}`;
  const latestProfileKey = useRef(profileKey);
  latestProfileKey.current = profileKey;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState(false);
  const [frames, setFrames] = useState<Record<string, string>>({});
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    const read = () =>
      moduleCatalog()
        .then((v) =>
          setInstalled(
            v.modules.some(
              (x: any) =>
                x.id === "lerobot0.6.1" && x.installed && !x.needs_repair,
            ),
          ),
        )
        .catch((e) => setError(e.message));
    read();
    const timer = setInterval(read, POLL_NODES_MS);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!installed) return;
    setupDevice("discover")
      .then((value: Discovery) => {
        setData(value);
        if (value.saved) {
          setProfile(value.saved);
          setConfirmed(!value.repair_step);
          setupDevice("prerequisite_status", value.saved).then(result => {
            setCheck(result);
            setStep(value.repair_step === 1 ? 2 : result.state !== 'passed' ? 3 : value.repair_step ? value.repair_step + 1 : 5);
          }).catch(e => setError(e.message));
        }
      })
      .catch((e) => setError(e.message));
  }, [installed]);
  useEffect(() => {
    if (!preview) return;
    const timer = setInterval(
      () =>
        setupCamera("frames")
          .then((v) => setFrames(v.frames || {}))
          .catch((e) => setError(e.message)),
      POLL_NODES_MS,
    );
    return () => {
      clearInterval(timer);
      setupCamera("close").catch(() => {});
    };
  }, [preview]);
  useEffect(() => {
    let alive = true;
    setupHealth().then(v => { if (alive) setHealth(v); }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [installed]);
  useEffect(() => {
    if (health?.inference_device) setProfile(p => ({...p, policy_device: health.inference_device}));
  }, [health]);
  useEffect(() => {
    setCheck(null);
    if (!installed || !profile.port || !profile.id || !profile.calibration_dir) return;
    let alive = true;
    setupDevice("prerequisite_status", profile).then(v => { if (alive) setCheck(v); }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [installed, profileKey]);
  useEffect(() => {
    if (!job) return;
    let alive = true;
    let pending = false;
    const key = profileKey;
    const poll = async () => {
      if (pending || !alive) return;
      pending = true;
      try {
        const result = await setupCheckJob(job, "status");
        if (alive && latestProfileKey.current === key && result.state !== 'checking') {
          setCheck(result.result || result);
          setJob(null);
        }
      } catch(e) { if (alive) { setError((e as Error).message); setJob(null); } }
      finally { pending = false; }
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => { alive = false; clearInterval(timer); setupCheckJob(job, "cancel").catch(() => {}); };
  }, [job, profileKey]);
  useEffect(() => {
    if (!installed || ![2, 4].includes(step) || preview || job) return;
    let alive = true;
    let pending = false;
    const poll = async () => {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const found: Discovery = await setupDevice("discover");
        if (alive) {
          setData(previous => {
            if (JSON.stringify(previous.ports) !== JSON.stringify(found.ports)) setMessage("Connected devices changed. Confirm your selection.");
            return found;
          });
          const usb = found.ports.filter(p => p.vid != null);
          if (usb.length === 1) setProfile(p => p.port ? p : {...p, port: usb[0].port});
        }
      } catch(e) { if (alive) setError((e as Error).message); }
      finally { pending = false; }
    };
    poll(); const timer = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [installed, step, preview, job]);
  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function next() {
    if (step === 3 && check?.state !== "passed") throw new Error(t("Check this device before continuing."));
    if (step === 3)
      await setupDevice("validate_calibration", {
        id: profile.id,
        calibration_dir: profile.calibration_dir,
      });
    if (step === 4) {
      if (!confirmed)
        throw new Error(t("Confirm the two camera views before saving"));
      setPreview(false);
      await setupCamera("close");
    }
    setStep((s) => Math.min(steps.length - 1, s + 1));
  }
  const portCard = (p: Discovery["ports"][number]) => (
    <button key={p.port}
      className={`nerv-control block w-full rounded-xl border p-4 text-left ${profile.port === p.port ? "nerv-selected" : "border-neutral-700"}`}
      onClick={() => setProfile({ ...profile, port: p.port })}>
      <span className="block">{p.label}</span>
      <span className="text-xs text-neutral-400">{p.port}{p.serial ? ` · ${p.serial}` : ""}</span>
    </button>
  );
  async function save() {
    if (!confirmed)
      throw new Error(t("Confirm the two camera views before saving"));
    await setupDevice("validate_calibration", {
      id: profile.id,
      calibration_dir: profile.calibration_dir,
    });
    await setupDevice("configure_standard", profile);
    onChanged();
    onCreated?.("");
  }
  return (
    <section className="col-span-2 overflow-auto p-6">
      <div className="mx-auto max-w-5xl space-y-6">
        <header>
          <h1 className="text-xl">{t("Connect your SO-101")}</h1>
          <p className="mt-2 text-sm text-neutral-400">
            {t(
              "Use your existing LeRobot calibration. Device settings are remembered on this computer.",
            )}
          </p>
        </header>
        <div className="grid gap-8 md:grid-cols-[210px_minmax(0,1fr)]">
        <nav className="flex flex-col gap-2 self-start" aria-label={t("Setup steps")}>
          {steps.map((label, i) => (
            <button
              key={label}
              disabled={busy || !!job || (i > step && (i > 3 || !data.saved))}
              className={`nerv-control rounded-xl px-4 py-3 text-left text-sm ${i === step ? "nerv-selected" : "hidden md:block text-neutral-400"}`}
              onClick={() => {
                setPreview(false);
                setStep(i);
              }}
              aria-current={i === step ? "step" : undefined}
            >
              {i + 1}. {t(label)}
            </button>
          ))}
        </nav>
        <div className="min-w-0 space-y-5">
        <h2 className="text-lg">{t(steps[step])}</h2>
        {step === 0 && <div className="space-y-4">
          {health ? <SetupReport checks={health.checks} checkedAt={health.checked_at} /> : <p role="status">{t("Checking your computer…")}</p>}
          <button className="nerv-link" disabled={busy || !!job} onClick={() => perform(async () => setHealth(await setupHealth(true)))}>{t("Check again")}</button>
          <a className="nerv-link ml-4" href="#settings/system">{t("System and installation")}</a>
          {health && <details className="text-sm"><summary>{t("Installation location")}</summary><p className="break-all">{health.installation_root}</p></details>}
        </div>}
        {step === 1 && (
          <ModulesPanel only="lerobot0.6.1" onChanged={onChanged} />
        )}
        {step === 2 && (
          <div className="space-y-3">
            <div className="rounded-xl border border-dashed border-neutral-600 p-5 text-center text-sm">{t("USB → computer · follower power → power supply")}</div>
            <button
              className="nerv-control rounded border px-3 py-2"
              disabled={busy || !!job}
              onClick={() =>
                perform(async () => setData(await setupDevice("discover")))
              }
            >
              {t("Scan connected devices")}
            </button>
            {data.ports.filter(p => p.vid != null).length === 0 && <p className="text-sm text-neutral-400">{t("No USB serial device found. Connect the follower, then check again.")}</p>}
            <div className="space-y-2">{data.ports.filter(p => p.vid != null).map(portCard)}</div>
            {data.ports.some(p => p.vid == null) && <details>
              <summary className="nerv-link">{t("Other serial ports")}</summary>
              <div className="max-h-64 overflow-auto space-y-2 mt-3">{data.ports.filter(p => p.vid == null).map(portCard)}</div>
            </details>}
            <label className="block text-sm">
              {t("Serial port")}
              <input
                className={field}
                value={profile.port}
                onChange={(e) =>
                  setProfile({ ...profile, port: e.target.value })
                }
                placeholder="/dev/serial/by-id/… or COM5"
              />
            </label>
            <p className="text-xs text-neutral-400">
              {t("Scanning does not connect or energize the arm.")}
            </p>
          </div>
        )}
        {step === 3 && (
          <div className="space-y-3">
            {data.calibrations.map((c) => (
              <button
                className="nerv-control rounded border p-3"
                key={c.id}
                onClick={() =>
                  setProfile({
                    ...profile,
                    id: c.id,
                    calibration_dir: c.directory,
                  })
                }
              >
                {c.id}
              </button>
            ))}
            <label className="block text-sm">
              {t("Robot ID")}
              <input
                className={field}
                disabled={!!job} value={profile.id}
                onChange={(e) => setProfile({ ...profile, id: e.target.value })}
              />
            </label>
            <label className="block text-sm">
              {t("Calibration directory")}
              <input
                className={field}
                disabled={!!job} value={profile.calibration_dir}
                onChange={(e) =>
                  setProfile({ ...profile, calibration_dir: e.target.value })
                }
              />
            </label>
            <DirectoryPicker
               value={profile.calibration_dir}
              disabled={busy || !!job}
              onChange={(path) =>
                perform(async () => {
                  setProfile({ ...profile, calibration_dir: path });
                  const found = await setupDevice("calibrations", {
                    directory: path,
                  });
                  setData({ ...data, calibrations: found.calibrations });
                  if (found.calibrations.length === 1)
                    setProfile({
                      ...profile,
                      calibration_dir: path,
                      id: found.calibrations[0].id,
                    });
                })
              }
            />
            <p className="text-xs text-neutral-400">
              {t(
                "Choose the folder containing your robot ID JSON file. Calibration stays in LeRobot.",
              )}
            </p>
            <p className="text-sm">{t("Inference device")}: {health?.inference_device || t("Not checked")}</p>
            <div className="rounded-2xl border border-neutral-700 p-5 space-y-4">
              <p className="text-sm">{t("Checking reads motor settings only. NERV does not configure or calibrate motors.")}</p>
              {check?.checks && <SetupReport checks={check.checks} checkedAt={check.checked_at} />}
              <SetupExplanation code={check?.code} />
              {job ? <><p role="status">{t("Checking device…")}</p><button className="nerv-control rounded-lg border px-4 py-2" onClick={() => setupCheckJob(job, "cancel")}>{t("Cancel check")}</button></> :
                <button disabled={busy || !profile.port || !profile.id || !profile.calibration_dir} className="nerv-control nerv-primary rounded-lg px-4 py-2" onClick={() => perform(async () => {
                  setCheck(null);
                  const v = await startSetupCheck(profile);
                  setJob(v.id);
                })}>{t("Check device")}</button>}
              <a className="nerv-link block" href="https://huggingface.co/docs/lerobot/so101" target="_blank" rel="noreferrer">{t("External motor setup and calibration guide")}</a>
            </div>
          </div>
        )}
        {step === 4 && (
          <div className="space-y-4">
            <button
              disabled={busy || preview}
              className="nerv-control rounded border px-3 py-2"
              onClick={() =>
                perform(async () => setData(await setupDevice("discover")))
              }
            >
              {t("Scan connected devices")}
            </button>
            {data.warnings?.map((w) => (
              <p role="status" key={w} className="text-sm text-amber-300">
                {t(w)}
              </p>
            ))}
            <div className="grid gap-4 sm:grid-cols-2">
              {["top", "wrist"].map((name) => (
                <div key={name} className="space-y-2">
                  <h3>{t(name === "top" ? "Top camera" : "Wrist camera")}</h3>
                  <div className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded-xl bg-neutral-950">
                    {frames[name] ? (
                      <img
                        src={frames[name]}
                        alt={t(name === "top" ? "Top camera" : "Wrist camera")}
                      />
                    ) : (
                      <p className="text-xs text-neutral-500">
                        {t("Preview appears here")}
                      </p>
                    )}
                  </div>
                  <input
                    aria-label={t("Camera device")}
                    className={field}
                    list="camera-devices"
                    disabled={preview}
                    value={profile.cameras[name].index_or_path}
                    onChange={(e) => {
                      setConfirmed(false);
                      setProfile({
                        ...profile,
                        cameras: {
                          ...profile.cameras,
                          [name]: {
                            ...profile.cameras[name],
                            index_or_path: /^\d+$/.test(e.target.value)
                              ? Number(e.target.value)
                              : e.target.value,
                            backend: data.cameras.find(
                              (c) => String(c.device) === e.target.value,
                            )?.backend,
                          },
                        },
                      });
                    }}
                  />
                  <select
                    aria-label={t("Camera rotation")}
                    className={field}
                    disabled={preview}
                    value={profile.cameras[name].rotation || 0}
                    onChange={(e) => {
                      setConfirmed(false);
                      setProfile({
                        ...profile,
                        cameras: {
                          ...profile.cameras,
                          [name]: {
                            ...profile.cameras[name],
                            rotation: Number(e.target.value),
                          },
                        },
                      });
                    }}
                  >
                    {[0, 90, 180, 270].map((n) => (
                      <option key={n} value={n}>
                        {n}°
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
            <datalist id="camera-devices">
              {data.cameras.map((c) => (
                <option key={c.device} value={c.device}>
                  {c.label}
                </option>
              ))}
            </datalist>
            <button
              className="nerv-control rounded border px-3 py-2"
              disabled={busy || !!job}
              onClick={() =>
                perform(async () => {
                  if (preview) {
                    await setupCamera("close");
                    setPreview(false);
                  } else {
                    setFrames({});
                    setConfirmed(false);
                    await setupCamera("open", { cameras: profile.cameras });
                    setPreview(true);
                  }
                })
              }
            >
              {t(preview ? "Close preview" : "Open camera preview")}
            </button>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                disabled={!preview || !frames.top || !frames.wrist}
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              {t("The top and wrist views and their directions are correct")}
            </label>
          </div>
        )}
        {step === 5 && (
          <div className="space-y-4">
            <p>
              {profile.id} · {profile.port}
            </p>
            <p className="text-sm text-neutral-400">
              {t(
                "Save the configuration, then connect from My robot. No motors are connected here.",
              )}
            </p>
            <button
              className="nerv-control nerv-primary rounded-lg px-4 py-2"
              disabled={busy || !!job}
              onClick={() => perform(save)}
            >
              {t("Save configuration")}
            </button>
          </div>
        )}
        {message && <p role="status" className="text-sm">{t(message)}</p>}
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        <footer className="flex gap-3">
          {step > 0 && (
            <button
              className="nerv-control rounded border px-4 py-2"
              disabled={busy || !!job}
              onClick={() => {
                setPreview(false);
                setStep((s) => s - 1);
              }}
            >
              {t("Back")}
            </button>
          )}
          {step < steps.length - 1 && (
            <button
              className="nerv-control nerv-primary rounded px-4 py-2"
              disabled={
                busy || !!job ||
                (step === 0 && !health?.can_install && !health?.environment_ready) ||
                (step === 1 && !installed) ||
                (step === 2 && !profile.port) ||
                (step === 3 && check?.state !== "passed") ||
                (step === 4 && !confirmed)
              }
              onClick={() => perform(next)}
            >
              {t("Continue")}
            </button>
          )}
        </footer>
        </div>
        </div>
      </div>
    </section>
  );
}
