# Install, update and recover

[中文](../i18n/zh/github-install.md) · [NERV](../README.md)

This guide describes the 0.8.5 candidate. The public installer installs the latest published
release; check [release notes](https://github.com/Yanshi-Robotics/nerv/releases) for its version.
Physical-device acceptance and student onboarding trials remain separate from software checks.

## Install NERV

Linux/macOS:

```bash
curl -fsSL https://github.com/Yanshi-Robotics/nerv/releases/latest/download/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://github.com/Yanshi-Robotics/nerv/releases/latest/download/install.ps1 | iex
```

NERV installs managed CPython, uv and application packages under your user directory's
`.nerv`. It uses standard Python virtual environments and explicit interpreter paths.
It does not install into your system Python or an existing training environment. Normal
installation does not require Git, Node.js, npm or a source checkout. Read the
[environment disclosure](environments.md) for storage, isolation and shared hardware limits.

Open a new terminal, then run:

```bash
nerv
```

The interface opens at `http://127.0.0.1:8170`. Use `nerv serve --no-browser` to keep the
browser closed, or `nerv serve --port PORT` to choose another port. NERV reports an occupied
port without stopping its owner. Remote/headless terminals do not force a browser open.

The managed launcher lives in `~/.nerv/bin`. If another `nerv` command already exists, check
`command -v nerv` on POSIX or `Get-Command nerv -All` in PowerShell. An old npm launcher may
need a one-time GitHub installation to register the managed command. There is no need to
install the development repository inside Node.js.

## Install a supplied candidate

Extract the complete candidate bundle. From its directory:

```bash
sh ./install.sh --bundle .
```

Or in Windows PowerShell:

```powershell
./install.ps1 -Bundle .
```

The installer uses pinned platform files and integrity checks. A supplied bundle contains
NERV packages and manifests, but it is not necessarily a complete offline cache: Python,
uv and third-party wheels may still need downloading. Downloaded files remain available
for retry. Incomplete environments do not become active.

The Shell installer accepts `--no-path` when shell-profile changes are unwanted. Launch
the printed absolute command and keep the same `NERV_HOME` override on subsequent runs.

## Prepare components and assets

Open **Settings → System and installation** to inspect the host system and CPU/NVIDIA plan.
Then use **Components and simulation**, or the SO-101 setup flow, to install a Runtime.
Installed components appear first. Each card exposes its path, download progress, stages,
logs and recovery actions. Installing LeRobot does not connect or energize the arm.

```bash
nerv runtime install lerobot0.6.1
nerv runtime install so101-sim
nerv runtime list
```

Downloads use a persistent per-file cache with checksums and validated range responses.
Temporary network failures retry with bounded backoff; unsupported resume or changed files
restart only the affected file. Pause and restart retain useful fragments. Download percent
measures bytes, not environment creation or verification. Installation completes only after
its checks pass. Configure concurrency, bandwidth and proxy settings in System and installation;
see [recoverable installation](recoverable-installation.md).

Scene packages are versioned separately from Python environments. Use **My assets** to
install official scenes, create personal copies or import a data-only MJCF package with a
NERV manifest. See [asset management](assets.md). Loading and rendering a scene does not
establish task success or compatibility with an arbitrary trained policy.

## Update and recover

Stop tasks, disconnect devices and exit NERV before changing versions.

```bash
nerv update --check
nerv update
nerv rollback
```

Checking downloads metadata without installing. Updating selects compatible locked plans,
reuses unchanged verified environments, stages changed ones and checks them before switching
pointers. It coordinates installed official components and their device-profile references.
Unknown modules or configuration formats block activation. A failure restores the previous
configuration and pointers. An interrupted update blocks startup until `nerv rollback`.

Credentials and event history remain user data. Recovery preserves newer configuration in
the reported backup directory rather than silently discarding it. External checkpoints and
calibration files stay at their original locations. Do not remove `.nerv` to fix an update.

## Use an isolated preview

With a 0.8.5-capable managed host, install a complete candidate into the preview channel:

```bash
nerv preview update --bundle /path/to/extracted/bundle
nerv preview
nerv preview rollback
```

Preview runs on port 8100. Its sessions, credentials, device profiles and editable assets
live under `.nerv/profiles/preview`. Stable data stays independent. Copy model configuration
explicitly if needed; later changes are not synchronized and motion authorization is never
copied. Immutable files and compatible environments can share managed storage.

## Build from source

Developers edit a Git checkout, then build outside it:

```bash
nerv preview build --source /path/to/nerv
```

The command creates a source snapshot and pinned toolchain under `.nerv`, runs checks and
produces a bundle. It does not activate the candidate. Install that bundle with the preview
command above. Initial contributor setup and CI use the bootstrap in
[CONTRIBUTING.md](../CONTRIBUTING.md). Do not create `.venv`, `node_modules`, build caches or
dependency symlinks in the author checkout.

## Platform limits

Install plans are specific to the operating system, architecture, Python ABI and compute
backend. Windows and WSL cannot share virtual environments. WSL uses Linux packages but
requires separate USB, camera and GPU forwarding. macOS uses the CPU route; no MPS support
is promised. A target without compatible binary packages is unavailable for that component.

Software checks do not validate USB permissions, camera identity, motor configuration,
calibration, torque behavior or grasp reliability. Follow the SO-101 setup guide and keep
physical connection and motion under operator control. Linux is the primary physical
acceptance target; other platforms retain their stated experimental hardware status.
