import { useEffect, useState } from "react";
import { Icon } from "./icons.jsx";
import { SCENE_ORDER, SCENES } from "./scenes.js";
import site from "./content/site.json";
import pipe from "./assets/landing/pipe-corrosion.jpg";
import parking from "./assets/landing/parking-aerial.jpg";
import store from "./assets/landing/store-entrance.jpg";
import refinery from "./assets/landing/refinery-skyline.jpg";
import safety from "./assets/landing/safety-worker.jpg";
import welding from "./assets/landing/welding.jpg";

const PORTFOLIO = site.founder.url;
const TICKER = [
  "Parking occupancy",
  "Store foot traffic",
  "Corrosion grading",
  "Weld defects",
  "Hard hats & vests",
  "Oil leaks",
  "Blocked fire exits",
  "Empty shelves",
  "Gauge readings",
  "Flare smoke",
  "Loading-bay queues",
  "Cracked panels",
];

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

function Demo() {
  const [current, setCurrent] = useState("corrosion");
  const [swapping, setSwapping] = useState(false);
  const [shown, setShown] = useState(SCENES.corrosion);
  const [auto, setAuto] = useState(true);

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
      setSwapping(false);
    }, 220);
    return () => window.clearTimeout(t);
  }, [current]);

  function select(key) {
    setAuto(false);
    setCurrent(key);
  }

  return (
    <div className="lp-demo" data-testid="landing-hero-demo">
      <div className="lp-demo-bar">
        <span className="lp-dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <div className="lp-demo-tabs" role="tablist" aria-label="Example inspections">
          <button
            type="button"
            role="tab"
            aria-selected={current === "corrosion"}
            data-testid="landing-demo-tab-corrosion"
            onClick={() => select("corrosion")}
          >
            Corrosion
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={current === "parking"}
            data-testid="landing-demo-tab-parking"
            onClick={() => select("parking")}
          >
            Parking
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={current === "footfall"}
            data-testid="landing-demo-tab-footfall"
            onClick={() => select("footfall")}
          >
            Foot traffic
          </button>
        </div>
        <span className="lp-demo-live">LIVE · QWEN-VL</span>
      </div>
      <div className="lp-demo-stage">
        <div className="lp-demo-frame" data-swap={swapping ? "true" : "false"}>
          <img src={shown.img} alt={shown.alt} width="1400" height="933" />
          <span className="lp-scan" aria-hidden="true" />
          {shown.boxes.map(([x, y, w, h, sev], i) => (
            <span
              key={`${shown.alt}-${i}`}
              className="lp-dbox"
              data-sev={sev}
              style={{
                left: `${x}%`,
                top: `${y}%`,
                width: `${w}%`,
                height: `${h}%`,
                animationDelay: `${0.15 + i * 0.12}s`,
              }}
            >
              <i>{i + 1}</i>
            </span>
          ))}
        </div>
        <div className="lp-report" data-swap={swapping ? "true" : "false"} aria-live="polite">
          <p className="lp-report-q">{shown.q}</p>
          <span className="lp-verdict" data-v={shown.v}>
            {shown.word}
          </span>
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
            {shown.items.map(([sev, text], i) => (
              <li key={text} data-sev={sev}>
                <b>{i + 1}</b>
                {text}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const items = document.querySelectorAll(".lp-reveal");
    if (!("IntersectionObserver" in window)) {
      items.forEach((item) => item.classList.add("is-in"));
      return undefined;
    }
    const seen = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-in");
            seen.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 },
    );
    items.forEach((item) => seen.observe(item));
    return () => seen.disconnect();
  }, []);

  function closeMenu() {
    setOpen(false);
  }

  return (
    <div className="lp">
      <a className="lp-skip" href="#main">
        Skip to main content
      </a>
      <header
        className="lp-nav"
        id="lp-nav"
        data-testid="landing-nav"
        data-open={open ? "true" : "false"}
        data-scrolled={scrolled ? "true" : "false"}
      >
        <div className="lp-container lp-nav-inner">
          <Brand />
          <nav className="lp-links" id="lp-links" aria-label="Page sections">
            <a href="#how" data-testid="landing-nav-how" onClick={closeMenu}>
              How it works
            </a>
            <a href="#use-cases" data-testid="landing-nav-use-cases" onClick={closeMenu}>
              Use cases
            </a>
            <a href="#your-ai" data-testid="landing-nav-ai" onClick={closeMenu}>
              Your AI
            </a>
            <a href="#open" data-testid="landing-nav-open" onClick={closeMenu}>
              Open source
            </a>
            <a href="#founder" data-testid="landing-nav-founder" onClick={closeMenu}>
              Founder
            </a>
          </nav>
          <div className="lp-nav-cta">
            <PortfolioButton className="lp-btn lp-btn--primary" testId="landing-nav-cta-button">
              Portfolio
              <Icon name="arrow" size={16} />
            </PortfolioButton>
            <button
              className="lp-menu"
              id="lp-menu"
              type="button"
              aria-expanded={open ? "true" : "false"}
              aria-controls="lp-links"
              aria-label="Open the menu"
              data-testid="landing-menu-toggle"
              onClick={() => setOpen((v) => !v)}
            >
              <Icon name="menu" size={22} />
            </button>
          </div>
        </div>
      </header>

      <main id="main">
        <section className="lp-hero" aria-labelledby="hero-title">
          <div className="lp-container lp-hero-grid">
            <div className="lp-hero-copy">
              <p className="lp-kicker">
                <span className="lp-live" aria-hidden="true" />
                Vision AI for people who run sites, not servers
              </p>
              <h1 className="lp-h1" id="hero-title" data-testid="landing-hero-title">
                Ask your cameras <em>anything.</em>
              </h1>
              <p className="lp-lead">
                Show Athena a photo or a short video and ask a question in plain words. It counts, spots and{" "}
                <strong>explains</strong> — free parking bays, people walking into your store, corrosion on a pipe
                rack — then tells you what to do next. No code. No data science.
              </p>
              <div className="lp-cta-row">
                <PortfolioButton className="lp-btn lp-btn--primary lp-btn--lg" testId="landing-hero-cta-button">
                  Visit the portfolio
                  <Icon name="arrow" size={18} />
                </PortfolioButton>
                <a className="lp-btn lp-btn--line lp-btn--lg" href="#how" data-testid="landing-hero-secondary-button">
                  See how it works
                </a>
              </div>
              <ul className="lp-proof" aria-label="At a glance">
                <li>
                  <Icon name="cpu" size={16} />
                  Runs on open-source <strong>Qwen</strong>, or your own AI
                </li>
                <li>
                  <Icon name="server" size={16} />
                  Self-hosted · Apache-2.0
                </li>
              </ul>
            </div>
            <Demo />
          </div>
        </section>

        <div className="lp-ticker" aria-label="Things people check with Athena">
          <div className="lp-ticker-track">
            {TICKER.map((item) => (
              <span key={item}>{item}</span>
            ))}
            {TICKER.map((item) => (
              <span key={`${item}-dup`} aria-hidden="true">
                {item}
              </span>
            ))}
          </div>
        </div>

        <section className="lp-section lp-why" aria-labelledby="why-title">
          <div className="lp-container">
            <div className="lp-head lp-reveal">
              <p className="lp-eyebrow">
                <b>01</b> Why Athena
              </p>
              <h2 className="lp-h2" id="why-title">
                A detector tells you <span>what</span>.
                <br />
                Athena tells you <em>why it matters.</em>
              </h2>
            </div>
            <div className="lp-compare">
              <article className="lp-compare-card lp-reveal" data-testid="landing-compare-detector">
                <p className="lp-compare-tag">A normal detector (CNN)</p>
                <div className="lp-compare-img lp-compare-img--plain">
                  <img src={pipe} alt="" loading="lazy" width="1400" height="933" />
                  <span className="lp-cbox" data-b="1">
                    <i>rust 0.82</i>
                  </span>
                  <span className="lp-cbox" data-b="2">
                    <i>rust 0.77</i>
                  </span>
                  <span className="lp-cbox" data-b="3">
                    <i>rust 0.61</i>
                  </span>
                </div>
                <ul className="lp-list lp-list--muted">
                  <li>Boxes and a class name. You work out what it means.</li>
                  <li>Needs thousands of labelled pictures before day one.</li>
                  <li>One model per problem, retrained by a specialist.</li>
                </ul>
              </article>
              <article className="lp-compare-card lp-compare-card--athena lp-reveal" data-testid="landing-compare-athena">
                <p className="lp-compare-tag">
                  <span className="lp-live" aria-hidden="true" />
                  Athena with a vision-language model
                </p>
                <div className="lp-compare-img">
                  <img src={pipe} alt="" loading="lazy" width="1400" height="933" />
                  <span className="lp-abox" data-b="1" data-sev="high">
                    <i>1</i>
                  </span>
                  <span className="lp-abox" data-b="2" data-sev="medium">
                    <i>2</i>
                  </span>
                  <span className="lp-abox" data-b="3" data-sev="low">
                    <i>3</i>
                  </span>
                </div>
                <p className="lp-quote">
                  “<strong>Severe scaling on the pipe clamp</strong> (1) — likely trapped moisture under the band.
                  Coating has broken down on the left pipe (2) and is flaking on the right (3). Wire-brush and
                  recoat, then measure wall thickness at the clamp.”
                </p>
                <ul className="lp-list">
                  <li>Plain-language answers with context and next steps.</li>
                  <li>Works from the first picture, with a template or your own question.</li>
                  <li>One assistant for every kind of check.</li>
                </ul>
              </article>
            </div>
          </div>
        </section>

        <section className="lp-section lp-cases" id="use-cases" aria-labelledby="cases-title">
          <div className="lp-container">
            <div className="lp-head lp-head--split lp-reveal">
              <div>
                <p className="lp-eyebrow">
                  <b>02</b> Use cases
                </p>
                <h2 className="lp-h2" id="cases-title">
                  Ready-made checks for the jobs you already do.
                </h2>
              </div>
              <p className="lp-sub">
                Pick one and add a photo — or skip the templates and simply type your question. Every check returns
                counts, marked areas and next steps.
              </p>
            </div>
            <div className="lp-bento">
              <article className="lp-case lp-case--hero lp-reveal" data-testid="landing-case-oil-gas">
                <img src={refinery} alt="A refinery at dusk" loading="lazy" width="1600" height="1068" />
                <div className="lp-case-body">
                  <span className="lp-case-tag">Oil &amp; gas · utilities</span>
                  <h3>Corrosion, leaks &amp; coating damage</h3>
                  <p>
                    Grade rust on pipe racks, flanges and tanks as light, moderate or severe — and get the repair
                    steps an inspector would write.
                  </p>
                  <p className="lp-case-q">“Is there corrosion under the insulation on line 12?”</p>
                </div>
              </article>
              <article className="lp-case lp-reveal" data-testid="landing-case-parking">
                <img src={parking} alt="A car park seen from above" loading="lazy" width="1200" height="800" />
                <div className="lp-case-body">
                  <span className="lp-case-tag">Facilities</span>
                  <h3>Parking occupancy</h3>
                  <p>Free, taken and badly parked — counted from a camera or drone photo.</p>
                </div>
              </article>
              <article className="lp-case lp-reveal" data-testid="landing-case-footfall">
                <img src={store} alt="People at a store entrance" loading="lazy" width="1000" height="641" />
                <div className="lp-case-body">
                  <span className="lp-case-tag">Retail</span>
                  <h3>People in &amp; out</h3>
                  <p>Who is walking in, who is leaving, and how long the queue is.</p>
                </div>
              </article>
              <article className="lp-case lp-reveal" data-testid="landing-case-ppe">
                <img src={safety} alt="A worker in a helmet and high-visibility vest" loading="lazy" width="1000" height="1500" />
                <div className="lp-case-body">
                  <span className="lp-case-tag">Construction · plants</span>
                  <h3>Safety gear</h3>
                  <p>Helmets, vests and glasses — and the hazards around people.</p>
                </div>
              </article>
              <article className="lp-case lp-reveal" data-testid="landing-case-defects">
                <img src={welding} alt="Welding sparks on a metal part" loading="lazy" width="1000" height="667" />
                <div className="lp-case-body">
                  <span className="lp-case-tag">Manufacturing</span>
                  <h3>Surface &amp; weld defects</h3>
                  <p>Cracks, dents, porosity and spatter, with size and location.</p>
                </div>
              </article>
              <article className="lp-case lp-case--text lp-reveal" data-testid="landing-case-custom">
                <span className="lp-case-icon">
                  <Icon name="sparkle" size={24} />
                </span>
                <div className="lp-case-body">
                  <span className="lp-case-tag">Anything else</span>
                  <h3>Your own question</h3>
                  <p>
                    “Are the gauge needles in the green?” “Which shelves need restocking?” If you can photograph it,
                    you can ask it.
                  </p>
                </div>
                <PortfolioButton className="lp-btn lp-btn--primary lp-case-cta" testId="landing-case-custom-cta">
                  Visit the portfolio
                  <Icon name="arrow" size={16} />
                </PortfolioButton>
              </article>
            </div>
          </div>
        </section>

        <section className="lp-section lp-how" id="how" aria-labelledby="how-title">
          <div className="lp-container">
            <div className="lp-head lp-reveal">
              <p className="lp-eyebrow">
                <b>03</b> How it works
              </p>
              <h2 className="lp-h2" id="how-title">
                From photo to report in three steps.
              </h2>
            </div>
            <ol className="lp-steps">
              <li className="lp-step lp-reveal" data-testid="landing-step-1">
                <span className="lp-step-n">01</span>
                <h3>Say what to check</h3>
                <p>
                  Choose a ready-made check like <em>Parking occupancy</em>, or write your question the way you would
                  ask a colleague.
                </p>
                <div className="lp-mini lp-mini--chips" aria-hidden="true">
                  <span data-on="true">Corrosion check</span>
                  <span>Parking</span>
                  <span>Safety gear</span>
                  <span>Your own question…</span>
                </div>
              </li>
              <li className="lp-step lp-reveal" data-testid="landing-step-2">
                <span className="lp-step-n">02</span>
                <h3>Add photos or a video</h3>
                <p>Drop in up to six photos, or a short clip. Athena takes the still frames it needs by itself.</p>
                <div className="lp-mini lp-mini--drop" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                  <b>+</b>
                </div>
              </li>
              <li className="lp-step lp-reveal" data-testid="landing-step-3">
                <span className="lp-step-n">03</span>
                <h3>Read the report</h3>
                <p>A verdict, counts, numbered areas on the picture, and practical next steps. Print it, share it, or rate it.</p>
                <div className="lp-mini lp-mini--report" aria-hidden="true">
                  <b>NEEDS ATTENTION</b>
                  <i />
                  <i />
                  <i />
                </div>
              </li>
            </ol>
          </div>
        </section>

        <section className="lp-section lp-ai" id="your-ai" aria-labelledby="ai-title">
          <div className="lp-container lp-ai-grid">
            <div className="lp-reveal">
              <p className="lp-eyebrow">
                <b>04</b> Your AI, your rules
              </p>
              <h2 className="lp-h2" id="ai-title">
                Open-source Qwen by default. Any AI you like, when you want.
              </h2>
              <p className="lp-sub">
                Athena ships with the open Qwen vision-language model running on your own server, so pictures never
                leave your network. Each person can also plug in their own AI with an address and a key — anything
                that speaks the OpenAI-compatible API.
              </p>
              <ul className="lp-pills" aria-label="Works with">
                <li>Qwen-VL</li>
                <li>OpenAI</li>
                <li>Google Gemini</li>
                <li>OpenRouter</li>
                <li>vLLM</li>
                <li>Ollama</li>
                <li>LM Studio</li>
                <li>Alibaba Cloud</li>
              </ul>
            </div>
            <div className="lp-ai-cards lp-reveal" aria-hidden="true">
              <div className="lp-ai-card lp-ai-card--on">
                <div className="lp-ai-top">
                  <strong>Built-in Qwen</strong>
                  <span className="lp-ai-badge">Default</span>
                </div>
                <p>qwen2.5vl:7b · on your own server</p>
                <span className="lp-ai-status">
                  <i />
                  Answering · 1.8 s
                </span>
              </div>
              <div className="lp-ai-card">
                <div className="lp-ai-top">
                  <strong>Your own AI</strong>
                  <span className="lp-ai-badge lp-ai-badge--line">Optional</span>
                </div>
                <div className="lp-ai-field">
                  <span>Address</span>
                  <code>https://api.openai.com/v1</code>
                </div>
                <div className="lp-ai-field">
                  <span>Model</span>
                  <code>gpt-4o-mini</code>
                </div>
                <div className="lp-ai-field">
                  <span>API key</span>
                  <code>••••••••••••3f9a</code>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="lp-section lp-open" id="open" aria-labelledby="open-title">
          <div className="lp-container">
            <div className="lp-head lp-reveal">
              <p className="lp-eyebrow">
                <b>05</b> Built to be trusted
              </p>
              <h2 className="lp-h2" id="open-title">
                Open source. On your servers. A person decides.
              </h2>
            </div>
            <div className="lp-trust">
              <div className="lp-trust-item lp-reveal">
                <span>
                  <Icon name="code" size={22} />
                </span>
                <h3>Open source</h3>
                <p>Apache-2.0. Read every line, change what you need, no licence fees.</p>
              </div>
              <div className="lp-trust-item lp-reveal">
                <span>
                  <Icon name="server" size={22} />
                </span>
                <h3>Self-hosted</h3>
                <p>One server, your data. Works without any Athena cloud service.</p>
              </div>
              <div className="lp-trust-item lp-reveal">
                <span>
                  <Icon name="shield" size={22} />
                </span>
                <h3>A person decides</h3>
                <p>Reports are a careful first look. People confirm, rate and act on them.</p>
              </div>
              <div className="lp-trust-item lp-reveal">
                <span>
                  <Icon name="detector" size={22} />
                </span>
                <h3>Grow into custom models</h3>
                <p>When a check repeats every day, label pictures and train a dedicated detector — no code.</p>
              </div>
            </div>
          </div>
        </section>

        <section className="lp-section lp-about" id="founder" aria-labelledby="founder-title">
          <div className="lp-container">
            <div className="lp-head lp-reveal">
              <p className="lp-eyebrow">
                <b>06</b> Who is behind this
              </p>
              <h2 className="lp-h2" id="founder-title">
                Founder, Athena Vision, and Inspec-Ther.
              </h2>
            </div>
            <div className="lp-about-grid">
              <article className="lp-about-card lp-reveal">
                <p className="lp-about-kicker">Founder</p>
                <h3>{site.founder.name}</h3>
                <p>{site.founder.summary}</p>
                <p className="lp-about-links">
                  <a href={site.founder.url}>Portfolio</a>
                </p>
              </article>
              <article className="lp-about-card lp-reveal">
                <p className="lp-about-kicker">Athena Vision</p>
                <h3>{site.athenaVision.name}</h3>
                <p>{site.athenaVision.summary}</p>
                <p className="lp-about-links">
                  <a href={site.founder.url}>Portfolio</a>
                </p>
              </article>
              <article className="lp-about-card lp-reveal">
                <p className="lp-about-kicker">Inspec-Ther</p>
                <h3>{site.inspecTher.name}</h3>
                <p>
                  {site.inspecTher.name} is {site.inspecTher.description.replace(/\.$/, "")}.{" "}
                  {site.inspecTher.modelNote}
                </p>
                <p className="lp-about-links">
                  <a href={site.inspecTher.url}>{site.inspecTher.name}</a>
                  <a href={site.founder.url}>Portfolio</a>
                </p>
              </article>
            </div>
          </div>
        </section>

        <section className="lp-section lp-faq" id="faq" aria-labelledby="faq-title">
          <div className="lp-container">
            <div className="lp-head lp-reveal">
              <p className="lp-eyebrow">
                <b>07</b> Questions
              </p>
              <h2 className="lp-h2" id="faq-title">
                Straight answers.
              </h2>
            </div>
            <div className="lp-faq-list">
              {site.faq.map((item) => (
                <details key={item.q} className="lp-faq-item lp-reveal">
                  <summary>
                    <h3>{item.q}</h3>
                  </summary>
                  <p>{item.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-final" aria-labelledby="final-title">
          <div className="lp-container lp-final-inner lp-reveal">
            <h2 className="lp-h2" id="final-title">
              Your first report in about a minute.
            </h2>
            <p className="lp-sub">See the work, the models, and the products on the portfolio.</p>
            <div className="lp-cta-row">
              <PortfolioButton className="lp-btn lp-btn--primary lp-btn--lg" testId="landing-final-cta-button">
                Visit the portfolio
                <Icon name="arrow" size={18} />
              </PortfolioButton>
              <a className="lp-btn lp-btn--line lp-btn--lg" href="#how" data-testid="landing-final-how">
                See how it works
              </a>
            </div>
            <p className="lp-fine">Apache-2.0. No Athena cloud, and no account on this page — the console is still being prepared for open source.</p>
          </div>
        </section>
      </main>

      <footer className="lp-foot">
        <div className="lp-container lp-foot-inner">
          <Brand />
          <p>
            Open-source computer vision for operations teams. Built by{" "}
            <a href={site.founder.url}>{site.founder.name}</a>. The solar inspection product is{" "}
            <a href={site.inspecTher.url}>{site.inspecTher.name}</a>. Code under Apache-2.0; model
            weights carry their own terms.
          </p>
        </div>
      </footer>
    </div>
  );
}
