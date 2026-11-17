/* Landing page: the live demo tabs, the mobile menu, the sticky-nav edge, and reveal-on-scroll. */
(() => {
  "use strict";
  const STATIC = "/static/projects/landing/";
  const SCENES = {
    corrosion: {
      img: "pipe-corrosion.jpg", alt: "Corroded pipes with a rusted clamp",
      q: "“Is there corrosion, and how bad is it?”", v: "attention", word: "Needs attention",
      h: "Severe scaling at the pipe clamp, with coating breaking down on both pipes. Plan a repair this month.",
      counts: [["3", "Corroded areas"], ["1", "Severe"], ["2", "Pipes affected"]],
      items: [["high", "Severe scaling under the clamp band"], ["medium", "Coating breakdown, left pipe"], ["low", "Paint flaking, right pipe"]],
      boxes: [[36, 30, 58, 32, "high"], [25, 55, 24, 43, "medium"], [52, 62, 22, 36, "low"]]
    },
    parking: {
      img: "parking-aerial.jpg", alt: "A car park seen from above",
      q: "“How many spaces are free?”", v: "clear", word: "All clear",
      h: "30 of 51 marked spaces are free. The middle and bottom rows have the most room.",
      counts: [["51", "Total spaces"], ["21", "Occupied"], ["30", "Free"]],
      items: [["ok", "Free block, bays 640–637"], ["ok", "Free block, bays 660–657"], ["info", "Free bays 617–615, top row"]],
      boxes: [[39, 52, 19, 13, "ok"], [39, 68, 19, 14, "ok"], [45, 23, 13, 13, "info"]]
    },
    footfall: {
      img: "store-entrance.jpg", alt: "People at a store entrance",
      q: "“What is visible near the entrance?”", v: "attention", word: "Needs attention",
      h: "People are visible near the entrance. Parked bikes are narrowing the doorway. A still picture cannot establish crossing totals.",
      counts: [["6", "People in view"]],
      items: [["info", "People near the entrance"], ["info", "Person by the doors"], ["medium", "Bikes narrowing the entrance"]],
      boxes: [[25, 38, 16, 47, "info"], [44, 39, 10, 33, "info"], [17, 66, 32, 34, "medium"]]
    }
  };

  const frame = document.getElementById("demo-frame");
  const img = document.getElementById("demo-img");
  const report = document.getElementById("demo-report");
  let current = "corrosion";
  let auto = null;

  function draw(key) {
    const s = SCENES[key];
    frame.querySelectorAll(".lp-dbox").forEach(n => n.remove());
    s.boxes.forEach(([x, y, w, h, sev], i) => {
      const box = document.createElement("span");
      box.className = "lp-dbox";
      box.dataset.sev = sev;
      box.style.left = `${x}%`;
      box.style.top = `${y}%`;
      box.style.width = `${w}%`;
      box.style.height = `${h}%`;
      box.style.animationDelay = `${0.15 + i * 0.12}s`;
      const tag = document.createElement("i");
      tag.textContent = String(i + 1);
      box.append(tag);
      frame.append(box);
    });
    document.getElementById("demo-q").textContent = s.q;
    const verdict = document.getElementById("demo-verdict");
    verdict.dataset.v = s.v;
    verdict.textContent = s.word;
    document.getElementById("demo-h").textContent = s.h;
    const counts = document.getElementById("demo-counts");
    counts.replaceChildren(...s.counts.map(([value, label]) => {
      const cell = document.createElement("div");
      const b = document.createElement("b");
      b.textContent = value;
      const span = document.createElement("span");
      span.textContent = label;
      cell.append(b, span);
      return cell;
    }));
    const list = document.getElementById("demo-list");
    list.replaceChildren(...s.items.map(([sev, text], i) => {
      const li = document.createElement("li");
      li.dataset.sev = sev;
      const b = document.createElement("b");
      b.textContent = String(i + 1);
      li.append(b, document.createTextNode(text));
      return li;
    }));
  }

  function show(key, fromUser) {
    if (!SCENES[key] || key === current && !fromUser) return;
    current = key;
    document.querySelectorAll("[data-scene]").forEach(t => t.setAttribute("aria-selected", t.dataset.scene === key ? "true" : "false"));
    frame.dataset.swap = "true";
    report.dataset.swap = "true";
    setTimeout(() => {
      img.src = STATIC + SCENES[key].img;
      img.alt = SCENES[key].alt;
      draw(key);
      frame.dataset.swap = "false";
      report.dataset.swap = "false";
    }, 220);
    if (fromUser) { clearInterval(auto); auto = null; }
  }

  if (frame) {
    draw(current);
    document.querySelectorAll("[data-scene]").forEach(tab => tab.addEventListener("click", () => show(tab.dataset.scene, true)));
    const order = Object.keys(SCENES);
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      auto = setInterval(() => show(order[(order.indexOf(current) + 1) % order.length], false), 6500);
    }
  }

  const nav = document.getElementById("lp-nav");
  const navInner = document.getElementById("lp-nav-inner");
  const navLinks = document.getElementById("lp-links");
  const menu = document.getElementById("lp-menu");
  const supportsPopover = typeof HTMLElement !== "undefined" && Object.hasOwn(HTMLElement.prototype, "popover");
  let wideMeasurePending = false;

  function isPopoverOpen(el) {
    if (!supportsPopover || !el || typeof el.matches !== "function") return false;
    try {
      return el.matches(":popover-open");
    } catch {
      return false;
    }
  }

  function syncNavLayout() {
    if (!nav || !navInner || !navLinks || !menu) return;
    const wasOpen = isPopoverOpen(navLinks);
    nav.classList.add("is-measuring", "is-wide");
    navLinks.removeAttribute("popover");
    const fits = navInner.scrollWidth <= navInner.clientWidth + 1;
    nav.classList.toggle("is-wide", fits);
    if (fits) {
      menu.setAttribute("aria-expanded", "false");
      nav.dataset.open = "false";
    } else if (supportsPopover) {
      navLinks.setAttribute("popover", "auto");
      if (wasOpen && typeof navLinks.showPopover === "function") navLinks.showPopover();
    }
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => nav.classList.remove("is-measuring"));
    } else {
      nav.classList.remove("is-measuring");
    }
  }

  if (nav && navInner && navLinks && menu) {
    syncNavLayout();
    if ("ResizeObserver" in window) {
      let lastHeaderW = nav.clientWidth || 0;
      const navRo = new ResizeObserver(() => {
        if (wideMeasurePending) return;
        const wNow = nav.clientWidth || 0;
        const isWide = nav.classList.contains("is-wide");
        if (isWide || wNow > lastHeaderW) {
          wideMeasurePending = true;
          lastHeaderW = wNow;
          syncNavLayout();
          wideMeasurePending = false;
        } else {
          lastHeaderW = wNow;
        }
      });
      navRo.observe(nav);
      navRo.observe(navLinks);
    }
    if (supportsPopover) {
      navLinks.addEventListener("toggle", event => {
        const open = event.newState === "open";
        menu.setAttribute("aria-expanded", open ? "true" : "false");
        nav.dataset.open = open ? "true" : "false";
      });
    } else {
      menu.addEventListener("click", () => {
        const open = nav.dataset.open !== "true";
        nav.dataset.open = open ? "true" : "false";
        menu.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }
    navLinks.querySelectorAll("a").forEach(a => a.addEventListener("click", () => {
      nav.dataset.open = "false";
      menu.setAttribute("aria-expanded", "false");
      if (isPopoverOpen(navLinks) && typeof navLinks.hidePopover === "function") navLinks.hidePopover();
    }));
    let lastScrollY = window.scrollY || 0;
    const supportsScrollState = typeof CSS !== "undefined" && typeof CSS.supports === "function" && CSS.supports("container-type", "scroll-state");
    const onScroll = () => {
      const y = Math.max(0, window.scrollY || 0);
      nav.dataset.scrolled = y > 8 ? "true" : "false";
      if (!supportsScrollState) {
        const maxY = Math.max(0, (document.documentElement?.scrollHeight || 0) - (window.innerHeight || 0));
        const clamped = Math.min(y, maxY);
        if (clamped !== lastScrollY) {
          nav.classList.toggle("is-hidden", clamped > lastScrollY && clamped > (nav.offsetHeight || 68) && nav.dataset.open !== "true");
          lastScrollY = clamped;
        }
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  const STAGES = {
    raw: "STAGE 01 · RAW DATA",
    label: "STAGE 02 · TEACH WITH LABELS",
    eval: "STAGE 03 · HELD-OUT CHECK",
    deploy: "STAGE 04 · DEPLOY & IMPROVE"
  };

  const OUTCOMES = {
    vision: {
      badge: "P0 · Locally verified slice + Wave 1 MVP",
      title: "Object & Defect Detection",
      status: "Available in console (local prototype)",
      filename: "weld-seam-station04.jpg · 1920 × 1080",
      split: "80% Train · 10% Val · 10% Held-out",
      scenario: "An engineer labels equipment, defective components or anomalies. Athena trains a compatible detector, evaluates on held-out pictures, and deploys a check screen.",
      data: "JPEG, PNG or WebP pictures + boxes",
      quality: "Recall, precision, mAP and false positives",
      recipe: "OS-VIS-001 · Image dataset, annotation, training, held-out evaluation and check screen",
      href: "/login/?next=/detectors%3Frecipe%3Dvision",
      stageCaptions: {
        raw: "Upload inspection photos with SHA-256 checksums. Athena verifies resolution, dimensions and keeps every file untouched.",
        label: "Draw bounding boxes manually or review SAM + CLIP suggestions. Boxes stay “Needs review” until a person accepts them.",
        eval: "Evaluate on held-out test images: see found vs missed items, false alarms, mAP50 and exact missed-box previews.",
        deploy: "Publish an approved model to an operator check screen. Corrections flow back as reviewable candidates without silently changing weights."
      },
      stageMetrics: {
        raw: [["240", "Verified photos"], ["0", "Unchecked boxes"], ["100%", "SHA-256 intact"]],
        label: [["418", "Accepted boxes"], ["14", "Needs review"], ["3", "Target classes"]],
        eval: [["0.91", "mAP50"], ["94%", "Defect recall"], ["2 / 48", "Missed items"]],
        deploy: [["v1.4", "Approved model"], ["100%", "Human reviewed"], ["1-click", "Rollback safe"]]
      },
      samples: [
        {
          label: "Weld porosity",
          title: "Weld porosity cluster (box 01)",
          detail: "Accepted box on seam B · included in snapshot v3.",
          rect: [0.09, 0.24, 0.2, 0.34],
          sev: "high"
        },
        {
          label: "Undercut groove",
          title: "Undercut groove along toe (box 02)",
          detail: "Auto-suggest (0.81) · stays Needs review until accepted.",
          rect: [0.3, 0.42, 0.18, 0.28],
          sev: "medium"
        },
        {
          label: "Clean weld pass",
          title: "Clean reference pass (negative check)",
          detail: "Verified clean zone on held-out split.",
          rect: [0.46, 0.2, 0.13, 0.32],
          sev: "ok"
        }
      ]
    },
    audio: {
      badge: "P0 · Wave 1 Multi-task MVP",
      title: "Acoustic Event Detection",
      status: "Proposed roadmap recipe",
      filename: "pump-bearing-line4.wav · 48 kHz · 12.0s",
      split: "80% Train · 10% Val · 10% Held-out",
      scenario: "Healthy motor baseline vs bearing faults.",
      data: "WAV recordings + event labels",
      quality: "Missed sound events and false alarms",
      recipe: "OS-AUD-001 · Audio classifier + test check",
      href: "/login/?next=/detectors%3Frecipe%3Daudio",
      stageCaptions: {
        raw: "Upload WAV motor clips; Athena verifies sample rate & checksum.",
        label: "Mark healthy and faulty windows on the spectrogram & waveform.",
        eval: "Check Event F1, missed faults and false alarms/hr on unseen motors.",
        deploy: "Run live acoustic checks or export ONNX with human review."
      },
      stageMetrics: {
        raw: [["48 kHz", "WAV PCM audio"], ["18 clips", "Motor sessions"], ["0", "Clipped files"]],
        label: [["64", "Event windows"], ["3", "Acoustic classes"], ["100%", "Reviewer signed"]],
        eval: [["0.94", "Event F1"], ["1 / 42", "Missed faults"], ["0.4 / hr", "False alarms"]],
        deploy: [["ONNX", "Edge export"], ["Baseline", "Anomaly watch"], ["v1.2", "Approved bundle"]]
      },
      samples: [
        {
          label: "Healthy hum",
          title: "Healthy baseline hum (0.6s–3.2s)",
          detail: "Steady 60 Hz motor tone establishing normal baseline.",
          rect: [0.05, 0.16, 0.16, 0.72],
          sev: "ok"
        },
        {
          label: "Bearing fault 3.4 kHz",
          title: "Bearing fault 3.4 kHz (4.2s–6.8s)",
          detail: "Harmonic spike marked on timeline; checked against held-out faults.",
          rect: [0.23, 0.16, 0.17, 0.72],
          sev: "high"
        },
        {
          label: "Cavitation spike",
          title: "Transient cavitation burst (8.4s–10.6s)",
          detail: "Broadband high-frequency burst captured for multi-class classifier.",
          rect: [0.42, 0.16, 0.16, 0.72],
          sev: "medium"
        }
      ]
    },
    ocr: {
      badge: "P0 · Wave 1 Multi-task MVP",
      title: "Handwriting & Document OCR",
      status: "Proposed roadmap recipe (Forge TrOCR)",
      filename: "maintenance-log-408B.png · Handwritten form",
      split: "80% Train · 10% Val · 10% Held-out",
      scenario: "Correct handwritten forms and fine-tune TrOCR.",
      data: "Scanned forms + corrected transcripts",
      quality: "Character errors, word errors and field accuracy",
      recipe: "OS-OCR-001 · Fine-tune TrOCR + measure errors",
      href: "/login/?next=/detectors%3Frecipe%3Docr",
      stageCaptions: {
        raw: "Scan handwritten logbooks and extract line image crops.",
        label: "Clerks fix misread characters so TrOCR learns site handwriting.",
        eval: "Track Character Error Rate (2.1%) & field accuracy on unseen pages.",
        deploy: "Extract clean form fields while logging clerk corrections."
      },
      stageMetrics: {
        raw: [["120", "Form pages"], ["680", "Line crops"], ["Pretrained", "Initial TrOCR"]],
        label: [["680", "Verified lines"], ["42", "Clerk fixes"], ["0", "Unreviewed lines"]],
        eval: [["2.1%", "Char error (CER)"], ["4.8%", "Word error (WER)"], ["97.4%", "Field accuracy"]],
        deploy: [["TrOCR", "Fine-tuned"], ["+14%", "Vs base reader"], ["Audit", "Transcript log"]]
      },
      samples: [
        {
          label: "Valve ID",
          title: "Line 01 · “Valve 12B — Inlet”",
          detail: "Clerk fixed “Valvc 128” -> “Valve 12B” for TrOCR fine-tuning.",
          rect: [0.04, 0.2, 0.54, 0.2],
          sev: "info"
        },
        {
          label: "Defect note",
          title: "Line 02 · “Seal worn, weeping oil”",
          detail: "Cursive inspection remark aligned and verified.",
          rect: [0.04, 0.44, 0.54, 0.2],
          sev: "medium"
        },
        {
          label: "Pressure value",
          title: "Line 03 · “2.4 bar · Sign: A. Rahman”",
          detail: "Numeric pressure value & signature validated.",
          rect: [0.04, 0.68, 0.54, 0.2],
          sev: "ok"
        }
      ]
    },
    documents: {
      badge: "P1 · Wave 2 Language & Documents",
      title: "Document Classification & Extraction",
      status: "Proposed Wave 2 recipe",
      filename: "shift-incident-2026-11.txt · Work order",
      split: "80% Train · 10% Val · 10% Held-out",
      scenario: "Tag document categories, entities and structured fields.",
      data: "Text documents + spans, tags or fields",
      quality: "Class F1, confusion analysis and field accuracy",
      recipe: "OS-NLP-001 / OS-DOC-001 · Classification, NER & fields",
      href: "/login/?next=/detectors%3Frecipe%3Ddocuments",
      stageCaptions: {
        raw: "Import work orders and deduplicate near-copies before splitting.",
        label: "Highlight entity spans (Equipment, Fault, Metric, Action).",
        eval: "Inspect Class F1, span match and confusion pairs on held-out docs.",
        deploy: "Extract structured JSON records with human override gates."
      },
      stageMetrics: {
        raw: [["1,450", "Shift records"], ["4", "Doc categories"], ["Deduped", "Split safe"]],
        label: [["3,120", "Entity spans"], ["4", "Field schemas"], ["100%", "Human verified"]],
        eval: [["0.92", "Class F1"], ["95.1%", "Span match"], ["3", "Top confusions"]],
        deploy: [["OS-DOC", "Field extractor"], ["Schema", "JSON validated"], ["Human", "Override gate"]]
      },
      samples: [
        {
          label: "EQUIPMENT",
          title: "Span · EQUIPMENT: “Pump P-104B”",
          detail: "Equipment tag resolved into structured asset field.",
          rect: [0.04, 0.21, 0.54, 0.19],
          sev: "info"
        },
        {
          label: "FAULT & METRIC",
          title: "Span · FAULT: “seal weep · 78 °C”",
          detail: "Symptom paired with temperature for work-order routing.",
          rect: [0.04, 0.43, 0.54, 0.19],
          sev: "high"
        },
        {
          label: "ACTION",
          title: "Span · ACTION: “Isolate & replace gasket”",
          detail: "Recommended repair action extracted automatically.",
          rect: [0.04, 0.65, 0.54, 0.19],
          sev: "ok"
        }
      ]
    },
    movements: {
      badge: "P2 · Wave 3 Motion & Operations",
      title: "Human Posture & Gesture AI",
      status: "Proposed Wave 3 recipe",
      filename: "ergonomic-lift-bay2.mp4 · 17 keypoints",
      split: "Session-grouped Train / Val / Held-out",
      scenario: "Extract body/hand keypoints and classify movements.",
      data: "Clips + keypoints and movement labels",
      quality: "Keypoint accuracy (OKS/PCK) and visibility failures",
      recipe: "Wave 3 · Pose & gesture models on landmark estimators",
      href: "/login/?next=/detectors%3Frecipe%3Dmovements",
      stageCaptions: {
        raw: "Group clips by worker session so frames never leak across splits.",
        label: "Review 17 body or 21 hand landmarks and posture episodes.",
        eval: "Check OKS/PCK landmark precision and occlusion visibility.",
        deploy: "Run ergonomic posture checks without biometric identity tracking."
      },
      stageMetrics: {
        raw: [["36 clips", "Grouped by bay"], ["17 pts", "Body skeleton"], ["Privacy", "No face ID"]],
        label: [["890", "Pose frames"], ["4", "Posture classes"], ["12", "Occluded fixes"]],
        eval: [["0.89", "OKS / PCK"], ["93%", "Lift recall"], ["4.1%", "Occlusion miss"]],
        deploy: [["Edge", "Pose + action"], ["Session", "No biometrics"], ["Review", "Safety sign-off"]]
      },
      samples: [
        {
          label: "Safe squat lift",
          title: "Safe posture · Back neutral (16 deg)",
          detail: "Upright spine vector with bent knees · approved lift.",
          rect: [0.08, 0.16, 0.19, 0.68],
          sev: "ok"
        },
        {
          label: "Spine bend alert",
          title: "Strain risk · Forward flexion (48 deg)",
          detail: "Torso bend exceeds 35 deg ergonomic threshold.",
          rect: [0.27, 0.18, 0.18, 0.66],
          sev: "high"
        },
        {
          label: "Stop hand signal",
          title: "Hand landmark gesture · Open palm hold",
          detail: "21-point hand skeleton recognized clearly with glove.",
          rect: [0.46, 0.16, 0.14, 0.5],
          sev: "info"
        }
      ]
    },
    spreadsheets: {
      badge: "P0 · Wave 1 Multi-task MVP",
      title: "Tabular Prediction from Spreadsheets",
      status: "Proposed roadmap recipe (Forge PR #79)",
      filename: "compressor-telemetry-q3.csv · 12 columns",
      split: "80% Train · 10% Val · 10% Held-out",
      scenario: "Upload Excel/CSV, pick target column and test unseen rows.",
      data: "Excel (.xlsx) or CSV rows + target column",
      quality: "Held-out error rate, class F1 and feature checks",
      recipe: "OS-TAB-001 · Tabular trainer + unseen-row test",
      href: "/login/?next=/detectors%3Frecipe%3Dspreadsheets",
      stageCaptions: {
        raw: "Upload Excel or CSV; Athena profiles types and checks leakage.",
        label: "Choose target column (Failure_48h) and feature inputs.",
        eval: "Inspect unseen-row F1, risk calibration and top driver signals.",
        deploy: "Score new telemetry batches with explainable feature weights."
      },
      stageMetrics: {
        raw: [["4,820", "Telemetry rows"], ["12 cols", "Validated types"], ["0", "Leakage cols"]],
        label: [["Target", "Failure_48h"], ["9", "Input features"], ["80/10/10", "Time-safe split"]],
        eval: [["0.91", "Class F1"], ["96.2%", "Held-out acc"], ["0.03", "False alert rate"]],
        deploy: [["Batch", "CSV/XLSX score"], ["Top 3", "Driver signals"], ["v1.1", "Reproducible"]]
      },
      samples: [
        {
          label: "Row #1042 · Risk",
          title: "Row #1042 · Temp 86 °C · Vib 8.4 mm/s",
          detail: "Failure_48h = High (0.91) driven by vibration + temp.",
          rect: [0.03, 0.27, 0.56, 0.15],
          sev: "high"
        },
        {
          label: "Row #1043 · Watch",
          title: "Row #1043 · Drop 1.9 bar · Watch",
          detail: "Failure_48h = Watch (0.54) queued for operator review.",
          rect: [0.03, 0.44, 0.56, 0.15],
          sev: "medium"
        },
        {
          label: "Row #1044 · Normal",
          title: "Row #1044 · Temp 52 °C · Vib 1.8 mm/s",
          detail: "Failure_48h = Normal (0.97) on held-out check.",
          rect: [0.03, 0.61, 0.56, 0.15],
          sev: "ok"
        }
      ]
    }
  };

  const FUSION_SCENARIOS = {
    motor: {
      badge: "Cross-modal sync · Missing-input fallback · Modality ablation",
      verdict: "Joint fault verdict: Bearing wear (inspect within 48 hrs)",
      streams: [
        { kind: "Audio", title: "48 kHz Mic", val: "3.4 kHz spike", sev: "high", bars: [0.2, 0.4, 0.92, 0.78, 0.35] },
        { kind: "Vibration", title: "Accelerometer", val: "8.2 mm/s RMS", sev: "high", bars: [0.3, 0.55, 0.88, 0.9, 0.6] },
        { kind: "Telemetry", title: "Bearing Temp", val: "74 °C @ 1450 RPM", sev: "medium", bars: [0.4, 0.5, 0.62, 0.7, 0.75] }
      ],
      ablation: [["Audio only", "0.86"], ["Vibration only", "0.89"], ["Fused (3 streams)", "0.97"]]
    },
    safety: {
      badge: "Temporal alignment · Privacy-safe landmarks · Human override",
      verdict: "Joint safety alert: Dock unlocked while worker in active zone",
      streams: [
        { kind: "Video", title: "Bay Camera", val: "Forklift entering", sev: "medium", bars: [0.3, 0.6, 0.72, 0.65, 0.5] },
        { kind: "Human Pose", title: "17 Keypoints", val: "Worker bending", sev: "high", bars: [0.5, 0.75, 0.85, 0.82, 0.78] },
        { kind: "PLC State", title: "Dock Lock", val: "Gate UNLOCKED", sev: "high", bars: [0.2, 0.2, 0.95, 0.95, 0.95] }
      ],
      ablation: [["Video only", "0.78"], ["Pose + Video", "0.88"], ["Fused + PLC", "0.96"]]
    },
    thermal: {
      badge: "Spatial alignment · Asset provenance · Release bundle verified",
      verdict: "Joint inspection finding: Critical flange hotspot + corrosion",
      streams: [
        { kind: "Thermal IR", title: "LWIR Heat Map", val: "+28 °C hotspot", sev: "high", bars: [0.35, 0.6, 0.94, 0.88, 0.65] },
        { kind: "RGB Vision", title: "Clamp Photo", val: "Rust scaling", sev: "medium", bars: [0.4, 0.55, 0.74, 0.68, 0.6] },
        { kind: "Asset Log", title: "Line 12 Spec", val: "14.2 bar · 410d", sev: "info", bars: [0.5, 0.5, 0.55, 0.58, 0.6] }
      ],
      ablation: [["RGB only", "0.81"], ["Thermal only", "0.85"], ["Fused (IR+RGB+Log)", "0.95"]]
    }
  };

  const outcomeTablist = document.getElementById("lp-outcome-tabs");
  const outcomePanel = document.getElementById("lp-outcome-panel");
  const outcomeShell = document.getElementById("lp-outcome-shell");
  const visCanvas = document.getElementById("lp-vis-canvas");
  const visCtx = visCanvas ? visCanvas.getContext("2d") : null;
  const fusionCanvas = document.getElementById("lp-fusion-canvas");
  const fusionCtx = fusionCanvas ? fusionCanvas.getContext("2d") : null;

  const PREVIEW_IMAGES = {};
  if (visCanvas) {
    [["welding", "welding.jpg"], ["corrosion", "pipe-corrosion.jpg"], ["worker", "safety-worker.jpg"]].forEach(([key, file]) => {
      const pic = new Image();
      pic.src = STATIC + file;
      pic.addEventListener("load", () => { PREVIEW_IMAGES[key] = pic; drawStudioCanvas(); renderMiniCanvases(); });
      PREVIEW_IMAGES[key] = pic;
    });
  }

  let activeOutcome = "audio";
  let activeStage = "label";
  let activeSample = 1;
  let activeFusion = "motor";
  let animPhase = 0;
  let animRaf = null;
  let studioActive = true;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function setupHiDpiCanvas(canvas, ctx, logicalW, logicalH) {
    const dpr = Math.min(Math.max(Number(window.devicePixelRatio) || 2, 2), 3);
    const targetW = Math.round(logicalW * dpr);
    const targetH = Math.round(logicalH * dpr);
    if (canvas.width !== targetW || canvas.height !== targetH) {
      canvas.width = targetW;
      canvas.height = targetH;
    }
    if (typeof ctx.setTransform === "function") ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
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

  function drawCoverImage(ctx, imgObj, x, y, w, h, alpha = 0.5) {
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
    // Left workspace header
    ctx.fillStyle = "#8fa0b5";
    ctx.font = "600 10.5px 'Martian Mono', monospace";
    ctx.fillText(leftTitle, 24, 24);
    // Divider + arrow flow connector
    ctx.strokeStyle = "rgba(148, 180, 220, 0.18)";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(splitX, 16); ctx.lineTo(splitX, h - 16); ctx.stroke();

    // Right solution card container
    const rx = splitX + 18;
    const rw = w - rx - 18;
    ctx.fillStyle = "#0d1420";
    ctx.beginPath(); ctx.roundRect(rx, 16, rw, h - 32, 12); ctx.fill();
    ctx.strokeStyle = "rgba(56, 189, 248, 0.35)";
    ctx.lineWidth = 1.2;
    ctx.stroke();
    return { splitX, rx, rw };
  }

  function drawSolutionPanel(ctx, rx, rw, h, spec) {
    const pad = 16;
    ctx.fillStyle = "#8fa0b5";
    ctx.font = "600 10px 'Martian Mono', monospace";
    ctx.fillText("LIVE SOLUTION PREVIEW", rx + pad, 38);

    drawBadge(ctx, spec.verdict, rx + pad, 48, spec.verdictColor, spec.verdictFg || "#04121c");

    ctx.fillStyle = "#ffffff";
    ctx.font = "700 16px 'Hanken Grotesk', sans-serif";
    ctx.fillText(spec.headline, rx + pad, 92);

    // Mini bars / field rows
    let y = 114;
    spec.rows.forEach(row => {
      ctx.fillStyle = "rgba(255, 255, 255, 0.04)";
      ctx.beginPath(); ctx.roundRect(rx + pad, y, rw - pad * 2, 42, 8); ctx.fill();
      ctx.fillStyle = "#8fa0b5";
      ctx.font = "600 10.5px 'Martian Mono', monospace";
      ctx.fillText(row.label, rx + pad + 10, y + 16);
      ctx.fillStyle = row.color || "#eef3f8";
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

    // Bottom action controls graphic
    const btnY = h - 62;
    drawBadge(ctx, spec.btnPrimary, rx + pad, btnY, "#38bdf8", "#04121c");
    drawBadge(ctx, spec.btnSecondary, rx + pad + 132, btnY, "rgba(255,255,255,0.08)", "#c9d3df");
  }

  function drawAudioCanvas(ctx, w, h, data) {
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
    for (let c = 0; c < cols; c++) {
      const t = c / cols;
      const inFault = t >= 0.34 && t <= 0.62;
      const inSpike = t >= 0.68 && t <= 0.92;
      for (let r = 0; r < rows; r++) {
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

    if (activeStage !== "raw") {
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
        drawBadge(ctx, ["1 Healthy", "2 3.4 kHz fault", "3 Cavitation"][idx] || `${idx + 1} ${sample.label}`, x, specTop - 22, col, sample.sev === "high" ? "#fff" : "#04121c");
      });
    }

    const midY = waveTop + waveH / 2;
    for (let i = 0; i < 68; i++) {
      const t = i / 68;
      let amp = 0.18 + 0.12 * Math.sin(i * 0.6 + animPhase * 2);
      if (t >= 0.34 && t <= 0.62) amp = 0.45 + 0.42 * Math.abs(Math.sin(i * 1.15 + animPhase));
      if (t >= 0.68 && t <= 0.92) amp = 0.35 + 0.35 * Math.abs(Math.cos(i * 1.7));
      const barH = amp * waveH;
      const inSelected = activeStage !== "raw" && (() => {
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
      verdict: activeStage === "raw" ? "UNLABELED AUDIO" : sample.sev === "high" ? "FAULT DETECTED" : sample.sev === "medium" ? "ANOMALY SPIKE" : "BASELINE NORMAL",
      verdictColor: activeStage === "raw" ? "#7d8ba0" : sevColor(sample.sev),
      verdictFg: sample.sev === "high" ? "#fff" : "#04121c",
      headline: activeStage === "raw" ? "Ready for timeline labeling" : sample.label,
      rows: [
        { label: "PEAK BAND", value: activeSample === 1 ? "3.4 kHz (+18 dB)" : activeSample === 2 ? "8.1 kHz transient" : "60 Hz nominal", color: sevColor(sample.sev), bar: activeSample === 1 ? 0.92 : activeSample === 2 ? 0.76 : 0.25 },
        { label: "EVENT F1 · MISSED", value: "0.94 · 1 / 42 faults", color: "#34d399", bar: 0.94 },
        { label: "FALSE ALARMS / HR", value: "0.4 per hour (ONNX)", color: "#7dd3fc", bar: 0.16 }
      ],
      btnPrimary: "Confirm Fault",
      btnSecondary: "Flag False Alarm"
    });
  }

  function drawVisionCanvas(ctx, w, h, data) {
    const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · TRAIN PICTURES · MANUAL + AUTO-SUGGEST BOXES");
    drawCoverImage(ctx, PREVIEW_IMAGES.welding, 24, 38, splitX - 42, h - 56, activeStage === "raw" ? 0.9 : 0.75);

    if (activeStage !== "raw") {
      data.samples.forEach((sample, idx) => {
        const [rxBox, ry, rwBox, rh] = sample.rect;
        const x = rxBox * w;
        const y = ry * h;
        const bw = rwBox * w;
        const bh = rh * h;
        const col = sevColor(sample.sev);
        const selected = idx === activeSample;

        ctx.save();
        if (idx === 1 && activeStage === "label") ctx.setLineDash([6, 4]);
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
      drawBadge(ctx, "RAW PHOTO · SHA-256 VERIFIED", 40, 56, "#0e141e", "#eef3f8");
    }

    const sample = data.samples[activeSample];
    drawSolutionPanel(ctx, rx, rw, h, {
      verdict: activeStage === "raw" ? "0 BOXES YET" : "NEEDS ATTENTION",
      verdictColor: activeStage === "raw" ? "#7d8ba0" : "#ffb020",
      headline: "Operator Check Screen v1.4",
      rows: [
        { label: "SELECTED FINDING", value: sample.label, color: sevColor(sample.sev), bar: 0.91 },
        { label: "HELD-OUT CHECK", value: "mAP50 0.91 · Recall 94%", color: "#34d399", bar: 0.91 },
        { label: "HUMAN REVIEW GATE", value: "418 accepted · 14 pending", color: "#7dd3fc", bar: 0.85 }
      ],
      btnPrimary: "Approve Model",
      btnSecondary: "Fix Missed Box"
    });
  }

  function drawOcrCanvas(ctx, w, h, data) {
    const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · HANDWRITING SCAN · LINE CROPS + TrOCR CORRECTIONS");
    ctx.fillStyle = "#0e1522";
    ctx.beginPath(); ctx.roundRect(24, 38, splitX - 42, h - 56, 12); ctx.fill();
    ctx.strokeStyle = "rgba(148, 180, 220, 0.22)";
    ctx.stroke();

    const lines = [
      { pred: activeStage === "raw" ? "Valvc 128 — lnlet" : "Valve 12B — Inlet", y: 95 },
      { pred: activeStage === "raw" ? "Seal worm, weaping" : "Seal worn, weeping oil", y: 185 },
      { pred: activeStage === "raw" ? "2.4 bar · A. Rahmn" : "2.4 bar · Sign: A. Rahman", y: 275 }
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

      ctx.strokeStyle = "rgba(148, 180, 220, 0.16)";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(42, line.y + 22); ctx.lineTo(splitX - 38, line.y + 22); ctx.stroke();

      ctx.strokeStyle = "#e2edf8";
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      for (let s = 0; s <= 26; s++) {
        const px = 46 + s * 8.5;
        const py = line.y + 8 + Math.sin(s * 0.9 + idx * 2) * 6 + Math.cos(s * 2.1) * 3.5;
        if (s === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.stroke();

      if (activeStage !== "raw") {
        ctx.fillStyle = selected ? `${col}20` : `${col}0c`;
        ctx.fillRect(x, y, bw, bh);
        ctx.strokeStyle = col;
        ctx.lineWidth = selected ? 2.4 : 1;
        ctx.strokeRect(x, y, bw, bh);
      }

      ctx.font = "600 12.5px 'Martian Mono', monospace";
      ctx.fillStyle = activeStage === "raw" ? "#fbbf24" : "#34d399";
      ctx.fillText(line.pred, 300, line.y + 12);
    });

    drawSolutionPanel(ctx, rx, rw, h, {
      verdict: activeStage === "raw" ? "BASE OCR ONLY" : "TrOCR FINE-TUNED",
      verdictColor: activeStage === "raw" ? "#ffb020" : "#34d399",
      headline: "Form Reader & Field Output",
      rows: [
        { label: "UNIT FIELD", value: "Valve 12B — Inlet", color: "#7dd3fc", bar: 0.98 },
        { label: "PRESSURE READING", value: "2.4 bar (verified)", color: "#34d399", bar: 0.97 },
        { label: "ERROR REDUCTION", value: "CER 2.1% (-14% vs base)", color: "#34d399", bar: 0.92 }
      ],
      btnPrimary: "Save Correction",
      btnSecondary: "Export Fields"
    });
  }

  function drawDocumentsCanvas(ctx, w, h, data) {
    const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · TEXT WORK ORDER · NAMED-ENTITY SPANS");
    ctx.fillStyle = "#0e1522";
    ctx.beginPath(); ctx.roundRect(24, 38, splitX - 42, h - 56, 12); ctx.fill();
    ctx.strokeStyle = "rgba(148, 180, 220, 0.22)";
    ctx.stroke();

    const rows = [
      { prefix: "Unit: ", span: "Pump P-104B", tag: "EQUIPMENT", y: 105 },
      { prefix: "Issue: ", span: "seal weep at 78 °C", tag: "FAULT+METRIC", y: 192 },
      { prefix: "Next: ", span: "Isolate & replace gasket", tag: "ACTION", y: 279 }
    ];

    rows.forEach((row, idx) => {
      const sample = data.samples[idx];
      const [rxBox, ry, rwBox, rh] = sample.rect;
      const selected = idx === activeSample;
      const col = sevColor(sample.sev);
      if (activeStage !== "raw") {
        ctx.fillStyle = selected ? `${col}26` : `${col}10`;
        ctx.beginPath();
        ctx.roundRect(rxBox * w, ry * h, rwBox * w, rh * h, 8);
        ctx.fill();
        ctx.strokeStyle = col;
        ctx.lineWidth = selected ? 2.4 : 1;
        ctx.stroke();
      }
      ctx.font = "500 15px 'Hanken Grotesk', sans-serif";
      ctx.fillStyle = "#8fa0b5";
      ctx.fillText(row.prefix, 48, row.y);
      ctx.fillStyle = "#ffffff";
      ctx.fillText(row.span, 102, row.y);
      if (activeStage !== "raw") {
        drawBadge(ctx, row.tag, splitX - 155, row.y - 16, col, sample.sev === "high" ? "#fff" : "#04121c");
      }
    });

    drawSolutionPanel(ctx, rx, rw, h, {
      verdict: activeStage === "raw" ? "RAW TEXT" : "STRUCTURED RECORD",
      verdictColor: activeStage === "raw" ? "#7d8ba0" : "#38bdf8",
      headline: "Class: Rotating Equipment",
      rows: [
        { label: "EQUIPMENT · FAULT", value: "Pump P-104B · seal weep", color: "#ff6b3d", bar: 0.95 },
        { label: "ACTION EXTRACTED", value: "Isolate & replace gasket", color: "#34d399", bar: 0.92 },
        { label: "HELD-OUT CLASS F1", value: "0.92 (deduped split)", color: "#7dd3fc", bar: 0.92 }
      ],
      btnPrimary: "Confirm Fields",
      btnSecondary: "Edit Span"
    });
  }

  function drawMovementsCanvas(ctx, w, h, data) {
    const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · POSE & HAND LANDMARKS · 17 BODY + 21 HAND PTS");
    drawCoverImage(ctx, PREVIEW_IMAGES.worker, 24, 38, splitX - 42, h - 56, 0.62);

    const poses = [
      { cx: 155, angle: 0.25, col: sevColor("ok"), label: "Safe squat (16 deg)" },
      { cx: 335, angle: 0.78, col: sevColor("high"), label: "Bend alert (48 deg)" }
    ];

    poses.forEach((p, idx) => {
      const selected = idx === activeSample || (activeSample === 2 && idx === 1);
      const hipY = 225;
      const hipX = p.cx;
      const torsoLen = 82;
      const shX = hipX + Math.sin(p.angle) * torsoLen;
      const shY = hipY - Math.cos(p.angle) * torsoLen;
      const headX = shX + Math.sin(p.angle * 0.6) * 22;
      const headY = shY - 26;
      const kneeX = hipX + (idx === 0 ? 26 : 10);
      const kneeY = hipY + 46;
      const footX = hipX + 2;
      const footY = hipY + 95;
      const handX = shX + (activeSample === 2 && idx === 1 ? 48 : 30);
      const handY = shY + (activeSample === 2 && idx === 1 ? -22 : 34);

      if (activeStage === "raw") {
        ctx.strokeStyle = "rgba(148, 180, 220, 0.5)";
        ctx.lineWidth = 2;
        ctx.strokeRect(hipX - 60, 75, 130, 250);
        return;
      }

      const boneColor = selected ? p.col : "rgba(125, 211, 252, 0.68)";
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

    if (activeStage !== "raw") {
      const hx = 485;
      const hy = 120;
      ctx.fillStyle = "rgba(10, 15, 23, 0.9)";
      ctx.beginPath(); ctx.roundRect(hx - 40, hy - 42, 118, 128, 10); ctx.fill();
      ctx.strokeStyle = activeSample === 2 ? "#38bdf8" : "rgba(148,180,220,0.3)";
      ctx.lineWidth = activeSample === 2 ? 2.4 : 1;
      ctx.stroke();
      ctx.fillStyle = "#8fa0b5";
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
      verdict: activeStage === "raw" ? "SESSION GROUPED" : sample.sev === "high" ? "STRAIN ALERT" : "SAFE POSTURE",
      verdictColor: activeStage === "raw" ? "#7d8ba0" : sevColor(sample.sev),
      verdictFg: sample.sev === "high" ? "#fff" : "#04121c",
      headline: "Ergonomic & Gesture Monitor",
      rows: [
        { label: "DETECTED EPISODE", value: sample.label, color: sevColor(sample.sev), bar: 0.93 },
        { label: "KEYPOINT OKS / PCK", value: "0.89 · 93% lift recall", color: "#34d399", bar: 0.89 },
        { label: "PRIVACY CONTROL", value: "Landmarks only (no face ID)", color: "#7dd3fc", bar: 1.0 }
      ],
      btnPrimary: "Approve Action",
      btnSecondary: "Adjust Joint"
    });
  }

  function drawSpreadsheetsCanvas(ctx, w, h, data) {
    const { splitX, rx, rw } = drawZoneChrome(ctx, w, h, "1 · CSV / EXCEL TABLE · TARGET & FEATURE DRIVERS");
    ctx.fillStyle = "#0e1522";
    ctx.beginPath(); ctx.roundRect(24, 38, splitX - 42, h - 56, 12); ctx.fill();
    ctx.strokeStyle = "rgba(148, 180, 220, 0.22)";
    ctx.stroke();

    const cols = ["Row", "Temp °C", "Vib mm/s", "Bar", "Target: Failure_48h"];
    const xs = [40, 118, 206, 296, 362];
    ctx.fillStyle = "#7dd3fc";
    ctx.font = "700 11px 'Martian Mono', monospace";
    cols.forEach((col, idx) => ctx.fillText(col, xs[idx], 74));
    ctx.strokeStyle = "rgba(148, 180, 220, 0.2)";
    ctx.beginPath(); ctx.moveTo(34, 88); ctx.lineTo(splitX - 28, 88); ctx.stroke();

    const rows = [
      ["#1042", "86.4", "8.4", "14.1", activeStage === "raw" ? "?" : "High (0.91)"],
      ["#1043", "63.1", "4.9", "12.2", activeStage === "raw" ? "?" : "Watch (0.54)"],
      ["#1044", "52.0", "1.8", "14.0", activeStage === "raw" ? "?" : "Normal (0.97)"]
    ];

    rows.forEach((r, idx) => {
      const sample = data.samples[idx];
      const [rxBox, ry, rwBox, rh] = sample.rect;
      const selected = idx === activeSample;
      const col = sevColor(sample.sev);
      if (activeStage !== "raw") {
        ctx.fillStyle = selected ? `${col}24` : `${col}0d`;
        ctx.fillRect(rxBox * w, ry * h, rwBox * w, rh * h);
        if (selected) {
          ctx.strokeStyle = col; ctx.lineWidth = 2;
          ctx.strokeRect(rxBox * w, ry * h, rwBox * w, rh * h);
        }
      }
      ctx.font = "500 13px 'Martian Mono', monospace";
      r.forEach((cell, cIdx) => {
        ctx.fillStyle = cIdx === 4 && activeStage !== "raw" ? col : "#eef3f8";
        ctx.fillText(cell, xs[cIdx], ry * h + 34);
      });
    });

    // Sparkline at bottom of table
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
      verdict: activeStage === "raw" ? "RAW COLUMNS" : sample.sev === "high" ? "HIGH RISK (0.91)" : sample.sev === "medium" ? "WATCH (0.54)" : "NORMAL (0.97)",
      verdictColor: activeStage === "raw" ? "#7d8ba0" : sevColor(sample.sev),
      verdictFg: sample.sev === "high" ? "#fff" : "#04121c",
      headline: "Tabular Forecast Scorecard",
      rows: [
        { label: "TOP DRIVER: RMS_VIB", value: "44% impact on Failure_48h", color: "#38bdf8", bar: 0.84 },
        { label: "DRIVER 2: HOUSING_TEMP", value: "33% impact (86.4 °C)", color: "#ffb020", bar: 0.68 },
        { label: "HELD-OUT UNSEEN ROWS", value: "F1 0.91 · 96.2% acc", color: "#34d399", bar: 0.91 }
      ],
      btnPrimary: "Score Batch CSV",
      btnSecondary: "Check Leakage"
    });
  }

  function drawStudioCanvas() {
    if (!visCanvas || !visCtx) return;
    const data = OUTCOMES[activeOutcome];
    if (!data) return;
    const { w, h } = setupHiDpiCanvas(visCanvas, visCtx, 960, 380);
    drawStudioGrid(visCtx, w, h);
    if (activeOutcome === "audio") drawAudioCanvas(visCtx, w, h, data);
    else if (activeOutcome === "vision") drawVisionCanvas(visCtx, w, h, data);
    else if (activeOutcome === "ocr") drawOcrCanvas(visCtx, w, h, data);
    else if (activeOutcome === "documents") drawDocumentsCanvas(visCtx, w, h, data);
    else if (activeOutcome === "movements") drawMovementsCanvas(visCtx, w, h, data);
    else if (activeOutcome === "spreadsheets") drawSpreadsheetsCanvas(visCtx, w, h, data);
  }

  function renderMiniCanvases() {
    document.querySelectorAll(".lp-mini-canvas[data-mini-modality]").forEach(canvas => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const mode = canvas.dataset.miniModality;
      const { w, h } = setupHiDpiCanvas(canvas, ctx, 320, 145);
      ctx.fillStyle = "#080d16";
      ctx.fillRect(0, 0, w, h);

      if (mode === "audio") {
        for (let i = 0; i < 36; i++) {
          const t = i / 36;
          const fault = t >= 0.34 && t <= 0.62;
          const bh = (fault ? 0.45 + 0.4 * Math.abs(Math.sin(i * 1.4)) : 0.16 + 0.15 * Math.cos(i)) * 96;
          ctx.fillStyle = fault ? "#ff6b3d" : "#38bdf8";
          ctx.fillRect(16 + i * 8, h / 2 - bh / 2, 5, bh);
        }
        ctx.strokeStyle = "#ff6b3d";
        ctx.lineWidth = 1.6;
        ctx.strokeRect(110, 18, 92, 108);
        drawBadge(ctx, "FAULT 3.4 kHz", 112, 16, "#ff6b3d", "#fff");
      } else if (mode === "ocr") {
        [38, 76, 114].forEach((y, idx) => {
          ctx.strokeStyle = "rgba(148,180,220,0.2)";
          ctx.beginPath(); ctx.moveTo(16, y + 12); ctx.lineTo(w - 16, y + 12); ctx.stroke();
          ctx.strokeStyle = "#e2edf8"; ctx.lineWidth = 2;
          ctx.beginPath();
          for (let s = 0; s < 14; s++) {
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
        drawCoverImage(ctx, PREVIEW_IMAGES.worker, 0, 0, w, h, 0.62);
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
          ctx.fillStyle = "#eef3f8"; ctx.font = "600 11.5px 'Martian Mono', monospace";
          ctx.fillText(`${id}  ${tmp}`, 26, y + 18);
          ctx.fillStyle = col; ctx.fillText(res, 172, y + 18);
        });
      } else if (mode === "vision") {
        drawCoverImage(ctx, PREVIEW_IMAGES.welding, 0, 0, w, h, 0.9);
        ctx.strokeStyle = "#ff6b3d"; ctx.lineWidth = 2.4; ctx.strokeRect(48, 32, 108, 72);
        drawBadge(ctx, "Porosity", 48, 12, "#ff6b3d", "#fff");
        ctx.strokeStyle = "#ffb020"; ctx.lineWidth = 2; ctx.strokeRect(184, 62, 86, 56);
        drawBadge(ctx, "Undercut", 184, 42, "#ffb020");
      }
    });
  }

  function drawFusionCanvas() {
    if (!fusionCanvas || !fusionCtx) return;
    const scenario = FUSION_SCENARIOS[activeFusion];
    if (!scenario) return;
    const { w, h } = setupHiDpiCanvas(fusionCanvas, fusionCtx, 880, 210);
    drawStudioGrid(fusionCtx, w, h);

    // 3 input stream cards on left -> convergence lines -> joint decision card on right
    const leftX = 20;
    const cardW = 286;
    const mergeX = 446;
    const outX = 532;
    const outW = w - outX - 20;

    scenario.streams.forEach((s, idx) => {
      const y = 16 + idx * 62;
      const col = sevColor(s.sev);
      fusionCtx.fillStyle = "#0e1522";
      fusionCtx.beginPath(); fusionCtx.roundRect(leftX, y, cardW, 50, 8); fusionCtx.fill();
      fusionCtx.strokeStyle = col; fusionCtx.lineWidth = 1.4; fusionCtx.stroke();

      fusionCtx.fillStyle = col;
      fusionCtx.font = "700 10px 'Martian Mono', monospace";
      fusionCtx.fillText(s.kind.toUpperCase(), leftX + 12, y + 17);
      fusionCtx.fillStyle = "#ffffff";
      fusionCtx.font = "700 12.5px 'Hanken Grotesk', sans-serif";
      fusionCtx.fillText(`${s.title} · ${s.val}`, leftX + 12, y + 36);

      // Signal spark bars inside card
      s.bars.forEach((b, bIdx) => {
        const bx = leftX + cardW - 50 + bIdx * 8;
        const bh = b * 24;
        fusionCtx.fillStyle = col;
        fusionCtx.fillRect(bx, y + 38 - bh, 5, bh);
      });

      // Connector curve to fusion node
      fusionCtx.strokeStyle = col;
      fusionCtx.lineWidth = 2;
      fusionCtx.beginPath();
      fusionCtx.moveTo(leftX + cardW, y + 25);
      fusionCtx.bezierCurveTo(leftX + cardW + 60, y + 25, mergeX - 50, h / 2, mergeX, h / 2);
      fusionCtx.stroke();
    });

    // Center Fusion Sync Node
    fusionCtx.fillStyle = "#0e1522";
    fusionCtx.beginPath(); fusionCtx.arc(mergeX, h / 2, 34, 0, Math.PI * 2); fusionCtx.fill();
    fusionCtx.strokeStyle = "#38bdf8"; fusionCtx.lineWidth = 2.4; fusionCtx.stroke();
    fusionCtx.fillStyle = "#7dd3fc";
    fusionCtx.font = "700 10px 'Martian Mono', monospace";
    fusionCtx.fillText("SYNC", mergeX - 15, h / 2 - 2);
    fusionCtx.fillText("FUSE", mergeX - 15, h / 2 + 12);

    fusionCtx.strokeStyle = "#34d399";
    fusionCtx.lineWidth = 2.5;
    fusionCtx.beginPath();
    fusionCtx.moveTo(mergeX + 34, h / 2);
    fusionCtx.lineTo(outX, h / 2);
    fusionCtx.stroke();

    // Right Output + Ablation Panel
    fusionCtx.fillStyle = "#0d1822";
    fusionCtx.beginPath(); fusionCtx.roundRect(outX, 18, outW, h - 36, 12); fusionCtx.fill();
    fusionCtx.strokeStyle = "rgba(52, 211, 153, 0.55)"; fusionCtx.lineWidth = 1.6; fusionCtx.stroke();

    drawBadge(fusionCtx, "JOINT DECISION + ABLATION", outX + 16, 30, "#34d399", "#04121c");
    fusionCtx.fillStyle = "#ffffff";
    fusionCtx.font = "700 13.5px 'Hanken Grotesk', sans-serif";
    fusionCtx.fillText(scenario.verdict.replace(/^Joint [^:]+:\s*/, ""), outX + 16, 73);

    scenario.ablation.forEach(([lbl, score], idx) => {
      const ay = 94 + idx * 30;
      const pct = Number(score) || 0.8;
      fusionCtx.fillStyle = "#8fa0b5";
      fusionCtx.font = "600 10.5px 'Martian Mono', monospace";
      fusionCtx.fillText(lbl, outX + 16, ay + 12);
      const barX = outX + 168;
      const barW = outW - 222;
      fusionCtx.fillStyle = "rgba(255,255,255,0.08)";
      fusionCtx.fillRect(barX, ay + 4, barW, 8);
      fusionCtx.fillStyle = idx === 2 ? "#34d399" : "#38bdf8";
      fusionCtx.fillRect(barX, ay + 4, barW * pct, 8);
      fusionCtx.fillStyle = "#ffffff";
      fusionCtx.fillText(score, outX + outW - 44, ay + 12);
    });
  }

  function renderInspector() {
    const data = OUTCOMES[activeOutcome];
    if (!data) return;
    const sample = data.samples[activeSample] || data.samples[0];
    document.getElementById("lp-vis-filename").textContent = data.filename;
    document.getElementById("lp-vis-mode").textContent = STAGES[activeStage] || STAGES.label;
    const stageCapEl = document.getElementById("lp-stage-caption");
    if (stageCapEl) stageCapEl.textContent = data.stageCaptions[activeStage] || "";
    document.getElementById("lp-vis-selection-title").textContent = sample.title;
    document.getElementById("lp-vis-split").textContent = data.split;
    document.getElementById("lp-vis-selection-detail").textContent = sample.detail;

    const samplesBox = document.getElementById("lp-vis-samples");
    if (samplesBox) {
      samplesBox.replaceChildren(...data.samples.map((item, idx) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "lp-vis-sample";
        btn.dataset.sample = String(idx);
        btn.setAttribute("aria-pressed", idx === activeSample ? "true" : "false");
        btn.textContent = item.label;
        return btn;
      }));
    }

    const metricsBox = document.getElementById("lp-vis-metrics");
    if (metricsBox) {
      const cells = data.stageMetrics[activeStage] || data.stageMetrics.eval;
      metricsBox.replaceChildren(...cells.map(([val, lbl]) => {
        const card = document.createElement("div");
        const b = document.createElement("b");
        b.textContent = val;
        const span = document.createElement("span");
        span.textContent = lbl;
        card.append(b, span);
        return card;
      }));
    }
    drawStudioCanvas();
  }

  if (outcomeTablist && outcomePanel) {
    const tabs = [...outcomeTablist.querySelectorAll("[data-outcome]")];
    const stageButtons = [...document.querySelectorAll("#lp-stage-tabs [data-stage]")];
    const samplesWrap = document.getElementById("lp-vis-samples");
    const galleryCards = [...document.querySelectorAll(".lp-mod-card[data-jump-outcome]")];

    const selectOutcome = (key, moveFocus = false) => {
      const data = OUTCOMES[key];
      if (!data) return;
      activeOutcome = key;
      activeSample = key === "audio" ? 1 : 0;
      tabs.forEach(tab => {
        const active = tab.dataset.outcome === key;
        tab.setAttribute("aria-selected", active ? "true" : "false");
        tab.tabIndex = active ? 0 : -1;
        if (active) {
          outcomePanel.setAttribute("aria-labelledby", tab.id);
          if (typeof tab.scrollIntoView === "function") {
            tab.scrollIntoView({ block: "nearest", inline: "nearest" });
          }
          if (moveFocus) tab.focus();
        }
      });
      galleryCards.forEach(card => card.setAttribute("aria-pressed", String(card.dataset.jumpOutcome === key)));
      document.getElementById("lp-outcome-badge").textContent = data.badge;
      document.getElementById("lp-outcome-title").textContent = data.title;
      const statusEl = document.getElementById("lp-outcome-status");
      if (statusEl) statusEl.textContent = data.status;
      const scenEl = document.getElementById("lp-outcome-scenario");
      if (scenEl) scenEl.textContent = data.scenario;
      document.getElementById("lp-outcome-data").textContent = data.data;
      document.getElementById("lp-outcome-quality").textContent = data.quality;
      document.getElementById("lp-outcome-recipe").textContent = data.recipe;
      const cta = document.getElementById("lp-outcome-cta");
      if (cta) cta.setAttribute("href", data.href);
      renderInspector();
    };

    const selectStage = stage => {
      if (!STAGES[stage]) return;
      activeStage = stage;
      stageButtons.forEach(btn => btn.setAttribute("aria-pressed", String(btn.dataset.stage === stage)));
      renderInspector();
    };

    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => selectOutcome(tab.dataset.outcome, false));
      tab.addEventListener("keydown", event => {
        let next = -1;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        if (next >= 0) {
          event.preventDefault();
          selectOutcome(tabs[next].dataset.outcome, true);
        }
      });
    });

    stageButtons.forEach(btn => btn.addEventListener("click", () => selectStage(btn.dataset.stage)));

    if (samplesWrap) {
      samplesWrap.addEventListener("click", event => {
        const btn = event.target.closest("[data-sample]");
        if (!btn) return;
        activeSample = Number(btn.dataset.sample) || 0;
        renderInspector();
      });
    }

    if (visCanvas) {
      visCanvas.addEventListener("click", event => {
        const rect = visCanvas.getBoundingClientRect();
        const nx = (event.clientX - rect.left) / rect.width;
        const ny = (event.clientY - rect.top) / rect.height;
        const samples = OUTCOMES[activeOutcome]?.samples || [];
        const hit = samples.findIndex(({ rect: [rx, ry, rw, rh] }) => nx >= rx && nx <= rx + rw && ny >= ry && ny <= ry + rh);
        if (hit >= 0) {
          activeSample = hit;
          if (activeStage === "raw") selectStage("label");
          else renderInspector();
        }
      });
    }

    document.querySelectorAll("[data-jump-outcome]").forEach(card => {
      card.addEventListener("click", () => {
        const key = card.dataset.jumpOutcome;
        if (!OUTCOMES[key]) return;
        selectOutcome(key, false);
        outcomeShell?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" });
      });
    });

    const startCanvasLoop = () => {
      if (reducedMotion || animRaf !== null) return;
      let lastTick = 0;
      const tick = now => {
        if (!studioActive) { animRaf = null; return; }
        if (now - lastTick > 70) {
          lastTick = now;
          animPhase += 0.14;
          if (activeOutcome === "audio") drawStudioCanvas();
        }
        animRaf = requestAnimationFrame(tick);
      };
      animRaf = requestAnimationFrame(tick);
    };

    const stopCanvasLoop = () => {
      if (animRaf !== null) { cancelAnimationFrame(animRaf); animRaf = null; }
    };

    if (outcomeShell) {
      outcomeShell.addEventListener("contentvisibilityautostatechange", event => {
        studioActive = !event.skipped;
        if (studioActive) startCanvasLoop();
        else stopCanvasLoop();
      });
      if (!("contentVisibility" in document.documentElement.style) && "IntersectionObserver" in window) {
        const visObs = new IntersectionObserver(entries => {
          studioActive = entries.some(entry => entry.isIntersecting);
          if (studioActive) startCanvasLoop();
          else stopCanvasLoop();
        }, { rootMargin: "200px" });
        visObs.observe(outcomeShell);
      }
    }

    renderInspector();
    renderMiniCanvases();
    startCanvasLoop();
  }

  const fusionTabs = [...document.querySelectorAll("#lp-fusion-tabs [data-fusion]")];
  if (fusionTabs.length) {
    drawFusionCanvas();
    fusionTabs.forEach(tab => {
      tab.addEventListener("click", () => {
        const scenario = FUSION_SCENARIOS[tab.dataset.fusion];
        if (!scenario) return;
        activeFusion = tab.dataset.fusion;
        fusionTabs.forEach(btn => btn.setAttribute("aria-pressed", String(btn === tab)));
        document.getElementById("lp-fusion-badge").textContent = scenario.badge;
        document.getElementById("lp-fusion-verdict").textContent = scenario.verdict;
        drawFusionCanvas();
      });
    });
  }

  let lastDpr = window.devicePixelRatio || 1;
  window.addEventListener("resize", () => {
    const dprNow = window.devicePixelRatio || 1;
    if (dprNow !== lastDpr) {
      lastDpr = dprNow;
      drawStudioCanvas();
      renderMiniCanvases();
      drawFusionCanvas();
    }
  }, { passive: true });

  const items = document.querySelectorAll(".lp-reveal");
  if ("IntersectionObserver" in window) {
    const seen = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) { entry.target.classList.add("is-in"); seen.unobserve(entry.target); }
    }), { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    items.forEach(item => seen.observe(item));
  } else {
    items.forEach(item => item.classList.add("is-in"));
  }
})();
