"""Native ACT loading; no robot creation, connection or actuator writes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def artifact_files(manifest: dict):
    value = os.environ.get(manifest["root_env"], "")
    if not value:
        raise ValueError(f"set {manifest['root_env']} to the frozen checkpoint directory")
    root = Path(value).expanduser().resolve(strict=True)
    for name, digest in manifest["sha256"].items():
        file = root / name
        if not file.is_file():
            raise ValueError(f"policy artifact file missing: {name}")
        with file.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError(f"policy artifact fingerprint mismatch: {name}")
    return root


class ACTRunner:
    def __init__(self, manifest: dict, device: str):
        root = artifact_files(manifest)
        import torch
        from lerobot.policies.act.modeling_act import ACTPolicy
        from lerobot.policies.factory import make_pre_post_processors
        self.policy = ACTPolicy.from_pretrained(str(root), local_files_only=True)
        self.policy.to(device).eval()
        self.pre, self.post = make_pre_post_processors(
            self.policy.config, pretrained_path=str(root),
            preprocessor_overrides={"device_processor": {"device": device}})
        self.device = torch.device(device)
        self.manifest = manifest
        config = json.loads((root / "config.json").read_text())
        if config["type"] != "act" or config["input_features"] != manifest["input_features"]:
            raise ValueError("checkpoint does not match registered ACT input contract")

    def reset(self):
        self.policy.reset()
        self.pre.reset()
        self.post.reset()

    def action(self, raw: dict):
        import numpy as np
        import torch
        from lerobot.policies.utils import prepare_observation_for_inference
        frame = {"observation.state": np.asarray([raw[key] for key in self.manifest["state_names"]], dtype=np.float32)}
        for key in self.manifest["image_names"]:
            frame[f"observation.images.{key}"] = raw[key]
        with torch.inference_mode():
            inputs = prepare_observation_for_inference(frame, self.device)
            action = self.post(self.policy.select_action(self.pre(inputs))).squeeze(0).cpu()
        values = action.tolist()
        if len(values) != len(self.manifest["action_names"]):
            raise ValueError("policy returned a different action shape")
        return dict(zip(self.manifest["action_names"], values))
