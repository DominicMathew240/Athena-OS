/* Shared helpers for the check screen pages (builder + operator).
   Never builds markup from strings: every piece of text goes in through textContent. */
(function () {
  "use strict";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const CS = (window.CheckScreen = {});

  CS.el = function (tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };
  CS.byId = (id) => document.getElementById(id);
  CS.clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };
  CS.csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || "";
  CS.reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  CS.scrollTo = (node) => node.scrollIntoView({ block: "start", behavior: CS.reducedMotion() ? "auto" : "smooth" });

  CS.TYPE_NAMES = { "image/jpeg": "JPEG photo", "image/png": "PNG picture", "image/webp": "WebP picture" };
  CS.DECISIONS = {
    confirm: { label: "Confirm", help: "I agree with this result", done: "You confirmed this result" },
    reject: { label: "Reject", help: "I disagree with this result", done: "You rejected this result" },
    needs_second_look: { label: "Needs a second look", help: "Someone else should check", done: "You asked for a second look" }
  };

  CS.percent = (fraction) => `${Math.round(fraction * 100)}%`;
  CS.typeList = (types) => types.map((t) => CS.TYPE_NAMES[t] || t).join(", ");
  CS.megabytes = (bytes) => `${Math.round(bytes / 1048576)} MB`;

  /* A plain phrase for the computer's own score. It is a guess, never a measured accuracy. */
  CS.sureness = function (score) {
    if (score >= 0.9) return "Very sure";
    if (score >= 0.75) return "Fairly sure";
    if (score >= 0.5) return "Somewhat sure";
    return "Not very sure";
  };

  CS.thresholdWords = function (threshold) {
    const pct = Math.round(threshold * 100);
    if (pct === 0) return "Flags everything the model finds, even very weak guesses";
    if (pct < 40) return `Flags more things: it points out anything the computer is at least ${pct}% sure about`;
    if (pct <= 65) return `Middle setting: it points out things the computer is at least ${pct}% sure about`;
    if (pct < 100) return `Flags fewer things: it points out only what the computer is at least ${pct}% sure about`;
    return "Flags almost nothing: only a perfect score counts";
  };

  /* Network. Never throws; always resolves to {status, ok, body, networkError}. */
  CS.request = async function (url, options) {
    const opts = options || {};
    const headers = { Accept: "application/json", "X-CSRFToken": CS.csrf() };
    let body = opts.body;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.json);
    }
    let response;
    try {
      response = await fetch(url, { method: opts.method || "GET", credentials: "same-origin", headers, body, signal: opts.signal });
    } catch (error) {
      if (error && error.name === "AbortError") return { status: 0, ok: false, body: {}, aborted: true };
      return { status: 0, ok: false, body: {}, networkError: true };
    }
    let data = {};
    try { data = await response.json(); } catch (error) { data = {}; }
    return { status: response.status, ok: response.ok, body: data };
  };

  /* Turn a failed response into {title, text, detail}. `what` says what the person was trying to do. */
  CS.explain = function (result, what) {
    const serverText = (result.body && result.body.error) || "";
    const did = what || "do that";
    if (result.networkError) {
      return { title: "Could not reach the server", text: `Check your network connection, then try to ${did} again. Nothing was saved.`, detail: "" };
    }
    switch (result.status) {
      case 401:
        return { title: "You have been signed out", text: "Sign in again, then try again. Nothing was saved.", detail: "", signIn: true };
      case 403:
        return { title: "This page is out of date", text: "Reload the page and try again. Nothing was saved.", detail: "" };
      case 404:
        return { title: "This screen was not found", text: "It may have been removed or moved. Ask the person who set it up.", detail: serverText };
      case 409:
        if (/slug/i.test(serverText)) {
          return { title: "That web address name is taken", text: "Another screen already uses it. Choose a different short name, or leave it empty to have one made from the title.", detail: serverText };
        }
        if (/archived/i.test(serverText)) {
          return { title: "This screen is switched off", text: "It has been archived, so it will not check pictures. Ask the person who set it up to switch it on.", detail: serverText };
        }
        if (/no longer approved|not approved|is (candidate|staged|retired)|approved model/i.test(serverText)) {
          return { title: "The model behind this screen is no longer approved", text: "Nothing was checked. Ask the person who set up this screen to choose the model that is approved now.", detail: serverText };
        }
        return { title: "That could not be done right now", text: serverText || "The situation changed. Reload the page and try again.", detail: "" };
      case 413:
        return { title: "That picture is too large", text: "Choose a smaller picture.", detail: serverText };
      case 415:
        return { title: "This screen cannot use that kind of file", text: serverText || "Choose a different picture.", detail: "" };
      case 429:
        return { title: "Many pictures are waiting", text: "The checker is busy. Wait a minute and try again.", detail: "" };
      case 503:
        return {
          title: "The checking program is not running",
          text: "Nothing was checked and nothing was decided. The program that looks at pictures is switched off or cannot start. Tell your administrator, then try again.",
          detail: serverText
        };
      default:
        if (result.status >= 500) {
          return { title: "Something went wrong on the server", text: `Nothing was saved (error ${result.status}). Try again in a moment. If it keeps happening, tell your administrator.`, detail: serverText };
        }
        return { title: "That did not work", text: serverText || `The server refused the request (error ${result.status}).`, detail: "" };
    }
  };

  CS.showAlert = function (ids, info) {
    const box = CS.byId(ids.box);
    CS.byId(ids.title).textContent = info.title;
    CS.byId(ids.text).textContent = info.text;
    if (info.signIn) {
      const link = CS.el("a", "cs-link", " Sign in again");
      link.href = `/login/?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
      CS.byId(ids.text).appendChild(link);
    }
    if (ids.details) {
      const details = CS.byId(ids.details);
      details.hidden = !info.detail;
      details.open = false;
      CS.byId(ids.detailText).textContent = info.detail || "";
    }
    box.hidden = false;
  };
  CS.hideAlert = function (ids) {
    CS.byId(ids.box).hidden = true;
    if (ids.details) CS.byId(ids.details).hidden = true;
  };

  /* -- API paths -------------------------------------------------------------------------- */
  CS.paths = function (projectId, appId) {
    const apps = `/api/projects/${projectId}/inference/apps`;
    const base = `${apps}/${appId}`;
    return {
      apps,
      base,
      preview: `${base}/preview`,
      run: `${base}/run`,
      observation: (id) => `${base}/observations/${id}`,
      image: (id) => `${base}/observations/${id}/image`,
      feedback: (id) => `${base}/observations/${id}/feedback`,
      decision: (id) => `${base}/observations/${id}/decision`
    };
  };

  /* Client-side check of a chosen picture against the screen's own settings. Returns "" when fine. */
  CS.checkFile = function (file, render) {
    if (!file) return "Choose a picture first.";
    const types = render.accepted_inputs;
    if (!types.includes(file.type)) {
      return `This screen takes ${CS.typeList(types)} only. The file you chose is ${file.type ? `a ${file.type.replace("image/", "").toUpperCase()} file` : "not a recognised picture"}.`;
    }
    const max = render.limits && render.limits.max_upload_bytes;
    if (max && file.size > max) return `That picture is ${CS.megabytes(file.size)}. This screen takes pictures up to ${CS.megabytes(max)}.`;
    if (file.size === 0) return "That file is empty. Choose another picture.";
    return "";
  };

  /* Send one picture and wait for the result. Calls hooks.onStatus({status, elapsed, workerOnline}).
     Resolves {ok:true, render} | {ok:false, error:{title,text,detail}} | {ok:false, timedOut:true, resume()}. */
  CS.runImage = async function (paths, file, hooks, signal) {
    const form = new FormData();
    form.append("image", file);
    const sent = await CS.request(paths.run, { method: "POST", body: form, signal });
    if (sent.aborted) return { ok: false, aborted: true };
    if (!sent.ok) return { ok: false, error: CS.explain(sent, "check that picture") };
    const id = sent.body.observation_id;
    return CS.waitFor(paths, id, { workerOnline: sent.body.worker_online }, hooks, signal);
  };

  CS.waitFor = async function (paths, id, info, hooks, signal) {
    const started = Date.now();
    const limit = 120000;
    let misses = 0;
    for (;;) {
      if (signal && signal.aborted) return { ok: false, aborted: true };
      const polled = await CS.request(paths.observation(id), { signal });
      if (polled.aborted) return { ok: false, aborted: true };
      if (!polled.ok) {
        misses += 1;
        if (polled.status && polled.status < 500 && polled.status !== 0) return { ok: false, error: CS.explain(polled, "see the result") };
        if (misses >= 5) return { ok: false, error: CS.explain(polled, "see the result") };
      } else {
        misses = 0;
        const result = polled.body.render.result;
        const elapsed = Math.floor((Date.now() - started) / 1000);
        hooks.onStatus({ status: result.status, elapsed, workerOnline: info.workerOnline });
        if (result.status === "succeeded") return { ok: true, render: polled.body.render };
        if (result.status === "failed") {
          return { ok: false, failed: true, error: { title: "The picture could not be checked", text: "Nothing was decided. Try the same picture again, or a different one. If it keeps failing, tell your administrator.", detail: result.error || "" }, render: polled.body.render };
        }
        if (Date.now() - started > limit) {
          return { ok: false, timedOut: true, resume: () => CS.waitFor(paths, id, info, hooks, signal) };
        }
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
  };

  /* -- picture + boxes -------------------------------------------------------------------- */
  /* The overlay is exactly as large as the picture and uses 0-100 coordinates, so boxes stay aligned at any size. */
  CS.buildStage = function (src, alt) {
    const stage = CS.el("div", "cs-stage");
    const img = CS.el("img", "cs-stage-img");
    img.alt = alt;
    img.src = src;
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("class", "cs-overlay");
    svg.setAttribute("aria-hidden", "true");
    const tags = CS.el("div", "cs-tags");
    tags.setAttribute("aria-hidden", "true");
    const draw = CS.el("div", "cs-draw-layer");
    draw.setAttribute("aria-hidden", "true");
    stage.append(img, svg, tags, draw);

    const boxes = new Map();
    const rect = (cls) => {
      const r = document.createElementNS(SVG_NS, "rect");
      r.setAttribute("class", cls);
      return r;
    };
    const place = (r, box) => {
      r.setAttribute("x", String(box.x * 100));
      r.setAttribute("y", String(box.y * 100));
      r.setAttribute("width", String(box.width * 100));
      r.setAttribute("height", String(box.height * 100));
    };
    const api = {
      stage, img, draw,
      addDetection(det, number) {
        const group = document.createElementNS(SVG_NS, "g");
        const halo = rect("cs-box-halo");
        const line = rect("cs-box-line");
        place(halo, det.box); place(line, det.box);
        group.append(halo, line);
        svg.appendChild(group);
        const tag = CS.el("span", "cs-tag", String(number));
        tag.style.left = `${det.box.x * 100}%`;
        tag.style.top = `${det.box.y * 100}%`;
        if (det.box.y < 0.07) tag.classList.add("cs-tag-inside");
        tags.appendChild(tag);
        boxes.set(det.index, { group, tag });
      },
      addNote(box, number) {
        const group = document.createElementNS(SVG_NS, "g");
        const halo = rect("cs-box-halo");
        const line = rect("cs-box-line cs-box-added");
        place(halo, box); place(line, box);
        group.append(halo, line);
        svg.appendChild(group);
        const tag = CS.el("span", "cs-tag cs-tag-added", `+${number}`);
        tag.style.left = `${box.x * 100}%`;
        tag.style.top = `${box.y * 100}%`;
        if (box.y < 0.07) tag.classList.add("cs-tag-inside");
        tags.appendChild(tag);
      },
      highlight(index) {
        boxes.forEach((parts, key) => {
          parts.group.classList.toggle("is-active", key === index);
          parts.tag.classList.toggle("is-active", key === index);
        });
      },
      draftRect: null,
      setDraft(box) {
        if (!box) {
          if (api.draftRect) { api.draftRect.remove(); api.draftRect = null; }
          return;
        }
        if (!api.draftRect) {
          api.draftRect = rect("cs-box-draft");
          svg.appendChild(api.draftRect);
        }
        place(api.draftRect, box);
      },
      clearBoxes() {
        CS.clear(svg); CS.clear(tags); boxes.clear(); api.draftRect = null;
      }
    };
    return api;
  };

  CS.detectionRow = function (det, number) {
    const item = CS.el("li", "cs-det");
    item.dataset.index = String(det.index);
    const head = CS.el("div", "cs-det-head");
    head.appendChild(CS.el("span", "cs-det-num", String(number)));
    head.appendChild(CS.el("strong", "cs-det-label", det.label));
    const sure = CS.el("span", "cs-det-sure");
    sure.textContent = `${CS.sureness(det.score)} · ${CS.percent(det.score)}`;
    sure.title = "The percentage is the computer’s own guess. It is not a measured accuracy.";
    head.appendChild(sure);
    item.appendChild(head);
    return item;
  };

  CS.stateIcon = { clear: Athena.ICONS.done, flagged: Athena.ICONS.warn, needs_review: Athena.ICONS.unknown, processing: Athena.ICONS.busy, failed: Athena.ICONS.close };

  /* One sentence that says what the computer found, separate from the operator's own wording. */
  CS.machineSentence = function (result, render) {
    if (result.status !== "succeeded") return "";
    const n = result.detections.length;
    const threshold = CS.percent(render.confidence_threshold);
    // At a sureness of 0 "at least 0% sure" is meaningless: say what happened without a number.
    if (threshold === "0%") return n === 0 ? "The computer found nothing at all." : `The computer found ${n === 1 ? "1 thing" : `${n} things`}.`;
    if (n === 0) return `The computer found nothing it is at least ${threshold} sure about.`;
    return `The computer found ${n === 1 ? "1 thing" : `${n} things`} it is at least ${threshold} sure about.`;
  };
})();
