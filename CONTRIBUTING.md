# Contributing to NERV

NERV connects independently trained policies and model tools to robot runtimes. Training
and automatic calibration are outside its scope. The first real product contract is
standard SO-101 with LeRobot 0.6.1; see the [student guide](guides/standard-so101.md).

## Prepare a change

Describe the problem, the affected runtime or interface, and a reproducible case. Keep
device details in adapters and policy details in policy backends. Core services should
use declared capabilities. A new robot or policy needs its own compatibility evidence;
an interface fixture does not establish that a real model or robot works.

## Build outside the checkout

The 0.8.5 development workflow keeps Python environments, Node.js, frontend dependencies,
caches and candidate packages under the NERV storage root. Edit the Git checkout in your
project directory; do not create a venv or run npm install there. The managed build takes
an indexed source snapshot, including edits to tracked files. Add new files to the index
when they should be included; credentials, user data and existing build directories are excluded.

With a host that supports the managed preview commands:

```bash
git submodule update --init --recursive
nerv preview build --source /path/to/nerv
```

The command reports the candidate bundle directory. It prepares pinned tools, builds the
static interface and Python packages, then runs frontend and software contract checks in
a separate validation environment. It does not activate the candidate.

```bash
nerv preview update --bundle /path/to/reported/bundle
nerv preview
```

Preview data belongs to its own profile. Device authorization and conversation history
are not copied from the stable channel. Stop the preview before updating or rolling it back;
never stop another service to free a port.

For CI or initial development before a supporting host has been installed, Python 3.12
and uv 0.12.2 can bootstrap the same build workflow:

```bash
python scripts/ci_managed.py
```

This creates a bootstrap environment in `.nerv/ci`, obtains the locked build tools and
runs an installed-host update/rollback smoke test in a new isolated directory. It does
not change a stable or preview active pointer. On Windows, Linux and macOS the tools
use their respective platform packages; venvs are not portable between systems.

Dependency baselines live in `release/constraints`, `release/build.lock` and
`release/validation.lock`. Change them deliberately and test affected runtimes. The
three SO-101 scenes use bounded local joint/gripper control, not a deployed ACT policy.
Simulation checks, model inference and physical task acceptance remain separate.
The [asset guide](guides/assets.md) explains data-only scene packages and personal revisions.

Use the paths in the build record for additional diagnostics. Do not move dependencies
back into the author checkout or add dependency symlinks there. Run
`python scripts/check_source_clean.py` to check the source directory after migration.
Existing legacy dependencies require a reviewed inventory and recovery check before removal.

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
