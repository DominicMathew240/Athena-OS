import pipeImg from "./assets/landing/pipe-corrosion.jpg";

/**
 * Real PCM audio oscillogram: amplitude axis, +/-0.5 guide lines, DC zero line, time ruler,
 * and dual-layer peak min/max + inner RMS energy envelope polygons.
 */
export function RealAudioWaveform({ variant = "fault", compact = false }) {
  const w = compact ? 280 : 480;
  const h = compact ? 42 : 104;
  const padL = compact ? 4 : 30;
  const padR = 4;
  const padT = compact ? 4 : 8;
  const padB = compact ? 4 : 18;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const midY = padT + plotH / 2;
  const halfH = plotH / 2 - 1;
  const steps = compact ? 140 : 240;

  const upperPeak = [];
  const lowerPeak = [];
  const upperRms = [];
  const lowerRms = [];

  for (let i = 0; i < steps; i += 1) {
    const t = i / (steps - 1);
    const x = padL + t * plotW;

    const noiseA = Math.sin(i * 12.9898 + 1.3) * 43758.5453;
    const randA = noiseA - Math.floor(noiseA);
    const noiseB = Math.sin(i * 78.233 + 4.7) * 24634.6345;
    const randB = noiseB - Math.floor(noiseB);

    let posAmp = 0.08 + 0.06 * Math.abs(Math.sin(t * Math.PI * 18)) + randA * 0.05;
    let negAmp = 0.08 + 0.06 * Math.abs(Math.cos(t * Math.PI * 18)) + randB * 0.05;
    let rms = 0.07 + 0.03 * Math.sin(t * Math.PI * 12);

    if (variant === "fault") {
      const cycle = (t * 7.2) % 1;
      const impact = Math.exp(-cycle * 8.5);
      const secondary = Math.exp(-(((t * 7.2 + 0.48) % 1) * 11));
      const ring = 0.65 + 0.35 * Math.sin(i * 2.3);
      posAmp = Math.min(0.95, posAmp + impact * 0.72 * (0.7 + 0.3 * randA) + secondary * 0.32 * ring);
      negAmp = Math.min(0.95, negAmp + impact * 0.68 * (0.7 + 0.3 * randB) + secondary * 0.28 * ring);
      rms = Math.min(0.55, 0.14 + impact * 0.28 + secondary * 0.1);
    } else if (variant === "slip") {
      const gate1 = Math.exp(-Math.pow((t - 0.34) / 0.09, 2));
      const gate2 = Math.exp(-Math.pow((t - 0.68) / 0.12, 2));
      const env = gate1 * 0.85 + gate2 * 0.95;
      posAmp = Math.min(0.92, posAmp + env * (0.45 + 0.38 * randA));
      negAmp = Math.min(0.92, negAmp + env * (0.44 + 0.38 * randB));
      rms = Math.min(0.58, 0.08 + env * 0.36);
    }

    const pyTop = (midY - posAmp * halfH).toFixed(1);
    const pyBot = (midY + negAmp * halfH).toFixed(1);
    const ryTop = (midY - rms * halfH).toFixed(1);
    const ryBot = (midY + rms * halfH).toFixed(1);
    const px = x.toFixed(1);

    upperPeak.push(`${px},${pyTop}`);
    lowerPeak.unshift(`${px},${pyBot}`);
    upperRms.push(`${px},${ryTop}`);
    lowerRms.unshift(`${px},${ryBot}`);
  }

  const peakPolygon = [...upperPeak, ...lowerPeak].join(" ");
  const rmsPolygon = [...upperRms, ...lowerRms].join(" ");
  const sev = variant === "fault" ? "high" : variant === "slip" ? "medium" : "ok";

  return (
    <svg
      className="lp-pcm-svg"
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`Sound wave showing ${variant === "fault" ? "repeated clicking impacts" : variant === "slip" ? "belt squeal bursts" : "steady normal hum"}`}
    >
      {!compact && (
        <g className="lp-pcm-axes">
          <text x="4" y={padT + 8}>+</text>
          <text x="4" y={midY + 3}>0</text>
          <text x="4" y={padT + plotH - 1}>-</text>
          <line x1={padL} y1={padT + plotH * 0.25} x2={w - padR} y2={padT + plotH * 0.25} className="lp-pcm-guide" />
          <line x1={padL} y1={padT + plotH * 0.75} x2={w - padR} y2={padT + plotH * 0.75} className="lp-pcm-guide" />
          {[0, 0.25, 0.5, 0.75, 1].map((r, idx) => {
            const tx = padL + r * plotW;
            const labels = ["0s", "1s", "2s", "3s", "4.2s"];
            return (
              <g key={r}>
                <line x1={tx} y1={padT + plotH} x2={tx} y2={padT + plotH + 3} className="lp-pcm-tick" />
                <text x={idx === 4 ? tx - 22 : idx === 0 ? tx : tx - 6} y={h - 3}>{labels[idx]}</text>
              </g>
            );
          })}
        </g>
      )}
      <line x1={padL} y1={midY} x2={w - padR} y2={midY} className="lp-pcm-zero" />
      <polygon points={peakPolygon} className="lp-pcm-peak" data-sev={sev} />
      <polygon points={rmsPolygon} className="lp-pcm-rms" data-sev={sev} />
    </svg>
  );
}

