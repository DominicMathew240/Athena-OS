/* Live RTSP installer preview (T10). Safe DOM only — no innerHTML for server text. */
(function () {
  "use strict";

  function byId(id) {
    return document.getElementById(id);
  }

  function api(path, opts) {
    if (window.Athena && typeof window.Athena.api === "function") {
      return window.Athena.api(path, opts || {});
    }
    const headers = { Accept: "application/json" };
    const init = { method: (opts && opts.method) || "GET", credentials: "same-origin", headers };
    if (opts && opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.json);
    }
    return fetch(path, init).then(async (response) => {
      const text = await response.text();
      let body = {};
      try { body = text ? JSON.parse(text) : {}; } catch (_) { body = { error: text || response.statusText }; }
      if (!response.ok) {
        const err = new Error(body.error || response.statusText || "Request failed");
        err.status = response.status;
        err.body = body;
        throw err;
      }
      return body;
    });
  }

  function setText(node, text) {
    if (node) node.textContent = text || "";
  }

  function fillCameraSources(sources) {
    const select = byId("live-rtsp-source");
    if (!select) return;
    const current = select.value;
    select.replaceChildren();
    const cameras = (sources || []).filter((s) => s.kind === "camera");
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = cameras.length ? "Choose a camera source…" : "Add a camera source first…";
    placeholder.disabled = !cameras.length;
    select.append(placeholder);
    for (const source of cameras) {
      const opt = document.createElement("option");
      opt.value = source.id;
      opt.textContent = source.name;
      select.append(opt);
    }
    if ([...select.options].some((o) => o.value === current)) select.value = current;
  }

  async function refreshSources() {
    const result = await api("/api/operation-sources");
    fillCameraSources(result.sources || []);
  }

  function showCategory(category) {
    const node = byId("live-rtsp-category");
    if (!node) return;
    if (!category) {
      node.hidden = true;
      setText(node, "");
      return;
    }
    node.hidden = false;
    setText(node, "Failure category: " + category);
  }

  function wire() {
    const panel = byId("live-rtsp-details");
    if (!panel) return;

    byId("live-rtsp-save-password").addEventListener("click", async () => {
      setText(byId("live-rtsp-error"), "");
      showCategory("");
      const sourceId = byId("live-rtsp-source").value;
      const password = byId("live-rtsp-password").value;
      if (!sourceId) {
        setText(byId("live-rtsp-error"), "Choose a camera source first.");
        return;
      }
      if (!password) {
        setText(byId("live-rtsp-error"), "Enter the camera password to store.");
        return;
      }
      setText(byId("live-rtsp-status"), "Saving password…");
      try {
        await api("/api/operation-sources/" + encodeURIComponent(sourceId) + "/credential", {
          method: "PUT",
          json: { secret: password },
        });
        byId("live-rtsp-password").value = "";
        setText(byId("live-rtsp-status"), "Password saved for this source (not shown again).");
      } catch (error) {
        setText(byId("live-rtsp-status"), "");
        setText(byId("live-rtsp-error"), error.message || "Could not save the password.");
        if (error.body && error.body.category) showCategory(error.body.category);
      }
    });

    byId("live-rtsp-preview").addEventListener("click", async () => {
      setText(byId("live-rtsp-error"), "");
      showCategory("");
      byId("live-rtsp-preview-wrap").hidden = true;
      const sourceId = byId("live-rtsp-source").value;
      const url = byId("live-rtsp-url").value.trim();
      const username = byId("live-rtsp-username").value.trim();
      if (!sourceId) {
        setText(byId("live-rtsp-error"), "Choose a camera source first.");
        return;
      }
      if (!url) {
        setText(byId("live-rtsp-error"), "Enter the RTSP URL (without a password).");
        return;
      }
      setText(byId("live-rtsp-status"), "Connecting to the camera…");
      try {
        const body = { url, include_preview: true };
        if (username) body.username = username;
        const result = await api(
          "/api/operation-sources/" + encodeURIComponent(sourceId) + "/live-rtsp/preview",
          { method: "POST", json: body }
        );
        const stream = result.stream || {};
        setText(
          byId("live-rtsp-status"),
          result.message || "Camera connected."
        );
        setText(
          byId("live-rtsp-stream-info"),
          (stream.codec_name || "?") +
            " · " +
            (stream.width || "?") +
            "×" +
            (stream.height || "?") +
            " · " +
            (stream.transport || "tcp") +
            (result.credential_used ? " · credential used" : " · no credential")
        );
        const img = byId("live-rtsp-frame");
        if (result.preview_jpeg_base64) {
          img.src = "data:image/jpeg;base64," + result.preview_jpeg_base64;
          byId("live-rtsp-preview-wrap").hidden = false;
        }
      } catch (error) {
        setText(byId("live-rtsp-status"), "");
        setText(byId("live-rtsp-error"), error.message || "Could not connect.");
        if (error.body && error.body.category) showCategory(error.body.category);
      }
    });

    // Refresh camera list when the panel opens or after sources change.
    panel.addEventListener("toggle", () => {
      if (panel.open) refreshSources().catch(() => {});
    });
    document.addEventListener("athena:sources-changed", () => {
      refreshSources().catch(() => {});
    });
    refreshSources().catch(() => {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
