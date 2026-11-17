/* A draft line is geometry on a recording, not an analysis or a count. */
(() => {
  "use strict";
  window.createOperationLineEditor = ({ video, canEdit, onSave }) => {
    const section = document.getElementById("line-section");
    const canvas = document.getElementById("line-canvas");
    const ctx = canvas.getContext("2d");
    const snapshot = document.createElement("canvas");
    const inputs = ["line-start-x", "line-start-y", "line-end-x", "line-end-y", "line-positive", "line-negative"].map(id => document.getElementById(id));
    const frame = document.getElementById("line-frame");
    const save = document.getElementById("line-save");
    const clear = document.getElementById("line-clear");
    const status = document.getElementById("line-status");
    const error = document.getElementById("line-error");
    let original = "";
    let draft = null;
    let pickingEnd = false;
    let busy = false;
    let hasFrame = false;
    const values = () => inputs.map(input => input.value);
    const dirty = () => !!draft?.recording && JSON.stringify(values()) !== original;
    const coords = () => inputs.slice(0, 4).map(input => input.value === "" ? null : Number(input.value));
    const validPoint = (x, y) => x !== null && y !== null && Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 100 && y >= 0 && y <= 100;

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
      status.textContent = `${saveState}Frame ready. Choose a start and an end point, or enter their positions below.`;
      paint();
    }

    function load(nextDraft) {
      draft = nextDraft;
      section.hidden = !draft?.recording;
      const line = draft?.line;
      const percentage = number => String(Number((number * 100).toFixed(6)));
      const next = line ? [percentage(line.start[0]), percentage(line.start[1]), percentage(line.end[0]), percentage(line.end[1]), line.positive_label, line.negative_label] : ["", "", "", "", "", ""];
      inputs.forEach((input, i) => { input.value = next[i]; });
      original = JSON.stringify(values());
      pickingEnd = false;
      hasFrame = false;
      error.textContent = "";
      status.textContent = line ? "Saved line. Changes are saved with the draft." : "No line saved yet.";
      paint();
    }

    function read() {
      if (!draft?.recording) return draft?.line ?? null;
      const [sx, sy, ex, ey] = coords();
      if ([sx, sy, ex, ey].every(value => value === null)) {
        if (inputs[4].value.trim() || inputs[5].value.trim()) throw new Error("Choose both line points before naming directions.");
        return null;
      }
      if (!validPoint(sx, sy) || !validPoint(ex, ey)) throw new Error("Enter both points as percentages between 0 and 100.");
      if (sx === ex && sy === ey) throw new Error("The two line points must be different.");
      const positive = inputs[4].value.trim(), negative = inputs[5].value.trim();
      if (!positive || !negative || positive.length > 40 || negative.length > 40) throw new Error("Name both directions, using up to 40 characters each.");
      return { start: [sx / 100, sy / 100], end: [ex / 100, ey / 100], positive_label: positive, negative_label: negative };
    }

    function setBusy(value) {
      busy = value;
      [...inputs, frame, save, clear].forEach(input => { input.disabled = value; });
    }
    inputs.forEach(input => input.addEventListener("input", () => { error.textContent = ""; paint(); }));
    canvas.addEventListener("pointerdown", event => {
      if (busy || !canEdit() || !hasFrame || event.button !== 0) return;
      const box = canvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(100, (event.clientX - box.left) / box.width * 100));
      const y = Math.max(0, Math.min(100, (event.clientY - box.top) / box.height * 100));
      const offset = pickingEnd ? 2 : 0;
      inputs[offset].value = x.toFixed(2);
      inputs[offset + 1].value = y.toFixed(2);
      if (!pickingEnd) { inputs[2].value = ""; inputs[3].value = ""; }
      pickingEnd = !pickingEnd;
      status.textContent = pickingEnd ? "Start point selected. Choose the end point." : "Line selected. Name both directions and save the draft.";
      error.textContent = "";
      paint();
    });
    frame.addEventListener("click", capture);
    video.addEventListener("loadeddata", () => { if (!section.hidden) capture(); });
    clear.addEventListener("click", () => { inputs.forEach(input => { input.value = ""; }); pickingEnd = false; error.textContent = ""; status.textContent = "Line cleared; save the draft to apply."; paint(); });
    save.addEventListener("click", onSave);
    return { load, read, dirty, setBusy, setError: message => { error.textContent = message; } };
  };
})();
