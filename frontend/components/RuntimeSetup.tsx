"use client";
import { useState } from "react";
import { manageRuntime, type Registry } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Field = {type: string; title: string; enum?: string[]; default?: string | boolean};
type Descriptor = {label: string; operations: Record<string, {properties: Record<string, Field>}>};

export default function RuntimeSetup({registry, onChanged}: {registry: Registry | null; onChanged: () => void}) {
  const {t} = useI18n();
  const bindings = registry?.bodies.flatMap(b => Object.values(b.buses)) ?? [];
  const [python, setPython] = useState("");
  const [provider, setProvider] = useState("");
  const [descriptor, setDescriptor] = useState<Descriptor | null>(null);
  const [values, setValues] = useState<Record<string, Record<string, string | boolean>>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  async function run(operation: string) {
    setBusy(true); setError(""); setResult("");
    try {
      const response = await manageRuntime(python, provider, operation, values[operation] ?? {});
      if (operation === "describe") {
        setDescriptor(response as Descriptor);
        setValues(Object.fromEntries(Object.entries((response as Descriptor).operations).map(([op, schema]) =>
          [op, Object.fromEntries(Object.entries(schema.properties).map(([key, field]) => [key, field.default ?? (field.type === "boolean" ? false : "")]))])));
      } else { setResult(JSON.stringify(response, null, 2)); onChanged(); }
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const input = "w-full rounded border border-neutral-600 bg-neutral-900 p-2 text-sm";
  return <section className="col-span-2 overflow-auto p-6 space-y-5">
    <h1 className="text-xl">{t("Device and policy setup")}</h1>
    <p>{t("Configure an installed runtime and import a trained policy. These steps do not connect the robot.")}</p>
    <label className="block">{t("Runtime Python executable")}<input className={input} value={python} onChange={e => setPython(e.target.value)} /></label>
    <label className="block">{t("Runtime provider")}<input list="runtime-providers" className={input} value={provider} onChange={e => setProvider(e.target.value)} /></label>
    <datalist id="runtime-providers">{Array.from(new Set(bindings.map(b => b.provider).filter(Boolean))).map(p => <option key={p} value={p} />)}</datalist>
    <button className="nerv-control rounded border p-2" disabled={busy || !python || !provider} onClick={() => run("describe")}>{t("Read setup options")}</button>
    {descriptor && <h2>{descriptor.label}</h2>}
    {descriptor && Object.entries(descriptor.operations).map(([operation, schema]) => <fieldset key={operation} disabled={busy} className="border border-neutral-700 rounded p-4 space-y-3">
      <legend>{t(operation)}</legend>
      {Object.entries(schema.properties).map(([key, field]) => <label key={key} className="block text-sm">{t(field.title)}
        {field.type === "boolean" ? <input type="checkbox" className="ml-3" checked={Boolean(values[operation]?.[key])} onChange={e => setValues(v => ({...v, [operation]: {...v[operation], [key]: e.target.checked}}))} />
          : field.enum ? <select className={input} value={String(values[operation]?.[key] ?? "")} onChange={e => setValues(v => ({...v, [operation]: {...v[operation], [key]: e.target.value}}))}>{field.enum.map(value => <option key={value} value={value}>{value}</option>)}</select>
          : <input required className={input} value={String(values[operation]?.[key] ?? "")} onChange={e => setValues(v => ({...v, [operation]: {...v[operation], [key]: e.target.value}}))} />}
      </label>)}
      <button className="nerv-control rounded border p-2" onClick={() => run(operation)}>{t("Apply")}</button>
    </fieldset>)}
    {error && <p role="alert" className="text-red-400">{error}</p>}
    {result && <pre className="whitespace-pre-wrap text-xs">{result}</pre>}
  </section>;
}
