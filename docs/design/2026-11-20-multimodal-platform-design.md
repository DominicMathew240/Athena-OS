# Athena OS multimodal platform — current development design and the Antigravity/Hydrogen frontend plan

Date: 20 November 2026. Status: design record for the platform as it stands after the
round-1–3 swarm merges (main at the state with T01–T14, T19–T22 on the vision list; Wave 0
complete, Wave 1's four slices and Wave 2's text/signals slices merged), plus the frontend
development process the team will use going forward. Nothing in this document claims
shipped behaviour that does not exist in the tree; where a section describes a target, it
says so.

## 1. Where the multimodal platform actually is

The platform's shape is one shared lifecycle with modality plugins riding on it:

- **One project lifecycle, many data types.** A project carries a `recipe` (task type);
  every recipe belongs to a modality; every modality has a plugin that validates uploads,
  validates annotations, and describes assets. The web console dispatches through the
  registries (`web/projects/tasks/`, `web/projects/modalities/`) — no `if modality ==`
  branches in views.
- **Frozen dataset snapshots are universal.** Any modality freezes a dataset version
  through the same endpoints: one `DatasetEntry` per asset carrying the accepted
  annotation verbatim, deterministic SHA-keyed splits, manifest checksum, guarded and
  audited export. Images additionally export COCO/YOLO; other modalities export the
  manifest plus raw files.
- **Training runs behind one contract.** `TrainingBackend` (name, licence note,
  `check_available()`, `train()`, `evaluate()`, `predict()`) with a registry that loads
  builtins plus third-party entry points. The web process never imports the heavy
  libraries; workers do the work. The current backend set: `torchvision-fasterrcnn`
  (image detection, default), `audio-wav-cnn`, `ocr-trocr`, `tabular-sklearn`,
  `text-sklearn`, `signal-sklearn`, `dfine-coco` (opt-in).
