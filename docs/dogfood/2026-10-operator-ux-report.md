# Dogfood QA Report — first-time operator journey

**Target:** local test instance, `http://127.0.0.1:8806` (fresh database, DEBUG, loopback). **Local test instance — prototype, synthetic data, toy model.**
**Date:** 2026-10-16
**Scope:** sign-in → project → pictures → labels → boxes → auto-suggest → review → snapshot → COCO/YOLO download → teach → results → trial → approve → check screen → operator run → feedback; adversarial checks; 390 px phone pass; spec items (a)–(d) of the integration task
**Tester:** Hermes Agent (W10), automated Chromium via Playwright, acting as a first-time warehouse security-scanner operator
**Data:** 14 synthetic 640×480 pictures (knife / laptop / bottle shapes drawn with Pillow) + 1 exact duplicate + `shift-notes.txt` + a 22 MB `huge-scan.jpg`; one user `supervisor` created with `createsuperuser`.

> Everything here comes from a local test instance. It says nothing about real scanners, real operators or real accuracy.

---

## Executive summary

**Verified outcome.** One automated first-time-operator walk-through, in real Chromium against a fresh local database, completed the whole path from the sign-in page to a confirmed operator decision: project → 15 pictures (+ a non-picture, an oversized file and an exact duplicate) → labels → boxes (mouse, typed position, and **real SAM + CLIP auto-suggest** from the local cache, no seeding) → review → snapshot → COCO and YOLO downloads → **real CPU training** (30 and 100 rounds) → results → trial → approval with a note → check screen → operator runs with findings → per-finding and whole-result feedback. Model rows were checked afterwards: Model 1's `artifact_sha256` equals the value recorded before it was approved, both models' recorded hashes equal the SHA-256 of their files on disk after 23 operator runs, feedback records and picture removals, and the rows (`stage`, hash) were identical before and after a last operator run with feedback. The only stage changes were the ones made on purpose (trial, approve, and Model 1 retiring when Model 2 was approved).

This walk-through found that **the console was dead on load** (F1, Critical, left by the interrupted W9 edit; all 414 tests were green). It is fixed and every item of the integration spec (picture removal with snapshot confirmation and the Incomplete badge, the Check screens list, dialog focus traps, one icon approach, shared wording) was re-verified in the browser. Component styles were then unified across the console, the check-screen builder and the operator screen and measured at 390/768/1024/1440 px on fresh loads (no horizontal scroll anywhere).

**Not shown by this report:** that real operators can use it (no operator took part), anything about a real scanner, a real screen reader, Firefox/Safari, a GPU, or accuracy. The model here is a toy trained on synthetic shapes; its "Very sure · 99%" is a raw score. The M2 exit gate is **not** met and was not ticked.

| Severity | Count | Fixed | Open |
|----------|-------|-------|------|
| 🔴 Critical | 1 | 1 | 0 |
| 🟠 High | 1 | 1 | 0 |
| 🟡 Medium | 8 | 6 | 2 |
| 🔵 Low | 6 | 3 | 3 |
| **Total** | **16** | **11** | **5** |

**Overall assessment:** the happy path works end to end and its wording is plain; the defects found were load-time, batch-upload, consistency and long-input problems, all fixed; what remains open is a default split that leaves no Final-test picture, an approval-policy question, and small polish.

---

## Issues

### F1 — Console script dies on load: `Cannot access 'trDialog' before initialization` (Critical, Functional/Console) — FIXED

| Field | Value |
|---|---|
| **Severity** | Critical |
| **Category** | Functional / Console |
| **Status** | Fixed |

- **URL:** `/` (first load after sign-in, empty database)
- **Seen:** page rendered but dead: "PROJECTS 00", "AUTO-SUGGEST Checking…" forever, no project could be created. Left over from the interrupted W9 edit: `Athena.manageDialog(trDialog)` ran ~1,500 lines before `const trDialog` was declared. All 414 unit tests passed because they only grep the source.
- **Fix:** call moved directly under the declaration in `console.js`; a regression test now pins the order. Re-tested on a fresh load: zero page errors; status cell reads "Not running" and the empty project list shows its empty state.
- **Lesson:** source-grep tests cannot see load-time errors; every page was re-loaded in Chromium with page-error capture for the rest of this report.

