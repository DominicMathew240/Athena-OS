import { useState } from "react";

const DOC_TABS = [
  { id: "overview", label: "Platform Overview" },
  { id: "capabilities", label: "Capabilities (P0-P3)" },
  { id: "waves", label: "Delivery Waves" },
  { id: "ecosystem", label: "Ecosystem & Quality" },
];

const CAPABILITY_ROWS = [
  { modality: "Computer Vision", prio: "P0", train: "Classification, detection, segmentation, defects & anomalies", quality: "Recall, precision, mAP50, missed defect crops" },
  { modality: "Acoustic AI", prio: "P0", train: "Equipment faults, bearing anomalies, unusual sounds (WAV/ONNX)", quality: "Event F1, missed fault count, false alarms/hr" },
  { modality: "OCR & Handwriting", prio: "P0", train: "Handwriting fine-tuning (TrOCR), printed text & field extraction", quality: "CER, WER & structured field match rate" },
  { modality: "Tabular AI", prio: "P0", train: "Classification & prediction from Excel/CSV spreadsheets", quality: "Held-out F1, calibration & leakage checks" },
  { modality: "Speech AI", prio: "P1", train: "Transcription, commands & dialect adaptation (Sarawak Malay ASR)", quality: "Word Error Rate (WER) & keyword recall" },
  { modality: "Text & Documents", prio: "P1", train: "Document classification, NER spans & structured extraction", quality: "Class F1, confusion pairs & schema validation" },
  { modality: "Sensor AI", prio: "P1", train: "Anomaly detection & forecasting from time-series telemetry", quality: "Lead-time detection & false trigger rate" },
  { modality: "Pose & Hand Tracking", prio: "P2", train: "17-pt body skeleton, 21-pt hand landmarks, actions & gestures", quality: "OKS / PCK keypoint accuracy & occlusion misses" },
  { modality: "Video AI & Face AI (gated)", prio: "P2", train: "Sequence tracking & separately gated biometric verification", quality: "Temporal F1; Face AI gated by strict consent policy" },
  { modality: "Multimodal Fusion", prio: "P3", train: "Joint models combining audio, video, thermal, text & telemetry", quality: "Single-stream vs fused ablation & fallback tests" },
];

const WAVE_ROWS = [
  { wave: "Wave 0 (0-6 wks)", ids: "OS-CORE-001 to OS-CORE-006", scope: "Shared Task, Observation, Annotation, DatasetVersion, TrainingRun & ReleaseGate resources; T01-T05 vision workstream" },
  { wave: "Wave 1 (2-4 mos · MVP)", ids: "OS-VIS-001 · OS-AUD-001 · OS-OCR-001 · OS-TAB-001", scope: "Outcome-first No-Code Wizard across Vision, Acoustic Events, Handwriting OCR (TrOCR), and Tabular CSV/XLSX" },
  { wave: "Wave 2 (4-7 mos)", ids: "OS-ASR-001 · OS-NLP-001 · OS-DOC-001 · OS-SIG-001 · OS-AUD-002", scope: "Speech ASR, Document NER/extraction, Time-series telemetry forecasting & acoustic baseline anomalies" },
  { wave: "Wave 3 (7-10 mos)", ids: "OS-POS-001 · OS-HND-001 · OS-VID-001 · OS-FAC-001", scope: "Pose & hand gestures, reusable industry Domain Packs, video events & compliance-gated Face AI" },
  { wave: "Wave 4 (10-15+ mos)", ids: "OS-FUS-001 · OS-NOD-001", scope: "Cross-modal late/feature fusion, missing-modality fallback, ONNX/edge bundles & optional Athena Nodes compute" },
];

