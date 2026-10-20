"use client";
import { useEffect, useState } from "react";
import { changeWorld, getWorldControl, type SessionSummary, type WorldDescriptor } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import WorldProperties from "./WorldProperties";

export default function WorldControlPanel({session, onChanged}: {session: SessionSummary; onChanged?: () => void}) {
  const {t} = useI18n();
  const [descriptor, setDescriptor] = useState<WorldDescriptor | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [commandValues, setCommandValues] = useState<Record<string, Record<string, unknown>>>({});
  useEffect(() => {
    let gone = false;
    setDescriptor(null); setValues({}); setCommandValues({}); setError("");
    getWorldControl(session.id).then(data => {
      if (!gone) {setDescriptor(data); setValues(data.state.configuration ?? {});}
    }).catch(e => !gone && setError(e.message));
    return () => {gone = true;};
  }, [session.id]);
  async function apply(command: string, arguments_: Record<string, unknown>) {
    if (!descriptor) return;
    setBusy(true); setError("");
    try {
      const result = await changeWorld(session.id, descriptor, command, arguments_);
      if (result.session) window.dispatchEvent(new CustomEvent("nerv-instance-created", {detail: result.session.id}));
      else setDescriptor(await getWorldControl(session.id));
      onChanged?.();
    } catch (e) {
      setError((e as Error).message);
      // A language command may have advanced the revision since the panel opened.
      // Refresh for the next explicit click; never silently retry a world mutation.
      try {setDescriptor(await getWorldControl(session.id));} catch {setDescriptor(null);}
    } finally {setBusy(false);}
  }
  return <details className="border-b border-neutral-800 bg-neutral-900 px-3 py-2 text-xs"><summary className="cursor-pointer text-neutral-300">{t("World control")} · {t("Simulation only")}</summary>
    <div className="max-h-64 space-y-3 overflow-auto pt-3">
      <p className="text-neutral-400">{t("World changes stop conflicting tasks. Object changes create a new instance in standby.")}</p>
      {descriptor && <fieldset disabled={busy} className="space-y-3 disabled:opacity-60">
        {!!Object.keys(descriptor.configuration.properties ?? {}).length && <><WorldProperties schema={descriptor.configuration} values={values} onChange={setValues}/><button onClick={() => apply("rebuild", values)} className="rounded bg-blue-600 px-3 py-1.5 text-white">{t("End and rebuild instance")}</button></>}
        {descriptor.commands.filter(c => c.name !== "rebuild").map(command => <div key={command.name} className="space-y-2"><WorldProperties schema={command.parameters} values={commandValues[command.name] ?? {}} onChange={v => setCommandValues({...commandValues, [command.name]: v})}/><button onClick={() => apply(command.name, commandValues[command.name] ?? {})} className="rounded border border-neutral-600 px-3 py-1.5">{t(command.name === "reset" ? "Reset world" : command.name === "time" ? "Apply lighting phase" : command.name)}</button></div>)}
      </fieldset>}
      {busy && <p role="status">{t("Applying world change…")}</p>}{error && <p role="alert" className="text-red-300">{error}</p>}
    </div>
  </details>;
}
