import pipe from "./assets/landing/pipe-corrosion.jpg";
import parking from "./assets/landing/parking-aerial.jpg";
import store from "./assets/landing/store-entrance.jpg";

export const SCENES = {
  corrosion: {
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
      ["high", "Severe scaling under the clamp band"],
      ["medium", "Coating breakdown, left pipe"],
      ["low", "Paint flaking, right pipe"],
    ],
    boxes: [
      [36, 30, 58, 32, "high"],
      [25, 55, 24, 43, "medium"],
      [52, 62, 22, 36, "low"],
    ],
  },
  parking: {
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
      ["ok", "Free block, bays 640–637"],
      ["ok", "Free block, bays 660–657"],
      ["info", "Free bays 617–615, top row"],
    ],
    boxes: [
      [39, 52, 19, 13, "ok"],
      [39, 68, 19, 14, "ok"],
      [45, 23, 13, 13, "info"],
    ],
  },
  footfall: {
    img: store,
    alt: "People at a store entrance",
    q: "“How many people are walking in and out?”",
    v: "attention",
    word: "Needs attention",
    h: "Two people are heading in and one is leaving. Parked bikes are narrowing the doorway.",
    counts: [
      ["6", "People in view"],
      ["2", "Walking in"],
      ["1", "Walking out"],
    ],
    items: [
      ["info", "Walking in, centre left"],
      ["info", "Walking out, by the doors"],
      ["medium", "Bikes narrowing the entrance"],
    ],
    boxes: [
      [25, 38, 16, 47, "info"],
      [44, 39, 10, 33, "info"],
      [17, 66, 32, 34, "medium"],
    ],
  },
};

export const SCENE_ORDER = ["corrosion", "parking", "footfall"];
