# M2 usability session kit

**Kit version:** 1.0 · **Written:** 2026-10-17 · **Status: not yet run. No participant has taken part, and no result exists.**
This kit is a plan and a set of scripts. Nothing in it is evidence that operators can use the console.

> Prototype software, synthetic data, toy model. Every screen, picture and number in a session comes from a disposable local
> instance. A session says nothing about real scanners, real baggage images or real model accuracy.

## 1. Purpose, and what the gate requires

`ROADMAP.md` (M2) leaves its exit gate unticked because one condition needs real people. The gate reads, verbatim:

> **Exit gate:** from a clean self-hosted install, a nontechnical operator can use one dashboard to create a project, add and manually label images, review and export a versioned dataset, train or connect a compatible model, configure a task-specific web UI, run an image through it, submit true/false feedback, and verify that the correction is preserved without changing live model weights. The project registry/connector boundary must be modality-extensible, but only the image-detection path is claimed complete at this gate. The flow must be covered by tests and observed in a usability session; production scanner integration remains gated on selecting real hardware/protocol.

An automated Chromium walk-through already ran the mechanical flow once (`docs/dogfood/2026-10-operator-ux-report.md`). It was a
tester acting as an operator, not an operator. This kit covers the remaining clause, "**observed in a usability session**".

**What a session can show:** where non-technical people get stuck, what they misread, and whether a small group can finish the
whole flow without help.
**What it cannot show:** a success rate for a population (n = 5 is far too small), real-scanner suitability, accessibility for
screen-reader users, other browsers, or model accuracy. Those stay open in the roadmap.

**What passing this does not do by itself.** The gate has other clauses ("clean self-hosted install", "covered by tests",
"modality-extensible registry"). A passing session closes only the usability clause. Section 11 says how to record that.

## 2. Participants

* **Number:** at least **5** who complete a session (recruit 7 to allow for no-shows). More is better; the pass rule scales (section 11).
* **Who:** non-technical operators, for example warehouse, logistics, security-scanner or inspection-lane staff, or people doing
  comparable screen-based checking work.
* **Include:** adults who use a web browser and mouse in daily life; comfortable reading English (the console is English only).
* **Exclude:** anyone who writes code or does data/ML work; anyone who has used an image-labelling or model-training tool;
  anyone who works on, contributes to, or is related to someone on the Athena OS project; anyone who has seen the console before.
* **Record, don't require:** anyone who uses assistive technology or has other access needs may take part and say so. Their result
  is reported separately; it is **not** counted as "screen-reader tested" unless the session was built for that.
* **Recruitment note.** Recruit through your own contacts or a third party who can reach this group. If participants work for a
  company, get that company's permission for staff time, and tell them in writing that **individual performance is not reported to
  employers**. Do not recruit from people who might feel unable to refuse. Say plainly that the software is a prototype.
* **Compensation:** `[PLACEHOLDER: the owner decides whether, how much and in what form participants are thanked. Do not state or imply any
  payment or benefit in recruitment messages until this is decided.]`
* **Background questions** (ask before the tasks, record answers by participant code only): job title in your own words (not employer);
  how often you use a computer at work; have you ever drawn boxes on pictures for a computer program to learn from (screen-out if yes); do you
  need anything adjusted to use a computer comfortably (optional).

## 3. Roles and materials

* **Moderator:** runs the script in section 7, one person. Does not fix the software during a session.
* **Observer:** second person, silent, fills the observation sheet (section 9) and keeps time. If you only have one person, record the screen
  and fill the sheet afterwards from the recording (it takes about 2x the session length).
* **Facilitation rule:** the moderator knows where everything is and must not show it. See section 7.2 for what can be said.
* **Materials:** laptop with the disposable instance (section 6); a second screen or screen-recording on that laptop; stopwatch; printed
  task cards (section 7.4, one per page); printed consent form; printed SUS sheet; the observation sheet; the folder `session-pictures/` (section 6.3).
* **Setting:** quiet room. The participant uses the mouse and keyboard; the moderator sits beside, not behind. Browser: Chromium-family browser
  at 100% zoom, window at least 1280 px wide. Other browsers and phones are **not** covered by this plan.

