# Standard SO-101 release acceptance

The 0.7.1 package is a development candidate. This checklist defines release evidence; it
does not assert that physical acceptance has already passed. Run it with the installed
wheels outside the source checkout and retain the exact versions and artifact fingerprints.

## Installation and device control

On Linux, create new host and device environments using the student guide. Record Python,
OS, LeRobot, PyTorch, camera backend and inference device. Confirm that installation does
not require Node.js, simulation assets or changes to the training environment.

The operator performs device connection and all powered tests. Check missing calibration,
wrong robot ID, calibration mismatch, occupied serial port, missing/swapped cameras and
changed device indices. Camera labels establish logical names; the operator must confirm
the physical views and mounting direction. Verify fresh observations before enabling motion.

Check bounded motion, measured feedback, native target adjustment, cancellation, repeated
stop, explicit torque release and recovery. Support the arm before torque release. Verify
that releasing a hold or reconnecting does not resume an old task. Test ACT/VLM switching,
late decisions, camera loss, serial loss, inference timeout and worker termination. Record
whether a hold actually reached the driver; a lost connection cannot prove a successful hold.

## Frozen task trials

Freeze the checkpoint, preprocessing, calibration, camera configuration and scene. Select
one DeepSeek model and one OpenAI model and record their exact IDs and settings before runs.

For VLM-supervised ACT, run five trials per model. Each group must include at least one
complete success, no false success reports, and passing fault checks. For the snack-in-box
case, success requires the whole object in the box, release, and the policy returning
empty-handed to Rest. An added return command cannot satisfy that criterion.

For direct VLM control, separately run five trials per model with fixed task, initial
conditions, action budget and termination rules. Report successes, failures and operator
interventions. Success requires physical grasp, transport and release at the target.
Command completion or model narration is insufficient; the ACT-specific Rest criterion
does not apply. Report observed success rates without claiming generalization.

## Release decision

Publish the tested package versions, support matrix and known limitations after the Linux
installed-package workflow and both physical routes have been reviewed. Keep Windows,
WSL2 and macOS experimental until their USB, camera, torque and recovery behavior has been
tested on the respective OS and architecture. A green software workflow alone does not
change their hardware status.

## Three-layer and student acceptance

Verify a fixed ACT start/stop/timeout without any API key, with the visible operator page
providing supervision. Closing or hiding that page must stop dispatch after the configured
supervision deadline. Neither a chat-model change nor stopping a reply may alter the task.

Attempt out-of-group ACT calls, ACT calls in visual-direct mode, old-generation actions,
late model responses and incompatible Runtime protocols. Each must be rejected. Editing a
scheme during execution must preserve the frozen running revision. Unverified transitions
between different ACT skills must request operator intervention.

Invite at least two students who did not develop NERV. Each should install from the actual
candidate bundle, complete first setup, reopen it, repair a missing device and select a
scheme using only the public guide. Record where they needed help. These trials are pending;
a guided screen implementation is not evidence of novice usability.

Test module download interruption, cancel/retry, invalid hashes, missing assets and failed
updates. Confirm data retention and the previous host's rollback. Keep operating-system
software results separate from USB/camera and accelerator results. Do not publish a support
status based only on this checklist existing.
