"""Build a brain plugin by registry name. The platform calls only this."""
from __future__ import annotations

from .providers.factory import list_brains, make_llm
from ..harness.agent import AgentHarness
from ..harness.context import ContextManager
from .. import config

__all__ = ["load", "list_brains"]


def load(name: str, wrap=None) -> AgentHarness:
    llm = make_llm(name)
    if wrap:
        llm = wrap(llm)
    return AgentHarness(llm, name, ContextManager(config.REQUEST_TOKEN_BUDGET,
                                                config.MAX_TOKENS,
                                                config.IMAGE_TOKEN_ALLOWANCE))
