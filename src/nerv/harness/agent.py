"""Default agent harness. All side effects are requested through the host Link."""
from __future__ import annotations

from ..brain import prompts
from ..brain.react import ReactBrain
from ..nerve.brain import CallTool, Say, SetRegister, Think
from .context import ContextManager
from .compaction import ModelCompactor


class AgentHarness(ReactBrain):
    def __init__(self, llm, name: str, context: ContextManager, compactor=None):
        super().__init__(llm, name)
        self.context_manager = context
        self.compactor = compactor or ModelCompactor()

    def run_turn(self, link, user) -> None:
        from ..brain.react import _meta_tools
        def chat(*args):
            extra = {"cancelled": lambda: not link.is_current()} if getattr(self.llm, "cancellable", False) else {}
            return self.llm.chat(*args, **extra)
        while True:
            if not link.is_current():
                link.stop_reason()
                return
            obs = link.observe()
            images = obs.images if obs and self.vision else None
            node_tools = link.tools()
            tools = node_tools + _meta_tools({t.name for t in node_tools})
            memory = link.memory()

            def summarize(history, previous):
                text, usage = self.compactor.summarize(chat, history, previous)
                return text, self.model, usage

            system, history = self.context_manager.prepare(
                self._system(link), link.raw_history(), tools, images, memory, summarize,
                cancelled=lambda: not link.is_current())
            memory.flush()
            if not link.is_current():
                link.stop_reason()
                return
            memory.append("model_request_started", {"run_id": getattr(link, "run_id", ""), "model": self.model,
                                                     "budget_is_estimate": True})
            if not link.is_current():
                link.stop_reason()
                return
            reply = chat(system, history, tools, images)
            if not link.is_current():
                link.stop_reason()
                return
            link.model_reply(reply)
            if reply.finish_status != "completed":
                raise RuntimeError(f"model request ended with {reply.finish_status}")
            if not reply.tool_calls:
                # A reply cannot silently abandon a still-running robot skill.
                if link.executing():
                    link.think(Think(reply.text or "", []))
                    link.supervise_wait()
                    if link.stop_reason():
                        return
                    continue
                link.say(Say(reply.text or ""))
                return
            calls = [CallTool(tc.id, tc.name, tc.arguments) for tc in reply.tool_calls]
            link.think(Think(reply.text or "", calls))
            for call in calls:
                if not link.is_current():
                    return
                memory.flush()
                if call.name in prompts.META_TOOL_NAMES:
                    link.set_register(SetRegister(call.name, dict(call.arguments) | {"call_id": call.call_id}))
                else:
                    link.call_tool(call)
            if link.stop_reason():
                return
