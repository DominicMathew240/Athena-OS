# Athena Forge Voice Data Readiness and ASR Review Upgrade Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Follow strict TDD for every behavior change.

**Goal:** Make Athena Forge’s existing Sarawak Malay speech-recognition workflow safe and honest for real recordings by preventing unreviewed or uncleared audio from appearing training-ready, while making the existing voice review path understandable to non-technical users.

**Architecture:** Extend the existing voice project, transcript, ASR-readiness, and export paths rather than creating a second audio application or altering the Inspec-Ther vision case study. Use one shared backend eligibility predicate for ASR readiness/training/export, persist explicit review status, and have the existing React project page and recording studio show what is still missing. Real corpus use remains blocked until the user supplies authorized, consented, transcript-reviewed speech data and its source evidence.

**Tech Stack:** FastAPI/Python 3.12, MongoDB/Motor, pytest, React 19, Jest/React Testing Library, Yarn.

---

## Current context / assumptions

### What already exists

| Existing capability | Code / evidence | Gap this plan addresses |
|---|---|---|
| Sarawak Malay ASR and speaker splitting | `backend/ml/asr_trainer.py`; `backend/routers/projects.py:2314` (`_load_asr_clips`), `:2360` (`/asr-readiness`), `:2369` (`/train-asr`) | `clip_is_eligible()` currently treats missing consent/rights fields as granted; single-clip/same-speaker evaluation is diagnostic, not evidence of generalization. |
| Audio upload, transcript and word/segment persistence | `backend/routers/projects.py:1101`, `:1307`, `:1444` | New recordings are persisted with `consent_status="granted"`, `rights_status="granted"`, and `quality_ok=True` even though no review is performed by the endpoint. |
| Audio dataset ZIP export | `backend/routers/projects.py:1622` | Exports all audio files, including files with no transcript, and supplies permissive defaults for missing rights/consent/transcript data. |
| Voice workflow guide and transcript editing | `frontend/src/pages/ProjectDetail.js`, `frontend/src/components/TranscriptEditor.js` | “Reviewed” count checks only transcript text, `quality_ok !== false`, and exclusion; it does not require rights, consent, or transcript review. |
| Recording studio with prompts and autosave | `frontend/src/components/AudioRecordingStudio.js` | Sample prompt text is also used to initialize the transcript/translation fields, so a prompt can be mistaken for a transcript verified against recorded audio. |
| Existing tests | `backend/tests/test_asr_trainer.py`, `backend/tests/test_audio_transcript_dataset.py`, `frontend/src/pages/ProjectDetail.test.jsx`, `frontend/src/components/AudioRecordingStudio.test.jsx` | Extend existing tests; do not create duplicate test harnesses. |

The repository’s `TASK_LIST_FORGE_TRAINING.md` marks `ATH-VOICE-001` complete, so this is a **new hardening follow-up**, not a rewrite of that historical completion record. Its declared goal is Sarawak Malay ASR; do not present this as translation, TTS, or a trained model result.

Previously inventoried local audio consists of ten byte-identical `.m4a` uploads and one audio-only `.webm`; no transcript manifest or separately verified labeled voice corpus was found. Treat these as unverified application uploads, not a training dataset. Do not copy or commit them into a demo, tests, or training export. Test fixtures must be generated/synthetic only.

The recent working tree is already dirty: `config/dataset-rights-manifest.toml` is modified, `IDEA.md` is untracked, and `docs/case-studies/` is untracked. Do not stage, overwrite, or clean those paths. Implement on a new feature branch and commit only explicit paths listed in each task.

## Decisions and assumptions

- The first voice outcome remains **Sarawak Malay ASR**; the vision/segmentation data remains separate and is never offered to voice workflows.
- Missing, pending, or unknown consent/rights/review values are **not eligible**. New recordings begin pending/unreviewed.
- A non-empty ASR transcript is not automatically a human-verified transcript. A recorded teleprompter sentence is a prompt until the user listens and confirms/corrects it.
- “Development-ready” and “validated for release” are different claims. Existing single-speaker fallback may remain usable for local smoke testing, but it must be labeled non-generalizing; release/model-quality claims require a speaker-held-out evaluation set.
- No public or training use of the local `.m4a`/`.webm` files until their source, speaker consent, transcription, rights, and permitted use are independently verified.
- Do not invent a minimum corpus size or legal consent copy. Keep training blocked and document the missing decision/evidence if no approved source corpus is supplied.

## Acceptance criteria

