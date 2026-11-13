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
      q: "“How many people are walking in and out?”", v: "attention", word: "Needs attention",
      h: "Two people are heading in and one is leaving. Parked bikes are narrowing the doorway.",
      counts: [["6", "People in view"], ["2", "Walking in"], ["1", "Walking out"]],
      items: [["info", "Walking in, centre left"], ["info", "Walking out, by the doors"], ["medium", "Bikes narrowing the entrance"]],
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
  const menu = document.getElementById("lp-menu");
  menu.addEventListener("click", () => {
    const open = nav.dataset.open !== "true";
    nav.dataset.open = open ? "true" : "false";
    menu.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.querySelectorAll(".lp-links a").forEach(a => a.addEventListener("click", () => { nav.dataset.open = "false"; menu.setAttribute("aria-expanded", "false"); }));
  const onScroll = () => { nav.dataset.scrolled = window.scrollY > 8 ? "true" : "false"; };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

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
