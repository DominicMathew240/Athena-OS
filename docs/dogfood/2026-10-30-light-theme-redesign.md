# Light-theme redesign: measurements and a simulated-user pass (2026-10-30)

**Local prototype, synthetic data, no helper programs running (suggesting, teaching and checking showed their "not switched on" states).**
Everything below came from disposable consoles built by `web/scripts/seed_browser_smoke.py` and driven with Playwright in Chromium. The persona is a
**simulated user, not a usability session**, and is not evidence for the roadmap gate that needs real operators (kit: `docs/usability/`).
Screenshots are of a prototype, not a deployed product.

## What changed

The console moved from the dark yellow "spectrum plate" to a calm light theme (`docs/design/DESIGN.md`), and a project now opens on an **Overview** (one
recommended next step and four counts) instead of "Get started". Scope: `console.css`, `access.css`, `checkscreen.css`, the sign-in, access, message and
check-screen shells, and the Overview in `console.html` / `console.js`. Not done: splitting the project list and the open project into two routes,
renaming Pictures / snapshot / Teach to the plan's nouns, a dark theme, rights and duplicate safeguards (see the plan's Epics C, E3, H).

## Measurements

| Check | Before (dark) | After (light) |
|---|---|---|
| Django tests (`manage.py test projects`) | 1486 OK, 28 skipped | 1486 OK, 28 skipped |
| Browser smoke, Chromium | 682 passed, 18 failed | 682 passed, same 18 failed |
| Console / page errors across the capture script (32 views, 1440 and 390 px) | 0 (one expected 404 on the error page) | 0 (same) |
| Horizontal overflow at 390 px | 0 px on every page except the 404 page (498 px, **unchanged, pre-existing**: that page is Django's default and does not load the stylesheet) | same |
| Body colour / font on console pages | near-black, Anybody + Hanken Grotesk | `rgb(245,246,248)`, Hanken Grotesk only |

The 18 baseline smoke failures are all in the Teach part and need the training helper ("The teaching tool cannot run here yet"); they fail the same way
without any of these changes. The colour and wording assertions in `browser_smoke.mjs` (yellow RGB values, "Get started") were updated to the new
palette and name; one assertion that read the model list before it had loaded was made to wait for it.

### Page length (document height in px, same capture script, fresh load per view)

Seeded project (same data before and after):

| View | Before (dark) | After (light, final) |
|---|---|---|
| Overview, 1440 | 1709 | 1875 (the Overview card and counts are new) |
| Overview, 390 | 3004 | 3195 |
| Labels, 1440 / 390 | 1508 / 2531 | 1519 / 2454 |
| Pictures, 1440 / 390 | 2983 / 4775 | 3027 / 4869 |
| Make a training set, 1440 / 390 | 2325 / 3586 | 2354 / 3675 |
| Teach, 1440 / 390 | 3232 / 5093 | 3288 / 5193 |
| Check screens, 1440 / 390 | 1385 / 2261 | 1396 / 2311 |

Fresh console with no projects: first-run page 1586 to 1625 at 1440 px and 2720 to 3025 at 390 px; one new project open 1694 to 1866 and 2857 to 3162.
So pages are 1 to 10 percent longer, and the first-run page on a phone about 11 percent longer. The panel padding, step padding and gaps were tightened after the
first pass (an earlier pass was longer still); the remaining growth is mostly the new Overview card and the larger text spacing.

**Correction.** An earlier draft of this note said the first-run page had grown from 2720 to 3612 px. That comparison was wrong: the "empty" console had already
been given a project by the baseline capture, so it was not empty. The table above uses a freshly built database.

## Persona: "Pak Hamid", 61, warehouse quality supervisor

Excel and WhatsApp only. Needs to start a project for cracked tiles, add one photo, say what to look for, and mark one crack. Gives up after about ten
seconds of confusion. Script: sign in, New project, follow the blue button, add a photo, add a label, mark a box (`persona.mjs`, not committed).

> "The first thing I see now is one big blue button that says what to do. Good. 'Choose pictures', I choose pictures, it takes me to the right place
> and the cursor is already in the box. Then I have to hunt for the word 'Overview' at the top to find out what is next. Every time. Why does it not just
> take me to the next thing? And on the list below there is a *second* blue button that says the same as the first. Which one is it?
> On the phone the picture is at the bottom, under four rows of buttons."

### Pragmatism filter

| | Complaint | Verdict | Evidence / action |
|---|---|---|---|
| RED | Two identical blue buttons for the same step: the new next-step card and the highlighted "Do this next" checklist row. Breaks the contract's "one blue button per part". | **Fixed**: the checklist row's button is now a normal (white) button; the card holds the one blue action. | `persona/desk-4-overview-after-upload.png` (before the fix) |
| RED | After each step the person had to find the Overview tab at the top to learn what is next (3 times in a 15-action cold start). | **Partly fixed**: every part after Labels now ends with an "Overview: what to do next" button beside Back and Next. The click count did not fall (14 to 15 either way); the way back is now at the point where the person is looking. Making the footer's Next button follow the recommended step would remove the detour; not done. | `persona/persona.log`, `persona2/persona.log` |
| YELLOW | Phone: after "Mark items" the picture started about 500 px down, under the label picker, undo/zoom row, "Suggest boxes" and "type a box position". | **Fixed** (CSS order, 580 px and narrower): the picture now starts at about 250 px; undo/zoom, "Suggest boxes" and typing a position follow it. | `persona/phone-6-annotation.png` (before), `persona3/phone-6-annotation.png` (after) |
| YELLOW | The floating message at the bottom overlaps the last checklist row until dismissed. | Open, pre-existing. | `persona/desk-4-overview-after-upload.png` |
| YELLOW | Page length grew (table above). | Partly addressed: spacing tightened; +1 to 10 percent remains. | `capture_views.mjs` runs |
| WHITE | "I want a paper list." | Skip | |
| GREEN | "Boxes to review" counts boxes, but a person thinks in pictures ("which pictures still need me?"). | **Done**: the project list API now returns `pending_pictures` and the Overview shows "Pictures to review" (test added). Query cost not measured. | `docs/api-reference.md` |

### Measured

- Cold start (sign in, New project, Create, Choose pictures, Add pictures, Add label, Save labels, draw one box, Done): **15 clicks/drags and 5 typed fields**
  at 1440 px and again at 390 px. The Overview named the right next step at each of four checkpoints (Add pictures, Define labels, Draw or auto-suggest boxes,
  Make a training set). Counts followed the data (Pictures 0 to 1; pictures to review stays 0 because a hand-drawn box is accepted, not suggested).
- The first blue button put keyboard focus on the file field; the second on the new-label field.
- Console and page errors: **0**. Horizontal overflow: 0 px at both widths.

## Final runs

Last run, after the final edit:

- `manage.py test projects`: **1487 tests, OK, 28 skipped** (the baseline 1486 plus the new `pending_pictures` test).
- Browser smoke, Chromium: **682 checks passed, 18 failed**; the 18 are the same set as before the redesign (diffed line by line), all needing the training helper.
- Browser smoke, WebKit (Safari engine): **642 passed, 20 failed** on the final run, against **627 passed, 21 failed** on the untouched commit `0ddeb1a` run the same way
  (a clean `git archive` copy). Failures differ run to run in WebKit (one extra pair of timing-dependent steps appeared once and not on the second run), and
  the redesign adds none that reproduce. WebKit with the iPhone 13 profile: 631 passed, 23 failed on one run, not compared with a baseline.
- Firefox: **not run**. The Playwright Firefox build in this environment exits at launch (before and after the change), so no Firefox result exists.
- Persona script, desktop and phone: 14 clicks and 5 typed fields, 0 console errors, 0 px horizontal overflow.

An earlier run in this round had 4 extra Chromium failures (hard-coded yellow RGB values and "Get started" in the script), 1 failing Django test (the "incomplete" badge
border), and later 1 more (a test pins the sentence "a saved copy of your labelled pictures", which a rewording had dropped); each was fixed and the runs above are after the
fixes. One smoke step that read the model list before it had loaded was made to wait for it: it passed in the baseline and failed once, so it was a timing race the new
spacing exposed, not a product change.

## Accessibility and roles (Chromium, 1440 and 390 px)

- **axe-core** (wcag2a, wcag2aa, wcag21aa, wcag22aa, best-practice): **0 violations** on sign-in, the console, the create dialog, all six project parts, the picture
  dialog, the operator check screen, the check-screen builder and Access, at both widths (26 page states). axe cannot judge reading order, whether wording makes
  sense, or text drawn over a photograph.
- **Keyboard, Overview** (project at "Add pictures"): from the Overview tab, Tab reaches the next-step button in one stop with a 3 px solid blue outline;
  Enter moves to the Pictures part and puts focus on the file field. This did not start from the very top of the page (the skip link and rail were not
  counted), so it is a partial walk, not a full keyboard test of the page.
- **Role gate** (project at "Add pictures"): a Viewer sees "Next: Add pictures", the button **switched off**, and the sentence "Your account can look but not change
  things. Ask an administrator for the Labeler role."; a Labeler sees it enabled. Not seen: a Labeler on step 6 (needs a Trainer), because no test project is at that step.

## Detector

`impeccable detect` found nothing in `console.css`, `checkscreen.css` or `access.css`. Its three findings on the templates are artefacts: the stylesheet link is
a Django `{% static %}` tag it cannot resolve (so it read browser defaults, hence "flat type hierarchy 16/16/16"), and the "img with no src" is the
annotation dialog's picture, which the script fills in.

## Changed after the persona pass

- The next-step card names pictures, not boxes: the project list API gained `pending_pictures` and the Overview shows "Pictures to review" (test in `tests_autolabel.py`).
- The Teach plan sentence now names the saved copy it will use ("your latest saved copy ... (snapshot 3)") or the new one it will make first
  ("make a saved copy of your labelled pictures first (snapshot 4; the ones you already saved stay as they are)"). The journey map called this "silent"; that was too strong, because
  the sentence already said it would save a copy first and the page says so afterwards. What was missing was the number. **Not seen on screen**: the Teach button is switched off
  on this machine (no training helper), so the sentence was checked by reading the code and the unit tests that pin its wording, not in a browser.
- Phone picture dialog: the picture starts at about 250 px instead of 500 px (undo and zoom, "Suggest boxes" and typing a position come after it).
- Spacing tightened (panel and step padding, gaps), see the page-length table.

## Round 2 (same day): keyboard, roles, tree proxy, adversarial moves

Chromium, disposable consoles, synthetic data. Script `round2.mjs` (not committed). To get a project at step 6 the disposable database's approved model was set back to "candidate"
(so the next step is "Teach the computer", which needs the Trainer role).

- **Keyboard, from the top of the page.** The next-step button is the **14th Tab stop** (skip link, Projects, Access, Sign out, New project, the project row, Delete this project,
  the six project tabs, then the button). Every one of the 14 stops has a visible outline of 3 px or more and is on screen; Shift+Tab goes back one stop. The button sits after the
  whole project list and the delete button; moving it earlier would mean changing the page order, which was not done. Filed as YELLOW.
- **Role gate at step 6.** A Labeler sees "Next: Teach the computer", the button switched off, and "Your account can label pictures but cannot teach the computer, move a
  model to trial or set up check screens. Ask an administrator for the Trainer role." A Viewer sees the same button off with "Your account can look but not change things.
  Ask an administrator for the Trainer role." (Earlier in the round: a Viewer and a Labeler at step 1.)
- **Accessibility-tree proxy (not a screen reader).** Landmarks: banner, "Main navigation", main, "Jump to a part of this project"; headings in order H1 "Your projects",
  H2 "All projects", H2 project name, H3 "Overview", H4 "Next: ..."; 12 live regions; **0 unnamed controls**. Only a real screen reader run counts as screen-reader tested.
- **Adversarial moves on the Overview.**
  - Double-clicking Create produced one project.
  - A 118-character project name caused 0 px horizontal overflow at 1440 and 390 px.
  - Refresh and the Back button kept the project, the Overview and the person inside the console.
  - A non-image (audio) project hides the Overview and says "This kind of project has no labelling tools yet."
  - Not conclusive: the double-click on the next-step button (the project open at that moment was not the one I meant to test) and the phone check of the long name's Overview text.
- **Create dialog copy.** The dialog now gives a worked example ("Dented cans", to find dents in photos of cans) and says pictures must be JPEG, PNG or WebP. Seen in the dialog; no test pins the old wording.
- **Console errors:** 0.

### Filed from round 2

| | Complaint | Verdict |
|---|---|---|
| YELLOW | The next-step button is the 14th Tab stop, after the project list and "Delete this project". | Open. |
| WHITE | None found. | |

## Firefox

Playwright's Firefox build (1555) exits at launch with "Could not find profile folder" on this Mac, with a normal temp folder and with a changed one, on the untouched commit as well.
So Firefox has not been run at all. This is an environment fault, not a Firefox result.

## Not done or not verified

Firefox (the browser would not start here); Safari beyond the WebKit engine; a screen reader; a Labeler or Viewer on the approve step; the Teach, results and approval screens with real helper programs; real operators; contrast of text over photographs;
the dark theme (not built); the project-list/project route split, renaming to the plan's nouns, and the rights and duplicate safeguards (Epics C, E3, H), which are
plan items that need design or owner decisions, not styling.