1. ASR readiness/training/export reject clips unless transcript, speaker ID, explicit granted consent, explicit granted rights, positive quality review, and explicit transcript review are present; exclusion still always wins.
2. New recording creation stores consent/rights as pending and quality/transcript review as false; it never silently turns a missing field into approval.
3. Dataset export contains only eligible utterances and fails with a clear message if none qualify; it never substitutes a filename for transcript text.
4. The project UI’s reviewed/ready counts use the same policy and explain pending blockers in plain language.
5. The studio keeps prompts distinct from transcript labels and requires listening/review before marking a transcript reviewed.
6. All behavior is demonstrated with synthetic test fixtures. No model metrics, corpus claims, or public-release clearance are asserted from the current local uploads.

## Step-by-step tasks

### Phase 0 — Confirm corpus reality before using any audio

#### Task 1: Re-inventory local audio without exposing content

**Objective:** Confirm which audio files exist and detect repeated uploads by SHA-256 before anyone proposes using them.

**Files:** Read-only: `backend/uploads/`, `backend/projects/`.

Run from the repository root:

```bash
python - <<'PY'
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

roots = [Path("backend/uploads"), Path("backend/projects")]
extensions = {".wav", ".mp3", ".m4a", ".webm", ".flac", ".ogg", ".aac"}
files = sorted(p for root in roots if root.exists() for p in root.rglob("*")
               if p.is_file() and p.suffix.lower() in extensions)
groups = defaultdict(list)
for path in files:
    groups[sha256(path.read_bytes()).hexdigest()].append(path.as_posix())
print(f"audio files: {len(files)}")
print(f"unique SHA-256 groups: {len(groups)}")
print(f"duplicate groups: {sum(len(paths) > 1 for paths in groups.values())}")
for digest, paths in sorted(groups.items()):
    if len(paths) > 1:
        print(f"{len(paths)} identical files: {paths[0]} (sha256 {digest})")
PY
```

**Expected:** The prior inventory found 11 paths and 2 content hashes, with one group of 10 identical `.m4a` files. If the counts differ, record the new inventory and re-check before proceeding; never print transcript/audio contents. This task makes no file changes and is not committed.

### Phase 1 — Make ASR eligibility fail closed

#### Task 2: Add the failing ASR eligibility contract test

**Objective:** Prove that a clip with missing consent, rights, quality review, or transcript review is currently treated too generously.

**Files:** Test: `backend/tests/test_asr_trainer.py`.

Add this test before production changes:

```python
def test_clip_missing_approval_or_review_is_not_eligible():
    base = {"transcription": "Kamek mok makan", "speaker_id": "speaker_a"}
    assert not clip_is_eligible(base)
    assert not clip_is_eligible({**base, "consent_status": "granted"})
    assert not clip_is_eligible({**base, "consent_status": "granted", "rights_status": "granted"})

    reviewed = {
        **base,
        "consent_status": "granted",
        "rights_status": "granted",
        "quality_ok": True,
        "transcript_reviewed": True,
    }
    assert clip_is_eligible(reviewed)
    assert not clip_is_eligible({**reviewed, "exclude_from_training": True})
```

**Run RED:** `source .venv/bin/activate && pytest backend/tests/test_asr_trainer.py::test_clip_missing_approval_or_review_is_not_eligible -q`

**Expected:** FAIL because the existing predicate accepts missing consent/rights/review values. Confirm the assertion failure, not an import/setup error.

**Commit:** No commit yet; pair this test with Task 3’s implementation.

#### Task 3: Enforce the shared fail-closed ASR predicate

**Objective:** Make missing approval/review metadata ineligible for readiness and training.

**Files:** Modify `backend/ml/asr_trainer.py`; test `backend/tests/test_asr_trainer.py`.

Update `clip_is_eligible()` so it requires non-empty transcription and speaker ID, explicit `quality_ok is True`, explicit `transcript_reviewed is True`, and explicit consent and rights values in the existing accepted set (`granted`, `yes`, `approved`). Keep `exclude_from_training` as an unconditional rejection. Do not default missing values to `granted` or `True`.

Update existing readiness fixtures in `backend/tests/test_asr_trainer.py` with an explicit eligible-clip helper so the tests continue to represent reviewed, approved training examples:

```python
def _eligible_clip(**overrides):
    return {
        "transcription": "Kamek mok makan",
        "speaker_id": "speaker_a",
        "consent_status": "granted",
        "rights_status": "granted",
        "quality_ok": True,
        "transcript_reviewed": True,
        **overrides,
    }
```

Use `_eligible_clip(...)` for readiness/split tests; keep the negative cases intentionally incomplete.

**Run GREEN:** `source .venv/bin/activate && pytest backend/tests/test_asr_trainer.py -q`

