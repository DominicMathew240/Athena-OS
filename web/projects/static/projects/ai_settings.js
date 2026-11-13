/* AI model page: choose the built-in Qwen model or connect your own OpenAI-compatible endpoint. */
(() => {
  "use strict";
  const { api, icon, esc, toast } = window.AthenaApp;
  const $ = id => document.getElementById(id);
  const PRESETS = [
    { name: "OpenAI", url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
    { name: "Google Gemini", url: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-2.5-flash" },
    { name: "OpenRouter", url: "https://openrouter.ai/api/v1", model: "qwen/qwen2.5-vl-72b-instruct" },
    { name: "Alibaba Qwen", url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", model: "qwen-vl-max" },
    { name: "Ollama", url: "http://localhost:11434/v1", model: "qwen2.5vl:7b" },
    { name: "vLLM", url: "http://localhost:8000/v1", model: "Qwen/Qwen2.5-VL-7B-Instruct" },
    { name: "LM Studio", url: "http://localhost:1234/v1", model: "qwen2.5-vl-7b-instruct" }
  ];
  let state = null;
  let choice = "builtin";

  $("presets").innerHTML = PRESETS.map((p, i) => `<button class="preset" type="button" data-preset="${i}" aria-pressed="false" data-testid="ai-preset-${esc(p.name.toLowerCase().replace(/\s+/g, "-"))}">${esc(p.name)}</button>`).join("");
  $("presets").addEventListener("click", event => {
    const button = event.target.closest("[data-preset]");
    if (!button) return;
    const preset = PRESETS[Number(button.dataset.preset)];
    $("base-url").value = preset.url;
    $("model-name").value = preset.model;
    if (!$("display-name").value) $("display-name").value = preset.name;
    document.querySelectorAll(".preset").forEach(b => b.setAttribute("aria-pressed", b === button ? "true" : "false"));
    $("own-result").innerHTML = "";
  });

  function result(target, data) {
    target.innerHTML = `<div class="test-result" data-ok="${data.ok ? "true" : "false"}" data-testid="ai-test-result">${icon(data.ok ? "check" : "alert", 20)}<span>${esc(data.message)}${data.ok && data.latency_ms ? ` <span class="a-muted">(${(data.latency_ms / 1000).toFixed(1)} s)</span>` : ""}</span></div>`;
  }

  function setChoice(next) {
    choice = next;
    document.querySelectorAll("[data-choice]").forEach(b => b.setAttribute("aria-checked", b.dataset.choice === next ? "true" : "false"));
    $("own-form").hidden = next !== "own";
    $("builtin-actions").hidden = next !== "builtin";
    $("builtin-result").hidden = next !== "builtin";
  }

  function render(data) {
    state = data;
    const own = data.own;
    $("base-url").value = own.base_url || "";
    $("model-name").value = own.model || "";
    $("display-name").value = own.label || "";
    $("api-key").value = "";
    $("api-key").placeholder = own.has_key ? `Saved key ending in ${own.key_hint || "••••"} — type to replace` : "sk-…";
    $("key-help").textContent = own.key_unreadable
      ? "Your saved key can no longer be read (the server's secret changed). Enter it again."
      : own.has_key ? "A key is saved. Leave this empty to keep it." : "Leave empty for servers that need no key. Your key is stored encrypted on this Athena server and is never shown again.";
    $("forget-own").hidden = !(own.base_url || own.has_key);
    $("own-meta").innerHTML = own.base_url
      ? `<span class="a-tag">${esc(own.model || "no model")}</span><span class="a-tag">${esc(new URL(own.base_url).host)}</span>`
      : `<span class="a-tag">Bring your own key</span>`;
    const active = data.active;
    $("active-text").innerHTML = `Your next inspection will use <strong data-testid="ai-active-model">${esc(active.label)} · ${esc(active.model || "no model")}</strong>${active.host ? ` at <span class="a-mono">${esc(active.host)}</span>` : ""}.`;
    setChoice(own.use_own ? "own" : "builtin");
  }

  document.querySelectorAll("[data-choice]").forEach(button => button.addEventListener("click", async () => {
    const next = button.dataset.choice;
    setChoice(next);
    if (next === "builtin" && state && state.own.use_own) {
      try {
        render(await api("/api/vision/connection", { method: "PUT", json: { use_own: false } }));
        toast("Switched to the built-in Qwen model.");
      } catch (error) { toast(error.message, "bad"); }
    }
  }));

  $("key-show").addEventListener("click", () => {
    const input = $("api-key");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    $("key-show").textContent = show ? "Hide" : "Show";
  });

  function draft() {
    return { base_url: $("base-url").value.trim(), model: $("model-name").value.trim(), label: $("display-name").value.trim(), api_key: $("api-key").value.trim() };
  }

  async function busy(button, label, work) {
    const before = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `${icon("loader", 18, "a-spin")}${label}`;
    try { return await work(); } finally { button.disabled = false; button.innerHTML = before; }
  }

  $("test-builtin").addEventListener("click", () => busy($("test-builtin"), "Testing…", async () => {
    try { result($("builtin-result"), await api("/api/vision/connection/test", { method: "POST", json: { target: "builtin" } })); }
    catch (error) { result($("builtin-result"), { ok: false, message: error.message }); }
  }));

  $("test-own").addEventListener("click", () => busy($("test-own"), "Testing…", async () => {
    $("own-error").textContent = "";
    const d = draft();
    if (!d.base_url || !d.model) { $("own-error").textContent = "Enter the address and the model name first."; return; }
    try { result($("own-result"), await api("/api/vision/connection/test", { method: "POST", json: { target: "draft", ...d } })); }
    catch (error) { result($("own-result"), { ok: false, message: error.message }); }
  }));

  $("own-form").addEventListener("submit", event => {
    event.preventDefault();
    busy($("save-own"), "Saving…", async () => {
      $("own-error").textContent = "";
      const d = draft();
      if (!d.base_url || !d.model) { $("own-error").textContent = "Enter the address and the model name."; return; }
      const payload = { use_own: true, base_url: d.base_url, model: d.model, label: d.label };
      if (d.api_key) payload.api_key = d.api_key;
      try {
        render(await api("/api/vision/connection", { method: "PUT", json: payload }));
        toast("Saved. Your inspections now use your own AI.");
      } catch (error) { $("own-error").textContent = error.message; }
    });
  });

  $("forget-own").addEventListener("click", async () => {
    const ok = await window.Athena.askConfirm({ title: "Forget your own AI?", message: "The address and the saved key are removed. Your inspections go back to the built-in Qwen model.", keepLabel: "Keep it", confirmLabel: "Forget it" });
    if (!ok) return;
    try { render(await api("/api/vision/connection", { method: "DELETE" })); $("own-result").innerHTML = ""; toast("Removed. Using the built-in Qwen model."); }
    catch (error) { toast(error.message, "bad"); }
  });

  api("/api/vision/connection").then(render).catch(error => { $("active-text").textContent = error.message; });
})();
