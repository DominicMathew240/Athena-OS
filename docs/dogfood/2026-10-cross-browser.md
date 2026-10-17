# Cross-browser + accessibility-tree pass (Chromium, WebKit; Firefox could not run)

**Local test instance, prototype, synthetic data.** `http://127.0.0.1:8814`, fresh disposable database, `DJANGO_DEBUG=1`, loopback only. Every picture is a drawn rectangle; the one trained model is a random-start toy that finds nothing. Nothing here says anything about real scanners, real operators, real accuracy or real assistive technology.

**Date:** 2026-10-17 · **Tester:** Hermes subagent W13, automated (Playwright 1.64.0, headless) · **W13 pass was report-only.** The findings below carry selectors and line numbers as W13 recorded them; the line numbers refer to the files as they were then. **W15 (same day) fixed XB-1, XB-2, XB-4, XB-6 and XB-7 and re-verified them: see "W15 update" next, and the `Status` line under each finding.**

Working-tree state tested (first 8 hex of md5): `console.js` 51c0afba, `console.css` 4a2a9a2a, `console.html` 7266b101, `checkscreen_core.js` dab950f5. W12 was editing concurrently, so later edits are not covered.

## Verified outcome

| Browser | Version | Result |
|---|---|---|
| Chromium, desktop 1440 and 390 px | 156.0.8078.4 | 18/18 scripted steps ok at both widths; 0 console messages, 0 page errors, 0 failed requests |
| **WebKit** (Safari engine), desktop 1440 and **iPhone 13 emulation** (390×664, DPR 3, mobile UA) | 27.2 (Playwright build) | 18/18 scripted steps ok at both; 0 page errors; 84 console "errors" per session, all one WebKit-internal CSP message (XB-3) |
| **Firefox** | 157.0 (installed by `npx playwright install firefox`) | **Not tested. Cannot start on this machine.** See "Firefox blocker" |

WebKit via Playwright is the Safari *engine*, not the Safari app and not iOS: no real touch, no iOS keyboard, no address-bar resizing, no iOS zoom behaviour. "iPhone 13" is a viewport + UA + DPR emulation.

### What was exercised in each of the four runs

Login page → sign in → console (fresh load, then 25-stop Tab ring before any click) → **New project dialog** (open, Tab trap, Esc, focus return) → create project → labels (type + Enter, save) → upload 15 PNGs → **annotation dialog** (same dialog checks) → box by **mouse** (alignment measured against the stored value) → box by **keyboard** form → **label edit** (select) → saved boxes → snapshot readiness + save → **COCO and YOLO downloads** → seeded project: every panel (checklist, labels, pictures, auto-suggest, snapshot, teach, check screens) → training panel + model **Results** table (separate probe, all three engines/profiles) → **check-screen builder** (load, new screen, save) → **operator screen** (pick picture, run, per-finding "right"/"wrong", reason box) → sign-out control present.

Not exercised in a browser: the approve/rollback decision dialog, starting a real training run from the UI (done in earlier reports), auto-suggest, deleting a project. Operator findings in these runs come from a **SYNTHETIC stand-in worker that writes fixed boxes**, not from a model (the real inference worker path was run once in Chromium: toy model, "found nothing"; screenshot kept in scratch, not in the repo).

## W15 update: what was fixed, and how it was re-checked

Same local test instance conditions (disposable data, `DJANGO_DEBUG=1`, loopback, synthetic pictures, stand-in worker). Same engines: Chromium 156 and WebKit (Playwright build; the Safari **engine**, not the Safari app or iOS), each desktop 1440 and phone (390; WebKit as the iPhone 13 profile). **Firefox still cannot run on this machine (XB-8 unchanged).** Every number below is from a fresh page load.

