# Install, update and recover

[中文](../i18n/zh/github-install.md) · [NERV](../README.md)

0.8.1 is a development release for local testing. A release bundle contains the host, Runtime SDK and LeRobot adapter wheels, dependency locks, the optional SO-101 simulation archive, installers and `release.json`. Physical acceptance and student onboarding trials are still pending.

## Install a supplied candidate

Extract the complete bundle. From its directory, Linux/macOS users run:

```bash
sh ./install.sh --bundle .
```

Windows PowerShell users run:

```powershell
./install.ps1 -Bundle .
```

The installer downloads pinned uv from its official distribution when needed, obtains Python 3.12, verifies release assets and installs hashed dependencies. Internet access is still needed for Python and dependencies. It does not reuse a training environment. Installation can be retried after a failed download. Incomplete environments never become the active host.

Open a new terminal so the user PATH change takes effect:

```bash
nerv
```

The default address is `http://127.0.0.1:8170`. Browser opening waits for readiness and is suppressed for remote/headless terminals. `nerv serve --no-browser` disables it explicitly. `nerv serve --port PORT` overrides the default. Port conflicts are reported without terminating other processes.

If a previous `nerv` command exists, the installer prints its location. Check `command -v nerv` on POSIX or `Get-Command nerv -All` in PowerShell. The managed command lives in `~/.nerv/bin`. Do not assume an older npm installation changed just because the checkout changed.

## GitHub one-command entry

These commands install the latest published development release. Read its release notes for the tested platforms and remaining acceptance work.

Linux/macOS:

```bash
curl -fsSL https://github.com/Yanshi-Robotics/nerv/releases/latest/download/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://github.com/Yanshi-Robotics/nerv/releases/latest/download/install.ps1 | iex
```

The public origin is GitHub Releases, not the npm registry. The downloaded shell/PowerShell script bootstraps the installer; wheel, module and dependency integrity checks follow the release manifest. Review the installer before executing it if required by your environment.

## Components

Use **Simulation and components** to install or reinstall SO-101 / LeRobot or SO-101 Simulation. The interface reports phase, bundle size and download progress when available. Python dependencies are additional downloads; LeRobot's tensor stack is substantially larger. The host owns no model tensors. Install jobs persist across browser refresh; interrupted jobs can be retried. Concurrent installation is refused.

Equivalent commands:

```bash
nerv runtime install lerobot0.6.1
```

```bash
nerv runtime install so101-sim
```

```bash
nerv runtime list
```

Close the robot instance before installing from the interface. The LeRobot component does not connect the arm. Simulation registration follows XML loading, stepping and two-camera rendering checks. It is not a task-success test. Importing arbitrary simulation packages is not available in the student interface.

## User data and updates

User state lives in `~/.nerv`: device profiles, schemes and immutable revisions, policy references, model settings, private credentials, session journals, installation jobs, versioned environments and caches. Source checkpoints and calibration remain at their selected locations. Explicit `NERV_HOME` chooses an alternative root; keep that override when launching again.

```bash
nerv update --check
```

```bash
nerv update
```

```bash
nerv rollback
```

The check downloads release metadata without installing it. Before update/rollback, stop the task, disconnect devices and exit the server. The host lock rejects updates while NERV is running. Update snapshots versioned configuration, installs the host and every installed official module into new environments, checks dependencies and module imports, then activates the complete set. A failure restores the previous pointers and configuration. Rollback restores that set and its configuration; credentials and event histories stay current. Configuration created after the update is preserved in the reported backup directory, including newer policy references. Original checkpoints remain untouched.

Updates coordinate installed official Runtime modules and rewrite their exact interpreter references in device profiles. Unknown modules or configuration formats block the update before activation. An interrupted update blocks startup: run `nerv rollback` to recover. This release has configuration schema 1; it refuses unknown schemas instead of attempting an unverified migration. Updates require the server to be stopped.

On POSIX, credentials use private file permissions; on Windows, the key store uses the operating system credential service. Updates do not move secrets into an installation environment. Backups containing private state stay under the user directory.

## Build a candidate

Maintainers need Python 3.12, uv, Node.js and the initialized world/policy submodules. Follow [CONTRIBUTING.md](../CONTRIBUTING.md) to install build dependencies. From the source repository:

```bash
python scripts/build_release.py
```

This builds the interface, wheels, three scene resources and hashed dependency locks in `temp/release`. It does not publish. Distribute the files listed in the manifest and the bootstrap/install scripts together, rather than only the host wheel. Retain the manifest with each tested bundle.

## npm compatibility

The existing source installation remains available for contributors with Node.js 22+, npm and uv:

```bash
npm install --global --install-links .
```

```bash
nerv
```

Launchers shipped from 0.7.1 support `nerv update` and delegate to the active release after migration. Older npm launchers do not gain new commands when a release is published: run the GitHub installer once to register the managed command, then open a new terminal. Future updates use `nerv update`. When a managed release installation exists, the current npm launcher delegates to its active host. The source fallback still builds the frontend and creates a source-fingerprinted environment; it is not the recommended student distribution. No new npm registry registration is needed.

## Platform limits

Linux is the primary software and physical acceptance target. Windows native, WSL2, Apple Silicon macOS and Intel macOS remain experimental until their dependencies and devices have been tested separately. WSL2 USB passthrough and cameras require host setup. Linux persistent camera paths, Windows DirectShow devices and macOS AVFoundation devices are enumerated without opening cameras. Where available, device identities are saved and resolved again before connection. Devices without a unique identity require visual confirmation; manual indices remain available if enumeration fails. Enumerating a serial device does not establish that its calibration or mechanics are correct.

The bootstrap uses uv's [unmanaged installation option](https://docs.astral.sh/uv/reference/installer/)
so uv itself does not modify global shell configuration. NERV registers its own command.
For a temporary install that must not edit your shell profile, the Shell installer accepts
`--no-path`; launch the printed absolute command and retain the same `NERV_HOME`.
