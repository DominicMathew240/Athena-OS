# Third-party notices

Athena OS is licensed under the Apache License 2.0 (see `LICENSE`). It does **not** vendor or copy any third-party source
code: the components below are installed from their upstream distributions (PyPI, Docker Hub, Hugging Face) when you build or
run it, and each remains under its own license. This list is an engineering attribution list, not legal advice; the full
analysis, evidence and caveats are in [`docs/LICENSE_AUDIT.md`](docs/LICENSE_AUDIT.md). Each package's full license text is
shipped in its own `*.dist-info/licenses/` (or `.dist-info/`) directory after `pip install`; keep those directories in any
image or bundle you distribute.

Versions are those pip resolved when the audit ran (2026-10-16). Pinned direct dependencies come from `web/requirements.txt`
and `web/requirements-autolabel.txt`; the rest are transitive and may change when you rebuild.

## Python packages: console and ML workers (`web/`)

| Package | Version | License (SPDX) | Copyright / note |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT |  |
| anyio | 4.15.1 | MIT |  |
| asgiref | 3.12.1 | BSD-3-Clause | Copyright (c) Django Software Foundation and individual contributors |
| certifi | 2026.7.22 | MPL-2.0 | Mozilla CA bundle; unmodified |
| click | 8.5.0 | BSD-3-Clause |  |
| Django | 6.1.2 | BSD-3-Clause | Copyright (c) Django Software Foundation and individual contributors |
| filelock | 4.2.0 | MIT |  |
| fsspec | 2026.9.0 | BSD-3-Clause |  |
| gunicorn | 26.2.0 | MIT | (c) 2009-2026 Benoît Chesneau |
| h11 | 0.16.0 | MIT |  |
| hf-xet | 1.7.0 | Apache-2.0 |  |
| httpcore | 1.0.9 | BSD-3-Clause |  |
| httpx | 0.28.1 | BSD-3-Clause |  |
| huggingface_hub | 1.33.0 | Apache-2.0 |  |
| idna | 3.20 | BSD-3-Clause |  |
| Jinja2 | 3.1.6 | BSD-3-Clause | Copyright 2007 Pallets |
| markdown-it-py | 4.2.0 | MIT |  |
| MarkupSafe | 3.0.4 | BSD-3-Clause |  |
| mdurl | 0.1.2 | MIT | Copyright (c) 2015 Vitaly Puzrin, Alex Kocharin |
| mpmath | 1.3.0 | BSD-3-Clause | Copyright (c) 2005-2021 Fredrik Johansson and mpmath contributors |
| networkx | 3.7 | BSD-3-Clause |  |
| numpy | 2.1.3 | BSD-3-Clause | Copyright (c) 2005-2024, NumPy Developers; wheel LICENSE.txt also covers OpenBLAS, LAPACK and the GCC runtime library (GPL-3.0 with GCC Runtime Library Exception) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |  |
| pillow | 12.3.0 | MIT-CMU | Pillow (see bundled LICENSE in the wheel, which also carries notices for the C libraries it bundles) |
| Pygments | 2.21.0 | BSD-2-Clause |  |
| PyYAML | 6.0.3 | MIT |  |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python |  |
| rich | 15.0.0 | MIT |  |
| safetensors | 0.8.0 | Apache-2.0 |  |
| setuptools | 84.0.0 | MIT |  |
| shellingham | 1.5.4 | ISC |  |
| sqlparse | 0.6.0 | BSD-3-Clause | Copyright (c) 2016, Andi Albrecht |
| sympy | 1.14.0 | BSD-3-Clause | Copyright (c) 2006-2023 SymPy Development Team |
| tokenizers | 0.23.3 | Apache-2.0 | the wheel ships no LICENSE file, text at https://github.com/huggingface/tokenizers/blob/main/LICENSE |
| torch | 2.9.1 | BSD-3-Clause | see the wheel's LICENSE and NOTICE, which list further third-party notices |
| torchvision | 0.24.1 | BSD-3-Clause | Copyright (c) Soumith Chintala 2016. Training worker only (`web/requirements-autolabel.txt`); metadata says `License: BSD`, the wheel's `LICENSE` is the BSD 3-Clause text |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | unmodified |
| transformers | 5.17.0 | Apache-2.0 | |
| typer | 0.27.3 | MIT |  |
| typing_extensions | 4.16.0 | PSF-2.0 |  |
| whitenoise | 6.9.0 | MIT |  |

Notes:
* MPL-2.0 (certifi, tqdm) is file-level copyleft. These packages are used unmodified; if you modify their files and distribute them, publish those modified files.
* When installed from default PyPI on Linux x86_64, `torch` additionally pulls NVIDIA's proprietary `nvidia-*-cu12` CUDA wheels. The Docker image uses the PyTorch CPU index instead and does not contain them.