| Finding | Status | Fix | Re-check (all four runs unless stated) |
|---|---|---|---|
| XB-1 address after create | **Fixed** | one helper, `rememberProjectInAddress()`, used by opening *and* creating a project (`console.js`) | address equals `#project=<new id>` after create; reload reopens the new project; 4/4 |
| XB-2 fields under 16 px | **Fixed** | one variable `--field-size: 16px` for every field rule plus a catch-all rule, no media query (`console.css`, `checkscreen.css`) | 0 fields under 16 px in the console, builder and sign-in at iPhone SE 320, iPhone 13 390 and landscape 750, iPad 810, iPad Pro 834 and 1194, desktop 768/900/1024/1100/1440 (the W13 table had 13/17/6 per page on iPads) |
| XB-3 WebKit CSP noise on `<select>` | **Informational, unchanged** | none (policy left alone) | the smoke test counts these messages in WebKit (93 per run) and ignores only that one message; it fails on any other CSP violation |
| XB-4 Tab leaves dialog in WebKit | **Fixed** | `trapTab` now steps focus itself through the dialog's own controls (Tab and Shift+Tab, wrapping), instead of waiting for focus to sit on the last button (`ui_common.js`) | 48 Tab + 48 Shift+Tab in the New-project and picture dialogs: **0 escapes and 0 stops on `<body>`** in Chromium and WebKit, desktop and iPhone 13 (W13: 4 and 3 escapes in WebKit); Esc closes and focus returns to the opener |
| XB-5 Safari Tab skips buttons | **Informational, unchanged** | none; side effect: inside dialogs, Tab now reaches buttons in every engine, because the script steps focus | Option+Tab is not intercepted (left to the browser) |
| XB-6 "at least 0% sure" | **Fixed** | at a sureness that rounds to 0% the sentence is "The computer found nothing at all." / "The computer found 4 things." (`checkscreen_core.js`) | operator screen with the threshold set to 0, stand-in worker: both sentences shown in all four runs; other thresholds keep their old sentence (Node test) |
| XB-7 landmarks | **Fixed** | the console's side bar is now a top-level `<header>` (banner); the sign-in page has a skip link and a `<main id="main">`; every `<form>` on the sign-in, console, builder and operator pages has an accessible name (the one built in script, the snapshot download form, too) | exactly 1 top-level banner and 1 main on sign-in and console; 0 unnamed forms; Chromium AX tree: 1 banner, 1 main, 7 named forms and 0 unnamed on the console (W13: no banner, 7 unnamed forms) |
| XB-8 Firefox | **Still open** (machine blocker) | none | none |

**Extra findings during the fix (all fixed):**

- **Focus did not return to the opener in WebKit** (found by the new smoke test, not by W13): Safari does not focus a button when it is clicked, so at the moment a dialog opened `document.activeElement` was `<main>` and closing returned focus there. `manageDialog` now remembers the control that was just clicked and prefers it (`ui_common.js`).
- **16 px made the picture upload (`<input type=file>`) 8 px too wide at 320 and 390 px** and the whole console scrolled sideways (found by the width probe before it shipped). Fixed with `min-width: 0; width: 100%` on `.asset-upload input`; the smoke test now fails on any sideways scroll at 390/820/1024/1440.
- **A 500 instead of a plain message** when a model's training tool is not installed in the console process (`POST …/inference/apps/<id>/run`): now a 503 saying so (`lifecycle.backend_runnable`), with a test. Found while seeding the smoke test.

**axe-core** (wcag2a/2aa/21a/21aa/22aa + best-practice, Chromium, `reducedMotion: reduce`, desktop 1440 and phone 390): **0 violations** on sign-in, console with a project open, New-project dialog, picture dialog, check-screen builder, operator screen, and the Teach panel with the new start-from control open (both the greyed-out state and the licence box with the tick-box).

### Browser smoke test (new in W15)

`web/scripts/browser_smoke.mjs`, run by `bash web/scripts/run_browser_smoke.sh [chromium|webkit] ["iPhone 13"]` (disposable database, free loopback port, torn down afterwards; Node and `npx playwright install chromium` are needed and are **not** product dependencies). It loads sign-in, the console, a seeded project (every panel), the New-project, picture, remove-picture-confirmation and approve-a-model dialogs, the check-screen builder and the operator screen, and fails on any uncaught script error, console error, Content Security Policy violation, failed or 4xx/5xx request, console that rendered but did not start, unnamed control or form, missing banner/main, duplicate id, field under 16 px or sideways scroll at 390/820/1024/1440, dialog that leaks Tab/Shift+Tab or loses focus, and a created project that is missing from the address.

