# Athena Open Source API reference

This reference describes the public Python surfaces of the Athena SDK and CLI.
All code is interface or schema-only unless explicitly labeled as a reference
implementation.

## `athena_sdk.contracts`

Versioned Core contracts. All models inherit from `AthenaContractModel`, which
sets `extra="forbid"` so unknown fields are rejected at validation time.

| Class | Purpose |
| --- | --- |
| `Observation` | Immutable capture or derived input. |
| `Asset` | Physical asset or component. |
| `Model` | Identified model artifact with stage and checksum. |
| `AssetStateFinding` | Domain-neutral asserted or unknown asset state. |
| `Evidence` | Retained supporting material by reference. |
| `DomainPolicy` | Versioned domain knowledge (labels, severity map). |
| `RecommendedAction` | Traceable policy output tied to a finding. |

Enums: `ContractVersion`, `Modality`, `ModelStage`, `Severity`.

## `athena_sdk.client`

| Class | Purpose |
| --- | --- |
| `AthenaClient` | Top-level composition root over an `InferenceClient`. |
| `InferenceClient` | Abstract surface: Observations in, `AssetStateFinding`s out. |

## `athena_sdk.registry`

| Class | Purpose |
| --- | --- |
| `ModelRegistry` | Abstract surface for registering and resolving `Model` metadata. |
| `ModelHandle` | Protocol for an opaque loaded-model handle. |

## `athena_sdk.interfaces`

| Class | Purpose |
| --- | --- |
| `ModelAdapter` | Interface every model implementation implements. |
| `AdapterCapabilities` | Static capability declaration for an adapter. |
| `AdapterInterfaceVersion` | Version of the adapter interface contract itself. |
| `Runtime` | Abstract runtime that dispatches observations to adapters. |
| `LocalRuntime` | Reference in-process runtime implementation. |
| `DomainPack` | Interface that maps findings to recommended actions. |
| `assert_adapter_conforms` | Standard conformance battery for any `ModelAdapter` implementation. |
| `AdapterConformanceError` | Raised by `assert_adapter_conforms` on the first contract violation found. |

## `athena_sdk.evaluation`

| Class | Purpose |
| --- | --- |
| `EvaluationHarness` | Abstract harness: adapter + labeled dataset → result. |
| `LabeledDataset` | Dataset of labeled `Sample`s for evaluation. |
| `Sample` | Single labeled observation. |
| `EvaluationResult` | Metric values, sample count, and timestamp. |
| `Metric` | Abstract metric interface. |
| `ExactMatchMetric` | Simple reference metric implementation. |

## `athena_cli`

Entrypoint: `athena`

Commands:

| Command | Description |
| --- | --- |
| `athena validate-contracts` | Round-trip validate the seven Core contracts. |
| `athena run-example` | Run the bundled synthetic example pipeline. |
| `athena run-audio-example` | Run the bundled acoustic noise-analysis example pipeline. |
| `athena knowledge add <file-or-folder>` | Index local `.txt`, `.md`, and `.faq` files. |
| `athena knowledge list` | List indexed local documents. |
| `athena knowledge search <words>` | Find keyword-matched passages with file-and-line citations. |
| `athena knowledge remove <path>` | Remove a document and its indexed passages. |
| `athena --version` | Print CLI version. |

The knowledge commands store source text in a local SQLite database by
default (`~/.athena/knowledge.sqlite3`) and accept `--database` to select a
different location. Search returns matching source passages; it is not
semantic retrieval or AI-generated answering.

## Console HTTP API: the Teach flow additions

Everything above is the SDK. The console's own JSON routes are listed in `web/projects/urls.py`; this section documents
only what the one-button Teach flow added or changed (2026-10-23, prototype). Every route needs a signed-in session and
the role named in `@access(...)` (`POST …/training/runs` needs Trainer). Errors are `{"error": "plain text", …}`. Example bodies were
captured on 2026-10-23 from a disposable local console with 60 synthetic pictures and one model that learned almost nothing
(a one-epoch run from random weights); ids are real values from that instance. They show the **shape**, not what a real
project would score. Tests: `web/projects/tests_data_health.py`, `tests_results_plain.py`, `tests_training_auto.py`,
`tests_training_outcomes.py`, `tests_console_settings.py`, `tests_operating_points.py`, `tests_training_dfine.py`, and, for the improve loop,
`tests_feedback_import.py`, `tests_autolabel_feedback.py`, `tests_comparison.py` and `tests_api_common.py`. The sections on the
download switch and on operating points (below) were checked by calling the endpoints with Django's test client on a throwaway database
(2026-10-23); every score, count and percentage in those examples is **synthetic** (generated boxes with a known score distribution,
`synthetic_split` in `tests_operating_points.py`), not a measurement of any real model. The improve-loop sections at the end of this
part (corrections, `comparison`, shared request rules) were checked the same way on 2026-10-24 (three generated check-screen results with
feedback, one sideways JPEG; the comparison counts come from `make_block` in `tests_comparison.py`, hand-built, so they are **synthetic**
and show the shape and the verdict rules, not how any model performs).

### `GET /api/projects/<id>/datasets/health` (any role; read-only, writes nothing)

View: `project_dataset_health` (`web/projects/dataset_views.py`); logic: `web/projects/datasets/health.py`. Optional split
query parameters are the ones `GET …/datasets/readiness` takes. It counts **accepted** boxes only, as a snapshot would.

| Key | Meaning |
|---|---|
| `labels[]` | Per label: `label`, `accepted_boxes`, `pictures`, `verdict` (`enough` / `few` / `none`), `message`, `expected_boxes` (`train`/`val`/`test`), `held_out_verdict`, `held_out_message` |
| `pictures` | `total`, `with_boxes`, `without_boxes`, `needs_review_boxes` |
| `held_out` | `would_have_test_pictures`, `expected_counts`, `split_config`, `message` |
| `near_duplicates` | `groups` (lists of picture ids), `group_count`, `pictures_involved`, `groups_crossing_splits`, `identical_groups`, `identical_pictures_involved`, `method` (`dhash-9x8`), `hamming_threshold`, `checked`, `not_checked[]` (`asset_id`, `reason`), `not_checked_count`, `compared`, `message` |
| `ready_to_teach` | `true` when `blockers` is empty |
| `blockers`, `advice`, `reasons` | Plain sentences. `reasons` = `blockers` followed by `advice` |
| `priority_notes` | The one or two advice items worth reading first (for example a label with no boxes, or near-duplicates split across learning and test) |
| `headline` | `{"level": "not_ready" \| "ready_with_warnings" \| "ready", "text": …}` |
| `thresholds` | The named constants behind the verdicts: `min_train_boxes_enough` 30, `min_test_boxes_readable` 30, `min_test_boxes_floor` 10, `min_test_pictures` 30, `hamming_threshold` 6, `hash_bits` 64, `max_mean_grey_difference` 32, `max_pictures_hashed` 2000, `max_hash_seconds` 4, `max_picture_pixels` 25000000 |

The thresholds are advice, not measured rules (see the header of `health.py`). The near-duplicate check was tried only on
generated pictures; it does not catch rotated, mirrored or noticeably re-framed copies. Real response (lists shortened):

```json
{
  "pictures": {"total": 60, "with_boxes": 60, "without_boxes": 0, "needs_review_boxes": 0},
  "held_out": {
    "would_have_test_pictures": true,
    "expected_counts": {"train": 49, "val": 6, "test": 5},
    "split_config": {"seed": 0, "train": 0.8, "val": 0.1, "test": 0.1, "split_algorithm": "hash-bucket-min1-v2"},
    "message": "5 pictures would be held back for the final test - fewer than 30, so a few mistakes can swing the score."
  },
  "ready_to_teach": true,
  "blockers": [],
  "priority_notes": [
    "3 groups of nearly identical pictures would be split between learning and the final test, which would make the score look better than it is. Keep one picture from each group before you rely on the score.",
    "'tear' has the fewest examples: 34 boxed examples, 29 of them to learn from. Aim for at least 30 to learn from."
  ],
  "headline": {"level": "ready_with_warnings", "text": "Ready to teach, but read the warnings below first."},
  "labels": [
    {
      "label": "dent", "accepted_boxes": 40, "pictures": 31, "verdict": "enough",
      "message": "'dent' has 40 boxed examples (35 to learn from, 5 held back for checking and scoring), enough to start with.",
      "expected_boxes": {"train": 35, "val": 3, "test": 2},
      "held_out_verdict": "few",
      "held_out_message": "Only 2 'dent' examples would be held back for the final test. One or two mistakes would swing the score; about 30 are needed for a number worth reading."
    }
  ],
  "near_duplicates": {"group_count": 6, "pictures_involved": 45, "groups_crossing_splits": 3, "identical_pictures_involved": 0,
                      "method": "dhash-9x8", "hamming_threshold": 6, "checked": 60, "not_checked": [], "not_checked_count": 0, "groups": ["…"]},
  "thresholds": {"min_train_boxes_enough": 30, "min_test_boxes_readable": 30, "min_test_boxes_floor": 10, "min_test_pictures": 30,
                 "hamming_threshold": 6, "hash_bits": 64, "max_mean_grey_difference": 32, "max_pictures_hashed": 2000,
                 "max_hash_seconds": 4, "max_picture_pixels": 25000000}
}
```

