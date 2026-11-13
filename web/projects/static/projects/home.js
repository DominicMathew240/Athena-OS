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
      line.innerHTML = `<span class="dot" data-state="down"></span>Add your Qwen server or another OpenAI-compatible AI — open to set up`;
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
})();
