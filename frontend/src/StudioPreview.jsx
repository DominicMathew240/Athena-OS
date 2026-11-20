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
          <div className="lp-vis-samples" id="lp-vis-samples" role="group" aria-label="Inspect training examples and annotations">
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
            <img src={pipeImg} alt="Corroded pipes showing accepted and suggested detection boxes" width={1400} height={933} />
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
                      {idx + 1} · {s.boxState === "suggested" ? "Needs review" : "Accepted"}
                    </span>
                  </button>
                );
              })
            ) : (
              <span className="lp-raw-pill">RAW PHOTO · SHA-256 VERIFIED · 0 BOXES</span>
            )}
          </div>
        )}

        {outcome === "audio" && (
          <div className="lp-stage-media lp-stage-media--panel">
            <div className="lp-clip-head">
              <span className="lp-clip-spec">CH1 MONO · 16-BIT PCM WAV · 48 kHz</span>
              <span className="lp-clip-tag" data-sev={stage === "raw" ? "info" : active.sev}>
                {stage === "raw" ? "Unlabeled clip" : `${active.label} · ${active.peakHz}`}
              </span>
            </div>
            <RealAudioWaveform variant={audioVariant} />
            <SpectrogramGraphic variant={audioVariant} />
            <div className="lp-clip-list" role="group" aria-label="WAV clips in dataset">
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
                    {stage === "raw" ? "Pending" : "Clip label"}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {outcome === "ocr" && (
          <div className="lp-stage-media lp-stage-media--panel">
            <div className="lp-clip-head">
              <span className="lp-clip-spec">SCANNED LINE CROPS · TrOCR TRANSCRIPT REVIEW</span>
              <span className="lp-clip-tag" data-sev={stage === "raw" ? "medium" : "ok"}>
                {stage === "raw" ? "Base OCR output" : "Human transcript verified"}
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
                        {stage === "raw" ? "Base TrOCR (uncorrected)" : "Clerk transcript"}
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
              <table className="lp-mini-table" aria-label="Sample spreadsheet rows and target predictions">
                <thead>
                  <tr>
                    <th scope="col">Row</th>
                    <th scope="col">Temp</th>
                    <th scope="col">Vib RMS</th>
                    <th scope="col">Pressure</th>
                    <th scope="col">Target: Failure_48h</th>
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
                          {stage === "raw" ? "Target col" : s.pred}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="lp-tab-drivers" aria-label="Top feature clues driving prediction">
              <span className="lp-clip-spec">TOP FEATURE DRIVERS · TABULAR-SKLEARN</span>
              <div className="lp-driver-bars">
                {[
                  ["rms_vib", 44, "high"],
                  ["housing_temp", 33, "medium"],
                  ["pressure_drop", 16, "info"],
                ].map(([col, pct, sev]) => (
                  <div key={col} className="lp-driver-row">
                    <code>{col}</code>
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
              <span className="lp-clip-spec">CSV {`{text, label}`} · TF-IDF EVIDENCE TOKENS</span>
              <span className="lp-clip-tag" data-sev={stage === "raw" ? "info" : active.sev}>
                {stage === "raw" ? "Raw shift logs" : `${active.predClass} (${active.conf})`}
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
                      {stage === "raw" ? "Unclassified" : `${s.predClass} (${s.conf})`}
                    </span>
                  </div>
                  <p>{s.text}</p>
                </button>
              ))}
            </div>
            <div className="lp-token-strip" aria-label="Top weighted n-gram tokens">
              <span className="lp-clip-spec">WEIGHTED TERMS</span>
              <div className="lp-token-chips">
                <span data-w="high">seal weep (+0.68)</span>
                <span data-w="high">pump p-104b (+0.52)</span>
                <span data-w="med">breaker tripped (+0.49)</span>
                <span data-w="ok">nominal (+0.41)</span>
              </div>
            </div>
          </div>
        )}

        {outcome === "signals" && (
          <div className="lp-stage-media lp-stage-media--panel">
            <div className="lp-clip-head">
              <span className="lp-clip-spec">{active.seriesId} · DUAL-CHANNEL WINDOW (16 STATS)</span>
              <span className="lp-clip-tag" data-sev={stage === "raw" ? "info" : active.sev}>
                {stage === "raw" ? "Wide CSV series" : active.pred}
              </span>
            </div>
            <SignalMultiTrace points={active.points || []} sev={stage === "raw" ? "info" : active.sev} />
            <div className="lp-sig-stats">
              <div><span>RMS</span><b>{active.rms}</b></div>
              <div><span>Peak-to-peak</span><b>{active.peak}</b></div>
              <div><span>Trend slope</span><b>{active.slope}</b></div>
            </div>
          </div>
        )}
      </div>

      <aside className="lp-studio-inspector" id="lp-vis-inspector" aria-live="polite" data-testid="landing-vis-inspector">
        <div className="lp-insp-head">
          <span className="lp-insp-eyebrow">Console inspector · {data.recipeKey}</span>
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
