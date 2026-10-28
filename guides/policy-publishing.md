# Publishing NERV policies

A policy release contains the inference weights, their deployment contract, provenance,
license notices and a verification summary. Each policy has its own repository and
immutable release revision. NERV imports a specific commit and checks file hashes before
asking an installed adapter to inspect or load it.

## License scope

For new Yanshi-owned policy releases, the preferred license is **AGPL-3.0-only**.
This is a publication policy, not a license grant for files that have not passed review.
Each released repository must include its complete license text and an explicit list of
covered files. Third-party files retain their original notices and license terms.
NERV application code retains the license in its own repository.

For a release approved under these terms, use `license: agpl-3.0` in the Hugging Face
model card and state `AGPL-3.0-only` in its license notice and file coverage list.
The Hub metadata label does not replace the full license or grant an option to use a
later version. List separately licensed source and dependencies in third-party notices.

AGPL requires corresponding source when covered software is conveyed in object form.
Its network clause also applies to users interacting remotely with a modified covered
program. It permits commercial use. It does not require every independent program that
uses a service or a model output to become open source.

Weights need a separate review: copyright protection, the preferred form for modifying
a model, and the relationship between a model and its surrounding software can depend
on the material and applicable law. Do not promise that attaching AGPL to an ONNX export
forces all downstream products to disclose their code. Do not describe the ONNX file
alone as complete corresponding source without documenting that conclusion.

Before publishing, identify the editable model representation, any checkpoint needed to
modify it, the architecture, export configuration and scripts needed to rebuild the
inference artifact. Record which materials form the corresponding source, how recipients
obtain them, and any unresolved provenance or permission. A publication with unresolved
rights or missing required source stays private. A new license does not withdraw rights
already granted for older copies under a permissive license.

References: [AGPL-3.0 text](https://www.gnu.org/licenses/agpl-3.0.html),
[GNU license FAQ](https://www.gnu.org/licenses/gpl-faq.html),
[Hub license metadata](https://huggingface.co/docs/hub/en/repositories-licenses).

## Release contents

ONNX releases include `policy.onnx`, the authoritative `contract.json`, `release.yaml`,
`nerv-policy.json`, `README.md`, `LICENSE`, `checksums.sha256` and an evaluation summary.
Include corresponding source and third-party notices as established by the license review.
Keep LeRobot ACT exports in their native layout; do not rename processor or statistics files.

Use one contract as the authority for joint order, units, observations and actions. An
older summary must reference its hash and agree with its dimensions, timing and control
mode. Missing command limits remain missing; do not invent limits to complete a release.

The model card distinguishes file checks, CPU inference, simulation and physical trials.
A finite synthetic forward pass is not a walking or grasping result. Do not upload private
paths, credentials, internal audit records or unselected training artifacts.

## Publication sequence

1. Inventory the source files and their original hashes.
2. Check provenance, license scope, required source and deployment contracts.
3. Build a staging directory from an explicit file list.
4. Upload to a private repository and download the exact uploaded commit again.
5. Compare hashes and run the relevant adapter checks.
6. Publish only after the required checks pass, then create an immutable release tag.
7. Verify anonymous access and pin the commit in the official policy catalogue.

Never move a published tag or replace its weights. Changes to weights, processors,
contracts or runtime configuration require a new release. User imports retain their
selected revision when a remote branch changes.
