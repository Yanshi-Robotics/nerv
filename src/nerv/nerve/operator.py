"""NERV/Operator — the operator-facing surface (HTTP + SSE), as names.

Event types on the chat stream, in the order a turn produces them:
  start · perception · thinking · tool_call · progress · tool_result · reply · done
`reply` may carry `stop_reason` (interrupt | steps | time) when the turn was cut short.
Session states: active · frozen · reconnect_required.
"""
from __future__ import annotations

EV_START = "start"
EV_PERCEPTION = "perception"
EV_THINKING = "thinking"
EV_TOOL_CALL = "tool_call"
EV_PROGRESS = "progress"
EV_TOOL_RESULT = "tool_result"
EV_REPLY = "reply"
EV_DONE = "done"

SESSION_ACTIVE = "active"
SESSION_FROZEN = "frozen"
SESSION_RECONNECT = "reconnect_required"
