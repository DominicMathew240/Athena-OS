# Athena OS computer vision implementation task list

Date: 15 November 2026. Status: proposed backlog under implementation; T01–T04 are locally verified and T05 has a locally verified draft-version foundation, but release bundles and runtime release gates remain incomplete. Under the [17 November 2026 Multimodal Platform Roadmap](2026-11-17-multimodal-platform-roadmap.md), T01–T65 remain preserved and are placed into OS Core (`T06–T08`, `T32–T40`, `T51–T65`), the Video Operations / Vision Domain Pack (`T09–T31`), and industry operations (`T41–T50`). Read the [workflow and build plan](2026-11-15-vision-workflows-build-plan.md) for evidence, screen layouts, scenario details and release criteria.

## Implementation checkpoint — 16 November 2026

The local, uncommitted working tree supports private people-counting drafts with an uploaded H.264 MP4, a saved finite counting line, and manually marked reference crossing times/directions. Reference marks persist when a draft is reopened and are invalidated when their recording or saved line geometry changes. The setup UI labels these entries as human reference labels, not Athena detections or an automatic count.

T03 adds owner-scoped `Site`, `OperationSource`, and `Operation` records and APIs. Creating an operation requires a site and a source belonging to that same site and owner; reads remain owner-filtered and operations are private. Source records contain metadata only, not URLs, credentials, or a live connection. T04 documents the current role/capability map, lets the draft API bind a new private draft to one owned operation, and adds regression checks for private scope, recording access and unavailable metric/export/publication routes. The existing draft screen is not yet wired to select an operation; guided setup and release-bundle integration remain future work.

A synthetic one-second MP4 browser flow at 390px verified upload, playback, line drawing/save/reload, two reference marks/save/reload, clearing marks after a line change or recording removal, stale-write refusal, no browser errors, and no horizontal overflow. The browser pass also checked signed-in Home and public landing capability copy. These are local workflow checks only. The recording is not run through a detector/tracker; no replay analysis, automatic count, or comparison result is produced. **T01–T04 are complete locally**: T01 passed server-rendered and browser copy checks; T02 produced a prospective metric/recipe specification; T03 adds private site/source scope; T04 binds optional drafts to owned operations, documents role boundaries, and tests owner-only access. T05 now records each successful API draft revision and recording change in a hashed immutable snapshot; required model/screen/policy/source-view version resources and the actual release-bundle service remain missing. No operation sharing, metric/runtime inference or publication is implemented.

