import pipeImg from "./assets/landing/pipe-corrosion.jpg";
import storeImg from "./assets/landing/store-entrance.jpg";
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
        <SignalMultiTrace sev={stream.sev} compact />
      </div>
    );
  }
  if (stream.graphic === "photo") {
    return (
      <div className="lp-fs-gfx lp-fs-gfx--photo" aria-hidden="true">
        <img src={pipeImg} alt="" loading="lazy" width={280} height={140} />
        <span className="lp-fs-box" style={{ left: "34%", top: "24%", width: "54%", height: "40%" }}><i>Clamp</i></span>
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
  if (stream.graphic === "safety") {
    return (
      <div className="lp-fs-gfx lp-fs-gfx--photo" aria-hidden="true">
        <img src={storeImg} alt="" loading="lazy" width={280} height={140} />
        <span className="lp-fs-box lp-fs-box--ok" style={{ left: "24%", top: "30%", width: "20%", height: "58%" }}><i>Worker</i></span>
      </div>
    );
  }
  if (stream.graphic === "plc") {
    return (
      <div className="lp-fs-gfx lp-fs-gfx--plc" aria-hidden="true">
        <span className="lp-plc-state">
          <i />
          UNLOCKED
        </span>
        <span className="lp-plc-sub">Interlock bit 04 · active</span>
      </div>
    );
  }
  return (
    <div className="lp-fs-gfx lp-fs-gfx--gauge" aria-hidden="true">
      <div className="lp-fs-gauge-bar">
        <span style={{ width: `${stream.pct || 72}%` }} data-sev={stream.sev} />
      </div>
      <div className="lp-fs-gauge-scale">
        <span>OK</span>
        <span>Warn</span>
        <span>Alert</span>
      </div>
    </div>
  );
}

export function FusionSchematic({ scenario }) {
  return (
    <div className="lp-fusion-board" id="lp-fusion-diagram" aria-live="polite">
      <div className="lp-fusion-streams" aria-label="Real-world sensor and camera inputs">
        {scenario.streams.map((stream) => (
          <div key={stream.kind} className="lp-fusion-stream" data-sev={stream.sev}>
            <div className="lp-fusion-stream-meta">
              <span className="lp-fusion-kind">{stream.kind}</span>
              <strong>{stream.title}</strong>
              <span className="lp-fusion-val">{stream.val}</span>
            </div>
            <StreamGraphic stream={stream} />
          </div>
        ))}
      </div>

      <div className="lp-fusion-connector" aria-hidden="true">
        <svg viewBox="0 0 92 260" preserveAspectRatio="none">
          <path d="M 0 42 C 42 42, 42 130, 66 130" />
          <path d="M 0 130 L 66 130" />
          <path d="M 0 218 C 42 218, 42 130, 66 130" />
          <circle cx="66" cy="130" r="6" />
          <line x1="72" y1="130" x2="92" y2="130" />
        </svg>
        <div className="lp-fusion-node">
          <strong>Edge Node</strong>
          <span>Offline · resilient</span>
        </div>
      </div>

      <div className="lp-fusion-output">
        <div className="lp-fusion-out-head">
          <span className="lp-fusion-badge" id="lp-fusion-badge">{scenario.edgeNode}</span>
          <span className="lp-fusion-pair">{scenario.badge}</span>
        </div>
        <strong className="lp-fusion-verdict" id="lp-fusion-verdict">{scenario.verdict}</strong>
        <p className="lp-fusion-why">{scenario.whyUseful}</p>
        <dl className="lp-fusion-outcomes" aria-label="Why combining sensors helps on site">
          {scenario.outcomes.map(([label, desc]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{desc}</dd>
            </div>
          ))}
        </dl>
        <div className="lp-fusion-foot">
          <span className="lp-fusion-fallback">{scenario.fallbackNote}</span>
          <span className="lp-fusion-action">{scenario.actionLabel}</span>
        </div>
      </div>
    </div>
  );
}
