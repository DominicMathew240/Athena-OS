# License audit: commercial use of Athena OS and everything it ships or downloads

> **This is an engineering audit, not legal advice.** It records what the package metadata,
> LICENSE files and model cards say, and flags what needs a lawyer. Anything I could not read
> or check is marked **unverified** rather than guessed. Audit date: 2026-10-16.

## Verdict

| Area | Commercial use (self-hosting, selling services around it) |
|---|---|
| Athena OS itself (Apache-2.0) | **Yes** |
| Console runtime deps: Django, Pillow, whitenoise, gunicorn + 33 transitive | **Yes**, permissive. MPL-2.0 only for certifi and tqdm (unmodified use is fine) |
| Auto-label and training deps: numpy, torch, torchvision, transformers + transitive | **Yes**, permissive, **provided the CPU torch wheel is used** (see caveat 3) |
| SDK/CLI deps: pydantic 2.14.1 + 4 transitive | **Yes**, MIT/PSF |
| `examples/adapters/yolo_adapter.py` | Adapter source is Apache-2.0 and **links nothing copyleft** at ship time. `ultralytics` (AGPL-3.0) is *not* a declared dependency. **Using** the adapter with ultralytics installed pulls in AGPL-3.0: **NO** for a closed commercial service without an Ultralytics Enterprise license |
| SAM weights `facebook/sam-vit-base` | **Yes**, Apache-2.0 (declared on the model card; upstream LICENSE read) |
| CLIP weights `openai/clip-vit-base-patch32` | **Unclear, treat as risk.** The Hugging Face repo declares **no license**; upstream code repo is MIT; the model card states **any deployed use, commercial or not, is out of scope** (not a license term, but a stated intended-use limit) |
| Static assets / fonts / CDNs | **None third-party found.** System font stack, no CDN, no icons |
| Docker base `python:3.13-slim` | **Yes**, but distributing the image means complying with Debian's GPL/LGPL packages (source offer) |
| GitHub Actions used | **Yes**, MIT (CI-only, not distributed) |

Nothing in the **shipped** dependency set carries AGPL, GPL, SSPL, Commons Clause, CC-BY-NC or an
explicit non-commercial term. Items that fall short of a clean "yes" are in *Risks and caveats*.

## Method

* Two throwaway venvs under `~/.hermes/profiles/athena/cache/scratch/license-audit/` (Python 3.13.0, macOS arm64):
  `web` = `web/requirements.txt` + `web/requirements-autolabel.txt` (44 distributions, 40 after removing my own
  tooling: pip, pip-licenses, prettytable, wcwidth); `root` = `sdk/python` + `cli` + pytest.
  The repo was not touched.
* Licenses taken from installed `METADATA` (`License-Expression`, `License`, `License ::` classifiers) and, where
  the metadata was only a classifier, from the `LICENSE` text in the dist-info. Versions are the ones pip resolved today.
