# Model quality: what is measured, what is not, and how to measure yours

> **SYNTHETIC SHAPES SAY NOTHING ABOUT REAL SCANNER IMAGERY.** Every number on this page comes from drawn circles, squares and triangles on made-up backgrounds, one machine, one or two runs. They show whether the training code and the optional pretrained starts *learn at all*. They are not an accuracy claim for any real product, camera or scanner, and the console's trained models should not be trusted until you have measured them on your own held-out pictures (last section).
>
> Prototype. Local test machine. No operator, scanner or customer data was involved.

## What the reference backend is

`torchvision-fasterrcnn`: Faster R-CNN with a MobileNetV3-Large backbone and a 320 px feature pyramid, trained on CPU. Until now it always started from random weights, so its quality was unproven. This page records the first honest measurement and adds an **opt-in** pretrained start.

## The `pretrained` setting (opt-in, off by default)

| Value | What happens | Download |
|---|---|---|
| `"none"` (default) | Random start, exactly as before | Never |
| `"imagenet_backbone"` | MobileNetV3-Large backbone from torchvision's ImageNet-1K weights (`MobileNet_V3_Large_Weights.IMAGENET1K_V1`, 21.1 MB per torchvision's metadata, 22,139,423 bytes on disk); the detector heads start random | Only if enabled |
| `"coco"` | Whole detector from torchvision's COCO weights (`FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1`, 74.2 MB per metadata, 77,844,807 bytes on disk); only the final class layer is replaced with your labels and starts random | Only if enabled |

**Enabling downloads** is the operator's decision, in the environment of the process that runs `manage.py train_worker`:

```
ATHENA_TRAINING_ALLOW_DOWNLOAD=1
```

Default is `0`. With it off and the weights not already cached, the run **fails before any work** with this message (copied from a real run):

> This run asked for pretrained weights ('coco': Faster R-CNN MobileNetV3-Large 320 FPN trained on COCO, about 74 MB), but downloading is switched off on this computer. Either set ATHENA_TRAINING_ALLOW_DOWNLOAD=1 in the environment of the training helper (manage.py train_worker) and run it again - the file is fetched once from download.pytorch.org and kept in your Athena data folder under models/pretrained - or start the run with pretrained set to 'none', which downloads nothing. Read the licence note first: the pretrained weights come with their own training-data terms.

Details that matter:

