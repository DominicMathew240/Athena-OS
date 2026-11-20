import pipeImg from "./assets/landing/pipe-corrosion.jpg";
import { HandwritingInkGraphic, RealAudioWaveform, SignalMultiTrace, SpectrogramGraphic } from "./ModalityGraphics.jsx";

export function StudioPreview({ outcome, stage, sampleIdx, onSelectSample, data }) {
  const samples = data.samples || [];
  const active = samples[sampleIdx] || samples[0];
  const metrics = data.stageMetrics[stage] || data.stageMetrics.eval;
  const stageCaption = data.stageCaptions?.[stage] || data.honestyNote;
  const audioVariant = active.waveSeed === 2 ? "fault" : active.waveSeed === 3 ? "slip" : "healthy";

  return (
    <div className="lp-studio-grid" data-testid="landing-multimodal-visualizer">
      <div className="lp-studio-stage">
        <div className="lp-stage-head">
          <span className="lp-stage-file" id="lp-vis-filename">{data.filename}</span>
          <div className="lp-vis-samples" id="lp-vis-samples" role="group" aria-label="Inspect examples">
            {samples.map((s, idx) => (
              <button
                key={s.label}
                type="button"
                className="lp-vis-sample"
                data-sample={idx}
                aria-pressed={sampleIdx === idx}
                onClick={() => onSelectSample(idx)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {outcome === "vision" && (
          <div className="lp-stage-media lp-stage-media--photo">
            <img src={pipeImg} alt="Corroded pipes showing confirmed and suggested boxes" width={1400} height={933} />
            {stage !== "raw" ? (
              samples.map((s, idx) => {
                const [x, y, w, h] = s.rect;
                const isSelected = idx === sampleIdx;
                return (
                  <button
                    key={s.label}
                    type="button"
                    className="lp-sbox"
                    data-sev={s.sev}
                    data-state={s.boxState}
                    data-selected={isSelected}
                    style={{ left: `${x}%`, top: `${y}%`, width: `${w}%`, height: `${h}%` }}
                    onClick={() => onSelectSample(idx)}
                    aria-label={`Inspect ${s.title}`}
                  >
                    <span>
                      {idx + 1} · {s.boxState === "suggested" ? "Check this" : "Confirmed"}
                    </span>
                  </button>
                );
              })
            ) : (
              <span className="lp-raw-pill">ORIGINAL PHOTO · SAVED SAFELY</span>
            )}
          </div>
        )}

        {outcome === "audio" && (
          <div className="lp-stage-media lp-stage-media--panel">
            <div className="lp-clip-head">
              <span className="lp-clip-spec">SOUND WAVE &amp; PITCH MAP · 4.2 SEC RECORDING</span>
              <span className="lp-clip-tag" data-sev={stage === "raw" ? "info" : active.sev}>
                {stage === "raw" ? "Not labeled yet" : `${active.label} · ${active.peakHz}`}
              </span>
            </div>
            <RealAudioWaveform variant={audioVariant} />
            <SpectrogramGraphic variant={audioVariant} />
            <div className="lp-clip-list" role="group" aria-label="Sample sound clips">
              {samples.map((s, idx) => (
                <button
                  key={s.label}
                  type="button"
                  className="lp-clip-row"
                  aria-pressed={sampleIdx === idx}
                  onClick={() => onSelectSample(idx)}
                >
                  <span className="lp-clip-name">{s.label}</span>
                  <span className="lp-clip-meta">{s.peakHz} · {s.rmsDb}</span>
                  <span className="lp-clip-pill" data-sev={stage === "raw" ? "info" : s.sev}>
                    {stage === "raw" ? "Waiting" : "Labeled"}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {outcome === "ocr" && (
          <div className="lp-stage-media lp-stage-media--panel">
            <div className="lp-clip-head">
              <span className="lp-clip-spec">SCANNED LOGBOOK LINES · HUMAN CORRECTIONS</span>
              <span className="lp-clip-tag" data-sev={stage === "raw" ? "medium" : "ok"}>
                {stage === "raw" ? "First computer read" : "Corrected by person"}
              </span>
            </div>
            <div className="lp-ocr-rows">
              {samples.map((s, idx) => {
                const selected = idx === sampleIdx;
                return (
                  <button
                    key={s.label}
                    type="button"
                    className="lp-ocr-row"
                    aria-pressed={selected}
                    onClick={() => onSelectSample(idx)}
                  >
                    <div className="lp-ocr-scan">
                      <span className="lp-ocr-line-id">L{idx + 1}</span>
                      <HandwritingInkGraphic variant={s.inkVariant} />
                    </div>
                    <div className="lp-ocr-diff">
                      <span className="lp-ocr-tag">
                        {stage === "raw" ? "Before correction" : "Corrected text"}
                      </span>
                      <code>{stage === "raw" ? s.rawRead : s.corrected}</code>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {outcome === "spreadsheets" && (
          <div className="lp-stage-media lp-stage-media--panel">
            <div className="lp-stage-media--table">
              <table className="lp-mini-table" aria-label="Sample spreadsheet rows and risk predictions">
                <thead>
                  <tr>
                    <th scope="col">Unit</th>
                    <th scope="col">Temp</th>
                    <th scope="col">Shake</th>
                    <th scope="col">Pressure</th>
                    <th scope="col">Predicted: Needs Repair</th>
                  </tr>
                </thead>
                <tbody>
                  {samples.map((s, idx) => (
                    <tr
                      key={s.rowId}
                      data-selected={idx === sampleIdx}
                      onClick={() => onSelectSample(idx)}
                    >
                      <td>
                        <button
                          type="button"
                          className="lp-row-btn"
                          aria-pressed={idx === sampleIdx}
                          onClick={(ev) => {
                            ev.stopPropagation();
                            onSelectSample(idx);
                          }}
                        >
                          <code>{s.rowId}</code>
                        </button>
                      </td>
                      <td>{s.temp}</td>
                      <td>{s.vib}</td>
                      <td>{s.bar}</td>
                      <td>
                        <span className="lp-table-pred" data-sev={stage === "raw" ? "info" : s.sev}>
                          {stage === "raw" ? "Column to guess" : s.pred}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="lp-tab-drivers" aria-label="Biggest clues from your spreadsheet">
              <span className="lp-clip-spec">BIGGEST CLUES IN THIS SPREADSHEET</span>
              <div className="lp-driver-bars">
                {[
                  ["Vibration", 44, "high"],
                  ["Temperature", 33, "medium"],
                  ["Pressure drop", 16, "info"],
                ].map(([col, pct, sev]) => (
                  <div key={col} className="lp-driver-row">
                    <span>{col}</span>
                    <span className="lp-driver-track"><i style={{ width: `${pct * 1.9}%` }} data-sev={sev} /></span>
                    <b>{pct}%</b>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {outcome === "documents" && (
          <div className="lp-stage-media lp-stage-media--panel">
            <div className="lp-clip-head">
              <span className="lp-clip-spec">WORK ORDERS &amp; SHIFT LOGS · AUTOMATIC ROUTING</span>
              <span className="lp-clip-tag" data-sev={stage === "raw" ? "info" : active.sev}>
                {stage === "raw" ? "Unsorted notes" : `${active.predClass} (${active.conf})`}
              </span>
            </div>
            <div className="lp-doc-list">
              {samples.map((s, idx) => (
                <button
                  key={s.docId}
                  type="button"
                  className="lp-doc-row"
                  aria-pressed={idx === sampleIdx}
                  onClick={() => onSelectSample(idx)}
                >
                  <div className="lp-doc-top">
                    <code>{s.docId}</code>
                    <span className="lp-doc-class" data-sev={stage === "raw" ? "info" : s.sev}>
                      {stage === "raw" ? "Needs team" : `${s.predClass} (${s.conf})`}
                    </span>
                  </div>
                  <p>{s.text}</p>
                </button>
              ))}
            </div>
            <div className="lp-token-strip" aria-label="Words that helped Athena sort this note">
              <span className="lp-clip-spec">KEY WORDS ATHENA NOTICED</span>
              <div className="lp-token-chips">
                <span data-w="high">seal weeping</span>
                <span data-w="high">pump P-104B</span>
                <span data-w="med">breaker tripped</span>
                <span data-w="ok">pressure normal</span>
              </div>
            </div>
          </div>
        )}

        {outcome === "signals" && (
          <div className="lp-stage-media lp-stage-media--panel">
            <div className="lp-clip-head">
              <span className="lp-clip-spec">{active.seriesId} · 60-SECOND MACHINE LOG</span>
              <span className="lp-clip-tag" data-sev={stage === "raw" ? "info" : active.sev}>
                {stage === "raw" ? "Raw sensor log" : active.pred}
              </span>
            </div>
            <SignalMultiTrace mode={active.mode || "imbalance"} sev={stage === "raw" ? "info" : active.sev} />
            <div className="lp-sig-stats">
              <div><span>Average shake</span><b>{active.rms}</b></div>
              <div><span>Highest spike</span><b>{active.peak}</b></div>
              <div><span>Direction</span><b>{active.slope}</b></div>
            </div>
          </div>
        )}
      </div>

      <aside className="lp-studio-inspector" id="lp-vis-inspector" aria-live="polite" data-testid="landing-vis-inspector">
        <div className="lp-insp-head">
          <span className="lp-insp-eyebrow">{data.badge}</span>
          <span className="lp-insp-backend">{data.backend}</span>
        </div>

        <div className="lp-insp-selection">
          <strong id="lp-vis-selection-title">{active.title}</strong>
          <p id="lp-vis-selection-detail">{active.detail}</p>
        </div>

        <div className="lp-vis-metrics" id="lp-vis-metrics">
          {metrics.map(([val, lbl]) => (
            <div key={lbl}>
              <b>{val}</b>
              <span>{lbl}</span>
            </div>
          ))}
        </div>

        <p className="lp-insp-caption">{stageCaption}</p>
        <p className="lp-insp-honesty">{data.honestyNote}</p>
      </aside>
    </div>
  );
}
