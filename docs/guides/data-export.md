# Exporting a dataset (dataset-version API)

> **Status: JSON API, with a console panel for the common steps.** The console's "Make a training set" panel can save a
> snapshot and download a zip (the "Teach the computer" button also saves one for you when the work has changed); everything
> below is the HTTP API that panel uses, and the parts the panel does not show are done with HTTP calls. All example data is synthetic. Responses shown were produced by running the
> API against a throwaway project of six generated PNG images; ids, hashes, and timestamps are specific to that run.

A *dataset version* is an immutable, numbered snapshot of the **reviewed** bounding boxes in one image
project. You create a version, then export it as a COCO or YOLO zip. Creating a version never changes your
project; exporting never changes a version.

## What goes into a version

- **Only accepted boxes.** A box drawn by a person is accepted from the start. A box proposed by the
  auto-labeller stays **Needs review** until someone accepts or edits it; until then it is left out. The response
  reports how many were left out (`excluded.suggested_boxes`).
- **Only images that have at least one accepted box** (`excluded.images_without_accepted_boxes` counts the rest).
- Image projects only; any other project id returns 404.
- Versions are numbered 1, 2, 3 … per project. Creating a new one never alters an older one.

## Authentication

Every route needs a signed-in session. `curl` needs a cookie jar and, for `POST`, the CSRF token. Use your own
host and credentials. `$BASE` below is a placeholder.

```bash
BASE=https://console.example.com
JAR=$(mktemp)

# 1. Get a CSRF cookie, then sign in.
curl -s -c "$JAR" -o /dev/null "$BASE/login/"
CSRF=$(awk '$6=="csrftoken"{print $7}' "$JAR")
curl -s -b "$JAR" -c "$JAR" -o /dev/null -e "$BASE/login/" \
  -d "username=YOUR_USER" -d "password=YOUR_PASSWORD" -d "csrfmiddlewaretoken=$CSRF" "$BASE/login/"

# 2. Re-read the token (it can rotate at login). Reuse $JAR and $CSRF below.
CSRF=$(awk '$6=="csrftoken"{print $7}' "$JAR")
PROJECT=see-athena--5vnim2of     # your image project's id
```

Not signed in: `401 {"error": "Sign in to continue."}`. `GET` calls need only the cookie. `POST` also needs
`-H "X-CSRFToken: $CSRF"` and a `Referer` (or `Origin`) matching your site, as shown below. Replies are
`Cache-Control: no-store`.

## 1. Check readiness (creates nothing)

```bash
curl -s -b "$JAR" \
  "$BASE/api/projects/$PROJECT/datasets/readiness?seed=0&train=0.8&val=0.1&test=0.1"
```

All four query parameters are optional; give all three of `train`, `val`, `test` or none of them. Response
(abridged) for the synthetic project, which had 5 accepted boxes and 1 proposal still awaiting review:

```json
{
  "ready": true,
  "blockers": [],
  "counts": {"images_total": 6, "images": 5, "accepted_boxes": 5, "suggested_boxes": 1},
  "excluded": {"suggested_boxes": 1, "images_without_accepted_boxes": 1, "reason": "Boxes marked Needs review are model proposals that nobody has accepted yet, so they are not ground truth and were left out."},
  "split_config": {"seed": 0, "train": 0.8, "val": 0.1, "test": 0.1},
  "label_names": ["damaged", "normal"],
  "preview": {
    "split_counts": {"train": {"images": 3, "boxes": 3}, "val": {"images": 1, "boxes": 1}, "test": {"images": 1, "boxes": 1}},
    "class_balance": {"damaged": {"train": 2, "val": 0, "test": 1, "total": 3}, "normal": {"train": 1, "val": 1, "test": 0, "total": 2}},
    "duplicates": {"groups": 0, "images_in_groups": 0, "leaks": 0, "note": "Only byte-identical files (same SHA-256) are detected; near-duplicates are not.", "examples": []},
    "warnings": []
  },
  "problems": [], "problem_count": 0, "problems_truncated": false
}
```

