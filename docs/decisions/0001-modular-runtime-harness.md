# Modular runtime and harness

Date: 2026-10-19. Status: accepted for 0.6 development.

## Decision

Keep the host's HTTP/MCP boundary and extract a small runtime SDK into an independently installable package. Discover Python providers through package entry points in each worker environment; allow other languages to register a compatible service URL. Separate robot profile, environment binding and policy artifact. Each instance binds one robot/environment and each active control conversation has one revocable generation.

Retain the existing Brain Link and session data, while composing the default harness from a provider, context manager, compactor and host-supplied memory. Raw events remain durable; compacted model context is a derived view. Production model options come from a visual registry with explicit capability evidence and configuration-specific checks.

## Rationale

Device SDKs and tensor libraries have different dependency and process lifecycles from the host. Loading them in the host would couple equipment ownership, inference and agent failures. Independent workers keep those responsibilities together at the device boundary while letting new vendors reuse the protocol.

ROS 2 control offers a useful lifecycle model, but requiring a ROS deployment would add a second transport and packaging system to the existing SO-101 and G1 paths. The SDK therefore borrows lifecycle and capability patterns while retaining HTTP/MCP.

Replacing the entire agent stack would discard working session history, notes and logging. Small injectable boundaries address coupling and allow later replacement of memory, budgeting, compaction or the whole harness without rewriting adapters.

## Consequences

Plugins implement real preparation and resource cleanup, fresh sensor provenance, permission checks and cancellable final dispatch. Discovery is metadata only. Host credentials stay outside workers. A stop, emergency hold, release, return, disconnect and torque release have distinct semantics.

Simulation and real bindings have separate epochs, calibration and evidence. The transition contract is reserved; running transitions and automatic sim2real equivalence are excluded from this version. Historical implementations remain available for regression and history, while disabled bindings and nonvisual models stay outside production choices.

The release gate remains 0.5.0 baseline, 0.6.x development and operator physical acceptance before 1.0.0. Software and API checks cannot award physical task success.
