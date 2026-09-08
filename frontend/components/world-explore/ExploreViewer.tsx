"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

import { useI18n } from "@/lib/i18n";
import { exploreAsset, isExteriorFacility, localized, type Bounds, type ExploreManifest, type XYZ } from "@/lib/world-explore";

export type ExploreSelection = { kind: "room" | "facility"; id: string } | null;
export type ExploreLevel = number | "all" | "courtyard";
type Options = { floor: ExploreLevel; labels: boolean; facilities: boolean; surroundings: boolean; selection: ExploreSelection; reset: number; lang: string };
type Controller = { update: (options: Options) => void };
type MeshMeta = { floor?: number; levels?: number[]; role?: string; room?: string; facility?: string; facilities?: string[] };
const WALL_SECTION_M = 1.3; // Knee-height wall sections expose furnishings without cutting them.
const FLOOR_COMPLIANCE_M = 0.2; // Keep slab thickness and the bottom of stair flights.
const VIEW_FOV_DEG = 42;
const VIEW_DURATION_MS = 420;
const PIXEL_RATIO_LIMIT = 1.5; // Avoid a fourfold fill-rate cost on high-density screens.
const CLICK_MOVEMENT_PX = 5;
const LABEL_GAP_PX = 5;
const FACILITY_MARKER_SIZE_PX = 14;
const CAMERA_MIN_DISTANCE_M = 0.3;
const VIEW_MARGIN = 1.35;
const ROUTE_LIFT_M = 0.06; // Avoid z-fighting with the source-defined walking surface.

function disposeObject(root: THREE.Object3D, extraMaterials: THREE.Material[] = []) {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>(extraMaterials);
  const textures = new Set<THREE.Texture>();
  const bitmaps = new Set<ImageBitmap>();
  root.traverse((node) => {
    if (!(node instanceof THREE.Mesh || node instanceof THREE.Line)) return;
    geometries.add(node.geometry);
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) materials.add(material);
  });
  for (const material of materials) {
    for (const value of Object.values(material)) {
      if (!(value instanceof THREE.Texture)) continue;
      textures.add(value);
      if (typeof ImageBitmap !== "undefined" && value.source.data instanceof ImageBitmap) bitmaps.add(value.source.data);
    }
    material.dispose();
  }
  geometries.forEach((geometry) => geometry.dispose());
  textures.forEach((texture) => texture.dispose());
  bitmaps.forEach((bitmap) => bitmap.close());
}