The focused operation-scope, operation-draft, region, and role-route suites now pass **85 tests** (plus the recording suite's 9); `manage.py check`, migration consistency, Ruff on changed implementation/tests/migration, and `git diff --check` pass. The full console suite was rerun on 17 November 2026 after the repository cleanup and the recipe/policy version work: **OK, 28 skipped** (known lean-environment skips, no torch). The previously recorded failure in `projects.tests_operation_recordings` was a fixture error — it marked a reference crossing at 1.2 s on a 1-second synthetic clip and the duration validation correctly refused it — and is fixed.

Next: finish T05 by adding model/screen version references with server-verified compatibility, then the immutable release-bundle service. Later, progress toward recorded replay without fabricating detector/tracker events. Real inference/training logic remains outside this repository.


Use the IDs below in tickets and reviews. Dependencies identify the main prerequisite tasks; a phase gate also requires its supporting access, runtime and validation tasks. Owners are proposed disciplines to assign. P0 = blocks a truthful/safe core workflow; P1 = core usable delivery; P2 = expanded full-platform work. A checkbox is complete only when its acceptance evidence is attached.

## A Product foundation and existing workflow continuity

| Done | ID | Priority | Task and proposed owner | Depends on | Acceptance evidence |
|---|---|---|---|---|---|
| [x] | T01 | P0 | Reconcile current capability claims. Product + engineering. | — | Home, templates and docs distinguish photo interpretation, six-frame sampling, detector training and unsupported live operations. A novice-facing label does not promise complete counts from sparse images. Verified by `projects.tests_vision_home` and the 390px Playwright flow in `web/scripts/operation_drafts_browser.mjs`. |
| [x] | T02 | P0 | Specify metrics and recipe contracts. Product + CV engineer. | T01 | Defines entries, exits, net flow, visible presence, dwell, estimated occupancy, bay status, and coverage with examples/counterexamples; specifies recipe-compatible inputs/models and missing-data states. Evidence: [vision metric and recipe contracts](2026-11-16-vision-metric-and-recipe-contracts.md). This is a design specification, not a runtime/API implementation. |
| [x] | T03 | P0 | Add sites, sources and operation scope. Backend. | T02 | `Site`, `OperationSource`, and `Operation` models; API requires an owner-owned site and a source belonging to that site, returns owner-scoped records as private, rejects cross-owner/cross-site links, and stores no source URL or credential. Existing inspection privacy is regression-tested. Verified by `projects.tests_operation_scope` and role-route checks. |
| [x] | T04 | P0 | Extend authorization to operation capabilities. Backend + security/QA. | T03 | Owner-scoped site/source/operation and draft/recording access; drafts can bind only to one owned operation and cannot be reassigned; cross-owner/cross-site associations are rejected; unauthorized media reads do not open storage; metrics/exports/publication routes remain absent. Role limits are documented in [operation access capability map](2026-11-16-operation-access-capability-map.md). Verified by 79 focused tests including `projects.tests_roles`. This is private owner-only access, not collaborator sharing. |
| [ ] | T05 | P0 | Define versioned operation drafts and release bundles. Backend. | T02–T04 | Partial: `OperationDraftVersion` preserves successful revisions; `SourceViewVersion`, `GeometryVersion`, `RecipeVersion`, and `PolicyVersion` add immutable, hashed, owner-scoped component records, with geometry pinned to a source-view version and recipe/policy parameters mirroring the implemented `CrossingCounter` and comparison semantics. Drafts can now pin these component versions into their snapshots with server-enforced coherence (own-operation versions, line/geometry match, invalidation on line or recording change). Verified by `projects.tests_operation_versions`, `projects.tests_operation_drafts`, and the focused operation/role suite. Contract: [draft/release bundle](2026-11-16-operation-draft-release-bundles.md). Still required: model/screen version references, server-verified compatibility, and an immutable release-bundle service; no snapshot can be published. |
| [ ] | T06 | P1 | Build guided outcome setup with autosave/resume. Frontend + design. | T02–T05 | One clear next action per step; reload/resume preserves work; failed saves are visible; navigation offers an intentional discard path. |
| [ ] | T07 | P1 | Keep existing inspection and training routes discoverable. Frontend + QA. | T01, T06 | Current image import, annotation, dataset export, Teach, model review and check screens remain reachable. Task-linked help explains the relationship to operations. |
| [ ] | T08 | P0 | Add environment and dependency preflight. Platform + frontend. | T03, T06 | Show source, worker, compatible model/weights, storage and processing readiness; each blocker has an owner and actionable recovery text. Missing setup cannot look like a queued successful test. |

Gate A: demonstrate a first-time user selecting a supported outcome and resuming a draft, plus an existing user's uninterrupted image workflow.

## B Sources and calibration

| Done | ID | Priority | Task and proposed owner | Depends on | Acceptance evidence |
|---|---|---|---|---|---|
| [ ] | T09 | P0 | Import recorded video as durable source jobs. Backend + media engineer. | T03, T08 | Validate supported containers/codecs, size and duration; report decode failures; preserve recording identity, timestamps and checksum; do not silently substitute six stills. |
| [ ] | T10 | P0 | Implement one supported live camera integration. Media/platform. | T08, T19 | Installer can connect and preview an approved RTSP source; network/credential failures are distinguishable; supported codecs and network placement are documented. |
| [ ] | T11 | P0 | Protect source credentials and destinations. Backend + security. | T03–T04 | Credentials encrypted, redacted and restricted; approved network destinations and export/webhook endpoints prevent arbitrary outbound access; source tests do not reveal secrets. |
| [ ] | T12 | P1 | Add view suitability and source health. CV + frontend. | T09, T11 | Users see object size/coverage, day/night examples, frame age and obstruction limits. An unsuitable view leads to an installer action or recorded test. |
| [ ] | T13 | P1 | Build shared replay and image overlay surface. Frontend. | T06, T09 | Play/pause/scrub, scale-correct overlays, object visibility toggles and readable timestamps work without losing configuration. Original coordinates remain stable at different viewport sizes. |
| [ ] | T14 | P0 | Add finite line drawing and direction confirmation. Frontend + CV. | T02, T13 | User names a line, moves endpoints, labels sides and reverses direction; preview shows a crossing example; degenerate lines are rejected. |
| [ ] | T15 | P1 | Add accessible geometry editing. Frontend + accessibility QA. | T13–T14 | Keyboard/numeric alternatives, selected-shape text, undo/redo and clear saved/unsaved states work; no essential action requires dragging or colour recognition. |
| [ ] | T16 | P0 | Version geometry and camera calibration. Backend + CV. | T05, T14 | Rotation, aspect ratio and source transform are stored; format changes invalidate or explicitly remap geometry; likely moved views request review. |
| [ ] | T17 | P1 | Add polygon bays with stable names/IDs. Frontend + backend. | T13, T15–T16 | Draw/edit/duplicate named four-corner bays; reject self-intersections and invalid bounds; bay names can change without losing history. |
| [ ] | T18 | P1 | Add zone and disabled-bay management. Frontend + backend. | T17 | Users define areas, label unobservable bays, distinguish Disabled from Unknown and set capacity without changing historical records. |

Gate B: a recorded clip and reusable geometry survive reload and resizing; invalid views and shapes produce actionable feedback.

## C Stateful runtime and events

| Done | ID | Priority | Task and proposed owner | Depends on | Acceptance evidence |
|---|---|---|---|---|---|
| [ ] | T19 | P0 | Move operation execution into supervised durable workers. Platform + backend. | T03, T05 | Runs survive HTTP exits; leases/heartbeats detect lost work; bounded retry/cancel states are durable. Continuous operation does not depend on web-process daemon threads. |
| [ ] | T20 | P0 | Implement source time and ordered processing contracts. Media + backend. | T09, T19 | Preserve capture/recording time and ingestion time; define late frames, discontinuities and session boundaries; recorded replays are reproducible. |
| [ ] | T21 | P0 | Add compatible detector inference adapters. CV engineer. | T08, T19–T20 | Model task/label/version compatibility is validated; real inference on reference frames yields normalized outputs and explicit failures. |
| [ ] | T22 | P0 | Add per-camera object tracking. CV engineer. | T20–T21 | Tracks have camera/session scope, timestamps and lifecycle; tests cover short occlusions, reacquisition and ID changes without claiming persistent identity. |
| [ ] | T23 | P0 | Add durable event ledger and duplicate prevention. Backend. | T05, T19–T22 | Retries/replays cannot double-commit events; event identity and pinned versions are queryable; raw events can rebuild aggregates. |
| [ ] | T24 | P0 | Add checkpoints, bounded queues and gap accounting. Platform + backend. | T20, T23 | Worker restart/reconnect drills recover state or show gaps; recorded full analysis and live frame-dropping policies differ explicitly; queue overload cannot masquerade as full coverage. |
| [ ] | T25 | P0 | Implement directional crossing episodes. CV + backend. | T14, T16, T22–T24 | Finite line crossings produce direction/time exactly once per valid episode; returns count; jitter, touches, line-parallel motion and ambiguous long gaps do not produce fabricated events. |
| [ ] | T26 | P0 | Add labeled reference events and replay comparison. CV + frontend/QA. | T13, T23, T25 | People can mark expected event time/direction; comparison reports matched events, misses, extras and timing under a declared matching tolerance. |
| [ ] | T27 | P1 | Add period and shift aggregates. Backend. | T23, T25–T26 | Source-time totals reconcile with ledger; UTC storage and site time zones handle midnight, shift boundaries and daylight-saving transitions where applicable. |
| [ ] | T28 | P0 | Add guarded estimated occupancy. Backend + product. | T02, T24, T27 | Require a baseline and complete declared entrance coverage; missing doors/feeds and impossible values trigger uncertainty; net crossings remain separately named. |
| [ ] | T29 | P0 | Implement bay-to-vehicle matching. CV engineer. | T17–T18, T21 | Compare matching strategies on oblique views; ambiguity/occlusion is explicit; a large box does not automatically occupy every intersected bay. |
| [ ] | T30 | P0 | Implement temporal bay states. CV + backend. | T23–T24, T29 | Free/Occupied/Unknown/Disabled transitions use valid evidence and configured delays; stale feeds and stationary cars behave correctly; state history is reproducible. |
| [ ] | T31 | P0 | Add parking reference replay and metrics. CV + QA/frontend. | T26, T30 | Reference bay-time labels and transition times produce per-bay confusion/errors, unknown rate and transition delay; false-free cases are easy to inspect. |

Gate C: recorded people and parking replays match event/state ground truth, including the difficult conditions in scenarios 1–3 and 8–9.

## D Learning and training inside an operation

| Done | ID | Priority | Task and proposed owner | Depends on | Acceptance evidence |
|---|---|---|---|---|---|
| [ ] | T32 | P1 | Route test failures to a diagnosis. Product + CV/frontend. | T26, T31 | Users distinguish source/view, geometry, rules and recognition mistakes; correct recognition does not lead them into unnecessary training. |
| [ ] | T33 | P1 | Capture event-linked examples and negatives. Backend + CV. | T23, T32 | Examples retain recording/time/site provenance and authorized evidence; deduplicate selections; explicitly verified negatives are supported. |
| [ ] | T34 | P0 | Extend review and correction queues. Backend + frontend. | T04, T33 | Suggestions/corrections have author, time and source; accepted truth is distinct from pending proposals; sampling policy avoids overwhelming operators. |
| [ ] | T35 | P0 | Add recording/session-aware dataset splits. CV + backend. | T33–T34 | Frames from one source group cannot leak across train/validation/test; split manifest records grouping policy; augmentation happens after split. |
| [ ] | T36 | P1 | Integrate guided teaching with operation drafts. Frontend + training engineer. | T06, T08, T35 | Draft resumes after dataset review; compatible starting models shown; progress/cancellation/retry are clear; exact data/model/config versions stored. |
| [ ] | T37 | P0 | Verify real weights and training on representative data. Training/CV + platform. | T21, T35–T36 | Clean environment completes a real run with approved model artifacts and checksums; independent data evaluated; terms/setup responsibilities recorded. |
| [ ] | T38 | P0 | Compare candidate and active operation outcomes. CV + backend. | T26, T31, T37 | Fixed regression replays compare event and bay metrics by condition; score improvements cannot hide a night/occlusion regression; final test reuse is disclosed. |
| [ ] | T39 | P0 | Add approval gates for operation releases. Backend + product. | T04–T05, T38 | Approver sees all changed versions and test scope; incompatible labels, failed checks and untested conditions block or restrict release according to explicit policy. |
| [ ] | T40 | P1 | Add progressive quality explanations. Frontend + design. | T26, T31, T38 | Show “missed 2 of 40 reference entries” with denominator, footage conditions and coverage; detector score, uncertainty and business outcome are not conflated. |

Gate D: a real example correction reaches reviewed data, real training, comparison and an approved operation revision without silently changing the live release.

## E Operator screens and customization

| Done | ID | Priority | Task and proposed owner | Depends on | Acceptance evidence |
|---|---|---|---|---|---|
| [ ] | T41 | P1 | Ship default people-counting board. Frontend + design. | T25–T28, T40 | Entries/exits, named period, direction, live freshness, coverage and event evidence visible; estimated occupancy shown only when configured. |
| [ ] | T42 | P1 | Ship default parking board. Frontend + design. | T30–T31, T40 | Bay map/table, Free/Occupied/Unknown/Disabled totals and timestamps reconcile; Unknown is never included in Free; select a bay to inspect history. |
| [ ] | T43 | P1 | Add schema-validated widget and layout configuration. Frontend + backend. | T05, T41–T42 | Metric, camera, bay map, trend, event and review widgets use supported bindings; draft/published layout and reset behavior work; no arbitrary executable content. |
| [ ] | T44 | P1 | Add operator preview and role visibility. Frontend + QA. | T04, T43 | Preview respects the chosen role; editing/reordering cannot bypass data access or hide required freshness/uncertainty context. |
| [ ] | T45 | P0 | Add corrections and manual override lifecycle. Frontend + backend. | T23, T30, T34 | Correct an event/bay with reason; original result preserved; override expiry and raw/adjusted metrics visible; corrections do not automatically retrain. |
| [ ] | T46 | P1 | Add reports and authorized exports. Backend + frontend. | T27–T28, T31, T45 | Site/operation/time filters consistent; CSV/report distinguishes observed, adjusted, unknown and uncovered periods; expired evidence explained. |

Gate E: a nontechnical operator finds a result, explains its scope and freshness, traces its evidence and submits a correction from desktop or phone.

## F Expanded full-platform scope

| Done | ID | Priority | Task and proposed owner | Depends on | Acceptance evidence |
|---|---|---|---|---|---|
| [ ] | T47 | P2 | Complete classification and segmentation lifecycles. CV + frontend/backend. | T34–T40 | Task-specific labels/masks, review, dataset versions, exports, compatible training/inference and evaluation round-trip; SAM box proposals alone do not count as segmentation delivery. |
| [ ] | T48 | P2 | Add zone presence, dwell and queue recipes. CV + product. | T18, T22–T24, T38 | Define presence versus unique entries; timestamp-based duration handles irregular frames; occlusion/exit/censored visits explicit; recipe-specific replay tests pass. |
| [ ] | T49 | P2 | Add validated defect/PPE/custom inspection recipes. CV + domain reviewer. | T38, T47–T48 | Every recipe specifies visible evidence, associations, review behavior and measurement limits; domain test data supports each advertised output. |
| [ ] | T50 | P2 | Connect photo inspection feedback to learning deliberately. Backend + product. | T33–T35, T45 | User can choose to contribute authorized feedback/examples to an operation/project; creator-private reports are not exposed or imported automatically. |
| [ ] | T51 | P1 | Add shadow mode and bundle rollback. Backend + platform. | T05, T24, T39 | Compare candidate without changing live totals/actions; rollback restores model, geometry, rule and layout references; historical events retain original release. |
| [ ] | T52 | P1 | Add alerts from durable events. Backend + platform. | T23–T24, T39 | Approved channels support threshold/duration rules, cooldown, acknowledgement and retry identity; replay/shadow events do not send duplicate live alerts. |
| [ ] | T53 | P2 | Support a measured offline edge configuration. Platform. | T10, T19–T24, T51–T52 | Bounded local storage, reconnection synchronization and signed compatible releases tested; remote boards show stale status; supported hardware envelope recorded. |

Gate F: announce only recipes whose complete data-to-operation journey has passed an independent replay and an operator task test.

## G Production readiness and validation

| Done | ID | Priority | Task and proposed owner | Depends on | Acceptance evidence |
|---|---|---|---|---|---|
| [ ] | T54 | P0 | Complete access/privacy/security review. Backend/security + site owner. | T04, T11, T23, T34, T43–T46 | Evidence access, retention/deletion, secret handling, endpoint restrictions, CSRF/upload checks, audit trail and site separation verified; relevant deployment responsibilities assigned. |
| [ ] | T55 | P0 | Complete source, rule and time boundary regression suite. QA + CV/backend. | T16, T24–T31 | Covers line jitter/return, first-frame state, occlusion, resolution/view change, straddled bays, shifts, reconnect and multi-door coverage. |
| [ ] | T56 | P0 | Validate job failures and cancellation. Platform + QA. | T19, T24, T36 | Kill workers, fill disk, exhaust resources, timeout providers and interrupt uploads; UI reaches recoverable terminal states without losing committed work or duplicating actions. |
| [ ] | T57 | P0 | Demonstrate backup and restore. Platform + QA. | T23–T24, T51, T54 | Restore data, event ledger, model artifacts, layouts and configuration into an isolated environment; compare aggregates/checksums and document recovery expectations. |
| [ ] | T58 | P0 | Benchmark supported live deployment. Platform + CV/QA. | T10, T24–T31, T41–T42, T51–T52 | Record hardware, camera count, codecs, resolution, frame rate, latency, throughput, queue delay, storage and recovery at pilot and 2× load; publish supported limits. |
| [ ] | T59 | P1 | Verify dataset/model interoperability and upgrades. Training/backend + QA. | T35–T39, T47 | COCO/YOLO and supported mask/classification formats round-trip; unsupported tasks rejected; older schemas/models/layouts have tested migration or explicit refusal. |
| [ ] | T60 | P0 | Evaluate representative real footage. CV + independent reviewer. | T26, T31, T37–T40, T55 | Consented held-out clips cover day/night, occlusion, crowding and camera angles; reference labels reviewed; per-condition metrics, denominators and exclusions reported. |
| [ ] | T61 | P1 | Document installation and operator recovery. Technical writer + platform/design. | T08, T56–T58 | A fresh installer follows supported setup; operators find the next action for offline source, missed event, blocked training and rollback without developer instructions. |
| [ ] | T62 | P1 | Add telemetry for workflow health. Product + engineering. | T06, T19, T41–T46 | Privacy-conscious measurements capture setup completion, blockers, correction rate and runtime gaps; no sensitive footage/keys in ordinary usage analytics. |
| [ ] | T63 | P0 | Run formative operator sessions. Research/design. | T06, T13–T18, T26, T31, T36, T41–T45 | Recruit 8–12 relevant participants across core roles; observe scenarios 1–5 and 9; record rescue, time, mistakes and comprehension; revise and retest failures. |
| [ ] | T64 | P0 | Verify accessibility and responsive journeys. Frontend + accessibility QA. | T15, T40–T46 | Keyboard and screen-reader tasks, drag alternatives, zoom/large text and 320/390 px mobile checks pass; perform real-device checks on supported browsers. |
| [ ] | T65 | P0 | Run on-site shadow pilots and release review. Product + QA + pilot owner. | T39–T46, T51–T52, T54–T64 | Minimum two relevant deployments, one entrance and one parking site; proposed evaluation targets and recovery drills reviewed; unresolved conditions remain restricted; owner accepts supported operating envelope. |

Gate G: the supported pilot configuration satisfies the build plan's usability, accuracy, freshness, recovery, access and real-training gates. Production approval is a recorded decision based on these results, not on all boxes being ticked mechanically.

## Recommended first delivery package

Start T01–T09, T11–T16 and T19–T27 as the first people-counting slice, with T04/T05 built before sharing or publication. The visible deliverable is a saved operation draft, an uploaded recording, a named directional line, and a replay whose events can be checked by a person. Include T32/T40 explanations and the relevant parts of T54/T55/T63/T64 in this slice; do not postpone validation until the full system is assembled.

Then add T17–T18 and T29–T31 for parking replay. Add live sources and release/rollback only after recorded event semantics work. Run the guided real-training and dashboard-customization work alongside controlled pilots once shared contracts are stable. Expanded recipes, segmentation and offline edge support follow their own gates.

## Scenario coverage and completion record

| Scenario in build plan | Key tasks | Evidence required before calling it workable |
|---|---|---|
| 1 Single shop entrance | T14, T25–T28, T41, T60, T63 | Operator sets direction and reconciles a labeled clip, including returns and side-by-side people. |
| 2 Angled parking view | T17–T18, T29–T31, T42, T60 | Bay states and uncertainty match independent references under obstruction and stationary occupancy. |
| 3 Multiple entrances | T16, T24, T28, T46, T55 | Correct coverage/baseline behavior when an entrance fails or is missing. |
| 4 Installer handoff | T03–T04, T08, T10–T12, T54 | Two users finish assigned steps without credential sharing or access expansion. |
| 5 Custom recognition | T32–T40, T56, T63 | Real training and comparison resume the operation; failed jobs are recoverable. |
| 6 PPE in a zone | T48–T49, T52, T60 | Person/equipment associations and unknowns reviewed on domain footage. |
| 7 Custom board and queue | T41–T44, T48, T52, T63 | User binds correct metrics and distinguishes queue size from dwell. |
| 8 Network outage | T19–T24, T51–T53, T56–T58 | Reconnection reconciles once; stale values and storage bounds visible. |
| 9 Operator correction | T30–T31, T34, T45, T54 | Original result, override, expiry, reviewer action and adjusted totals auditable. |
| 10 Segmentation | T47, T49, T59–T60 | Mask lifecycle and calibrated interpretation demonstrated end to end. |
| 11 Mobile viewer | T04, T43–T46, T54, T64 | Accessible report tasks and negative cross-site access checks pass. |
| 12 Reject and rollback | T05, T38–T40, T51, T57–T58 | Version comparison, rejected candidate and whole-release restore demonstrated. |

For each completed task attach: responsible person, build/commit, environment/hardware, test or session evidence, observed result, unresolved limits and linked follow-up. Track current status separately from priority; no task in this proposed backlog is claimed shipped by this review.
