/* Named four-corner bays on the draft's recording (T17).
   A bay is a plain quadrilateral drawn on the same picture as the counting
   line: exactly four corners in percentages of the picture, a name the
   person chooses, and a stable ID minted once at creation so RENAMING the
   bay never loses its identity or history. Bays ride the same discipline as
   the line: coordinates are normalized 0–1 on the server, the same drawing
   canvas is shared, and the whole state is restated as text in an aria-live
   summary. Every essential action also works without a mouse: corners can
   be chosen by button and nudged with the arrow keys (fine 0.001 / coarse
   0.01 Shift steps, clamped to the picture), an in-session undo/redo stack
   keeps corner moves, adds, removes, duplicates and renames, and clearing
   warns before discarding unsaved work. */
(() => {
  "use strict";
  window.createOperationBayEditor = ({ video, canEdit, onSave }) => {
    const section = document.getElementById("bay-section");
    const canvas = document.getElementById("bay-canvas");
    if (!section || !canvas) return null;
    const ctx = canvas.getContext("2d");
    const snapshot = document.createElement("canvas");
    const name = document.getElementById("bay-name");
    const frame = document.getElementById("bay-frame");
    const save = document.getElementById("bay-save");
    const clear = document.getElementById("bay-clear");
    const add = document.getElementById("bay-add");
    const remove = document.getElementById("bay-remove");
    const duplicate = document.getElementById("bay-duplicate");
    const rename = document.getElementById("bay-rename");
    const status = document.getElementById("bay-status");
    const error = document.getElementById("bay-error");
    const summary = document.getElementById("bay-summary");
    const undoButton = document.getElementById("bay-undo");
    const redoButton = document.getElementById("bay-redo");
    const select = document.getElementById("bay-select");
    const nudgeButtons = {
      left: document.getElementById("bay-move-left"),
      right: document.getElementById("bay-move-right"),
      up: document.getElementById("bay-move-up"),
      down: document.getElementById("bay-move-down")
    };
    const inputs = [
      document.getElementById("bay-corner-0-x"), document.getElementById("bay-corner-0-y"),
      document.getElementById("bay-corner-1-x"), document.getElementById("bay-corner-1-y"),
      document.getElementById("bay-corner-2-x"), document.getElementById("bay-corner-2-y"),
      document.getElementById("bay-corner-3-x"), document.getElementById("bay-corner-3-y")
    ];
    /* Bays are a LIST: the draft carries any number, this editor edits the
       selected one. Each bay: { id, name, corners: [[x,y]×4] } in percentages;
       read()/payload convert to normalized 0–1. */
    let bays = [];
    let original = "[]";
    let selectedId = null;
    let draft = null;
    let busy = false;
    let hasFrame = false;
    let dragging = null;
    let chosen = 0;
    const history = { undo: [], redo: [], batchFrom: null, batchTimer: null, editFrom: null };
    const NUDGE_FINE = 0.1;
    const NUDGE_COARSE = 1;
    const NUDGE_ROUNDED = value => String(Math.round(value * 1000) / 1000);
    const MIN_AREA_PERCENT = 1e-4;
    const BAY_COUNT_LIMIT = 200;

    const selected = () => bays.find(bay => bay.id === selectedId) || null;
    const values = () => inputs.map(input => input.value);
    const sameValues = (a, b) => a.join("\u0000") === b.join("\u0000");
    const dirty = () => !!draft?.recording && JSON.stringify(bays) !== original;
    const coords = () => inputs.map(input => input.value === "" ? null : Number(input.value));
    const validPoint = (x, y) => x !== null && y !== null && Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 100 && y >= 0 && y <= 100;
    const persistedIds = () => {
      try { return new Set(JSON.parse(original).map(bay => bay.id)); }
      catch (_) { return new Set(); }
    };

    /* The duplicate's new identity is the one ID the server added, not
       "the first bay that isn't the source" — that picks the wrong bay
       as soon as two or more already exist. */
    function mintedBayId(previousIds, serverBays) {
      const minted = (serverBays || []).filter(item => !previousIds.has(item.id));
      return minted.length === 1 ? minted[0].id : null;
    }

    /* Duplicate and rename reload the server's draft. Any unsaved geometry
       would be discarded; a locally added bay's ID is not on the server at
       all. Refuse those identity actions before the POST. Rename may still
       send the name field — that field is the action's input — but every
       other unsaved edit must be saved first. */
    function unsavedIdentityBlock(bay, { ignoreSelectedName } = {}) {
      if (!persistedIds().has(bay.id)) {
        return "Save the draft first so this new bay exists on the server. Duplicate and rename need a saved bay ID.";
      }
      const saved = JSON.parse(original);
      const local = JSON.parse(JSON.stringify(bays));
      if (ignoreSelectedName) {
        const localBay = local.find(item => item.id === bay.id);
        const savedBay = saved.find(item => item.id === bay.id);
        if (localBay && savedBay) localBay.name = savedBay.name;
      }
      if (JSON.stringify(local) !== JSON.stringify(saved)) {
        return "Save the draft first. Unsaved bay changes would be lost if this ran now.";
      }
      return null;
    }

    /* --- Self-intersection and area, mirrored from the server's math -----
       A bay outline must not cross itself: the two edge pairs of the closed
       quad are checked with segment-crossing math, so a "bowtie" is caught
       no matter how rectangular its corner positions look. The same rules
       the server enforces (bays.py), so the client's messages are the ones
       the server would give, just earlier. */
    function orientationsCross(p1, p2, p3, p4, strict) {
      const orient = (a, b, c) => {
        const value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
        return value > 1e-9 ? 1 : value < -1e-9 ? -1 : 0;
      };
      const o1 = orient(p1, p2, p3), o2 = orient(p1, p2, p4);
      const o3 = orient(p3, p4, p1), o4 = orient(p3, p4, p2);
      if (o1 !== o2 && o3 !== o4) return true;
      if (strict) return false;
      const onSegment = (a, b, c) =>
        Math.min(a[0], b[0]) - 1e-9 <= c[0] && c[0] <= Math.max(a[0], b[0]) + 1e-9 &&
        Math.min(a[1], b[1]) - 1e-9 <= c[1] && c[1] <= Math.max(a[1], b[1]) + 1e-9;
      if (o1 === 0 && onSegment(p1, p2, p3)) return true;
      if (o2 === 0 && onSegment(p1, p2, p4)) return true;
      if (o3 === 0 && onSegment(p3, p4, p1)) return true;
      if (o4 === 0 && onSegment(p3, p4, p2)) return true;
      return false;
    }

    function bayArea(corners) {
      return 0.5 * Math.abs(corners.reduce(
        (sum, corner, index) => {
          const next = corners[(index + 1) % 4];
          return sum + corner[0] * next[1] - next[0] * corner[1];
        }, 0
      ));
    }

    function bayProblem(corners) {
      // The same order as the server (bays.py): a genuinely crossed degenerate
      // quad is a crossing; a merely collapsed one is too close together.
      if (bayArea(corners) < MIN_AREA_PERCENT) {
        if (orientationsCross(corners[0], corners[1], corners[2], corners[3], true)
          || orientationsCross(corners[1], corners[2], corners[3], corners[0], true)) {
          return "The four corners cross each other; a bay's outline must not overlap itself.";
        }
        return "The four corners are too close together to enclose a bay.";
      }
      if (orientationsCross(corners[0], corners[1], corners[2], corners[3])
        || orientationsCross(corners[1], corners[2], corners[3], corners[0])) {
        return "The four corners cross each other; a bay's outline must not overlap itself.";
      }
      return null;
    }

    /* --- Text summary (aria-live) ---------------------------------------
       The whole bay state restated as plain text, like the line summary:
       nothing depends on the picture or on colour. */
    function describeBays() {
      const count = bays.length;
      if (!count) return "No bays yet. Add a bay with its four corner positions to name one area of the picture.";
      const current = selected();
      const point = (corner, x, y) => validPoint(x, y)
        ? `${Number(x.toFixed(3))}% from the left, ${Number(y.toFixed(3))}% from the top`
        : "not entered yet";
      const listText = count === 1 ? "1 bay." : `${count} bays.`;
      const names = bays.map(bay => `"${bay.name || "unnamed"}"`).join(", ");
      const saveState = dirty()
        ? " There are unsaved changes to the bays."
        : draft ? " The bays match what is saved." : "";
      if (!current) return `${listText} Named: ${names}.${saveState}`;
      const c = coords();
      const problem = [c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]].every(v => v !== null) && bayProblem([[c[0], c[1]], [c[2], c[3]], [c[4], c[5]], [c[6], c[7]]]);
      return `${listText} Editing bay "${current.name || "unnamed"}". `
        + [0, 1, 2, 3].map(i => `Corner ${i + 1}: ${point(i, c[i * 2], c[i * 2 + 1])}.`).join(" ")
        + (problem ? ` Warning: ${problem}` : "")
        + saveState;
    }

    function refreshSummary() {
      if (summary) summary.textContent = describeBays();
    }

    /* --- Undo/redo, the same discipline as the line editor ---------------- */
    const editorControls = new Set([...inputs, name, select, undoButton, redoButton, ...Object.values(nudgeButtons), canvas].filter(Boolean));

    function endBatch() {
      if (history.batchTimer) { clearTimeout(history.batchTimer); history.batchTimer = null; }
      history.batchFrom = null;
    }

    function snapshotState() {
      return { bays: JSON.parse(JSON.stringify(bays)), selectedId };
    }

    function pushHistory(previous) {
      history.undo.push(previous);
      if (history.undo.length > 100) history.undo.shift();
      history.redo = [];
      renderHistoryButtons();
    }

    function currentValuesSignature() { return values(); }

    let editFromState = null;
    function settleRun() {
      if (editFromState != null && JSON.stringify(editFromState) !== JSON.stringify(snapshotState())) {
        pushHistory(editFromState);
      }
      editFromState = null;
    }

    function rememberBase() {
      history.undo = [];
      history.redo = [];
      editFromState = null;
      endBatch();
      renderHistoryButtons();
    }

    const canUndo = () => history.undo.length > 0;
    const canRedo = () => history.redo.length > 0;

    function renderHistoryButtons() {
      if (!undoButton || !redoButton) return;
      undoButton.disabled = busy || !canUndo();
      redoButton.disabled = busy || !canRedo();
    }

    function restoreState(state) {
      bays = JSON.parse(JSON.stringify(state.bays));
      selectedId = state.selectedId;
      renderSelect();
      loadSelectedIntoFields();
      dragging = null;
      if (error) error.textContent = "";
      paint();
      refreshSummary();
      renderHistoryButtons();
      renderChosenButtons();
    }

    function doUndo() {
      if (busy || !canEdit()) return;
      settleRun();
      if (!canUndo()) return;
      history.redo.push(snapshotState());
      restoreState(history.undo.pop());
      if (status) status.textContent = "Undone. " + (dirty() ? "The bays now differ from what is saved." : "The bays are back to what is saved.");
    }

    function doRedo() {
      if (busy || !canEdit()) return;
      settleRun();
      if (!canRedo()) return;
      history.undo.push(snapshotState());
      restoreState(history.redo.pop());
      if (status) status.textContent = "Redone. " + (dirty() ? "The bays now differ from what is saved." : "The bays are back to what is saved.");
    }

    if (undoButton) undoButton.addEventListener("click", doUndo);
    if (redoButton) redoButton.addEventListener("click", doRedo);

    /* --- Bay selection -----------------------------------------------------
       The bay dropdown lists every bay by name; selecting one loads its
       corners into the shared fields. The fields belong to the selected bay,
       and moving between bays settles the editing run of the previous one. */
    function renderSelect() {
      if (!select) return;
      const previous = select.value;
      select.replaceChildren();
      if (!bays.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No bays yet";
        select.append(option);
        select.disabled = true;
        return;
      }
      select.disabled = false;
      for (const bay of bays) {
        const option = document.createElement("option");
        option.value = bay.id;
        option.textContent = bay.name || "unnamed";
        select.append(option);
      }
      if (bays.some(bay => bay.id === selectedId)) select.value = selectedId;
      else if (bays.some(bay => bay.id === previous)) select.value = previous;
    }

    function loadSelectedIntoFields() {
      const bay = selected();
      if (bay) {
        bay.corners.forEach((corner, index) => {
          inputs[index * 2].value = corner[0] === null || corner[0] === undefined ? "" : String(corner[0]);
          inputs[index * 2 + 1].value = corner[1] === null || corner[1] === undefined ? "" : String(corner[1]);
        });
        if (name && document.activeElement !== name) name.value = bay.name || "";
      } else {
        inputs.forEach(input => { input.value = ""; });
        if (name) name.value = "";
      }
    }

    function writeFieldsIntoSelected() {
      const bay = selected();
      if (!bay) return;
      const c = coords();
      bay.corners = [[c[0], c[1]], [c[2], c[3]], [c[4], c[5]], [c[6], c[7]]];
      bay.name = name ? name.value.trim() : bay.name;
    }

    if (select) select.addEventListener("change", () => {
      settleRun();
      writeFieldsIntoSelected();
      selectedId = select.value || null;
      loadSelectedIntoFields();
      chosen = 0;
      renderChosenButtons();
      paint();
      refreshSummary();
    });

    /* --- Keyboard corner choice and nudging (T15 idioms) ------------------- */
    function renderChosenButtons() {
      for (let i = 0; i < 4; i++) {
        const button = document.getElementById(`bay-select-corner-${i}`);
        if (button) button.setAttribute("aria-pressed", chosen === i ? "true" : "false");
      }
    }

    function chooseCorner(which) {
      chosen = which;
      renderChosenButtons();
      const c = coords();
      const x = c[which * 2], y = c[which * 2 + 1];
      if (status) status.textContent = `Keyboard corner chosen: corner ${which + 1} of the selected bay`
        + (validPoint(x, y) ? ` at ${Number(x.toFixed(3))}% from the left, ${Number(y.toFixed(3))}% from the top.` : " is not placed yet. Enter its position in the fields first.");
    }

    for (let i = 0; i < 4; i++) {
      const button = document.getElementById(`bay-select-corner-${i}`);
      if (button) button.addEventListener("click", () => { settleRun(); chooseCorner(i); });
    }

    function nudge(dx, dy) {
      if (busy || !canEdit()) return;
      const bay = selected();
      if (!bay) { if (error) error.textContent = "Select a bay before moving its corners."; return; }
      const index = chosen * 2;
      const c = coords();
      const x = c[index], y = c[index + 1];
      if (!validPoint(x, y)) {
        if (error) error.textContent = `Place corner ${chosen + 1} first: enter both of its position fields before nudging.`;
        return;
      }
      settleRun();
      if (!history.batchFrom) {
        history.batchFrom = snapshotState();
        pushHistory(history.batchFrom);
        history.batchTimer = setTimeout(endBatch, 1200);
      }
      inputs[index].value = NUDGE_ROUNDED(Math.max(0, Math.min(100, x + dx)));
      inputs[index + 1].value = NUDGE_ROUNDED(Math.max(0, Math.min(100, y + dy)));
      writeFieldsIntoSelected();
      if (error) error.textContent = "";
      if (status) status.textContent = `Corner ${chosen + 1} moved to ${Number(inputs[index].value)}% from the left, ${Number(inputs[index + 1].value)}% from the top.`;
      paint();
      refreshSummary();
    }

    const NUDGE = { left: [-1, 0], right: [1, 0], up: [0, -1], down: [0, 1] };
    for (const [key, button] of Object.entries(nudgeButtons)) {
      if (!button) continue;
      button.addEventListener("click", () => { const [dx, dy] = NUDGE[key]; nudge(dx * NUDGE_FINE, dy * NUDGE_FINE); });
      button.addEventListener("keydown", arrowNudge);
    }

    function arrowNudge(event) {
      const map = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
      const step = map[event.key];
      if (!step) return false;
      event.preventDefault();
      const size = event.shiftKey ? NUDGE_COARSE : NUDGE_FINE;
      nudge(step[0] * size, step[1] * size);
      return true;
    }

    canvas.addEventListener("keydown", arrowNudge);
    inputs.forEach((input, index) => {
      input.addEventListener("keydown", event => {
        const map = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        if (!map[event.key]) return;
        event.preventDefault();
        const size = event.shiftKey ? NUDGE_COARSE : NUDGE_FINE;
        chosen = Math.floor(index / 2);
        renderChosenButtons();
        nudge(map[event.key][0] * size, map[event.key][1] * size);
      });
    });

    /* Field edits write into the selected bay; entering a field opens an
       editing run, leaving the editor's controls settles it. */
    const allInputs = [...inputs, name].filter(Boolean);
    allInputs.forEach(input => {
      input.addEventListener("focus", () => {
        if (editFromState == null) editFromState = snapshotState();
      });
      input.addEventListener("input", () => {
        writeFieldsIntoSelected();
        if (error) error.textContent = "";
        renderSelect();
        if (select && selectedId) select.value = selectedId;
        paint();
        refreshSummary();
      });
      input.addEventListener("keydown", event => {
        if (event.key === "Enter") { event.preventDefault(); settleRun(); }
      });
      input.addEventListener("blur", event => {
        if (!event.relatedTarget || !editorControls.has(event.relatedTarget)) settleRun();
      });
    });
    canvas.addEventListener("focus", () => { if (editFromState == null) editFromState = snapshotState(); });
    for (const control of [select, undoButton, redoButton, ...Object.values(nudgeButtons)]) {
      if (control) control.addEventListener("focus", settleRun);
    }

    /* --- Painting: the shared frozen frame with every bay, the selected one
       emphasised, and a halo circle over the corner being dragged. --------- */
    function paint() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (hasFrame) ctx.drawImage(snapshot, 0, 0, canvas.width, canvas.height);
      const radius = Math.max(2, Math.min(canvas.width, canvas.height) * .012);
      for (const bay of bays) {
        const corners = bay.corners.filter(corner => Array.isArray(corner) && corner.every(v => v !== null && Number.isFinite(v)));
        if (corners.length !== 4) continue;
        const isSelected = bay.id === selectedId;
        ctx.beginPath();
        bay.corners.forEach((corner, index) => {
          const x = corner[0] * canvas.width / 100, y = corner[1] * canvas.height / 100;
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.closePath();
        if (isSelected) {
          ctx.fillStyle = "rgba(127, 209, 192, .25)";
          ctx.fill();
        }
        ctx.setLineDash(isSelected ? [] : [radius * 2, radius]);
        ctx.lineWidth = Math.max(3, radius * 2); ctx.strokeStyle = "#101820"; ctx.stroke();
        ctx.lineWidth = Math.max(1.5, radius); ctx.strokeStyle = "#ffffff"; ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "#ffffff";
        for (const corner of bay.corners) {
          ctx.beginPath();
          ctx.arc(corner[0] * canvas.width / 100, corner[1] * canvas.height / 100, radius, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      if (dragging !== null) {
        const bay = selected();
        if (bay) {
          const corner = bay.corners[dragging];
          ctx.beginPath();
          ctx.arc(corner[0] * canvas.width / 100, corner[1] * canvas.height / 100, radius * 2, 0, Math.PI * 2);
          ctx.lineWidth = 2; ctx.strokeStyle = "#7fd1c0"; ctx.stroke();
        }
      }
    }

    function capture() {
      if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
        if (status) status.textContent = "Wait for the recording to load before choosing a frame.";
        return;
      }
      video.pause();
      canvas.width = snapshot.width = video.videoWidth;
      canvas.height = snapshot.height = video.videoHeight;
      snapshot.getContext("2d").drawImage(video, 0, 0);
      hasFrame = true;
      if (status) status.textContent = `${dirty() ? "Bay changes are not saved. " : bays.length ? "Saved bays. " : ""}Frame ready. Drag a corner, use the keyboard corner buttons, or enter positions below.`;
      paint();
    }

    if (frame) frame.addEventListener("click", capture);
    video.addEventListener("loadeddata", () => { if (!section.hidden) capture(); });

    /* --- Pointer dragging (an alternative, never a requirement) ------------ */
    canvas.addEventListener("pointerdown", event => {
      if (busy || !canEdit() || !hasFrame || event.button !== 0) return;
      const bay = selected();
      if (!bay) return;
      const box = canvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(100, (event.clientX - box.left) / box.width * 100));
      const y = Math.max(0, Math.min(100, (event.clientY - box.top) / box.height * 100));
      const grab = Math.min(canvas.width, canvas.height) * .012 / Math.min(box.width, box.height) * 100 * 1.5;
      let hit = null;
      bay.corners.forEach((corner, index) => {
        if (hit === null && Math.hypot(x - corner[0], y - corner[1]) <= grab) hit = index;
      });
      if (hit === null) return;
      dragging = hit;
      chosen = hit;
      renderChosenButtons();
      canvas.setPointerCapture(event.pointerId);
      event.preventDefault();
      settleRun();
      if (!history.batchFrom) {
        history.batchFrom = snapshotState();
        pushHistory(history.batchFrom);
      }
      if (status) status.textContent = `Moving corner ${hit + 1}.`;
    });
    canvas.addEventListener("pointermove", event => {
      if (dragging === null || busy || !canEdit()) return;
      const box = canvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(100, (event.clientX - box.left) / box.width * 100));
      const y = Math.max(0, Math.min(100, (event.clientY - box.top) / box.height * 100));
      const bay = selected();
      bay.corners[dragging] = [Number(x.toFixed(2)), Number(y.toFixed(2))];
      inputs[dragging * 2].value = String(Number(x.toFixed(2)));
      inputs[dragging * 2 + 1].value = String(Number(y.toFixed(2)));
      if (error) error.textContent = "";
      paint();
      refreshSummary();
    });
    const endDrag = () => {
      if (dragging === null) return;
      dragging = null;
      if (status) status.textContent = "Corner moved. Save the draft to keep this change.";
      paint();
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);

    /* --- Add / remove / duplicate / rename / clear --------------------------
       Duplicate and rename are server identity actions (POST /bays/actions):
       they mint or keep a stable ID on a bay that already exists on the draft.
       Unsaved geometry (including a locally added bay) is refused before the
       network call, so a reload of the server answer cannot discard edits and
       a locally minted ID is never POSTed. After duplicate, the selected bay
       is the one new ID in the server result. */
    const api = () => window.AthenaApp?.api;

    async function bayAction(payload) {
      if (!draft?.id) throw new Error("Save the draft before changing its bays.");
      const client = api();
      if (!client) throw new Error("The bay action could not be sent.");
      setBusy(true);
      try {
        const result = await client(`/api/operation-drafts/${draft.id}/bays/actions`, { method: "POST", json: payload });
        if (error) error.textContent = "";
        if (status) status.textContent = result.message;
        return result;
      } finally {
        setBusy(false);
      }
    }

    function newCornerSet() {
      return [[20, 20], [45, 20], [45, 45], [20, 45]];
    }

    if (add) add.addEventListener("click", () => {
      if (busy || !canEdit()) return;
      settleRun();
      if (bays.length >= BAY_COUNT_LIMIT) { if (error) error.textContent = `A draft keeps at most ${BAY_COUNT_LIMIT} bays.`; return; }
      const before = snapshotState();
      const bay = { id: crypto.randomUUID(), name: "", corners: newCornerSet() };
      bays.push(bay);
      selectedId = bay.id;
      renderSelect();
      loadSelectedIntoFields();
      pushHistory(before);
      if (status) status.textContent = `Bay ${bays.length} added with its own new ID. Name it and set its four corners.`;
      paint();
      refreshSummary();
      if (name) name.focus();
    });

    if (remove) remove.addEventListener("click", () => {
      if (busy || !canEdit()) return;
      const bay = selected();
      if (!bay) { if (error) error.textContent = "Select a bay to remove."; return; }
      settleRun();
      window.Athena?.askConfirm({
        title: "Remove this bay?",
        message: [
          `The bay "${bay.name || "unnamed"}" will be removed from the draft when you save.`,
          "Its stable ID goes with it: removing a bay ends its history on this draft. Undo can bring it back within this session."
        ],
        keepLabel: "Keep the bay",
        confirmLabel: "Remove the bay"
      }).then(answer => {
        if (!answer) return;
        const before = snapshotState();
        bays = bays.filter(item => item.id !== bay.id);
        selectedId = bays.length ? bays[0].id : null;
        renderSelect();
        loadSelectedIntoFields();
        pushHistory(before);
        if (status) status.textContent = "Bay removed. Save the draft to apply the removal.";
        paint();
        refreshSummary();
      });
    });

    if (duplicate) duplicate.addEventListener("click", async () => {
      if (busy || !canEdit()) return;
      const bay = selected();
      if (!bay) { if (error) error.textContent = "Select a bay to duplicate."; return; }
      settleRun();
      writeFieldsIntoSelected();
      const blocked = unsavedIdentityBlock(bay);
      if (blocked) { if (error) error.textContent = blocked; return; }
      const previousIds = persistedIds();
      const before = snapshotState();
      try {
        const result = await bayAction({ action: "duplicate", bay_id: bay.id });
        load(result.draft);
        selectedId = mintedBayId(previousIds, result.draft.bays) || bay.id;
        renderSelect();
        loadSelectedIntoFields();
        pushHistory(before);
        paint();
        refreshSummary();
        document.dispatchEvent(new CustomEvent("athena:draft-updated", { detail: result.draft }));
      } catch (problem) {
        if (error) error.textContent = problem.message;
      }
    });

    if (rename) rename.addEventListener("click", async () => {
      if (busy || !canEdit()) return;
      const bay = selected();
      if (!bay) { if (error) error.textContent = "Select a bay to rename."; return; }
      settleRun();
      writeFieldsIntoSelected();
      const newName = name ? name.value.trim() : "";
      if (!newName) { if (error) error.textContent = "Type the new name into the bay name field first."; return; }
      if (newName.length > 80) { if (error) error.textContent = "Name the bay using up to 80 characters."; return; }
      const blocked = unsavedIdentityBlock(bay, { ignoreSelectedName: true });
      if (blocked) { if (error) error.textContent = blocked; return; }
      const before = snapshotState();
      try {
        const result = await bayAction({ action: "rename", bay_id: bay.id, name: newName });
        load(result.draft);
        selectedId = bay.id;
        renderSelect();
        loadSelectedIntoFields();
        pushHistory(before);
        paint();
        refreshSummary();
        document.dispatchEvent(new CustomEvent("athena:draft-updated", { detail: result.draft }));
        rename.dispatchEvent(new CustomEvent("athena:bay-renamed", { bubbles: true, detail: { id: bay.id, name: newName } }));
      } catch (problem) {
        if (error) error.textContent = problem.message;
      }
    });

    if (clear) clear.addEventListener("click", () => {
      if (busy || !canEdit()) return;
      settleRun();
      const wasUnsaved = dirty();
      const go = () => {
        const before = snapshotState();
        bays = [];
        selectedId = null;
        dragging = null;
        renderSelect();
        loadSelectedIntoFields();
        if (error) error.textContent = "";
        if (status) status.textContent = wasUnsaved
          ? "Unsaved bay changes discarded. No bays are set now; save the draft to apply the clear."
          : bays.length ? "Editor cleared." : "Bays cleared; save the draft to apply.";
        paint();
        refreshSummary();
        if (before.bays.length) pushHistory(before);
        clear.dispatchEvent(new CustomEvent("athena:bays-cleared", { bubbles: true }));
      };
      if (wasUnsaved && window.Athena?.askConfirm) {
        window.Athena.askConfirm({
          title: "Discard the unsaved bays?",
          message: [
            "The bays in the editor have changes that have not been saved yet.",
            "Clearing now throws those changes away; nothing is saved until you save the draft. Undo can bring them back within this session."
          ],
          keepLabel: "Keep editing",
          confirmLabel: "Discard and clear"
        }).then(answer => { if (answer) go(); });
      } else {
        go();
      }
    });

    /* --- Load / read / busy ------------------------------------------------- */
    function load(nextDraft) {
      const savingSameDraft = draft && nextDraft && draft.id === nextDraft.id;
      const previousSelectedId = selectedId;
      draft = nextDraft;
      section.hidden = !draft?.recording;
      bays = (draft?.bays || []).map(bay => ({
        id: bay.id,
        name: bay.name,
        corners: bay.corners.map(corner => [Number((corner[0] * 100).toFixed(6)), Number((corner[1] * 100).toFixed(6))])
      }));
      original = JSON.stringify(bays);
      selectedId = savingSameDraft && bays.some(bay => bay.id === previousSelectedId)
        ? previousSelectedId
        : bays.length ? bays[0].id : null;
      renderSelect();
      loadSelectedIntoFields();
      dragging = null;
      hasFrame = false;
      if (error) error.textContent = "";
      // An action's own answer (set by bayAction) stays visible; a plain load
      // states the usual saved/empty line.
      if (status && status.textContent === "") {
        status.textContent = bays.length ? "Saved bays. Changes are saved with the draft." : "No bays saved yet.";
      }
      paint();
      if (savingSameDraft) {
        endBatch();
        renderHistoryButtons();
      } else {
        rememberBase();
      }
      refreshSummary();
    }

    function read() {
      if (!draft?.recording) return (draft?.bays ?? []);
      const result = bays.map(bay => ({
        id: bay.id,
        name: bay.name,
        corners: bay.corners.map(corner => [corner[0] / 100, corner[1] / 100])
      }));
      for (const bay of result) {
        if (!bay.name) throw new Error("Name every bay before saving.");
        if (bay.name.length > 80) throw new Error("Name each bay using up to 80 characters.");
        if (!bay.corners.every(corner => corner.every(value => Number.isFinite(value) && value >= 0 && value <= 1))) {
          throw new Error("Enter all four corners of every bay as percentages between 0 and 100.");
        }
        const problem = bayProblem(bay.corners.map(corner => [corner[0] * 100, corner[1] * 100]));
        if (problem) throw new Error(problem);
      }
      const ids = new Set(result.map(bay => bay.id));
      if (ids.size !== result.length) throw new Error("Each bay needs its own ID.");
      return result;
    }

    function setBusy(value) {
      busy = value;
      [...inputs, name, frame, save, clear, add, remove, duplicate, rename, undoButton, redoButton, select, ...Object.values(nudgeButtons)].forEach(input => { if (input) input.disabled = value; });
      if (!value) { renderHistoryButtons(); if (select) select.disabled = !bays.length; }
    }

    if (save) save.addEventListener("click", () => { settleRun(); onSave(); });

    renderChosenButtons();
    renderHistoryButtons();
    return { load, read, dirty, setBusy, setError: message => { if (error) error.textContent = message; } };
  };
})();
