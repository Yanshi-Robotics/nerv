"""See → think → gate → act. The whole reasoning loop, as a plain loop.

The brain sees only what the Link hands it, acts only through the Link, and ends the turn
by answering in words. The three stops (interrupt, step ceiling, wall clock) are the
platform's; the brain asks `link.stop_reason()` after every action and yields when told.
"""
from __future__ import annotations

import logging

from ..nerve.body import KIND_READ, ToolSpec
from ..nerve.brain import CallTool, Link, Say, SetRegister, Think, UserMessage
from . import prompts
from .providers.base import LLM

_log = logging.getLogger(__name__)


def _meta_tools(taken: set[str]) -> list[ToolSpec]:
    out = []
    for spec in (prompts.CORE_TASK_SET_TOOL, prompts.CORE_TASK_CLEAR_TOOL,
                 prompts.NOTE_ADD_TOOL, prompts.NOTE_DROP_TOOL):
        if spec["name"] in taken:
            _log.warning("meta tool shadowed by a node tool, yielding: %s", spec["name"])
            continue
        out.append(ToolSpec(spec["name"], spec["description"], spec["parameters"], KIND_READ))
    return out


class ReactBrain:
    """A NERV/Brain plugin wrapping one model provider."""

    def __init__(self, llm: LLM, name: str = "") -> None:
        self.llm = llm
        self.name = name or getattr(llm, "model", "brain")
        self.model = getattr(llm, "model", self.name)
        self.vision = bool(getattr(llm, "vision", False))

    # -- system prompt ------------------------------------------------------------------
    def _system(self, link: Link) -> str:
        ctx = link.system_context()
        s = prompts.system_prompt()
        body = ctx.get("body")
        if not body:
            s += prompts.NO_BODY_BLOCK
        else:
            if ctx.get("has_tools", True):
                s += prompts.BODY_ATTACHED_BLOCK.format(body=body, family=ctx.get("family") or "?",
                                                        world=ctx.get("world") or "?")
            else:
                s += prompts.BODY_ATTACHED_NO_TOOLS_BLOCK.format(body=body)
            for what, fenced in ctx.get("guidance_blocks", []):
                s += prompts.NODE_GUIDANCE_BLOCK.format(what=what, fenced=fenced)
            items = ctx.get("config_lines") or []
            if items:
                s += prompts.BODY_CONFIG_BLOCK.format(items="\n".join(items))
            names = ctx.get("sensor_names") or []
            if names:
                s += prompts.SENSORS_BLOCK.format(names=", ".join(names))
            s += prompts.ARMED_BLOCK if ctx.get("armed") else prompts.DISARMED_BLOCK
        regs = link.registers()
        if regs.get("core_task"):
            s += prompts.CORE_TASK_BLOCK.format(task=regs["core_task"])
        if regs.get("notes"):
            listed = "\n".join(f"{i}. {n}" for i, n in enumerate(regs["notes"], 1))
            s += prompts.NOTES_BLOCK.format(notes=listed)
        return s

    # -- the loop -------------------------------------------------------------------------
    def run_turn(self, link: Link, user: UserMessage) -> None:
        while True:
            # SEE
            obs = link.observe()
            images = obs.images if (obs and self.vision) else None
            # THINK
            node_tools = link.tools()
            tools = node_tools + _meta_tools({t.name for t in node_tools})
            reply = self.llm.chat(self._system(link), link.history(), tools, images)
            if not reply.tool_calls:
                link.say(Say(reply.text or ""))
                return
            calls = [CallTool(tc.id, tc.name, tc.arguments) for tc in reply.tool_calls]
            link.think(Think(reply.text or "", calls))
            # GATE + ACT happen on the platform side of call_tool
            for c in calls:
                if c.name in prompts.META_TOOL_NAMES:
                    link.set_register(SetRegister(c.name, dict(c.arguments) | {"call_id": c.call_id}))
                else:
                    link.call_tool(c)
            if link.stop_reason():
                return
