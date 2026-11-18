/* Environment preflight (T08): asks the server which parts an operation depends
   on are actually ready, and shows who fixes each one that is not. Read-only;
   nothing here queues or starts anything. */
(() => {
  "use strict";
  const { api } = window.AthenaApp;
  const section = document.getElementById("preflight-section");
  if (!section) return;

  const list = document.getElementById("preflight-list");
  const status = document.getElementById("preflight-status");
  const refresh = document.getElementById("preflight-refresh");

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    if (props) for (const [key, value] of Object.entries(props)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value);
    }
    for (const child of children) if (child !== null && child !== undefined) node.append(child);
    return node;
  }

  function render(checks) {
    list.replaceChildren();
    for (const check of checks) {
      const body = el("div", { class: "op-row-main" },
        el("strong", { text: check.title }),
        el("span", { class: "a-help", text: check.detail }));
      if (!check.ok) {
        body.append(el("span", {
          class: "a-help",
          text: `Ask the ${check.owner} to fix this. ${check.recovery}`
        }));
      }
      list.append(el("li", { class: "op-row", "data-testid": `preflight-${check.id}`, "data-ok": check.ok ? "1" : "0" },
        body,
        el("span", { class: `a-chip ${check.ok ? "a-chip--clear" : "a-chip--critical"}`, text: check.ok ? "Ready" : "Blocked" })));
    }
  }

  async function load() {
    status.textContent = "Checking this server…";
    refresh.disabled = true;
    try {
      const result = await api("/api/operation-preflight");
      render(result.checks);
      const blocked = result.checks.filter(c => !c.ok).length;
      status.textContent = blocked
        ? `${blocked} part${blocked === 1 ? "" : "s"} of this server need${blocked === 1 ? "s" : ""} attention before an operation can run.`
        : "Every part of this server is ready.";
    } catch (problem) {
      status.textContent = `Could not check this server: ${problem.message}`;
    } finally {
      refresh.disabled = false;
    }
  }

  refresh.addEventListener("click", load);
  load();
})();
