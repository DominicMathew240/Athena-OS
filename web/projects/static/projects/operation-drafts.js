/* Private drafts and recording intake; analysis is not yet available. */
(() => {
  "use strict";
  const { api } = window.AthenaApp;
  const form = document.getElementById("draft-form");
  const name = document.getElementById("draft-name");
  const save = document.getElementById("draft-save");
  const start = document.getElementById("draft-new");
  const remove = document.getElementById("draft-delete");
  const status = document.getElementById("draft-status");
  const error = document.getElementById("draft-error");
  const list = document.getElementById("draft-list");
  const listStatus = document.getElementById("draft-list-status");
  const recordingSection = document.getElementById("recording-section");
  const recordingAdd = document.getElementById("recording-add");
  const recordingSaved = document.getElementById("recording-saved");
  const recordingFile = document.getElementById("recording-file");
  const recordingUpload = document.getElementById("recording-upload");
  const recordingDelete = document.getElementById("recording-delete");
  const recordingInfo = document.getElementById("recording-info");
  const recordingPreview = document.getElementById("recording-preview");
  const recordingStatus = document.getElementById("recording-status");
  const recordingError = document.getElementById("recording-error");
  const markPositiveEvent = document.getElementById("mark-positive-event");
  const markNegativeEvent = document.getElementById("mark-negative-event");
  const referenceEventList = document.getElementById("reference-event-list");
  const referenceEventsStatus = document.getElementById("reference-events-status");
  let referenceEvents = [];
  let savedReferenceEvents = "[]";
  let current = null;
  let savedName = "";
  let busy = false;
  const lineEditor = window.createOperationLineEditor({
    video: recordingPreview,
    canEdit: () => !busy && window.Athena.can("label"),
    onSave: () => form.requestSubmit()
  });
  function renderReferenceEvents() {
    const line = (() => { try { return lineEditor.read(); } catch (_) { return null; } })();
    const positive = line?.positive_label || "towards the arrow";
    const negative = line?.negative_label || "against the arrow";
    markPositiveEvent.textContent = `Mark current time: ${positive}`;
    markNegativeEvent.textContent = `Mark current time: ${negative}`;
    referenceEventList.replaceChildren();
    referenceEvents.forEach((event, index) => {
      const item = document.createElement("li");
      const description = document.createElement("span");
      description.textContent = `${event.time.toFixed(3)} s · ${event.direction === "negative_to_positive" ? positive : negative}`;
      const removeEvent = document.createElement("button");
      removeEvent.type = "button";
      removeEvent.className = "a-btn";
      removeEvent.textContent = "Remove";
      removeEvent.setAttribute("aria-label", `Remove crossing at ${event.time.toFixed(3)} seconds`);
      removeEvent.disabled = busy;
      removeEvent.addEventListener("click", () => {
        if (busy) return;
        referenceEvents.splice(index, 1);
        renderReferenceEvents();
        status.textContent = "Reference crossing removed. Save the draft to keep this change.";
      });
      item.append(description, removeEvent);
      referenceEventList.append(item);
    });
    referenceEventsStatus.textContent = referenceEvents.length
      ? `${referenceEvents.length} manually marked ${referenceEvents.length === 1 ? "crossing" : "crossings"}. Save the draft to keep them.`
      : "No reference crossings marked.";
  }
  const setBusy = value => { busy = value; lineEditor.setBusy(value); renderReferenceEvents(); };
  const dirty = () => name.value !== savedName || recordingFile.files.length > 0
    || lineEditor.dirty() || JSON.stringify(referenceEvents) !== savedReferenceEvents;
  const mayLeave = () => !dirty() ? Promise.resolve(true) : window.Athena.askConfirm({
    title: "Discard unsaved changes?", message: "Your name, line or selected recording has not been saved.",
    keepLabel: "Keep editing", confirmLabel: "Discard changes"
  });

  function showDraft(draft, keepFile = false) {
    current = draft;
    name.value = draft ? draft.name : "";
    savedName = name.value;
    remove.hidden = !draft;
    recordingSection.hidden = !draft;
    recordingAdd.hidden = !!draft?.recording;
    recordingSaved.hidden = !draft?.recording;
    if (!keepFile) recordingFile.value = "";
    recordingStatus.textContent = "";
    recordingError.textContent = "";
    if (draft?.recording) {
      const video = draft.recording;
      recordingInfo.textContent = `${video.filename} · ${video.width} × ${video.height} · ${video.duration_seconds.toFixed(1)} s`;
      recordingPreview.src = video.content_url;
    } else {
      recordingPreview.removeAttribute("src");
      recordingPreview.load();
    }
    lineEditor.load(draft);
    referenceEvents = (draft?.reference_events || []).map(event => ({ time: Number(event.time), direction: event.direction }));
    savedReferenceEvents = JSON.stringify(referenceEvents);
    renderReferenceEvents();
    status.textContent = draft ? "Saved draft. Changes are saved when you select Save draft." : "New draft — not saved yet.";
    error.textContent = "";
  }

  async function loadList() {
    try {
      const result = await api("/api/operation-drafts");
      list.replaceChildren();
      for (const draft of result.drafts) {
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "a-btn";
        button.textContent = draft.name;
        button.addEventListener("click", async () => {
          if (busy) return;
          setBusy(true);
          try {
            if (!await mayLeave()) return;
            name.disabled = true;
            const result = await api(`/api/operation-drafts/${draft.id}`);
            showDraft(result.draft);
          } catch (problem) { error.textContent = problem.message; }
          finally { name.disabled = false; setBusy(false); }
        });
        item.append(button);
        list.append(item);
      }
      listStatus.textContent = result.drafts.length ? (result.drafts.length === 200 ? "Showing up to 200 recent drafts." : "Select a draft to continue.") : "No drafts yet.";
    } catch (problem) { listStatus.textContent = `Could not load drafts: ${problem.message}`; }
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    let line;
    try { line = lineEditor.read(); lineEditor.setError(""); }
    catch (problem) { lineEditor.setError(problem.message); return; }
    const referenceEventsInvalidated = !!current && JSON.stringify(line) !== JSON.stringify(current.line) && referenceEvents.length > 0;
    setBusy(true);
    save.disabled = true;
    name.disabled = true;
    error.textContent = "";
    status.textContent = "Saving…";
    const payload = { name: name.value, line, reference_events: referenceEventsInvalidated ? [] : referenceEvents };
    if (current) payload.revision = current.revision;
    try {
      const result = await api(current ? `/api/operation-drafts/${current.id}` : "/api/operation-drafts", {
        method: current ? "PUT" : "POST", json: payload
      });
      showDraft(result.draft, true);
      if (referenceEventsInvalidated) status.textContent = "Line changed; the old reference crossings were cleared. Mark them again against the saved line.";
      await loadList();
    } catch (problem) {
      status.textContent = "Not saved. Your changes are still here.";
      error.textContent = problem.message;
      lineEditor.setError(problem.message);
    } finally { setBusy(false); save.disabled = false; name.disabled = false; }
  });

  start.addEventListener("click", async () => {
    if (busy) return;
    setBusy(true);
    try { if (await mayLeave()) showDraft(null); }
    finally { setBusy(false); }
  });
  remove.addEventListener("click", async () => {
    if (busy || !current) return;
    setBusy(true);
    try {
      const accepted = await window.Athena.askConfirm({ title: "Delete this draft?", message: "This cannot be undone.", keepLabel: "Keep draft", confirmLabel: "Delete draft" });
      if (!accepted) return;
      await api(`/api/operation-drafts/${current.id}`, { method: "DELETE" });
      showDraft(null);
      status.textContent = "Draft deleted.";
      await loadList();
    } catch (problem) { error.textContent = problem.message; }
    finally { setBusy(false); }
  });
  recordingUpload.addEventListener("click", async () => {
    if (busy || !current || current.recording) return;
    recordingError.textContent = "";
    if (name.value !== savedName) { recordingError.textContent = "Save the draft name before uploading."; return; }
    const file = recordingFile.files[0];
    if (!file || file.size > 50 * 1024 * 1024) { recordingError.textContent = "Choose one MP4 under 50 MB."; return; }
    const formData = new FormData();
    formData.append("revision", String(current.revision));
    formData.append("video", file);
    setBusy(true);
    name.disabled = recordingFile.disabled = recordingUpload.disabled = true;
    recordingStatus.textContent = "Uploading and checking the recording…";
    try {
      const result = await api(`/api/operation-drafts/${current.id}/recording`, { method: "POST", form: formData });
      showDraft(result.draft);
      recordingStatus.textContent = "Recording saved. Counting is not available yet.";
    } catch (problem) {
      recordingStatus.textContent = "Recording not saved. Your selection is still here.";
      recordingError.textContent = problem.message;
    } finally { setBusy(false); name.disabled = recordingFile.disabled = recordingUpload.disabled = false; }
  });
  recordingDelete.addEventListener("click", async () => {
    if (busy || !current?.recording) return;
    recordingError.textContent = "";
    if (name.value !== savedName || lineEditor.dirty()) { recordingError.textContent = "Save the draft and line before removing the recording."; return; }
    setBusy(true);
    try {
      const accepted = await window.Athena.askConfirm({ title: "Remove recording?", message: "This also clears any line saved for this view.", keepLabel: "Keep recording", confirmLabel: "Remove recording" });
      if (!accepted) return;
      name.disabled = true;
      const result = await api(`/api/operation-drafts/${current.id}/recording`, { method: "DELETE", json: { revision: current.revision } });
      showDraft(result.draft);
      recordingStatus.textContent = "Removed from this draft. If private storage is unavailable, cleanup remains pending for an administrator.";
    } catch (problem) { recordingError.textContent = problem.message; }
    finally { name.disabled = false; setBusy(false); }
  });
  function markReferenceEvent(direction) {
    if (busy || !current?.recording) return;
    try {
      if (lineEditor.dirty()) throw new Error("Save the line before marking reference crossings.");
      if (!lineEditor.read()) throw new Error("Mark and name a counting line before recording crossings.");
      const time = recordingPreview.currentTime;
      const duration = current.recording.duration_seconds;
      if (!Number.isFinite(time) || time < 0 || time > duration) throw new Error("Pause the recording at a valid time first.");
      if (referenceEvents.length >= 1000) throw new Error("This draft already has the maximum of 1,000 marked crossings.");
      referenceEvents.push({ time: Math.min(duration, Math.round(time * 1000) / 1000), direction });
      referenceEvents.sort((a, b) => a.time - b.time);
      renderReferenceEvents();
      status.textContent = "Reference crossing marked. Save the draft to keep it.";
    } catch (problem) {
      lineEditor.setError(problem.message);
    }
  }
  markPositiveEvent.addEventListener("click", () => markReferenceEvent("negative_to_positive"));
  markNegativeEvent.addEventListener("click", () => markReferenceEvent("positive_to_negative"));
  document.getElementById("line-clear").addEventListener("click", () => {
    if (!lineEditor.read() && referenceEvents.length) {
      referenceEvents = [];
      renderReferenceEvents();
      status.textContent = "Clearing the line also cleared its reference crossings. Save the draft to keep this change.";
    }
  });
  for (const id of ["line-positive", "line-negative"]) {
    document.getElementById(id).addEventListener("input", renderReferenceEvents);
  }
  window.addEventListener("beforeunload", event => { if (busy || dirty()) { event.preventDefault(); event.returnValue = ""; } });
  showDraft(null);
  loadList();
})();
