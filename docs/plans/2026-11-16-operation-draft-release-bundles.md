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

**17 November 2026 update:** recipe and policy version records now exist too. `RecipeVersion` stores an immutable, numbered `people_counting` rule configuration (crossing dead band, side confirmations, maximum track gap, reference-comparison tolerance) keyed by recipe and hashed canonically; `PolicyVersion` stores an immutable evidence-retention period and maximum coverage gap. Both are created and listed through owner-scoped operation routes (`POST/GET /api/operations/<id>/{recipe-versions,policy-versions}`), follow the same frozen-model/immutability rules as source-view and geometry versions, and their parameters mirror the `CrossingCounter` and comparison semantics already implemented in `projects/operations`. They are executable parameter records only: no runtime consumes them yet, and they are not automatically pinned into draft snapshots.

Accordingly each draft snapshot has `release_ready: false`. It is a historical draft version, not a release candidate, published result, or inference configuration. No endpoint publishes or activates a release.

## Required immutable bundle shape

A future release bundle must include a full, immutable snapshot and digest for all of the following, all scoped to the same operation and owner:

1. `source_view`: source identity, view/calibration version, and content digest.
2. `geometry`: version and digest, explicitly bound to that source-view version.
3. `recipe`: stable recipe key, executable recipe version, and contract/config digest.
4. `model`: exact model-version identity, numeric version, artifact digest, and a server-verified compatibility result for the recipe/source.
5. `screen`: exact operation-layout version and validated configuration digest.
6. `policy`: exact version and digest for rules, coverage thresholds, evidence retention, and any supported notification behavior.

The bundle must also pin the operation/draft revision and recording identity/checksum where relevant. Every reference must resolve to an owned/authorized version; client-provided IDs or role names are not proof of compatibility. Missing, mutable, incompatible, or unverifiable references block bundle creation. A draft save must never edit an existing bundle. Publication/activation and rollback are separate audited state transitions and remain future work.

## Remaining T05 work

Still implement executable recipe and policy versions; connect exact approved models and versioned screens through compatibility checks; then build the append-only bundle service/API and prove that changes to a draft or source cannot alter an existing bundle. Source-view/geometry records are foundations only: until every required pin and check exists, do not call a draft snapshot a release bundle or report publication readiness.
