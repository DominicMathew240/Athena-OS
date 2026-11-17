import { FUSION_SCENARIOS, OUTCOMES } from "./studioData.js";

export function setupHiDpiCanvas(canvas, ctx, logicalW, logicalH) {
  const dpr = Math.min(Math.max(Number(window.devicePixelRatio) || 2, 2), 3);
  const targetW = Math.round(logicalW * dpr);
  const targetH = Math.round(logicalH * dpr);
  if (canvas.width !== targetW || canvas.height !== targetH) {
    canvas.width = targetW;
    canvas.height = targetH;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  return { w: logicalW, h: logicalH };
}

function sevColor(sev) {
  if (sev === "high") return "#ff6b3d";
  if (sev === "medium") return "#ffb020";
  if (sev === "ok") return "#34d399";
  return "#38bdf8";
}

function drawCoverImage(ctx, imgObj, x, y, w, h, alpha = 0.65) {
  if (!imgObj || !imgObj.complete || !imgObj.naturalWidth) return false;
  ctx.save();
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, 10);
  ctx.clip();
  const scale = Math.max(w / imgObj.naturalWidth, h / imgObj.naturalHeight);
  const dw = imgObj.naturalWidth * scale;
  const dh = imgObj.naturalHeight * scale;
  ctx.globalAlpha = alpha;
  ctx.drawImage(imgObj, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
  ctx.restore();
  return true;
}

function drawStudioGrid(ctx, w, h) {
  ctx.fillStyle = "#080a0e";
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(255, 255, 255, 0.045)";
  ctx.lineWidth = 1;
  for (let x = 24; x < w; x += 32) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  for (let y = 24; y < h; y += 32) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
}

function drawBadge(ctx, text, x, y, bg, fg = "#04121c") {
  ctx.font = "700 11px 'Martian Mono', monospace";
  const padX = 8;
  const width = ctx.measureText(text).width + padX * 2;
  ctx.fillStyle = bg;
  ctx.beginPath();
  ctx.roundRect(x, y, width, 20, 5);
  ctx.fill();
  ctx.fillStyle = fg;
  ctx.fillText(text, x + padX, y + 14);
  return width;
}

function drawZoneChrome(ctx, w, h, leftTitle) {
  const splitX = Math.round(w * 0.62);
  ctx.fillStyle = "#9aa5b5";
  ctx.font = "600 10.5px 'Martian Mono', monospace";
  ctx.fillText(leftTitle, 24, 24);
  ctx.strokeStyle = "rgba(255, 255, 255, 0.14)";
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(splitX, 16); ctx.lineTo(splitX, h - 16); ctx.stroke();

  const rx = splitX + 18;
  const rw = w - rx - 18;
  ctx.fillStyle = "#101319";
  ctx.beginPath(); ctx.roundRect(rx, 16, rw, h - 32, 10); ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.22)";
  ctx.lineWidth = 1.2;
  ctx.stroke();
  return { splitX, rx, rw };
}

function drawSolutionPanel(ctx, rx, rw, h, spec) {
  const pad = 16;
  ctx.fillStyle = "#838e9e";
  ctx.font = "600 10px 'Martian Mono', monospace";
  ctx.fillText("LIVE SOLUTION PREVIEW", rx + pad, 38);

  drawBadge(ctx, spec.verdict, rx + pad, 48, spec.verdictColor, spec.verdictFg || "#04121c");
  ctx.fillStyle = "#ffffff";
  ctx.font = "700 16px 'Hanken Grotesk', sans-serif";
  ctx.fillText(spec.headline, rx + pad, 92);

  let y = 114;
  spec.rows.forEach((row) => {
    ctx.fillStyle = "rgba(255, 255, 255, 0.04)";
    ctx.beginPath(); ctx.roundRect(rx + pad, y, rw - pad * 2, 42, 7); ctx.fill();
    ctx.fillStyle = "#838e9e";
    ctx.font = "600 10.5px 'Martian Mono', monospace";
    ctx.fillText(row.label, rx + pad + 10, y + 16);
    ctx.fillStyle = row.color || "#eef2f6";
    ctx.font = "700 13px 'Hanken Grotesk', sans-serif";
    ctx.fillText(row.value, rx + pad + 10, y + 34);
    if (typeof row.bar === "number") {
      const bw = 72;
      const bx = rx + rw - pad - bw - 10;
      ctx.fillStyle = "rgba(255,255,255,0.08)";
      ctx.fillRect(bx, y + 18, bw, 7);
      ctx.fillStyle = row.color || "#38bdf8";
      ctx.fillRect(bx, y + 18, bw * Math.max(0.05, Math.min(1, row.bar)), 7);
    }
    y += 50;
  });

  const btnY = h - 62;
  drawBadge(ctx, spec.btnPrimary, rx + pad, btnY, "#38bdf8", "#04121c");
  drawBadge(ctx, spec.btnSecondary, rx + pad + 132, btnY, "rgba(255,255,255,0.08)", "#c9d3df");
}

