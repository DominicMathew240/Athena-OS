export const STAGES = {
  raw: "STAGE 01 · RAW DATA",
  label: "STAGE 02 · TEACH WITH LABELS",
  eval: "STAGE 03 · HELD-OUT CHECK",
  deploy: "STAGE 04 · DEPLOY & IMPROVE",
};

export const OUTCOMES = {
  vision: {
    badge: "P0 · Locally verified slice + Wave 1 MVP",
    title: "Object & Defect Detection",
    status: "Merged end to end (synthetic-verified)",
    filename: "weld-seam-station04.jpg · 1920 × 1080",
    split: "80% Train · 10% Val · 10% Held-out",
    data: "JPEG, PNG or WebP pictures + boxes",
    quality: "Recall, precision, mAP and false positives",
    recipe: "OS-VIS-001 · Dataset, boxes, train & check screen",
    stageMetrics: {
      raw: [["240", "Verified photos"], ["0", "Unchecked boxes"], ["100%", "SHA-256 intact"]],
      label: [["418", "Accepted boxes"], ["14", "Needs review"], ["3", "Target classes"]],
      eval: [["0.91", "mAP50"], ["94%", "Defect recall"], ["2 / 48", "Missed items"]],
      deploy: [["v1.4", "Approved model"], ["100%", "Human reviewed"], ["1-click", "Rollback safe"]],
    },
    samples: [
      { label: "Weld porosity", title: "Weld porosity cluster (box 01)", detail: "Accepted box on seam B · included in snapshot v3.", rect: [0.09, 0.24, 0.2, 0.34], sev: "high" },
      { label: "Undercut groove", title: "Undercut groove along toe (box 02)", detail: "Auto-suggest (0.81) · stays Needs review until accepted.", rect: [0.3, 0.42, 0.18, 0.28], sev: "medium" },
      { label: "Clean weld pass", title: "Clean reference pass (negative check)", detail: "Verified clean zone on held-out split.", rect: [0.46, 0.2, 0.13, 0.32], sev: "ok" },
    ],
  },
  audio: {
    badge: "P0 · Wave 1 Multi-task MVP",
    title: "Acoustic Event Detection",
    status: "Merged in console (synthetic-verified)",
    filename: "pump-bearing-line4.wav · 48 kHz · 12.0s",
    split: "80% Train · 10% Val · 10% Held-out",
    data: "WAV recordings + event labels",
    quality: "Missed sound events and false alarms",
    recipe: "OS-AUD-001 · Audio classifier + test check",
    stageMetrics: {
      raw: [["48 kHz", "WAV PCM audio"], ["18 clips", "Motor sessions"], ["0", "Clipped files"]],
      label: [["64", "Event windows"], ["3", "Acoustic classes"], ["100%", "Reviewer signed"]],
      eval: [["0.94", "Event F1"], ["1 / 42", "Missed faults"], ["0.4 / hr", "False alarms"]],
      deploy: [["ONNX", "Edge export"], ["Baseline", "Anomaly watch"], ["v1.2", "Approved bundle"]],
    },
    samples: [
      { label: "Healthy hum", title: "Healthy baseline hum (0.6s–3.2s)", detail: "Steady 60 Hz motor tone establishing normal baseline.", rect: [0.05, 0.16, 0.16, 0.72], sev: "ok" },
      { label: "Bearing fault 3.4 kHz", title: "Bearing fault 3.4 kHz (4.2s–6.8s)", detail: "Harmonic spike marked on timeline; checked against held-out faults.", rect: [0.23, 0.16, 0.17, 0.72], sev: "high" },
      { label: "Cavitation spike", title: "Transient cavitation burst (8.4s–10.6s)", detail: "Broadband high-frequency burst captured for multi-class classifier.", rect: [0.42, 0.16, 0.16, 0.72], sev: "medium" },
    ],
  },
  ocr: {
    badge: "P0 · Wave 1 Multi-task MVP",
    title: "Handwriting & Document OCR",
    status: "Merged in console (training verified; serving is backend-level)",
    filename: "maintenance-log-408B.png · Handwritten form",
    split: "80% Train · 10% Val · 10% Held-out",
    data: "Scanned forms + corrected transcripts",
    quality: "Character errors, word errors and field accuracy",
    recipe: "OS-OCR-001 · Fine-tune TrOCR + measure errors",
    stageMetrics: {
      raw: [["120", "Form pages"], ["680", "Line crops"], ["Pretrained", "Initial TrOCR"]],
      label: [["680", "Verified lines"], ["42", "Clerk fixes"], ["0", "Unreviewed lines"]],
      eval: [["2.1%", "Char error (CER)"], ["4.8%", "Word error (WER)"], ["97.4%", "Field accuracy"]],
      deploy: [["TrOCR", "Fine-tuned"], ["+14%", "Vs base reader"], ["Audit", "Transcript log"]],
    },
    samples: [
      { label: "Valve ID", title: "Line 01 · “Valve 12B — Inlet”", detail: "Clerk fixed “Valvc 128” -> “Valve 12B” for TrOCR fine-tuning.", rect: [0.04, 0.2, 0.54, 0.2], sev: "info" },
      { label: "Defect note", title: "Line 02 · “Seal worn, weeping oil”", detail: "Cursive inspection remark aligned and verified.", rect: [0.04, 0.44, 0.54, 0.2], sev: "medium" },
      { label: "Pressure value", title: "Line 03 · “2.4 bar · Sign: A. Rahman”", detail: "Numeric pressure value & signature validated.", rect: [0.04, 0.68, 0.54, 0.2], sev: "ok" },
    ],
  },
  documents: {
    badge: "P1 · Wave 2 Language & Documents",
    title: "Document Classification & Extraction",
    status: "Text classification merged; field extraction in progress",
    filename: "shift-incident-2026-11.txt · Work order",
    split: "80% Train · 10% Val · 10% Held-out",
    data: "Text documents + spans, tags or fields",
    quality: "Class F1, confusion analysis and field accuracy",
    recipe: "OS-NLP-001 classification merged · NER & fields to come",
    stageMetrics: {
      raw: [["1,450", "Shift records"], ["4", "Doc categories"], ["Deduped", "Split safe"]],
      label: [["3,120", "Entity spans"], ["4", "Field schemas"], ["100%", "Human verified"]],
      eval: [["0.92", "Class F1"], ["95.1%", "Span match"], ["3", "Top confusions"]],
      deploy: [["OS-DOC", "Field extractor"], ["Schema", "JSON validated"], ["Human", "Override gate"]],
    },
    samples: [
      { label: "EQUIPMENT", title: "Span · EQUIPMENT: “Pump P-104B”", detail: "Equipment tag resolved into structured asset field.", rect: [0.04, 0.21, 0.54, 0.19], sev: "info" },
      { label: "FAULT & METRIC", title: "Span · FAULT: “seal weep · 78 °C”", detail: "Symptom paired with temperature for work-order routing.", rect: [0.04, 0.43, 0.54, 0.19], sev: "high" },
      { label: "ACTION", title: "Span · ACTION: “Isolate & replace gasket”", detail: "Recommended repair action extracted automatically.", rect: [0.04, 0.65, 0.54, 0.19], sev: "ok" },
    ],
  },
  movements: {
    badge: "P2 · Wave 3 Motion & Operations",
    title: "Human Posture & Gesture AI",
    status: "Proposed Wave 3 recipe",
    filename: "ergonomic-lift-bay2.mp4 · 17 keypoints",
    split: "Session-grouped Train / Val / Held-out",
    data: "Clips + keypoints and movement labels",
    quality: "Keypoint accuracy (OKS/PCK) and visibility failures",
    recipe: "Wave 3 · Pose & gesture models on landmark estimators",
    stageMetrics: {
      raw: [["36 clips", "Grouped by bay"], ["17 pts", "Body skeleton"], ["Privacy", "No face ID"]],
      label: [["890", "Pose frames"], ["4", "Posture classes"], ["12", "Occluded fixes"]],
      eval: [["0.89", "OKS / PCK"], ["93%", "Lift recall"], ["4.1%", "Occlusion miss"]],
      deploy: [["Edge", "Pose + action"], ["Session", "No biometrics"], ["Review", "Safety sign-off"]],
    },
    samples: [
      { label: "Safe squat lift", title: "Safe posture · Back neutral (16 deg)", detail: "Upright spine vector with bent knees · approved lift.", rect: [0.08, 0.16, 0.19, 0.68], sev: "ok" },
      { label: "Spine bend alert", title: "Strain risk · Forward flexion (48 deg)", detail: "Torso bend exceeds 35 deg ergonomic threshold.", rect: [0.27, 0.18, 0.18, 0.66], sev: "high" },
      { label: "Stop hand signal", title: "Hand landmark gesture · Open palm hold", detail: "21-point hand skeleton recognized clearly with glove.", rect: [0.46, 0.16, 0.14, 0.5], sev: "info" },
    ],
  },
  spreadsheets: {
    badge: "P0 · Wave 1 Multi-task MVP",
    title: "Tabular Prediction from Spreadsheets",
    status: "Merged in console (synthetic-verified)",
    filename: "compressor-telemetry-q3.csv · 12 columns",
    split: "80% Train · 10% Val · 10% Held-out",
    data: "Excel (.xlsx) or CSV rows + target column",
    quality: "Held-out error rate, class F1 and feature checks",
    recipe: "OS-TAB-001 · Tabular trainer + unseen-row test",
    stageMetrics: {
      raw: [["4,820", "Telemetry rows"], ["12 cols", "Validated types"], ["0", "Leakage cols"]],
      label: [["Target", "Failure_48h"], ["9", "Input features"], ["80/10/10", "Time-safe split"]],
      eval: [["0.91", "Class F1"], ["96.2%", "Held-out acc"], ["0.03", "False alert rate"]],
      deploy: [["Batch", "CSV/XLSX score"], ["Top 3", "Driver signals"], ["v1.1", "Reproducible"]],
    },
    samples: [
      { label: "Row #1042 · Risk", title: "Row #1042 · Temp 86 °C · Vib 8.4 mm/s", detail: "Failure_48h = High (0.91) driven by vibration + temp.", rect: [0.03, 0.27, 0.56, 0.15], sev: "high" },
      { label: "Row #1043 · Watch", title: "Row #1043 · Drop 1.9 bar · Watch", detail: "Failure_48h = Watch (0.54) queued for operator review.", rect: [0.03, 0.44, 0.56, 0.15], sev: "medium" },
      { label: "Row #1044 · Normal", title: "Row #1044 · Temp 52 °C · Vib 1.8 mm/s", detail: "Failure_48h = Normal (0.97) on held-out check.", rect: [0.03, 0.61, 0.56, 0.15], sev: "ok" },
    ],
  },
};

