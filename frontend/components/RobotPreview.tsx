"use client";
import { useEffect, useRef, useState } from "react";
import { assetUrl } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
export default function RobotPreview({ image }: { image?: string | null }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false),
    [error, setError] = useState(false);
  const mount = useRef<HTMLDivElement>(null);
  const reset = useRef<() => void>(() => {});
  useEffect(() => {
    if (!open || !mount.current) return;
    let disposed = false;
    let cleanup = () => {};
    (async () => {
      const THREE = await import("three");
      const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
      const { DRACOLoader } = await import(
        "three/addons/loaders/DRACOLoader.js"
      );
      const { OrbitControls } = await import(
        "three/addons/controls/OrbitControls.js"
      );
      const view = await fetch("/robots/so101/view.json").then((r) => {
        if (!r.ok) throw new Error("Missing robot view configuration");
        return r.json();
      });
      if (disposed || !mount.current) return;
      const element = mount.current;
      const renderer = new THREE.WebGLRenderer({
        alpha: true,
        antialias: true,
      });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      element.appendChild(renderer.domElement);
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
      camera.up.fromArray(view.up);
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enablePan = false;
      scene.add(new THREE.HemisphereLight(0xffffff, 0x60747b, 1.6));
      const light = new THREE.DirectionalLight(0xffffff, 2);
      light.position.set(2, -3, 4);
      scene.add(light);
      const draco = new DRACOLoader().setDecoderPath("/robots/draco/");
      const render = () => {
        if (!disposed) renderer.render(scene, camera);
      };
      controls.addEventListener("change", render);
      const resize = new ResizeObserver(() => {
        const { width, height } = element.getBoundingClientRect();
        renderer.setSize(width, height);
        camera.aspect = width / Math.max(height, 1);
        camera.updateProjectionMatrix();
        render();
      });
      resize.observe(element);
      const disposeModel = (object: import("three").Object3D) =>
        object.traverse((node) => {
          if (node instanceof THREE.Mesh) {
            node.geometry.dispose();
            const materials = Array.isArray(node.material)
              ? node.material
              : [node.material];
            materials.forEach((m) => m.dispose());
          }
        });
      cleanup = () => {
        resize.disconnect();
        controls.dispose();
        draco.dispose();
        disposeModel(scene);
        renderer.dispose();
        renderer.domElement.remove();
      };
      const model = await new GLTFLoader()
        .setDRACOLoader(draco)
        .loadAsync("/robots/so101/so101.glb");
      if (disposed) {
        disposeModel(model.scene);
        return;
      }
      // The source gallery includes a leader; this product presents the follower only.
      const excluded: import("three").Object3D[] = [];
      model.scene.traverse((node) => {
        if (view.hideNodes.includes(node.name)) excluded.push(node);
      });
      excluded.forEach((node) => {
        node.removeFromParent();
        disposeModel(node);
      });
      for (const [joint, degrees] of Object.entries(view.restPoseDegrees)) {
        const pivot = model.scene.getObjectByName(joint);
        if (pivot) pivot.rotation.z = THREE.MathUtils.degToRad(Number(degrees));
      }
      scene.add(model.scene);
      const box = new THREE.Box3().setFromObject(model.scene);
      const center = box.getCenter(new THREE.Vector3());
      const extent = box.getSize(new THREE.Vector3()).length();
      reset.current = () => {
        camera.position
          .copy(center)
          .add(
            new THREE.Vector3(
              ...(view.cameraDirection as [number, number, number]),
            ).multiplyScalar(extent),
          );
        controls.target.copy(center);
        controls.update();
        render();
      };
      reset.current();
    })().catch(() => {
      cleanup();
      if (!disposed) {
        setError(true);
        setOpen(false);
      }
    });
    return () => {
      disposed = true;
      cleanup();
    };
  }, [open]);
  return (
    <div className="robot-preview">
      <div className="robot-preview-stage">
        {open ? (
          <div ref={mount} className="h-full w-full" />
        ) : (
          <img
            src={image ? assetUrl(image) : "/robots/so101/poster.jpg"}
            alt="SO-101"
            className="h-full w-full object-contain"
          />
        )}
      </div>
      <div className="flex justify-center gap-4 py-2 text-xs">
        <button
          className="nerv-control underline"
          onClick={() => {
            setError(false);
            setOpen(!open);
          }}
        >
          {t(open ? "Close 3D" : "View 3D")}
        </button>
        {open && (
          <button
            className="nerv-control underline"
            onClick={() => reset.current()}
          >
            {t("Reset view")}
          </button>
        )}
      </div>
      {error && (
        <p role="status" className="text-center text-xs">
          {t("3D unavailable. The image and robot controls remain available.")}
        </p>
      )}
    </div>
  );
}
