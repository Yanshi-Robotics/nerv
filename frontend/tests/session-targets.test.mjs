import { test } from "node:test";
import assert from "node:assert/strict";
import {
  sessionTargets,
  selectedTarget,
  adjacentTarget,
} from "../lib/session-targets.ts";
const registry = {
  bodies: [
    {
      name: "arm",
      label: "Standard arm",
      buses: {
        real: { provider: "adapter", enabled: true },
        sim: { enabled: true },
      },
    },
  ],
  worlds: [
    { name: "pick", kind: "sim", label: "Pick and place" },
    { name: "sort", kind: "sim", label: "Sorting" },
    { name: "unsupported", kind: "sim" },
  ],
  matrix: [
    { body: "arm", world: "pick", ok: true },
    { body: "arm", world: "sort", ok: true },
    { body: "arm", world: "unsupported", ok: false },
  ],
};
const robot = {
  registered: true,
  name: "Classroom arm",
  image: "/api/robot/image?v=1",
};
test("registration and compatibility determine available scenes, not a generic robot catalogue", () => {
  assert.deepEqual(sessionTargets(null, null), []);
  const sims = sessionTargets(registry, { ...robot, registered: false });
  assert.deepEqual(
    sims.map((t) => t.world),
    ["pick", "sort"],
  );
  assert.ok(sims.every((t) => t.mode === "sim" && !t.image));
  const all = sessionTargets(registry, robot);
  assert.equal(all[0].robotLabel, "Classroom arm");
  assert.equal(all[0].mode, "real");
  assert.equal(all[0].world, "");
  assert.equal(all[0].image, robot.image);
});
test("carousel wraps and keeps robot, environment and world in one selection", () => {
  const all = sessionTargets(registry, robot);
  const sim = adjacentTarget(all, all[0], 1);
  assert.equal(sim.world, "pick");
  assert.equal(selectedTarget(all, sim), sim);
  assert.equal(adjacentTarget(all, all[0], -1).world, "sort");
  assert.equal(adjacentTarget(all, all[2], 1).mode, "real");
});
test("removed scenes never resolve to a different active target silently", () => {
  const all = sessionTargets(registry, robot);
  const stale = { body: "arm", mode: "sim", world: "deleted" };
  assert.equal(selectedTarget(all, stale), undefined);
  assert.equal(adjacentTarget(all, stale, 1), all[0]);
  assert.equal(adjacentTarget([], stale, 1), undefined);
  assert.deepEqual(
    sessionTargets(
      {
        ...registry,
        bodies: [{ ...registry.bodies[0], buses: { sim: { enabled: false } } }],
      },
      robot,
    ),
    [],
  );
});