export const OUTCOME_TABS = [
  { key: "vision", icon: "image", label: "Vision" },
  { key: "audio", icon: "volume", label: "Audio" },
  { key: "ocr", icon: "file-text", label: "Handwriting" },
  { key: "documents", icon: "book", label: "Documents" },
  { key: "movements", icon: "activity", label: "Movements" },
  { key: "spreadsheets", icon: "table", label: "Spreadsheets" },
];

export const GALLERY_ITEMS = [
  { key: "audio", prio: "P0", pClass: "p0", name: "Acoustic AI", sub: "Audio -> Fault alert" },
  { key: "ocr", prio: "P0", pClass: "p0", name: "Handwriting OCR", sub: "Ink -> TrOCR fields" },
  { key: "documents", prio: "P1", pClass: "p0", name: "Documents", sub: "Spans -> JSON" },
  { key: "movements", prio: "P2", pClass: "p2", name: "Movements", sub: "Pose -> Check" },
  { key: "spreadsheets", prio: "P0", pClass: "p0", name: "Spreadsheets", sub: "Rows -> Forecast" },
  { key: "vision", prio: "P0", pClass: "p0", name: "Vision", sub: "Boxes -> Inspection" },
];

export const FUSION_SCENARIOS = {
  motor: {
    badge: "Cross-modal sync · Missing-input fallback · Modality ablation",
    verdict: "Joint fault verdict: Bearing wear (inspect within 48 hrs)",
    streams: [
      { kind: "Audio", title: "48 kHz Mic", val: "3.4 kHz spike", sev: "high", bars: [0.2, 0.4, 0.92, 0.78, 0.35] },
      { kind: "Vibration", title: "Accelerometer", val: "8.2 mm/s RMS", sev: "high", bars: [0.3, 0.55, 0.88, 0.9, 0.6] },
      { kind: "Telemetry", title: "Bearing Temp", val: "74 °C @ 1450 RPM", sev: "medium", bars: [0.4, 0.5, 0.62, 0.7, 0.75] },
    ],
    ablation: [["Audio only", "0.86"], ["Vibration only", "0.89"], ["Fused (3 streams)", "0.97"]],
  },
  safety: {
    badge: "Temporal alignment · Privacy-safe landmarks · Human override",
    verdict: "Joint safety alert: Dock unlocked while worker in active zone",
    streams: [
      { kind: "Video", title: "Bay Camera", val: "Forklift entering", sev: "medium", bars: [0.3, 0.6, 0.72, 0.65, 0.5] },
      { kind: "Human Pose", title: "17 Keypoints", val: "Worker bending", sev: "high", bars: [0.5, 0.75, 0.85, 0.82, 0.78] },
      { kind: "PLC State", title: "Dock Lock", val: "Gate UNLOCKED", sev: "high", bars: [0.2, 0.2, 0.95, 0.95, 0.95] },
    ],
    ablation: [["Video only", "0.78"], ["Pose + Video", "0.88"], ["Fused + PLC", "0.96"]],
  },
  thermal: {
    badge: "Spatial alignment · Asset provenance · Release bundle verified",
    verdict: "Joint inspection finding: Critical flange hotspot + corrosion",
    streams: [
      { kind: "Thermal IR", title: "LWIR Heat Map", val: "+28 °C hotspot", sev: "high", bars: [0.35, 0.6, 0.94, 0.88, 0.65] },
      { kind: "RGB Vision", title: "Clamp Photo", val: "Rust scaling", sev: "medium", bars: [0.4, 0.55, 0.74, 0.68, 0.6] },
      { kind: "Asset Log", title: "Line 12 Spec", val: "14.2 bar · 410d", sev: "info", bars: [0.5, 0.5, 0.55, 0.58, 0.6] },
    ],
    ablation: [["RGB only", "0.81"], ["Thermal only", "0.85"], ["Fused (IR+RGB+Log)", "0.95"]],
  },
};

