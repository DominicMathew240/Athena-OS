import { useRef, useState } from "react";
import { Icon } from "./icons.jsx";
import {
  FUSION_SCENARIOS,
  GALLERY_ITEMS,
  LIFECYCLE_RESOURCES,
  OUTCOMES,
  OUTCOME_TABS,
  PILLARS,
  STAGES,
  STAGE_LIST,
  VISION_WORKFLOW_HIGHLIGHTS,
  WAVES,
} from "./studioData.js";
import { StudioPreview } from "./StudioPreview.jsx";
import { FusionSchematic } from "./FusionSchematic.jsx";
import { ModalityThumb } from "./ModalityGraphics.jsx";

export function MultimodalStudio({ portfolioUrl }) {
  const [outcome, setOutcome] = useState("vision");
  const [stage, setStage] = useState("label");
  const [sampleIdx, setSampleIdx] = useState(0);
  const [fusion, setFusion] = useState("motor");

  const shellRef = useRef(null);
  const tabRefs = useRef({});

  function selectOutcome(nextKey, moveFocus = false) {
    if (!OUTCOMES[nextKey]) return;
    setOutcome(nextKey);
    setSampleIdx(0);
    const tabEl = tabRefs.current[nextKey];
    if (tabEl) {
      tabEl.scrollIntoView({ block: "nearest", inline: "nearest" });
      if (moveFocus) tabEl.focus();
    }
  }

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

  const data = OUTCOMES[outcome];
  const fusionData = FUSION_SCENARIOS[fusion];

  return (
    <>
      <section className="lp-section lp-teach" id="teach-workflow" aria-labelledby="teach-title" data-testid="landing-teach-workflow">
        <div className="lp-container">
          <div className="lp-head lp-head--split lp-reveal">
            <div>
              <p className="lp-eyebrow">
                <b>04</b> Console recipes · 6 merged
              </p>
              <h2 className="lp-h2" id="teach-title">
                What do you want Athena to <em>learn?</em>
              </h2>
            </div>
            <p className="lp-sub">One guided Project to Approve lifecycle across Vision, Audio, Handwriting, Spreadsheets, Text, and Signals — all synthetic-verified on main.</p>
          </div>

          <div className="lp-outcome-shell lp-reveal" id="lp-outcome-shell" ref={shellRef}>
            <div className="lp-outcome-tabs" id="lp-outcome-tabs" role="tablist" aria-label="Training recipes">
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
                  <span className="lp-outcome-status" data-tone={data.statusTone} id="lp-outcome-status">{data.status}</span>
                </div>
                <div className="lp-stage-tabs" id="lp-stage-tabs" role="group" aria-label="Inspect training pipeline stages" data-testid="landing-stage-stepper">
                  {STAGE_LIST.map((st) => (
                    <button
                      key={st.key}
                      type="button"
                      className="lp-stage-btn"
                      data-stage={st.key}
                      aria-pressed={stage === st.key}
                      data-testid={`landing-stage-${st.key}`}
                      onClick={() => setStage(st.key)}
                    >
                      <b>{st.num}</b>
                      <span>{st.label}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="lp-studio-visualizer" id="lp-studio-visualizer">
                <div className="lp-vis-bar">
                  <span className="lp-vis-mode" id="lp-vis-mode">{STAGES[stage]}</span>
                  <span className="lp-vis-recipe-id">{data.recipeKey}</span>
                  <span className="lp-vis-split-pill">{data.split}</span>
                </div>
                <StudioPreview
                  outcome={outcome}
                  stage={stage}
                  sampleIdx={sampleIdx}
                  onSelectSample={(idx) => {
                    setSampleIdx(idx);
                    if (stage === "raw") setStage("label");
                  }}
                  data={data}
                />
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
                    <dt>Recipe &amp; backend</dt>
                    <dd id="lp-outcome-recipe">{data.recipe}</dd>
                  </div>
                </dl>
                <div className="lp-outcome-foot">
                  <span className="lp-vis-split" id="lp-vis-split">Synthetic-verified · Real-world quality unmeasured</span>
                  <a className="lp-btn lp-btn--primary" id="lp-outcome-cta" href={portfolioUrl} data-testid="landing-outcome-explore">
                    Explore on portfolio
                    <Icon name="arrow" size={16} />
                  </a>
                </div>
              </div>
            </div>
          </div>

          <div className="lp-mod-gallery lp-reveal" id="lp-modality-gallery" data-testid="landing-modality-gallery" aria-label="Merged training recipes in Athena console">
            {GALLERY_ITEMS.map((item) => (
              <button
                key={item.key}
                type="button"
                className="lp-mod-card"
                data-jump-outcome={item.key}
                aria-pressed={outcome === item.key}
                onClick={() => {
                  selectOutcome(item.key, false);
                  shellRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
                }}
              >
                <ModalityThumb modality={item.key} />
                <div className="lp-mod-card-top">
                  <span className={`lp-prio lp-prio--${item.tone}`}>{item.prio}</span>
                  <code className="lp-mod-backend">{item.sub}</code>
                </div>
                <strong>{item.name}</strong>
                <span className="lp-mod-detail">{item.detail}</span>
              </button>
            ))}
          </div>

          <ol className="lp-six-steps lp-reveal" data-testid="landing-six-steps" aria-label="Six consistent training steps across all modalities">
            <li className="lp-six-step"><span className="lp-step-n">01</span><h3>Goal</h3><p>Pick a TaskRecipe.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">02</span><h3>Data</h3><p>Upload &amp; SHA-256 check.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">03</span><h3>Label</h3><p>Review before freeze.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">04</span><h3>Snapshot</h3><p>Immutable 80/10/10.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">05</span><h3>Train</h3><p>Worker + held-out eval.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">06</span><h3>Approve</h3><p>Gate, check &amp; rollback.</p></li>
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
                One shared engine across <em>every modality.</em>
              </h2>
            </div>
            <p className="lp-sub">Registry-driven recipes and modality plugins share one frozen snapshot, worker contract, and approval lifecycle.</p>
          </div>

          <div className="lp-arch lp-reveal" aria-label="Athena OS multimodal architecture">
            <div className="lp-arch-pillars">
              {PILLARS.map((p) => (
                <div key={p.title} className="lp-pillar">
                  <span className={`lp-prio lp-prio--${p.tone}`}>{p.prio}</span>
                  <strong>{p.title}</strong>
                  <span>{p.sub}</span>
                </div>
              ))}
            </div>
            <div className="lp-arch-layer lp-arch-layer--engine">
              <p className="lp-arch-label">Shared Lifecycle Contract (Wave 0 Complete)</p>
              <div className="lp-lifecycle-chips" aria-label="Shared training lifecycle resources">
                {LIFECYCLE_RESOURCES.map((res) => (
                  <span key={res}>{res}</span>
                ))}
              </div>
            </div>
          </div>

          <div className="lp-waves lp-reveal" aria-label="Delivery waves and current merge status">
            {WAVES.map((w) => (
              <article key={w.tag} className={`lp-wave${w.focus ? " lp-wave--focus" : ""}`}>
                <div className="lp-wave-top">
                  <span className="lp-wave-tag">{w.tag}</span>
                  <span className={`lp-prio lp-prio--${w.tone}`}>{w.state}</span>
                </div>
                <h3>{w.title}</h3>
                <p><code>{w.code}</code></p>
              </article>
            ))}
          </div>

          <div className="lp-vision-ops lp-reveal" aria-label="Vision operations workflow status">
            <div className="lp-vision-ops-head">
              <div>
                <p className="lp-arch-label">Vision operations workstream · 18 of 65 tasks locally verified</p>
                <h3>Guided setup, view health, frame replay &amp; bound tracking</h3>
              </div>
              <span className="lp-outcome-status" data-tone="warn">Live cameras (T10) &amp; crossings (T25) remain open</span>
            </div>
            <div className="lp-vision-ops-grid">
              {VISION_WORKFLOW_HIGHLIGHTS.map((item) => (
                <div key={item.id} className="lp-vision-op-item">
                  <div className="lp-vision-op-top">
                    <code>{item.id}</code>
                    <span>{item.state}</span>
                  </div>
                  <p>{item.label}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="lp-fusion-showcase lp-reveal" id="lp-fusion-showcase" data-testid="landing-fusion-showcase">
            <div className="lp-fusion-head">
              <div>
                <p className="lp-arch-label">Wave 4 · Architecture concept</p>
                <h3 className="lp-fusion-title">Cross-modal stream convergence</h3>
              </div>
              <div className="lp-fusion-tabs" id="lp-fusion-tabs" role="group" aria-label="Preview Wave 4 multimodal fusion pipelines">
                {[
                  ["motor", "Audio + Signal + Table"],
                  ["safety", "Video + Pose + PLC"],
                  ["thermal", "Thermal + RGB + Log"],
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

            <FusionSchematic scenario={fusionData} />
          </div>

          <div className="lp-roadmap-foot">
            <p className="lp-roadmap-note">Merged status reflects main on 20 November 2026 (synthetic-verified; real-data vertical &amp; usability checks pending).</p>
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
