/* Home: the ask box, the AI status card, the counts and the latest inspections. */
(() => {
  "use strict";
  const { api, icon, esc, inspectionRow } = window.AthenaApp;

  const hour = new Date().getHours();
  const greeting = document.getElementById("greeting");
  if (greeting) greeting.textContent = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  const input = document.getElementById("ask-input");
  document.querySelectorAll("[data-example]").forEach(chip => {
    chip.addEventListener("click", () => { input.value = chip.dataset.example; input.focus(); });
  });
  document.getElementById("ask-form").addEventListener("submit", event => {
    if (input.value.trim().length < 4) { event.preventDefault(); input.focus(); input.placeholder = "Type a short question first, for example: How many cars are parked here?"; }
  });

  window.AthenaApp.aiStatus().then(status => {
    const name = document.getElementById("ai-status-name");
    const line = document.getElementById("ai-status-line");
    if (!status) { name.textContent = "AI model"; line.textContent = "Could not check the AI model."; return; }
    if (!status.active.configured) {
      name.textContent = "Connect your AI to start";
      line.innerHTML = `<span class="dot" data-state="down"></span>Picture reading is not ready. Open for setup.`;
      return;
    }
    name.textContent = `${status.active.label} · ${status.active.model}`;
    line.innerHTML = status.reachable
      ? `<span class="dot" data-state="ok"></span>Connected at ${esc(status.active.host)}`
      : `<span class="dot" data-state="down"></span>Not answering at ${esc(status.active.host || "no address")} — open to fix`;
  });

  const recent = document.getElementById("recent");
  api("/api/inspections/summary").then(data => {
    document.getElementById("stat-week").textContent = data.this_week;
    document.getElementById("stat-attention").textContent = data.attention;
    document.getElementById("stat-critical").textContent = data.critical;
    if (!data.recent.length) {
      recent.innerHTML = `<div class="empty" data-testid="home-recent-empty">${icon("sparkle", 26)}<strong>No inspections yet</strong>
        <p>Pick a ready-made check above, or type a question. Your first report takes about a minute.</p>
        <a class="a-btn a-btn--primary" href="/inspect/new" data-testid="home-empty-start">${icon("plus", 18)}Start your first inspection</a></div>`;
      return;
    }
    recent.innerHTML = `<div class="insp-list">${data.recent.map(inspectionRow).join("")}</div>`;
  }).catch(error => {
    recent.innerHTML = `<div class="empty"><p>${esc(error.message)}</p></div>`;
  });

  const HOME_OUTCOMES = {
    vision: {
      status: "Available now in custom projects",
      title: "Object & Defect Detection",
      summary: "Label equipment, parts or surface defects in your pictures, freeze a training set, teach a detector on your hardware and publish a check screen.",
      data: "JPEG, PNG or WebP pictures + boxes",
      quality: "Found vs missed items and false alarms on held-out pictures",
      steps: "Choose a goal · Add pictures · Draw and review boxes · Teach · Check results · Use and improve",
      href: "/detectors?recipe=vision"
    },
    audio: {
      status: "Wave 1 roadmap (coming soon)",
      title: "Acoustic Event Detection",
      summary: "Upload recordings of a healthy motor and known faults. Athena learns to classify characteristic sounds or flag unusual sounds against a normal baseline.",
      data: "WAV recordings + event labels",
      quality: "Missed sound events and false alarms",
      steps: "Choose a goal · Add examples · Teach with labels · Train · Check on held-out examples · Use and improve",
      href: "/detectors?recipe=audio"
    },
    ocr: {
      status: "Wave 1 roadmap (coming soon)",
      title: "Handwriting & Form Reading",
      summary: "Upload handwritten forms or line crops, correct recognized text and fine-tune a reader for your handwriting style or document type.",
      data: "Handwritten forms or line pictures + corrected transcripts",
      quality: "Character and word errors on unseen pages",
      steps: "Choose a goal · Add forms · Correct transcripts · Train · Check errors · Use and improve",
      href: "/detectors?recipe=ocr"
    },
    documents: {
      status: "Wave 2 roadmap (coming soon)",
      title: "Document Understanding",
      summary: "Label categories, names or key fields across text documents so Athena learns to sort incoming records and extract structured answers.",
      data: "Text documents + category tags, names or fields",
      quality: "Wrongly grouped documents and missed fields",
      steps: "Choose a goal · Add documents · Label spans or tags · Train · Check errors · Use and improve",
      href: "/detectors?recipe=documents"
    },
    movements: {
      status: "Wave 3 roadmap (coming soon)",
      title: "Human Posture & Movement Recognition",
      summary: "Label approved postures and working movements. Athena finds body points with a starting model, then learns to classify movements for ergonomic review.",
      data: "Pictures or clips + body points and movement labels",
      quality: "Point accuracy and missed movements",
      steps: "Choose a goal · Add clips · Mark postures · Train · Check errors · Use and improve",
      href: "/detectors?recipe=movements"
    },
    spreadsheets: {
      status: "Wave 1 roadmap (coming soon)",
      title: "Spreadsheet Prediction",
      summary: "Upload an Excel or CSV table, pick the target column you want to predict, train a model and check it against unseen rows.",
      data: "Excel or CSV spreadsheets + chosen target column",
      quality: "Prediction errors on unseen rows",
      steps: "Choose a goal · Upload spreadsheet · Pick target column · Train · Check unseen rows · Use and improve",
      href: "/detectors?recipe=spreadsheets"
    }
  };

  const tablist = document.getElementById("home-outcome-tabs");
  const panel = document.getElementById("home-outcome-panel");
  if (tablist && panel) {
    const tabs = [...tablist.querySelectorAll("[data-outcome]")];
    const selectOutcome = (key, moveFocus = false) => {
      const data = HOME_OUTCOMES[key];
      if (!data) return;
      tabs.forEach(tab => {
        const active = tab.dataset.outcome === key;
        tab.setAttribute("aria-selected", active ? "true" : "false");
        tab.tabIndex = active ? 0 : -1;
        if (active) {
          panel.setAttribute("aria-labelledby", tab.id);
          if (typeof tab.scrollIntoView === "function") {
            tab.scrollIntoView({ block: "nearest", inline: "nearest" });
          }
          if (moveFocus) tab.focus();
        }
      });
      document.getElementById("home-outcome-status").textContent = data.status;
      document.getElementById("home-outcome-title").textContent = data.title;
      document.getElementById("home-outcome-summary").textContent = data.summary;
      document.getElementById("home-outcome-data").textContent = data.data;
      document.getElementById("home-outcome-quality").textContent = data.quality;
      document.getElementById("home-outcome-steps").textContent = data.steps;
      document.getElementById("home-outcome-cta").setAttribute("href", data.href);
    };

    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => selectOutcome(tab.dataset.outcome, false));
      tab.addEventListener("keydown", event => {
        let next = -1;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        if (next >= 0) {
          event.preventDefault();
          selectOutcome(tabs[next].dataset.outcome, true);
        }
      });
    });
  }
})();
