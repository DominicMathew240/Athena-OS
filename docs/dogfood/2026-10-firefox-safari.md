# Real Firefox run, Safari attempt, VoiceOver checklist

**Local test instance, prototype, synthetic data.** `http://127.0.0.1:8817`, disposable database, `DJANGO_DEBUG=1`, loopback only. Every picture is a drawn rectangle. Operator "findings" come from a **SYNTHETIC stand-in worker that writes fixed boxes**, not from a model. Nothing here says anything about real scanners, operators, accuracy or assistive technology.

**Date:** 2026-10-17 · **Tester:** Hermes subagent W18 · **Report only:** no UI file was changed. Another agent (W15) was editing the UI during the run; findings carry selectors.

Working tree at the start of the final runs (first 8 hex of md5): `console.js` bc2057b8, `console.css` 60c1e3fd, `console.html` e859a626, `checkscreen_core.js` c532c904, `checkscreen.css` b2dd0ce7, `ui_common.js` 3376916a. `console.js` changed to d63de00e before the last run ended, so the final desktop/phone runs may span both versions.

## Verified outcome

| What | Result |
|---|---|
| **Real Firefox 158.0 ran the console** (official Mozilla build, outside Playwright, driven by geckodriver 0.37.1 + Selenium 4.51.0 over WebDriver, BiDi for console/JS errors) | **18/18 scripted steps passed at 1440 px and at 390 px.** 0 console errors, 0 uncaught JS errors, 0 CSP violations in the final runs. |
| Real Safari | **Not run.** `safaridriver` exists but "Allow remote automation" is off and enabling it needs an admin password. Exact error and one-time owner steps: `safari-run.md` (with a script, harness-checked against Firefox). |
| Real screen reader (VoiceOver) | **Not run.** Manual checklist below. |

The Playwright Firefox 157 build still cannot start on this Mac ("Could not find profile folder", see `2026-10-cross-browser.md`). The official Firefox 158 starts and works fine on macOS 27.0.1, so the blocker is in Playwright's patched build or its launch, not in the OS (that is an inference, not proven).

### What is actually Firefox here

- Download: `https://download.mozilla.org/?product=firefox-latest&os=osx&lang=en-US` → `Firefox 158.0.dmg`. Its SHA-256 `dfe8508ee8880cec1578253eb02b9154b5971a55e832d97396f463cd07b5ef67` equals the line `mac/en-US/Firefox 158.0.dmg` in `https://archive.mozilla.org/pub/firefox/releases/158.0/SHA256SUMS`.
- Mounted read-only with `hdiutil attach -nobrowse -readonly -mountpoint …`, the app copied to `scratch/w18/Firefox.app` (nothing installed system-wide, nothing in /Applications), image detached, **dmg deleted**.
- Signature, verbatim: `codesign -v --verbose=2 Firefox.app` → `Firefox.app: valid on disk` / `Firefox.app: satisfies its Designated Requirement`. `codesign -dv`: `Identifier=org.mozilla.firefox`, `Authority=Developer ID Application: Mozilla Corporation (43AQ936H96)`, `TeamIdentifier=43AQ936H96`, `Format=app bundle with Mach-O universal (x86_64 arm64)`. `spctl -a -vv` → `accepted`, `source=Notarized Developer ID`.
- `Firefox.app/Contents/MacOS/firefox --version` → `Mozilla Firefox 158.0`. Session UA: `Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:158.0) Gecko/20100101 Firefox/158.0` (Firefox reports 10.15 on every macOS).
- geckodriver: `geckodriver-v0.37.1-macos-aarch64.tar.gz`, SHA-256 `d02b3f7003f999caf90974a2ef5da0286c05d01cee19112c86846d759fdba4f5` equals the digest GitHub publishes for that release asset. `geckodriver --version` → `geckodriver 0.37.1 (300705c65d1b 2026-07-17 09:25 +0000)`. I did not verify geckodriver's signature beyond the checksum; I cleared extended attributes (`xattr -c`) before use.
- Headless (`-headless`). Screenshots are from Firefox's own screenshot command.

