/* View suitability and source health (T12): for each source, measure the view
   from the actual stored recording and show each dimension's verdict with one
   next step. The object-size estimate is the user's own statement, never a
   measurement. Read-only apart from saving that estimate. */
(() => {
  "use strict";
  const { api } = window.AthenaApp;
  const section = document.getElementById("view-health-section");
  if (!section) return;

  const status = document.getElementById("view-health-status");
  const box = document.getElementById("view-health-sources");

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    if (props) for (const [key, value] of Object.entries(props)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const child of children) if (child !== null && child !== undefined) node.append(child);
    return node;
  }

  const VERDICT_CHIP = { ok: "a-chip--clear", review: "a-chip--attention", unsuitable: "a-chip--critical" };
  const VERDICT_LABEL = { ok: "Suitable", review: "Review", unsuitable: "Unsuitable" };

  function dimensionRow(dimension) {
    const body = el("div", { class: "op-row-main" },
      el("strong", { text: dimension.title }),
      el("span", { class: "a-help", text: dimension.detail }));
    if (dimension.next_step) {
      body.append(el("span", { class: "a-help", text: `Next step: ${dimension.next_step}` }));
    }
    return el("li", {
      class: "op-row",
      "data-testid": `view-health-${dimension.id}`,
      "data-verdict": dimension.verdict
    }, body, el("span", { class: `a-chip ${VERDICT_CHIP[dimension.verdict] || "a-chip--attention"}`,
      text: VERDICT_LABEL[dimension.verdict] || dimension.verdict }));
  }

  function estimateArea(source, estimate, guidance, recordings) {
    const wrap = el("div", { class: "op-details-box", "data-testid": "object-size-estimate" });
    wrap.append(el("p", { class: "a-help", text: guidance }));
    const input = el("input", {
      class: "a-input", type: "number", id: `estimate-${source.id}`, min: "1", max: "100", step: "any",
      placeholder: "e.g. 50", "data-testid": "object-size-input"
    });
    if (estimate) input.value = String(estimate.person_height_percent);
    const button = el("button", { class: "a-btn", type: "button", text: "Save estimate", "data-testid": "object-size-save" });
    button.addEventListener("click", async () => {
      wrap.querySelector(".estimate-status").textContent = "Saving…";
      try {
        const value = Number(input.value);
        if (!input.value || Number.isNaN(value)) throw new Error("Enter your estimate as a percentage of the picture height.");
        await api(`/api/operation-sources/${source.id}/view-estimate`, {
          method: "PUT", json: { person_height_percent: value }
        });
        wrap.querySelector(".estimate-status").textContent = "Estimate saved.";
        await load();
      } catch (problem) {
        wrap.querySelector(".estimate-status").textContent = problem.message;
      }
    });
    const clear = estimate
      ? el("button", { class: "a-btn", type: "button", text: "Clear estimate", "data-testid": "object-size-clear" })
      : null;
    if (clear) clear.addEventListener("click", async () => {
      wrap.querySelector(".estimate-status").textContent = "Clearing…";
      try {
        await api(`/api/operation-sources/${source.id}/view-estimate`, { method: "DELETE" });
        await load();
      } catch (problem) {
        wrap.querySelector(".estimate-status").textContent = problem.message;
      }
    });
    wrap.append(
      el("label", { class: "a-label", for: `estimate-${source.id}`,
        text: "A person stands about this tall (% of picture height) — your estimate" }),
      el("div", { class: "estimate-controls" }, input, button, clear),
      el("p", { class: "a-help estimate-status", role: "status", "aria-live": "polite",
        text: recordings ? "" : "A recording is needed before the estimate changes any verdict." }));
    return wrap;
  }

  async function renderSource(source) {
    const card = el("section", { class: "a-card", "data-testid": `view-health-source` });
    const result = await api(`/api/operation-sources/${source.id}/recordings`);
    const health = result.view_health;
    const estimate = result.object_size_estimate;
    const recordings = result.recordings;
    const verdict = health ? health.verdict : "review";
    const heading = el("div", { class: "op-row" },
      el("div", { class: "op-row-main" },
        el("strong", { text: source.name }),
        el("span", { class: "a-help",
          text: recordings.length
            ? `Measured from the latest recording (${recordings[0].width}×${recordings[0].height}, `
              + `${Math.round(recordings[0].duration_seconds)} s, imported ${recordings[0].ingested_at.slice(0, 10)}).`
            : "No recording imported for this source yet." })),
      el("span", { class: `a-chip ${VERDICT_CHIP[verdict]}`, text: VERDICT_LABEL[verdict] }));
    card.append(heading);
    if (health) {
      const list = el("ul", { class: "op-rows" });
      for (const dimension of health.dimensions) list.append(dimensionRow(dimension));
      card.append(list);
    } else {
      card.append(el("p", { class: "a-help", text: "View health could not be shown for this source." }));
    }
    const guidanceResult = await api(`/api/operation-sources/${source.id}/view-estimate`);
    card.append(estimateArea(source, estimate, guidanceResult.guidance, recordings.length > 0));
    return card;
  }

  async function load() {
    status.textContent = "Loading view health…";
    box.replaceChildren();
    try {
      const sources = (await api("/api/operation-sources")).sources;
      if (!sources.length) {
        status.textContent = "No sources yet. Add a source below, import a recording, and its view health appears here.";
        return;
      }
      const cards = await Promise.all(sources.map(source =>
        renderSource(source).catch(problem => el("p", { class: "a-error",
          text: `Could not show view health for ${source.name}: ${problem.message}` }))));
      box.replaceChildren(...cards);
      const unsuitable = box.querySelectorAll('[data-verdict="unsuitable"]').length;
      status.textContent = unsuitable
        ? `${unsuitable} view dimension${unsuitable === 1 ? "" : "s"} need${unsuitable === 1 ? "s" : ""} an installer action or a recorded test clip.`
        : "Every measured view dimension is suitable or needs review only.";
    } catch (problem) {
      status.textContent = `Could not load view health: ${problem.message}`;
    }
  }

  load();
})();
