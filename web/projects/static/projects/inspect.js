/* New inspection: choose a check (or describe one), add photos or a video, and run it. */
(() => {
  "use strict";
  const { api, icon, esc, toast } = window.AthenaApp;
  const MAX_PICTURES = 6;
  const MAX_BYTES = 20 * 1024 * 1024;
  const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];
  const VIDEO_FRAMES = 6;

  const templates = JSON.parse(document.getElementById("athena-templates").textContent);
  const byKey = Object.fromEntries(templates.map(t => [t.key, t]));
  const form = document.getElementById("wiz");
  const grid = document.getElementById("pick-grid");
  const detail = document.getElementById("pick-detail");
  const question = document.getElementById("pick-question");
  const instructions = document.getElementById("instructions");
  const instructionsLabel = document.getElementById("instructions-label");
  const examples = document.getElementById("examples");
  const fileInput = document.getElementById("file-input");
  const drop = document.getElementById("drop");
  const thumbs = document.getElementById("thumbs");
  const videoStatus = document.getElementById("video-status");
  const errorBox = document.getElementById("wiz-error");
  const runButton = document.getElementById("run");

  const state = { template: "", source: "photo", pictures: [], regions: [], busy: false, submitting: false, aiMissing: false };

  // Step 1: the checks
  grid.innerHTML = templates.map(t => `
    <button class="pick${t.key === "custom" ? " pick--custom" : ""}${t.featured ? " pick--hero" : ""}" type="button" role="radio" aria-checked="false" data-key="${esc(t.key)}" data-testid="template-option-${esc(t.key)}">
      ${t.cover ? `<img class="pick-cover" src="/static/${esc(t.cover)}" alt="">` : `<span class="tpl-icon">${icon(t.icon, 20)}</span>`}
      <span class="pick-name">${esc(t.name)}</span>
      <span class="pick-line">${esc(t.blurb || t.tagline)}</span>
    </button>`).join("");

  function choose(key, focus = false) {
    state.template = key;
    grid.querySelectorAll(".pick").forEach(b => {
      const on = b.dataset.key === key;
      b.setAttribute("aria-checked", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    const t = byKey[key];
    detail.hidden = false;
    const custom = key === "custom";
    examples.hidden = !custom;
    question.hidden = custom;
    if (!custom) {
      const counts = t.counts.length ? ` You get: <strong>${t.counts.map(esc).join(" · ")}</strong>.` : "";
      question.innerHTML = `${icon("message", 18)}<span>Athena answers: <strong>“${esc(t.question)}”</strong>${counts} <span class="a-muted">Good pictures: ${esc(t.source_hint)}.</span></span>`;
    }
    instructionsLabel.innerHTML = custom ? "What do you want to know?" : 'Anything specific to look for? <span class="a-muted">(optional)</span>';
    instructions.placeholder = custom ? "e.g. Are any fire extinguishers missing from their brackets?" : "e.g. Focus on the flange bolts of the 6-inch line";
    document.getElementById("step-what").dataset.done = "true";
    update();
    if (state.regions.length) {
      state.regions.forEach((region, index) => { region.label = regionLabel(index + 1); });
    }
    renderMarks();
  }

  grid.addEventListener("click", event => {
    const pick = event.target.closest(".pick");
    if (pick) choose(pick.dataset.key);
  });
  grid.addEventListener("keydown", event => {
    const keys = ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const list = templates.map(t => t.key);
    const at = Math.max(0, list.indexOf(state.template));
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
    choose(list[(at + step + list.length) % list.length], true);
  });
  examples.addEventListener("click", event => {
    const chip = event.target.closest("[data-example]");
    if (chip) { instructions.value = chip.dataset.example; instructions.focus(); update(); }
  });
  instructions.addEventListener("input", update);

  const chosen = form.dataset.chosen || (instructions.value.trim() ? "custom" : "");
  if (chosen) choose(chosen);
  else grid.querySelector(".pick").tabIndex = 0;

  // Step 2: pictures
  document.querySelectorAll(".source-tab").forEach(tab => tab.addEventListener("click", () => setSource(tab.dataset.source)));

  function setSource(source) {
    if (state.busy) return;
    state.source = source;
    document.querySelectorAll(".source-tab").forEach(tab => tab.setAttribute("aria-selected", tab.dataset.source === source ? "true" : "false"));
    clearPictures();
    const video = source === "video";
    fileInput.accept = video ? "video/*" : IMAGE_TYPES.join(",");
    fileInput.multiple = !video;
    document.getElementById("drop-title").textContent = video ? "Drop a short video here, or click to choose" : "Drop photos here, or click to choose";
    document.getElementById("drop-hint").textContent = video ? `MP4, WebM or MOV · Athena takes ${VIDEO_FRAMES} still frames from it` : "JPEG, PNG or WebP · up to 6 · 20 MB each";
    update();
  }

  function clearPictures() {
    state.pictures.forEach(p => URL.revokeObjectURL(p.url));
    state.pictures = [];
    renderThumbs();
  }

  function addFiles(files) {
    errorBox.textContent = "";
    if (state.source === "video") { if (files[0]) takeFrames(files[0]); return; }
    const skipped = [];
    for (const file of files) {
      if (state.pictures.length >= MAX_PICTURES) { skipped.push(`${file.name} (6 pictures is the limit)`); continue; }
      if (!IMAGE_TYPES.includes(file.type)) { skipped.push(`${file.name} (not a JPEG, PNG or WebP picture)`); continue; }
      if (file.size > MAX_BYTES) { skipped.push(`${file.name} (larger than 20 MB)`); continue; }
      state.pictures.push({ file, url: URL.createObjectURL(file), name: file.name, frameTime: null });
    }
    if (skipped.length) toast(`Left out: ${skipped.join(", ")}`, "bad");
    renderThumbs();
    update();
  }

  function renderThumbs() {
    thumbs.innerHTML = state.pictures.map((p, i) => `
      <figure class="thumb" data-testid="inspection-thumbnail-${i + 1}"><img src="${p.url}" alt="${esc(p.name)}">
        ${p.frameTime !== null ? `<span class="thumb-tag">${p.frameTime.toFixed(1)}s</span>` : `<span class="thumb-tag">${i + 1}</span>`}
        <button class="thumb-x" type="button" data-remove="${i}" aria-label="Remove ${esc(p.name)}" data-testid="remove-picture-${i + 1}">${icon("x", 16)}</button></figure>`).join("");
    document.getElementById("step-pics").dataset.done = state.pictures.length ? "true" : "false";
    renderMarks();
  }

  function regionLabel(index) {
    return state.template === "parking" ? `Bay ${index}` : String(index);
  }

  function renderMarks() {
    const stage = document.getElementById("mark-stage");
    const help = document.getElementById("mark-help");
    const img = document.getElementById("mark-img");
    if (!state.pictures.length) {
      stage.hidden = true;
      state.regions = [];
      return;
    }
    stage.hidden = false;
    if (!stage.dataset.seen) {
      stage.dataset.seen = "1";
      stage.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    help.textContent = state.template === "parking"
      ? "Optional. Draw a box around each parking bay so Athena can count which are free and which are taken. Click and drag on the picture."
      : "Optional. Draw a box around each place Athena should check — a bay, a pipe, a shelf. Click and drag on the picture. Skip this if the whole picture should be checked.";
    img.src = state.pictures[0].url;
    const boxes = document.getElementById("mark-boxes");
    boxes.replaceChildren(...state.regions.map((region, index) => {
      const el = document.createElement("span");
      el.className = "mark-box";
      el.style.left = `${region.x * 100}%`;
      el.style.top = `${region.y * 100}%`;
      el.style.width = `${region.w * 100}%`;
      el.style.height = `${region.h * 100}%`;
      const tag = document.createElement("b");
      tag.textContent = region.label;
      el.append(tag);
      el.dataset.index = String(index);
      return el;
    }));
    const list = document.getElementById("mark-list");
    list.innerHTML = state.regions.map((region, index) => `
      <li><span>${esc(region.label)}</span>
        <button type="button" data-unmark="${index}" aria-label="Remove ${esc(region.label)}" data-testid="remove-mark-${index + 1}">${icon("x", 14)}</button></li>`).join("");
    document.getElementById("mark-count").textContent = state.regions.length
      ? `${state.regions.length} marked area${state.regions.length === 1 ? "" : "s"}`
      : "No areas marked yet.";
  }

  document.getElementById("mark-list").addEventListener("click", event => {
    const button = event.target.closest("[data-unmark]");
    if (!button) return;
    state.regions.splice(Number(button.dataset.unmark), 1);
    state.regions.forEach((region, index) => { region.label = regionLabel(index + 1); });
    renderMarks();
  });

  (function markDraw() {
    const frame = document.getElementById("mark-frame");
    const img = document.getElementById("mark-img");
    let drag = null;
    function point(event) {
      const box = img.getBoundingClientRect();
      if (box.width < 8 || box.height < 8) return null;
      return {
        x: Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)),
        y: Math.min(1, Math.max(0, (event.clientY - box.top) / box.height)),
      };
    }
    frame.addEventListener("pointerdown", event => {
      if (event.button !== 0 || state.regions.length >= 24) return;
      const start = point(event);
      if (!start) return;
      drag = { ...start };
      frame.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    frame.addEventListener("pointerup", event => {
      if (!drag) return;
      const end = point(event) || drag;
      const x = Math.min(drag.x, end.x);
      const y = Math.min(drag.y, end.y);
      const w = Math.abs(end.x - drag.x);
      const h = Math.abs(end.y - drag.y);
      drag = null;
      if (w < 0.02 || h < 0.02) return;
      state.regions.push({ x, y, w, h, label: regionLabel(state.regions.length + 1), image: 1 });
      renderMarks();
    });
  })();

  thumbs.addEventListener("click", event => {
    const button = event.target.closest("[data-remove]");
    if (!button) return;
    const index = Number(button.dataset.remove);
    const [removed] = state.pictures.splice(index, 1);
    if (index === 0 && state.regions.length) {
      state.regions = [];
      toast("Marked areas were cleared because their picture was removed.");
    }
    URL.revokeObjectURL(removed.url);
    renderThumbs();
    update();
  });
  fileInput.addEventListener("change", () => { addFiles(Array.from(fileInput.files)); fileInput.value = ""; });
  ["dragenter", "dragover"].forEach(type => drop.addEventListener(type, event => { event.preventDefault(); drop.dataset.over = "true"; }));
  ["dragleave", "drop"].forEach(type => drop.addEventListener(type, () => { drop.dataset.over = "false"; }));
  drop.addEventListener("drop", event => { event.preventDefault(); addFiles(Array.from(event.dataTransfer.files)); });

  function seek(video, time) {
    return new Promise((resolve, reject) => {
      const done = () => { video.removeEventListener("seeked", done); resolve(); };
      video.addEventListener("seeked", done);
      video.addEventListener("error", reject, { once: true });
      video.currentTime = time;
    });
  }

  async function takeFrames(file) {
    clearPictures();
    state.busy = true;
    videoStatus.hidden = false;
    videoStatus.textContent = "Taking still pictures from your video…";
    update();
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    video.playsInline = true;
    video.src = url;
    try {
      await new Promise((resolve, reject) => { video.onloadeddata = resolve; video.onerror = () => reject(new Error("This browser cannot open that video. Try an MP4 file, or add photos instead.")); });
      const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 1;
      const scale = Math.min(1, 1280 / Math.max(video.videoWidth, video.videoHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const context = canvas.getContext("2d");
      for (let i = 0; i < VIDEO_FRAMES; i += 1) {
        const time = Math.min(duration - 0.05, (duration * (i + 0.5)) / VIDEO_FRAMES);
        await seek(video, Math.max(0, time));
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.88));
        const name = `frame-${String(i + 1).padStart(2, "0")}.jpg`;
        const frame = new File([blob], name, { type: "image/jpeg" });
        state.pictures.push({ file: frame, url: URL.createObjectURL(frame), name, frameTime: time });
        videoStatus.textContent = `Taking still pictures from your video… ${i + 1} of ${VIDEO_FRAMES}`;
        renderThumbs();
      }
      videoStatus.textContent = `Took ${VIDEO_FRAMES} still pictures from “${file.name}” (${duration.toFixed(1)} s).`;
    } catch (error) {
      clearPictures();
      videoStatus.textContent = "";
      videoStatus.hidden = true;
      errorBox.textContent = error.message || "That video could not be read. Try another file, or add photos.";
    } finally {
      URL.revokeObjectURL(url);
      state.busy = false;
      update();
    }
  }

  // AI status is refreshed when returning from settings without discarding the in-memory inspection.
  let statusRequest = 0;
  function showAIStatus(status) {
    const text = document.getElementById("sum-ai-text");
    const row = document.getElementById("sum-ai");
    if (!status || !status.active) { row.dataset.ok = "false"; text.textContent = "AI model status unknown"; return; }
    state.aiMissing = !status.active.configured;
    document.getElementById("connect-first").hidden = !state.aiMissing;
    if (!runButton.hasAttribute("aria-disabled")) runButton.disabled = state.aiMissing || state.submitting;
    if (state.aiMissing) {
      row.dataset.ok = "false";
      text.textContent = "No AI model connected yet";
      document.getElementById("sum-ai-sub").innerHTML = `<span class="dot" data-state="down"></span><a href="/settings/ai" target="_blank" rel="noopener" data-testid="summary-connect-ai-link">Connect your AI first (opens a new tab)</a>`;
      return;
    }
    text.textContent = `${status.active.label} · ${status.active.model}`;
    row.dataset.ok = status.reachable ? "true" : "false";
    document.getElementById("sum-ai-sub").innerHTML = status.reachable
      ? `<span class="dot" data-state="ok"></span>Answering · <a href="/settings/ai" target="_blank" rel="noopener">change (opens a new tab)</a>`
      : `<span class="dot" data-state="down"></span>Not answering right now · <a href="/settings/ai" target="_blank" rel="noopener" data-testid="summary-fix-ai-link">connect an AI (opens a new tab)</a>`;
  }
  async function refreshAIStatus(initial = false) {
    const request = ++statusRequest;
    const status = initial ? await window.AthenaApp.aiStatus() : await window.AthenaApp.refreshAIStatus();
    if (request === statusRequest) showAIStatus(status);
  }
  refreshAIStatus(true);
  window.addEventListener("focus", () => refreshAIStatus());
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshAIStatus(); });

  function problem() {
    if (state.aiMissing) return "Connect an AI model first (see the note at the top of the page).";
    if (!state.template) return "Choose what Athena should check (step 1).";
    if (state.template === "custom" && instructions.value.trim().length < 4) return "Describe what you want to know (step 1).";
    if (state.busy) return "Wait a moment while Athena reads your video.";
    if (!state.pictures.length) return "Add at least one picture (step 2).";
    return "";
  }

  function update() {
    const t = byKey[state.template];
    const whatOk = Boolean(t) && !(state.template === "custom" && instructions.value.trim().length < 4);
    document.getElementById("sum-what").dataset.ok = whatOk ? "true" : "false";
    document.getElementById("sum-what-text").textContent = t ? t.name : "Choose what to check";
    document.getElementById("sum-what-sub").textContent = t
      ? (state.template === "custom" ? (instructions.value.trim() || "Describe what you want to know") : t.question)
      : "Step 1";
    const n = state.pictures.length;
    document.getElementById("sum-pics").dataset.ok = n ? "true" : "false";
    document.getElementById("sum-pics-text").textContent = n
      ? (state.source === "video" ? `${n} frames from a video` : `${n} picture${n === 1 ? "" : "s"}`)
      : "No pictures yet";
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (state.submitting) return;
    const reason = problem();
    errorBox.textContent = reason;
    if (reason) return;
    const data = new FormData();
    data.append("template", state.template);
    data.append("instructions", instructions.value.trim());
    data.append("title", document.getElementById("title").value.trim());
    data.append("source", state.source);
    data.append("frame_times", JSON.stringify(state.pictures.map(p => p.frameTime)));
    data.append("regions", JSON.stringify(state.regions));
    state.pictures.forEach(p => data.append("images", p.file, p.name));
    state.submitting = true;
    runButton.disabled = true;
    runButton.innerHTML = `${icon("loader", 18, "a-spin")}Sending your pictures…`;
    try {
      const response = await api("/api/inspections", { method: "POST", form: data });
      location.href = response.inspection.url;
    } catch (error) {
      errorBox.textContent = error.message;
      state.submitting = false;
      if (!runButton.hasAttribute("aria-disabled")) runButton.disabled = state.aiMissing;
      runButton.innerHTML = `${icon("zap", 18)}Run inspection`;
    }
  });

  update();
})();