- Files are cached under `<data home>/models/pretrained/checkpoints/`, checked against the checksum prefix torchvision publishes in the file name (a mismatch discards the download), and the full SHA-256 is stored in the run's `metrics.pretrained` next to the source URL. Observed: `8738ca79…61759a04` (ImageNet backbone) and `907ea3f9…7977b5` (COCO), both matching their published prefixes.
- **A file already in that cache and matching its checksum is used even with downloads off** (nothing is downloaded; useful offline). Verified: the second benchmark run below ran with the switch unset and reported `from: cache` for both files. A wrong or corrupt cached file is never trusted.
- Models trained with a pretrained start save their `pretrained` mode in the artifact so they reload correctly (these use frozen batch-norm layers, which have a different saved layout). Models saved before this setting existed still load.
- **The console's Teach panel offers this setting under a collapsed "Advanced: start from a ready-made model"** (added 2026-10-17, W15): "Start from scratch" (default), "Start from a general picture model (ImageNet)" (`imagenet_backbone`) and "Start from a general object model (COCO)" (`coco`). The panel greys the two ready-made choices out, with the reason and the administrator's steps, unless their weights are already cached or downloads are on (the training helper's own `ATHENA_TRAINING_ALLOW_DOWNLOAD` is what counts; the console's environment is only a fallback when no helper is running). Choosing one shows the licence note in plain words, the "ask a lawyer before commercial use" warning and a tick-box that must be ticked before "Start teaching" works. The panel repeats none of the numbers on this page; it points here. The API enforces the same rules: `POST /api/projects/<id>/training/runs` with `"config": {"pretrained": "coco"}` needs `"acknowledge_pretrained_terms": true` (400 otherwise) and answers 409 before queueing anything if downloads are off and the file is not cached. The choice is in the run's write-once config and the acknowledgement is written in the run's audit event. Verified in a real browser with no download (disabled state, cached state, licence box, tick-box, a queued run's stored config); a real teaching run started from the console panel has **not** been done. The benchmark below was run through the script, not the panel.
- The `epochs` and other settings are unchanged. Tests never download: the downloader is patched, and "published weights" in the tests are random tensors written to a temp folder.

### Licence: what was checked and what was not

The backend's `license_note` (shown wherever the backend is described) says the same thing in short. Checked on 2026-10-17:

| Item | Finding | Source |
|---|---|---|
| torchvision and PyTorch code | BSD-3-Clause | installed package metadata (`License: BSD`, `LICENSE`) |
| torchvision's statement about its pretrained models | "The pre-trained models provided in this library may have their own licenses or terms and conditions derived from the dataset used for training. It is your responsibility to determine whether you have permission to use the models for your use case." (also in the installed `LICENSE`) | `https://raw.githubusercontent.com/pytorch/vision/main/README.md`, "Pre-trained Model License"; installed `LICENSE` file |
| Licence field in the weight metadata | **None.** In torchvision 0.24.1, `weights.meta` has no key containing "licen" (keys seen: `min_size`, `num_params`, `recipe`, `_metrics`, `_ops`, `_file_size`, `_docs`, `categories`, same keys for both weights files). A test pins this so a newer torchvision that adds one gets noticed | installed package; `projects/tests_training_quality.py` |
| ImageNet terms of access | The Database may be used "only for non-commercial research and educational purposes" (term 1 of the access agreement shown for the full dataset; employer bound if for-profit, term 6) | `https://image-net.org/download.php` |
| COCO annotations | Creative Commons Attribution 4.0; the COCO Consortium "does not own the copyright of the images", and use of the images "must abide by the Flickr Terms of Use" | `https://cocodataset.org/#termsofuse` |
| `_docs` text torchvision gives the weights | ImageNet backbone: "trained from scratch by using a simple training recipe"; COCO detector: "produced by following a similar training recipe as on the paper" | installed package |

**Unverified, ask a lawyer:** whether the ImageNet "non-commercial research" terms reach *weights trained on* ImageNet and models fine-tuned from them; whether Flickr's mixed per-image licences in COCO reach weights trained on those images; whether torchvision's weights files carry any further terms I did not find. This page is not legal advice. If any doubt applies to you, use `"none"`.

## Benchmark

### How to reproduce

```
cd web
ATHENA_DATA_HOME=<scratch dir> ATHENA_TRAINING_ALLOW_DOWNLOAD=1 \
  <venv with torch 2.9.1 + torchvision 0.24.1>/bin/python scripts/benchmark_training.py \
  --seed 7 --epochs 8 --modes none,imagenet_backbone,coco --workdir <scratch dir>/bench
```

The script draws the dataset itself (deterministic from `--seed`), writes a YOLO export, trains each mode with the backend's own `train()`, then scores the held-out **test** split with `backend.evaluate()`, which is the same `projects/training/evaluation.py` code the worker uses, at the worker's operating confidence (`EVAL_CONFIDENCE = 0.5`) and IoU 0.5. It writes `results.json` and `results.md`.

The synthetic data (200 train / 60 validation / 60 test pictures at 320×320; the backend trains on train only and the validation split was not used for anything): three classes (circle, square, triangle); scale 28–130 px; random rotation; random colours (colour is not a cue); gradient and blotch backgrounds, noise, exposure jitter, optional blur; hollow and filled shapes; unlabelled look-alike distractors (ellipses, rectangles, hexagons, pentagons, crosses, lines); thin strokes over objects as partial occlusion; 1–4 objects per picture. Nothing was tuned on the test split. The test boxes per class (36–58) are above the ~30 suggested below, but a single run of 8 epochs is still a small sample.

### Run 1 — seed 7 (copied from the script's output)

Command: `python scripts/benchmark_training.py --seed 7 --epochs 8 --modes none,imagenet_backbone,coco --workdir <scratch>/w13-bench`
Seed 7, 8 epochs, batch 4, confidence threshold 0.5 (the product's `EVAL_CONFIDENCE`), IoU 0.5.
Hardware: Apple M3; 8 logical CPUs; macOS 27.0.1 arm64; CPU only. Library versions: torch 2.9.1, torchvision 0.24.1, device cpu, threads 4.
Data (SYNTHETIC shapes): train 200 pictures / 505 boxes (circle 163, square 158, triangle 184); val 60 pictures / 146 boxes; test 60 pictures / 144 boxes (circle 58, square 38, triangle 48).

**`pretrained = none` — train 859.6 s, evaluate 5.4 s, mean loss 1.13806 → 0.3781 (epoch 1 → last)**

| class | test boxes | AP50 | precision | recall | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| circle | 58 | 0.221 | n/a | 0.000 | 0 | 0 | 58 |
| square | 38 | 0.039 | n/a | 0.000 | 0 | 0 | 38 |
| triangle | 48 | 0.023 | n/a | 0.000 | 0 | 0 | 48 |
| **all (mAP50 = mean of class AP50)** | 144 | 0.094 | n/a | 0.000 | 0 | 0 | 144 |

**`pretrained = imagenet_backbone` — train 444.3 s, evaluate 4.9 s, mean loss 0.72569 → 0.50412**

| class | test boxes | AP50 | precision | recall | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| circle | 58 | 0.966 | 0.930 | 0.914 | 53 | 4 | 5 |
| square | 38 | 0.958 | 0.889 | 0.842 | 32 | 4 | 6 |
| triangle | 48 | 0.920 | 0.796 | 0.896 | 43 | 11 | 5 |
| **all (mAP50 = mean of class AP50)** | 144 | 0.948 | 0.871 | 0.889 | 128 | 19 | 16 |

**`pretrained = coco` — train 330.7 s, evaluate 7.1 s, mean loss 1.28365 → 0.68406**

| class | test boxes | AP50 | precision | recall | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| circle | 58 | 0.995 | 0.919 | 0.983 | 57 | 5 | 1 |
| square | 38 | 0.972 | 0.949 | 0.974 | 37 | 2 | 1 |
| triangle | 48 | 0.998 | 0.941 | 1.000 | 48 | 3 | 0 |
| **all (mAP50 = mean of class AP50)** | 144 | 0.988 | 0.934 | 0.986 | 142 | 10 | 2 |

### Run 2 — seed 8, pretrained modes only (different drawn data *and* different initialisation)

Command: `python scripts/benchmark_training.py --seed 8 --epochs 8 --modes imagenet_backbone,coco --workdir <scratch>/w13-bench2`, run with `ATHENA_TRAINING_ALLOW_DOWNLOAD` **unset** (both weight files came from the cache). Same hardware and settings. Data: train 200 / 485 boxes; val 60 / 156; test 60 pictures / 142 boxes (circle 58, square 36, triangle 48).

**`imagenet_backbone` — train 541.8 s, evaluate 5.9 s, mean loss 0.7318 → 0.50131**

| class | test boxes | AP50 | precision | recall | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| circle | 58 | 0.933 | 0.980 | 0.845 | 49 | 1 | 9 |
| square | 36 | 0.892 | 0.795 | 0.861 | 31 | 8 | 5 |
| triangle | 48 | 0.871 | 0.886 | 0.646 | 31 | 4 | 17 |
| **all** | 142 | 0.899 | 0.895 | 0.782 | 111 | 13 | 31 |

**`coco` — train 326.1 s, evaluate 10.3 s, mean loss 1.22783 → 0.76343**

| class | test boxes | AP50 | precision | recall | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| circle | 58 | 0.995 | 0.934 | 0.983 | 57 | 4 | 1 |
| square | 36 | 0.952 | 0.943 | 0.917 | 33 | 2 | 3 |
| triangle | 48 | 0.982 | 0.746 | 0.979 | 47 | 16 | 1 |
| **all** | 142 | 0.976 | 0.862 | 0.965 | 137 | 22 | 5 |

### Reading these tables without overreaching

- **From random weights, 8 epochs, 200 pictures:** no test box was found at the 0.5 operating confidence (0 of 144; precision undefined because nothing was predicted that confident). AP50 is not zero (0.094 overall) because it is computed from every prediction down to the 0.05 floor; the model ranks some boxes sensibly but is never sure. The console's approval gate refuses a model like this ("it found none of the test boxes") unless a person ticks the acknowledgement. I did not test whether many more epochs would fix it.
- **With either published start, same 8 epochs and same data:** mAP50 0.948 (ImageNet backbone) and 0.988 (COCO) on seed 7; 0.899 and 0.976 on seed 8. In both runs the COCO start scored higher than the ImageNet-backbone start. Two runs are not enough to call that difference reliable, and the seeds change the data as well as the start, so run-to-run spread is visible: the ImageNet-backbone triangle recall was 0.896 on seed 7 and 0.646 on seed 8.
- **Why the pretrained starts help here** is not tested by this benchmark. A reasonable guess is that generic edge/shape features carry over to drawn shapes; that guess says nothing about X-ray or camera imagery, where the images look nothing like ImageNet or COCO.
- **Precision/recall are at one threshold (0.5)**; AP50 is threshold-free. A different operating point trades one for the other. The false positives counted here include the unlabelled look-alike distractors, so they are "real" mistakes of the model on this data, not noise in the metric.
- **Wall times are not a speed comparison.** The machine was shared during all runs (load average about 8–10: other jobs and browser tests ran at the same time), so the same mode can take very different times. `none` took 859.6 s and the pretrained runs 326–542 s, but with the machine shared I cannot say how much of that gap is the setting and how much is load. Do not infer that pretrained training is faster from these numbers.
- The numbers above are what the script printed. Nothing else is claimed.

## How to measure on your own data

The point: a number is only worth reading if it comes from pictures the model never saw, in enough quantity, from the situation you will actually use it in.

1. **Label honestly.** Boxes in your project are the answer key; if labellers disagree or miss objects, the score is measuring the labellers.
2. **Save a snapshot with a Final test group.** In the project, under *Make a training set* → *Save a snapshot*, open **Advanced** and give **Final test** an amount above 0 (the default 80 / 10 / 10 split on 15 pictures gave 12 Training, 2 Checking and **1** Final test picture in a test run, which is far too few to say anything). With no Final test pictures the Teach panel says "you will get no results".
3. **Teach from that snapshot** (*Teach the computer* → pick the snapshot → start). When it finishes, open **See the results** (or *Your taught models* → results). The table "How it did on each label, on the Final test pictures" is computed by the same evaluation code the benchmark above used.
4. **Read the numbers per label, with their counts.** Found / Missed / Wrong boxes it drew, per label, on the Final test pictures only. The "limits" printed with the results repeat the rules below.
5. **Do not approve on a number you cannot defend** (see counts below). The approval screen needs a note and refuses models that found none of the test boxes.

### Minimum quantities

- The product warns below **30 Final test pictures** and below **10 Final test boxes per label** (`MIN_IMAGES_FOR_CONFIDENCE = 30`, `MIN_BOXES_PER_LABEL = 10` in `projects/training/evaluation.py`). Those are floors for a warning, not a licence to trust the result.
- Aim for **at least ~30 Final test boxes per label, and more for any label whose mistakes are costly.** Why: with few boxes, one or two mistakes move every percentage. A rough 95% interval for a measured recall of 0.90 (standard Wilson formula, boxes treated as independent, which they are not quite): 9 of 10 boxes → about 0.60–0.98; 27 of 30 → about 0.74–0.97; 90 of 100 → about 0.83–0.94. With under ~30 test boxes for a label, treat its figures as "somewhere from fine to poor".
- To get there, work backwards: test boxes per label ≈ pictures × (test share) × (boxes of that label per picture). With a 10% test share and 2 boxes of a label per picture on average, 30 test boxes needs about 150 pictures *just for that label*, in addition to enough Training pictures; rare labels need far more pictures or a larger Final test share. If a label is rare, say so in the model's approval note.
- Final test pictures should come from different bags, days, scanners or sites than the Training pictures. The product keeps byte-identical duplicates in one group but cannot see near-duplicates (consecutive frames of the same scene), which would make results look better than they are.
- Re-measure after any change to labels, labellers, scanner settings, or the picture source. A score on last month's pictures says nothing certain about next month's.

## Files

- `web/projects/training/torchvision_frcnn.py` — the `pretrained` setting, download gate, cache, licence note.
- `web/projects/tests_training_quality.py` — config validation, gate, cache, checksum, structure round-trip (the torch-gated tests run in the ML venv; they never touch the network).
- `web/scripts/benchmark_training.py` — the benchmark.
- `web/projects/training/torchvision_frcnn.py` (`start_options`, `start_check`) and the Teach panel in `web/projects/static/projects/console.js` — the console's "Advanced: start from a ready-made model" control; `web/projects/tests_pretrained_ui.py` pins what the server tells the page and refuses.
