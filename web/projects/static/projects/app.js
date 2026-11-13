/* Athena OS app shell: theme switch, mobile menu, AI status dot, and small helpers the pages share (AthenaApp). */
(() => {
  "use strict";

  const ICON_PATHS = (() => {
    const node = document.getElementById("athena-icons");
    try { return node ? JSON.parse(node.textContent) : {}; } catch (error) { return {}; }
  })();

  function icon(name, size = 20, extra = "") {
    const paths = ICON_PATHS[name] || ICON_PATHS.info || "";
    return `<svg class="ico ${extra}" xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${paths}</svg>`;
  }

  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  function csrf() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  async function api(path, { method = "GET", json, form } = {}) {
    const headers = { Accept: "application/json" };
    let body;
    if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
    if (form) body = form;
    if (method !== "GET") headers["X-CSRFToken"] = csrf();
    let response;
    try {
      response = await fetch(path, { method, headers, body, credentials: "same-origin" });
    } catch (error) {
      throw new Error("Athena could not be reached. Check your connection and try again.");
    }
    let data = {};
    try { data = await response.json(); } catch (error) { data = {}; }
    if (response.status === 401) { location.href = `/login/?next=${encodeURIComponent(location.pathname + location.search)}`; throw new Error("Sign in to continue."); }
    if (!response.ok) throw new Error(data.error || `Something went wrong (${response.status}).`);
    return data;
  }

  let toastTimer = null;
  function toast(message, tone = "ok") {
    let node = document.getElementById("a-toast");
    if (!node) {
      node = document.createElement("div");
      node.id = "a-toast";
      node.className = "a-toast";
      node.setAttribute("role", "status");
      node.setAttribute("data-testid", "toast-message");
      document.body.append(node);
    }
    node.dataset.tone = tone;
    node.innerHTML = `${icon(tone === "bad" ? "alert" : "check", 18)}<span>${esc(message)}</span>`;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 4200);
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (seconds < 45) return "just now";
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours} h ago`;
    const days = Math.round(hours / 24);
    if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
    return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  const VERDICTS = {
    clear: { word: "All clear", chip: "clear", icon: "check" },
    attention: { word: "Needs attention", chip: "attention", icon: "alert" },
    critical: { word: "Critical", chip: "critical", icon: "octagon" }
  };

  function statusChip(item) {
    if (item.status === "queued" || item.status === "running") return `<span class="a-chip a-chip--running" data-testid="status-chip">Analysing</span>`;
    if (item.status === "failed") return `<span class="a-chip a-chip--failed" data-testid="status-chip">Did not finish</span>`;
    const info = VERDICTS[item.verdict] || { word: "Done", chip: "" };
    return `<span class="a-chip a-chip--${info.chip}" data-testid="status-chip">${info.word}</span>`;
  }

  function inspectionRow(item) {
    const thumb = item.thumbnail_url ? `<img class="insp-thumb" src="${esc(item.thumbnail_url)}" alt="" loading="lazy">` : `<span class="insp-thumb"></span>`;
    const sub = item.status === "done" ? item.headline : item.status === "failed" ? "Did not finish. Open it to see why and try again." : "Athena is looking at the pictures…";
    return `<a class="insp-row" href="${esc(item.url)}" data-testid="inspection-row-${esc(item.id)}">${thumb}
      <span class="insp-main"><span class="insp-title">${icon(item.template_icon || "sparkle", 16)}<span>${esc(item.title)}</span></span><span class="insp-sub">${esc(sub)}</span></span>
      ${statusChip(item)}<span class="insp-when">${esc(timeAgo(item.created_at))}</span></a>`;
  }

  // Theme switch
  function syncThemeButtons() {
    const dark = document.documentElement.dataset.theme === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach(button => {
      button.setAttribute("aria-pressed", dark ? "true" : "false");
      const label = button.querySelector("[data-theme-label]");
      if (label) label.textContent = dark ? "Control-room mode: on" : "Control-room mode";
    });
  }
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-theme-toggle]");
    if (!button) return;
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("athena-theme", next); } catch (error) { /* private mode */ }
    syncThemeButtons();
  });
  syncThemeButtons();

  // Mobile menu
  const sidebar = document.getElementById("sidebar");
  const menu = document.getElementById("sb-menu");
  if (sidebar && menu) {
    menu.addEventListener("click", () => {
      const open = sidebar.dataset.open !== "true";
      sidebar.dataset.open = open ? "true" : "false";
      menu.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && sidebar.dataset.open === "true") { sidebar.dataset.open = "false"; menu.setAttribute("aria-expanded", "false"); menu.focus(); }
    });
  }

  // AI status: one request, shared with the page through a promise.
  const statusPromise = document.querySelector("[data-ai-dot], [data-ai-status]")
    ? api("/api/vision/status").catch(() => null)
    : Promise.resolve(null);
  statusPromise.then(status => {
    document.querySelectorAll("[data-ai-dot]").forEach(dot => {
      if (!status) return;
      dot.dataset.state = status.reachable ? "ok" : "down";
      dot.title = status.reachable ? `${status.active.label} is answering` : `${status.active.label} is not answering right now`;
    });
  });

  window.AthenaApp = Object.freeze({ api, icon, esc, toast, timeAgo, VERDICTS, statusChip, inspectionRow, aiStatus: () => statusPromise });
})();
