"""OpenAI, Ollama and any OpenAI-compatible gateway: swap base_url."""
from __future__ import annotations

import base64
import json

from .. import prompts
from ...nerve.body import ToolSpec
from .base import LLMReply, ToolCall, norm_images


class OpenAICompatLLM:
    def __init__(self, model: str, base_url: str | None, api_key: str, vision: bool = True):
        from openai import OpenAI
        self.model = model
        self.vision = vision
        self.client = OpenAI(base_url=base_url, api_key=api_key or "EMPTY")

    def chat(self, system, history, tools, images) -> LLMReply:
        # No max_tokens on this path: new OpenAI models reject it and Ollama does not need it.
        resp = self.client.chat.completions.create(
            model=self.model, messages=_messages(system, history, images),
            tools=_tools(tools) or None, tool_choice="auto" if tools else None)
        msg = resp.choices[0].message
        calls = [ToolCall(c.id, c.function.name, json.loads(c.function.arguments or "{}"))
                 for c in (msg.tool_calls or [])]
        return LLMReply(text=msg.content, tool_calls=calls, usage=_usage(getattr(resp, "usage", None)))


def _usage(u):
    if u is None:
        return None
    inp = getattr(u, "prompt_tokens", 0) or 0
    out = getattr(u, "completion_tokens", 0) or 0
    return {"input": inp, "output": out, "total": getattr(u, "total_tokens", inp + out) or (inp + out)}


def _tools(tools: list[ToolSpec]):
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.parameters}} for t in tools]


def _messages(system, history, images):
    msgs: list[dict] = [{"role": "system", "content": system}]
    for it in history:
        if it["role"] == "user":
            msgs.append({"role": "user", "content": it["text"]})
        elif it["role"] == "assistant":
            m: dict = {"role": "assistant", "content": it.get("text") or ""}
            if it.get("tool_calls"):
                m["tool_calls"] = [{"id": tc.id, "type": "function",
                                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                                   for tc in it["tool_calls"]]
            msgs.append(m)
        elif it["role"] == "tool":
            msgs.append({"role": "tool", "tool_call_id": it["id"], "content": it["content"]})
    imgs = norm_images(images)
    if imgs:
        content: list = [{"type": "text", "text": prompts.IMAGE_FRAMING}]
        for name, png in imgs:
            if name:
                content.append({"type": "text", "text": prompts.IMAGE_CAMERA_LABEL.format(name=name)})
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(png).decode()}})
        content.append({"type": "text", "text": prompts.IMAGE_NO_ACTION_REMINDER})
        msgs.append({"role": "user", "content": content})
    return msgs
