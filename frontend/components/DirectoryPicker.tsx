"use client";
import {useEffect, useRef, useState} from "react";
import {browseDirectories} from "@/lib/api";
import {useI18n} from "@/lib/i18n";

type Listing = {path: string; parent: string; roots: string[]; directories: {name: string; path: string}[]; next_offset: number | null};

export default function DirectoryPicker({value, onChange, disabled}: {value: string; onChange: (path: string) => void; disabled?: boolean}) {
  const {t} = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const [listing, setListing] = useState<Listing | null>(null);
  const [path, setPath] = useState(value);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {setPath(value);}, [value]);
  async function load(target?: string, offset = 0) {
    setBusy(true); setError("");
    try {const data: Listing = await browseDirectories(target, offset); setListing(data); setPath(data.path);}
    catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  return <>
    <button type="button" disabled={disabled} className="nerv-control mt-2 rounded border px-3 py-2" onClick={() => {dialog.current?.showModal(); load(value || undefined);}}>{t("Browse folders")}</button>
    <dialog ref={dialog} className="m-auto w-[min(36rem,94vw)] max-h-[85vh] overflow-auto rounded-xl border border-neutral-600 bg-neutral-900 p-5 text-neutral-100 backdrop:bg-black/60" aria-label={t("Choose a folder")}>
      <h3 className="mb-3 text-lg">{t("Choose a folder")}</h3>
      <p className="mb-3 text-xs text-neutral-400">{t("Folders are on the computer running NERV. Model files stay in place.")}</p>
      <form className="flex gap-2" onSubmit={e => {e.preventDefault(); load(path);}}><input aria-label={t("Folder path")} className="min-w-0 flex-1 rounded border border-neutral-600 bg-neutral-950 p-2" value={path} onChange={e => setPath(e.target.value)} /><button disabled={busy} className="nerv-control rounded border px-3">{t("Open")}</button></form>
      <div className="my-3 flex flex-wrap gap-2">{listing?.roots.map(root => <button disabled={busy} className="nerv-control max-w-full break-all rounded border px-2 py-1 text-xs" key={root} onClick={() => load(root)}>{root}</button>)}<button disabled={busy || !listing || listing.path === listing.parent} className="nerv-control rounded border px-2 py-1" onClick={() => load(listing?.parent)}>{t("Parent folder")}</button></div>
      {error && <p role="alert" className="break-words text-sm text-[var(--control-danger-text)]">{error}</p>}
      <ul className="max-h-64 overflow-auto rounded border border-neutral-700">{listing?.directories.map(item => <li key={item.path}><button disabled={busy} className="nerv-control w-full break-all p-2 text-left" onClick={() => load(item.path)}>▸ {item.name}</button></li>)}</ul>
      {listing?.next_offset != null && <button disabled={busy} className="nerv-control mt-2 underline" onClick={() => load(listing.path, listing.next_offset!)}>{t("More folders")}</button>}
      <div className="mt-4 flex justify-end gap-3"><button className="nerv-control rounded border px-3 py-2" onClick={() => dialog.current?.close()}>{t("Cancel")}</button><button disabled={busy || !listing || !!error || path !== listing.path} className="nerv-control nerv-primary rounded px-3 py-2" onClick={() => {if (listing) onChange(listing.path); dialog.current?.close();}}>{t("Use this folder")}</button></div>
    </dialog>
  </>;
}