Last runs: Chromium 142 checks, 0 failed (six runs in a row, to rule out flakiness); Chromium with the iPhone 13 profile 142/0; WebKit 135/0; WebKit iPhone 13 135/0. **Mutation checks, each restored afterwards (md5 of `console.js`, `ui_common.js`, `console.css` identical before and after):** a deliberate `deliberatelyMissingFunction();` at the top of `console.js` made it exit 1 ("uncaught script error: deliberatelyMissingFunction is not defined", "the console did not start: project counter is still 00", 11 failures); the old `trapTab` back in made WebKit fail 4 dialog checks ("Tab press 4 moved focus to body"); removing the XB-1 line failed the address check; a field put back at 14 px failed the field-size check at every width. What it does **not** do: accuracy, Firefox, real Safari or iOS, real touch, a screen reader, contrast of every state, or any flow that needs a real trained model (the seed uses a stand-in that learns nothing).

### Teach panel: "Advanced: start from a ready-made model" (new in W15)

Collapsed by default. Three choices: **Start from scratch** (says honestly that learning from nothing needs many labelled pictures), **Start from a general picture model (ImageNet)**, **Start from a general object model (COCO)**. The server says whether each one can be used: a ready-made choice is usable if its weights are already on the computer and match their published checksum, or if downloading is allowed (`ATHENA_TRAINING_ALLOW_DOWNLOAD=1`; the running training helper reports its own setting, the console's environment is used only when no helper is running, and the panel says so). Otherwise the choice is disabled, with the reason and the administrator's steps in plain words. Choosing a ready-made start shows the backend's licence note in plain words, the "Ask a lawyer before commercial use" warning, the tool's own note word for word, a pointer to `docs/guides/model-quality.md` (no accuracy number is repeated in the panel), and a tick-box that starts unticked every time; "Start teaching" stays off until it is ticked. The server enforces it too: a request for a ready-made start without `acknowledge_pretrained_terms: true` is a 400, and one that cannot run (downloads off, nothing cached) is a 409 before anything is queued. The choice is stored in the run's write-once config (`pretrained`) and the acknowledgement in its audit event.

Verified (no download was made): **downloads off, nothing cached:** ImageNet and COCO disabled with the reason and `ATHENA_TRAINING_ALLOW_DOWNLOAD=1` / `manage.py train_worker` steps, clicking a disabled choice changes nothing, a forged POST with the tick is refused 409 in plain words (13 checks, Chromium and WebKit, desktop and phone). **Weights already cached** (read-only symlink to the W13 benchmark cache, downloads still off): both enabled and described as "already on this computer … nothing will be downloaded"; arrow keys reach COCO; the licence box shows; Start is off and says why; submitting without the tick queues nothing; Space ticks; going back to scratch hides the box and clears the tick; ImageNet shows its own text; with the tick a run is queued (no helper running, then stopped from the UI), "Settings used for this run" says "How it started: A general object model (COCO)", and the run's stored config has `pretrained: coco` (16 checks, same four runs). **Not verified:** a real teaching run from a ready-made start through the UI (it needs the 75 MB download or a long CPU run; the training code path itself is covered by W13's benchmark and the ML-venv tests), and any operator's understanding of the licence wording.

## Per-browser record

*This section and the findings below are W13's original record (before the W15 fixes); the `Status` lines and the W15 update above supersede them where they differ.*

