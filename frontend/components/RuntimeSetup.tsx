"use client";
import { useEffect, useState } from "react";
import {
  moduleCatalog,
  setupDevice,
  setupCamera,
  POLL_NODES_MS,
  type Registry,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import ModulesPanel from "./ModulesPanel";
import DirectoryPicker from "./DirectoryPicker";

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
  ports: { port: string; label: string; serial?: string }[];
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
  "Install runtime",
  "Choose the arm",
  "Existing calibration",
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
    policy_device: "cpu",
    cameras: { top: cameraDefault(0), wrist: cameraDefault(1) },
  });
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
          setStep(Math.min(value.repair_step ?? 4, 4));
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
    if (step === 2)
      await setupDevice("validate_calibration", {
        id: profile.id,
        calibration_dir: profile.calibration_dir,
      });
    if (step === 3) {
      if (!confirmed)
        throw new Error(t("Confirm the two camera views before saving"));
      setPreview(false);
      await setupCamera("close");
    }
    setStep((s) => Math.min(steps.length - 1, s + 1));
  }
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
      <div className="mx-auto max-w-3xl space-y-6">
        <header>
          <h1 className="text-xl">{t("Connect your SO-101")}</h1>
          <p className="mt-2 text-sm text-neutral-400">
            {t(
              "Use your existing LeRobot calibration. Device settings are remembered on this computer.",
            )}
          </p>
        </header>
        <nav className="flex flex-wrap gap-2" aria-label={t("Setup steps")}>
          {steps.map((label, i) => (
            <button
              key={label}
              disabled={busy || (!data.saved && i > step)}
              className={`nerv-control rounded-full border px-3 py-1 text-xs ${i === step ? "nerv-selected" : "text-neutral-400"}`}
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
        <h2 className="text-lg">{t(steps[step])}</h2>
        {step === 0 && (
          <ModulesPanel only="lerobot0.6.1" onChanged={onChanged} />
        )}
        {step === 1 && (
          <div className="space-y-3">
            <button
              className="nerv-control rounded border px-3 py-2"
              disabled={busy}
              onClick={() =>
                perform(async () => setData(await setupDevice("discover")))
              }
            >
              {t("Scan connected devices")}
            </button>
            {data.ports.map((p) => (
              <button
                key={p.port}
                className={`nerv-control block w-full rounded-xl border p-4 text-left ${profile.port === p.port ? "nerv-selected" : "border-neutral-700"}`}
                onClick={() => setProfile({ ...profile, port: p.port })}
              >
                <span className="block">{p.label}</span>
                <span className="text-xs text-neutral-400">
                  {p.port}
                  {p.serial ? ` · ${p.serial}` : ""}
                </span>
              </button>
            ))}
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
        {step === 2 && (
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
                value={profile.id}
                onChange={(e) => setProfile({ ...profile, id: e.target.value })}
              />
            </label>
            <label className="block text-sm">
              {t("Calibration directory")}
              <input
                className={field}
                value={profile.calibration_dir}
                onChange={(e) =>
                  setProfile({ ...profile, calibration_dir: e.target.value })
                }
              />
            </label>
            <DirectoryPicker
              value={profile.calibration_dir}
              disabled={busy}
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
            <label className="block text-sm">
              {t("Inference device")}
              <select
                className={field}
                value={profile.policy_device}
                onChange={(e) =>
                  setProfile({ ...profile, policy_device: e.target.value })
                }
              >
                {["cpu", "cuda", "mps"].map((device) => (
                  <option key={device}>{device}</option>
                ))}
              </select>
            </label>
          </div>
        )}
        {step === 3 && (
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
              disabled={busy}
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
        {step === 4 && (
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
              disabled={busy}
              onClick={() => perform(save)}
            >
              {t("Save configuration")}
            </button>
          </div>
        )}
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        <footer className="flex gap-3">
          {step > 0 && (
            <button
              className="nerv-control rounded border px-4 py-2"
              disabled={busy}
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
                busy ||
                (step === 0 && !installed) ||
                (step === 1 && !profile.port) ||
                (step === 2 && (!profile.id || !profile.calibration_dir)) ||
                (step === 3 && !confirmed)
              }
              onClick={() => perform(next)}
            >
              {t("Continue")}
            </button>
          )}
        </footer>
      </div>
    </section>
  );
}
