"""Opt-in second training backend: D-FINE (nano / small, COCO-only checkpoints) on CPU via Hugging Face transformers.

Why it exists: it is a second ready-made start next to the torchvision reference backend, with an explicit Apache-2.0
statement on the published weights. It does NOT change the default. ``DEFAULT_BACKEND`` stays ``torchvision-fasterrcnn``
and the one-click Teach start keeps using it; this backend is chosen only by naming it (the Teach panel's administrator
fold). Choosing a default ready-made base is the owner's open licence decision; see ``license_note``.

What it will and will not load: only the two exact checkpoint ids in ``CHECKPOINTS`` at their pinned revisions, with
every file's SHA-256 pinned. The Objects365-derived checkpoints (``*-obj365``, ``*-obj2coco``) are REFUSED (see
``refuse_checkpoint``): D-FINE's own README says they "should not be assumed to be commercially cleared". Starting from
nothing (``pretrained = "none"``) is refused too: a detector with a transformer head started from random weights is
useless on a few hundred pictures.

Download: nothing is fetched unless the same switch that governs the torchvision backend's downloads is on (the
availability helpers are imported from ``torchvision_frcnn`` so the console switch and the helper's report apply here
too). A file already in ``<data home>/models/pretrained/dfine/...`` that matches its pinned SHA-256 is used with no
download. Weights are read with safetensors (no pickle); the saved model is one ``torch.save`` file read back with
``weights_only=True``.

Coordinates: D-FINE predicts normalised centre-x, centre-y, width, height on the (non-aspect-preserving) resized
picture, which is the same as normalised coordinates on the original. They are converted to Athena's normalised
top-left x, y, width, height (``BoxPrediction``). Inputs are resized to ``INPUT_SIZE`` square and scaled to 0..1 (the
checkpoint's preprocessor config says ``do_normalize: false``), so no image processor is imported.

Module import is torch-free; torch and transformers are imported inside the methods the worker calls.
"""

from __future__ import annotations

import hashlib
import importlib.util
import errno
import math
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image

from . import torchvision_frcnn as tv
from .base import (
    Availability,
    BoxPrediction,
    CancelCallback,
    EvalItem,
    ProgressCallback,
    StartBlocked,
    TrainingBackend,
    TrainingCancelled,
    TrainResult,
)

IMAGE_EXTENSIONS = tv.IMAGE_EXTENSIONS
INPUT_SIZE = 640
"""The size the checkpoints were trained at. Fixed; the artifact records the size it was trained at."""
MIN_BOX_PIXELS = 1.0
HEAD_LR_FACTOR = 1.0
BACKBONE_LR_FACTOR = 0.1
"""The ready-made picture "eyes" are changed more gently than the new class layers (usual DETR fine-tuning practice)."""
GRADIENT_CLIP = 0.1
"""D-FINE's own training recipe clips the gradient norm at 0.1."""
WEIGHT_DECAY = 1e-4

REQUIRED_MODULES = ("torch", "transformers", "scipy")
"""scipy is needed by transformers' Hungarian matcher, which the training loss uses (verified: without it the first
training step raises ``RTDetrHungarianMatcher requires the scipy library``)."""
SIZES = ("nano", "small")
AUTO = "auto"
PRETRAINED_CHOICES = ("coco", AUTO)
DEFAULTS = {
    "epochs": 10,
    "batch_size": 4,
    "learning_rate": 1e-4,
    "seed": 0,
    "threads": 0,
    "size": "nano",
    "pretrained": "coco",
}
EXTRA_KEYS = ("quality", "pretrained_requested", "derived")
DERIVED_KEYS = ("epochs", "batch_size")
QUALITIES = ("quick", "better")
ARTIFACT_NAME = "model.pt"
ARTIFACT_FORMAT = "athena.dfine/1"
MAX_BOXES_LIMIT = 1_000_000

# -- the only checkpoints this backend will load ---------------------------------------------------------
HF_HOST = "https://huggingface.co"
CHECKPOINTS: dict[str, dict[str, Any]] = {
    "nano": {
        "repo_id": "ustc-community/dfine-nano-coco",
        "revision": "066438d3d8f0da137a37b38fdf3368fd4afceced",
        "params": 3_798_993,
        "approx_mb": 15,
        "files": {
            "config.json": {"sha256": "7edf2d96d3deb10aa4ec920be0e8e444b617454545bb58093e32bd8e2b96fa2f", "bytes": 6557},
            "model.safetensors": {
                "sha256": "19e06bdc873da819920a8d373b879721a5b9759d822f8213220bb09abbdab58b",
                "bytes": 15_278_996,
            },
        },
    },
    "small": {
        "repo_id": "ustc-community/dfine-small-coco",
        "revision": "f79e65b5fbb33ceb9d3ebba042955d7410c608f8",
        "params": 10_350_057,
        "approx_mb": 41,
        "files": {
            "config.json": {"sha256": "9f206c4c935c67c957af2f15c3333b4ff224e72b1881bc7bbde9f873edf4448e", "bytes": 6865},
            "model.safetensors": {
                "sha256": "a144baff3a27bfd538fe37b11fa8d4fca5744b8c054b5301e7c699a1379cd18f",
                "bytes": 41_497_620,
            },
        },
    },
}
"""Verified 2026-10-23 from https://huggingface.co/api/models/<repo_id> (``sha``, ``safetensors.total``, tags
``license:apache-2.0`` and ``dataset:coco``) and its ``/tree/main`` listing (LFS ``oid`` = SHA-256 of
model.safetensors). ``config.json`` hashes are of the bytes served at the pinned revision."""
ALLOWED_REPO_IDS = frozenset(c["repo_id"] for c in CHECKPOINTS.values())
OBJECTS365_MARKERS = ("obj365", "o365", "objects365", "obj2coco")

