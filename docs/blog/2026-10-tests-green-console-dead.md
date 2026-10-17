---
title: "414 tests were green and the console was dead"
date: 2026-10 (draft)
status: first draft, not yet published
---

# 414 tests were green and the console was dead

*Draft. Everything below comes from a local test instance with synthetic data and a toy model. Source: `docs/dogfood/2026-10-operator-ux-report.md` (dated 2026-10-16, with a follow-up on 2026-10-17). Nothing here says anything about real operators, real scanners or real accuracy.*

On the day we first walked the Athena console from sign-in to a confirmed operator decision in a real browser, the first page load failed. The page rendered, and nothing worked. The dashboard counter showed "PROJECTS 00". The auto-suggest badge said "Checking…" and kept saying it. No project could be created.

At that moment the project's test suite had 414 tests, and all of them passed.

## What was wrong

The browser console said: `Cannot access 'trDialog' before initialization`.

In JavaScript, a `const` exists from the top of its scope but cannot be read until the line that declares it has run. Reading it earlier throws. That is the "temporal dead zone". In `console.js` a call, `Athena.manageDialog(trDialog)`, sat roughly 1,500 lines above the line `const trDialog = …`. The report attributes it to an edit that was interrupted part-way.

A top-level script that throws stops at that line. Everything after it never runs, including all the code that wires up buttons and loads the project list. The server had already sent the HTML, so the page looked fine and was dead.

## Why 414 passing tests did not notice

The console's tests are Django tests. They check the HTML the server returns, the JSON API, permissions, validation and the database. For the front-end script they mostly read `console.js` as text and assert that certain strings are present. `web/projects/tests_integration_ui.py` says as much in its opening docstring: focus order, dialogs and layout "are verified in a real Chromium run" and these tests "pin what must stay true on every commit".

A test that asks "does the text `Athena.manageDialog(trDialog)` appear in `console.js`?" passes whether the call is on the right line or 1,500 lines too early. Nothing in the suite executed the script. The report puts it plainly: all 414 tests passed "because they only grep the source".

That limit is documented. What we got wrong was treating a green suite as evidence that the page worked.

## What changed, and what did not

What exists now:

* The call was moved directly under the declaration. The report records a fresh load afterwards with zero page errors.
* One regression test, `LoadOrderTests.test_every_dialog_is_managed_only_after_its_variable_exists` in `web/projects/tests_integration_ui.py`. For three named dialogs it checks that the `const` declaration appears earlier in the file than the `manageDialog` call. It is still a text check. It would catch this exact mistake for those three dialogs; it would not catch the same class of mistake anywhere else in the script.
* The rest of the dogfood report was run with the browser's page errors and failed requests logged on every fresh page load, and it found fifteen further issues (one High, eight Medium, six Low). Fifteen of the sixteen total are fixed; one Low is open by design.
* A follow-up on 2026-10-17 re-checked fixes in Chromium on a fresh load and ran an accessibility scanner (axe) with no violations reported on three screens at two widths.

What did **not** exist when the first draft of this post was written: an automated browser test. We had searched the tree for Playwright, Selenium, Puppeteer and headless-browser configuration, and checked the workflow files; the browser runs in the report used scripts that were not in the repository.

**Update (2026-10-17, later the same day):** it exists now. `web/scripts/browser_smoke.mjs`, run with `bash web/scripts/run_browser_smoke.sh`, builds a disposable database, starts the console on a free loopback port and loads sign-in, the console, a seeded project, every dialog, the check-screen builder and the operator screen in a real browser (Chromium or WebKit). It fails on an uncaught script error, a console error, a Content Security Policy violation, a failed request, a console that rendered but never started, an unnamed control, a field under 16 px, a dialog that lets Tab escape, and a few more. To check that it can fail for the right reason we put a deliberate `ReferenceError` at the top of `console.js`: the run exited 1 with "uncaught script error: deliberatelyMissingFunction is not defined" and "the console did not start: project counter is still 00"; we then restored the file (identical hash). A job in `.github/workflows/web-ci.yml` runs it on a manual dispatch and on pull requests that touch the console's scripts, styles or templates. What it is not: it has not yet run in GitHub's CI (only locally), it does not replace the Django tests, and it covers what a script thinks to look at, so another kind of dead page could still slip past.

## Lessons

1. **A test that cannot fail for the right reason is not coverage.** Before trusting a test, ask what real defect would make it go red. For a text check on a script, the honest answer is "only a missing string".
2. **Load every page in a real browser and fail on page errors.** It is the cheapest check that catches "dead on load", and it takes one screen's worth of code.
3. **Say what a test layer proves.** Our docstring did. A bare count of passing tests does not: it tells a reader nothing about which layer ran.
4. **Dogfooding with a browser is not optional.** The tester behaved like a first-time operator and wrote down what happened. Many of the sixteen findings are things no unit test would see: batch uploads that fail on one wrong file, pages that grow sideways with long names, Back landing on the wrong project.

## What is still unverified

* **Real operators.** No usability session has been run. The report was written by an automated tester acting as an operator. A kit for a real session exists (`docs/usability/m2-usability-session.md`) and has not been used.
* **Other browsers.** The first run used Chromium only. A second pass (`docs/dogfood/2026-10-cross-browser.md`) added WebKit, the Safari engine, on desktop and an iPhone 13 emulation, and found several small things that are now fixed. Firefox could not be started on the test machine and is untested. Real Safari, real iOS and the iOS zoom on focus were not observed, only inferred from the 16-pixel field size.
* **Screen readers.** Labels, roles and focus order were checked from the page structure and with the keyboard, not with VoiceOver, NVDA or JAWS.
* **Real images and accuracy.** The pictures were flat shapes drawn for the test. The model that was taught and approved was a toy that found none of the test boxes. Scores are raw model scores.
* **A guard against the next dead page.** Built (see the update above) and run locally; not yet seen running in CI.

The M2 exit gate in the roadmap stays unticked for these reasons.

## The takeaway

The suite was not lying: it answered questions about the server, and we asked it about the browser. The honest status of the console after the first run was "the server logic is well covered by tests and, on one machine, one browser and fake data, the happy path works end to end". That is a smaller claim than "414 tests pass", and it is the one we can back.