## 4. Consent and ethics

Read this aloud, give it on paper, and answer questions before you start. Do not start recording until the participant has agreed in writing.

### 4.1 Consent text (plain language)

> **What this is.** We are testing a piece of software called the Athena console. It helps people mark objects in pictures so that a
> computer can learn to find them, and then lets a person check new pictures. We want to find out where the software is confusing.
> **We are testing the software, not you.** There are no wrong answers, and you cannot fail.
>
> **What you will do.** You will do nine small jobs on a computer, one at a time, and tell us what you are thinking while you do them.
> Then you will answer ten short questions about how it felt, and we will have a short chat. It takes up to two hours. You can have a break whenever you like.
>
> **The pictures are made up.** Every picture and name in the software is invented. You will not see or enter any real baggage, real
> people, real company information or your real passwords. Please do not type any.
>
> **Recording.** With your permission we will record your screen and your voice (not your face) so we can study what happened. Only the project
> team will see the recording. We will refer to you by a code such as "P3", not your name. We will keep the recording on an encrypted disk for
> `[PLACEHOLDER: retention period, e.g. 6 months]` and then delete it. We will not publish the recording. We may quote what you say in
> reports without your name. You can say yes to the session and no to the recording; then we will take notes only.
>
> **You can stop.** You can skip a job, take a break or stop the whole session at any time, without giving a reason and without any consequence.
> If you stop, we will delete your data on request until `[PLACEHOLDER: date, e.g. 30 days after your session]`.
>
> **Your employer.** If you came through your employer, we will not tell them how you did or what you said. We report patterns across people.
>
> **Questions or complaints.** `[PLACEHOLDER: contact name and email]`

### 4.2 Consent form

```
Participant code (filled in by the team): P__        Date: ____________

[ ] I have read or heard the information above and had the chance to ask questions.
[ ] I agree to take part in the session.
[ ] I agree to my screen and voice being recorded.        (leave unticked for notes only)
[ ] I understand I can stop at any time without giving a reason.

Signature: ______________________     Printed name (kept apart from results): ______________________
Moderator: ______________________
```

Keep signed forms apart from notes and recordings, in a locked place. The only link between name and code is the signed form.

### 4.3 Stop conditions for the moderator

Stop and thank the participant if they ask to stop, appear distressed, or if the software shows a real person's data (it must not).
Never push a participant to continue. Log "stopped by participant" and keep what exists up to then.

## 5. Data handling

* **Only synthetic data** goes into the instance. The generator in section 6.3 draws coloured shapes stamped "SYNTHETIC".
* **Recording** is screen + audio only, to a local encrypted disk; no cloud upload, no transcription service that stores audio.
* **Notes** use codes (P1, P2...). The name-to-code sheet is the signed consent form and nothing else.
* **Instance:** destroyed after every session (section 6.5). Its database holds only the synthetic project and the participant account.
* **Retention:** `[PLACEHOLDER: owner sets]`. Delete on request within the withdrawal window.
* **Repository:** results files may be committed. Recordings, consent forms and the code-to-person link must never be committed.
* **Local law:** this kit is not a data-protection review. `[PLACEHOLDER: confirm requirements for your jurisdiction before recruiting.]`

### 5.1 Screenshots and labelling rules (prototype / synthetic)

Everything shown outside the project team follows the existing dogfood convention (`docs/dogfood/screenshots/`):

1. Every screenshot or clip carries the words **"Local test instance - prototype, synthetic data, toy model"** in the caption, and the picture
   itself keeps the "SYNTHETIC" stamp visible.
2. Never present a model score, "found N of M" figure or screen wording from a session as evidence about real items or real accuracy.
3. Never publish raw recordings, participant voices, or screenshots that show a participant's face, name, account or desktop. Crop to the browser window.
4. Quote participants only as "P3" with their consent; never attach a job title and employer together.
5. Do not re-use a session screenshot in marketing. Keep to the report, the blog and the roadmap evidence trail.