| Check | Chromium 1440 | Chromium 390 | WebKit 1440 | WebKit iPhone 13 |
|---|---|---|---|---|
| Page errors (`pageerror`) | 0 | 0 | 0 | 0 |
| Console errors/warnings | 0 | 0 | 84 (XB-3) | 84 (XB-3) |
| Failed/4xx/5xx requests | 0 | 0 | 2 `cancelled` (page left mid-fetch, benign) | 2 `cancelled` |
| Horizontal overflow (`scrollWidth > innerWidth`) at 13 checkpoints (login, console, project page, panels, both dialogs, builder ×2, operator ×2) | none | none | none | none |
| `<dialog>`, `showModal`, `:modal`, `:focus-visible`, `:has()`, `dvh`, `aspect-ratio`, `inset`, `gap` | all supported | all supported | all supported | all supported |
| Focus ring, fresh load, 25 Tab stops: stops with no outline/box-shadow, or not `:focus-visible` | 0 | 0 | 0 | 0 |
| Focus ring, pixels actually change when focused (first 10 stops) | 10/10 | 10/10 | 10/10 | 10/10 |
| Distinct controls reached by Tab in 25 presses | 24 | 23 | 11 | 7 (XB-5) |
| New-project dialog: first focus inside, Esc closes, focus returns to opener, `:modal` true | yes | yes | yes | yes |
| Annotation dialog: same | yes | yes | yes | yes |
| Tab escapes the dialog (48 presses each) | 0 / 0 | 0 / 0 | 4 / 3 (XB-4) | 4 / 3 (XB-4) |
| Downloads (COCO, YOLO): saved, non-empty, start with `PK` | 28,339 / 29,540 bytes | 28,368 / 29,558 | 28,330 / 29,533 | 28,354 / 29,536 |
| Annotation canvas vs image rect | identical | identical | identical | identical |
| Mouse-drawn box, stored vs intended (480×320 picture) | ≤0.00002 px | ≤0.004 px (326×217 display) | ≤0.75 px | ≤0.53 px |
| Operator SVG overlay vs picture rect; 8 finding boxes vs viewBox fractions | 0 px; max 0.00002 px | 0 px; 0.00002 px | 0 px; 0.00001 px | 0 px; 0.00007 px |
| Form fields below 16 px (iOS focus-zoom threshold) | 34 distinct fields at 14 px (expected on desktop) | **none** | 34 at 14 px (desktop) | **none** |

"8 finding boxes" is the number of `<rect>` elements with geometry in the overlay (the stand-in worker returns 4 findings); the alignment error is the largest difference of left/top/width/height against `img rect × fraction`. The sub-pixel mouse error is probably pointer-position rounding in WebKit, not misalignment (not isolated).

### iPad and landscape widths (WebKit device emulation, extra probe)

| Device profile | Viewport | Fields below 16 px |
|---|---|---|
| iPhone SE | 320 | 0 |
| iPhone 13, iPhone 13 landscape (Playwright: 750 px wide), SE landscape (568) | 390 / 750 / 568 | 0 |
| iPad (gen 7) | 810 | console 13, New-project dialog 17, builder 6 |
| iPad Pro 11, portrait / landscape | 834 / 1194 | same counts |

## Findings

Severity: **Medium** = a user can lose their place or do the wrong thing; **Low** = rough edge; **Info** = engine behaviour or note, no change needed. No Critical or High findings in what ran.

### XB-1 — Creating a project does not update the address; reload returns to a different project (Medium) — all engines tested

**Status: fixed in W15** (see the table above). The text below is W13's original finding.

- **Seen in:** Chromium 1440 and 390, WebKit 1440 and iPhone — 4 of 4 runs, plus a clean repro (`hash-probe`) with one project open.
- **Repro:** sign in with ≥1 project → console auto-opens the first project (address `#project=<A>`) → **New project** → fill name and "what you want to find" → **Create project**. The workspace shows the new project, but the address still says `#project=<A>`. Reload (or Back, or coming back from the check-screen builder via "All projects") opens **A**, not the project just made. Measured: address after create `#project=b8543586…` (A) while title read the new project; after reload the title was `Bag scanner photos (synthetic)` (A).
- **Cause:** `web/projects/static/projects/console.js` project form submit handler (`projectForm.addEventListener("submit"`, around lines 1253–1290): `selectedId = body.project.id;` is set but `history.replaceState(null, "", "#project=" + id)` is not (that call lives only in `selectProject`, line 144).
- **Why it matters:** after a reload a person may add pictures or labels to the wrong project without noticing. W10's "remember the open project" fix covered clicking a project, not creating one.
- **Suggested fix (not applied):** call `selectProject(body.project.id)` after creation, or add the `replaceState` line. Add a test that `location.hash` equals the new id after create.

### XB-2 — Form fields are 14 px above 768 px wide, so iPad Safari will zoom on focus (Low–Medium) — computed size verified; zoom itself not observable here

**Status: fixed in W15** (computed size only; Playwright cannot show the iOS zoom, so "Safari no longer zooms" is inferred from the 16 px rule, not observed). The text below is W13's original finding.

