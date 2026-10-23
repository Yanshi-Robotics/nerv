# Run a standard SO-101 with NERV

[中文](../i18n/zh/standard-so101.md)

NERV 0.8.0 is a development candidate. Its real-device adapter targets one standard SO-101 follower running LeRobot 0.6.1, with `top` and `wrist` OpenCV cameras at 640 × 480 and 30 FPS. Five arm joints use degrees; the native gripper uses 0–100. Real acceptance is pending. Model loading and software tests do not establish grasp reliability.

Train and calibrate with LeRobot before using NERV. Keep the original checkpoint, calibration and course camera configuration. The leader arm is not needed for inference. Do not run LeRobot teleoperation and NERV against the same serial port or cameras simultaneously.

## Guided device setup

Install the [candidate bundle](github-install.md), start `nerv`, and select **My robot → Register SO-101**. The guided steps replace manual interpreter paths and camera JSON editing:

1. Install **SO-101 / LeRobot** in its independent environment.
2. Scan serial candidates and select the follower. Scanning does not open the motor bus.
3. Select an existing calibration or browse to its directory and enter the robot ID. Choose CPU, CUDA or MPS only when supported by the installed backend.
4. Preview the two camera devices, assign top/wrist and rotation, and confirm their physical roles. Linux offers persistent device paths; other systems currently require camera indices.
5. Save configuration and return to My robot. Policy import is a separate next step in Settings → Policy library.
6. Review the supervised-acceptance notice and connect. Connection can enable torque; motion remains disarmed.

No hidden environment variable is required for the normal setup flow. Acceptance acknowledges an unverified integration; it does not change its support status. The preview process releases its cameras before the native LeRobot instance takes ownership. Preview and runtime never intentionally open the same camera together.

The calibration directory must contain `<robot-id>.json`. Missing calibration or disagreement with motor calibration blocks operation. NERV uses `connect(calibrate=False)` and does not overwrite calibration. Close other programs that own the serial port or cameras.

Profiles live under `~/.nerv`. Saved USB serial identities are resolved again on connection; ambiguous identities are refused. Devices without unique IDs still require operator confirmation. Return to the affected setup step if a camera or port changes. Reopening restores settings, not motor connection or motion authorization.

## Import your ACT checkpoint

Under **import**, browse to the local `pretrained_model` folder, enter a new policy ID and describe the demonstrated task/scene conditions. Confirm the standard course convention only if the training data used that convention. This confirmation establishes declared units and ordering when the model does not encode them; it is recorded as a user declaration, not independent evidence.

The importer requires `config.json`, `model.safetensors`, the preprocessor/postprocessor JSON files, and every tensor-state file those processors reference. It checks six standard position channels and the `top`/`wrist` image contract, then fingerprints the files. No arbitrary joint mapping or automatic unit conversion is offered. A changed checkpoint requires another check; import a new policy ID for a new version.

**inspect** verifies the stored files and contract. **check** loads the policy and native processors and performs a CPU/CUDA/MPS inference probe on explicitly synthetic inputs without connecting hardware. It verifies six finite outputs, not visual correctness or grasp success. It does not download backbone initialization weights. For a real observation forward pass, use the disarmed check described below.

The equivalent CLI uses the same provider service:

```bash
nerv policy import --python /absolute/path/to/runtime/bin/python --provider lerobot0.6.1 --id pick-v1 --path /absolute/path/to/pretrained_model --conditions 'Task and camera setup used during training' --standard-declared
nerv policy inspect --python /absolute/path/to/runtime/bin/python --provider lerobot0.6.1 --id pick-v1
nerv policy check --python /absolute/path/to/runtime/bin/python --provider lerobot0.6.1 --id pick-v1 --device cpu
```

On Windows, use the corresponding `Scripts` executables. Quote paths containing spaces. No weights are downloaded by these commands.

## Connect and run

Open **My robot → Device and fixed policy workspace** for operation without API credentials. Create an Agent chat from Home with a configured model. Review the runtime's capability manifest under **Device and access** before granting access.

**Prepare device** connects LeRobot and may enable motor torque. A person must trigger it after checking the work area. NERV uses the selected calibration and refuses a mismatch instead of recalibrating. Inspect the two live views and confirm their mount identity, orientation and scene.

Load an imported policy, leave control disarmed, and select **Check policy without motion**. This runs the native ACT processors and model on current observations, returns candidate positions, and performs no actuator write. Then enable control, create a **Fixed policy** scheme in **Control schemes**, select the imported skill, and click **Run selected policy** in the workspace. A policy group selects a decision model and its allowed ACT skills; visual direct control selects bounded primitives. The fixed-policy mode needs the visible operator page to keep receiving observations; closing or hiding the page eventually expires supervision and stops dispatch. The user must continue observing the physical scene.

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

Camera candidates use Linux device paths, Windows DirectShow or macOS AVFoundation metadata. Unique device identities are saved where available. Missing or ambiguous identities return setup to the affected step; identical model names never establish a camera role. Use the two previews to confirm top/wrist and direction. Folder browsers select calibration and policy directories on the host computer without uploading their contents. Native OS permission and hardware behavior still need platform-specific acceptance.

The host uses the Runtime SDK and never imports LeRobot. The adapter owns one native device and dispatch authority. Model runners are discovered through `nerv.policy_backends`; a backend receives a validated artifact and device string, implements `reset()`, `action(observation)` and `close()`, and returns standard native position targets without opening hardware. ACT keeps its own chunk queue. A future backend may hide its own sequence or remote-inference mechanics behind this interface, but it must not bypass freshness or dispatch validation.

SmolVLA and GR00T are extension targets, not delivered capabilities. Controller tests do not establish their real-model compatibility. See the [SDK guide](../packages/nerv-runtime-sdk/README.md) for the host/runtime boundary.