## 6. Pre-session checklist (disposable instance)

Everything marked **verified** was run on 2026-10-17 on macOS with Python 3.13.0 in the repository's `.venv-web`
(Django 6.1.2, torch 2.9.1, torchvision 0.24.1, transformers 5.17.0). Everything marked **not run** is taken from `web/DEPLOYMENT.md` or
`web/docker-compose.yml` and was not executed for this kit.

There is **no reset script** in the repository. "Reset" below means deleting the data directory and recreating it with the commands that exist.

### 6.1 Preferred, for "clean self-hosted install" evidence: Docker Compose (not run)

From `web/DEPLOYMENT.md`:

```bash
cd web
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(64))"     # paste into DJANGO_SECRET_KEY
$EDITOR .env      # set ATHENA_ALLOWED_HOSTS and ATHENA_CSRF_TRUSTED_ORIGINS; for a session also set ATHENA_AUTOLABEL_BACKEND=disabled (see 6.4)
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

`DEPLOYMENT.md` says a TLS-terminating proxy is expected in front of port 8000. For a session on one laptop you can open
`http://127.0.0.1:8000` only if you set `ATHENA_SECURE_SSL=0` (documented as "trusted internal network" use); **not run**, so check the console loads before the first participant.
Reset: `docker compose down -v` removes the compose stack and its `athena-data` volume (standard Docker Compose behaviour; **not run** here).

### 6.2 Local, single machine (verified)

```bash
cd "<repo root>"
source .venv-web/bin/activate     # or: python3.13 -m venv .venv-web && pip install -r web/requirements.txt && pip install -r web/requirements-autolabel.txt (see web/README.md; on Linux add --extra-index-url https://download.pytorch.org/whl/cpu)
export DJANGO_DEBUG=1                                  # loopback only, no HTTPS redirect (web/README.md)
export ATHENA_DATA_HOME="$HOME/athena-session/data"    # disposable data directory (settings.py: DATA_HOME)
export ATHENA_AUTOLABEL_BACKEND=disabled               # see 6.4
python web/manage.py migrate
python web/manage.py createsuperuser                   # one account for the participant; choose a throw-away password and keep it out of this repo
```

Then in three terminals, same exports in each (each of these printed its "ready" line when run on 2026-10-17):

```bash
python web/manage.py runserver 127.0.0.1:8000          # web
python web/manage.py train_worker                      # prints: Training worker ready. Backends usable here: torchvision-fasterrcnn.
python web/manage.py inference_worker                  # prints: Inference worker ready.
```

Run **exactly one** of each worker (`web/DEPLOYMENT.md`). `createsuperuser --noinput` needs `--email` as well as
`DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD` (checked: without `--email` it stops with "You must use --email with --noinput.").
If `docs/guides/access-roles.md` describes roles, follow it when you create the participant account and record which role the participant had.

### 6.3 Synthetic pictures (verified)

Save as `make_pictures.py` and run `python make_pictures.py` (needs Pillow, which `web/requirements.txt` installs). It writes 14 pictures for the
participant to add and 2 held back for the "run an image" task; the seed is fixed so every session gets the same files.

```python
# Synthetic session pictures: flat coloured shapes, NOT real items. Output: ./session-pictures/
import random
from pathlib import Path
from PIL import Image, ImageDraw

out = Path("session-pictures")
out.mkdir(exist_ok=True)
rng = random.Random(7)  # fixed seed: every session gets the same pictures

def knife(d, x, y):   d.polygon([(x, y), (x + 120, y + 10), (x + 120, y + 26), (x, y + 22)], fill=(190, 190, 200)); d.rectangle([x - 50, y + 6, x, y + 24], fill=(80, 50, 30))
def laptop(d, x, y):  d.rectangle([x, y, x + 140, y + 90], fill=(60, 70, 90)); d.rectangle([x - 15, y + 90, x + 155, y + 104], fill=(120, 120, 130))
def bottle(d, x, y):  d.rectangle([x, y + 30, x + 40, y + 140], fill=(40, 130, 90)); d.rectangle([x + 13, y, x + 27, y + 30], fill=(40, 130, 90))

names = [f"bag-scan-{i:02d}.png" for i in range(1, 15)] + ["check-me-01.png", "check-me-02.png"]
for name in names:
    img = Image.new("RGB", (640, 480), (225, 225, 220))
    d = ImageDraw.Draw(img)
    for fn in rng.sample([knife, laptop, bottle], k=rng.choice([1, 2])):
        fn(d, rng.randint(80, 420), rng.randint(60, 300))
    d.text((8, 8), "SYNTHETIC - not a real scan", fill=(120, 0, 0))
    img.save(out / name)
print(f"wrote {len(names)} pictures to {out.resolve()}")
```