function drawAudioCanvas(ctx, w, h, data, stage, activeSample, animPhase) {
  const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · TRAIN AUDIO · SPECTROGRAM + WAVEFORM");
  const padX = 24;
  const plotW = splitX - padX * 2;
  const specTop = 44;
  const specH = 128;
  const waveTop = 194;
  const waveH = 134;

  const cols = 42;
  const rows = 10;
  const cw = plotW / cols;
  const ch = specH / rows;
  for (let c = 0; c < cols; c += 1) {
    const t = c / cols;
    const inFault = t >= 0.34 && t <= 0.62;
    const inSpike = t >= 0.68 && t <= 0.92;
    for (let r = 0; r < rows; r += 1) {
      let energy = 0.14 + 0.18 * Math.sin(c * 0.45 + r * 0.6);
      if (r >= 7) energy += 0.25;
      if (inFault && (r === 2 || r === 3)) energy = 0.94;
      if (inSpike && r <= 4) energy = 0.72 + 0.16 * Math.cos(c + r);
      energy = Math.max(0.08, Math.min(1, energy));
      ctx.fillStyle = energy > 0.78 ? "rgba(255, 107, 61, 0.85)"
        : energy > 0.52 ? "rgba(255, 176, 32, 0.68)"
        : `rgba(56, 189, 248, ${(energy * 0.55).toFixed(2)})`;
      ctx.fillRect(padX + c * cw + 1, specTop + r * ch + 1, cw - 2, ch - 2);
    }
  }

  if (stage !== "raw") {
    data.samples.forEach((sample, idx) => {
      const [rxBox, , rwBox] = sample.rect;
      const x = rxBox * w;
      const bw = rwBox * w;
      const selected = idx === activeSample;
      const col = sevColor(sample.sev);
      ctx.fillStyle = selected ? `${col}2a` : `${col}10`;
      ctx.fillRect(x, specTop - 4, bw, specH + waveH + 34);
      ctx.strokeStyle = col;
      ctx.lineWidth = selected ? 2.5 : 1.2;
      ctx.strokeRect(x, specTop - 4, bw, specH + waveH + 34);
      drawBadge(ctx, ["1 Healthy", "2 3.4 kHz fault", "3 Cavitation"][idx] || sample.label, x, specTop - 22, col, sample.sev === "high" ? "#fff" : "#04121c");
    });
  }

  const midY = waveTop + waveH / 2;
  for (let i = 0; i < 68; i += 1) {
    const t = i / 68;
    let amp = 0.18 + 0.12 * Math.sin(i * 0.6 + animPhase * 2);
    if (t >= 0.34 && t <= 0.62) amp = 0.45 + 0.42 * Math.abs(Math.sin(i * 1.15 + animPhase));
    if (t >= 0.68 && t <= 0.92) amp = 0.35 + 0.35 * Math.abs(Math.cos(i * 1.7));
    const barH = amp * waveH;
    const inSelected = stage !== "raw" && (() => {
      const [rxBox, , rwBox] = data.samples[activeSample].rect;
      return t * splitX >= rxBox * w && t * splitX <= (rxBox + rwBox) * w;
    })();
    ctx.fillStyle = inSelected ? sevColor(data.samples[activeSample].sev) : "rgba(125, 211, 252, 0.72)";
    ctx.fillRect(padX + t * plotW, midY - barH / 2, 5, barH);
  }

  const playX = padX + ((animPhase * 0.12) % 1) * plotW;
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 1.6;
  ctx.beginPath(); ctx.moveTo(playX, specTop); ctx.lineTo(playX, waveTop + waveH); ctx.stroke();

  const sample = data.samples[activeSample];
  drawSolutionPanel(ctx, rx, rw, h, {
    verdict: stage === "raw" ? "UNLABELED AUDIO" : sample.sev === "high" ? "FAULT DETECTED" : sample.sev === "medium" ? "ANOMALY SPIKE" : "BASELINE NORMAL",
    verdictColor: stage === "raw" ? "#7d8ba0" : sevColor(sample.sev),
    verdictFg: sample.sev === "high" ? "#fff" : "#04121c",
    headline: stage === "raw" ? "Ready for timeline labeling" : sample.label,
    rows: [
      { label: "PEAK BAND", value: activeSample === 1 ? "3.4 kHz (+18 dB)" : activeSample === 2 ? "8.1 kHz transient" : "60 Hz nominal", color: sevColor(sample.sev), bar: activeSample === 1 ? 0.92 : activeSample === 2 ? 0.76 : 0.25 },
      { label: "EVENT F1 · MISSED", value: "0.94 · 1 / 42 faults", color: "#34d399", bar: 0.94 },
      { label: "FALSE ALARMS / HR", value: "0.4 per hour (ONNX)", color: "#7dd3fc", bar: 0.16 },
    ],
    btnPrimary: "Confirm Fault",
    btnSecondary: "Flag False Alarm",
  });
}