* Linux/Docker resolution: `pip install --dry-run --platform manylinux_2_28_{x86_64,aarch64} --report` with and
  without `--extra-index-url https://download.pytorch.org/whl/cpu`. With the CPU index pip picks `torch==2.9.1+cpu`;
  the CPU wheel's `.whl.metadata` (fetched) lists no `nvidia-*`/`triton` requirement. Without it torch is the PyPI
  wheel, whose PyPI `requires_dist` (fetched) adds 15 `nvidia-*-cu12` packages and `triton` on Linux x86_64.
  (pip's `--platform` dry run evaluates environment markers for the host, so it did **not** show those; I used the PyPI JSON instead.)
* Model cards and LICENSE files fetched from huggingface.co and raw.githubusercontent.com (URLs below).
* `scripts/check_licenses.py` run against both venvs: 0 FAIL, 0 WARN, 0 UNKNOWN, 2 allowlisted (certifi, tqdm).

## 1. First-party (Athena OS)

| Component | Version | SPDX license | Evidence (read this session) | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| Athena OS code (`sdk/`, `cli/`, `web/`, `examples/`, `tests/`, `contracts/` schemas) | 0.1.0 | Apache-2.0 | `LICENSE` read (Apache 2.0 text); `sdk/python/pyproject.toml` and `cli/pyproject.toml` declare `Apache-2.0`; `docs/LICENSE` | yes | Distributors keep LICENSE, add NOTICE if one exists (none today) |
| Docs: `docs/`, `contracts/README.md`, `README.md` | n/a | CC-BY-4.0 | `docs/LICENSE` read | yes | Attribution when reusing docs |

## 2. Console runtime and auto-label worker (`web/requirements*.txt`, installed in `web/Dockerfile`)

Direct dependencies:

| Component | Version | SPDX license | Evidence (read this session) | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| Django | 6.1.2 | BSD-3-Clause | METADATA License-Expression; `licenses/LICENSE` read (also `LICENSE.python` for bundled PSF-licensed code) | yes | Keep copyright + disclaimer |
| pillow | 12.3.0 | MIT-CMU | METADATA License-Expression; `licenses/LICENSE` read (wheel bundles C libs; its text says GPL/LGPL xz helper files are not in the built binaries) | yes-with-conditions | Keep license + bundled-library notices (shipped in wheel) |
| whitenoise | 6.9.0 | MIT | METADATA classifier + `LICENSE` text read | yes | Keep copyright + permission notice |
| gunicorn | 26.2.0 | MIT | METADATA License-Expression; `licenses/LICENSE` read | yes | Keep copyright + permission notice |
| numpy | 2.1.3 | BSD-3-Clause | METADATA classifier 'BSD License'; `LICENSE.txt` read. Its bundled-library section lists OpenBLAS/LAPACK (BSD) and the GCC runtime library as `GPL-3.0-with-GCC-exception` (line 170) | yes-with-conditions | Keep notice; GCC runtime exception permits non-GPL programs |
| torch | 2.9.1 | BSD-3-Clause | METADATA License field; PyPI JSON; CPU wheel `.whl.metadata` (download.pytorch.org) says `License: BSD-3-Clause`; `licenses/LICENSE` read: aggregates ~82 third-party notices incl. GPL/LGPL texts for some vendored components | yes-with-conditions | Keep LICENSE + NOTICE; see caveat 3 about which wheel is installed |
| torchvision | 0.24.1 | BSD-3-Clause | Added after the original audit (training worker, `web/requirements-autolabel.txt`). Read from the installed `torchvision-0.24.1.dist-info`: METADATA says `License: BSD` (no SPDX expression, no classifier); the bundled `LICENSE` is the BSD 3-Clause text, "Copyright (c) Soumith Chintala 2016". `scripts/check_licenses.py` classifies it OK, so no allowlist entry. Requires `torch==2.9.1`, numpy, pillow only (no new transitive packages) | yes | Keep LICENSE |
| transformers | 5.17.0 | Apache-2.0 | METADATA `License: Apache 2.0 License`; `LICENSE` read | yes | Keep LICENSE/NOTICE; state changes if modified; patent grant |

Transitive dependencies (33), same venv:

| Component | Version | SPDX license | Evidence (read this session) | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | METADATA License-Expression | yes | Keep notice |
| anyio | 4.15.1 | MIT | METADATA License-Expression | yes | Keep notice |
| asgiref | 3.12.1 | BSD-3-Clause | METADATA License field; `LICENSE` read | yes | Keep notice |
| certifi | 2026.7.22 | MPL-2.0 | METADATA License + classifier; `licenses/LICENSE` read (it is Mozilla's CA bundle) | yes-with-conditions | MPL-2.0 file-level copyleft: unmodified use is fine; if you modify certifi files, publish those files' source |
| click | 8.5.0 | BSD-3-Clause | METADATA License-Expression | yes | Keep notice |
| filelock | 4.2.0 | MIT | METADATA License-Expression + classifier | yes | Keep notice |
| fsspec | 2026.9.0 | BSD-3-Clause | METADATA License-Expression | yes | Keep notice |
| h11 | 0.16.0 | MIT | METADATA License + classifier | yes | Keep notice |
| hf-xet | 1.7.0 | Apache-2.0 | METADATA License-Expression | yes | Keep LICENSE/NOTICE |
| httpcore | 1.0.9 | BSD-3-Clause | METADATA License-Expression + classifier | yes | Keep notice |
| httpx | 0.28.1 | BSD-3-Clause | METADATA License field + classifier | yes | Keep notice |
| huggingface_hub | 1.33.0 | Apache-2.0 | METADATA License + classifier; `LICENSE` file present | yes | Keep LICENSE/NOTICE |
| idna | 3.20 | BSD-3-Clause | METADATA License-Expression | yes | Keep notice |
| Jinja2 | 3.1.6 | BSD-3-Clause (METADATA: classifier 'BSD License'; text has 3-clause terms) | `licenses/LICENSE.txt` read (Copyright Pallets) | yes | Keep notice |
| markdown-it-py | 4.2.0 | MIT | METADATA classifier; `licenses/LICENSE` read | yes | Keep notice |
| MarkupSafe | 3.0.4 | BSD-3-Clause | METADATA License-Expression | yes | Keep notice |
| mdurl | 0.1.2 | MIT | METADATA classifier; `LICENSE` read | yes | Keep notice |
| mpmath | 1.3.0 | BSD-3-Clause (METADATA: 'BSD'; text has 3-clause terms) | `LICENSE` read | yes | Keep notice |
| networkx | 3.7 | BSD-3-Clause | METADATA License-Expression | yes | Keep notice |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | METADATA License-Expression | yes | Choose either; keep notice |
| Pygments | 2.21.0 | BSD-2-Clause | METADATA License-Expression | yes | Keep notice |
| PyYAML | 6.0.3 | MIT | METADATA License + classifier | yes | Keep notice |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python | METADATA License-Expression | yes | Keep both notices |
| rich | 15.0.0 | MIT | METADATA License + classifier | yes | Keep notice |
| safetensors | 0.8.0 | Apache-2.0 | METADATA classifier; `licenses/LICENSE` read | yes | Keep LICENSE/NOTICE |
| setuptools | 84.0.0 | MIT | METADATA License-Expression | yes | Keep notice |
| shellingham | 1.5.4 | ISC | METADATA License field + classifier | yes | Keep notice |
| sqlparse | 0.6.0 | BSD-3-Clause (METADATA: classifier 'BSD License'; text has 3-clause terms) | `licenses/LICENSE` read | yes | Keep notice |
| sympy | 1.14.0 | BSD-3-Clause (METADATA: 'BSD'; LICENSE text has 3-clause terms plus MIT-licensed portions) | `licenses/LICENSE` read | yes | Keep notice |
| tokenizers | 0.23.3 | Apache-2.0 | METADATA classifier only (wheel ships no LICENSE file); upstream https://raw.githubusercontent.com/huggingface/tokenizers/main/LICENSE fetched = Apache-2.0 | yes | Keep LICENSE/NOTICE; wheel omits the file, so reproduce it in notices |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | METADATA License field; file is named `LICENCE` | yes-with-conditions | MPL-2.0 file-level copyleft (only some files); unmodified use fine |
| typer | 0.27.3 | MIT | METADATA License-Expression | yes | Keep notice |
| typing_extensions | 4.16.0 | PSF-2.0 | METADATA License-Expression | yes | Keep notice |

Not installed on Linux/macOS (environment markers): `colorama` (Windows), `tomli` (Python < 3.11), `exceptiongroup`
(Python < 3.11), `tzdata` (Windows, via Django). Not read; if you build for Windows, re-run the gate.

If torch is installed from default PyPI on Linux x86_64 (not the Docker path), these are added (PyPI `requires_dist` fetched):
15 `nvidia-*-cu12` packages and `triton==3.5.1`.

| Component | Version | SPDX license | Evidence | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| nvidia-cuda-runtime-cu12 (sample of the 15) | 12.9.79 (latest on PyPI; torch pins 12.8.90) | `LicenseRef-NVIDIA-Proprietary` | https://pypi.org/pypi/nvidia-cuda-runtime-cu12/json (fetched) | unverified (NVIDIA proprietary EULA; terms not read) | Read the EULA before shipping a GPU image |
| nvidia-cudnn-cu12 | 9.27.0.42 (latest) | no license metadata on PyPI | https://pypi.org/pypi/nvidia-cudnn-cu12/json (fetched, empty) | unverified | As above |
| triton | 3.8.0 (latest; torch pins 3.5.1) | MIT | https://pypi.org/pypi/triton/json (fetched) | yes | Keep notice |

## 3. SDK, CLI, tests and CI tooling

`sdk/python/pyproject.toml` depends only on `pydantic>=2.6,<3`; `cli/pyproject.toml` only on `athena-sdk`;
root `pytest.ini` declares no packages (it needs pytest to run). Resolved in the `root` venv:

| Component | Version | SPDX license | Evidence (read this session) | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| pydantic | 2.14.1 | MIT | METADATA License-Expression | yes | Keep notice |
| pydantic-core | 2.50.1 | MIT | METADATA License-Expression | yes | Keep notice |
| annotated-types | 0.8.0 | MIT | METADATA | yes | Keep notice |
| typing-inspection | 0.4.4 | MIT | METADATA | yes | Keep notice |
| typing_extensions | 4.16.0 | PSF-2.0 | METADATA | yes | Keep notice |
| pytest (tests only, not shipped) | 9.1.1 | MIT | METADATA | yes | none (dev) |
| pluggy / iniconfig (pytest deps) | 1.7.0 / 2.3.1 | MIT | METADATA | yes | none (dev) |
| packaging, Pygments (pytest deps) | 26.3 / 2.21.0 | Apache-2.0 OR BSD-2-Clause / BSD-2-Clause | METADATA | yes | none (dev) |
| ruff (lint in `.github/workflows/ci.yml`, not shipped) | 0.17.1 (latest; not installed by me) | MIT | https://pypi.org/pypi/ruff/json `license_expression` (fetched) | yes | none (dev) |

`cli/pyproject.toml` packages `examples`, `examples.adapters` etc. into the `athena-cli` wheel, so the example
sources, **including `yolo_adapter.py`**, are distributed inside that wheel.

## 4. `examples/`: the third-party YOLO adapter

* File read: `examples/adapters/yolo_adapter.py` (SPDX header `Apache-2.0`). It does `try: from ultralytics import YOLO / except ImportError: YOLO = None`
  and raises `ImportError` in `__init__` when missing.
* `ultralytics` appears in **no** requirements file, no `pyproject.toml` (`sdk`, `cli`), no workflow and not in the Dockerfile
  (searched the whole tree). `tests/test_interfaces_conformance.py` skips the YOLO test unless `importlib.util.find_spec("ultralytics")` finds it.
  The test uses `YOLOAdapter("yolov8n.yaml")` (untrained architecture config bundled with ultralytics, no weights download).
* **Ultralytics license:** `AGPL-3.0` (PyPI metadata for ultralytics 8.4.178 and the repo `LICENSE`, which starts "GNU AFFERO GENERAL PUBLIC LICENSE Version 3"; both fetched).
  Its README (fetched) offers a separate "Ultralytics Enterprise License" for commercial use that avoids the AGPL
  requirements, and says that license covers the software "and AI models".
* **What is linked into the shipped product:** nothing copyleft. The adapter only *references* ultralytics at runtime if the user installs it.
  The Docker build context is `web/` only (no `examples/`, SDK or CLI in the image) and no file under `web/` imports `athena_sdk`/`athena_cli` (grep).
* **What is only documented as optional:** `docs/getting-started.md`, `docs/guides/bring-your-own-model.md`, `docs/guides/writing-a-custom-adapter.md`
  and `docs/api-reference.md` name the YOLO adapter and tell users to `pip install ultralytics`.
  Those docs do not currently warn that doing so brings in AGPL-3.0 (pre-trained YOLO `.pt` weights are covered by the same
  Ultralytics licensing per its README; I did not read the weights' own license text, so that is **unverified**).
* **Apache-2.0 compatibility:** shipping Apache-2.0 source that imports an AGPL library is generally considered fine for the source.
  The AGPL applies to the combined work **whoever installs ultralytics and distributes it, or lets users interact with it over a network** (AGPL §13).
  So a company running Athena + ultralytics as a closed service needs the Enterprise license or must publish its modifications under AGPL. Needs a lawyer to confirm the exact boundary.

## 5. Model weights downloaded at runtime (`web/projects/autolabel/sam_clip.py`)

Weights are **not** in the repo or the Docker image. `SamModel.from_pretrained` / `CLIPModel.from_pretrained`
fetch them from the Hugging Face Hub on the operator's worker the first time (defaults in `web/athena_web/settings.py`:
`ATHENA_SAM_MODEL=facebook/sam-vit-base`, `ATHENA_CLIP_MODEL=openai/clip-vit-base-patch32`; `ATHENA_AUTOLABEL_ALLOW_DOWNLOAD=0` forbids download).
So Athena OS does not redistribute them, but its docs recommend them and the operator's service uses them.

| Component | Version | SPDX license | Evidence | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| `facebook/sam-vit-base` weights | HF rev `70c1a07f894e` | Apache-2.0 | HF API https://huggingface.co/api/models/facebook/sam-vit-base (tag `license:apache-2.0`); model card https://huggingface.co/facebook/sam-vit-base/raw/main/README.md front matter `license: apache-2.0`; upstream https://raw.githubusercontent.com/facebookresearch/segment-anything/main/LICENSE (Apache 2.0) and its README "The model is licensed under the Apache 2.0 license" | yes | Apache-2.0 notice/attribution; the HF repo itself contains no LICENSE file, only the metadata |
| `openai/clip-vit-base-patch32` weights | HF rev `3d74acf9a28c` | **No license declared on the HF repo.** Upstream code repo is MIT | HF API https://huggingface.co/api/models/openai/clip-vit-base-patch32 (no `license:` tag, no LICENSE in file list); https://raw.githubusercontent.com/openai/CLIP/main/LICENSE ("MIT License, Copyright (c) 2021 OpenAI") | **yes-with-conditions / partly unverified**: the MIT text covers "the Software" in the code repo. I found no statement that explicitly grants the *weights* under MIT (the CLIP README mentions no license). Widely treated as MIT in practice | If MIT applies: keep OpenAI's copyright + permission notice |

### Model-card intended-use language

**CLIP** (HF model card https://huggingface.co/openai/clip-vit-base-patch32/raw/main/README.md; same text in
https://raw.githubusercontent.com/openai/CLIP/main/model-card.md). Quoted:

> "The model is intended as a research output for research communities."
>
> "**Any** deployed use case of the model - whether commercial or not - is currently out of scope. Non-deployed use cases such as image search in a constrained environment, are also not recommended unless there is thorough in-domain testing of the model with a specific, fixed class taxonomy."
>
> "Certain use cases which would fall under the domain of surveillance and facial recognition are always out-of-scope regardless of performance of the model."
>
> "Since the model has not been purposefully trained in or evaluated on any languages other than English, its use should be limited to English language use cases."
>
> (Data section) "We do not intend for this dataset to be used as the basis for any commercial or deployed model and will not be releasing the dataset."

This is a **distinct non-license caveat**: it is an intended-use statement, not a license restriction (the model card has no license field and the
upstream license is MIT, which has no field-of-use limit). It still matters: Athena's auto-labeler is a deployed use, and a customer or regulator can point to the
vendor's own statement that deployed use is out of scope. Athena's design (CLIP only proposes labels that a human reviews) mitigates but does not remove it.

**SAM** (https://huggingface.co/facebook/sam-vit-base/raw/main/README.md): the card has no intended-use or out-of-scope section and no commercial restriction.
It says the model is trained on a dataset of 11 million images and 1.1 billion masks ("11M licensed and privacy respecting images").

### Training-data caveats

* **CLIP:** trained on "publicly available image-caption data ... crawling a handful of websites and using commonly-used pre-existing image datasets such as YFCC100M", dataset not released
  (model card). The copyright/privacy status of web-scraped training data is unsettled law; no per-image licensing exists. Model card also documents bias findings (Fairface), and English-only.
* **SAM:** weights are Apache-2.0, but the SA-1B *dataset* is distributed under the "SA-1B Dataset Research License" (SAM README, line 118: "By downloading the datasets you agree that you have read and accepted the terms of the SA-1B Dataset Research License"). That governs downloading the dataset, not using the weights. I could not fetch the dataset license text (ai.meta.com returned HTTP 401), so its terms are **unverified**.
* The Hugging Face Hub's own terms of service and any gated-model terms apply to the download itself; I did not review them (unverified).

## 6. Static assets, templates, fonts, CDNs

Read: `web/projects/static/projects/console.css`, `console.js`, `web/projects/templates/{projects/console.html,registration/login.html}`.

* The only references are `{% static 'projects/console.css' %}` and `console.js`. A search for `http(s)://`, `@import`, `@font-face`, `<link`, `<script` found **no external URL, CDN, web font, icon set or third-party JS/CSS**.
* Font stack is `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`: system fonts, nothing is shipped. No images/SVG/woff/ico files under `web/projects`.
* No Django admin in `INSTALLED_APPS`, so Django's bundled admin static (jQuery, Select2, etc.) is not shipped. `web/staticfiles/` is build output (git-ignored) containing only the two own files.
* No copyright/license banners in `console.js`/`console.css`: no vendored third-party code found by inspection. `.DS_Store` in `web/projects/templates/` is an untracked junk file, not a license matter.

| Component | Version | SPDX license | Evidence | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| Console CSS/JS/templates | n/a | Apache-2.0 (first-party) | files read as above | yes | none |
| Third-party fonts/icons/CDN assets | none | n/a | grep of templates/static | n/a | none |

## 7. Docker base image and GitHub Actions

| Component | Version | SPDX license | Evidence | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| `python:3.13-slim` (CPython) | 3.13.x (3.13.16 in docker-library master at audit time) | PSF-2.0 | https://docs.python.org/3/license.html (fetched, PSF License); https://raw.githubusercontent.com/docker-library/python/master/3.13/slim-trixie/Dockerfile | yes | Keep PSF notice |
| docker-library/python build scripts | n/a | MIT ("Copyright (c) 2014 Docker, Inc.") | https://raw.githubusercontent.com/docker-library/python/master/LICENSE | yes | n/a (not shipped) |
| Debian trixie-slim userland under it (61 source packages in https://raw.githubusercontent.com/docker-library/repo-info/master/repos/python/local/3.13-slim.md, e.g. bash, coreutils, dpkg, glibc, apt, util-linux, openssl, systemd) | per tag | mixed: GPL-2/2+/3, LGPL-2.1/3, BSD, X11, MIT/Expat, public-domain, GFDL, Artistic, MPL, ... as parsed from Debian copyright files in that page | same page (fetched; 47 of 61 sources had parseable license lines) | yes-with-conditions | If you **distribute the image** you must be able to provide corresponding source for GPL/LGPL packages (Debian publishes it; the repo-info page lists `apt-get source` URIs) and keep their license texts (already at `/usr/share/doc/*/copyright`) |
| actions/checkout@v4, actions/setup-python@v5 (`web-ci.yml`, `ci.yml`) | v4 / v5 | MIT ("Copyright (c) 2018 GitHub, Inc. and contributors") | https://raw.githubusercontent.com/actions/checkout/main/LICENSE and `.../actions/setup-python/main/LICENSE` (both fetched; the `main` branch, not the exact tags: tag text unverified) | yes | none (CI only, not distributed) |
| Docker CLI/engine on `ubuntu-latest` runner | n/a | not assessed | n/a | unverified | n/a (CI only) |

Dockerfile also runs `pip install` (pip 24.2/26.x, MIT) and does no `apt-get install`, so no extra Debian packages beyond the base are added.

## Compatibility with distributing Athena OS under Apache-2.0

| License class (found here) | Compatible with Apache-2.0 distribution? | What you owe |
|---|---|---|
| MIT, MIT-CMU, BSD-2/3, ISC, PSF-2.0, CNRI-Python | Yes | Reproduce copyright + license text with binaries/images (`THIRD_PARTY_NOTICES.md`; ship the dist-info licenses) |
| Apache-2.0 (transformers, hf-xet, huggingface_hub, safetensors, tokenizers, regex, SAM weights) | Yes (same license) | Include license; preserve upstream NOTICE files if the package has one; state changes. Patent grant flows downstream, with the usual termination if you sue over the work |
| MPL-2.0 (certifi, tqdm part) | Yes as a separate, unmodified component | If you modify those files and distribute, publish the modified MPL files. Does not affect Athena's own files |
| GPL-3.0-with-GCC-exception (GCC runtime library bundled inside numpy wheels) | Yes: the exception allows linking into non-GPL programs | Keep numpy's LICENSE.txt |
| GPL/LGPL in torch's aggregated third-party notices and Debian packages | Yes for separately-distributed binaries/aggregation; not linked into Athena's Python code | Source offer for the Debian image; LGPL libs must remain replaceable. Which torch-bundled components are really linked into the CPU wheel binary is **unverified** |
| AGPL-3.0 (ultralytics, optional) | **Not compatible with a closed commercial service**; Apache-2.0 code can call into it, but the combination must meet AGPL (§13 network clause) | Don't ship it; document the trap; or buy an Enterprise license |
| NVIDIA proprietary (CUDA wheels, only if PyPI torch is used on Linux x86_64) | Redistribution governed by NVIDIA's EULA | Read it first (unverified) |
| CC-BY-4.0 (project docs) | Docs only; Apache-2.0 code unaffected | Attribution when reusing docs |

Apache-2.0 §4 requires recipients to get a copy of the license, modification notices, and any upstream NOTICE text.
This repo has **no `NOTICE` file**. `THIRD_PARTY_NOTICES.md` (repo root) now carries the attributions.

## Risks and caveats (ranked by severity)

1. **HIGH, conditional: AGPL-3.0 `ultralytics` via the YOLO example and docs.** Not linked into the product, but the CLI wheel ships
   the adapter, the docs say "pip install ultralytics", and nothing warns that this makes the user's deployment subject to AGPL-3.0 (including §13 for network use)
   or requires an Ultralytics Enterprise license. Suggest: add a licensing warning to the three docs and the adapter docstring (I did not edit them, out of scope), and keep ultralytics out of every requirements file and CI image. Pre-trained YOLO weights: unverified.
2. **HIGH (non-license): CLIP model card says deployed use, commercial or not, is out of scope**, plus the HF repo declares no license and the training set is undisclosed web-scrape.
   The MIT license on the upstream repo has no field-of-use limit, but the vendor statement is real. Treat the auto-labeler as an optional, human-reviewed feature, put the model's limits in the product docs, and consider a CLIP-class model with an explicit weights license (not evaluated here).
3. **MEDIUM: torch wheel selection.** `web/requirements-autolabel.txt` alone on Linux x86_64 installs the PyPI CUDA build, pulling 15 NVIDIA proprietary `nvidia-*-cu12` packages whose EULA I did not read. `web/Dockerfile` avoids this with `--extra-index-url https://download.pytorch.org/whl/cpu` and I confirmed pip then selects `2.9.1+cpu` with no NVIDIA deps, but `web/DEPLOYMENT.md` "Without Docker" tells users to run plain `pip install -r requirements-autolabel.txt`. Document the CPU index there (or ship GPU images only after reading the EULA). CI now scans the CPU resolution, so it will not catch NVIDIA packages by itself.
4. **MEDIUM: Debian base image obligations** when you distribute (or sell) the container image: GPL/LGPL source availability and license texts. Debian-hosted source satisfies this in practice only if you keep the exact tag/digest reproducible; a written offer or a mirror is the cautious route (lawyer).
5. **LOW: MPL-2.0 (certifi, tqdm)**, file-level copyleft; fine unmodified.
6. **LOW: attribution gap.** No NOTICE file and no THIRD_PARTY_NOTICES before this audit; the wheel/image should ship the notices file. `tokenizers` wheel ships no LICENSE file (Apache-2.0 per upstream).
7. **LOW: license drift.** Transitive deps are unpinned (`web/requirements*.txt` pins only the 7 direct ones); a future resolve can change a transitive license. The new CI job `licenses` (`scripts/check_licenses.py`) guards that for new PRs; it does not run on a schedule.
8. **LOW (repo hygiene, not my files):** `.github/workflows/ci.yml` uses `working-directory: athena-oss` and `paths: athena-oss/**`, which does not match this repository layout, and `docs/LICENSE` also refers to `athena-oss/...` paths. The root-package CI may never trigger here. I left both untouched.
9. Python 3.13 slim tag, Debian codename and package versions change over time; the Debian figures above are from one repo-info snapshot (unverified for your exact digest).

## Needs a lawyer / owner decision

1. Whether Athena may offer the CLIP-based auto-labeler as a hosted/paid feature given OpenAI's "any deployed use ... out of scope" statement and the lack of an explicit weights license on the HF repo. Or whether to swap or gate it.
2. Training-data exposure for CLIP (scraped web data) and SAM (SA-1B research license for the dataset) for your jurisdictions and customers' contracts (e.g. IP indemnities you may be asked to give).
3. The AGPL boundary for the YOLO adapter: keep it in the Apache-2.0 tree with a warning, move it out of the `athena-cli` wheel, or remove it. And whether to buy an Ultralytics Enterprise license if customers use it.
4. Container distribution: the exact GPL/LGPL source-offer mechanism for the Debian base layer and torch's bundled components.
5. Whether to publish GPU images (NVIDIA EULA review).
6. Whether to add a `NOTICE` file and which attribution text to put in it (Apache-2.0 §4(d)); whether docs stay CC-BY-4.0 while code is Apache-2.0.
7. Trademark ("Athena", "Hugging Face", "Ultralytics", etc.) is out of scope for this audit.

## What I could not verify

* The NVIDIA EULA text and `nvidia-cudnn-cu12` license (empty metadata).
* SA-1B dataset license text; Hugging Face Hub terms; Ultralytics weights license text.
* Whether OpenAI explicitly licensed the CLIP *weights* under MIT (only the code repo LICENSE found).
* Exact third-party components linked inside the torch/numpy/Pillow Linux wheels (I inspected the macOS arm64 wheels' license bundles; the Linux CPU wheel's own bundle was not downloaded, only its metadata).
* Action tags v4/v5 license (read `main`).
* Windows-only transitive deps.
* The gate has been run on macOS arm64 envs only; CI will run it on Linux.

## How to re-run

```bash
pip install -r web/requirements.txt
pip install --extra-index-url https://download.pytorch.org/whl/cpu -r web/requirements-autolabel.txt
pip install ./sdk/python ./cli
python scripts/check_licenses.py --quiet        # exit 1 on AGPL/GPL/SSPL/Commons Clause/CC-BY-NC/non-commercial
python -m pytest tests/test_license_gate.py
```

Reviewed exceptions go in `scripts/license_allowlist.json` (each needs a `reason`). Deny rules: AGPL, GPL, SSPL, Commons Clause, BUSL,
CC-BY-NC, "non-commercial" fail. LGPL, MPL/EPL/CDDL, "GPL with exception", proprietary/`LicenseRef-`, unrecognized strings warn.
Distributions with no license metadata are reported as UNKNOWN (fail with `--strict-unknown`).


## Resolutions

*Added 2026-10-16 after the audit (docs/tests/packaging notes only; no dependency or code behaviour changed).*

| Audit item | Status |
|---|---|
| Caveat 1: no AGPL warning where docs mention `ultralytics` | **Done.** Licence-warning blockquotes now sit in `docs/getting-started.md`, `docs/guides/bring-your-own-model.md`, `docs/guides/writing-a-custom-adapter.md` and `docs/api-reference.md`, pointing back to section 4 above. The root `README.md` also links here. **Still open:** the same warning in the `examples/adapters/yolo_adapter.py` docstring and its `ImportError` message (source code, not changed here), and the question of whether the adapter stays in the `athena-cli` wheel (owner/lawyer decision 3). |
| Caveat 2: CLIP deployed-use language not surfaced to operators | **Partly done.** `web/DEPLOYMENT.md` has a new "Licences and model terms" section: SAM (Apache-2.0) and the CLIP caveat as this audit recorded it (no licence declared on the Hugging Face repo; model card calls deployed use out of scope), with operator options (evaluate, set `ATHENA_AUTOLABEL_BACKEND=disabled`, or point `ATHENA_CLIP_MODEL` at a different model after your own licence review). **Still open:** the hosted/paid-feature decision (owner/lawyer decision 1) and any in-console notice. |
| Training/inference follow-ups (W8) | **Done.** `torchvision==0.24.1` added to this audit and `THIRD_PARTY_NOTICES.md` (BSD-3-Clause; gate result OK with no allowlist change: ML venv of 16 distributions, OK=16, WARN=0, UNKNOWN=0, FAIL=0). The ML-venv resolution is macOS arm64; the Linux CPU wheel's own licence bundle was not downloaded. `examples/adapters/yolo_adapter.py` now carries the AGPL-3.0 warning in its docstring and `ImportError` text (message text only), closing the "still open" part of caveat 1; whether the adapter stays in the `athena-cli` wheel remains an owner decision. |
| Caveat 3: "Without Docker" installed plain `requirements-autolabel.txt` | **Done.** `web/DEPLOYMENT.md` now uses `pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-autolabel.txt`, the same command `web/Dockerfile` runs, with the reason (PyPI CUDA wheels pull in NVIDIA packages) stated. The wording was not re-resolved on Linux in this pass; it reuses the approach verified above. |
| Risk 8: CI path / layout mismatch | **Reported, not fixed** (owner decides; `.github/` untouched). See below. |
| Pydantic floor (found while fixing `tests/test_contracts.py::test_generated_schema_matches_models`) | **Done.** `sdk/python/pyproject.toml` declared `pydantic>=2.6,<3`, but the checked-in `contracts/v1/model.schema.json` carries `"additionalProperties": true` on `metadata`, which Pydantic emits from 2.11 on. With 2.10.6 the schema generator output differs and the drift test fails; with 2.11.0 and 2.12.5 it passes unchanged. The checked-in schema was not stale; the declared floor was too low. Floor raised to `pydantic>=2.11,<3`; the test was not changed. |

### Root CI and licence-text path mismatch (reported only)

This repository's root *is* the project; there is no `athena-oss/` directory. Therefore:

* `.github/workflows/ci.yml`: both triggers filter on `paths: ["athena-oss/**"]`, and the `lint` and `test` jobs set `defaults.run.working-directory: athena-oss`. A push or PR can never match that path filter, and the job would fail on a missing directory if it did run. Proposed one-line fixes: change both `- "athena-oss/**"` entries to `- "**"` (or delete the `paths:` blocks), and change both `working-directory: athena-oss` lines to `working-directory: .` (or delete the `defaults:` blocks).
* `docs/LICENSE` names `athena-oss/docs/`, `athena-oss/contracts/README.md` and `athena-oss/README.md`; they should read `docs/`, `contracts/README.md` and `README.md`. Same pattern: `docs/getting-started.md` says "From the `athena-oss/` directory", and the `Homepage` URL in `sdk/python/pyproject.toml` ends in `/tree/main/athena-oss`. (Possibly intended if this tree is later published as a subdirectory of a larger repo; the owner should decide which layout is canonical.)