Put the two `check-me-*.png` files in a separate folder (`session-pictures-held-back/`) before the session so the participant meets them only in task 7.
The pictures are plain shapes; objects overlap rarely but sometimes. A first-time labeller will mark the same shape the same way each time; that is the point.
(A similar setup in the dogfood run (14 synthetic pictures, 30 rounds on a laptop CPU) produced a toy model that found 0 of 4 test boxes. Expect a weak model; see 6.6.)

### 6.4 Auto-suggest is switched off for sessions

`ATHENA_AUTOLABEL_BACKEND=disabled` hides the feature in the console (`web/athena_web/settings.py`; the autolabel worker then exits with
"Auto-labelling is disabled", checked). Reasons: it is not one of the nine tasks, its first run downloads ~1 GB of model weights, it slows teaching on a shared CPU, and it
brings in the CLIP licence caveat in `docs/LICENSE_AUDIT.md`. If you want to observe it as an extra, do it **after** task 9, with the weights already cached
(`python web/manage.py autolabel_worker` once beforehand, which loaded from cache on 2026-10-17), and record it separately.

### 6.5 Reset between participants (local)

1. Close the participant's browser window; stop the web server and both workers (Ctrl-C in each terminal).
2. `rm -rf "$ATHENA_DATA_HOME"` (this removes the database, uploaded pictures, login-throttle cache and any trained models, all under that directory; double-check the variable first with `echo "$ATHENA_DATA_HOME"`).
3. `python web/manage.py migrate` then `python web/manage.py createsuperuser` again. Use a fresh password.
4. Restart the three processes. Confirm: `curl -s http://127.0.0.1:8000/healthz` answers 200 with JSON; the worker terminals print their ready lines; sign in yourself and see the empty "Create your first project" state; sign out.
5. Reset the browser: new profile or private window, no saved passwords, cache cleared (stale scripts hide defects; the dogfood run needed `Cache-Control: no-cache` for this reason).
6. Copy `session-pictures/` fresh to the participant folder; restore the held-back folder.
7. Write the commit hash (`git rev-parse HEAD`) and whether the working tree is dirty (`git status --short`) on the observation sheet. **A gate run must use a committed build.**

### 6.6 Dry run (required before participant 1)

The moderator does all nine tasks once on a fresh instance with a stopwatch. This checks the setup, calibrates the time caps in section 8, and shows the model result the participant will see
(a weak or empty result is likely: from-scratch training on a few synthetic pictures; approving a model that found none of the test boxes requires ticking an acknowledgement box). **Freeze the caps and wording before participant 1; do not change them between participants.** If you must change the software or
the script after participant 1, note it in the results file and treat the change as a new round.

### 6.7 Final readiness check

[ ] Instance fresh, three processes running, `healthz` OK · [ ] participant account works · [ ] pictures in place, held-back pair separate · [ ] auto-suggest disabled ·
[ ] recorder tested with audio · [ ] consent forms, task cards, SUS sheet printed · [ ] observation sheet open · [ ] commit hash noted · [ ] phone on silent.

## 7. Moderator script

### 7.1 Opening (about 8 minutes)