function drawVisionCanvas(ctx, w, h, data, stage, activeSample, images) {
  const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · TRAIN PICTURES · MANUAL + AUTO-SUGGEST BOXES");
  drawCoverImage(ctx, images.welding, 24, 38, splitX - 42, h - 56, stage === "raw" ? 0.9 : 0.78);

  if (stage !== "raw") {
    data.samples.forEach((sample, idx) => {
      const [rxBox, ry, rwBox, rh] = sample.rect;
      const x = rxBox * w;
      const y = ry * h;
      const bw = rwBox * w;
      const bh = rh * h;
      const col = sevColor(sample.sev);
      const selected = idx === activeSample;

      ctx.save();
      if (idx === 1 && stage === "label") ctx.setLineDash([6, 4]);
      ctx.fillStyle = selected ? `${col}30` : `${col}12`;
      ctx.fillRect(x, y, bw, bh);
      ctx.strokeStyle = col;
      ctx.lineWidth = selected ? 3 : 1.8;
      ctx.strokeRect(x, y, bw, bh);
      ctx.restore();

      drawBadge(ctx, `${idx + 1} ${sample.label}`, x, Math.max(40, y - 22), col, sample.sev === "high" ? "#fff" : "#04121c");
      if (selected) {
        [[x, y], [x + bw, y], [x, y + bh], [x + bw, y + bh]].forEach(([cx, cy]) => {
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(cx - 4, cy - 4, 8, 8);
        });
      }
    });
  } else {
    drawBadge(ctx, "RAW PHOTO · SHA-256 VERIFIED", 40, 56, "#101319", "#eef2f6");
  }

  const sample = data.samples[activeSample];
  drawSolutionPanel(ctx, rx, rw, h, {
    verdict: stage === "raw" ? "0 BOXES YET" : "NEEDS ATTENTION",
    verdictColor: stage === "raw" ? "#7d8ba0" : "#ffb020",
    headline: "Operator Check Screen v1.4",
    rows: [
      { label: "SELECTED FINDING", value: sample.label, color: sevColor(sample.sev), bar: 0.91 },
      { label: "HELD-OUT CHECK", value: "mAP50 0.91 · Recall 94%", color: "#34d399", bar: 0.91 },
      { label: "HUMAN REVIEW GATE", value: "418 accepted · 14 pending", color: "#7dd3fc", bar: 0.85 },
    ],
    btnPrimary: "Approve Model",
    btnSecondary: "Fix Missed Box",
  });
}

