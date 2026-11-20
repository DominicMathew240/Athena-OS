# Legal review checklist: what a lawyer must confirm before commercial use of Athena OS

> **This is a hand-off document written by the engineering team, not legal advice, and not a legal review.**
> It tells a qualified lawyer exactly what to look at, where the evidence lives, and what we could and could not
> verify. Every claim links back to [`docs/LICENSE_AUDIT.md`](../LICENSE_AUDIT.md) (the full audit, also not legal
> advice), which records the sources, dates and "unverified" gaps. Versions and licence tags were checked on the
> dates noted there; the latest full pass is **2026-11-19** ("second pass"). Licence tags on model hubs can change
> silently — re-check them on the day of the review.

## How to use this page

Each item below says, in plain language: what the thing is, what we found, and the **question for you**. Where we
wrote "unverified", we mean we could not read the source at all (or what we read was inconclusive) — please do not
treat those as cleared. Background reading: [`docs/LICENSE_AUDIT.md`](../LICENSE_AUDIT.md) (structure: verdict table,
per-package evidence, model-weights section, risks, "What I could not verify").

---

## 1. The product itself

**Athena OS code** is Apache-2.0, first-party, no vendored third-party source (audit §1). Docs are CC-BY-4.0.

- [ ] Confirm Apache-2.0 is the right licence for the owner's commercial model (patent-grant termination, §4 NOTICE duties).
- [ ] Confirm whether a `NOTICE` file should be added and what goes in it (audit "Needs a lawyer" item 7); `THIRD_PARTY_NOTICES.md` exists but is an engineering attribution list, not a legal notice file.
- [ ] Confirm the docs (CC-BY-4.0) / code (Apache-2.0) split is intended.

## 2. Shipped software dependencies

All pinned and transitive packages the platform installs are recorded in audit §2, §2a and §3 with per-package
evidence. Nothing shipped carries AGPL/GPL/SSPL/Commons Clause/CC-BY-NC or a non-commercial term. The exceptions we
know about:

- [ ] **MPL-2.0** (certifi, part of tqdm) — file-level copyleft, used unmodified. Confirm our obligations in a distributed image are as we assume (audit risk 5).
- [ ] **GPL-3.0-with-GCC-exception and LGPL components inside binary wheels** (numpy, scipy bundle `libgfortran`/`libgcc`/`libquadmath`; torch aggregates GPL/LGPL notices). Confirm the exception reading and the LGPL "replaceable shared library" duty for a distributed Docker image (audit §2 rows, "Compatibility" table).
- [ ] **The license gate's coverage**: the 2026-10-16 gate run predates the S3-storage pins (`django-storages`, `boto3` stack) and `cryptography` — all permissive on their PyPI metadata (audit §2a, fetched 2026-11-19), but the gate was never run over an environment containing them. Have engineering re-run `scripts/check_licenses.py` and confirm the result before release (audit risk 4b).
- [ ] **Binary-wheel internals** we did not open: cryptography's bundled OpenSSL NOTICE, onnxruntime's bundled MLAS/Eigen notices, the Linux scipy/numpy/torch/Pillow wheel licence bundles (audit "What I could not verify"). If we distribute the Docker image, these attributions ship with it.
- [ ] **NVIDIA CUDA wheels** — only relevant if a GPU image is ever published from PyPI's default index; NVIDIA's EULA was never read (unverified). Confirm or forbid (audit risk 3, lawyer item 6).

## 3. Model weights the platform can download (the biggest section)

Athena OS does **not** redistribute any weights; the operator's worker downloads them, admin-gated, checksum-pinned,
from `huggingface.co`/`*.hf.co` or `download.pytorch.org` (audit §5; `web/DEPLOYMENT.md` "Licences and model terms").
The operator's *use* is still the operator's legal question — that is why this checklist exists.

### 3a. CLIP (auto-suggested labels, on by default)

