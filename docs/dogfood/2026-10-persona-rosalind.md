# Adversarial persona walk-through: "Rosalind Teh"

**Local prototype, synthetic data, synthetic stand-in worker (it writes fixed boxes; it is not a model).** One persona, one session, scripted with
Playwright in Chromium (`scratch/w20/journey.mjs`; not committed). This is a simulated user, **not a usability session** and not evidence for the
roadmap gate that needs real operators (kit: `docs/usability/`). Date 2026-10-17, tree after the athena-forge restyle and the jump bar.

**Persona.** Rosalind Teh, 58, airport security shift supervisor. WhatsApp and a paper log. Needs to (A) check a bag picture and say a finding is
wrong, and (B) as the shift's only "computer person", set up a project and get one picture marked. Gives up after about 10 seconds of confusion.

## Her rant

> "The first screen is at least tidy. It says what the computer found, in words, and it says in a yellow stripe it is not the final word. Good,
> that is how I want it. Two buttons, right, wrong. I pressed wrong and it asked me why. Fine. I would have liked it to say what I am
> being asked: 'what did the computer get wrong?'.
>
> Setting up a project, though. Four screens of reading before I draw one box. The page is as long as a church. They added a row of buttons
> at the top so I can jump, thank goodness, but 'Teach' and 'Training set' mean nothing to me. And on the Pictures part there are *two*
> bright yellow buttons next to each other. Which one is the one I press? And a thing that says 'Helper program is not running' in
> yellow type. Is that my fault? Who do I call?"

## Pragmatism filter

| | Complaint | Verdict | Evidence |
|---|---|---|---|
| RED | Pictures panel has competing yellow buttons: "Suggest boxes for pictures without boxes" is yellow while the helper is not running, next to "Mark items" (the thing the person actually wants). Breaks rule 2 in `docs/design/DESIGN.md` ("one yellow action at a time"). | Fix next | `shots/journey/B-03-after-upload.png` |
| RED | "Helper program is not running" is yellow warning text with no instruction for a non-technical person (the Teach panel does say who to ask). | Fix next | same |
| YELLOW | The page is about 7,300 px long. The new jump bar helps; the section names "Training set" and "Teach" are the page's own words, so they were left. | Noted | `B-05-jumped-to-teach.png` |
| YELLOW | "Why is it wrong? (required)" does not say what to write. | Noted | `A-phone-03-wrong-reason.png` |
| WHITE | "Four screens of reading", "I want it like my paper log". | Skip | |
| GREEN | A first-run "start here" that opens the right panel for a brand-new project. The checklist's "Do this next" row does this already; a button that scrolls to it would shorten the cold start. | Consider | `B-02-after-create.png` |

## What was measured

- Operator task (sign in, check a picture, mark finding 1 wrong, save the reason): **4 clicks** at 1440 px and at 390 px. Results, overlay boxes with
  number tags, the "not a final decision" stripe and the reason box all rendered and were readable. No stale "Saved" line was visible on the phone
  (FF-4 from `2026-10-firefox-safari.md` did not show up in this run; I did not test the right-then-wrong sequence).
- Cold start to one marked picture (sign in, New project, Create, Add pictures, Mark items, draw, Done): **8 actions**.
- Jump bar: appears when 2 or more panels are on the page, "Teach" scrolled to 3,071 px, focus moved to the "Teach the computer" heading, the address
  stayed `/#project=<id>`.
- Console and page errors across the whole run: **0**. axe-core after the jump bar: 0 violations on the six pages at 1440 and 390 px.
- A defect I fixed while looking: the sticky jump bar stopped about 44 px short of the right edge, so page content showed through there
  (`.workspace > *` capped its width). Fixed with `.workspace > .jump { max-width: none; }`; I re-ran axe and the smoke script but did **not** re-take the screenshot.

## Follow-up (same day): the two RED items fixed, adversarial moves run

- "Suggest boxes for pictures without boxes" is now an outline button, so the Pictures panel has one yellow action ("Add pictures"; "Mark items" is the small yellow row action).
  The offline line now reads "Suggesting is switched off: the helper program is not running. Ask your administrator."
- The "Why is it wrong?" box now has a one-line help ("Say what you can see that the computer got wrong, for example: 'that is a shadow, not damage'").
- **Found and fixed: two tabs silently lost an edit.** Tab A added a label and saved; tab B (opened before that) added another and saved, and the project ended with
  only B's list; A's label was gone and no message appeared. The label save now sends the list the page last saw (`based_on`); the server refuses with a plain 409
  ("Someone changed this project's labels while you had the page open, so nothing was saved. Reload the page...") when it differs. `based_on` is optional, so API
  callers that omit it behave as before. Three new tests; re-run in the browser: A's label kept, B told to reload.
- Double-click "Create project": one project. Double-click "Add pictures" with one file: one picture.
- Back button with the New project dialog open: the address and page were unchanged and the dialog was closed (it did not take the person out of the console).
- Jump bar checked by eye at 1440 px and 390 px (edge to edge, heading visible below it) and at 1024 px for overflow (0 px).

## Not done

Right-then-wrong on one finding; a failed or empty result ("none-" and "fail-" file names are wired into the stand-in); very long names; refresh during a job; two tabs on anything other than the label list; real operators; Safari; a screen reader. 