**Expected:** The module passes; pytest prints `passed` and no failures. Also run `git diff --check` (expected no output, exit 0).

**Commit:** `git add backend/ml/asr_trainer.py backend/tests/test_asr_trainer.py && git commit -m "fix(asr): require explicit clip approval and review"`.

### Phase 2 — Persist honest review state on new voice clips

#### Task 4: Test that new recordings start pending and unreviewed

**Objective:** Ensure the recording endpoint does not manufacture approval or quality-review evidence.

**Files:** Test: `backend/tests/test_audio_transcript_dataset.py` (extend `test_audio_recording_create_and_list`).

After the endpoint returns, assert:

```python
    transcript_doc = res["transcript"]
    assert transcript_doc["consent_status"] == "pending"
    assert transcript_doc["rights_status"] == "pending"
    assert transcript_doc["quality_ok"] is False
    assert transcript_doc["transcript_reviewed"] is False
```

**Run RED:** `source .venv/bin/activate && pytest backend/tests/test_audio_transcript_dataset.py::test_audio_recording_create_and_list -q`

**Expected:** FAIL on the existing `granted` / `True` defaults.

#### Task 5: Change creation defaults and persist transcript-review state

**Objective:** New audio remains ineligible until an authorized user reviews it.

**Files:** Modify `backend/routers/projects.py`; tests: `backend/tests/test_audio_transcript_dataset.py` and `backend/tests/test_asr_trainer.py`.

Set the new `transcript_doc` defaults in `create_audio_recording()` to `consent_status="pending"`, `rights_status="pending"`, `quality_ok=False`, `transcript_reviewed=False`. Add `transcript_reviewed: Optional[bool] = None` to `AudioTranscriptUpdateRequest`; in `update_audio_transcript()`, persist it only when the request explicitly supplies a value. Do not change existing records to approved as a migration side effect.

**Run GREEN:** `source .venv/bin/activate && pytest backend/tests/test_audio_transcript_dataset.py::test_audio_recording_create_and_list backend/tests/test_asr_trainer.py -q`

**Expected:** Both focused suites pass; the newly created clip is ineligible until every approval/review field is explicitly completed.

**Commit:** `git add backend/routers/projects.py backend/tests/test_audio_transcript_dataset.py backend/tests/test_asr_trainer.py && git commit -m "fix(audio): default new recordings to pending review"`.

#### Task 6: Add a regression test for explicit transcript review updates

**Objective:** Verify a reviewer’s explicit transcript-review decision survives the update endpoint.

**Files:** Test: `backend/tests/test_audio_transcript_dataset.py`; implementation if needed: `backend/routers/projects.py`.

Extend the existing transcript-update test with `transcript_reviewed=True`, then assert the persisted response includes `True`. Add a second assertion that omitting the field does not turn a pending transcript into reviewed. Keep the update’s current authorization checks; do not broaden who can modify transcript records.

**Run RED:** `source .venv/bin/activate && pytest backend/tests/test_audio_transcript_dataset.py -k transcript -q`

**Expected:** The new explicit-review assertion fails until the request model/update fields are wired; if it already passes, retain it as regression coverage and continue only after identifying the current behavior.

**Run GREEN:** `source .venv/bin/activate && pytest backend/tests/test_audio_transcript_dataset.py -k transcript -q`

**Expected:** All selected transcript tests pass.

**Commit:** `git add backend/routers/projects.py backend/tests/test_audio_transcript_dataset.py && git commit -m "feat(audio): persist transcript review decision"`.

### Phase 3 — Gate dataset export on the same eligibility contract

#### Task 7: Write export tests for pending and unlabeled recordings

**Objective:** Prove an export omits recordings that are not ASR-eligible.

**Files:** Test: `backend/tests/test_audio_transcript_dataset.py`.

Extend `test_audio_dataset_zip_export` to include two synthetic WAV clips: one with explicit eligible metadata and one with pending consent/rights, `transcript_reviewed=False`, and an empty transcript. Assert the ZIP contains only the eligible WAV/metadata row. Add a case where every clip is ineligible and assert the endpoint returns HTTP 400 with a clear “no eligible reviewed audio clips” detail. Update existing positive export fixtures to include all explicit eligibility fields; do not make production defaults permissive to preserve old fixtures.

**Run RED:** `source .venv/bin/activate && pytest backend/tests/test_audio_transcript_dataset.py::test_audio_dataset_zip_export -q`

**Expected:** FAIL because current export includes all audio records and uses filenames/default-granted metadata when transcript fields are absent.

#### Task 8: Filter export through `clip_is_eligible()`

