# NERV roadmap

[中文](i18n/zh/ROADMAP.md) · [Project overview](README.md)

NERV is a robot foundation for different Agents, covering real devices and simulated worlds. The 0.9.1 local candidate introduces Projects and same-machine MCP while retaining environment, asset, policy and device management. Its [support matrix](README.md#robots-and-current-limits) distinguishes implementation from client, simulation and hardware acceptance.

The work below has no promised release date. Entries describe user value, the available foundation, the next step and evidence needed for completion. None of the future products below is delivered merely by adding an interface to 0.9.1.

Candidate 25 completed the local persistent-World gate: autonomous instance allocation, dynamic G1 membership, installed control/stopping engineering and three fresh paired apt rendezvous rounds. All six robot tasks passed fixed scoring and independent review on one Linux CPU package, without false claims, falls or robot collisions during formal navigation. Earlier failures remain separate and unchanged. This is a local candidate, not a public release; see the [qualification limits](guides/brain-core-091.md#current-local-qualification).

## Agent and Harness integration

**Status: fixed Linux CPU shared-world rendezvous passed through real local MCP; named-client qualification remains pending.**

Candidate 08 recorded 0/6 navigation successes and three false claims. Later repairs addressed stopping, observation grounding, capability readiness and installation/transport consistency. Candidate 25's separate fresh series passed six robot tasks, retaining original images, physics evidence, rejected requests and independent reviews. This bounded result does not establish compatibility with every client or task.

Users should be able to use their preferred Agent without replacing robot drivers. Brain Core provides model-free identity, observation, permission and execution services; internal model loops are retired. The external bridge adds project permissions, observations and bounded tasks.

Next: test named client versions, document configuration and observability, and validate another Harness integration. Completion requires tool discovery, image handling, continuous supervision, task/stop behavior and reconnect tests in the actual client. Protocol fixtures alone do not establish arbitrary third-party compatibility.

## Independent local Agent and remote supervision

**Status: planned.**

An independent local Agent project could own models, context and planning, connecting to NERV Brain Core and Runtime. Brain Core stays bundled with NERV. The current MCP/task boundaries provide the starting point. A remote Agent additionally needs authenticated encrypted transport, device registration and connection management.

Next: qualify external Agent deployment and remote threat/connection model. Completion requires reconnect without action replay, explicit budgets and stopping conditions, and local failure handling when the cloud or model is unavailable. Being always online must not be described as complete autonomy. Neither product is included in 0.9.1.

## Real and simulation transfer

**Status: exploration.**

The long-term direction includes Real2Sim, Sim2Real and multi-stage transfer. Projects, immutable asset/policy revisions and separate environment bindings provide a base for traceable comparisons. Reserved project types do not currently run transfer workflows.

Next: select one robot/task, define calibration and observation correspondence, and establish evaluation criteria. Completion requires reproducible transforms, deployment-contract checks and separate simulation and physical results. A scene loading or a matching action dimension is not evidence of successful transfer.

## Plugin management

**Status: planned.**

NERV has specific provider and Runtime extension interfaces. Users would benefit from discovery, installation, compatibility checks, permission declarations and removal through a consistent workflow. A general plugin marketplace and compatibility with other products' plugin formats are not implemented.

Next: validate the full lifecycle of one supported plugin, including missing dependencies and incompatible versions. Completion requires a documented manifest and permission boundary. Plugins must pass through Host/Runtime motion authorization; installation must never grant control automatically.

## Yanshifu integration

**Status: exploration.**

The proposed integration brings course knowledge, setup guidance and related learning tools into robot workflows. NERV does not currently load the Yanshifu plugin.

Next: review content, licenses and interfaces, then select the integration format. Completion requires an installable guidance workflow that identifies its sources and respects permissions. Content coverage and supported versions remain undecided. Training and automatic calibration remain outside the current product scope.

## Broader robot and policy support

**Status: adapter foundations available; validation and provenance work ongoing.**

SO-101 and G1 paths provide starting points; Go2 still needs a complete verified deployment contract before motion can be offered. VLA, WAM and traditional planning have reserved extension contracts, not delivered general backends.

Next: obtain reproducible policy metadata, confirm distribution rights and validate stopping/supervision per adapter. Completion requires compatible installation plans, declared sensor/action semantics, repeatable simulation checks and separate hardware acceptance where applicable. Multi-robot coordination remains future work.

## Contribute a use case

Use [GitHub Issues](https://github.com/Yanshi-Robotics/nerv/issues) to describe the robot, operating system, task, current obstacle and expected result. Integration proposals should include the project link, license, required permissions and a small useful demonstration. Follow [contribution guidance](CONTRIBUTING.md).

Do not include API keys, private datasets or identifiable camera recordings. Roadmap discussion can change priorities; it does not certify a feature or commit a release date.