/**
 * Sound frequency heatmap (0 to 8 kHz over 4.2 seconds).
 */
export function SpectrogramGraphic({ variant = "fault", compact = false }) {
  const cols = compact ? 24 : 36;
  const rows = compact ? 6 : 8;
  const w = compact ? 280 : 480;
  const h = compact ? 52 : 112;
  const padL = compact ? 4 : 34;
  const padR = 4;
  const padT = 5;
  const padB = compact ? 4 : 18;
  const cw = (w - padL - padR) / cols;
  const ch = (h - padT - padB) / rows;

  const cells = [];
  for (let r = 0; r < rows; r += 1) {
    const freqNorm = 1 - r / (rows - 1);
    for (let c = 0; c < cols; c += 1) {
      const t = c / (cols - 1);
      let energy = 0.14 + 0.1 * Math.sin(c * 0.55 + r * 0.7);
      if (r === rows - 1) energy += 0.28;
      if (variant === "fault") {
        const band = Math.exp(-Math.pow((freqNorm - 0.52) / 0.18, 2));
        const pulse = 0.55 + 0.45 * Math.sin(t * Math.PI * 6);
        energy += band * pulse * 0.72;
      } else if (variant === "slip") {
        const inBurst = t >= 0.36 && t <= 0.78;
        const hiBand = Math.exp(-Math.pow((freqNorm - 0.78) / 0.2, 2));
        if (inBurst) energy += hiBand * 0.76;
      } else {
        energy += Math.exp(-Math.pow(freqNorm / 0.22, 2)) * 0.34;
      }
      energy = Math.max(0.08, Math.min(1, energy));
      const tier = energy > 0.74 ? "high" : energy > 0.48 ? "med" : energy > 0.28 ? "low" : "bg";
      cells.push({
        key: `${r}-${c}`,
        x: padL + c * cw + 0.8,
        y: padT + r * ch + 0.8,
        w: Math.max(2, cw - 1.6),
        h: Math.max(2, ch - 1.6),
        tier,
        opacity: tier === "bg" ? 0.35 : Number((0.45 + energy * 0.55).toFixed(2)),
      });
    }
  }

  return (
    <svg
      className="lp-spec-svg"
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`Sound pitch map showing ${variant === "fault" ? "high-pitched clicking" : variant === "slip" ? "brief belt squeal" : "steady low hum"}`}
    >
      {!compact && (
        <g className="lp-spec-axes">
          <text x="2" y="14">High</text>
          <text x="2" y="54">Mid</text>
          <text x="2" y="92">Low</text>
          <text x={padL} y={h - 4}>0s</text>
          <text x={w / 2} y={h - 4}>2s</text>
          <text x={w - 26} y={h - 4}>4.2s</text>
        </g>
      )}
      {cells.map((cell) => (
        <rect
          key={cell.key}
          x={cell.x}
          y={cell.y}
          width={cell.w}
          height={cell.h}
          rx="1.5"
          className={`lp-spec-cell lp-spec-cell--${cell.tier}`}
          style={{ opacity: cell.opacity }}
        />
      ))}
      {!compact && variant === "fault" && (
        <rect
          x={padL + cols * cw * 0.14}
          y={padT + rows * ch * 0.28}
          width={cols * cw * 0.72}
          height={rows * ch * 0.32}
          rx="3"
          className="lp-spec-focus"
        />
      )}
    </svg>
  );
}

