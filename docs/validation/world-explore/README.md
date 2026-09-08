# World interaction and Explore validation

This record covers the apt/house interaction release tested on 2026-09-08 with MuJoCo 3.12.0, the released G1 CPU policy and the local RTX 5070 Ti. Physics and model acceptance used world source `b0793444c2f5813e4a52de25e6715567436058ce`. The pool-route correction is `10c87999d861cfae2a6f07da27e36b1d89834350`; it changes only two route points, with all four generated MJCF files byte-identical. World commit `725d3b3e486ecb9ba57417ff43a68497f69262bb` additionally replaces an internal chair-processing note with a public display label. Its [comparison record](explore-final-labels.json) confirms unchanged GLBs, MJCF, routes and asset locks. Delivered world commit `dd1f24d888b2d898ec5aa6a112f96b9bc711322f` adds only bilingual README updates and three screenshots. Tests distinguish physical fixtures, the running NERV control chain and the browser. They do not establish autonomous G1 manipulation or stair climbing.

## Physical interaction

| Test | Result | Evidence |
|---|---|---|
| Appliance coverage | Every one of 22 joints reached both endpoint commands and was cancelled; G1 and Go2 scene variants | [World-library record](../../../worlds/docs/explore/verification/README.md) |
| Furniture and props | All nine free objects have centre/eccentric grip, release and cancellation traces; 51 physical trajectories, 620510 observed substeps, 72 invalid requests refused | [Full trajectories](../../../worlds/docs/explore/verification/interaction-cases.json) |
| Actual kitchen obstruction | Closed freezer drawers stopped; unlocked refrigerator/oven doors could be pushed open by their contacting drawer/rack. A carried can blocked the refrigerator door. Maximum recorded penetration: 8.464 mm, below the 10 mm limit | [World-library record](../../../worlds/docs/explore/verification/README.md) |
| Chair boundaries | Four end chairs reached real table legs; two middle chairs hit neighbouring chairs first. Six table-edge releases contacted the tabletop | [Chair boundary traces](../../../worlds/docs/explore/verification/chair-boundaries.json) |
| Refrigerator task in running NERV physics | Five consecutive physical placements passed with the original G1 standing policy, no pose pinning and no object position writes. Held and open-door states failed the predicates | [Five current CLI repetitions](interaction/fridge-five-current.json) |
| Runtime interruptions | Physical moving-door and held-can tests passed for disconnection, cancellation, reset, session freezing and world shutdown | [Current CLI lifecycle report](interaction/lifecycle-current.json) |
| Browser close | The actual browser closed while holding a free object; the lease expired and the held object was cleared | [Before](interaction/pagehide-before.json), [after](interaction/pagehide-after.json) |
| Night reset | The actual inline confirmation reset the scene and cleared task progress while retaining Night | [Browser observations](browser-coexistence.json) |

The kitchen fixtures park the robot only inside the standalone inspection harness. The five NERV task repetitions and live interruption tests use the normal G1 controller to stand. The HTTP interruption report observes lease state, object motion and process shutdown; direct force-array and damping restoration checks are recorded separately in [ray-and-cleanup.json](interaction/ray-and-cleanup.json).

Final independent review found and reproduced a second arming path in generic node configuration. Enable aliases and a delayed request could bypass scene ownership. The final handler shares the existing per-body control lock and rechecks ownership after waiting. Seven spelling cases and both ordering cases pass; emergency stop remains independent of that lock. See the [retained original failure](interaction/node-config-original-failure.json), [review and fix check](interaction/core-review.md), and [regression tests](../../../tests/test_scene_node_config.py).

A first browser carry attempt contacted the open refrigerator door and dropped the can. It correctly remained unsuccessful; [that observation is retained](interaction/browser-fridge-blocked.json). After correcting command ordering and retaining object feedback, the complete browser flow passed: focus and select the visible door, open it, grab the can, carry it in front of the open door, align with the shelf, insert it, release, settle and close. All seven physical predicates were true. The [held-inside state](interaction/browser-fridge-held-inside.json) remained unsuccessful, while the [released and closed state](interaction/browser-fridge-complete.json) completed the task.

## Actual G1 routes and stopping

