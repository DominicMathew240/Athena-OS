import pipeImg from "./assets/landing/pipe-corrosion.jpg";

/**
 * Real PCM audio oscillogram layout:
 * - Left amplitude axis (+1.0 / 0 / -1.0), horizontal ±0.5 guide lines, DC zero line, and time ruler
 * - Dual-layer acoustic envelope computed from realistic motor/fault/slip physics:
 *   outer peak min/max polygon + inner RMS energy polygon + crisp 1px peak contour
 */
export function RealAudioWaveform({ variant = "fault", compact = false }) {
  const w = compact ? 280 : 480;
  const h = compact ? 42 : 104;
  const padL = compact ? 4 : 34;
  const padR = 4;
  const padT = compact ? 4 : 8;
  const padB = compact ? 4 : 18;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const midY = padT + plotH / 2;
  const halfH = plotH / 2 - 1;
  const steps = compact ? 120 : 220;

  const upperPeak = [];
  const lowerPeak = [];
  const upperRms = [];
  const lowerRms = [];

  for (let i = 0; i < steps; i += 1) {
    const t = i / (steps - 1);
    const x = padL + t * plotW;

    // Deterministic acoustic pseudo-noise + harmonic carrier + physical transient envelope
    const noiseA = Math.sin(i * 12.9898 + 1.3) * 43758.5453;
    const randA = noiseA - Math.floor(noiseA);
    const noiseB = Math.sin(i * 78.233 + 4.7) * 24634.6345;
    const randB = noiseB - Math.floor(noiseB);

    let posAmp = 0.08 + 0.06 * Math.abs(Math.sin(t * Math.PI * 18)) + randA * 0.05;
    let negAmp = 0.08 + 0.06 * Math.abs(Math.cos(t * Math.PI * 18)) + randB * 0.05;
    let rms = 0.07 + 0.03 * Math.sin(t * Math.PI * 12);

    if (variant === "fault") {
      // Periodic bearing raceway impact impulses every ~0.14 normalized time with exponential decay
      const cycle = (t * 7.2) % 1;
      const impact = Math.exp(-cycle * 8.5);
      const secondary = Math.exp(-(((t * 7.2 + 0.48) % 1) * 11));
      const ring = 0.65 + 0.35 * Math.sin(i * 2.3);
      posAmp = Math.min(0.95, posAmp + impact * 0.72 * (0.7 + 0.3 * randA) + secondary * 0.32 * ring);
      negAmp = Math.min(0.95, negAmp + impact * 0.68 * (0.7 + 0.3 * randB) + secondary * 0.28 * ring);
      rms = Math.min(0.55, 0.14 + impact * 0.28 + secondary * 0.1);
    } else if (variant === "slip") {
      // High-frequency belt friction squeal burst with amplitude modulation
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
      aria-label={`PCM audio waveform showing ${variant === "fault" ? "periodic bearing impact spikes" : variant === "slip" ? "intermittent friction squeal bursts" : "steady low-amplitude baseline"}`}
    >
      {!compact && (
        <g className="lp-pcm-axes">
          <text x="2" y={padT + 8}>+1.0</text>
          <text x="8" y={midY + 3}>0.0</text>
          <text x="2" y={padT + plotH - 1}>-1.0</text>
          <line x1={padL} y1={padT + plotH * 0.25} x2={w - padR} y2={padT + plotH * 0.25} className="lp-pcm-guide" />
          <line x1={padL} y1={padT + plotH * 0.75} x2={w - padR} y2={padT + plotH * 0.75} className="lp-pcm-guide" />
          {[0, 0.25, 0.5, 0.75, 1].map((r, idx) => {
            const tx = padL + r * plotW;
            const labels = ["0.0s", "1.0s", "2.1s", "3.1s", "4.2s"];
            return (
              <g key={r}>
                <line x1={tx} y1={padT + plotH} x2={tx} y2={padT + plotH + 3} className="lp-pcm-tick" />
                <text x={idx === 4 ? tx - 22 : idx === 0 ? tx : tx - 10} y={h - 3}>{labels[idx]}</text>
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
 * Deterministic log-mel spectrogram grid + PCM waveform SVG for Acoustic AI.
 * Avoids generic rainbow/neon blocks; uses clinical instrument blue/amber/coral intensity tiers.
 */
export function SpectrogramGraphic({ variant = "fault", compact = false }) {
  const cols = compact ? 22 : 34;
  const rows = compact ? 6 : 8;
  const w = compact ? 280 : 480;
  const h = compact ? 74 : 116;
  const padL = compact ? 4 : 34;
  const padR = 4;
  const padT = 6;
  const padB = compact ? 4 : 18;
  const cw = (w - padL - padR) / cols;
  const ch = (h - padT - padB) / rows;

  const cells = [];
  for (let r = 0; r < rows; r += 1) {
    const freqNorm = 1 - r / (rows - 1); // 1 = top (8 kHz), 0 = bottom (60 Hz)
    for (let c = 0; c < cols; c += 1) {
      const t = c / (cols - 1);
      let energy = 0.14 + 0.1 * Math.sin(c * 0.55 + r * 0.7);
      if (r === rows - 1) energy += 0.28; // 60 Hz hum baseline
      if (variant === "fault") {
        // Periodic 3.4 kHz bearing impact harmonics around mid-upper band
        const band = Math.exp(-Math.pow((freqNorm - 0.52) / 0.18, 2));
        const pulse = 0.55 + 0.45 * Math.sin(t * Math.PI * 6);
        energy += band * pulse * 0.72;
      } else if (variant === "slip") {
        // Transient high-frequency squeal in middle window
        const inBurst = t >= 0.36 && t <= 0.78;
        const hiBand = Math.exp(-Math.pow((freqNorm - 0.78) / 0.2, 2));
        if (inBurst) energy += hiBand * 0.76;
      } else {
        // Healthy baseline: steady low-frequency hum only
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
      aria-label={`Log-mel spectrogram showing ${variant === "fault" ? "3.4 kHz bearing harmonic peaks" : variant === "slip" ? "transient 6.8 kHz burst" : "steady 60 Hz motor baseline"}`}
    >
      {!compact && (
        <g className="lp-spec-axes">
          <text x="2" y="14">8 kHz</text>
          <text x="2" y="56">3.4 k</text>
          <text x="2" y="96">60 Hz</text>
          <text x={padL} y={h - 4}>0.0s</text>
          <text x={w / 2} y={h - 4}>2.1s</text>
          <text x={w - 28} y={h - 4}>4.2s</text>
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
 * Authentic cursive/printed handwriting strokes on ruled form paper (not math sine waves).
 */
export function HandwritingInkGraphic({ variant = 0, compact = false }) {
  const strokes = [
    // "Valve 12B — 2.4" stylized continuous pen strokes
    "M 18 38 C 22 48, 25 48, 30 22 M 34 36 C 36 30, 42 30, 42 37 C 42 40, 45 40, 47 34 M 50 20 L 49 38 C 52 38, 55 34, 57 31 C 59 38, 63 38, 66 32 C 69 32, 71 38, 76 34 M 92 22 L 92 38 M 99 26 C 106 20, 111 28, 99 38 L 111 38 M 118 22 L 118 38 C 127 38, 127 30, 119 30 C 126 29, 126 22, 118 22 M 138 31 L 154 31 M 166 24 C 174 20, 177 29, 165 38 L 176 38 M 183 37 L 184 37 M 196 22 L 191 33 L 203 33 M 199 22 L 199 39",
    // "Seal worn" stylized pen strokes
    "M 28 24 C 18 22, 18 30, 27 32 C 33 34, 31 40, 20 38 M 36 34 C 42 34, 44 30, 39 30 C 35 30, 35 38, 44 37 M 48 34 C 53 31, 56 38, 50 38 C 46 38, 52 31, 57 37 M 62 20 L 61 38 M 74 30 L 78 38 L 82 31 L 86 38 L 91 30 M 96 34 C 96 29, 104 29, 104 34 C 104 39, 96 39, 96 34 M 110 38 L 110 31 C 113 29, 117 30, 118 32 M 123 38 L 123 31 C 126 29, 131 30, 131 38 M 144 38 L 142 42 M 156 31 L 160 38 L 164 31 L 168 38 L 173 31 M 179 34 C 185 34, 187 30, 182 30 C 178 30, 178 38, 187 37",
    // "2.4 bar · OK" stylized pen strokes
    "M 20 26 C 28 20, 32 28, 20 38 L 32 38 M 39 37 L 40 37 M 52 22 L 46 33 L 58 33 M 54 22 L 54 39 M 74 20 L 74 38 C 82 38, 82 30, 74 30 M 89 34 C 94 31, 97 38, 91 38 C 87 38, 93 31, 98 37 M 104 38 L 104 31 C 107 29, 111 30, 112 32 M 126 30 L 127 30 M 144 23 C 138 23, 138 38, 145 38 C 152 38, 152 23, 144 23 M 158 22 L 158 38 M 168 23 L 159 31 L 169 38",
  ];
  const d = strokes[variant % strokes.length];
  const w = compact ? 240 : 260;
  const h = compact ? 58 : 54;

  return (
    <svg className="lp-ink-svg" viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Scanned handwritten line crop">
      <line x1="8" y1="42" x2={w - 8} y2="42" className="lp-ink-rule" />
      <line x1="8" y1="16" x2={w - 8} y2="16" className="lp-ink-rule lp-ink-rule--top" />
      <rect x="12" y="12" width={w - 24} height="34" rx="3" className="lp-ink-crop" />
      <path d={d} className="lp-ink-path" />
    </svg>
  );
}

/**
 * Dual-channel telemetry trace + sliding window bracket for Signal Windows (OS-SIG-001).
 */
export function SignalMultiTrace({ points = [], sev = "high", compact = false }) {
  const maxX = compact ? 280 : 460;
  const maxY = compact ? 92 : 142;
  const padX = compact ? 10 : 18;
  const padT = compact ? 12 : 16;
  const padB = compact ? 12 : 22;
  const plotH = maxY - padT - padB;
  const pts = points.length ? points : [28, 44, 22, 68, 18, 76, 20, 82, 16, 74, 24, 66];

  const ch1 = pts.map((val, idx) => {
    const x = padX + (idx / (pts.length - 1)) * (maxX - padX * 2);
    const y = padT + plotH - (val / 100) * plotH;
    return [Math.round(x), Math.round(y)];
  });
  const ch2 = pts.map((val, idx) => {
    const x = padX + (idx / (pts.length - 1)) * (maxX - padX * 2);
    const smoothed = 32 + val * 0.32 + Math.sin(idx * 0.7) * 6;
    const y = padT + plotH - (smoothed / 100) * plotH;
    return [Math.round(x), Math.round(y)];
  });

  const line1 = ch1.map(([x, y]) => `${x},${y}`).join(" ");
  const area1 = `${padX},${padT + plotH} ${line1} ${ch1[ch1.length - 1][0]},${padT + plotH}`;
  const line2 = ch2.map(([x, y]) => `${x},${y}`).join(" ");

  return (
    <svg className="lp-sig-svg" viewBox={`0 0 ${maxX} ${maxY}`} role="img" aria-label="Dual-channel vibration and temperature signal windows">
      {[0.25, 0.5, 0.75].map((r) => (
        <line key={r} x1={padX} y1={padT + plotH * r} x2={maxX - padX} y2={padT + plotH * r} className="lp-sig-grid" />
      ))}
      <rect
        x={padX + (maxX - padX * 2) * 0.27}
        y={padT - 2}
        width={(maxX - padX * 2) * 0.46}
        height={plotH + 4}
        rx="4"
        className="lp-sig-window"
      />
      <polygon points={area1} className="lp-sig-area" data-sev={sev} />
      <polyline points={line2} fill="none" className="lp-sig-line-sec" />
      <polyline points={line1} fill="none" className="lp-sig-line" data-sev={sev} />
      {!compact &&
        ch1.map(([cx, cy], idx) => (
          <circle key={idx} cx={cx} cy={cy} r="3" className="lp-sig-dot" data-sev={sev} />
        ))}
      {!compact && (
        <g className="lp-sig-legend">
          <text x={padX} y={maxY - 5}>CH1 Vibration (mm/s)</text>
          <text x={padX + 165} y={maxY - 5}>CH2 Housing (°C)</text>
          <text x={maxX - 98} y={maxY - 5}>Window [16..48]</text>
        </g>
      )}
    </svg>
  );
}

/**
 * Crisp visual thumbnail for each of the 6 modality recipe cards in the Multimodal Gallery.
 */
export function ModalityThumb({ modality }) {
  if (modality === "vision") {
    return (
      <div className="lp-thumb lp-thumb--photo" aria-hidden="true">
        <img src={pipeImg} alt="" loading="lazy" width={320} height={180} />
        <span className="lp-thumb-box lp-thumb-box--ok" style={{ left: "34%", top: "24%", width: "54%", height: "38%" }}>1</span>
        <span className="lp-thumb-box lp-thumb-box--draft" style={{ left: "22%", top: "54%", width: "28%", height: "36%" }}>2?</span>
      </div>
    );
  }

  if (modality === "audio") {
    return (
      <div className="lp-thumb lp-thumb--audio" aria-hidden="true">
        <SpectrogramGraphic variant="fault" compact />
        <RealAudioWaveform variant="fault" compact />
      </div>
    );
  }

  if (modality === "ocr") {
    return (
      <div className="lp-thumb lp-thumb--ocr" aria-hidden="true">
        <HandwritingInkGraphic variant={0} compact />
        <div className="lp-thumb-ocr-meta">
          <span>TrOCR</span>
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
          <text x="16" y="21" className="lp-th-txt">ROW</text>
          <text x="72" y="21" className="lp-th-txt">TEMP</text>
          <text x="128" y="21" className="lp-th-txt">VIB</text>
          <text x="184" y="21" className="lp-th-txt-accent">TARGET</text>
          {[
            ["#1042", "86.4°", "8.4", "High 0.91", "high", 32],
            ["#1043", "63.1°", "4.9", "Watch .54", "med", 52],
            ["#1044", "52.0°", "1.8", "Norm .97", "ok", 72],
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
          <rect x="16" y="16" width="104" height="16" rx="3" className="lp-th-pill-accent" />
          <text x="23" y="27" className="lp-th-txt-accent">rotating_equip</text>
          <text x="218" y="27" className="lp-th-txt">F1 0.92</text>
          <rect x="16" y="40" width="96" height="15" rx="3" className="lp-th-row lp-th-row--high" />
          <text x="22" y="51" className="lp-th-cell">seal weep</text>
          <rect x="118" y="40" width="56" height="15" rx="3" className="lp-th-row lp-th-row--med" />
          <text x="124" y="51" className="lp-th-cell">78 °C</text>
          <rect x="180" y="40" width="82" height="15" rx="3" className="lp-th-row lp-th-row--ok" />
          <text x="186" y="51" className="lp-th-cell">P-104B</text>
          <rect x="16" y="64" width="214" height="7" rx="3" className="lp-th-bar-bg" />
          <rect x="16" y="64" width="164" height="7" rx="3" className="lp-th-bar-fill" />
        </svg>
      </div>
    );
  }

  return (
    <div className="lp-thumb lp-thumb--svg" aria-hidden="true">
      <SignalMultiTrace sev="high" compact />
    </div>
  );
}