CHECKPOINT_WHAT = {
    "nano": "D-FINE nano (3.8 million parameters) trained on COCO",
    "small": "D-FINE small (10.4 million parameters) trained on COCO",
}

# -- plain-language texts -------------------------------------------------------------------------------
LICENSE_NOTE = (
    "Code: D-FINE (github.com/Peterande/D-FINE) is under the Apache License 2.0 (its LICENSE file), and the model code "
    "used here is Hugging Face transformers (Apache-2.0). Weights: only the two checkpoints "
    "ustc-community/dfine-nano-coco (revision 066438d3d8f0da137a37b38fdf3368fd4afceced) and "
    "ustc-community/dfine-small-coco (revision f79e65b5fbb33ceb9d3ebba042955d7410c608f8) are used; their model cards "
    "(huggingface.co/ustc-community/dfine-nano-coco and .../dfine-small-coco) declare `license: apache-2.0` and "
    "`datasets: coco` and say '_coco -> model trained on COCO'. They are fetched only when "
    "ATHENA_TRAINING_ALLOW_DOWNLOAD=1 (or the console download switch) is on, and every file is checked against a "
    "pinned SHA-256. This note quotes what the publisher wrote; the Apache-2.0 declaration is the model repository's "
    "licence and is NOT a statement about the licences of the data the weights were trained on. The COCO "
    "training-data question is therefore UNCHANGED from the torchvision COCO start: cocodataset.org says the "
    "annotations are CC BY 4.0, the COCO Consortium does not own the images and their use must follow the Flickr terms "
    "(mixed per-image licences); whether those terms reach weights trained on them is unverified. The picture-model "
    "part (PPHGNetV2 backbone files loaded by the D-FINE authors) has pretraining data that this project has NOT "
    "verified. Who the licensor is for the Hugging Face-converted weights is also unverified: the ustc-community "
    "organisation's own card says it 'is created by the Transformers team to maintain the D-FINE Object Detection "
    "model checkpoints, originally released by the University of Science and Technology', so it is a maintenance "
    "organisation of the Transformers team and not the authors of the D-FINE paper. Refused on purpose: "
    "checkpoints trained or pretrained on Objects365 (*_obj365, *_obj2coco): the D-FINE README says they "
    "'may be subject to the Objects365 dataset terms and should not be assumed to be commercially cleared under the "
    "D-FINE license', and Objects365 describes itself as 'available for the academic purpose only'. This is an "
    "engineering note, not legal advice and not a clearance for commercial use: ask a lawyer before commercial use. "
    "Models you train are derived from your own images; check the licence of those images."
)
CONSTRAINTS = (
    "Needs torch==2.9.1, transformers==5.17.0 and scipy in the worker environment (web/requirements-autolabel.txt).",
    "CPU only. Pictures are resized to 640 x 640; expect about a second or two per step of 4 pictures on a recent "
    "laptop (nano), more for small. No GPU is used.",
    "Always starts from a COCO-only ready-made checkpoint (nano or small); starting from nothing is refused. "
    "Objects365-based checkpoints are refused. The first run needs ATHENA_TRAINING_ALLOW_DOWNLOAD=1 (about 15 MB nano, "
    "41 MB small); read the licence note first.",
    "This kind of detector learns a new set of objects more slowly than the default one: give it a few more passes "
    "over the pictures, and read the evaluation report before believing any number.",
    "Uses the train split only; the validation split is not used by this backend.",
    "Not run on real scanner images yet: the only measurements are on synthetic shapes, and the figures given here "
    "(the number of passes, the time estimate) come from synthetic shapes on one computer and are not comparable with "
    "the default tool's.",
)
LAWYER = tv.LAWYER
START_TEXT = {
    "title": "Start from a general object model (COCO), D-FINE",
    "summary": (
        "The computer starts with a ready-made D-FINE model that already finds everyday objects in photos (taught on the "
        "COCO collection), and only learns your own kinds of item on top. It is the only way this tool starts: "
        "starting from nothing is not offered."
    ),
    "licence_plain": (
        f"{tv.GENERAL_TERMS} The publisher of this model says it is under the Apache-2.0 licence, but that is the "
        "licence of the model file, not a statement about the photos it was taught from. The photos in the COCO "
        "collection come from many owners with mixed terms, and the collection says it does not own them. Nobody has "
        f"confirmed whether those terms reach a model that was taught from them. {LAWYER}"
    ),
}
NONE_REFUSAL = (
    "pretrained must be 'coco' for the D-FINE tool. It always starts from the ready-made COCO model: a detector of this "
    "kind started from nothing needs far more pictures than a few hundred, so Athena does not offer that here."
)