## Python packages: SDK and CLI (`sdk/python`, `cli`)

| Package | Version | License (SPDX) | Copyright / note |
|---|---|---|---|
| pydantic | 2.14.1 | MIT | |
| pydantic-core | 2.50.1 | MIT | |
| annotated-types | 0.8.0 | MIT | |
| typing-inspection | 0.4.4 | MIT | |
| typing_extensions | 4.16.0 | PSF-2.0 | |

Test/CI tooling (pytest, ruff, pluggy, iniconfig; MIT) is not distributed.

## Optional, not shipped: Ultralytics YOLO (AGPL-3.0)

`examples/adapters/yolo_adapter.py` (Apache-2.0, ours) can wrap the independent **Ultralytics** project if the user installs it
themselves. Ultralytics is licensed **AGPL-3.0** (or under a separate Ultralytics Enterprise license). It is not a dependency of any
Athena package, is not in the Docker image, and is not included in this repository. If you install it alongside Athena, AGPL-3.0
terms apply to what you do with it.

## Model weights (downloaded at runtime; not redistributed by Athena OS)

| Model | License | Source |
|---|---|---|
| `facebook/sam-vit-base` (Segment Anything, ViT-B) | Apache-2.0 | https://huggingface.co/facebook/sam-vit-base ; https://github.com/facebookresearch/segment-anything (facebookresearch) |
| `openai/clip-vit-base-patch32` (CLIP) | No license declared on the Hugging Face repo; the upstream code repository https://github.com/openai/CLIP is MIT, "Copyright (c) 2021 OpenAI". OpenAI's model card states that any deployed use of the model, commercial or not, is out of scope; see `docs/LICENSE_AUDIT.md` | https://huggingface.co/openai/clip-vit-base-patch32 |

### Third-party model terms

Model weights are not covered by Athena OS's Apache-2.0 licence and are not redistributed by it; your worker downloads them from
Hugging Face and their terms apply to you. For **CLIP** (`openai/clip-vit-base-patch32`), the Hugging Face repository declares no
licence (checked 2026-10-17), the MIT licence in the upstream `openai/CLIP` code repository is not a statement about the weights, the
model card says any deployed use, commercial or not, is out of scope, and the training data is undisclosed. The Athena owner decided on
2026-10-17 to keep CLIP enabled by default; that is a risk acceptance, **not a legal clearance, and not one that downstream operators
can rely on**. Anyone deploying Athena OS commercially should take their own advice and may switch it off with
`ATHENA_AUTOLABEL_BACKEND=disabled` or change the model with `ATHENA_CLIP_MODEL` (see `web/DEPLOYMENT.md`). Details and sources:
[`docs/LICENSE_AUDIT.md`](docs/LICENSE_AUDIT.md).

Citations requested by the model cards: Kirillov et al., "Segment Anything", arXiv:2304.02643; Radford et al., "Learning Transferable Visual Models From Natural Language Supervision", arXiv:2103.00020.

## Container base image

`web/Dockerfile` builds `FROM python:3.13-slim` (CPython under the PSF License; Debian "trixie" userland whose packages carry their own licenses,
mostly GPL/LGPL/BSD/MIT, in `/usr/share/doc/*/copyright` inside the image). Debian source packages are available from https://deb.debian.org/debian/ ;
if you distribute the image, you are responsible for making corresponding source available for the GPL/LGPL components.

## Static assets

The web console uses its own CSS/JS/templates. It ships three third-party **fonts**, served from the console itself (no CDN), and no third-party icons or scripts. All three are under the SIL Open Font License 1.1; each licence text is in `web/projects/static/projects/fonts/OFL-*.txt`, and the fonts may be redistributed with the console but not sold on their own:

| Font | Used for | Copyright | Licence |
|---|---|---|---|
| Anybody (variable) | headings, buttons | 2020 The Anybody Project Authors (https://github.com/Etcetera-Type-Co/Anybody) | OFL-1.1 |
| Hanken Grotesk (variable) | text | 2021 The Hanken Grotesk Project Authors (https://github.com/marcologous/hanken-grotesk) | OFL-1.1 |
| Martian Mono (variable) | labels, numbers, ids | 2021 The Martian Mono Project Authors (https://github.com/evilmartians/mono) | OFL-1.1 |

They are the Google Fonts builds (Latin and Latin Extended subsets), the same faces as the athena-forge landing page. The console's logo is the Athena mark, copied from athena-forge (`frontend/public/athena-mark.png`), resized; it is Athena's own artwork.

## Documentation

Project documentation (`docs/`, `contracts/README.md`, `README.md`) is licensed CC-BY-4.0 (see `docs/LICENSE`).
