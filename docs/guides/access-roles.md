# Access roles: who can look, label, teach and approve

Athena has four roles. They are ordinary Django **groups**, so there are no extra tables to migrate and any
tool that edits Django users can manage them. The console enforces them on the server for every change; the
buttons you cannot use are also switched off in the page, with the reason shown, but that is a courtesy, not
the lock.

| Role (group name) | Can do | Cannot do |
| --- | --- | --- |
| **Viewer** (`Athena Viewer`) | Look at everything: every project, picture, box, snapshot, model, report and check screen. | Change anything. |
| **Labeler** (`Athena Labeler`) | Everything a Viewer can, plus: create projects; add and remove pictures; draw, edit and delete boxes; edit labels; run auto-suggest and review its suggestions; save snapshots; download (export) snapshots; run a picture through a check screen; record feedback and review decisions. | Start or stop teaching, move a model to trial, set up check screens, delete a project, approve. |
| **Trainer** (`Athena Trainer`) | Everything a Labeler can, plus: start and stop teaching; move a model to trial ("staged"); create, edit and delete check screens; delete a whole project. | Approve a model for use or go back to the previous one. |
| **Approver** (`Athena Approver`) | Everything a Trainer can, plus: **approve a model for use** and **go back to the previous model**. | — |
| **Superuser** (Django flag) | Everything an Approver can. | — |

A person who holds several of the four groups gets the highest one. A role can be given **console-wide** (it
applies to every project) or **for one project**. A role given on a project *replaces* the person's console-wide
role on that project, up or down, and on no other project. For example, someone who is a Viewer everywhere can be
a Labeler on one project, and a Trainer everywhere can be limited to Viewer on a sensitive one. Superusers are
never lowered by a project role. The role of someone with a console-wide role and no project role is unchanged.

Deleting a whole project is a Trainer action (not Labeler) on purpose: it also deletes the project's
snapshots, models and decision history.

## People who have no role yet

A signed-in user who is in none of the four groups is **unassigned**. What they can do is the setting
`ATHENA_UNASSIGNED_ROLE`:

| Value | Unassigned users can |
| --- | --- |
| `labeler` (default) | Everything a Labeler can. |
| `viewer` | Only look. |

An unassigned user can **never** approve or roll back a model, whatever the setting. That is the one
behaviour change when you upgrade from a console that had no roles, where any signed-in user could approve.
They also lose "start teaching", "move to trial", "set up check screens" and "delete a project", because
those are Trainer actions; assign the Trainer role to the people who do that work.

An unassigned user sees "No role has been assigned to you, so this console treats you as a Labeler" at the
top of the page.

## Giving roles: the access screen

Administrators (superusers) get an **Access** link in the console's left-hand menu, which opens `/access`
("Who can do what"). Nobody else sees the link, and the page and its API answer 403 to anyone who is not a
superuser. On it:

- **The four roles**, with one line each, and what an unassigned person counts as.
- **People**: one card per active account, with a search box. Each card has *Role everywhere* (a console-wide
  role, or none) and *Roles on single projects*: a role per project, added with *Give role*, changed with its own
  Save, and removed with *Remove* (which asks first; Esc keeps the role). Superusers are listed but have no
  controls, because a role would change nothing for them.
- **Record of role changes** (below): newest first, filterable by project and by person.

Saving takes effect on the person's next click; they do not need to sign in again. Changing someone's
console-wide role leaves them with exactly that one role (any other Athena group they held is removed, and the
card warns first).

The same actions exist as JSON for administrators (all need a superuser; a write that changes nothing writes
nothing and answers `"changed": false`):

| Call | Does |
| --- | --- |
| `GET /api/access` | Everyone who can sign in, their console-wide role and per-project roles, and the projects. |
| `PUT /api/access/people/<user id>/console` `{"role": "trainer"}` or `{"role": null}` | Set or clear a console-wide role. Optional `"note"` (300 characters). |
| `PUT /api/access/people/<user id>/projects` `{"project_id": "…", "role": "viewer"}` | Give a role on one project. |
| `DELETE /api/access/people/<user id>/projects?project=<id>` | Take it away: their console-wide role applies there again. |
| `GET /api/access/changes?project=<id>&person=u3&limit=50&offset=0` | The record. |

Giving a role to a superuser is refused (409): it would change nothing.

## Setting it up from the command line

Run these where the console runs (with Docker Compose: `docker compose exec web python manage.py ...`):