export function DocsSection() {
  const [tab, setTab] = useState("overview");
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();

  const filteredCaps = CAPABILITY_ROWS.filter(
    (r) => !q || `${r.modality} ${r.prio} ${r.train} ${r.quality}`.toLowerCase().includes(q),
  );
  const filteredWaves = WAVE_ROWS.filter(
    (w) => !q || `${w.wave} ${w.ids} ${w.scope}`.toLowerCase().includes(q),
  );

  return (
    <section className="lp-section lp-docs" id="docs" aria-labelledby="docs-title" data-testid="landing-docs-section">
      <div className="lp-container">
        <div className="lp-head lp-head--split lp-reveal">
          <div>
            <p className="lp-eyebrow">
              <b>06</b> Docs &amp; roadmap specification
            </p>
            <h2 className="lp-h2" id="docs-title">
              Platform architecture &amp; <em>reference docs.</em>
            </h2>
          </div>
          <p className="lp-sub">Search capabilities, quality gates, and delivery waves from the 17 November 2026 roadmap.</p>
        </div>

        <div className="lp-docs-bar">
          <div className="lp-docs-tabs" role="group" aria-label="Documentation sections">
            {DOC_TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                className="lp-docs-tab"
                aria-pressed={tab === t.id}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <input
            type="search"
            className="lp-docs-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter docs (e.g. TrOCR, Wave 1, F1)..."
            aria-label="Filter documentation tables"
          />
        </div>

        <div className="lp-docs-panel">
          {(tab === "overview" || q) && (
            <div className="lp-docs-grid">
              <div className="lp-docs-card">
                <strong>Athena OS Core Lifecycle</strong>
                <p>Every modality shares one open-source resource flow: Project -&gt; Task -&gt; Observation -&gt; Annotation -&gt; Dataset Version -&gt; Training Run -&gt; Model Version -&gt; Evaluation -&gt; Deployment -&gt; Feedback.</p>
              </div>
              <div className="lp-docs-card">
                <strong>Current Verified Baseline vs Roadmap</strong>
                <p>Today: plain-language photo &amp; 6-frame video checks plus prototype image object detection (T01-T04 local, T05 partial). Audio, TrOCR, Tabular, Pose &amp; Fusion extend through versioned Forge interfaces without proprietary lock-in.</p>
              </div>
            </div>
          )}

          {(tab === "capabilities" || q) && (
            <div className="lp-docs-table-wrap">
              <table className="lp-docs-table">
                <thead>
                  <tr>
                    <th>Modality</th>
                    <th>Priority</th>
                    <th>What Users Train</th>
                    <th>Quality Gate</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCaps.map((row) => (
                    <tr key={row.modality}>
                      <td>{row.modality}</td>
                      <td><code>{row.prio}</code></td>
                      <td>{row.train}</td>
                      <td>{row.quality}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(tab === "waves" || q) && (
            <div className="lp-docs-table-wrap">
              <table className="lp-docs-table">
                <thead>
                  <tr>
                    <th>Wave</th>
                    <th>Workstream IDs</th>
                    <th>Scope &amp; Deliverables</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredWaves.map((w) => (
                    <tr key={w.wave}>
                      <td>{w.wave}</td>
                      <td><code>{w.ids}</code></td>
                      <td>{w.scope}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(tab === "ecosystem" || q) && (
            <div className="lp-docs-grid">
              <div className="lp-docs-card">
                <strong>System Boundaries (OS · Forge · Lab · Nodes)</strong>
                <p>Athena OS remains Apache-2.0 self-hostable. Athena Forge contributes specialized trainers via versioned contracts; Athena Lab handles research benchmarking &amp; release qualification; Athena Nodes provides optional distributed GPU compute.</p>
              </div>
              <div className="lp-docs-card">
                <strong>Repository Roadmap Documents</strong>
                <p>Full specifications in <code>PRODUCT.md</code>, <code>ROADMAP.md</code>, <code>docs/plans/2026-11-15-vision-workflows-task-list.md</code>, and <code>docs/plans/2026-11-17-multimodal-platform-roadmap.md</code>.</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
