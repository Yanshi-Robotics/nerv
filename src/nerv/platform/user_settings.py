"""Operator preferences; each conversation takes an immutable effective snapshot."""
import json
import math
import os
from pathlib import Path

from dotenv import dotenv_values
from filelock import FileLock
from pydantic import TypeAdapter
from typing import Annotated

from .. import config, paths
from ..installation import write_json

FIELDS = config._RUNTIME_PARAMS_SHOWN


def location():
    return Path(paths.NERV_HOME) / "devices" / "preferences.json"


def overrides():
    configured = {**dotenv_values(paths.ENV_FILE), **os.environ}
    return {key for key in FIELDS if config.Settings.model_fields[key].validation_alias in configured}


def read():
    path = location()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def snapshot():
    saved, locked = read(), overrides()
    baseline = config.Settings(_env_file=paths.ENV_FILE)
    return {key: getattr(baseline, key) if key in locked else saved.get(key, config.Settings.model_fields[key].default)
            for key in FIELDS}


def describe():
    effective, saved, locked = snapshot(), read(), overrides()
    return [{**p, "value": effective[p["key"]], "editable": p["key"] not in locked,
             "default": config.Settings.model_fields[p["key"]].default,
             "constraints": _adapter(p["key"]).json_schema(),
             "source": "environment" if p["key"] in locked else "saved" if p["key"] in saved else "default"}
            for p in config.runtime_params()]


def _adapter(key):
    field = config.Settings.model_fields[key]
    return TypeAdapter(Annotated[field.annotation, *field.metadata])


def save(values, reset=False):
    if set(values) - set(FIELDS):
        raise ValueError("Unknown runtime parameter")
    path = location()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock"):
        saved = read()
        if reset:
            saved = {}
        else:
            if set(values) & overrides():
                raise ValueError("An environment setting overrides this parameter")
            for key, value in values.items():
                integer = key != "turn_time_budget_s"
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("Runtime parameters must be finite numbers")
                if integer and not isinstance(value, int):
                    raise ValueError("This parameter requires a whole number")
                _adapter(key).validate_python(value, strict=True)
            saved.update(values)
        write_json(path, saved)
    return describe()