(`advice`, `reasons` and the `message`/`compared` strings of `near_duplicates` are omitted for length. A label with no accepted box
is `verdict: "none"` but is still advice, not a blocker: `blockers` holds only the problems that would also stop a snapshot, such as no accepted box,
boxes outside their picture, or missing or changed picture files; see `health.build`.)

### `GET /api/projects/<id>/models/<model_id>/results/plain` (any role; read-only)

View: `model_results_plain` (`web/projects/results_views.py`); logic: `plain_results` in `web/projects/training/report.py`.
A model of another project is a 404. Everything is derived from the evaluation stored on the model; nothing is recomputed.

| Key | Meaning |
|---|---|
| `evaluated`, `reason_if_not` | `false` plus a reason when the model has no held-out evaluation (then `overall.verdict` is `cannot_tell`) |
| `counts` | `test_pictures`, `test_boxes` |
| `labels[]` | `label`, `test_boxes`, `found`, `missed`, `wrong_boxes`, `sentence` |
| `overall` | `found`, `of`, `wrong_boxes`, `verdict`, `headline` (always ends with "Not a final decision. This is the computer’s opinion. A person decides."), `reasons[]` |
| `misses` | `misses_available`, `reason`, `items[]`, `capped_at` (24), `total` (boxes), `no_longer_in_project`, `pictures` (`total`, `shown`, `exact`), `distinct_pictures` |
| `confidence_used`, `iou_used` | The operating point the counts were taken at (0.5 and 0.5 in the example) |
| `limits[]` | The plain-language limits stored with the evaluation |
| `operating_points` | `{available: false, reason}` for a model checked before this existed (or whose tool could not score the pictures), otherwise `{available: true, …}` plus the stored block described under [Operating points](#operating-points-evaluationoperating_points-and-the-plain-keys) below |
| `wording` | `{available, reason, split, buckets[]}`: one measured sentence per score range (`split` is `"Final test pictures"`); `{available: false, reason, split, buckets: []}` when nothing is measured |
| `comparison` | How this model did against the model in use on the same Final test pictures: `{available: false, reason}` or `{available: true, …}`; see [`comparison`](#comparison-how-a-new-model-did-against-the-model-in-use) below |

`overall.verdict` is one of `good_enough_to_try`, `needs_more_examples`, `not_ready`, `cannot_tell`. The rules are in `report._verdict`
and the constants `RECALL_FLOOR_NOT_READY` 0.5, `RECALL_FLOOR_GOOD_ENOUGH` 0.85, `PRECISION_FLOOR_GOOD_ENOUGH` 0.7 and
`MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT` 30 are the owner's to change; they are not measured. No wording calls a model accurate or safe.

`misses.items[]` has one entry per box (not per picture), grouped by picture: `asset_id`, `kind` (`missed` = a person-drawn box the model did
not find, `false_alarm` = a box the model drew that matches no person-drawn box), `label`, `score` (`null` for a miss), `box`
(`[x, y, width, height]` as fractions of the picture, the same convention as the label editor). `misses_available` is `false`, with a
`reason`, for a model checked before per-picture outcomes were stored (`held_out_outcomes` in `evaluation.py`; only evaluations made after
this change carry them; older rows still give counts), when the stored list does not match the stored counts, or when none of the pictures
is still in the project (`no_longer_in_project` counts the ones deleted since).

Real response for the almost-empty model (`misses.items` cut from 19 to 3; the rest is verbatim):

```json
{
  "model_id": "7bd4b411-b007-4bb2-88a9-d98af6087870",
  "evaluated": true,
  "reason_if_not": "",
  "counts": {"test_pictures": 11, "test_boxes": 19},
  "labels": [
    {"label": "dent", "test_boxes": 5, "found": 0, "missed": 5, "wrong_boxes": 0, "sentence": "Found none of 5 “dent” boxes; drew no extra boxes."}
  ],
  "overall": {
    "found": 0, "of": 19, "wrong_boxes": 0, "verdict": "not_ready",
    "headline": "Not ready: it found none of the 19 test boxes. Not a final decision. This is the computer’s opinion. A person decides.",
    "reasons": ["It found 0 of 19 test boxes (0%) and drew no extra boxes.", "Only 5 test boxes for “dent”; about 30 per label are needed before a result means much."]
  },
  "misses": {
    "misses_available": true, "reason": "", "capped_at": 24, "total": 19, "no_longer_in_project": 0,
    "pictures": {"total": 11, "shown": 11, "exact": true}, "distinct_pictures": 11,
    "items": [
      {"asset_id": "421763dd-dfb7-4bfa-a867-cd9e336e20b5", "kind": "missed", "label": "dent", "score": null, "box": [0.3531, 0.6219, 0.2062, 0.2062]},
      {"asset_id": "421763dd-dfb7-4bfa-a867-cd9e336e20b5", "kind": "missed", "label": "scratch", "score": null, "box": [0.5813, 0.5406, 0.3063, 0.3063]}
    ]
  },
  "confidence_used": 0.5,
  "iou_used": 0.5,
  "limits": ["These numbers describe only the held-out test images listed above. They say nothing certain about other cameras, lighting, products or time periods.", "…"]
}
```

A `false_alarm` item looks the same with a `score` such as `0.95`. No example of a real `good_enough_to_try` result exists: no model on the test machine got there.

### `POST /api/projects/<id>/training/runs` (Trainer): changed request

New key `"snapshot": "latest"` (the only accepted value; anything else is 400, and sending it together with `dataset_id` is 400).
`dataset_id` and explicit `config` requests behave as before (`tests_training_auto.py`, `test_without_snapshot_the_old_behaviour_and_messages_are_unchanged`).

```json
{"snapshot": "latest", "backend": "torchvision-fasterrcnn", "config": {"quality": "quick"}, "acknowledge_pretrained_terms": true}
```

- With `snapshot: "latest"` the server reuses the latest dataset version if it holds exactly the current accepted work, otherwise it freezes a
  new one (default 80/10/10 split) and starts the run on it, in one transaction: any refusal rolls the new snapshot back too (`lifecycle.start_run_on_latest_snapshot`).
- Unless the caller sets them, the config defaults to `"pretrained": "auto"` and `"quality": "quick"` (`lifecycle.snapshot_default_config`), and only for a backend that can pick its own start.
- `config.pretrained` may now be `"auto"`: the web process resolves it to `coco`, else `imagenet_backbone` (whichever can run: cached and checksum-verified, or downloads on). **It never resolves to `none`.** If neither can run the answer is **409** and nothing is written, no snapshot either. The stored run config holds the resolved value, plus `pretrained_requested: "auto"`.
- `config.quality` is `"quick"` or `"better"`. It fills in `epochs` and `batch_size` when the caller did not set them; the names Athena chose are recorded in `config.derived`. A caller cannot send `derived` or `pretrained_requested` (400).
- The licence tick (`acknowledge_pretrained_terms: true`) is still required for any ready-made start; missing, it is 400 with `needs_acknowledgement: true`.
- Explicit choices still work: `"pretrained": "none"` queues a from-scratch run (the 409 above guards only `auto`). That start found 0 of 144 test boxes on synthetic shapes ([model-quality.md](guides/model-quality.md)), and the console's "Details for your administrator" fold still offers it.

Real 409 when nothing ready-made can run (the default state of a fresh install, downloads off):

```json
{"error": "No ready-made starting model can be used on this computer right now: none has been downloaded before, and downloading is switched off. In Athena’s one test run, a model started from nothing found none of the test objects, so Athena will not start one for you. Only an administrator can fix this. An administrator can switch this on in the console, under “Access”, in “Ready-made model downloads” … (steps as in `start_from.how_to_enable` below)",
 "start_from": {"resolved": null, "available": [], "reason_if_none": "No ready-made starting model can be used … Only an administrator can fix this.", "how_to_enable": "An administrator can switch this on in the console, under “Access”, in “Ready-made model downloads” (it needs an administrator account …) …"}}
```

202 body: `{"run": <run, with logs/metrics/runtime_info/events>, "snapshot": {…}, "worker_online": false}`. The new `snapshot` object is built by
`snapshot_data` in `training_views.py`. A real 202 captured with the explicit from-scratch route (no ready-made weights exist on the test machine, so a
`pretrained: "auto"` 202 could not be captured; the shape is the same):

```json
{
  "run": {"id": "7e85e870-71a4-4164-800b-3a8867f8c17c", "status": "queued", "backend": "torchvision-fasterrcnn", "progress": 0.0,
          "config": {"pretrained": "none", "epochs": 24, "batch_size": 4, "learning_rate": 0.005, "seed": 0, "threads": 0, "quality": "quick", "derived": ["batch_size", "epochs"]},
          "dataset": {"id": "ea32d11a-b2bf-449b-9ab6-68f938a5d38d", "version": 1, "manifest_sha256": "90c8d62d…"}, "…": "other run fields"},
  "snapshot": {"id": "ea32d11a-b2bf-449b-9ab6-68f938a5d38d", "version": 1, "newly_created": false, "image_count": 60, "box_count": 109,
               "split_counts": {"train": {"images": 39, "boxes": 74}, "val": {"images": 10, "boxes": 16}, "test": {"images": 11, "boxes": 19}},
               "created_at": "2026-10-23T06:36:25.828388+00:00"},
  "worker_online": false
}
```

### `GET /api/projects/<id>/training/readiness`: additive keys

The existing keys (`ready`, `blockers`, `warnings`, `backend`, `worker`, `dataset`, `active_run_id`) are unchanged and `ready` still means the old
explicit-dataset rule. Three keys were added (`lifecycle.readiness`, `_one_click_preview`):

| Key | Meaning |
|---|---|
| `start_from` | `resolved` (`"coco"`, `"imagenet_backbone"` or `null`), `available[]` (the usable options: `value`, `title`, `summary`, `licence_plain`, `available`, `ready`, `needs_acknowledgement`, `unavailable_reason`, `how_to_enable`, `size_note`), `reason_if_none`, `how_to_enable` (the administrator's steps; the same text as `AUTO_NONE_STEPS` in `torchvision_frcnn.py`). `null` for a backend that cannot pick its own start |
| `derived` | `train_pictures`, `estimate_for_start`, `seconds_per_picture_epoch` (0.21 for COCO), `estimate_note`, `quick` and `better` (each `epochs`, `batch_size`, `estimated_minutes`; `estimated_minutes` is `null` when no start can run), `based_on` (`latest_snapshot` / `new_snapshot` / `null`) |
| `one_click` | `supported`, `ready`, `blockers[]` (data problems first, then the start), `warnings[]` (not blockers; for example that the new snapshot would leave no held-out pictures), `notes[]` and `files_rechecked`, `snapshot` (`latest_version`, `up_to_date`, `would_create_new`) |

`notes` and `files_rechecked` are about how far the preview looked. Each poll of this route re-checks picture files for at most
`READINESS_FILE_CHECK_SECONDS` (2) seconds (`dataset_service.analyse(..., file_check=2)`, `web/projects/training/lifecycle.py`), so on a big project some
files are not opened. Then `files_rechecked` is `false`, `notes` holds `FILES_NOT_RECHECKED_NOTE` (“The picture files were not re-checked for this
preview, to keep the page quick. Missing or changed files are checked when you start.”) and `ready: true` means only “nothing that was looked at is wrong”.
The start itself (`POST …/training/runs` with `snapshot: "latest"`, and `create_version`) always checks every file. When every file was checked, `notes` is `[]`
and `files_rechecked` is `true`. Both cases were observed on 2026-10-24 on the improve-loop probe project (14 generated pictures): normally `notes: []` and `files_rechecked: true`; with the
module clock (`_clock`) patched to run past the 2-second budget, `files_rechecked: false` and the note above (`ready` was `false` in both only because the helper was not running).

Real body on the instance above (downloads off, nothing cached, no helper running):

```json
{
  "start_from": {"resolved": null, "available": [],
    "reason_if_none": "No ready-made starting model can be used on this computer right now: none has been downloaded before, and downloading is switched off. In Athena’s one test run, a model started from nothing found none of the test objects, so Athena will not start one for you. Only an administrator can fix this.",
    "how_to_enable": "An administrator can switch this on in the console, under “Access”, in “Ready-made model downloads” (it needs an administrator account; the Trainer and Approver roles cannot). A choice made there wins over the setting below. The other way is … (full text: `AUTO_NONE_STEPS`; once a console choice exists, `auto_none_steps(\"console\")` says it was switched off in the console and the environment setting is ignored)"},
  "derived": {"train_pictures": 39, "estimate_for_start": null, "seconds_per_picture_epoch": null,
              "estimate_note": "This is an estimate, not a promise. It is worked out from timing on one laptop-class processor (an Apple M3) while other programs were running, …",
              "quick": {"epochs": 24, "batch_size": 4, "estimated_minutes": null},
              "better": {"epochs": 40, "batch_size": 4, "estimated_minutes": null},
              "based_on": "latest_snapshot"},
  "one_click": {"supported": true, "ready": false, "blockers": ["No ready-made starting model can be used …", "An administrator can switch this on in the console …"],
                "snapshot": {"latest_version": 1, "up_to_date": true, "would_create_new": false}}
}
```

When a start can run, `start_from.resolved` is `"coco"` (or `"imagenet_backbone"`), `available[]` lists the options, and `estimated_minutes` is a whole number
(rounded up, at least 1) from the constants in [model-quality.md](guides/model-quality.md#how-quick-and-better-are-worked-out). That case was **not** captured from
a real helper run; the option fields are read from `start_options` in `torchvision_frcnn.py`.

### `GET /api/console/training-downloads` and `PUT /api/console/training-downloads`: the download switch

Views: `training_downloads` (`web/projects/settings_views.py`); logic: `web/projects/console_settings.py`; stored in the `ConsoleSetting`
row `training_downloads` and one append-only `ConsoleSettingEvent` per change (migration `0009_console_setting_and_event.py`). **The web
process never downloads anything.** The route only stores whether the teaching helper (`manage.py train_worker`) may; the helper reads the
setting from the same database each time a run starts and each time it reports for duty.

| Method | Who may call it | Anonymous |
|---|---|---|
| `GET` | Trainer and above (`@access(GET="train")`) | 401 `{"error": "Sign in to continue."}` |
| `PUT` | a **superuser** only (`@access(PUT="admin")`), whatever other role they hold | 401 |

A Viewer or Labeler `GET` is 403 `{"error": "Your account can look but not change things. Ask an administrator for the Trainer role.",
"your_role": "viewer", "required_role": "trainer"}` (the role screen's own wording). A `PUT` by anyone who is not a superuser is 403 with this
setting's own sentence: `{"error": "Only an administrator can change this setting.", "your_role": "trainer", "required_role": "administrator"}`.

**Precedence** (`training_downloads()` in `console_settings.py`):

1. a stored console choice, on or off, wins: `source: "console"` (so an administrator can also switch downloads **off** while the
   environment says on);
2. otherwise the environment variable `ATHENA_TRAINING_ALLOW_DOWNLOAD`, when set to any non-blank value (`1`/`true`/`yes` mean on, anything
   else off): `source: "environment"`;
3. otherwise off: `source: "default"`.

There is no route, field or value that removes a stored console choice, so **it cannot be cleared back to `"environment"`**; it only moves
between `on` and `off`. If the database cannot be read (for example a helper started without one) the environment decides.

`GET` returns (real body, fresh database, no variable set, as a superuser; the `licence` texts are shortened here, they are the same
sentences the Teach panel shows):

```json
{
  "allow": false,
  "source": "default",
  "stored": null,
  "environment": {"variable": "ATHENA_TRAINING_ALLOW_DOWNLOAD", "set": false, "allow": null},
  "can_change": true,
  "summary": "Downloading ready-made models is switched off, the built-in default. No one has chosen yet.",
  "precedence": "A choice made here wins over the setting ATHENA_TRAINING_ALLOW_DOWNLOAD in the helper’s environment, in both directions. The environment setting is only used until someone chooses here.",
  "fetched_by": "The teaching helper (the program started with `python manage.py train_worker`) fetches the files, once, from download.pytorch.org when a run needs them. This page only records whether it may: the console itself never downloads anything.",
  "licence": {
    "general_terms": "Ready-made models come with their own terms, separate from the free software that trains them. …",
    "options": [{"value": "imagenet_backbone", "title": "Start from a general picture model (ImageNet)", "licence_plain": "…"},
                {"value": "coco", "title": "Start from a general object model (COCO)", "licence_plain": "…"}],
    "backend_note": "Code: torchvision and PyTorch, BSD-3-Clause. … (the torchvision backend's `license_note`, unchanged)"
  }
}
```

`stored` is `null` or `{"allow": bool, "changed_at": ISO time}`. `environment.allow` is `null` while the variable is unset. `can_change` is true
for a superuser. `fetched_by` names download.pytorch.org because that is where the default tool's files come from; the optional D-FINE
tool's files come from `huggingface.co` and `*.hf.co` (see below).

`PUT` body (unknown keys are 400 `Unsupported field: …`):

| Key | Type | Meaning |
|---|---|---|
| `allow` | boolean, required | `true` to allow downloads, `false` to forbid them. A string such as `"yes"` is 400 |
| `acknowledge_terms` | boolean | Must be exactly `true` to switch **on** (the administrator's own licence tick, once). Not needed to switch off |
| `note` | string | Plain text. Required for **on** (at least 3 characters after trimming); at most 300 characters either way ("The note is too long (300 characters at most)."); optional for off. Control characters, bidi overrides and the like are refused ("The note contains characters that cannot be saved.") |
| `based_on` | boolean, optional | What the caller last saw as `allow`. If it differs from what is in force now the answer is **409** and nothing changes (two administrators cannot overwrite each other unseen) |

Errors are `{"error": "plain sentence"}` plus, for 409, the current `allow` and `source`. Real answers:

| Call | Status | Body |
|---|---|---|
| `PUT {"allow": true, "note": "ok for pilot"}` (no tick) | 400 | `{"error": "To switch ready-made model downloads on, tick the box saying you have read the licence note, and write a short note saying why (at least 3 characters)."}` (same for a tick and no note) |
| `PUT {"allow": false, "bogus": 1}` | 400 | `{"error": "Unsupported field: bogus."}` |
| `PUT {"note": "x"}` | 400 | `{"error": "Say whether to allow downloads: “allow”: true or false."}` |
| `PUT {"allow": "yes"}` | 400 | `{"error": "“allow” must be true or false."}` |
| `PUT {"allow": false, "based_on": "yes"}` | 400 | `{"error": "“based_on” must be true or false: what you saw when you opened the page."}` |
| `PUT {"allow": false, "based_on": false}` while downloads are on | 409 | `{"error": "Someone changed this setting while you had the page open (downloads are now on), so nothing was changed. Reload the page to see it, then choose again.", "allow": true, "source": "console"}` |

A successful `PUT` returns the same body as `GET` plus `changed` and `event`. Real answer to
`{"allow": true, "acknowledge_terms": true, "note": "Owner reviewed the licence note for the pilot.", "based_on": false}` (other keys as in the
`GET` above, `licence` omitted):

```json
{
  "allow": true,
  "source": "console",
  "stored": {"allow": true, "changed_at": "2026-10-23T10:55:39.307206+00:00"},
  "environment": {"variable": "ATHENA_TRAINING_ALLOW_DOWNLOAD", "set": false, "allow": null},
  "summary": "Downloading ready-made models is switched on, by an administrator in the console.",
  "changed": true,
  "event": {"id": 1, "when": "2026-10-23T10:55:39.307206+00:00", "by": "u1", "role": "administrator",
            "before": "off", "before_source": "default", "after": "on", "terms_acknowledged": true,
            "note": "Owner reviewed the licence note for the pilot."}
}
```

Sending the value already in force (after a console choice exists) is `200` with `changed: false, event: null` and writes nothing. The
first choice, even one equal to what the environment already says, always counts as a change (it makes the console the source). `event.by`
is an opaque `u<id>`, never a username or email. With the variable set to `1` and a console choice of off, a `PUT {"allow": false, …}`
answered `allow: false, source: "console", environment: {set: true, allow: true}`: the console choice wins.

### `GET …/training/backends`: `downloads` and the D-FINE tool

`downloads` is `{"allowed": bool, "reported_by": "setting" | "helper" | "console"}`, built by `downloads_for` (`torchvision_frcnn.py`):

| `reported_by` | Where the answer came from |
|---|---|
| `setting` | the stored console switch (it wins and is read live from the database, so a helper report up to ten seconds old cannot hide it) |
| `helper` | no console choice; the running helper's own report of its environment |
| `console` | no console choice and no helper report: the web process's own environment, a guess (the Teach panel says so) |

(Older text of this file listed only `helper | console`; the first row is new with the download switch.) Real body, no helper running,
console choice made earlier: `{"default": "torchvision-fasterrcnn", "worker": {"online": false, "loading": false}, "downloads": {"allowed": false, "reported_by": "setting"}, "backends": […]}`.
Each backend's `start_options` are returned by `training_backends` in `training_views.py` (they predate the Teach flow; the console's data-health and plan
sentence read them). Every entry in `backends[]` has: `name`, `description`, `license_note`, `constraints`, `dataset_format`,
`default_config`, `availability`, `available_in_web_process`, `worker_reports_available`, `start_options`.

**`dfine-coco`** (opt-in; `web/projects/training/dfine.py`, registered in `settings.TRAINING["BACKENDS"]`; the default stays
`torchvision-fasterrcnn`). Real `default_config`: `{"epochs": 10, "batch_size": 4, "learning_rate": 0.0001, "seed": 0, "threads": 0, "size": "nano", "pretrained": "coco"}`;
`dataset_format` is `yolo`. Config keys accepted by `POST …/training/runs` with `"backend": "dfine-coco"` (`DFineBackend.validate_config`; any other key is 400
`Unsupported training setting: …`):

| Key | Accepted | Notes |
|---|---|---|
| `size` | `"nano"` (default) or `"small"` | Picks one of the two pinned COCO-only checkpoints. The checkpoint itself cannot be named: `checkpoint`, `repo_id`, `model`, `revision` or `weights` is 400 ("The checkpoint cannot be chosen by name…") |
| `pretrained` | `"coco"` (default) or `"auto"` (becomes `coco`) | `"none"` and `"imagenet_backbone"` are 400 ("pretrained must be 'coco' for the D-FINE tool…"): there is no from-scratch start for this tool |
| `epochs` | integer 1 to 100 | |
| `batch_size` | integer 1 to 16 | |
| `learning_rate` | number above 0, at most 1 | A huge integer is a plain 400, not an error page |
| `seed` | integer 0 to 2147483647 | |
| `threads` | integer 0 to 64 | 0 = let the library choose |
| `quality` | `"quick"` or `"better"` | Fills `epochs` and `batch_size` when not given; recorded in `config.derived`. Targets 400 and 600 optimiser steps (`QUALITY_PLANS`) |
| `pretrained_requested`, `derived` | filled in by Athena | A caller cannot send them (400) |

The one-click body `{"snapshot": "latest", "backend": "dfine-coco", "config": {"quality": "quick"}, "acknowledge_pretrained_terms": true}` works the
same way as for the default tool (the default config for this backend is `{"pretrained": "auto", "quality": "quick"}`, `tests_training_dfine.py`). The licence
tick is required (400 with `needs_acknowledgement: true` without it). It uses the **train split only**; the validation split is used afterwards for the
operating points. `start_options` has one entry (`value: "coco"`, `needs_acknowledgement: true`, `size_note` “About 15 MB (nano) or 41 MB (small). It is
downloaded once, from huggingface.co and *.hf.co …”); `available` is false and `unavailable_reason` says downloads are off until the switch above (or a cached,
checksum-verified copy) makes it true. `license_note` is the long text in `dfine.py` (`LICENSE_NOTE`); its checks are repeated in
[`docs/LICENSE_AUDIT.md`](LICENSE_AUDIT.md) §5. `availability` names the packages that are missing when `torch`, `transformers` or `scipy` is not installed in
the worker environment. Numbers measured with this tool are in [model-quality.md](guides/model-quality.md#d-fine-the-optional-second-tool), labelled synthetic.

### Operating points (`evaluation["operating_points"]`) and the plain keys

Stored by the training worker after evaluation (`work_out_operating_points` in `training/runner.py`; optional and additive: a run never fails because of it,
and a model made before this existed simply has no key). Logic and the rounding rules are documented at the top of
`web/projects/training/operating_points.py`; the plain-language side is `plain_operating_points`, `wording_table`, `confidence_wording` and
`suggested_threshold` in the same file. Nothing here calibrates a score and nothing changes a live model: a person still chooses every setting.

What is stored (schema `athena.operating_points/1`; everything JSON, additive):

| Key | Meaning |
|---|---|
| `source`, `bucket_source` | `"validation split"` (thresholds are picked on the Checking pictures) and `"test split"` (the score ranges are measured on the Final test pictures) |
| `records`, `ground_truth_boxes`, `pictures`, `boxes_per_label` | What the checking split held: scored predictions, drawn boxes, pictures, drawn boxes per label |
| `prediction_floor`, `iou_threshold` | Predictions are kept down to a score of 0.05 and matched to drawn boxes at IoU 0.5, same label (the same matching function as the test numbers) |
| `recall_ceiling` | The most drawn boxes any cut-off could find on the checking split; a target above it has `reachable: false` |
| `minimum_boxes`, `enough_data`, `why_not` | `{total: 400, per_label: 30}`; `enough_data` is true only with at least 400 drawn boxes on the Checking pictures **and** at least 30 for every label **and** a Final test split with drawn boxes; `why_not` is the plain reason when false |
| `test` | `{pictures, ground_truth_boxes, records}` or `null` when there were no Final test pictures |
| `bootstrap` | `{by: "picture", resamples: 1000, seed: 20261023, level: 0.9}`: 90% ranges come from re-drawing whole pictures, not boxes |
| `suggestions[]` | One per target share found (`target_recall` 0.8, 0.9, 0.95), keys below |
| `bucket_edges`, `bucket_precision[]` | Score ranges (0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0) and, per range, `{low, high, predictions, correct, precision, interval90, enough}` measured on the Final test pictures; `enough` needs at least 20 predictions in the range **and** a Final test split of at least 30 pictures and 30 drawn boxes (`MIN_TEST_PICTURES`, `MIN_TEST_BOXES`) |

`suggestions[]` item: `target_recall`, `reachable`, `threshold` (the **highest** score whose recall on the Checking pictures reaches the target; `null` when
not reachable), `enough_data`, `recommended`, `validation` (`{precision, recall, n_gt}`), `test` (the same at that cut-off on the Final test pictures, or `null`),
`interval90` (`{recall: [low, high] | null, precision: [low, high] | null, resamples_used, resamples, basis}`), `test_at_usual_cutoff` (the Final test pictures scored at
the usual 0.5: `{threshold: 0.5, precision, recall, n_gt}`; present when the target was reachable) and `plain`, one sentence. `interval90.resamples_used` counts the
re-draws in which the target was reached at all; `recommended` needs `enough_data` **and** `resamples_used` of at least 90% of `resamples`. A test recall below the target
is worded in `plain` but does not by itself remove a recommendation. **Rounding never flatters:** cut-offs, stored shares, shown percentages and the low end of a
range are rounded down, the high end of a range up (`floor4`, `ceil4`, `share_down`, `pct_down`, `pct_up`); the "about N in 10" labels read a good share down to the next fraction and a wrong
share up. Real synthetic body (generated boxes with a known score distribution, 260 checking and 300 test pictures, two labels; `plain` sentences shortened):

```json
{
  "available": true,
  "schema": "athena.operating_points/1", "source": "validation split", "bucket_source": "test split",
  "enough_data": true, "why_not": "", "minimum_boxes": {"total": 400, "per_label": 30},
  "bootstrap": {"by": "picture", "resamples": 1000, "seed": 20261023, "level": 0.9},
  "suggestions": [
    {"target_recall": 0.8, "reachable": true, "threshold": 0.4109, "enough_data": true, "recommended": true,
     "validation": {"precision": 0.8533, "recall": 0.8003, "n_gt": 516},
     "test": {"precision": 0.8512, "recall": 0.811, "n_gt": 614},
     "interval90": {"recall": [0.7589, 0.8807], "precision": [0.7631, 0.8823], "resamples_used": 999, "resamples": 1000,
                    "basis": "checking and Final test pictures resampled; cut-off re-chosen each time"},
     "test_at_usual_cutoff": {"threshold": 0.5, "precision": 0.8927, "recall": 0.7182, "n_gt": 614},
     "plain": "A cut-off of 0.4109 found 80% of the 516 boxes on the Checking pictures and 81% of the 614 boxes on the Final test pictures (90% range 75% to 89%, among the 999 of 1000 re-draws of the pictures that reached the target); 85% of the boxes it drew there were right (90% range 76% to 89%). At this setting about 2 in 10 of the boxes it draws are wrong on the Final test pictures. The same cut-off applies to every label. For comparison, at the usual 0.5 setting it found 71% of the boxes, and 89% of the boxes it drew were right (Final test pictures). A person chooses the setting."},
    {"target_recall": 0.9, "reachable": false, "threshold": null, "recommended": false, "…": "…",
     "plain": "On the Checking pictures, no cut-off found more than 86% of the drawn boxes, so a target of 90% cannot be reached."}
  ],
  "bucket_precision": [{"low": 0.6, "high": 0.7, "predictions": 117, "correct": 99, "precision": 0.8461, "interval90": [0.7835, 0.8931], "enough": true}, "…"]
}
```

(`recall_ceiling`, `records`, `pictures`, `ground_truth_boxes`, `boxes_per_label`, `prediction_floor`, `iou_threshold`, `test`, `bucket_edges` and the 0.95 suggestion are omitted for length.
The 0.8 / 81% / 0.4109 figures come from generated data and say nothing about a real model.) `wording.buckets[]` is each `bucket_precision` row plus `sentence`, for example
"Scores between 0.6 and 0.7 were right 84% of the time on the Final test pictures (117 boxes)."; a range with too little data reads "…: only 7 boxes on the Final test pictures, too few to say how often they were right." A model without the block gets, verbatim,
`{"available": false, "reason": "How often each score was right is not measured for this model (it was checked before this was added, or its backend could not score the pictures)."}` for `operating_points`
and the same reason with `split: "Final test pictures", buckets: []` for `wording`.

### Check-screen render model: `confidence_wording` and `suggested_threshold`

Both are always present in the render model of an inference app (`render_preview` and the rendered result, `_static` in `web/projects/inference/service.py`, from the **pinned** model's
stored evaluation; contract in `web/projects/inference/schema.py`). They are data for the screen to use; nothing is applied automatically, and the builder's own
threshold validation (a number from 0 to 1) is unchanged.

`confidence_wording`: `{measured: bool, bands: [{low, high, label, precision, n, enough}], note}`. `bands` tile 0 to 1 from the lowest score up (`low` inclusive, `high` exclusive, the last band includes 1.0);
a detection belongs to the band holding its score. `label` is neutral and quantitative, for example `"right about 1 in 2 times in our check"` or `"right fewer than 1 in 20 times in our check"`, read **down** from the low end of
the band's 90% range and never "at least"; it is `"too few examples to say"` when `enough` is false (then `precision` is given but must not be worded as a result). `n` is the number of predictions the band was measured on.
Real synthetic body (first three of eight bands shown):

```json
{"measured": true,
 "bands": [{"low": 0.0, "high": 0.2, "label": "right fewer than 1 in 20 times in our check", "precision": 0.0251, "n": 199, "enough": true},
           {"low": 0.2, "high": 0.4, "label": "right about 1 in 10 times in our check", "precision": 0.2027, "n": 222, "enough": true},
           {"low": 0.4, "high": 0.5, "label": "right about 1 in 2 times in our check", "precision": 0.5871, "n": 109, "enough": true}, "…"],
 "note": "Measured on the Final test pictures only, not on new cameras, lighting or products. The score is the computer's own number, not a probability. A person decides."}
```

Not measured (an older model, a tool that could not score, or no band with enough predictions) is `{"measured": false, "bands": [], "note": "Scores are the computer's own numbers; they have not been checked against pictures it has not seen."}`;
the screen then shows the raw score as the computer's own number and nothing stronger. **The fixed "Very sure / Fairly sure / Somewhat sure / Not very sure" phrases that the check screen used before 2026-10-23 (cut at scores 0.9, 0.75, 0.5) were removed**; the screen shows a band's `label` or the raw score.

`suggested_threshold`: `{value, target_recall, basis, enough_data, plain}`. `value` is a cut-off from 0 to 1 only when the 80% target was reachable **and** `recommended` (see above); otherwise `null`, with
`plain` saying why (`target_recall` is then `0.8` or `null` when nothing is measured; `basis` is `"validation split"` or `"not measured"`). Synthetic example: `{"value": 0.4109, "target_recall": 0.8, "basis": "validation split", "enough_data": true, "plain": "A cut-off of 0.4109 found 80% of …"}`.
Not measured: `{"value": null, "target_recall": null, "basis": "not measured", "enough_data": false, "plain": "No cut-off is suggested: How often each score was right is not measured for this model (…)"}`. The builder shows a "Use the suggested setting" button only when `value` is a number from 0 to 1 and `enough_data` is true; pressing it puts the value, rounded down to a whole percent, in the slider and saves nothing.

### `evaluation` documents: `held_out_outcomes`

Evaluations made after this change carry `held_out_outcomes`: `{"schema": "athena.held_out_outcomes/1", "missed_total", "false_alarm_total", "items": […]}` with at most
250 stored items per kind, worst first (`MAX_STORED_OUTCOMES_PER_KIND` in `evaluation.py`). The totals equal the summed false negatives / false positives. The `plain`
endpoint above is the intended way to read them.

## Console HTTP API: the improve loop (2026-10-24, prototype)

What the check-screen operators said can be pulled back into the project as pictures a person reviews, and a newly taught model is
compared with the model in use. **Nothing here changes a model, a training run or a dataset version, and nothing here is automatic**;
the boxes it creates are *Needs review* suggestions that count for nothing until a person accepts them. Code:
`web/projects/feedback_import.py`, `feedback_views.py` (routes in `urls.py`), `training/comparison.py`. Tested on generated pictures and scripted
test-double models only.

### `GET /api/projects/<id>/feedback/corrections` (any role; read-only) and `POST …/feedback/corrections/import` (Labeler and above)

Views `feedback_corrections` and `feedback_corrections_import` (`web/projects/feedback_views.py`). A role given for this project applies. A Viewer
who posts gets 403 `{"error": "Your account can look but not change things. Ask an administrator for the Labeler role.", "your_role": "viewer", "required_role": "labeler"}`.

**What counts as a correction.** A finished check-screen result (an `ObservationRecord`, `status: succeeded`) in this project that has at least one `FeedbackRecord`.
For each shown detection the **latest** verdict wins (ordered by `(created_at, id)`, the order the feedback export uses: `effective_feedback`); every missed-object report counts.
What an import makes (`build_plan`):

| Operator said | Box made | Notes |
|---|---|---|
| detection is right (`true_positive`) | one box: the model's own box, with the model's score as `confidence` | counted in `kinds.true_positive` |
| detection is wrong (`false_positive`) | **none** | a wrong model box is never turned into ground truth; counted in `false_alarms` |
| missed object, box drawn | one box: the operator's box, `confidence` null | |
| missed object, no box | none | counted in `missed_without_box` so the labeler is told something was missed there |

A box needs a label that is **in the project's label list exactly** (capital letters count; a label is never invented) and the manual-box geometry rules
(positive size, inside [0, 1]); otherwise that one box is skipped and named in `skipped_boxes` with a reason code (below), and the picture still comes in.
The picture is **copied** into the project as a new picture (a new id and storage key; the check-screen file is untouched), once per result, ever.

**Box rows made** (probe 2026-10-24): `origin: "model"`, `status: "suggested"` (shown as **Needs review**), `model_name: "operator feedback (model v1)"`,
`confidence` 0.9 for a confirmed detection and `null` for a drawn miss, `created_by` the person who gave the feedback. They are *not* ground truth: datasets,
readiness, the data-health check and exports read accepted boxes only, so they are ignored until a person accepts them with the ordinary review controls.

**`GET` body** (probe: four results with feedback; `items` cut to one per state; ids are real values from the throwaway database):

```json
{
  "pending": 1, "imported": 3, "cannot_import": 0, "cannot_import_scanned": 1, "total": 4, "truncated": false,
  "items": [
    {"observation_id": "7fbd5cdf-a64b-4950-9259-7dd6e0352c89", "app_id": "c39af4e2-3f36-47d2-9816-fd64f18ab49b", "app_name": "Feedback line",
     "taken_at": "2026-10-23T15:47:29.654626+00:00", "model_version": {"id": "ebfde77b-880b-4153-85ed-13a03acc24fd", "version": 1},
     "kinds": {"true_positive": 1, "false_positive": 0, "missed": 0}, "state": "pending", "reason": null, "message": ""},
    {"observation_id": "c8db21a8-eb3c-4a42-9960-34b87a7775d4", "app_id": "c39af4e2-3f36-47d2-9816-fd64f18ab49b", "app_name": "Feedback line",
     "taken_at": "2026-10-23T15:47:29.632707+00:00", "model_version": {"id": "ebfde77b-880b-4153-85ed-13a03acc24fd", "version": 1},
     "kinds": {"true_positive": 1, "false_positive": 1, "missed": 2}, "state": "imported", "reason": null, "message": "",
     "asset_id": "67a73cca-4899-4df5-a59d-7b494a0965df", "imported_at": "2026-10-23T15:47:29.672126+00:00", "imported_by": "u1",
     "boxes_created": 2, "missed_without_box": 1, "boxes_skipped": 0, "skipped_reasons": []}
  ],
  "batch_limit": 50,
  "notice": "Pulling corrections in only adds pictures with Needs-review boxes. Nothing is used for teaching until a person accepts the boxes, and the model in use is not changed."
}
```

* `state` is `pending`, `imported` or `cannot_import` (then `reason` and `message`). The list cheaply checks, without decoding any picture, that the stored
  file exists (`picture_file_missing`), is at most 20 MB (`picture_too_large`) and has a JPEG/PNG/WebP content type (`picture_not_a_supported_image`).
  **Problems that need a look inside the file are found only by an import attempt** (below), so a sideways picture is still `pending` here and is named as skipped
  each time an import runs.
* **Window and counts.** The newest `SCAN_LIMIT` (2000) results that are still waiting are looked at; results already pulled in never use that window up.
  `imported` and `total` are exact. `pending` and `cannot_import` are counted among the results looked at, and `cannot_import_scanned` says how many that was;
  `truncated: true` means more were waiting than were looked at. `items` holds at most `LIST_LIMIT` (100) entries, waiting ones first and the rest filled with the newest imported ones, then sorted newest first.
* `kinds` are the counts after the latest-verdict rule. An imported item adds `asset_id`, `imported_at`, `imported_by` (opaque `u<id>`), `boxes_created`, `missed_without_box`,
  `boxes_skipped` and `skipped_reasons[]` (`reason`, `count`, `message`): boxes left out are never silent.

**`POST` body** (optional; no body, `{}` or no `observation_ids` means "everything waiting"). `{"observation_ids": ["…", …]}` names results (at most `IMPORT_BATCH_LIMIT` = 50, duplicates dropped,
each a UUID string); any other field is 400 `Unsupported field: all.`; more than 50 is 400 `Name at most 50 results at a time; the rest can be pulled in next.`; a non-list is 400.
With no ids, the newest 50 results that pass the cheap checks are imported and the ones that cannot be are named in `skipped` without using up the 50. Each result is its own
transaction: the file is saved first, then the picture row, the boxes and the link row are written together, and on any failure the file is removed again, so one bad picture never loses the others.
Two people pressing the button at once end with one picture (a database uniqueness rule plus a row lock). Probe response (A = the picture with feedback of all kinds; B = a false alarm only; C = a sideways JPEG):

```json
{
  "imported": [
    {"observation_id": "2a8b9600-af45-47a5-8397-e49c3bfadc96", "asset_id": "b9c8ebac-b6ff-4511-a191-b1924cc05344",
     "boxes_created": 2, "missed_without_box": 1, "false_alarms": 1, "skipped_boxes": []},
    {"observation_id": "99f19678-bc67-4ee6-98fb-af1e3eab2bd1", "asset_id": "a27d5190-8b9e-4dfe-8c9e-db5a53b70fa4",
     "boxes_created": 0, "missed_without_box": 0, "false_alarms": 1, "skipped_boxes": []}
  ],
  "skipped": [
    {"observation_id": "6cb41731-363e-473a-b849-760d6f5b9dbb", "reason": "picture_rotated_by_camera",
     "message": "This picture is stored sideways (the camera rotated it), so the boxes would not line up. It was left out."}
  ],
  "totals": {"requested": 3, "imported": 2, "skipped": 1, "boxes_created": 2, "missed_without_box": 1, "remaining_pending": 2},
  "notice": "These boxes say Needs review. They are not used for teaching until a person accepts them."
}
```

A second import of A gives `skipped: [{"reason": "already_imported", "message": "This result was already pulled into the project.", "asset_id": "…"}]` and creates nothing. When a project label has
been removed since the feedback, the picture still comes in and `imported[].skipped_boxes[]` has `{"reason": "label_not_in_project", "kind": "missed", "label": "normal", "prediction_index": null, "feedback_id": "…", "message": "…"}`.

**Reason codes** (`MESSAGES` in `feedback_import.py`; the code is for programs, the `message` is plain language and is what the console shows). *Whole result left out* (`skipped[].reason`; nothing is saved):
`not_found`, `no_feedback`, `already_imported`, `observation_not_finished`, `picture_file_missing`, `picture_too_large` (over 20 MB), `picture_not_a_supported_image`, `picture_dimensions_too_large`
(more pixels than the upload gate allows, read from the header), `picture_rotated_by_camera`, `storage_error` (the project's storage refused the write, for example a full disk; the half-written file is removed and the batch goes on),
`picture_changed_since_check` (the stored bytes no longer hash to the checksum the check screen recorded). *One box left out* (`skipped_boxes[].reason`): `label_not_in_project`, `prediction_not_found`, `box_not_valid`, `box_outside_image`.

**Pictures the camera rotated are skipped, not turned upright.** If the stored file has an EXIF orientation other than 1 (`EXIF_ORIENTATION` 0x0112; all of 2 to 8 were exercised), the model's boxes are in the stored pixels while boxes drawn on the check screen
are in the picture as shown, so the two would not line up; the picture is left out with `picture_rotated_by_camera`. This is a limit, not a rotation feature (`tests_feedback_import.py`, `test_every_other_orientation_is_also_left_out`).

**What is stored: `FeedbackImport`** (`web/projects/models.py`, migration `0010_feedback_import_link.py`). One append-only row per check-screen result, ever (a uniqueness constraint; update and delete raise), with:
`observation` (cascade) and its id as text, `asset` (cleared if the picture is deleted later) and its id as text, `model_version_ref`, `model_version_number`, `artifact_sha256` of the model that made the result,
`image_sha256`, `kinds` (counts of right / wrong / missed), `boxes_created`, `missed_without_box`, `skipped` (the box-level reasons), `annotation_ids` (ids of the boxes made, as text; they may be edited or removed later, the ids stay),
`imported_by` (cleared if the account is deleted), `imported_by_ref` (`u1`) and `imported_by_role` (the role held at the time), `created_at`. It holds **no real foreign key to a model version, run or dataset**, so it can never hold one back.
Probe row for A: `kinds {'true_positive': 1, 'false_positive': 1, 'missed': 2}`, `boxes_created 2`, `missed_without_box 1`, `imported_by_ref 'u1'`. It is the only audit record of an import: nothing is written to `ModelEvent` or `ExportEvent`.

**Pictures with no box.** A false-alarm-only result is imported as a picture with no boxes. A picture with no accepted box is left out of every dataset snapshot (`Analysis.excluded`: `images_without_accepted_boxes`),
so it is **not** used as a "nothing to find here" example. To use it, a labeler has to draw a box.

### Picture list: `origin` and `from_observation`

Each entry of `GET /api/projects/<id>/assets` (`_asset_data` in `web/projects/views.py`) now carries `"origin": "upload"` or `"operator_feedback"` and `"from_observation": null` or the id of the check-screen result the picture was copied from
(read from the `FeedbackImport` link). Probe: 14 uploads, then three entries with `origin: "operator_feedback"`; the one with two Needs-review boxes had `accepted_boxes: 0, pending_boxes: 2`. The console uses it for the "Show only pictures from check screens" filter.

### Auto-suggest jobs: `kept_for_review` and `kept_for_review_reason`

`job_data` (`web/projects/autolabel_views.py`) adds `kept_for_review` (an integer) and `kept_for_review_reason` (`"kept for review: this picture came from operator feedback"`, or `""` when 0). A picture linked to a `FeedbackImport` is **never auto-suggested**:
a re-run of the suggester deletes the earlier unreviewed suggestions of a picture first, and that would remove the operator's boxes before anyone had judged them (`web/projects/autolabel/runner.py`: `_target_assets`, `_linked_to_feedback`, `job_note`). The
count is worked out when the job is read, by asking which pictures in the job's scope are linked (`kept_for_review_count`), not stored; the finished job's `error` text also says "N pictures left out (…)". The guard is checked again while the job runs
(a picture linked after it was queued is still left alone), and, as a last line, the delete skips boxes named "operator feedback…" or listed in a link row's `annotation_ids`. Tested in `tests_autolabel_feedback.py`.

**Caveat, stated plainly:** the bulk accept (`POST /api/projects/<id>/annotations/review`, `{"action": "accept"}`, optional `min_confidence`, `ids`, `asset_id`) has **no server-side guard**. It accepts every `suggested` box it is asked about, operator-feedback boxes included
(those with a model score pass a `min_confidence` filter; those the operator drew have a null score and do not). The console tells the person before the button (`#autolabel-feedback-note`: "N of these boxes come from check-screen corrections. Check them first…") and can show only
those pictures, but a script calling the route is not stopped.

### `comparison`: how a new model did against the model in use

Stored by the training worker as `evaluation["comparison"]` of the **new** candidate, and passed through by `GET …/models/<id>/results/plain` as `comparison` (and shown in the approve dialog as information; it adds no tick and blocks nothing). Code:
`web/projects/training/comparison.py` (`compare_with_approved` writes, `plain_comparison` reads); worker hook `work_out_comparison` in `training/runner.py`; tests `tests_comparison.py` (70 test methods, scripted test-double backends and hand-built blocks).

**When it is computed.** After a run is evaluated, if the project has an `approved` model, the new run has a held-out Final test split with results, and the approved model's backend can run in the helper. It costs **one extra prediction pass** of the model in use over the test pictures
(plus one more of the new model only when its stored miss list is missing or cut short, so the two can be paired box by box). Otherwise nothing is stored and the log says why. It never fails the run (any error is logged and the key is absent), but cancel and the time limit still end the run. A model that hangs inside a pass is **not**
caught (there is no per-pass timeout). If the approved model's file is missing or no longer matches its recorded checksum, `{schema, available: false, reason}` is stored instead (the only case that stores a "not available" block).

**Fields** (`available: true`; `recall` and `precision` are shares rounded **down**, derived again by the reader from the counts):

| Key | Meaning |
|---|---|
| `schema` | `athena.comparison/1` |
| `baseline` | the model in use: `model_id`, `version`, `artifact_sha256`, `backend`, `trained_on_pictures` (count of **different** pictures, by SHA-256, in its Training and Checking groups) |
| `test_pictures`, `test_boxes` | the new model's Final test pictures and the person-drawn boxes on them |
| `fair`, `unfair_reason`, `overlap_pictures` | see fairness below |
| `baseline_result`, `candidate_result` | `{found, of, wrong_boxes, recall, precision}` at the same confidence and overlap as the new model's own evaluation |
| `per_label[]` | `label`, `baseline_found`, `candidate_found`, `of` |
| `difference` | `found` (new minus in use), `wrong_boxes`, `only_candidate_found` (**b**), `only_baseline_found` (**c**); `b − c` equals `found` |
| `verdict` | `better`, `about_the_same`, `worse` or `cannot_tell` |
| `plain` | the sentence; always ends with "Not a final decision. This is the computer’s opinion. A person decides." |

**Verdict rules** (`decide`, `margin_needed`; constants `MIN_DIFFERENCE_BOXES` = 3, `MIN_DIFFERENCE_SHARE` = 1/10 of the test boxes, `evaluation.MIN_BOXES_PER_LABEL_FOR_VERDICT` = 30; all judgements, none measured), applied to counts, never to rounded shares, in this order:

1. not fair → `cannot_tell`;
2. no test pictures or no test boxes → `cannot_tell`;
3. **any** label of the model with fewer than 30 test boxes, a label with **zero** test boxes included → `cannot_tell` (the same rule as the plain verdict on the results card);
4. `b − c ≥ max(3, ceil(10% × test boxes))` → `better`; `c − b ≥` the same margin → `worse`; anything else → `about_the_same`.

The wrong boxes are reported but never change the verdict; the sentence says so when the new model drew at least a margin more of them. Because the verdict is about the **same boxes**, not about two percentages, a bigger share found with few disagreements can still read "about the same".

**Fairness.** The comparison is `fair: true` only when the model in use is known to have been made from a dataset version whose stored manifest is **intact** (its checksum and picture count still match, the test `datasets.service.verify_integrity` uses), that manifest lists at least one Training or Checking picture, and **none of the new model's
Final test pictures is among them (matched by SHA-256, so the same bytes under another picture id still count)**. Otherwise `fair: false`, the verdict is `cannot_tell` however large the difference, and `unfair_reason` says which case: some test pictures were also used to teach or check the model in use (the sentence is built from the counts, for example “12 of the 106 test pictures were also
used to teach or check the model in use …”); it is **not known** what the model in use was taught from (no run or manifest, for example an imported model); the saved list is missing, empty or damaged. "Saw nothing" is never concluded from a list that may have lost entries. Near-duplicates are not detected, so a fair comparison can still be flattering.

**The reader re-derives the text.** `plain_comparison` never trusts a stored verdict or sentence: it validates the counts against the evaluation they sit in (same pictures, boxes and totals), re-computes the verdict with `decide` and rebuilds the sentence; an unfair sentence is rebuilt from the overlap counts or matched against fixed wording; an "unavailable" reason is shown only if it is one of ours; labels, ids and backend names must have the shape of the real thing. A malformed block reads
`{"available": false, "reason": "The saved comparison with the model in use could not be read, so it is not shown."}`. Other `available: false` reasons: no comparison saved (“…none was in use when it was taught, it was checked before comparisons existed, or the model in use could not be run”), no held-out test results, and the checksum case above.

Synthetic shapes from `plain_comparison` (counts hand-built with `make_block`, **not a model's results**): 100 test boxes, the model in use found 80, the new model 86, `only_candidate_found` 7, `only_baseline_found` 1, 3 and 6 wrong boxes:

```json
{"available": true, "schema": "athena.comparison/1",
 "baseline": {"model_id": "0f6f5d44-2f5a-4c9c-9f55-5a3c1f0b7a11", "version": 3, "artifact_sha256": "abab…ab", "backend": "script-a", "trained_on_pictures": 120},
 "test_pictures": 106, "test_boxes": 100, "fair": true, "unfair_reason": "", "overlap_pictures": 0,
 "baseline_result": {"found": 80, "of": 100, "wrong_boxes": 3, "recall": 0.8, "precision": 0.9638},
 "candidate_result": {"found": 86, "of": 100, "wrong_boxes": 6, "recall": 0.86, "precision": 0.9347},
 "per_label": [{"label": "dent", "baseline_found": 50, "candidate_found": 55, "of": 60}, {"label": "scratch", "baseline_found": 30, "candidate_found": 31, "of": 40}],
 "difference": {"found": 6, "wrong_boxes": 3, "only_candidate_found": 7, "only_baseline_found": 1},
 "verdict": "about_the_same",
 "plain": "About the same on these test pictures: the new model found 86 of 100 test boxes (86%) and the model in use found 80 (80%). The two models disagreed on 8 boxes: the new model alone found 7, the model in use alone found 1. One model is called better only when it found at least 10 boxes more that the other missed. Not a final decision. …"}
```

(Here `b − c` is 6 against a needed margin of `max(3, ceil(10))` = 10, hence “about the same”.) The same counts with `fair: false`, 12 overlapping pictures read `cannot_tell`; with only 20 and 10 test boxes in the two labels they read `cannot_tell` with “About 30 per label are needed before two models can be compared.”

**Where it shows.** The results card has a block “Compared with the model in use” (`trComparisonBlock` in `console.js`): a verdict word, the server's sentence, a table of boxes found and wrong boxes drawn for both models, and “Not a fair comparison: …” when `fair` is false. When there is none it says so in one line. The approve dialog shows the same sentence above the verdict, as information only.

### Shared request rules (JSON routes written on `api_common`)

* **Body limits** (`json_payload` in `web/projects/api_common.py`; routes using it include `POST …/training/runs` and `POST …/feedback/corrections/import`; other route families keep their own smaller or larger limits). More than `MAX_REQUEST_BYTES` (32 KiB) is `413 {"error": "The request is too large."}`;
  more than Django's own `DATA_UPLOAD_MAX_MEMORY_SIZE` (1 MiB, `settings.py`) is also that JSON 413 (the exception is caught where the body is first read, so it is never an HTML error page). A body that is not JSON, or a number with more than 4300 digits (Python's limit), or nesting too deep, is
  `400 {"error": "The request body is not valid JSON."}`. A non-object body is 400, a non-JSON content type is 415, and a route that allows an empty body (the import) treats **no body and no `Content-Type`** as `{}`. A multipart body, such as the one Django's test client sends by default, is 415. Probe results: 40 KB → 413, 1 MiB + 10 bytes → 413, 6,000 digits → 400, `text/plain` → 415, empty JSON body → 200.
* **Role messages** (`access(..., messages={...})`, `web/projects/permissions.py`): a view can give the sentence a refused signed-in person sees for one access level (`messages={"admin": "Only an administrator can change this setting."}`). Other levels, the 401 “Sign in” answer and the 403 status are unchanged. The download switch uses it.
* **Redirects when a helper downloads weights** (`redirect_refusal`, `PinnedRedirects`, `open_pinned` in `training/torchvision_frcnn.py`; used by both backends): a redirect is followed only if the target is **https**, on the default port, has **no user name or password in the address**, and its host is on the backend's list, compared exactly and ignoring capitals
  (`download.pytorch.org` for the torchvision files; `huggingface.co` and any host ending `.hf.co` for D-FINE). A host such as `download.pytorch.org.evil.example`, one with an empty name part, or an address with spaces or control characters, is refused; the download stops with a plain sentence naming the host and nothing is saved. Each file is also cut off one byte past its known size (D-FINE: the pinned size; torchvision: the published size, or 200 MB if the size is not known) and
  checked against its checksum before it is kept.
* **Shared helpers in `datasets/service.py`:** `accepted_boxes(project)` is the one query for "the accepted boxes of this project" and `content_fingerprint(project)` is the public name of the hash that notices boxes changed between looking and writing (`_fingerprint` stays as an alias). `analyse(project, config, file_check="full" | "skip" | <seconds>)` says how many picture files are
  re-opened; `Analysis.files_rechecked` and `Analysis.complete` say whether it looked at all of them, and `create_version` refuses an incomplete analysis with `IncompleteAnalysis` (nothing written), so a version can only be frozen from a full check. A bad `file_check` value is a `ValueError`.

## Examples

- [`examples/synthetic_pipeline/`](../examples/synthetic_pipeline/) — in-memory
  registry, inference client, and example pipeline using synthetic data.
- [`examples/adapters/yolo_adapter.py`](../examples/adapters/yolo_adapter.py) —
  reference third-party adapter wrapping an Ultralytics YOLO detector behind the
  `ModelAdapter` interface (image modality). **Requires `pip install ultralytics`, which is
  AGPL-3.0** — see [`LICENSE_AUDIT.md`](LICENSE_AUDIT.md) §4 before using it in a service.
- [`examples/adapters/audio_noise_adapter.py`](../examples/adapters/audio_noise_adapter.py) —
  from-scratch acoustic noise/SNR analysis adapter, no trained model required
  (audio/sensor-timeseries modality).
- [`examples/domain_packs/acoustic_condition_pack.py`](../examples/domain_packs/acoustic_condition_pack.py) —
  reference `DomainPack` pairing with the audio adapter above.
- [`examples/audio_pipeline/`](../examples/audio_pipeline/) — synthetic WAV
  generation and an end-to-end `LocalRuntime` run of the audio adapter +
  domain pack.

## Guides

- [Writing a custom adapter](guides/writing-a-custom-adapter.md) — implement,
  register, and conformance-test a `ModelAdapter` for your own model.
- [Bring your own model or data type](guides/bring-your-own-model.md) — for
  readers newer to ML tooling, or working with a data type other than
  images: where training happens, and how a trained model plugs in.
