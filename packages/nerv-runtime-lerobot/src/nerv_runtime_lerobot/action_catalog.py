"""Offline action declarations shared with the device runtime."""
from .standard import JOINTS

def primitive_actions(settings):
    evidence = {"frame_seq": {"type": "integer", "x-observation-field": "frame_seq"}, "instance_id": {"type": "string", "x-observation-field": "instance_id"}}
    duration = {"type": "number", "minimum": settings["min_duration_s"], "maximum": settings["max_duration_s"]}
    targets = {"type": "object", "properties": {k: {"type": "number"} for k in JOINTS},
               "additionalProperties": False, "minProperties": 1}
    specs = [("read_state", "Read a fresh observation and its frame_seq and instance_id before motion.", {}, "read"),
             ("move_joints", "Move standard arm joints to degree targets; reobserve afterwards.", {"targets": targets, "duration_s": duration, **evidence}, "primitive"),
             ("nudge_joints", "Move standard arm joints by bounded degree increments.", {"targets": targets, "duration_s": duration, **evidence}, "primitive"),
             ("set_gripper", "Set native gripper opening from 0 (closed) to 100 (open).", {"percent": {"type": "number", "minimum": 0, "maximum": 100}, "duration_s": duration, **evidence}, "primitive")]
    return [{"name": name, "description": description, "kind": kind,
             "parameters": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}}
            for name, description, props, kind in specs]
