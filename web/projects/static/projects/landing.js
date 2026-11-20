/* Athena OS Django landing page script — interactive inspection demos, studio explorer, and site monitor. */
(() => {
  "use strict";

  const STATIC = "/static/projects/landing/";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("data-")) node.setAttribute(k, String(v));
      else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
      else node.setAttribute(k, String(v));
    }
    for (const child of children) {
      if (child === null || child === undefined) continue;
      node.append(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function svgEl(tag, attrs = {}, children = []) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "text") node.textContent = v;
      else node.setAttribute(k, String(v));
    }
    for (const child of children) {
      if (child === null || child === undefined) continue;
      node.append(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  /* ── Shared Realistic Signal / Audio / Ink Graphics ─────────────────────────────────────────── */

  function createAudioWaveSvg(variant = "fault", compact = false) {
    const width = compact ? 280 : 480;
    const height = compact ? 54 : 86;
    const midY = height / 2;
    const padL = compact ? 4 : 24;
    const padR = compact ? 4 : 6;
    const samples = compact ? 76 : 128;
    const ampScale = compact ? 21 : 34;
    const sev = variant === "fault" ? "high" : variant === "slip" ? "medium" : "ok";

    const topPts = [];
    const botPts = [];
    const rmsTop = [];
    const rmsBot = [];

    for (let i = 0; i < samples; i += 1) {
      const t = i / (samples - 1);
      const x = padL + (i / (samples - 1)) * (width - padL - padR);
      const h1 = Math.sin(t * Math.PI * 18) * 0.16;
      const h2 = Math.cos(t * Math.PI * 43 + 0.8) * 0.11;
      const n = Math.sin(i * 12.9898) * 43758.5453;
      const jitter = (n - Math.floor(n)) * 0.14;

      let env = 0.24 + Math.abs(h1) + jitter;
      if (variant === "fault") {
        const hitPhase = (t * 6.5) % 1;
        const click = Math.exp(-hitPhase * 7.5) * 0.68;
        env = Math.min(0.96, 0.2 + click + Math.abs(h1) + jitter);
      } else if (variant === "slip") {
        const inBurst = t > 0.36 && t < 0.78;
        const swell = inBurst ? Math.sin(((t - 0.36) / 0.42) * Math.PI) * 0.7 : 0;
        env = Math.min(0.95, 0.18 + swell + Math.abs(h1) + Math.abs(h2) + jitter);
      }

      const rms = Math.max(0.08, env * (0.48 + 0.18 * Math.sin(t * Math.PI * 11)));
      topPts.push(`${x.toFixed(1)},${(midY - env * ampScale).toFixed(1)}`);
      botPts.unshift(`${x.toFixed(1)},${(midY + env * ampScale).toFixed(1)}`);
      rmsTop.push(`${x.toFixed(1)},${(midY - rms * ampScale).toFixed(1)}`);
      rmsBot.unshift(`${x.toFixed(1)},${(midY + rms * ampScale).toFixed(1)}`);
    }

    const svg = svgEl("svg", {
      class: "lp-pcm-svg",
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": variant === "fault"
        ? "Audio recording showing sharp clicking bursts"
        : variant === "slip"
          ? "Audio recording showing belt squeal burst"
          : "Steady motor sound wave"
    });

    svg.append(
      svgEl("line", { x1: padL, y1: midY, x2: width - padR, y2: midY, class: "lp-pcm-zero" }),
      svgEl("line", { x1: padL, y1: (midY - ampScale * 0.55).toFixed(1), x2: width - padR, y2: (midY - ampScale * 0.55).toFixed(1), class: "lp-pcm-guide" }),
      svgEl("line", { x1: padL, y1: (midY + ampScale * 0.55).toFixed(1), x2: width - padR, y2: (midY + ampScale * 0.55).toFixed(1), class: "lp-pcm-guide" }),
      svgEl("polygon", { points: `${topPts.join(" ")} ${botPts.join(" ")}`, class: "lp-pcm-peak", "data-sev": sev }),
      svgEl("polygon", { points: `${rmsTop.join(" ")} ${rmsBot.join(" ")}`, class: "lp-pcm-rms", "data-sev": sev })
    );

    if (!compact) {
      svg.append(svgEl("g", { class: "lp-pcm-axes" }, [
        svgEl("text", { x: 2, y: 15, text: "+" }),
        svgEl("text", { x: 2, y: midY + 3, text: "0" }),
        svgEl("text", { x: 2, y: height - 7, text: "-" })
      ]));
    }
    return svg;
  }

  function createSpectrogramSvg(variant = "fault", compact = false) {
    const w = compact ? 280 : 480;
    const h = compact ? 42 : 104;
    const cols = compact ? 22 : 34;
    const rows = compact ? 6 : 11;
    const padL = compact ? 4 : 38;
    const padR = 4;
    const padT = 4;
    const padB = compact ? 4 : 18;
    const cw = (w - padL - padR) / cols;
    const ch = (h - padT - padB) / rows;

    const svg = svgEl("svg", {
      class: "lp-spec-svg",
      viewBox: `0 0 ${w} ${h}`,
      role: "img",
      "aria-label": `Sound pitch map showing ${variant === "fault" ? "high-pitched clicking" : variant === "slip" ? "brief belt squeal" : "steady low hum"}`
    });

    if (!compact) {
      svg.append(svgEl("g", { class: "lp-spec-axes" }, [
        svgEl("text", { x: 2, y: 14, text: "High" }),
        svgEl("text", { x: 2, y: 54, text: "Mid" }),
        svgEl("text", { x: 2, y: 92, text: "Low" }),
        svgEl("text", { x: padL, y: h - 4, text: "0s" }),
        svgEl("text", { x: w / 2, y: h - 4, text: "2s" }),
        svgEl("text", { x: w - 26, y: h - 4, text: "4.2s" })
      ]));
    }

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
        const op = tier === "bg" ? "0.35" : (0.45 + energy * 0.55).toFixed(2);
        svg.append(svgEl("rect", {
          x: (padL + c * cw + 0.8).toFixed(2),
          y: (padT + r * ch + 0.8).toFixed(2),
          width: Math.max(2, cw - 1.6).toFixed(2),
          height: Math.max(2, ch - 1.6).toFixed(2),
          rx: 1.5,
          opacity: op,
          class: `lp-spec-cell lp-spec-cell--${tier}`
        }));
      }
    }

    if (!compact && variant === "fault") {
      svg.append(svgEl("rect", {
        x: (padL + cols * cw * 0.14).toFixed(1),
        y: (padT + rows * ch * 0.28).toFixed(1),
        width: (cols * cw * 0.72).toFixed(1),
        height: (rows * ch * 0.32).toFixed(1),
        rx: 3,
        class: "lp-spec-focus"
      }));
    }
    return svg;
  }

  function createInkSvg(variant = 0, compact = false) {
    const strokes = [
      "M 18 38 C 22 48, 25 48, 30 22 M 34 36 C 36 30, 42 30, 42 37 C 42 40, 45 40, 47 34 M 50 20 L 49 38 C 52 38, 55 34, 57 31 C 59 38, 63 38, 66 32 C 69 32, 71 38, 76 34 M 92 22 L 92 38 M 99 26 C 106 20, 111 28, 99 38 L 111 38 M 118 22 L 118 38 C 127 38, 127 30, 119 30 C 126 29, 126 22, 118 22 M 138 31 L 154 31 M 166 24 C 174 20, 177 29, 165 38 L 176 38 M 183 37 L 184 37 M 196 22 L 191 33 L 203 33 M 199 22 L 199 39",
      "M 28 24 C 18 22, 18 30, 27 32 C 33 34, 31 40, 20 38 M 36 34 C 42 34, 44 30, 39 30 C 35 30, 35 38, 44 37 M 48 34 C 53 31, 56 38, 50 38 C 46 38, 52 31, 57 37 M 62 20 L 61 38 M 74 30 L 78 38 L 82 31 L 86 38 L 91 30 M 96 34 C 96 29, 104 29, 104 34 C 104 39, 96 39, 96 34 M 110 38 L 110 31 C 113 29, 117 30, 118 32 M 123 38 L 123 31 C 126 29, 131 30, 131 38 M 144 38 L 142 42 M 156 31 L 160 38 L 164 31 L 168 38 L 173 31 M 179 34 C 185 34, 187 30, 182 30 C 178 30, 178 38, 187 37",
      "M 20 26 C 28 20, 32 28, 20 38 L 32 38 M 39 37 L 40 37 M 52 22 L 46 33 L 58 33 M 54 22 L 54 39 M 74 20 L 74 38 C 82 38, 82 30, 74 30 M 89 34 C 94 31, 97 38, 91 38 C 87 38, 93 31, 98 37 M 104 38 L 104 31 C 107 29, 111 30, 112 32 M 126 30 L 127 30 M 144 23 C 138 23, 138 38, 145 38 C 152 38, 152 23, 144 23 M 158 22 L 158 38 M 168 23 L 159 31 L 169 38"
    ];
    const w = compact ? 240 : 260;
    const h = compact ? 56 : 54;
    return svgEl("svg", {
      class: "lp-ink-svg",
      viewBox: `0 0 ${w} ${h}`,
      role: "img",
      "aria-label": "Scanned handwritten line from maintenance logbook"
    }, [
      svgEl("line", { x1: 8, y1: 42, x2: w - 8, y2: 42, class: "lp-ink-rule" }),
      svgEl("line", { x1: 8, y1: 16, x2: w - 8, y2: 16, class: "lp-ink-rule lp-ink-rule--top" }),
      svgEl("rect", { x: 12, y: 12, width: w - 24, height: 34, rx: 3, class: "lp-ink-crop" }),
      svgEl("path", { d: strokes[variant % strokes.length], class: "lp-ink-path" })
    ]);
  }

  function createSignalTraceSvg(mode = "imbalance", sev = "high", compact = false) {
    const maxX = compact ? 280 : 480;
    const maxY = compact ? 76 : 148;
    const padL = compact ? 6 : 38;
    const padR = 8;
    const padT = compact ? 8 : 16;
    const padB = compact ? 8 : 24;
    const plotW = maxX - padL - padR;
    const plotH = maxY - padT - padB;
    const steps = compact ? 96 : 150;
    const thresholdY = Math.round(padT + plotH * (1 - 0.56));

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
        const ramp = 0.16 + 0.42 * (1 / (1 + Math.exp(-(t - 0.32) * 8)));
        const shaft = 0.14 * Math.sin(t * Math.PI * 22) + 0.06 * Math.sin(t * Math.PI * 44);
        vib = Math.max(0.08, Math.min(0.94, ramp + shaft + micro));
        temp = Math.min(0.76, 0.34 + t * 0.32 + 0.015 * Math.sin(t * Math.PI * 5));
      } else if (mode === "cavitation") {
        const b1 = Math.exp(-Math.pow((t - 0.28) / 0.045, 2)) * 0.55;
        const b2 = Math.exp(-Math.pow((t - 0.64) / 0.05, 2)) * 0.62;
        vib = Math.max(0.08, Math.min(0.95, 0.22 + b1 + b2 + ripple * 1.4 + micro));
        temp = 0.36 + t * 0.12;
      } else {
        vib = Math.max(0.08, Math.min(0.4, 0.21 + 0.035 * Math.sin(t * Math.PI * 16) + micro * 0.6));
        temp = 0.28 + 0.015 * Math.sin(t * Math.PI * 3);
      }

      ch1.push([x.toFixed(1), (padT + plotH * (1 - vib)).toFixed(1)]);
      ch2.push([x.toFixed(1), (padT + plotH * (1 - temp)).toFixed(1)]);
    }

    const line1 = ch1.map(([x, y]) => `${x},${y}`).join(" ");
    const area1 = `${padL},${(padT + plotH).toFixed(1)} ${line1} ${(padL + plotW).toFixed(1)},${(padT + plotH).toFixed(1)}`;
    const line2 = ch2.map(([x, y]) => `${x},${y}`).join(" ");

    const svg = svgEl("svg", {
      class: "lp-sig-svg",
      viewBox: `0 0 ${maxX} ${maxY}`,
      role: "img",
      "aria-label": `Machine sensor chart showing ${mode === "imbalance" ? "vibration rising past safe limit" : mode === "cavitation" ? "brief vibration spikes" : "steady readings within safe limit"}`
    });

    [0.25, 0.5, 0.75].forEach((r) => {
      svg.append(svgEl("line", { x1: padL, y1: (padT + plotH * r).toFixed(1), x2: maxX - padR, y2: (padT + plotH * r).toFixed(1), class: "lp-sig-grid" }));
    });
    svg.append(
      svgEl("line", { x1: padL, y1: thresholdY, x2: maxX - padR, y2: thresholdY, class: "lp-sig-limit" }),
      svgEl("polygon", { points: area1, class: "lp-sig-area", "data-sev": sev }),
      svgEl("polyline", { points: line2, fill: "none", class: "lp-sig-line-sec" }),
      svgEl("polyline", { points: line1, fill: "none", class: "lp-sig-line", "data-sev": sev })
    );

    if (!compact) {
      svg.append(
        svgEl("g", {}, [
          svgEl("rect", { x: padL + 6, y: thresholdY - 14, width: 68, height: 14, rx: 3, fill: "#0b1018", stroke: "#f59e0b", "stroke-opacity": "0.45", "stroke-width": "1" }),
          svgEl("text", { x: padL + 11, y: thresholdY - 4, class: "lp-sig-limit-txt", text: "Safe limit" })
        ]),
        svgEl("g", { class: "lp-sig-legend" }, [
          svgEl("text", { x: 4, y: padT + 8, text: "12" }),
          svgEl("text", { x: 8, y: thresholdY + 3, text: "6" }),
          svgEl("text", { x: 8, y: padT + plotH, text: "0" }),
          svgEl("text", { x: padL, y: maxY - 5, text: "Vibration sensor" }),
          svgEl("text", { x: padL + 148, y: maxY - 5, text: "Temperature trend" }),
          svgEl("text", { x: maxX - 42, y: maxY - 5, text: "60 sec" })
        ])
      );
    }
    return svg;
  }

  /* ── Theme Toggle + Responsive Nav ───────────────────────────────────────────────────────────── */

  const themeBtn = document.getElementById("landing-theme-toggle");
  const themeText = document.getElementById("lp-theme-text");
  function syncThemeLabel(theme) {
    if (!themeBtn) return;
    const isDark = theme === "dark";
    themeBtn.setAttribute("aria-pressed", String(isDark));
    themeBtn.setAttribute("aria-label", isDark ? "Switch to light mode" : "Switch to dark mode");
    if (themeText) themeText.textContent = isDark ? "Light" : "Dark";
  }
  syncThemeLabel(document.documentElement.dataset.theme === "dark" ? "dark" : "light");

  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("athena-theme", next); } catch (e) {}
      syncThemeLabel(next);
    });
  }

  const nav = document.getElementById("lp-nav");
  const navInner = document.getElementById("lp-nav-inner");
  const navLinks = document.getElementById("lp-links");
  const menuBtn = document.getElementById("lp-menu");
  const linksList = navLinks ? [...navLinks.querySelectorAll("a")] : [];

  function isPopoverOpen() {
    return Boolean(navLinks && typeof navLinks.matches === "function" && navLinks.matches(":popover-open"));
  }
  function syncNavLayout() {
    if (!nav || !navInner || !navLinks) return;
    nav.classList.add("is-measuring");
    const wasWide = nav.classList.contains("is-wide");
    nav.classList.add("is-wide");
    const fits = navInner.scrollWidth <= navInner.clientWidth + 2;
    if (!fits) nav.classList.remove("is-wide");
    else if (!wasWide && isPopoverOpen() && typeof navLinks.hidePopover === "function") navLinks.hidePopover();
    nav.classList.remove("is-measuring");
  }
  if (nav && navLinks && menuBtn) {
    syncNavLayout();
    window.addEventListener("resize", syncNavLayout, { passive: true });
    navLinks.addEventListener("toggle", (e) => {
      const open = e.newState === "open";
      nav.dataset.open = String(open);
      menuBtn.setAttribute("aria-expanded", String(open));
    });
    linksList.forEach((a) => a.addEventListener("click", () => {
      if (isPopoverOpen() && typeof navLinks.hidePopover === "function") navLinks.hidePopover();
    }));
    window.addEventListener("scroll", () => {
      nav.dataset.scrolled = String(window.scrollY > 24);
    }, { passive: true });
  }

  /* ── Hero 5-Tab Demo Stage ───────────────────────────────────────────────────────────────────── */

  const SCENES = {
    corrosion: {
      mode: "image",
      img: "pipe-corrosion.jpg",
      alt: "Corroded pipes with a rusted clamp",
      q: "“Is there rust, and how urgent is it?”",
      v: "attention",
      word: "Needs repair",
      h: "Heavy rust under the middle clamp and peeling paint on both pipes. Plan repair this month.",
      counts: [["3", "Rust spots"], ["1", "Severe"], ["2", "Check soon"]],
      items: [["high", "Heavy rust under middle clamp"], ["medium", "Peeling paint on left pipe"], ["low", "Minor flaking on right pipe"]],
      boxes: [[36, 30, 58, 32, "high"], [25, 55, 24, 43, "medium"], [52, 62, 22, 36, "low"]]
    },
    parking: {
      mode: "image",
      img: "parking-aerial.jpg",
      alt: "A car park seen from above",
      q: "“How many bays are open right now?”",
      v: "clear",
      word: "Bays open",
      h: "30 out of 51 spaces are empty in this view, mostly in the middle and lower rows.",
      counts: [["51", "Total bays"], ["21", "Parked"], ["30", "Free"]],
      items: [["ok", "Open bays 640–637"], ["ok", "Open bays 660–657"], ["info", "Open bays 617–615 near top"]],
      boxes: [[39, 52, 19, 13, "ok"], [39, 68, 19, 14, "ok"], [45, 23, 13, 13, "info"]]
    },
    footfall: {
      mode: "image",
      img: "store-entrance.jpg",
      alt: "People near a store entrance",
      q: "“Is anything blocking the entrance?”",
      v: "attention",
      word: "Path narrowed",
      h: "People are by the doors and parked bikes narrow the walkway. Still photos show this moment only, not daily traffic.",
      counts: [["6", "People seen"], ["1", "Blocked zone"]],
      items: [["info", "People gathered near doorway"], ["info", "Person entering left doors"], ["medium", "Parked bikes narrowing main doorway"]],
      boxes: [[25, 38, 16, 47, "info"], [44, 39, 10, 33, "info"], [17, 66, 32, 34, "medium"]]
    },
    audio: {
      mode: "audio",
      q: "“Does Pump 04 sound healthy today?”",
      v: "attention",
      word: "Clicking found",
      h: "Repeated clicking stands out clearly from the steady motor hum. Schedule a bearing inspection.",
      counts: [["4.2s", "Sound clip"], ["94%", "Accuracy"], ["1", "Alert"]],
      items: [["ok", "0.0s–1.4s: Normal smooth motor hum"], ["high", "1.4s–3.0s: Sharp bearing clicking"], ["medium", "3.2s–4.0s: Brief vibration rattle"]]
    },
    ocr: {
      mode: "ocr",
      q: "“Turn these handwritten log sheets into clean digital text.”",
      v: "clear",
      word: "3 lines read",
      h: "Handwritten entries read cleanly and converted into structured fields for your shift log.",
      counts: [["3", "Lines read"], ["98%", "Accuracy"], ["0", "Blurred lines"]],
      items: [["ok", "Valve 12B — 2.4 bar"], ["ok", "Seal worn — swap Fri"], ["ok", "2.4 bar · OK"]]
    }
  };

  const frame = document.getElementById("demo-frame");
  const demoImg = document.getElementById("demo-img");
  const demoCustom = document.getElementById("demo-custom-stage");
  const demoScan = document.getElementById("demo-scan");
  const report = document.getElementById("demo-report");
  let currentScene = "corrosion";
  let activeFinding = 0;
  let sceneTimer = null;

  function drawScene(key) {
    const s = SCENES[key];
    frame.querySelectorAll(".lp-dbox").forEach((n) => n.remove());

    if (s.mode === "image") {
      frame.className = "lp-demo-frame";
      demoImg.hidden = false;
      demoScan.hidden = false;
      demoCustom.hidden = true;
      demoCustom.replaceChildren();
      demoImg.src = STATIC + s.img;
      demoImg.alt = s.alt;
      s.boxes.forEach(([x, y, w, h, sev], i) => {
        const box = el("span", {
          class: "lp-dbox",
          "data-sev": sev,
          "data-active": i === activeFinding ? "true" : "false",
          style: { left: `${x}%`, top: `${y}%`, width: `${w}%`, height: `${h}%`, animationDelay: `${0.12 + i * 0.1}s` }
        }, [el("i", { text: String(i + 1) })]);
        frame.append(box);
      });
    } else if (s.mode === "audio") {
      frame.className = "lp-demo-frame lp-demo-frame--audio";
      demoImg.hidden = true;
      demoScan.hidden = true;
      demoCustom.hidden = false;
      demoCustom.replaceChildren(el("div", { class: "lp-hero-modality" }, [
        el("div", { class: "lp-hero-mod-head" }, [
          el("span", { text: "pump-bearing-line4.wav · 4.2s" }),
          el("code", { text: "Clicking detected (1.4s–3.0s)" })
        ]),
        createAudioWaveSvg("fault", false),
        createSpectrogramSvg("fault", false)
      ]));
    } else {
      frame.className = "lp-demo-frame lp-demo-frame--ocr";
      demoImg.hidden = true;
      demoScan.hidden = true;
      demoCustom.hidden = false;
      const lines = ["Valve 12B — 2.4 bar", "Seal worn — swap Fri", "2.4 bar · OK"];
      demoCustom.replaceChildren(el("div", { class: "lp-hero-modality" }, [
        el("div", { class: "lp-hero-mod-head" }, [
          el("span", { text: "maintenance-log-408B.png" }),
          el("code", { text: "3 handwritten lines read" })
        ]),
        el("div", { class: "lp-hero-ocr-stack" }, lines.map((line, idx) => {
          const item = el("button", {
            type: "button",
            class: "lp-hero-ocr-item",
            "aria-pressed": String(idx === activeFinding)
          }, [
            createInkSvg(idx, false),
            el("div", { class: "lp-hero-mod-head" }, [el("code", { text: line })])
          ]);
          item.addEventListener("click", () => { activeFinding = idx; drawScene(key); });
          return item;
        }))
      ]));
    }

    document.getElementById("demo-q").textContent = s.q;
    const verdict = document.getElementById("demo-verdict");
    verdict.dataset.v = s.v;
    verdict.textContent = s.word;
    document.getElementById("demo-h").textContent = s.h;

    document.getElementById("demo-counts").replaceChildren(
      ...s.counts.map(([val, lbl]) => el("div", {}, [el("b", { text: val }), el("span", { text: lbl })]))
    );
    document.getElementById("demo-list").replaceChildren(
      ...s.items.map(([sev, txt], i) => {
        const btn = el("button", {
          type: "button",
          class: "lp-finding-btn",
          "aria-pressed": String(i === activeFinding)
        }, [el("b", { text: String(i + 1) }), el("span", { text: txt })]);
        btn.addEventListener("click", () => { activeFinding = i; drawScene(key); });
        return el("li", { "data-sev": sev }, [btn]);
      })
    );
  }

  function showScene(key, fromUser) {
    if (!SCENES[key] || (key === currentScene && !fromUser)) return;
    currentScene = key;
    activeFinding = 0;
    document.querySelectorAll("[data-scene]").forEach((t) => {
      t.setAttribute("aria-selected", t.dataset.scene === key ? "true" : "false");
    });
    frame.dataset.swap = "true";
    report.dataset.swap = "true";
    setTimeout(() => {
      drawScene(key);
      frame.dataset.swap = "false";
      report.dataset.swap = "false";
    }, 180);
    if (fromUser && sceneTimer) {
      clearInterval(sceneTimer);
      sceneTimer = null;
    }
  }

  if (frame) {
    drawScene(currentScene);
    document.querySelectorAll("[data-scene]").forEach((tab) => {
      tab.addEventListener("click", () => showScene(tab.dataset.scene, true));
    });
    if (!reducedMotion) {
      const order = Object.keys(SCENES);
      sceneTimer = setInterval(() => {
        showScene(order[(order.indexOf(currentScene) + 1) % order.length], false);
      }, 5500);
    }
  }

  /* ── Section 04: Interactive Studio Explorer ─────────────────────────────────────────────────── */

  const OUTCOMES = {
    vision: {
      recipe: "vision",
      badge: "Photo & clip check",
      title: "Sort Photos & Clips",
      status: "Ready to use today",
      tone: "ok",
      filename: "weld-seam-station04.jpg",
      data: "Inspection photos or short 6-frame video clips",
      quality: "Checks how many defects are spotted vs. false alarms",
      howItWorks: "Draw boxes on a few photos so Athena learns what to find",
      split: "Tested on photos the AI has never seen",
      mode: "image",
      stageCaptions: {
        raw: "Upload photos directly from cameras or phones. Originals are saved untouched.",
        label: "Point out defects once — AI guesses stay marked “Check this” until your team confirms.",
        eval: "Reviews accuracy on unseen photos before anyone relies on it in production.",
        deploy: "Go live on your check screen. Uncertain detections ask a human before acting."
      },
      samples: [
        { label: "Weld hole", title: "Pinholes on weld seam (#1)", detail: "Confirmed by team inspector — 3 spots marked clearly.", sev: "high", metrics: [["91%", "Score"], ["94%", "Found"], ["2 / 48", "Missed"]] },
        { label: "Shallow edge", title: "Rough weld edge (#2)", detail: "AI flagged “Check this” — waiting for a supervisor click.", sev: "medium", metrics: [["81%", "AI guess"], ["Wait", "Needs check"], ["14", "Queued"]] },
        { label: "Clean pass", title: "Clean metal check (#3)", detail: "Verified healthy seam — helps prevent false alarms.", sev: "ok", metrics: [["100%", "Clear"], ["0", "False alarms"], ["Good", "Baseline"]] }
      ]
    },
    audio: {
      recipe: "audio",
      badge: "Sound check",
      title: "Listen for Machine Faults",
      status: "Ready to use today",
      tone: "ok",
      filename: "pump-bearing-line4.wav · 12 seconds",
      data: "Short motor & pump sound recordings",
      quality: "Makes sure real machine faults aren’t missed",
      howItWorks: "Turns sounds into clear frequency maps you can see and tag",
      split: "Tested on recordings the AI has never heard before",
      mode: "audio",
      stageCaptions: {
        raw: "Drop audio clips from normal shop microphones or phones.",
        label: "Highlight healthy hum vs. clicking or squealing sections.",
        eval: "Tests on new motors so alerts don’t cry wolf on normal shop noise.",
        deploy: "Runs quietly offline and flags unusual sounds as they happen."
      },
      samples: [
        { label: "Healthy hum", variant: "healthy", title: "Normal steady motor hum", detail: "Baseline sound from a healthy pump — smooth and even.", sev: "ok", metrics: [["Clear", "Status"], ["0", "Alerts"], ["Smooth", "Tone"]] },
        { label: "Bearing click", variant: "fault", title: "Bearing fault — steady clicking", detail: "High-pitched clicking stands out cleanly above the normal motor hum.", sev: "high", metrics: [["94%", "Accuracy"], ["1 / 42", "Missed"], ["Rare", "False alerts"]] },
        { label: "Belt squeal", variant: "slip", title: "Brief belt slip squeal", detail: "Short high-frequency burst caught immediately between normal hums.", sev: "medium", metrics: [["Caught", "Alert"], ["1.6s", "Duration"], ["High", "Pitch"]] }
      ]
    },
    ocr: {
      recipe: "ocr",
      badge: "Handwriting reader",
      title: "Read Handwritten Logs",
      status: "Ready in Studio · Check screen coming next",
      tone: "ok",
      filename: "maintenance-log-408B.png · Scanned form",
      data: "Photos or scans of handwritten forms & paper logs",
      quality: "Checks how often every letter and number matches",
      howItWorks: "Crops each handwritten line and learns your team’s writing style",
      split: "Tested on sheets written by different shift staff",
      mode: "ocr",
      stageCaptions: {
        raw: "Scan or snap paper log sheets — no need to change your clipboards.",
        label: "Fix any misread numbers once so Athena adapts to your handwriting.",
        eval: "Measures word and character accuracy on held-out forms.",
        deploy: "Exports clean digital rows straight into your maintenance tracker."
      },
      samples: [
        { line: 0, title: "Line 01 · “Valve 12B — 2.4 bar”", detail: "Pressure reading turned cleanly into digital text with 98% certainty.", sev: "ok", metrics: [["98%", "Certain"], ["100%", "Numbers right"], ["Ready", "Approved"]] },
        { line: 1, title: "Line 02 · “Seal worn — swap Fri”", detail: "Slightly smudged note — Athena highlights it in yellow to double-check.", sev: "medium", metrics: [["91%", "Certain"], ["Check", "1 smudged word"], ["Review", "Status"]] },
        { line: 2, title: "Line 03 · “2.4 bar · OK”", detail: "Routine clear line logged in seconds without manual retyping.", sev: "ok", metrics: [["99%", "Certain"], ["Clean", "Legible"], ["Instant", "Saved"]] }
      ]
    },
    spreadsheets: {
      recipe: "spreadsheets",
      badge: "Spreadsheet helper",
      title: "Sort Spreadsheets & Rows",
      status: "Ready to use today",
      tone: "ok",
      filename: "asset-readings-q4.csv · 1,240 rows",
      data: "Everyday CSV or Excel tables with past outcomes",
      quality: "Shows which columns mattered most in plain English",
      howItWorks: "Learns patterns across columns like heat, shake speed, and service age",
      split: "Tested on held-out equipment rows",
      mode: "spreadsheets",
      stageCaptions: {
        raw: "Upload CSV or Excel logs — checks automatically for blank or broken rows.",
        label: "Pick the outcome column you want predicted (such as Urgent / Watch / OK).",
        eval: "See accuracy clearly and verify why the AI ranked specific rows as risky.",
        deploy: "Upload tomorrow’s sheet to spot risky pumps before they fail."
      },
      samples: [
        { id: "#1042", status: "Inspect", sev: "high", title: "Row #1042 · Marked “Needs Repair” (91% sure)", detail: "Flagged because heat is 86 °C and vibration hit 8.4 mm/s.", metrics: [["High", "Priority"], ["86°C", "Temp"], ["8.4", "Shake"]] }
      ]
    },
    documents: {
      recipe: "documents",
      badge: "Shift notes & tickets",
      title: "Sort Shift Logs & Tickets",
      status: "Sorting ready · detail pulling coming next",
      tone: "warn",
      filename: "shift-incident-logs.csv · Work orders",
      data: "Past work orders, messages, or emails with team tags",
      quality: "Makes sure urgent notes go to the right crew fast",
      howItWorks: "Spots key repair phrases like “seal”, “tripped”, and “breaker”",
      split: "Tested on notes the AI has never seen",
      mode: "documents",
      stageCaptions: {
        raw: "Import support notes or shift handovers without manual formatting.",
        label: "Pick the team name for each message (Mechanical, Electrical, Routine).",
        eval: "Verifies urgent work orders don’t accidentally get filed under Routine.",
        deploy: "Sorts new messages automatically in milliseconds on a normal office PC."
      },
      samples: [
        { title: "Ticket #401 · Routed to Mechanical (94% sure)", detail: "Matched seal, pump, and gasket wording to the Mechanical repair team.", sev: "high", metrics: [["4", "Categories"], ["100%", "Valid file"], ["Fast", "Runs on CPU"]] }
      ]
    },
    movements: {
      recipe: "signals",
      badge: "Machine sensors",
      title: "Spot Machine Sensor Faults",
      status: "Works on any standard CPU",
      tone: "ok",
      filename: "vibration-readings-q4.csv · 60-second windows",
      data: "A CSV of sensor readings over time with normal / fault labels",
      quality: "How accurately it tells smooth operation from shaking faults",
      howItWorks: "Compare vibration and heat patterns against your Safe Limit",
      split: "Tested on readings the AI has never seen",
      mode: "imbalance",
      limitNote: "Sorting labelled sensor windows works today; predicting future trends hours ahead is coming next.",
      stageCaptions: {
        raw: "Drop time-stamped vibration or heat readings straight from plant sensors.",
        label: "Confirm your machine states — such as Normal run, Cavitation, or Imbalance.",
        eval: "Checks real alarm accuracy so crews don’t waste time on false alerts.",
        deploy: "Monitors incoming sensor streams on a compact edge box right next to the pump."
      },
      samples: [
        { label: "Imbalance", mode: "imbalance", avg: "7.8 mm/s (Over limit)", spike: "11.4 mm/s", dir: "Rising", title: "Reading #088 · Shaft imbalance (Over safe limit)", detail: "Vibration climbed steadily above the 6.5 mm/s safe limit with heavy oscillation.", sev: "high", metrics: [["3", "Machine states"], ["2", "Sensor channels"], ["Safe", "Clean split"]] },
        { label: "Cavitation", mode: "cavitation", avg: "5.2 mm/s", spike: "9.8 mm/s", dir: "Sudden bursts", title: "Reading #091 · Brief cavitation bursts", detail: "Two sudden pressure-collapse spikes crossed the limit briefly before settling.", sev: "medium", metrics: [["2", "Burst spikes"], ["60s", "Window"], ["Watch", "Status"]] },
        { label: "Normal run", mode: "normal", avg: "2.1 mm/s", spike: "3.0 mm/s", dir: "Steady", title: "Reading #094 · Healthy smooth operation", detail: "All readings stayed smoothly below the safe threshold across the full minute.", sev: "ok", metrics: [["Safe", "Below limit"], ["0", "Spikes"], ["Smooth", "Baseline"]] }
      ]
    }
  };

  const STAGE_NAMES = {
    raw: "STEP 01 · ADD FILES",
    label: "STEP 02 · SHOW WHAT TO SPOT",
    eval: "STEP 03 · CHECK ACCURACY",
    deploy: "STEP 04 · READY TO USE"
  };

  const outcomeShell = document.getElementById("lp-outcome-shell");
  let activeOutcome = "audio";
  let activeStage = "label";
  let activeSample = 1;

  function renderStudioVisual(o, sample) {
    const container = document.getElementById("lp-vis-interactive");
    if (!container) return;

    const stageHead = o.samples.length > 1
      ? el("div", { class: "lp-stage-head" }, [
          el("span", { class: "lp-stage-file", text: o.filename }),
          el("div", { class: "lp-vis-samples", role: "group", "aria-label": "Sample scenarios" },
            o.samples.map((s, idx) => {
              const b = el("button", {
                type: "button",
                class: "lp-vis-sample",
                "aria-pressed": String(idx === activeSample),
                text: s.label || s.title
              });
              b.addEventListener("click", () => { activeSample = idx; renderStudio(); });
              return b;
            }))
        ])
      : null;

    if (o.mode === "image") {
      const boxCoords = [
        { left: "22%", top: "24%", width: "26%", height: "38%", text: "#1 Severe", sev: "high" },
        { left: "50%", top: "36%", width: "22%", height: "30%", text: "#2 Check", sev: "medium" },
        { left: "14%", top: "62%", width: "20%", height: "28%", text: "#3 Clear", sev: "low" }
      ];
      const photo = el("div", { class: "lp-stage-media lp-stage-media--photo" }, [
        el("img", { src: `${STATIC}pipe-corrosion.jpg`, alt: "Pipe inspection photo with labelled defect boxes", width: 960, height: 540 }),
        ...boxCoords.map((b, idx) => {
          const btn = el("button", {
            type: "button",
            class: "lp-sbox",
            "data-sev": b.sev,
            "data-selected": String(idx === activeSample),
            style: { left: b.left, top: b.top, width: b.width, height: b.height }
          }, [el("span", { text: b.text })]);
          btn.addEventListener("click", () => { activeSample = idx; renderStudio(); });
          return btn;
        })
      ]);
      container.replaceChildren(el("div", { class: "lp-studio-stage" }, [stageHead, photo]));
      return;
    }

    if (o.mode === "audio") {
      const variant = sample.variant || "fault";
      const sev = sample.sev || "high";
      const panel = el("div", { class: "lp-stage-media lp-stage-media--panel" }, [
        el("div", { class: "lp-clip-head" }, [
          el("span", { class: "lp-clip-spec", text: "pump-bearing-line4.wav · 4.2s" }),
          el("span", { class: "lp-clip-tag", "data-sev": sev, text: sample.label || "Bearing click" })
        ]),
        createAudioWaveSvg(variant, false),
        createSpectrogramSvg(variant, false)
      ]);
      container.replaceChildren(el("div", { class: "lp-studio-stage" }, [stageHead, panel]));
      return;
    }

    if (o.mode === "ocr") {
      const texts = ["Valve 12B — 2.4 bar", "Seal worn — swap Fri", "2.4 bar · OK"];
      const certs = ["98% sure", "Check smudged word", "99% sure"];
      const rows = el("div", { class: "lp-ocr-rows" }, texts.map((txt, idx) => {
        const row = el("button", {
          type: "button",
          class: "lp-ocr-row",
          "aria-pressed": String(idx === activeSample)
        }, [
          el("div", { class: "lp-ocr-scan" }, [createInkSvg(idx, false)]),
          el("div", { class: "lp-ocr-diff" }, [
            el("span", { class: "lp-ocr-tag", text: certs[idx] }),
            el("code", { text: txt })
          ])
        ]);
        row.addEventListener("click", () => { activeSample = idx; renderStudio(); });
        return row;
      }));
      container.replaceChildren(el("div", { class: "lp-studio-stage" }, [rows]));
      return;
    }

    if (o.mode === "spreadsheets") {
      const rows = [
        ["#1042", "86°C", "8.4 mm/s", "Needs Repair", "high"],
        ["#1043", "63°C", "4.9 mm/s", "Watch Closely", "medium"],
        ["#1044", "52°C", "1.8 mm/s", "Normal", "ok"]
      ];
      const clues = [
        ["Shaking level", "68%", 68, "high"],
        ["Bearing heat", "54%", 54, "medium"],
        ["Line pressure", "38%", 38, "ok"],
        ["Days since clean", "24%", 24, "ok"]
      ];
      const tbl = el("div", { class: "lp-stage-media lp-stage-media--table" }, [
        el("table", { class: "lp-mini-table" }, [
          el("thead", {}, [el("tr", {}, [
            el("th", { text: "PUMP ID" }),
            el("th", { text: "HEAT" }),
            el("th", { text: "SHAKE" }),
            el("th", { text: "ATHENA SAYS" })
          ])]),
          el("tbody", {}, rows.map(([id, t, v, s, sev], idx) => el("tr", { "data-selected": String(idx === 0) }, [
            el("td", {}, [el("span", { class: "lp-row-btn", "aria-pressed": String(idx === 0), text: id })]),
            el("td", { text: t }),
            el("td", { text: v }),
            el("td", {}, [el("span", { class: "lp-table-pred", "data-sev": sev, text: s })])
          ])))
        ])
      ]);
      const explain = el("div", { class: "lp-tab-drivers" }, [
        el("span", { class: "lp-clip-spec", text: "Biggest clues in this spreadsheet" }),
        el("div", { class: "lp-driver-bars" }, clues.map(([name, val, pct, sev]) => el("div", { class: "lp-driver-row" }, [
          el("code", { text: name }),
          el("div", { class: "lp-driver-track" }, [el("i", { "data-sev": sev, style: { width: `${pct}%` } })]),
          el("b", { text: val })
        ])))
      ]);
      container.replaceChildren(el("div", { class: "lp-studio-stage" }, [
        tbl,
        el("div", { class: "lp-stage-media lp-stage-media--panel" }, [explain])
      ]));
      return;
    }

    if (o.mode === "documents") {
      const tickets = [
        ["#401", "Pump P-104B mechanical seal weeping oil at 78 °C; isolate and replace gasket.", "Mechanical (94%)", "high"],
        ["#402", "Compressor panel breaker tripped twice on startup — inspect relay contacts.", "Electrical (89%)", "medium"],
        ["#403", "Routine filter washdown completed cleanly; pressure returned to normal.", "Routine (97%)", "ok"]
      ];
      const tokens = [["seal", "high"], ["weeping", "high"], ["gasket", "med"], ["breaker", "med"], ["normal", "ok"]];
      const panel = el("div", { class: "lp-stage-media lp-stage-media--panel" }, [
        el("div", { class: "lp-doc-list" }, tickets.map(([id, txt, badge, sev], idx) => el("div", {
          class: "lp-doc-row",
          "aria-pressed": String(idx === 0)
        }, [
          el("div", { class: "lp-doc-top" }, [
            el("code", { text: `Ticket ${id}` }),
            el("span", { class: "lp-doc-class", "data-sev": sev, text: badge })
          ]),
          el("p", { text: txt })
        ]))),
        el("div", { class: "lp-token-strip" }, [
          el("span", { class: "lp-clip-spec", text: "Key words Athena noticed" }),
          el("div", { class: "lp-token-chips" }, tokens.map(([w, weight]) => el("span", { "data-w": weight, text: w })))
        ])
      ]);
      container.replaceChildren(el("div", { class: "lp-studio-stage" }, [panel]));
      return;
    }

    // Machine sensors ("movements" -> 150-point continuous chart + Safe limit badge)
    const panel = el("div", { class: "lp-stage-media lp-stage-media--panel" }, [
      el("div", { class: "lp-clip-head" }, [
        el("span", { class: "lp-clip-spec", text: "Reading #088 · 60-SECOND MACHINE LOG" }),
        el("span", { class: "lp-clip-tag", "data-sev": sample.sev || "high", text: `${sample.label || "Imbalance"} (93% sure)` })
      ]),
      createSignalTraceSvg(sample.mode || "imbalance", sample.sev || "high", false),
      el("div", { class: "lp-sig-stats" }, [
        el("div", {}, [el("span", { text: "Average shake" }), el("b", { text: sample.avg || "7.8 mm/s" })]),
        el("div", {}, [el("span", { text: "Highest spike" }), el("b", { text: sample.spike || "11.4 mm/s" })]),
        el("div", {}, [el("span", { text: "Direction" }), el("b", { text: sample.dir || "Rising" })])
      ])
    ]);
    container.replaceChildren(el("div", { class: "lp-studio-stage" }, [stageHead, panel]));
  }

  function renderGalleryThumbs() {
    document.querySelectorAll("[data-mini-modality]").forEach((host) => {
      const mod = host.dataset.miniModality;
      if (mod === "vision") {
        host.replaceChildren(el("div", { class: "lp-thumb lp-thumb--photo" }, [
          el("img", { src: `${STATIC}pipe-corrosion.jpg`, alt: "", width: 320, height: 180 }),
          el("span", { class: "lp-thumb-box lp-thumb-box--ok", style: { left: "34%", top: "24%", width: "54%", height: "38%" }, text: "Rust" }),
          el("span", { class: "lp-thumb-box lp-thumb-box--draft", style: { left: "22%", top: "54%", width: "28%", height: "36%" }, text: "Check" })
        ]));
      } else if (mod === "audio") {
        host.replaceChildren(el("div", { class: "lp-thumb lp-thumb--audio" }, [
          createAudioWaveSvg("fault", true),
          createSpectrogramSvg("fault", true)
        ]));
      } else if (mod === "ocr") {
        host.replaceChildren(el("div", { class: "lp-thumb lp-thumb--ocr" }, [
          createInkSvg(0, true),
          el("div", { class: "lp-thumb-ocr-meta" }, [el("span", { text: "Read as" }), el("code", { text: "Valve 12B — 2.4 bar" })])
        ]));
      } else if (mod === "spreadsheets") {
        const rows = [["#1042", "86°C", "8.4", "Inspect", "high", 32], ["#1043", "63°C", "4.9", "Watch", "med", 52], ["#1044", "52°C", "1.8", "Normal", "ok", 72]];
        const svg = svgEl("svg", { class: "lp-thumb-svg", viewBox: "0 0 280 96" }, [
          svgEl("rect", { x: 8, y: 8, width: 264, height: 20, rx: 3, class: "lp-th-head" }),
          svgEl("text", { x: 16, y: 21, class: "lp-th-txt", text: "UNIT" }),
          svgEl("text", { x: 72, y: 21, class: "lp-th-txt", text: "TEMP" }),
          svgEl("text", { x: 128, y: 21, class: "lp-th-txt", text: "SHAKE" }),
          svgEl("text", { x: 184, y: 21, class: "lp-th-txt-accent", text: "RISK" }),
          ...rows.map(([id, t, v, pred, sev, y]) => svgEl("g", {}, [
            svgEl("rect", { x: 8, y, width: 264, height: 17, rx: 2, class: `lp-th-row lp-th-row--${sev}` }),
            svgEl("text", { x: 16, y: y + 12, class: "lp-th-cell", text: id }),
            svgEl("text", { x: 72, y: y + 12, class: "lp-th-cell", text: t }),
            svgEl("text", { x: 128, y: y + 12, class: "lp-th-cell", text: v }),
            svgEl("text", { x: 184, y: y + 12, class: `lp-th-pred lp-th-pred--${sev}`, text: pred })
          ]))
        ]);
        host.replaceChildren(el("div", { class: "lp-thumb lp-thumb--svg" }, [svg]));
      } else if (mod === "documents") {
        const rows = [["Pump P-104B weeping", "Mechanical", "high", 32], ["Breaker tripped startup", "Electrical", "med", 56]];
        const svg = svgEl("svg", { class: "lp-thumb-svg", viewBox: "0 0 280 96" }, [
          svgEl("rect", { x: 8, y: 8, width: 264, height: 80, rx: 5, class: "lp-th-head" }),
          svgEl("text", { x: 16, y: 24, class: "lp-th-txt", text: "INCOMING SHIFT NOTES" }),
          ...rows.map(([note, dept, sev, y]) => svgEl("g", {}, [
            svgEl("rect", { x: 14, y, width: 252, height: 20, rx: 3, class: `lp-th-row lp-th-row--${sev}` }),
            svgEl("text", { x: 20, y: y + 13, class: "lp-th-cell", text: note }),
            svgEl("text", { x: 258, y: y + 13, "text-anchor": "end", class: `lp-th-pred lp-th-pred--${sev}`, text: dept })
          ]))
        ]);
        host.replaceChildren(el("div", { class: "lp-thumb lp-thumb--svg" }, [svg]));
      } else {
        host.replaceChildren(el("div", { class: "lp-thumb lp-thumb--svg" }, [
          createSignalTraceSvg("imbalance", "high", true)
        ]));
      }
    });
  }

  function renderStudio() {
    const o = OUTCOMES[activeOutcome];
    if (!o) return;
    const sample = o.samples[Math.min(activeSample, o.samples.length - 1)] || o.samples[0];

    document.getElementById("lp-outcome-badge").textContent = o.badge;
    document.getElementById("lp-outcome-title").textContent = o.title;
    const st = document.getElementById("lp-outcome-status");
    st.textContent = o.status;
    st.dataset.tone = o.tone;
    document.getElementById("lp-vis-filename").textContent = o.filename;
    document.getElementById("lp-vis-mode").textContent = STAGE_NAMES[activeStage] || STAGE_NAMES.label;
    document.getElementById("lp-hud-eyebrow").textContent = o.badge;
    document.getElementById("lp-vis-selection-title").textContent = sample.title;
    document.getElementById("lp-vis-selection-detail").textContent = sample.detail;
    document.getElementById("lp-hud-caption").textContent = o.stageCaptions[activeStage] || "";

    const lim = document.getElementById("lp-hud-limit");
    if (o.limitNote) { lim.hidden = false; lim.textContent = o.limitNote; }
    else { lim.hidden = true; lim.textContent = ""; }

    document.getElementById("lp-vis-metrics").replaceChildren(
      ...(sample.metrics || []).map(([v, l]) => el("div", {}, [el("b", { text: v }), el("span", { text: l })]))
    );

    document.getElementById("lp-outcome-data").textContent = o.data;
    document.getElementById("lp-outcome-quality").textContent = o.quality;
    document.getElementById("lp-outcome-recipe").textContent = o.howItWorks;
    document.getElementById("lp-vis-split").textContent = o.split;
    document.getElementById("lp-outcome-cta").href = `/login/?next=/detectors%3Frecipe%3D${o.recipe}`;

    const panel = document.getElementById("lp-outcome-panel");
    if (panel) panel.setAttribute("aria-labelledby", `lp-outcome-tab-${activeOutcome}`);

    document.querySelectorAll("#lp-outcome-tabs [data-outcome]").forEach((tab) => {
      const sel = tab.dataset.outcome === activeOutcome;
      tab.setAttribute("aria-selected", String(sel));
      tab.tabIndex = sel ? 0 : -1;
    });
    document.querySelectorAll("#lp-stage-tabs [data-stage]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(btn.dataset.stage === activeStage));
    });
    document.querySelectorAll("#lp-modality-gallery [data-jump-outcome]").forEach((card) => {
      card.setAttribute("aria-pressed", String(card.dataset.jumpOutcome === activeOutcome));
    });

    renderStudioVisual(o, sample);
  }

  const outcomeTabs = [...document.querySelectorAll("#lp-outcome-tabs [data-outcome]")];
  if (outcomeTabs.length) {
    renderStudio();
    renderGalleryThumbs();

    outcomeTabs.forEach((tab, idx) => {
      tab.addEventListener("click", () => {
        activeOutcome = tab.dataset.outcome;
        activeSample = activeOutcome === "audio" ? 1 : 0;
        renderStudio();
      });
      tab.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        e.preventDefault();
        const nextIdx = e.key === "ArrowRight" ? (idx + 1) % outcomeTabs.length : (idx - 1 + outcomeTabs.length) % outcomeTabs.length;
        const nextTab = outcomeTabs[nextIdx];
        activeOutcome = nextTab.dataset.outcome;
        activeSample = activeOutcome === "audio" ? 1 : 0;
        renderStudio();
        nextTab.focus();
      });
    });

    document.querySelectorAll("#lp-stage-tabs [data-stage]").forEach((btn) => {
      btn.addEventListener("click", () => { activeStage = btn.dataset.stage; renderStudio(); });
    });

    document.querySelectorAll("[data-jump-outcome]").forEach((card) => {
      card.addEventListener("click", () => {
        const key = card.dataset.jumpOutcome;
        if (!OUTCOMES[key]) return;
        activeOutcome = key;
        activeSample = key === "audio" ? 1 : 0;
        renderStudio();
        outcomeShell?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" });
      });
    });

    if (outcomeShell) {
      outcomeShell.addEventListener("contentvisibilityautostatechange", () => {});
    }
  }

  /* ── Section 05: Combined Site Monitor Scenarios ─────────────────────────────────────────────── */

  const FUSION_SCENARIOS = {
    motor: {
      siteTitle: "Pump Station 04 · Combined Check",
      edgeNode: "Runs offline on your plant computer",
      verdictBadge: "Needs attention",
      verdict: "Bearing wear on Pump 04 — inspect within 48 hours",
      whyUseful: "A single sensor can easily cry wolf. By checking sound, vibration, and bearing temperature together, your maintenance team catches real wear early without chasing false alarms.",
      checks: [
        "Microphone hears repeated clicking that wasn’t there last week",
        "Vibration crossed the 6.5 mm/s safe limit and keeps oscillating",
        "Bearing housing warmed up from 62 °C to 74 °C"
      ],
      fallbackNote: "Keeps watching even if the microphone disconnects — vibration and heat still protect the pump.",
      actionLabel: "Supervisor decides · Schedule check or dismiss",
      streams: [
        { kind: "Sound check", title: "Pump microphone", val: "Clicking noise detected", graphic: "audio" },
        { kind: "Vibration", title: "Shaft sensor", val: "Crossed safe limit", graphic: "signal", mode: "imbalance", sev: "high" },
        { kind: "Temperature", title: "Bearing heat", val: "74 °C (normal is 62 °C)", graphic: "gauge", pct: 78, sev: "medium" }
      ]
    },
    thermal: {
      siteTitle: "Line 12 Walkdown · Field Check",
      edgeNode: "Runs on field laptop · No internet needed",
      verdictBadge: "Action needed",
      verdict: "Hotspot (+28 °C) matches rusted clamp on Line 12 — inspect seal",
      whyUseful: "Sunlight glare can fool a thermal camera on its own, and a normal photo cannot feel heat. Matching the heat spot directly to visible rust and service history shows crews exactly which clamp needs a gasket.",
      checks: [
        "Thermal view shows +28 °C hotspot centered on the pipe clamp",
        "Normal photo confirms heavy rust and paint flaking in the exact spot",
        "Maintenance log shows 410 days since the last gasket change"
      ],
      fallbackNote: "Works right on your field laptop during walkdowns — photos never leave your computer.",
      actionLabel: "Crew decides · Tag clamp for repair",
      streams: [
        { kind: "Thermal camera", title: "Heat view", val: "+28 °C hot spot on clamp", graphic: "thermal" },
        { kind: "Normal camera", title: "Pipe photo", val: "Heavy rust around clamp", graphic: "photo" },
        { kind: "Service log", title: "Line 12 history", val: "410 days since last service", graphic: "gauge", pct: 86, sev: "high" }
      ]
    },
    safety: {
      siteTitle: "North Loading Yard · Gate Check",
      edgeNode: "Runs locally on site · Photos stay private",
      verdictBadge: "Needs attention",
      verdict: "Entry gate is open while loading bays are busy — check lane",
      whyUseful: "A gate sensor alone doesn’t know if the yard is full, and a photo alone doesn’t know if the gate is locked. Combining camera bays, gate status, and vehicle flow prevents yard bottlenecks.",
      checks: [
        "Overhead camera sees 21 bays taken and heavy traffic in middle row",
        "Gate sensor reports the north loading gate is currently OPEN",
        "Entry lane activity shows vehicles queuing at the entrance"
      ],
      fallbackNote: "Runs completely inside your warehouse server — zero video or photos sent to external clouds.",
      actionLabel: "Dispatcher decides · Direct trucks to open south bays",
      streams: [
        { kind: "Overhead camera", title: "North yard bays", val: "21 of 51 bays occupied", graphic: "parking" },
        { kind: "Gate sensor", title: "North entry gate", val: "Gate OPEN right now", graphic: "plc" },
        { kind: "Lane activity", title: "Arrival sensor", val: "Vehicle burst at entry", graphic: "signal", mode: "cavitation", sev: "medium" }
      ]
    }
  };

  function buildStreamGraphic(s) {
    if (s.graphic === "audio") return el("div", { class: "lp-fs-gfx", "aria-hidden": "true" }, [createAudioWaveSvg("fault", true)]);
    if (s.graphic === "signal") return el("div", { class: "lp-fs-gfx", "aria-hidden": "true" }, [createSignalTraceSvg(s.mode || "imbalance", s.sev || "high", true)]);
    if (s.graphic === "photo") {
      return el("div", { class: "lp-fs-gfx lp-fs-gfx--photo", "aria-hidden": "true" }, [
        el("img", { src: `${STATIC}pipe-corrosion.jpg`, alt: "", width: 280, height: 140 }),
        el("span", { class: "lp-fs-box", style: { left: "34%", top: "24%", width: "54%", height: "42%" } }, [el("i", { text: "Rust" })])
      ]);
    }
    if (s.graphic === "thermal") {
      return el("div", { class: "lp-fs-gfx lp-fs-gfx--thermal", "aria-hidden": "true" }, [
        el("img", { src: `${STATIC}pipe-corrosion.jpg`, alt: "", width: 280, height: 140 }),
        el("span", { class: "lp-fs-hotspot" }),
        el("span", { class: "lp-fs-temp-tag", text: "+28 °C" })
      ]);
    }
    if (s.graphic === "parking") {
      return el("div", { class: "lp-fs-gfx lp-fs-gfx--photo", "aria-hidden": "true" }, [
        el("img", { src: `${STATIC}parking-aerial.jpg`, alt: "", width: 280, height: 140 }),
        el("span", { class: "lp-fs-box lp-fs-box--ok", style: { left: "38%", top: "48%", width: "22%", height: "34%" } }, [el("i", { text: "30 free" })])
      ]);
    }
    if (s.graphic === "plc") {
      return el("div", { class: "lp-fs-gfx lp-fs-gfx--plc", "aria-hidden": "true" }, [
        el("span", { class: "lp-plc-state" }, [el("i"), "GATE OPEN"]),
        el("span", { class: "lp-plc-sub", text: "North bay entry active" })
      ]);
    }
    return el("div", { class: "lp-fs-gfx lp-fs-gfx--gauge", "aria-hidden": "true" }, [
      el("div", { class: "lp-fs-gauge-bar" }, [el("span", { style: { width: `${s.pct || 72}%` }, "data-sev": s.sev })]),
      el("div", { class: "lp-fs-gauge-scale" }, [el("span", { text: "Safe" }), el("span", { text: "Watch" }), el("span", { text: "Alert" })])
    ]);
  }

  function renderFusion(key) {
    const scenario = FUSION_SCENARIOS[key];
    if (!scenario) return;
    document.getElementById("lp-monitor-title").textContent = scenario.siteTitle;
    document.getElementById("lp-fusion-badge").textContent = scenario.edgeNode;
    document.getElementById("lp-monitor-verdict-badge").textContent = scenario.verdictBadge;
    document.getElementById("lp-fusion-verdict").textContent = scenario.verdict;
    document.getElementById("lp-monitor-why").textContent = scenario.whyUseful;
    document.getElementById("lp-monitor-fallback").textContent = scenario.fallbackNote;
    document.getElementById("lp-monitor-action").textContent = scenario.actionLabel;

    document.getElementById("lp-monitor-inputs").replaceChildren(
      el("p", { class: "lp-monitor-subhead", text: "1 · What Athena is checking on site" }),
      ...scenario.streams.map((stream) => el("div", { class: "lp-monitor-feed" }, [
        el("div", { class: "lp-monitor-feed-text" }, [
          el("span", { class: "lp-fusion-kind", text: stream.kind }),
          el("strong", { text: stream.title }),
          el("span", { class: "lp-fusion-val", text: stream.val })
        ]),
        buildStreamGraphic(stream)
      ]))
    );

    document.getElementById("lp-monitor-checks").replaceChildren(
      ...scenario.checks.map((item) => el("li", { text: item }))
    );
  }

  const fusionTabs = [...document.querySelectorAll("#lp-fusion-tabs [data-fusion]")];
  if (fusionTabs.length) {
    renderFusion("motor");
    fusionTabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const key = tab.dataset.fusion;
        if (!FUSION_SCENARIOS[key]) return;
        fusionTabs.forEach((btn) => btn.setAttribute("aria-pressed", String(btn === tab)));
        renderFusion(key);
      });
    });
  }

  /* ── Reveal on scroll ────────────────────────────────────────────────────────────────────────── */

  const revealItems = document.querySelectorAll(".lp-reveal");
  if ("IntersectionObserver" in window) {
    const seen = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-in");
          seen.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    revealItems.forEach((item) => seen.observe(item));
  } else {
    revealItems.forEach((item) => item.classList.add("is-in"));
  }
})();
