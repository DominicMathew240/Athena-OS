import { useState } from "react";

const DOC_TABS = [
  { id: "overview", label: "Platform Overview" },
  { id: "recipes", label: "Merged Recipes (6)" },
  { id: "capabilities", label: "Capabilities (P0-P3)" },
  { id: "waves", label: "Waves & Vision (18/65)" },
  { id: "ecosystem", label: "Design & Honesty" },
];

const MERGED_RECIPES = [
  { key: "vision-detection", modality: "image", backend: "torchvision-fasterrcnn / dfine-coco", status: "Merged end to end (synthetic-verified)" },
  { key: "audio-classification", modality: "audio", backend: "audio-wav-cnn (ONNX / torch)", status: "Merged in console (synthetic-verified)" },
  { key: "handwriting-ocr", modality: "image", backend: "ocr-trocr", status: "Merged in console (serving backend-level; weight licence open)" },
  { key: "tabular-prediction", modality: "table", backend: "tabular-sklearn", status: "Merged in console (synthetic-verified)" },
  { key: "text-classification", modality: "text", backend: "text-sklearn (TF-IDF + linear)", status: "Merged in console (NER/fields OS-DOC-001 upcoming)" },
  { key: "signal-classification", modality: "time_series", backend: "signal-sklearn (16 window stats)", status: "Merged in console (forecast/anomaly open)" },
];

const CAPABILITY_ROWS = [
  { modality: "Computer Vision", prio: "P0 · Merged", train: "Detection boxes, SAM+CLIP review, COCO/YOLO export & check screens", quality: "Recall, precision, mAP50, missed defect crops" },
  { modality: "Acoustic AI", prio: "P0 · Merged (clips)", train: "WAV clip classification (ONNX/torch); event windows in Wave 2", quality: "Macro F1, per-class precision/recall, confusion matrix" },
  { modality: "OCR & Handwriting", prio: "P0 · Merged (train)", train: "Handwriting transcript fine-tuning (TrOCR); backend serving", quality: "CER, WER & exact transcript match" },
  { modality: "Tabular AI", prio: "P0 · Merged", train: "Classification & prediction from Excel/CSV spreadsheets", quality: "Held-out F1, accuracy & leakage checks" },
  { modality: "Text & Documents", prio: "P1 · Half merged", train: "CSV text classification merged; NER spans & fields in progress", quality: "Class F1, confusion pairs & schema validation" },
  { modality: "Signal AI", prio: "P1 · Half merged", train: "Windowed series classification merged; forecasting & anomaly open", quality: "Series F1 & unseen-window evaluation" },
  { modality: "Speech AI", prio: "P1 · Planned", train: "Transcription, commands & dialect adaptation (Sarawak Malay ASR)", quality: "Word Error Rate (WER) & keyword recall" },
  { modality: "Pose & Hand Tracking", prio: "P2 · Wave 3", train: "17-pt body skeleton, 21-pt hand landmarks, actions & gestures", quality: "OKS / PCK keypoint accuracy & occlusion misses" },
  { modality: "Video AI & Face AI (gated)", prio: "P2 · Wave 3", train: "Operations replay (18/65 verified) & consent-gated biometrics", quality: "Temporal F1; Face AI gated by strict consent policy" },
  { modality: "Multimodal Fusion", prio: "P3 · Wave 4", train: "Joint models combining audio, video, thermal, text & telemetry", quality: "Single-stream vs fused ablation & fallback tests" },
];

const WAVE_ROWS = [
  { wave: "Wave 0 · Complete", ids: "OS-CORE-001 to OS-CORE-006", scope: "Shared TaskRecipe & ModalityPlugin registries, generic Annotations, universal DatasetVersion snapshots & TrainingBackend contract." },
  { wave: "Wave 1 · 4/4 Merged (gate open)", ids: "OS-VIS-001 · OS-AUD-001 · OS-OCR-001 · OS-TAB-001", scope: "Vision detection, Acoustic WAV classification, Handwriting OCR (TrOCR), and Tabular CSV/XLSX — all synthetic-verified." },
  { wave: "Wave 2 · Text & Signals Merged", ids: "OS-NLP-001 · OS-SIG-001 (merged) · OS-ASR · OS-DOC · OS-AUD-002 (open)", scope: "Text classification and windowed Signal classification merged; Speech ASR, Document NER/fields & acoustic anomaly open." },
  { wave: "Wave 3 · Proposed", ids: "OS-POS-001 · OS-HND-001 · OS-VID-001 · OS-FAC-001", scope: "Pose & hand gestures, reusable industry Domain Packs, video crossing events & compliance-gated Face AI." },
  { wave: "Wave 4 · Contracts & Roadmap", ids: "OS-FUS-001 · OS-NOD-001", scope: "Cross-modal late/feature fusion, missing-modality fallback, edge bundles & optional Athena Nodes compute." },
];

