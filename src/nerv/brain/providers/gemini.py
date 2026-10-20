"""Gemini Interactions API with stateless signed-step continuation.

Only client function tools are exposed. The SDK handles transport/SSE; this adapter
collects the documented text, thought-signature and function-argument deltas.
Unknown deltas fail closed rather than silently damaging a signed continuation.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json

from .base import LLMReply, ToolCall, norm_images


class GeminiLLM:
    vision = True
    cancellable = True

    def __init__(self, spec, api_key, effort=None, client=None):
        self.spec, self.model, self.effort = spec, spec.model, effort
        self.context_window, self.max_output_tokens = spec.context_window, spec.max_output_tokens
        self.identity = f"{spec.signature()}:{effort}:" + hashlib.sha256(api_key.encode()).hexdigest()
        if client is None:
            from google import genai
            client = genai.Client(api_key=api_key, http_options={
                "base_url": spec.base_url, "timeout": int(spec.request_timeout_s * 1000),
                "retry_options": {"attempts": 1},
            })
        self.client = client

    def inputs(self, history, images):
        result = []
        for message in history:
            role = message["role"]
            if role == "user":
                result.append({"type": "user_input", "content": [{"type": "text", "text": message["text"]}]})
            elif role == "assistant":
                state = message.get("continuation", {})
                if state.get("identity") == self.identity:
                    result.extend(copy.deepcopy(state["items"]))
                else:
                    if message.get("text"):
                        result.append({"type": "model_output", "content": [{"type": "text", "text": message["text"]}]})
                    for call in message.get("tool_calls", []):
                        result.append({"type": "function_call", "id": call.id, "name": call.name,
                                       "arguments": call.arguments})
            elif role == "tool":
                result.append({"type": "function_result", "call_id": message["id"], "name": message["name"],
                               "result": [{"type": "text", "text": message["content"]}]})
        if images:
            content = []
            for name, png in norm_images(images):
                content.extend([{"type": "text", "text": f"Current camera: {name}"},
                                {"type": "image", "mime_type": "image/png", "data": base64.b64encode(png).decode()}])
            result.append({"type": "user_input", "content": content})
        return result

    def chat(self, system, history, tools, images, *, cancelled=lambda: False):
        config = {"max_output_tokens": self.max_output_tokens}
        if self.effort is not None:
            config["thinking_level"] = self.effort
        request = {"model": self.model, "system_instruction": system,
                   "input": self.inputs(history, images), "generation_config": config,
                   "store": False, "stream": True, "timeout": self.spec.request_timeout_s}
        if tools:
            request["tools"] = [{"type": "function", "name": t.name, "description": t.description,
                                  "parameters": t.parameters} for t in tools]
        if cancelled():
            raise InterruptedError("model request cancelled before dispatch")
        stream = self.client.interactions.create(**request)
        steps, arguments = {}, {}
        terminal = None
        try:
            for event in stream:
                if cancelled():
                    raise InterruptedError("model request cancelled")
                data = event.model_dump(mode="json", exclude_none=True)
                kind = data.get("event_type")
                if kind == "step.start":
                    steps[data["index"]] = copy.deepcopy(data["step"])
                elif kind == "step.delta":
                    index, delta = data["index"], data["delta"]
                    step = steps[index]
                    delta_type = delta["type"]
                    if delta_type == "thought_signature":
                        step["signature"] = step.get("signature", "") + delta.get("signature", "")
                    elif delta_type == "thought_summary" and step["type"] == "thought":
                        if delta.get("content"):
                            step.setdefault("summary", []).append(delta["content"])
                    elif delta_type == "arguments_delta":
                        arguments[index] = arguments.get(index, "") + delta.get("arguments", "")
                    elif delta_type == "text" and step["type"] == "model_output":
                        content = step.setdefault("content", [])
                        if content and content[-1]["type"] == "text":
                            content[-1]["text"] += delta.get("text", "")
                        else:
                            content.append({"type": "text", "text": delta.get("text", "")})
                    else:
                        raise RuntimeError("unsupported Gemini stream delta; no tools dispatched")
                elif kind in ("interaction.completed", "interaction.failed", "interaction.incomplete"):
                    terminal = data["interaction"]
                elif kind == "interaction.status_update":
                    if data.get("status") in ("requires_action", "failed", "cancelled", "incomplete", "budget_exceeded"):
                        terminal = data
                elif kind == "error":
                    raise RuntimeError("provider returned a streaming error")
                elif kind not in ("interaction.created", "step.stop"):
                    raise RuntimeError("unsupported Gemini stream event; no tools dispatched")
        finally:
            stream.close()
        if cancelled():
            raise InterruptedError("model request cancelled")
        if terminal is None:
            raise RuntimeError("model stream ended without a terminal response")
        # Some deployments return the complete steps in the terminal event.
        items = terminal.get("steps")
        if items is None:
            for index, fragments in arguments.items():
                steps[index]["arguments"] = json.loads(fragments)
            items = [steps[index] for index in sorted(steps)]
        text = "\n".join(block["text"] for item in items if item["type"] == "model_output"
                         for block in item.get("content", []) if block["type"] == "text")
        calls = [ToolCall(item["id"], item["name"], item["arguments"])
                 for item in items if item["type"] == "function_call"]
        usage = terminal.get("usage")
        if usage:
            usage = {**usage, "input": usage.get("total_input_tokens"),
                     "output": usage.get("total_output_tokens"), "total": usage.get("total_tokens")}
        status = terminal.get("status", "incomplete")
        if status == "requires_action" and calls:
            status = "completed"  # The model turn completed; host tools may now run.
        return LLMReply(text, calls, usage, {"identity": self.identity, "items": items}, status)
