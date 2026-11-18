/* Guided outcome setup (T06): walks a person through building an operation draft one
   step at a time. The step logic is a thin layer over the section scripts (drafts,
   versions): they own editing and saving; the guide only decides which sections are
   shown, what the next action is, and remembers where you were across a reload. */
(() => {
  "use strict";
  const card = document.getElementById("guide-card");
  if (!card) return;
  const stepsBox = document.getElementById("guide-steps");
  const stage = document.getElementById("guide-stage");
  const exitButton = document.getElementById("guide-exit");
  const RESUME_KEY = "athena.operation-setup.guide";

  /* Sections shown for each step. The recording and line cards are also managed by the
     drafts editor (hidden until a draft exists); the guide never overrides that. */
  const SECTIONS = {
    outcome: ["draft-section"],
    recording: ["recording-section"],
    line: ["line-section"],
    operation: ["operation-setup"],
    versions: ["pin-section", "creator-section"],
    release: ["release-section"]
  };

  // A step is "done" only from what the draft itself has saved — never from what is
  // sitting unsaved in a form. That keeps resume honest after a reload.
  const STEPS = [
    {
      id: "outcome", label: "Outcome",
      done: d => !!d?.name,
      next: d => d?.name ? "Name saved — continue to the recording." : "Name what you want to count, then save the draft.",
      focus: d => d ? null : "#draft-name"
    },
    {
      id: "recording", label: "Recording",
      done: d => !!d?.recording,
      next: d => d?.recording ? "Recording attached — continue to the line." : "Attach one private test recording.",
      focus: () => "#recording-file"
    },
    {
      id: "line", label: "Line",
      done: d => !!d?.line,
      next: d => d?.line ? "Counting line saved — continue to the operation." : "Mark the counting line on the recording.",
      focus: () => "#line-canvas"
    },
    {
      id: "operation", label: "Operation",
      done: d => !!d?.operation_id,
      next: d => d?.operation_id ? "Draft attached to its operation — continue to the versions." : "Create the site, source and operation, then attach this draft to it.",
      focus: () => null
    },
    {
      id: "versions", label: "Versions",
      done: d => !!(d?.pins?.geometry_version_id && d?.pins?.recipe_version_id && d?.pins?.policy_version_id && d?.pins?.model_binding_id && d?.pins?.screen_version_id),
      next: d => "Freeze each choice, then pin it. A release candidate needs every part pinned."
        + (d?.operation_id ? "" : " Attach the draft to an operation first."),
      focus: () => "#pin-fields"
    },
    {
      id: "release", label: "Release",
      done: d => (d?.release_bundles ?? 0) > 0,
      next: d => d?.release_bundles ? "A release candidate exists. Publishing and running it are not built yet." : "Assemble a release candidate once every part is pinned.",
      focus: () => "#release-area"
    }
  ];

  let draft = null;          // the draft the drafts editor has shown (null = none yet)
  let stepId = null;         // current step
  let autoSelected = false;  // one reload-resume attempt per page load
  let resuming = false;      // the guide itself re-opened the saved draft (reload resume)

  const editorControlled = id => id === "recording-section" || id === "line-section";

  function enforceVisibility() {
    const show = new Set(SECTIONS[stepId] || []);
    for (const step of STEPS) {
      for (const id of SECTIONS[step.id]) {
        const node = document.getElementById(id);
        if (!node) continue;
        // The drafts editor keeps the recording/line cards hidden until a draft
        // exists; the guide must not fight that, only narrow further.
        if (editorControlled(id) && !draft) continue;
        if (step.id === stepId || show.has(id)) node.removeAttribute("hidden");
        else node.setAttribute("hidden", "");
      }
    }
  }

  function allDoneUpTo(index) {
    return STEPS.slice(0, index + 1).every(s => s.done(draft));
  }

  function render() {
    const current = STEPS.find(s => s.id === stepId) || STEPS[0];
    stepId = current.id;
    enforceVisibility();
    const complete = STEPS.every(s => s.done(draft));
    stepsBox.replaceChildren();
    for (const step of STEPS) {
      const isCurrent = step.id === current.id;
      const reachable = allDoneUpTo(STEPS.indexOf(step));
      const button = document.createElement("button");
      button.type = "button";
      button.className = "guide-step";
      button.dataset.testid = `guide-step-${step.id}`;
      button.setAttribute("aria-current", isCurrent ? "step" : "false");
      if (step.done(draft)) button.dataset.done = "1";
      const dot = document.createElement("span");
      dot.className = "guide-dot";
      dot.setAttribute("aria-hidden", "true");
      const text = document.createElement("span");
      text.textContent = step.label + (step.done(draft) ? " ✓" : "");
      button.append(dot, text);
      if (!reachable && !isCurrent) {
        button.disabled = true;
        button.title = "Finish the earlier steps first.";
      }
      button.addEventListener("click", () => go(step.id));
      const item = document.createElement("li");
      item.append(button);
      stepsBox.append(item);
    }
    const hint = document.createElement("p");
    hint.className = "guide-hint";
    const lead = document.createElement("span");
    lead.textContent = complete
      ? "Every step is saved. Review any step, or exit setup — your draft stays saved."
      : current.next(draft);
    hint.append(lead);
    const advance = document.createElement("button");
    advance.type = "button";
    advance.className = "a-btn";
    const nextStep = STEPS[STEPS.indexOf(current) + 1];
    if (complete) {
      advance.textContent = "Exit setup";
      advance.dataset.testid = "guide-finish";
      advance.addEventListener("click", turnOff);
    } else if (current.done(draft) && nextStep) {
      advance.textContent = `Continue to ${nextStep.label}`;
      advance.dataset.testid = "guide-continue";
      advance.addEventListener("click", () => go(nextStep.id));
    } else {
      const target = current.focus(draft);
      advance.textContent = "Go to this step";
      advance.dataset.testid = "guide-go";
      if (target) advance.addEventListener("click", () => document.querySelector(target)?.scrollIntoView({ behavior: "smooth", block: "center" }));
      else advance.hidden = true;
    }
    hint.append(advance);
    stage.replaceChildren(hint);
    remember();
  }

  function go(id, quiet = false) {
    const step = STEPS.find(s => s.id === id);
    if (!step) return;
    stepId = id;
    render();
    if (!quiet) card.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function remember() {
    try {
      // Only record a resumable point once a draft is actually in play; the blank
      // start state must never overwrite where a reload should return to.
      if (stepId && draft) sessionStorage.setItem(RESUME_KEY, JSON.stringify({ step: stepId, draftId: draft.id }));
    } catch (_) { /* a blocked sessionStorage must not break the guide */ }
  }

  function readResume() {
    try { return JSON.parse(sessionStorage.getItem(RESUME_KEY) || "null"); } catch (_) { return null; }
  }

  /* Exit is the intentional discard path for the guide itself: the sections return to
     how the editors last left them and every step stays reachable; the draft stays
     saved. Choosing a draft from the list brings the guide back. */
  function turnOff() {
    document.body.dataset.guide = "off";
    for (const step of STEPS) {
      for (const id of SECTIONS[step.id]) {
        const node = document.getElementById(id);
        if (node && !editorControlled(id)) node.removeAttribute("hidden");
      }
    }
    try { sessionStorage.removeItem(RESUME_KEY); } catch (_) { /* ignore */ }
  }

  exitButton.addEventListener("click", turnOff);

  /* The drafts editor announces the selected draft; the versions script keeps pin and
     bundle state on it. The guide re-reads saved state and re-renders from it. */
  document.addEventListener("athena:draft-shown", event => {
    const wasOff = document.body.dataset.guide === "off";
    if (wasOff) delete document.body.dataset.guide;
    const previous = draft;
    draft = event.detail;
    if (!draft) {
      // "Start another": a fresh draft restarts the guide from the beginning.
      stepId = STEPS[0].id;
      render();
      return;
    }
    if (previous && previous.id === draft.id) {
      // A save of the current draft: keep the step and its messages where they
      // are — only the done-marks refresh. The person advances with Continue.
      render();
      return;
    }
    if (resuming) {
      // Reload-resume: the guide itself re-opened the saved draft; land on the
      // saved step (or the first gap if that step no longer exists).
      resuming = false;
      const saved = readResume();
      const firstUndone = STEPS.find(s => !s.done(draft)) || STEPS[STEPS.length - 1];
      go((STEPS.find(s => s.id === saved?.step) || firstUndone).id);
      return;
    }
    // A first save or a deliberate switch from the list: stay on the current step
    // so nothing the person was reading disappears; the done-marks and the
    // Continue button show the way forward.
    render();
  });
  document.addEventListener("athena:draft-updated", event => {
    // Pin, attach and bundle events change the draft in place: refresh the marks
    // without moving the person off their step.
    draft = event.detail;
    render();
  });
  document.addEventListener("athena:draft-list", () => {
    if (draft || autoSelected) return;
    autoSelected = true;
    // After a reload the guide re-opens the draft it was last guiding, so work
    // resumes where it left off instead of at an empty form.
    const saved = readResume();
    if (!saved?.draftId) return;
    const button = document.querySelector(`#draft-list [data-draft-id="${saved.draftId}"]`);
    if (button) { resuming = true; button.click(); }
  });
  document.addEventListener("athena:bundle-assembled", async () => {
    // The bundle POST changes the draft on the server; re-read it so the release
    // step's done-mark reflects the real bundle count, not the pre-assembly copy.
    try {
      const { api } = window.AthenaApp;
      const result = await api(`/api/operation-drafts/${draft.id}`);
      draft = result.draft;
    } catch (_) { /* the next draft-shown refreshes state anyway */ }
    render();
  });

  /* The drafts editor fires its first athena:draft-shown while this script is still
     loading, so the listener above misses it. Render the start state directly; the
     same logic runs again on the first real draft selection. */
  render();
})();
