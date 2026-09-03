# From ANIMA Zero to NERV

ANIMA Zero (2026-06 → 2026-08) was the brain of an embodied robot: a ReAct loop over a world
reached through AWI, an MCP-based interface with four channels (tools, observation, guidance,
config). Its worlds were whole programs — a chess set, a webcam, an apartment with a walking
robot — and each bundled the scene, the robot and its gait policy in one process.

NERV keeps what Zero got right and changes one address:

| Kept | Changed |
|---|---|
| the four channels (now NERV/Body), the trust store, the fence, the liveness watchdog, the session registers, the stop gates, the one-log discipline | actions go to the body, not the world; the body and the world are two processes joined by a motor bus |
| MCP over Streamable HTTP, stateless, SSE responses | the brain is a plugin behind a message protocol, not the platform itself |
| the web app's shape | a session is brain × body × world; sessions arm; sensor streams; a tool node |

Lessons carried over verbatim: the orchestrator holds no task knowledge; the world reports what
happened, not what was asked; a placeholder is declared; whole sets are appended to; tests that
assert wording rather than facts go red for the wrong reasons; a green check that guards nothing
is worse than none.