# -- derive_settings / time estimate -----------------------------------------------------------------------
BATCH_SIZE = DEFAULTS["batch_size"]
MIN_EPOCHS = 3
FLOOR_EPOCHS = 2
QUALITY_PLANS = {
    # target_steps: optimizer steps wanted. A DETR-style head needs more steps than the torchvision default to adapt to
    #   new labels. Measured on synthetic shapes (200 train pictures, batch 4, seed 7, nano, learning rate 1e-4, one
    #   M3 CPU; test split of 60 pictures at confidence 0.5): 100 steps found none of 144 boxes (mAP50 0.076);
    #   400 steps (8 passes) mAP50 0.792, recall 0.833, precision 0.615; 600 steps (12 passes) mAP50 0.907, recall
    #   0.903, precision 0.730. So "quick" aims at 400 and "better" at 600. Real images may need more.
    # max_epochs / budget: as in the torchvision backend (budget = pictures x epochs, which CPU time follows):
    #   1600 = 400 steps of 4 pictures; 2400 = 600 steps.
    "quick": {"target_steps": 400, "max_epochs": 40, "budget": 1600},
    "better": {"target_steps": 600, "max_epochs": 80, "budget": 2400},
}
SECONDS_PER_PICTURE_EPOCH = {"nano": 0.35}
"""Training seconds per (training picture x epoch) on CPU, nano only (small was not measured, so it has no entry and no
estimate). Measured, Apple M3 CPU, 4 torch threads, 640 px, synthetic shapes: 0.266 (2 passes, includes model load),
0.234 (12 passes), 0.238 (8 passes) s per picture-pass on average; the slowest single pass was 0.326. 0.35 rounds the
slowest up, so the estimate leans long. An estimate for that kind of machine, not a promise."""
ESTIMATE_NOTE = (
    "This is an estimate, not a promise. It is worked out from timing on one laptop-class processor (an Apple M3) on "
    "synthetic pictures, and covers the teaching itself, not time spent waiting in the queue. A slower computer, or other "
    "programs running, will take longer. These figures come from synthetic shapes on one computer and are not "
    "comparable with the default tool's."
)


