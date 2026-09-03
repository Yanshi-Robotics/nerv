"""Run a world node:  python -m nerv.world --world W --body B --http-port P --bus-port Q
                   python -m nerv.world --arena scene.xml --http-port P --bus-port Q"""
from __future__ import annotations

import sys

from .mujoco_node import main

if __name__ == "__main__":
    sys.exit(main())