- **Recipes today** (the console's "New project" cards): vision detection, audio
  classification, handwriting OCR, spreadsheet prediction, text classification, signal
  classification, plus the operations people-counting workflow (vision domain pack).

The honest limits stay the honest limits: all training verified on synthetic data only;
no real-data vertical, no usability session, model quality unmeasured; the OCR serving
path is backend-level; TrOCR base weights have no licence (open owner decision — see
`docs/LICENSE_AUDIT.md` risk 2b).

## 2. What the console looks like today

The current frontend is server-rendered Django templates with small vanilla-JS modules:
`console.html` (project home with per-recipe cards), stepper-driven teach flows, the
operations draft/setup screen (T06) with the T13 replay surface and T14 line editor, and
the check-screen builder. Styling is a token system (`static/projects/tokens.css`): light
("clean and friendly") and dark ("control room") themes, three self-hosted OFL fonts
(Anybody display, Hanken Grotesk body, Martian Mono for identifiers), a CSP that allows
no external origins. Everything is desktop-first with deliberate 390 px mobile checks.

This is a working, tested UI — but it has grown screen by screen, and the multimodal
expansion (seven recipes and counting) is exposing the pattern this design document
exists to fix: every new modality currently means a new bespoke screen family.

## 3. The target frontend design: one console, every modality

The design direction is consolidation around shared surfaces rather than new screens per
modality:

1. **A modality-agnostic project workspace.** One workspace layout — data, labels,
   snapshots, teach, check — whose panels are configured by the project's recipe, not
   hardcoded per modality. A recipe declares its workspace shape (which panels, which
   annotation editor) through the same registry the backend uses, so a new modality
   arrives with its workspace, not a new screen tree. The per-recipe cards on the console
   home remain the entry point.
2. **The operations screens as the pattern for guided work.** The T06 stepper (draft →
   recording → line → reference marks → pins → release) is the established guided-flow
   idiom; the replay surface (T13) and the accessible line editor (T15, in flight) are
   the established media-annotation idioms. The workspace reuses these components — the
   same replay/overlay canvas serves video events (T25+), audio scrubbing, and document
   page views, with per-modality overlay renderers behind one component contract.
3. **Readable-state discipline everywhere.** The console's honesty rules become UI rules:
   every panel shows its state (ready / blocked with one actionable step — the T08
   preflight and T12 view-health card pattern), every number shows its denominator and
   freshness, and unavailable capability is said plainly, never hidden behind a spinner.
4. **Tokens stay the contract.** All new surfaces consume `tokens.css` variables only;
   no per-screen colour or font values. The light/dark pair is non-negotiable; a new
   surface that only works in one theme is a defect.
5. **Progressive enhancement.** Server-rendered pages with JS modules that enhance, never
   pages that render nothing without JS. Forms work without JS; the replay surface and
   editor degrade to their numeric alternatives (the T15 accessibility requirement).

## 4. The Antigravity / Gemini Hydrogen frontend development process

All frontend development for this platform will use **Google Antigravity** with the
**Gemini 4 Hydrogen** model as the execution engine.

Why this pairing: Hydrogen is Antigravity's fast-execution model, strong at
design-to-code and live UI iteration, and it works inside Antigravity's agentic editor
(browser tooling, editor-artifact loops, planning surfaces) — a good match for a
token-driven, progressively-enhanced template codebase where the feedback loop is
"design → implement → look at it in the browser → refine". The division of labour:
Hydrogen implements frontend code (templates, CSS against tokens, JS modules, Playwright
E2E scripts); the Athena OS backend, models, and the repo's verification suites remain
the existing Django/Python stack with the existing review discipline.

Working rules, so the process fits this repo's discipline rather than fighting it:

1. **Tokens and CSP are load-bearing constraints.** Hydrogen gets `tokens.css` and the
   CSP as fixed inputs: all colours/fonts via variables, no external origins, no new
   runtime dependencies (no React/Vue/etc. — the stack is deliberately dependency-light
   vanilla JS + Django templates, and a design that needs a framework is a design
   change to propose, not to smuggle in).
2. **Every generated screen ships with its verification.** A Hydrogen-produced surface
   is not done until the existing test patterns cover it: server-side markup tests
   (tests_operation_setup_ui style), the Playwright E2E script family in
   `web/scripts/`, and the 390 px mobile check. Generated code goes through the same
   review-and-verify gates as human code — including the swarm rule that claimed-green
   suites are re-run by a verifier who did not write them.
3. **The honesty rules are UI requirements, not niceties.** Prompts to Hydrogen must
   carry them explicitly: no capability the backend does not have may appear in the UI;
   blocked states show one actionable next step; denominators and freshness on every
   number; "not available" is said plainly.
4. **Design artifacts are executable prompts.** Mockups and component specs live in this
   `docs/design/` directory next to this record; Hydrogen works from them with the token
   file, and the artifact is updated when implementation legitimately diverges — the
   artifact and the tree must not drift.
5. **Accessibility is a first-class input.** The T15 keyboard/numeric/aria-live patterns
   are the baseline contract for every new surface Hydrogen builds; a surface that needs
   a mouse or colour recognition is rejected at review.

## 5. What this means for the roadmap

- The vision task list's operator-screen section (E: T41–T46) is where this design lands
  first: the default people-counting board (T41) and parking board (T42) become the
  first Hydrogen-built surfaces under this process, on top of the existing widget/layout
  versioning (T43).
- The multimodal workspace consolidation (section 3.1) is Wave-2-adjacent platform work;
  it does not replace any roadmap item, it makes the per-modality slices stop multiplying
  screens.
- Backend work continues on the existing plan (T22 tracking → T23 event ledger → T25
  crossings on the vision side; ASR/NLP-remainder on the multimodal side) regardless of
  the frontend process change.

## 6. Open decisions (owner)

1. TrOCR base-weight licence (audit risk 2b) — blocks any commercial claim on the
   handwriting tool, unrelated to this design but still open.
2. Whether the workspace consolidation (3.1) is scheduled before or after the first
   operator boards (T41/T42) — this document assumes boards first, workspace as the
   consolidation step after.
3. Antigravity/Hydrogen usage details (seats, prompt libraries, artifact conventions)
   are the team's to define as adoption starts; this document fixes only the repo-side
   rules.