- **Where:** `web/projects/static/projects/console.css` lines ~616–619: `@media (max-width: 768px) { … textarea, .search { font-size: 16px; } … }`. Above 768 px every field computes to 14 px. Measured at 810 / 834 / 1194 px (table above). Examples: `input#search`, `input#label-new`, `input[id^=label-name-]`, `select#autolabel-strictness`, `input#split-train`, `select#tr-snapshot`, `select#tr-tool`, `input#tr-rounds`, `input#project-name`, `select#annotation-label`, `input#box-left` … (34 distinct fields across the console, its dialogs and the builder; 6 of them on the builder: `select#cs-app-select`, `select#cs-model`, `input#cs-name`, `input#cs-say-clear`, `input#cs-say-flagged`, `input#cs-say-review`).
- **Passes:** iPhone 13 (390) and SE (320): no field under 16 px in the console, both dialogs, builder.
- **Unverified:** that Safari zooms (Playwright cannot show it), and larger iPhones in landscape (≥ 769 px; the emulated landscape profiles are narrower than that).
- **Suggested fix:** use `@media (pointer: coarse)` or raise the breakpoint to ~1100 px, or make the base field size 16 px.

### XB-3 — WebKit logs 84 CSP "errors" per session, one per `<select>` per load (Info) — WebKit only

**Status (W15): informational, no change.** The policy stays as it is. The smoke test counts these messages (93 in a W15 WebKit run) and fails on any other CSP violation. If you collect CSP reports, expect this noise from Safari.

- **Seen:** `Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the style-src directive` — `securitypolicyviolation` reports `style-src-attr inline` at source lines 109, 117, 202, 203, 280, 297 of the rendered console page: exactly the six `<select>` elements (`#autolabel-strictness`, `#autolabel-accept-threshold`, `#tr-snapshot`, `#tr-tool`, `#project-modality`, `#annotation-label`).
- **Not the page:** the served HTML has 0 `style=` attributes. A bare page with one `<select>` under the same `style-src 'self'` policy produces the same violation in WebKit and none in Chromium, so WebKit applies an internal inline style to native selects. No visible effect found: every select worked in all WebKit runs.
- **Recommendation:** leave the policy alone (do not add `unsafe-inline`). Do not alert on console errors from Safari; if CSP reports are collected, expect this noise.

### XB-4 — WebKit: Tab past the last control in a modal dialog lands on `<body>` instead of wrapping (Low) — WebKit only

**Status: fixed in W15.** W13's "likely cause, not proven" was right in outline: the trap waited for focus on the last button, which Safari's default Tab never visits. The trap now steps focus itself, so it no longer depends on which controls the browser would stop at. 0 escapes in 48 Tab and 48 Shift+Tab presses per dialog in Chromium and WebKit. Real Safari (with and without "Press Tab to highlight each item") has **not** been tested.

