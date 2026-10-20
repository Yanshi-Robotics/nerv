"""Registered Claude VLMs: native Messages streaming and signed block replay."""
from __future__ import annotations

import hashlib

from .base import LLMReply, ToolCall
from .claude import _messages, _tools, _usage


class AnthropicMessagesLLM:
    vision = True
    cancellable = True

    def __init__(self, spec, api_key, effort=None, client=None):
        self.spec, self.model, self.effort = spec, spec.model, effort
        self.context_window, self.max_output_tokens = spec.context_window, spec.max_output_tokens
        self.identity = f"{spec.signature()}:{effort}:" + hashlib.sha256(api_key.encode()).hexdigest()
        if client is None:
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key, base_url=spec.base_url,
                               timeout=spec.request_timeout_s, max_retries=0)
        self.client = client

    def inputs(self, history, images):
        messages = []
        for item in history:
            state = item.get("continuation", {})
            if item["role"] == "assistant" and state.get("identity") == self.identity:
                messages.append({"role": "assistant", "content": state["items"]})
            else:
                messages.extend(_messages([item], None))
        messages.extend(_messages([], images))
        # Parallel tool results must be a single user message, preserving order.
        merged = []
        for message in messages:
            if merged and merged[-1]["role"] == message["role"]:
                previous = merged[-1]["content"]
                content = message["content"]
                merged[-1]["content"] = (
                    previous if isinstance(previous, list) else [{"type": "text", "text": previous}]
                ) + (content if isinstance(content, list) else [{"type": "text", "text": content}])
            else:
                merged.append(message)
        return merged

    def chat(self, system, history, tools, images, *, cancelled=lambda: False):
        request = {"model": self.model, "system": system,
                   "messages": self.inputs(history, images), "max_tokens": self.max_output_tokens}
        if tools:
            request["tools"] = _tools(tools)
        if self.effort is not None:
            request["output_config"] = {"effort": self.effort}
        if cancelled():
            raise InterruptedError("model request cancelled before dispatch")
        with self.client.messages.stream(**request) as stream:
            for _ in stream:
                if cancelled():
                    raise InterruptedError("model request cancelled")
            response = stream.get_final_message()
        if cancelled():
            raise InterruptedError("model request cancelled")
        payload = response.model_dump(exclude_none=True)
        blocks = payload.get("content", [])
        text = "\n".join(block["text"] for block in blocks if block["type"] == "text")
        calls = [ToolCall(block["id"], block["name"], block["input"])
                 for block in blocks if block["type"] == "tool_use"]
        # A truncated tool call is never eligible for execution by the harness.
        completed = payload.get("stop_reason") in ("end_turn", "tool_use", "stop_sequence")
        return LLMReply(text, calls, _usage(response.usage),
                        {"identity": self.identity, "items": blocks},
                        "completed" if completed else "incomplete")
