# License audit: commercial use of Athena OS and everything it ships or downloads

> **This is an engineering audit, not legal advice.** It records what the package metadata,
> LICENSE files and model cards say, and flags what needs a lawyer. Anything I could not read
> or check is marked **unverified** rather than guessed. Audit date: 2026-10-16. **Addendum 2026-10-24:** the optional D-FINE
> training tool added one package (`scipy`) and two sets of pinned model weights; their rows are marked *added 2026-10-24*. **Addendum 2026-11-19:** the Wave 0/1 multimodal
> work added one optional package (`onnxruntime`, needed only by the `audio-wav-cnn` backend when an ONNX artifact is wanted) and one set
> of downloaded-at-runtime weights (`microsoft/trocr-small-handwritten`, fine-tuned by the `ocr-trocr` handwriting backend); their rows are marked
> *added 2026-11-19*. The new backends also use `torch` and `transformers`, both already covered below. Nothing else was re-audited
> in that addendum, so the version numbers in the original tables are those of 2026-10-16.
>
> **Addendum 2026-11-19 (second pass, the M0 completion pass).** This pass re-fetched every Hugging Face record and the
> COCO terms (sources and dates in the rows), audited four pinned packages the earlier passes missed (`django-storages`, `boto3`
> and its transitive `botocore`/`s3transfer`/`jmespath`/`python-dateutil`/`urllib3`, and `cryptography` with its `cffi`/`pycparser`
> chain — all in `web/requirements.txt` since the S3-storage and connector-secrets features, never previously listed here), did
> the **formal licence read of the COCO and YOLO export-format specifications** the roadmap had never done (new section 5b), added
> the dev-only browser tooling (Playwright and its downloaded browsers, new section 7b), and recorded two findings that change
> earlier conclusions: **the TrOCR model card no longer carries any licence tag** (the earlier `license: mit` claim could not be
> reproduced today — see the TrOCR row), and the COCO terms of use were located and read in full. Rows touched by this pass are
> marked *re-checked 2026-11-19 (second pass)*.

## Verdict

