import { useEffect, useRef, useState } from "react";
import { Icon } from "./icons.jsx";
import { SCENE_ORDER, SCENES } from "./scenes.js";
import site from "./content/site.json";
import { MultimodalStudio } from "./MultimodalStudio.jsx";
import { DocsSection } from "./DocsSection.jsx";
import { HandwritingInkGraphic, RealAudioWaveform, SpectrogramGraphic } from "./ModalityGraphics.jsx";
import pipe from "./assets/landing/pipe-corrosion.jpg";
import parking from "./assets/landing/parking-aerial.jpg";
import store from "./assets/landing/store-entrance.jpg";
import refinery from "./assets/landing/refinery-skyline.jpg";
import safety from "./assets/landing/safety-worker.jpg";
import welding from "./assets/landing/welding.jpg";

const PORTFOLIO = site.founder.url;
const STORAGE_KEY = "color-scheme";
const TICKER = [
  "Corrosion & defect checks",
  "Motor fault acoustics",
  "Handwritten maintenance logs",
  "Spreadsheet predictions",
  "Work-order sorting",
  "Sensor signal monitoring",
  "Multi-signal fusion",
  "Offline edge deployment",
  "Human approval gates",
  "Self-hosted Qwen or BYO AI",
];

function getSystemScheme() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readStoredScheme() {
  try {
    const val = window.localStorage.getItem(STORAGE_KEY);
    return val === "light" || val === "dark" ? val : null;
  } catch {
    return null;
  }
}

function applySchemeToDocument(stored) {
  const meta = document.querySelector('meta[name="color-scheme"]');
  if (stored === "light" || stored === "dark") {
    document.documentElement.dataset.theme = stored;
    if (meta) meta.setAttribute("content", stored);
  } else {
    delete document.documentElement.dataset.theme;
    if (meta) meta.setAttribute("content", "light dark");
  }
}

function Brand() {
  return (
    <a className="lp-brand" href="./" aria-label="Athena OS home" data-testid="landing-brand">
      <span className="lp-mark" aria-hidden="true" />
      <span>
        Athena<em>OS</em>
      </span>
    </a>
  );
}

function PortfolioButton({ className, children, testId }) {
  return (
    <a className={className} href={PORTFOLIO} data-testid={testId}>
      {children}
    </a>
  );
}

