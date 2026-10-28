"use client";
import { useEffect, useState } from "react";
import { setupDevice } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import DirectoryPicker from "./DirectoryPicker";
import HubPolicies from "./HubPolicies";
import MotionPolicies from "./MotionPolicies";
export default function PolicyLibrary({
  onComponents,
}: {
  onComponents: () => void;
}) {
  const { t } = useI18n();
  const [format, setFormat] = useState('act');
  const [policies, setPolicies] = useState<any[]>([]);
  const [draft, setDraft] = useState({
    id: "",
    path: "",
    conditions: "",
    standard_declared: false,
  });
  const [device, setDevice] = useState("cpu"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const refresh = () =>
    setupDevice("policies").then((v) => setPolicies(v.policies));
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);
  async function perform(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      await refresh().catch(() => {});
      setBusy(false);
    }
  }
  return (
    <div className="settings-content space-y-5">
      <h1>{t("Policy library")}</h1>
      <p className="muted">
        {t(
          "Import externally trained policies. NERV does not train models.",
        )}
      </p>
      <label className="form-label">{t('Policy format')}
        <select className="form-field" value={format} onChange={e => setFormat(e.target.value)}>
          <option value="act">LeRobot ACT</option><option value="onnx">ONNX</option>
        </select>
      </label>
      <HubPolicies format={format} onChoose={path => setDraft(value => ({...value, path}))}/>
      {format === 'onnx' ? <MotionPolicies source={draft.path}/> : <>
      <h2>{t("Imported policies")}</h2>
      {!policies.length && (
        <p className="muted">{t("No policies imported yet.")}</p>
      )}
      {policies.map((p) => (
        <article className="settings-row" key={p.id}>
          <div>
            <b>{p.id}</b>
            <p className="muted">{p.conditions}</p>
            <p className="text-xs">
              {t(
                p.check?.state === "loaded"
                  ? "Native inference check passed"
                  : "Needs setup",
              )}
            </p>
          </div>
          <button
            className="nerv-control action"
            disabled={busy}
            onClick={() =>
              perform(() => setupDevice("check", { id: p.id, device }))
            }
          >
            {t("Check")}
          </button>
        </article>
      ))}
      <details open={!policies.length || !!draft.path}>
        <summary className="cursor-pointer py-3 font-medium">
          {t("Import local policy")}
        </summary>
        <div className="space-y-3">
          {(
            [
              ["id", "Policy ID"],
              ["conditions", "Task and starting conditions"],
            ] as const
          ).map(([key, label]) => (
            <label className="form-label" key={key}>
              {t(label)}
              <input
                className="form-field"
                value={draft[key]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            </label>
          ))}
          <DirectoryPicker
            value={draft.path}
            onChange={(path) => setDraft({ ...draft, path })}
          />
          <p className="break-all text-xs">{draft.path}</p>
          <label className="form-label">
            {t("Inference device")}
            <select
              className="form-field"
              value={device}
              onChange={(e) => setDevice(e.target.value)}
            >
              {["cpu", "cuda", "mps"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
          <label className="flex gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.standard_declared}
              onChange={(e) =>
                setDraft({ ...draft, standard_declared: e.target.checked })
              }
            />
            {t("I trained with the standard SO-101 course convention")}
          </label>
          <button
            className="nerv-control nerv-primary action"
            disabled={
              busy || !draft.id || !draft.path || !draft.standard_declared
            }
            onClick={() =>
              perform(async () => {
                await setupDevice("import", draft);
                await setupDevice("check", { id: draft.id, device });
              })
            }
          >
            {t(busy ? "Checking…" : "Import and check policy")}
          </button>
        </div>
      </details>
      <p className="muted">
        {t(
          "Inference checks use synthetic inputs and do not prove a real grasp.",
        )}
      </p>
      {error && (
        <div role="alert" className="error-text">
          <p>{error}</p>
          <button className="nerv-control underline" onClick={onComponents}>
            {t("Open components")}
          </button>
        </div>
      )}
      </>}
    </div>
  );
}
