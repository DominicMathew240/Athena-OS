import pipe from "./assets/landing/pipe-corrosion.jpg";
import parking from "./assets/landing/parking-aerial.jpg";
import store from "./assets/landing/store-entrance.jpg";

export const SCENES = {
  corrosion: {
    kind: "photo",
    img: pipe,
    alt: "Corroded pipes with a rusted clamp",
    q: "“Is there corrosion, and how bad is it?”",
    v: "attention",
    word: "Needs attention",
    h: "Severe scaling at the pipe clamp, with coating breaking down on both pipes. Plan a repair this month.",
    counts: [
      ["3", "Corroded areas"],
      ["1", "Severe"],
      ["2", "Pipes affected"],
    ],
    items: [
      ["high", "Severe scaling under the clamp band", "clamp_corrosion"],
      ["medium", "Coating breakdown, left pipe", "coating_loss"],
      ["low", "Paint flaking, right pipe", "surface_flake"],
    ],
    boxes: [
      [36, 30, 58, 32, "high"],
      [25, 55, 24, 43, "medium"],
      [52, 62, 22, 36, "low"],
    ],
  },
  parking: {
    kind: "photo",
    img: parking,
    alt: "A car park seen from above",
    q: "“How many spaces are free?”",
    v: "clear",
    word: "All clear",
    h: "30 of 51 marked spaces are free. The middle and bottom rows have the most room.",
    counts: [
      ["51", "Total spaces"],
      ["21", "Occupied"],
      ["30", "Free"],
    ],
    items: [
      ["ok", "Free block, bays 640–637", "free_bays"],
      ["ok", "Free block, bays 660–657", "free_bays"],
      ["info", "Free bays 617–615, top row", "free_bays"],
    ],
    boxes: [
      [39, 52, 19, 13, "ok"],
      [39, 68, 19, 14, "ok"],
      [45, 23, 13, 13, "info"],
    ],
  },
  footfall: {
    kind: "photo",
    img: store,
    alt: "People at a store entrance",
    q: "“What is visible near the entrance?”",
    v: "attention",
    word: "Needs attention",
    h: "People are visible near the entrance. Parked bikes are narrowing the doorway. A still picture cannot establish crossing totals.",
    counts: [
      ["6", "People in view"],
      ["1", "Access pinch"],
      ["Still", "Snapshot scope"],
    ],
    items: [
      ["info", "People near the entrance", "person"],
      ["info", "Person by the doors", "person"],
      ["medium", "Bikes narrowing the entrance", "obstruction"],
    ],
    boxes: [
      [25, 38, 16, 47, "info"],
      [44, 39, 10, 33, "info"],
      [17, 66, 32, 34, "medium"],
    ],
  },
  audio: {
    kind: "audio",
    q: "“Does this pump WAV recording match a bearing fault?”",
    v: "attention",
    word: "Needs attention",
    h: "Clip-level audio-wav-cnn flags bearing_fault (synthetic-verified) with harmonic energy centered near 3.4 kHz.",
    counts: [
      ["48 kHz", "PCM WAV"],
      ["0.94", "Macro F1 (synth)"],
      ["ONNX", "Export verified"],
    ],
    items: [
      ["high", "Harmonic spike at 3.4 kHz (-11.8 dBFS)", "bearing_fault"],
      ["medium", "Transient squeal band near 6.8 kHz", "belt_slip"],
      ["ok", "60 Hz motor fundamental baseline", "healthy_motor"],
    ],
    variants: ["fault", "slip", "healthy"],
  },
  ocr: {
    kind: "ocr",
    q: "“What did the technician write on maintenance log 408B?”",
    v: "clear",
    word: "All clear",
    h: "Clerk-verified transcripts fine-tune TrOCR (serving is backend-level; base-weight licence decision open).",
    counts: [
      ["2.1%", "Char error (CER)"],
      ["4.8%", "Word error (WER)"],
      ["97.4%", "Line match"],
    ],
    items: [
      ["info", "Line 01 · Valve 12B — Inlet", "transcript"],
      ["medium", "Line 02 · Seal worn, weeping oil", "transcript"],
      ["ok", "Line 03 · 2.4 bar · Sign: A. Rahman", "transcript"],
    ],
  },
};

export const SCENE_ORDER = ["corrosion", "parking", "footfall", "audio", "ocr"];
