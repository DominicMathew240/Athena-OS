/* Inspection report: waits for the answer, then shows the verdict, counts, findings with marked areas, and next steps. */
(() => {
  "use strict";
  const { api, icon, esc, toast, timeAgo, VERDICTS } = window.AthenaApp;
  const root = document.getElementById("report");
  const id = root.dataset.id;
  const $ = selector => document.getElementById(selector);
  const SEV_WORD = { info: "Note", low: "Low", medium: "Medium", high: "High", critical: "Critical" };
  const STEPS = ["Preparing your pictures", "Sending them to the AI model", "Reading the scene", "Writing your report"];

  let current = null;
  let picture = 1;
  let pollTimer = null;
  let stepTimer = null;
  let activeFinding = null;

  function setSub(item) {
    const bits = [`${icon(item.template_icon || "sparkle", 15)} ${esc(item.template_name)}`, `${icon("clock", 15)} ${esc(timeAgo(item.created_at))}`];
    if (item.image_count) bits.push(`${icon(item.source === "video" ? "video" : "image", 15)} ${item.image_count} ${item.source === "video" ? "frames" : item.image_count === 1 ? "picture" : "pictures"}`);
    $("rep-sub").innerHTML = bits.map(b => `<span class="a-row">${b}</span>`).join("");
  }

  function renderStage() {
    const item = current;
    const image = item.images.find(img => img.position === picture) || item.images[0];
    if (!image) return;
    const img = $("stage-img");
    if (img.getAttribute("src") !== image.url) img.src = image.url;
    img.alt = `Picture ${image.position} of this inspection`;
    $("stage-caption").textContent = `Picture ${image.position} of ${item.images.length}${image.frame_time !== null && image.frame_time !== undefined ? ` · ${image.frame_time.toFixed(1)} s into the video` : ""}`;
    const frame = $("stage-frame");
    frame.querySelectorAll(".ov, .scanline, .stage-scrim").forEach(node => node.remove());
    if (item.status === "queued" || item.status === "running") {
      frame.insertAdjacentHTML("beforeend", '<span class="stage-scrim"></span><span class="scanline"></span>');
    }
    const findings = (item.result && item.result.findings) || [];
    if ($("show-boxes").checked) {
      findings.forEach((finding, index) => {
        if (!finding.box || finding.image !== image.position) return;
        const box = document.createElement("button");
        box.type = "button";
        box.className = "ov";
        box.dataset.sev = finding.severity;
        box.dataset.index = String(index);
        box.setAttribute("data-testid", `report-overlay-box-${index + 1}`);
        box.setAttribute("aria-label", `Marked area ${index + 1}: ${finding.title}`);
        const [x, y, w, h] = finding.box;
        box.style.left = `${x * 100}%`;
        box.style.top = `${y * 100}%`;
        box.style.width = `${w * 100}%`;
        box.style.height = `${h * 100}%`;
        box.innerHTML = `<span class="ov-tag">${index + 1}</span>`;
        box.addEventListener("click", () => highlight(index, true));
        frame.append(box);
      });
    }
    const thumbs = $("stage-thumbs");
    thumbs.hidden = item.images.length < 2;
    thumbs.innerHTML = item.images.map(img => `<button class="stage-thumb" type="button" data-position="${img.position}" aria-current="${img.position === image.position}" aria-label="Show picture ${img.position}" data-testid="report-thumb-${img.position}"><img src="${esc(img.url)}" alt=""></button>`).join("");
    highlight(activeFinding, false);
  }

  $("stage-thumbs").addEventListener("click", event => {
    const button = event.target.closest("[data-position]");
    if (button) { picture = Number(button.dataset.position); renderStage(); }
  });
  $("show-boxes").addEventListener("change", renderStage);

  function highlight(index, scroll) {
    activeFinding = index;
    const stage = $("stage");
    stage.dataset.dim = index === null ? "false" : "true";
    document.querySelectorAll(".ov").forEach(box => { box.dataset.active = box.dataset.index === String(index) ? "true" : "false"; });
    document.querySelectorAll(".finding").forEach(row => { row.dataset.active = row.dataset.index === String(index) ? "true" : "false"; });
    if (scroll && index !== null) {
      const row = document.querySelector(`.finding[data-index="${index}"]`);
      if (row) row.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function renderRunning() {
    $("state-running").hidden = false;
    $("state-failed").hidden = true;
    $("state-done").hidden = true;
    const started = new Date(current.started_at || current.created_at).getTime();
    const draw = () => {
      const seconds = (Date.now() - started) / 1000;
      const at = seconds < 2 ? 0 : seconds < 6 ? 1 : seconds < 25 ? 2 : 3;
      $("running-steps").innerHTML = STEPS.map((label, i) => {
        const state = i < at ? "done" : i === at ? "now" : "todo";
        const name = state === "done" ? "check" : state === "now" ? "loader" : "clock";
        return `<li data-state="${state}">${icon(name, 18)}${esc(i === 1 && current.model_label ? `Sending them to ${current.model_label}` : label)}</li>`;
      }).join("");
      if (seconds > 90) $("running-help").textContent = "This is taking longer than usual. A slow or busy AI server can take a few minutes; Athena gives up after the time your administrator set.";
    };
    draw();
    clearInterval(stepTimer);
    stepTimer = setInterval(draw, 1000);
  }

  function renderFailed() {
    clearInterval(stepTimer);
    $("state-running").hidden = true;
    $("state-done").hidden = true;
    $("state-failed").hidden = false;
    $("failed-message").textContent = current.error || "Something went wrong.";
  }

  function renderDone() {
    clearInterval(stepTimer);
    const r = current.result || {};
    const info = VERDICTS[r.verdict] || VERDICTS.clear;
    $("state-running").hidden = true;
    $("state-failed").hidden = true;
    $("state-done").hidden = false;
    $("verdict").dataset.verdict = r.verdict;
    $("verdict-word").innerHTML = `${icon(info.icon, 18)}${esc(info.word)}`;
    $("verdict-template").textContent = current.template_name;
    $("verdict-headline").textContent = r.headline || "";
    $("verdict-summary").textContent = r.summary || "";
    $("verdict-summary").hidden = !r.summary;

    const counts = r.counts || [];
    $("counts-block").hidden = !counts.length;
    $("counts").innerHTML = counts.map((c, i) => `<div class="count" data-testid="report-count-${i + 1}"><span class="count-value">${esc(c.value)}</span><span class="count-label">${esc(c.label)}</span>${c.note ? `<span class="count-note">${esc(c.note)}</span>` : ""}</div>`).join("");

    const findings = r.findings || [];
    $("findings-count").textContent = findings.length ? `${findings.length} item${findings.length === 1 ? "" : "s"}` : "";
    $("findings").innerHTML = findings.length ? findings.map((f, i) => {
      const confidence = f.confidence === null || f.confidence === undefined ? "" :
        `<span class="conf" title="How sure the AI is">Sure: ${Math.round(f.confidence * 100)}%<span class="conf-bar"><i data-w="${Math.round(f.confidence * 100)}"></i></span></span>`;
      const where = current.images.length > 1 ? `<span>Picture ${f.image}</span>` : "";
      const marked = f.box ? `<span>${icon("eye", 14)} Marked on the picture</span>` : "";
      return `<li class="finding" data-index="${i}" data-sev="${esc(f.severity)}" data-has-box="${f.box ? "true" : "false"}" data-testid="report-finding-${i + 1}" ${f.box ? 'tabindex="0" role="button"' : ""}>
        <span class="finding-num">${i + 1}</span>
        <div><div class="finding-top"><span class="finding-title">${esc(f.title)}</span><span class="sev" data-sev="${esc(f.severity)}">${esc(SEV_WORD[f.severity] || f.severity)}</span></div>
        ${f.detail ? `<p>${esc(f.detail)}</p>` : ""}
        <div class="finding-meta">${confidence}${where}${marked}</div></div></li>`;
    }).join("") : `<li class="a-note a-note--ok">${icon("check", 20)}<p>Nothing that needs your attention was found.</p></li>`;
    document.querySelectorAll(".conf-bar i").forEach(bar => { bar.style.width = `${bar.dataset.w}%`; });
    document.querySelectorAll(".finding[data-has-box='true']").forEach(row => {
      const go = () => {
        const finding = findings[Number(row.dataset.index)];
        if (finding.image !== picture) { picture = finding.image; renderStage(); }
        highlight(Number(row.dataset.index), false);
      };
      row.addEventListener("click", go);
      row.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); go(); } });
      row.addEventListener("mouseenter", () => highlight(Number(row.dataset.index), false));
    });

    const actions = r.actions || [];
    $("actions-block").hidden = !actions.length;
    $("actions").innerHTML = actions.map(a => `<li>${esc(a)}</li>`).join("");

    const context = [];
    if (current.question) context.push(["The question", current.question]);
    if (current.instructions && current.template !== "custom") context.push(["Your extra instructions", current.instructions]);
    if (r.scene) context.push(["What Athena saw", r.scene]);
    if (r.limits) context.push(["What it could not be sure about", r.limits]);
    $("context").innerHTML = context.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");

    document.querySelectorAll("[data-rating]").forEach(b => b.setAttribute("aria-pressed", b.dataset.rating === current.feedback ? "true" : "false"));
    $("feedback-help").textContent = current.feedback ? "Thanks — your answer is saved with this report." : "Your answer is saved with the report so your team knows how far to trust it.";

    const seconds = current.duration_ms ? `${(current.duration_ms / 1000).toFixed(1)} s` : "–";
    const when = current.finished_at ? new Date(current.finished_at).toLocaleString() : "–";
    $("meta").innerHTML = [["AI model", current.model_label || "–"], ["Answered by", current.endpoint_host || "–"], ["Time taken", seconds], ["Finished", when]]
      .map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
  }

  function render() {
    const item = current;
    document.title = `${item.title} · Athena OS`;
    $("rep-title").textContent = item.title;
    $("crumb-title").textContent = item.title;
    setSub(item);
    const busy = item.status === "queued" || item.status === "running";
    $("rep-rerun").hidden = busy;
    $("rep-print").hidden = item.status !== "done";
    if (busy) renderRunning();
    else if (item.status === "failed") renderFailed();
    else renderDone();
    renderStage();
  }

  async function load() {
    clearTimeout(pollTimer);
    try {
      const data = await api(`/api/inspections/${id}`);
      current = data.inspection;
      render();
      if (current.status === "queued" || current.status === "running") pollTimer = setTimeout(load, 2000);
    } catch (error) {
      $("rep-title").textContent = "This report could not be opened";
      toast(error.message, "bad");
      pollTimer = setTimeout(load, 5000);
    }
  }

  async function rerun() {
    try {
      const data = await api(`/api/inspections/${id}/rerun`, { method: "POST" });
      current = data.inspection;
      activeFinding = null;
      render();
      pollTimer = setTimeout(load, 1500);
      toast("Running the inspection again.");
    } catch (error) { toast(error.message, "bad"); }
  }
  $("rep-rerun").addEventListener("click", rerun);
  $("failed-retry").addEventListener("click", rerun);
  $("rep-print").addEventListener("click", () => window.print());
  $("rep-delete").addEventListener("click", async () => {
    const ok = await window.Athena.askConfirm({
      title: "Delete this inspection?",
      message: "The report and its pictures are removed from Athena. This cannot be undone.",
      keepLabel: "Keep it",
      confirmLabel: "Delete inspection"
    });
    if (!ok) return;
    try {
      await api(`/api/inspections/${id}`, { method: "DELETE" });
      location.href = "/inspections";
    } catch (error) { toast(error.message, "bad"); }
  });
  document.querySelectorAll("[data-rating]").forEach(button => button.addEventListener("click", async () => {
    const rating = button.getAttribute("aria-pressed") === "true" ? "" : button.dataset.rating;
    try {
      const data = await api(`/api/inspections/${id}/feedback`, { method: "POST", json: { rating } });
      current = data.inspection;
      renderDone();
      if (rating) toast("Thanks, saved with the report.");
    } catch (error) { toast(error.message, "bad"); }
  }));
  document.addEventListener("keydown", event => { if (event.key === "Escape") highlight(null, false); });

  load();
})();
