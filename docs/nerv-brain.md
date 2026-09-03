# NERV/Brain

The messages between the platform and a brain plugin. A brain never touches a body, a world
or a tool; it talks to NERV through a `Link` and NERV does the rest. Messages are dataclasses
in `src/nerv/nerve/brain.py`, plain data so the transport can change (in-process today, a
socket later) without the protocol changing.

## Messages

Platform → brain:

| Message | Fields | Meaning |
|---|---|---|
| `UserMessage` | `text` | what the person said; one per turn |
| `ToolResult` | `call_id, name, ok, message, data` | what a tool call came back with (measured, not requested) |
| `Stop` | `reason` | the platform is ending the turn (interrupt · steps · time) |

Brain → platform:

| Message | Fields | Meaning |
|---|---|---|
| `Think` | `text, tool_calls:[CallTool]` | the decision for one step, sent before the calls |
| `CallTool` | `call_id, name, arguments` | call a body verb or a tool function |
| `SetRegister` | `kind, value` | write the brain's own working memory (`set_core_task`, `clear_core_task`, `add_note`, `drop_note`) |
| `Say` | `text` | answer the person; ends the turn |
| `EndTurn` | — | (implied by `Say`) |

## The Link

| Method | Returns |
|---|---|
| `tools()` | the tool sheet: body verbs (primitives, skills, reads) and tool-node functions, descriptions clipped |
| `system_context()` | `body, family, world, has_tools, guidance_blocks (fenced), config_lines, sensor_names, armed` |
| `history()` | the token-budgeted history with every tool call paired to a result |
| `registers()` | `{core_task, notes}` |
| `observe()` | one `Observation` (state + named images), recorded and emitted; counts a step |
| `think(Think)` | records the step |
| `call_tool(CallTool)` | gate → route → run (with progress and stop) → record → `ToolResult` |
| `set_register(SetRegister)` | updates the registers → `ToolResult` |
| `say(Say)` | records and emits the reply |
| `stop_reason()` | `None` or the reason; when set, the platform has already replied for you |

## The reference brain

`src/nerv/brain/react.py` is see → think → gate → act as a plain loop: observe; one model call
with the system prompt (generic text + fenced node guidance + the registers), the history, the
tool sheet and the images; if the model answered in words, say it and stop; otherwise send
`Think` and the calls, then check `stop_reason()`. The gate runs inside `call_tool`; the brain
does not see it as a step.

Providers behind the loop: Claude (Anthropic SDK), OpenAI-compatible (OpenAI, Ollama, any
gateway), mock (no key: calls the first read-only tool and answers). The registry is one table
in `brain/providers/factory.py`; model ids come from `config.py`.

Everything the model reads is English and lives in `brain/prompts.py`. It is told to answer in
the person's language.
