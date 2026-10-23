"use client";
import { useEffect, useRef, useState } from "react";
import { configureVlm, getVlm, saveCredential, type VlmModel, type VlmProvider } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const field = "w-full rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--control-focus)]";
const button = "rounded-md border border-neutral-600 px-3 py-2 text-sm hover:bg-neutral-800";

export default function VlmPanel({ onChanged }: { onChanged: () => void }) {
  const { t } = useI18n();
  const [models, setModels] = useState<VlmModel[]>([]);
  const [providers, setProviders] = useState<VlmProvider[]>([]);
  const [providerId, setProviderId] = useState("");
  const [key, setKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const request = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(false);
  const [keyPath, setKeyPath] = useState("");
  const [result, setResult] = useState<"saved" | "failed" | "removed" | null>(null);
  const provider = providers.find(p => p.id === providerId);
  const providerModels = models.filter(m => m.provider === providerId && m.credential_ref === provider?.credential_ref && m.enabled && m.input_modalities.includes("image"));
  // One enabled registry model validates the shared provider credential.
  // Model and effort selection belongs to the session, not credential registration.
  const verificationModel = providerModels[0];
  const disabled = busy || locked || loading || !!loadError;
  async function load() {
    const data = await getVlm();
    if (!Array.isArray(data.providers) || !Array.isArray(data.models)) throw new Error("Model settings could not be loaded. Check the backend version.");
    setModels(data.models); setProviders(data.providers); setLocked(data.task_locked); setKeyPath(data.credential_path);
    const initial = data.models.find(m => m.id === data.selection) || data.models[0];
    setProviderId(value => value || initial?.provider || data.providers[0]?.id || "");
  }
  async function reload() {
    setLoading(true); setLoadError("");
    try {await load();} catch(e) {setLoadError((e as Error).message);} finally {setLoading(false);}
  }
  useEffect(() => { reload(); return () => request.current?.abort(); }, []);
  async function configure() {
    if (!verificationModel || disabled || !key) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setResult(null);
    try {
      const response = await configureVlm(verificationModel.id, key, controller.signal);
      if (controller.signal.aborted) return;
      if (response.ok && response.saved) {
        setKey(""); setResult("saved");
        // A refresh failure must not relabel a successfully saved credential.
        await reload(); onChanged();
      } else setResult("failed");
    } catch {if (!controller.signal.aborted) setResult("failed");}
    finally {if (request.current === controller) {request.current = null; setBusy(false);}}
  }
  async function remove() {
    if (!provider || disabled) return;
    setBusy(true); setResult(null);
    try {
      await saveCredential(provider.credential_ref, null);
      setKey(""); setResult("removed"); await reload(); onChanged();
    } catch {setResult("failed");}
    finally {setBusy(false);}
  }
  function chooseProvider(p: VlmProvider) {
    setProviderId(p.id); setKey(""); setResult(null);
  }
  return <section className="h-full overflow-y-auto bg-neutral-950 p-6 text-neutral-200">
    <div className="mx-auto max-w-5xl">
      <h1 className="text-2xl font-semibold">{t("Models and API keys")}</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-400">{t("Save one key per provider. Choose a model and reasoning effort in the session.")}</p>
      {loading && <p role="status">{t("Loading…")}</p>}
      {loadError && <div role="alert" className="mt-4 text-[var(--control-danger-text)]"><p>{t(loadError)}</p><button className="nerv-control rounded border px-3 py-1" onClick={reload}>{t("Retry")}</button></div>}
      {!loading && !loadError && !providers.length && <p role="status">{t("No model providers were returned. Check the backend configuration.")}</p>}
      <div className="mt-6 grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label={t("Model providers")} className="flex flex-wrap content-start gap-2 lg:flex-col">
          {providers.map(p => <button key={p.id} disabled={busy} onClick={() => chooseProvider(p)} aria-pressed={p.id === providerId} className={`nerv-control rounded-lg border px-4 py-3 text-left ${p.id === providerId ? "nerv-selected" : "border-neutral-800 hover:border-neutral-600"}`}>
            <div className="font-medium">{p.label}</div>
            <div className="mt-1 text-xs text-neutral-400">{p.credential.configured ? t("Key saved") : t("Key not configured")}</div>
          </button>)}
        </nav>
        <div className="min-w-0 space-y-6">
          {provider && <div className="rounded-lg border border-neutral-800 p-4">
            <label htmlFor="provider-key" className="block text-sm font-medium">{provider.label} · {t("API key")}</label>
            <p className="mt-1 text-xs text-neutral-400">{provider.credential.configured ? t("A key is saved. The empty field does not mean it is missing.") : t("Enter a key for this provider. Models from the same provider share it.")}</p>
            <input id="provider-key" type="password" autoComplete="new-password" spellCheck={false} value={key} onChange={event => {setKey(event.target.value); setResult(null);}} disabled={disabled} className={`${field} mt-3`} placeholder={t("Saved keys are never displayed")} />
            <div className="mt-3 flex flex-wrap gap-2">
              <button className={`nerv-control ${button}`} disabled={!key || !verificationModel || disabled} onClick={configure}>{t(busy ? "Saving…" : "Save key")}</button>
              <button className={`nerv-control ${button}`} disabled={disabled || !provider.credential.configured} onClick={remove}>{t("Remove key")}</button>
            </div>
            {result && <p role={result === "failed" ? "alert" : "status"} className={`mt-3 text-sm ${result === "failed" ? "text-[var(--control-danger-text)]" : "text-green-400"}`}>{t(result === "saved" ? "Saved successfully" : result === "removed" ? "Key removed" : "Save failed")}</p>}
            <p className="mt-3 text-xs text-neutral-400">{t("Testing sends one billable request with a built-in image, never a camera frame. Opening settings does not call the provider.")}</p>
            <p className="mt-3 break-all text-xs text-neutral-500">{t("Stored on the backend host")}: {keyPath}</p>
            <div className="mt-5 border-t border-neutral-800 pt-4">
              <h2 className="text-sm font-medium">{t("Supported models")}</h2>
              <ul className="mt-2 space-y-2 text-sm text-neutral-400">{providerModels.map(model => <li key={model.id}>{model.label} <span className="text-xs">({model.model})</span></li>)}</ul>
              {!providerModels.length && <p className="mt-2 text-sm text-neutral-400">{t("No enabled visual models for this provider.")}</p>}
              <p className="mt-2 text-xs text-neutral-500">{t("A saved key is shared by these models. Availability also depends on your provider account.")}</p>
            </div>
          </div>}
          {locked && <p className="text-sm text-amber-400">{t("Finish the active task before changing models")}</p>}
        </div>
      </div>
    </div>
  </section>;
}
