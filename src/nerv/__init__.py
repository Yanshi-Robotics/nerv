"""NERV — a nervous system for robots.

Four kinds of node (brain, body, world, tool), five interfaces (NERV/Operator, NERV/Brain,
NERV/Body, NERV/World, NERV/Tool), one platform in the middle that registers, routes, gates
and logs. The interface contracts live in :mod:`nerv.nerve`; the platform in
:mod:`nerv.platform`; the node implementations in :mod:`nerv.brain`, :mod:`nerv.body`,
:mod:`nerv.world`, :mod:`nerv.tool`.
"""
from ._version import __version__

__all__ = ["__version__"]
