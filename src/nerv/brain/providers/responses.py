"""Stateless Responses adapters with provider-specific reasoning continuation."""
from __future__ import annotations

import base64
import hashlib
import json

from .base import LLMReply, ToolCall, norm_images


class ResponsesLLM:
    vision = True
    cancellable = True

    def __init__(self, spec, api_key, effort=None, client=None):
        self.spec, self.model, self.effort = spec, spec.model, effort
        self.context_window = spec.context_window
        self.max_output_tokens = spec.max_output_tokens
        self.identity = f"{spec.signature()}:{effort}:" + hashlib.sha256(api_key.encode()).hexdigest()
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=spec.base_url,
                            timeout=spec.request_timeout_s, max_retries=0)
        self.client = client

    def inputs(self, system, history, images):
        result = [{"role": "developer", "content": system}]
        for message in history:
            role = message["role"]
            if role == "user":
                result.append({"role": "user", "content": message["text"]})
            elif role == "assistant":
                continuation = message.get("continuation", {})
                if continuation.get("identity") == self.identity:
                    result.extend(continuation["items"])
                else:
                    if message.get("text"):
                        result.append({"role": "assistant", "content": message["text"]})
                    for call in message.get("tool_calls", []):
                        result.append({"type": "function_call", "call_id": call.id,
                                       "name": call.name, "arguments": json.dumps(call.arguments)})
            elif role == "tool":
                result.append({"type": "function_call_output", "call_id": message["id"],
                               "output": message["content"]})
        if images:
            content = []
            for name, png in norm_images(images):
                content += [{"type": "input_text", "text": f"Current camera: {name}"},
                            {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png).decode()}]
            result.append({"role": "user", "content": content})
        return result

    def chat(self, system, history, tools, images, *, cancelled=lambda: False):
        request = {"model": self.model, "input": self.inputs(system, history, images),
                   "max_output_tokens": self.spec.max_output_tokens, "stream": True}
        if self.spec.protocol == "responses":
            request.update(store=False, include=["reasoning.encrypted_content"], parallel_tool_calls=False)
        elif self.spec.protocol == "xai_responses":
            request.update(store=False, include=["reasoning.encrypted_content"], parallel_tool_calls=False)
        if self.effort is not None:
            request["reasoning"] = {"effort": self.effort}
        if tools:
            request["tools"] = [{"type": "function", "name": tool.name, "description": tool.description,
                                 "parameters": tool.parameters, "strict": False} for tool in tools]
        if cancelled():
            raise InterruptedError("model request cancelled before dispatch")
        stream = self.client.responses.create(**request)
        response = None
        try:
            for event in stream:
                if cancelled():
                    raise InterruptedError("model request cancelled")
                if event.type in ("response.completed", "response.incomplete", "response.failed"):
                    response = event.response
                if event.type == "error":
                    raise RuntimeError("provider returned a streaming error")
        finally:
            stream.close()
        if cancelled():
            raise InterruptedError("model request cancelled")
        if response is None:
            raise RuntimeError("model stream ended without a terminal response")
        payload = response.model_dump(exclude_none=True)
        output = payload.get("output", [])
        text, calls = [], []
        for item in output:
            if item["type"] == "message":
                text.extend(block.get("text", "") for block in item.get("content", []) if block["type"] == "output_text")
            elif item["type"] == "function_call":
                calls.append(ToolCall(item["call_id"], item["name"], json.loads(item["arguments"])))
        usage = payload.get("usage")
        if usage:
            usage.update(input=usage.get("input_tokens"), output=usage.get("output_tokens"),
                         total=usage.get("total_tokens"))
        return LLMReply("\n".join(text), calls, usage,
                        {"identity": self.identity, "items": output,
                         "response_model": payload.get("model"), "effort": self.effort},
                        payload.get("status", "completed"))
