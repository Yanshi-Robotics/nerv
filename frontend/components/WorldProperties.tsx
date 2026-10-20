"use client";
import { useI18n } from "@/lib/i18n";
import { type PropertySchema } from "@/lib/api";

export default function WorldProperties({schema, values, onChange}: {schema: PropertySchema; values: Record<string, unknown>; onChange: (values: Record<string, unknown>) => void}) {
  const {t, lang} = useI18n();
  return <div className="space-y-2">{Object.entries(schema.properties ?? {}).map(([key, field]) => {
    const value = values[key] ?? field.default ?? "";
    const choices = field.oneOf ?? field.enum?.map(value => ({const: value, title: value, description: "", title_locales: {} as Record<string, string>, description_locales: {} as Record<string, string>}));
    return <label className="block text-xs" key={key}><span className="mb-1 block text-neutral-300">{t(field.title || key)}</span>
      {choices ? <><select aria-label={field.title || key} className="w-full rounded border border-neutral-600 bg-neutral-900 p-1.5" value={String(value)} onChange={e => onChange({...values, [key]: e.target.value})}>
        <option value="" disabled>{t("Choose a value")}</option>{choices.map(choice => <option key={choice.const} value={choice.const}>{choice.title_locales?.[lang] || t(choice.title)}</option>)}
      </select><span className="mt-1 block text-[11px] text-neutral-400">{choices.find(c => c.const === value)?.description_locales?.[lang] || choices.find(c => c.const === value)?.description}</span></> :
      <input type={field.type === "number" || field.type === "integer" ? "number" : field.type === "boolean" ? "checkbox" : "text"} value={String(value)} checked={field.type === "boolean" ? Boolean(value) : undefined} onChange={e => onChange({...values, [key]: field.type === "boolean" ? e.target.checked : field.type === "number" || field.type === "integer" ? Number(e.target.value) : e.target.value})} className="w-full rounded border border-neutral-600 bg-neutral-900 p-1.5" />}
    </label>;
  })}</div>;
}
