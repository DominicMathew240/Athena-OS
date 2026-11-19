/* Shared replay surface (T13): one video, one scale-correct overlay and plain controls.

   A replay surface is video playback plus a canvas drawn over it. Everything drawn is described in
   normalized coordinates (0–1 fractions of the video picture), and the canvas only ever maps those
   fractions to the pixels it has right now; nothing here saves, transforms or re-encodes geometry,
   so resizing the viewport can never change a saved line or mark. Visibility toggles are view state
   only: they hide objects on this screen and are never sent anywhere.

   Shape of one overlay object:
     { id: string, label: string, kind: "line", start: [x, y], end: [x, y], positive_label, negative_label }
   The line is drawn exactly like the draft line editor draws it (two end dots, a stroke and a normal
   arrow naming the positive side), so the same geometry looks the same wherever it is shown. */
(() => {
  "use strict";
  window.createReplaySurface = ({ root, video }) => {
    if (!root || !video) throw new Error("createReplaySurface needs a root element and a video element.");
    const stage = root.querySelector(".replay-stage");
    const canvas = root.querySelector(".replay-overlay");
    const play = root.querySelector(".replay-play");
    const scrub = root.querySelector(".replay-scrub");
    const time = root.querySelector(".replay-time");
    const duration = root.querySelector(".replay-duration");
    const toggles = root.querySelector(".replay-toggles");
    if (!stage || !canvas || !play || !scrub || !time || !duration || !toggles) {
      throw new Error("The replay surface markup is incomplete.");
    }
    const ctx = canvas.getContext("2d");
    let objects = [];
    let hidden = new Set();
    let marks = [];
    let playing = false;
    let wasPlaying = false;

    /* The canvas backing store is the video's own pixel size, clamped so a huge recording cannot
       allocate an unbounded canvas. The CSS size follows the element; drawing uses fractions, so the
       two never need to agree for the overlay to stay scale-correct. */
    function fit() {
      const width = Math.min(video.videoWidth || 640, 1920);
      const height = Math.min(video.videoHeight || 360, 1080);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
    }

    function drawLine(object) {
      const x1 = object.start[0] * canvas.width, y1 = object.start[1] * canvas.height;
      const x2 = object.end[0] * canvas.width, y2 = object.end[1] * canvas.height;
      const radius = Math.max(2, Math.min(canvas.width, canvas.height) * .012);
      ctx.fillStyle = "#ffffff";
      ctx.beginPath(); ctx.arc(x1, y1, radius, 0, Math.PI * 2); ctx.fill();
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

    function paint() {
      fit();
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      for (const object of objects) {
        if (hidden.has(object.id) || !Array.isArray(object.start) || !Array.isArray(object.end)) continue;
        drawLine(object);
      }
      /* Reference marks: one small tick per saved reference event, drawn at the marked height so
         a person can see where their labels sit while scrubbing. */
      const total = video.duration;
      if (Number.isFinite(total) && total > 0 && marks.length) {
        ctx.fillStyle = "#7fd1c0";
        for (const mark of marks) {
          const time = Number(mark?.time);
          if (!Number.isFinite(time) || time < 0 || time > total) continue;
          const x = time / total * canvas.width;
          ctx.fillRect(x - 2, 0, 4, Math.max(6, canvas.height * .05));
        }
      }
    }

    const format = seconds => {
      if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
      const whole = Math.floor(seconds);
      const minutes = Math.floor(whole / 60);
      const rest = whole % 60;
      const tenths = Math.floor((seconds - whole) * 10);
      return `${minutes}:${String(rest).padStart(2, "0")}.${tenths}`;
    };

    function renderToggles() {
      toggles.replaceChildren();
      if (!objects.length) {
        toggles.hidden = true;
        return;
      }
      toggles.hidden = false;
      for (const object of objects) {
        const label = document.createElement("label");
        label.className = "replay-toggle";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = !hidden.has(object.id);
        box.setAttribute("data-object-id", object.id);
        box.setAttribute("data-testid", `replay-toggle-${object.id}`);
        const name = document.createElement("span");
        name.textContent = object.label || "Counting line";
        label.append(box, name);
        box.addEventListener("change", () => {
          if (box.checked) hidden.delete(object.id);
          else hidden.add(object.id);
          paint();
        });
        toggles.append(label);
      }
    }

    function syncScrub() {
      const total = video.duration;
      if (Number.isFinite(total) && total > 0) {
        scrub.disabled = false;
        if (!scrubbing) scrub.value = String(video.currentTime / total);
      } else {
        scrub.disabled = true;
      }
      time.textContent = format(video.currentTime);
      duration.textContent = format(Number.isFinite(total) ? total : 0);
    }

    let scrubbing = false;
    scrub.addEventListener("pointerdown", () => { wasPlaying = playing; scrubbing = true; });
    scrub.addEventListener("input", () => {
      const total = video.duration;
      if (!Number.isFinite(total) || total <= 0) return;
      const next = Number(scrub.value) * total;
      if (Number.isFinite(next)) video.currentTime = Math.max(0, Math.min(total, next));
      time.textContent = format(video.currentTime);
    });
    scrub.addEventListener("change", () => {
      scrubbing = false;
      if (wasPlaying) video.play().catch(() => {});
    });

    function setPlaying(next) {
      playing = next;
      play.textContent = playing ? "Pause" : "Play";
      play.setAttribute("aria-label", playing ? "Pause the recording" : "Play the recording");
      play.setAttribute("data-testid", playing ? "replay-pause" : "replay-play");
    }
    play.addEventListener("click", () => {
      if (playing) {
        video.pause();
      } else {
        video.play().catch(() => { setPlaying(false); });
      }
    });
    video.addEventListener("play", () => setPlaying(true));
    video.addEventListener("pause", () => setPlaying(false));
    video.addEventListener("ended", () => setPlaying(false));

    /* Redraw whenever the displayed size changes: the fractions are unchanged, only the pixel
       mapping is, so the overlay tracks the video at every viewport size. */
    if (window.ResizeObserver) new ResizeObserver(paint).observe(stage);
    window.addEventListener("resize", paint);
    video.addEventListener("loadeddata", () => { paint(); syncScrub(); });
    video.addEventListener("timeupdate", syncScrub);
    /* Duration arriving late also means the marks can be placed now. */
    video.addEventListener("durationchange", () => { paint(); syncScrub(); });
    video.addEventListener("loadedmetadata", syncScrub);

    return {
      /* Show these normalized objects on the overlay, with optional time marks. Visibility state
         is reset by id: an object that is still shown stays shown, one that is gone loses its toggle. */
      show(nextObjects, nextMarks = []) {
        objects = Array.isArray(nextObjects) ? nextObjects.filter(Boolean) : [];
        marks = Array.isArray(nextMarks) ? nextMarks.filter(Boolean) : [];
        hidden = new Set([...hidden].filter(id => objects.some(object => object.id === id)));
        renderToggles();
        paint();
      },
      paint,
      syncScrub,
      /* True while every object is visible; used by tests and callers that need to prove a toggle. */
      allVisible: () => objects.every(object => !hidden.has(object.id)),
      root, video, canvas
    };
  };
})();
