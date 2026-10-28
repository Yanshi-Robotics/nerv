# Candidate distribution

`constraints/` fixes third-party versions separately for the host, LeRobot and simulation.
The build resolves platform markers and creates hashed install locks. NERV wheel hashes
come from the current build, so rebuilding a candidate cannot reuse stale wheel hashes.

The development CLI provides `nerv preview build --source <checkout>`. This command is
not available in the currently published 0.8.1 installer. It snapshots indexed source
files and their worktree edits under `NERV_HOME/builds`, records submodule commits and
file hashes, then builds in that snapshot. Untracked files must be reviewed and added
to the index to become inputs. Dependencies, credentials and runtime data are excluded.

`build-tools.json` pins Node distribution hashes for each target. `build.lock` pins the
Python build tools and their package hashes; the frontend uses its existing npm lock.
The build uses a managed Python 3.12 interpreter and checks the bootstrap uv version.
Build environments and caches stay under managed storage. These are development tools,
not changes to the application's runtime dependencies or a hardware-support claim.

Each build retains `build.json`, a build log and an extracted `bundle/` directory.
Successful packaging creates `nerv-VERSION-bundle.zip`. A successful build does not
install, activate, publish or restart NERV. Inspect failures before explicit cleanup.
The CI entry point uses the same managed builder. Reviewed compaction retains recent
builds and all installed references; see CONTRIBUTING.md for retention limits.
Linux simulation regression fixtures restore missing data from pinned official asset
packages into the snapshot. They never copy ignored files from the author checkout.

Students can use the extracted bundle's Shell/PowerShell installer after candidate
validation. Keep hardware and student acceptance separate from CI and package checks.
