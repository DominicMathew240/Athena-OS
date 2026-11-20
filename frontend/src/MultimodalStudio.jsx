import { useRef, useState } from "react";
import { Icon } from "./icons.jsx";
import {
  EDGE_BENEFITS,
  FUSION_SCENARIOS,
  GALLERY_ITEMS,
  LIFECYCLE_RESOURCES,
  OUTCOMES,
  OUTCOME_TABS,
  STAGES,
  STAGE_LIST,
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
                <b>04</b> Training studio
              </p>
              <h2 className="lp-h2" id="teach-title">
                What do you want Athena to <em>learn?</em>
              </h2>
            </div>
            <p className="lp-sub">One guided workflow across photos, audio clips, handwriting, spreadsheets, text logs, and sensor signals — no coding required.</p>
          </div>

          <div className="lp-outcome-shell lp-reveal" id="lp-outcome-shell" ref={shellRef}>
            <div className="lp-outcome-tabs" id="lp-outcome-tabs" role="tablist" aria-label="Training data types">
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
                  <span className="lp-vis-recipe-id">{data.backend}</span>
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
                    <dt>How it works</dt>
                    <dd id="lp-outcome-recipe">{data.recipe}</dd>
                  </div>
                </dl>
                <div className="lp-outcome-foot">
                  <span className="lp-vis-split" id="lp-vis-split">Tested on synthetic data · Human approval before live use</span>
                  <a className="lp-btn lp-btn--primary" id="lp-outcome-cta" href={portfolioUrl} data-testid="landing-outcome-explore">
                    Explore on portfolio
                    <Icon name="arrow" size={16} />
                  </a>
                </div>
              </div>
            </div>
          </div>

          <div className="lp-mod-gallery lp-reveal" id="lp-modality-gallery" data-testid="landing-modality-gallery" aria-label="All six training workflows in Athena OS">
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

          <ol className="lp-six-steps lp-reveal" data-testid="landing-six-steps" aria-label="Consistent training steps across all data types">
            <li className="lp-six-step"><span className="lp-step-n">01</span><h3>Goal</h3><p>Pick a workflow.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">02</span><h3>Data</h3><p>Add &amp; verify files.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">03</span><h3>Review</h3><p>Accept labels first.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">04</span><h3>Snapshot</h3><p>Lock 80/10/10 split.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">05</span><h3>Train</h3><p>Check real errors.</p></li>
            <li className="lp-six-step"><span className="lp-step-n">06</span><h3>Approve</h3><p>Sign off or roll back.</p></li>
          </ol>
        </div>
      </section>

      <section className="lp-section lp-roadmap" id="roadmap" aria-labelledby="roadmap-title" data-testid="landing-roadmap-section">
        <div className="lp-container">
          <div className="lp-head lp-head--split lp-reveal">
            <div>
              <p className="lp-eyebrow">
                <b>05</b> Combined site checks &amp; offline edge
              </p>
              <h2 className="lp-h2" id="roadmap-title">
                Combine everything. Run <em>right on site.</em>
              </h2>
            </div>
            <p className="lp-sub">A single camera or sensor can give false alarms. Combining photos, sounds, heat maps, and machine gauges on your own local computer gives your team clear answers — even without internet.</p>
          </div>

          <div className="lp-fusion-showcase lp-reveal" id="lp-fusion-showcase" data-testid="landing-fusion-showcase">
            <div className="lp-fusion-head">
              <div>
                <p className="lp-arch-label">Example operator screen</p>
                <h3 className="lp-fusion-title">Pick a real-world site check</h3>
              </div>
              <div className="lp-fusion-tabs" id="lp-fusion-tabs" role="group" aria-label="Preview real-world site checks">
                {Object.values(FUSION_SCENARIOS).map((sc) => (
                  <button
                    key={sc.id}
                    type="button"
                    className="lp-fusion-tab"
                    data-fusion={sc.id}
                    aria-pressed={fusion === sc.id}
                    onClick={() => setFusion(sc.id)}
                  >
                    {sc.tab}
                  </button>
                ))}
              </div>
            </div>

            <FusionSchematic scenario={fusionData} />
          </div>

          <div className="lp-edge-strip lp-reveal" aria-label="How edge deployment works on site">
            {EDGE_BENEFITS.map((item) => (
              <article key={item.step} className="lp-edge-card">
                <span className="lp-edge-step">{item.step}</span>
                <h3>{item.title}</h3>
                <p>{item.detail}</p>
              </article>
            ))}
          </div>

          <div className="lp-arch lp-reveal" aria-label="Simple lifecycle from upload to daily use">
            <div className="lp-arch-layer lp-arch-layer--engine">
              <p className="lp-arch-label">The same 8 plain-English steps for every project</p>
              <div className="lp-lifecycle-chips" aria-label="Shared steps">
                {LIFECYCLE_RESOURCES.map((res) => (
                  <span key={res}>{res}</span>
                ))}
              </div>
            </div>
          </div>

          <div className="lp-roadmap-foot">
            <p className="lp-roadmap-note">Single-data-type training runs today; multi-sensor site monitors are currently being built on top of it.</p>
            <a className="lp-btn lp-btn--outline lp-btn--sm" href="#docs">
              Read the quick guide
              <Icon name="arrow" size={15} />
            </a>
          </div>
        </div>
      </section>
    </>
  );
}