- **Seen:** New-project dialog 4 of 48 presses, annotation dialog 3 of 48, desktop and iPhone profiles; Chromium 0 (its trap wraps). Focus never reached background controls, Esc closed the dialog, and focus returned to the opener (`#open-project-dialog`, `button.asset-annotate`) every time. In real Safari that stop would be browser UI.
- **Where:** `web/projects/static/projects/ui_common.js`, `trapTab` (line 42; installed by `manageDialog`, line 64; used for the console's dialogs). **Likely cause, not proven:** the trap wraps only when `document.activeElement` is exactly the first or last element of `tabbableIn(dialog)`, and the last one is a button; Safari's Tab skips buttons by default (XB-5), so focus leaves from the last control Safari does stop on.
- **Note:** harmless under native `<dialog>` behaviour; fix only if a strict wrap is wanted (e.g. also wrap on the last *focusable* control, not only the last button).

### XB-5 — Safari's default Tab key skips buttons and links (Info) — WebKit

**Status (W15): informational, nothing to fix in the page.** Outside dialogs the browser's own Tab behaviour is untouched, so a real Safari keyboard test still needs Option+Tab or that setting on. Inside dialogs the script now steps through every control including buttons, so there Tab reaches the buttons whatever the setting.

- **Measured:** 25 × Tab reaches 12 distinct controls (inputs, selects, `<summary>`), 25 × Alt+Tab reaches 21 (buttons). This is Safari's "Press Tab to highlight each item" setting being off by default, not an app defect. The skip link ("Skip to main content") exists on console, builder and operator pages.
- **Consequence for testing:** the 7 vs 24 gap in the table above is this setting; a real Safari keyboard-only test needs Option+Tab or that setting on.

### XB-6 — Operator headline reads "found nothing it is at least 0% sure about" when sureness is set to 0 (Low, copy)

**Status: fixed in W15.**

- **Seen:** check screen with `confidence_threshold` 0, picture with no detections: "Needs review by a person — The computer found nothing it is at least 0% sure about." Odd at 0 (it means "nothing at all").
- **Where:** `web/projects/static/projects/checkscreen_core.js` line 311 (`The computer found nothing it is at least ${threshold} sure about.`); the slider wording at line 42 is fine.

### XB-7 — Accessibility-tree landmark notes (Info)

**Status: fixed in W15** (banner on the console, skip link and main on sign-in, every form named). Chromium AX tree and DOM only; not a screen-reader test.

- Console project page has **no `banner` landmark** (the `<header class="page-head">` sits inside `<main>`; the sidebar is `complementary "Athena navigation"` with `navigation "Main navigation"`). Builder, operator and login do have `banner`.
- Login page has no skip link (one form, three controls).
- Seven `<form>` elements on the console page, one on login/builder/operator, are exposed as unnamed `form` landmarks (Chromium exposes them even without a name). Screen readers may announce "form" with no name; giving them an `aria-label`, or dropping the form role where a wrapper is not a real form, is optional.

### XB-8 — Coverage gap: Firefox never ran (process blocker, not an app finding)

See next section. Until Firefox is run elsewhere, **nothing in this report supports Firefox**. (Status after W15: unchanged. The smoke test takes `chromium` or `webkit`; Playwright's `firefox` would be accepted by the runner but has never been run here.)

## Firefox blocker (exact)

- `npx playwright install firefox webkit` (Playwright 1.64.0 from the OCPP System `node_modules`) succeeded: Firefox 157.0 (Playwright build v1555, 105.7 MiB) and WebKit 27.2 (v2370, 78.9 MiB).
- `firefox.launch()` and `launchPersistentContext()` both fail with `Failed to launch the browser process … *** You are running in headless mode. Could not find profile folder.` and exit code 1.
- Reproduced **outside Playwright** with the raw binary `…/ms-playwright/firefox-1555/firefox/Nightly.app/Contents/MacOS/firefox -headless -profile <existing dir> about:blank`; same message with an existing profile directory under `/tmp`, under `~/.hermes/…/scratch`, with no `-profile` at all, with `HOME` redirected, with `-CreateProfile`, and with `MOZ_DISABLE_*_SANDBOX=1`. `-CreateProfile` additionally printed `sandbox_extension_issue_file_to_process failed … (Operation not permitted)`.
- Host: macOS 27.0.1 (Darwin 27.0.0), Apple M3. No system Firefox is installed. I did not find a workaround in the time available; likely an incompatibility between this Firefox build and this macOS release, but that is a guess.
- Side effect: after the install the older `chromium-1243` / `chromium_headless_shell-1243` caches are no longer in `~/Library/Caches/ms-playwright` (the installer prunes revisions this Playwright does not use); revision 1248 remains.

## Screen-reader PROXY check (Chromium accessibility tree) — NOT a screen-reader test

What this is: Chromium's full accessibility tree via CDP (`Accessibility.getFullAXTree`) and a DOM audit, run programmatically on seven states. What it is **not**: VoiceOver, NVDA, JAWS or TalkBack, which differ in what they announce, in how they treat live regions and in reading order. Passing this check does not mean the screens are pleasant with a screen reader, and "no unnamed control" does not mean the names are good.

| State | AX nodes exposed | Interactive controls | Unnamed controls | `main` | `h1` | Heading-level skips | Live regions (AX) | Dialogs (AX) | Duplicate ids |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|
| Login | 39 | 3 | **0** | 1 | 1 | 0 | 0 | – | 0 |
| Console, project open, every panel | 1606 | 135 | **0** | 1 | 1 | 0 | 10 (`status`, polite) | none open | 0 |
| New-project dialog open | 56 | 17 | **0** | – (background inert) | 1 | 0 | 0 | 1: `dialog` "Create a project", modal | 0 |
| Annotation dialog open | 95 | 16 | **0** | – (inert) | 1 | 0 | 3 | 1: `dialog` "Mark items in this picture", modal | 0 |
| Check-screen builder | 360 | 35 | **0** | 1 | 1 | 0 | 2 | – | 0 |
| Operator screen, empty | 52 | 4 | **0** | 1 | 1 | 0 | 2 | – | 0 |
| Operator screen, result (stand-in findings) | 198 | 17 | **0** | 1 | 1 | 0 | 7 | – | 0 |

Also checked and clean: no focusable element inside `aria-hidden`; no `<img>` without `alt`; every `<table>` has `<th>`; `lang="en"`; page titles distinct per page (`Sign in · Athena console`, `Projects · Athena console`, `Set up your check screen · Athena`, `Seeded bag check · Athena`); no icon-only button without a name; the annotation `<canvas>` is exposed as role Canvas, focusable, named "Drawing area for boxes. Select a box, then use the arrow keys…"; the operator step numbers are `aria-hidden` so headings read "Choose a picture", not "1Choose a picture". Heading structure on the console: h1 "Your projects" → h2 "All projects" / project → h3 sections → h4 sub-sections → h5 per snapshot/model (no skipped level). Controls checked: button, link, textbox, combobox, checkbox, radio, switch, tab, menuitem, slider, spinbutton, searchbox, listbox, option.

Live-region note: the console exposes 10 polite statuses plus several alerts at once (`#save-state`, `#action-notice`, `#labels-dirty`, `#autolabel-state`, `#asset-status`, `#dataset-headline`, `#split-total`, `#snapshots-status`, `#tr-headline`, …); the operator screen creates one status per finding (`#cs-det-msg-N`). Whether that chatter is useful or tiring can only be judged with a real screen reader. Landmark notes are XB-7. Raw tree dumps (`aria-*.yml`, `a11y.json`) are in the scratch folder, not in the repo.

## Screenshots (reduced, each labelled in the image)

All under `docs/dogfood/screenshots/`, all "local test instance, prototype, synthetic data":

- `xb-webkit-phone-02-console.png` — WebKit iPhone 13 emulation, console.
- `xb-webkit-phone-05-box-mouse.png` — same, annotation dialog, box drawn by mouse.
- `xb-webkit-phone-17-operator-wrong-reason.png` — same, operator screen, "This is wrong" reason box (stand-in findings).
- `xb-webkit-desktop-08-snapshot-saved.png` — WebKit 1440, snapshot saved with COCO/YOLO choice.
- `xb-webkit-desktop-15-operator-result.png` — WebKit 1440, operator result (stand-in findings; image cropped to the first 1800 px).
- `xb-chromium-phone-14-operator-empty.png` — Chromium 390, operator screen.
- `xb-chromium-desktop-10-train-panel.png` — Chromium 1440, Teach panel with the seeded toy model.

## Setup and limits (so this can be repeated)

- Server: `manage.py runserver 127.0.0.1:8814 --noreload` (main venv), `ATHENA_DATA_HOME=…/scratch/w13-data`, `DJANGO_DEBUG=1`. Seed: a project with 30 synthetic pictures and boxes, a snapshot, a 6-epoch random-start torchvision model (found 0 of 7 test boxes; approved only by passing `acknowledge_weak_results`), and one check screen. A fake worker (fixed boxes, labelled stand-in) served the operator runs.
- Each browser run used a fresh context and its own new project (`XB <browser>-<profile> #n (synthetic)`); boxes for pictures 3–15 were added through the API as setup, not as a UI test. Servers, workers and browsers were stopped afterwards.
- Pointer actions used the mouse API, not touch gestures, even on the iPhone profile. Headless only. No contrast, zoom/reflow, reduced-motion or high-contrast checks in this pass.
- **Not shown by this report:** Firefox behaviour, real Safari or iOS, real assistive technology, real touch input, accuracy or usability for real operators.