function drawOcrCanvas(ctx, w, h, data, stage, activeSample) {
  const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · HANDWRITING SCAN · LINE CROPS + TrOCR CORRECTIONS");
  ctx.fillStyle = "#10141c";
  ctx.beginPath(); ctx.roundRect(24, 38, splitX - 42, h - 56, 10); ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.16)";
  ctx.stroke();

  const lines = [
    { pred: stage === "raw" ? "Valvc 128 — lnlet" : "Valve 12B — Inlet", y: 95 },
    { pred: stage === "raw" ? "Seal worm, weaping" : "Seal worn, weeping oil", y: 185 },
    { pred: stage === "raw" ? "2.4 bar · A. Rahmn" : "2.4 bar · Sign: A. Rahman", y: 275 },
  ];

  lines.forEach((line, idx) => {
    const sample = data.samples[idx];
    const [rxBox, ry, rwBox, rh] = sample.rect;
    const x = rxBox * w;
    const y = ry * h;
    const bw = rwBox * w;
    const bh = rh * h;
    const selected = idx === activeSample;
    const col = sevColor(sample.sev);

    ctx.strokeStyle = "rgba(255, 255, 255, 0.14)";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(42, line.y + 22); ctx.lineTo(splitX - 38, line.y + 22); ctx.stroke();

    ctx.strokeStyle = "#eef2f6";
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    for (let s = 0; s <= 26; s += 1) {
      const px = 46 + s * 8.5;
      const py = line.y + 8 + Math.sin(s * 0.9 + idx * 2) * 6 + Math.cos(s * 2.1) * 3.5;
      if (s === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.stroke();

    if (stage !== "raw") {
      ctx.fillStyle = selected ? `${col}20` : `${col}0c`;
      ctx.fillRect(x, y, bw, bh);
      ctx.strokeStyle = col;
      ctx.lineWidth = selected ? 2.4 : 1;
      ctx.strokeRect(x, y, bw, bh);
    }

    ctx.font = "600 12.5px 'Martian Mono', monospace";
    ctx.fillStyle = stage === "raw" ? "#fbbf24" : "#34d399";
    ctx.fillText(line.pred, 300, line.y + 12);
  });

  drawSolutionPanel(ctx, rx, rw, h, {
    verdict: stage === "raw" ? "BASE OCR ONLY" : "TrOCR FINE-TUNED",
    verdictColor: stage === "raw" ? "#ffb020" : "#34d399",
    headline: "Form Reader & Field Output",
    rows: [
      { label: "UNIT FIELD", value: "Valve 12B — Inlet", color: "#7dd3fc", bar: 0.98 },
      { label: "PRESSURE READING", value: "2.4 bar (verified)", color: "#34d399", bar: 0.97 },
      { label: "ERROR REDUCTION", value: "CER 2.1% (-14% vs base)", color: "#34d399", bar: 0.92 },
    ],
    btnPrimary: "Save Correction",
    btnSecondary: "Export Fields",
  });
}

function drawDocumentsCanvas(ctx, w, h, data, stage, activeSample) {
  const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · TEXT WORK ORDER · NAMED-ENTITY SPANS");
  ctx.fillStyle = "#10141c";
  ctx.beginPath(); ctx.roundRect(24, 38, splitX - 42, h - 56, 10); ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.16)";
  ctx.stroke();

  const rows = [
    { prefix: "Unit: ", span: "Pump P-104B", tag: "EQUIPMENT", y: 105 },
    { prefix: "Issue: ", span: "seal weep at 78 °C", tag: "FAULT+METRIC", y: 192 },
    { prefix: "Next: ", span: "Isolate & replace gasket", tag: "ACTION", y: 279 },
  ];

  rows.forEach((row, idx) => {
    const sample = data.samples[idx];
    const [rxBox, ry, rwBox, rh] = sample.rect;
    const selected = idx === activeSample;
    const col = sevColor(sample.sev);
    if (stage !== "raw") {
      ctx.fillStyle = selected ? `${col}26` : `${col}10`;
      ctx.beginPath();
      ctx.roundRect(rxBox * w, ry * h, rwBox * w, rh * h, 8);
      ctx.fill();
      ctx.strokeStyle = col;
      ctx.lineWidth = selected ? 2.4 : 1;
      ctx.stroke();
    }
    ctx.font = "500 15px 'Hanken Grotesk', sans-serif";
    ctx.fillStyle = "#838e9e";
    ctx.fillText(row.prefix, 48, row.y);
    ctx.fillStyle = "#ffffff";
    ctx.fillText(row.span, 102, row.y);
    if (stage !== "raw") {
      drawBadge(ctx, row.tag, splitX - 155, row.y - 16, col, sample.sev === "high" ? "#fff" : "#04121c");
    }
  });

  drawSolutionPanel(ctx, rx, rw, h, {
    verdict: stage === "raw" ? "RAW TEXT" : "STRUCTURED RECORD",
    verdictColor: stage === "raw" ? "#7d8ba0" : "#38bdf8",
    headline: "Class: Rotating Equipment",
    rows: [
      { label: "EQUIPMENT · FAULT", value: "Pump P-104B · seal weep", color: "#ff6b3d", bar: 0.95 },
      { label: "ACTION EXTRACTED", value: "Isolate & replace gasket", color: "#34d399", bar: 0.92 },
      { label: "HELD-OUT CLASS F1", value: "0.92 (deduped split)", color: "#7dd3fc", bar: 0.92 },
    ],
    btnPrimary: "Confirm Fields",
    btnSecondary: "Edit Span",
  });
}