---
### F2 — One wrong file in a batch rejected all 17 pictures, and the message did not say which one (High, UX/Functional) — FIXED

| Field | Value |
|---|---|
| **Severity** | High |
| **Category** | UX / Functional |
| **Status** | Fixed |

- **URL:** `/` → project → Pictures. Chose 14 good pictures + 1 exact duplicate + `shift-notes.txt` (not a picture) + `huge-scan.jpg` (22 MB).
- **Seen:** "The pictures were not added. Each image must be 20 MB or smaller." Nothing saved, no file named, and the file chooser still said "17 files", so a retry failed the same way.
- **Fix:** the console now checks each file before sending, uploads the usable ones and says what it left out ("Left out: “huge-scan.jpg” is 22 MB, and the limit is 20 MB; “shift-notes.txt” is not a JPEG, PNG or WebP picture."). The server (still the authority) now names the offending file in its refusal. Re-tested on fresh load: 15 added, 2 named as left out. The duplicate was accepted as a separate picture (by design: identical copies are kept in one split group later).

Screenshot: `screenshots/05-10-after-upload.png` (local test instance — prototype, synthetic data, toy model).

---
### F3 — Picture list order changed on every load (Medium, UX) — FIXED

| Field | Value |
|---|---|
| **Severity** | Medium |
| **Category** | UX |
| **Status** | Fixed |

- `Asset` queryset used `.annotate(Count)`, which ignores `Meta.ordering` (Django ≥ 3.1), so the Pictures list came back in arbitrary order (06, 11, 10, 14, 07 …). Explicit `.order_by("created_at","id")` added.

---
### F4 — Default 80/10/10 split on 15 pictures leaves 0 pictures in "Final test", and the snapshot card still says "Final test 10%" (Medium, UX/Content) — FIXED (see resolution below)

| Field | Value |
|---|---|
| **Severity** | Medium |
| **Category** | UX / Content |
| **Status** | Open |