/**
 * Handwritten line crop on ruled maintenance logbook paper.
 */
export function HandwritingInkGraphic({ variant = 0, compact = false }) {
  const strokes = [
    "M 18 38 C 22 48, 25 48, 30 22 M 34 36 C 36 30, 42 30, 42 37 C 42 40, 45 40, 47 34 M 50 20 L 49 38 C 52 38, 55 34, 57 31 C 59 38, 63 38, 66 32 C 69 32, 71 38, 76 34 M 92 22 L 92 38 M 99 26 C 106 20, 111 28, 99 38 L 111 38 M 118 22 L 118 38 C 127 38, 127 30, 119 30 C 126 29, 126 22, 118 22 M 138 31 L 154 31 M 166 24 C 174 20, 177 29, 165 38 L 176 38 M 183 37 L 184 37 M 196 22 L 191 33 L 203 33 M 199 22 L 199 39",
    "M 28 24 C 18 22, 18 30, 27 32 C 33 34, 31 40, 20 38 M 36 34 C 42 34, 44 30, 39 30 C 35 30, 35 38, 44 37 M 48 34 C 53 31, 56 38, 50 38 C 46 38, 52 31, 57 37 M 62 20 L 61 38 M 74 30 L 78 38 L 82 31 L 86 38 L 91 30 M 96 34 C 96 29, 104 29, 104 34 C 104 39, 96 39, 96 34 M 110 38 L 110 31 C 113 29, 117 30, 118 32 M 123 38 L 123 31 C 126 29, 131 30, 131 38 M 144 38 L 142 42 M 156 31 L 160 38 L 164 31 L 168 38 L 173 31 M 179 34 C 185 34, 187 30, 182 30 C 178 30, 178 38, 187 37",
    "M 20 26 C 28 20, 32 28, 20 38 L 32 38 M 39 37 L 40 37 M 52 22 L 46 33 L 58 33 M 54 22 L 54 39 M 74 20 L 74 38 C 82 38, 82 30, 74 30 M 89 34 C 94 31, 97 38, 91 38 C 87 38, 93 31, 98 37 M 104 38 L 104 31 C 107 29, 111 30, 112 32 M 126 30 L 127 30 M 144 23 C 138 23, 138 38, 145 38 C 152 38, 152 23, 144 23 M 158 22 L 158 38 M 168 23 L 159 31 L 169 38",
  ];
  const d = strokes[variant % strokes.length];
  const w = compact ? 240 : 260;
  const h = compact ? 56 : 54;

  return (
    <svg className="lp-ink-svg" viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Scanned handwritten line from maintenance logbook">
      <line x1="8" y1="42" x2={w - 8} y2="42" className="lp-ink-rule" />
      <line x1="8" y1="16" x2={w - 8} y2="16" className="lp-ink-rule lp-ink-rule--top" />
      <rect x="12" y="12" width={w - 24} height="34" rx="3" className="lp-ink-crop" />
      <path d={d} className="lp-ink-path" />
    </svg>
  );
}

/**
 * Real high-resolution (150-point) continuous sensor telemetry plot with Safe Limit threshold.
 * Eliminates the fake 12-point zigzag and cartoon dots.
 */
