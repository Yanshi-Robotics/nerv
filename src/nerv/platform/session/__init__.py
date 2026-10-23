from .store import Session, SessionStore  # noqa: F401


def require_scene(body, world, mode):
    """Public creation requires a robot/environment; old records remain readable."""
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Select a robot and scene before creating a session")
    if mode not in ("sim", "real"):
        raise ValueError("Select a Sim or Real environment")
    if mode == "sim" and (not isinstance(world, str) or not world.strip()):
        raise ValueError("Select a simulation scene before creating a session")
    if mode == "real" and world:
        raise ValueError("A real workspace cannot use a simulation scene")
