import { useState } from "react";

const DOC_TABS = [
  { id: "overview", label: "What works today" },
  { id: "datatypes", label: "All 6 data types" },
  { id: "privacy", label: "Privacy & control" },
  { id: "limits", label: "Honest limits" },
];

const DATA_TYPE_ROWS = [
  { kind: "Photos & short clips", status: "Ready today", what: "Spot rust, safety gear, empty parking bays, or weld defects", check: "Shows what it found vs missed on unseen photos" },
  { kind: "Sound clips (WAV)", status: "Ready today", what: "Tell normal motor hum apart from bearing wear or belt squeal", check: "Shows per-sound accuracy on unseen clips" },
  { kind: "Handwriting & forms", status: "Training ready", what: "Fix misread words on scanned logbooks so Athena learns your writing", check: "Check screen for operators is being finished" },
  { kind: "Spreadsheets (CSV/XLSX)", status: "Ready today", what: "Pick one column to predict (such as Needs Repair) from the other columns", check: "Tests on new rows and lists the biggest clues" },
  { kind: "Shift notes & tickets", status: "Sorting ready", what: "Sort incoming incident notes to the right team automatically", check: "Pulling out individual equipment names comes next" },
  { kind: "Machine sensors", status: "Sorting ready", what: "Compare vibration and temperature windows against normal limits", check: "Forecasting hours ahead comes next" },
];

export function DocsSection() {
  const [tab, setTab] = useState("overview");
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();

  const filteredTypes = DATA_TYPE_ROWS.filter(
    (r) => !q || `${r.kind} ${r.status} ${r.what} ${r.check}`.toLowerCase().includes(q),
  );

  return (
    <section className="lp-section lp-docs" id="docs" aria-labelledby="docs-title" data-testid="landing-docs-section">
      <div className="lp-container">
        <div className="lp-head lp-head--split lp-reveal">
          <div>
            <p className="lp-eyebrow">
              <b>06</b> Quick guide
            </p>
            <h2 className="lp-h2" id="docs-title">
              Plain-English guide &amp; <em>honest status.</em>
            </h2>
          </div>
          <p className="lp-sub">Everything you need to know about what works right now, how your data stays private, and where people stay in charge.</p>
        </div>

        <div className="lp-docs-bar">
          <div className="lp-docs-tabs" role="group" aria-label="Guide sections">
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
            placeholder="Search guide (e.g. audio, Excel, privacy)..."
            aria-label="Search quick guide"
          />
        </div>

        <div className="lp-docs-panel">
          {(tab === "overview" || q) && (
            <div className="lp-docs-grid">
              <div className="lp-docs-card">
                <strong>No coding or data-science team required</strong>
                <p>Ask questions about photos today in plain English, or walk through a simple 6-step checklist to train your own model on photos, sounds, handwriting, spreadsheets, shift notes, or sensor logs.</p>
              </div>
              <div className="lp-docs-card">
                <strong>One simple workspace for every job</strong>
                <p>Instead of learning six different tools, every project follows the exact same steps: add your data, check the labels, freeze a test set, train, check real mistakes, and approve before anyone uses it.</p>
              </div>
            </div>
          )}

          {(tab === "datatypes" || q) && (
            <div className="lp-docs-table-wrap">
              <table className="lp-docs-table">
                <thead>
                  <tr>
                    <th scope="col">Data type</th>
                    <th scope="col">Status</th>
                    <th scope="col">What you can do</th>
                    <th scope="col">Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredTypes.map((row) => (
                    <tr key={row.kind}>
                      <td>{row.kind}</td>
                      <td><code>{row.status}</code></td>
                      <td>{row.what}</td>
                      <td>{row.check}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(tab === "privacy" || q) && (
            <div className="lp-docs-grid">
              <div className="lp-docs-card">
                <strong>Your files never have to leave your building</strong>
                <p>Run Athena on your own office server, plant computer, or local laptop. Connect local AI models and your photos, sound clips, and spreadsheets stay completely inside your own network.</p>
              </div>
              <div className="lp-docs-card">
                <strong>Nothing changes without human sign-off</strong>
                <p>A new model never goes live automatically. A supervisor reviews its test results first, approves it, and can roll back to the previous version in one click at any time.</p>
              </div>
            </div>
          )}

          {(tab === "limits" || q) && (
            <div className="lp-docs-grid">
              <div className="lp-docs-card">
                <strong>What is ready vs still being tested</strong>
                <p>Instant photo checks and all six training workflows run today and have been verified on test examples. Multi-sensor fusion and live camera people-counting are in active development and not yet ready for live production use on real plant data.</p>
              </div>
              <div className="lp-docs-card">
                <strong>Video clip &amp; handwriting notes</strong>
                <p>Uploaded videos are checked as six evenly spaced still frames — so they show a snapshot rather than a live crossing count. Handwriting training works today while its operator upload screen is being finalized.</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
