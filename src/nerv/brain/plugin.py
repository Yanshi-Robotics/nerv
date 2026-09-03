"""Build a brain plugin by registry name. The platform calls only this."""
from __future__ import annotations

from ..platform.session.log import LoggingLLM
from .providers.factory import list_brains, make_llm
from .react import ReactBrain

__all__ = ["load", "list_brains"]


def load(name: str) -> ReactBrain:
    llm = LoggingLLM(make_llm(name), name)
    return ReactBrain(llm, name=name)
