# NERV roadmap

[中文](i18n/zh/ROADMAP.md) · [Project overview](README.md)

NERV helps people connect a robot, select a control scheme and inspect what happened during a task. Development currently focuses on a usable standard SO-101 workflow and clear boundaries between conversation, task decisions and local execution.

This roadmap describes intended work, not available features or release dates. Priorities can change after testing and community feedback. The extensions below are outside the 0.8.2 candidate. Physical acceptance and operating-system hardware support remain separate from software progress; see the [support matrix](README.md#modules-updates-and-support).

## Harness configuration and extension

**Status: foundation available; user-facing extensions planned.**

The internal Harness already manages execution loops, context, compaction and session memory. The next step is to make supported configuration and diagnostics easier to understand, document the extension boundaries, and validate an additional Harness integration.

Users should be able to inspect how an execution is managed and choose a supported integration without changing robot drivers. Completion requires a documented interface and a working additional integration that preserves independent chat and task cancellation, task records, and Host/Runtime authorization. Existing interfaces alone do not establish compatibility with arbitrary third-party agents.

## Plugin management

**Status: planned.**

NERV already has specific provider and Runtime extension interfaces. A general plugin installation and management experience is not implemented.

Planned work covers discovery, installation, compatibility checks, permission declarations and removal. A first supported plugin must complete that lifecycle and clearly report missing dependencies or incompatibilities. Plugins must use the existing authorization path for robot actions; installing a plugin must never grant motion authority. A plugin marketplace and compatibility with other applications' plugin formats are not current commitments.

## Yanshifu integration

**Status: exploration.**

The intended use is to bring course knowledge, setup guidance and relevant learning tools into the robot workflow. NERV does not currently load the Yanshifu plugin.

The next step is to review the available content, licensing and interfaces, then define an integration using the plugin boundaries above. Completion requires a documented installation path and a demonstrable guidance workflow that identifies its sources and respects tool permissions. The integration format, content coverage and supported versions have not been selected. Robot training and automatic calibration remain outside NERV's current scope.

## Contributing requirements

Use [GitHub Issues](https://github.com/Yanshi-Robotics/nerv/issues) to describe a use case or propose an extension. Include the robot, operating system, task, current obstacle and expected outcome. For a Harness or plugin proposal, include the project link, license, required permissions and the smallest useful demonstration.

Do not include API keys, private datasets or identifiable camera recordings. A roadmap entry is an invitation to discuss and validate the work, not a promise of a delivery date. See [contribution guidance](CONTRIBUTING.md) before starting an implementation.