**Objective:** Use the same backend eligibility policy for both readiness/training and ASR export.

**Files:** Modify `backend/routers/projects.py`; test: `backend/tests/test_audio_transcript_dataset.py`.

For a segmented recording, build each candidate from the segment’s text/speaker override plus the parent transcript’s consent, rights, quality, and review state; skip any ineligible segment. For a non-segmented recording, use the actual transcript only—never the filename as transcript text. Reuse `backend/ml/asr_trainer.py:clip_is_eligible`; do not duplicate the predicate. If no rows survive, raise `HTTPException(400, "No eligible reviewed audio clips are available for export")` before building a ZIP. Explicitly write statuses into exported metadata without `or "granted"` fallbacks.

**Run GREEN:** `source .venv/bin/activate && pytest backend/tests/test_audio_transcript_dataset.py -q`

**Expected:** The test module passes; the export archive includes only approved, quality-checked, transcript-reviewed synthetic fixture clips.

**Commit:** `git add backend/routers/projects.py backend/tests/test_audio_transcript_dataset.py && git commit -m "fix(audio): export only eligible ASR clips"`.

### Phase 4 — Make voice review legible in the existing React flow

#### Task 9: Test the project guide’s reviewed count against every gate

**Objective:** Prevent pending clips from being presented as reviewed in the voice workflow guide.

**Files:** Test: `frontend/src/pages/ProjectDetail.test.jsx`.

Add a voice-project case with one pending clip, one transcript-reviewed/quality-approved clip whose consent is pending, and one fully eligible clip. Assert only the fully eligible clip counts as reviewed/ready and the guide exposes pending consent/rights/review as next actions. Use the existing rendered page and mocked project data; do not add a second voice dashboard.

**Run RED:** `cd frontend && CI=true yarn test --watchAll=false src/pages/ProjectDetail.test.jsx`

**Expected:** `ProjectDetail.test.jsx` fails because the current reviewed count ignores consent/rights and transcript review.

#### Task 10: Align the guide count with backend eligibility

**Objective:** Make the user-facing “review examples” step use the same field requirements as the backend.

**Files:** Modify `frontend/src/pages/ProjectDetail.js`; test: `frontend/src/pages/ProjectDetail.test.jsx`.

Update the guide’s reviewed predicate to require non-empty transcript, `transcript_reviewed === true`, `quality_ok === true`, `consent_status` and `rights_status` explicitly approved, and `exclude_from_training !== true`. Show a concise status for each blocked clip, with plain-language labels such as “Transcript not checked”, “Speaker permission pending”, “Data rights not approved”, and “Recording quality not checked”. Do not show an ASR score for a blocked clip.

**Run GREEN:** `cd frontend && CI=true yarn test --watchAll=false src/pages/ProjectDetail.test.jsx`

**Expected:** `PASS src/pages/ProjectDetail.test.jsx`; the new pending/approved assertions pass.

**Commit:** `git add frontend/src/pages/ProjectDetail.js frontend/src/pages/ProjectDetail.test.jsx && git commit -m "feat(audio): show ASR review blockers in project guide"`.

#### Task 11: Separate teleprompter prompts from verified transcript text

**Objective:** Prevent sample prompt text from being silently saved as a verified transcript.

**Files:** Test: `frontend/src/components/AudioRecordingStudio.test.jsx`; modify `frontend/src/components/AudioRecordingStudio.js`.

First add a test asserting the sample sentence is visible in the teleprompter while the transcript input is empty and the save/review control is not marked complete. The sample remains a recording prompt only. Then initialize transcript/translation as empty for a new draft; keep script prompts and script loading intact; add an explicit “I listened and checked this transcript” control. Until it is checked, send `transcript_reviewed: false` (and do not imply that the prompt text is verified). When the user edits the transcript after checking it, clear the review flag until checked again. Keep autosave behavior, but autosaved state is still unreviewed unless the user explicitly re-confirms after restoring it.

**Run RED:** `cd frontend && CI=true yarn test --watchAll=false src/components/AudioRecordingStudio.test.jsx`

**Expected:** The new assertion fails because the current component initializes transcript from the sample prompt.

**Run GREEN:** `cd frontend && CI=true yarn test --watchAll=false src/components/AudioRecordingStudio.test.jsx`

**Expected:** `PASS src/components/AudioRecordingStudio.test.jsx`; prompt loading/teleprompter tests continue to pass and no new clip becomes review-complete without an explicit action.

**Commit:** `git add frontend/src/components/AudioRecordingStudio.js frontend/src/components/AudioRecordingStudio.test.jsx && git commit -m "fix(audio): separate prompts from verified transcripts"`.

