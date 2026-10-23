"""Metadata-only provider import. Native dependencies load during explicit preparation."""
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path
import hashlib

from nerv_runtime_sdk.contracts import RuntimeManifest


class LeRobotProvider:
    def describe(self):
        try:
            installed = version("lerobot")
        except PackageNotFoundError:
            installed = None
        adapter_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted(Path(__file__).parent.glob("*.py"))}
        sdk_hashes = {}
        native = find_spec("lerobot") if installed else None
        if native and native.submodule_search_locations:
            root = Path(next(iter(native.submodule_search_locations)))
            for relative in ("robots/so_follower/so_follower.py", "cameras/opencv/camera_opencv.py",
                             "policies/act/modeling_act.py", "policies/factory.py"):
                path = root / relative
                if path.is_file():
                    sdk_hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return RuntimeManifest(id="lerobot0.6.1", version="0.7.0",
                               features=["real", "named_sensors", "managed_execution", "native_act", "bounded_primitives", "policy_backends", "offline_setup"],
                               source={"sdk": "lerobot", "required_version": "0.6.1",
                                       "installed_version": installed, "adapter_sha256": adapter_hashes, "sdk_source_sha256": sdk_hashes},
                               configuration_schema={"type": "object", "required": ["port", "camera_config_file"]})

    def manage(self, operation, payload, home):
        from .manage import manage
        return manage(operation, payload, home)

    def create(self, profile, binding):
        from .runtime import LeRobotRuntime
        return LeRobotRuntime(profile, binding)