`ready` is `false` when there are no accepted boxes or any validation problem (below). `preview` is built exactly
like the manifest's summary, so what you see is what a version would contain. Splits start from a hash of the seed and
the image's SHA-256, not a quota. If that would leave a split you asked for (fraction > 0) with no pictures, which is
likely with small projects, one whole group of identical pictures is moved from the fullest split into the empty one,
choosing the group with the lowest hash value, so the result stays deterministic and identical pictures stay together.
This needs at least as many distinct pictures as requested splits; below that, a split stays empty and `warnings` says so.
Note that with this rule a picture's split can depend on which other pictures are present. Change `seed` to get a
different assignment. The numbers above are from a synthetic five-picture project run through this endpoint.

## 2. Create a version

```bash
curl -s -b "$JAR" -X POST "$BASE/api/projects/$PROJECT/datasets" \
  -H "X-CSRFToken: $CSRF" -H "Referer: $BASE/" -H "Content-Type: application/json" \
  -d '{"seed": 0, "split": {"train": 0.8, "val": 0.1, "test": 0.1}}'
```

The body is optional JSON (send `{}` for defaults), at most 16 KiB:

| Field | Rule | Default |
| --- | --- | --- |
| `seed` | whole number, 0 to 2147483647 | `0` |
| `split` | object with **all of** `train`, `val`, `test`; each a number 0–1; sum must be 1 (±1e-6) | 0.8 / 0.1 / 0.1 |

Any other field is rejected. Result `201`:

```json
{"dataset": {
  "id": "22bddc20-e11e-4427-abbf-e0cdf34d83bd", "version": 1, "project_id": "…", "status": "ready",
  "created_at": "2026-10-16T08:56:47.272968+00:00", "created_by": "u1",
  "label_names": ["damaged", "normal"],
  "counts": {"images": 5, "boxes": 5},
  "excluded": {"suggested_boxes": 1, "images_without_accepted_boxes": 1, "reason": "…"},
  "split_config": {"seed": 0, "train": 0.8, "val": 0.1, "test": 0.1},
  "manifest_sha256": "5295be1ba549a40eb3459f78dea9a3384863c142c7a5f63c91600dab9237c3b8",
  "export_formats": ["coco", "yolo"],
  "manifest": { "…": "see Manifest below" }
}}
```

`created_by` is an opaque `u<id>`; usernames and emails never appear in a version or an export.

### Why a create can fail

| Status | Meaning |
| --- | --- |
| `400` | Bad body (unsupported field, bad `seed`, fractions not summing to 1, not a JSON object), **or** no accepted boxes at all. Body `{"error": …}`; the "no accepted boxes" case also returns `excluded`. |
| `401` | Not signed in. |
| `404` | Project does not exist or is not an image project. |
| `409` | Boxes changed while the version was being prepared, or another version was created at the same moment. Nothing was created; retry. |
| `413` / `415` | Body over 16 KiB / not sent as `application/json`. |
| `422` | Validation problems. **Nothing is coerced and nothing is created.** |

