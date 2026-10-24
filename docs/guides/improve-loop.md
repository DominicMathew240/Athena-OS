# Improve the model from check-screen corrections

> **Prototype, and not yet tried by real operators.** The steps below exist in the console and are covered by automated tests that use generated
> pictures and stand-in models. Nobody has run the whole loop on a real production line, and no number in the product says how much a correction
> round improves a real model. The roadmap gate for this feature is open (see [`ROADMAP.md`](../../ROADMAP.md), M4). Engineering read, not a promise.

## What this is for

On a check screen, an operator can say "this is right", "this is wrong", or "the computer missed something". Those answers are saved. This guide is for
the person who then wants the computer to learn from them: a **Labeler** (or above) pulls the corrected pictures into the project, checks the boxes,
teaches again, looks at how the new model compares with the one in use, and only then decides whether to switch.

**Nothing in this loop happens by itself.** Saying "this is wrong" on a check screen never changes the model that is in use, never starts teaching and never
adds anything to a training set. Every step below is a button a person presses.

## The steps

1. **Operators give feedback on the check screen** (as before): "This is right", "This is wrong", "The computer missed something" (they may draw where).
2. **A Labeler opens the project, part "Pictures", card "Corrections from check screens".** It says how many pictures with corrections are waiting.
   Press **Add corrected pictures for review** (it adds up to 50 at a time; press again for more).
   - Each corrected picture is **copied** into the project. The original on the check screen is not touched.
   - A box the operator **confirmed** arrives as the computer's own box; a box the operator **drew** for something missed arrives as that box.
     Both are marked **Needs review**.
   - A box the operator called **wrong** is *not* turned into an example. A wrong box never becomes a "right answer".
   - If the operator said something was missed but did not draw where, no box is made. The card tells you that something was missed there.
3. **Review the boxes.** Open the pictures (the Pictures list can show only the ones that came from check screens) and accept, fix or delete each box as you
   would for any suggestion. **Until you accept a box it is not used for teaching, for a training set or for an export.**
4. **Teach the computer.** Press the usual button. If you accepted new boxes it saves a fresh snapshot first. The old model keeps running meanwhile.
5. **Read the result.** The results card has a block **Compared with the model in use**: how many of the test boxes each model found, with the
   words *Better*, *About the same*, *Worse* or *Cannot tell*, and why. It is information. It does not stop you and it does not decide for you.
6. **Approve (or not).** Moving a model to trial and approving it are separate steps that need the Trainer and Approver roles, a note, and, for weak results,
   a tick. Approving retires the previous model; "Go back to the previous model" restores it.

## What the card will not do

- **Pictures turned by the camera are left out.** If the picture file says the camera rotated it (an EXIF turn), the boxes on the check screen and in the file
  would not line up, so the picture is skipped and listed with a reason. Athena does not turn pictures upright for you. Upload a corrected copy by hand if you need it.
- **"Only false alarms" pictures come in with no boxes.** The picture is added, but a picture with no accepted box is left out of every snapshot, so it
  teaches nothing. Athena has no way yet to use "there is nothing here" as an example. If something real is in that picture, draw its box.
- **Other pictures it may skip:** the file is gone or changed since the check, it is too big (20 MB) or has too many pixels, or the storage could not save it
  (for example a full disk). A box can also be left out when its label is no longer in the project (capital letters count). Every skip is listed with a reason.
- **The auto-suggest button leaves these pictures alone.** A re-run would erase the unreviewed boxes before anyone judged them. The job says how many it left out.
- **"Accept these suggestions" does not know the difference.** The bulk accept button accepts every suggested box above the score you chose, including boxes from
  check-screen corrections. The card warns you first, but nothing stops you. Look at the corrected pictures before pressing it.

## Reading "Compared with the model in use"

