# Operation access capability map (T04)

Status: locally verified, uncommitted. This map describes the current private operation API, not a future sharing or runtime design.

## Role capability map

| Current console role | Operation-scope capability | Boundary / not included |
|---|---|---|
| Viewer | Read site, source, operation, draft, and recording metadata where the requesting user owns the row. | Cannot create or mutate. No cross-owner access, even when a record ID is known. |
| Labeler | Viewer access plus create private site/source/operation records; create, edit, or delete own drafts; upload/remove own recordings; add/edit/remove operator-provided reference marks. | Those marks are human labels, not detections. No sharing, inference, metric result, export, or publication. |
| Trainer | All lower console-role capabilities, plus the existing project-training capabilities. | No operation-specific training route exists. Project training permission does not grant another user's operation data. |
| Approver | All lower console-role capabilities, plus existing model approval/rollback capabilities. | No operation release/publication route exists. Model approval is not operation publication. |
| Superuser | Passes role checks. | Private operation querysets still scope by owner; administrator status is not a cross-owner data-sharing grant. |

Operation routes use the console-wide role: they do not contain a `project_id`, so `ProjectRole` does not replace that role on these routes. Row ownership is a separate, mandatory scope check; a role never widens an operation queryset.

## Enforced data boundaries

- A `Site` and `OperationSource` are private to their creator. An operation can be created only from a site and source owned by that same user, with the source attached to that site.
- Operation list/detail queries are owner-scoped. Foreign IDs return not-found rather than revealing existence.
- Drafts are creator-scoped and may be explicitly bound to one owned operation at creation; the binding is included in draft responses and cannot be reassigned. Legacy/unbound drafts remain creator-private. Recording metadata/content and upload/removal are reached through the draft and check its creator before processing storage.
- Metrics, exports, operation sharing, and publication routes are not implemented. Requests to the anticipated operation paths return 404; they do not return placeholder data.
- Inspection history retains its existing creator privacy. No operation capability implicitly shares inspections.

## Verification

`projects.tests_operation_scope` now checks cross-owner site/source/operation isolation, cross-site associations, draft read/update/delete denial, operation-binding ownership and immutability, recording upload/removal/content denial (including that unauthorized content requests do not open storage), and absence of metric/export/publication paths. `projects.tests_roles` walks declared routes and enforces the role required for every write. Together with the operation-draft and region suites, 79 focused tests passed on 16 November 2026.

This policy intentionally has no collaborator grant path. If sharing is added, introduce an explicit operation-scoped grant model and audit trail, then authorize each nested queryset by both capability and operation ID; do not relax the owner filters globally.
