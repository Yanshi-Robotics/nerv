# Import an ONNX motion policy

[中文](../i18n/zh/motion-policies.md) · [NERV](../README.md)

Install the simulation component, then open **Settings → Policy library → ONNX**.
Choose a local deployment directory containing `policy.onnx`, `contract.json` and
`release.yaml`. A downloaded Hub repository can supply this directory. NERV records
file hashes and keeps the original files unchanged.

Choose **Check** to run a CPU inference check in the installed simulation environment.
The check validates joint order, observation dimensions, gains, command ranges and finite
outputs. It does not connect hardware or demonstrate walking or task success. The current
motion controller accepts the humanoid deployment contract; downloading another robot's
weights does not install its controller.

Choose **Use this policy revision** after the check passes. The selection applies to new
instances of the matching robot and controller family. Close an existing robot instance
before changing its selection. Re-importing the directory creates a new record; an explicit
selection stays on its original revision until you select another one. Changed source
files invalidate the record and require import and checking again.

Keep the policy directory available for offline use. Native ACT exports use the ACT
import flow and keep their LeRobot processors and statistics. ONNX imports require one
observation input and one action output, with weights contained in the ONNX file;
external weight files and custom native operators are not enabled.

A local import is not a redistribution license. See the [publication guide](policy-publishing.md)
before sharing weights. Unknown-origin legacy weights are excluded from new official Hub
publication until their redistribution terms can be established.
