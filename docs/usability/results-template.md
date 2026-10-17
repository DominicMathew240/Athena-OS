# M2 usability session results — TEMPLATE

Copy this file to `docs/usability/results/YYYY-MM-DD-m2-results.md` and fill it in. Do not edit the template for a real round.
Plan and rules: [`m2-usability-session.md`](m2-usability-session.md).

> Local test instance - prototype, synthetic data, toy model. This file reports what a small number of people did with a
> disposable build. It does not describe real operators in general, real scanners or real model accuracy.

**Status of this file:** TEMPLATE. No session has been run. (Replace with "Round N, complete / incomplete" when real.)

## 1. Round details

| Field | Value |
|---|---|
| Round | |
| Dates | |
| Build | commit hash: ______ ; working tree clean? Y / N (must be Y for a gate run) |
| Instance | Docker Compose / local venv ; Python ____ ; OS ____ ; browser ____ |
| Kit version | 1.0 (frozen before participant 1? Y / N) |
| Changes to software or script during the round | none / list |
| Auto-suggest | disabled (as planned) / other |
| Moderator(s) / observer(s) | codes or initials |
| Compensation / thanks | as decided by the owner: ______ |

## 2. Participants (codes only; no names, employers or job titles together)

| Code | Role described (broad) | Fits criteria? | Assistive tech / access needs (optional) | Consent: session / recording | Completed session? | Stopped early? why |
|---|---|---|---|---|---|---|
| P1 | | | | | | |
| P2 | | | | | | |
| P3 | | | | | | |
| P4 | | | | | | |
| P5 | | | | | | |

Recruitment channel(s) and limits (e.g. all from one site): ______

## 3. Task results (U = completed unassisted, A = completed with A2 help, X = not completed / A3)

| Task | P1 | P2 | P3 | P4 | P5 | Unassisted count | A2 count | A3 count | Errors (total) | Median time (min) | Range (min) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 Create project + labels | | | | | | | | | | | |
| 2 Add pictures | | | | | | | | | | | |
| 3 Mark pictures | | | | | | | | | | | |
| 4 Check, keep a copy, get a file | | | | | | | | | | | |
| 5 Teach, read, decide, approve | | | | | | | | | | | |
| 6 Set up the check screen | | | | | | | | | | | |
| 7 Check one picture | | | | | | | | | | | |
| 8 Give feedback | | | | | | | | | | | |
| 9 Verify preservation | | | | | | | | | | | |
| **All nine unassisted?** | | | | | | | | | | | |

Task 9 moderator check (models before and after identical, feedback count up): P1 __ P2 __ P3 __ P4 __ P5 __

## 4. SUS

| Code | Raw answers 1-10 | Score (0-100) |
|---|---|---|
| P1 | | |
| P2 | | |
| P3 | | |
| P4 | | |
| P5 | | |

Range: ____ to ____. No benchmark claim is made from n = 5.

## 5. Issue log

| ID | Title | Task | Participants | Category | Severity (agreed) | Evidence (timestamp / quote) | Proposed fix | Status | Re-verified how / when |
|---|---|---|---|---|---|---|---|---|---|
| U1 | | | | | | | | | |

Severity per [`m2-usability-session.md`](m2-usability-session.md) section 10 (Critical / High / Medium / Low, same as the dogfood report). Three or more participants raises one level.

| Severity | Found | Fixed and re-verified | Accepted by owner (reason) | Open |
|---|---|---|---|---|
| Critical | | | | |
| High | | | | |
| Medium | | | | |
| Low | | | | |

## 6. Pass/fail check (section 11.2)

| Condition | Met? | Evidence |
|---|---|---|
| 1. n >= 5 fitting participants, fresh instance, committed build, kit frozen | | |
| 2. At least 4 of first 5 (or 80% rounded up) completed all nine unassisted | | |
| 3. No Critical open | | |
| 4. Every High fixed and re-verified, or accepted in writing by the owner | | |
| 5. No real data entered; recordings handled per the plan | | |

**Result:** usability clause MET / NOT MET.  **Date:** ____  **Decided by (owner):** ____

If NOT MET: what changes before the next round, and who the new participants will be.

## 7. Limits of this round

State what this round did not cover: browsers, assistive technology, touch, real scanners, real images, accuracy, population size. Add anything that went wrong in running it.

## 8. Roadmap note (only if MET and the owner agrees)

Text to add under M2 in `ROADMAP.md`: "Usability session (date): N operators on build <hash>; K of N completed all nine tasks unassisted; accepted High issues: <list or none>; limits: <list>. Results: docs/usability/results/<file>."
The gate is ticked only after the owner has also checked its other clauses.