1. Welcome. "Thanks for coming. I'm going to ask you to try some software and talk through what you're doing. It's the software that's being tested, not you."
2. Consent (section 4). Start recording only after the signed form.
3. Background questions (section 2).
4. Explain think-aloud: "As you work, please say out loud what you're looking at, what you're trying to do, and anything that surprises or confuses you. If you go quiet, I may remind you. I'll mostly stay quiet and won't be able to help much, because I want to see what you'd do on your own."
5. Scenario: "Imagine you work in a team that checks bag pictures. Your team lead wants a way for colleagues to check pictures for three kinds of items: knives, laptops and bottles. You'll set that up from the beginning. The pictures are made up. Use whatever you'd naturally use."
6. Show the sign-in page; the participant signs in with the account you give them. (Signing in is not a scored task. Hand over the password verbally; do not write it on the task cards.)

### 7.2 Neutral wording and assistance levels

**You may say:** "Please keep thinking aloud." "What are you looking for?" "What would you do next if I weren't here?" "What did you expect to happen?"
"Take your time." "There's no right answer, do what feels natural." Silence, then wait at least 20 seconds before saying anything.
If asked for help: "I can't tell you where to click. What would you try?" If they say "I'm stuck" after 2 minutes with no progress: "Would you like to carry on or move to the next job?"

**You may not say:** names of buttons, panels or steps, "try the ... menu", "that's right/wrong", "good", "almost", or any hint toward the screen.
Do not react to errors. Do not read the screen aloud.

**Assistance levels** (the observer logs each as it happens):

| Level | Meaning | Counts as "assisted"? |
|---|---|---|
| A0 | No help. | No |
| A1 | Neutral prompt from the "you may say" list above. | No (log it anyway) |
| A2 | Any hint that points at a place, word or action on screen, or answers a question about how the software works. | **Yes** |
| A3 | Moderator performs the step, or the participant gives up / hits the time cap and the moderator moves on. | **Yes**, and the task is **not completed** |

After A3 on a task other tasks depend on, the moderator completes the missing step out of the participant's sight where possible, and the sheet records "set up by moderator", so
later tasks can still be observed. Those later tasks are scored on their own.

### 7.3 Order and flow

Hand over one task card at a time, read it aloud, then start the stopwatch when the participant starts. Stop it when the participant says they are done, or at the cap (section 8).
Training takes minutes; the clock **pauses while the console shows that teaching is in progress** and the participant has nothing to do. Do not say what to do while waiting; if they ask, "what would you do?"
After each task: "Thank you. Anything you want to say about that one?" (one sentence, no probing) then the next card.
Taking breaks is fine; note the break on the sheet.

### 7.4 The nine tasks (print one per card)

Each task is mapped to the clause of the exit gate it observes. Card text avoids the console's own words on purpose, so that finding the right place is part of the test.

| # | Gate clause | Card text (read aloud and hand over) |
|---|---|---|
| 1 | "create a project" | "Start a new piece of work called **Bag check - lane 4**. It will be used to find three kinds of items in pictures: **knife**, **laptop** and **bottle**. Set it up so the software knows those three." |
| 2 | "add ... images" | "Put the pictures from the folder **session-pictures** into your work." |
| 3 | "manually label images" | "For at least **10** of the pictures, mark every knife, laptop and bottle you can see, so each item is outlined and named correctly." |
| 4 | "review and export a versioned dataset" | "Make sure your pictures and markings are in good shape to be kept. Then keep a frozen copy of them exactly as they are now, and get a file you could give to another team." |
| 5 | "train or connect a compatible model" | "Make the computer learn to find these three items from your work. When you have read how it did, decide whether it is good enough to be used for real checks, and if so allow it." |
| 6 | "configure a task-specific web UI" | "Make a screen that a colleague in lane 4 could use to check a bag picture with that computer model. Give it a name and wording your colleagues would understand." |
| 7 | "run an image through it" | "Pretend you are the colleague in lane 4. Check the picture **check-me-01.png** from the folder **held-back** using the screen you made." |
| 8 | "submit true/false feedback" | "Look at what the computer said about that picture. Tell the system what was right and what was wrong, including anything the computer missed. (If you think nothing was wrong, tell the system that.)" |
| 9 | "verify that the correction is preserved without changing live model weights" | "A colleague asks: *Did the system keep what I just told it? And did my telling it change the computer model that is being used for checks?* Show me how you would find out." |