### Limits of this Firefox run

- **390 px is a viewport override, not a 390 px window.** Firefox will not make a window narrower than about 500 px (`set_window_size(390)` gave `innerWidth` 500). The phone run uses WebDriver BiDi `browsingContext.setViewport` (390×844, DPR 2). Media queries and layout respond as at 390 px; the UA is still desktop Firefox, there is no touch, no Firefox for Android.
- Headless, WebDriver pointer actions (rounded to whole pixels), no real hardware keyboard. Native `<select>` popups are not drawn headless, so selects were set by script (value + `input`/`change` events); keyboard use of an open select popup is **not tested**.
- Firefox reached buttons with plain Tab here (22 distinct controls in 25 presses, including `button.filter`). I did not read `accessibility.tabfocus`; a user whose macOS "keyboard navigation" setting is off may behave differently (not tested).
- Console capture was proven working first with a positive control (`console.error`, a thrown error, an inline `style` attribute CSP block were all captured). Failed/4xx network requests were **not** captured in this run (no network log); the server log was not archived.
- No zoom/reflow, high-contrast, reduced-motion or contrast measurements in Firefox.

## Per-step record (final runs)

Each run: fresh browser, own new project, shared server. "Chromium/WebKit" is the baseline in `2026-10-cross-browser.md`.

| # | Step | Firefox 1440 | Firefox 390 |
|---|---|---|---|
| 1 | Login page loads | pass | pass |
| 2 | Sign in | pass | pass |
| 3 | Console loads fresh; 25 Tab stops | pass (21 distinct controls, 0 on body, 0 without ring) | pass (20 distinct, 0 on body, 0 without ring) |
| 4 | New-project dialog: open, Tab trap (48 presses), Esc, focus return | pass (0 escapes, closes, focus back on `#open-project-dialog`, `:modal` true) | pass (same) |
| 5 | Create project | pass; address hash == new project id | pass; same |
| 6 | Labels (type + Enter, save) | pass | pass |
| 7 | Upload 15 PNGs | pass (15 cards) | pass |
| 8 | Annotation dialog: open, Tab trap, Esc, focus return | pass (0 escapes; focus back on the "Mark items in xb-00.png" button) | pass |
| 9 | Box by mouse; stored vs intended | pass, error ≤0.25 px on a 480×320 picture | pass, ≤0.96 px on 326×217 |
| 10 | Box by keyboard form + label edit | pass ("All changes saved") | pass |
| 11 | Remaining boxes via API (setup), reload keeps the project | pass (kept) | pass (kept) |
| 12 | Snapshot saved | pass | pass |
| 13–14 | COCO and YOLO downloads | pass: 28,322 / 29,534 bytes, start `PK` | pass: 28,343 / 29,535 bytes, `PK` |
| 15 | Seeded project: all 7 panels visible | pass | pass |
| 16 | Check-screen builder: load + create screen | pass | pass |
| 17 | Operator screen: run, overlay, "right"/"wrong" + reason box | pass | pass |
| 18 | Sign-out control present | pass | pass |

Other measurements (both widths): horizontal overflow at 13 checkpoints (login, console, project page, pictures, snapshot, both dialogs, seeded project, builder ×2, operator ×2): **none**. Form fields: 35 distinct visible fields, **all computed ≥16 px at both widths** (the 14 px fields at 810+ px that XB-2 reported are gone in the current tree). Features present: `<dialog>`/`showModal`, `:modal`, `:focus-visible`, `:has()`, `dvh`, `aspect-ratio`, `inset`, `gap`, `structuredClone`, `PointerEvent`, `ResizeObserver`. Focus ring: every one of the 25 stops had an outline and matched `:focus-visible`; removing the outline changed pixels at 10 of 10 tested stops. Computed ring: `3px solid rgb(11, 79, 208)`, offset 2px, plus a `0 0 0 2px` white box-shadow on light surfaces; `3px solid rgb(255, 214, 102)` with a `rgb(8, 10, 11)` 2px shadow on the dark ones. Canvas vs image rect: identical (0 px).

