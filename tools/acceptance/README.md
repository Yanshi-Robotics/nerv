# Residence acceptance

These programs exercise generated scenes with the released G1 policy. They do not request a language-model turn or control hardware. Run them from the NERV repository after preparing the external furniture, generated scenes and Python dependencies in the [installation guide](../../README.md#installation).

| Program | What it checks |
|---|---|
| `verify_routes.py` | Source-defined routes and measured returns through the NERV safety gate, MCP body tools and ZMQ motor bus |
| `verify_task_runtime.py` | Repeated refrigerator placement using simulated interaction forces and the normal G1 standing controller |
| `verify_lifecycle.py` | Lease expiry, cancellation, stale-command rejection, whole-scene reset, session replacement and shutdown |
| `verify_hazards.py` | Braking at the pool, closed gate, stairs, low furniture, table and a displaced chair |
| `verify_confined.py` | Bidirectional narrow-door passage and refusal to turn when the body envelope would meet a wall |
| `verify_time.py` | Four lighting phases, actual camera images and unchanged physical state during each visual mutation |

## CPU checks

The following programs disable rendering for their own processes. Existing output directories are refused. Use a fresh output directory for each run; reports, action journals and node logs stay together. The route and lifecycle programs create isolated sessions and close only the nodes they launch. They refuse descriptors pointing to an attached world or body.

```bash
.venv/bin/python tools/acceptance/verify_routes.py --scene apt --repeats 5 --output temp/acceptance/routes-apt
.venv/bin/python tools/acceptance/verify_routes.py --scene house --repeats 5 --output temp/acceptance/routes-house
.venv/bin/python tools/acceptance/verify_task_runtime.py --repeats 5 --output temp/acceptance/fridge
.venv/bin/python tools/acceptance/verify_lifecycle.py --repeats 1 --output temp/acceptance/lifecycle
.venv/bin/python tools/acceptance/verify_hazards.py --repeats 1 --output temp/acceptance/hazards
.venv/bin/python tools/acceptance/verify_confined.py --output temp/acceptance/confined
```

`verify_routes.py --route` accepts a comma-separated subset. Apt provides `entry_gallery`, `living_dining` and `kitchen_access`. House provides `gate_approach`, `pool_approach`, `lawn_approach`, `pool_loop`, `residence_loop` and `rear_branch`. Route names are validated before launching nodes. Inspect `--help` for other options. For parallel transport runs, assign separate free ranges within the host's allocated node pool using `NERV_NODE_PORTS=lo-hi`.

The refrigerator program uses one WorldSim physics clock and the original G1 controller. It never fixes the robot's pose during an action or writes the can into place. The lifecycle program observes state over the transport; it cannot directly inspect force arrays. A state request can itself expire a lease, so its expiry timing is not a direct measurement of autonomous force clearing at exactly two seconds.

The hazard program samples motion at 50 Hz and observes three seconds after stopping. The confined-space program records contact maxima after every physics step. A route test passes only after measured arrival and return; a successful tool response alone is insufficient. The chair hazard moves and releases a chair before walking, rather than inserting it during a step.

## Rendering checks

Reserve the GPU through the host's existing queue before running:

```bash
.venv/bin/python tools/acceptance/verify_time.py --scenes apt house --repeats 1 --queue-id YOUR_RESERVED_ID --output temp/acceptance/time
```

`--queue-id` records an existing reservation; it does not acquire one. The tool renders the native head and chase cameras plus authored room and route views. Time changes run on the production render thread, and the report compares physical state immediately before and after each mutation.

For scene generation, interaction fixtures, Explore export and dual-camera performance, use the [world-library tools](../../worlds/docs/explore/README.md). The [release validation record](../../docs/validation/world-explore/README.md) separates current results, retained failures, actual browser operations and measurements that require manual browser inspection.
