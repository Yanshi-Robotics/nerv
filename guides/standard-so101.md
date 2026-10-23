# Run a standard SO-101 with NERV

[中文](../i18n/zh/standard-so101.md)

NERV 0.6.3 is a development candidate. Its real-device adapter targets one standard SO-101 follower running LeRobot 0.6.1, with `top` and `wrist` OpenCV cameras at 640 × 480 and 30 FPS. Five arm joints use degrees; the native gripper uses 0–100. Real acceptance is pending. Model loading and software tests do not establish grasp reliability.

Train and calibrate with LeRobot before using NERV. Keep the original checkpoint, calibration and course camera configuration. The leader arm is not needed for inference. Do not run LeRobot teleoperation and NERV against the same serial port or cameras simultaneously.

## Install the candidate

The [GitHub launcher](github-install.md) supports installation followed by `nerv`, or
direct `npx` launch. It manages the two Python environments and discovers installed
runtimes automatically. The wheel installation below remains an alternative without Node.js.

Use Python 3.12 for the tested dependency baseline. Obtain all three wheels from the same candidate build. A maintainer builds these with `python scripts/build_release.py`; this creates files under `temp/release/wheels` and publishes nothing. Copy that wheel directory to the student machine as `wheels`.

Linux and macOS:

```bash
python3.12 -m venv .nerv-host
python3.12 -m venv .nerv-device
.nerv-host/bin/python -m pip install wheels/nerv_runtime_sdk-0.6.3-py3-none-any.whl 'wheels/nerv-0.6.3-py3-none-any.whl[platform]'
.nerv-device/bin/python -m pip install wheels/nerv_runtime_sdk-0.6.3-py3-none-any.whl wheels/nerv_runtime_lerobot-0.6.3-py3-none-any.whl
.nerv-host/bin/nerv serve
```

Windows PowerShell:

```powershell
py -3.12 -m venv .nerv-host
py -3.12 -m venv .nerv-device
.\.nerv-host\Scripts\python.exe -m pip install wheels/nerv_runtime_sdk-0.6.3-py3-none-any.whl 'wheels/nerv-0.6.3-py3-none-any.whl[platform]'
.\.nerv-device\Scripts\python.exe -m pip install wheels/nerv_runtime_sdk-0.6.3-py3-none-any.whl wheels/nerv_runtime_lerobot-0.6.3-py3-none-any.whl
.\.nerv-host\Scripts\nerv.exe serve
```

The device package installs `lerobot[feetech]==0.6.1`. Select the PyTorch installation suitable for your OS and inference device using its official installation instructions. Do not upgrade the training environment to install NERV. The host wheel includes the web interface; students do not need Node.js or simulation submodules.

Open the address printed by `nerv serve`. The default is `http://127.0.0.1:8000`. Candidate real bindings remain unverified: supervised acceptance requires setting `NERV_ALLOW_UNVERIFIED=1` before starting the host (`export` on POSIX, `$env:NERV_ALLOW_UNVERIFIED='1'` in PowerShell). This acknowledges an experimental route; it does not connect or arm the robot.

## Configure the device

Open **Device and policy setup**. Enter the absolute path to the device environment's Python executable and provider `lerobot0.6.1`, then read its setup options. Under **configure**, provide the serial port, robot ID, existing calibration directory, course `cameras.json`, and `cpu`, `cuda` or `mps` as the inference device. Only choose an inference device supported by your installed backend.

The calibration directory must contain `<robot-id>.json`. Camera configuration must identify two different devices named `top` and `wrist`, with the standard dimensions and FPS. Keep each mount's actual rotation from the course camera setup. NERV cannot infer whether the physical camera mounts have been exchanged from the device names alone: inspect both live images before enabling motion.

Configuration writes a user profile under `NERV_HOME` (default `~/.nerv`). It does not connect the arm. Close the current robot instance before changing its profile. Existing configurations and source checkpoints are not overwritten by a policy import.

## Import your ACT checkpoint

Under **import**, enter a new policy ID, the local `pretrained_model` directory and the demonstrated task/scene conditions. Confirm the standard course convention only if the training data used that convention. This confirmation establishes declared units and ordering when the model does not encode them; it is recorded as a user declaration, not independent evidence.

The importer requires `config.json`, `model.safetensors`, the preprocessor/postprocessor JSON files, and every tensor-state file those processors reference. It checks six standard position channels and the `top`/`wrist` image contract, then fingerprints the files. No arbitrary joint mapping or automatic unit conversion is offered. A changed checkpoint requires another check; import a new policy ID for a new version.

