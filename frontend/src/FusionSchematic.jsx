export function FusionSchematic({ scenario }) {
  return (
    <div className="lp-fusion-board" id="lp-fusion-diagram" aria-live="polite">
      <div className="lp-fusion-streams" aria-label="Input modality streams">
        {scenario.streams.map((stream) => (
          <div key={stream.kind} className="lp-fusion-stream" data-sev={stream.sev}>
            <div className="lp-fusion-stream-meta">
              <span className="lp-fusion-kind">{stream.kind}</span>
              <strong>{stream.title}</strong>
              <span className="lp-fusion-val">{stream.val}</span>
            </div>
            <div className="lp-fusion-bars" aria-hidden="true">
              {stream.bars.map((b, idx) => (
                <span key={idx} style={{ height: `${Math.round(b * 100)}%` }} />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="lp-fusion-connector" aria-hidden="true">
        <svg viewBox="0 0 84 180" preserveAspectRatio="none">
          <path d="M 0 28 C 36 28, 36 90, 62 90" />
          <path d="M 0 90 L 62 90" />
          <path d="M 0 152 C 36 152, 36 90, 62 90" />
          <circle cx="62" cy="90" r="6" />
          <line x1="68" y1="90" x2="84" y2="90" />
        </svg>
        <span className="lp-fusion-node">Sync + fallback</span>
      </div>

      <div className="lp-fusion-output">
        <span className="lp-fusion-badge" id="lp-fusion-badge">{scenario.badge}</span>
        <strong className="lp-fusion-verdict" id="lp-fusion-verdict">{scenario.verdict}</strong>
        <dl className="lp-fusion-ablation" aria-label="Single-stream vs fused ablation scores">
          {scenario.ablation.map(([label, score], idx) => (
            <div key={label} className={idx === scenario.ablation.length - 1 ? "is-fused" : ""}>
              <dt>{label}</dt>
              <dd>
                <span className="lp-ablation-track" aria-hidden="true">
                  <span style={{ width: `${Math.round(score * 100)}%` }} />
                </span>
                <b>{score.toFixed(2)}</b>
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