```bash
python manage.py athena_roles                          # creates the four groups (safe to repeat) and lists members
python manage.py athena_roles --assign alice trainer   # exactly one role per person: replaces a previous one
python manage.py athena_roles --assign bob approver
python manage.py athena_roles --revoke alice trainer   # take one role away
python manage.py athena_roles --revoke alice           # take every Athena role away (back to unassigned)
python manage.py athena_roles --project "Pallet check" --assign alice viewer   # a role on one project only
python manage.py athena_roles --project "Pallet check" --revoke alice          # back to their console-wide role there
```

A project is named by its exact name or its id (an ambiguous name is refused: use the id). Every change made
this way is written to the record with "athena_roles command" as the source.

Roles are `viewer`, `labeler`, `trainer`, `approver`. Superusers (made with `createsuperuser`) need no group.
Output from a throwaway console with synthetic users:

```text
Users in no Athena group count as: Labeler (ATHENA_UNASSIGNED_ROLE); they can never approve.
Approval needs a second person (ATHENA_APPROVAL_REQUIRES_SECOND_PERSON): off.

Approver (2): approver1, approver2
Trainer (1): trainer1
Labeler (1): labeler1
Viewer (1): viewer1
Superusers, who can do everything (1): admin1
No Athena role, so Labeler (1): plain1
```

Mistakes are plain errors, for example `There is no user named “nobody”.` and
`Unknown role “boss”. Use one of: viewer, labeler, trainer, approver.`

Role changes take effect on the person's next request; they do not need to sign in again. The `athena_roles`
listing now ends with "Roles on single projects".

## Settings (environment variables, also in `web/.env.example`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `ATHENA_UNASSIGNED_ROLE` | `labeler` | Role of a signed-in user in none of the four groups: `labeler` or `viewer`. Any other value stops the console from starting. |
| `ATHENA_APPROVAL_REQUIRES_SECOND_PERSON` | off | `1`/`true`: the person who started the teaching run for a model cannot approve that model. |

### Separation of duties (second person)

With `ATHENA_APPROVAL_REQUIRES_SECOND_PERSON=1`, approving a model whose teaching run **you** started is refused
(HTTP 403, "You started the teaching run for this model, and this console is set so that someone else has to
approve it. Ask another Approver."). This holds for Approvers **and superusers**, so a console with only one
administrator cannot approve the models that administrator trained: have a second Approver. It applies only
to *approve*; going back to the previous model is not restricted. A model with no teaching run (one brought in
from elsewhere) has no starter and is not restricted. The model's JSON names the starter with an opaque id
(`lineage.run_started_by`, such as `u3`), never a name.

## What the people using the console see

- A line at the top of the page: "Your role: Viewer. Your account can look but not change things. Ask an
  administrator for the Labeler role." (and the equivalent for the other roles).
- Controls the person cannot use are **switched off**, not hidden, with the reason shown above the area
  (and next to the main button, such as "Start teaching" or "Save a snapshot"). Approve and "Go back to the
  previous model" say which role is missing; with the second-person rule on, the model card also says "you
  started the teaching run for this model".
- A Viewer can still open a picture to look at its boxes (the button says "See boxes"); dragging on the
  picture, the label and undo controls, and the per-box buttons are off. On the check-screen setup page the
  screen picker stays on (to look at a screen) while the form and Save are off; on the operator page the file
  picker and "Check this picture" are off for a Viewer.
- The page gets this from the server (`data-role`, `data-can-label`, `data-can-train`, `data-can-approve`,
  `data-second-person` on `<body>`). Editing those in the browser changes how the page looks, not what the
  server allows.

## What the server answers

A refused call returns **403** with a plain sentence and no change is made:

```json
{"error": "Your account can look but not change things. Ask an administrator for the Labeler role.",
 "your_role": "viewer", "required_role": "labeler"}
```

Other sentences: for Labelers on Trainer actions, "Your account can label pictures but cannot teach the
computer, move a model to trial or set up check screens. Ask an administrator for the Trainer role."; for
anyone below Approver on approve and rollback, "Your account cannot approve a model for use or go back to the
previous model. Ask an administrator for the Approver role."

Not signed in is unchanged: API calls get **401** `{"error": "Sign in to continue."}` and pages redirect to the
sign-in page.

`GET /api/me` tells a client what the signed-in person may do console-wide. It contains an opaque id
(`u<id>`), never a username or e-mail address:

```json
{"id": "u1", "role": "viewer", "roles": ["viewer"], "assigned": true, "superuser": false,
 "scope": "console", "project_id": null, "project_roles": {"<project id>": "labeler"},
 "can_view": true, "can_label": false, "can_train": false, "can_approve": false,
 "can_administer": false, "approval_requires_second_person": false}
```

`role` is the highest role that applies now; `roles` lists the groups held (empty when unassigned).
`project_roles` maps a project id to the role the person was given on it alone. `can_view` is always true for a
signed-in user. `can_administer` is true only for superusers. The console page also carries the roles on
single projects (`data-project-roles` on `<body>`), so its buttons switch when a project is opened.

### Which route needs which role

Every route is declared in code with one decorator, `@access(...)`, and a test walks the whole URL
configuration and compares it with the table below, so a new route cannot be added without a decision.

| Role needed | Routes |
| --- | --- |
| Viewer (any signed-in user with a role) | Every `GET` except dataset export; the console and check-screen pages; `GET /api/me` |
| Labeler | `POST /api/projects`; `PATCH …/projects/<id>` (labels); `POST …/assets`; `DELETE …/assets/<id>`; `POST …/annotations`; `PATCH`/`DELETE …/annotations/<id>`; `POST …/autolabel`, `…/autolabel/<job>/cancel`, `…/annotations/review`; `POST …/datasets`; `GET …/datasets/<id>/export`; `POST …/inference/apps/<id>/run`, `…/observations/<id>/feedback`, `…/observations/<id>/decision` |
| Trainer | `POST …/training/runs`, `…/runs/<id>/cancel`; `POST …/models/<id>/stage`; `POST`/`PATCH`/`DELETE …/inference/apps…`; `DELETE …/projects/<id>` |
| Approver | `POST …/models/<id>/approve`, `…/models/<id>/rollback` |
| Administrator (superuser) | `GET /access`; `GET /api/access`, `GET /api/access/changes`; `PUT /api/access/people/<id>/console`; `PUT`/`DELETE /api/access/people/<id>/projects` |

For routes under `…/projects/<id>/…` the role that applies is the person's role **on that project** (their own
role there if they have one, otherwise their console-wide role). Routes with no project in them, such as creating
a project, use the console-wide role.