**inspect** verifies the stored files and contract. **check** loads the policy and native processors in the device environment without connecting hardware; it does not perform inference. For a real observation forward pass, use the disarmed check described below.

The equivalent CLI uses the same provider service:

```bash
.nerv-host/bin/nerv policy import --python /absolute/path/to/.nerv-device/bin/python --provider lerobot0.6.1 --id pick-v1 --path /absolute/path/to/pretrained_model --conditions 'Task and camera setup used during training' --standard-declared
.nerv-host/bin/nerv policy inspect --python /absolute/path/to/.nerv-device/bin/python --provider lerobot0.6.1 --id pick-v1
.nerv-host/bin/nerv policy check --python /absolute/path/to/.nerv-device/bin/python --provider lerobot0.6.1 --id pick-v1 --device cpu
```

On Windows, use the corresponding `Scripts` executables. Quote paths containing spaces. No weights are downloaded by these commands.

## Connect and run

Create a real-hardware session for SO-101. Choose **Operator only** for manual operation without API credentials, or a configured visual model for Agent control. Review the runtime's capability manifest under **Device and access** before granting access.

**Prepare device** connects LeRobot and may enable motor torque. A person must trigger it after checking the work area. NERV uses the selected calibration and refuses a mismatch instead of recalibrating. Inspect the two live views and confirm their mount identity, orientation and scene.

Load an imported policy, leave control disarmed, and select **Check policy without motion**. This runs the native ACT processors and model on current observations, returns candidate positions, and performs no actuator write. Then enable control to run the policy from the Operator Toolbox. The manual mode needs the visible operator page to keep receiving observations; closing or hiding the page eventually expires supervision and stops dispatch. The user must continue observing the physical scene.

For VLM direct control, use the bounded joint and gripper tools. Each decision refers to a recent observation and the current device instance. The runtime interpolates motion locally. Reobserve after execution; a command ending is not proof of a grasp. Cartesian placement, new robot structures and nonstandard units are outside this release.

Stop the current execution before switching between ACT and direct commands. **Emergency stop** revokes control and attempts to hold the measured pose. Releasing the stop does not rearm or replay the task. **Disconnect, keep torque** closes communication while retaining torque; **Release torque and disconnect** is a separate operator action that requires physically supporting the arm. A software stop is not a power cut; a failed serial connection cannot acknowledge a hold.

## Diagnose a failure

| Symptom | Check |
|---|---|
| No runtime provider | Use the device environment's Python, with the SDK and adapter installed there |
| Calibration missing or mismatched | Robot ID, calibration directory and LeRobot calibration procedure |
| Port unavailable | OS permissions and another program owning the serial device; NERV does not terminate other programs |
| Camera contract rejected | Names, dimensions, FPS and distinct devices in the course configuration |
| Policy rejected | Standard ACT export, original processors, shape, ordering and declared units |
| Decision expired | Obtain new observations and decide again; do not reuse an old command |
| Target adjusted or not reached | Compare requested target, LeRobot's returned target and measured position |

Detailed action receipts are local under `NERV_HOME/runtime-events`. Session images and logs may contain private scenes and paths. Review and redact them before sharing. Report the host/SDK/adapter versions, OS and architecture, LeRobot/PyTorch versions, inference device, exact error and reproduction steps. Never include API keys or an unreviewed configuration archive.

## Platform status and extensions

Linux is the primary physical-acceptance target. Windows native, WSL2 and macOS remain experimental until community hardware testing. WSL serial passthrough and camera passthrough are separate checks; consult [Microsoft's USB guide](https://learn.microsoft.com/en-us/windows/wsl/connect-usb). Apple Silicon and Intel macOS need separate dependency and hardware reports.

The host uses the Runtime SDK and never imports LeRobot. The adapter owns one native device and dispatch authority. Model runners are discovered through `nerv.policy_backends`; a backend receives a validated artifact and device string, implements `reset()`, `action(observation)` and `close()`, and returns standard native position targets without opening hardware. ACT keeps its own chunk queue. A future backend may hide its own sequence or remote-inference mechanics behind this interface, but it must not bypass freshness or dispatch validation.

SmolVLA and GR00T are extension targets, not delivered capabilities. Controller tests do not establish their real-model compatibility. See the [SDK guide](../packages/nerv-runtime-sdk/README.md) for the host/runtime boundary.