export function DocsSection() {
  const [tab, setTab] = useState("overview");
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();

  const filteredRecipes = MERGED_RECIPES.filter(
    (r) => !q || `${r.key} ${r.modality} ${r.backend} ${r.status}`.toLowerCase().includes(q),
  );
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
              <b>06</b> Docs &amp; current development
            </p>
            <h2 className="lp-h2" id="docs-title">
              Verified main state &amp; <em>reference docs.</em>
            </h2>
          </div>
          <p className="lp-sub">Updated 20 November 2026: Wave 0 complete, Wave 1 merged, Text &amp; Signal classification merged, and Vision workflows at 18 of 65.</p>
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
            placeholder="Filter docs (e.g. TrOCR, Wave 2, signal)..."
            aria-label="Filter documentation tables"
          />
        </div>

        <div className="lp-docs-panel">
          {(tab === "overview" || q) && (
            <div className="lp-docs-grid">
              <div className="lp-docs-card">
                <strong>Registry-Driven Shared Lifecycle</strong>
                <p>Every project picks a <code>TaskRecipe</code> and runs through one lifecycle: Project -&gt; Task -&gt; Observation -&gt; Annotation -&gt; DatasetVersion -&gt; TrainingRun -&gt; ModelVersion -&gt; Evaluation -&gt; Deployment -&gt; Feedback. Views dispatch via plugin registries without modality branches.</p>
              </div>
              <div className="lp-docs-card">
                <strong>Verified Baseline on Main (20 Nov 2026)</strong>
                <p>Wave 0 complete; all 4 Wave 1 slices merged (vision, audio, TrOCR, tabular); Text &amp; Signal halves of Wave 2 merged; Vision workflows at 18 of 65 tasks (T01–T14, T19–T22) including T06 setup, T12 view health, T13 replay, T14 line direction &amp; encrypted credentials.</p>
              </div>
            </div>
          )}

          {(tab === "recipes" || q) && (
            <div className="lp-docs-table-wrap">
              <table className="lp-docs-table">
                <thead>
                  <tr>
                    <th scope="col">Recipe Key</th>
                    <th scope="col">Modality</th>
                    <th scope="col">Training Backend</th>
                    <th scope="col">Verified State</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRecipes.map((row) => (
                    <tr key={row.key}>
                      <td><code>{row.key}</code></td>
                      <td><code>{row.modality}</code></td>
                      <td><code>{row.backend}</code></td>
                      <td>{row.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(tab === "capabilities" || q) && (
            <div className="lp-docs-table-wrap">
              <table className="lp-docs-table">
                <thead>
                  <tr>
                    <th scope="col">Modality</th>
                    <th scope="col">Status</th>
                    <th scope="col">What Users Train</th>
                    <th scope="col">Quality Gate</th>
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
                    <th scope="col">Wave</th>
                    <th scope="col">Workstream IDs</th>
                    <th scope="col">Scope &amp; Deliverables</th>
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
                <strong>Console Design &amp; Honesty Rules</strong>
                <p>Token-only Light &amp; Dark themes (<code>tokens.css</code>), strict CSP with zero external origins, progressive enhancement, readable state (<code>Ready</code> / <code>Blocked</code> with one next action), and explicit limits: synthetic-only verification, backend-level OCR serving, and open TrOCR weight licence decision.</p>
              </div>
              <div className="lp-docs-card">
                <strong>System Boundaries (OS · Forge · Lab · Nodes)</strong>
                <p>Athena OS stays Apache-2.0 self-hostable. Athena Forge contributes specialized trainers via versioned contracts; Athena Lab runs benchmarking &amp; release qualification; Athena Nodes offers optional distributed GPU compute.</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