- It runs the model in use on the same held-out test pictures and counts, box by box, which model found each box a person drew.
- **Better / Worse** need a real gap: at least 3 boxes and at least 10% of the test boxes. Smaller gaps read **About the same**.
- **Cannot tell** appears when the test is not fair, or when any label has fewer than about 30 test boxes (a label with none counts). Rare labels will
  often read "cannot tell"; the honest fix is more labelled pictures of that label, not a different number.
- **"Not a fair comparison"** means the model in use had already seen some of the new model's test pictures, or Athena cannot tell what it was taught from
  (for example an imported model). The sentence says how many pictures overlapped. Copies of the same scene are not detected, so even a fair test can flatter.
- It is only worked out when there is a model in use, the new run has a held-out test group, and the model in use can run on the teaching computer.
  Models taught before this existed show a plain "no comparison was saved".
- Extra or misplaced boxes are shown but never change the word.

## How it works, for engineers

```text
check-screen feedback (FeedbackRecord, append-only)
        │  GET  /api/projects/<id>/feedback/corrections        (list, any role)
        ▼  POST /api/projects/<id>/feedback/corrections/import  (Labeler+; ≤ 50 per request)
copy of the picture (Asset) + Needs-review boxes (origin "model", model_name "operator feedback (model vN)")
        + FeedbackImport link row (observation, model version + artifact SHA-256, kinds, annotation ids, imported_by)
        │  a person accepts / edits / deletes boxes           (ordinary review controls)
        ▼
accepted boxes ─► snapshot (DatasetVersion) ─► "Teach the computer" ─► run ─► candidate ModelVersion
        │                                                              evaluation["comparison"] vs the approved model
        ▼  stage ─► approve (Trainer, then Approver, note) ─► rollback pointer kept
```

- **Selection rule** (`effective_feedback`, `build_plan` in `web/projects/feedback_import.py`): the latest verdict per shown detection wins; a missed-object report always counts.
  Right → the model's box with its score; wrong → nothing; missed with a box → the operator's box; missed without a box → counted only.
- **Idempotent and isolated.** One `FeedbackImport` row per check-screen result ever (database uniqueness, row lock); each result is its own transaction; a saved file is
  removed again if the write fails. The row stores ids as text next to its links, so deleting a picture later leaves the record readable.
- **What never happens automatically:** no model, run, dataset version or check screen is read or written by the import; feedback is never auto-accepted; a new model
  is never staged or approved by a worker; the comparison never gates approval; the auto-suggest job never touches these pictures.
- **Where the limits live:** pending/cannot-import counts are taken from the newest 2,000 results still waiting (`SCAN_LIMIT`), imported/total counts are exact; the
  list shows at most 100 entries. The list only checks the file's existence, size and type; rotation, pixel count and changed bytes are found by an import attempt.
- **Comparison internals** (`web/projects/training/comparison.py`): `compare_with_approved` runs the approved model's own backend (`predict_split`) over the candidate's test items, pairs
  found/not-found per drawn box (`b` = only new, `c` = only old), and `decide` applies the verdict rules to counts. Fairness uses the SHA-256 lists in the dataset manifests
  (`_fairness`, `_manifest_intact`). The reader (`plain_comparison`) recomputes verdict and text from the counts and refuses a block that disagrees with its evaluation.
- Shapes, reason codes and examples: [API reference, improve loop](../api-reference.md#console-http-api-the-improve-loop-2026-10-24-prototype). What was measured about quality, and what was
  not: [model-quality.md](model-quality.md#comparing-a-new-model-with-the-model-in-use-prototype-synthetic-checks-only). Roles: [access-roles.md](access-roles.md).

## Known gaps (open follow-ups)

Pictures turned by the camera are skipped instead of turned upright; a false-alarm-only picture cannot become a "nothing here" example; a model that hangs
during the comparison pass is not stopped by a timeout; per-label cut-offs and calibration do not exist; the whole loop has not been traced on real
operators and real imagery; the bulk accept has no server-side guard. The plan and the open owner decisions are in
[`docs/plans/2026-10-23-improve-loop.md`](../plans/2026-10-23-improve-loop.md).
