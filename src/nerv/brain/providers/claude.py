"""Claude via the Anthropic SDK."""
from __future__ import annotations

import base64
import os

from .. import prompts
from ...nerve.body import ToolSpec
from .base import MAX_TOKENS, LLMReply, ToolCall, norm_images


class ClaudeLLM:
    vision = True

    def __init__(self, model: str):
        import anthropic
        self.model = model
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY") or "EMPTY")

    def chat(self, system, history, tools, images) -> LLMReply:
        resp = self.client.messages.create(model=self.model, max_tokens=MAX_TOKENS, system=system,
                                           messages=_messages(history, images), tools=_tools(tools))
        text, calls = None, []
        for b in resp.content:
            if b.type == "text":
                text = b.text
            elif b.type == "tool_use":
                calls.append(ToolCall(b.id, b.name, dict(b.input)))
        return LLMReply(text=text, tool_calls=calls, usage=_usage(getattr(resp, "usage", None)))


def _usage(u):
    if u is None:
        return None
    inp = ((getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
           + (getattr(u, "cache_creation_input_tokens", 0) or 0))
    out = getattr(u, "output_tokens", 0) or 0
    return {"input": inp, "output": out, "total": inp + out}


def _tools(tools: list[ToolSpec]):
    return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]


def _messages(history, images):
    msgs: list[dict] = []
    for it in history:
        if it["role"] == "user":
            msgs.append({"role": "user", "content": it["text"]})
        elif it["role"] == "assistant":
            content: list = []
            if it.get("text"):
                content.append({"type": "text", "text": it["text"]})
            for tc in it.get("tool_calls", []):
                content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            msgs.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})
        elif it["role"] == "tool":
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": it["id"],
                                                      "content": it["content"]}]})
    imgs = norm_images(images)
    if imgs:
        content = [{"type": "text", "text": prompts.IMAGE_FRAMING}]
        for name, png in imgs:
            if name:
                content.append({"type": "text", "text": prompts.IMAGE_CAMERA_LABEL.format(name=name)})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                        "data": base64.b64encode(png).decode()}})
        content.append({"type": "text", "text": prompts.IMAGE_NO_ACTION_REMINDER})
        msgs.append({"role": "user", "content": content})
    return msgs