| Area | Commercial use (self-hosting, selling services around it) |
|---|---|
| Athena OS itself (Apache-2.0) | **Yes** |
| Console runtime deps: Django, Pillow, whitenoise, gunicorn + 33 transitive | **Yes**, permissive. MPL-2.0 only for certifi and tqdm (unmodified use is fine). *Re-checked 2026-11-19 (second pass):* the S3/connector half of `web/requirements.txt` (`django-storages`, `boto3` stack, `cryptography` stack) is also all permissive (BSD/Apache/MIT); rows in section 2a |
| Auto-label and training deps: numpy, torch, torchvision, transformers + transitive | **Yes**, permissive, **provided the CPU torch wheel is used** (see caveat 3). *Added 2026-10-24:* `scipy` for the optional D-FINE tool, BSD-3-Clause; see its row in section 2 for the libraries its wheels bundle. *Added 2026-11-19:* `onnxruntime` for the optional audio backend's ONNX export, MIT; see its row in section 2 |
| SDK/CLI deps: pydantic 2.14.1 + 4 transitive | **Yes**, MIT/PSF |
| `examples/adapters/yolo_adapter.py` | Adapter source is Apache-2.0 and **links nothing copyleft** at ship time. `ultralytics` (AGPL-3.0) is *not* a declared dependency. **Using** the adapter with ultralytics installed pulls in AGPL-3.0: **NO** for a closed commercial service without an Ultralytics Enterprise license |
| SAM weights `facebook/sam-vit-base` | **Yes**, Apache-2.0 (declared on the model card; upstream LICENSE read; HF tag re-checked 2026-11-19, still `license:apache-2.0`) |
| CLIP weights `openai/clip-vit-base-patch32` | **Unclear, treat as risk.** The Hugging Face repo declares **no license** (re-confirmed 2026-11-19, second pass: no `license:` tag, no LICENSE file, unchanged since 2024-02-29); upstream code repo is MIT; the model card states **any deployed use, commercial or not, is out of scope** (not a license term, but a stated intended-use limit) **Owner decision 2026-10-17: stays enabled by default** as the owner's accepted business risk, not a legal clearance (see [Owner decision: CLIP](#owner-decision-clip-stays-enabled-2026-10-17)) |
| TrOCR weights `microsoft/trocr-small-handwritten` | **Unclear, treat as risk.** *Second pass 2026-11-19:* the Hugging Face repo **no longer carries any licence tag** (the earlier `license: mit` reading could not be reproduced; see the TrOCR row in section 5). No use restriction is stated on the card, but nothing grants the weights either |
| COCO / YOLO export-format specifications | **Yes** for Athena's own generated files (the formats carry no licence claims found; section 5b). The COCO *dataset* terms are a separate matter and are quoted there |
| Dev-only browser tooling (Playwright + downloaded Chromium/WebKit) | **Yes**, Apache-2.0 package + browser-binary terms; not shipped, CI/dev only (section 7b) |
| Static assets / fonts / CDNs | **Three OFL-1.1 fonts, self-hosted** (Anybody, Hanken Grotesk, Martian Mono). No CDN, no icon set, no third-party JS/CSS |
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
| scipy | 1.18.1 | BSD-3-Clause | *Added 2026-10-24.* Pinned in `web/requirements-autolabel.txt` for the optional D-FINE backend only (transformers' Hungarian matcher, used by the D-FINE training loss, raises "requires the scipy library" without it). Read from the installed `scipy-1.18.1.dist-info` (macOS arm64, Python 3.14.4, in the QA virtual environment): METADATA `License:` begins "Copyright (c) 2001-2002 Enthought, Inc. 2003, SciPy Developers." with classifier `License :: OSI Approved :: BSD License`; the bundled `LICENSE.txt` (937 lines) opens with the BSD 3-Clause text and then lists the third-party software the **binary wheel** bundles: OpenBLAS (BSD-3-Clause), LAPACK (BSD-3-Clause-Open-MPI), the GCC runtime library files `libgfortran*` / `libgcc*` (**GPL-3.0-or-later WITH GCC-exception-3.1**) and `libquadmath` (**LGPL-2.1-or-later**). `scripts/check_licenses.py` run in that environment (43 distributions): OK=43, WARN=0, UNKNOWN=0, FAIL=0, scipy classed OK (from metadata alone, so it does not see the bundled libraries) with no allowlist entry. The Linux wheel's own licence bundle was **not** downloaded here, so what the Docker image would contain is unverified (the macOS wheel's list is the evidence). The scipy 1.18.1 pin needs numpy>=2.0,<2.8, so it fits the numpy pin and the numpy 2.3.2 the D-FINE tool was exercised with | yes-with-conditions | Keep scipy's `LICENSE.txt` (ship the dist-info licence); the GCC runtime library exception permits non-GPL programs, as for numpy; LGPL `libquadmath` stays a replaceable shared library; do not strip the wheel's notices |
| onnxruntime | 1.20.1 | MIT | *Added 2026-11-19.* Pinned in `web/requirements-autolabel.txt` for the `audio-wav-cnn` backend only (`web/projects/training/audio_wav.py`): when it is installed the trained clip classifier is exported to an ONNX graph and predictions run through onnxruntime's `CPUExecutionProvider`; when it is missing the backend falls back to a torch state-dict artifact and torch-based prediction, so the package is optional and nothing else imports it (grep of `web/`, 2026-11-19). Read from the installed `onnxruntime-1.20.1.dist-info` (macOS arm64, in the ML virtual environment the audio tests ran in): METADATA `License: MIT License` and classifier `License :: OSI Approved :: MIT License`. **Not verified:** the third-party notices of the binary wheel were not read (onnxruntime bundles MLAS and its own build of Eigen; the wheel's `NOTICE`/`ThirdPartyNotices` files were not opened here), and the Linux wheel was not inspected. The version the pin resolves to on other platforms/Python versions may differ | yes-with-conditions (MIT; keep the wheel's licence and notice files) | Keep the MIT notice and any bundled third-party notices in the dist-info when distributing an image that installs it |
| openpyxl | 3.1.5 | MIT | *Added 2026-11-19.* Pinned in `web/requirements-autolabel.txt` for the `tabular-sklearn` backend's .xlsx path only (`web/projects/training/tabular_sklearn.py`): pandas' `read_excel` needs openpyxl as its Excel engine, and nothing else in the tree imports it (grep of `web/`, 2026-11-19; CSV table training, the other backends and the web process run without it — without it an .xlsx run fails in plain words telling the operator to install it or upload a CSV). Read from the installed `openpyxl-3.1.5.dist-info` (in the ML virtual environment the tabular tests ran in): METADATA says `License: MIT` with the MIT classifier, and the dist-info ships `LICENCE.rst` (the MIT text, not opened line-by-line here). Its one transitive `et-xmlfile` 2.0.0 (MIT, same authors) comes with it and is used only for *writing* Excel files, which Athena never does. **Not verified:** the Linux wheel was not inspected; the version the pin resolves to on other platforms/Python versions may differ | yes | Keep the MIT notice (ship the dist-info licence) if you distribute an image that installs it |

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

### 2a. The S3-storage and connector-secrets stack (`web/requirements.txt`; missed by earlier passes, audited 2026-11-19 second pass)

Four pinned direct dependencies in `web/requirements.txt` had no rows in the 2026-10-16 audit (that pass's venv
predates the S3-storage and vision-connector features): `django-storages[s3]==1.14.6`, `boto3==1.43.125`,
`httpx==0.28.1` (already listed among transitives — used directly by `web/projects/vision/client.py`), and
`cryptography==44.0.3` (used by `web/projects/vision/secrets.py`, Fernet). Verified in the tree on 2026-11-19:
`django-storages`/`boto3` back the optional S3 media storage (`web/athena_web/settings.py` `STORAGE_KIND`),
`cryptography` encrypts connector credentials. Because this pass had no fresh venv, the evidence below is
**PyPI JSON metadata fetched 2026-11-19** (URLs below), not installed `METADATA`; the wheel-bundled licence texts
were not opened (**unverified**), and the transitive set for the *pinned* versions (botocore 1.43.x etc.) was taken
from each pin's `requires_dist`, not from a resolve.

| Component | Version | SPDX license | Evidence (read this session) | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| django-storages | 1.14.6 (pin) | BSD-3-Clause | https://pypi.org/pypi/django-storages/json (fetched 2026-11-19): `license: BSD-3-Clause`, OSI classifier | yes | Keep notice |
| boto3 | 1.43.125 (pin) | Apache-2.0 | https://pypi.org/pypi/boto3/json (fetched 2026-11-19): `license: Apache-2.0` | yes | Keep LICENSE/NOTICE |
| botocore | 1.43.125 (from boto3's `botocore<1.44.0,>=1.43.125`) | Apache-2.0 | https://pypi.org/pypi/botocore/json (fetched 2026-11-19): `license: Apache-2.0`; requires `jmespath`, `python-dateutil`, `urllib3` only (extras not pulled) | yes | Keep LICENSE/NOTICE |
| s3transfer | 0.19.x (from boto3's pin `<0.20.0,>=0.19.0`) | Apache-2.0 | https://pypi.org/pypi/s3transfer/json (fetched 2026-11-19): `license: Apache License 2.0`, OSI classifier | yes | Keep notice |
| jmespath | 1.1.0 (latest; boto3/botocore accept `>=0.7.1,<2`) | MIT | https://pypi.org/pypi/jmespath/json (fetched 2026-11-19) | yes | Keep notice |
| python-dateutil | 4.0.0 (latest; botocore accepts `>=2.1,<3`) | BSD-3-Clause | https://pypi.org/pypi/python-dateutil/json (fetched 2026-11-19); pulls `six` (MIT, same fetch) | yes | Keep notice |
| urllib3 | 2.8.0 (latest; botocore accepts `>=1.25.4,!=2.2.0,<3`) | MIT | https://pypi.org/pypi/urllib3/json (fetched 2026-11-19) | yes | Keep notice |
| cryptography | 44.0.3 (pin) | Apache-2.0 OR BSD-3-Clause (dual, either may be chosen) | https://pypi.org/pypi/cryptography/44.0.3/json (fetched 2026-11-19): `license_expression` Apache-2.0 OR BSD-3-Clause; runtime requires only `cffi` (non-PyPy). **Unverified:** the wheel bundles OpenSSL; its NOTICE/ licence text inside the wheel was not opened in this pass | yes-with-conditions | Keep the wheel's licence and NOTICE (which carries the OpenSSL attribution — reproduce it when distributing an image) |
| cffi | 1.x (`>=1.12`) | MIT-0 | https://pypi.org/pypi/cffi/json (fetched 2026-11-19): `license_expression` MIT-0 (MIT with attribution dropped, Apache-2.0-compatible); pulls `pycparser` (BSD-3-Clause, same fetch) | yes | Keep notice |

No copyleft, no non-commercial term anywhere in this stack. The gate (`scripts/check_licenses.py`) has not been run
against an environment containing these pins in this pass (unverified); the 2026-10-16 venv evidently predates the
pins, so **the gate's "0 FAIL" claim did not cover these four packages** — re-run it before the next release.

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

## 5. Model weights downloaded at runtime (`web/projects/autolabel/sam_clip.py`, `web/projects/training/`)

Weights are **not** in the repo or the Docker image. `SamModel.from_pretrained` / `CLIPModel.from_pretrained`
fetch them from the Hugging Face Hub on the operator's worker the first time (defaults in `web/athena_web/settings.py`:
`ATHENA_SAM_MODEL=facebook/sam-vit-base`, `ATHENA_CLIP_MODEL=openai/clip-vit-base-patch32`; `ATHENA_AUTOLABEL_ALLOW_DOWNLOAD=0` forbids download).
So Athena OS does not redistribute them, but its docs recommend them and the operator's service uses them.

The training backends fetch their ready-made starts the same admin-gated way: the torchvision reference backend pulls
two weight files from `download.pytorch.org` (below), D-FINE and TrOCR pull pinned revisions from the HF Hub (rows
below). The full engineering read of the torchvision and D-FINE licence facts — the torchvision "Pre-trained Model
License" statement, the `weights.meta` having **no licence key**, the COCO terms summary and the unverified
dataset-terms-reach-weights question — lives in [`docs/guides/model-quality.md`](guides/model-quality.md)
("Licence: what was checked and what was not" and the D-FINE section); this audit cross-links it rather than
duplicating, and adds the re-checks of the second pass.

| Component | Version | SPDX license | Evidence | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| `facebook/sam-vit-base` weights | HF rev `70c1a07f894e` | Apache-2.0 | HF API https://huggingface.co/api/models/facebook/sam-vit-base (tag `license:apache-2.0`; **re-checked 2026-11-19, second pass: still `license:apache-2.0`, sha and lastModified unchanged since 2024-01-11**); model card https://huggingface.co/facebook/sam-vit-base/raw/main/README.md front matter `license: apache-2.0`; upstream https://raw.githubusercontent.com/facebookresearch/segment-anything/main/LICENSE (Apache 2.0) and its README "The model is licensed under the Apache 2.0 license" | yes | Apache-2.0 notice/attribution; the HF repo itself contains no LICENSE file, only the metadata |
| torchvision COCO-pretrained `FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1` (`fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth`, 74.2 MB) and ImageNet-pretrained `MobileNet_V3_Large_Weights.IMAGENET1K_V1` backbone (`mobilenet_v3_large-8738ca79.pth`) — the reference training backend's ready-made starts | torch/torchvision pins; the weight files themselves are unversioned downloads (URLs checksum-prefixed) | **No licence stated on the weights**; torchvision's README says pre-trained models "may have their own licenses or terms and conditions derived from the dataset used for training. It is your responsibility to determine whether you have permission to use the models for your use case." | URLs in `web/projects/training/torchvision_frcnn.py` (`https://download.pytorch.org/models/…`); **both fetched again 2026-11-19 (second pass): HTTP 200, unchanged addresses**. torchvision README "Pre-trained Model License" re-fetched 2026-11-19 from https://raw.githubusercontent.com/pytorch/vision/main/README.md (same statement; it also notes SWAG models are CC-BY-NC 4.0 — not used here). The weight files' `meta` carries **no licence key** (pinned by a test, per `docs/guides/model-quality.md`) | **yes-with-conditions / partly unverified** (engineering read): nothing restricts the MobileNetV3/FasterRCNN weights themselves; the open question is whether COCO's dataset terms (annotations CC-BY-4.0, images under Flickr terms — now quoted in full in section 5b) or ImageNet's terms *reach weights trained on them*. That is the same unverified question the D-FINE row carries, and it is for a lawyer | Download is admin-gated and checksum-prefixed like every ready-made model; if you redistribute a fine-tuned artifact, the derived-weights question is yours. See [`docs/guides/model-quality.md`](guides/model-quality.md) for the full table |
| `openai/clip-vit-base-patch32` weights | HF rev `3d74acf9a28c` | **No license declared on the HF repo.** Upstream code repo is MIT | HF API https://huggingface.co/api/models/openai/clip-vit-base-patch32 (no `license:` tag, no LICENSE in file list); https://raw.githubusercontent.com/openai/CLIP/main/LICENSE ("MIT License, Copyright (c) 2021 OpenAI") | **yes-with-conditions / partly unverified**: the MIT text covers "the Software" in the code repo. I found no statement that explicitly grants the *weights* under MIT (the CLIP README mentions no license). Widely treated as MIT in practice | If MIT applies: keep OpenAI's copyright + permission notice |
| `ustc-community/dfine-nano-coco` and `ustc-community/dfine-small-coco` weights (optional D-FINE tool) | HF revisions `066438d3d8f0…` (about 15 MB) and `f79e65b5fbb3…` (about 41 MB), each file pinned by size and SHA-256 in `CHECKPOINTS`, `web/projects/training/dfine.py` | Declared **Apache-2.0** on the model cards (HF API tags `license:apache-2.0`, `dataset:coco`; revisions match the pinned ones; first fetched 2026-10-23, **re-fetched 2026-11-19 second pass: both still `license:apache-2.0`, revisions unchanged**). *Added 2026-10-24.* | https://huggingface.co/ustc-community/dfine-nano-coco and `…/dfine-small-coco` (model cards and API tags); D-FINE code https://github.com/Peterande/D-FINE `LICENSE` (Apache 2.0); organisation card https://huggingface.co/ustc-community ("created by the Transformers team to maintain the D-FINE Object Detection model checkpoints, originally released by the University of Science and Technology"); the full engineering licence table is in [`docs/guides/model-quality.md`](guides/model-quality.md) (D-FINE section) | **yes-with-conditions / partly unverified**: Apache-2.0 on a model repository is that repository's licence; it is **not a statement about the data the weights were trained on** (COCO: annotations CC BY 4.0, the COCO Consortium does not own the images, use of them must follow the Flickr terms — now quoted in full in section 5b; whether that reaches trained weights is unverified, the same open question as the torchvision COCO start). Who licenses the *converted* weights is unverified. The picture-model (PPHGNetV2) backbone files the D-FINE authors loaded have pretraining data this project has not verified | Athena does not redistribute them (the worker fetches them from `huggingface.co`, redirects allowed only to `*.hf.co`, only when downloads are switched on, see `web/DEPLOYMENT.md`); Apache-2.0 notice if you redistribute. **Objects365-based checkpoints (`*-obj365`, `*-obj2coco`) are refused in code** (`refuse_checkpoint`): D-FINE's README says they "should not be assumed to be commercially cleared". Engineering read, not legal advice |
| `microsoft/trocr-small-handwritten` weights (handwriting-OCR tool, `ocr-trocr` backend) | The pinned commit `b4648cfa171985a6745f37ddd637e98c0da958ac` of the repository (`web/projects/training/ocr_trocr.py` fetches `config.json`, `preprocessor_config.json` and `pytorch_model.bin` from `…/resolve/<commit>/`, about 258 MB total per the backend's own estimate; every file is checked against its pinned size and SHA-256 after download and again when read from the cache — the D-FINE pattern. The repository has no `model.safetensors`, so the weights are the `pytorch_model.bin` LFS file whose `oid` the `/tree/main` listing publishes). *Added 2026-11-19; the SHA pin and the corrected file list 2026-11-19; licence status corrected in the second pass the same day (below).* | **CORRECTED (second pass 2026-11-19): the repository currently declares NO licence.** The earlier addendum's "MIT on the model card" could **not** be reproduced in this pass: the HF API record (fetched 2026-11-19) has no `license` in `cardData`, no `license:*` tag in `tags`, and the file list (`README.md`, `config.json`, `generation_config.json`, `preprocessor_config.json`, `pytorch_model.bin`, `sentencepiece.bpe.model`, `special_tokens_map.json`, `tokenizer_config.json`, `.gitattributes`) contains **no LICENSE file** at either `main` or the pinned commit; a text search of the model card finds no licence statement. Sibling repositories are inconsistent (`microsoft/trocr-base-handwritten` carries `license: mit`; `trocr-large-handwritten`, `trocr-small-printed`, `trocr-small-stage1` carry none), so the missing tag cannot be assumed to be a display accident. The earlier reading may have been of a since-changed tag, or an error; either way **the MIT claim is unverified as of 2026-11-19 and must not be relied on**. No use restriction is stated either (the card's only intended-use text is permissive), but with no licence grant the default is "all rights reserved" | https://huggingface.co/api/models/microsoft/trocr-small-handwritten (fetched 2026-11-19; also the `?full=true` listing and the model-card page); raw README at both `main` and the pinned commit (front matter read in full: `tags`, `widget` only). TrOCR paper: Li et al., "TrOCR: Transformer-based Optical Character Recognition with Pre-trained Models", arXiv:2109.10282; first released in the `microsoft/unilm` repository (linked from the card; **that** repository is MIT — https://raw.githubusercontent.com/microsoft/unilm/master/LICENSE, fetched 2026-11-19 — but unilm's MIT is a licence for the *code* repository and no statement in it addresses the trained weights on the HF hub) | **unclear / needs escalation.** With no licence on the weight repository, commercial use of the weights (and of models fine-tuned from them, which the `ocr-trocr` backend produces) is **not cleared by anything this audit could read**. Athena's code paths are unchanged (downloads remain admin-gated, pinned, checksum-verified, `huggingface.co`/`*.hf.co` only), but the in-product licence text (`LICENSE_NOTE`, `START_TEXT`, `licence_declared` in `web/projects/training/ocr_trocr.py`) still says "MIT licence on its model card", which this pass could not confirm — **that wording is now known to be unreliable and needs an owner decision** (correct it, and/or ask Microsoft / a lawyer, or switch to a base model with an explicit weights licence, e.g. a Microsoft model whose card carries one). The model card's stated training data (IAM Handwriting Database) carries its own terms for the *dataset*; the database's own site (fki.tic.heia-fr.ch) timed out when fetched on 2026-11-19, so its current terms are **unverified** |

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

### 5b. The COCO and YOLO export-format specifications (the formal read ROADMAP M2 asked for; done 2026-11-19 second pass)

Athena's dataset export writes two third-party interchange formats (`web/projects/datasets/writers.py`: COCO
instances JSON and YOLO txt + `classes.txt` + `data.yaml`). The ROADMAP M2 item had noted "formal licence review of
the COCO/YOLO format specifications and of the `info` block conventions has not been done here". This is that read.
Engineering read, not legal advice; a format *specification* is not obviously copyrightable in the way code is, but
the exercise matters for two real questions: (a) does anything on the spec pages restrict *implementing or writing*
the format, and (b) does Athena's output embed anyone's content.

**COCO.** The terms live at https://cocodataset.org/#termsofuse, which loads the tab content from
https://cocodataset.org/dataset/termsofuse.htm (fetched 2026-11-19; the page source is
github.com/cocodataset/cocodataset.github.io, last modified 2023-11-09). Three separate grants, quoted:

> "The annotations in this dataset along with this website belong to the COCO Consortium and are licensed under a Creative Commons Attribution 4.0 License."
>
> "The COCO Consortium does not own the copyright of the images. Use of the images must abide by the Flickr Terms of Use. The users of the images accept full responsibility for the use of the dataset, including but not limited to the use of any copies of copyrighted images that they may create from the dataset."
>
> "Software: Copyright (c) 2015, COCO Consortium. […] Redistribution and use software in source and binary form, with or without modification, are permitted provided that the following conditions are met: [BSD-style: retain the notice; reproduce in binary; no endorsement use without written permission] […] THIS SOFTWARE AND ANNOTATIONS ARE PROVIDED […] 'AS IS' […]" (their disclaimer runs over "SOFTWARE AND ANNOTATIONS")

Findings for Athena:

* **The format specification itself carries no licence claim.** The terms govern the *dataset* (annotations CC-BY-4.0, images under Flickr's terms, the site's own software BSD-style). Nothing on the page claims rights over the JSON schema (`info`/`licenses`/`images`/`annotations`/`categories` keys, `[x,y,w,h]` boxes), and the reference API (github.com/cocodataset/cocoapi, fetched 2026-11-19: GitHub reports no SPDX id; its `license.txt` is a BSD-style "Copyright (c) 2014, Piotr Dollar and Tsung-Yi Lin") likewise licences code, not the schema. Writing a COCO-format JSON of **your own** annotations with **your own** images (what Athena does) is not restricted by anything read. **Unverified:** whether any jurisdiction gives the consortium rights in the schema itself; a lawyer confirms, not this audit.
* **Athena does not copy COCO content.** `build_coco` in `web/projects/datasets/writers.py` writes only the project's own labels, boxes, image files and provenance (`info.description`, `info.contributor: "Athena OS"`, Athena's own extra keys, and `"licenses": []`). No COCO annotation, category list or image is embedded, so the CC-BY-4.0 attribution duty on COCO's *annotations* is not triggered by an Athena export.
* **The `"licenses": []` key**: COCO's own exports list the licence of each image there. Athena's writer emits an empty array, which is the honest statement (Athena does not know the licence of the operator's images). The ROADMAP's "info block conventions" question: Athena adds non-standard keys (`athena_version`, `manifest_sha256`, `bbox_format`, …) inside `info`, which the COCO format tolerates (unknown keys are ignored by common consumers) — an interoperability convention, not a licence issue.
* **The dataset terms matter elsewhere, not here**: the annotations-CC-BY-4.0 / images-Flickr terms are the *training-data* question already recorded against the torchvision COCO start and the D-FINE checkpoints (whether dataset terms reach weights trained on them is unverified, same as before). That question is about *using the dataset to train*, which Athena's operators do not do when they merely export.

**YOLO.** The format (one `class cx cy w h` txt per image, normalized 0–1; `classes.txt`; `data.yaml`) is documented
by Ultralytics at https://docs.ultralytics.com/datasets/detect/ and
https://docs.ultralytics.com/yolov5/tutorials/train_custom_data/ (both fetched 2026-11-19). Findings:

* **The pages claim no licence over the format.** It is described as a plain-text convention; the AGPL-3.0 notice shown in Ultralytics' docs applies to *their code and example files* (e.g. the comment inside their `coco8.yaml`), not to the label format. An AGPL notice on a *description* of a text format does not restrict writing files in that format.
* **Athena links none of it.** Nothing in Athena imports or derives from Ultralytics code to write YOLO files — `build_yolo` is original Apache-2.0 code emitting plain text (grep of `web/projects/datasets/writers.py`, no ultralytics reference). The only Ultralytics exposure in the repo remains the optional YOLO *adapter* (section 4), which is about their AGPL-3.0 software, not the format.
* **`data.yaml` conventions**: Athena's generated YAML (`path: .`, split dirs, `nc`, `names`) follows the layout Ultralytics' loader expects. No Ultralytics content is copied; the field names are the format's interface.
* **Unverified:** whether Ultralytics (or the original YOLO authors) could assert anything over tooling that *reads* their format in a jurisdiction that protects interfaces more strongly; nothing found asserts it today.

**Verdict for the format specs:** writing and distributing COCO-format and YOLO-format exports of the operator's own
data is **not restricted by anything this pass could read**. The formats' documentation is either unlicensed-claim-free
(COCO's schema, YOLO's convention) or expressly permissive about the parts it does licence (COCO's CC-BY-4.0
annotations, BSD-style site software — neither of which Athena copies). The M2 note can be considered answered at the
engineering level; the residual questions (schema/Interface rights in some jurisdiction, dataset-terms-reach-weights)
go to the lawyer checklist ([`docs/guides/legal-review-checklist.md`](guides/legal-review-checklist.md)).

## 6. Static assets, templates, fonts, CDNs

Read: `web/projects/static/projects/console.css`, `console.js`, `web/projects/templates/{projects/console.html,registration/login.html}`.

* The only references are `{% static 'projects/console.css' %}` and `console.js`. A search for `http(s)://`, `@import`, `@font-face`, `<link`, `<script` found **no external URL, CDN, web font, icon set or third-party JS/CSS**.
* **Updated 2026-10-17:** the console now ships three variable fonts, Anybody, Hanken Grotesk and Martian Mono (Latin and Latin Extended `woff2`, `web/projects/static/projects/fonts/`), all SIL OFL 1.1 (copyright lines and licence texts in `OFL-*.txt` beside them; checked against github.com/google/fonts `ofl/anybody`, `ofl/hankengrotesk`, `ofl/martianmono`). OFL permits commercial use and bundling; the fonts must not be sold alone, and the reserved font names stay with their authors. They are served from the console's own origin, which the Content Security Policy requires (`font-src 'self'`). Also shipped: the Athena mark as two small PNGs (`brand/`), Athena's own artwork.
* No Django admin in `INSTALLED_APPS`, so Django's bundled admin static (jQuery, Select2, etc.) is not shipped. `web/staticfiles/` is build output (git-ignored) containing only the two own files.
* No copyright/license banners in `console.js`/`console.css`: no vendored third-party code found by inspection. `.DS_Store` in `web/projects/templates/` is an untracked junk file, not a license matter.

| Component | Version | SPDX license | Evidence | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| Console CSS/JS/templates | n/a | Apache-2.0 (first-party) | files read as above | yes | none |
| Fonts: Anybody, Hanken Grotesk, Martian Mono | Google Fonts builds, v13 / v12 / v6 | OFL-1.1 | `fonts/OFL-*.txt`; upstream METADATA.pb | yes | keep the licence text with the fonts; do not sell the fonts alone |
| Icons / CDN assets | none | n/a | grep of templates/static | n/a | none |

## 7. Docker base image and GitHub Actions

| Component | Version | SPDX license | Evidence | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| `python:3.13-slim` (CPython) | 3.13.x (3.13.16 in docker-library master at audit time) | PSF-2.0 | https://docs.python.org/3/license.html (fetched, PSF License); https://raw.githubusercontent.com/docker-library/python/master/3.13/slim-trixie/Dockerfile | yes | Keep PSF notice |
| docker-library/python build scripts | n/a | MIT ("Copyright (c) 2014 Docker, Inc.") | https://raw.githubusercontent.com/docker-library/python/master/LICENSE | yes | n/a (not shipped) |
| Debian trixie-slim userland under it (61 source packages in https://raw.githubusercontent.com/docker-library/repo-info/master/repos/python/local/3.13-slim.md, e.g. bash, coreutils, dpkg, glibc, apt, util-linux, openssl, systemd) | per tag | mixed: GPL-2/2+/3, LGPL-2.1/3, BSD, X11, MIT/Expat, public-domain, GFDL, Artistic, MPL, ... as parsed from Debian copyright files in that page | same page (fetched; 47 of 61 sources had parseable license lines) | yes-with-conditions | If you **distribute the image** you must be able to provide corresponding source for GPL/LGPL packages (Debian publishes it; the repo-info page lists `apt-get source` URIs) and keep their license texts (already at `/usr/share/doc/*/copyright`) |
| actions/checkout@v4, actions/setup-python@v5 (`web-ci.yml`, `ci.yml`) | v4 / v5 | MIT ("Copyright (c) 2018 GitHub, Inc. and contributors") | https://raw.githubusercontent.com/actions/checkout/main/LICENSE and `.../actions/setup-python/main/LICENSE` (both fetched; the `main` branch, not the exact tags: tag text unverified) | yes | none (CI only, not distributed) |
| Docker CLI/engine on `ubuntu-latest` runner | n/a | not assessed | n/a | unverified | n/a (CI only) |

Dockerfile also runs `pip install` (pip 24.2/26.x, MIT) and does no `apt-get install`, so no extra Debian packages beyond the base are added.

### 7b. Browser tooling: Playwright and its downloaded browsers (dev/CI only; audited 2026-11-19 second pass)

The console has no browser dependency at runtime (no `package.json` anywhere in the tree — verified by find, and
`web/projects/tests_crossbrowser.py::test_playwright_is_not_a_runtime_dependency` pins that neither requirements file
mentions playwright and that `web/package.json` does not exist). Browser tooling is **development-only**, used by
`web/scripts/browser_smoke.mjs` (loaded via `createRequire` from `$PLAYWRIGHT_NODE_MODULES`,
`web/scripts/.smoke/node_modules` or `./node_modules`) and the optional CI job `browser-smoke`
(`workflow_dispatch` / console-front-end changes only). The browsers are downloaded into a playwright cache
(`npx playwright install chromium` / `webkit`), never into the repo or the Docker image.

| Component | Version | SPDX license | Evidence (read this session) | Commercial use OK? | Obligations |
|---|---|---|---|---|---|
| playwright (npm package, dev only) | 1.65.2 (latest; nothing pins a version — `run_browser_smoke.sh` installs whatever npm resolves) | Apache-2.0 | https://pypi.org/pypi/playwright/json (fetched 2026-11-19, `license_expression`: Apache-2.0; same license on the npm side: https://github.com/microsoft/playwright — GitHub API reports `Apache-2.0`, LICENSE fetched and is the Apache 2.0 text) | yes (n/a — not distributed) | none (dev tool; not in the product) |
| Chromium builds that `playwright install` downloads | per playwright version | BSD-3-Clause and other permissive (Chromium and its dependencies) | **Unverified:** the licence manifest inside the downloaded browser build (`LICENSE`/`credits` files in the playwright cache) was not opened in this pass. Chromium upstream is BSD-3-Clause with bundled permissive deps | presumed yes for **internal testing**; unverified for redistribution | Do not redistribute the downloaded browser builds; treat as internal test tooling |
| WebKit builds (Safari engine) | per playwright version | LGPL-2.1 and BSD-style (WebKit) | **Unverified** (same as above; WebKit upstream mixes LGPL-2.1 and BSD-2/BSD-3) | LGPL dynamic-linking obligations if ever distributed; not distributed here | Same: internal test tooling only |

Because none of this ships (not in requirements, not in the Docker build context, not in the wheel), the only exposure
is the developer's/CI's own machine, where the licence question is the same as running any dev tool. If a future change
redistributes the smoke kit with pinned browsers, re-audit then — especially the WebKit LGPL angle.

## Compatibility with distributing Athena OS under Apache-2.0

| License class (found here) | Compatible with Apache-2.0 distribution? | What you owe |
|---|---|---|
| MIT, MIT-CMU, BSD-2/3, ISC, PSF-2.0, CNRI-Python | Yes | Reproduce copyright + license text with binaries/images (`THIRD_PARTY_NOTICES.md`; ship the dist-info licenses) |
| Apache-2.0 (transformers, hf-xet, huggingface_hub, safetensors, tokenizers, regex, SAM weights) | Yes (same license) | Include license; preserve upstream NOTICE files if the package has one; state changes. Patent grant flows downstream, with the usual termination if you sue over the work |
| MPL-2.0 (certifi, tqdm part) | Yes as a separate, unmodified component | If you modify those files and distribute, publish the modified MPL files. Does not affect Athena's own files |
| GPL-3.0-with-GCC-exception (GCC runtime library bundled inside numpy wheels) | Yes: the exception allows linking into non-GPL programs | Keep numpy's LICENSE.txt |
| GPL/LGPL in torch's aggregated third-party notices and Debian packages | Yes for separately-distributed binaries/aggregation; not linked into Athena's Python code | Source offer for the Debian image; LGPL libs must remain replaceable. Which torch-bundled components are really linked into the CPU wheel binary is **unverified**. *Added 2026-10-24:* the macOS scipy wheel also bundles `libgfortran`/`libgcc` (GPL-3.0-or-later WITH GCC-exception-3.1) and `libquadmath` (LGPL-2.1-or-later); same treatment, and what the Linux wheel bundles is unverified |
| AGPL-3.0 (ultralytics, optional) | **Not compatible with a closed commercial service**; Apache-2.0 code can call into it, but the combination must meet AGPL (§13 network clause) | Don't ship it; document the trap; or buy an Enterprise license |
| NVIDIA proprietary (CUDA wheels, only if PyPI torch is used on Linux x86_64) | Redistribution governed by NVIDIA's EULA | Read it first (unverified) |
| CC-BY-4.0 (project docs) | Docs only; Apache-2.0 code unaffected | Attribution when reusing docs |

Apache-2.0 §4 requires recipients to get a copy of the license, modification notices, and any upstream NOTICE text.
This repo has **no `NOTICE` file**. `THIRD_PARTY_NOTICES.md` (repo root) now carries the attributions.

## Risks and caveats (ranked by severity)

1. **HIGH, conditional: AGPL-3.0 `ultralytics` via the YOLO example and docs.** Not linked into the product, but the CLI wheel ships
   the adapter, the docs say "pip install ultralytics", and nothing warns that this makes the user's deployment subject to AGPL-3.0 (including §13 for network use)
   or requires an Ultralytics Enterprise license. Suggest: add a licensing warning to the three docs and the adapter docstring (I did not edit them, out of scope), and keep ultralytics out of every requirements file and CI image. Pre-trained YOLO weights: unverified. *(The docstring and ImportError warning were since added — see Resolutions — the AGPL boundary itself stays a lawyer question.)*
2. **HIGH (non-license): CLIP model card says deployed use, commercial or not, is out of scope**, plus the HF repo declares no license (re-confirmed unchanged 2026-11-19) and the training set is undisclosed web-scrape.
   The MIT license on the upstream repo has no field-of-use limit, but the vendor statement is real. Treat the auto-labeler as an optional, human-reviewed feature, put the model's limits in the product docs, and consider a CLIP-class model with an explicit weights license (not evaluated here).
2b. **HIGH (new, second pass 2026-11-19): TrOCR weights carry no findable licence today.** The `microsoft/trocr-small-handwritten` HF repository has no `license:` tag, no LICENSE file and no licence text in the card (fetched 2026-11-19; details in the TrOCR row). The earlier addendum's "MIT on the model card" is **not reproducible** and must not be relied on, yet the `ocr-trocr` backend's user-facing `license_note`, start-screen text and `licence_declared` metadata still say MIT. This is worse than CLIP in one way (no licence at all found) and better in others (no out-of-scope statement, publisher is Microsoft with an MIT code repository, sibling `trocr-base-handwritten` is tagged MIT). Needs an owner decision: correct the in-product wording, verify with Microsoft or a lawyer, or switch base model. Until then treat commercial use of the handwriting tool's base weights as **not cleared**.
3. **MEDIUM: torch wheel selection.** `web/requirements-autolabel.txt` alone on Linux x86_64 installs the PyPI CUDA build, pulling 15 NVIDIA proprietary `nvidia-*-cu12` packages whose EULA I did not read. `web/Dockerfile` avoids this with `--extra-index-url https://download.pytorch.org/whl/cpu` and I confirmed pip then selects `2.9.1+cpu` with no NVIDIA deps, but `web/DEPLOYMENT.md` "Without Docker" tells users to run plain `pip install -r requirements-autolabel.txt`. Document the CPU index there (or ship GPU images only after reading the EULA). CI now scans the CPU resolution, so it will not catch NVIDIA packages by itself.
4. **MEDIUM: Debian base image obligations** when you distribute (or sell) the container image: GPL/LGPL source availability and license texts. Debian-hosted source satisfies this in practice only if you keep the exact tag/digest reproducible; a written offer or a mirror is the cautious route (lawyer).
4b. **MEDIUM (second pass 2026-11-19): the license gate has never covered the S3/crypto pins.** `django-storages`, `boto3`, `cryptography` (all permissive, section 2a) were absent from the 2026-10-16 venv the gate ran on, so its clean result did not include them; re-run `scripts/check_licenses.py` in an environment built from today's `web/requirements.txt` before the next release, and note the gate's CI environment must install that file (the audit could not verify it does).
5. **LOW: MPL-2.0 (certifi, tqdm)**, file-level copyleft; fine unmodified.
6. **LOW: attribution gap.** No NOTICE file and no THIRD_PARTY_NOTICES before this audit; the wheel/image should ship the notices file. `tokenizers` wheel ships no LICENSE file (Apache-2.0 per upstream).
7. **LOW: license drift.** Transitive deps are unpinned (`web/requirements*.txt` pins only the 7 direct ones); a future resolve can change a transitive license. The new CI job `licenses` (`scripts/check_licenses.py`) guards that for new PRs; it does not run on a schedule. HF model tags also drift silently (the TrOCR tag is the live example: whatever the card said on 2026-11-19 morning, it says nothing today) — the weight-revision pins protect the *bytes*, not the *licence*; re-check tags before any release.
8. **LOW (repo hygiene, not my files):** `.github/workflows/ci.yml` uses `working-directory: athena-oss` and `paths: athena-oss/**`, which does not match this repository layout, and `docs/LICENSE` also refers to `athena-oss/...` paths. The root-package CI may never trigger here. I left both untouched.
9. Python 3.13 slim tag, Debian codename and package versions change over time; the Debian figures above are from one repo-info snapshot (unverified for your exact digest).

## Needs a lawyer / owner decision

1. Whether Athena may offer the CLIP-based auto-labeler as a hosted/paid feature given OpenAI's "any deployed use ... out of scope" statement and the lack of an explicit weights license on the HF repo. Or whether to swap or gate it. *Status 2026-10-17: the owner decided to keep CLIP enabled (see [Owner decision: CLIP](#owner-decision-clip-stays-enabled-2026-10-17)). That is a risk acceptance, not an answer to this question, so the question stays open for any hosted or paid offering.*
2. **(Second pass 2026-11-19)** The TrOCR base-weights licence (or absence of one — see the TrOCR row and risk 2b): whether the unilm code repository's MIT reaches the trained checkpoints, or whether Microsoft's publication practice elsewhere (the MIT tag on `trocr-base-handwritten`) can be relied on for `trocr-small-handwritten`, or whether to ask Microsoft in writing. Until answered, the handwriting tool's base weights are **not cleared** for commercial use by anything this audit read, and the in-product "MIT licence on its model card" wording is inaccurate as of today.
3. Training-data exposure for CLIP (scraped web data), SAM (SA-1B research license for the dataset), TrOCR (IAM Handwriting Database terms — site unreachable 2026-11-19, unverified), torchvision/D-FINE (COCO: annotations CC-BY-4.0, images under Flickr terms, terms now quoted in full in section 5b; whether dataset terms reach weights trained on them) for your jurisdictions and customers' contracts (e.g. IP indemnities you may be asked to give).
4. The AGPL boundary for the YOLO adapter: keep it in the Apache-2.0 tree with a warning, move it out of the `athena-cli` wheel, or remove it. And whether to buy an Ultralytics Enterprise license if customers use it.
5. Container distribution: the exact GPL/LGPL source-offer mechanism for the Debian base layer and torch's bundled components.
6. Whether to publish GPU images (NVIDIA EULA review).
7. Whether to add a `NOTICE` file and which attribution text to put in it (Apache-2.0 §4(d)); whether docs stay CC-BY-4.0 while code is Apache-2.0.
8. Trademark ("Athena", "Hugging Face", "Ultralytics", "COCO", etc.) is out of scope for this audit. The full, plain-language list of what a qualified lawyer must confirm before commercial use is now written up separately: **[`docs/guides/legal-review-checklist.md`](guides/legal-review-checklist.md)** (every item links back to a section of this file).

## Owner decision: CLIP stays enabled (2026-10-17)

**Decision (owner, 2026-10-17):** keep using CLIP (`openai/clip-vit-base-patch32`) for auto-suggested labels. The default
configuration keeps `ATHENA_AUTOLABEL_BACKEND=sam` and `ATHENA_CLIP_MODEL=openai/clip-vit-base-patch32`
(`web/athena_web/settings.py`, `web/.env.example`). Nothing in the code was changed by this decision.

**What this decision is, and is not.**

* It is the owner's acceptance of a business risk for the project's own default setup. It is **not** a legal clearance, a
  finding that CLIP may be used commercially, or advice from a lawyer. No lawyer reviewed it.
* It binds **nobody else**. A company that self-hosts Athena OS runs its own worker, downloads the weights itself and carries
  its own exposure. It cannot rely on the project owner's decision and must take its own advice before commercial deployment
  (`web/DEPLOYMENT.md`, "Licences and model terms", says so).
* Athena OS must not describe CLIP as "MIT-licensed weights" or "cleared for commercial use". Searched on 2026-10-17 in `README.md`,
  `PRODUCT.md`, `ROADMAP.md`, `web/README.md`, `web/DEPLOYMENT.md`, `THIRD_PARTY_NOTICES.md`, the console templates and static
  files, and `web/projects/autolabel/`: none does.

**What remains true and unresolved** (re-checked 2026-10-17, sources below):

1. **The Hugging Face repo declares no licence.** The model's API record at revision `3d74acf9a28c` (the same revision as the original
   audit) has no `license:` tag, the model card's front matter has no `license` field, and the file list has no LICENSE file.
2. **The upstream code repository is MIT; that is not a statement about the weights.** `openai/CLIP` `LICENSE` begins "MIT License /
   Copyright (c) 2021 OpenAI" and grants permission "to deal in the Software without restriction, including without limitation the
   rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software". It defines the licensed
   thing as "the Software" and says nothing about trained weights. Whether OpenAI licensed the weights under MIT is **not stated** on
   the pages read.
3. **The model card says deployed use is out of scope.** Auto-suggest in a running console is a deployed use. The card is a statement of
   intended use, not a licence term, but a customer or regulator can quote it.
4. **The card also puts "surveillance" use out of scope**, and Athena's example operators are warehouse and security-scanner staff.
   Whether labelling pictures for an inspection-screen workflow falls under "surveillance" is a question for a lawyer, not answered here.
5. **Training data is undisclosed.** The card says part of the data came from the developers' own web crawl and that they "will not be releasing the dataset".
   Copyright and privacy status of that data is unsettled and unreviewed here.
6. **CLIP quality on real items is unmeasured.** The only run so far used 14 flat synthetic shapes (see `docs/dogfood/2026-10-operator-ux-report.md`).
   The card says CLIP's performance varies with the class taxonomy and calls for "thorough in-domain testing".

**What mitigates, without removing, the risk.** CLIP only proposes label names for regions that SAM found. Every proposal is stored
as **Needs review** and is never exported or trained on until a person accepts it. An operator can switch the feature off
(`ATHENA_AUTOLABEL_BACKEND=disabled`) or point `ATHENA_CLIP_MODEL` at another model; manual labelling does not use either model.

**Quoted text (fetched 2026-10-17; only what was read).**

Model card, `https://huggingface.co/openai/clip-vit-base-patch32/raw/main/README.md`:

> "It was not developed for general model deployment - to deploy models like CLIP, researchers will first need to carefully study their capabilities in relation to the specific context they’re being deployed within."
>
> "**Any** deployed use case of the model - whether commercial or not - is currently out of scope. Non-deployed use cases such as image search in a constrained environment, are also not recommended unless there is thorough in-domain testing of the model with a specific, fixed class taxonomy."
>
> "Certain use cases which would fall under the domain of surveillance and facial recognition are always out-of-scope regardless of performance of the model."
>
> "The model was trained on publicly available image-caption data. This was done through a combination of crawling a handful of websites and using commonly-used pre-existing image datasets such as YFCC100M."
>
> "We do not intend for this dataset to be used as the basis for any commercial or deployed model and will not be releasing the dataset."

Code repository licence, `https://raw.githubusercontent.com/openai/CLIP/main/LICENSE` (the whole file is the standard MIT text):

> "MIT License
>
> Copyright (c) 2021 OpenAI
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, [...]"

Hugging Face API record, `https://huggingface.co/api/models/openai/clip-vit-base-patch32`: revision `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`,
last modified 2024-02-29; no tag beginning `license:`; files: `.gitattributes`, `README.md`, `config.json`, `flax_model.msgpack`, `merges.txt`,
`preprocessor_config.json`, `pytorch_model.bin`, `special_tokens_map.json`, `tf_model.h5`, `tokenizer.json`, `tokenizer_config.json`, `vocab.json`.

**Fetch notes.** The model-card page and the LICENSE file were fetched with the web extraction tool on 2026-10-17; the page extraction was
abridged, so the card's full text and the API record were read from the raw/API URLs above with `curl` on the same day. I did not re-fetch
`https://github.com/openai/CLIP/blob/main/model-card.md` on 2026-10-17 (the 2026-10-16 audit said it has the same text; unverified today).
This section is an engineering record, not legal advice.

**Triggers to revisit:** a hosted or paid Athena offering; a customer contract asking for IP indemnity or a licence warranty; OpenAI or
Hugging Face adding or changing a licence statement; a measured quality problem; a decision to move to a model with an explicit weights licence.

## What I could not verify

* The NVIDIA EULA text and `nvidia-cudnn-cu12` license (empty metadata).
* SA-1B dataset license text; Hugging Face Hub terms; Ultralytics weights license text.
* Whether OpenAI explicitly licensed the CLIP *weights* under MIT (only the code repo LICENSE found).
* **(Second pass 2026-11-19)** Why the TrOCR model card no longer shows a licence, and whether Microsoft grants one for those weights by any other instrument. What the earlier pass saw, only it knows; today's record is the evidence.
* **(Second pass 2026-11-19)** The IAM Handwriting Database's current terms (fki.tic.heia-fr.ch timed out, 2026-11-19).
* **(Second pass 2026-11-19)** The wheel-internal licence/notice bundles of the S3/crypto stack (cryptography's OpenSSL NOTICE in particular) and of onnxruntime's binary wheel; only PyPI metadata was read.
* **(Second pass 2026-11-19)** The licence manifests inside the Playwright-downloaded Chromium and WebKit builds (section 7b).
* Exact third-party components linked inside the torch/numpy/Pillow Linux wheels (I inspected the macOS arm64 wheels' license bundles; the Linux CPU wheel's own bundle was not downloaded, only its metadata).
* Action tags v4/v5 license (read `main`).
* Windows-only transitive deps.
* The gate has been run on macOS arm64 envs only; CI will run it on Linux. **(Second pass)** And its 2026-10-16 run predates the S3/crypto pins (risk 4b).

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
| Caveat 2 / owner decision 1 (CLIP) | **Owner decision recorded 2026-10-17: CLIP stays enabled by default**, as the owner's accepted risk, not a legal clearance and not transferable to downstream operators. See [Owner decision: CLIP](#owner-decision-clip-stays-enabled-2026-10-17). `web/DEPLOYMENT.md` and `THIRD_PARTY_NOTICES.md` updated the same day. **Still open:** the legal question for hosted/paid use; an in-console notice. |
| Risk 8: CI path / layout mismatch | **Reported, not fixed** (owner decides; `.github/` untouched). See below. |
| D-FINE tool: `scipy` and the two `ustc-community` weights (2026-10-24) | **Documented, not legally resolved.** `scipy==1.18.1` (BSD-3-Clause) and the two pinned Apache-2.0-declared checkpoints are in the rows above; the gate ran clean (OK=43 in the QA environment). Open: the COCO training-data question (same as the torchvision COCO start), who licenses the converted weights, the Linux scipy wheel's bundled libraries, and whether D-FINE becomes the default ready-made base (an owner decision, `docs/plans/2026-10-23-no-code-teach-flow.md`). Objects365 checkpoints are refused in code. Engineering read, not legal advice. |
| Multimodal Wave 0/1: `onnxruntime` and the TrOCR base model (2026-11-19) | **Documented, not legally resolved.** `onnxruntime==1.20.1` (MIT) is pinned in `web/requirements-autolabel.txt` for the `audio-wav-cnn` backend's ONNX export only, with a torch fallback when it is missing; its row is in section 2 (binary-wheel notices unread — unverified). The `ocr-trocr` backend fine-tunes `microsoft/trocr-small-handwritten` (MIT declared on the model card), fetched at runtime only, admin-gated, checksum-verified, redirected only to `huggingface.co`/`*.hf.co` — the D-FINE pattern, inherited from `torchvision_frcnn.downloads_allowed`; its row is in section 5. The audio and OCR backends need no other new packages: both run on the existing `torch`/`transformers`/`numpy` pins (`scipy` remains D-FINE-only; scikit-learn/pandas/joblib are *not* used by anything in this tree — they belong to the tabular slice that is still in flight, and nothing pins them here). Open: the IAM-dataset-to-weights question for the fine-tuned artifact, the onnxruntime wheel's bundled notices, and whether the license gate's CI environment installs the new pin. Engineering read, not legal advice. |
| Tabular slice: `openpyxl` (2026-11-19) | **Documented, not legally resolved.** `openpyxl==3.1.5` (MIT) is now pinned in `web/requirements-autolabel.txt` for the `tabular-sklearn` backend's .xlsx path only (pandas' Excel engine); its row is in section 2. It pulls in one transitive, `et-xmlfile` 2.0.0 (MIT). This supersedes this addendum's earlier "nothing pins them here" wording for the Excel half: scikit-learn/pandas/joblib remain unpinned pending the rest of the tabular slice. Open: the Linux wheel, and whether the license gate's CI environment installs the new pin. Engineering read, not legal advice. |
| Pydantic floor (found while fixing `tests/test_contracts.py::test_generated_schema_matches_models`) | **Done.** `sdk/python/pyproject.toml` declared `pydantic>=2.6,<3`, but the checked-in `contracts/v1/model.schema.json` carries `"additionalProperties": true` on `metadata`, which Pydantic emits from 2.11 on. With 2.10.6 the schema generator output differs and the drift test fails; with 2.11.0 and 2.12.5 it passes unchanged. The checked-in schema was not stale; the declared floor was too low. Floor raised to `pydantic>=2.11,<3`; the test was not changed. |
| M0 completion pass (2026-11-19, second pass) | **Documented; two findings for the owner.** (1) The S3/crypto pins (`django-storages`, `boto3` stack, `cryptography` stack) now have rows (section 2a, all permissive; the gate must be re-run to cover them). (2) The COCO/YOLO format-spec read the ROADMAP M2 item asked for is done (section 5b: no licence claim found on either format; COCO dataset terms quoted in full). (3) Playwright browser tooling documented as dev-only (section 7b). (4) **The TrOCR MIT claim could not be reproduced** — the HF repo carries no licence today (TrOCR row, risk 2b, lawyer item 2); the in-product wording in `web/projects/training/ocr_trocr.py` and `web/DEPLOYMENT.md` still says MIT and needs an owner decision to correct. (5) SAM/D-FINE tags and both torchvision weight URLs re-checked, unchanged. No code was changed by this pass (docs only), by design. |
| Lawyer hand-off | **Done.** `docs/guides/legal-review-checklist.md` written (plain-language, every item linked back to this file's sections). |

## Suggested ROADMAP.md edits (for the merge owner; ROADMAP.md itself was NOT edited by this pass)

* **M0 first unticked item** ("Verify the exact repository contents, ownership, licenses, dependency notices, model/data rights, and trademark/asset boundaries"): the note can now add — the audit's second pass (2026-11-19) covered the previously missing S3/crypto package stack (permissive), completed the formal COCO/YOLO format-spec licence read (no licence claim found on either format; COCO dataset terms quoted in `docs/LICENSE_AUDIT.md` §5b), audited the dev-only Playwright browser tooling (§7b), and produced the lawyer hand-off checklist (`docs/guides/legal-review-checklist.md`). Still open and now *newly* flagged: the TrOCR base-weights licence could not be found on the HF repository on 2026-11-19 (the earlier "MIT on the model card" reading is not reproducible; see `docs/LICENSE_AUDIT.md` risk 2b and lawyer item 2). The item should stay unticked until a lawyer signs off via the checklist.
* **M2 "NOT done" note** ("formal licence review of the COCO / YOLO format specifications and of the `info` block conventions has not been done here"): this is now done at the engineering level — `docs/LICENSE_AUDIT.md` §5b (read 2026-11-19: no licence claim found on the format specifications; COCO's own terms govern the dataset, not the format; Athena's exports copy nothing from either project). Suggest removing that clause from the M2 "NOT done" list or marking it "done 2026-11-19, see LICENSE_AUDIT §5b" (the other M2 NOT-done items stand).
* **M2 note about the CLIP legal review and D-FINE model-card question**: unchanged in substance; both remain open for a lawyer (checklist items). No edit needed beyond the M0 note above.
* Optional, if the owner acts on the TrOCR finding: a line in M3's execution log noting the TrOCR licence wording correction once `ocr_trocr.py`/`DEPLOYMENT.md` are fixed (this pass deliberately changed no product code or deployment docs — docs/ only — so the correction is a separate commit by whoever owns that decision).

### Root CI and licence-text path mismatch (reported only)

This repository's root *is* the project; there is no `athena-oss/` directory. Therefore:

* `.github/workflows/ci.yml`: both triggers filter on `paths: ["athena-oss/**"]`, and the `lint` and `test` jobs set `defaults.run.working-directory: athena-oss`. A push or PR can never match that path filter, and the job would fail on a missing directory if it did run. Proposed one-line fixes: change both `- "athena-oss/**"` entries to `- "**"` (or delete the `paths:` blocks), and change both `working-directory: athena-oss` lines to `working-directory: .` (or delete the `defaults:` blocks).
* `docs/LICENSE` names `athena-oss/docs/`, `athena-oss/contracts/README.md` and `athena-oss/README.md`; they should read `docs/`, `contracts/README.md` and `README.md`. Same pattern: `docs/getting-started.md` says "From the `athena-oss/` directory", and the `Homepage` URL in `sdk/python/pyproject.toml` ends in `/tree/main/athena-oss`. (Possibly intended if this tree is later published as a subdirectory of a larger repo; the owner should decide which layout is canonical.)
