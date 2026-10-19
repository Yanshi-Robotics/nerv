"use client";
import { useEffect, useState } from "react";
import { getVlm, putVlm, saveCredential, selectVlm, validateVlm, type VlmModel } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const field = "w-full rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";
const button = "rounded-md border border-neutral-600 px-3 py-2 text-xs hover:bg-neutral-800 disabled:opacity-40";

export default function VlmPanel({ onChanged }: { onChanged: () => void }) {
  const { t } = useI18n();
  const [models, setModels] = useState<VlmModel[]>([]);
  const [chosen, setChosen] = useState("");
  const [draft, setDraft] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const selected = models.find(m => m.id === chosen);
  const load = async () => {
    const data = await getVlm();
    setModels(data.models); setLocked(data.task_locked);
    setChosen(value => value || data.selection || data.models[0]?.id || "");
  };
  useEffect(() => { load().catch(e => setError(e.message)); }, []);
  useEffect(() => {
    if (!selected) return;
    const { credential: _credential, checks: _checks, available: _available, effort: _effort, physical: _physical, ...spec } = selected;
    setDraft(JSON.stringify(spec, null, 2)); setKey("");
  }, [selected]);
  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError(""); setMessage("");
    try { await action(); await load(); onChanged(); setMessage(t("Settings updated")); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="h-full overflow-y-auto bg-neutral-950 p-6 text-neutral-200">
    <div className="mx-auto max-w-5xl">
      <h1 className="text-2xl font-semibold">{t("Visual model registry")}</h1>
      <p className="mt-2 max-w-3xl text-sm leading-relaxed text-neutral-400">{t("Register a visual model, configure its key, then test image input, tools and continuation. API validation does not certify robot performance.")}</p>
      <div className="mt-6 grid gap-6 md:grid-cols-[280px_minmax(0,1fr)]">
        <div className="space-y-2">
          {models.map(model => <button key={model.id} onClick={() => { setChosen(model.id); setKey(""); }} className={`w-full rounded-lg border p-4 text-left ${chosen === model.id ? "border-blue-500 bg-blue-950/20" : "border-neutral-800 hover:border-neutral-600"}`}>
            <div className="font-medium">{model.label}</div>
            <div className="mt-1 text-xs text-neutral-500">{model.model}</div>
            <div className={`mt-3 text-xs ${model.available ? "text-green-400" : "text-amber-400"}`}>{model.available ? t("Ready for API use") : t("Validation required")}</div>
          </button>)}
          <button className={`${button} w-full`} onClick={() => { setChosen(""); setDraft(JSON.stringify({id:"new-vlm", provider:"vendor", label:"", model:"", base_url:"", protocol:"responses", credential_ref:"VENDOR_API_KEY", input_modalities:["text","image"], supports_tools:true, efforts:[], evidence:"", enabled:true}, null, 2)); }}>{t("Register another VLM")}</button>
        </div>
        <div className="min-w-0 space-y-5">
          {selected && <>
            <div className="grid gap-3 sm:grid-cols-3">{["image", "tools", "continuation"].map(check => <div key={check} className="border-b border-neutral-700 pb-2 text-sm"><div className="text-neutral-500">{t(check)}</div><div className="mt-1">{t(selected.checks?.[check] || "unverified")}</div></div>)}</div>
            <p className="text-xs text-neutral-500">{t("Physical acceptance")}: {t(selected.physical?.status || "unverified")}</p>
            <label className="block text-sm">{t("Reasoning effort")}
              <select className={`${field} mt-2`} value={selected.effort ?? ""} disabled={busy || locked} onChange={event => act(() => selectVlm(selected.id, event.target.value || null))}>
                <option value="">{t("Provider default")}</option>{selected.efforts.map(effort => <option key={effort} value={effort}>{effort}</option>)}
              </select>
            </label>
            <div className="border-y border-neutral-800 py-4">
              <div className="text-sm">{t("API key")} <span className="text-xs text-neutral-500">{selected.credential.configured ? t("configured") : t("not configured")} ({selected.credential_ref})</span></div>
              <input type="password" autoComplete="new-password" value={key} onChange={event => setKey(event.target.value)} disabled={!selected.credential.writable || busy || locked} className={`${field} mt-2`} aria-label={t("API key")} placeholder={t("Saved keys are never displayed")} />
              <div className="mt-2 flex flex-wrap gap-2">
                <button className={button} disabled={!key || busy || locked || !selected.credential.writable} onClick={() => { const secret = key; setKey(""); act(() => saveCredential(selected.credential_ref, secret)); }}>{t("Save key")}</button>
                <button className={button} disabled={busy || locked || !selected.credential.writable} onClick={() => act(() => saveCredential(selected.credential_ref, null))}>{t("Remove key")}</button>
                <button className={button} disabled={busy || locked || !selected.credential.configured} onClick={() => act(() => validateVlm(selected.id))}>{t("Run API validation (billable)")}</button>
              </div>
            </div>
          </>}
          <label className="block text-sm">{t("Model registration")}
            <textarea value={draft} onChange={event => setDraft(event.target.value)} spellCheck={false} rows={18} className={`${field} mt-2 font-mono text-xs`} aria-label={t("Model registration")} />
          </label>
          <p className="text-xs text-neutral-500">{t("Changing registration or effort invalidates matching API and physical acceptance. Unsupported effort values are rejected.")}</p>
          <button className={button} disabled={busy || locked} onClick={() => act(() => putVlm(JSON.parse(draft)))}>{busy ? t("Working…") : t("Save registration")}</button>
          {locked && <p className="text-sm text-amber-400">{t("Finish the active task before changing models")}</p>}
          {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
          {message && <p role="status" className="text-sm text-green-400">{message}</p>}
        </div>
      </div>
    </div>
  </section>;
}
