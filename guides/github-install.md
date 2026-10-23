# Install and launch from GitHub

[中文](../i18n/zh/github-install.md)

The launcher is distributed from GitHub without an npm-registry publication. Install
Node.js 22 or later (including npm) and [uv](https://docs.astral.sh/uv/getting-started/installation/)
once. uv creates isolated Python 3.12 environments and can obtain Python when it is absent.
NERV does not install into the user's training or system Python environment.

## Install once

Replace `COMMIT` below with the published commit supplied in the release notes. A local
commit that has not been pushed cannot be installed from GitHub. The 0.6.3 launcher is a
development candidate; software installation does not establish hardware acceptance.

```bash
npm install -g "https://github.com/Yanshi-Robotics/nerv/archive/COMMIT.tar.gz"
nerv
```

The first launch installs the host and matching Runtime SDK. Later launches reuse that
environment. Open the URL printed by the server, normally `http://127.0.0.1:8000`.
To choose another port, use `nerv serve --port 8100`. Ctrl+C ends this server.

An equivalent one-command entry, without global npm installation, is:

```bash
npx --yes "https://github.com/Yanshi-Robotics/nerv/archive/COMMIT.tar.gz"
```

Use the GitHub archive URL. npm's Git-clone installation also initializes submodules,
which would fetch unrelated simulation and policy repositories. The source archive
includes the frontend sources; the npm installation builds the web interface once.
Packed npm candidates already carry the built interface. Both routes still download
normal npm/Python dependencies; this is not an offline installer.

## Install the device runtime when needed

For standard SO-101, run:

```bash
nerv runtime install lerobot0.6.1
nerv runtime list
```

This installs LeRobot and Feetech in a separate environment; the download can be much
larger than the host installation. It does not connect a serial device, calibrate or
energize a robot. PyTorch/device support must still be checked on the target OS.
Open **Device and policy setup**; when exactly one installed runtime is detected, its
provider and Python path are filled in automatically. Supply the actual serial port,
robot ID, calibration and camera configuration, then import a trained policy using the
[standard SO-101 guide](standard-so101.md).

An npx user can pass `runtime install lerobot0.6.1` after the archive URL. Subsequent
host launches discover the same runtime under the same user data directory.

## User data and updates

Normal launches require no `NERV_HOME` assignment. Both installed and source launches
use `~/.nerv` by default (the Windows user profile's `.nerv` directory).

| Content | Location under `.nerv` |
|---|---|
| Sessions, event journals and images | `memory/sessions/` |
| Logs and runtime action receipts | `logs/`, `runtime-events/` |
| Device profiles and imported policy manifests | `bodies/`, `artifacts/` |
| Model settings and capability approvals | `vlm.json`, `trust.json` |
| Managed host and device environments | `envs/` |
| Runtime installation records | `runtimes/` |
| Managed Python downloads and caches | `python/`, `.cache/` |

POSIX credentials use the private `credentials.json`; Windows credentials remain in
Windows Credential Manager. Trained weights and calibration files stay in their original
locations and are referenced by the saved profile. npm manages its installed code and
download cache using npm's own directories; these are not NERV session data.

Install a new published archive to update. Source fingerprints select a new environment;
the launcher preserves prior environments and user data. Restart the application yourself
after stopping robot execution. It does not terminate an existing server or move logs.

`NERV_HOME` remains an explicit override for isolated tests. Older source checkouts wrote
sessions and logs inside the repository; those files remain there and are not automatically
merged into `.nerv`. Back them up and migrate them deliberately, without replacing an
existing user's journals or credentials. A failed interrupted installer may leave the
empty `.installing` lock directory named in its error; remove that specific directory
only after confirming the installer has ended, then retry.

For maintainers, `npm pack --pack-destination temp/launcher-distribution` creates a local
candidate. Test or install that `.tgz` with npm/npx before publishing its source commit.
The wheel-based installation remains available for machines that do not use Node.js.