### Run history (so nothing is hidden)

1. **First desktop run: 18/18 steps ok but 2 JS errors** (`TypeError: can't access property "addEventListener", trStartFromOptions is null`, console.js line ~3915) on a reload of the console. Cause: my `runserver --noreload` had cached the old `console.html`; W15 then added `#tr-start-from-options` to the template (08:57:30) and a matching `console.js` (08:58:02). New JS against an old cached template. After restarting the server the same steps gave 0 errors. This is a test artifact, not an app defect, but it is the known "template caching" pitfall: **any deploy that ships the new JS before the new template process restarts will throw this error.**
2. A first phone run used `set_window_size(390)` and got `innerWidth` 500 plus a crop bug in my own script (1 step failed); rerun with the BiDi viewport override, then once more to record focus-ring details. Final runs are those two.
3. Mid-run measurement note: operator-overlay boxes measured **5.13 px off in Firefox** at first. That is the measuring method, not the page: Firefox's `getBoundingClientRect()` on an SVG shape **includes the stroke** (Chromium and WebKit do not), so the `cs-box-halo` (5 px stroke) rect reads 5 px wider. Adding the computed stroke width back gives **0.13 px (1440) and 0.10 px (390)** error against the viewBox fractions; `getBBox()` returns exactly the `x/y/width/height` attributes. Overlay SVG vs picture rect: 0 px at both widths. Any automated test of the overlay that uses `getBoundingClientRect` must account for this in Firefox.

## Findings

Severity: **Medium** = a person can lose their place or do the wrong thing; **Low** = rough edge; **Info** = engine behaviour, no change needed. **No Critical, High or Medium findings in Firefox.**

| ID | Sev | Finding | Where |
|---|---|---|---|
| FF-1 | Info | Nothing Firefox-specific broke: dialogs, canvas drawing, downloads (blob + `<a download>`), CSP (no violations, including no WebKit-style `style-src-attr` noise on `<select>`), focus rings, layout all behave like Chromium. | – |
| FF-2 | Info | Firefox `getBoundingClientRect()` includes SVG stroke (see run history 3). Relevant only to tests. | `svg.cs-overlay rect.cs-box-halo`, `.cs-box-line` |
| FF-3 | Low | `/favicon.ico` is requested by every browser; there is no `<link rel="icon">` in `console.html`, `checkscreen_base.html` or `login.html`, so the request is answered with a 302 to the sign-in page (`GET /favicon.ico 302` then `/login/?next=%2Ffavicon.ico 200`, seen in the server log). Harmless, but it adds a request and a log line per page load, and a signed-out 302 where a 204/icon is expected. | `web/projects/templates/…` head |
| FF-4 | Low (copy/state) | Operator screen: after clicking **This is right** on finding 1 and then **This is wrong** on the same finding, the reason box opens while the green line "Saved: you said this is right. Saved for later review; it does not change the model." is still shown above it (visible in `ff-phone-17-operator-wrong-reason.png`). Not checked whether the old answer is replaced on Save; not checked in other engines (almost certainly engine-independent). | `ol#cs-detections` finding message element (`#cs-det-msg-N`), `checkscreen_core.js`/`checkscreen.js` |
| FF-5 | Info | Earlier findings re-checked in Firefox: **XB-1** (address after create) is **fixed** in the current tree (hash equals the new project id, reload keeps it, both widths). **XB-2** (14 px fields above 768 px) is **fixed** (all fields ≥16 px at 1440; not re-checked at 810–1194 px). XB-4 (Tab leaking from a dialog) does not occur in Firefox (0 escapes in 48 presses, both dialogs, both widths). Not re-verified: XB-3, XB-5 and XB-6 (WebKit/copy). | – |

