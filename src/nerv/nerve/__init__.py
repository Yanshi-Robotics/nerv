"""The five NERV interfaces, as types.

- :mod:`nerve.body`     NERV/Body  — what a body node declares and how the brain sees it
- :mod:`nerve.brain`    NERV/Brain — the messages between the platform and a brain plugin
- :mod:`nerve.world`    NERV/World — the motor bus, sensor streams and the control plane
- :mod:`nerve.tool`     NERV/Tool  — tool nodes (tools only, no senses)
- :mod:`nerve.operator` NERV/Operator — event names on the operator-facing stream
- :mod:`nerve.sensors`  sensor stream naming and frames, shared by Body and World
"""
