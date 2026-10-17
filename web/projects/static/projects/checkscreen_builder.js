/* "Set up your check screen": configure an inference app and preview it from the server's own render model. */
(function () {
  "use strict";
  const CS = window.CheckScreen;
  const { el, byId } = CS;
  const root = byId("cs-root");
  const projectId = root.dataset.projectId;
  const appsPath = `/api/projects/${projectId}/inference/apps`;

  const f = {
    form: byId("cs-form"), appSelect: byId("cs-app-select"), appLinkRow: byId("cs-app-link-row"), appLink: byId("cs-app-link"),
    model: byId("cs-model"), name: byId("cs-name"), slug: byId("cs-slug"), slugField: byId("cs-slug-field"),
    labels: byId("cs-labels"), types: byId("cs-types"),
    threshold: byId("cs-threshold"), thresholdOut: byId("cs-threshold-out"),
    sayClear: byId("cs-say-clear"), sayFlagged: byId("cs-say-flagged"), sayReview: byId("cs-say-review"),
    decisions: byId("cs-decisions"), noteRequired: byId("cs-note-required"), human: byId("cs-human"),
    save: byId("cs-save"), saveMsg: byId("cs-save-msg"), unsaved: byId("cs-unsaved"),
    active: byId("cs-active"), activeRow: byId("cs-active-row")
  };
  const pageAlert = { box: "cs-page-alert", title: "cs-page-alert-title", text: "cs-page-alert-text" };
  const saveAlert = { box: "cs-save-error", title: "cs-save-error-title", text: "cs-save-error-text" };
  const tryAlert = { box: "cs-try-error", title: "cs-try-error-title", text: "cs-try-error-text", details: "cs-try-error-details", detailText: "cs-try-error-detail-text" };

  const DEFAULT_WORDS = { clear: "Nothing found", flagged: "Something found", needs_review: "Please look closely" };
  const ALL_TYPES = ["image/jpeg", "image/png", "image/webp"];
  const ALL_DECISIONS = ["confirm", "reject", "needs_second_look"];

  let models = [];        // every model in this project
  let apps = [];          // saved screens
  let currentApp = null;  // null = a new screen
  let preview = null;     // the last render model fetched from GET .../preview
  let tryController = null;

  const approved = () => models.filter((m) => m.stage === "approved");
  const modelById = (id) => models.find((m) => m.id === id);
  const setMsg = (node, text, tone) => { node.textContent = text; node.dataset.tone = tone || ""; };

  /* -- building the form ------------------------------------------------------------------ */
  function checkbox(container, name, value, labelText, checked, helpText) {
    const label = el("label", "cs-check");
    const input = el("input");
    input.type = "checkbox"; input.name = name; input.value = value; input.checked = checked;
    const text = el("span");
    text.appendChild(el("strong", "", labelText));
    if (helpText) text.appendChild(el("span", "cs-help cs-block", helpText));
    label.append(input, text);
    container.appendChild(label);
    return input;
  }
  const checked = (container) => [...container.querySelectorAll("input:checked")].map((i) => i.value);

  function drawLabels(names, selected) {
    CS.clear(f.labels);
    names.forEach((name) => checkbox(f.labels, "label", name, name, selected.includes(name)));
  }
  function drawTypes(selected) {
    CS.clear(f.types);
    ALL_TYPES.forEach((t) => checkbox(f.types, "type", t, CS.TYPE_NAMES[t], selected.includes(t)));
  }
  function drawDecisions(selected) {
    CS.clear(f.decisions);
    ALL_DECISIONS.forEach((d) => checkbox(f.decisions, "decision", d, CS.DECISIONS[d].label, selected.includes(d), CS.DECISIONS[d].help));
  }
  function drawModels(selectedId) {
    CS.clear(f.model);
    const list = approved();
    list.forEach((m) => {
      const o = el("option", "", `Version ${m.version}: finds ${m.label_names.join(", ")}`);
      o.value = m.id; f.model.appendChild(o);
    });
    // A screen can still point at a model that is no longer approved; show it so it is not silently swapped.
    if (selectedId && !list.some((m) => m.id === selectedId)) {
      const old = modelById(selectedId);
      if (old) {
        const o = el("option", "", `Version ${old.version}: finds ${old.label_names.join(", ")} (no longer approved)`);
        o.value = old.id; f.model.appendChild(o);
      }
    }
    if (!f.model.options.length) {
      const o = el("option", "", "No approved model yet");
      o.value = ""; f.model.appendChild(o);
    }
    if (selectedId && [...f.model.options].some((o) => o.value === selectedId)) f.model.value = selectedId;
  }
  function modelLabels() {
    const m = modelById(f.model.value);
    return m ? m.label_names : [];
  }

  function updateThresholdText() {
    const t = Number(f.threshold.value) / 100;
    const words = CS.thresholdWords(t);
    f.thresholdOut.textContent = `${t.toFixed(2)} (${Math.round(t * 100)}%). ${words}.`;
    f.threshold.setAttribute("aria-valuetext", `${Math.round(t * 100)} percent. ${words}`);
  }

  function fillForm(app) {
    const cfg = app ? app.config : null;
    const appModelId = app ? app.model_version.id : null;
    // an app whose model was retired starts on the model that is approved now, so saving repairs it
    const list = approved();
    let modelId = appModelId;
    let retired = false;
    if (app && !list.some((m) => m.id === appModelId)) {
      retired = true;
      modelId = list.length ? list[0].id : appModelId;
    }
    if (!app) modelId = list.length ? list[0].id : null;
    drawModels(modelId);
    f.name.value = app ? app.name : "";
    f.slug.value = "";
    f.slugField.hidden = Boolean(app);
    f.activeRow.hidden = !app;
    f.active.checked = app ? app.status === "active" : true;
    const names = modelLabels();
    const keepLabels = cfg ? cfg.displayed_labels.filter((l) => names.includes(l)) : names;
    drawLabels(names, keepLabels.length ? keepLabels : names);
    drawTypes(cfg ? cfg.accepted_inputs : ALL_TYPES);
    f.threshold.value = String(Math.round((cfg ? cfg.confidence_threshold : 0.5) * 100));
    const words = cfg ? cfg.status_texts : DEFAULT_WORDS;
    f.sayClear.value = words.clear; f.sayFlagged.value = words.flagged; f.sayReview.value = words.needs_review;
    drawDecisions(cfg ? cfg.decision_controls.options : ALL_DECISIONS);
    f.noteRequired.checked = cfg ? cfg.decision_controls.required_note_on_override : true;
    f.human.checked = cfg ? cfg.human_review_required : true;
    updateThresholdText();
    f.unsaved.hidden = true;
    touched = false;
    CS.hideAlert(saveAlert);
    setMsg(f.saveMsg, "");
    f.save.textContent = app ? "Save changes" : "Create this screen";
    if (retired) {
      CS.showAlert(pageAlert, {
        title: "This screen’s model is no longer approved",
        text: list.length
          ? "Nobody can check pictures with it until you save it with the model that is approved now. The form already shows that model; check it and save."
          : "No model is approved right now, so this screen cannot check pictures. A model has to be approved first, in the training area."
      });
      markDirty();
    } else {
      CS.hideAlert(pageAlert);
    }
    if (!list.length) {
      CS.showAlert(pageAlert, {
        title: "No model is approved yet",
        text: "A check screen needs a model that a person has approved. Train a model, read how it did on the test pictures, and approve it. Then come back here."
      });
      f.save.disabled = true;
    } else {
      f.save.disabled = false;
    }
  }

  function drawAppSelect(selectedId) {
    CS.clear(f.appSelect);
    const fresh = el("option", "", "A new check screen");
    fresh.value = "new";
    f.appSelect.appendChild(fresh);
    apps.forEach((a) => {
      const o = el("option", "", `${a.name}${a.status === "archived" ? " (switched off)" : ""}`);
      o.value = a.id; f.appSelect.appendChild(o);
    });
    f.appSelect.value = selectedId && apps.some((a) => a.id === selectedId) ? selectedId : "new";
  }

  /* -- reading the form ------------------------------------------------------------------- */
  function problem(message, field) { return { message, field }; }

  function collect() {
    const name = f.name.value.trim();
    if (!name) return problem("Give the screen a title.", f.name);
    if (!f.model.value) return problem("Choose which approved model should check the pictures.", f.model);
    const labels = checked(f.labels);
    if (!labels.length) return problem("Tick at least one thing for the screen to look for.", f.labels.querySelector("input") || f.labels);
    const types = checked(f.types);
    if (!types.length) return problem("Tick at least one kind of picture people can upload.", f.types.querySelector("input"));
    const words = { clear: f.sayClear.value.trim(), flagged: f.sayFlagged.value.trim(), needs_review: f.sayReview.value.trim() };
    if (!words.clear) return problem("Write what the screen should say when nothing was found.", f.sayClear);
    if (!words.flagged) return problem("Write what the screen should say when something was found.", f.sayFlagged);
    if (!words.needs_review) return problem("Write what the screen should say when a person should look closely.", f.sayReview);
    const options = checked(f.decisions);
    if (!options.length) return problem("Tick at least one decision button for the person checking.", f.decisions.querySelector("input"));
    if (f.human.checked && !options.includes("confirm") && !options.includes("reject")) {
      return problem("A person must always decide, so they need “Confirm” or “Reject” (or both) to record what they decided.", f.decisions.querySelector("input"));
    }
    const slug = f.slug.value.trim();
    if (!currentApp && slug && !/^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$/.test(slug)) {
      return problem("The web address name can only use lower-case letters, numbers and dashes, and must be at least 2 characters.", f.slug);
    }
    const config = {
      task_title: name,
      accepted_inputs: types,
      displayed_labels: labels,
      confidence_threshold: Number(f.threshold.value) / 100,
      status_texts: words,
      decision_controls: { options, required_note_on_override: f.noteRequired.checked },
      human_review_required: f.human.checked
    };
    return { name, slug, config, model: f.model.value };
  }

  /* -- saving ----------------------------------------------------------------------------- */
  async function onSave(event) {
    event.preventDefault();
    if (!Athena.can("train")) { CS.showAlert(saveAlert, { title: "Not saved", text: Athena.reasonFor("train") }); return; }
    CS.hideAlert(saveAlert);
    setMsg(f.saveMsg, "");
    const data = collect();
    if (data.message) {
      CS.showAlert(saveAlert, { title: "Not saved yet", text: data.message });
      data.field.focus();
      return;
    }
    f.save.disabled = true;
    let result;
    if (currentApp) {
      const body = { name: data.name, config: data.config };
      if (data.model !== currentApp.model_version.id) body.model_version_id = data.model;
      const wanted = f.active.checked ? "active" : "archived";
      if (wanted !== currentApp.status) body.status = wanted;
      result = await CS.request(`${appsPath}/${currentApp.id}`, { method: "PATCH", json: body });
    } else {
      const body = { name: data.name, model_version_id: data.model, config: data.config };
      if (data.slug) body.slug = data.slug;
      result = await CS.request(appsPath, { method: "POST", json: body });
    }
    f.save.disabled = false;
    if (!result.ok) {
      CS.showAlert(saveAlert, CS.explain(result, "save this screen"));
      return;
    }
    const saved = result.body.app;
    const creating = !currentApp;
    await loadApps(saved.id);
    await selectApp(saved.id, true);
    CS.hideAlert(pageAlert);
    setMsg(f.saveMsg, creating ? "Created. People can open it at the web address shown above." : "Saved. The preview now shows the saved screen.", "ok");
  }

  /* -- preview (the server's own render model) -------------------------------------------- */
  async function loadPreview(app) {
    const got = await CS.request(`${appsPath}/${app.id}/preview`);
    if (!got.ok) {
      CS.showAlert(pageAlert, CS.explain(got, "load the preview"));
      return;
    }
    preview = got.body.preview;
    paintPreview(preview);
  }

  function paintPreview(p) {
    byId("cs-preview").hidden = false;
    byId("cs-preview-source").textContent = `This is exactly what people will see on “${p.app.slug}”, read from the server for the saved screen.`;
    byId("cs-pv-title").textContent = p.title;
    byId("cs-pv-disclaimer").textContent = p.disclaimer;
    byId("cs-pv-labels").textContent = p.displayed_labels.join(", ");
    byId("cs-pv-types").textContent = CS.typeList(p.accepted_inputs);
    byId("cs-pv-threshold").textContent = `${CS.percent(p.confidence_threshold)} sure or more (${p.confidence_threshold.toFixed(2)})`;
    byId("cs-pv-model").textContent = `Version ${p.model.version}${p.model_in_use ? "" : ` (${p.model.stage_now}: no longer approved, so this screen will not run)`}`;
    byId("cs-pv-human").textContent = p.human_review_required ? "Yes, always" : "No";
    const says = byId("cs-pv-says");
    CS.clear(says);
    const names = { clear: "Nothing found", flagged: "Something found", needs_review: "Look closely" };
    const person = p.human_review_required;
    const when = {
      clear: person ? "Appears only after a person confirms a result where nothing was found." : "Appears when nothing was found.",
      flagged: person ? "Appears only after a person confirms a result where something was found." : "Appears when something was found.",
      needs_review: person ? "Appears first, before a person has decided, and again after a reject or a second look." : "Appears after a person rejects a result or asks for a second look."
    };
    p.examples.forEach((ex) => {
      const li = el("li", "cs-say");
      li.dataset.state = ex.state;
      li.appendChild(el("span", "cs-status-icon", CS.stateIcon[ex.state]));
      const text = el("span");
      text.appendChild(el("strong", "", `“${ex.status_text}”`));
      text.appendChild(el("span", "cs-help cs-block", `${names[ex.state]}. ${when[ex.state]} (Example wording, not a real result.)`));
      li.appendChild(text);
      says.appendChild(li);
    });
    const buttons = byId("cs-pv-buttons");
    CS.clear(buttons);
    p.decision_controls.options.forEach((key) => {
      const b = el("button", key === "confirm" ? "primary cs-decide" : "secondary cs-decide");
      b.type = "button"; b.disabled = true;
      b.appendChild(el("span", "cs-decide-label", CS.DECISIONS[key].label));
      b.appendChild(el("span", "cs-decide-help", CS.DECISIONS[key].help));
      buttons.appendChild(b);
    });
    byId("cs-pv-buttons-help").textContent = `These buttons only work on the real screen, after a picture has been checked. ${p.decision_controls.required_note_on_override ? "A note is required when the checker disagrees." : "A note is optional when the checker disagrees."}`;
  }

  /* -- trying a picture ------------------------------------------------------------------- */
  async function onTry() {
    if (!Athena.can("label")) return;
    const status = byId("cs-try-msg");
    setMsg(status, "");
    CS.hideAlert(tryAlert);
    byId("cs-try-result").hidden = true;
    if (!currentApp || !preview) { setMsg(status, "Save the screen first, then try a picture.", "error"); return; }
    const file = byId("cs-try-file").files[0];
    const problemText = CS.checkFile(file, preview);
    if (problemText) { setMsg(status, problemText, "error"); byId("cs-try-file").focus(); return; }
    if (tryController) tryController.abort();
    tryController = new AbortController();
    const run = byId("cs-try-run");
    run.disabled = true;
    const progress = byId("cs-try-progress");
    progress.hidden = false;
    byId("cs-try-progress-text").textContent = "Sending your picture…";
    const paths = CS.paths(projectId, currentApp.id);
    const hooks = {
      onStatus(info) {
        const text = info.status === "queued"
          ? (info.workerOnline === false && info.elapsed >= 5 ? "Still waiting. The checking program may not be running." : "Your picture is in line to be checked…")
          : "The computer is looking at your picture…";
        const node = byId("cs-try-progress-text");
        if (node.textContent !== text) node.textContent = text;
      }
    };
    const outcome = await CS.runImage(paths, file, hooks, tryController.signal);
    progress.hidden = true;
    run.disabled = false;
    if (outcome.aborted) return;
    if (outcome.timedOut) { setMsg(status, "This is taking longer than usual. The picture is still waiting; try again in a moment.", "error"); return; }
    if (!outcome.ok) { CS.showAlert(tryAlert, outcome.error); return; }
    showTryResult(outcome.render, paths);
  }

  function showTryResult(render, paths) {
    const result = render.result;
    byId("cs-try-result").hidden = false;
    const box = byId("cs-try-status");
    box.dataset.state = result.state;
    byId("cs-try-status-icon").textContent = CS.stateIcon[result.state];
    byId("cs-try-status-text").textContent = result.status_text;
    byId("cs-try-status-detail").textContent = CS.machineSentence(result, render);
    const holder = byId("cs-try-stage");
    CS.clear(holder);
    const stage = CS.buildStage(paths.image(result.observation_id), "The picture you tried");
    holder.appendChild(stage.stage);
    const list = byId("cs-try-detections");
    CS.clear(list);
    result.detections.forEach((det, i) => {
      stage.addDetection(det, i + 1);
      list.appendChild(CS.detectionRow(det, i + 1));
    });
    byId("cs-try-note").textContent = `${result.detections.length ? "" : "Nothing was found above the sureness setting. "}This try is saved as a record. The checking buttons and feedback are only on the real screen.`;
    CS.scrollTo(byId("cs-try-result"));
  }

  /* -- choosing which screen -------------------------------------------------------------- */
  async function loadApps(wantedId) {
    const got = await CS.request(appsPath);
    if (!got.ok) { CS.showAlert(pageAlert, CS.explain(got, "load your screens")); apps = []; }
    else apps = got.body.apps;
    drawAppSelect(wantedId);
  }

  async function selectApp(id, keepAlerts) {
    currentApp = id === "new" ? null : apps.find((a) => a.id === id) || null;
    if (!keepAlerts) { CS.hideAlert(saveAlert); CS.hideAlert(pageAlert); }
    fillForm(currentApp);
    byId("cs-try-result").hidden = true;
    CS.hideAlert(tryAlert);
    if (currentApp) {
      f.appLinkRow.hidden = false;
      f.appLink.href = `/check/${currentApp.slug}`;
      f.appLink.textContent = `${window.location.origin}/check/${currentApp.slug}`;
      await loadPreview(currentApp);
    } else {
      f.appLinkRow.hidden = true;
      preview = null;
      byId("cs-preview").hidden = true;
      byId("cs-preview-source").textContent = "Save the screen to see its preview.";
    }
  }

  let touched = false;   // true from the first edit until the screen is saved or another one is loaded
  function markDirty() { touched = true; if (currentApp) f.unsaved.hidden = false; }
  // Same protection as the labels in the console: leaving the page with unsaved edits asks first.
  window.addEventListener("beforeunload", (event) => { if (touched) { event.preventDefault(); event.returnValue = ""; } });

  /* -- wiring ----------------------------------------------------------------------------- */
  f.form.addEventListener("submit", onSave);
  f.form.addEventListener("input", () => { setMsg(f.saveMsg, ""); markDirty(); });
  f.form.addEventListener("change", markDirty);
  f.threshold.addEventListener("input", updateThresholdText);
  f.appSelect.addEventListener("change", () => selectApp(f.appSelect.value, false));
  f.model.addEventListener("change", () => {
    const names = modelLabels();
    const prior = checked(f.labels).filter((n) => names.includes(n));
    drawLabels(names, prior.length ? prior : names);
    markDirty();
  });
  f.human.addEventListener("change", async () => {
    if (f.human.checked) return;
    f.human.checked = true;            // stays on until the person confirms
    const turnOff = await Athena.askConfirm({
      title: "Let the screen decide without a person?",
      message: "If you turn this off, the screen can say “nothing found” on its own, and nobody has to look at the picture. The computer will sometimes be wrong, and a missed problem may go unnoticed. Only do this if a person double-checks in another way.",
      keepLabel: "Keep a person deciding",
      confirmLabel: "Yes, turn it off"
    });
    if (turnOff) { f.human.checked = false; markDirty(); }
    f.human.focus();
  });
  byId("cs-try-run").addEventListener("click", onTry);

  (async function init() {
    const [m, a] = await Promise.all([CS.request(`/api/projects/${projectId}/models`), CS.request(appsPath)]);
    if (!m.ok) { CS.showAlert(pageAlert, CS.explain(m, "load the models")); return; }
    models = m.body.models;
    apps = a.ok ? a.body.apps : [];
    if (!a.ok) CS.showAlert(pageAlert, CS.explain(a, "load your screens"));
    const wanted = root.dataset.initialApp || (apps.length ? apps[0].id : "new");
    drawAppSelect(wanted);
    await selectApp(f.appSelect.value, true);
  })();
})();
