"""Discover providers in this interpreter's environment and serve a selected one."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

from .contracts import ENTRY_POINT_GROUP, EnvironmentBinding, RobotProfile


def providers():
    entries = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    found = {}
    for entry in entries:
        if entry.name in found:
            raise ValueError(f"duplicate runtime provider {entry.name}")
        found[entry.name] = entry
    return found


def discover():
    result = []
    for entry in providers().values():
        provider = entry.load()()
        manifest = provider.describe()
        manifest.check_protocol()
        result.append(manifest.model_dump())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover")
    verify = sub.add_parser("check")
    verify.add_argument("--provider", required=True)
    verify.add_argument("--url")
    serve = sub.add_parser("serve")
    serve.add_argument("--provider", required=True)
    serve.add_argument("--configuration", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == "discover":
        print(json.dumps(discover()))
        return 0
    if args.command == "check":
        from .conformance import check
        print(json.dumps(check(args.provider, args.url)))
        return 0
    payload = json.loads(Path(args.configuration).read_text())
    profile = RobotProfile(**payload["profile"])
    binding = EnvironmentBinding(**payload["binding"])
    provider = providers()[args.provider].load()()
    manifest = provider.describe()
    manifest.check_protocol()
    if binding.provider != manifest.id:
        raise ValueError("binding does not match provider manifest")
    runtime = provider.create(profile, binding)
    from .control import StopFlag
    from .node import BodyNode, build_app
    import uvicorn
    node = BodyNode(runtime, guidance=payload.get("guidance", ""), stop=StopFlag(), armed=False,
                    world=binding.world, bus_kind=manifest.id,
                    stream_fps=payload["stream_fps"], require_control=True)
    node.manifest = manifest.model_dump()
    uvicorn.run(build_app(node, [payload["cors_origin"]]), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
