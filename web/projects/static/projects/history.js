/* Inspections history: filter by result and type, search, open a report. */
(() => {
  "use strict";
  const { api, icon, esc, inspectionRow } = window.AthenaApp;
  const templates = JSON.parse(document.getElementById("athena-templates").textContent);
  const list = document.getElementById("history-list");
  const select = document.getElementById("template-filter");
  const search = document.getElementById("history-search");
  const filters = { verdict: "", template: "", q: "" };
  let timer = null;
  let request = 0;

  select.insertAdjacentHTML("beforeend", templates.map(t => `<option value="${esc(t.key)}">${esc(t.name)}</option>`).join(""));

  async function load() {
    const mine = ++request;
    const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
    try {
      const data = await api(`/api/inspections?${params}`);
      if (mine !== request) return;
      if (!data.inspections.length) {
        const filtered = filters.verdict || filters.template || filters.q;
        list.innerHTML = `<div class="empty" data-testid="history-empty">${icon(filtered ? "search" : "sparkle", 26)}
          <strong>${filtered ? "Nothing matches these filters" : "No inspections yet"}</strong>
          <p>${filtered ? "Try another filter or clear the search." : "Your reports will appear here. Start with a ready-made check or your own question."}</p>
          ${filtered ? "" : `<a class="a-btn a-btn--primary" href="/inspect/new">${icon("plus", 18)}Start an inspection</a>`}</div>`;
        return;
      }
      list.innerHTML = data.inspections.map(inspectionRow).join("");
      if (data.inspections.some(item => item.status === "queued" || item.status === "running")) setTimeout(() => { if (mine === request) load(); }, 4000);
    } catch (error) {
      list.innerHTML = `<div class="empty"><p>${esc(error.message)}</p></div>`;
    }
  }

  document.getElementById("verdict-filter").addEventListener("click", event => {
    const button = event.target.closest("[data-verdict]");
    if (!button) return;
    filters.verdict = button.dataset.verdict;
    document.querySelectorAll("#verdict-filter [data-verdict]").forEach(b => b.setAttribute("aria-pressed", b === button ? "true" : "false"));
    load();
  });
  select.addEventListener("change", () => { filters.template = select.value; load(); });
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { filters.q = search.value.trim(); load(); }, 250); });

  load();
})();
