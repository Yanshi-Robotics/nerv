"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { getExplore, localized, type ExploreManifest } from "@/lib/world-explore";
import type { WorldSpec } from "@/lib/api";
import type { ExploreLevel, ExploreSelection } from "./ExploreViewer";

// Keep the renderer and its dependencies out of session-only page loads.
const ExploreViewer = dynamic(() => import("./ExploreViewer"), { ssr: false });
const KIND_LABELS: Record<string, string> = {
  joint: "Operable joint", movable: "Movable object", boundary: "Route and boundary", pose: "Seating geometry", static: "Fixed furnishing",
};
const OPERATION_LABELS: Record<string, string> = { joint: "Open, close or adjust", grab: "Grab and drag", release: "Release" };

export default function WorldExplore({ worlds, defaultWorld }: { worlds: WorldSpec[]; defaultWorld: string | null }) {
  const { t, lang } = useI18n();
  const candidates = worlds.filter((world) => world.kind === "sim");
  const [world, setWorld] = useState(defaultWorld && candidates.some((w) => w.name === defaultWorld) ? defaultWorld : candidates[0]?.name ?? "");
  const [manifest, setManifest] = useState<ExploreManifest | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [floor, setFloor] = useState<ExploreLevel>(0);
  const [labels, setLabels] = useState(true);
  const [facilities, setFacilities] = useState(true);
  const [surroundings, setSurroundings] = useState(true);
  const [selection, setSelection] = useState<ExploreSelection>(null);
  const [search, setSearch] = useState("");
  const [reset, setReset] = useState(0);

  useEffect(() => { if (!world && candidates.length) setWorld(candidates[0].name); }, [world, candidates]);
  useEffect(() => {
    const abort = new AbortController();
    setManifest(null); setError(""); setSelection(null); setSearch(""); setLoading(true);
    if (world) getExplore(world, abort.signal).then((next) => {
      if (next.schema_version !== 1 || next.coordinate_system?.glb_up_axis !== "Y") throw new Error("The display resources use an unsupported format. Regenerate them.");
      setManifest(next); setFloor(next.floors[0]?.id ?? 0);
    }).catch((e: Error) => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    else setLoading(false);
    return () => abort.abort();
  }, [world]);
  const room = selection?.kind === "room" ? manifest?.rooms.find((r) => r.id === selection.id) : null;
  const facility = selection?.kind === "facility" ? manifest?.facilities.find((f) => f.id === selection.id) : null;
  const relatedRoom = facility?.room ? manifest?.rooms.find((r) => r.id === facility.room) : room;
  const query = search.trim().toLocaleLowerCase();
  const matching = <T extends { label: Record<string, string>; id: string }>(items: T[]) => items.filter((item) =>
    !query || Object.values(item.label).some((label) => label.toLocaleLowerCase().includes(query)) || item.id.toLowerCase().includes(query));
  const roomList = matching(manifest?.rooms ?? []).filter((r) => query || floor === "all" || (floor === "courtyard" ? r.exterior : r.floor === floor));
  const facilityList = matching(manifest?.facilities ?? []).filter((f) => query || floor === "all" || (floor === "courtyard" ? manifest?.rooms.find((r) => r.id === f.room)?.exterior || f.kind === "boundary" : f.floor === floor));
  function pick(next: ExploreSelection) {
    if (!next || !manifest) { setSelection(null); return; }
    const source = next.kind === "room" ? manifest.rooms.find((r) => r.id === next.id) : manifest.facilities.find((f) => f.id === next.id);
    if (source) setFloor(source.floor);
    setSelection(next);
  }
  const button = "rounded-md border border-neutral-700 px-2.5 py-1.5 text-xs text-neutral-300 hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-blue-500";
  return (
    <section className="flex h-full min-w-0 flex-col overflow-hidden" aria-label="Nerv World Explore">
      <header className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2 border-b border-neutral-800 bg-neutral-900 px-5 py-3">
        <div className="mr-auto"><h1 className="text-sm font-semibold">Nerv World Explore</h1><p className="mt-0.5 text-[11px] text-neutral-500">{t("Scene guide · initial layout · daylight")}</p></div>
        <label className="flex items-center gap-2 text-xs text-neutral-400">{t("Map")}
          <select value={world} onChange={(e) => setWorld(e.target.value)} className="rounded-md border border-neutral-700 bg-neutral-800 px-3 py-1.5 text-neutral-200" aria-label={t("Map")}>
            {candidates.map((w) => <option key={w.name} value={w.name}>{w.name}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-neutral-400">{t("Level")}
          <select value={floor} onChange={(e) => { const v = e.target.value; setFloor(v === "all" || v === "courtyard" ? v : Number(v)); setSelection(null); }}
            className="rounded-md border border-neutral-700 bg-neutral-800 px-3 py-1.5 text-neutral-200" aria-label={t("Level")} disabled={!manifest}>
            <option value="all">{t("Whole building")}</option>
            {manifest?.floors.map((f) => <option key={f.id} value={f.id}>{localized(f.label, lang)}</option>)}
            {manifest?.rooms.some((r) => r.exterior) && <option value="courtyard">{t("Courtyard")}</option>}
          </select>
        </label>
        <button className={button} onClick={() => { setSelection(null); setReset((n) => n + 1); }}>{t("Restore view")}</button>
        <div className="flex w-full flex-wrap gap-4 text-[11px] text-neutral-400">
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={labels} onChange={(e) => setLabels(e.target.checked)} />{t("Room labels")}</label>
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={facilities} onChange={(e) => setFacilities(e.target.checked)} />{t("Facility markers")}</label>
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={surroundings} onChange={(e) => setSurroundings(e.target.checked)} />{t("Surroundings")}</label>
        </div>
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_290px] max-[1000px]:grid-cols-[minmax(0,1fr)_230px]">
        <div className="relative min-h-0 min-w-0 bg-neutral-950">
          {manifest && <ExploreViewer manifest={manifest} floor={floor} labels={labels} facilities={facilities} surroundings={surroundings} selection={selection} reset={reset} onSelect={pick} />}
          {!manifest && <div className="flex h-full items-center justify-center p-8 text-center text-sm text-neutral-400" role={error ? "alert" : "status"}>
            <div><p>{error ? t(error) : loading ? t("Loading the scene guide…") : t("No display worlds are available.")}</p>
              {error && <p className="mt-3 text-xs text-neutral-500">{t("Prepare the display resources, then reopen this view. The simulation does not need to be running.")}</p>}</div>
          </div>}
        </div>
        <aside className="flex min-h-0 flex-col border-l border-neutral-800 bg-neutral-900" aria-label={t("Rooms and facilities")}>
          <div className="shrink-0 border-b border-neutral-800 p-3">
            <input aria-label={t("Search rooms and facilities")} placeholder={t("Search rooms and facilities")} value={search} onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-xs outline-none focus:border-blue-500" />
          </div>
          {(room || facility) && <div className="max-h-[45%] shrink-0 overflow-auto border-b border-neutral-800 p-4 text-xs">
            <button className="float-right text-neutral-500 hover:text-neutral-200" aria-label={t("Clear selection")} onClick={() => setSelection(null)}>×</button>
            <p className="pr-4 text-sm font-medium">{localized((facility ?? room)?.label, lang)}</p>
            <p className="mt-1 text-[11px] text-neutral-500">{localized(manifest?.floors.find((f) => f.id === (facility ?? room)?.floor)?.label, lang)}{facility && relatedRoom ? ` · ${localized(relatedRoom.label, lang)}` : ""}</p>
            {facility && <>
              <p className="mt-3 text-blue-400">{t(KIND_LABELS[facility.kind])}</p>
              <p className="mt-2 leading-relaxed text-neutral-400">{localized(facility.description, lang)}</p>
              {facility.operations.length > 0 && <p className="mt-3 text-neutral-300">{facility.operations.map((op) => t(OPERATION_LABELS[op] ?? op)).join(" · ")}</p>}
              <p className="mt-3 font-medium text-neutral-300">{t("Test method")}</p>
              <p className="mt-1 leading-relaxed text-neutral-400">{localized(facility.test, lang)}</p>
            </>}
            {room && <div className="mt-3 space-y-1">{manifest?.facilities.filter((f) => f.room === room.id).map((f) => <button key={f.id} onClick={() => pick({ kind: "facility", id: f.id })} className="block text-left text-blue-400 hover:text-blue-300">{localized(f.label, lang)}</button>)}</div>}
          </div>}
          <div className="min-h-0 flex-1 overflow-auto p-3">
            <p className="px-1 pb-2 text-[10px] font-medium uppercase tracking-wide text-neutral-500">{t("Rooms")}</p>
            {roomList.map((r) => <button key={r.id} className={`mb-0.5 block w-full rounded-md px-2 py-2 text-left text-xs hover:bg-neutral-800 ${selection?.id === r.id ? "bg-blue-600/15 text-blue-400" : "text-neutral-300"}`}
              onClick={() => pick({ kind: "room", id: r.id })}>{localized(r.label, lang)}</button>)}
            <p className="mt-4 px-1 pb-2 text-[10px] font-medium uppercase tracking-wide text-neutral-500">{t("Facilities")}</p>
            {facilityList.map((f) => <button key={f.id} className={`mb-0.5 block w-full rounded-md px-2 py-2 text-left text-xs hover:bg-neutral-800 ${selection?.id === f.id ? "bg-blue-600/15 text-blue-400" : "text-neutral-400"}`}
              onClick={() => pick({ kind: "facility", id: f.id })}><span>{localized(f.label, lang)}</span><span className="mt-0.5 block text-[10px] text-neutral-500">{t(KIND_LABELS[f.kind])}</span></button>)}
            {manifest && roomList.length + facilityList.length === 0 && <p className="p-2 text-xs text-neutral-500">{t("No matching rooms or facilities.")}</p>}
          </div>
          <p className="shrink-0 border-t border-neutral-800 p-3 text-[10px] leading-relaxed text-neutral-500">{t("This guide does not control the simulation. Use Scene test in an active session to operate furniture.")}</p>
        </aside>
      </div>
    </section>
  );
}
