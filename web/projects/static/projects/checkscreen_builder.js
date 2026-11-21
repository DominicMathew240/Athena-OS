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
    suggestText: byId("cs-suggest-text"), suggestUse: byId("cs-suggest-use"), suggestMsg: byId("cs-suggest-msg"),
    sayClear: byId("cs-say-clear"), sayFlagged: byId("cs-say-flagged"), sayReview: byId("cs-say-review"),
    decisions: byId("cs-decisions"), noteRequired: byId("cs-note-required"), human: byId("cs-human"),
    save: byId("cs-save"), saveMsg: byId("cs-save-msg"), unsaved: byId("cs-unsaved"),
    active: byId("cs-active"), activeRow: byId("cs-active-row")
  };
  const pageAlert = { box: "cs-page-alert", title: "cs-page-alert-title", text: "cs-page-alert-text" };
  const saveAlert = { box: "cs-save-error", title: "cs-save-error-title", text: "cs-save-error-text" };
  const tryAlert = { box: "cs-try-error", title: "cs-try-error-title", text: "cs-try-error-text", details: "cs-try-error-details", detailText: "cs-try-error-detail-text" };
  const wf = {
    root: byId("cs-workflow"),
    badge: byId("cs-wf-badge"),
    stages: {
      input: { stage: byId("cs-wf-stage-input"), vals: byId("cs-wf-vals-input") },
      model: { stage: byId("cs-wf-stage-model"), vals: byId("cs-wf-vals-model") },
      findings: { stage: byId("cs-wf-stage-findings"), vals: byId("cs-wf-vals-findings") },
      decision: { stage: byId("cs-wf-stage-decision"), vals: byId("cs-wf-vals-decision") }
    }
  };

  const DEFAULT_WORDS = { clear: "Nothing found", flagged: "Something found", needs_review: "Please look closely" };
  const ALL_TYPES = ["image/jpeg", "image/png", "image/webp"];
  const ALL_DECISIONS = ["confirm", "reject", "needs_second_look"];

  let models = [];        // every model in this project
  let apps = [];          // saved screens
  let currentApp = null;  // null = a new screen
  let preview = null;     // the last render model fetched from GET .../preview
  let tryController = null;
  let previewController = null;
  let screenSeq = 0;
  let trySeq = 0;
  let saving = false;
  let confirmingSwitch = false;

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
      const what = m.task === "handwriting-ocr"
        ? `reads handwriting (${m.label_names.join(", ")})`
        : `finds ${m.label_names.join(", ")}`;
      const o = el("option", "", `Version ${m.version}: ${what}`);
      o.value = m.id; f.model.appendChild(o);
    });
    // A screen can still point at a model that is no longer approved; show it so it is not silently swapped.
    if (selectedId && !list.some((m) => m.id === selectedId)) {
      const old = modelById(selectedId);
      if (old) {
        const what = old.task === "handwriting-ocr"
          ? `reads handwriting (${old.label_names.join(", ")})`
          : `finds ${old.label_names.join(", ")}`;
        const o = el("option", "", `Version ${old.version}: ${what} (no longer approved)`);
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
  const isTextModel = (id) => {
    const m = modelById(id || f.model.value);
    return Boolean(m && m.task === "handwriting-ocr");
  };

  function updateThresholdText() {
    const t = Number(f.threshold.value) / 100;
    const words = CS.thresholdWords(t);
    f.thresholdOut.textContent = `${Math.round(t * 100)}%. ${words}.`;
    f.threshold.setAttribute("aria-valuetext", `${Math.round(t * 100)} percent. ${words}`);
  }

  /* A hint from the model's own checking results, shown beside the slider. It never moves the slider by itself. */
  function suggestedFor(modelId) {
    if (!currentApp || !preview) return { text: "Save the screen first. A suggested setting then appears here, worked out from how the model did on its Checking pictures.", value: null };
    if (modelId && preview.model && preview.model.id !== modelId) {
      return { text: "You chose a different model than the one saved. Save the screen, and a suggested setting for that model appears here.", value: null };
    }
    const hint = preview.suggested_threshold;
    if (!hint || typeof hint.plain !== "string") return { text: "No suggested setting is available for this model.", value: null };
    const usable = typeof hint.value === "number" && hint.value >= 0 && hint.value <= 1 && hint.enough_data === true;
    return { text: hint.plain, value: usable ? hint.value : null };
  }
  let suggestion = null;
  function drawSuggestion() {
    suggestion = suggestedFor(f.model.value);
    f.suggestText.textContent = suggestion.text;
    f.suggestUse.hidden = suggestion.value === null;
    setMsg(f.suggestMsg, "");
  }
  function useSuggestion() {
    if (!suggestion || suggestion.value === null) return;
    const percent = Math.floor(suggestion.value * 100 + 1e-6);
    f.threshold.value = String(percent);
    updateThresholdText();
    f.threshold.dispatchEvent(new Event("input", { bubbles: true })); // marks the form as changed; nothing is saved
    const exact = Math.abs(percent - suggestion.value * 100) < 1e-6;
    setMsg(f.suggestMsg, `The setting is now ${(percent / 100).toFixed(2)}${exact ? "" : " (the slider moves in whole percents)"}. It is not saved until you press the save button.`, "ok");
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
    drawSuggestion();
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
    setEditingStage(null);
    renderWorkflow();
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
      human_review_required: f.human.checked,
      output: isTextModel(f.model.value) ? "text" : "boxes"
    };
    return { name, slug, config, model: f.model.value };
  }

  /* -- saving ----------------------------------------------------------------------------- */
  async function onSave(event) {
    event.preventDefault();
    if (saving) return;
    if (!Athena.can("train")) { CS.showAlert(saveAlert, { title: "Not saved", text: Athena.reasonFor("train") }); return; }
    CS.hideAlert(saveAlert);
    setMsg(f.saveMsg, "");
    const data = collect();
    if (data.message) {
      CS.showAlert(saveAlert, { title: "Not saved yet", text: data.message });
      data.field.focus();
      return;
    }
    const targetApp = currentApp;
    const creating = !targetApp;
    const controls = [...f.form.querySelectorAll("input, select, button, textarea")];
    const disabledStates = controls.map((control) => [control, control.disabled]);
    saving = true;
    controls.forEach((control) => { control.disabled = true; });
    try {
      let result;
      if (targetApp) {
        const body = { name: data.name, config: data.config };
        if (data.model !== targetApp.model_version.id) body.model_version_id = data.model;
        const wanted = f.active.checked ? "active" : "archived";
        if (wanted !== targetApp.status) body.status = wanted;
        result = await CS.request(`${appsPath}/${targetApp.id}`, { method: "PATCH", json: body });
      } else {
        const body = { name: data.name, model_version_id: data.model, config: data.config };
        if (data.slug) body.slug = data.slug;
        result = await CS.request(appsPath, { method: "POST", json: body });
      }
      if (!result.ok) {
        CS.showAlert(saveAlert, CS.explain(result, "save this screen"));
        return;
      }
      const saved = result.body.app;
      const idx = apps.findIndex((a) => a.id === saved.id);
      if (idx >= 0) apps[idx] = saved;
      else apps.push(saved);
      const refreshed = await loadApps(saved.id);
      if (!refreshed) drawAppSelect(saved.id);
      await selectApp(saved.id, true);
      if (!refreshed) {
        CS.showAlert(pageAlert, {
          title: "Saved",
          text: "Screen saved, but the screen list could not be refreshed. Reload this page."
        });
        setMsg(
          f.saveMsg,
          "Screen saved, but the screen list could not be refreshed. Reload this page.",
          "ok"
        );
      } else {
        if (preview !== null) CS.hideAlert(pageAlert);
        setMsg(
          f.saveMsg,
          preview === null
            ? "Saved, but preview could not be loaded. Reload this page."
            : (creating ? "Created. People can open it at the web address shown above." : "Saved. The preview now shows the saved screen."),
          "ok"
        );
      }
    } catch (error) {
      CS.showAlert(saveAlert, { title: "That did not work", text: "Could not save this screen. Try again." });
    } finally {
      saving = false;
      disabledStates.forEach(([control, wasDisabled]) => { control.disabled = wasDisabled; });
    }
  }

  /* -- preview (the server's own render model) -------------------------------------------- */
  async function loadPreview(app, seq, signal) {
    let got;
    try {
      got = await CS.request(`${appsPath}/${app.id}/preview`, { signal });
    } catch (error) {
      if (seq !== screenSeq || (signal && signal.aborted)) return;
      byId("cs-preview").hidden = true;
      byId("cs-preview-source").textContent = "Preview could not be loaded.";
      CS.showAlert(pageAlert, { title: "That did not work", text: "Could not load the preview. Try again in a moment." });
      return;
    } finally {
      if (previewController && previewController.signal === signal) previewController = null;
    }
    if (seq !== screenSeq || got.aborted || (signal && signal.aborted)) return;
    if (!got.ok) {
      byId("cs-preview").hidden = true;
      byId("cs-preview-source").textContent = "Preview could not be loaded.";
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
    const isText = p.output === "text";
    byId("cs-pv-labels").textContent = isText
      ? "Reads the handwriting in each picture and writes out what it says"
      : p.displayed_labels.join(", ");
    byId("cs-pv-types").textContent = CS.typeList(p.accepted_inputs);
    byId("cs-pv-threshold").textContent = isText
      ? "Not used: a reading has no score to compare"
      : `${CS.percent(p.confidence_threshold)} sure or more`;
    byId("cs-pv-model").textContent = `Version ${p.model.version}${p.model_in_use ? "" : ` (${p.model.stage_now}: no longer approved, so this screen will not run)`}`;
    byId("cs-pv-human").textContent = p.human_review_required ? "Yes, always" : "No";
    const wording = p.confidence_wording;
    byId("cs-pv-wording").textContent = isText
      ? "No scores: the reading is shown as the computer’s own, and a person checks it against the picture."
      : (wording && wording.measured === true
        ? `Each score is shown with how often that range was right in our check. ${CS.scoreNote(p)}`
        : `Each score is shown as the computer’s own number, not checked. ${CS.scoreNote(p)}`);
    drawSuggestion();
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
    const controller = new AbortController();
    tryController = controller;
    const myScreenSeq = screenSeq;
    const myTrySeq = ++trySeq;
    const run = byId("cs-try-run");
    run.disabled = true;
    const progress = byId("cs-try-progress");
    progress.hidden = false;
    byId("cs-try-progress-text").textContent = "Sending your picture…";
    const paths = CS.paths(projectId, currentApp.id);
    const isStale = () => myScreenSeq !== screenSeq || myTrySeq !== trySeq || controller.signal.aborted;
    const hooks = {
      onStatus(info) {
        if (isStale()) return;
        const text = info.status === "queued"
          ? (info.workerOnline === false && info.elapsed >= 5 ? "Still waiting. The checking program may not be running." : "Your picture is in line to be checked…")
          : "The computer is looking at your picture…";
        const node = byId("cs-try-progress-text");
        if (node.textContent !== text) node.textContent = text;
      }
    };
    let outcome;
    try {
      outcome = await CS.runImage(paths, file, hooks, controller.signal);
    } catch (error) {
      if (!isStale()) {
        CS.showAlert(tryAlert, { title: "That did not work", text: "Could not check that picture. Try again." });
      }
      return;
    } finally {
      if (tryController === controller) tryController = null;
      if (myScreenSeq === screenSeq && myTrySeq === trySeq) {
        progress.hidden = true;
        run.disabled = !Athena.can("label");
      }
    }
    if (isStale() || outcome.aborted) return;
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
    const isText = render.output === "text";
    if (isText) {
      const answer = result.text_answer;
      const read = byId("cs-try-text");
      read.hidden = false;
      read.textContent = answer && typeof answer.text === "string" && answer.text.trim() ? answer.text : "The computer read no writing in this picture.";
      byId("cs-try-note").textContent = "This try is saved as a record. The decision buttons are only on the real screen.";
      return;
    }
    byId("cs-try-text").hidden = true;
    result.detections.forEach((det, i) => {
      stage.addDetection(det, i + 1);
      list.appendChild(CS.detectionRow(det, i + 1, render));
    });
    byId("cs-try-note").textContent = `${result.detections.length ? `${CS.scoreNote(render)} ` : "Nothing was found above the sureness setting. "}This try is saved as a record. The checking buttons and feedback are only on the real screen.`;
    CS.scrollTo(byId("cs-try-result"));
  }

  /* -- choosing which screen -------------------------------------------------------------- */
  async function loadApps(wantedId) {
    const got = await CS.request(appsPath);
    if (!got.ok) {
      CS.showAlert(pageAlert, CS.explain(got, "load your screens"));
      return false;
    }
    apps = got.body.apps;
    drawAppSelect(wantedId);
    return true;
  }

  async function selectApp(id, keepAlerts) {
    const seq = ++screenSeq;
    if (previewController) {
      previewController.abort();
      previewController = null;
    }
    trySeq += 1;
    if (tryController) {
      tryController.abort();
      tryController = null;
    }
    byId("cs-try-progress").hidden = true;
    byId("cs-try-progress-text").textContent = "";
    byId("cs-try-run").disabled = !Athena.can("label");
    setMsg(byId("cs-try-msg"), "");
    byId("cs-try-result").hidden = true;
    CS.hideAlert(tryAlert);

    currentApp = id === "new" ? null : apps.find((a) => a.id === id) || null;
    f.appSelect.value = currentApp ? currentApp.id : "new";
    preview = null; // the last screen's render model must not feed this screen's suggestion
    byId("cs-preview").hidden = true;
    byId("cs-preview-source").textContent = currentApp ? "Loading preview…" : "Save the screen to see its preview.";
    if (!keepAlerts) { CS.hideAlert(saveAlert); CS.hideAlert(pageAlert); }
    fillForm(currentApp);
    if (currentApp) {
      f.appLinkRow.hidden = false;
      f.appLink.href = `/check/${currentApp.slug}`;
      f.appLink.textContent = `${window.location.origin}/check/${currentApp.slug}`;
      const controller = new AbortController();
      previewController = controller;
      await loadPreview(currentApp, seq, controller.signal);
    } else {
      f.appLinkRow.hidden = true;
    }
  }

  /* -- interactive workflow canvas (live current form values) ----------------------------- */
  let flashTimer = null;
  let flashedNode = null;

  function addWfFact(dl, term, value, tone) {
    const row = el("div", "cs-wf-val");
    if (tone) row.dataset.tone = tone;
    row.appendChild(el("dt", "", term));
    row.appendChild(el("dd", "", value));
    dl.appendChild(row);
  }

  function setWfStage(key, state, rows) {
    const entry = wf.stages[key];
    if (!entry || !entry.stage || !entry.vals) return;
    entry.stage.dataset.state = state;
    CS.clear(entry.vals);
    rows.forEach(([term, value, tone]) => addWfFact(entry.vals, term, value, tone));
  }

  function setEditingStage(activeKey) {
    Object.keys(wf.stages).forEach((key) => {
      const node = wf.stages[key].stage;
      if (!node) return;
      if (key === activeKey) node.dataset.editing = "true";
      else delete node.dataset.editing;
    });
  }

  function stageForControl(node) {
    if (!node || typeof node.closest !== "function") return null;
    if (node.closest("#cs-types") || node.closest("#cs-active-row")) return "input";
    if (node.closest("#cs-model") || node.closest("#cs-name") || node.closest("#cs-slug-field")) return "model";
    if (node.closest("#cs-labels") || node.closest("#cs-threshold") || node.closest("#cs-suggest")) return "findings";
    if (node.closest("#cs-decisions") || node.closest("#cs-note-required") || node.closest("#cs-human") || node.closest("#cs-say-clear") || node.closest("#cs-say-flagged") || node.closest("#cs-say-review")) return "decision";
    return null;
  }

  function renderWorkflow() {
    if (!wf.root) return;
    if (!currentApp) {
      wf.badge.textContent = "Current form · not saved yet";
      wf.badge.dataset.state = "unsaved";
    } else if (touched) {
      wf.badge.textContent = "Unsaved form edits · Preview shows saved settings";
      wf.badge.dataset.state = "unsaved";
    } else {
      wf.badge.textContent = "Current form · matches saved screen";
      wf.badge.dataset.state = "saved";
    }

    const types = checked(f.types);
    const activeOn = !currentApp || f.active.checked;
    setWfStage("input", types.length ? (activeOn ? "ready" : "warn") : "bad", [
      ["Accepts", types.length ? CS.typeList(types) : "None selected — pick at least 1", types.length ? "" : "bad"],
      ["Screen status", currentApp ? (f.active.checked ? "Switched on" : "Switched off") : "Will start switched on", currentApp ? (f.active.checked ? "ok" : "warn") : ""]
    ]);

    const selectedModel = modelById(f.model.value);
    const titleVal = f.name.value.trim();
    if (selectedModel) {
      const isApproved = selectedModel.stage === "approved";
      setWfStage("model", isApproved ? (titleVal ? "ready" : "warn") : "bad", [
        ["Model", `Version ${selectedModel.version} (${isApproved ? "approved" : "not approved"})`, isApproved ? "ok" : "bad"],
        ["Screen title", titleVal || "Not named yet", titleVal ? "" : "warn"],
        ["Finds", selectedModel.label_names.length ? selectedModel.label_names.join(", ") : "None"]
      ]);
    } else {
      setWfStage("model", "bad", [
        ["Model", "No approved model yet", "bad"],
        ["Screen title", titleVal || "Not named yet", titleVal ? "" : "warn"]
      ]);
    }

    const allLabels = modelLabels();
    const selectedLabels = checked(f.labels);
    let labelText = "Waiting for an approved model";
    let labelTone = "warn";
    if (allLabels.length) {
      if (!selectedLabels.length) {
        labelText = "None selected — pick at least 1";
        labelTone = "bad";
      } else {
        const hiddenCount = allLabels.length - selectedLabels.length;
        labelText = hiddenCount > 0 ? `${selectedLabels.join(", ")} (${hiddenCount} hidden)` : selectedLabels.join(", ");
        labelTone = "";
      }
    }
    const pct = Math.round(Number(f.threshold.value));
    const thresholdText = isTextModel() ? "Not used for handwriting" : `${pct}% sure or more`;
    setWfStage("findings", (!allLabels.length || !selectedLabels.length) ? (allLabels.length ? "bad" : "warn") : "ready", [
      ["Show findings", labelText, labelTone],
      ["Sureness threshold", thresholdText]
    ]);

    const humanReq = f.human.checked;
    const options = checked(f.decisions);
    const missingConfirmOrReject = humanReq && !options.includes("confirm") && !options.includes("reject");
    let btnText = options.length
      ? options.map((d) => (CS.DECISIONS[d] ? CS.DECISIONS[d].label : d)).join(", ")
      : "None selected — pick at least 1";
    if (missingConfirmOrReject) btnText += " (needs Confirm or Reject)";
    setWfStage("decision", (!options.length || missingConfirmOrReject) ? "bad" : (humanReq ? "ready" : "warn"), [
      ["Person decides", humanReq ? "Always required" : "Optional (can clear on its own)", humanReq ? "ok" : "warn"],
      ["Buttons", btnText, (!options.length || missingConfirmOrReject) ? "bad" : ""],
      ["Disagree note", f.noteRequired.checked ? "Required" : "Optional"]
    ]);
  }

  function editStage(key) {
    const map = {
      input: {
        section: f.types.closest(".cs-card"),
        flashEl: f.types.closest(".cs-fieldset") || f.types,
        focusEl: () => f.types.querySelector("input") || f.types
      },
      model: {
        section: f.model.closest(".cs-card"),
        flashEl: f.model.closest(".cs-field") || f.model,
        focusEl: () => f.model
      },
      findings: {
        section: f.labels.closest(".cs-card"),
        flashEl: f.labels.closest(".cs-fieldset") || f.labels,
        focusEl: () => f.labels.querySelector("input") || f.threshold
      },
      decision: {
        section: f.human.closest(".cs-card"),
        flashEl: f.human.closest(".cs-card") || f.human,
        focusEl: () => f.human
      }
    };
    const spec = map[key];
    if (!spec) return;
    setEditingStage(key);
    const target = spec.focusEl();
    const section = spec.section;
    const scrollTarget = (section && section.getBoundingClientRect().height < window.innerHeight * 0.75) ? section : (spec.flashEl || target || section);
    if (scrollTarget) CS.scrollTo(scrollTarget);
    if (target && !target.disabled && typeof target.focus === "function") {
      target.focus({ preventScroll: true });
    } else if (section) {
      if (!section.hasAttribute("tabindex")) section.setAttribute("tabindex", "-1");
      section.focus({ preventScroll: true });
    }
    if (flashedNode) flashedNode.classList.remove("cs-wf-flash");
    window.clearTimeout(flashTimer);
    flashedNode = spec.flashEl || section;
    if (flashedNode) {
      flashedNode.classList.add("cs-wf-flash");
      flashTimer = window.setTimeout(() => {
        if (flashedNode) flashedNode.classList.remove("cs-wf-flash");
        flashedNode = null;
      }, 1400);
    }
  }

  let touched = false;   // true from the first edit until the screen is saved or another one is loaded
  function markDirty() { touched = true; if (currentApp) f.unsaved.hidden = false; renderWorkflow(); }
  // Same protection as the labels in the console: leaving the page with unsaved edits asks first.
  window.addEventListener("beforeunload", (event) => { if (touched) { event.preventDefault(); event.returnValue = ""; } });

  /* -- wiring ----------------------------------------------------------------------------- */
  f.form.addEventListener("submit", onSave);
  f.form.addEventListener("input", (event) => { if (event.target === f.appSelect) return; setMsg(f.saveMsg, ""); markDirty(); });
  f.form.addEventListener("change", (event) => { if (event.target === f.appSelect) return; markDirty(); });
  f.form.addEventListener("focusin", (event) => { setEditingStage(stageForControl(event.target)); });
  if (wf.root) {
    wf.root.addEventListener("click", (event) => {
      const btn = event.target instanceof Element ? event.target.closest("[data-wf-edit]") : null;
      if (btn) editStage(btn.dataset.wfEdit);
    });
  }
  f.threshold.addEventListener("input", updateThresholdText);
  f.suggestUse.addEventListener("click", useSuggestion);
  f.appSelect.addEventListener("change", async () => {
    const nextId = f.appSelect.value;
    const prevId = currentApp ? currentApp.id : "new";
    if (saving || confirmingSwitch) {
      f.appSelect.value = prevId;
      return;
    }
    if (nextId === prevId) return;
    if (touched) {
      confirmingSwitch = true;
      let discard = false;
      try {
        discard = await Athena.askConfirm({
          title: "Leave without saving?",
          message: "You have unsaved changes on this screen. If you switch screens now, those changes will be lost.",
          keepLabel: "Stay on this screen",
          confirmLabel: "Discard changes"
        });
      } catch (error) {
        discard = false;
      } finally {
        confirmingSwitch = false;
      }
      if (!discard || saving) {
        f.appSelect.value = currentApp ? currentApp.id : "new";
        return;
      }
    }
    await selectApp(nextId, false);
  });
  f.model.addEventListener("change", () => {
    const names = modelLabels();
    const prior = checked(f.labels).filter((n) => names.includes(n));
    drawLabels(names, prior.length ? prior : names);
    drawSuggestion();
    markDirty();
  });
  f.human.addEventListener("change", async () => {
    if (f.human.checked) return;
    f.human.checked = true;            // stays on until the person confirms
    const seq = screenSeq;
    let turnOff = false;
    try {
      turnOff = await Athena.askConfirm({
        title: "Let the screen decide without a person?",
        message: "If you turn this off, the screen can say “nothing found” on its own, and nobody has to look at the picture. The computer will sometimes be wrong, and a missed problem may go unnoticed. Only do this if a person double-checks in another way.",
        keepLabel: "Keep a person deciding",
        confirmLabel: "Yes, turn it off"
      });
    } catch (error) {
      turnOff = false;
    }
    if (seq !== screenSeq) return;
    if (turnOff) { f.human.checked = false; markDirty(); }
    f.human.focus();
  });
  byId("cs-try-run").addEventListener("click", onTry);

  (async function init() {
    try {
      const [m, a] = await Promise.all([CS.request(`/api/projects/${projectId}/models`), CS.request(appsPath)]);
      if (!m.ok) { CS.showAlert(pageAlert, CS.explain(m, "load the models")); renderWorkflow(); return; }
      models = m.body.models;
      apps = a.ok ? a.body.apps : [];
      if (!a.ok) CS.showAlert(pageAlert, CS.explain(a, "load your screens"));
      const wanted = root.dataset.initialApp || (apps.length ? apps[0].id : "new");
      drawAppSelect(wanted);
      await selectApp(f.appSelect.value, true);
    } catch (error) {
      CS.showAlert(pageAlert, { title: "That did not work", text: "Could not load this page. Reload and try again." });
      renderWorkflow();
    }
  })();
})();
