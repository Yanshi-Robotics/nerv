"""The nerv command."""
from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv

from .. import __version__, config, paths


def _hub():
    from .hub import Nerv
    return Nerv()


def cmd_registry(a) -> int:
    from .registry import Registry
    from ..brain.providers import list_brains
    reg = Registry()
    print("brains:")
    for b in list_brains():
        print(f"  {b['name']:<12} {b['label']:<44} {'ready' if b['available'] else 'not configured'}")
    print("bodies:")
    for b in reg.bodies.values():
        print(f"  {b.name:<24} family={b.family:<9} buses={','.join(b.buses)}  skills={','.join(b.skills) or '-'}")
    print("worlds:")
    for w in reg.worlds.values():
        print(f"  {w.name:<24} kind={w.kind:<5} supports={','.join(w.supports) or '-'}  ambient={','.join(s.name for s in w.ambient) or '-'}")
    print("tools:")
    for t in reg.tools.values():
        print(f"  {t.name:<24} {t.module or t.url}")
    print("compatibility:")
    from .registry.compat import matrix
    for row in matrix(reg):
        print(f"  {row['world']:<12} × {row['body']:<24} {'ok' if row['ok'] else 'refused: ' + row['reason']}")
    return 0


def cmd_doctor(a) -> int:
    from .registry import Registry
    from ..brain.providers import list_brains
    print(f"nerv {__version__}")
    print(f"data root      {paths.DATA_ROOT}")
    print(f".env           {paths.ENV_FILE} {'(present)' if os.path.isfile(paths.ENV_FILE) else '(missing)'}")
    print(f"node ports     {config.NODE_PORTS}")
    print("Runtime dependencies are checked in their selected interpreter with nerv-runtime check.")
    for b in list_brains():
        print(f"brain {b['name']:<12} {'ready' if b['available'] else 'not configured'}")
    reg = Registry()
    for w in reg.worlds.values():
        for body, arena in w.supports.items():
            p = os.path.join(w.assets_root, arena) if w.assets_root else arena
            print(f"arena {w.name}×{body}: {p} {'ok' if os.path.isfile(p) else '⚠ missing'}")
    for b in reg.bodies.values():
        for sk, d in b.skills.items():
            print(f"policy {b.name}.{sk}: {d.manifest or d.policy} {'present' if os.path.exists(d.manifest or d.policy) else 'missing'}")
    return 0