The route harness calls the real NERV body tools through the Hub, HTTP/MCP and ZMQ. Layout routes supply target points to the operator acceptance program; these coordinates are not added to the brain's observation or tools. Reaching a point means its measured position falls within the declared 0.18 m tolerance. The program checks every result and the returned body state, then verifies the return to the start.

| Route | Repetitions | Maximum point error | Maximum return error |
|---|---:|---:|---:|
| apt entrance/gallery | 5/5 | 0.1430 m | 0.1134 m |
| apt living/dining access | 5/5 | 0.1790 m | 0.0845 m |
| apt kitchen access | 5/5 | 0.1638 m | 0.0803 m |
| house gate approach | 5/5 | 0.1363 m | 0.0270 m |
| house pool approach | 5/5 | 0.1451 m | 0.0318 m |
| house lawn approach | 5/5 | 0.1071 m | 0.0292 m |
| house complete pool circuit | 5/5 | 0.1594 m | 0.0421 m |
| house west/rear/east circuit | 1/1 | 0.1641 m | 0.0071 m |
| house rear-garden branch | 1/1 | 0.1531 m | 0.0356 m |

Apt's 15 trips include 405 accepted target samples. The three long house circuits include 823 body actions, without a fall or failed action. See [apt routes](routes/apt.json), [apt source and compiled native-CCD flags](routes/apt-version.json) and [house circuits](routes/house-branches.json). The [house approaches](routes/house-approaches.json) add ten complete trips and 580 accepted targets. A [source comparison](routes/house-approaches-version.json) confirms that both routes and every target remain identical after the separate pool-loop correction. Native CCD and the original 0.002 s timestep are retained. Earlier temporary collision candidates are not used for these final results.

The original pool circuit passed twice, then correctly stopped near a pergola column on a later repetition. Its front turn allowed too little clearance for the declared arrival tolerance. Only the first two points were changed to reuse the existing pool-approach corridor; geometry, policy and stopping thresholds remain unchanged. The original passes are not counted toward the revised route’s five repetitions. All five new repetitions passed, with 685 accepted target samples. Return errors are measured against each repetition’s actual starting pose. See the [complete revised run](routes/pool-corrected.json), [summary](routes/pool-corrected-summary.json), [source binding](routes/pool-corrected-source.json) and [independent review](routes/pool-corrected-independent-review.json). See the [retained failed run](routes/pool-original-failure.json), [collision diagnosis](routes/pool-corner-diagnosis.json) and [independent review](routes/pool-corner-review.json).

The narrowest tested apt doorway has 0.90 m of measured frame clearance. The entire robot crossed it in both directions without wall, frame or furniture contact. A separate 90° turn request near a clear wall segment was refused before contact; measured final rotation was about 0.45°, with stable standing three seconds later. The [final installed-tool run](hazards/confined-current.json) records every-step contact maxima. An earlier proposed starting point already intersected the entrance door, so that [invalid initial condition](hazards/confined-initial.json) remains a failed setup and is not used to claim protection worked.

Seven negative walking cases check the closed gate, pool coping, upward stair entry, downward stair edge, low coffee table, tabletop and a physically moved chair. Each uses the real gait and observes another three seconds after stopping. The chair is moved and released before the robot walks; this does not test a chair inserted into its path during a step. See [hazard summary](hazards/summary.json) and its original/final traces.

The first pool case failed: the previous 0.08 m permitted rise accepted the 0.075 m coping, and the robot fell after its action returned. The final 0.03 m limit stops before the coping. That same initial pose and movement request passed on retest. The original failure remains in [initial/report.json](hazards/initial/report.json); it is not counted as a pass. Scene geometry, policy weights and the test's spawn were not changed to obtain the retest.

## Lighting and rendering

Four time presets were applied through the production render thread. The comparison hashes show no change to positions, velocities, controls, external forces, damping, mass, inertia or simulation time during each preset mutation. Subsequent physics continues normally. The apt record contains the four inherited times; the final house record includes ten camera positions in four phases, including the west path, entrance-to-gate path, inside gate and west pool terrace.

| Measurement | Duration | Head camera | Chase camera | Simulation/wall time |
|---|---:|---:|---:|---:|
| apt, final native CCD | 300.05 s | 11.86 fps | 11.86 fps | 0.9937 |
| house | 300.05 s | 11.83 fps | 11.83 fps | 0.9996 |
| apt while browser Explore loads/focuses house | 90.04 s | 11.87 fps | 11.87 fps | 0.9999 |