## Screenshots (reduced, each labelled in the image)

`docs/dogfood/screenshots/`, all captioned "Local test instance, prototype, synthetic data": `ff-desktop-02-console.png`, `ff-desktop-05-box-mouse.png`, `ff-desktop-dialog-new-project.png`, `ff-desktop-08-snapshot-saved.png`, `ff-desktop-15-operator-result.png` (stand-in findings, cropped), `ff-phone-02-console.png`, `ff-phone-05-box-mouse.png`, `ff-phone-17-operator-wrong-reason.png` (stand-in findings). All under 80 KB. "phone" = 390 px viewport override in desktop Firefox.

## Safari

See `safari-run.md`. Real error, one-time owner steps (Safari Settings → Advanced → web-developer features; Developer → Allow remote automation; `sudo safaridriver --enable` typed by the owner), and a ready-to-run script. The script ran green 11/11 against Firefox 158 as a harness check, so only the `webdriver.Safari()` line is unproven. Known WebKit differences to expect (from the earlier WebKit run): Safari's plain Tab skips buttons/links (script uses Option+Tab), one CSP console line per native `<select>`, a possible Tab leak from the last dialog control.

## VoiceOver checklist for the owner (manual; I did not automate VoiceOver)

Setup: Safari (VoiceOver pairs best with it), instance running with disposable data, signed-in test user, window at normal size. **Cmd+F5** toggles VoiceOver (first start offers a tutorial: press Esc). VO keys below mean Control+Option held. Useful: **VO+U** rotor (Headings, Landmarks, Form controls, Links), **VO+A** read all, **VO+Cmd+H** next heading, **VO+Shift+Down/Up** enter/leave a group, **Esc** closes a dialog. Turn on the Caption Panel (VoiceOver Utility → Visuals) if you want to see what is spoken. Write down anything that is confusing, wrong, too chatty or silent; "I could not tell where I was" counts.

Landmarks and headings below are read from the templates (`console.html`, `checkscreen*.html`, `registration/login.html`); **confirm each against what VoiceOver actually says**, that is the point.