def derive_settings(n_train_pictures: int, quality: str, batch_size: int | None = None) -> dict[str, int]:
    """Same rules as the torchvision backend's, with this backend's own step targets (pure and deterministic)."""
    if isinstance(n_train_pictures, bool) or not isinstance(n_train_pictures, int) or n_train_pictures < 1:
        raise ValueError("There must be at least one training picture to work out the settings.")
    if not isinstance(quality, str) or quality not in QUALITIES:
        raise ValueError("quality must be one of: " + ", ".join(f"'{q}'" for q in QUALITIES) + ".")
    plan = QUALITY_PLANS[quality]
    if batch_size is not None and (isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1):
        raise ValueError("batch_size must be a whole number of at least 1.")
    batch = min(BATCH_SIZE if batch_size is None else batch_size, n_train_pictures)
    steps_per_epoch = -(-n_train_pictures // batch)
    epochs = -(-plan["target_steps"] // steps_per_epoch)
    epochs = max(MIN_EPOCHS, min(epochs, plan["max_epochs"]))
    epochs = min(epochs, max(FLOOR_EPOCHS, plan["budget"] // n_train_pictures))
    return {"epochs": epochs, "batch_size": batch}


def estimate_minutes(n_train_pictures: int, epochs: int, size: str | None = "nano") -> int | None:
    per = SECONDS_PER_PICTURE_EPOCH.get(size or "")
    if per is None:
        return None
    return max(1, math.ceil(n_train_pictures * epochs * per / 60))


# -- allow-list --------------------------------------------------------------------------------------------
def refuse_checkpoint(repo_id: str) -> None:
    """Raise ``ValueError`` unless ``repo_id`` is one of the two COCO-only checkpoints listed above."""
    if repo_id in ALLOWED_REPO_IDS:
        return
    lowered = str(repo_id).lower()
    if any(marker in lowered for marker in OBJECTS365_MARKERS):
        raise ValueError(
            f"The checkpoint {repo_id!r} was trained or pretrained on Objects365. Its publisher says such checkpoints "
            "'should not be assumed to be commercially cleared', so Athena refuses it. Only the COCO-only nano and small "
            "checkpoints can be used."
        )
    raise ValueError(
        f"The checkpoint {repo_id!r} is not on Athena's list. Only these two COCO-only checkpoints can be used: "
        + ", ".join(sorted(ALLOWED_REPO_IDS))
        + "."
    )


def checkpoint_for(size: str) -> dict[str, Any]:
    if size not in CHECKPOINTS:
        raise ValueError("size must be one of: " + ", ".join(f"'{s}'" for s in SIZES) + ".")
    info = CHECKPOINTS[size]
    refuse_checkpoint(info["repo_id"])  # belt and braces: the table itself is checked against the allow-list
    return info


# -- cache / download --------------------------------------------------------------------------------------
def checkpoint_dir(size: str) -> Path:
    info = checkpoint_for(size)
    return tv.pretrained_dir() / "dfine" / info["repo_id"].replace("/", "__") / info["revision"]


def file_url(size: str, name: str) -> str:
    info = checkpoint_for(size)
    return f"{HF_HOST}/{info['repo_id']}/resolve/{info['revision']}/{name}"


def _sha256(path: Path) -> str:
    return tv._sha256(path)  # noqa: SLF001 - the one hashing helper


def _file_ok(path: Path, expected: dict[str, Any]) -> bool:
    try:
        return path.is_file() and path.stat().st_size == expected["bytes"] and _sha256(path) == expected["sha256"]
    except OSError:
        return False


_VERIFIED: dict[tuple[str, int, int, str], bool] = {}


def cached_checkpoint_ok(size: str) -> bool:
    """True when every pinned file for ``size`` is cached and matches its pinned SHA-256 (memoised per stat)."""
    info = checkpoint_for(size)
    folder = checkpoint_dir(size)
    for name, expected in info["files"].items():
        path = folder / name
        try:
            stat = path.stat()
        except OSError:
            return False
        key = (str(path), stat.st_size, stat.st_mtime_ns, expected["sha256"])
        if key not in _VERIFIED:
            _VERIFIED[key] = _file_ok(path, expected)
        if not _VERIFIED[key]:
            return False
    return True


SAVE_FAILED = "Could not save the pretrained weights on this computer{why}. Check the Athena data folder (models/pretrained/dfine) and try again."
DOWNLOAD_HOSTS = "huggingface.co and *.hf.co"
"""Real downloads from huggingface.co are redirected to a content-delivery host under hf.co; the texts name both."""
PINNED_HOSTS = tv.AllowedHosts(("huggingface.co",), (".hf.co",), DOWNLOAD_HOSTS)
"""The only places a redirect may lead (over https): huggingface.co itself and any host under ``.hf.co``."""


def _save_failed(error: OSError) -> ValueError:
    if error.errno == errno.ENOSPC:
        why = " (the disk is full)"
    elif error.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        why = " (Athena is not allowed to write there)"
    elif error.errno in (errno.EISDIR, errno.ENOTDIR):
        why = " (a folder is in the way of a file, or the other way round)"
    else:
        why = ""
    return ValueError(SAVE_FAILED.format(why=why))


def _fetch(url: str, destination: Path, sha256: str, expected_bytes: int | None = None) -> None:
    """Download ``url`` to ``destination`` and check the pinned SHA-256. Tests replace this.

    With ``expected_bytes`` (the pinned size) the transfer is cut off one byte past it, so a server that keeps sending
    cannot fill the disk before the checksum is looked at. The temporary ``.part`` file is created fresh (any existing
    file or symlink at that name is removed first, and it is opened with O_EXCL | O_NOFOLLOW), so a pre-planted link
    is never written through. Trouble while reading from the network and trouble while writing to disk are reported
    separately."""
    partial = destination.with_name(destination.name + ".part")
    try:
        try:
            response = tv.open_pinned(url, PINNED_HOSTS, timeout=tv.DOWNLOAD_TIMEOUT_SECONDS)
        except urllib.error.URLError as error:
            raise ValueError(
                f"Could not download the pretrained weights ({getattr(error, 'reason', error)}). Check this "
                "computer's internet access and try again."
            ) from None
        except OSError:
            raise ValueError(_READ_FAILED) from None
        with response:
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                partial.unlink(missing_ok=True)  # never reuse or follow something that was already there
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                out = os.fdopen(os.open(partial, flags, 0o644), "wb")
            except OSError as error:
                raise _save_failed(error) from None
            with out:
                digest = hashlib.sha256()
                total = 0
                while True:
                    ask = 1 << 20 if expected_bytes is None else min(1 << 20, expected_bytes + 1 - total)
                    try:
                        chunk = response.read(ask)
                    except OSError:
                        raise ValueError(_READ_FAILED) from None
                    if not chunk:
                        break
                    total += len(chunk)
                    if expected_bytes is not None and total > expected_bytes:
                        raise ValueError(
                            f"The download was larger than expected ({expected_bytes:,} bytes for this file), so it "
                            "was stopped and discarded."
                        )
                    digest.update(chunk)
                    try:
                        out.write(chunk)
                    except OSError as error:
                        raise _save_failed(error) from None
        if digest.hexdigest() != sha256:
            raise ValueError("The downloaded weights file did not match its pinned checksum, so it was discarded.")
        try:
            partial.replace(destination)
        except OSError as error:
            raise _save_failed(error) from None
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass


_READ_FAILED = (
    "Could not download the pretrained weights (the connection was interrupted or timed out). Check this computer's "
    "internet access and try again."
)


def _blocked_message(size: str, info: dict[str, Any]) -> str:
    """Why nothing could be fetched, and how to change it - saying which setting is in charge."""
    try:
        from ..console_settings import SOURCE_CONSOLE, training_downloads

        source = training_downloads().source
    except Exception:  # noqa: BLE001 - the wording must never be the reason a run fails differently
        source, SOURCE_CONSOLE = "default", "console"
    helper = getattr(tv, "download_blocked_message", None)
    how = None
    if callable(helper):
        try:
            how = str(helper(source))
        except Exception:  # noqa: BLE001 - fall back to the sentence below
            how = None
    if not how:
        if source == SOURCE_CONSOLE:
            how = (
                "Downloads were switched off in the console (Access > Ready-made model downloads); the environment "
                "setting is ignored while a console choice exists. An administrator can switch them on there."
            )
        else:
            how = (
                "An administrator can switch it on in the console (Access, “Ready-made model downloads”) or set "
                f"{tv.DOWNLOAD_ENV}=1 in the environment of the training helper (manage.py train_worker)."
            )
    return (
        f"This run needs the ready-made D-FINE {size} model ({CHECKPOINT_WHAT[size]}, about {info['approx_mb']} MB), "
        f"but downloading is switched off on this computer. {how} Then run it again - the files are fetched once from "
        f"{DOWNLOAD_HOSTS} at a pinned version, checked against pinned checksums, and kept in your Athena data folder "
        "under models/pretrained/dfine. There is no from-scratch alternative for this tool; to download nothing, use "
        "the default tool instead. Read the licence note first: the model comes with its own training-data terms."
    )


def ensure_checkpoint(size: str) -> dict[str, Any]:
    """Make sure the pinned files for ``size`` are cached and verified; returns provenance for the run record.

    Nothing is fetched unless downloads are switched on (same switch as the torchvision backend). Files that are
    already cached and match their pinned SHA-256 are used without it. A stale or corrupt file is replaced."""
    info = checkpoint_for(size)
    folder = checkpoint_dir(size)
    source = "cache"
    for name, expected in info["files"].items():
        path = folder / name
        if _file_ok(path, expected):
            continue
        if not tv.downloads_allowed():
            raise ValueError(_blocked_message(size, info))
        try:
            path.unlink(missing_ok=True)  # a stale or corrupt file must not survive
        except OSError as error:
            if path.is_dir() and not path.is_symlink():
                raise ValueError(
                    f"Athena cannot save the model file {name}: a folder with that name is in its place under "
                    "models/pretrained/dfine in the Athena data folder. Remove or rename that folder and run it again."
                ) from None
            raise _save_failed(error) from None
        _fetch(file_url(size, name), path, expected["sha256"], expected_bytes=expected["bytes"])
        source = "download"
    weights = info["files"]["model.safetensors"]
    return {
        "mode": "coco",
        "what": CHECKPOINT_WHAT[size],
        "backend": "dfine-coco",
        "size": size,
        "repo_id": info["repo_id"],
        "revision": info["revision"],
        "source_url": file_url(size, "model.safetensors"),
        "sha256": weights["sha256"],
        "bytes": weights["bytes"],
        "files": {name: expected["sha256"] for name, expected in info["files"].items()},
        "licence_declared": "apache-2.0 (model card); COCO training-data terms not cleared - see license_note",
        "from": source,
    }


def downloads_for(worker: dict[str, Any]) -> tuple[bool, str]:
    """(allowed, who said so): exactly the torchvision backend's rule, so the console switch is inherited."""
    rule = getattr(tv, "downloads_for", None) or tv._downloads_for  # noqa: SLF001 - public name once it exists
    return rule(worker)


# -- helpers -----------------------------------------------------------------------------------------------
def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _int(config: dict[str, Any], key: str, low: int, high: int) -> int:
    value = config.get(key, DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{key} must be a whole number between {low} and {high}.")
    return value


def _json_safe(value: Any) -> Any:
    """Plain dict/list/str/number/None only, so the saved config loads with ``weights_only=True``."""
    import json

    return json.loads(json.dumps(value, default=str))


class DFineBackend(TrainingBackend):
    name = "dfine-coco"
    description = (
        "D-FINE (nano or small) on CPU, always started from the ready-made COCO-only checkpoint "
        "(Apache-2.0 declared on the model card). Opt-in second tool; the default stays the torchvision one."
    )
    license_note = LICENSE_NOTE
    dataset_format = "yolo"
    constraints = CONSTRAINTS

    def __init__(self) -> None:
        self._cache: tuple[tuple[str, int, int], Any, dict[str, Any]] | None = None

    # -- availability / config -------------------------------------------------------------
    def check_available(self) -> Availability:
        missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
        if not missing:
            return Availability(True, "torch, transformers and scipy are installed.")
        return Availability(
            False,
            f"{_join(missing)} {'is' if len(missing) == 1 else 'are'} not installed in this environment.",
            "Install the worker requirements (pip install -r web/requirements-autolabel.txt) in the "
            "environment that runs `manage.py train_worker`, then start the worker.",
        )

    def default_config(self) -> dict[str, Any]:
        return dict(DEFAULTS)

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        unknown = set(config) - set(DEFAULTS) - set(EXTRA_KEYS)
        if unknown:
            if unknown & {"checkpoint", "repo_id", "model", "revision", "weights"}:
                raise ValueError(
                    "The checkpoint cannot be chosen by name. This tool only uses its two COCO-only checkpoints "
                    "(setting 'size': 'nano' or 'small'); Objects365-based checkpoints are refused."
                )
            raise ValueError(f"Unsupported training setting: {', '.join(sorted(map(str, unknown)))}.")
        rate = config.get("learning_rate", DEFAULTS["learning_rate"])
        try:  # an integer too large for a float (10**400) raises OverflowError, which must not reach the caller
            rate_ok = not isinstance(rate, bool) and isinstance(rate, (int, float)) and 0 < float(rate) <= 1
        except (OverflowError, ValueError, TypeError):
            rate_ok = False
        if not rate_ok:
            raise ValueError("learning_rate must be a number above 0 and at most 1.")
        size = config.get("size", DEFAULTS["size"])
        if not isinstance(size, str) or size not in SIZES:
            raise ValueError("size must be one of: " + ", ".join(f"'{s}'" for s in SIZES) + ".")
        pretrained = config.get("pretrained", DEFAULTS["pretrained"])
        if isinstance(pretrained, str) and pretrained in ("none", "imagenet_backbone"):
            raise ValueError(NONE_REFUSAL)
        if not isinstance(pretrained, str) or pretrained not in PRETRAINED_CHOICES:
            raise ValueError("pretrained must be 'coco' (or 'auto', which becomes 'coco').")
        normalized = {
            "pretrained": pretrained,
            "size": size,
            "epochs": _int(config, "epochs", 1, 100),
            "batch_size": _int(config, "batch_size", 1, 16),
            "learning_rate": float(rate),
            "seed": _int(config, "seed", 0, 2**31 - 1),
            "threads": _int(config, "threads", 0, 64),
        }
        if "quality" in config:
            if not isinstance(config["quality"], str) or config["quality"] not in QUALITIES:
                raise ValueError("quality must be one of: " + ", ".join(f"'{q}'" for q in QUALITIES) + ".")
            normalized["quality"] = config["quality"]
        if "pretrained_requested" in config:
            if config["pretrained_requested"] != AUTO:
                raise ValueError(f"pretrained_requested can only be '{AUTO}'.")
            normalized["pretrained_requested"] = AUTO
        if "derived" in config:
            derived = config["derived"]
            if (
                not isinstance(derived, list)
                or not derived
                or not all(isinstance(key, str) for key in derived)  # before building a set: [[1]] and [{}] are unhashable
                or not set(derived) <= set(DERIVED_KEYS)
                or len(set(derived)) != len(derived)
            ):
                raise ValueError("derived must list which of " + ", ".join(DERIVED_KEYS) + " Athena chose.")
            normalized["derived"] = sorted(derived)
        return normalized

    # -- "auto" / derived settings (the same hooks the torchvision backend implements) -----------
    def _unavailable(self, size: str, worker: dict[str, Any]) -> tuple[str, str]:
        if cached_checkpoint_ok(size):
            return "", ""
        allowed, who = downloads_for(worker)
        if allowed:
            return "", ""
        mb = CHECKPOINTS[size]["approx_mb"]
        reason = (
            f"Downloading ready-made models is switched off on this computer, and this one (about {mb} MB) has not been "
            "downloaded before, so it cannot be used. Only an administrator can change this."
        )
        if who == "console" and not worker.get("online"):
            reason += (
                " The teaching helper is not running, so this is the setting of the console itself; the helper must have "
                "the same setting."
            )
        return reason, tv.ENABLE_STEPS

    def start_options(self, worker: dict[str, Any]) -> list[dict[str, Any]]:
        size = DEFAULTS["size"]
        reason, how = self._unavailable(size, worker)
        ready = cached_checkpoint_ok(size)
        return [{
            "value": "coco", "title": START_TEXT["title"], "summary": START_TEXT["summary"],
            "licence_plain": START_TEXT["licence_plain"], "available": not reason, "ready": ready,
            "needs_acknowledgement": True, "unavailable_reason": reason, "how_to_enable": how,
            "size_note": (
                f"Already on this computer (about {CHECKPOINTS[size]['approx_mb']} MB, verified). Nothing will be downloaded."
                if ready else
                f"About {CHECKPOINTS['nano']['approx_mb']} MB (nano) or {CHECKPOINTS['small']['approx_mb']} MB (small). "
                f"It is downloaded once, from {DOWNLOAD_HOSTS}, and then kept in Athena’s data folder."
            ),
        }]

    def start_check(self, config: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
        size = config.get("size", DEFAULTS["size"])
        reason, how = self._unavailable(size, worker)
        return {"needs_acknowledgement": True, "blocked": f"{reason} {how}".strip()}

    def resolve_start(self, worker: dict[str, Any]) -> dict[str, Any]:
        options = self.start_options(worker)
        usable = [o for o in options if o["available"]]
        if usable:
            return {"resolved": usable[0]["value"], "available": usable, "reason_if_none": "", "how_to_enable": ""}
        return {
            "resolved": None, "available": [],
            "reason_if_none": (
                "The D-FINE ready-made model cannot be used on this computer right now: it has not been downloaded "
                "before, and downloading is switched off. Starting it from nothing is not offered. Only an administrator "
                "can fix this."
            ),
            "how_to_enable": tv.AUTO_NONE_STEPS,
        }

    def prepare_config(self, config: dict[str, Any], *, train_pictures: int, worker: dict[str, Any]) -> dict[str, Any]:
        forged = [key for key in ("pretrained_requested", "derived") if key in config]
        if forged:
            raise ValueError(f"{forged[0]} is filled in by Athena and cannot be set in a request.")
        prepared = dict(config)
        if prepared.get("pretrained") == AUTO:
            plan = self.resolve_start(worker)
            if plan["resolved"] is None:
                raise StartBlocked(f"{plan['reason_if_none']} {plan['how_to_enable']}", start_from=plan)
            prepared["pretrained"] = plan["resolved"]
            prepared["pretrained_requested"] = AUTO
        if "quality" in prepared:
            chosen = [key for key in DERIVED_KEYS if key not in prepared]
            explicit = prepared.get("batch_size")
            if isinstance(explicit, bool) or not isinstance(explicit, int) or not 1 <= explicit <= 16:
                explicit = None
            derived_now = derive_settings(train_pictures, prepared["quality"], batch_size=explicit)
            if explicit is not None:
                prepared["batch_size"] = derived_now["batch_size"]
            for key, value in derived_now.items():
                prepared.setdefault(key, value)
            if chosen:
                prepared["derived"] = chosen
        return prepared

    def teach_preview(self, train_pictures: int | None, start: str | None) -> dict[str, Any]:
        preview: dict[str, Any] = {
            "train_pictures": train_pictures,
            "estimate_for_start": start,
            "seconds_per_picture_epoch": SECONDS_PER_PICTURE_EPOCH.get(DEFAULTS["size"]) if start else None,
            "estimate_note": ESTIMATE_NOTE,
        }
        if isinstance(train_pictures, bool) or not isinstance(train_pictures, int):
            train_pictures = None  # not a count: no estimate rather than a crash
            preview["train_pictures"] = None
        for quality in QUALITIES:
            if train_pictures is None or train_pictures < 1:
                preview[quality] = None
                continue
            settings = derive_settings(train_pictures, quality)
            preview[quality] = {
                **settings,
                "estimated_minutes": estimate_minutes(train_pictures, settings["epochs"], DEFAULTS["size"]) if start else None,
            }
        return preview

    def runtime_info(self) -> dict[str, Any]:
        import torch
        import transformers

        return {
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "device": "cpu",
            "threads": torch.get_num_threads(),
        }

    # -- dataset (the YOLO export is read exactly as the reference backend reads it) ---------------
    @staticmethod
    def read_classes(dataset_dir: Path) -> list[str]:
        return tv.TorchvisionFasterRCNN.read_classes(dataset_dir)

    @staticmethod
    def read_split(dataset_dir: Path, split: str):
        return tv.TorchvisionFasterRCNN.read_split(dataset_dir, split)

    def eval_items(self, dataset_dir: Path, split: str = "test") -> list[EvalItem]:
        labels = self.read_classes(dataset_dir)
        return [
            EvalItem(path, [(labels[c], x, y, w, h) for c, x, y, w, h in boxes], width, height)
            for path, boxes, width, height in self.read_split(dataset_dir, split)
        ]

    # -- model ---------------------------------------------------------------------------------
    @staticmethod
    def _load_pretrained(size: str, num_labels: int):
        """The COCO checkpoint with a fresh class layer for ``num_labels`` labels. Reads the verified cache only;
        never touches the network. Tests replace this with a tiny randomly initialised model."""
        from transformers import DFineForObjectDetection

        return DFineForObjectDetection.from_pretrained(
            str(checkpoint_dir(size)), num_labels=num_labels, ignore_mismatched_sizes=True, local_files_only=True
        )

    @staticmethod
    def _build_structure(config_dict: dict[str, Any]):
        """The same network without any weights, for loading a saved artifact."""
        from transformers import DFineConfig, DFineForObjectDetection

        return DFineForObjectDetection(DFineConfig.from_dict(dict(config_dict)))

    @staticmethod
    def _to_tensor(image: Image.Image, size: int):
        import numpy as np
        import torch

        resized = image.convert("RGB").resize((size, size), Image.BILINEAR)
        array = np.asarray(resized, dtype=np.float32) / 255.0
        return torch.from_numpy(array).permute(2, 0, 1).contiguous()

    def train(
        self,
        dataset_dir: Path,
        config: dict[str, Any],
        progress_cb: ProgressCallback,
        cancel_cb: CancelCallback,
    ) -> TrainResult:
        import torch

        cfg = self.validate_config(config)
        if cfg["pretrained"] == AUTO:
            raise ValueError("This run still says 'auto' for its starting model: it was never resolved. Start it again.")
        provenance = ensure_checkpoint(cfg["size"])  # fails fast, before any work; verifies hashes
        labels = self.read_classes(dataset_dir)
        samples = self.read_split(dataset_dir, "train")
        if not samples:
            raise ValueError("The train split has no images, so there is nothing to learn from.")
        if cfg["threads"]:
            torch.set_num_threads(cfg["threads"])
        torch.manual_seed(cfg["seed"])
        shuffler = random.Random(cfg["seed"])
        size = INPUT_SIZE

        model = self._load_pretrained(cfg["size"], len(labels))
        model.train()
        backbone = [p for n, p in model.named_parameters() if p.requires_grad and ".backbone." in f".{n}."]
        backbone_ids = {id(p) for p in backbone}
        head = [p for p in model.parameters() if p.requires_grad and id(p) not in backbone_ids]
        rate = cfg["learning_rate"]
        groups = [{"params": head, "lr": rate * HEAD_LR_FACTOR}]
        if backbone:
            groups.append({"params": backbone, "lr": rate * BACKBONE_LR_FACTOR})
        optimizer = torch.optim.AdamW(groups, weight_decay=WEIGHT_DECAY)

        batches_per_epoch = -(-len(samples) // cfg["batch_size"])
        total_steps = cfg["epochs"] * batches_per_epoch
        warmup = max(1, min(50, total_steps // 10))

        def factor(step: int) -> float:  # linear warm-up, then cosine down to 10%
            if step < warmup:
                return (step + 1) / warmup
            progress = (step - warmup) / max(1, total_steps - warmup)
            return 0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * progress))

        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, factor)

        def load(sample, flip: bool):
            path, boxes, width, height = sample
            with Image.open(path) as opened:
                tensor = self._to_tensor(opened, size)
            classes, centres = [], []
            for cls, x, y, w, h in boxes:
                w = max(w, MIN_BOX_PIXELS / width)
                h = max(h, MIN_BOX_PIXELS / height)
                cx, cy = min(max(x + w / 2, 0.0), 1.0), min(max(y + h / 2, 0.0), 1.0)
                if flip:
                    cx = 1.0 - cx
                classes.append(cls)
                centres.append([cx, cy, min(w, 1.0), min(h, 1.0)])
            if flip:
                tensor = tensor.flip(-1)
            return tensor, {
                "class_labels": torch.tensor(classes, dtype=torch.int64),
                "boxes": torch.tensor(centres, dtype=torch.float32).reshape(-1, 4),
            }

        step = 0
        history = []
        started = time.monotonic()
        for epoch in range(1, cfg["epochs"] + 1):
            epoch_started = time.monotonic()
            order = list(range(len(samples)))
            shuffler.shuffle(order)
            running, count = 0.0, 0
            for start in range(0, len(order), cfg["batch_size"]):
                if cancel_cb():
                    raise TrainingCancelled("Training was cancelled.")
                batch = [load(samples[i], shuffler.random() < 0.5) for i in order[start : start + cfg["batch_size"]]]
                outputs = model(pixel_values=torch.stack([b[0] for b in batch]), labels=[b[1] for b in batch])
                loss = outputs.loss
                if not torch.isfinite(loss):
                    raise ValueError("Training diverged (the loss became infinite). Lower learning_rate and try again.")
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), GRADIENT_CLIP)
                optimizer.step()
                scheduler.step()
                running += float(loss.detach())
                count += 1
                step += 1
                progress_cb(step / total_steps, f"epoch {epoch}/{cfg['epochs']} step {step}/{total_steps} loss {float(loss.detach()):.4f}")
            history.append({
                "epoch": epoch,
                "mean_loss": round(running / max(count, 1), 5),
                "seconds": round(time.monotonic() - epoch_started, 1),
            })

        model.eval()
        artifact = dataset_dir.parent / ARTIFACT_NAME
        torch.save(
            {
                "format": ARTIFACT_FORMAT,
                "labels": labels,
                "size": cfg["size"],
                "input_size": size,
                "pretrained": {k: provenance[k] for k in ("mode", "repo_id", "revision", "sha256")},
                "config": _json_safe(model.config.to_dict()),
                "state_dict": {k: v.detach().cpu() for k, v in model.state_dict().items()},
            },
            artifact,
        )
        return TrainResult(
            artifact_path=artifact,
            metrics={
                "train_images": len(samples),
                "train_boxes": sum(len(s[1]) for s in samples),
                "epochs": history,
                "train_seconds": round(time.monotonic() - started, 1),
                "pretrained": provenance,
                "size": cfg["size"],
                "input_size": size,
                "note": "Training loss only; see the held-out evaluation for quality.",
            },
            label_names=labels,
        )

    # -- inference -----------------------------------------------------------------------------
    def _load(self, artifact: Path):
        import torch

        stat = Path(artifact).stat()
        key = (str(artifact), stat.st_mtime_ns, stat.st_size)
        if self._cache and self._cache[0] == key:
            return self._cache[1], self._cache[2]
        checkpoint = torch.load(artifact, map_location="cpu", weights_only=True)
        if checkpoint.get("format") != ARTIFACT_FORMAT:
            raise ValueError("This file is not a model saved by the dfine-coco backend.")
        meta = {"labels": list(checkpoint["labels"]), "input_size": int(checkpoint["input_size"])}
        model = self._build_structure(checkpoint["config"])
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        self._cache = (key, model, meta)
        return model, meta

    def predict(self, artifact: Path, image: Image.Image, thresholds: dict[str, Any]) -> list[BoxPrediction]:
        import torch

        model, meta = self._load(artifact)
        labels = meta["labels"]
        confidence = thresholds.get("confidence", 0.5)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be a number between 0 and 1.")  # NaN fails the comparison too
        confidence = float(confidence)
        max_boxes = thresholds.get("max_boxes", 100)
        if isinstance(max_boxes, bool) or not isinstance(max_boxes, int) or not 1 <= max_boxes <= MAX_BOXES_LIMIT:
            raise ValueError(f"max_boxes must be a whole number between 1 and {MAX_BOXES_LIMIT}.")
        pixel_values = self._to_tensor(image, meta["input_size"]).unsqueeze(0)
        with torch.no_grad():
            output = model(pixel_values=pixel_values)
        # Same post-processing as transformers' RTDetrImageProcessor for focal-loss models: sigmoid over every
        # (query, class) pair, keep the best num_queries pairs, centre boxes are already normalised.
        logits, boxes = output.logits[0], output.pred_boxes[0]
        queries, classes = logits.shape
        scores = logits.sigmoid().flatten()
        top_scores, top_index = scores.topk(min(queries, scores.numel()))
        found = []
        for score, index in zip(top_scores.tolist(), top_index.tolist()):
            if not math.isfinite(score):
                continue  # a NaN score sorts unpredictably and must never become a box
            if score < confidence or len(found) >= max_boxes:
                break
            query, label = divmod(index, classes)
            if not 0 <= label < len(labels):
                continue
            cx, cy, w, h = boxes[query].tolist()
            if not all(math.isfinite(v) for v in (cx, cy, w, h)):
                continue  # a NaN or infinite box would otherwise survive the clamping below as a stray box
            x1, y1 = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
            x2, y2 = min(1.0, cx + w / 2), min(1.0, cy + h / 2)
            if x2 <= x1 or y2 <= y1:
                continue
            found.append(BoxPrediction(labels[label], x1, y1, x2 - x1, y2 - y1, float(score)))
        return found