As a safety net, a console view that was added without a declaration answers 403 ("This page has no access
rule yet, so it is closed") instead of quietly opening. Sign-in, sign-out and `/healthz` are the only
exceptions.

## What is recorded

### Role changes

Every change of who holds which role writes one row to an append-only record (it cannot be edited or deleted
through the console): when, which person, **console-wide or which project**, the role before and after (none is
empty), who made the change, and where it was made: the **access screen**, the **`athena_roles` command**, or
**another tool**. A change to one of the four Athena groups made some other way (the Django shell, a user-admin
add-on) is caught and recorded as "another tool" with no actor. A change that changes nothing is not recorded.

People are stored in the record as opaque ids (`u3`), and projects as an id plus the name they had at the time, so
the record survives an account or project being deleted (it then shows "account removed" / "project deleted").
Names are not stored: the access screen looks up today's username for display. The record is shown on the access
screen and through `GET /api/access/changes`, to administrators only. There is no export to a file yet.

### Approvals and rollbacks

Approvals and rollbacks write the **role the person acted under (on that model's project) and their opaque user id** into the model's
history (the `ModelEvent` note), for example:

```text
Browser check: reviewed the synthetic results.
[Acted as Approver, user u4.]
```

The event also keeps the numeric actor id as before. No username or e-mail address is written into notes, API
JSON or dataset exports. The role recorded is the one the person held at that moment; if someone's role is
changed later, earlier entries are not rewritten.

## Limits and what this does not do

- There is no separate role for the people who only use a check screen: they need at least Labeler (console-wide or
  on that project) to run pictures and record decisions.
- Only superusers can assign roles. There is no "project owner" who can assign roles on their own project, and no
  role that can assign without being a superuser.
- A role on one project does not change what a person sees in the project list: every signed-in person can still
  see every project's name and open it read-only. Per-project roles limit who may *change* a project, not who may
  *look* at it. A person cannot be hidden from a project.
- The record of role changes is not exported (no CSV or signed file) and is not tamper-proof against someone with
  database access.
- No single sign-on or external identity provider: people are local Django users.
- Roles control what the console and its API allow. They do not protect the data directory, the database file
  or the server itself; anyone with shell access to those can bypass them.
- The second-person rule compares user accounts. It cannot tell if two accounts belong to one person.
- There is no export of "who approved what" yet beyond each model's history in the console and API.
- Checked in Chromium (every role at 390 px and 1440 px, axe-core with no violations on the pages touched) and, for
  the access screen, in real Firefox 158 at 1440 px and a 390 px viewport (15 scripted steps each: roles given and
  taken away, the record and its filters, a non-administrator refused, and a console-Viewer who is a Labeler on one
  project allowed to write there and refused on another). **Not run in Safari** (needs the owner to enable
  `safaridriver`) and **not run with a screen reader**. The model used in the Chromium check was a synthetic
  stand-in, not a trained detector; it exercises the approval controls, not model quality.