def _review_trust(hub, s: dict) -> None:
    """A node's text reaches the brain only after the operator approved its manifest."""
    from . import trust
    keys = ([f"body:{s['body']}"] if s.get("body") else []) + [f"tool:{t}" for t in s.get("tools", [])]
    for key in keys:
        c = hub.node_client(key)
        if c is None:
            continue
        d = c.trust_decision()
        if d.allowed:
            continue
        print(f"\n[{key}] {d.reason}")
        if d.changes:
            for line in d.changes:
                print(f"   · {line}")
        m = d.manifest
        print(f"   url: {m['url']}")
        for t in m["tools"]:
            print(f"   tool {t['name']} ({t['kind']}): {t['description'][:140]}")
        if m["guidance"]:
            print("   guidance:\n     " + m["guidance"][:1200].replace("\n", "\n     "))
        try:
            ans = input("   approve this manifest? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans == "y":
            print(f"   approved ({c.approve()[:12]}…)")
        else:
            print(f"   not approved — {key} contributes no tools or guidance; set {trust.TRUST_ALL_ENV}=1 for development")


def _new_session(hub, a) -> str:
    from .session import require_scene
    require_scene(a.body, a.world, a.mode)
    from ..vlm.store import ModelRegistry
    selected = ModelRegistry().selection() or config.DEFAULT_BRAIN
    s = hub.new_session(a.brain or selected, a.body, a.world, a.sensors or [], None, a.mode)
    print(f"session {s['id']}  brain={s['brain']} body={s['body']} world={s['world']} armed={s['armed']}")
    _review_trust(hub, s)
    return s["id"]


def _print_events(evs) -> None:
    for ev in evs:
        t = ev.get("type")
        if t == "thinking":
            print(f"  ~ {ev['text']}")
        elif t == "tool_call":
            print(f"  → {ev['name']}({ev['args']})")
        elif t == "progress":
            print(f"    … {ev.get('message','')} {ev.get('progress','')}")
        elif t == "tool_result":
            print(f"  ← {'ok' if ev['ok'] else 'FAIL'}: {ev['message']}")
        elif t == "gate" and not ev.get("allowed"):
            print(f"  ⛔ gate: {ev['reason']}")
        elif t == "reply":
            print(f"\n{ev['text']}\n")


def cmd_chat(a) -> int:
    hub = _hub()
    try:
        sid = _new_session(hub, a)
        if a.arm:
            print(hub.arm(sid, True))
        print("type a message; 'arm' / 'disarm' / 'stop' / 'quit' are commands.")
        while True:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text == "quit":
                break
            if text in ("arm", "disarm"):
                print(hub.arm(sid, text == "arm"))
                continue
            if text == "stop":
                hub.stop(sid)
                continue
            _print_events(hub.handle_stream(sid, text))
    finally:
        hub.shutdown()
    return 0


def cmd_run(a) -> int:
    hub = _hub()
    try:
        sid = _new_session(hub, a)
        if a.arm:
            hub.arm(sid, True)
        _print_events(hub.handle_stream(sid, a.say))
    finally:
        hub.shutdown()
    return 0


def cmd_serve(a) -> int:
    import uvicorn
    from .serving import BrowserServer
    def request_shutdown():
        from .server import request_shutdown as begin
        begin()
    server = BrowserServer(uvicorn.Config(
        "nerv.platform.server:app", host=a.host or config.SERVE_HOST,
        port=a.port or config.SERVE_PORT, log_level="info",
        timeout_graceful_shutdown=config.SERVE_SHUTDOWN_TIMEOUT_S),
        open_browser=not a.no_browser, before_shutdown=request_shutdown)
    from filelock import FileLock
    from pathlib import Path
    Path(paths.NERV_HOME).mkdir(parents=True, exist_ok=True)
    with FileLock(str(Path(paths.NERV_HOME) / "host-running.lock"), timeout=0):
        if (Path(paths.NERV_HOME) / "update-pending.json").exists():
            raise ValueError("An update was interrupted. Run nerv rollback before starting NERV.")
        server.run()
    return 0


def cmd_node(a) -> int:
    rest = a.rest
    if a.node == "world":
        from ..world.__main__ import main as m
        return m(rest)
    if a.node == "body":
        from ..body.__main__ import main as m
        return m(rest)
    if a.node == "tool":
        from ..tool.__main__ import main as m
        return m(rest)
    print("node must be world | body | tool", file=sys.stderr)
    return 2


def cmd_conformance(a) -> int:
    from ..nerve.conformance import run
    rep = run(a.url, kind=a.kind)
    print(rep.render())
    return 0 if rep.ok else 1


def cmd_session(a) -> int:
    hub = _hub()
    if a.action == "list":
        for s in hub.store.list():
            print(json.dumps(s, ensure_ascii=False))
        return 0
    if a.action == "new":
        try:
            _new_session(hub, a)
        except Exception as e:
            print(f"refused: {e}")
            return 1
        finally:
            hub.shutdown()
        return 0
    return 2


def cmd_policy(a):
    from .runtime_management import manage
    payload = {"id": a.id} if a.action in ("inspect", "check") else {}
    if a.action == "import":
        payload = {"id": a.id, "path": a.path, "conditions": a.conditions,
                   "standard_declared": a.standard_declared}
    if a.action == "check":
        payload["device"] = a.device
    if a.action == "configure":
        payload = {key: getattr(a, key) for key in ("port", "id", "calibration_dir", "camera_config_file", "policy_device")}
    try:
        print(json.dumps(manage(a.python, a.provider, a.action, payload), indent=2))
        return 0
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


def cmd_install_system(args):
    from .. import installation, install_system
    from .module_jobs import system_description
    values = {key: getattr(args, key) for key in ('target', 'compute') if getattr(args, key) is not None}
    if values:
        from filelock import FileLock
        with FileLock(installation.home() / 'host-running.lock', timeout=0), FileLock(installation.home() / 'installation-process.lock', timeout=0):
            install_system.save_settings(installation.home(), values)
    print(json.dumps(system_description(True), indent=2))
    return 0


def cmd_install_runtime(args):
    from . import module_jobs
    from .. import installation, install_plan
    action = args.action
    if action == 'list':
        result = module_jobs.catalog()
    elif action == 'plan':
        manifest, _ = installation.read_manifest()
        result = install_plan.describe(installation.home(), manifest, args.provider)
    elif action == 'install':
        result = module_jobs.start(args.provider)
    else:
        result = getattr(module_jobs, action)(args.provider)
    print(json.dumps(result, indent=2))
    if action in ('install', 'resume'):
        import time
        try:
            previous = None
            while True:
                job = next(j for j in module_jobs.catalog()['jobs'] if j['id'] == result['id'])
                status = (job['state'], job['phase'], job.get('downloaded'), job.get('download_total'))
                if status != previous:
                    print(json.dumps(dict(zip(('state', 'phase', 'downloaded', 'total'), status))), flush=True)
                    previous = status
                if job['state'] not in ('queued', 'running'):
                    return 0 if job['state'] == 'complete' else 1
                time.sleep(1)
        except KeyboardInterrupt:
            module_jobs.pause(result['id'])
            module_jobs.shutdown()
            return 130
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(paths.ENV_FILE)
    ap = argparse.ArgumentParser(prog="nerv", description="NERV — a nervous system for robots")
    ap.add_argument("--version", action="version", version=f"nerv {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _sess_args(p):
        p.add_argument("--brain", default="")
        p.add_argument("--body", default=None)
        p.add_argument("--mode", choices=["sim", "real"], default="sim")
        p.add_argument("--world", default=None)
        p.add_argument("--sensors", nargs="*", default=[], help="ambient streams to include")
        p.add_argument("--arm", action="store_true", help="arm the session at start (sim only!)")

    from .. import installation
    p = sub.add_parser("update", help="check or install the next official release after exiting NERV")
    p.add_argument("--check", action="store_true")
    p.add_argument("--manifest", help="use an extracted release bundle manifest")
    p.set_defaults(fn=lambda a: print(json.dumps(installation.update(a.check, manifest=a.manifest), indent=2)) or 0)
    p = sub.add_parser("rollback", help="restore the previous working installation")
    p.set_defaults(fn=lambda a: print(json.dumps(installation.rollback(), indent=2)) or 0)
    p = sub.add_parser('system', help='inspect or select the installation platform')
    p.add_argument('--target', choices=['auto', *installation.install_system.TARGETS])
    p.add_argument('--compute', choices=['auto', 'cpu', 'cu130'])
    p.set_defaults(fn=cmd_install_system)
    p = sub.add_parser("runtime", help="install an official isolated module")
    p.add_argument("action", choices=["install", "list", "plan", "pause", "resume", "cancel"])
    p.add_argument("provider", nargs="?")
    p.set_defaults(fn=cmd_install_runtime)

    p = sub.add_parser("policy", help="manage a provider's local device and policy configuration")
    p.add_argument("action", choices=["describe", "configure", "import", "inspect", "check"])
    p.add_argument("--python", required=True)
    p.add_argument("--provider", required=True)
    for field in ("id", "path", "conditions", "port", "calibration-dir", "camera-config-file"):
        p.add_argument("--" + field, default="")
    p.add_argument("--device", default="cpu")
    p.add_argument("--policy-device", default="cpu")
    p.add_argument("--standard-declared", action="store_true")
    p.set_defaults(fn=cmd_policy)

    p = sub.add_parser("chat", help="a conversation in the terminal")

    _sess_args(p)

    p.set_defaults(fn=cmd_chat)
    p = sub.add_parser("run", help="one turn, scripted")
    _sess_args(p)
    p.add_argument("--say", required=True)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("serve", help="the NERV/Operator API")
    p.add_argument("--host", default="")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-browser", action="store_true", help="serve without opening a browser")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("node", help="start a node by hand: node world|body|tool -- <args>")
    p.add_argument("node")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_node)
    p = sub.add_parser("registry", help="brains, bodies, worlds, tools, compatibility")
    p.set_defaults(fn=cmd_registry)
    p = sub.add_parser("conformance", help="check a body or tool node against its interface")
    p.add_argument("url")
    p.add_argument("--kind", default="body", choices=["body", "tool", "world"])
    p.set_defaults(fn=cmd_conformance)
    p = sub.add_parser("doctor", help="what is configured and what is reachable")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("session", help="session new|list")
    p.add_argument("action", choices=["new", "list"])
    _sess_args(p)
    p.set_defaults(fn=cmd_session)
    arguments = sys.argv[1:] if argv is None else argv
    a = ap.parse_args(arguments or ["serve"])
    if a.cmd == "node" and a.rest[:1] == ["--"]:
        a.rest = a.rest[1:]
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