### Phase 5 — Real corpus readiness and final verification

#### Task 12: Document the real voice corpus acceptance checklist

**Objective:** Give the data owner a concrete gate for supplying voice data without misrepresenting local uploads as a corpus.

**Files:** Create `docs/voice/sarawak-malay-asr-data-readiness.md`.

Document required fields and review evidence: stable clip ID; source/collection description; language/dialect and region only when known; pseudonymous speaker ID; transcript corrected against audio; human transcript-review status; audio-quality decision; explicit speaker consent status; rights/license status and evidence reference; exclusion reason; split assignment; SHA-256; and duration/sample-rate metadata. State that the existing ten identical `.m4a` files and lone `.webm` are unverified uploads, not a validated corpus. Require speaker-disjoint evaluation for any generalization claim. Do not include audio, names, consent forms, or test secrets in the document.

**Validation:** `git diff --check` (expected no output, exit 0); review the document against `backend/ml/asr_trainer.py`, `backend/routers/projects.py`, and `TASK_LIST_FORGE_TRAINING.md` so it describes the implemented contract and clearly labels unresolved policy decisions.

**Commit:** `git add docs/voice/sarawak-malay-asr-data-readiness.md && git commit -m "docs(audio): define Sarawak Malay corpus readiness gate"`.

#### Task 13: Run focused regressions and frontend build

**Objective:** Confirm the hardening works across backend and existing React voice surfaces.

**Files:** No new files; verification only.

Run from repository root:

```bash
source .venv/bin/activate && pytest backend/tests/test_asr_trainer.py backend/tests/test_audio_transcript_dataset.py -q
cd frontend && CI=true yarn test --watchAll=false src/pages/ProjectDetail.test.jsx src/components/AudioRecordingStudio.test.jsx && yarn build
```

**Expected:** Both backend modules pass; both targeted frontend suites report `PASS`; `yarn build` exits 0. If the repository has unrelated known test failures, record them separately and do not claim the full suite passed. Run `git diff --check` and inspect `git status --short` to ensure no audio uploads or `IDEA.md` were staged.

No real ASR training is run in this plan. A successful mock-mode ASR test is code-path evidence only, not model accuracy evidence.

## Tests / validation summary

- Backend policy: `backend/tests/test_asr_trainer.py` proves missing/unknown/pending approval and review values fail closed, while an explicitly approved and reviewed clip passes.
- API and archive: `backend/tests/test_audio_transcript_dataset.py` proves new uploads default pending, transcript review is explicit, and export excludes ineligible clips.
- React flow: `frontend/src/pages/ProjectDetail.test.jsx` proves pending clips are not counted as reviewed; `frontend/src/components/AudioRecordingStudio.test.jsx` proves prompts do not become transcript labels without human confirmation.
- Commands follow repository guidance in `AGENTS.md`: root `.venv`, separate backend pytest collection, and Yarn/CRACO for frontend tests/build.
- Tests use synthetic WAV fixtures only. Actual voice data acceptance requires a later human review of the supplied corpus and rights/consent evidence.

## Risks, tradeoffs, and open questions

1. **No verified voice corpus is currently available.** The existing duplicate `.m4a` blobs and one `.webm` do not establish speaker consent, source rights, labels, or suitability. Real ASR training and a public voice demo remain blocked until the owner supplies a lawful corpus.
2. **Consent and rights evidence policy is not yet specified.** Before adding an approval UI, the product owner/legal reviewer must decide the approved notice wording, who can approve rights, how consent can be revoked, and where evidence is stored. Until then, preserve pending states and do not make “granted” the fallback.
3. **Privacy:** voice may identify a speaker. Use pseudonymous IDs, private storage, retention/deletion rules, and avoid putting raw voice or consent records in public artifacts. Do not log transcripts or identifying metadata.
4. **Evaluation:** a single-clip or same-speaker split can smoke-test wiring but cannot support a cross-speaker quality claim. Require a distinct-speaker holdout and disclose sample counts/limitations; do not invent a corpus-size threshold in code.
5. **UX:** more gates can feel slower. Keep technical details hidden, explain the next action in plain language, and allow saving drafts without implying they are training-ready.
6. **Existing local work:** this repo is on `main` with unrelated untracked/modified files. Branch before implementation, preserve those files, and stage only each task’s listed paths.
7. **Follow-up decision:** once a cleared corpus exists, plan a separate task for importing it, resolving evidence paths beneath the dataset root, hashing source/evidence into a frozen artifact, and evaluating speaker-held-out WER. That work must use persisted source IDs and real rights evidence; never use synthetic rights attestations outside tests.