function drawMovementsCanvas(ctx, w, h, data, stage, activeSample, images) {
  const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · POSE & HAND LANDMARKS · 17 BODY + 21 HAND PTS");
  drawCoverImage(ctx, images.worker, 24, 38, splitX - 42, h - 56, 0.62);

  const poses = [
    { cx: 155, angle: 0.25, col: sevColor("ok"), label: "Safe squat (16 deg)" },
    { cx: 335, angle: 0.78, col: sevColor("high"), label: "Bend alert (48 deg)" },
  ];

  poses.forEach((p, idx) => {
    const selected = idx === activeSample || (activeSample === 2 && idx === 1);
    const hipY = 225;
    const hipX = p.cx;
    const shX = hipX + Math.sin(p.angle) * 82;
    const shY = hipY - Math.cos(p.angle) * 82;
    const headX = shX + Math.sin(p.angle * 0.6) * 22;
    const headY = shY - 26;
    const kneeX = hipX + (idx === 0 ? 26 : 10);
    const kneeY = hipY + 46;
    const footX = hipX + 2;
    const footY = hipY + 95;
    const handX = shX + (activeSample === 2 && idx === 1 ? 48 : 30);
    const handY = shY + (activeSample === 2 && idx === 1 ? -22 : 34);

    if (stage === "raw") {
      ctx.strokeStyle = "rgba(255, 255, 255, 0.45)";
      ctx.lineWidth = 2;
      ctx.strokeRect(hipX - 60, 75, 130, 250);
      return;
    }

    const boneColor = selected ? p.col : "rgba(125, 211, 252, 0.72)";
    ctx.strokeStyle = boneColor;
    ctx.lineWidth = selected ? 3.8 : 2.2;
    ctx.beginPath();
    ctx.moveTo(headX, headY); ctx.lineTo(shX, shY);
    ctx.lineTo(hipX, hipY); ctx.lineTo(kneeX, kneeY); ctx.lineTo(footX, footY);
    ctx.moveTo(shX, shY); ctx.lineTo(handX, handY);
    ctx.stroke();

    [[headX, headY], [shX, shY], [hipX, hipY], [kneeX, kneeY], [footX, footY], [handX, handY]].forEach(([jx, jy]) => {
      ctx.fillStyle = "#ffffff";
      ctx.beginPath(); ctx.arc(jx, jy, selected ? 5 : 3.5, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = boneColor; ctx.lineWidth = 2; ctx.stroke();
    });

    drawBadge(ctx, p.label, hipX - 60, 56, boneColor, p.col === "#ff6b3d" ? "#fff" : "#04121c");
  });

  if (stage !== "raw") {
    const hx = 485;
    const hy = 120;
    ctx.fillStyle = "rgba(12, 15, 20, 0.92)";
    ctx.beginPath(); ctx.roundRect(hx - 40, hy - 42, 118, 128, 10); ctx.fill();
    ctx.strokeStyle = activeSample === 2 ? "#38bdf8" : "rgba(255,255,255,0.25)";
    ctx.lineWidth = activeSample === 2 ? 2.4 : 1;
    ctx.stroke();
    ctx.fillStyle = "#838e9e";
    ctx.font = "600 10px 'Martian Mono', monospace";
    ctx.fillText("21-PT HAND", hx - 28, hy - 22);
    const wrist = [hx + 18, hy + 58];
    [[hx - 14, hy + 20], [hx + 2, hy - 6], [hx + 18, hy - 12], [hx + 34, hy - 6], [hx + 46, hy + 12]].forEach(([tx, ty]) => {
      ctx.strokeStyle = "#38bdf8"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(wrist[0], wrist[1]); ctx.lineTo(tx, ty); ctx.stroke();
      ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(tx, ty, 3, 0, Math.PI * 2); ctx.fill();
    });
  }

  const sample = data.samples[activeSample];
  drawSolutionPanel(ctx, rx, rw, h, {
    verdict: stage === "raw" ? "SESSION GROUPED" : sample.sev === "high" ? "STRAIN ALERT" : "SAFE POSTURE",
    verdictColor: stage === "raw" ? "#7d8ba0" : sevColor(sample.sev),
    verdictFg: sample.sev === "high" ? "#fff" : "#04121c",
    headline: "Ergonomic & Gesture Monitor",
    rows: [
      { label: "DETECTED EPISODE", value: sample.label, color: sevColor(sample.sev), bar: 0.93 },
      { label: "KEYPOINT OKS / PCK", value: "0.89 · 93% lift recall", color: "#34d399", bar: 0.89 },
      { label: "PRIVACY CONTROL", value: "Landmarks only (no face ID)", color: "#7dd3fc", bar: 1.0 },
    ],
    btnPrimary: "Approve Action",
    btnSecondary: "Adjust Joint",
  });
}

function drawSpreadsheetsCanvas(ctx, w, h, data, stage, activeSample) {
  const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · CSV / EXCEL TABLE · TARGET & FEATURE DRIVERS");
  ctx.fillStyle = "#10141c";
  ctx.beginPath(); ctx.roundRect(24, 38, splitX - 42, h - 56, 10); ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.16)";
  ctx.stroke();

  const cols = ["Row", "Temp °C", "Vib mm/s", "Bar", "Target: Failure_48h"];
  const xs = [40, 118, 206, 296, 362];
  ctx.fillStyle = "#7dd3fc";
  ctx.font = "700 11px 'Martian Mono', monospace";
  cols.forEach((col, idx) => ctx.fillText(col, xs[idx], 74));
  ctx.strokeStyle = "rgba(255, 255, 255, 0.15)";
  ctx.beginPath(); ctx.moveTo(34, 88); ctx.lineTo(splitX - 28, 88); ctx.stroke();

  const rows = [
    ["#1042", "86.4", "8.4", "14.1", stage === "raw" ? "?" : "High (0.91)"],
    ["#1043", "63.1", "4.9", "12.2", stage === "raw" ? "?" : "Watch (0.54)"],
    ["#1044", "52.0", "1.8", "14.0", stage === "raw" ? "?" : "Normal (0.97)"],
  ];

  rows.forEach((r, idx) => {
    const sample = data.samples[idx];
    const [rxBox, ry, rwBox, rh] = sample.rect;
    const selected = idx === activeSample;
    const col = sevColor(sample.sev);
    if (stage !== "raw") {
      ctx.fillStyle = selected ? `${col}24` : `${col}0d`;
      ctx.fillRect(rxBox * w, ry * h, rwBox * w, rh * h);
      if (selected) {
        ctx.strokeStyle = col; ctx.lineWidth = 2;
        ctx.strokeRect(rxBox * w, ry * h, rwBox * w, rh * h);
      }
    }
    ctx.font = "500 13px 'Martian Mono', monospace";
    r.forEach((cell, cIdx) => {
      ctx.fillStyle = cIdx === 4 && stage !== "raw" ? col : "#eef2f6";
      ctx.fillText(cell, xs[cIdx], ry * h + 34);
    });
  });

  ctx.strokeStyle = "#38bdf8";
  ctx.lineWidth = 2;
  ctx.beginPath();
  [0.2, 0.25, 0.35, 0.42, 0.65, 0.91, 0.88].forEach((v, i) => {
    const sx = 42 + i * 76;
    const sy = h - 38 - v * 46;
    if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
  });
  ctx.stroke();

  const sample = data.samples[activeSample];
  drawSolutionPanel(ctx, rx, rw, h, {
    verdict: stage === "raw" ? "RAW COLUMNS" : sample.sev === "high" ? "HIGH RISK (0.91)" : sample.sev === "medium" ? "WATCH (0.54)" : "NORMAL (0.97)",
    verdictColor: stage === "raw" ? "#7d8ba0" : sevColor(sample.sev),
    verdictFg: sample.sev === "high" ? "#fff" : "#04121c",
    headline: "Tabular Forecast Scorecard",
    rows: [
      { label: "TOP DRIVER: RMS_VIB", value: "44% impact on Failure_48h", color: "#38bdf8", bar: 0.84 },
      { label: "DRIVER 2: HOUSING_TEMP", value: "33% impact (86.4 °C)", color: "#ffb020", bar: 0.68 },
      { label: "HELD-OUT UNSEEN ROWS", value: "F1 0.91 · 96.2% acc", color: "#34d399", bar: 0.91 },
    ],
    btnPrimary: "Score Batch CSV",
    btnSecondary: "Check Leakage",
  });
}

