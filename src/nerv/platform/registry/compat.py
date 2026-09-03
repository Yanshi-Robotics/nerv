"""Can this body stand in this world? A pure function over the registry — testable, and the
same answer the UI greys out with."""
from __future__ import annotations

from .schema import KIND_SIM, BodySpec, WorldSpec


def check(world: WorldSpec, body: BodySpec) -> tuple[bool, str]:
    if body.name not in world.supports:
        return False, (f"world `{world.name}` has no arena for body `{body.name}` "
                       f"(supports: {', '.join(world.supports) or 'nothing'})")
    if world.kind not in body.buses:
        return False, (f"body `{body.name}` has no bus endpoint for a {world.kind} world "
                       f"(has: {', '.join(body.buses) or 'none'})")
    if world.kind == KIND_SIM and not (world.supports[body.name] or "").strip():
        return False, f"world `{world.name}` lists `{body.name}` but names no arena file"
    return True, ""


def matrix(reg) -> list[dict]:
    out = []
    for w in reg.worlds.values():
        for b in reg.bodies.values():
            ok, reason = check(w, b)
            out.append({"world": w.name, "body": b.name, "ok": ok, "reason": reason})
    return out
