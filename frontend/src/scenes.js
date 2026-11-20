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
      ["3", "Rust spots"],
      ["1", "Severe"],
      ["2", "Pipes affected"],
    ],
    items: [
      ["high", "Severe rust under the clamp band", "Severe"],
      ["medium", "Paint peeling on left pipe", "Check soon"],
      ["low", "Surface flaking on right pipe", "Minor"],
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
      ["21", "Taken"],
      ["30", "Free"],
    ],
    items: [
      ["ok", "Open row, bays 640–637", "Open"],
      ["ok", "Open row, bays 660–657", "Open"],
      ["info", "Open bays 617–615, top row", "Open"],
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
    h: "People are visible near the entrance. Parked bikes are narrowing the doorway. A single photo cannot count everyone entering over time.",
    counts: [
      ["6", "People visible"],
      ["1", "Blocked path"],
      ["Photo", "Single snapshot"],
    ],
    items: [
      ["info", "People near the entrance", "Person"],
      ["info", "Person by the doors", "Person"],
      ["medium", "Bikes narrowing the doorway", "Obstacle"],
    ],
    boxes: [
      [25, 38, 16, 47, "info"],
      [44, 39, 10, 33, "info"],
      [17, 66, 32, 34, "medium"],
    ],
  },
  audio: {
    kind: "audio",
    q: "“Does this pump sound healthy?”",
    v: "attention",
    word: "Needs attention",
    h: "Repeated sharp clicks in this 4-second recording point to early bearing wear on Pump 04.",
    counts: [
      ["4.2s", "Sound clip"],
      ["94%", "Test accuracy"],
      ["Local", "Runs offline"],
    ],
    items: [
      ["high", "Repeated clicking noise (bearing wear)", "Fault"],
      ["medium", "Brief belt squeal near middle", "Watch"],
      ["ok", "Normal steady motor hum", "Normal"],
    ],
    variants: ["fault", "slip", "healthy"],
  },
  ocr: {
    kind: "ocr",
    q: "“What is written on Maintenance Log 408B?”",
    v: "clear",
    word: "All clear",
    h: "Reads handwritten logbook lines cleanly after learning from a few technician corrections.",
    counts: [
      ["98%", "Letters right"],
      ["95%", "Words right"],
      ["97%", "Lines matched"],
    ],
    items: [
      ["info", "Line 01 · Valve 12B — Inlet", "Read"],
      ["medium", "Line 02 · Seal worn, weeping oil", "Read"],
      ["ok", "Line 03 · 2.4 bar · Sign: A. Rahman", "Read"],
    ],
  },
};

export const SCENE_ORDER = ["corrosion", "parking", "footfall", "audio", "ocr"];
