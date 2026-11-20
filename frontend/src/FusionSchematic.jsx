import pipeImg from "./assets/landing/pipe-corrosion.jpg";
import parkingImg from "./assets/landing/parking-aerial.jpg";
import { RealAudioWaveform, SignalMultiTrace } from "./ModalityGraphics.jsx";

function StreamGraphic({ stream }) {
  if (stream.graphic === "audio") {
    return (
      <div className="lp-fs-gfx" aria-hidden="true">
        <RealAudioWaveform variant="fault" compact />
      </div>
    );
  }
  if (stream.graphic === "signal") {
    return (
      <div className="lp-fs-gfx" aria-hidden="true">
        <SignalMultiTrace mode={stream.mode || "imbalance"} sev={stream.sev || "high"} compact />
      </div>
    );
  }
  if (stream.graphic === "photo") {
    return (
      <div className="lp-fs-gfx lp-fs-gfx--photo" aria-hidden="true">
        <img src={pipeImg} alt="" loading="lazy" width={280} height={140} />
        <span className="lp-fs-box" style={{ left: "34%", top: "24%", width: "54%", height: "42%" }}><i>Rust</i></span>
      </div>
    );
  }
  if (stream.graphic === "thermal") {
    return (
      <div className="lp-fs-gfx lp-fs-gfx--thermal" aria-hidden="true">
        <img src={pipeImg} alt="" loading="lazy" width={280} height={140} />
        <span className="lp-fs-hotspot" />
        <span className="lp-fs-temp-tag">+28 °C</span>
      </div>
    );
  }
  if (stream.graphic === "parking") {
    return (
      <div className="lp-fs-gfx lp-fs-gfx--photo" aria-hidden="true">
        <img src={parkingImg} alt="" loading="lazy" width={280} height={140} />
        <span className="lp-fs-box lp-fs-box--ok" style={{ left: "38%", top: "48%", width: "22%", height: "34%" }}><i>30 free</i></span>
      </div>
    );
  }
  if (stream.graphic === "plc") {
    return (
      <div className="lp-fs-gfx lp-fs-gfx--plc" aria-hidden="true">
        <span className="lp-plc-state">
          <i />
          GATE OPEN
        </span>
        <span className="lp-plc-sub">North bay entry active</span>
      </div>
    );
  }
  return (
    <div className="lp-fs-gfx lp-fs-gfx--gauge" aria-hidden="true">
      <div className="lp-fs-gauge-bar">
        <span style={{ width: `${stream.pct || 72}%` }} data-sev={stream.sev} />
      </div>
      <div className="lp-fs-gauge-scale">
        <span>Safe</span>
        <span>Watch</span>
        <span>Alert</span>
      </div>
    </div>
  );
}

export function FusionSchematic({ scenario }) {
  return (
    <div className="lp-site-monitor" id="lp-fusion-diagram" aria-live="polite">
      <div className="lp-monitor-bar">
        <strong className="lp-monitor-title">{scenario.siteTitle}</strong>
        <span className="lp-monitor-edge" id="lp-fusion-badge">{scenario.edgeNode}</span>
      </div>

      <div className="lp-monitor-grid">
        <div className="lp-monitor-inputs" aria-label="What Athena is checking on site">
          <p className="lp-monitor-subhead">1 · What Athena is checking on site</p>
          {scenario.streams.map((stream) => (
            <div key={stream.kind} className="lp-monitor-feed">
              <div className="lp-monitor-feed-text">
                <span className="lp-fusion-kind">{stream.kind}</span>
                <strong>{stream.title}</strong>
                <span className="lp-fusion-val">{stream.val}</span>
              </div>
              <StreamGraphic stream={stream} />
            </div>
          ))}
        </div>

        <div className="lp-monitor-decision">
          <p className="lp-monitor-subhead">2 · Clear answer for your team</p>
          <div className="lp-monitor-verdict-card">
            <span className={`lp-verdict`} data-v={scenario.verdictTone === "bad" ? "attention" : "attention"}>
              {scenario.verdictBadge}
            </span>
            <strong className="lp-fusion-verdict" id="lp-fusion-verdict">{scenario.verdict}</strong>
            <p className="lp-fusion-why">{scenario.whyUseful}</p>

            <ul className="lp-monitor-checks">
              {scenario.checks.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>

            <div className="lp-monitor-resilience">
              <strong>If a sensor drops out</strong>
              <p>{scenario.fallbackNote}</p>
            </div>

            <div className="lp-monitor-foot">
              <span>{scenario.actionLabel}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