A `422` looks like this (here one box used `Damaged` while the project's label is `damaged`):

```json
{
  "error": "Fix the problems below before creating a dataset version. Nothing was created.",
  "blockers": ["1 box(es) use a label that is not in this project's label list (labels must match exactly, including capital letters)."],
  "excluded": {"suggested_boxes": 1, "images_without_accepted_boxes": 1, "reason": "…"},
  "problems": [{"code": "label_not_in_project", "message": "Label 'Damaged' is not in this project's label list (matching is case-sensitive).", "asset_id": "de7bcb0f-…", "annotation_id": "dc427ac4-…"}],
  "problem_count": 1,
  "problems_truncated": false
}
```

Every problem is listed, not just the first (up to 200; `problem_count` is the true total and `problems_truncated`
says whether the list was cut). Problem codes:

| `code` | Cause |
| --- | --- |
| `label_not_in_project` | Box label is not in the project's label list. Matching is exact and case-sensitive. |
| `label_unsupported_characters` | A project label contains line breaks or control characters, which would corrupt class files. |
| `non_positive_size` | A box has zero or negative width or height. |
| `geometry_out_of_range` | A box is not finite or extends outside the image: `x`, `y` ≥ 0 and `x+width`, `y+height` ≤ 1 (normalised). |
| `file_missing` | The image file is not in storage. |
| `hash_mismatch` | The file on disk no longer matches the SHA-256 recorded at upload. |
| `too_many_images` | More than 50,000 images (the default cap in code). |

The geometry check is against normalised 0–1 coordinates; it is not checked against the decoded image dimensions.

## 3. List and read versions

```bash
curl -s -b "$JAR" "$BASE/api/projects/$PROJECT/datasets"                 # {"datasets": [...]} newest first, no manifest
curl -s -b "$JAR" "$BASE/api/projects/$PROJECT/datasets/$DATASET"        # {"dataset": {... "manifest": {...}}}
```

A version id from a different project returns 404.

## 4. Export

```bash
curl -s -b "$JAR" -o dataset-v1-coco.zip "$BASE/api/projects/$PROJECT/datasets/$DATASET/export?format=coco"
curl -s -b "$JAR" -o dataset-v1-yolo.zip "$BASE/api/projects/$PROJECT/datasets/$DATASET/export?format=yolo"
```

`format` is required and must be `coco` or `yolo` (otherwise `400 {"error": "format must be one of: coco, yolo."}`).
The zip is streamed. The suggested file name is `athena-<first 8 chars of project id>-v<version>-<format>.zip`.

### Refusals

| Status | Meaning |
| --- | --- |
| `409` | The stored snapshot no longer matches its recorded checksum (rows were altered), **or** an image is missing or changed on disk since the version was made (body lists them under `problems`). |
| `413` | The images would exceed the size cap (2 GiB by default in code). |

Versions freeze box data and image checksums, **not copies of the image bytes**. So deleting a picture that is
part of a version is refused: `DELETE /api/projects/<id>/assets/<asset_id>` returns `409` with
`{"error", "snapshots": [1, 2], "hint"}`. Repeat it with `?remove_from_snapshots=1` to delete anyway; each affected
version is then permanently marked incomplete (`is_incomplete`, `incomplete_reason`, `missing_pictures` in its JSON),
keeps its manifest and checksum, and answers export with `409` and `{"incomplete": true, "missing_pictures": n}`.
Export the versions you need before cleaning up. Replacing a file on disk still makes the export fail with the
affected files listed under `problems`.

Every export attempt, including refused and abandoned ones, is recorded in an append-only log:
`GET /api/projects/<id>/datasets/<dataset_id>/exports` returns `{"exports": [{"id", "format", "bytes", "success",
"status", "error", "user", "created_at"}], "total"}`, newest first.

### Archive layout

Every path inside the zip is generated by Athena (`<6-digit index>_<first 12 hex of sha256>.<ext>`). Filenames,
labels, and storage keys you supplied never become path components. Index and order are deterministic, so
exporting the same version twice gives the same content.

**COCO** (`format=coco`): `images/<split>/…`, one `annotations/instances_<split>.json` per split
(always written, possibly with empty lists), and `manifest.json`.

```
images/train/000001_1fe5b2607b94.png
annotations/instances_train.json   annotations/instances_val.json   annotations/instances_test.json
manifest.json
```

- `bbox` is `[x, y, width, height]` in **absolute pixels** from the top-left; `area` = width × height; `iscrowd` is 0.
- `categories` ids start at **1** in the project's label order. Image and annotation ids are unique across splits.
- `info` carries Athena fields (`dataset_id`, `dataset_version`, `manifest_sha256`, `split`, …); `licenses` is `[]`.

```json
{"id": 1, "image_id": 1, "category_id": 1, "bbox": [20.0, 20.0, 60.0, 40.0], "area": 2400.0, "iscrowd": 0}
```

**YOLO** (`format=yolo`): `images/<split>/…`, matching `labels/<split>/<same stem>.txt`, `classes.txt`,
`data.yaml`, `manifest.json`.

- Each label line is `class cx cy w h`, normalised 0–1, 9 decimals. Class ids start at **0** in the project's
  label order (`classes.txt` is one name per line, same order).
- `data.yaml` lists only splits that have images: `path: .`, `train: images/train`, `nc: 2`, `names: ["damaged", "normal"]`.

```
0 0.250000000 0.400000000 0.300000000 0.400000000
```

## Split rules

Assignment depends only on the seed and the image's SHA-256, never on filename, asset id, or upload order:

1. `position` = the first 8 bytes of `SHA-256("<seed>:<image sha256>")`, read as a big-endian integer and divided by 2^64: a number in [0, 1).
2. Walk `train`, `val`, `test` in that order adding up fractions; the image goes to the first split whose running
   total exceeds `position`. A split with fraction 0 stays empty; floating-point remainder goes to the last non-empty split.

Consequences:

- **Byte-identical images always share a split**, so exact duplicates cannot leak between train and test
  (`duplicates.leaks` is the check; it should be 0). **Near-duplicates (re-saved, resized, cropped) are not detected.**
- The same image lands in the same split across versions that use the same seed and fractions.
- Splits are by hash, so counts are only approximately your fractions, and tiny datasets can produce empty splits
  (reported in `warnings`).

## Manifest

`manifest` (in `GET`/`POST` responses) and `manifest.json` (in the zip, under `dataset`) share this document,
schema `athena.dataset-manifest/1`:

| Field | Contents |
| --- | --- |
| `schema`, `dataset_id`, `version`, `created_at`, `created_by`, `athena_version` | Identity and provenance (`created_by` is `u<id>`). |
| `project` | `{id, name}` at the time of creation. |
| `label_names` | Label list at creation; order defines class ids. |
| `split_config` | `{seed, train, val, test}` actually used. |
| `counts` | `{images, boxes}`. |
| `excluded` | `{suggested_boxes, images_without_accepted_boxes, reason}`. |
| `split_counts`, `class_balance` | Images/boxes per split; boxes per label per split. |
| `duplicates` | `{groups, images_in_groups, leaks, note, examples}` for byte-identical files. |
| `warnings` | Plain-language notes (empty split, label with no boxes, label missing from train). |
| `images[]` | Per image: `asset_id`, `source_filename`, `sha256`, `width`, `height`, `size_bytes`, `content_type`, `split`, `boxes[]`. |
| `images[].boxes[]` | `annotation_id`, `label`, `x`, `y`, `width`, `height` (normalised, 8-decimal **strings**), `origin` (`manual` or `model`), `model_name`, `confidence`, `created_by`, `reviewed_by`. |

Images are sorted by `(sha256, asset_id)` and boxes by `annotation_id`, so the document is stable.

**`manifest_sha256`** is the SHA-256 of that document serialised as canonical JSON (sorted keys, `,`/`:` separators
with no spaces, UTF-8, non-ASCII kept). It does not depend on the export format. `manifest.json` in the zip wraps
it as `{manifest_sha256, manifest_sha256_covers, dataset, export}`, where `export.files` lists the path, byte size,
and SHA-256 of every other file in the archive. Verify an export (tested on a synthetic zip):

```python
import hashlib, json, sys, zipfile

with zipfile.ZipFile(sys.argv[1]) as z:
    doc = json.loads(z.read("manifest.json"))
    canonical = json.dumps(doc["dataset"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == doc["manifest_sha256"], "manifest hash mismatch"
    for entry in doc["export"]["files"]:
        assert hashlib.sha256(z.read(entry["path"])).hexdigest() == entry["sha256"], entry["path"]
print("ok")
```

The hash proves the zip is the version that was recorded and is internally consistent. It is not a signature:
anyone with database access could rewrite both. Athena rechecks the stored rows against the hash on every export.

## Immutability

Versions, their images, and their boxes are write-once: updates and single-row deletes are refused by the
application. The one exception is a version's `incomplete_reason` text, which can only be appended to (see above). Deleting the whole project removes its versions. Editing, accepting, or deleting boxes afterwards
does not change an existing version; create a new one instead.

## Not covered yet

Pictures that came from operator corrections ([improve loop](improve-loop.md)) are ordinary pictures here: their boxes are **Needs review**, so a
snapshot taken before a person accepts them leaves the boxes out (and counts them in `excluded`), and a picture with no accepted box at all, such as one
that only has a false alarm, is not in the snapshot. Once accepted, a correction box is listed in the manifest like any other, with `origin: "model"` and
`model_name: "operator feedback (model v1)"` (the number is the version of the model that made the result); the link back to the check-screen result itself is
kept in the project (`FeedbackImport`, `origin: "operator_feedback"` in `GET …/assets`), not in the manifest.

The console panel shows only the common steps (the API has more); creating a version does no near-duplicate check (a separate read-only `GET …/datasets/health` reports near-copies in advance, see the [API reference](../api-reference.md#console-http-api-the-teach-flow-additions), and it has been tried only on generated pictures); no re-import of an exported dataset;
no check against decoded image dimensions; no rights or licence review of the images (a version records only that
a person accepted the boxes); formal review of the COCO/YOLO format conventions used here has not been done.
