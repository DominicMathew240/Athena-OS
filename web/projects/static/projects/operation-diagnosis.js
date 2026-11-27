/* T32: plain-language diagnosis of a succeeded operation execution.
   Categories (source/view, geometry, rules, recognition) keep correct
   recognition from looking like a reason to retrain. */
(() => {
  "use strict";

  const CATEGORY_LABELS = {
    none: "Looks correct",
    source_view: "Source or recording",
    geometry: "Counting line or bays",
    rules: "Counting rules",
    recognition: "Recognition",
    bind_model: "Model binding"
  };

  const TRAINING_SKIP = "Recognition looks fine — do not start training from this miss.";
  const TRAINING_OK = "Recognition needs work — capture examples before training.";

  function pickBranch(diagnosis) {
    if (!diagnosis || typeof diagnosis !== "object") return null;
    if (diagnosis.comparison && typeof diagnosis.comparison === "object") {
      return {
        primary: diagnosis.primary_category || diagnosis.comparison.primary_category || "none",
        recommend: Boolean(diagnosis.recommend_training),
        plain: diagnosis.comparison.plain || "",
        next: diagnosis.comparison.next_step || "",
        counts: diagnosis.comparison.counts || {},
        parking: diagnosis.parking && typeof diagnosis.parking === "object" ? diagnosis.parking : null
      };
    }
    // Flat t32.replay_diagnosis.v1 block
    return {
      primary: diagnosis.primary_category || "none",
      recommend: Boolean(diagnosis.recommend_training),
      plain: diagnosis.plain || "",
      next: diagnosis.next_step || "",
      counts: diagnosis.counts || {},
      parking: null
    };
  }

  function formatDiagnosis(diagnosis) {
    const branch = pickBranch(diagnosis);
    if (!branch) {
      return {
        ok: false,
        title: "No diagnosis yet",
        body: "Run a release candidate against a recording to see whether misses are source, geometry, rules or recognition.",
        recommend_training: false,
        primary_category: "none",
        category_label: CATEGORY_LABELS.none,
        training_line: ""
      };
    }
    const label = CATEGORY_LABELS[branch.primary] || branch.primary;
    const training_line = branch.recommend ? TRAINING_OK : TRAINING_SKIP;
    let body = branch.plain || `Primary cause: ${label}.`;
    if (branch.parking && branch.parking.plain) {
      body += ` Parking: ${branch.parking.plain}`;
    }
    const matched = branch.counts.matched;
    const missed = branch.counts.missed;
    const extra = branch.counts.extra;
    let totals = "";
    if (Number.isFinite(matched) || Number.isFinite(missed) || Number.isFinite(extra)) {
      totals = ` Matched ${matched ?? 0}, missed ${missed ?? 0}, extra ${extra ?? 0}.`;
    }
    return {
      ok: true,
      title: `Diagnosis: ${label}`,
      body: body + totals,
      recommend_training: branch.recommend,
      primary_category: branch.primary,
      category_label: label,
      training_line,
      next_step: branch.next || ""
    };
  }

  function renderDiagnosis(container, diagnosis) {
    if (!container) return formatDiagnosis(diagnosis);
    const view = formatDiagnosis(diagnosis);
    container.replaceChildren();
    container.hidden = false;
    const heading = document.createElement("p");
    heading.className = "a-help";
    heading.dataset.testid = "diagnosis-title";
    const strong = document.createElement("strong");
    strong.textContent = view.title;
    heading.append(strong);
    const body = document.createElement("p");
    body.className = "a-help";
    body.dataset.testid = "diagnosis-body";
    body.textContent = view.body;
    const train = document.createElement("p");
    train.className = "a-help";
    train.dataset.testid = "diagnosis-training";
    train.dataset.recommendTraining = view.recommend_training ? "true" : "false";
    train.textContent = view.training_line;
    if (view.primary_category && view.primary_category !== "none") {
      const tag = document.createElement("span");
      tag.className = "a-tag";
      tag.dataset.testid = "diagnosis-category";
      tag.dataset.category = view.primary_category;
      tag.textContent = view.category_label;
      heading.append(" ", tag);
    }
    container.append(heading, body, train);
    return view;
  }

  window.AthenaOperationDiagnosis = { formatDiagnosis, renderDiagnosis, CATEGORY_LABELS };
})();
