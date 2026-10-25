# Recoverable installation

[中文](../i18n/zh/recoverable-installation.md) · [Installation guide](github-install.md)

This feature belongs to the unreleased 0.8.3 candidate. Public installation commands continue to install the published version until a release is approved.

## Choose an environment

Open **Settings → System and installation**. NERV inspects the computer running its host process: operating system, process architecture, Python ABI, Linux libc, WSL/container status and NVIDIA driver metadata. The browser's operating system does not select packages. Detection failure is reported separately from an absent GPU.

Automatic selection uses a compatible CPU plan unless a validated NVIDIA plan and compatible driver are available. CPU and CUDA 13.0 plans retain the release's exact dependency versions. Manual selection cannot override a known architecture, libc or driver conflict. Apple Silicon requires macOS 14 or newer and uses the CPU route; this release does not promise MPS, AMD or Intel GPU acceleration. Intel macOS is unavailable with the current locked dependencies. A platform without compatible binary dependencies remains unavailable.

Changing the selection affects future installations. Close the robot instance and pause active installation jobs first. Existing environments and running tasks retain their selection. Updating an installed NVIDIA environment preserves its backend; it does not silently change to CPU.

The command line reads the same settings:

```bash
nerv system
nerv system --compute cpu
nerv runtime plan lerobot0.6.1
nerv runtime install lerobot0.6.1
```

The Shell installer accepts `--target linux-x86_64 --compute cpu` (or a compatible macOS target). PowerShell accepts `-Target windows-x86_64 -Compute cpu`. Both inspect the running process before accepting an override. Host installation always uses CPU dependencies.

## Read the progress

The download bar covers Python, dependency wheels and component resources in the selected plan. Expand **Download files** for individual files, retry reasons and retry times. Cached bytes count toward the total; transfer speed excludes the bytes present when the job started. Estimates can change when a server requires a file to restart.

Downloading, checksum verification, environment creation, dependency installation and environment checks are separate stages. A full download bar does not mean installation has finished. Only a checked environment becomes active. Expand **Installation location** to inspect its directory. Live logs refresh while installation runs, and scrolling upward pauses automatic scrolling.

The default is four single-connection downloads with no additional aggregate bandwidth limit. Settings can reduce concurrency or set a total limit in bytes per second. A retry reduces new transfers to one at a time. Each file allows five automatic retries, bounded exponential backoff and a 15-minute cumulative retry-wait budget. Connections time out after 60 seconds; a transfer with no data for 180 seconds is retried. Server retry delays are respected within that budget.

## Pause and continue

A brief interruption retries automatically. **Pause** and **Cancel** stop this job's transfers and retain valid fragments. Closing or refreshing a browser does not stop the background installer. Exiting NERV leaves unfinished jobs paused; reopening does not resume them automatically.

Continue from the component card, or use its job ID:

```bash
nerv runtime list
nerv runtime pause JOB_ID
nerv runtime resume JOB_ID
nerv runtime cancel JOB_ID
```

The CLI remains attached during installation; Ctrl+C requests a pause. Repeating a resume request does not create another worker. For first installation or an interrupted update, rerun the same installer/update command with the same bundle and data directory. Verified files and usable fragments are reused.

Resume checks the response range, file length and available server validators. A server that ignores ranges causes that file to restart safely. Every completed file must match the published SHA-256 before installation. Certificate, access, disk-space and persistent checksum errors stop the operation instead of repeatedly downloading an unusable file.

## Storage and network

`NERV_HOME` defaults to `~/.nerv`. Installation preferences, system reports, job records, environments and the SHA-256 download cache remain under that directory. Existing uv caches stay intact; their private partial-file format is not converted. Training environments and source policy directories remain unchanged.

The managed downloader fetches from the official GitHub, Python package and PyTorch origins in the release plan. Configure an optional proxy in Settings, or provide standard proxy environment variables before starting NERV. Saved proxy credentials are not returned by the settings API. They are not forwarded to models or Runtime management commands.

After all artifacts pass verification, uv installs explicit local wheels with online resolution and implicit Python downloads disabled. Environment checks must pass before switching the active reference. Keep the cache to support an offline reinstall of the same plan. An offline request with a missing artifact stops and reports the missing cache condition.

The first installer needs curl (curl.exe on Windows), archive extraction and a SHA-256 utility. Its generated platform seed downloads pinned Python and uv with curl resume and checksum checks before Python can run NERV's download manager. The small release entry scripts and manifest are fetched separately. Missing system tools require operator installation; NERV does not change the OS package manager or driver.

## Candidate package targets

| Target | Host | LeRobot | SO-101 simulation |
|---|---|---|---|
| Linux x86-64, glibc 2.28+ | CPU | CPU / CUDA 13.0 | CPU rendering |
| Windows x86-64 | CPU | CPU / CUDA 13.0 | Platform plan resolved; execution unverified |
| Apple Silicon, macOS 14+ | CPU | CPU | Platform plan resolved; execution unverified |
| Intel macOS | Unavailable with current pins | Unavailable with current pins | Unavailable with current pins |

These rows describe artifact availability. They do not raise the hardware support level.

## Validation limits

Platform dependency resolution, installation checks and hardware acceptance are separate results. A resolved Windows or macOS plan does not establish camera, USB or motor support. CUDA metadata compatibility does not establish successful GPU inference. Real device connection and movement remain operator actions. Read the candidate's validation report before using it for acceptance.