export const LIFECYCLE_RESOURCES = [
  "Project", "Task", "Observation", "Annotation", "Dataset", "Run", "Model", "Eval", "Deploy", "Feedback",
];

export const PILLARS = [
  { prio: "P0", pClass: "p0", title: "Vision", sub: "Images & video" },
  { prio: "P0/P1", pClass: "p0", title: "Audio", sub: "Sounds & speech" },
  { prio: "P0/P1", pClass: "p0", title: "Language", sub: "OCR & text" },
  { prio: "P2", pClass: "p2", title: "Motion", sub: "Pose & gestures" },
  { prio: "P0/P1", pClass: "p0", title: "Signals", sub: "Tables & sensors" },
  { prio: "P3", pClass: "p3", title: "Fusion", sub: "Cross-modal AI" },
];

export const WAVES = [
  { tag: "Wave 0", win: "0–6 wks", title: "Shared foundation", code: "OS-CORE-001 to 006", focus: false },
  { tag: "Wave 1 · MVP", win: "2–4 mos", title: "Multi-task MVP", code: "OS-VIS-001 · OS-AUD-001 · OS-OCR-001 · OS-TAB-001", focus: true },
  { tag: "Wave 2", win: "4–7 mos", title: "Language & signals", code: "OS-ASR · OS-NLP · OS-DOC · OS-SIG · OS-AUD-002", focus: false },
  { tag: "Wave 3", win: "7–10 mos", title: "Motion & packs", code: "Pose, gestures, Domain Packs, Face AI", focus: false },
  { tag: "Wave 4", win: "10–15+ mos", title: "Fusion & edge", code: "Cross-modal fusion & Athena Nodes", focus: false },
];
