# Connect an already configured SO-101

NERV 0.8.4 is a local candidate. In **Your Robots**, open SO-101 setup. You need a
standard follower whose motors have already been configured and calibrated in
LeRobot. Keep its calibration JSON file and the robot ID used to create it.

1. **Check your computer.** Review the system, storage, download tool and installed
   runtime. Expand technical details when a check needs attention. System settings
   provide compatible CPU/NVIDIA installation choices.
2. **Install runtime.** Install the required component or repair an existing environment.
   The download view shows the installation location, progress and resumable jobs.
3. **Choose the arm.** Connect its USB cable and power supply, then confirm the detected
   port. Discovery reads OS metadata; it does not open the motor connection.
4. **Check prerequisites.** Choose the calibration directory and robot ID, then click
   **Check device**. NERV reads the motor models, position mode and calibration values.
   It does not write IDs, baud rates, offsets, torque settings or calibration registers.
5. **Set up cameras.** Preview and confirm the `top` and `wrist` views and their directions.
   The standard preset uses two distinct OpenCV cameras at 640 × 480 and 30 FPS.
6. **Save configuration.** Return to the robot details and connect explicitly. Connection
   can energize motors. Motion stays disabled until the operator enables it.

A missing file means NERV could not find the calibration, not that the robot was
never calibrated. Unreachable motors, a busy port or insufficient permissions leave
checks incomplete. A known mismatch blocks setup. Follow the
[LeRobot SO-101 guide](https://huggingface.co/docs/lerobot/so101) outside NERV, then
select the correct file and check again. There is no automatic calibration button.

Checks run in a separate process, can be cancelled, and stop when their page lease
expires. Returning to setup restores configuration. Changed device identity,
calibration contents or runtime receipt require another check. Older profiles are
preserved but need prerequisite evidence before connection with the new adapter.

Host, device and simulation environments are managed independently under `NERV_HOME`
(default `~/.nerv`). ACT checkpoints share the verified device inference environment;
weights and external calibration files remain read-only references. NERV does not
modify a course training environment, install system drivers or configure USB passthrough.
Future VLA plugins may request separate compatible environments; they are not included.

The operator prerequisite capability is versioned separately from the action protocol.
An older adapter without that capability must be upgraded. Passing checks establishes
setup readiness, not grasp reliability or successful physical acceptance.