export default function ExploreViewer({ manifest, floor, labels, facilities, surroundings, selection, reset, onSelect }: {
  manifest: ExploreManifest; floor: ExploreLevel; labels: boolean; facilities: boolean; surroundings: boolean;
  selection: ExploreSelection; reset: number; onSelect: (selection: ExploreSelection) => void;
}) {
  const { t, lang } = useI18n();
  const host = useRef<HTMLDivElement>(null);
  const controller = useRef<Controller | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const options = useRef<Options>({ floor, labels, facilities, surroundings, selection, reset, lang });
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;
  options.current = { floor, labels, facilities, surroundings, selection, reset, lang };

  useEffect(() => { controller.current?.update(options.current); }, [floor, labels, facilities, surroundings, selection, reset, lang]);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    setLoading(true); setError("");
    const abort = new AbortController();
    let disposed = false;
    let frame = 0;
    let drawnFrames = 0;
    let previousAnimatedFrame: number | null = null;
    let animationFrames = 0;
    let animationMilliseconds = 0;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false }); }
    catch { setError("WebGL 2 is unavailable. Use a browser with hardware acceleration."); setLoading(false); return; }
    renderer.setPixelRatio(Math.min(devicePixelRatio, PIXEL_RATIO_LIMIT));
    renderer.localClippingEnabled = true;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;
    renderer.domElement.setAttribute("aria-label", "3D scene");
    renderer.domElement.tabIndex = 0;
    renderer.domElement.style.cssText = "width:100%;height:100%;display:block;touch-action:none";
    element.appendChild(renderer.domElement);
    const overlay = document.createElement("div");
    overlay.style.cssText = "position:absolute;inset:0;overflow:hidden;pointer-events:none";
    element.appendChild(overlay);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#cbd3d5");
    const camera = new THREE.PerspectiveCamera(VIEW_FOV_DEG, 1, 0.05, 5000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false; // Rendering is event-driven, including when the camera rests.
    controls.minDistance = CAMERA_MIN_DISTANCE_M;
    controls.maxPolarAngle = Math.PI * 0.49;
    const hemisphere = new THREE.HemisphereLight(0xeaf2ff, 0x8c7961, 2.3);
    const sun = new THREE.DirectionalLight(0xfff0dc, 2.1);
    sun.position.set(-35, 70, 25);
    scene.add(hemisphere, sun);
    const toGLB = new THREE.Matrix4().fromArray(manifest.coordinate_system.to_glb);
    const toSource = toGLB.clone().invert();
    const convert = (point: XYZ) => new THREE.Vector3(...point).applyMatrix4(toGLB);
    const convertBounds = (bounds: Bounds) => new THREE.Box3(new THREE.Vector3(...bounds[0]), new THREE.Vector3(...bounds[1])).applyMatrix4(toGLB);
    const floorHeights = manifest.floors.map((f) => f.z).sort((a, b) => a - b);
    const bounds = new THREE.Box3();
    for (const room of manifest.rooms.filter((r) => !r.exterior)) bounds.union(convertBounds(room.bounds));
    const highlight = new THREE.Box3Helper(new THREE.Box3(), 0x3186ff);
    for (const material of Array.isArray(highlight.material) ? highlight.material : [highlight.material]) {
      material.depthTest = false;
      material.transparent = true;
      material.opacity = 0.9;
    }
    highlight.renderOrder = 2;
    highlight.visible = false;
    scene.add(highlight);
    const route = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x1774ff, depthTest: false }));
    route.renderOrder = 3;
    route.visible = false;
    scene.add(route);
    let root: THREE.Group | null = null;
    const originals = new Set<THREE.Material>();
    const clones = new Map<string, THREE.Material>();
    const baseMaterials = new Map<THREE.Mesh, THREE.Material | THREE.Material[]>();
    const highlightedMaterials = new Map<THREE.Material, THREE.Material>();
    const meshes: THREE.Mesh[] = [];
    let currentOptions = options.current;
    let lastFocus = "";
    let tween: { fromPosition: THREE.Vector3; fromTarget: THREE.Vector3; toPosition: THREE.Vector3; toTarget: THREE.Vector3; start: number } | null = null;
    const markerItems: { kind: "room" | "facility"; id: string; floor: number; point: THREE.Vector3; button: HTMLButtonElement; exterior: boolean }[] = [];
    const pickRay = new THREE.Raycaster();

    function requestRender() {
      if (!disposed && !frame) frame = requestAnimationFrame(render);
    }
    function placeLabels() {
      const occupied: { left: number; top: number; right: number; bottom: number }[] = [];
      const width = element!.clientWidth, height = element!.clientHeight;
      const sorted = [...markerItems].sort((a, b) => Number(b.id === currentOptions.selection?.id) - Number(a.id === currentOptions.selection?.id));
      for (const item of sorted) {
        const allowed = (item.kind === "room" ? currentOptions.labels : currentOptions.facilities)
          && (typeof currentOptions.floor !== "number" || item.floor === currentOptions.floor)
          && (currentOptions.floor !== "courtyard" || item.exterior);
        const projected = item.point.clone().project(camera);
        if (!allowed || projected.z < -1 || projected.z > 1 || Math.abs(projected.x) > 1 || Math.abs(projected.y) > 1) {
          item.button.style.visibility = "hidden"; continue;
        }
        const x = (projected.x + 1) * width / 2, y = (1 - projected.y) * height / 2;
        const bw = item.button.offsetWidth, bh = item.button.offsetHeight;
        const rect = { left: x - bw / 2, right: x + bw / 2, top: y - bh / 2, bottom: y + bh / 2 };
        if (occupied.some((r) => rect.left < r.right + LABEL_GAP_PX && rect.right > r.left - LABEL_GAP_PX && rect.top < r.bottom + LABEL_GAP_PX && rect.bottom > r.top - LABEL_GAP_PX)) {
          item.button.style.visibility = "hidden"; continue;
        }
        occupied.push(rect);
        item.button.style.visibility = "visible";
        item.button.style.transform = `translate(${Math.round(rect.left)}px,${Math.round(rect.top)}px)`;
      }
    }
    function render(now: number) {
      frame = 0;
      if (disposed) return;
      if (tween && previousAnimatedFrame !== null) {
        animationFrames += 1;
        animationMilliseconds += now - previousAnimatedFrame;
      }
      if (tween) {
        const progress = Math.min(1, (now - tween.start) / VIEW_DURATION_MS);
        const eased = progress * progress * (3 - 2 * progress);
        camera.position.lerpVectors(tween.fromPosition, tween.toPosition, eased);
        controls.target.lerpVectors(tween.fromTarget, tween.toTarget, eased);
        if (progress === 1) tween = null;
        controls.update();
      }
      renderer.render(scene, camera);
      placeLabels();
      previousAnimatedFrame = tween ? now : null;
      // Read-only diagnostics for the browser acceptance harness; no continuous telemetry.
      element!.dataset.drawnFrames = String(++drawnFrames);
      element!.dataset.animationFrames = String(animationFrames);
      element!.dataset.animationSeconds = String(animationMilliseconds / 1000);
      element!.dataset.renderCalls = String(renderer.info.render.calls);
      element!.dataset.geometries = String(renderer.info.memory.geometries);
      element!.dataset.textures = String(renderer.info.memory.textures);
      if (tween) requestRender();
    }
    function focus(box: THREE.Box3, immediate = false) {
      if (box.isEmpty()) return;
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      const extent = Math.max(size.x / Math.max(camera.aspect, 0.6), size.z, size.y, 1);
      const distance = extent / (2 * Math.tan(THREE.MathUtils.degToRad(VIEW_FOV_DEG / 2))) * VIEW_MARGIN;
      const offset = new THREE.Vector3(0.65, 0.95, 1).normalize().multiplyScalar(distance);
      if (immediate) { camera.position.copy(center).add(offset); controls.target.copy(center); controls.update(); }
      else tween = { fromPosition: camera.position.clone(), fromTarget: controls.target.clone(), toPosition: center.clone().add(offset), toTarget: center, start: performance.now() };
      requestRender();
    }
    function visibleMeta(meta: MeshMeta) {
      if (meta.role === "background") return currentOptions.surroundings;
      if (typeof currentOptions.floor !== "number") return true;
      if (meta.role === "ceiling" || meta.role === "roof") return false;
      // A slab belongs to the floor it supports, even when its thickness overlaps the level below.
      if (meta.role === "floor") return meta.floor === currentOptions.floor;
      if (meta.role === "exterior") return currentOptions.floor === manifest.floors[0]?.id;
      return (meta.levels ?? [meta.floor]).includes(currentOptions.floor);
    }
    function apply(next: Options) {
      currentOptions = next;
      const level = manifest.floors.find((f) => f.id === next.floor);
      const base = level?.z ?? 0;
      const ceiling = floorHeights.find((height) => height > base) ?? Math.max(...manifest.rooms.filter((r) => r.floor === level?.id).map((r) => r.bounds[1][2]), base + WALL_SECTION_M);
      for (const mesh of meshes) {
        const meta = mesh.userData as MeshMeta;
        mesh.visible = visibleMeta(meta);
        const baseMaterial = baseMaterials.get(mesh)!;
        const selectedFacility = next.selection?.kind === "facility" &&
          (meta.facility === next.selection.id || meta.facilities?.includes(next.selection.id));
        const tint = (material: THREE.Material) => {
          if (!selectedFacility) return material;
          let selectedMaterial = highlightedMaterials.get(material);
          if (!selectedMaterial) {
            selectedMaterial = material.clone();
            if (selectedMaterial instanceof THREE.MeshStandardMaterial) {
              selectedMaterial.emissive.set(0x1365cc);
              selectedMaterial.emissiveIntensity = 0.45;
            } else if ("color" in selectedMaterial && selectedMaterial.color instanceof THREE.Color) {
              selectedMaterial.color.lerp(new THREE.Color(0x3186ff), 0.4);
            }
            highlightedMaterials.set(material, selectedMaterial);
          }
          return selectedMaterial;
        };
        mesh.material = Array.isArray(baseMaterial) ? baseMaterial.map(tint) : tint(baseMaterial);
        const structural = ["wall", "stairs", "ceiling", "roof"].includes(meta.role ?? "");
        const planes = level && structural ? [
          new THREE.Plane(new THREE.Vector3(0, 1, 0), -base + FLOOR_COMPLIANCE_M),
          new THREE.Plane(new THREE.Vector3(0, -1, 0), meta.role === "wall" ? Math.min(ceiling, base + WALL_SECTION_M) : ceiling),
        ] : [];
        for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
          if ((material.clippingPlanes?.length ?? 0) !== planes.length) material.needsUpdate = true;
          material.clippingPlanes = planes;
        }
      }
      for (const item of markerItems) {
        const source = item.kind === "room" ? manifest.rooms.find((r) => r.id === item.id) : manifest.facilities.find((f) => f.id === item.id);
        const label = localized(source?.label, next.lang);
        item.button.title = label;
        item.button.setAttribute("aria-label", label);
        if (item.kind === "room") item.button.textContent = label;
      }
      const selected = next.selection?.kind === "room" ? manifest.rooms.find((r) => r.id === next.selection?.id)
        : manifest.facilities.find((f) => f.id === next.selection?.id);
      const points = selected && "points" in selected ? selected.points : null;
      let selectedBounds: THREE.Box3 | null = null;
      if (points?.length) selectedBounds = new THREE.Box3().setFromPoints(points.map(convert));
      else if (selected?.bounds) selectedBounds = convertBounds(selected.bounds);
      else if (selected && "anchor" in selected) selectedBounds = new THREE.Box3().setFromCenterAndSize(convert(selected.anchor), new THREE.Vector3(1, 1, 1));
      highlight.visible = !!selectedBounds && !points?.length;
      if (selectedBounds) {
        highlight.box.copy(selectedBounds);
        if (next.selection?.kind === "room") highlight.box.max.y = highlight.box.min.y + 0.08;
      }
      route.visible = !!points?.length;
      if (points?.length) {
        route.geometry.dispose();
        route.geometry = new THREE.BufferGeometry().setFromPoints(points.map((point) => convert(point).add(new THREE.Vector3(0, ROUTE_LIFT_M, 0))));
      }
      const focusKey = `${next.floor}:${next.selection?.kind}:${next.selection?.id}:${next.reset}`;
      if (focusKey !== lastFocus) {
        let viewBounds = bounds.clone();
        if (next.floor === "courtyard") {
          viewBounds.makeEmpty();
          manifest.rooms.forEach((r) => viewBounds.union(convertBounds(r.bounds)));
        } else if (level) {
          viewBounds.makeEmpty();
          manifest.rooms.filter((r) => r.floor === level.id && !r.exterior).forEach((r) => viewBounds.union(convertBounds(r.bounds)));
        }
        focus(selectedBounds ?? viewBounds, !lastFocus);
        lastFocus = focusKey;
      }
      requestRender();
    }
    function createMarkers() {
      const sources = [
        ...manifest.rooms.map((room) => ({ kind: "room" as const, item: room, point: convert([...(room.bounds[0].map((v, i) => (v + room.bounds[1][i]) / 2))] as XYZ), exterior: !!room.exterior })),
        ...manifest.facilities.map((facility) => ({ kind: "facility" as const, item: facility, point: convert(facility.anchor), exterior: isExteriorFacility(facility, manifest.rooms) })),
      ];
      for (const source of sources) {
        const button = document.createElement("button");
        button.type = "button";
        button.style.cssText = "position:absolute;left:0;top:0;pointer-events:auto;white-space:nowrap;cursor:pointer;visibility:hidden";
        if (source.kind === "room") {
          button.className = "rounded-md border border-white/70 bg-white/90 px-2 py-1 text-[11px] font-medium text-slate-700 shadow-sm hover:bg-blue-100 focus-visible:outline-2 focus-visible:outline-blue-500";
          source.point.y = convert((source.item as typeof manifest.rooms[number]).bounds[0]).y + 0.18;
        } else {
          button.style.width = `${FACILITY_MARKER_SIZE_PX}px`; button.style.height = `${FACILITY_MARKER_SIZE_PX}px`;
          button.className = "rounded-full border-2 border-white bg-blue-600 shadow-md hover:scale-125 focus-visible:outline-2 focus-visible:outline-blue-500";
        }
        button.onclick = () => selectRef.current({ kind: source.kind, id: source.item.id });
        overlay.appendChild(button);
        markerItems.push({ kind: source.kind, id: source.item.id, floor: source.item.floor, point: source.point, button, exterior: source.exterior });
      }
    }
    const start = () => { tween = null; };
    controls.addEventListener("start", start);
    controls.addEventListener("change", requestRender);
    const resize = new ResizeObserver(() => {
      const width = element.clientWidth, height = element.clientHeight;
      if (!width || !height) return;
      renderer.setSize(width, height, false);
      camera.aspect = width / height; camera.updateProjectionMatrix();
      requestRender();
    });
    resize.observe(element);
    let down: { x: number; y: number } | null = null;
    const pointerDown = (event: globalThis.PointerEvent) => { down = { x: event.clientX, y: event.clientY }; };
    const pointerUp = (event: globalThis.PointerEvent) => {
      if (!down || Math.hypot(event.clientX - down.x, event.clientY - down.y) > CLICK_MOVEMENT_PX || event.button !== 0) return;
      const rect = renderer.domElement.getBoundingClientRect();
      pickRay.setFromCamera(new THREE.Vector2(2 * (event.clientX - rect.left) / rect.width - 1, 1 - 2 * (event.clientY - rect.top) / rect.height), camera);
      const intersections = pickRay.intersectObjects(meshes.filter((mesh) => mesh.visible), false);
      for (const hit of intersections) {
        const mesh = hit.object as THREE.Mesh;
        const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
        if (material.clippingPlanes?.some((plane) => plane.distanceToPoint(hit.point) < 0)) continue;
        const meta = mesh.userData as MeshMeta;
        const facility = meta.facility ?? meta.facilities?.[0];
        if (facility) { selectRef.current({ kind: "facility", id: facility }); return; }
        if (meta.room) { selectRef.current({ kind: "room", id: meta.room }); return; }
        const point = hit.point.clone().applyMatrix4(toSource);
        const room = manifest.rooms.find((r) => (typeof currentOptions.floor !== "number" || r.floor === currentOptions.floor)
          && point.x >= r.bounds[0][0] && point.x <= r.bounds[1][0] && point.y >= r.bounds[0][1] && point.y <= r.bounds[1][1]
          && point.z >= r.bounds[0][2] - FLOOR_COMPLIANCE_M && point.z <= r.bounds[1][2]);
        if (room) selectRef.current({ kind: "room", id: room.id });
        return;
      }
    };
    renderer.domElement.addEventListener("pointerdown", pointerDown);
    renderer.domElement.addEventListener("pointerup", pointerUp);
    controller.current = { update: apply };
    const load = async () => {
      try {
        const asset = manifest.assets.find((a) => a.path.endsWith(".glb"));
        if (!asset) throw new Error("The display model is missing. Generate the display resources.");
        const response = await fetch(exploreAsset(manifest.scene, asset.path), { signal: abort.signal });
        if (!response.ok) throw new Error("The display model is missing or out of date. Generate the display resources.");
        const data = await response.arrayBuffer();
        if (disposed) return;
        const gltf = await new GLTFLoader().parseAsync(data, "");
        if (disposed) { disposeObject(gltf.scene); return; }
        root = gltf.scene;
        root.traverse((node) => {
          if (!(node instanceof THREE.Mesh)) return;
          let ancestor: THREE.Object3D | null = node;
          let metadata: MeshMeta = {};
          while (ancestor && !metadata.role) { metadata = ancestor.userData as MeshMeta; ancestor = ancestor.parent; }
          node.userData = { ...metadata, ...node.userData };
          const replace = (original: THREE.Material) => {
            originals.add(original);
            const key = `${original.uuid}:${node.userData.role}:${(node.userData.levels ?? [node.userData.floor]).join(",")}`;
            if (!clones.has(key)) clones.set(key, original.clone());
            return clones.get(key)!;
          };
          node.material = Array.isArray(node.material) ? node.material.map(replace) : replace(node.material);
          baseMaterials.set(node, node.material);
          meshes.push(node);
        });
        scene.add(root);
        createMarkers();
        apply(options.current);
        setLoading(false);
      } catch (e) { if (!disposed) { setError((e as Error).message); setLoading(false); } }
    };
    void load();
    return () => {
      disposed = true; abort.abort(); cancelAnimationFrame(frame); controller.current = null;
      resize.disconnect(); controls.removeEventListener("start", start); controls.removeEventListener("change", requestRender); controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown); renderer.domElement.removeEventListener("pointerup", pointerUp);
      disposeObject(scene, [...originals, ...clones.values(), ...highlightedMaterials.values()]);
      renderer.renderLists.dispose(); renderer.dispose(); renderer.forceContextLoss();
      renderer.domElement.remove(); overlay.remove();
    };
  }, [manifest]);

  return (
    <div className="relative h-full min-h-60 w-full overflow-hidden" ref={host} data-testid="explore-viewer">
      {(loading || error) && <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-neutral-950/90 p-8 text-center text-sm text-neutral-300" role={error ? "alert" : "status"}>
        {loading && <span className="h-6 w-6 animate-spin rounded-full border-2 border-neutral-700 border-t-blue-500" />}
        <p>{error ? t(error) : t("Loading the scene model…")}</p>
      </div>}
      {!loading && !error && <p className="pointer-events-none absolute bottom-3 left-3 rounded-md bg-white/80 px-2 py-1 text-[10px] text-slate-600">{t("Drag to orbit · scroll to zoom · right-drag to pan")}</p>}
    </div>
  );
}
