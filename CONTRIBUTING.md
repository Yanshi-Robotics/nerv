# Contributing to NERV

NERV connects independently trained policies and model tools to robot runtimes. Training
and automatic calibration are outside its scope. The first real product contract is
standard SO-101 with LeRobot 0.6.1; see the [student guide](guides/standard-so101.md).

## Prepare a change

Describe the problem, the affected runtime or interface, and a reproducible case. Keep
device details in adapters and policy details in policy backends. Core services should
use declared capabilities. A new robot or policy needs its own compatibility evidence;
an interface fixture does not establish that a real model or robot works.

Use a separate Python environment. Install the Runtime SDK and host development extras
from this checkout; adapter tests can use a dependency-free editable adapter installation.
Install the native LeRobot environment separately for model integration or hardware work.

```bash
python -m pip install -e ./packages/nerv-runtime-sdk -e '.[platform,dev]'
```

```bash
python -m pip install --no-deps -e ./packages/nerv-runtime-lerobot
```

```bash
python -m pytest tests/test_standard_product.py tests/test_lerobot_adapter.py tests/test_runtime_sdk.py tests/test_runtime_management.py -q
```

```bash
npm --prefix frontend ci
```

```bash
npm --prefix frontend run check:locales
```

For simulation regression, also install the host `sim` extra and initialize the required
submodules. Headless Linux may need `MUJOCO_GL=egl LIBGL_ALWAYS_SOFTWARE=1`. Run the relevant
tests and report their environment. Build the complete candidate bundle with
`python scripts/build_release.py` (uv is also required). Students use its prebuilt installer
without Node.js. Dependency constraints live under `release/constraints`; changing those
versions requires package installation and relevant runtime checks.

From the repository root on Linux or macOS, the existing simulation setup is:

```bash
git submodule update --init --recursive
```

```bash
.venv/bin/python -m pip install -e '.[sim]'
```

After building the static UI as described in the README installation guide, start
the development Python host:

```bash
.venv/bin/nerv serve
```

The optional frontend development server uses port 8100:

```bash
npm --prefix frontend run dev
```

G1 worlds use their registered gait skills. The SO-101 worlds `so101-pick-place`,
`so101-sort-snacks` and `so101-hanoi-five` use bounded joint/gripper control, not a
deployed ACT policy. The standard workbench scene generator is:

```bash
.venv/bin/python worlds/tools/make_workbench.py --all
```

See the [workbench guide](worlds/scenes/so101_workbench/README.md) for scene configuration.
On Windows, use the corresponding `.venv\Scripts` executables; hardware and rendering
support still require platform-specific verification.

## Review and evidence

Keep a pull request focused and describe the resulting behavior, tests and remaining
limitations. Update both language guides when changing student workflows. Preserve the
distinction between configuration checks, model inference, simulated motion and physical
task completion. Include a failure case for changes to motion authorization or cancellation.

Do not connect hardware in CI. Physical testing requires an operator, supported hardware
and an explicit record of outcomes and interventions. Reports should distinguish a target
request, the driver's sent target, measured position and actual task outcome.

## Report a problem

Use the issue template and include versions, OS, reproduction steps and sanitized error
text. Remove API keys, serial numbers, usernames, private paths, images of people and
unrelated logs. Share a minimal configuration with device paths replaced by placeholders.
Do not upload trained weights or camera recordings unless you have permission to distribute
them. A report can describe model feature shapes and processor filenames without uploading
the model.

## AI contributors

DeepSeek Harness and Codex have contributed AI-assisted development to NERV.
Maintainers review changes and remain responsible for merge and release decisions.
AI-assisted contributions follow the same scope, testing and evidence requirements
as other changes. Planned Harness and plugin integrations are described in the
[roadmap](ROADMAP.md).
