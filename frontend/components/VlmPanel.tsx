"use client";
import { useEffect, useRef, useState } from "react";
import { configureVlm, type ConfigurationResult, getVlm, putVlm, saveCredential, selectVlm, validateVlm, type VlmModel, type VlmProvider } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const field = "w-full rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--control-focus)]";
const button = "rounded-md border border-neutral-600 px-3 py-2 text-sm hover:bg-neutral-800";
function registration(model: VlmModel): Record<string, unknown> & {id: string} {
  const { credential: _credential, checks: _checks, available: _available, effort: _effort, physical: _physical, ...spec } = model;
  return spec;
}

export default function VlmPanel({ onChanged }: { onChanged: () => void }) {
  const { t } = useI18n();
  const [models, setModels] = useState<VlmModel[]>([]);
  const [providers, setProviders] = useState<VlmProvider[]>([]);
  const [providerId, setProviderId] = useState("");
  const [chosen, setChosen] = useState("");
  const [draft, setDraft] = useState<Record<string, unknown> & {id: string} | null>(null);
  const [key, setKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [probe, setProbe] = useState<ConfigurationResult | null>(null);
  const request = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(false);
  const [keyPath, setKeyPath] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const provider = providers.find(p => p.id === providerId);
  const selected = models.find(m => m.id === chosen);
  const providerModels = models.filter(m => m.provider === providerId);
  const disabled = busy || locked;
  async function load() {
    const data = await getVlm();
    if (!Array.isArray(data.providers) || !Array.isArray(data.models)) throw new Error("Model settings could not be loaded. Check the backend version.");
    setModels(data.models); setProviders(data.providers); setLocked(data.task_locked); setKeyPath(data.credential_path);
    const initial = data.models.find(m => m.id === data.selection) || data.models[0];
    setProviderId(value => value || initial?.provider || data.providers[0]?.id || "");
    setChosen(value => value || initial?.id || "");
  }
  async function reload() {
    setLoading(true); setLoadError("");
    try {await load();} catch(e) {setLoadError((e as Error).message);} finally {setLoading(false);}
  }
  useEffect(() => { reload(); return () => request.current?.abort(); }, []);
  async function configure() {
    if (!selected || busy || !key) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError(""); setMessage(""); setProbe(null);
    try {
      const result = await configureVlm(selected.id, key, controller.signal);
      if (controller.signal.aborted) return;
      setProbe(result);
      if (result.saved) {
        setKey(""); setMessage(t("Configuration successful. Connection and image test passed."));
        await load(); onChanged();
      } else setError(t(result.error?.message || "The image answer did not match. The key was not saved."));
    } catch(e) {if (!controller.signal.aborted) setError((e as Error).message);}
    finally {if (request.current === controller) {request.current = null; setBusy(false);}}
  }
  useEffect(() => { if (selected) setDraft(registration(selected)); }, [selected]);
  async function act(action: () => Promise<unknown>, clearKey = false) {
    setBusy(true); setError(""); setMessage("");
    try {
      await action(); if (clearKey) setKey("");
      await load(); onChanged(); setMessage(t("Settings updated"));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function chooseProvider(p: VlmProvider) {
    setProviderId(p.id); setChosen(models.find(m => m.provider === p.id)?.id || "");
    setKey(""); setError(""); setMessage(""); setProbe(null);
  }
  const edit = (name: string, value: unknown) => setDraft(d => d ? {...d, [name]: value} : d);
  return <section className="h-full overflow-y-auto bg-neutral-950 p-6 text-neutral-200">
    <div className="mx-auto max-w-5xl">
      <h1 className="text-2xl font-semibold">{t("Models and API keys")}</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-400">{t("Choose a provider and visual model, then submit a key for a small image test. The key is saved only if the test passes.")}</p>
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
          <label className="block text-sm">{t("Visual model")}
            <select aria-label={t("Visual model")} className={`${field} mt-2`} value={chosen} disabled={busy} onChange={event => { setChosen(event.target.value); setError(""); setMessage(""); setProbe(null); }}>
              {!selected && <option value="">{t("New model")}</option>}
              {providerModels.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
          {provider && <div className="rounded-lg border border-neutral-800 p-4">
            <label htmlFor="provider-key" className="block text-sm font-medium">{provider.label} · {t("API key")}</label>
            <p className="mt-1 text-xs text-neutral-400">{provider.credential.configured ? t("A key is saved. The empty field does not mean it is missing.") : t("Enter a key for this provider. Models from the same provider share it.")}</p>
            <input id="provider-key" type="password" autoComplete="new-password" spellCheck={false} value={key} onChange={event => setKey(event.target.value)} disabled={disabled} className={`${field} mt-3`} placeholder={t("Saved keys are never displayed")} />
            <div className="mt-3 flex flex-wrap gap-2">
              <button className={`nerv-control ${button}`} disabled={!key || !selected || disabled} onClick={configure}>{t(busy ? "Testing…" : "Submit and test")}</button>
              <button className={`nerv-control ${button}`} disabled={disabled || !provider.credential.configured} onClick={() => act(() => saveCredential(provider.credential_ref, null), true)}>{t("Remove key")}</button>
            </div>
            <p className="mt-3 text-xs text-neutral-400">{t("Submitting sends one billable image request. Opening settings and creating sessions do not call the provider.")}</p>
            {request.current && <button className="nerv-control rounded border px-3 py-1 text-xs" onClick={() => {request.current?.abort(); setMessage(t("Configuration cancelled. Refresh to check whether saving had already completed."));}}>{t("Cancel test")}</button>}
            {probe && <figure className="mt-3 space-y-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`data:image/png;base64,${probe.image}`} alt={t("Configuration test image")} width={320} height={160} className="max-w-full rounded" />
              <figcaption className="break-words text-xs text-neutral-300">{t("Model answer")}: {probe.answer || t("No answer")}</figcaption>
            </figure>}
            <p className="mt-3 break-all text-xs text-neutral-500">{t("Stored on the backend host")}: {keyPath}</p>
            <p className="mt-1 text-xs text-neutral-500">{t("Project .env and shell API keys are ignored. Re-enter existing keys here; old files are not changed.")}</p>
          </div>}
          {selected && <>
            <div className="flex flex-wrap items-center gap-3">
              <span className={`text-sm ${selected.available ? "text-green-400" : "text-amber-400"}`}>{selected.available ? t("Key registered — sessions can be created") : t("Key not configured")}</span>
              <span className="text-xs text-neutral-500">{selected.model}</span>
            </div>
            <label className="block text-sm">{t("Reasoning effort")}
              <select aria-label={t("Reasoning effort")} className={`${field} mt-2`} value={selected.effort ?? ""} disabled={disabled} onChange={event => act(() => selectVlm(selected.id, event.target.value || null))}>
                <option value="">{t("Provider default")}{selected.provider_default_effort ? ` (${selected.provider_default_effort})` : ""}</option>
                {selected.efforts.map(e => <option key={e} value={e}>{e}</option>)}
              </select>
            </label>
            <details><summary className="cursor-pointer text-sm">{t("Optional API diagnostics")}</summary><div className="mt-3 grid gap-3 sm:grid-cols-3">{["image", "tools", "continuation"].map(check => <div key={check} className="border-b border-neutral-700 pb-2 text-sm"><div className="text-neutral-500">{t(check)}</div><div className="mt-1">{t(selected.checks?.[check] || "unverified")}</div></div>)}</div>
            <p className="text-xs text-neutral-500">{t("Physical acceptance")}: {t(selected.physical?.status || "unverified")}</p>
            <button className={`nerv-control ${button}`} disabled={disabled || !provider?.credential.configured} onClick={() => act(async () => {
              const result = await validateVlm(selected.id);
              if (!["image", "tools", "continuation"].every(c => result[c] === "passed")) {
                await load(); throw new Error(`${t("API validation failed")}: ${result.error || t("unverified")}`);
              }
            })}>{busy ? t("Working…") : t("Run API validation (billable)")}</button>
            <p className="text-xs text-neutral-500">{t("These diagnostics do not gate session creation or model use. They do not certify robot performance.")}</p></details>
          </>}
          {draft && <details className="border-t border-neutral-800 pt-4" key={chosen}>
            <summary className="cursor-pointer text-sm">{t("Advanced model settings")}</summary>
            <form className="mt-4 grid gap-4 sm:grid-cols-2" onSubmit={event => { event.preventDefault(); act(async () => { await putVlm(draft); setChosen(draft.id); }); }}>
              {[["id", t("Registration ID")], ["label", t("Display name")], ["model", t("Model ID")], ["base_url", t("API base URL")], ["evidence", t("Image capability documentation")]].map(([name, label]) => <label key={name} className={`block text-sm ${name === "evidence" ? "sm:col-span-2" : ""}`}>{label}
                <input required type={name === "base_url" || name === "evidence" ? "url" : "text"} value={String(draft[name] ?? "")} disabled={disabled || (name === "id" && !!selected)} className={`${field} mt-2`} onChange={event => edit(name, event.target.value)} />
              </label>)}
              <label className="block text-sm">{t("Maximum output tokens")}<input required type="number" min="1" value={Number(draft.max_output_tokens)} disabled={disabled} className={`${field} mt-2`} onChange={event => edit("max_output_tokens", event.target.valueAsNumber)} /></label>
              <label className="block text-sm">{t("Request timeout (seconds)")}<input required type="number" min="1" value={Number(draft.request_timeout_s)} disabled={disabled} className={`${field} mt-2`} onChange={event => edit("request_timeout_s", event.target.valueAsNumber)} /></label>
              <p className="text-xs text-neutral-500 sm:col-span-2">{t("Changing registration or effort invalidates matching API and physical acceptance. Unsupported effort values are rejected.")}</p>
              <button className={`nerv-control ${button}`} disabled={disabled} type="submit">{t("Save registration")}</button>
              <button type="button" className={`nerv-control ${button}`} disabled={disabled || !selected} onClick={() => {
                if (!selected) return;
                setChosen(""); setDraft({...registration(selected), id: "", label: "", model: "", evidence: "", efforts: [], context_window: null, provider_output_limit: null, provider_default_effort: null, limits_basis: ""});
              }}>{t("Register another VLM")}</button>
            </form>
          </details>}
          {locked && <p className="text-sm text-amber-400">{t("Finish the active task before changing models")}</p>}
          {error && <p role="alert" className="text-sm text-[var(--control-danger-text)]">{error}</p>}
          {message && <p role="status" className="text-sm text-green-400">{message}</p>}
        </div>
      </div>
    </div>
  </section>;
}
