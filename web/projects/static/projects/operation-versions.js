/* Operation setup: where a draft lives (site, source, operation) and the frozen
   versions it is built from. Nothing here counts people; it prepares a private
   draft so a later runtime can be held to exactly these choices. */
(() => {
  "use strict";
  const { api } = window.AthenaApp;
  const { gate } = window.Athena;
  const section = document.getElementById("operation-setup");
  if (!section) return;

  // One status line and one error line per area, so a person acting there sees
  // the result there — the same approach the drafts editor above uses.
  const byId = id => document.getElementById(id);
  const area = id => ({ note: byId(`${id}-status`), err: byId(`${id}-error`) });
  const areas = {
    setup: area("operation"),
    attach: area("attach"),
    pins: area("pin"),
    creators: area("creator"),
    release: area("release")
  };
  const attachLine = byId("draft-operation-line");
  // The pin, freeze and release areas only make sense once a draft is shown; the
  // scope area (sites, sources, operations) is useful any time and always visible.
  const DRAFT_SECTIONS = ["pin-section", "creator-section", "release-section"].map(byId);

  const SOURCE_KIND_LABELS = { recorded_video: "Recorded video", camera: "Camera", image_collection: "Image collection" };
  const PIN_HUMAN = { source_view: "camera view", geometry: "counting line", recipe: "counting rules", policy: "evidence rules", model: "model", screen: "board" };

  let busy = false;
  let draft = null;            // the draft currently shown by the drafts editor
  let scope = { sites: [], sources: [], operations: [] };
  let operationVersions = {};  // operation id -> loaded version lists
  let projectsCache = null;

  const SCREEN_DEFAULT_WIDGET_ORDER = ["entries", "exits"];
  // Unsaved "Freeze the board look" form state per draft.id (in-memory only, never persisted).
  const screenDraftStates = new Map();

  const normalizeScreenOrder = order => (
    Array.isArray(order) && order.length === 2 && order.includes("entries") && order.includes("exits")
      ? [...order]
      : [...SCREEN_DEFAULT_WIDGET_ORDER]
  );

  const cloneScreenConfig = config => ({
    title: typeof config?.title === "string" ? config.title : "",
    show_entries: Boolean(config?.show_entries),
    show_exits: Boolean(config?.show_exits),
    widget_order: normalizeScreenOrder(config?.widget_order)
  });

  const isScreenConfigDirty = (current, loaded) => (
    current.title !== loaded.title
    || current.show_entries !== loaded.show_entries
    || current.show_exits !== loaded.show_exits
    || current.widget_order[0] !== loaded.widget_order[0]
    || current.widget_order[1] !== loaded.widget_order[1]
  );

  const isAnyScreenDraftDirty = () => {
    for (const state of screenDraftStates.values()) {
      if (isScreenConfigDirty(state, state.loadedConfig)) return true;
    }
    return false;
  };

  async function guarded(action, working, where = areas.setup) {
    if (busy) return;
    busy = true;
    if (where.err) where.err.textContent = "";
    if (where.note) {
      where.note.textContent = working || "Working…";
      where.note.hidden = false;
    }
    try {
      await action();
    } catch (problem) {
      if (where.err) where.err.textContent = problem.message;
    } finally {
      busy = false;
      // Hide only the "Working…" placeholder; a message the action left behind (e.g.
      // "Source added.") must stay visible until the next action starts.
      if (where.note && where.note.textContent === (working || "Working…")) where.note.hidden = true;
    }
  }

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    if (props) for (const [key, value] of Object.entries(props)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const child of children) if (child !== null && child !== undefined) node.append(child);
    return node;
  }

  const formRow = (labelText, control) => el("div", { class: "a-field" },
    el("label", { class: "a-label", for: control.id, text: labelText }), control);

  function numberInput(id, { min, max, step, value, placeholder }) {
    const input = el("input", { class: "a-input", type: "number", id, "data-needs": "label" });
    if (min !== undefined) input.min = String(min);
    if (max !== undefined) input.max = String(max);
    if (step !== undefined) input.step = String(step);
    if (value !== undefined) input.value = String(value);
    if (placeholder) input.placeholder = placeholder;
    return input;
  }

  const option = (value, label, disabled) => el("option", { value, text: label, ...(disabled ? { disabled: "" } : {}) });

  /* ---------- data loading ---------- */

  async function loadScope() {
    const [sites, sources, operations] = await Promise.all([
      api("/api/operation-sites"), api("/api/operation-sources"), api("/api/operations")
    ]);
    scope = { sites: sites.sites, sources: sources.sources, operations: operations.operations };
    renderScope();
  }

  async function loadOperationVersions(operationId) {
    if (!operationId || operationVersions[operationId]) return;
    const [sourceViews, geometry, recipes, policies, bindings, screens] = await Promise.all([
      api(`/api/operations/${operationId}/source-views`),
      api(`/api/operations/${operationId}/geometry-versions`),
      api(`/api/operations/${operationId}/recipe-versions`),
      api(`/api/operations/${operationId}/policy-versions`),
      api(`/api/operations/${operationId}/model-bindings`),
      api(`/api/operations/${operationId}/screen-versions`)
    ]);
    operationVersions[operationId] = {
      source_views: sourceViews.source_views,
      geometry: geometry.geometry_versions,
      recipes: recipes.recipe_versions,
      policies: policies.policy_versions,
      model_bindings: bindings.model_bindings,
      screens: screens.screen_versions
    };
  }

  async function loadProjects() {
    if (projectsCache) return projectsCache;
    projectsCache = (await api("/api/projects")).projects;
    return projectsCache;
  }

  /* ---------- scope lists and creation forms ---------- */

  function fillSiteSelects() {
    for (const id of ["source-site", "operation-site"]) {
      const select = byId(id);
      const current = select.value;
      select.replaceChildren(option("", scope.sites.length ? "Choose a site…" : "Create a site first…", !scope.sites.length));
      for (const site of scope.sites) select.append(option(site.id, site.name));
      if ([...select.options].some(o => o.value === current)) select.value = current;
    }
    fillOperationSourceSelect();
  }

  function fillOperationSourceSelect() {
    const select = byId("operation-source");
    const siteId = byId("operation-site").value;
    const sources = scope.sources.filter(s => s.site_id === siteId);
    select.replaceChildren(option("", sources.length ? "Choose a source…" : "This site has no sources yet…", !sources.length));
    for (const source of sources) select.append(option(source.id, `${source.name} (${SOURCE_KIND_LABELS[source.kind] || source.kind})`));
  }

  function renderScope() {
    fillSiteSelects();
    const operationsBox = byId("operation-list");
    operationsBox.replaceChildren();
    if (!scope.operations.length) {
      operationsBox.append(el("p", { class: "a-help", text: "No operations yet. Create your first below: name a site, add its source, and name the operation." }));
    } else {
      for (const operation of scope.operations) {
        const site = scope.sites.find(s => s.id === operation.site_id);
        const source = scope.sources.find(s => s.id === operation.source_id);
        const summary = `${site ? site.name : "Unknown site"} · ${source ? `${source.name} (${SOURCE_KIND_LABELS[source.kind] || source.kind})` : "unknown source"} · ${site ? site.timezone_name : ""}`;
        operationsBox.append(el("li", { class: "op-row" },
          el("div", { class: "op-row-main" },
            el("strong", { text: operation.name }),
            el("span", { class: "a-help", text: summary })),
          el("span", { class: "a-tag", text: "Private" })));
      }
    }
    const sitesBox = byId("site-list");
    sitesBox.replaceChildren();
    if (!scope.sites.length) {
      sitesBox.append(el("p", { class: "a-help", text: "No sites yet." }));
    } else {
      for (const site of scope.sites) {
        const sources = scope.sources.filter(s => s.site_id === site.id);
        const names = sources.length ? sources.map(s => s.name).join(", ") : "no sources yet";
        sitesBox.append(el("li", { class: "op-row" },
          el("div", { class: "op-row-main" },
            el("strong", { text: site.name }),
            el("span", { class: "a-help", text: `${site.timezone_name} · ${names}` }))));
      }
    }
    renderAttach();
  }

  function wireScopeForms() {
    byId("operation-site").addEventListener("change", fillOperationSourceSelect);
    byId("site-create").addEventListener("click", () => guarded(async () => {
      const name = byId("site-name").value.trim();
      const timezone = byId("site-timezone").value.trim();
      if (!name || !timezone) throw new Error("Enter both a site name and a time zone.");
      await api("/api/operation-sites", { method: "POST", json: { name, timezone_name: timezone } });
      byId("site-name").value = "";
      byId("site-timezone").value = "";
      byId("site-status").textContent = "Site created.";
      await loadScope();
    }, "Creating the site…", { note: byId("site-status"), err: byId("site-error") }));
    byId("source-create").addEventListener("click", () => guarded(async () => {
      const siteId = byId("source-site").value;
      const name = byId("source-name").value.trim();
      if (!siteId) throw new Error("Choose the site this source belongs to.");
      if (!name) throw new Error("Enter a source name.");
      await api("/api/operation-sources", {
        method: "POST", json: { site_id: siteId, name, kind: byId("source-kind").value }
      });
      byId("source-name").value = "";
      byId("source-status").textContent = "Source added.";
      await loadScope();
    }, "Adding the source…", { note: byId("source-status"), err: byId("source-error") }));
    byId("operation-create").addEventListener("click", () => guarded(async () => {
      const siteId = byId("operation-site").value;
      const sourceId = byId("operation-source").value;
      const name = byId("operation-name").value.trim();
      if (!siteId || !sourceId) throw new Error("Choose both a site and one of its sources.");
      if (!name) throw new Error("Enter an operation name.");
      await api("/api/operations", { method: "POST", json: { name, site_id: siteId, source_id: sourceId } });
      byId("operation-name").value = "";
      byId("operation-create-status").textContent = "Operation created.";
      await loadScope();
    }, "Creating the operation…", { note: byId("operation-create-status"), err: byId("operation-create-error") }));
  }

  /* ---------- attach flow ---------- */

  function renderAttach() {
    const wrap = byId("attach-area");
    wrap.replaceChildren();
    if (!draft) return;
    if (draft.operation_id) {
      const operation = scope.operations.find(o => o.id === draft.operation_id);
      attachLine.hidden = false;
      attachLine.textContent = `This draft is attached to: ${operation ? operation.name : "an operation"}. The attachment is permanent.`;
      return;
    }
    attachLine.hidden = true;
    if (!scope.operations.length) {
      wrap.append(el("p", { class: "a-help", text: "Create an operation below, then attach this draft to it." }));
      return;
    }
    const select = el("select", { class: "a-input", id: "attach-operation", "data-testid": "attach-operation", "data-needs": "label" });
    select.append(option("", "Choose an operation…"));
    for (const operation of scope.operations) select.append(option(operation.id, operation.name));
    const button = gate(el("button", { class: "a-btn", type: "button", text: "Attach draft", "data-testid": "attach-submit" }), "label");
    button.addEventListener("click", () => guarded(async () => {
      if (!select.value) throw new Error("Choose an operation to attach this draft to.");
      const payload = {
        name: draft.name, line: draft.line, bays: draft.bays || [], reference_events: draft.reference_events,
        revision: draft.revision, operation_id: select.value
      };
      const result = await api(`/api/operation-drafts/${draft.id}`, { method: "PUT", json: payload });
      setDraft(result.draft);
      announceDraft(result.draft);
      areas.attach.note.textContent = "Draft attached to the operation. You can now freeze and pin its versions.";
    }, "Attaching…", areas.attach));
    wrap.append(formRow("Attach this draft to an operation", select), button);
  }

  /* ---------- pins ---------- */

  function pinSelect(name, human, current, choices, emptyText) {
    const select = el("select", { class: "a-input", id: `pin-${name}`, "data-testid": `pin-select-${name}`, "data-needs": "label" });
    if (!choices.length) {
      select.append(option("", emptyText));
      select.disabled = true;
      select.dataset.empty = "1";
    } else {
      select.append(option("", current ? "Keep the current choice" : "Nothing chosen yet"));
      if (current) select.append(option("none", "Remove this pin"));
      for (const choice of choices) select.append(option(choice.id, choice.label));
      if (current) select.value = current;
    }
    return { select, wrap: el("div", { class: "a-field pin-field" },
      el("label", { class: "a-label", for: `pin-${name}`, text: human }), select) };
  }

  /* The pin pickers are rebuilt each time the draft or its operation's versions change. */
  function renderPins() {
    const box = byId("pin-fields");
    box.replaceChildren();
    if (!draft) return;
    if (!draft.operation_id) {
      box.append(el("p", { class: "a-help", text: "Attach this draft to an operation to choose its frozen versions." }));
      return;
    }
    const versions = operationVersions[draft.operation_id]
      || { source_views: [], geometry: [], recipes: [], policies: [], model_bindings: [], screens: [] };
    const line = draft.line;
    const bays = draft.bays || [];
    const geometryChoices = versions.geometry
      .filter(g => !line || JSON.stringify(g.geometry.line) === JSON.stringify(line))
      .filter(g => JSON.stringify(g.geometry.bays || []) === JSON.stringify(bays))
      .map(g => ({ id: g.id, label: `Version ${g.version} of this line and its ${g.geometry.bays?.length || 0} ${g.geometry.bays?.length === 1 ? "bay" : "bays"}` }));
    const pins = [
      ["geometry", "Counting-line version", draft.pins.geometry_version_id, geometryChoices, "No frozen version of this exact line yet. Save the line on the draft, then freeze it below."],
      ["recipe", "Counting rules", draft.pins.recipe_version_id, versions.recipes.map(r => ({ id: r.id, label: `Version ${r.version}` })), "No counting rules frozen yet. Create them below."],
      ["policy", "Evidence rules", draft.pins.policy_version_id, versions.policies.map(p => ({ id: p.id, label: `Version ${p.version}` })), "No evidence rules frozen yet. Create them below."],
      ["model", "Model used", draft.pins.model_binding_id, versions.model_bindings.map(b => ({ id: b.id, label: `Binding ${b.version}` })), "No model bound yet. Bind one below."],
      ["screen", "Board look", draft.pins.screen_version_id, versions.screens.map(s => ({ id: s.id, label: `Version ${s.version}` })), "No board frozen yet. Create one below."]
    ].map(([name, human, current, choices, emptyText]) => pinSelect(name, human, current, choices, emptyText));
    for (const pin of pins) {
      pin.select.addEventListener("change", () => applyPin(pin.select));
      box.append(pin.wrap);
    }
    box.append(el("p", { class: "a-help", text: "Pins are saved with the draft. Each pin points at one frozen version; changing the draft later never changes a frozen version." }));
    renderBundleArea();
  }

  async function applyPin(select) {
    if (!draft || busy) return;
    const kind = select.id.replace("pin-", "");
    const field = { geometry: "geometry_version_id", recipe: "recipe_version_id", policy: "policy_version_id", model: "model_binding_id", screen: "screen_version_id" }[kind];
    const chosen = select.dataset.empty === "1" ? null : select.value === "" ? undefined : select.value === "none" ? null : select.value;
    if (chosen === undefined) return;
    const payload = {
      name: draft.name, line: draft.line, bays: draft.bays || [], reference_events: draft.reference_events,
      revision: draft.revision, [field]: chosen
    };
    await guarded(async () => {
      const result = await api(`/api/operation-drafts/${draft.id}`, { method: "PUT", json: payload });
      setDraft(result.draft);
      announceDraft(result.draft);
      areas.pins.note.textContent = chosen ? "Pin saved with the draft." : "Pin removed. Pin every part again before assembling a release candidate.";
    }, "Saving the pin…", areas.pins);
  }

  /* ---------- version creators ---------- */

  function creatorCard(id, heading, explanation, buildBody) {
    const body = el("div");
    const card = el("section", { class: "a-card op-creator", id, "aria-labelledby": `${id}-heading` },
      el("h3", { class: "a-h3", id: `${id}-heading`, text: heading }),
      el("p", { class: "a-help", text: explanation }),
      body);
    buildBody(body);
    return card;
  }

  async function refreshVersions() {
    delete operationVersions[draft.operation_id];
    await loadOperationVersions(draft.operation_id);
    renderPins();
    renderCreators();
  }

  function renderCreators() {
    const box = byId("operation-creators");
    box.replaceChildren();
    if (!draft || !draft.operation_id) return;
    const versions = operationVersions[draft.operation_id]
      || { source_views: [], geometry: [], recipes: [], policies: [], model_bindings: [], screens: [] };

    box.append(creatorCard("create-source-view", "Freeze the camera view", "A frozen view records the picture size and orientation this draft was marked against, so later analysis uses the same picture.", body => {
      const recording = draft.recording;
      const width = numberInput("sv-width", { min: 1, max: 16384, step: 1, placeholder: "e.g. 1920", value: recording ? recording.width : undefined });
      const height = numberInput("sv-height", { min: 1, max: 16384, step: 1, placeholder: "e.g. 1080", value: recording ? recording.height : undefined });
      const rotation = el("select", { class: "a-input", id: "sv-rotation", "data-needs": "label" },
        option("0", "No turn"), option("90", "Turned 90°"), option("180", "Turned 180°"), option("270", "Turned 270°"));
      const flips = el("div", { class: "op-checks" },
        el("label", { class: "a-check", for: "sv-hflip" }, el("input", { type: "checkbox", id: "sv-hflip", "data-needs": "label" }), "Mirror left-right"),
        el("label", { class: "a-check", for: "sv-vflip" }, el("input", { type: "checkbox", id: "sv-vflip", "data-needs": "label" }), "Flip upside-down"));
      const button = gate(el("button", { class: "a-btn", type: "button", text: "Freeze this view", "data-testid": "create-source-view" }), "label");
      button.addEventListener("click", () => guarded(async () => {
        const configuration = {
          width: Number(width.value), height: Number(height.value), rotation: Number(rotation.value),
          horizontal_flip: byId("sv-hflip").checked, vertical_flip: byId("sv-vflip").checked
        };
        const result = await api(`/api/operations/${draft.operation_id}/source-views`, { method: "POST", json: { configuration } });
        await refreshVersions();
        areas.creators.note.textContent = `Camera view frozen as version ${result.source_view.version}.`;
      }, "Freezing the view…", areas.creators));
      body.append(
        el("p", { class: "a-help", text: recording ? "The picture size is taken from your recording; change it only if you know the source differs." : "Enter the picture size of the source this operation watches." }),
        formRow("Picture width (pixels)", width), formRow("Picture height (pixels)", height),
        formRow("Orientation", rotation), flips, button);
    }));

    box.append(creatorCard("create-geometry", "Freeze the counting line and bays", "A frozen version is this exact line and bay list kept forever. Freeze it after saving them on the draft, so later analysis uses the same geometry.", body => {
      if (!draft.line) {
        body.append(el("p", { class: "a-help", text: "Save a counting line on the draft first; then it can be frozen here. The draft's bays are frozen with it." }));
        return;
      }
      const views = versions.source_views;
      const select = views.length
        ? el("select", { class: "a-input", id: "geometry-view", "data-needs": "label" },
          ...views.map(v => option(v.id, `View version ${v.version} (${v.configuration.width}×${v.configuration.height})`)))
        : null;
      const button = gate(el("button", { class: "a-btn", type: "button", text: "Freeze this geometry", "data-testid": "create-geometry" }), "label");
      button.addEventListener("click", () => guarded(async () => {
        if (!select || !select.value) throw new Error("Freeze the camera view first; the geometry is stored against it.");
        await api(`/api/operations/${draft.operation_id}/geometry-versions`, {
          method: "POST", json: { source_view_version_id: select.value, geometry: { line: draft.line, bays: draft.bays || [] } }
        });
        await refreshVersions();
        areas.creators.note.textContent = "Geometry frozen. Pin it on the draft above to use it.";
      }, "Freezing the geometry…", areas.creators));
      const bays = draft.bays || [];
      const summary = `The line saved on this draft will be frozen exactly as marked: from ${Math.round(draft.line.start[0] * 100)}%, ${Math.round(draft.line.start[1] * 100)}% to ${Math.round(draft.line.end[0] * 100)}%, ${Math.round(draft.line.end[1] * 100)}%, naming movement "${draft.line.positive_label}" and "${draft.line.negative_label}"`
        + (bays.length
          ? `, together with ${bays.length} named ${bays.length === 1 ? "bay" : "bays"} (${bays.map(b => b.name).join(", ")}), each keeping its stable ID.`
          : ", with no bays.");
      body.append(
        select ? formRow("Frozen against view", select) : el("p", { class: "a-help", text: "Freeze the camera view first; the geometry is stored against it." }),
        el("p", { class: "a-help", text: summary }),
        button);
    }));

    box.append(creatorCard("create-recipe", "Freeze the counting rules", "How strict the counting should be: how long to wait before confirming, and how much of a gap to tolerate.", body => {
      const deadBand = numberInput("recipe-dead-band", { min: 0, max: 0.99, step: 0.01, value: 0.01 });
      const confirmations = numberInput("recipe-confirmations", { min: 1, max: 100, step: 1, value: 2 });
      const maxGap = numberInput("recipe-max-gap", { min: 0.1, step: 0.1, value: 1 });
      const tolerance = numberInput("recipe-tolerance", { min: 0.1, step: 0.1, value: 0.5 });
      const button = gate(el("button", { class: "a-btn", type: "button", text: "Freeze these rules", "data-testid": "create-recipe" }), "label");
      button.addEventListener("click", () => guarded(async () => {
        const configuration = {
          dead_band: Number(deadBand.value), confirmations: Number(confirmations.value),
          max_gap_seconds: Number(maxGap.value), comparison_tolerance_seconds: Number(tolerance.value)
        };
        const result = await api(`/api/operations/${draft.operation_id}/recipe-versions`, {
          method: "POST", json: { recipe_key: "people_counting", configuration }
        });
        await refreshVersions();
        areas.creators.note.textContent = `Counting rules frozen as version ${result.recipe_version.version}. Pin them on the draft above to use them.`;
      }, "Freezing the rules…", areas.creators));
      body.append(
        formRow("Dead band (0 to 1)", deadBand), formRow("Confirmations needed (1 to 100)", confirmations),
        formRow("Longest gap kept (seconds)", maxGap), formRow("Comparison tolerance (seconds)", tolerance), button);
    }));

    box.append(creatorCard("create-policy", "Freeze the evidence rules", "How long crossings and their pictures are kept, and how much missing coverage is tolerated.", body => {
      const days = numberInput("policy-days", { min: 1, max: 3650, step: 1, value: 90 });
      const gap = numberInput("policy-gap", { min: 0.1, step: 0.1, value: 5 });
      const button = gate(el("button", { class: "a-btn", type: "button", text: "Freeze these rules", "data-testid": "create-policy" }), "label");
      button.addEventListener("click", () => guarded(async () => {
        const configuration = { evidence_retention_days: Number(days.value), max_coverage_gap_seconds: Number(gap.value) };
        const result = await api(`/api/operations/${draft.operation_id}/policy-versions`, {
          method: "POST", json: { configuration }
        });
        await refreshVersions();
        areas.creators.note.textContent = `Evidence rules frozen as version ${result.policy_version.version}. Pin them on the draft above to use them.`;
      }, "Freezing the rules…", areas.creators));
      body.append(formRow("Keep evidence for (days, 1 to 3,650)", days), formRow("Longest coverage gap allowed (seconds)", gap), button);
    }));

    box.append(creatorCard("create-model", "Bind a model for counting", "Choose an approved model from one of your detector projects. Binding is permanent and records which exact model this operation must use.", body => {
      const projectSelect = el("select", { class: "a-input", id: "bind-project", "data-needs": "label" });
      const modelSelect = el("select", { class: "a-input", id: "bind-model", "data-testid": "bind-model", "data-needs": "label", disabled: "" });
      projectSelect.append(option("", "Choose a project…"));
      modelSelect.append(option("", "Choose a project first…"));
      projectSelect.addEventListener("change", async () => {
        if (!projectSelect.value) { modelSelect.replaceChildren(option("", "Choose a project first…")); return; }
        modelSelect.replaceChildren(option("", "Loading…"));
        try {
          const result = await api(`/api/projects/${projectSelect.value}/models`);
          const approved = result.models.filter(m => m.stage === "approved");
          modelSelect.replaceChildren();
          if (!approved.length) modelSelect.append(option("", "No approved models in this project yet."));
          else {
            modelSelect.append(option("", "Choose a model…"));
            for (const model of approved) modelSelect.append(option(model.id, `Version ${model.version} · approved`));
          }
          modelSelect.disabled = !approved.length;
        } catch (problem) {
          modelSelect.replaceChildren(option("", problem.message));
          modelSelect.disabled = true;
        }
      });
      const button = gate(el("button", { class: "a-btn", type: "button", text: "Bind this model", "data-testid": "create-binding" }), "label");
      button.addEventListener("click", () => guarded(async () => {
        if (!modelSelect.value) throw new Error("Choose an approved model to bind.");
        const result = await api(`/api/operations/${draft.operation_id}/model-bindings`, {
          method: "POST", json: { model_version_id: modelSelect.value }
        });
        await refreshVersions();
        areas.creators.note.textContent = `Model bound as binding ${result.model_binding.version}. Pin it on the draft above to use it.`;
      }, "Binding the model…", areas.creators));
      body.append(
        el("p", { class: "a-help", text: "Only models you trained and approved yourself can be bound, and only the approved ones." }),
        formRow("Detector project", projectSelect), formRow("Model", modelSelect), button);
      loadProjects().then(projects => {
        for (const project of projects) projectSelect.append(option(project.id, project.name));
      }).catch(() => areas.creators.err.textContent = "Could not load your projects.");
    }));

    box.append(creatorCard("create-screen", "Freeze the board look", "A private layout choice for this operation's counting board: its title and which numbers it shows. Not published or running.", body => {
      const WIDGET_LABELS = { entries: "Entries", exits: "Exits" };
      const savedScreens = Array.isArray(versions.screens) ? versions.screens : [];
      const draftId = draft.id;

      const newConfig = () => ({
        title: draft.name ?? "",
        show_entries: true,
        show_exits: true,
        widget_order: [...SCREEN_DEFAULT_WIDGET_ORDER]
      });
      const configFromScreen = screen => cloneScreenConfig(screen?.configuration);

      const savedState = draftId ? screenDraftStates.get(draftId) : null;
      let selectedVersionId = savedState ? (savedState.selectedVersionId || "") : "";
      if (selectedVersionId && !savedScreens.some(screen => screen.id === selectedVersionId)) {
        selectedVersionId = "";
      }
      let loadedConfig = savedState ? cloneScreenConfig(savedState.loadedConfig) : newConfig();
      const initialConfig = savedState ? cloneScreenConfig(savedState) : cloneScreenConfig(loadedConfig);
      let widgetOrder = [...initialConfig.widget_order];
      let confirmingVersionChange = false;

      const versionSelectEl = el("select", {
        class: "a-input",
        id: "screen-version-select",
        "data-testid": "screen-version-select",
        "aria-describedby": "screen-version-help"
      }, option("", "New layout"));
      for (const screen of savedScreens) {
        const screenTitle = typeof screen.configuration?.title === "string" ? screen.configuration.title.trim() : "";
        versionSelectEl.append(option(
          screen.id,
          screenTitle ? `Version ${screen.version} — ${screenTitle}` : `Version ${screen.version}`
        ));
      }
      if (!savedScreens.length) {
        versionSelectEl.disabled = true;
        versionSelectEl.dataset.empty = "1";
      }
      if (selectedVersionId) versionSelectEl.value = selectedVersionId;
      const versionSelect = gate(versionSelectEl, "label");
      const versionHelp = el("p", {
        class: "a-help",
        id: "screen-version-help",
        text: savedScreens.length
          ? "Saved versions never change. Choosing one copies its layout here so you can freeze a new version."
          : "No saved board versions yet. Freeze this board to create version 1."
      });

      const title = el("input", { class: "a-input", id: "screen-title", maxlength: "120", "data-needs": "label", placeholder: "e.g. Shop entrance today", value: initialConfig.title });
      const entriesInput = el("input", { type: "checkbox", id: "screen-entries", "data-needs": "label", ...(initialConfig.show_entries ? { checked: "" } : {}) });
      const exitsInput = el("input", { type: "checkbox", id: "screen-exits", "data-needs": "label", ...(initialConfig.show_exits ? { checked: "" } : {}) });
      title.value = initialConfig.title;
      entriesInput.checked = initialConfig.show_entries;
      exitsInput.checked = initialConfig.show_exits;
      const entries = el("label", { class: "a-check", for: "screen-entries" }, entriesInput, "Show entries");
      const exits = el("label", { class: "a-check", for: "screen-exits" }, exitsInput, "Show exits");

      const orderList = el("ol", { class: "screen-order-list", "data-testid": "screen-order-list" });
      const orderStatus = el("p", { class: "a-sr", role: "status", "aria-live": "polite" });
      const orderSection = el("div", { class: "screen-order", role: "group", "aria-labelledby": "screen-order-label", "data-testid": "screen-order" },
        el("div", { class: "screen-order-head" },
          el("span", { class: "a-label", id: "screen-order-label", text: "Tile order" }),
          el("span", { class: "a-help", text: "Arrange which tile appears first on the board." })),
        orderList,
        orderStatus);

      const previewTitle = el("p", { class: "screen-canvas-title", "data-testid": "screen-preview-title" });
      const previewTiles = el("div", { class: "screen-canvas-tiles", "data-testid": "screen-preview-tiles" });
      const previewSection = el("div", { class: "screen-preview", role: "region", "aria-labelledby": "screen-preview-heading", "data-testid": "screen-preview" },
        el("div", { class: "screen-preview-bar" },
          el("h4", { class: "screen-preview-heading", id: "screen-preview-heading", text: "Board canvas preview" }),
          el("span", { class: "a-tag screen-preview-badge", "data-testid": "screen-preview-badge", text: "Layout preview only" })),
        el("p", { class: "a-help screen-preview-notice", "data-testid": "screen-preview-notice", text: "Layout preview only, not live or published. Counts stay as dashes (—) here." }),
        el("div", { class: "screen-canvas", "data-testid": "screen-canvas" },
          el("div", { class: "screen-canvas-head" },
            previewTitle,
            el("span", { class: "screen-canvas-state", text: "Not live" })),
          previewTiles));

      function isShown(key) {
        return key === "entries" ? entriesInput.checked : exitsInput.checked;
      }

      function currentConfig() {
        return {
          title: title.value,
          show_entries: entriesInput.checked,
          show_exits: exitsInput.checked,
          widget_order: [...widgetOrder]
        };
      }

      function saveScreenDraftState() {
        if (!draftId) return;
        const current = currentConfig();
        screenDraftStates.set(draftId, {
          selectedVersionId,
          loadedConfig: cloneScreenConfig(loadedConfig),
          title: current.title,
          show_entries: current.show_entries,
          show_exits: current.show_exits,
          widget_order: [...current.widget_order]
        });
      }

      function isDirty() {
        return isScreenConfigDirty(currentConfig(), loadedConfig);
      }

      function applyConfig(config) {
        title.value = config.title;
        entriesInput.checked = config.show_entries;
        exitsInput.checked = config.show_exits;
        widgetOrder = [...config.widget_order];
        orderStatus.textContent = "";
        renderOrderControls();
        renderScreenPreview();
      }

      function moveWidget(index, delta, directionWord, focusKey) {
        const target = index + delta;
        if (target < 0 || target >= widgetOrder.length) return;
        const next = [...widgetOrder];
        const [item] = next.splice(index, 1);
        next.splice(target, 0, item);
        widgetOrder = next;
        saveScreenDraftState();
        renderOrderControls(focusKey);
        renderScreenPreview();
        orderStatus.textContent = `${WIDGET_LABELS[item]} moved ${directionWord}. Board order is ${widgetOrder.map(k => WIDGET_LABELS[k]).join(", then ")}.`;
      }

      function renderOrderControls(focusKey = null) {
        orderList.replaceChildren();
        widgetOrder.forEach((key, index) => {
          const label = WIDGET_LABELS[key];
          const hiddenNote = isShown(key) ? null : el("span", { class: "screen-order-muted", text: "(hidden)" });
          const earlierBtn = gate(el("button", {
            class: "a-btn", type: "button", text: "Move earlier",
            "aria-label": `Move ${label} earlier`,
            "data-testid": `move-earlier-${key}`,
            ...(index === 0 ? { disabled: "" } : {})
          }), "label");
          earlierBtn.addEventListener("click", () => moveWidget(index, -1, "earlier", key));

          const laterBtn = gate(el("button", {
            class: "a-btn", type: "button", text: "Move later",
            "aria-label": `Move ${label} later`,
            "data-testid": `move-later-${key}`,
            ...(index === widgetOrder.length - 1 ? { disabled: "" } : {})
          }), "label");
          laterBtn.addEventListener("click", () => moveWidget(index, 1, "later", key));

          orderList.append(el("li", { class: "screen-order-row", "data-widget": key },
            el("span", { class: "screen-order-name" }, `${index + 1}. ${label}`, hiddenNote),
            el("div", { class: "screen-order-actions" }, earlierBtn, laterBtn)));

          if (focusKey === key) {
            const targetBtn = !earlierBtn.disabled ? earlierBtn : !laterBtn.disabled ? laterBtn : null;
            if (targetBtn) targetBtn.focus();
          }
        });
      }

      function renderScreenPreview() {
        const trimmedTitle = title.value.trim();
        previewTitle.textContent = trimmedTitle || "Untitled board";
        if (trimmedTitle) previewTitle.removeAttribute("data-empty");
        else previewTitle.setAttribute("data-empty", "true");

        previewTiles.replaceChildren();
        const visible = widgetOrder.filter(isShown);
        if (!visible.length) {
          previewTiles.append(el("p", {
            class: "screen-canvas-empty",
            "data-testid": "screen-preview-empty",
            text: "No numbers shown — turn on Show entries or Show exits to preview tiles."
          }));
          return;
        }
        for (const key of visible) {
          previewTiles.append(el("div", { class: "screen-canvas-tile", "data-widget": key, "data-testid": `screen-preview-tile-${key}` },
            el("span", { class: "screen-canvas-tile-label", text: WIDGET_LABELS[key] }),
            el("span", { class: "screen-canvas-tile-value", "data-testid": `screen-preview-value-${key}`, text: "—" }),
            el("span", { class: "screen-canvas-tile-note", text: "No live count" })));
        }
      }

      versionSelect.addEventListener("change", async () => {
        const nextId = versionSelect.value;
        if (nextId === selectedVersionId) return;
        if (busy || confirmingVersionChange) {
          versionSelect.value = selectedVersionId;
          return;
        }
        if (isDirty()) {
          confirmingVersionChange = true;
          let accepted = false;
          try {
            accepted = await window.Athena.askConfirm({
              title: "Discard unsaved board changes?",
              message: "Your edits to the board layout have not been frozen as a new version.",
              keepLabel: "Keep editing",
              confirmLabel: "Discard changes"
            });
          } catch (_) {
            accepted = false;
          } finally {
            confirmingVersionChange = false;
          }
          if (!accepted || busy || !draft || draft.id !== draftId) {
            versionSelect.value = selectedVersionId;
            return;
          }
        }
        const nextScreen = nextId ? savedScreens.find(screen => screen.id === nextId) : null;
        if (nextId && !nextScreen) {
          versionSelect.value = selectedVersionId;
          return;
        }
        const nextConfig = nextScreen ? configFromScreen(nextScreen) : newConfig();
        selectedVersionId = nextId;
        loadedConfig = cloneScreenConfig(nextConfig);
        applyConfig(nextConfig);
        saveScreenDraftState();
      });

      title.addEventListener("input", () => { saveScreenDraftState(); renderScreenPreview(); });
      title.addEventListener("change", saveScreenDraftState);
      entriesInput.addEventListener("change", () => { saveScreenDraftState(); renderOrderControls(); renderScreenPreview(); });
      exitsInput.addEventListener("change", () => { saveScreenDraftState(); renderOrderControls(); renderScreenPreview(); });

      const button = gate(el("button", { class: "a-btn", type: "button", text: "Freeze this board", "data-testid": "create-screen" }), "label");
      button.addEventListener("click", () => guarded(async () => {
        const configuration = currentConfig();
        const result = await api(`/api/operations/${draft.operation_id}/screen-versions`, { method: "POST", json: { configuration } });
        if (draftId) screenDraftStates.delete(draftId);
        await refreshVersions();
        areas.creators.note.textContent = `Board frozen as version ${result.screen_version.version}. Pin it on the draft above to use it.`;
      }, "Freezing the board…", areas.creators));

      renderOrderControls();
      renderScreenPreview();
      body.append(formRow("Start from", versionSelect), versionHelp, formRow("Board title", title), el("div", { class: "op-checks" }, entries, exits), orderSection, previewSection, button);
    }));
  }

  /* ---------- release candidates + T32 diagnosis ---------- */

  const diagnosisPanel = byId("diagnosis-panel");
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function waitForExecution(executionId, { attempts = 90, delayMs = 1000 } = {}) {
    for (let i = 0; i < attempts; i += 1) {
      const body = await api(`/api/operation-executions/${executionId}`);
      const row = body.execution;
      if (!row) throw new Error("The server did not return this run.");
      if (row.status === "succeeded" || row.status === "failed" || row.status === "cancelled") {
        return row;
      }
      await sleep(delayMs);
    }
    throw new Error("This test run is still working. Check again in a moment.");
  }

  function showDiagnosisFromExecution(row) {
    const diag = window.AthenaOperationDiagnosis;
    if (!diag || !diagnosisPanel) return;
    if (!row || row.status !== "succeeded") {
      diagnosisPanel.hidden = false;
      diagnosisPanel.replaceChildren();
      const p = el("p", {
        class: "a-help",
        text: row?.error || "This run did not succeed, so there is no diagnosis yet."
      });
      p.dataset.testid = "diagnosis-body";
      diagnosisPanel.append(p);
      return;
    }
    const block = row.result && row.result.diagnosis;
    diag.renderDiagnosis(diagnosisPanel, block || null);
  }

  async function loadLatestDiagnosis(bundleId) {
    if (!bundleId || !diagnosisPanel) return;
    try {
      const body = await api(`/api/operation-release-bundles/${bundleId}/executions`);
      const rows = body.executions || [];
      const latest = rows.find(r => r.status === "succeeded" && r.result && r.result.diagnosis)
        || rows.find(r => r.status === "succeeded")
        || rows[0];
      if (latest) showDiagnosisFromExecution(latest);
    } catch (_ignore) {
      /* Diagnosis is optional chrome; leave the panel alone on list errors. */
    }
  }

  async function runBundleTest(bundle) {
    if (!draft?.recording?.id) {
      throw new Error("Attach the draft recording before running a test.");
    }
    const queued = await api(`/api/operation-release-bundles/${bundle.id}/executions`, {
      method: "POST",
      json: { recording_id: draft.recording.id }
    });
    const executionId = queued.execution?.id;
    if (!executionId) throw new Error("The server did not queue a test run.");
    areas.release.note.textContent = "Test run queued. Waiting for the worker…";
    const finished = await waitForExecution(executionId);
    if (finished.status !== "succeeded") {
      throw new Error(finished.error || `Test run ended as ${finished.status}.`);
    }
    showDiagnosisFromExecution(finished);
    const view = window.AthenaOperationDiagnosis?.formatDiagnosis(finished.result?.diagnosis);
    if (view?.recommend_training) {
      areas.release.note.textContent = "Test finished. Diagnosis says recognition needs work — capture examples before training.";
    } else {
      areas.release.note.textContent = "Test finished. Diagnosis says recognition does not need training from this result.";
    }
  }

  async function renderBundleArea() {
    const box = byId("release-area");
    box.replaceChildren();
    if (!draft || !draft.operation_id) return;
    let bundles = [];
    try {
      bundles = (await api(`/api/operation-drafts/${draft.id}/release-bundles`)).release_bundles;
    } catch (problem) {
      areas.release.err.textContent = problem.message;
      return;
    }
    // A source-view pin rides along with the counting-line pin: a frozen line is only
    // valid with the exact camera view it was created against.
    const pinned = {
      source_view: draft.pins.geometry_version_id,
      geometry: draft.pins.geometry_version_id,
      recipe: draft.pins.recipe_version_id,
      policy: draft.pins.policy_version_id,
      model: draft.pins.model_binding_id,
      screen: draft.pins.screen_version_id
    };
    const missing = Object.entries(pinned).filter(([, id]) => !id).map(([name]) => PIN_HUMAN[name]);
    const list = el("ul", { class: "op-rows" });
    if (!bundles.length) {
      list.append(el("li", { class: "a-help", text: "No release candidates yet." }));
    } else {
      for (const bundle of bundles) {
        const run = gate(el("button", {
          class: "a-btn",
          type: "button",
          text: "Run test on draft recording",
          "data-testid": "run-bundle-test",
          "data-bundle-id": bundle.id
        }), "label");
        if (run.tagName === "BUTTON") {
          run.addEventListener("click", () => guarded(
            () => runBundleTest(bundle),
            "Starting test run…",
            areas.release
          ));
        }
        list.append(el("li", { class: "op-row" },
          el("div", { class: "op-row-main" },
            el("strong", { text: `Release candidate ${bundle.number}` }),
            el("span", { class: "a-help", text: `Assembled from draft revision ${bundle.draft_revision}. Run a test to see whether misses are source, geometry, rules or recognition — correct recognition never starts training.` }),
            run),
          el("span", { class: "a-tag", text: "Candidate" })));
      }
      // Show the latest diagnosis for the newest bundle without another click.
      loadLatestDiagnosis(bundles[0].id);
    }
    const assemble = missing.length
      ? el("p", { class: "a-help", text: `Pin every part of the operation before assembling a release candidate (still missing: ${missing.join(", ")}).` })
      : gate(el("button", { class: "a-btn a-btn--primary", type: "button", text: "Assemble a release candidate", "data-testid": "assemble-bundle" }), "approve");
    if (assemble.tagName === "BUTTON") {
      assemble.addEventListener("click", () => guarded(async () => {
        if (!draft.recording) throw new Error("Attach a recording to the draft before assembling a release candidate.");
        const result = await api(`/api/operation-drafts/${draft.id}/release-bundles`, { method: "POST" });
        areas.release.note.textContent = `Release candidate ${result.release_bundle.number} assembled from draft revision ${result.release_bundle.draft_revision}.`;
        await renderBundleArea();
        document.dispatchEvent(new CustomEvent("athena:bundle-assembled", { detail: result.release_bundle }));
      }, "Assembling…", areas.release));
    }
    box.append(
      el("p", { class: "a-help", text: "A release candidate bundles every frozen choice and the recording into one sealed record. Assembling one needs the Approver role and never changes the draft. A test run diagnoses misses so correct recognition does not push you into unnecessary training." }),
      list, assemble);
  }

  /* ---------- draft wiring ---------- */

  function setDraft(next) {
    draft = next;
    for (const node of DRAFT_SECTIONS) if (node) node.hidden = !draft;
    if (draft && draft.operation_id) {
      loadOperationVersions(draft.operation_id)
        .then(() => { renderAttach(); renderPins(); renderCreators(); })
        .catch(problem => { areas.setup.err.textContent = problem.message; });
    } else {
      renderAttach();
      renderPins();
      renderCreators();
    }
  }

  /* The drafts editor (operation-drafts.js) owns the draft form and list; this script
     listens for its selection changes so the setup area always shows the same draft.
     Pin and attach saves change the draft too, so their results are announced the
     same way for anything watching (the guided setup re-reads state from them). */
  document.addEventListener("athena:draft-shown", event => setDraft(event.detail));
  document.addEventListener("athena:draft-updated", event => setDraft(event.detail));
  const announceDraft = draft => document.dispatchEvent(new CustomEvent("athena:draft-updated", { detail: draft }));

  window.addEventListener("beforeunload", event => {
    if (isAnyScreenDraftDirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  wireScopeForms();
  loadScope().catch(problem => { areas.setup.err.textContent = problem.message; });
})();
