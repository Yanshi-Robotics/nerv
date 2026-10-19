"""Replaceable, tool-free summarization using the current model provider."""
from __future__ import annotations

import json

from ..brain import prompts


class ModelCompactor:
    def summarize(self, chat, history, previous):
        plain = [{key: value for key, value in message.items() if key not in ("continuation", "seq")}
                 for message in history]
        reply = chat(prompts.COMPACTION_SYSTEM,
                     [{"role": "user", "text": json.dumps({"previous_summary": previous, "events": plain}, default=str)}], [], None)
        if reply.tool_calls or reply.finish_status != "completed":
            raise ValueError("summarizer did not finish a tool-free summary")
        return reply.text or "", reply.usage