1. **Sign-in page.** Load `/login/`. Expect the page title "Sign in · Athena console"; landmarks: a banner area (h1 "The computer suggests. You decide."), `main` with h2 "Sign in" and a form named "Sign in". Listen for: focus starts in "Username" (autofocus) and the label is read; password field says "secure text"; submit a wrong password and check the error is **announced without moving** (it is `role="alert"`). Note: there is no "skip to main content" link on this page.
2. **Console orientation.** After login open the Landmarks rotor. Expect: the rail `header` (banner), navigation "Main navigation" with "Projects" (current page), a form "Sign out", `main`, and sections "Workspace overview", "All projects". Headings rotor: h1 "Your projects", h2 "All projects", then the project h2, h3 "Get started", "Labels", "Pictures", "Make a training set", "Teach the computer", "Check screens", h2 "What you can do today". Listen for: no heading level skipped, headings do not read as numbers glued to words.
3. **Skip link.** On a fresh load press Tab once: "Skip to main content", Enter → focus should move to the main area (it has `tabindex="-1"`), and the next VO read should start in the main content, not the rail.
4. **Project list and filters.** Rotor → Form controls: the search box, the filter group "Show projects by kind of file", the table "Projects in this workspace" (row/column headers read as you move). Open a project; the status line `#save-state` / action notice should be spoken ("polite") without stealing focus.
5. **Create a project dialog.** Activate "New project". Expect: dialog named "Create a project", focus lands on "Project name", background content is not reachable (VO should not read the page behind it), Tab/VO+Right stays inside, **Esc** closes and focus returns to the "New project" button. Submit empty and listen for the error (`role="alert"`).
6. **Labels and uploading pictures.** Labels panel: add a label, hear "Add a label" form, the list "Labels in this project", move up/down/remove buttons with the label name in their spoken name (check they say *which* label, not just "Remove"). Pictures: choose files, hear the status ("Added 15 pictures…") spoken.
7. **Annotation dialog (the hard one).** Open "Mark items in xb-00.png". Expect: dialog "Mark items in this picture", picture filename announced, groups "Undo, redo and zoom", "Automatic suggestions for this picture" (if shown), the region "Zoomable image annotation workspace" (focusable, with help text), the drawing canvas named "Drawing area for boxes. Select a box, then use the arrow keys…", the list "Boxes in this picture". Tasks: (a) add a box **without the mouse** using "Add a box by typing its position" → "Place a box by typing its position" (Left, Top, Width, Height in %), then hear the new entry "Box 1: 22% from the left, 55% from the top…" and "All changes saved" announced; (b) change its label in the entry's select; (c) with the canvas focused, select a box and nudge with arrow keys; (d) Undo/Redo, zoom +/−, and listen that the zoom level `output` is read; (e) Done closes the dialog and focus returns to the opener. Listen for: mouse-only information (box on the picture) always has a spoken equivalent.
8. **Snapshot and download.** "Make a training set": the headline `#dataset-headline` ("Ready. A snapshot made now would hold … pictures and … boxes.") is read when it changes; "Save a snapshot" form; "Your snapshots" ordered list; the COCO/YOLO radio group is named per snapshot, "Download" button works, the "did not finish" or success status is spoken.
9. **Teach the computer and check screens (seeded project).** Panel headings "Teach the computer", "Is everything ready?", "Start teaching", "Your taught models", "History of decisions"; the training progress `progress` element is named and its text is read (use the seeded project; do not start a real run unless you want one). Open a model's decision dialog if present: dialog "Decision" with description `#decision-lead`, a reason field, errors announced.
10. **Operator screen.** Open `/check/<slug>` (the seeded one is "Seeded bag check"). Landmarks: banner with "Account" navigation and "Sign out", `main`; h1 = screen name, h2 "Before you start" (the "model's output, not a final determination" notice), h2 "Choose a picture" (the step number "1" must **not** be read), "Picture to check" file input, "Check this picture". After a result: h2 "What the computer saw", h3 "Things the computer found", a list of findings each with label, "Very sure · 93%" and buttons "This is right" / "This is wrong". Tasks: pick a picture, run, listen that "checking" progress and then the result are announced once, press "This is wrong" and hear the reason field ("Why is it wrong? (required)") get focus, save, hear the confirmation, then h2 "Your decision" and "Check another picture". Listen for: are the red/green outlines also explained in words (each finding says its label and score)? Is the picture itself described or at least named?
11. **(Bonus) Check-screen builder.** `/projects/<id>/check-screen/setup`: h1 "Set up your check screen", h2 sections "Which screen?", "The basics", "What to look for", "How sure must the computer be?" (slider: is its value read in plain words?), "What the screen says", "What the person checking can do", and the "Preview" complementary region with `role="status"` "You have changes that are not saved…".

Also note while listening: total number of announcements after one action (the console exposes ~10 polite status regions; too much chatter is a real finding), and whether any control is read as just "button" with no name.

## Files

Created in the repo: this file, `docs/dogfood/safari-run.md`, eight PNGs under `docs/dogfood/screenshots/` named `ff-*.png`. Nothing else in the repo was touched. Everything else is under `~/.hermes/profiles/athena/cache/scratch/w18/` (Firefox.app 520 MB, geckodriver, venv, `xb_ff.py` journey, `seed.py`, `reset.sh`, `serve.py`, `fakeworker.py`, `out/ff-*.json` raw results, `shots/` full-size, `safari_smoke.py`) and `w18-data/` (disposable data). Server (8817), stand-in worker, geckodriver and Firefox processes were stopped; the downloaded dmg and the geckodriver tarball were deleted.
