# AGENTS.md · NERV

Machine-facing entry point. Humans: read `README.md`. Rules that apply everywhere in this
repository, then where each fact lives.

## The layering rule

| Layer | Directory | May contain task/robot knowledge? |
|---|---|---|
| Interfaces | `src/nerv/nerve/` | No. Types and message shapes only. |
| Platform | `src/nerv/platform/` | No. Registry, sessions, gate, launcher, router, HTTP, CLI. |
| Brain plugin | `src/nerv/brain/` | No. Provider composition and generic prompts. |
| Agent harness | `src/nerv/harness/` | No. Loop, context, compaction and session memory interfaces. |
| Runtime SDK | `packages/nerv-runtime-sdk/` | No. Protocol, discovery, server and dispatch lifecycle. |
| Real adapter | `packages/nerv-runtime-lerobot/` | Yes. Native LeRobot device and ACT implementation. |
| Body node | `src/nerv/body/` | Families may (`families/`): verbs, closed loops, sensor summaries. Buses are endpoints. |
| World node | `src/nerv/world/` | Physics, motor firmware, cameras, rays. Never a brain concern. |
| Tool node | `src/nerv/tool/` | Whatever the tool computes. |
| Registry | `bodies/ tools/` in this repo; `worlds/` is the nerv-world submodule | Data: one YAML + guidance.md per entry. |

Test for placement: *would this code still make sense against a different body, world or brain?*
If not, it belongs in a family, a node or a registry entry — never in the platform or the brain.

## Where each fact lives

| Fact | Lives in |
|---|---|
| What a body can do (verbs, kinds) | its family module + `bodies/<name>/body.yaml` |
| Which policy drives a skill | `bodies/<name>/body.yaml` `skills.<verb>.policy` → `policies/<name>/` (submodule `nerv-policies`; relative paths resolve against the repo root) |
| Which bodies a world can host | `worlds/<name>/world.yaml` `supports` — the descriptor lives in the `worlds/` submodule (`nerv-world`) next to the scenes |
| Where scenes live | `worlds/` (submodule). It ships no policies. |
| Every tunable of the platform | `src/nerv/config.py` (env `NERV_*`, `.env`) |
| Tunables of a node | its YAML entry and its command-line args; nodes never import `config.py` |
| Which nodes the operator approved | `~/.nerv/trust.json` (outside the repo) |
| What happened | `memory/sessions/<id>/events.jsonl` (authoritative), `<id>.json` (snapshot), image assets and diagnostic logs |

## Commands

```
.venv/bin/nerv registry | doctor | chat | run | serve | node | conformance | session
.venv/bin/python -m pytest tests -q                # everything, MuJoCo included (one venv: pip install -e '.[all,dev]')
.venv/bin/python -m ruff check src tests
```

## Red lines

- The brain sees sensors, never ground truth. `/status` and the chase camera are for people.
- Every body action passes the safety gate; a node's self-declared `kind` is an input, not a bypass.
- A session starts disarmed. Arming is an operator action (HTTP or CLI), never a tool.
- Nothing hardcoded that should be computed, configured or discovered; placeholders are declared.
- Whole sets are appended to, never replaced (registry entries, README tables, `.env.example`).
- Text a model reads is English and lives in `brain/prompts.py`; text a person reads is localised.
- Real hardware commands are run by a person. Unverified bus endpoints stay `verified: false`.


## Modular runtime and harness (0.6)

The current checkout is the top-level `NERV/` project. One instance binds one robot/environment;
one active session controls it. `src/nerv/harness/` owns the generic loop, budgeting and session
journal interfaces. `packages/nerv-runtime-sdk/` owns the independent protocol/worker;
`packages/nerv-runtime-lerobot/` owns the real-device adapter. Vendor dependencies never enter
host code. New hardware uses a provider entry point or compatible service URL, a profile and
an enabled environment binding; the family identifier is open.

Production model options come from `src/nerv/vlm/catalog.json` plus the private registry.
Only documented image-capable models can register. Image/tool/continuation checks and physical
acceptance are independent and configuration-specific. Explicit Mock fixtures and legacy
providers do not belong in the production catalogue. Preserve raw events and legacy migration
snapshots. A restart never replays motion; releasing emergency hold disarms and cancels the old task.

Real preparation can energize motors. Only the operator may trigger it or physical testing.
Local tests, tensor forward passes, API validation and real task evidence are separate gates.
Use 0.5.0 as the audited baseline, 0.6.x for development, and 1.0.0 only after the 10-trial and
fault-test acceptance in `docs/acceptance.md`. Update both README languages and matching interface
contracts with runtime or lifecycle changes. Install local packages before testing:

```
.venv/bin/python -m pip install -e ./packages/nerv-runtime-sdk -e '.[platform,sim,dev]'
.venv/bin/python -m pip install --no-deps -e ./packages/nerv-runtime-lerobot
.venv/bin/python -m ruff check src packages tests
```