**Moderator-side evidence per task** (checked by the moderator after the participant says done, not told to the participant):

| # | End state that counts as completed | How the moderator checks |
|---|---|---|
| 1 | A project named as asked exists with exactly the labels knife, laptop, bottle (case may differ only if the console allows it; record exact text) | Project page |
| 2 | At least 14 pictures in the project, none from the held-back folder | Pictures panel |
| 3 | At least 10 pictures each have one or more accepted boxes, every box with the right label (ignore small placement differences; count wrong labels as errors, not as non-completion) | Picture dialog / panel counts |
| 4 | A saved snapshot (dataset version) exists **and** the participant obtained a downloaded file for it (COCO or YOLO, either is fine) | Snapshots panel; the file in Downloads |
| 5 | A model exists in the approved state with an approval note, made from that snapshot | Models panel (stage "approved") |
| 6 | A check screen exists for the project, switched on, pointing to that approved model, with a title the participant chose | Check screens list |
| 7 | An observation exists for `check-me-01.png` on that screen, shown as a result to the participant | Operator screen shows a result |
| 8 | At least one feedback record exists for that observation (right/wrong on a finding, a missed object, or a whole-result decision) | Operator screen "Recorded" message; or `manage.py shell` count below |
| 9 | The participant shows (a) the recorded decision and (b) that the approved model is the same one as before (same name/version, not a new model) and says in their own words that feedback did not change it | Participant's demonstration; then the checks below |

Moderator checks for tasks 8 and 9 (verified to run, on an empty database, on 2026-10-17). Run **before task 7 starts** and again **after task 9**:

```bash
python web/manage.py shell -c "from projects.models import ModelVersion, FeedbackRecord; print([(m.stage, m.artifact_sha256[:12]) for m in ModelVersion.objects.all()], FeedbackRecord.objects.count())"
```

Task 9 is passed only if the model list (stages and checksum prefixes) is **identical** before and after, and the feedback count went up. The console re-checks the full artifact hash itself before approval and before every prediction; the
prefix comparison only shows nothing was swapped.

### 7.5 Think-aloud prompts (use sparingly)

* Participant silent more than 20 seconds while working: "What are you thinking?" / "Keep talking me through it."
* After a surprise (page changed, message appeared): "What happened there?" / "What did you expect?"
* After a long pause on one screen: "What are you looking for?"
* Do **not** ask "was that confusing?", "did you see the button?", "do you like it?" during tasks. Save opinions for the debrief.

### 7.6 SUS questionnaire (after the last task)

Hand over the printed sheet. Ask the participant to answer quickly, with their first impression, for the whole of what they used today.
The ten items are the **System Usability Scale** as published by John Brooke, "SUS: A 'quick and dirty' usability scale", in *Usability Evaluation in Industry*
(Taylor & Francis, 1996). Each is answered 1 (strongly disagree) to 5 (strongly agree). Checked on 2026-10-17 against the chapter text reproduced at https://digital.ahrq.gov/sites/default/files/docs/survey/systemusabilityscale%2528sus%2529_comp%255B1%255D.pdf ("System Usability Scale (c) Digital Equipment Corporation, 1986"): the ten items and the scoring below match it. The original source is Brooke's chapter, which I did not obtain; keep the instrument unchanged (do not reword "system").

| # | Item |
|---|---|
| 1 | I think that I would like to use this system frequently. |
| 2 | I found the system unnecessarily complex. |
| 3 | I thought the system was easy to use. |
| 4 | I think that I would need the support of a technical person to be able to use this system. |
| 5 | I found the various functions in this system were well integrated. |
| 6 | I thought there was too much inconsistency in this system. |
| 7 | I would imagine that most people would learn to use this system very quickly. |
| 8 | I found the system very cumbersome to use. |
| 9 | I felt very confident using the system. |
| 10 | I needed to learn a lot of things before I could get going with this system. |

