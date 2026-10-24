# Model quality: what is measured, what is not, and how to measure yours

> **SYNTHETIC SHAPES SAY NOTHING ABOUT REAL SCANNER IMAGERY.** Every number on this page comes from drawn circles, squares and triangles on made-up backgrounds, one machine, one or two runs. They show whether the training code and the optional pretrained starts *learn at all*. They are not an accuracy claim for any real product, camera or scanner, and the console's trained models should not be trusted until you have measured them on your own held-out pictures (last section).
>
> Prototype. Local test machine. No operator, scanner or customer data was involved.

## What the reference backend is

`torchvision-fasterrcnn`: Faster R-CNN with a MobileNetV3-Large backbone and a 320 px feature pyramid, trained on CPU. Its first version always started from random weights, so its quality was unproven. This page records the first honest measurement of that, and of the two ready-made starts that were added afterwards. Since 2026-10-23 the console's one-click "Teach the computer" button no longer starts from random weights at all: see [The one-click start](#the-one-click-start-pretrained-auto).

## The `pretrained` setting

| Value | What happens | Download |
|---|---|---|
| `"auto"` | A request, not a stored value: the web process picks `coco` if it can run, else `imagenet_backbone`, when the run is created, and the run stores the picked value (plus `pretrained_requested: "auto"`). **Never picks `none`**; if neither ready-made start can run, the request is refused with a 409 (see [below](#the-one-click-start-pretrained-auto)). This is what the console's "Teach the computer" button gets: the server adds it to a `"snapshot": "latest"` request unless the caller names a start | Only if enabled, or already cached |
| `"none"` | Random start. It is still the default of the raw config (`DEFAULTS["pretrained"]` in `torchvision_frcnn.py`), so an API request that names no start behaves as it did before; the one-click path never sends it | Never |
| `"imagenet_backbone"` | MobileNetV3-Large backbone from torchvision's ImageNet-1K weights (`MobileNet_V3_Large_Weights.IMAGENET1K_V1`, 21.1 MB per torchvision's metadata, 22,139,423 bytes on disk); the detector heads start random | Only if enabled |
| `"coco"` | Whole detector from torchvision's COCO weights (`FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1`, 74.2 MB per metadata, 77,844,807 bytes on disk); only the final class layer is replaced with your labels and starts random | Only if enabled |

**Enabling downloads** is an administrator's decision. Two places can say it, and the first wins:

1. **The console** (a superuser): Access, "Ready-made model downloads": choose "Yes, allow downloads", tick that the licence text was read, write a note, save. Stored in the database and recorded; the web process never downloads anything, the teaching helper reads the setting each time a run starts. A choice made here, on **or off**, beats the environment variable, and cannot be cleared back to it (API: `PUT /api/console/training-downloads`, see the [API reference](../api-reference.md#get-apiconsoletraining-downloads-and-put-apiconsoletraining-downloads-the-download-switch)).
2. **The environment of the process that runs `manage.py train_worker`**, used only until someone chooses in the console:

```
ATHENA_TRAINING_ALLOW_DOWNLOAD=1
```

With neither set, the default is off. With it off and the weights not already cached, the run **fails before any work**. The current message is built from `ensure_pretrained_weights` and `download_blocked_message` in `torchvision_frcnn.py` (the sentence below is read from that code for the no-console-choice case; an earlier version of this page quoted the older wording from a real run, which did not mention the console):

> This run asked for pretrained weights ('coco': Faster R-CNN MobileNetV3-Large 320 FPN trained on COCO, about 74 MB), but downloading is switched off on this computer. An administrator can switch it on in the console (Access, Ready-made model downloads) or set ATHENA_TRAINING_ALLOW_DOWNLOAD=1 in the environment of the training helper (manage.py train_worker); once an administrator makes a choice in the console, that choice wins. Then run it again - the file is fetched once from download.pytorch.org and kept in your Athena data folder under models/pretrained - or start the run with pretrained set to 'none', which downloads nothing. Read the licence note first: the pretrained weights come with their own training-data terms.

If a console choice of "off" exists, the middle sentence instead says downloads were switched off in the console, that the environment setting is ignored while a console choice exists, and that an administrator can switch them on there.

Details that matter:

- Files are cached under `<data home>/models/pretrained/checkpoints/`, checked against the checksum prefix torchvision publishes in the file name (a mismatch discards the download), and the full SHA-256 is stored in the run's `metrics.pretrained` next to the source URL. Observed: `8738ca79…61759a04` (ImageNet backbone) and `907ea3f9…7977b5` (COCO), both matching their published prefixes.
- **A file already in that cache and matching its checksum is used even with downloads off** (nothing is downloaded; useful offline). Verified: the second benchmark run below ran with the switch unset and reported `from: cache` for both files. A wrong or corrupt cached file is never trusted.
- Models trained with a pretrained start save their `pretrained` mode in the artifact so they reload correctly (these use frozen batch-norm layers, which have a different saved layout). Models saved before this setting existed still load.
- **How the console uses this setting changed on 2026-10-23.** The earlier Teach panel offered the starts under a collapsed "Advanced" drawer with "Start from scratch" as the default; that drawer is gone. The main button now starts with `pretrained: "auto"`, added by the server (next section). The three explicit choices (scratch, ImageNet, COCO) remain only inside the closed "Details for your administrator" fold, under "Teach with exactly these settings", and the licence tick-box and the 409-before-queueing rule are unchanged and enforced by the API: `POST /api/projects/<id>/training/runs` with `"config": {"pretrained": "coco"}` needs `"acknowledge_pretrained_terms": true` (400 otherwise) and answers 409 before queueing anything if downloads are off and the file is not cached. The choice is in the run's write-once config and the acknowledgement is written in the run's audit event. What was verified: the disabled and cached states, the licence box and a queued run's stored config, in a real browser with no download (earlier rounds), and an automated persona pass on the new flow ([`docs/dogfood/2026-10-23-teach-flow.md`](../dogfood/2026-10-23-teach-flow.md), simulated user, synthetic data, Chromium only). **A real teaching run started from the console with real ready-made weights has not been done**, and neither has a run on the one-click path with a real helper. The benchmark below was run through the script, not the panel.
- The `epochs` and other settings are unchanged. Tests never download: the downloader is patched, and "published weights" in the tests are random tensors written to a temp folder.

### The one-click start (`pretrained: "auto"`)

What the console's "Teach the computer" button does (code: `lifecycle.start_run_on_latest_snapshot`, `TorchvisionFasterRCNN.resolve_start` and `prepare_config`; tests: `web/projects/tests_training_auto.py`):

- It asks the server to start on the latest saved copy of the labelled pictures ("snapshot"), or to freeze a new one first if the accepted boxes have changed since. The server picks the start (`coco`, else `imagenet_backbone`) and the training settings (below).
- **Athena will not start from scratch for you on this path.** If neither ready-made start can run (not cached with a matching checksum, and downloads off), the answer is a 409 with the reason and the administrator's steps, nothing is queued and no snapshot is written (`test_nothing_runnable_is_a_409_naming_the_administrator_step_never_a_scratch_run`). The reason: the one from-scratch run in this guide's benchmark, 8 epochs on 200 pictures, found **0 of 144** test boxes at the product's 0.5 operating confidence ("Reading these tables without overreaching", below, repeats this limit: precision undefined, "the console's approval gate refuses a model like this"). Those are synthetic shapes and one machine; they are not a measurement of a real project, and "I did not test whether many more epochs would fix it". With so little evidence for it, the button does not offer it.
- The explicit choices remain available to an administrator in the closed "Details for your administrator" fold, including "Start from scratch"; that route is not refused.
- The licence tick-box is still required whenever a ready-made start is used, and is enforced by the server.
- Both ready-made starts are off on a fresh install (the default is off; `ATHENA_TRAINING_ALLOW_DOWNLOAD=0` in the example file), so the button is off until an administrator allows the download (in the console under Access, or with that environment setting until someone chooses in the console) or copies the two weight files in; the steps are in [`web/DEPLOYMENT.md`](../../web/DEPLOYMENT.md#ready-made-starting-models-in-the-teach-panel). Not yet tried: a run on the one-click path with real weights.

#### How Quick and Better are worked out

`quality` is `quick` (the default) or `better`. `derive_settings` in `torchvision_frcnn.py` turns the number of **training** pictures into `epochs` and `batch_size` (batch 4, never above the number of pictures). The rules: take enough epochs to reach a target number of optimiser steps (240 for Quick, 400 for Better; 400 is the benchmark's own 8 epochs x 50 steps), keep epochs between 3 and 40 (Quick) or 80 (Better), and cap pictures x epochs at 1,600 (Quick) or 4,800 (Better), never going below 2 epochs. The target of 400 steps comes from the benchmark run that reached recall 0.986 with a COCO start; **it was derived from that synthetic benchmark and was not tuned on real imagery**. Worked table (copied from `DERIVE_TABLE` in `tests_training_auto.py`, which pins it):

| Training pictures | Quick: epochs | Better: epochs |
|---:|---:|---:|
| 10 | 40 | 80 |
| 30 | 30 | 50 |
| 60 | 16 | 27 |
| 200 | 5 | 8 |
| 1000 | 2 | 3 |

(Batch size is 4 in every row; 1, 2 or 3 pictures give a batch of that many.) An explicit `epochs` or `batch_size` in the request always wins; Athena records which values it chose in the run's `config.derived`.

The "about N minutes" sentence is `pictures x epochs x seconds-per-picture-epoch`, rounded up to whole minutes. The constants, `SECONDS_PER_PICTURE_EPOCH`:

| Start | Seconds per training picture per epoch | Measured in this guide's benchmark (1,600 picture-epochs) |
|---|---:|---|
| COCO | 0.21 | 330.7 s and 326.1 s (0.207, 0.204) |
| ImageNet backbone | 0.34 | 444.3 s and 541.8 s (0.278, 0.339) |
| from scratch | 0.54 | 859.6 s (0.537) |

Each constant is the slower measured run rounded up (`test_the_time_constant_is_not_faster_than_any_benchmark_run` pins that). **Caveat:** all of those timings come from one Apple M3, CPU only, 4 threads, **while other programs were running on it** (load average about 8 to 10, see "Wall times are not a speed comparison" below), on 320 px synthetic pictures. The estimate covers the teaching only, not time waiting in the queue, and the evaluation afterwards took about 5 to 10 seconds there. A slower computer will take longer; the console labels the figure "an estimate" and "not a promise". It is not a measurement of the computer you run it on.

#### What was measured: calibration and threshold spike (a scratch spike; its threshold and wording part is now built, calibration is not)

A scratch spike (not in the repository's code or tests; the scripts and report live in the QA scratch folder of the 2026-10-23 round) asked whether the detector's confidence scores can be trusted and whether a threshold could be chosen from the precision-recall curve. **Its limits are the point:** synthetic circle/square/triangle pictures, **one weak model trained from scratch** (30 epochs, seed 7, 200 pictures; no ready-made weights were cached and downloads stayed off, so the COCO and ImageNet starts were not run), one training seed, one machine, one i.i.d. fit/test split from the same generator. At the product's 0.5 confidence that model got precision 0.706 and recall 0.681 on 995 test boxes. Nothing below transfers to a stronger model or to real scanner images.

- **Raw scores were over-confident.** On 2,722 held-out predictions the mean raw score was 0.41 but only 31.7% were correct. The check screen's old fixed middle band (labelled "Somewhat sure" at the time, score 0.50 to 0.75; **those fixed phrases are no longer in the product**, see the next section) was right **40.1%** of the time (n = 244 on the fit split, 317 on the test split; 90% Wilson interval 36 to 45% on test), so "wrong more often than right". The old top band (0.90 or more) was right 91.5% (n = 399), the 0.75 to 0.90 band 75.9%. A score printed as "90-94%" was right 81% of the time.
- **Calibration works mechanically on this data, and changes only the words.** Expected calibration error (ECE, 10 equal-width bins) on the test split: raw 0.094, Platt 0.019, isotonic 0.023 (the noise floor of a perfect model at that sample size is about 0.017). Brier score 0.1239 raw, 0.1112 Platt, 0.1117 isotonic. Both calibrators keep the ordering of scores, so the precision-recall curve and any threshold are unchanged. Calibration is class-blind: a single calibrator left a per-class bias of -0.13 (circle) and +0.08 (triangle).
- **Threshold selection:** a recall target of 0.80 was reachable (threshold 0.235; test recall 0.790, test precision 0.521), but **recall targets of 0.90 and 0.95 were unreachable** for that model: it never proposed a matching box for 12 to 13% of the ground-truth boxes (recall ceiling 0.883 on the fit split, 0.866 on test). Resampling pictures put roughly plus or minus 4 recall points and 8 precision points of uncertainty on the chosen threshold with 630 fit boxes.
- **Status (updated 2026-10-23 later in the same round):** the threshold-selection and measured-wording parts of this spike **are now in the product** as a prototype, rebuilt in `web/projects/training/operating_points.py` and tested on generated data (next section). **Still not done:** Platt or isotonic calibration (scores stay the computer's own numbers; nothing is rescaled), per-label thresholds (one cut-off for all labels), and any measurement on real imagery. The evaluation's own fixed 0.5 operating confidence is unchanged. The open decisions are listed in [`docs/plans/2026-10-23-no-code-teach-flow.md`](../plans/2026-10-23-no-code-teach-flow.md#next-steps-and-open-decisions).

#### Operating points and measured wording (in the product, prototype, synthetic checks only)

Code: `web/projects/training/operating_points.py` (read its module docstring first); tests: `web/projects/tests_operating_points.py` (73 test methods, generated data and a fake backend, no real model). **Nothing here calibrates a score and nothing changes a live model; a person still chooses every setting.**

- **What is stored.** After a run is evaluated, the worker scores the **Checking** (validation) pictures and the **Final test** pictures with every prediction down to a score of 0.05 (`work_out_operating_points` in `training/runner.py`) and stores one block, `evaluation["operating_points"]`, on the model: for target shares of 80%, 90% and 95% of the drawn boxes found, the **highest** score whose share found on the Checking pictures reaches the target (the suggested cut-off), what that cut-off gave on the Checking and on the Final test pictures (found, and right among the boxes drawn), a 90% range from a seeded re-draw (1,000 times) of whole pictures (boxes of one picture are not independent), and, from the Final test pictures only, how often a score in each range (0 to 0.2, 0.2 to 0.4, 0.4 to 0.5, then steps of 0.1) was right. It is optional and additive: a model made before this, or from a tool that cannot score, has no block and the screens say "not measured". Field list: [API reference](../api-reference.md#operating-points-evaluationoperating_points-and-the-plain-keys).
- **What "enough data" means.** At least **400** drawn boxes on the Checking pictures in all **and** at least **30** for every label **and** a Final test split that has drawn boxes (`MIN_VALIDATION_BOXES`, `MIN_VALIDATION_BOXES_PER_LABEL`). Below that the numbers are still stored but `enough_data` is false, nothing is `recommended`, and the sentence begins with the reason. Why 400: the spike (above) found about plus or minus 4 recall points with 630 boxes, and the comment at `MIN_VALIDATION_BOXES` in the code records about 7 to 8 points with 150; "at least 400" was its practical minimum. That is a judgement, not a proven threshold. A score range needs at least 20 predictions **and** a Final test split of at least 30 pictures and 30 drawn boxes, otherwise it reads "too few … to say".
- **The recommended rule.** A suggested cut-off is `recommended` only if the data is enough **and** the target was reached in at least **90%** of the 1,000 re-draws of the pictures (`interval90.resamples_used`). The check screen's builder offers "Use the suggested setting" only for the 80% target and only when it is recommended; it fills the slider (rounded down to a whole percent), saves nothing, and is never applied automatically. A Final test result below the target is said in words but does not by itself withdraw the suggestion, so read the sentence, not only the flag.
- **Rounding never flatters.** Cut-offs, stored shares, shown percentages and the low end of a range are rounded **down**; the high end of a range and any "no more than" ceiling are rounded **up** (`floor4`, `ceil4`, `share_down`, `pct_down`, `pct_up`). The "about N in 10" labels (`odds_label`) read a good share down to the next everyday fraction from the low end of the 90% range, and a wrong share up; they are never "at least". When a range cannot be given the sentence says "range not available" and in how many re-draws the target was reached. Scores that are not finite numbers are dropped and the rest clipped to 0 to 1 before anything is computed.
- **Where it shows.** The results card has a closed "How far to trust the scores" disclosure (the measured sentences per score range and the cut-off sentences, ending "A person chooses the setting."). The check screen words a detection's score from its band, for example "Right about 1 in 2 times in our check · score 0.45", or shows "score 0.45 (the computer's own number, not checked)" when nothing is measured. The fixed phrases "Very sure / Fairly sure / Somewhat sure / Not very sure" (cut at 0.9, 0.75, 0.5, invented before any measurement) are **gone** from the check screen.
- **Numbers.** Two different things, both synthetic. (1) The **spike** (above): one weak model trained from scratch on synthetic shapes, 630 fit boxes and 995 test boxes: a recall target of 80% was reachable at a cut-off of 0.235 (test recall 0.790, precision 0.521) and 90% and 95% were not (ceiling 0.883 on fit, 0.866 on test). (2) The **code's own tests** use boxes and scores *generated* with a known distribution (hit rate 0.88, Beta-shaped scores), so a "model" is not involved at all; the example bodies in the [API reference](../api-reference.md#operating-points-evaluationoperating_points-and-the-plain-keys) come from that generator (an 80% target gave a cut-off of 0.4109 there). Neither says anything about a real scanner model or real pictures.
- **Not done.** Platt or isotonic calibration; per-label cut-offs; any run on real imagery or by real operators; checking the thresholds on a second, independent set of pictures; comparing a new model's operating points with the approved model's; a minimum for how many predictions a score range needs that is based on more than the spike (20 is a judgement).

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

**Unverified, ask a lawyer:** whether the ImageNet "non-commercial research" terms reach *weights trained on* ImageNet and models fine-tuned from them; whether Flickr's mixed per-image licences in COCO reach weights trained on those images; whether torchvision's weights files carry any further terms I did not find. This page is not legal advice. If any doubt applies to you, use `"none"`. (The optional D-FINE tool has its own table, in [its section below](#d-fine-the-optional-second-tool); its COCO question is the same.)

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

- **From random weights, 8 epochs, 200 pictures:** no test box was found at the 0.5 operating confidence (0 of 144; precision undefined because nothing was predicted that confident). AP50 is not zero (0.094 overall) because it is computed from every prediction down to the 0.05 floor; the model ranks some boxes sensibly but is never sure. The console's approval gate asks for a deliberate acknowledgement for a model like this ("it found none of the test boxes"), and also when the plain-results verdict is "not ready" or "cannot tell" (too few test boxes or too few found; `weak_results_reason` in `lifecycle.py`); it is an acknowledge-don't-block gate, and a "needs more examples" verdict needs only the approval note. I did not test whether many more epochs would fix it.
- **With either published start, same 8 epochs and same data:** mAP50 0.948 (ImageNet backbone) and 0.988 (COCO) on seed 7; 0.899 and 0.976 on seed 8. In both runs the COCO start scored higher than the ImageNet-backbone start. Two runs are not enough to call that difference reliable, and the seeds change the data as well as the start, so run-to-run spread is visible: the ImageNet-backbone triangle recall was 0.896 on seed 7 and 0.646 on seed 8.
- **Why the pretrained starts help here** is not tested by this benchmark. A reasonable guess is that generic edge/shape features carry over to drawn shapes; that guess says nothing about X-ray or camera imagery, where the images look nothing like ImageNet or COCO.
- **Precision/recall are at one threshold (0.5)**; AP50 is threshold-free. A different operating point trades one for the other. The false positives counted here include the unlabelled look-alike distractors, so they are "real" mistakes of the model on this data, not noise in the metric.
- **Wall times are not a speed comparison.** The machine was shared during all runs (load average about 8–10: other jobs and browser tests ran at the same time), so the same mode can take very different times. `none` took 859.6 s and the pretrained runs 326–542 s, but with the machine shared I cannot say how much of that gap is the setting and how much is load. Do not infer that pretrained training is faster from these numbers.
- The numbers above are what the script printed. Nothing else is claimed.

## D-FINE: the optional second tool

Backend `dfine-coco` (`web/projects/training/dfine.py`; tests `web/projects/tests_training_dfine.py`, 81 test methods, run in a separate environment that has transformers and scipy). **It is opt-in: the default tool is still `torchvision-fasterrcnn`** and the yellow "Teach the computer" button keeps using it; D-FINE is chosen only by an administrator in the Teach panel's "Details for your administrator" fold or by naming the backend in the API. Whether it should become the default ready-made base is the owner's open licence decision, not something this page settles.

**What it is.** A real-time DETR-style detector (D-FINE) from Hugging Face transformers, run on CPU at 640 x 640, always started from one of two COCO-only checkpoints: `ustc-community/dfine-nano-coco` (3.8 million parameters, about 15 MB, revision `066438d3d8f0…`) or `ustc-community/dfine-small-coco` (10.4 million, about 41 MB, revision `f79e65b5fbb3…`). Both are pinned by revision and every file by size and SHA-256; the checkpoint cannot be named in a request. Starting from nothing is refused (a detector of this kind started from random weights is not usable on a few hundred pictures; this is the same lesson as the torchvision from-scratch result above, not a measurement of D-FINE). It trains on the **train split only**. Needs `scipy` in the worker environment (see [`web/DEPLOYMENT.md`](../../web/DEPLOYMENT.md#ready-made-starting-models-in-the-teach-panel)); downloads use the same console switch as the default tool and come from `huggingface.co` and `*.hf.co`.

**What was measured: SYNTHETIC shapes, nano only, one machine, one run per setting.** The same kind of drawn circles, squares and triangles as the benchmark above (200 training pictures, seed 7, batch 4, learning rate 1e-4, Apple M3 CPU; the test split of 60 pictures holds 144 boxes), scored at the product's 0.5 confidence and IoU 0.5 (comments at `QUALITY_PLANS` in `dfine.py`):

| Passes over the 200 pictures (optimiser steps) | Found of the 144 test boxes | Wrong boxes drawn | Recall | Precision | mAP50 |
|---|---:|---:|---:|---:|---:|
| 2 (100 steps) | 0 | 4 | 0.000 | 0.000 | 0.076 |
| 8 (400 steps) | 120 | 75 | 0.833 | 0.615 | 0.792 |
| 12 (600 steps) | 130 | 48 | 0.903 | 0.730 | 0.907 |

(Counts read from the three run logs in the QA scratch folder, `dfine-run1.log`, `dfine-run3.log`, `dfine-run2.log`; the generator is the one in `web/scripts/benchmark_training.py`, driven by a scratch script.) Those are the tool's "quick" (400 steps) and "better" (600 steps) targets, and 100 steps was too few. Reading them without overreaching: (1) shapes drawn on made-up backgrounds say nothing about real scanner images; (2) **they are not comparable with the torchvision numbers above**, which come from a different tool, a different number of passes and one run each, so no ranking of the two tools follows; (3) the small model was not run, so it has no time estimate and no figure; (4) a pass over 200 pictures took about 37 to 65 seconds on that machine (logged per-pass times of the real runs; `SECONDS_PER_PICTURE_EPOCH` for nano is 0.35 seconds per picture and pass, rounded up from the slowest single pass at 0.326), more on a slower computer; (5) ONNX export of the trained model did not work, so no export path is offered; (6) wrong boxes include the generator's unlabelled look-alike distractors. Nothing else is claimed.

**Licence facts, engineering read, not legal advice** (the long text is `LICENSE_NOTE` in `dfine.py`, served as the backend's `license_note`; sources re-fetched 2026-10-23):

| Item | Finding | Source |
|---|---|---|
| D-FINE code | Apache License 2.0 | `github.com/Peterande/D-FINE` `LICENSE` (Apache 2.0 text) |
| Model code used here | Hugging Face transformers 5.17.0, Apache-2.0 | installed package metadata (`License: Apache 2.0 License`) |
| The two checkpoints | the model cards declare `license: apache-2.0` and `datasets: coco` (Hugging Face API tags `license:apache-2.0`, `dataset:coco`; revisions match the pinned ones) | `huggingface.co/ustc-community/dfine-nano-coco` and `…/dfine-small-coco` |
| What Apache-2.0 on the weights does and does not cover | It is the model repository's licence. **It is not a statement about the licences of the data the weights were trained on.** The COCO question is therefore unchanged from the torchvision COCO start (annotations CC BY 4.0, the COCO Consortium does not own the images, use of them must follow the Flickr terms; whether that reaches weights trained on them is unverified). The picture-model part (PPHGNetV2 backbone files loaded by the D-FINE authors) has pretraining data this project has not verified | `LICENSE_NOTE`; section above |
| Who licenses the converted weights | Unverified. The `ustc-community` organisation's own card says it "is created by the Transformers team to maintain the D-FINE Object Detection model checkpoints, originally released by the University of Science and Technology", so it is a maintenance organisation of the Transformers team, not the paper's authors | `huggingface.co/ustc-community` (organisation card) |
| Objects365 checkpoints | **Refused in code** (`refuse_checkpoint`; any id with `obj365`, `o365`, `objects365` or `obj2coco` is rejected). D-FINE's README says checkpoints trained or pretrained on Objects365 (`*_obj365`, `*_obj2coco`) "may be subject to the Objects365 dataset terms and should not be assumed to be commercially cleared under the D-FINE license"; `LICENSE_NOTE` also quotes Objects365 describing itself as "available for the academic purpose only" (that second quote was not re-fetched in this pass) | D-FINE `README.md` (its "License note" line) |

Ask a lawyer before commercial use. Models you train are derived from your own images; check the licence of those images.

## Comparing a new model with the model in use (prototype, synthetic checks only)

Code: `web/projects/training/comparison.py`; tests: `web/projects/tests_comparison.py` (70 test methods; scripted test-double backends that "find" exactly the pictures a test tells them to, and hand-built blocks, so **no real model and no real picture is involved**). Field-by-field shape, verdict rules and examples: [API reference](../api-reference.md#comparison-how-a-new-model-did-against-the-model-in-use). **Nothing is measured about any real model here, and nothing in this section is an accuracy claim.**

What it does, in plain terms: when a newly taught model finishes and the project already has a model in use, the helper runs the model in use over the new model's Final test pictures too (one extra pass), scores both with the same code, the same confidence and the same overlap rule, and stores how many of the person-drawn boxes each found. The comparison is **paired**: it counts the boxes only the new model found (b) and only the model in use found (c), because `b − c` is exactly the difference in boxes found, and two percentages can hide how few boxes actually changed.

How the words are chosen (every number is a judgement that was set, not measured):

- **Better / worse** need `b − c` (or `c − b`) of at least the larger of **3 boxes** and **10% of the test boxes** (rounded up). Closer than that reads "about the same". A handful of boxes is what one lucky or unlucky picture can swing.
- **"Cannot tell"** whenever the comparison is not fair (next point), there are no test boxes, or **any** label has fewer than **30** test boxes, a label with none at all included. That is the same 30-per-label floor as the results card's verdict, and it is why a project with a rare label will often read "cannot tell" no matter how the models do.
- **Fairness.** The model in use may already have seen some of the new model's test pictures (taught from, or used for checking, found by matching SHA-256 against the saved dataset list of the run that made it). Then the test says nothing fair and the verdict is "cannot tell" with the number of pictures shared. The same applies when it is not known what the model in use was taught from (no run, an imported model) or its saved list no longer matches its checksum or picture count. Near-copies of a picture are not detected, so a "fair" comparison can still flatter the model in use or the new one.
- **Wrong boxes** (extra or misplaced boxes) are shown but never change the verdict.
- The text is **rebuilt from the stored counts every time it is read**; a stored sentence is never shown as it was saved.

What it does not do: it does not change the approval rules (it is information in the approve dialog and on the results card; approving still needs a note and, for weak results, a tick), it does not look at confidence scores or cut-offs of the two models, it has no timeout for a model that hangs inside a pass, and it is not computed when there is no model in use, no test split, or the model in use cannot run in the helper. Comparing on **your own** pictures is still the point of the next section; ~30 Final test boxes per label is the least that lets this block say anything at all.

## How to measure on your own data

The point: a number is only worth reading if it comes from pictures the model never saw, in enough quantity, from the situation you will actually use it in.

1. **Label honestly.** Boxes in your project are the answer key; if labellers disagree or miss objects, the score is measuring the labellers.
2. **Make sure the Final test group is big enough.** The "Teach the computer" button saves a snapshot for you with the default 80 / 10 / 10 split when the accepted boxes have changed since the last one (that split gave 12 Training, 2 Checking and **1** Final test picture on 15 pictures in a test run, which is far too few to say anything). The picture check above the button says how many pictures and boxes of each label would be held back for the Final test. To use a larger Final test share, save a snapshot first under *Make a training set* → *Save a snapshot* → **Advanced** and give **Final test** an amount above 0; the button reuses the latest snapshot as long as it holds exactly the current accepted boxes and labels, so do not change boxes between saving it and pressing the button. With no Final test pictures you will get no results.
3. **Press "Teach the computer"** (choose Quick or Better if you wish; an administrator can pick a particular snapshot under "Details for your administrator"). When it finishes, open **See the results** (or *Your taught models* → results). The sentences per label ("found X of Y") and the "Pictures it got wrong" list are read from the same stored evaluation; the table "How it did on each label, on the Final test pictures" is computed by the same evaluation code the benchmark above used.
4. **Read the numbers per label, with their counts.** Found / Missed / Wrong boxes it drew, per label, on the Final test pictures only. The "limits" printed with the results repeat the rules below.
5. **Do not approve on a number you cannot defend** (see counts below). The approval screen needs a note, and it asks for a separate tick ("acknowledge weak results", `acknowledge_weak_results` in the API, otherwise 409 and nothing changes) when the model found none of the test boxes, has no held-out results, or the plain verdict is "not ready" or "cannot tell"; a "needs more examples" verdict needs only the note. The tick is written into the approval record. Staging a model still needs held-out results.

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
- `web/projects/training/torchvision_frcnn.py` (`start_options`, `start_check`, `resolve_start`, `derive_settings`, `teach_preview`) and the Teach part in `web/projects/static/projects/console.js` — the one-click button, the plan sentence and the closed "Details for your administrator" fold; `web/projects/tests_pretrained_ui.py` pins what the server tells the page and refuses, `web/projects/tests_training_auto.py` pins `auto`, the settings table and the time constants.
- `web/projects/datasets/health.py`, `web/projects/training/report.py` — the picture check and the plain results; see the [API reference](../api-reference.md#console-http-api-the-teach-flow-additions).
- `web/projects/training/operating_points.py`, `web/projects/tests_operating_points.py` — cut-off suggestions, score-range wording, rounding rules and the "enough data" rule; the stored shape is in the [API reference](../api-reference.md#operating-points-evaluationoperating_points-and-the-plain-keys).
- `web/projects/training/dfine.py`, `web/projects/tests_training_dfine.py` — the optional D-FINE tool, its pinned checkpoints and licence note.
- `web/projects/training/comparison.py`, `web/projects/tests_comparison.py` — the comparison with the model in use (rules, fairness, reader).
- `web/projects/feedback_import.py`, `web/projects/tests_feedback_import.py` — pulling check-screen corrections into a project; see [improve-loop.md](improve-loop.md).
- `web/projects/console_settings.py`, `web/projects/settings_views.py`, `web/projects/tests_console_settings.py` — the administrator's download switch.