export function SignalMultiTrace({ mode = "imbalance", sev = "high", compact = false }) {
  const maxX = compact ? 280 : 480;
  const maxY = compact ? 76 : 148;
  const padL = compact ? 6 : 38;
  const padR = 8;
  const padT = compact ? 8 : 16;
  const padB = compact ? 8 : 24;
  const plotW = maxX - padL - padR;
  const plotH = maxY - padT - padB;
  const steps = compact ? 96 : 150;
  const thresholdNorm = 0.56; // Safe limit line (~6.5 mm/s)
  const thresholdY = Math.round(padT + plotH * (1 - thresholdNorm));

  const ch1 = [];
  const ch2 = [];

  for (let i = 0; i < steps; i += 1) {
    const t = i / (steps - 1);
    const x = padL + t * plotW;

    const n1 = Math.sin(i * 19.13 + 0.7) * 43758.5453;
    const micro = (n1 - Math.floor(n1) - 0.5) * 0.08;
    const ripple = 0.05 * Math.sin(t * Math.PI * 26) + 0.03 * Math.cos(t * Math.PI * 41);

    let vib = 0.22 + ripple + micro;
    let temp = 0.3 + 0.03 * Math.sin(t * Math.PI * 2) + t * 0.04;

    if (mode === "imbalance") {
      // Gradual load climb crossing safe threshold with sustained high-frequency shaft oscillation
      const ramp = 0.16 + 0.42 * (1 / (1 + Math.exp(-(t - 0.32) * 8)));
      const shaft = 0.14 * Math.sin(t * Math.PI * 22) + 0.06 * Math.sin(t * Math.PI * 44);
      vib = Math.max(0.08, Math.min(0.94, ramp + shaft + micro));
      temp = Math.min(0.76, 0.34 + t * 0.32 + 0.015 * Math.sin(t * Math.PI * 5));
    } else if (mode === "cavitation") {
      // Intermittent pressure-collapse spikes crossing safe limit briefly
      const b1 = Math.exp(-Math.pow((t - 0.28) / 0.045, 2)) * 0.55;
      const b2 = Math.exp(-Math.pow((t - 0.64) / 0.05, 2)) * 0.62;
      vib = Math.max(0.08, Math.min(0.95, 0.22 + b1 + b2 + ripple * 1.4 + micro));
      temp = 0.36 + t * 0.12;
    } else {
      // Normal steady operation well below Safe Limit
      vib = Math.max(0.08, Math.min(0.4, 0.21 + 0.035 * Math.sin(t * Math.PI * 16) + micro * 0.6));
      temp = 0.28 + 0.015 * Math.sin(t * Math.PI * 3);
    }

    const y1 = padT + plotH * (1 - vib);
    const y2 = padT + plotH * (1 - temp);
    ch1.push([x.toFixed(1), y1.toFixed(1)]);
    ch2.push([x.toFixed(1), y2.toFixed(1)]);
  }

  const line1 = ch1.map(([x, y]) => `${x},${y}`).join(" ");
  const area1 = `${padL},${(padT + plotH).toFixed(1)} ${line1} ${(padL + plotW).toFixed(1)},${(padT + plotH).toFixed(1)}`;
  const line2 = ch2.map(([x, y]) => `${x},${y}`).join(" ");

  return (
    <svg
      className="lp-sig-svg"
      viewBox={`0 0 ${maxX} ${maxY}`}
      role="img"
      aria-label={`Machine sensor chart showing ${mode === "imbalance" ? "vibration rising past safe limit" : mode === "cavitation" ? "brief vibration spikes" : "steady readings within safe limit"}`}
    >
      {[0.25, 0.5, 0.75].map((r) => (
        <line key={r} x1={padL} y1={padT + plotH * r} x2={maxX - padR} y2={padT + plotH * r} className="lp-sig-grid" />
      ))}
      <line x1={padL} y1={thresholdY} x2={maxX - padR} y2={thresholdY} className="lp-sig-limit" />
      <polygon points={area1} className="lp-sig-area" data-sev={sev} />
      <polyline points={line2} fill="none" className="lp-sig-line-sec" />
      <polyline points={line1} fill="none" className="lp-sig-line" data-sev={sev} />
      {!compact && (
        <g>
          <rect x={padL + 6} y={thresholdY - 14} width="68" height="14" rx="3" fill="#0b1018" stroke="#f59e0b" strokeOpacity="0.45" strokeWidth="1" />
          <text x={padL + 11} y={thresholdY - 4} className="lp-sig-limit-txt">
            Safe limit
          </text>
        </g>
      )}
      {!compact && (
        <g className="lp-sig-legend">
          <text x="4" y={padT + 8}>12</text>
          <text x="8" y={thresholdY + 3}>6</text>
          <text x="8" y={padT + plotH}>0</text>
          <text x={padL} y={maxY - 5}>Vibration sensor</text>
          <text x={padL + 148} y={maxY - 5}>Temperature trend</text>
          <text x={maxX - 42} y={maxY - 5}>60 sec</text>
        </g>
      )}
    </svg>
  );
}

