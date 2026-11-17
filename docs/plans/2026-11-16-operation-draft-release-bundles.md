# Operation draft versions and release-bundle contract (T05)

**Status:** Draft revision snapshots and initial source-view/geometry version records are implemented and locally verified. Complete release bundles and publication are not implemented; the current snapshots are never release-ready. This contract records the gate, not a claim that the operational components already exist.

## Versioned draft contract

- `OperationDraft.revision` is the optimistic concurrency token. It starts at 1 and increases on every successful draft edit, recording attachment, or recording removal.
- Each successful revision is stored in `OperationDraftVersion` in the same database transaction as the state change. A stale/conflicting/invalid write creates no version.
- The row records a canonical-JSON SHA-256 digest and a snapshot of the operation/site/source identity, draft geometry, recipe specification status, recording checksum/dimensions/duration, and human reference events.
- `GET /api/operation-drafts/<id>/versions` is owner-scoped, no-store, verifies each digest before returning it, and caps one response at 200 versions with `has_more`.
- Version facts cannot be changed through model `save()` or queryset `update()`. Deleting an unpublished draft intentionally removes its draft-version rows too; a published release, once implemented, must not cascade with an editable draft.

## Component-pin readiness

The snapshot has explicit slots for the components required by a future release:

| Pin | Current snapshot | Missing runtime/version evidence |
|---|---|---|
| Source view | `null` | There is no versioned source-view/calibration entity. A source row is metadata, not a camera/view configuration. |
| Geometry | `null` pin; current line is preserved as draft configuration | A geometry version linked to a source-view version does not exist yet. |
| Recipe | `null` pin; `people_counting` is marked `specification_only` | T02 is a semantic contract, not an executable/versioned recipe catalog. |
| Model | `null` | No operation-to-model compatibility binding exists. Existing project `ModelVersion` records are not automatically compatible with a time-based operation. |
| Screen | `null` | Existing `InferenceApp` screens are for uploaded-image checks; there is no versioned operation dashboard layout. |
| Policy | `null` | No versioned operation rule/retention/notification policy exists. |

Initial source-view and geometry version APIs now create immutable, numbered records with canonical SHA-256 digests. A geometry version points to an exact source-view version; both are returned only through an owner-scoped operation route. The supported source-view manifest currently captures image dimensions, quarter-turn rotation and horizontal/vertical flips; the supported geometry is a normalized counting line. These records are not yet automatically pinned into draft snapshots or bound to the uploaded recording's transform.

**17 November 2026 update:** recipe and policy version records now exist too. `RecipeVersion` stores an immutable, numbered `people_counting` rule configuration (crossing dead band, side confirmations, maximum track gap, reference-comparison tolerance) keyed by recipe and hashed canonically; `PolicyVersion` stores an immutable evidence-retention period and maximum coverage gap. Both are created and listed through owner-scoped operation routes (`POST/GET /api/operations/<id>/{recipe-versions,policy-versions}`), follow the same frozen-model/immutability rules as source-view and geometry versions, and their parameters mirror the `CrossingCounter` and comparison semantics already implemented in `projects/operations`.

**Later on 17 November 2026:** the four existing component versions can now be **pinned into draft versions**. A draft save accepts optional `geometry_version_id`, `recipe_version_id` and `policy_version_id` fields (the source-view pin is always derived from the pinned geometry version, since a geometry version is only valid with the exact source-view version it was created against). Coherence is enforced server-side: a pin must reference a version of the draft's own operation; a geometry pin is refused unless the draft's line exactly matches the pinned geometry's line; changing the line without pinning a new geometry invalidates the kept pin (exactly as it clears reference crossings); and uploading or removing a recording — which clears the line — clears the geometry pin too. Each draft revision's snapshot records the pinned ids, version numbers and digests under `component_pins`, so older snapshots keep the pins they were saved with. The `recipe`/`geometry` snapshot states now read `pinned_version` when a pin exists. Pins are configuration references only: they do not make a snapshot release-ready, and model and screen pins remain unimplemented.

Accordingly each draft snapshot has `release_ready: false`. It is a historical draft version, not a release candidate, published result, or inference configuration. No endpoint publishes or activates a release.

**Final 17 November 2026 update — T05 bundle service:** all six component pins and the append-only bundle service now exist.

- **Model pin:** `OperationModelBinding` (`POST/GET /api/operations/<id>/model-bindings`) binds one exact `ModelVersion` to an operation after server-side compatibility checks, and the check results are recorded immutably in the binding (stage must be `approved`; the model's training data must come from the owner's own project; artifact digest and labels are recorded). An incompatible model is refused with 409.
- **Screen pin:** `OperationScreenVersion` (`POST/GET /api/operations/<id>/screen-versions`) stores an immutable, validated board-layout configuration (title plus metric toggles).
- **Bundle service:** `POST/GET /api/operation-drafts/<id>/release-bundles` (Approver role to assemble; owner-scoped) builds an `OperationReleaseBundle` — an append-only record (no update, no edit, no delete) numbered per operation, storing its own full snapshot (all six pins with digests, recording checksum, reference events, draft revision) and canonical SHA-256. Assembly requires the draft to be bound to an operation, every component pin present, and a recording attached; each missing piece is named in the refusal. Later draft edits cannot alter an existing bundle, and PROTECT foreign keys refuse deleting a draft or operation a bundle was built from. A bundle is a release candidate only: publication/activation and rollback remain future audited state transitions, so nothing is live yet.

## Required immutable bundle shape

A future release bundle must include a full, immutable snapshot and digest for all of the following, all scoped to the same operation and owner:

1. `source_view`: source identity, view/calibration version, and content digest.
2. `geometry`: version and digest, explicitly bound to that source-view version.
3. `recipe`: stable recipe key, executable recipe version, and contract/config digest.
4. `model`: exact model-version identity, numeric version, artifact digest, and a server-verified compatibility result for the recipe/source.
5. `screen`: exact operation-layout version and validated configuration digest.
6. `policy`: exact version and digest for rules, coverage thresholds, evidence retention, and any supported notification behavior.

The bundle must also pin the operation/draft revision and recording identity/checksum where relevant. Every reference must resolve to an owned/authorized version; client-provided IDs or role names are not proof of compatibility. Missing, mutable, incompatible, or unverifiable references block bundle creation. A draft save must never edit an existing bundle. Publication/activation and rollback are separate audited state transitions and remain future work.

## Remaining work after T05

The versioned-draft and release-bundle contract is implemented. What remains beyond it: publication/activation and rollback as separate audited state transitions (today a bundle is only a stored candidate); a runtime that consumes a bundle to produce results (T19+); and the replay/comparison loop (T26+). Do not describe a bundle as a published operation until activation exists.
