# NERV/Tool

A tool node is an MCP server with tools only: no senses, no guidance, nothing physical. Its
functions are merged into the brain's tool sheet next to the body's verbs; name collisions are
resolved in the body's favour and logged.

| Channel | Where | Content |
|---|---|---|
| Tools | MCP `tools/list`, `tools/call` at `/mcp/` | object schemas; `readOnlyHint: true`; `isError` on failure |
| Health | `GET /health` | `{ok, node:"tool", tool, version, tools}` |

Tools are not subject to the session's arming switch — they move nothing — but they pass the
gate's block/approval rules and every call is logged. A tool's manifest is approved like a
body's.

## Calculator

`calc(expression)` evaluates `+ - * / // % **`, parentheses, `sqrt abs round floor ceil sin cos
tan log log10 exp min max pow`, and `pi`, `e`. It is an AST walk over a whitelist, never
`eval`; division by zero, overflow and anything that is not arithmetic are refused with the
reason. Launch by hand: `nerv node tool -- --tool calculator --port P`. Registry:
`tools/calculator/tool.yaml` (`module: nerv.tool.calculator`).

Adding a tool: a Python module with `TOOLS` (list of `{name, description, parameters}`) and
`call(name, args) -> {ok, message, data}`, plus a `tools/<name>/tool.yaml` naming the module.
Anything else that speaks this page — a planner, a knowledge base, a ROS service behind a
bridge — joins the same way.