- The readiness panel does warn ("No pictures would go into “Final test”…you would have no honest final score"), which is good. But the saved snapshot card says "Pictures were divided up as Training 80%, Checking 10%, Final test 10%" next to a table with Final test = 0. A first-time operator will not know to open "Advanced". Split rounding lives in `datasets/` (not in this task's ownership); suggested fix: guarantee ≥1 Final-test picture once ≥10 pictures exist, or pre-select the split in the form when the warning appears.

Screenshot: `screenshots/10-17-snapshot-saved.png` (local test instance — prototype, synthetic data, toy model).

---
**Resolution (after the dogfood run):** `projects/datasets/splits.py` `assign_splits` now moves one whole group of identical pictures from the fullest split into any requested split that the hash left empty (needs at least as many distinct pictures as splits). Regression test `SplitTests.test_a_15_picture_snapshot_has_a_final_test_group` fails if the rebalance is removed (checked by temporarily reverting it). Not re-verified in a browser; the card's "Final test 10%" text still shows the requested share, not the achieved count.

### F6 — Buttons of one intent were four different sizes across the three screens (Medium, Visual/Consistency) — FIXED

| Field | Value |
|---|---|
| **Severity** | Medium |
| **Category** | Visual / Consistency |
| **Status** | Fixed |

Measured computed styles: console primary 13 px/40 px high, console secondary 12 px/36 px (a Cancel next to a primary button in a dialog was 4 px shorter), "Delete this project" 12 px/750 weight, check-screen builder + operator buttons 15 px/45 px. Fixed to one scale: standard buttons 13 px, weight 700, 40 px high (44 px at ≤ 768 px, as before); compact list-row buttons 12 px, 30 px high (were 28/29/30); the page-local `.cs-page .primary/.secondary` overrides were removed; two-line decision buttons share the padding. Danger buttons are one style (red outline).

Screenshot: `screenshots/34-final-11-builder-top.png` (local test instance — prototype, synthetic data, toy model).

---
### F7 — Text fields were seven different recipes; one was bold; borders and size differed across screens (Medium, Visual) — FIXED

| Field | Value |
|---|---|
| **Severity** | Medium |
| **Category** | Visual |
| **Status** | Fixed |

Heights 32/34/36/39/40/42/44, font 13/14/16 px, one input rendered bold (it inherited its label's weight, e.g. "New label name" showed typed text in bold), two border greys. Now: 40 px high, 14 px, weight 400, border `#5e605b` everywhere (list-row selects stay 32 px by design); on phones and tablets (≤ 768 px) every field is 16 px, which stops iPhones zooming the page on focus. *iPhone behaviour itself could not be verified here.*

Screenshot: `screenshots/28-final-03-labels.png` (local test instance — prototype, synthetic data, toy model).

---
### F10 — A very long project name, task or label widened the whole page (Medium, Visual) — FIXED

| Field | Value |
|---|---|
| **Severity** | Medium |
| **Category** | Visual |
| **Status** | Fixed |

- 135-character name (field keeps 120), 120-character unbroken task, 64-character unbroken label. **Desktop (1366 px):** page scrolled sideways (scrollWidth 2250 > 1366): the projects table grew to 2041 px and its screen-reader-only "Open" header (absolutely positioned inside a box that was not `position: relative`) escaped the scroll box. **390 px:** the project header grew to 1047 px. Fixed: `.table-wrap{position:relative}`, wrapping table cells, `min-width:0` on workspace grid children. Re-tested on fresh loads: scrollWidth = innerWidth at 1366 and 390. Over-long labels get a clear refusal ("“a very long label na…” is too long. Label names can be up to 64 characters.").

Screenshot: `screenshots/24-65-long-names-workspace-1366.png` (local test instance — prototype, synthetic data, toy model).

---
### F11 — Back button / "All projects" opened a different project than the one just left (Medium, UX) — FIXED

| Field | Value |
|---|---|
| **Severity** | Medium |
| **Category** | UX |
| **Status** | Fixed |

- Open "Bag scanner photos" → "Edit" a check screen → browser Back: the console showed the newest project ("Warehouse Terminal B…"), not the one the person came from. The open project is now kept in the address (`/#project=<id>`), the builder's "All projects" link carries it too, and a reload keeps it. Verified after fix: Back lands on "Bag scanner photos".

---
### F12 — Check-screen builder lost unsaved edits silently when leaving the page (Medium, UX) — FIXED

| Field | Value |
|---|---|
| **Severity** | Medium |
| **Category** | UX |
| **Status** | Fixed |

- The console already asked before discarding unsaved labels; the builder did not (Back after editing the title: no warning). It now uses the browser's leave-page prompt while edits are unsaved (cleared after save).

Screenshot: `screenshots/40-53-builder-confirm.png` (local test instance — prototype, synthetic data, toy model).

---
### F15 — A model that found none of the test boxes can still be approved and put on a check screen (Medium, UX/Safety) — OPEN, needs a product decision

| Field | Value |
|---|---|
| **Severity** | Medium |
| **Category** | UX / Safety |
| **Status** | Open (decision) |

- Model 1 ("found 0 of 4 boxes") was moved to trial and approved after typing a note. The dialog does warn ("this model found none of the boxes people had drawn. Read its results before you rely on it") and the operator screen then says "Please look closely" for every picture, never "nothing found" as a final answer, so a person still decides. But nothing stops an administrator from approving a useless model. Options for the product owner: block approval below a floor, or require a second approver. Gating lives in `training/` (backend), outside this task.

Screenshot: `screenshots/14-28-approve-dialog.png` (local test instance — prototype, synthetic data, toy model).

---
### F5 — "Boxes in this picture" side list is blank when a picture has no boxes (Low, UX) — OPEN

| Field | Value |
|---|---|
| **Severity** | Low |
| **Category** | UX |
| **Status** | Open |

- Status line under the picture explains it ("No boxes yet. Pick a label and drag…"), but the side list shows only its heading.

Screenshot: `screenshots/07-13-annotation-with-suggestion.png` (local test instance — prototype, synthetic data, toy model).

---
### F8 — Check-screen builder/operator headings and cards were a different scale from the console (Low, Visual) — FIXED

| Field | Value |
|---|---|
| **Severity** | Low |
| **Category** | Visual |
| **Status** | Fixed |

h1 36 px vs 35 px, card titles 20 px vs 17 px, card padding 16–22 px vs 14 px, help text 14 px vs 13 px. Aligned to the console tokens. Intentional differences kept and documented: the builder/operator use a top bar instead of the console's left rail; the yellow disclaimer title (15 px) and item titles (17 px) are separate components.

---
### F9 — Small content slips (Low, Content) — FIXED

| Field | Value |
|---|---|
| **Severity** | Low |
| **Category** | Content |
| **Status** | Fixed |

"It drew 1 box that were wrong." → "that was wrong". The upload hint under "Choose pictures" rendered bold; the small "Move down"/"Remove" buttons were 28–30 px high at different heights.

---
### F13 — Small dialog controls were 28–34 px high on phones (Low, Accessibility) — FIXED

| Field | Value |
|---|---|
| **Severity** | Low |
| **Category** | Accessibility |
| **Status** | Fixed |

- Annotation dialog close button 34 px, "How to mark items" / "Add a box by typing" summaries 28–34 px, per-box label selects 32 px; all 44 px at ≤ 850 px now. (Inline text links, e.g. the screen's web address, stay text-height.)

---
### F14 — A few controls are still under 40 px (Low, Accessibility) — OPEN

| Field | Value |
|---|---|
| **Severity** | Low |
| **Category** | Accessibility |
| **Status** | Open |

- At 390 px: the file chooser (38 px) and one nav pill (36 px). At 1024/1440 px: 25 compact controls (30 px list-row buttons, 32 px per-box selects). All are over WCAG 2.2's 24 px minimum; none is a blocker. Not changed: compact row buttons are a deliberate size.

---
### F16 — "Suggest boxes automatically after upload" is ticked by default and starts a ~2-minute CPU job after every upload (Low, UX) — OPEN

| Field | Value |
|---|---|
| **Severity** | Low |
| **Category** | UX |
| **Status** | Open |

- The person only asked to add pictures. A message follows ("Auto-suggest has started; its suggestions will need your review.") and the suggestions never count until accepted, so nothing is lost, but on a shared CPU it also delays training. Consider off-by-default, or a one-line note beside the checkbox saying how long it takes.

Screenshot: `screenshots/05-10-after-upload.png` (local test instance — prototype, synthetic data, toy model).

---

## Issues summary table

| # | Title | Severity | Category | Status |
|---|-------|----------|----------|--------|
| 1 | Console script dies on load: `Cannot access 'trDialog' before initialization` | Critical | Functional / Console | Fixed |
| 2 | One wrong file in a batch rejected all 17 pictures, and the message did not say which one | High | UX / Functional | Fixed |
| 3 | Picture list order changed on every load | Medium | UX | Fixed |
| 4 | Default 80/10/10 split on 15 pictures leaves 0 pictures in "Final test", and the snapshot card still says "Final test 10%" | Medium | UX / Content | Fixed |
| 6 | Buttons of one intent were four different sizes across the three screens | Medium | Visual / Consistency | Fixed |
| 7 | Text fields were seven different recipes; one was bold; borders and size differed across screens | Medium | Visual | Fixed |
| 10 | A very long project name, task or label widened the whole page | Medium | Visual | Fixed |
| 11 | Back button / "All projects" opened a different project than the one just left | Medium | UX | Fixed |
| 12 | Check-screen builder lost unsaved edits silently when leaving the page | Medium | UX | Fixed |
| 15 | A model that found none of the test boxes can still be approved and put on a check screen | Medium | UX / Safety | Open (decision) |
| 5 | "Boxes in this picture" side list is blank when a picture has no boxes | Low | UX | Open |
| 8 | Check-screen builder/operator headings and cards were a different scale from the console | Low | Visual | Fixed |
| 9 | Small content slips | Low | Content | Fixed |
| 13 | Small dialog controls were 28–34 px high on phones | Low | Accessibility | Fixed |
| 14 | A few controls are still under 40 px | Low | Accessibility | Open |
| 16 | "Suggest boxes automatically after upload" is ticked by default and starts a ~2-minute CPU job after every upload | Low | UX | Open |

## Testing coverage

### Pages and states tested
Sign-in (wrong and right password); console dashboard (empty, one project, two projects, long names, project open); every panel of the console (checklist, labels, pictures, auto-suggest, snapshots, teaching, models/results/history, check screens); dialogs (new project, picture annotation, decision, confirmations); check-screen builder (new, edit, retired model, unsaved edits); operator screen (empty, wrong file, nothing found, two findings, feedback, reject with and without a note); the same at 390 px.

### Features exercised with real clicks and keys
Everything in the executive summary, plus: keyboard-only focus ring over the first 25 Tab stops on a fresh load; Tab/Shift+Tab traps in six dialogs; double-clicks on seven submit buttons; refresh during training; session rows deleted mid-edit; two tabs editing labels; Back button and "All projects"; unsaved-labels confirmation; helper program stopped (auto-suggest); a snapshot made incomplete from a second client while the page was open.

### Journey log (verified in Chromium, steps 1–9)
- **Sign-in** page and wrong-password message OK ("Please enter a correct username and password…"). Empty dashboard shows an empty-state with "Create your first project".
- **Create project:** empty submit → native "Please fill out this field."; **double-click on "Create project" made one project** (button disables itself). Dialog Tab/Shift+Tab: 0 focus escapes in 24 presses.
- **Labels** typed + Enter; duplicate "Knife" refused with a plain message ("“Knife” is already a label in this project.").
- **Upload** (after F2): 15 added; bad files named. **Auto-suggest ran for real** (SAM `facebook/sam-vit-base` + CLIP `clip-vit-base-patch32` from the local Hugging Face cache, downloads switched off): 15/15 pictures, 10 suggested boxes, ~8 s per picture on CPU. *Not seeded.* Quality on these synthetic shapes: 7 of 10 matched a real item at ≥55 % overlap and were accepted; 3 were wrong (e.g. one box over a whole laptop+knife) and were rejected; items SAM missed were drawn by hand.
- **Boxes:** drawn by real mouse drag on 8 pictures, typed via "Add a box by typing its position" on 5; "Review suggestions" button text replaces "Mark items" on pictures with suggestions. After review: 20 accepted boxes, checklist steps 1–4 "Done".
- **Snapshot** saved (double-click → one snapshot). Second snapshot with 60/20/20 split so there is a Final-test group. **COCO** (104 KB, 19 files: images/{train,val,test}, annotations/instances_*.json, manifest.json) and **YOLO** (106 KB, 33 files: images/, labels/, classes.txt, data.yaml, manifest.json) downloaded through the page.
- **Teach:** real training (torchvision Faster R-CNN, CPU, 30 rounds, batch 4) started with a double-click → one run; a refresh mid-run kept the progress panel and disabled "Start teaching" with a reason.

### Journey log (steps 10–16)
- **Results (Model 1, 30 rounds, CPU, ~2 min):** "found 0 of 4 boxes (0%)… drew 2 wrong boxes". Expected for a random-start toy model on 11 pictures; the page says so plainly ("This report rests on only 2 pictures and 4 boxes… not a guarantee"). *Toy model; says nothing about real accuracy.*
- **Trial → approve:** confirm button stays disabled until a note is typed (empty note impossible); dialog Tab/Shift+Tab 0 escapes; Escape returns focus to the opener; after confirming, focus lands on the model title; double-click confirm = one decision. History shows 3 rows (taught by "Athena (automatic)", trial, approved) with the notes. The approval dialog warns "this model found none of the boxes people had drawn" before the person can confirm. Model row: stage `candidate → trial → approved`; `artifact_sha256` recorded for the unchanged-check at the end (`4460f1c8…21f5`, 76,057,419 bytes).
- **Check screen:** builder (empty-title submit → "Give the screen a title."; double-click create → 1 screen), `/check/bag-check-lane-4` opens from the new "Check screens" list on the project page (Open the screen + Edit links; checklist step 7 flips to Done).
- **Operator screen:** no picture / text file chosen → plain refusals ("This screen takes JPEG photo, PNG picture, WebP picture only. The file you chose is a TEXT/PLAIN file."). A real run on the worker returned "Please look closely – the computer found nothing it is at least 50 % sure about" (also says it had 18 weaker guesses). Reject with no note → alert + focus moves to the note box; with a note → "Recorded: You rejected this result at 8:02 PM." Nothing is trained from feedback, and the page says so.

### Step 1 verification of the integration spec (real Chromium, fresh loads)
| Item | Result |
|---|---|
| (a) Remove a picture that is in snapshots | **Works.** 1st confirm "Remove the picture “…”?" with **Keep the picture** focused by default → request returns 409 `{snapshots:[1,2]}` → 2nd confirm "“bag-scan-01-copy.png” is in snapshots 1 and 2 … can no longer be downloaded, because the picture would be missing" with **Keep the picture** / **Remove it anyway** → `?remove_from_snapshots=1` → 200 `{deleted, snapshots_marked_incomplete:[1,2]}`. Status line "Removed … Snapshots 1 and 2 can no longer be downloaded."; focus moves to the Pictures heading. Escape/keep paths return focus to the "Remove picture" button and send no delete. The two 409s are the only network errors, and are expected. |
| (a) Incomplete badge | "! Incomplete: 1 picture removed" (red 2 px outlined badge + words, not colour alone), reason paragraph on the card, download button disabled with `aria-describedby` pointing at the reason. List header: "2 snapshots cannot be downloaded because pictures were removed." |
| (a) Export 409 `{incomplete:true}` | Stale page (picture removed from a second client after load) → click download → "Snapshot 3 can no longer be downloaded: 1 picture was removed…". *Improvement made:* the list is now redrawn so the card also gets its Incomplete badge (before, the button stayed enabled until reload). |
| (b) Check screens list | Empty state before approval ("You need an approved model first: teach one above, read its results, and approve it."), then one row: name, Switched on, "Uses Model 1 · web address /check/…", **Open the screen** (`/check/<slug>`) and **Edit** (`…/check-screen/setup?app=<id>`). Checklist step 7 flips to Done. |
| (c) Dialogs | Tab-ring checked with 30 Tab + 30 Shift+Tab in each: new project (7 stops), picture annotation (14), remove picture (2), delete project (2), trial/approve decision, builder "Let the screen decide without a person?" (2). **0 focus escapes in all.** Escape restores focus to the opener in all six (picture annotation: to the same "Mark items" button). Focus ring on the dialog's default button: 3 px blue + 2 px white halo (visible on the black button). No `confirm()`/`alert()` anywhere (grep and runtime). On a fresh load the first 25 Tab stops of the dashboard all showed a ring. |
| (d) Consistency | See F6–F9 below. After the fixes the shared components measure identically on all three screens at 390/768/1024/1440 (fresh load each, `scrollWidth == innerWidth` everywhere). |

### Adversarial results that needed no change (verified)
- **Double-click** on: Create project, Save a snapshot, Start teaching, Approve, Create this screen, Check this picture, Reject: exactly one record each (counted in the database: 1 project, 1 screen, 1 training run per click, 21 observations = 21 deliberate runs).
- **Refresh during training:** the progress panel resumes at the real percentage and "Start teaching" stays off with a reason.
- **Session expiry** (session rows deleted while the page was open): labels save → red banner "Your session has ended, so your latest changes were not saved. Sign in again…", the unsaved labels stay on screen, Save-state says "Not saved. Your session has ended."; drawing a box → "That change was not saved. Your session has ended." (the box is not kept); "Sign in again" → sign-in page → back to the console. 5 expected 401s, no page errors.
- **Two tabs editing labels:** the stale tab's save is refused with "The label “laptop” no longer exists in this project. Reload the page to see the current labels, then make your change again." and nothing is overwritten (server kept the other tab's rename).
- **Wrong-file / empty states** on every panel: a brand-new project shows reasons instead of dead buttons ("You need at least one accepted box before you can save a snapshot.", "“Start teaching” is switched off: save a snapshot first.", "You have no check screens yet…").
- **390 px phone pass** over dashboard, checklist, labels, pictures, snapshots, teaching, models with results, check-screen list, annotation dialog (boxes drawn with the mouse; overlay and canvas rect identical to the image rect), confirm dialog, builder, operator result: no horizontal scroll anywhere; contact sheets read by eye: no clipped text, no overlap.

## Could not verify

- **A real scanner** or any real X-ray / camera feed; only uploaded synthetic pictures were used.
- **A real screen reader** (VoiceOver, NVDA, JAWS): accessible names, roles, live regions and focus order were checked from the DOM and with the keyboard only.
- **Firefox and Safari** (including iPhone zoom behaviour for the new 16 px phone field size): only Playwright's Chromium was available.
- **A GPU**, other CPU architectures, long training runs; training was 30 and 100 rounds on a laptop CPU (≈ 2 and ≈ 8 minutes).
- **SAM/CLIP quality on real items:** the cached `facebook/sam-vit-base` + `openai/clip-vit-base-patch32` ran for real, but only on 14 flat synthetic shapes (7 of 10 suggestions usable); no inference about real baggage images.
- **Model accuracy:** the 100-round toy model found 1 of 4 test boxes; all scores are raw model scores of a toy model.
- **Real operators:** no usability session took place. Wording was judged by the tester only. This is why the M2 exit gate stays unticked.
- Multi-hour session expiry (simulated by deleting session rows), many simultaneous operators, very large images or hundreds of pictures, slow disks, high-contrast/forced-colours mode, browser zoom beyond 100 %.

## Screenshots

All taken from the local test instance — **prototype, synthetic data, toy model** (also stamped on each image). Reduced PNGs, each under 400 KB.

| File | What it shows |
|---|---|
| `screenshots/01-01-signin.png` | Sign-in page (empty database) |
| `screenshots/02-03-empty-dashboard.png` | Dashboard with no projects |
| `screenshots/03-05-new-project-filled.png` | New project dialog |
| `screenshots/04-08-labels-saved.png` | Labels saved |
| `screenshots/05-10-after-upload.png` | After upload: 15 added, two files named as left out |
| `screenshots/06-11-autosuggest-done.png` | Real SAM+CLIP auto-suggest finished (10 boxes for review) |
| `screenshots/07-13-annotation-with-suggestion.png` | A suggested box that is wrong (needs review) |
| `screenshots/08-14-mouse-boxes-bagscan01copypng.png` | Boxes drawn with the mouse |
| `screenshots/09-14-keyboard-boxes-bagscan09png.png` | Boxes added by typing positions |
| `screenshots/10-17-snapshot-saved.png` | Snapshot saved (note: Final test 0 at default split) |
| `screenshots/11-19-snapshot2-downloaded.png` | Snapshot 2 downloaded as COCO and YOLO |
| `screenshots/12-22-training-after-refresh.png` | Training still shown after a page refresh |
| `screenshots/13-26-model-results.png` | Model 1 results (toy model, found 0%) |
| `screenshots/14-28-approve-dialog.png` | Approval dialog (note required) |
| `screenshots/15-29-approved.png` | Model approved |
| `screenshots/16-36-operator-result.png` | Operator screen: nothing found (Model 1) |
| `screenshots/17-49-operator-two-findings.png` | Operator screen: two findings (Model 2, 100 rounds) |
| `screenshots/18-50-operator-finding-feedback.png` | Per-finding feedback: 'wrong' asks for a reason |
| `screenshots/19-41-remove-confirm-2-snapshots.png` | Second confirmation: picture is in snapshots 1 and 2 |
| `screenshots/20-42-snapshots-incomplete.png` | Snapshots marked Incomplete |
| `screenshots/21-52-export-409-stale.png` | Download refused on a stale page (409) |
| `screenshots/22-61-session-expired-labels.png` | Session ended while editing labels |
| `screenshots/23-63-two-tabs-conflict.png` | Stale second tab: label save refused |
| `screenshots/24-65-long-names-workspace-1366.png` | BEFORE FIX: long names widened the page |
| `screenshots/25-70-autosuggest-worker-down.png` | Auto-suggest with the helper program stopped |
| `screenshots/26-35-operator-wrong-file.png` | Operator screen: a text file is turned away |
| `screenshots/27-final-01-dashboard-project.png` | FINAL: project open |
| `screenshots/28-final-03-labels.png` | FINAL: labels panel |
| `screenshots/29-final-05-snapshots.png` | FINAL: snapshots panel |
| `screenshots/30-final-06-teach.png` | FINAL: teaching panel |
| `screenshots/31-final-07-check-screens.png` | FINAL: check-screen list |
| `screenshots/32-final-08-annotation.png` | FINAL: annotation dialog |
| `screenshots/33-final-09-confirm.png` | FINAL: remove-picture confirmation |
| `screenshots/34-final-11-builder-top.png` | FINAL: check-screen builder |
| `screenshots/35-final-13-operator-result.png` | FINAL: operator screen with two findings |
| `screenshots/36-sheet-ph-a.png` | 390 px phone: dashboard, checklist, labels, pictures, snapshots |
| `screenshots/37-sheet-ph-b.png` | 390 px phone: teaching, models, check screens, annotation, confirm |
| `screenshots/38-sheet-ph-c.png` | 390 px phone: builder and operator result |
| `screenshots/39-68-unsaved-labels-switch.png` | Unsaved labels: confirmation before switching project |
| `screenshots/40-53-builder-confirm.png` | Builder: confirmation before letting the screen decide alone |

## Notes

- **Method.** One Playwright script per step; each opened a fresh page load, used real clicks/keys, and logged console errors, page errors and every 4xx/5xx. Expected 4xx are listed where they occur (two 409 on picture removal, one 409 on a stale download, five 401 after the session was deleted, one 400 for an over-long label, one 400 for a stale label save). No uncaught page error remains. Static assets were fetched with `Cache-Control: no-cache` so measurements never used a stale stylesheet (a stale cache hid the first re-measure).
- **Consistency rule adopted** (one scale for all three screens): standard buttons 13 px / 700 / 40 px (44 px ≤ 768 px); compact row buttons 12 px / 30 px; fields 14 px / 400 / 40 px with a `#5e605b` border (16 px / 44 px ≤ 768 px); card padding 14 px; section titles 17 px, page title from one shared `h1` rule; one icon approach (typed characters from `ICONS` in `ui_common.js`, no emoji, no icon font); remove = "Remove", only whole projects are "Deleted". Intentional differences: top bar instead of rail on the builder/operator pages; two-line decision buttons are taller.
- **Side effects.** Disposable data directory `…/scratch/w10-data` (not committed); servers, workers and Chromium were stopped at the end. No weights were downloaded (`ATHENA_AUTOLABEL_ALLOW_DOWNLOAD=0`, `HF_HUB_OFFLINE=1`); the autolabel worker ran with the system Python 3.13 that already had `transformers` + `torch`, because the ML venv for training lacks `transformers`.
- **Recommendation.** F1 shows that source-text tests cannot detect load-time failures. A CI smoke step that loads `/`, `/projects/<id>/check-screen/setup` and `/check/<slug>` in headless Chromium and fails on any page error would have caught it.
