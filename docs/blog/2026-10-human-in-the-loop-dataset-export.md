---
title: "A dataset you can trust starts with a person saying yes"
date: 2026-10 (draft)
status: first draft, not yet published
---

# A dataset you can trust starts with a person saying yes

*Draft. Status: the dataset-version API described here exists and is tested, but it is backend-only. There is no screen for it in the Athena console yet.*

Auto-labelling is useful, and it is also the easiest way to quietly train a model on its own guesses. Athena's console can run a segmentation model and a text-image model over your pictures and propose boxes. Those proposals are marked **Needs review** and, by design, they are not labels. This post is about the next step: what happens when you want to turn the labels you *do* trust into a dataset.

## Only reviewed boxes go in

When you ask for a dataset version, Athena includes the boxes a person drew or accepted. Anything still marked **Needs review** is left out, and the response tells you how many were left out and why. If nothing has been accepted yet, the request is refused with a plain message rather than producing an empty file.

The rule is small on purpose: a model's confidence score never becomes ground truth by default, and a dataset's contents can be described as "boxes a person looked at." That does not make them *correct*. Athena has not measured label quality or auto-label accuracy.

## Versions are frozen

A dataset version is a numbered snapshot. Once it exists, the application refuses to update or individually delete it. Editing boxes afterwards changes the project, not the version; to include the edits you create version 2.

Each version stores a manifest: the project and labels at that moment, the split settings, counts, and every image with its SHA-256 and boxes. The manifest has its own SHA-256, computed over a canonical serialisation, and every export recomputes it from the stored rows and refuses to continue (HTTP 409) if they no longer agree. That catches accidental drift and casual tampering. It is not a signature: someone who can rewrite the database can rewrite both. One more honest limit: a version keeps checksums, not copies of the pictures. Delete an image later and that version will tell you which file is missing and decline to export.

## Splits that cannot leak

Train, validation, and test sets go wrong in a classic way: the same picture ends up on both sides. Athena assigns each image to a split from a hash of the seed and the image's own SHA-256. It does not look at filenames, upload order, or ids. Two byte-identical files always land in the same split, and the same image lands in the same place whenever you reuse the seed and fractions. The default is 80/10/10, configurable.

Hash-based assignment has a cost: split sizes are approximate. With a handful of pictures a bucket can come out empty, so if a split you asked for would have nothing in it, Athena moves one whole group of identical pictures from the fullest split into it, deterministically, so identical files still stay together. With fewer pictures than splits, a split stays empty and the readiness check says so.

What it does not do is detect near-duplicates. A resized or re-saved copy of an image has a different hash and can still leak. The manifest says so in its own `duplicates.note`.

## Validation that says no

Before a version exists, every accepted box is checked: it must have a positive size inside the image, its label must match the project's label list exactly (case-sensitive), and its image file must exist and still match the checksum recorded at upload. A single failure blocks the version, and the response lists every problem with the image and box involved.

## What the data looks like

This is synthetic: six generated 200×100 images, five with one accepted box each and one carrying only an unreviewed proposal. The readiness check, before creating anything:

```json
{
  "ready": true,
  "counts": {"images_total": 6, "images": 5, "accepted_boxes": 5, "suggested_boxes": 1},
  "preview": {
    "split_counts": {"train": {"images": 3, "boxes": 3}, "val": {"images": 1, "boxes": 1}, "test": {"images": 1, "boxes": 1}},
    "warnings": []
  }
}
```

Five images, ten percent each for validation and test: by hash alone the validation set would have come out empty, which is how this example first looked. The rebalance rule above fixed it, and that is the kind of thing worth knowing before you train. One box in the manifest, exact fractions stored as strings:

```json
{"label": "damaged", "x": "0.10000000", "y": "0.20000000", "width": "0.30000000", "height": "0.40000000",
 "origin": "manual", "created_by": "u1", "reviewed_by": null}
```

The same box in a COCO export (200×100 pixel image, `[x, y, width, height]` in pixels) and a YOLO export (class `0`, normalised centre and size):

```
{"bbox": [20.0, 20.0, 60.0, 40.0], "category_id": 1, ...}
0 0.250000000 0.400000000 0.300000000 0.400000000
```

People appear only as opaque ids such as `u1`; no usernames or emails are written. The zip's own `manifest.json` lists the size and SHA-256 of every file, so you can verify an archive with a few lines of Python (see the [guide](../guides/data-export.md)).

## What exists, and what does not

Exists and tested: readiness check, version creation, listing, COCO and YOLO zip export, the validation and split rules above, and round-trip tests that read the exports back and compare them to the database.

Does not exist yet: any console UI for this, so today it is `curl` or a script; an audit log of exports; near-duplicate detection; re-importing a dataset; a check against decoded image dimensions; and any review of whether you have the rights to use the images. A version records that a person accepted the boxes, not that the pictures are yours to train on. There is also no training step yet, so nothing consumes these versions inside Athena. The COCO and YOLO conventions have not had a formal review against the specifications.

If you want to try it, the [data export guide](../guides/data-export.md) has the routes, parameters, error cases, and verification snippet.