export function drawStudio(canvas, outcome, stage, activeSample, animPhase, images) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const data = OUTCOMES[outcome];
  if (!ctx || !data) return;
  const { w, h } = setupHiDpiCanvas(canvas, ctx, 960, 380);
  drawStudioGrid(ctx, w, h);
  if (outcome === "audio") drawAudioCanvas(ctx, w, h, data, stage, activeSample, animPhase);
  else if (outcome === "vision") drawVisionCanvas(ctx, w, h, data, stage, activeSample, images);
  else if (outcome === "ocr") drawOcrCanvas(ctx, w, h, data, stage, activeSample);
  else if (outcome === "documents") drawDocumentsCanvas(ctx, w, h, data, stage, activeSample);
  else if (outcome === "movements") drawMovementsCanvas(ctx, w, h, data, stage, activeSample, images);
  else if (outcome === "spreadsheets") drawSpreadsheetsCanvas(ctx, w, h, data, stage, activeSample);
}

export function drawMiniThumb(canvas, mode, images) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { w, h } = setupHiDpiCanvas(canvas, ctx, 320, 145);
  ctx.fillStyle = "#080a0e";
  ctx.fillRect(0, 0, w, h);

  if (mode === "audio") {
    for (let i = 0; i < 36; i += 1) {
      const t = i / 36;
      const fault = t >= 0.34 && t <= 0.62;
      const bh = (fault ? 0.45 + 0.4 * Math.abs(Math.sin(i * 1.4)) : 0.16 + 0.15 * Math.cos(i)) * 96;
      ctx.fillStyle = fault ? "#ff6b3d" : "#38bdf8";
      ctx.fillRect(16 + i * 8, h / 2 - bh / 2, 5, bh);
    }
    ctx.strokeStyle = "#ff6b3d"; ctx.lineWidth = 1.6;
    ctx.strokeRect(110, 18, 92, 108);
    drawBadge(ctx, "FAULT 3.4 kHz", 112, 16, "#ff6b3d", "#fff");
  } else if (mode === "ocr") {
    [38, 76, 114].forEach((y, idx) => {
      ctx.strokeStyle = "rgba(255,255,255,0.16)";
      ctx.beginPath(); ctx.moveTo(16, y + 12); ctx.lineTo(w - 16, y + 12); ctx.stroke();
      ctx.strokeStyle = "#eef2f6"; ctx.lineWidth = 2;
      ctx.beginPath();
      for (let s = 0; s < 14; s += 1) {
        const px = 22 + s * 7.5;
        const py = y + Math.sin(s + idx) * 5;
        if (s === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.stroke();
      ctx.fillStyle = "#34d399";
      ctx.font = "600 11px 'Martian Mono', monospace";
      ctx.fillText(["Valve 12B", "Seal worn", "2.4 bar"][idx], 160, y + 4);
    });
  } else if (mode === "documents") {
    const spans = [["EQUIPMENT", "Pump P-104B", "#38bdf8", 24], ["FAULT", "seal weep 78°C", "#ff6b3d", 62], ["ACTION", "replace gasket", "#34d399", 100]];
    spans.forEach(([tag, txt, col, y]) => {
      ctx.fillStyle = `${col}20`;
      ctx.beginPath(); ctx.roundRect(16, y, w - 32, 28, 6); ctx.fill();
      ctx.strokeStyle = col; ctx.stroke();
      ctx.fillStyle = "#fff"; ctx.font = "600 12px 'Hanken Grotesk', sans-serif";
      ctx.fillText(txt, 26, y + 19);
      drawBadge(ctx, tag, w - 110, y + 4, col, col === "#ff6b3d" ? "#fff" : "#04121c");
    });
  } else if (mode === "movements") {
    drawCoverImage(ctx, images.worker, 0, 0, w, h, 0.62);
    [[105, 0.22, "#34d399"], [215, 0.72, "#ff6b3d"]].forEach(([cx, ang, col]) => {
      const hy = 92; const sx = cx + Math.sin(ang) * 42; const sy = hy - Math.cos(ang) * 42;
      ctx.strokeStyle = col; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(cx, hy + 34); ctx.lineTo(cx, hy); ctx.lineTo(sx, sy); ctx.lineTo(sx, sy - 14); ctx.stroke();
      [[cx, hy], [sx, sy], [sx, sy - 14]].forEach(([jx, jy]) => {
        ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(jx, jy, 3.5, 0, Math.PI * 2); ctx.fill();
      });
    });
    drawBadge(ctx, "OKS 0.89", 16, 14, "#34d399");
  } else if (mode === "spreadsheets") {
    const rows = [["#1042", "86°C", "High 0.91", "#ff6b3d", 26], ["#1043", "63°C", "Watch 0.54", "#ffb020", 62], ["#1044", "52°C", "Normal 0.97", "#34d399", 98]];
    rows.forEach(([id, tmp, res, col, y]) => {
      ctx.fillStyle = `${col}18`; ctx.fillRect(16, y, w - 32, 28);
      ctx.fillStyle = "#eef2f6"; ctx.font = "600 11.5px 'Martian Mono', monospace";
      ctx.fillText(`${id}  ${tmp}`, 26, y + 18);
      ctx.fillStyle = col; ctx.fillText(res, 172, y + 18);
    });
  } else if (mode === "vision") {
    drawCoverImage(ctx, images.welding, 0, 0, w, h, 0.9);
    ctx.strokeStyle = "#ff6b3d"; ctx.lineWidth = 2.4; ctx.strokeRect(48, 32, 108, 72);
    drawBadge(ctx, "Porosity", 48, 12, "#ff6b3d", "#fff");
    ctx.strokeStyle = "#ffb020"; ctx.lineWidth = 2; ctx.strokeRect(184, 62, 86, 56);
    drawBadge(ctx, "Undercut", 184, 42, "#ffb020");
  }
}

export function drawFusionDiagram(canvas, key) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const scenario = FUSION_SCENARIOS[key];
  if (!ctx || !scenario) return;
  const { w, h } = setupHiDpiCanvas(canvas, ctx, 880, 210);
  drawStudioGrid(ctx, w, h);

  const leftX = 20;
  const cardW = 286;
  const mergeX = 446;
  const outX = 532;
  const outW = w - outX - 20;

  scenario.streams.forEach((s, idx) => {
    const y = 16 + idx * 62;
    const col = sevColor(s.sev);
    ctx.fillStyle = "#10141c";
    ctx.beginPath(); ctx.roundRect(leftX, y, cardW, 50, 8); ctx.fill();
    ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.stroke();

    ctx.fillStyle = col;
    ctx.font = "700 10px 'Martian Mono', monospace";
    ctx.fillText(s.kind.toUpperCase(), leftX + 12, y + 17);
    ctx.fillStyle = "#ffffff";
    ctx.font = "700 12.5px 'Hanken Grotesk', sans-serif";
    ctx.fillText(`${s.title} · ${s.val}`, leftX + 12, y + 36);

    s.bars.forEach((b, bIdx) => {
      const bx = leftX + cardW - 50 + bIdx * 8;
      const bh = b * 24;
      ctx.fillStyle = col;
      ctx.fillRect(bx, y + 38 - bh, 5, bh);
    });

    ctx.strokeStyle = col;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(leftX + cardW, y + 25);
    ctx.bezierCurveTo(leftX + cardW + 60, y + 25, mergeX - 50, h / 2, mergeX, h / 2);
    ctx.stroke();
  });

  ctx.fillStyle = "#10141c";
  ctx.beginPath(); ctx.arc(mergeX, h / 2, 34, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = "#38bdf8"; ctx.lineWidth = 2.4; ctx.stroke();
  ctx.fillStyle = "#7dd3fc";
  ctx.font = "700 10px 'Martian Mono', monospace";
  ctx.fillText("SYNC", mergeX - 15, h / 2 - 2);
  ctx.fillText("FUSE", mergeX - 15, h / 2 + 12);

  ctx.strokeStyle = "#34d399";
  ctx.lineWidth = 2.5;
  ctx.beginPath(); ctx.moveTo(mergeX + 34, h / 2); ctx.lineTo(outX, h / 2); ctx.stroke();

  ctx.fillStyle = "#10161e";
  ctx.beginPath(); ctx.roundRect(outX, 18, outW, h - 36, 10); ctx.fill();
  ctx.strokeStyle = "rgba(52, 211, 153, 0.55)"; ctx.lineWidth = 1.6; ctx.stroke();

  drawBadge(ctx, "JOINT DECISION + ABLATION", outX + 16, 30, "#34d399", "#04121c");
  ctx.fillStyle = "#ffffff";
  ctx.font = "700 13.5px 'Hanken Grotesk', sans-serif";
  ctx.fillText(scenario.verdict.replace(/^Joint [^:]+:\s*/, ""), outX + 16, 73);

  scenario.ablation.forEach(([lbl, score], idx) => {
    const ay = 94 + idx * 30;
    const pct = Number(score) || 0.8;
    ctx.fillStyle = "#838e9e";
    ctx.font = "600 10.5px 'Martian Mono', monospace";
    ctx.fillText(lbl, outX + 16, ay + 12);
    const barX = outX + 168;
    const barW = outW - 222;
    ctx.fillStyle = "rgba(255,255,255,0.08)";
    ctx.fillRect(barX, ay + 4, barW, 8);
    ctx.fillStyle = idx === 2 ? "#34d399" : "#38bdf8";
    ctx.fillRect(barX, ay + 4, barW * pct, 8);
    ctx.fillStyle = "#ffffff";
    ctx.fillText(score, outX + outW - 44, ay + 12);
  });
}