- [ ] The Hugging Face repo `openai/clip-vit-base-patch32` declares **no licence** (re-confirmed 2026-11-19: no tag, no LICENSE file). The upstream *code* repo is MIT, which says nothing about the weights. **Question: may these weights be used commercially at all, and may a hosted/paid Athena service use them?**
- [ ] The model card states **"Any deployed use case of the model - whether commercial or not - is currently out of scope"**, and puts surveillance/facial-recognition uses always out of scope. Athena's example operators are warehouse and security-scanner staff. **Question: is offering an auto-label feature that runs CLIP on a customer's pictures a "deployed use"? Does the security-scanner context touch the surveillance exclusion?** The owner accepted this risk on 2026-10-17 (audit "Owner decision"); that decision is a risk acceptance, not a clearance, and binds nobody downstream.
- [ ] Training data is undisclosed web-scraped image-caption data. **Question: training-data exposure (copyright/privacy) in your jurisdictions, and any IP indemnities customers may demand.**

### 3b. TrOCR (handwriting OCR tool) — **newly flagged 2026-11-19**

- [ ] The `microsoft/trocr-small-handwritten` repository currently declares **no licence we could find** (no `license:` tag, no LICENSE file, nothing in the model card; fetched 2026-11-19). An earlier engineering pass recorded "MIT on the model card"; that reading is **not reproducible today**. The related *code* repository (`microsoft/unilm`) is MIT, and the sibling model `trocr-base-handwritten` is tagged MIT. **Question: is there any instrument that licences these weights (the unilm MIT, Microsoft's publication practice, an FAQ), and may models fine-tuned from them be used commercially?** Until answered, engineering treats the handwriting tool's base weights as **not cleared**.
- [ ] The card says the model was fine-tuned on the **IAM Handwriting Database**. Its site was unreachable on 2026-11-19 (unverified). **Question: do the IAM terms reach weights trained on it — and does it matter when Athena only fine-tunes *on top of* the published weights with the operator's own images?**
- [ ] Note: the in-product text (`web/projects/training/ocr_trocr.py`, `web/DEPLOYMENT.md`) still says "MIT licence on its model card". That wording is now known to be unreliable; correcting it is an owner decision (audit risk 2b) — flag it in any customer-facing claims review.

### 3c. SAM (auto-segmentation, on by default)

- [ ] Weights are Apache-2.0 (model card and upstream, re-checked 2026-11-19). **Question: the SA-1B training *dataset* is under a separate "SA-1B Dataset Research License" whose text we could never fetch (ai.meta.com returned 401; unverified). Does the dataset licence reach the weights in your view?** (Audit §5 "Training-data caveats".)

### 3d. torchvision reference detector (the default Teach tool)

- [ ] The two ready-made starts (COCO-trained `FasterRCNN_MobileNet_V3_Large_320_FPN`, ImageNet-pretrained MobileNetV3 backbone) carry **no licence in the weight files**; torchvision's README says pre-trained models "may have their own licenses or terms and conditions derived from the dataset used for training" (re-fetched 2026-11-19). **Question: do the COCO dataset terms (below) or ImageNet's terms reach weights trained on them, and to models fine-tuned from them?** (Audit §5 torchvision row; full evidence table in [`docs/guides/model-quality.md`](model-quality.md) "Licence: what was checked and what was not".)

### 3e. D-FINE (optional second detector)

- [ ] The two pinned checkpoints (`ustc-community/dfine-nano-coco`, `-small-coco`) are declared Apache-2.0 on their model cards (re-checked 2026-11-19), but the COCO training-data question is the same as 3d, **who licenses the converted weights is unverified**, and the backbone's pretraining data was never verified. Objects365-based checkpoints are refused in code. **Question: same COCO question as 3d, plus the converted-weights ownership question.** (Audit §5 D-FINE row; full table in [`docs/guides/model-quality.md`](model-quality.md) D-FINE section.)

### 3f. The COCO dataset terms (quoted in full in audit §5b, fetched 2026-11-19 from cocodataset.org)

- Annotations and website: **CC-BY-4.0**; images: **not owned by the consortium**, use "must abide by the Flickr Terms of Use", users accept full responsibility. **Question: whether these terms reach (i) pretrained weights (3d/3e) and (ii) anything an Athena operator does — note Athena's *exports* copy nothing from COCO (see item 5).**

## 4. Data the customer brings (provenance)

Athena's operators train on their own pictures, audio, tables and transcripts; the models they teach derive from those.

- [ ] **Question: what should the product tell operators about their own data rights** (ownership of the images, permission from data subjects, works-for-hire questions) so that Athena is not warranting something it cannot? Current wording is in `web/DEPLOYMENT.md` ("Licences and model terms") and each backend's `license_note` — confirm it is adequate and not over-promising.
- [ ] **Question: exports and trained artifacts** — when an operator downloads a dataset version (COCO/YOLO zip) or a fine-tuned model, they hold a derivative of their own data and of the base weights (3a–3e). Confirm the product does not need to (and should not) claim any licence over either.

## 5. Export format specifications (COCO / YOLO)

The formal read the roadmap asked for is done (audit §5b, 2026-11-19): neither the COCO JSON schema nor the YOLO txt
convention carries a licence claim on anything we could read; COCO's terms govern the dataset and its site software
(neither of which Athena copies — the writer emits only the operator's own labels, boxes and images, with
`"licenses": []`). Ultralytics' AGPL-3.0 applies to their code, not to the text format.

- [ ] **Question: confirm no jurisdiction treats the format specifications/interfaces as protected in a way that affects Athena's original writer code.** (Low priority in our read; listed because the roadmap explicitly asked for the format-spec review to be lawyer-confirmed.)

## 6. The optional YOLO adapter (AGPL-3.0 boundary)

`examples/adapters/yolo_adapter.py` is Apache-2.0 (ours), links nothing at ship time, but is packaged in the
`athena-cli` wheel and documented with a `pip install ultralytics` instruction (now carrying a licence warning).
Ultralytics is AGPL-3.0 (PyPI, re-confirmed 2026-11-19) with an Enterprise licence alternative that its README says
covers the software "and AI models".

- [ ] **Question: the AGPL §13 boundary** — is a user's combined Athena + ultralytics network service caught, and what is our exposure for *shipping the adapter and instructing users to install it*? Options we framed: keep with warning / remove from the wheel / remove entirely (audit "Needs a lawyer" item 4).
- [ ] Pre-trained YOLO `.pt` weights licence: **unverified** (never read; the README implies the same dual licensing covers models).

## 7. Container distribution (if the Docker image is distributed or sold)

- [ ] **Debian GPL/LGPL source-offer mechanism** for the `python:3.13-slim` base layer, and torch's bundled-component notices (audit §7, "Compatibility" table, lawyer item 5). Note the repo-info snapshot is not tied to a specific digest.
- [ ] The wheels' dist-info licence directories and `THIRD_PARTY_NOTICES.md` must survive into the distributed image (audit "Compatibility" table's "what you owe").

