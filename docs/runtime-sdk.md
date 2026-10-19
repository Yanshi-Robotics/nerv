# Runtime adapter guide

A runtime is an independent service. Install its SDK and driver in the environment that owns the device; the NERV host needs neither the driver nor the tensor framework. The first-party examples are [the LeRobot package](../packages/nerv-runtime-lerobot) and [the simulator adapter](../src/nerv/runtime/builtin.py).

## Register a Python provider

A vendor package declares its entry point in its own `pyproject.toml`:

```toml
[project]
name = "vendor-nerv-adapter"
version = "0.1.0"
dependencies = ["nerv-runtime-sdk==0.6.0"]

[project.entry-points."nerv.runtime_providers"]
vendor = "vendor_adapter:Provider"
```

The provider's metadata module imports only lightweight types. Device initialization belongs in the runtime's explicit preparation method:

```python
from nerv_runtime_sdk.contracts import RuntimeManifest

class Provider:
    def describe(self):
        return RuntimeManifest(
            id="vendor", version="0.1.0", features=["real", "named_sensors"],
            configuration_schema={"type": "object", "required": ["endpoint"]},
        )

    def create(self, profile, binding):
        from .runtime import VendorRuntime
        return VendorRuntime(profile, binding)
```

This is a registration example. `VendorRuntime` must implement the real driver; the snippet does not provide a working device or invented readings. A runtime constructor must also avoid connecting equipment or initializing a GPU.

## Register the robot and environment

Add a profile in an extra registry root and set `NERV_REGISTRY_PATHS` to that root. Each profile is a directory under `bodies/` with `body.yaml` and optional `guidance.md`:

```yaml
name: vendor-arm
family: vendor-arm-v2
label: Vendor arm
buses:
  real:
    kind: vendor
    provider: vendor
    verified: false
    python: "${VENDOR_RUNTIME_PYTHON}"
    settings:
      endpoint: "${VENDOR_DEVICE_ENDPOINT}"
```

The family is an open identifier. Set a binding's `enabled: false` when that environment is not configured. No core enum, UI branch or host environment injection is required. A configured `command` is an argv list with `{host}`, `{port}` and `{configuration}` placeholders, executed without a shell. Alternatively set `url` to an already running compatible HTTP/MCP service; a non-Python vendor can use this route.

The generated configuration file carries profile, binding and display settings. Artifact `root_env` values are explicitly forwarded for frozen model directories; model credentials are not inherited. Do not put secrets in profile YAML or command-line arguments.

## Implement the lifecycle

Implement the methods used by [the SDK server](../packages/nerv-runtime-sdk/src/nerv_runtime_sdk/node.py):

- `prepare()` connects the device after operator preparation and reports actual readiness. Partial failure must clean up acquired resources; failed cleanup leaves a fault requiring inspection.
- `tools()`, `sensor_names()`, `config_options()` and `policies()` report current capabilities. Discovery and metadata reads must not connect equipment.
- `observe()` returns sensor state and named PNGs with capture provenance. `sensor_jpeg(name)` exposes the same acquisition source for video.
- `load_policy(id)` verifies a registered artifact and loads its processors. It must send no motion and refuse replacement during execution.
- `set_armed(enabled)` changes local dispatch permission. `invoke()` performs an explicitly named read, control or motion tool.
- `cancel_current(reason)`, `hold()`, `release()` and `close()` have separate semantics. Hold must remain effective without the model or browser.

`ManagedExecutions` supplies one execution, local supervision timeout, cancellation and a revocable dispatch generation. Keep inference outside the final write lock. Capture a fixed policy runner per execution, validate the upstream control guard around startup, and send each target through `DispatchAuthority.write()`. Never hold the device lock throughout model inference or preparation.

## Wire protocol

The current protocol is major 1, minor 0. A different major is refused before preparation. Adapter version, dependency version, source fingerprint and per-boot epoch are separate facts.

| HTTP endpoint | Contract |
|---|---|
| `GET /health` | node identity, protocol major/minor, epoch, family, armed, held and manifest |
| `GET /config`, `POST /config` | operator configuration and enable state |
| `POST /prepare` | explicit preparation; never automatic for real equipment |
| `GET /policies`, `POST /policies/load` | artifact metadata and explicit loading |
| `POST /control/claim` | immutable controller `owner`, increasing `generation`, random `token` |
| `POST /executions/status` | execution ID, running flag, terminal outcome and stop reason |
| `POST /supervision` | matching epoch plus recent agent observation `frame_seq` |
| `POST /stop`, `/hold`, `/release` | cancel, latched emergency hold, disarmed standby |
| `GET /sensors`, `/streams/{name}`, `/snapshots/{name}` | named sensors and fresh camera frames |
| `/mcp` | tool schemas/calls, `nerv://observation`, config/capabilities and guidance |

The host adds the claimed token as reserved `_nerv_control` metadata to body calls; the model never chooses it. A worker accepts one controller for its lifetime. Start a new worker when attaching a new NERV controller instance. Stopping invalidates the request token; an older claim or call cannot obtain fresh permission later.

## Check before connecting

From the adapter environment:

```bash
nerv-runtime discover
nerv-runtime check --provider vendor
nerv-runtime check --provider vendor --url http://127.0.0.1:8112
```

The checks validate description, protocol and read-only HTTP shapes. They do not prepare, load, arm or move. The core `nerv conformance … --kind body` checks existing MCP shapes as well. Software conformance is not device safety or physical task acceptance.

Test delayed inference, cancelled startup, late network requests, simultaneous policy changes, failed preparation, camera staleness, supervisor loss and emergency release before physical trials. Examples of these CPU-only checks live in `tests/test_runtime_sdk.py`, `tests/test_lerobot_adapter.py` and `tests/test_upgrade_regressions.py`.

The lifecycle and discovery choices follow [ROS 2 control's component lifecycle](https://control.ros.org/jazzy/doc/getting_started/getting_started.html) and [Python entry-point discovery](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/). NERV uses its existing HTTP/MCP boundary rather than requiring ROS or a vendor-specific host dependency.
