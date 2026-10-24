# Layout pass for non-specialist use: persona "Rosa" (before and after)

**Local prototype, synthetic data, no helper programs running (suggesting, teaching and checking showed their "not switched on" states).** One persona, scripted with
Playwright against disposable instances. A simulated user, **not a usability session**, and not evidence for the roadmap gate that needs real operators
(kit: `docs/usability/`). Date 2026-10-17. Persona: Rosa, 57, quality-control supervisor at a canning plant; WhatsApp and Excel; wants to teach the computer to spot dented cans.

## What the first pass found (RED) and what changed

| Finding | Change |
|---|---|
| The project was one page, about 5,000 px empty and 8,231 px with data | One part at a time (Get started, Labels, Pictures, Make a training set, Teach the computer, Check screens). The chip bar now switches parts, marks the current one, and each part ends with Back / Next. Empty project: 2,028 px on the Pictures part; seeded Teach part 3,612 px |
| At 1366x768 the picture started at y=415, was cut off, and Done / "saved" were below the fold | The picture dialog fills the window; the picture gets the leftover space, Done and the save status sit in a sticky footer; the how-to is open the first time on tall screens only. Smoke test now fails if the picture or Done is off screen at 1366x768 |
| The Teach panel told people to run `python manage.py train_worker` | Plain "Teaching is not switched on yet. Ask your administrator"; the command is inside a "Details for your administrator" fold |
| Results appeared about 400 px from the button, in grey | A floating message at the bottom of the window; good news fades after 15 s, a problem stays until dismissed; also says what to do next |
| Three "create project" buttons on the empty console, one in a different colour | One yellow button; the others are gone |
| 22 yellow buttons on one page (12 "Mark items") | "Mark items" is outlined; one yellow action per part |
| Phone: three stat tiles and a six-chip filter grid before the list | Tiles removed (the count is in the list heading); filters and search appear from 6 projects |

Also: the empty console shows the seven steps with a worked example ("Dented cans"); the history log no longer shows internal ids ("[Acted as Approver, user u1.]" is now "(acted as Approver)", "Another person (u3)" is "Another person").

## Not changed
- "Candidate / Ready for trial / Approved" (three statuses), the word "snapshot", and "Auto-suggest ... helper program" in the Pictures part are the product's own words and were left.
- The Access screen still shows ids such as "u1".

## Verification
`manage.py test projects`: 610 tests OK. Browser smoke test: Chromium 221 checks, WebKit 209, WebKit with the iPhone 13 profile 209, 0 failures. The smoke test was updated: the console is "started" when `#project-count` has `data-ready="true"` (the tiles it used to read are gone), it opens each project part the way a person does, and it checks the picture dialog at 1366x768. Not tested: screen reader, Firefox, real operators.