Both cameras render actual 640 × 480 images through the production MJPEG route generators. These throughput measurements exclude HTTP network transport and browser image decoding. The five-minute runs meet the 10.8 fps and 0.95 real-time targets; the separate coexistence check lasts 90 seconds. Reports: [apt](performance/apt.json), [house](performance/house.json), [coexistence](performance/coexistence-apt.json), [final house phases](times/house.json), [apt and initial house phases](times/apt-and-initial-house.json). The original combined phase report also contains an earlier house lighting revision; the final house images are under `times/house/`.

## Browser guide

Actual browser checks covered the no-session default, both GLBs, room search, automatic floor selection, facility highlighting, apt 62F/63F, house three floors and courtyard, Chinese labels, collapsed navigation, orbit/zoom and cancellation of automatic camera motion. The guide uses design-state daytime assets and does not call simulation control endpoints.

Eight early-exit loading cycles and four fully loaded map-switch cycles left zero canvases on exit and one while displayed. Repeated fully loaded views retained the same geometry/texture counts. A stationary view produced no extra frames during the observed two-second interval. The source cleanup was independently reviewed for materials, textures, ImageBitmap objects, controls, listeners, ResizeObserver and pending loads.

At 1920 × 1080, measured focus transitions ran at approximately 60 fps on both maps. A separate [actual pointer-drag check](browser-drag-1080p.json) performed 40 alternating rotations per map over about six seconds: apt rendered 320 frames in 6.015 s (53.20 fps), and the house courtyard rendered 322 in 6.008 s (53.59 fps). Wall time includes automation transport. These checks cover rotation and focus, with zoom checked separately; they are not an hour-long interaction or heap benchmark. During the 90-second simulation coexistence test, house transitions measured about 59.5 fps at the browser's 1422 × 800 default size. Browser APIs did not expose a direct heap/listener-count measurement, so stable resource counts are not presented as a full heap-leak proof. See [browser lifecycle](browser-lifecycle.json) and [coexistence observations](browser-coexistence.json).

## Reproduction

Prepare external assets and generated scenes as described in the [root README](../../../README.md#installation). CPU acceptance uses `MUJOCO_GL=disable`; rendering must use the host's shared GPU queue. Acceptance tools create their own sessions and processes, then close them. They do not stop a resident NERV server.

The world-library checks and exporter are documented in [worlds/docs/explore](../../../worlds/docs/explore/README.md). The six installed [runtime acceptance tools](../../../tools/acceptance/README.md) provide commands and scope notes. Use fresh output folders under the project’s `temp/` directory. The installed refrigerator entry point passed five repetitions; lifecycle, narrow-door/turn, one apt entry route and the pool hazard were also rerun after promotion. The installed four-phase rendering tool also [passed for apt](times/promoted-apt.json) through queue 115. All six tools [refuse an existing output directory](cli-input-checks.json), and rendering refuses a missing reservation identifier before initializing the renderer.

Final regression: [160 tests passed](regression.json), including native rendering; the CPU-only subset contains 153 passing tests. The final static frontend build and all 423 locale keys pass. Publication and resident activation are recorded separately from these isolated acceptance runs.


## Resident activation

The authorized local service switch completed on 2026-09-08 through GPU queue 123, which exited with code 0. The new server on `localhost:8000` serves the verified frontend and both current Explore manifests. All 14 previous sessions, 22 stored observation images and existing logs were preserved and checked; private history and recovery backups remain local. One new apt session retains the previous brain, sensor and tool selection and remains disarmed.

Actual checks on the new page entered and exited scene testing, displayed both 640 × 480 cameras, focused the refrigerator and confirmed that an enable alias was refused while the browser held scene control. Both display models loaded, including the house courtyard and swimming-pool boundary description. Viewing house left the running apt session unchanged. Leaving Explore removed its canvas, and the test tab was closed before releasing the queue. See the [activation record](activation/report.json), [browser observations](activation/browser.json), [ownership refusal](activation/live-ownership.json) and [final state](activation/post-browser.json). The new resident remains running; the original frontend and full session backup are retained locally for recovery.
