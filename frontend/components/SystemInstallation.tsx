"use client";
import { useEffect, useState } from "react";
import EnvironmentDisclosure from "./EnvironmentDisclosure";
import { userApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function SystemInstallation() {
  const { t } = useI18n();
  const [data, setData] = useState<any>(null), [values, setValues] = useState<any>({});
  const [error, setError] = useState(""), [message, setMessage] = useState("");
  const [proxy, setProxy] = useState(""), [busy, setBusy] = useState(false);
  const load = (next: any) => {setData(next); setValues(next.settings); setProxy("");};
  useEffect(() => {userApi("system").then(load).catch(e => setError(e.message));}, []);
  async function action(kind: "save" | "detect" | "reset") {
    setBusy(true); setError(""); setMessage("");
    try {
      const {proxy_configured, ...settings} = values;
      const next = kind === "save"
        ? await userApi("system/settings", "PUT", {...settings, ...(proxy ? {proxy} : {})})
        : await userApi(kind === "detect" ? "system/detect" : "system/settings/reset", "POST", {});
      load(next); setMessage(t("Saved. Running tasks keep their current environment."));
    } catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  return <section className="settings-content space-y-5">
    <h1>{t("System and installation")}</h1>
    <p className="muted">{t("These settings describe the computer running NERV, not your browser.")}</p>
    {data && <>
      <article className="rounded-xl border border-neutral-700 p-4 space-y-3">
        <h2>{t("Detected environment")}</h2>
        <p>{data.facts.os} · {data.facts.arch} · {data.facts.os_version}</p>
        <p>Python {data.facts.python} · {data.facts.libc} {data.facts.libc_version}</p>
        <p>{t("NVIDIA status")}: {t(data.facts.gpu.status)}</p>
        {data.facts.gpu.devices.map((gpu: any, index: number) => <p key={index}>{gpu.name} · {t("Driver")}: {gpu.driver}</p>)}
        {data.facts.wsl && <p>{t("WSL uses Linux packages. USB, cameras and GPU access need separate checks.")}</p>}
        {data.facts.translated && <p>{t("Architecture translation detected. Packages follow the running process architecture.")}</p>}
        <p className="text-xs muted">{t("Last checked")}: {new Date(data.facts.checked_at * 1000).toLocaleString()}</p>
        <button className="nerv-control action" disabled={busy} onClick={() => action("detect")}>{t("Detect again")}</button>
      </article>
      <label className="block">{t("System target")}<select className="nerv-control block w-full" value={values.target} onChange={e => setValues({...values, target: e.target.value})}>
        <option value="auto">{t("Automatic")}</option>{data.targets.map((target: string) => <option key={target}>{target}</option>)}
      </select></label>
      <label className="block">{t("Policy compute environment")}<select className="nerv-control block w-full" value={values.compute} onChange={e => setValues({...values, compute: e.target.value})}>
        <option value="auto">{t("Automatic")}</option><option value="cpu">CPU</option><option value="cu130">NVIDIA · CUDA 13.0</option>
      </select></label>
      <p className="muted text-sm">{t("Manual choices cannot override platform or driver incompatibilities. New settings apply to new installations.")}</p>
      <label className="block">{t("Concurrent downloads")}<input className="nerv-control block" type="number" min={1} max={4} value={values.concurrency} onChange={e => setValues({...values, concurrency: Number(e.target.value)})}/></label>
      <label className="block">{t("Total bandwidth limit (bytes/s; 0 means unlimited)")}<input className="nerv-control block" type="number" min={0} value={values.bandwidth_bytes_s} onChange={e => setValues({...values, bandwidth_bytes_s: Number(e.target.value)})}/></label>
      <label className="block">{t("Download proxy")}<input className="nerv-control block w-full" type="password" autoComplete="off" value={proxy} onChange={e => setProxy(e.target.value)} placeholder={t(data.settings.proxy_configured ? "Proxy saved. Leave blank to keep it." : "Optional proxy URL")}/></label>
      <button className="nerv-control underline text-sm" disabled={busy} onClick={async () => {try {load(await userApi("system/settings", "PUT", {proxy: ""}));} catch(e) {setError((e as Error).message);}}}>{t("Clear saved proxy")}</button>
      <details className="rounded-xl border border-neutral-700 p-4 space-y-3"><summary className="nerv-control cursor-pointer">{t("Environment management and isolation")}</summary><p>{t("Installation location")}</p><code className="block break-all select-all">{data.installation_root}</code><EnvironmentDisclosure /></details>
      <div className="flex gap-3"><button className="nerv-control nerv-primary action" disabled={busy} onClick={() => action("save")}>{t("Save")}</button><button className="nerv-control action" disabled={busy} onClick={() => action("reset")}>{t("Restore defaults")}</button></div>
      <div className="space-y-3">{Object.entries(data.plans).map(([key, plan]: [string, any]) => <article key={key} className="rounded-xl border border-neutral-700 p-4"><h2>{key}</h2><p>{plan.target} · {plan.backend}</p><p>{plan.compatible ? `${t("Download size")}: ${(plan.download_bytes / 1048576).toFixed(1)} MiB` : plan.reason}</p>{plan.compatible && !plan.validated && <p className="muted">{t("Candidate environment. Installation checks are still required.")}</p>}</article>)}</div>
    </>}
    {message && <p role="status">{message}</p>}{error && <p role="alert" className="error-text">{error}</p>}
  </section>;
}
