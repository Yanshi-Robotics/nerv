import type { Registry, RobotDisplay } from "./api";

export type SessionTarget = {
  id: string;
  body: string;
  mode: "sim" | "real";
  world: string;
  robotLabel: string;
  sceneLabel: string;
  image?: string | null;
};

/** Only registered real devices and compatible installed worlds are selectable. */
export function sessionTargets(
  registry: Registry | null,
  robot: RobotDisplay | null,
): SessionTarget[] {
  if (!registry) return [];
  const targets: SessionTarget[] = [];
  const product = registry.bodies.find((body) => body.buses.real?.provider);
  if (robot?.registered && product?.buses.real?.enabled) {
    targets.push({
      id: `real:${product.name}`,
      body: product.name,
      mode: "real",
      world: "",
      robotLabel: robot.name,
      sceneLabel: "Real workspace",
      image: robot.image,
    });
  }
  for (const world of registry.worlds.filter((world) => world.kind === "sim")) {
    for (const body of registry.bodies) {
      if (
        !body.buses.sim?.enabled ||
        !registry.matrix.some(
          (cell) =>
            cell.ok && cell.body === body.name && cell.world === world.name,
        )
      )
        continue;
      targets.push({
        id: `sim:${body.name}:${world.name}`,
        body: body.name,
        mode: "sim",
        world: world.name,
        robotLabel: body.label || body.name,
        sceneLabel: world.label || world.name,
      });
    }
  }
  return targets;
}

export function selectedTarget(
  targets: SessionTarget[],
  selection: { body: string; mode: string; world: string },
) {
  return targets.find(
    (target) =>
      target.body === selection.body &&
      target.mode === selection.mode &&
      target.world === selection.world,
  );
}

export function adjacentTarget(
  targets: SessionTarget[],
  selection: { body: string; mode: string; world: string },
  direction: number,
) {
  if (!targets.length) return undefined;
  const index = targets.findIndex(
    (target) => target === selectedTarget(targets, selection),
  );
  if (index < 0) return direction < 0 ? targets.at(-1) : targets[0];
  return targets[
    (Math.max(index, 0) + direction + targets.length) % targets.length
  ];
}
