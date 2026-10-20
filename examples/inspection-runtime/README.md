# Inspection runtime example

This independently installable provider demonstrates protocol 2 discovery and lifecycle
without connecting hardware, loading a model, or pretending to execute a robot task.
Install the SDK and this example in the adapter's environment, from the NERV checkout:

```bash
python -m pip install -e packages/nerv-runtime-sdk -e examples/inspection-runtime
```

```bash
nerv-runtime discover
```

```bash
nerv-runtime check --provider inspection
```

`Provider.describe()` is static. `create()` allocates only a lifecycle object; preparation
is explicit. `actions()` exposes one read-only operation. Sensors and execution status
have separate methods. A vendor adapter follows the same registration pattern and adds
its own dependency metadata, configuration validation, sensor source and bounded executor.

This example has no motion implementation and is not a production robot profile. It does
not satisfy actuator, watchdog or policy acceptance. See the [SDK guide](../../packages/nerv-runtime-sdk/README.md).
