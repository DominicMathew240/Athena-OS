/* A draft line is geometry on a recording, not an analysis or a count.
   T14: the line is named, its two sides are labelled by the user (names, never
   assumptions — Athena does not decide which side is "in"), the direction is an
   explicit ordered pair of endpoints that can only be reversed by button, and a
   stepped preview shows a person crossing so the user can confirm which way
   counts as moving from the negative side to the positive side. */
(() => {
  "use strict";
  window.createOperationLineEditor = ({ video, canEdit, onSave }) => {
    const section = document.getElementById("line-section");
    const canvas = document.getElementById("line-canvas");
    const ctx = canvas.getContext("2d");
    const snapshot = document.createElement("canvas");
    const inputs = ["line-start-x", "line-start-y", "line-end-x", "line-end-y", "line-name", "line-positive", "line-negative"].map(id => document.getElementById(id));
    const frame = document.getElementById("line-frame");
    const save = document.getElementById("line-save");
    const clear = document.getElementById("line-clear");
    const reverse = document.getElementById("line-reverse");
    const status = document.getElementById("line-status");
    const error = document.getElementById("line-error");
    const preview = document.getElementById("line-preview");
    const previewCanvas = document.getElementById("line-preview-canvas");
    const previewText = document.getElementById("line-preview-text");
    let original = "";
    let draft = null;
    let busy = false;
    let hasFrame = false;
    /* Endpoint dragging: one of 0 (start), 1 (end) or null (no drag in progress). */
    let dragging = null;
    /* The preview's stepped person: position along a crossing path, advanced by the
       preview button or the play control. It is view state only, never saved. */
    const previewState = { t: 0, forward: true, playing: false, timer: null };
    const values = () => inputs.map(input => input.value);
    const dirty = () => !!draft?.recording && JSON.stringify(values()) !== original;
    const coords = () => inputs.slice(0, 4).map(input => input.value === "" ? null : Number(input.value));
    const validPoint = (x, y) => x !== null && y !== null && Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 100 && y >= 0 && y <= 100;
    /* Lines shorter than this fraction of the picture are degenerate: they have no
     * measurable direction. Vertical and horizontal lines are fine. The server
     * refuses the same geometry with LINE_MIN_LENGTH = 1e-6 in normalized units. */
    const MIN_LENGTH_PERCENT = 1e-4;

    function setPreviewText(message) {
      if (previewText) previewText.textContent = message;
    }

    function lineLength([sx, sy, ex, ey]) {
      return Math.hypot(ex - sx, ey - sy);
    }

    /* Paint the editing canvas: the frozen frame, the line with its positive-side
       arrow, and one halo circle over the endpoint being dragged. */
    function paint() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (hasFrame) ctx.drawImage(snapshot, 0, 0, canvas.width, canvas.height);
      const [sx, sy, ex, ey] = coords();
      if (!validPoint(sx, sy)) return;
      const x1 = sx * canvas.width / 100, y1 = sy * canvas.height / 100;
      const radius = Math.max(2, Math.min(canvas.width, canvas.height) * .012);
      ctx.fillStyle = "#ffffff";
      ctx.beginPath(); ctx.arc(x1, y1, radius, 0, Math.PI * 2); ctx.fill();
      if (!validPoint(ex, ey)) return;
      const x2 = ex * canvas.width / 100, y2 = ey * canvas.height / 100;
      const dx = x2 - x1, dy = y2 - y1, length = Math.hypot(dx, dy);
      if (!length) return;
      const ax = (x1 + x2) / 2, ay = (y1 + y2) / 2;
      const arrow = Math.min(canvas.width, canvas.height) * .12;
      const nx = -dy / length, ny = dx / length;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      ctx.moveTo(ax, ay); ctx.lineTo(ax + nx * arrow, ay + ny * arrow);
      ctx.moveTo(ax + nx * arrow - nx * arrow * .35 - ny * arrow * .25, ay + ny * arrow - ny * arrow * .35 + nx * arrow * .25);
      ctx.lineTo(ax + nx * arrow, ay + ny * arrow);
      ctx.lineTo(ax + nx * arrow - nx * arrow * .35 + ny * arrow * .25, ay + ny * arrow - ny * arrow * .35 - nx * arrow * .25);
      ctx.lineWidth = Math.max(3, radius * 2); ctx.strokeStyle = "#101820"; ctx.stroke();
      ctx.lineWidth = Math.max(1.5, radius); ctx.strokeStyle = "#ffffff"; ctx.stroke();
      ctx.beginPath(); ctx.arc(x2, y2, radius, 0, Math.PI * 2); ctx.fill();
      if (dragging !== null) {
        const [hx, hy] = dragging === 0 ? [x1, y1] : [x2, y2];
        ctx.beginPath(); ctx.arc(hx, hy, radius * 2, 0, Math.PI * 2);
        ctx.lineWidth = 2; ctx.strokeStyle = "#7fd1c0"; ctx.stroke();
      }
    }

    /* --- Crossing preview -------------------------------------------------
       A stepped person walks from the negative side of the line to the positive
       side, crossing through the middle of the segment. The side names come from
       the user's own labels; the example demonstrates which direction the current
       ordered endpoints count as "moving from the negative side to the positive
       side". It is an illustration, not a detection and not a promise. */
    function paintPreview() {
      if (!previewCanvas) return;
      const pctx = previewCanvas.getContext("2d");
      const width = previewCanvas.width, height = previewCanvas.height;
      pctx.clearRect(0, 0, width, height);
      pctx.fillStyle = "#17202a";
      pctx.fillRect(0, 0, width, height);
      /* The preview shows the line in the same aspect as the real one, centred. */
      const [sx, sy, ex, ey] = coords();
      let dx = 0.6, dy = 0;
      if (validPoint(sx, sy) && validPoint(ex, ey) && lineLength([sx, sy, ex, ey]) >= MIN_LENGTH_PERCENT) {
        dx = (ex - sx) / 100; dy = (ey - sy) / 100;
      }
      const length = Math.hypot(dx, dy) || 1;
      const ux = dx / length, uy = dy / length;      /* along the line */
      const nx = -uy, ny = ux;                        /* positive side (left of the start-to-end direction) */
      const span = Math.min(width, height) * 0.4;
      const cx = width / 2, cy = height / 2;
      const x1 = cx - ux * span, y1 = cy - uy * span;
      const x2 = cx + ux * span, y2 = cy + uy * span;
      pctx.lineWidth = 4; pctx.strokeStyle = "#ffffff";
      pctx.beginPath(); pctx.moveTo(x1, y1); pctx.lineTo(x2, y2); pctx.stroke();
      const arrow = Math.min(width, height) * .14;
      pctx.beginPath(); pctx.moveTo(cx, cy); pctx.lineTo(cx + nx * arrow, cy + ny * arrow);
      pctx.moveTo(cx + nx * arrow - nx * arrow * .35 - ny * arrow * .25, cy + ny * arrow - ny * arrow * .35 + nx * arrow * .25);
      pctx.lineTo(cx + nx * arrow, cy + ny * arrow);
      pctx.lineTo(cx + nx * arrow - nx * arrow * .35 + ny * arrow * .25, cy + ny * arrow - ny * arrow * .35 - nx * arrow * .25);
      pctx.stroke();
      /* The stepped person walks across the middle, from -1 to +1 along the normal. */
      const t = previewState.t;
      const px = cx + (t * 2 - 1) * nx * span * 0.8;
      const py = cy + (t * 2 - 1) * ny * span * 0.8;
      const personRadius = Math.max(4, Math.min(width, height) * .05);
      pctx.fillStyle = "#7fd1c0";
      pctx.beginPath(); pctx.arc(px, py - personRadius * 1.1, personRadius * .45, 0, Math.PI * 2); pctx.fill();
      pctx.strokeStyle = "#7fd1c0"; pctx.lineWidth = Math.max(3, personRadius * .4);
      pctx.beginPath(); pctx.moveTo(px, py - personRadius * .5); pctx.lineTo(px, py + personRadius); pctx.stroke();
      /* A trace of the path already walked, so the direction is readable at rest. */
      pctx.strokeStyle = "rgba(127, 209, 192, .45)"; pctx.lineWidth = 2;
      pctx.beginPath();
      pctx.moveTo(cx + -nx * span * .8, cy + -ny * span * .8);
      pctx.lineTo(px, py);
      pctx.stroke();
      const side = t >= 0.5 ? "positive" : "negative";
      previewCanvas.setAttribute("data-preview-side", side);
      previewCanvas.setAttribute("data-preview-step", String(Math.round(t * 4)));
      const positive = inputs[5].value.trim() || "the arrow side";
      const negative = inputs[6].value.trim() || "the other side";
      setPreviewText(`Step ${Math.round(t * 4) + 1} of 5: the person moves from the "${negative}" side towards the "${positive}" side. With the current direction, that crossing counts as moving ${side === "positive" ? `towards "${positive}"` : `away from "${positive}"`}.`);
    }

    function stopPreviewPlay() {
      if (previewState.timer) { clearInterval(previewState.timer); previewState.timer = null; }
      previewState.playing = false;
      const button = document.getElementById("line-preview-play");
      if (button) { button.textContent = "Play the example"; button.setAttribute("data-testid", "line-preview-play"); }
    }

    function stepPreview(amount) {
      const next = previewState.t + amount;
      if (next > 1 + 1e-9 || next < -1e-9) {
        /* The walk always restarts from the negative side, never from drag luck. */
        previewState.t = 0;
        previewState.forward = true;
      } else {
        previewState.t = Math.max(0, Math.min(1, next));
      }
      paintPreview();
    }

    function playPreview() {
      if (previewState.playing) { stopPreviewPlay(); return; }
      previewState.playing = true;
      const button = document.getElementById("line-preview-play");
      if (button) { button.textContent = "Pause the example"; button.setAttribute("data-testid", "line-preview-pause"); }
      previewState.timer = setInterval(() => {
        stepPreview(0.25);
        if (previewState.t >= 1) stopPreviewPlay();
      }, 600);
    }

    function capture() {
      if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
        status.textContent = "Wait for the recording to load before choosing a frame.";
        return;
      }
      video.pause();
      canvas.width = snapshot.width = video.videoWidth;
      canvas.height = snapshot.height = video.videoHeight;
      snapshot.getContext("2d").drawImage(video, 0, 0);
      hasFrame = true;
      const saveState = dirty() ? "Line changes are not saved. " : draft?.line ? "Saved line. " : "";
      status.textContent = `${saveState}Frame ready. Drag either endpoint, click two points, or enter positions below.`;
      paint();
    }

    function load(nextDraft) {
      draft = nextDraft;
      section.hidden = !draft?.recording;
      const line = draft?.line;
      const percentage = number => String(Number((number * 100).toFixed(6)));
      const next = line
        ? [percentage(line.start[0]), percentage(line.start[1]), percentage(line.end[0]), percentage(line.end[1]), line.name, line.positive_label, line.negative_label]
        : ["", "", "", "", "", "", ""];
      inputs.forEach((input, i) => { input.value = next[i]; });
      original = JSON.stringify(values());
      dragging = null;
      hasFrame = false;
      error.textContent = "";
      status.textContent = line ? "Saved line. Changes are saved with the draft." : "No line saved yet.";
      stopPreviewPlay();
      previewState.t = 0;
      paint();
      paintPreview();
    }

    function read() {
      if (!draft?.recording) return draft?.line ?? null;
      const [sx, sy, ex, ey] = coords();
      if ([sx, sy, ex, ey].every(value => value === null)) {
        if (inputs[4].value.trim() || inputs[5].value.trim() || inputs[6].value.trim()) throw new Error("Choose both line points before naming the line and its sides.");
        return null;
      }
      if (!validPoint(sx, sy) || !validPoint(ex, ey)) throw new Error("Enter both points as percentages between 0 and 100.");
      if (lineLength([sx, sy, ex, ey]) < MIN_LENGTH_PERCENT) throw new Error("Draw the line longer: the two endpoints are too close together to give the line a direction.");
      const name = inputs[4].value.trim(), positive = inputs[5].value.trim(), negative = inputs[6].value.trim();
      if (!name || name.length > 80) throw new Error("Name the line using up to 80 characters.");
      if (!positive || !negative || positive.length > 40 || negative.length > 40) throw new Error("Label both sides, using up to 40 characters each.");
      return { start: [sx / 100, sy / 100], end: [ex / 100, ey / 100], name, positive_label: positive, negative_label: negative };
    }

    function setBusy(value) {
      busy = value;
      [...inputs, frame, save, clear, reverse].forEach(input => { if (input) input.disabled = value; });
    }
    inputs.forEach(input => input.addEventListener("input", () => { error.textContent = ""; paint(); paintPreview(); }));
    canvas.addEventListener("pointerdown", event => {
      if (busy || !canEdit() || !hasFrame || event.button !== 0) return;
      const box = canvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(100, (event.clientX - box.left) / box.width * 100));
      const y = Math.max(0, Math.min(100, (event.clientY - box.top) / box.height * 100));
      /* Drag an endpoint if the press lands on one; otherwise place the start point. */
      const [sx, sy, ex, ey] = coords();
      const grab = Math.min(canvas.width, canvas.height) * .012 / Math.min(box.width, box.height) * 100 * 1.5;
      if (validPoint(sx, sy) && Math.hypot(x - sx, y - sy) <= grab) dragging = 0;
      else if (validPoint(ex, ey) && Math.hypot(x - ex, y - ey) <= grab) dragging = 1;
      if (dragging !== null) {
        canvas.setPointerCapture(event.pointerId);
        event.preventDefault();
        status.textContent = dragging === 0 ? "Moving the start point." : "Moving the end point.";
        return;
      }
      dragging = null;
      inputs[0].value = x.toFixed(2);
      inputs[1].value = y.toFixed(2);
      inputs[2].value = "";
      inputs[3].value = "";
      status.textContent = "Start point selected. Choose the end point.";
      error.textContent = "";
      paint();
    });
    canvas.addEventListener("pointermove", event => {
      if (dragging === null || busy || !canEdit()) return;
      const box = canvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(100, (event.clientX - box.left) / box.width * 100));
      const y = Math.max(0, Math.min(100, (event.clientY - box.top) / box.height * 100));
      inputs[dragging * 2].value = x.toFixed(2);
      inputs[dragging * 2 + 1].value = y.toFixed(2);
      error.textContent = "";
      paint();
    });
    const endDrag = () => {
      if (dragging === null) return;
      dragging = null;
      status.textContent = "Endpoint moved. Save the draft to keep this change.";
      paint();
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    frame.addEventListener("click", capture);
    video.addEventListener("loadeddata", () => { if (!section.hidden) capture(); });
    clear.addEventListener("click", () => {
      inputs.forEach(input => { input.value = ""; });
      dragging = null; error.textContent = "";
      status.textContent = "Line cleared; save the draft to apply.";
      previewState.t = 0;
      paint(); paintPreview();
    });
    /* Reversing the direction is an explicit, single-purpose action: swap the two
       endpoints and swap the two side labels with them, so "Street"/"Shop" keep
       naming the same physical sides while the ordered pair (and therefore which
       movement counts as towards the arrow) flips. */
    reverse.addEventListener("click", () => {
      if (busy || !canEdit()) return;
      const [sx, sy, ex, ey] = coords();
      if (!validPoint(sx, sy) || !validPoint(ex, ey)) { error.textContent = "Choose both line points before reversing the direction."; return; }
      if (lineLength([sx, sy, ex, ey]) < MIN_LENGTH_PERCENT) { error.textContent = "Draw the line longer before reversing it."; return; }
      inputs[0].value = String(ex); inputs[1].value = String(ey);
      inputs[2].value = String(sx); inputs[3].value = String(sy);
      const positive = inputs[5].value, negative = inputs[6].value;
      inputs[5].value = negative; inputs[6].value = positive;
      dragging = null; error.textContent = "";
      previewState.t = 0;
      status.textContent = "Direction reversed: the start and end points swapped, and the side labels moved with them. Save the draft to keep this change.";
      paint(); paintPreview();
      if (reverse) reverse.dispatchEvent(new CustomEvent("athena:line-direction-reversed", { bubbles: true }));
    });
    if (preview) {
      const play = document.getElementById("line-preview-play");
      const step = document.getElementById("line-preview-step");
      if (play) play.addEventListener("click", playPreview);
      if (step) step.addEventListener("click", () => stepPreview(0.25));
    }
    save.addEventListener("click", onSave);
    return { load, read, dirty, setBusy, setError: message => { error.textContent = message; }, repaint: paintPreview };
  };
})();