function ThemeToggle() {
  const [stored, setStored] = useState(() => readStoredScheme());
  const [system, setSystem] = useState(() => getSystemScheme());
  const effective = stored ?? system;

  useEffect(() => {
    applySchemeToDocument(stored);
  }, [stored]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystem = () => setSystem(mq.matches ? "dark" : "light");
    const onStorage = (ev) => {
      if (ev.key === STORAGE_KEY) setStored(readStoredScheme());
    };
    mq.addEventListener("change", onSystem);
    window.addEventListener("storage", onStorage);
    return () => {
      mq.removeEventListener("change", onSystem);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  function toggle() {
    const target = effective === "dark" ? "light" : "dark";
    const sys = getSystemScheme();
    if (target === sys) {
      try { window.localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
      setStored(null);
    } else {
      try { window.localStorage.setItem(STORAGE_KEY, target); } catch { /* ignore */ }
      setStored(target);
    }
  }

  const nextLabel = effective === "dark" ? "Switch to light theme" : "Switch to dark theme";
  return (
    <button
      type="button"
      className="lp-theme-btn"
      onClick={toggle}
      aria-label={nextLabel}
      data-testid="landing-theme-toggle"
    >
      <Icon name={effective === "dark" ? "sun" : "moon"} size={16} />
      <span className="lp-theme-text">{effective === "dark" ? "Light" : "Dark"}</span>
    </button>
  );
}

function Demo() {
  const [current, setCurrent] = useState("corrosion");
  const [swapping, setSwapping] = useState(false);
  const [shown, setShown] = useState(SCENES.corrosion);
  const [activeFinding, setActiveFinding] = useState(0);
  const [auto, setAuto] = useState(true);
  const tabRefs = useRef({});

  useEffect(() => {
    if (!auto) return undefined;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return undefined;
    const id = window.setInterval(() => {
      setCurrent((key) => SCENE_ORDER[(SCENE_ORDER.indexOf(key) + 1) % SCENE_ORDER.length]);
    }, 6500);
    return () => window.clearInterval(id);
  }, [auto]);

  useEffect(() => {
    setSwapping(true);
    const t = window.setTimeout(() => {
      setShown(SCENES[current]);
      setActiveFinding(0);
      setSwapping(false);
    }, 180);
    return () => window.clearTimeout(t);
  }, [current]);

  function select(key, moveFocus = false) {
    setAuto(false);
    setCurrent(key);
    if (moveFocus) tabRefs.current[key]?.focus();
  }

  function onDemoKeyDown(ev, idx) {
    let next = -1;
    if (ev.key === "ArrowRight") next = (idx + 1) % SCENE_ORDER.length;
    else if (ev.key === "ArrowLeft") next = (idx - 1 + SCENE_ORDER.length) % SCENE_ORDER.length;
    else if (ev.key === "Home") next = 0;
    else if (ev.key === "End") next = SCENE_ORDER.length - 1;
    if (next >= 0) {
      ev.preventDefault();
      select(SCENE_ORDER[next], true);
    }
  }

  const labels = {
    corrosion: "Corrosion",
    parking: "Parking",
    footfall: "Entrance",
    audio: "Audio WAV",
    ocr: "OCR Form",
  };

  return (
    <div className="lp-demo" data-testid="landing-hero-demo">
      <div className="lp-demo-bar">
        <span className="lp-demo-badge">MULTIMODAL</span>
        <div className="lp-demo-tabs" role="tablist" aria-label="Example checks across modalities">
          {SCENE_ORDER.map((key, idx) => (
            <button
              key={key}
              ref={(el) => { tabRefs.current[key] = el; }}
              type="button"
              role="tab"
              aria-selected={current === key}
              tabIndex={current === key ? 0 : -1}
              data-testid={`landing-demo-tab-${key}`}
              onClick={() => select(key, false)}
              onKeyDown={(ev) => onDemoKeyDown(ev, idx)}
            >
              {labels[key]}
            </button>
          ))}
        </div>
        <span className="lp-demo-live">PREVIEW</span>
      </div>

      <div className="lp-demo-stage">
        <div className={`lp-demo-frame lp-demo-frame--${shown.kind || "photo"}`} data-swap={swapping}>
          {shown.kind === "audio" ? (
            <div className="lp-hero-modality">
              <div className="lp-hero-mod-head">
                <span>pump-bearing-line4.wav · 16-bit PCM · 48 kHz</span>
                <code>audio-wav-cnn</code>
              </div>
              <RealAudioWaveform variant={shown.variants?.[activeFinding] || "fault"} />
              <SpectrogramGraphic variant={shown.variants?.[activeFinding] || "fault"} />
            </div>
          ) : shown.kind === "ocr" ? (
            <div className="lp-hero-modality lp-hero-modality--ocr">
              <div className="lp-hero-mod-head">
                <span>maintenance-log-408B.png · line crops</span>
                <code>ocr-trocr</code>
              </div>
              <div className="lp-hero-ocr-stack">
                {[0, 1, 2].map((idx) => (
                  <button
                    key={idx}
                    type="button"
                    className="lp-hero-ocr-item"
                    aria-pressed={activeFinding === idx}
                    onClick={() => { setAuto(false); setActiveFinding(idx); }}
                  >
                    <HandwritingInkGraphic variant={idx} />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              <img src={shown.img} alt={shown.alt} width={1400} height={933} />
              {(shown.boxes || []).map(([x, y, w, h, sev], i) => (
                <button
                  key={`${current}-${i}`}
                  type="button"
                  className="lp-dbox"
                  data-sev={sev}
                  data-active={activeFinding === i}
                  style={{ left: `${x}%`, top: `${y}%`, width: `${w}%`, height: `${h}%` }}
                  onClick={() => { setAuto(false); setActiveFinding(i); }}
                  aria-label={`Highlight finding ${i + 1}: ${shown.items[i]?.[1] || ""}`}
                >
                  <i>{i + 1}</i>
                </button>
              ))}
            </>
          )}
        </div>

        <div className="lp-report" data-swap={swapping} aria-live="polite">
          <div className="lp-report-top">
            <span className="lp-verdict" data-v={shown.v}>{shown.word}</span>
            <span className="lp-report-gate">Computer opinion · person decides</span>
          </div>
          <p className="lp-report-q">{shown.q}</p>
          <p className="lp-report-h">{shown.h}</p>
          <div className="lp-report-counts">
            {shown.counts.map(([value, label]) => (
              <div key={label}>
                <b>{value}</b>
                <span>{label}</span>
              </div>
            ))}
          </div>
          <ol className="lp-report-list">
            {shown.items.map(([sev, text, tag], i) => (
              <li key={text} data-sev={sev} data-active={activeFinding === i}>
                <button
                  type="button"
                  className="lp-finding-btn"
                  onClick={() => { setAuto(false); setActiveFinding(i); }}
                  aria-pressed={activeFinding === i}
                >
                  <b>{i + 1}</b>
                  <span>{text}</span>
                  <code>{tag}</code>
                </button>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const navRef = useRef(null);
  const navInnerRef = useRef(null);
  const navLinksRef = useRef(null);
  const menuRef = useRef(null);
  const [navWide, setNavWide] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [navHidden, setNavHidden] = useState(false);

  useEffect(() => {
    const items = document.querySelectorAll(".lp-reveal");
    if ("IntersectionObserver" in window) {
      const seen = new IntersectionObserver(
        (entries) =>
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              entry.target.classList.add("is-in");
              seen.unobserve(entry.target);
            }
          }),
        { rootMargin: "0px 0px -8% 0px", threshold: 0.06 },
      );
      items.forEach((item) => seen.observe(item));
      return () => seen.disconnect();
    }
    items.forEach((item) => item.classList.add("is-in"));
    return undefined;
  }, []);

  useEffect(() => {
    const nav = navRef.current;
    const inner = navInnerRef.current;
    const links = navLinksRef.current;
    if (!nav || !inner || !links) return undefined;

    const supportsPopover = typeof HTMLElement !== "undefined" && Object.hasOwn(HTMLElement.prototype, "popover");
    let measuring = false;

    const sync = () => {
      let wasOpen = false;
      if (supportsPopover) {
        try { wasOpen = links.matches(":popover-open"); } catch { wasOpen = false; }
      }
      nav.classList.add("is-measuring", "is-wide");
      links.removeAttribute("popover");
      const fits = inner.scrollWidth <= inner.clientWidth + 1;
      nav.classList.toggle("is-wide", fits);
      setNavWide(fits);
      if (fits) {
        setMenuOpen(false);
      } else if (supportsPopover) {
        links.setAttribute("popover", "auto");
        if (wasOpen && typeof links.showPopover === "function") links.showPopover();
      }
      requestAnimationFrame(() => nav.classList.remove("is-measuring"));
    };

    sync();
    let lastW = nav.clientWidth || 0;
    const ro = "ResizeObserver" in window
      ? new ResizeObserver(() => {
          if (measuring) return;
          const nowW = nav.clientWidth || 0;
          if (nav.classList.contains("is-wide") || nowW > lastW) {
            measuring = true;
            lastW = nowW;
            sync();
            measuring = false;
          } else {
            lastW = nowW;
          }
        })
      : null;
    if (ro) {
      ro.observe(nav);
      ro.observe(links);
    }

    const onToggle = (ev) => setMenuOpen(ev.newState === "open");
    if (supportsPopover) links.addEventListener("toggle", onToggle);

    let lastY = window.scrollY || 0;
    const supportsScrollState = typeof CSS !== "undefined" && typeof CSS.supports === "function" && CSS.supports("container-type", "scroll-state");
    const onScroll = () => {
      const y = Math.max(0, window.scrollY || 0);
      setScrolled(y > 8);
      if (!supportsScrollState) {
        const maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
        const clamped = Math.min(y, maxY);
        if (clamped !== lastY) {
          setNavHidden(clamped > lastY && clamped > (nav.offsetHeight || 64) && !menuOpen);
          lastY = clamped;
        }
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    return () => {
      if (ro) ro.disconnect();
      if (supportsPopover) links.removeEventListener("toggle", onToggle);
      window.removeEventListener("scroll", onScroll);
    };
  }, [menuOpen]);

  function closeMenu() {
    setMenuOpen(false);
    const links = navLinksRef.current;
    try {
      if (links && typeof links.matches === "function" && links.matches(":popover-open") && typeof links.hidePopover === "function") {
        links.hidePopover();
      }
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="lp">
      <a className="lp-skip" href="#main">Skip to main content</a>
      <header
        ref={navRef}
        className={`lp-nav${navWide ? " is-wide" : ""}${navHidden ? " is-hidden" : ""}`}
        id="lp-nav"
        data-scrolled={scrolled}
        data-open={menuOpen}
        data-testid="landing-nav"
      >
        <div className="lp-container lp-nav-inner" id="lp-nav-inner" ref={navInnerRef}>
          <Brand />
          <nav className="lp-nav-group" aria-label="Page sections">
            <button
              ref={menuRef}
              className="lp-menu"
              id="lp-menu"
              type="button"
              popovertarget="lp-links"
              popovertargetaction="toggle"
              aria-expanded={menuOpen}
              aria-controls="lp-links"
              aria-label="Open menu"
              data-testid="landing-menu-toggle"
              onClick={() => {
                if (!("popover" in HTMLElement.prototype)) setMenuOpen((o) => !o);
              }}
            >
              <Icon name="menu" size={20} />
            </button>
            <ul className="lp-links" id="lp-links" ref={navLinksRef}>
              <li><a href="#use-cases" data-testid="landing-nav-use-cases" onClick={closeMenu}>Use cases</a></li>
              <li><a href="#how" data-testid="landing-nav-how" onClick={closeMenu}>Workflow</a></li>
              <li><a href="#teach-workflow" data-testid="landing-nav-teach" onClick={closeMenu}>Studio</a></li>
              <li><a href="#roadmap" data-testid="landing-nav-roadmap" onClick={closeMenu}>Fusion &amp; Edge</a></li>
              <li><a href="#docs" data-testid="landing-nav-docs" onClick={closeMenu}>Docs</a></li>
              <li><a href="#founder" onClick={closeMenu}>Founder</a></li>
              <li><a href="#faq" onClick={closeMenu}>FAQ</a></li>
              <li className="lp-links-cta">
                <a className="lp-btn lp-btn--outline lp-btn--sm" href="#docs" onClick={closeMenu}>Docs &amp; Roadmap</a>
              </li>
            </ul>
          </nav>
          <div className="lp-nav-cta">
            <ThemeToggle />
            <a className="lp-btn lp-btn--outline" href="#docs" data-testid="landing-docs-button">Docs</a>
            <PortfolioButton className="lp-btn lp-btn--primary" testId="landing-nav-cta-button">
              Visit portfolio
              <Icon name="arrow" size={16} />
            </PortfolioButton>
          </div>
        </div>
      </header>

      <main id="main">
        <section className="lp-hero" aria-labelledby="hero-title">
          <div className="lp-container lp-hero-grid">
            <div className="lp-hero-copy">
              <p className="lp-kicker" data-testid="landing-positioning-kicker">
                Athena OS · Teach AI what matters to your world. No coding required.
              </p>
              <h1 className="lp-h1" id="hero-title" data-testid="landing-hero-title">
                Train AI on <em>all</em> your data.
              </h1>
              <p className="lp-lead">
                Build, review, test, and approve models across photos, audio clips, handwriting, spreadsheets, text, and signals — no coding required.
              </p>
              <p className="lp-lead-note">
                Inspect photos or 6-frame clips today (not a complete crossing count or live monitor), and train custom models across six data types on your own hardware.
              </p>
              <div className="lp-cta-row">
                <a className="lp-btn lp-btn--primary lp-btn--lg" href="#teach-workflow" data-testid="landing-hero-cta-button">
                  Explore the studio
                  <Icon name="arrow" size={18} />
                </a>
                <a className="lp-btn lp-btn--line lp-btn--lg" href="#roadmap" data-testid="landing-hero-secondary-button">
                  See fusion &amp; edge
                  <Icon name="arrow" size={16} />
                </a>
              </div>
              <ul className="lp-proof" aria-label="At a glance">
                <li><Icon name="layers" size={16} /><strong>6 data types</strong> ready to train</li>
                <li><Icon name="cpu" size={16} /><strong>Qwen</strong> or your own AI</li>
                <li><Icon name="server" size={16} />Offline edge · Apache-2.0</li>
              </ul>
            </div>
            <Demo />
          </div>
        </section>

        <div className="lp-ticker" aria-label="Capabilities and merged slices in Athena OS">
          <div className="lp-ticker-track">
            {TICKER.map((item) => (<span key={item}>{item}</span>))}
            {TICKER.map((item) => (<span key={`${item}-dup`} aria-hidden="true">{item}</span>))}
          </div>
        </div>

        <section className="lp-section lp-why" aria-labelledby="why-title">
          <div className="lp-container">
            <div className="lp-head lp-reveal">
              <p className="lp-eyebrow"><b>01</b> Why Athena</p>
              <h2 className="lp-h2" id="why-title">A detector shows <span>what</span>.<br />Athena tells you <em>why it matters.</em></h2>
            </div>
            <div className="lp-compare">
              <article className="lp-compare-card lp-reveal" data-testid="landing-compare-detector">
                <p className="lp-compare-tag">Standard detector (CNN)</p>
                <div className="lp-compare-img lp-compare-img--plain">
                  <img src={pipe} alt="Pipes with raw confidence-only boxes" loading="lazy" width={1400} height={933} />
                  <span className="lp-cbox" data-b="1"><i>rust 0.82</i></span>
                  <span className="lp-cbox" data-b="2"><i>rust 0.77</i></span>
                  <span className="lp-cbox" data-b="3"><i>rust 0.61</i></span>
                </div>
                <ul className="lp-list lp-list--muted">
                  <li>Raw boxes and class confidence scores only.</li>
                  <li>No guidance on severity or next maintenance action.</li>
                  <li>Separate tooling per data type.</li>
                </ul>
              </article>
              <article className="lp-compare-card lp-compare-card--athena lp-reveal" data-testid="landing-compare-athena">
                <p className="lp-compare-tag"><span className="lp-live" aria-hidden="true" />Athena inspection + human sign-off</p>
                <div className="lp-compare-img">
                  <img src={pipe} alt="Pipes with numbered severity boxes and review state" loading="lazy" width={1400} height={933} />
                  <span className="lp-abox" data-b="1" data-sev="high"><i>1</i></span>
                  <span className="lp-abox" data-b="2" data-sev="medium"><i>2 · review</i></span>
                  <span className="lp-abox" data-b="3" data-sev="low"><i>3</i></span>
                </div>
                <p className="lp-quote">“<strong>Severe clamp scaling (1)</strong> and coating breakdown on both pipes (2, 3) — wire-brush, recoat, and check wall thickness.”</p>
                <ul className="lp-list">
                  <li>Plain-language severity and repair actions with human review gates.</li>
                  <li>Suggested boxes stay marked Needs review until accepted.</li>
                  <li>One console across instant VLM checks and trained models.</li>
                </ul>
              </article>
            </div>
          </div>
        </section>

        <section className="lp-section lp-cases" id="use-cases" aria-labelledby="cases-title">
          <div className="lp-container">
            <div className="lp-head lp-head--split lp-reveal">
              <div><p className="lp-eyebrow"><b>02</b> Use cases</p><h2 className="lp-h2" id="cases-title">Ready-made checks out of the box.</h2></div>
              <p className="lp-sub">Marked regions, counts, and structured reports designed for human decision-making.</p>
            </div>
            <div className="lp-bento">
              <article className="lp-case lp-case--hero lp-reveal" data-testid="landing-case-oil-gas">
                <img src={refinery} alt="A refinery at dusk" loading="lazy" width={1600} height={1068} />
                <div className="lp-case-body">
                  <span className="lp-case-tag">Oil &amp; gas · utilities</span>
                  <h3>Corrosion, leaks &amp; coating damage</h3>
                  <p>Severity grading on pipe racks, flanges, and tanks with clear repair notes.</p>
                  <p className="lp-case-q">“Is there corrosion under the insulation on line 12?”</p>
                </div>
              </article>
              <article className="lp-case lp-reveal" data-testid="landing-case-parking">
                <img src={parking} alt="A car park seen from above" loading="lazy" width={1200} height={800} />
                <div className="lp-case-body"><span className="lp-case-tag">Facilities · snapshot</span><h3>Parking spaces</h3><p>Visible free vs. taken bays in photos (not live sensor tracking).</p></div>
              </article>
              <article className="lp-case lp-reveal" data-testid="landing-case-footfall">
                <img src={store} alt="People at a store entrance" loading="lazy" width={1000} height={641} />
                <div className="lp-case-body"><span className="lp-case-tag">Retail · snapshot</span><h3>People in snapshot</h3><p>A snapshot of people near entrances. Does not establish crossing totals or wait times.</p></div>
              </article>
              <article className="lp-case lp-reveal" data-testid="landing-case-ppe">
                <img src={safety} alt="A worker in a helmet and high-visibility vest" loading="lazy" width={1000} height={1500} />
                <div className="lp-case-body"><span className="lp-case-tag">Safety · plants</span><h3>Safety gear</h3><p>Helmets, vests, glasses, and zone hazards.</p></div>
              </article>
              <article className="lp-case lp-reveal" data-testid="landing-case-defects">
                <img src={welding} alt="Welding sparks on a metal part" loading="lazy" width={1000} height={667} />
                <div className="lp-case-body"><span className="lp-case-tag">Manufacturing</span><h3>Weld &amp; surface defects</h3><p>Cracks, porosity, dents, and spatter bounds.</p></div>
              </article>
              <article className="lp-case lp-case--text lp-reveal" data-testid="landing-case-custom">
                <div className="lp-case-prompt">
                  <div className="lp-case-prompt-main">
                    <span className="lp-case-tag">Custom prompt</span>
                    <h3>Ask your own inspection question</h3>
                    <p>Point Athena at any photo or 6-frame clip, mark optional target areas, and get a structured verdict ready for review.</p>
                  </div>
                  <div className="lp-case-prompt-sample" aria-hidden="true">
                    <span className="lp-prompt-label">Question</span>
                    <code>“Are gauge needles inside the green operating zone?”</code>
                    <span className="lp-prompt-result">Verdict: All clear · 3 gauges checked</span>
                  </div>
                  <PortfolioButton className="lp-btn lp-btn--primary" testId="landing-case-custom-cta">Visit portfolio<Icon name="arrow" size={16} /></PortfolioButton>
                </div>
              </article>
            </div>
          </div>
        </section>

        <section className="lp-section lp-how" id="how" aria-labelledby="how-title">
          <div className="lp-container">
            <div className="lp-head lp-reveal">
              <p className="lp-eyebrow"><b>03</b> How it works</p>
              <h2 className="lp-h2" id="how-title">Photo to reviewed report in three steps.</h2>
            </div>
            <ol className="lp-steps">
              <li className="lp-step lp-reveal" data-testid="landing-step-1">
                <span className="lp-step-n">01</span>
                <h3>Pick a check</h3>
                <p>Select a ready-made template or write your inspection question in plain English.</p>
                <div className="lp-mini lp-mini--chips" aria-hidden="true"><span data-on="true">Corrosion</span><span>Parking</span><span>Safety</span><span>Custom</span></div>
              </li>
              <li className="lp-step lp-reveal" data-testid="landing-step-2">
                <span className="lp-step-n">02</span>
                <h3>Add media</h3>
                <p>Drop up to six photos or one video clip (sampled evenly into six still frames in-browser).</p>
                <div className="lp-mini lp-mini--drop" aria-hidden="true"><span /><span /><span /><b>6 max</b></div>
              </li>
              <li className="lp-step lp-reveal" data-testid="landing-step-3">
                <span className="lp-step-n">03</span>
                <h3>Review &amp; decide</h3>
                <p>Read the verdict, inspect numbered boxes, and confirm or reject findings yourself.</p>
                <div className="lp-mini lp-mini--report" aria-hidden="true">
                  <div className="lp-mini-report-head">
                    <b>NEEDS ATTENTION</b>
                    <span>1 severe · 2 moderate</span>
                  </div>
                  <p>1. Severe clamp scaling — schedule wall-thickness check.</p>
                </div>
              </li>
            </ol>
          </div>
        </section>

        <MultimodalStudio portfolioUrl={PORTFOLIO} />
        <DocsSection />

        <section className="lp-section lp-ai" id="your-ai" aria-labelledby="ai-title">
          <div className="lp-container lp-ai-grid">
            <div className="lp-reveal">
              <p className="lp-eyebrow"><b>07</b> Your AI, your rules</p>
              <h2 className="lp-h2" id="ai-title">Run AI where you choose.</h2>
              <p className="lp-sub">Connect self-hosted Qwen, Ollama, or vLLM for picture reading, and run training workers locally on your own CPU/GPU with encrypted credentials.</p>
              <ul className="lp-pills" aria-label="Works with">
                <li>Qwen-VL</li><li>OpenAI</li><li>Google Gemini</li><li>OpenRouter</li><li>vLLM</li><li>Ollama</li><li>LM Studio</li><li>Alibaba Cloud</li>
              </ul>
            </div>
            <div className="lp-ai-cards lp-reveal" aria-hidden="true">
              <div className="lp-ai-card lp-ai-card--on">
                <div className="lp-ai-top"><strong>Built-in Qwen</strong><span className="lp-ai-badge">Default</span></div>
                <p>qwen2.5vl:7b · local or self-hosted</p>
                <span className="lp-ai-status"><i />Connected example</span>
              </div>
              <div className="lp-ai-card">
                <div className="lp-ai-top"><strong>Your own AI</strong><span className="lp-ai-badge lp-ai-badge--line">Optional</span></div>
                <div className="lp-ai-field"><span>Address</span><code>https://api.openai.com/v1</code></div>
                <div className="lp-ai-field"><span>Model</span><code>gpt-4o-mini</code></div>
                <div className="lp-ai-field"><span>API key</span><code>encrypted · 3f9a</code></div>
              </div>
            </div>
          </div>
        </section>

        <section className="lp-section lp-open" id="open" aria-labelledby="open-title">
          <div className="lp-container">
            <div className="lp-head lp-reveal">
              <p className="lp-eyebrow"><b>08</b> Built to be trusted</p>
              <h2 className="lp-h2" id="open-title">Open source. Self-hosted. Human-approved.</h2>
            </div>
            <div className="lp-trust">
              <div className="lp-trust-item lp-reveal"><span><Icon name="code" size={22} /></span><h3>Open source</h3><p>Apache-2.0 with zero required cloud service.</p></div>
              <div className="lp-trust-item lp-reveal"><span><Icon name="server" size={22} /></span><h3>Self-hosted</h3><p>One server; images, audio, and tables stay on your network.</p></div>
              <div className="lp-trust-item lp-reveal"><span><Icon name="shield" size={22} /></span><h3>Human sign-off</h3><p>No auto-promotion — review evidence and roll back anytime.</p></div>
              <div className="lp-trust-item lp-reveal"><span><Icon name="detector" size={22} /></span><h3>Real quality gates</h3><p>Task metrics like missed faults, WER, CER, F1, and mAP.</p></div>
            </div>
            <section className="lp-advisory" aria-labelledby="security-update-title">
              <div>
                <h3 id="security-update-title">Security update: esbuild advisory</h3>
                <p>GitHub advisory GHSA-gv7w-rqvm-qjhr was withdrawn because it identified the wrong package (esbuild’s Deno module). This landing page no longer includes esbuild in its build dependencies.</p>
              </div>
              <a className="lp-btn lp-btn--outline lp-btn--sm" href="https://github.com/advisories/GHSA-gv7w-rqvm-qjhr">Read the GitHub advisory<Icon name="arrow" size={15} /></a>
            </section>
          </div>
        </section>

        <section className="lp-section lp-about" id="founder" aria-labelledby="founder-title">
          <div className="lp-container">
            <div className="lp-head lp-reveal">
              <p className="lp-eyebrow"><b>09</b> Who is behind this</p>
              <h2 className="lp-h2" id="founder-title">Built by <a href={site.founder.url}>{site.founder.name}</a>.</h2>
            </div>
            <article className="lp-about-card lp-reveal">
              <p className="lp-about-copy">
                Co-Founder and CTO of Stratetics Experts. <a href={site.founder.url}>{site.athenaVision.name}</a> is the in-house Segment and Detect stack — label one picture, review AI drafts on the rest, export boxes and masks to COCO, YOLO or VOC. It sits behind <a href={site.inspecTher.url}>{site.inspecTher.name}</a>, AI-driven thermal inspection of solar PV assets. This page is the open-source Athena OS toolkit, not the hosted app.
              </p>
            </article>
          </div>
        </section>

        <section className="lp-section lp-faq" id="faq" aria-labelledby="faq-title">
          <div className="lp-container">
            <div className="lp-head lp-reveal">
              <p className="lp-eyebrow"><b>10</b> Questions</p>
              <h2 className="lp-h2" id="faq-title">Straight answers.</h2>
            </div>
            <div className="lp-faq-list">
              {site.faq.map((item) => (
                <details key={item.q} className="lp-faq-item lp-reveal">
                  <summary>{item.q}</summary>
                  <p>{item.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-final" aria-labelledby="final-title">
          <div className="lp-container lp-final-inner lp-reveal">
            <h2 className="lp-h2" id="final-title">Teach AI what matters to your world.</h2>
            <p className="lp-sub">Explore the portfolio, models, and open-source multimodal platform docs.</p>
            <div className="lp-cta-row">
              <PortfolioButton className="lp-btn lp-btn--primary lp-btn--lg" testId="landing-final-cta-button">Visit the portfolio<Icon name="arrow" size={18} /></PortfolioButton>
              <a className="lp-btn lp-btn--line lp-btn--lg" href="#docs" data-testid="landing-final-docs">Read the docs<Icon name="arrow" size={16} /></a>
            </div>
            <p className="lp-fine">Apache-2.0. Self-hosted workspace; no Athena cloud required.</p>
          </div>
        </section>
      </main>

      <footer className="lp-foot">
        <div className="lp-container lp-foot-inner">
          <Brand />
          <p>
            Open-source multimodal AI training &amp; inspection platform. Built by{" "}
            <a href={site.founder.url}>{site.founder.name}</a>. The solar inspection product is{" "}
            <a href={site.inspecTher.url}>{site.inspecTher.name}</a>. Code under Apache-2.0.
          </p>
        </div>
      </footer>
    </div>
  );
}
