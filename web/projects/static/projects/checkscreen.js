/* Operator check screen: choose a picture, see what the computer found, record decisions and feedback. */
(function () {
  "use strict";
  const CS = window.CheckScreen;
  const { el, byId } = CS;
  const root = byId("cs-root");
  const paths = CS.paths(root.dataset.projectId, root.dataset.appId);

  const ui = {
    title: byId("cs-title"), blocker: byId("cs-blocker"), disclaimer: byId("cs-disclaimer"),
    file: byId("cs-file"), fileHelp: byId("cs-file-help"), check: byId("cs-check"), pickMsg: byId("cs-pick-msg"),
    progress: byId("cs-progress"), progressText: byId("cs-progress-text"), elapsed: byId("cs-progress-elapsed"), keepWaiting: byId("cs-keep-waiting"),
    live: byId("cs-live"),
    result: byId("cs-result"), status: byId("cs-status"), statusIcon: byId("cs-status-icon"), statusText: byId("cs-status-text"), statusDetail: byId("cs-status-detail"),
    stageHolder: byId("cs-stage-holder"), detList: byId("cs-detections"), detEmpty: byId("cs-det-empty"), hiddenNote: byId("cs-hidden-note"),
    missed: byId("cs-missed"), missedForm: byId("cs-missed-form"), missedLabel: byId("cs-missed-label"), draw: byId("cs-draw"), drawHelp: byId("cs-draw-help"),
    unknown: byId("cs-box-unknown"), bx: byId("cs-box-x"), by: byId("cs-box-y"), bw: byId("cs-box-w"), bh: byId("cs-box-h"),
    missedNote: byId("cs-missed-note"), missedReq: byId("cs-missed-req"), missedMsg: byId("cs-missed-msg"), missedList: byId("cs-missed-list"),
    decision: byId("cs-decision"), decisionHelp: byId("cs-decision-help"), note: byId("cs-note"), noteReq: byId("cs-note-req"),
    decisionButtons: byId("cs-decision-buttons"), decisionMsg: byId("cs-decision-msg"), recorded: byId("cs-recorded"), another: byId("cs-another"),
    modelLine: byId("cs-model-line")
  };
  const errorIds = { box: "cs-error", title: "cs-error-title", text: "cs-error-text", details: "cs-error-details", detailText: "cs-error-detail-text" };

  let config = null;      // the preview render model (settings), refreshed from each result
  let current = null;     // the render model of the picture on screen
  let stage = null;
  let controller = null;
  let blocked = false;
  let missedCount = 0;
  let lastAnnounced = "";

  const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const announce = (text) => { ui.live.textContent = ""; window.setTimeout(() => { ui.live.textContent = text; }, 30); };
  const setMsg = (node, text, tone) => { node.textContent = text; node.dataset.tone = tone || ""; };
  // What happens to an answer, said once and plainly: it goes to a supervisor, and nothing changes by itself.
  const SAVED_FOR_SUPERVISOR = "Saved for your supervisor. It does not change the computer by itself.";
  const WRONG_CANNOT_TEACH = "A wrong-box answer cannot teach the computer anything on its own; drawing a box where something was missed can.";

  /* -- page setup ------------------------------------------------------------------------- */
  function block(title, text) {
    blocked = true;
    byId("cs-blocker-title").textContent = title;
    byId("cs-blocker-text").textContent = text;
    ui.blocker.hidden = false;
    ui.file.disabled = true;
    ui.check.disabled = true;
  }

  function applyConfig(render) {
    config = render;
    ui.title.textContent = render.title;
    document.title = `${render.title} · Athena`;
    ui.disclaimer.textContent = render.disclaimer;
    ui.file.accept = render.accepted_inputs.join(",");
    ui.fileHelp.textContent = `Accepted: ${CS.typeList(render.accepted_inputs)}, up to ${CS.megabytes(render.limits.max_upload_bytes)}. On a phone you can take a new photo.`;
    ui.modelLine.textContent = `Checked with model version ${render.model.version}. ${render.human_review_required ? "A person decides on every result." : "This screen does not require a person to decide."}`;
  }

  async function init() {
    const loaded = await CS.request(paths.preview);
    if (!loaded.ok) {
      const info = CS.explain(loaded, "open this screen");
      block(info.title, info.text);
      return;
    }
    const render = loaded.body.preview;
    applyConfig(render);
    if (render.app.status !== "active") {
      block("This screen is switched off", "It has been archived, so it will not check pictures. Ask the person who set it up to switch it on.");
    } else if (!render.model_in_use) {
      block("The model behind this screen is no longer approved", "Nothing can be checked here until the person who set it up chooses the model that is approved now.");
    }
  }

  /* -- checking a picture ----------------------------------------------------------------- */
  function resetResult() {
    ui.result.hidden = true;
    ui.decision.hidden = true;
    ui.another.hidden = true;
    ui.recorded.hidden = true;
    setMsg(ui.decisionMsg, "");
    setMsg(ui.missedMsg, "");
    CS.clear(ui.stageHolder); CS.clear(ui.detList); CS.clear(ui.missedList); CS.clear(ui.decisionButtons);
    ui.missed.open = false;
    ui.note.value = ""; ui.missedNote.value = "";
    [ui.bx, ui.by, ui.bw, ui.bh].forEach((input) => { input.value = ""; input.disabled = false; });
    ui.unknown.checked = false; ui.draw.disabled = false;
    ui.note.removeAttribute("aria-invalid");
    stage = null; current = null; missedCount = 0; lastAnnounced = ""; warned = false;
    setDrawMode(false);
  }

  function progressText(info) {
    if (info.status === "queued") {
      if (info.workerOnline === false && info.elapsed >= 5) return "Still waiting. The checking program may not be running, so this could take a long time. If nothing happens soon, tell your administrator.";
      return "Your picture is in line to be checked…";
    }
    return "The computer is looking at your picture…";
  }

  async function onCheck() {
    if (!Athena.can("label")) { setMsg(ui.pickMsg, Athena.reasonFor("label"), "error"); return; }
    if (blocked) return;
    setMsg(ui.pickMsg, "");
    CS.hideAlert(errorIds);
    const file = ui.file.files[0];
    const problem = CS.checkFile(file, config);
    if (problem) {
      setMsg(ui.pickMsg, problem, "error");
      ui.file.focus();
      return;
    }
    resetResult();
    if (controller) controller.abort();
    controller = new AbortController();
    ui.check.disabled = true;
    ui.progress.hidden = false;
    ui.keepWaiting.hidden = true;
    ui.progressText.textContent = "Sending your picture…";
    const outcome = await CS.runImage(paths, file, { onStatus: onStatus }, controller.signal);
    await settle(outcome);
  }

  function onStatus(info) {
    const text = progressText(info);
    if (ui.progressText.textContent !== text) ui.progressText.textContent = text;
    ui.elapsed.textContent = `${info.elapsed} s`;
  }

  async function settle(outcome) {
    if (outcome.aborted) return;
    if (outcome.timedOut) {
      ui.progressText.textContent = "This is taking longer than usual. Your picture is still waiting to be checked.";
      ui.keepWaiting.hidden = false;
      ui.keepWaiting.onclick = async () => {
        ui.keepWaiting.hidden = true;
        await settle(await outcome.resume());
      };
      ui.keepWaiting.focus();
      return;
    }
    ui.progress.hidden = true;
    ui.check.disabled = blocked;
    if (!outcome.ok) {
      const info = outcome.error;
      CS.showAlert(errorIds, info);
      if (/no longer approved/i.test(info.title)) block(info.title, info.text);
      announce(`${info.title}. ${info.text}`);
      return;
    }
    showResult(outcome.render);
  }

  /* -- showing a result ------------------------------------------------------------------- */
  function paintStatus(render) {
    const result = render.result;
    ui.status.dataset.state = result.state;
    ui.statusIcon.textContent = CS.stateIcon[result.state] || "";
    ui.statusText.textContent = result.status_text;
    const parts = [CS.machineSentence(result, render)];
    if (render.human_review_required && !result.decision) parts.push("Please check the picture yourself, then record your decision below.");
    if (result.decision && result.decision.decision === "reject") parts.push("You rejected the computer’s result.");
    if (result.decision && result.decision.decision === "needs_second_look") parts.push("You asked for a second look.");
    ui.statusDetail.textContent = parts.filter(Boolean).join(" ");
  }

  function showResult(render) {
    current = render;
    applyConfig(render);
    const result = render.result;
    paintStatus(render);

    stage = CS.buildStage(paths.image(result.observation_id), `The picture you checked${result.image.filename ? `: ${result.image.filename}` : ""}`);
    CS.clear(ui.stageHolder);
    ui.stageHolder.appendChild(stage.stage);
    wireDrawing(); // a fresh overlay per picture, so nothing carries over

    CS.clear(ui.detList);
    result.detections.forEach((det, i) => ui.detList.appendChild(detectionItem(det, i + 1)));
    ui.detEmpty.hidden = result.detections.length > 0;
    if (!result.detections.length) {
      ui.detEmpty.textContent = "Nothing to list. The computer did not find anything it is sure enough about. If you can see something it missed, use “The computer missed something” below.";
    }
    const hidden = [];
    if (result.hidden_below_threshold) hidden.push(`${result.hidden_below_threshold} weaker ${result.hidden_below_threshold === 1 ? "guess" : "guesses"} (less than ${CS.percent(render.confidence_threshold)} sure)`);
    if (result.hidden_other_labels) hidden.push(`${result.hidden_other_labels} ${result.hidden_other_labels === 1 ? "finding" : "findings"} of things this screen is not set up to show`);
    const scoreNote = CS.scoreNote(render);
    ui.hiddenNote.textContent = hidden.length ? `${scoreNote} The computer also had ${hidden.join(" and ")}, which ${hidden.length === 1 && result.hidden_below_threshold + result.hidden_other_labels === 1 ? "is" : "are"} not shown.` : scoreNote;

    // “The computer missed something”
    CS.clear(ui.missedLabel);
    render.displayed_labels.forEach((label) => { const o = el("option", "", label); o.value = label; ui.missedLabel.appendChild(o); });
    const noteNeeded = render.decision_controls.required_note_on_override;
    ui.missedReq.textContent = noteNeeded ? "(required)" : "(optional)";
    (result.feedback || []).filter((f) => f.verdict === "false_negative").forEach((f) => addMissedEntry(f.label, f.box, true));

    // decisions
    ui.noteReq.textContent = noteNeeded ? "(required if you reject the result)" : "(optional)";
    ui.decisionHelp.textContent = (render.human_review_required
      ? "Look at the picture, then say whether you agree with the computer. The result stays marked for review until you decide."
      : "You can record what you decided. This screen does not require it.")
      + " This is about the whole picture. The “right” and “wrong” buttons above are about one finding each.";
    render.decision_controls.options.forEach((key) => {
      const meta = CS.DECISIONS[key];
      const button = el("button", key === "confirm" ? "primary cs-decide" : "secondary cs-decide");
      button.type = "button";
      button.dataset.decision = key;
      button.appendChild(el("span", "cs-decide-label", meta.label));
      button.appendChild(el("span", "cs-decide-help", meta.help));
      button.addEventListener("click", () => onDecide(key, button));
      ui.decisionButtons.appendChild(button);
    });
    showRecorded(result.decision);

    ui.result.hidden = false;
    ui.decision.hidden = false;
    ui.another.hidden = false;
    CS.scrollTo(ui.result);
    announce(`Result: ${result.status_text}. ${CS.machineSentence(result, render)}`);
  }

  function showRecorded(decision) {
    if (!decision) { ui.recorded.hidden = true; return; }
    ui.recorded.hidden = false;
    CS.clear(ui.recorded);
    const meta = CS.DECISIONS[decision.decision];
    ui.recorded.appendChild(el("p", "cs-recorded-title", `Recorded: ${meta.done} at ${clock(decision.at)}.`));
    if (decision.note) ui.recorded.appendChild(el("p", "cs-help", `Your note: ${decision.note}`));
    ui.recorded.appendChild(el("p", "cs-help", `The result now reads: “${current ? current.result.status_text : ""}”. This is saved with the picture. You can change your mind: the latest decision counts.`));
  }

  /* -- per-finding feedback --------------------------------------------------------------- */
  function detectionItem(det, number) {
    stage.addDetection(det, number);
    const item = CS.detectionRow(det, number, current);
    const noteNeeded = current.decision_controls.required_note_on_override;
    const msg = el("p", "cs-inline-msg cs-det-msg");
    msg.setAttribute("role", "status");
    msg.id = `cs-det-msg-${det.index}`;
    const actions = el("div", "cs-det-actions");
    const right = el("button", "secondary", "This is right");
    right.type = "button";
    right.setAttribute("aria-label", `This is right: finding ${number}, ${det.label}`);
    const wrong = el("button", "secondary", "This is wrong");
    wrong.type = "button";
    wrong.setAttribute("aria-label", `This is wrong: finding ${number}, ${det.label}`);
    wrong.setAttribute("aria-expanded", "false");
    actions.append(right, wrong);

    const panel = el("div", "cs-wrong-panel");
    panel.hidden = true;
    const labelId = `cs-wrong-note-${det.index}`;
    const noteLabel = el("label", "", noteNeeded ? "Why is it wrong? (required)" : "Why is it wrong? (optional)");
    noteLabel.htmlFor = labelId;
    const note = el("textarea");
    note.id = labelId; note.rows = 2; note.maxLength = 2000;
    const panelActions = el("div", "cs-actions");
    const save = el("button", "secondary", "Save: this is wrong");
    save.type = "button";
    const cancel = el("button", "secondary", "Cancel");
    cancel.type = "button";
    panelActions.append(save, cancel);
    const noteHelp = el("p", "cs-help", "Say what you can see that the computer got wrong, for example: \"that is a shadow, not damage\". A short sentence is enough.");
    noteHelp.id = `${labelId}-help`;
    note.setAttribute("aria-describedby", noteHelp.id);
    panel.append(noteLabel, noteHelp, note, panelActions);

    const send = async (verdict, noteText, buttons) => {
      buttons.forEach((b) => { b.disabled = true; });
      const result = await CS.request(paths.feedback(current.result.observation_id), {
        method: "POST", json: { prediction_index: det.index, verdict, note: noteText }
      });
      buttons.forEach((b) => { b.disabled = false; });
      if (!result.ok) {
        const info = CS.explain(result, "save that answer");
        setMsg(msg, `Not saved. ${info.text}`, "error");
        return false;
      }
      item.dataset.verdict = verdict;
      setMsg(msg, verdict === "true_positive"
        ? `Saved: you said this is right. ${SAVED_FOR_SUPERVISOR}`
        : `Saved: you said this is wrong. ${SAVED_FOR_SUPERVISOR} ${WRONG_CANNOT_TEACH}`, "ok");
      return true;
    };
    right.addEventListener("click", async () => {
      panel.hidden = true; wrong.setAttribute("aria-expanded", "false");
      await send("true_positive", "", [right, wrong]);
    });
    wrong.addEventListener("click", () => {
      panel.hidden = !panel.hidden;
      wrong.setAttribute("aria-expanded", String(!panel.hidden));
      if (!panel.hidden) note.focus();
    });
    cancel.addEventListener("click", () => { panel.hidden = true; wrong.setAttribute("aria-expanded", "false"); wrong.focus(); });
    save.addEventListener("click", async () => {
      if (noteNeeded && !note.value.trim()) {
        setMsg(msg, "Please write a short reason first. This screen asks for one when you say a finding is wrong.", "error");
        note.setAttribute("aria-invalid", "true");
        note.focus();
        return;
      }
      note.removeAttribute("aria-invalid");
      if (await send("false_positive", note.value, [save, cancel, right, wrong])) { panel.hidden = true; wrong.setAttribute("aria-expanded", "false"); note.value = ""; }
    });

    ["mouseenter", "focusin"].forEach((evt) => item.addEventListener(evt, () => stage.highlight(det.index)));
    ["mouseleave", "focusout"].forEach((evt) => item.addEventListener(evt, () => stage.highlight(-1)));
    item.append(actions, panel, msg);
    return item;
  }

  /* -- “the computer missed something” ---------------------------------------------------- */
  const num = (input) => (input.value.trim() === "" ? NaN : Number(input.value));
  const round = (value, places) => Math.round(value * 10 ** places) / 10 ** places;

  function boxFromInputs() {
    if (ui.unknown.checked) return { box: null };
    const [x, y, w, h] = [num(ui.bx), num(ui.by), num(ui.bw), num(ui.bh)];
    if ([x, y, w, h].some((v) => !Number.isFinite(v))) return { error: "Show where it is: drag on the picture, or fill in all four numbers. If you cannot show where, tick “I can’t show where it is”." };
    if (x < 0 || y < 0) return { error: "The left and top edges cannot be below 0." };
    if (w <= 0 || h <= 0) return { error: "Width and height must be more than 0." };
    if (x + w > 100.0001) return { error: "The box runs past the right edge. Make the left edge plus the width 100 or less." };
    if (y + h > 100.0001) return { error: "The box runs past the bottom. Make the top edge plus the height 100 or less." };
    const fx = Math.min(round(x / 100, 4), 1), fy = Math.min(round(y / 100, 4), 1);
    return { box: { x: fx, y: fy, width: Math.min(round(w / 100, 4), round(1 - fx, 4)), height: Math.min(round(h / 100, 4), round(1 - fy, 4)) } };
  }

  function syncDraft() {
    if (!stage) return;
    const parsed = boxFromInputs();
    stage.setDraft(parsed.box && ui.missed.open ? parsed.box : null);
    [ui.bx, ui.by, ui.bw, ui.bh].forEach((input) => { input.disabled = ui.unknown.checked; });
    ui.draw.disabled = ui.unknown.checked;
  }

  function setDrawMode(on) {
    ui.draw.setAttribute("aria-pressed", String(on));
    ui.draw.textContent = on ? "Stop drawing" : "Draw a box on the picture";
    ui.drawHelp.textContent = on ? "On. Drag across the picture to draw a box." : "Off. Turn this on, then drag across the picture.";
    if (stage) {
      stage.draw.classList.toggle("is-on", on);
      // the picture is above this form: bring it into view so there is something to draw on
      if (on) stage.stage.scrollIntoView({ block: "center", behavior: CS.reducedMotion() ? "auto" : "smooth" });
    }
  }

  function wireDrawing() {
    if (!stage) return;
    let start = null;
    const point = (event) => {
      const rect = stage.draw.getBoundingClientRect();
      return { x: Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1), y: Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1) };
    };
    const rectOf = (a, b) => ({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(a.x - b.x), height: Math.abs(a.y - b.y) });
    const write = (box) => {
      ui.bx.value = String(round(box.x * 100, 1)); ui.by.value = String(round(box.y * 100, 1));
      ui.bw.value = String(round(box.width * 100, 1)); ui.bh.value = String(round(box.height * 100, 1));
    };
    stage.draw.addEventListener("pointerdown", (event) => {
      start = point(event);
      stage.draw.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    stage.draw.addEventListener("pointermove", (event) => {
      if (!start) return;
      const box = rectOf(start, point(event));
      write(box); stage.setDraft(box);
    });
    stage.draw.addEventListener("pointerup", (event) => {
      if (!start) return;
      const box = rectOf(start, point(event));
      start = null;
      if (box.width < 0.01 || box.height < 0.01) { setMsg(ui.missedMsg, "That box was too small. Drag a little further, or type the position instead.", "error"); return; }
      write(box); stage.setDraft(box); setDrawMode(false);
      setMsg(ui.missedMsg, "Box drawn. Check the numbers, choose what it is, then save.", "");
      ui.draw.focus(); // scrolls back to the form, ready for the note and Save
    });
    stage.draw.addEventListener("pointercancel", () => { start = null; });
  }

  function addMissedEntry(label, box, existing) {
    missedCount += 1;
    const where = !box ? "no position given" : `left ${round(box.x * 100, 1)}%, top ${round(box.y * 100, 1)}%, ${round(box.width * 100, 1)}% wide, ${round(box.height * 100, 1)}% tall`;
    ui.missedList.appendChild(el("li", "", `+${missedCount} You said it missed: ${label} (${where})${existing ? "" : ". Saved for your supervisor."}`));
    if (stage && box) stage.addNote(box, missedCount);
  }

  async function onMissedSave(event) {
    event.preventDefault();
    if (!current) return;
    const noteNeeded = current.decision_controls.required_note_on_override;
    const parsed = boxFromInputs();
    if (parsed.error) { setMsg(ui.missedMsg, parsed.error, "error"); return; }
    if (noteNeeded && !ui.missedNote.value.trim()) {
      setMsg(ui.missedMsg, "Please write a short note first. This screen asks for one when something was missed.", "error");
      ui.missedNote.setAttribute("aria-invalid", "true");
      ui.missedNote.focus();
      return;
    }
    ui.missedNote.removeAttribute("aria-invalid");
    const save = byId("cs-missed-save");
    save.disabled = true;
    const result = await CS.request(paths.feedback(current.result.observation_id), {
      method: "POST",
      json: { prediction_index: "missed", verdict: "false_negative", label: ui.missedLabel.value, box: parsed.box, note: ui.missedNote.value }
    });
    save.disabled = false;
    if (!result.ok) {
      setMsg(ui.missedMsg, `Not saved. ${CS.explain(result, "save that").text}`, "error");
      return;
    }
    addMissedEntry(ui.missedLabel.value, parsed.box, false);
    if (stage) stage.setDraft(null);
    ui.missedNote.value = "";
    [ui.bx, ui.by, ui.bw, ui.bh].forEach((input) => { input.value = ""; });
    ui.unknown.checked = false;
    syncDraft();
    setMsg(ui.missedMsg, `Saved: the computer missed this. ${SAVED_FOR_SUPERVISOR} You can add another.`, "ok");
  }

  /* -- decisions -------------------------------------------------------------------------- */
  async function onDecide(key, button) {
    if (!current || !Athena.can("label")) return;
    const noteNeeded = key === "reject" && current.decision_controls.required_note_on_override;
    if (noteNeeded && !ui.note.value.trim()) {
      setMsg(ui.decisionMsg, "Please write a short note saying why you disagree, then press Reject again.", "error");
      ui.note.setAttribute("aria-invalid", "true");
      ui.note.focus();
      return;
    }
    ui.note.removeAttribute("aria-invalid");
    const buttons = [...ui.decisionButtons.querySelectorAll("button")];
    buttons.forEach((b) => { b.disabled = true; });
    const result = await CS.request(paths.decision(current.result.observation_id), { method: "POST", json: { decision: key, note: ui.note.value } });
    buttons.forEach((b) => { b.disabled = false; });
    if (!result.ok) {
      const info = CS.explain(result, "record your decision");
      setMsg(ui.decisionMsg, `Not saved. ${info.title}. ${info.text}`, "error");
      if (/no longer approved/i.test(info.title)) block(info.title, info.text);
      return;
    }
    const render = result.body.render;
    current = render;
    paintStatus(render);
    showRecorded(render.result.decision);
    // A reject or a second look goes to the supervisor; a confirm is just recorded.
    const toSupervisor = key === "reject" ? `${SAVED_FOR_SUPERVISOR} ${WRONG_CANNOT_TEACH}` : key === "needs_second_look" ? SAVED_FOR_SUPERVISOR : "";
    setMsg(ui.decisionMsg, toSupervisor, toSupervisor ? "ok" : "");
    announce(`Decision recorded. ${render.result.status_text}.`);
    button.focus();
  }

  /* -- wiring ----------------------------------------------------------------------------- */
  ui.check.addEventListener("click", onCheck);
  ui.file.addEventListener("change", () => { setMsg(ui.pickMsg, ""); });
  ui.draw.addEventListener("click", () => setDrawMode(ui.draw.getAttribute("aria-pressed") !== "true"));
  [ui.bx, ui.by, ui.bw, ui.bh].forEach((input) => input.addEventListener("input", syncDraft));
  ui.unknown.addEventListener("change", syncDraft);
  ui.missed.addEventListener("toggle", () => { syncDraft(); if (!ui.missed.open) setDrawMode(false); });
  ui.missedForm.addEventListener("submit", onMissedSave);
  let warned = false;
  ui.another.addEventListener("click", () => {
    if (current && current.human_review_required && !current.result.decision && !warned) {
      warned = true;
      setMsg(ui.decisionMsg, "You have not recorded a decision for this picture. Choose one above, or press “Check another picture” again to leave without deciding.", "error");
      return;
    }
    warned = false;
    if (controller) controller.abort();
    resetResult();
    CS.hideAlert(errorIds);
    ui.file.value = "";
    setMsg(ui.pickMsg, "");
    ui.file.focus();
    window.scrollTo({ top: 0, behavior: CS.reducedMotion() ? "auto" : "smooth" });
  });

  init();
})();
