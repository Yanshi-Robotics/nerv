# NERV

[简体中文](i18n/zh/README.md) · [Quick install & start](#quick-install--start) · [SO-101 guide](guides/standard-so101.md) · [Architecture](guides/architecture.md) · [Runtime SDK](packages/nerv-runtime-sdk/README.md)

NERV connects a visual language model, robot observations and locally executed skills. Use it to run a trained policy, control a robot through bounded tools, or let a visual model supervise a task while you continue the conversation.

Version 0.6.3 is a development candidate. Standard SO-101 with LeRobot is the first real-device product; physical acceptance is pending. NERV runs externally trained policies and uses existing calibration. Training is outside its scope.

## Quick install & start

Install [Node.js 22 or later](https://nodejs.org/en/download) with npm and [uv](https://docs.astral.sh/uv/getting-started/installation/). The launcher manages Python 3.12 and separate host/device environments. Your training environment is kept separate.

From the root of a downloaded or cloned checkout containing this launcher, install once. These commands work in a POSIX shell or Windows PowerShell; hardware support is listed below.

```bash
npm install --global --install-links .
```

The installation builds and includes the web interface. `--install-links` installs a copy instead of leaving the command linked to the checkout. Start NERV from any directory:

```bash
nerv
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The first launch installs the host's Python dependencies; later launches reuse them. The same process serves the interface and API. No separate frontend server is needed. Ctrl+C ends the server.

```bash
nerv doctor
```

The diagnostic command reports the version, data directory and configuration without connecting hardware. User data defaults to `~/.nerv` on every installation; normal use needs no `NERV_HOME` assignment. To choose another port:

```bash
nerv serve --port 8100
```

For installation directly from a published GitHub archive, including the `npx --package` entry, follow the [GitHub installation guide](guides/github-install.md). No npm-registry publication is required. Use a published commit that contains the launcher; an unpushed local candidate cannot be downloaded from GitHub. The guide also covers local packages, updates and the wheel alternative.

## Connect a standard SO-101

The supported product contract is one SO-101 follower, LeRobot 0.6.1, and two cameras named `top` and `wrist` at 640 × 480 and 30 FPS. Five arm joints use degrees and the gripper uses LeRobot's 0–100 positions. The leader is not needed for inference. Students supply connection details and existing artifacts; there is no custom joint-mapping step.

Install the separate device environment once:

```bash
nerv runtime install lerobot0.6.1
```

```bash
nerv runtime list
```

This installs LeRobot and Feetech without connecting the arm. Its tensor dependencies are larger than the host installation. Then follow the [SO-101 setup and policy guide](guides/standard-so101.md):

1. Open **Device and policy setup**. With one installed runtime, its provider and Python path are filled in. Read its setup options and enter the serial port, robot ID, existing calibration directory, camera configuration and inference device.
2. For ACT, import your local export with its original weights, native processors and statistics. NERV checks the fixed device contract and records file fingerprints. Missing semantic information requires the standard course declaration; tensor shape alone does not establish units or ordering.
3. Inspect and check the policy before connecting. **check** loads the model without hardware; the later **Check policy without motion** operation performs inference on real observations while disarmed.
4. Create a real-hardware session, review access, and have the operator prepare the device. Preparation can enable motor torque. Check both camera views, then enable motion separately.

Candidate real bindings are unverified and require the explicit supervised-acceptance setting described in the guide. The default refusal is intentional. Neither installing a runtime nor starting the web server connects or enables a robot.

| Control route | What runs | What you provide |
|---|---|---|
| Manual ACT | Native ACT preprocessing, inference and postprocessing in the worker | Your trained standard ACT export; choose **Operator only**, with no VLM key |
| VLM direct control | A visual model chooses bounded joint and gripper tools; interpolation and feedback run locally | A configured visual model; no ACT checkpoint required |
| VLM-supervised ACT | System 2 observes and supervises the worker's policy execution | A configured visual model and your trained ACT export |

All routes use the same device connection and action scheduler. Stop the current execution before switching controllers. An action finishing is not evidence of a successful grasp. Requested targets, targets returned by LeRobot and measured positions are recorded separately.

## Architecture

One instance binds one robot and one environment, with one active control session. NERV Agent handles conversation and delegates goals to System 2. System 2 has its own model context, task state and cancellation. Ordinary chat does not cancel the task.

<div align="center"><img src="assets/readme/images/layers.svg" alt="Agent conversation and System 2 task supervision use independent contexts; local execution and device ownership are separate from simulation world control" width="960"></div>

The host owns sessions, control authorization, runtime lifecycle and event journals. A separate worker owns the robot, cameras and local execution. System 1 denotes that execution layer; it is not a third Python environment.

For standard SO-101, the adapter creates one native LeRobot device instance. ACT produces candidate positions through its native processing pipeline; direct VLM tools produce bounded position targets. Both pass through freshness, authorization and motion checks before the same `robot.send_action()` call. The main process never imports LeRobot or loads policy tensors.

The [architecture and control guide](guides/architecture.md) explains Agent/System 2 separation, observation flow, provider continuations, session memory, simulation controls and the Runtime SDK. The core coordinates interfaces; robot and model-specific implementation belongs in adapters and policy backends. SmolVLA and GR00T are future integrations, not delivered backends.

## Operator controls and models

The right sidebar switches between Agent chat and Operator Toolbox. **Stop reply** cancels the conversation response; **Stop task** cancels local execution; **Emergency stop** revokes motion and attempts to hold the measured pose. Releasing the stop returns to disarmed standby without resuming the previous task.

Connecting, loading a policy, enabling motion and disconnecting are separate operations. Ordinary disconnection retains torque. Explicit torque release requires an operator to support the arm. A software hold is not a power cut, and a lost connection cannot confirm that the hold was executed.

For visual control, open **Models and API keys**. NERV supports a curated catalogue across OpenAI, DeepSeek, Gemini, xAI and Anthropic. Submitting a new or replacement key triggers one billable image check before saving; opening settings and creating sessions do not request a model. Choose the conversation model and the task model in their respective controls. Existing registered keys remain usable without optional diagnostics.

An **Operator only** session can run manual ACT without API credentials. A VLM session requires a registered key. Robot camera frames enter model observations; simulator ground truth and operator-only overview cameras do not. The complete [model catalogue and control behavior](guides/architecture.md#visual-models) is documented separately.

## Data and installation layout

Both installed and source launches use `~/.nerv` for sessions, logs, imported manifests, device profiles and model settings. The launcher stores host and device environments under `envs/` and runtime installation records under `runtimes/`. An explicit `NERV_HOME` overrides the location for isolated tests.

Configuration values belong in `~/.nerv/.env`; see [.env.example](.env.example). API keys come from private credential storage, not project or shell API-key variables. POSIX uses a private credentials file; Windows uses Credential Manager. Training weights and calibration files remain in their original locations. npm manages installed launcher code and its own download cache.

Updates select an environment by source fingerprint and retain existing data and older environments. They do not stop running servers. Older repository-local sessions are not automatically merged into the user directory. See [installation and updates](guides/github-install.md#user-data-and-updates).

## Support and validation

| Connection | Current scope |
|---|---|
| SO-101 / real | Standard LeRobot 0.6.1 product, native ACT, bounded direct control and shared observations. Linux physical acceptance is pending. |
| G1 / simulation | Existing MuJoCo worlds and gait controller; CPU-rendered control regression retained. |
| SO-101 / simulation | Three tabletop presets, rigid/deformable object configuration and joint/gripper tools. No deployed ACT or Hanoi task policy. |
| G1 / real | Disabled legacy binding; no current execution support. |
| Visual models | Curated models across five providers; key checks and robot task acceptance are separate. |

Linux is the primary hardware acceptance target. Windows native, WSL2, Apple Silicon macOS and Intel macOS require separate dependency and hardware verification; they remain experimental. Software CI does not establish USB serial, camera or accelerator compatibility.

Simulation assets are optional and excluded from the student launcher installation. Source developers can initialize the world/policy submodules and install the host's simulation extra; see [development setup](CONTRIBUTING.md) and the [SO-101 workbench guide](worlds/scenes/so101_workbench/README.md). Changing between real and simulated environments requires ending the instance.

The original 100,000-step `snack_into_box` ACT checkpoint has CPU/CUDA tensor-forward evidence, not completed physical acceptance. Its weights are not included. Software fixtures do not prove grasping. The [release acceptance guide](guides/release-acceptance.md) defines separate ACT-supervision and direct-VLM trials and fault checks. Version 1.0.0 remains gated on operator acceptance; **ultimate** names the future autonomy goal.

## Demo and interface

The G1 recording below is from an earlier simulation version. It demonstrates head-camera observation and a gait skill; it is not SO-101 hardware evidence. The instruction is “Walk to the living room.” The unedited turn plays at 2.5× speed; an [original-speed MP4](assets/readme/media/walk-to-the-living-room.mp4) is available.

<div align="center"><img src="assets/readme/images/walk-to-the-living-room.gif" alt="Earlier G1 simulation: camera observation and a gait skill used to walk to the living room" width="960"></div>

<details>
<summary>Earlier interface and world views</summary>

The model, task and device setup controls have changed in 0.6. These preserved screenshots show earlier navigation, observation and operator views. The chase camera and World Explore views are operator-only.

<div align="center"><img src="assets/readme/images/web-session.png" alt="Earlier session view" width="960"></div>
<div align="center"><img src="assets/readme/images/web-teleop.png" alt="Earlier operator controls" width="960"></div>
<div align="center"><img src="assets/readme/images/web-dashboard.png" alt="Earlier registry and session dashboard" width="960"></div>
<div align="center"><img src="assets/readme/images/world-explore/apt-scene-test.png" alt="Operator scene test in the apartment" width="960"></div>
<div align="center"><img src="assets/readme/images/world-explore/apt-cutaway.png" alt="Apartment layout for operators" width="960"></div>
<div align="center"><img src="assets/readme/images/world-explore/house-west_pool-night.png" alt="Simulated house pool terrace at night" width="960"></div>

The [world-library tools](worlds/docs/explore/README.md) cover scene testing and browser views. G1's flat-ground gait does not supply grasping, furniture manipulation, autonomous sitting or stair traversal. Scene checks do not certify autonomous task success.

</details>

## Extend and contribute

The independently installable [Runtime SDK](packages/nerv-runtime-sdk/README.md) defines provider discovery and protocol 2 lifecycle, actions and observations. An [inspection-only example](examples/inspection-runtime/) illustrates the boundary. Device SDKs stay in workers; policy backends produce standard targets without opening hardware.

See [CONTRIBUTING.md](CONTRIBUTING.md) for source environments and tests, [AGENTS.md](AGENTS.md) for module boundaries, and [CHANGELOG.md](CHANGELOG.md) for version history. Report errors with versions and sanitized reproduction steps, without keys, unreviewed camera frames or private configuration archives.

NERV uses the [MIT license](LICENSE). Vendor SDKs, model weights, training data and external assets retain their own licenses.