**Scoring:** for items 1, 3, 5, 7, 9 take (answer - 1); for items 2, 4, 6, 8, 10 take (5 - answer); add the ten values and multiply by 2.5 for a score from 0 to 100.
SUS is **not** a pass criterion here: with five people it is a rough signal, not a measurement (section 11). Report each person's score and the range.

### 7.7 Debrief (about 10 minutes)

Ask: "What was the hardest part?" "Was there a moment you weren't sure what to do next?" "In your own words, what did the software do with your corrections?"
"Where, if anywhere, did you think it might lose or change your work?" "What would you change first?" "Did any wording sound like a different word to what you'd use at work?"
Thank them. Confirm withdrawal window and contact. Then reset (6.5).

## 8. Success criteria per task

Record, per participant and task: **completed without help** (A0/A1 only, end state met, before the cap), **time**, **errors**, **assistance count** (A2, A3 separately; A1 for information).

**Error** = an action that produced a refusal or warning message, an unintended record (a wrong label, an extra project, a wrong file added, a duplicate screen), or one the participant themselves undid because it was wrong.
Count each once. Reading a message is not an error.

**Time** = stopwatch from the participant starting the card to saying they are done, minus time the console shows teaching in progress with nothing for the participant to do.

**Time caps** (stop point, then A3 and move on). **These are working numbers chosen without data; adjust them in the dry run (6.6), then freeze them.**

| # | Task | Working cap |
|---|---|---|
| 1 | Create project + labels | 6 min |
| 2 | Add pictures | 5 min |
| 3 | Mark 10 pictures | 20 min |
| 4 | Check, keep a copy, get a file | 10 min |
| 5 | Teach, read results, decide, approve | 12 min (not counting teaching wait) |
| 6 | Set up the check screen | 10 min |
| 7 | Check one picture | 4 min |
| 8 | Give feedback | 6 min |
| 9 | Verify preservation without changing the model | 8 min |

A task is **completed unassisted** when the end state in 7.4 is met, no A2 or A3 was logged for it, and the cap was not reached.
A participant **completes the flow unassisted** when all nine tasks are completed unassisted.

## 9. Observation sheet

One copy per participant. The observer writes while the participant works.

```
Participant: P__   Date: ______   Build (commit): ________  Tree dirty? Y / N   Browser: ________
Moderator: ______  Observer: ______  Recording: Y / N   Break(s): ______
```

| # | Task | Start | End | Teach wait (min) | Done unassisted? (Y/N) | A1 | A2 | A3 | Errors | Quote / what they said | Issue IDs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Create project + labels | | | n/a | | | | | | | |
| 2 | Add pictures | | | n/a | | | | | | | |
| 3 | Mark pictures | | | n/a | | | | | | | |
| 4 | Check, keep a copy, get a file | | | n/a | | | | | | | |
| 5 | Teach, read, decide, approve | | | | | | | | | | |
| 6 | Set up the check screen | | | n/a | | | | | | | |
| 7 | Check one picture | | | n/a | | | | | | | |
| 8 | Give feedback | | | n/a | | | | | | | |
| 9 | Verify preservation | | | n/a | | | | | | | |