## 8. Trademarks and naming

Out of scope of the engineering audit, deliberately.

- [ ] "Athena" / "Athena OS" as a product name (conflicts, including in the software class), and use of third-party names in docs and UI: Hugging Face, Microsoft, OpenAI, Meta/Facebook, Ultralytics, COCO, PyTorch. We quote model and vendor names factually (nominative use) and do not imply endorsement. **Question: confirm this is safe for the markets you care about, and clear or register the product mark.**

## 9. Jurisdiction and export notes (not analysed by engineering at all)

- [ ] Which jurisdictions' law governs the project's own licences and any hosted offering.
- [ ] AI-specific regulation touching the offering (model transparency, high-risk use, the human-review features) — engineering has built human-review/needs-review flows and "not a final determination" wording (ROADMAP M4), but no regulatory review has been done.
- [ ] Cryptography export/import: `cryptography` (Fernet, AES) is used for connector credentials at rest; if the image is distributed internationally, confirm export-control obligations.

---

## Evidence index (where each fact lives)

| Topic | Where |
|---|---|
| Full audit, per-package evidence, dates, fetches | [`docs/LICENSE_AUDIT.md`](../LICENSE_AUDIT.md) |
| Model-quality licence tables (torchvision, D-FINE, benchmarks' disclaimers) | [`docs/guides/model-quality.md`](model-quality.md) |
| Operator-facing licence summary and the download switch | `web/DEPLOYMENT.md` ("Licences and model terms", "Ready-made starting models") |
| Attribution list shipped with the repo | [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) |
| Automated gate | `scripts/check_licenses.py`, `scripts/license_allowlist.json`, `tests/test_license_gate.py` |

*Engineering read throughout; not legal advice. Prepared 2026-11-19.*
