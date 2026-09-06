# AGENTS.md · NERV

Machine-facing entry point. Humans: read `README.md`. Rules that apply everywhere in this
repository, then where each fact lives.

## The layering rule

| Layer | Directory | May contain task/robot knowledge? |
|---|---|---|
| Interfaces | `src/nerv/nerve/` | No. Types and message shapes only. |
| Platform | `src/nerv/platform/` | No. Registry, sessions, gate, launcher, router, HTTP, CLI. |
| Brain plugin | `src/nerv/brain/` | No. The loop and the model providers. Prompts are generic. |
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
| What happened | `logs/sessions/session-<id>.jsonl`, `memory/sessions/<id>.json` |

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