Also log: what the participant expected vs what happened; words they used for things (their term vs the console's); any moment they believed something was saved or changed when it was not (or the reverse);
and anything they said about trust in the result. Fill in the issue log (section 10) the same day.

## 10. Issue log and severity rubric

Use the same four levels and the same categories as `docs/dogfood/2026-10-operator-ux-report.md`, so findings can be filed beside it.

| Severity | Meaning in a session | Dogfood examples |
|---|---|---|
| **Critical** | The participant cannot start or continue the flow even with a reasonable workaround; data or work is lost; the page is dead; a result or message is wrong in a way that could lead to a harmful decision. | F1: console dead on load. |
| **High** | A reasonable participant cannot complete a task unassisted, or misreads something important (e.g. believes feedback retrains the live model), or a failure is not explained. | F2: one wrong file rejected the whole batch, unnamed. |
| **Medium** | The participant completes the task but with confusion, a wrong turn, a workaround or a misleading label; inconsistent behaviour. | F4, F6, F11, F15. |
| **Low** | Cosmetic, wording slip, minor friction with no effect on completion or understanding. | F5, F9, F13. |

**Categories:** Functional, UX, Content (wording), Visual, Accessibility, Safety (a misleading or risky outcome). One issue may carry two (as "UX / Safety" in the dogfood report).

**Rules:**
1. Rate by what you observed, not by what you think users should have done. Two people (moderator and observer) rate independently, then agree; if they cannot, keep the higher level.
2. An issue seen with **three or more** participants is raised one level (Low to Medium, Medium to High, High to Critical).
3. Count a participant stopping or being given A3 on a task because of the issue as at least High.
4. A participant who believes feedback changed the live model, or believes their work was saved when it was not, is a **Safety** issue, High at minimum.
5. Each issue records: ID, title, task, participants affected, category, severity, evidence (timestamp or quote), proposed fix, status (open / fixed / accepted-with-reason), and how the fix was re-verified.

## 11. Analysis plan and pass/fail rule

### 11.1 Analysis

1. Matrix: participants x tasks, showing completed-unassisted / assisted / not completed.
2. Per task: count of unassisted completions, A2 and A3 counts, errors, median and range of time. With n = 5, do not compute means or percentages as if they generalise; show the counts.
3. SUS: each participant's score and the range; do not state a population benchmark or "good/bad" threshold from five people.
4. Issues: dedupe across participants, rate per section 10, group by task and by gate clause, list what changed in the software between participants (should be nothing; section 6.6).
5. Write the findings into `docs/usability/results/YYYY-MM-DD-m2-results.md` using `docs/usability/results-template.md`. Quote participants by code only.
6. State deviations honestly: skipped tasks, setup changes, participants who did not match the criteria, moderator slips (hints given without logging).

### 11.2 Pass/fail rule for the usability clause

The usability clause of the M2 exit gate may be marked as **met** only if **all** of these hold in the results file:

1. **n >= 5** participants who fit section 2 each completed a full session on a **fresh instance** of one **committed** build (hash recorded), with the kit frozen as in 6.6.
2. **At least 4 of the first 5** participants (or at least 80% of all, rounded up, if more than 5 took part) completed **all nine tasks unassisted**: all nine at A0/A1, within the caps. A participant who is stopped or A3'd on any task does not count towards this.
3. **No Critical issue is open.** "Open" means not fixed and re-verified. Re-verification is the moderator repeating the affected task on a fresh load of the fixed build, plus, for a Critical in tasks 1-9, a new participant walking the full flow.
4. **Every High issue** is either fixed and re-verified in the same way, or accepted in writing by the owner with the reason (an accepted High is listed in the roadmap note).
5. **No participant data issue:** no real data entered, recordings handled as in section 5.

Otherwise the clause is **not met**: leave the gate unticked, fix, and run a new round with **new** participants (people who have seen the console cannot be used again).
If 1-4 hold but participants were recruited from one narrow group, say so; it does not fail the rule, but the roadmap note must name the limit.

**Interpretation:** four of five is a smoke-test threshold for finding blockers in the flow, not evidence that 80% of operators would manage. The roadmap text must say "observed in a session of N operators", not "usable by operators".

**Tick procedure.** Only the owner ticks the gate. When the rule is met, add to `ROADMAP.md` M2: the date, n, the build hash, counts per task, any accepted High issues and the limits (one browser, synthetic data, toy model, no screen reader,
no real scanner), link the results file, and re-read the other gate clauses ("clean self-hosted install", "covered by tests") before ticking. If they are not independently evidenced, the gate remains unticked even if the session passes.

## 12. What this kit leaves out

* Sessions with operators using assistive technology, touch devices, or browsers other than Chromium-family: separate plans needed.
* Remote moderated sessions: not planned here (recording consent and instance access need a different setup).
* Real scanner images and real model accuracy: out of scope for M2 and for this kit.
* Auto-suggest labelling: optional extra, recorded separately (6.4).
* The Docker Compose path (6.1) and any browser other than Chromium were not run by the author of this kit.
