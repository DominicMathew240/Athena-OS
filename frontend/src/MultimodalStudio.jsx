import { useEffect, useRef, useState } from "react";
import { Icon } from "./icons.jsx";
import {
  FUSION_SCENARIOS,
  GALLERY_ITEMS,
  LIFECYCLE_RESOURCES,
  OUTCOMES,
  OUTCOME_TABS,
  PILLARS,
  STAGES,
  WAVES,
} from "./studioData.js";
import { drawFusionDiagram, drawMiniThumb, drawStudio } from "./studioDrawers.js";
import weldingImg from "./assets/landing/welding.jpg";
import workerImg from "./assets/landing/safety-worker.jpg";

export function MultimodalStudio({ portfolioUrl }) {
  const [outcome, setOutcome] = useState("audio");
  const [stage, setStage] = useState("label");
  const [sampleIdx, setSampleIdx] = useState(1);
  const [fusion, setFusion] = useState("motor");
  const [imgVersion, setImgVersion] = useState(0);

  const shellRef = useRef(null);
  const visCanvasRef = useRef(null);
  const fusionCanvasRef = useRef(null);
  const miniRefs = useRef({});
  const tabRefs = useRef({});
  const animPhaseRef = useRef(0);
  const studioActiveRef = useRef(true);
  const imagesRef = useRef({});

  useEffect(() => {
    const entries = [
      ["welding", weldingImg],
      ["worker", workerImg],
    ];
    entries.forEach(([key, src]) => {
      const pic = new Image();
      pic.src = src;
      pic.addEventListener("load", () => setImgVersion((v) => v + 1));
      imagesRef.current[key] = pic;
    });
  }, []);

  function selectOutcome(nextKey, moveFocus = false) {
    if (!OUTCOMES[nextKey]) return;
    setOutcome(nextKey);
    setSampleIdx(nextKey === "audio" ? 1 : 0);
    if (moveFocus) {
      tabRefs.current[nextKey]?.focus();
    }
  }

  useEffect(() => {
    drawStudio(visCanvasRef.current, outcome, stage, sampleIdx, animPhaseRef.current, imagesRef.current);
  }, [outcome, stage, sampleIdx, imgVersion]);

  useEffect(() => {
    GALLERY_ITEMS.forEach(({ key }) => {
      drawMiniThumb(miniRefs.current[key], key, imagesRef.current);
    });
  }, [imgVersion]);

  useEffect(() => {
    drawFusionDiagram(fusionCanvasRef.current, fusion);
  }, [fusion]);

  useEffect(() => {
    let lastDpr = window.devicePixelRatio || 1;
    const onResize = () => {
      const next = window.devicePixelRatio || 1;
      if (next !== lastDpr) {
        lastDpr = next;
        drawStudio(visCanvasRef.current, outcome, stage, sampleIdx, animPhaseRef.current, imagesRef.current);
        GALLERY_ITEMS.forEach(({ key }) => drawMiniThumb(miniRefs.current[key], key, imagesRef.current));
        drawFusionDiagram(fusionCanvasRef.current, fusion);
      }
    };
    window.addEventListener("resize", onResize, { passive: true });
    return () => window.removeEventListener("resize", onResize);
  }, [outcome, stage, sampleIdx, fusion]);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return undefined;
    let rafId = null;
    let lastTick = 0;

    const tick = (now) => {
      if (!studioActiveRef.current) {
        rafId = null;
        return;
      }
      if (now - lastTick > 70) {
        lastTick = now;
        animPhaseRef.current += 0.14;
        if (outcome === "audio") {
          drawStudio(visCanvasRef.current, outcome, stage, sampleIdx, animPhaseRef.current, imagesRef.current);
        }
      }
      rafId = requestAnimationFrame(tick);
    };

    const start = () => {
      if (rafId === null && studioActiveRef.current) rafId = requestAnimationFrame(tick);
    };
    const stop = () => {
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
    };

    const shell = shellRef.current;
    const onCv = (ev) => {
      studioActiveRef.current = !ev.skipped;
      if (studioActiveRef.current) start();
      else stop();
    };
    let io = null;
    if (shell) {
      shell.addEventListener("contentvisibilityautostatechange", onCv);
      if (!("contentVisibility" in document.documentElement.style) && "IntersectionObserver" in window) {
        io = new IntersectionObserver(
          (list) => {
            studioActiveRef.current = list.some((e) => e.isIntersecting);
            if (studioActiveRef.current) start();
            else stop();
          },
          { rootMargin: "200px" },
        );
        io.observe(shell);
      }
    }
    start();
    return () => {
      stop();
      if (shell) shell.removeEventListener("contentvisibilityautostatechange", onCv);
      if (io) io.disconnect();
    };
  }, [outcome, stage, sampleIdx]);

  function onTabKeyDown(event, idx) {
    let next = -1;
    if (event.key === "ArrowRight") next = (idx + 1) % OUTCOME_TABS.length;
    else if (event.key === "ArrowLeft") next = (idx - 1 + OUTCOME_TABS.length) % OUTCOME_TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = OUTCOME_TABS.length - 1;
    if (next >= 0) {
      event.preventDefault();
      selectOutcome(OUTCOME_TABS[next].key, true);
    }
  }

  function handleCanvasClick(event) {
    const canvas = visCanvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const nx = (event.clientX - rect.left) / rect.width;
    const ny = (event.clientY - rect.top) / rect.height;
    const list = OUTCOMES[outcome]?.samples || [];
    const hit = list.findIndex(({ rect: [rx, ry, rw, rh] }) => nx >= rx && nx <= rx + rw && ny >= ry && ny <= ry + rh);
    if (hit >= 0) {
      setSampleIdx(hit);
      if (stage === "raw") setStage("label");
    }
  }

  const data = OUTCOMES[outcome];
  const sample = data.samples[sampleIdx] || data.samples[0];
  const metrics = data.stageMetrics[stage] || data.stageMetrics.eval;
  const fusionData = FUSION_SCENARIOS[fusion];

  return (
    <>
      <section className="lp-section lp-teach" id="teach-workflow" aria-labelledby="teach-title" data-testid="landing-teach-workflow">
        <div className="lp-container">
          <div className="lp-head lp-head--split lp-reveal">
            <div>
              <p className="lp-eyebrow">
                <b>04</b> ATHENA OS · NEW MODEL
              </p>
              <h2 className="lp-h2" id="teach-title">
                What do you want Athena to <em>learn?</em>
              </h2>
            </div>
            <p className="lp-sub">Inspect the training data, labeling flow, and live solution for every modality.</p>
          </div>

          <div className="lp-outcome-shell lp-reveal" id="lp-outcome-shell" ref={shellRef}>
            <div className="lp-outcome-tabs" id="lp-outcome-tabs" role="tablist" aria-label="What do you want Athena to learn?">
              {OUTCOME_TABS.map((t, i) => (
                <button
                  key={t.key}
                  ref={(el) => { tabRefs.current[t.key] = el; }}
                  type="button"
                  role="tab"
                  className="lp-outcome-tab"
                  id={`lp-outcome-tab-${t.key}`}
                  aria-selected={outcome === t.key}
                  tabIndex={outcome === t.key ? 0 : -1}
                  data-outcome={t.key}
                  data-testid={`landing-outcome-${t.key}`}
                  onClick={() => selectOutcome(t.key, false)}
                  onKeyDown={(ev) => onTabKeyDown(ev, i)}
                >
                  <Icon name={t.icon} size={16} />
                  <span>{t.label}</span>
                </button>
              ))}
            </div>

            <div className="lp-outcome-panel" id="lp-outcome-panel" role="tabpanel" aria-labelledby={`lp-outcome-tab-${outcome}`} tabIndex={0} data-testid="landing-outcome-card">
              <div className="lp-studio-top">
                <div className="lp-studio-title">
                  <span className="lp-outcome-badge" id="lp-outcome-badge">{data.badge}</span>
                  <h3 id="lp-outcome-title">{data.title}</h3>
                  <span className="lp-outcome-status" id="lp-outcome-status">{data.status}</span>
                </div>
                <div className="lp-stage-tabs" id="lp-stage-tabs" role="group" aria-label="Inspect training pipeline stages" data-testid="landing-stage-stepper">
                  {[
                    ["raw", "01", "Raw"],
                    ["label", "02", "Label"],
                    ["eval", "03", "Check"],
                    ["deploy", "04", "Deploy"],
                  ].map(([k, n, lbl]) => (
                    <button
                      key={k}
                      type="button"
                      className="lp-stage-btn"
                      data-stage={k}
                      aria-pressed={stage === k}
                      data-testid={`landing-stage-${k}`}
                      onClick={() => setStage(k)}
                    >
                      <b>{n}</b>
                      <span>{lbl}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="lp-studio-visualizer" id="lp-studio-visualizer" data-testid="landing-multimodal-visualizer">
                <div className="lp-vis-bar">
                  <span className="lp-dots" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                  <span className="lp-vis-filename" id="lp-vis-filename">{data.filename}</span>
                  <div className="lp-vis-samples" id="lp-vis-samples" role="group" aria-label="Inspect training examples and annotations">
                    {data.samples.map((s, idx) => (
                      <button
                        key={s.label}
                        type="button"
                        className="lp-vis-sample"
                        data-sample={idx}
                        aria-pressed={sampleIdx === idx}
                        onClick={() => setSampleIdx(idx)}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                  <span className="lp-vis-mode" id="lp-vis-mode">{STAGES[stage]}</span>
                </div>
                <div className="lp-vis-stage">
                  <canvas
                    id="lp-vis-canvas"
                    ref={visCanvasRef}
                    width={960}
                    height={380}
                    onClick={handleCanvasClick}
                    aria-label="Interactive multimodal training data and solution preview — click any stage or sample region"
                  />
                </div>
                <div className="lp-vis-hud" id="lp-vis-inspector" aria-live="polite" data-testid="landing-vis-inspector">
                  <div className="lp-vis-hud-text">
                    <strong id="lp-vis-selection-title">{sample.title}</strong>
                    <p id="lp-vis-selection-detail">{sample.detail}</p>
                  </div>
                  <div className="lp-vis-metrics" id="lp-vis-metrics">
                    {metrics.map(([val, lbl]) => (
                      <div key={lbl}>
                        <b>{val}</b>
                        <span>{lbl}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="lp-studio-meta">
                <dl className="lp-outcome-specs">
                  <div>
                    <dt>Data needed</dt>
                    <dd id="lp-outcome-data">{data.data}</dd>
                  </div>
                  <div>
                    <dt>Quality check</dt>
                    <dd id="lp-outcome-quality">{data.quality}</dd>
                  </div>
                  <div>
                    <dt>Recipe</dt>
                    <dd id="lp-outcome-recipe">{data.recipe}</dd>
                  </div>
                </dl>
                <div className="lp-outcome-foot">
                  <span className="lp-vis-split" id="lp-vis-split">{data.split}</span>
                  <a className="lp-btn lp-btn--primary" id="lp-outcome-cta" href={portfolioUrl} data-testid="landing-outcome-explore">
                    Explore on portfolio
                    <Icon name="arrow" size={16} />
                  </a>
                </div>
              </div>
            </div>
          </div>

          <div className="lp-mod-gallery lp-reveal" id="lp-modality-gallery" data-testid="landing-modality-gallery" aria-label="Visual glimpse across all six modalities">
            {GALLERY_ITEMS.map((item) => (
              <button
                key={item.key}
                type="button"
                className="lp-mod-card"
                data-jump-outcome={item.key}
                aria-pressed={outcome === item.key}
                onClick={() => {
                  selectOutcome(item.key, false);
                  shellRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
                }}
              >
                <canvas
                  className="lp-mini-canvas"
                  ref={(el) => { miniRefs.current[item.key] = el; }}
                  width={320}
                  height={145}
                  data-mini-modality={item.key}
                  aria-hidden="true"
                />
                <div className="lp-mod-card-foot">
                  <span className={`lp-prio lp-prio--${item.pClass}`}>{item.prio}</span>
                  <strong>{item.name}</strong>
                  <span>{item.sub}</span>
                </div>
              </button>
            ))}
          </div>

          <ol className="lp-six-steps lp-reveal" data-testid="landing-six-steps" aria-label="Six consistent training steps across all modalities">
            <li className="lp-six-step"><span className="lp-step-n">01</span><h3>Goal</h3><p>Pick a recipe.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">02</span><h3>Data</h3><p>Add samples.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">03</span><h3>Label</h3><p>Mark regions.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">04</span><h3>Train</h3><p>Tracked runs.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">05</span><h3>Verify</h3><p>Real errors.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">06</span><h3>Deploy</h3><p>Approve &amp; retrain.</p></li>
          </ol>
        </div>
      </section>

      <section className="lp-section lp-roadmap" id="roadmap" aria-labelledby="roadmap-title" data-testid="landing-roadmap-section">
        <div className="lp-container">
          <div className="lp-head lp-head--split lp-reveal">
            <div>
              <p className="lp-eyebrow">
                <b>05</b> Architecture &amp; roadmap
              </p>
              <h2 className="lp-h2" id="roadmap-title">
                One shared engine across <em>six modalities.</em>
              </h2>
            </div>
            <p className="lp-sub">Open-source lifecycle on your CPU/GPU or Athena Nodes.</p>
          </div>

          <div className="lp-arch lp-reveal" aria-label="Proposed Athena OS architecture">
            <div className="lp-arch-pillars">
              {PILLARS.map((p) => (
                <div key={p.title} className="lp-pillar">
                  <span className={`lp-prio lp-prio--${p.pClass}`}>{p.prio}</span>
                  <strong>{p.title}</strong>
                  <span>{p.sub}</span>
                </div>
              ))}
            </div>
            <div className="lp-arch-layer lp-arch-layer--engine">
              <p className="lp-arch-label">Shared Lifecycle</p>
              <div className="lp-lifecycle-chips" aria-label="Shared training lifecycle resources">
                {LIFECYCLE_RESOURCES.map((res) => (
                  <span key={res}>{res}</span>
                ))}
              </div>
            </div>
          </div>

          <div className="lp-waves lp-reveal" aria-label="Proposed delivery waves (indicative sequence, not committed dates)">
            {WAVES.map((w) => (
              <article key={w.tag} className={`lp-wave${w.focus ? " lp-wave--focus" : ""}`}>
                <div className="lp-wave-top">
                  <span className="lp-wave-tag">{w.tag}</span>
                  <span className="lp-wave-win">{w.win}</span>
                </div>
                <h3>{w.title}</h3>
                <p><code>{w.code}</code></p>
              </article>
            ))}
          </div>

          <div className="lp-fusion-showcase lp-reveal" id="lp-fusion-showcase" data-testid="landing-fusion-showcase">
            <div className="lp-fusion-head">
              <div>
                <p className="lp-arch-label">Wave 4 · Cross-modal fusion</p>
                <h3 className="lp-fusion-title">Multiple streams converge</h3>
              </div>
              <div className="lp-fusion-tabs" id="lp-fusion-tabs" role="group" aria-label="Preview Wave 4 multimodal fusion pipelines">
                {[
                  ["motor", "Audio + Vibration + Temp"],
                  ["safety", "Video + Pose + Machine"],
                  ["thermal", "Thermal + RGB + Asset"],
                ].map(([k, lbl]) => (
                  <button
                    key={k}
                    type="button"
                    className="lp-fusion-tab"
                    data-fusion={k}
                    aria-pressed={fusion === k}
                    onClick={() => setFusion(k)}
                  >
                    {lbl}
                  </button>
                ))}
              </div>
            </div>

            <div className="lp-fusion-diagram" id="lp-fusion-diagram" aria-live="polite">
              <div className="lp-vis-stage">
                <canvas
                  id="lp-fusion-canvas"
                  ref={fusionCanvasRef}
                  width={880}
                  height={210}
                  aria-label="Interactive cross-modal fusion signal convergence diagram"
                />
              </div>
              <div className="lp-fusion-merge">
                <span className="lp-fusion-badge" id="lp-fusion-badge">{fusionData.badge}</span>
                <strong id="lp-fusion-verdict">{fusionData.verdict}</strong>
              </div>
            </div>
          </div>

          <div className="lp-roadmap-foot">
            <p className="lp-roadmap-note">Indicative sequence, not committed release dates.</p>
            <a className="lp-btn lp-btn--outline lp-btn--sm" href="#docs">
              Open Docs reference
              <Icon name="arrow" size={15} />
            </a>
          </div>
        </div>
      </section>
    </>
  );
}