/**
 * Visual thumbnails for all 6 data types in the Studio Gallery — plain, real-world output previews.
 */
export function ModalityThumb({ modality }) {
  if (modality === "vision") {
    return (
      <div className="lp-thumb lp-thumb--photo" aria-hidden="true">
        <img src={pipeImg} alt="" loading="lazy" width={320} height={180} />
        <span className="lp-thumb-box lp-thumb-box--ok" style={{ left: "34%", top: "24%", width: "54%", height: "38%" }}>Rust</span>
        <span className="lp-thumb-box lp-thumb-box--draft" style={{ left: "22%", top: "54%", width: "28%", height: "36%" }}>Check</span>
      </div>
    );
  }

  if (modality === "audio") {
    return (
      <div className="lp-thumb lp-thumb--audio" aria-hidden="true">
        <RealAudioWaveform variant="fault" compact />
        <SpectrogramGraphic variant="fault" compact />
      </div>
    );
  }

  if (modality === "ocr") {
    return (
      <div className="lp-thumb lp-thumb--ocr" aria-hidden="true">
        <HandwritingInkGraphic variant={0} compact />
        <div className="lp-thumb-ocr-meta">
          <span>Read as</span>
          <code>Valve 12B — 2.4 bar</code>
        </div>
      </div>
    );
  }

  if (modality === "spreadsheets") {
    return (
      <div className="lp-thumb lp-thumb--svg" aria-hidden="true">
        <svg className="lp-thumb-svg" viewBox="0 0 280 96">
          <rect x="8" y="8" width="264" height="20" rx="3" className="lp-th-head" />
          <text x="16" y="21" className="lp-th-txt">UNIT</text>
          <text x="72" y="21" className="lp-th-txt">TEMP</text>
          <text x="128" y="21" className="lp-th-txt">SHAKE</text>
          <text x="184" y="21" className="lp-th-txt-accent">RISK</text>
          {[
            ["#1042", "86°C", "8.4", "Inspect", "high", 32],
            ["#1043", "63°C", "4.9", "Watch", "med", 52],
            ["#1044", "52°C", "1.8", "Normal", "ok", 72],
          ].map(([id, t, v, pred, sev, y]) => (
            <g key={id}>
              <rect x="8" y={y} width="264" height="17" rx="2" className={`lp-th-row lp-th-row--${sev}`} />
              <text x="16" y={y + 12} className="lp-th-cell">{id}</text>
              <text x="72" y={y + 12} className="lp-th-cell">{t}</text>
              <text x="128" y={y + 12} className="lp-th-cell">{v}</text>
              <text x="184" y={y + 12} className={`lp-th-pred lp-th-pred--${sev}`}>{pred}</text>
            </g>
          ))}
        </svg>
      </div>
    );
  }

  if (modality === "documents") {
    return (
      <div className="lp-thumb lp-thumb--svg" aria-hidden="true">
        <svg className="lp-thumb-svg" viewBox="0 0 280 96">
          <rect x="8" y="8" width="264" height="80" rx="5" className="lp-th-head" />
          <text x="16" y="24" className="lp-th-txt">INCOMING SHIFT NOTES</text>
          {[
            ["Pump P-104B weeping", "Mechanical", "high", 32],
            ["Breaker tripped startup", "Electrical", "med", 56],
          ].map(([note, dept, sev, y]) => (
            <g key={note}>
              <rect x="14" y={y} width="252" height="20" rx="3" className={`lp-th-row lp-th-row--${sev}`} />
              <text x="20" y={y + 13} className="lp-th-cell">{note}</text>
              <text x="258" y={y + 13} textAnchor="end" className={`lp-th-pred lp-th-pred--${sev}`}>{dept}</text>
            </g>
          ))}
        </svg>
      </div>
    );
  }

  return (
    <div className="lp-thumb lp-thumb--svg" aria-hidden="true">
      <SignalMultiTrace mode="imbalance" sev="high" compact />
    </div>
  );
}
