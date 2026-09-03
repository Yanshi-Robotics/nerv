"""NERV/Tool — a tool node is an MCP server with tools only: no senses, no guidance.

Its tools are merged into the brain's tool sheet next to the body's verbs. They are not
gated by the session's arming switch (they move nothing) but they pass the gate and are
logged like everything else. Name collisions: the body wins, the tool is dropped with a
warning — a name must never have two destinations.
"""
from __future__ import annotations

HEALTH = "/health"
