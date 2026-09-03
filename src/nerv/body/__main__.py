"""Run a body node:  python -m nerv.body --body B --world W --bus tcp://… --port P [--kind sim|real]"""
from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from ..platform.registry import Registry
from ..platform.registry.schema import KIND_SIM
from . import families
from .node import BodyNode, build_app
from .skills import SkillRunner, StopFlag


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nerv body node")
    ap.add_argument("--body", required=True)
    ap.add_argument("--world", default="")
    ap.add_argument("--kind", default="", help="sim | real (default: from the world)")
    ap.add_argument("--bus", default="", help="bus URL for a zmq endpoint (tcp://127.0.0.1:PORT)")
    ap.add_argument("--host", default=os.environ.get("NERV_NODE_BIND_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--cors", default=os.environ.get("NERV_CORS_ORIGINS", "http://localhost:8100"))
    a = ap.parse_args(argv)

    reg = Registry()
    body = reg.body(a.body)
    kind = a.kind or (reg.world(a.world).kind if a.world else KIND_SIM)
    if kind not in body.buses:
        print(f"body {body.name} has no {kind} bus endpoint", file=sys.stderr)
        return 2
    ep = body.buses[kind]
    if not ep.verified and os.environ.get("NERV_ALLOW_UNVERIFIED", "") not in ("1", "true"):
        print(f"bus endpoint {ep.kind} for {body.name} is marked unverified; set "
              f"NERV_ALLOW_UNVERIFIED=1 to run it anyway (it starts disarmed)", file=sys.stderr)
        return 3
    if ep.kind == "zmq":
        if not a.bus:
            print("--bus is required for a zmq endpoint", file=sys.stderr)
            return 2
        from .bus_zmq import ZmqBus
        bus = ZmqBus(a.bus)
    elif ep.kind == "lerobot":
        from .bus_lerobot import LerobotBus
        bus = LerobotBus(ep.settings)
    elif ep.kind == "unitree":
        from .bus_unitree import UnitreeBus
        bus = UnitreeBus(ep.settings)
    else:
        print(f"unknown bus kind {ep.kind!r}", file=sys.stderr)
        return 2

    stop = StopFlag()
    runner = SkillRunner(stop)
    impl = families.load(body.family).build(body.model_dump(), bus, runner)
    node = BodyNode(impl, guidance=body.guidance, stop=stop, armed=(kind == KIND_SIM),
                    world=a.world, bus_kind=ep.kind)
    app = build_app(node, [o.strip() for o in a.cors.split(",") if o.strip()])
    print(f"[nerv body] {body.name} family={body.family} kind={kind} bus={ep.kind} "
          f"armed={node.armed} http://{a.host}:{a.port}", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
