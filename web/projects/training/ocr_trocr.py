"""Handwriting OCR backend: fine-tunes a ready-made TrOCR reader on your transcripts.

The task: pictures of handwriting, each with the corrected text a person typed. Training
fine-tunes Microsoft's TrOCR (``microsoft/trocr-small-handwritten`` by default) on those
picture/text pairs; the held-out test pictures are scored with the character error rate (CER)
and word accuracy. This is the Athena OS port of athena-forge's ``backend/ml/ocr_trainer.py``
(the training algorithm; none of its HTTP wiring).

Dataset layout (written by the runner's ``build_generic_dataset``): ``images/<split>/...`` plus
``transcripts.json`` (``[{split, asset_id, filename, sha256, text, path}]``). The backend reads
that one file; it never touches the database or the storage layer.

The artifact is a single ``model.pt`` file (the runner stores artifacts as one file under
``<data home>/models/<uuid>/model.bin`` and hashes it), holding a torch ``state_dict`` plus a
small processor spec, so a run's model is one verifiable file, like the other backends.

Download gating: fine-tuning needs the pretrained TrOCR weights. They are fetched only when
the same administrator switch that governs every ready-made model download is on (the console's
"Ready-made model downloads", or ATHENA_TRAINING_ALLOW_DOWNLOAD=1 in the training helper's
environment - see ``torchvision_frcnn.downloads_allowed``), from huggingface.co only, at a pinned
revision (a commit SHA, never a moving branch), with every file checked against its pinned size
and SHA-256, and cached under ``<data home>/models/pretrained/trocr/``. The Teach start option
carries the licence note and needs the acknowledgement tick, exactly like D-FINE's.

Module import is torch-free; torch and transformers are imported inside the worker's calls.
"""

from __future__ import annotations

import importlib.util
import json
import math
import time
from pathlib import Path
from typing import Any

from PIL import Image

from . import torchvision_frcnn as tv
from .base import (
    Availability,
    CancelCallback,
    EvalItem,
    ProgressCallback,
    StartBlocked,
    TrainingBackend,
    TrainingCancelled,
    TrainResult,
)

# -- the one model this backend fine-tunes ------------------------------------------------------------------
DEFAULT_BASE_MODEL = "microsoft/trocr-small-handwritten"
REVISION = "b4648cfa171985a6745f37ddd637e98c0da958ac"
"""The exact commit of the repository the weights are fetched from (never a moving branch); the
file hashes it pairs with live in ``BASE_FILES`` below."""
APPROX_MB = 258
"""config.json + preprocessor_config.json + the pytorch_model.bin weights of the small
handwritten checkpoint add up to roughly this; shown before a download, so it leans high."""
BASE_MODEL_WHAT = "TrOCR small (about 62 million parameters), Microsoft's published handwriting reader"
MAX_TARGET_LENGTH = 64
GENERATION_MAX_LENGTH = 64
MIN_RECOMMENDED_PICTURES = 10
REQUIRED_MODULES = ("torch", "transformers")
HF_HOST = "https://huggingface.co"
DOWNLOAD_HOSTS = "huggingface.co and *.hf.co"

DEFAULTS = {
    "epochs": 3,
    "batch_size": 2,
    "learning_rate": 5e-5,
    "seed": 0,
    "threads": 0,
    "base_model": DEFAULT_BASE_MODEL,
}
ARTIFACT_NAME = "model.pt"
ARTIFACT_FORMAT = "athena.ocr-trocr/1"
EVALUATION_SCHEMA = "athena.ocr-evaluation/1"

LAWYER = tv.LAWYER
GENERAL_TERMS = tv.GENERAL_TERMS

LICENSE_NOTE = (
    "Code: the model code used here is Hugging Face transformers (Apache-2.0) and Athena's own training loop "
    "(this repository). Weights: the base model is microsoft/trocr-small-handwritten, whose model card "
    "(huggingface.co/microsoft/trocr-small-handwritten) declares `license: mit`; the fine-tuned model you get is "
    "derived from it and from your own pictures. It is fetched only when downloads are switched on "
    "(ATHENA_TRAINING_ALLOW_DOWNLOAD=1 or the console's Ready-made model downloads switch), at the pinned revision "
    f"{REVISION} with every file checked against a pinned SHA-256, and cached in your Athena data folder. The MIT "
    "declaration is the model repository's own licence statement, quoted as the publisher wrote it; this is an "
    "engineering note, not legal advice and not a clearance for commercial use. Models you train also derive from "
    "your own pictures; check you have the right to use those pictures. " + LAWYER
)
START_TEXT = {
    "title": "Start from a ready-made handwriting reader (TrOCR)",
    "summary": (
        "The computer starts with Microsoft's published handwriting reader (TrOCR small, MIT licence on its model "
        "card) and learns your handwriting on top. It is the only way this tool starts: reading handwriting from "
        "nothing needs far more pictures than a few dozen."
    ),
    "licence_plain": (
        f"{GENERAL_TERMS} The publisher of this model says it is under the MIT licence; that is the licence of the "
        "model file, not a statement about the handwriting it was taught from. Nobody has confirmed whether those "
        f"terms reach a model taught from them. {LAWYER}"
    ),
}
CONSTRAINTS = (
    f"Needs torch and transformers in the worker environment (pip install -r web/requirements-autolabel.txt).",
    "CPU only, like the other backends. TrOCR small fine-tunes on a handful of pictures on a laptop-class CPU, "
    "but slowly: expect a few seconds per picture per pass, more for long transcripts. A GPU is not used.",
    f"Always starts from the ready-made TrOCR handwriting reader ({DEFAULT_BASE_MODEL}); starting from nothing is "
    "not offered. The first run needs the model downloaded (about "
    f"{APPROX_MB} MB from {DOWNLOAD_HOSTS}), which an administrator must allow.",
    f"Give it real handwriting: {MIN_RECOMMENDED_PICTURES}+ transcribed pictures before the numbers mean anything, "
    "and more is better. One clear line of handwriting per picture works best; full pages do not.",
    "The measure is the character error rate (how many characters of every 100 are wrong), not the box-detection "
    "accuracy the picture tools report; the numbers are not comparable.",
    "Not run on real handwriting at scale yet: the only end-to-end measurements are on a handful of pictures, so "
    "the time figures are estimates, not promises.",
)


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[names.__len__() - 1]


# -- the character error rate --------------------------------------------------------------------------------
def compute_cer(reference: str, hypothesis: str) -> float:
    """Character error rate in [0, 1]: edit distance over the reference's length.

    Empty reference with empty hypothesis is 0; an empty reference against anything is 1
    (nothing can be said, so it is counted as fully wrong rather than dividing by zero).
    Ported unchanged from athena-forge's ``backend/ml/ocr_trainer.py``.
    """
    ref = list(reference or "")
    hyp = list(hypothesis or "")
    if not ref and not hyp:
        return 0.0
    if not ref:
        return 1.0
    rows, cols = len(ref) + 1, len(hyp) + 1
    dp = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        dp[i][0] = i
    for j in range(cols):
        dp[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i - 1][j - 1] + cost, dp[i][j - 1] + 1)
    return dp[-1][-1] / float(len(ref))


def word_accuracy(reference: str, hypothesis: str) -> float:
    """The share of the reference's words that appear in the hypothesis, position-insensitive.

    A simple, honest word-level number next to the CER: it counts each reference word found
    anywhere in the hypothesis once (multiset intersection). 1.0 means every word was read
    (possibly in another order or with extra words around it)."""
    from collections import Counter

    wanted = Counter((reference or "").split())
    got = Counter((hypothesis or "").split())
    matched = sum((wanted & got).values())
    total = sum(wanted.values())
    return matched / total if total else 0.0


def plain_language_from_cer(cer: float, words_correct: float | None = None) -> str:
    """Everyday wording for the evaluation report - no CER jargon."""
    wrong = max(0.0, min(100.0, round(cer * 100)))
    right = 100 - wrong
    sentence = f"Out of every 100 characters of real handwriting, about {wrong:.0f} came out wrong (about {right:.0f} right)."
    if wrong == 0:
        sentence = "Every character of the held-out handwriting came out right."
    if words_correct is not None:
        share = max(0.0, min(100.0, round(words_correct * 100)))
        sentence += f" About {share:.0f} in 100 of the words were read correctly."
    return sentence


# -- the base model: pinned, verified, downloaded only when allowed -------------------------------------------
PINNED_HOSTS = tv.AllowedHosts(("huggingface.co",), (".hf.co",), DOWNLOAD_HOSTS)
BASE_FILES: dict[str, dict[str, Any]] = {
    "config.json": {"sha256": "1e12191191977ca7b70d39b8aa7ab10080c751d4", "bytes": 4211},
    "preprocessor_config.json": {"sha256": "8acdb9d2d64bc6a881a04d3682f2a696bce75492", "bytes": 272},
    "pytorch_model.bin": {
        "sha256": "1b83102cbc1520dee1c3937ac334da83da18cf4683d46ebd1bd4e93ebe584dd7",
        "bytes": 245_933_041,
    },
}
"""Verified 2026-11-19 from https://huggingface.co/api/models/microsoft/trocr-small-handwritten (the
``main`` revision's commit ``sha`` and file list) and its ``/tree/main`` listing: ``pytorch_model.bin``
is an LFS file whose ``oid`` is its SHA-256 and whose ``size`` is 245,933,041; the repository has NO
``model.safetensors`` (a ``/resolve/main/model.safetensors`` request returns 404). The JSON files'
SHA-256 values are of the bytes served at the pinned revision (the resolve URLs' ETags quoted the
same digests). These are the only files the backend downloads; ``from_pretrained`` on the cache
folder reads them without further network access."""
_VERIFIED: dict[tuple[str, int, int, str], bool] = {}


def base_model_dir() -> Path:
    return tv.pretrained_dir() / "trocr" / DEFAULT_BASE_MODEL.replace("/", "__")


def file_url(name: str) -> str:
    return f"{HF_HOST}/{DEFAULT_BASE_MODEL}/resolve/{REVISION}/{name}"


def _sha256(path: Path) -> str:
    return tv._sha256(path)  # noqa: SLF001 - the one hashing helper, shared on purpose


def _file_ok(path: Path, expected: dict[str, Any]) -> bool:
    try:
        return path.is_file() and path.stat().st_size == expected["bytes"] and _sha256(path) == expected["sha256"]
    except OSError:
        return False


def _files_ok(folder: Path) -> bool:
    for name, expected in BASE_FILES.items():
        if not _file_ok(folder / name, expected):
            return False
    return True


def cached_base_model_ok() -> bool:
    """True when every pinned file is in the cache and matches its pinned SHA-256 (memoised per stat)."""
    folder = base_model_dir()
    for name, expected in BASE_FILES.items():
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


def _blocked_message() -> str:
    """Why nothing could be fetched, and how to change it - the same rule as D-FINE's, but in this
    backend's own words: it names the TrOCR checkpoint files and huggingface.co, not the vision
    helper's torchvision checkpoints."""
    how = tv.download_blocked_message(tv.steps_source("environment"))
    files = _join(sorted(BASE_FILES))
    return (
        f"This run needs the ready-made handwriting reader ({BASE_MODEL_WHAT}, about {APPROX_MB} MB), but "
        f"downloading is switched off on this computer. {how} Then run it again - the {files} files are fetched once "
        f"from {DOWNLOAD_HOSTS} at the pinned revision, checked against pinned checksums, and kept in your Athena "
        "data folder under models/pretrained/trocr. Starting from nothing is not offered for this tool. Read the "
        "licence note first: the ready-made reader comes with its own terms."
    )


def _fetch_file(name: str, destination: Path) -> None:
    """Download one base-model file through the restricted opener and check its pinned SHA-256.

    The D-FINE backend's fetch, with the same cut-off-one-byte-past-the-pinned-size rule and the
    same fresh, never-followed ``.part`` file. Tests replace this."""
    import hashlib
    import os
    import urllib.error

    expected = BASE_FILES[name]
    partial = destination.with_name(destination.name + ".part")
    try:
        try:
            response = tv.open_pinned(file_url(name), PINNED_HOSTS, timeout=tv.DOWNLOAD_TIMEOUT_SECONDS)
        except urllib.error.URLError as error:
            raise ValueError(
                f"Could not download the handwriting reader's {name} ({getattr(error, 'reason', error)}). Check "
                "this computer's internet access and try again."
            ) from None
        with response:
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                partial.unlink(missing_ok=True)  # never reuse or follow something that was already there
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                out = os.fdopen(os.open(partial, flags, 0o644), "wb")
            except OSError as error:
                raise ValueError(
                    f"Could not save the handwriting reader on this computer ({error.__class__.__name__})."
                ) from None
            with out:
                digest = hashlib.sha256()
                total = 0
                while True:
                    chunk = response.read(min(1 << 20, expected["bytes"] + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > expected["bytes"]:
                        raise ValueError(
                            f"The download of {name} was larger than expected ({expected['bytes']:,} bytes), so it "
                            "was stopped and discarded."
                        )
                    digest.update(chunk)
                    out.write(chunk)
        if digest.hexdigest() != expected["sha256"]:
            raise ValueError(
                f"The downloaded {name} did not match its pinned checksum, so it was discarded."
            )
        partial.replace(destination)
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_base_model() -> dict[str, Any]:
    """Make sure the pinned TrOCR base model is cached; returns provenance for the run record.

    Nothing is fetched unless downloads are switched on (the same switch as every backend). Files
    that are already cached and match their pinned SHA-256 are used without it. A stale or corrupt
    file is replaced."""
    folder = base_model_dir()
    source = "cache"
    for name in BASE_FILES:
        target = folder / name
        if _file_ok(target, BASE_FILES[name]):
            continue
        if not tv.downloads_allowed():
            raise ValueError(_blocked_message())
        try:
            target.unlink(missing_ok=True)  # a stale or corrupt file must not survive
        except OSError as error:
            if target.is_dir() and not target.is_symlink():
                raise ValueError(
                    f"Athena cannot save the model file {name}: a folder with that name is in its place under "
                    "models/pretrained/trocr in the Athena data folder. Remove or rename it and run it again."
                ) from None
            raise ValueError(f"Could not save the handwriting reader on this computer ({error.__class__.__name__}).") from None
        _fetch_file(name, target)
        source = "download"
    weights = BASE_FILES["pytorch_model.bin"]
    return {
        "repo_id": DEFAULT_BASE_MODEL,
        "revision": REVISION,
        "what": BASE_MODEL_WHAT,
        "source_url": file_url("pytorch_model.bin"),
        "sha256": weights["sha256"],
        "bytes": weights["bytes"],
        "files": {name: expected["sha256"] for name, expected in BASE_FILES.items()},
        "licence_declared": "mit (model card)",
        "from": source,
    }


def downloads_for(worker: dict[str, Any]) -> tuple[bool, str]:
    """(allowed, who said so): exactly the torchvision backend's rule, so the console switch is inherited."""
    return tv.downloads_for(worker)


# -- the backend ----------------------------------------------------------------------------------------------
class OcrTrOCRBackend(TrainingBackend):
    name = "ocr-trocr"
    description = (
        "Handwriting reader (TrOCR small, fine-tuned on your corrected transcripts). Always starts from "
        "Microsoft's published handwriting reader (MIT licence on its model card); scored by character error rate."
    )
    license_note = LICENSE_NOTE
    constraints = CONSTRAINTS

    def __init__(self) -> None:
        self._cache: tuple[tuple[str, int, int], Any, dict[str, Any]] | None = None

    # -- availability / config -----------------------------------------------------------------------------
    def check_available(self) -> Availability:
        missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
        if not missing:
            return Availability(True, "torch and transformers are installed.")
        return Availability(
            False,
            f"{_join(missing)} {'is' if len(missing) == 1 else 'are'} not installed in this environment.",
            "Install the worker requirements (pip install -r web/requirements-autolabel.txt) in the "
            "environment that runs `manage.py train_worker`, then start the worker.",
        )

    def default_config(self) -> dict[str, Any]:
        return dict(DEFAULTS)

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        unknown = set(config) - set(DEFAULTS)
        if unknown:
            if unknown & {"checkpoint", "repo_id", "model", "revision", "weights"}:
                raise ValueError(
                    "The base model cannot be chosen by name. This tool uses one handwriting reader "
                    f"({DEFAULT_BASE_MODEL}); that is deliberate."
                )
            raise ValueError(f"Unsupported training setting: {', '.join(sorted(map(str, unknown)))}.")
        rate = config.get("learning_rate", DEFAULTS["learning_rate"])
        try:  # an integer too large for a float (10**400) raises OverflowError, which must not reach the caller
            rate_ok = not isinstance(rate, bool) and isinstance(rate, (int, float)) and 0 < float(rate) <= 1
        except (OverflowError, ValueError, TypeError):
            rate_ok = False
        if not rate_ok:
            raise ValueError("learning_rate must be a number above 0 and at most 1.")
        base = config.get("base_model", DEFAULTS["base_model"])
        if not isinstance(base, str) or base != DEFAULT_BASE_MODEL:
            raise ValueError(
                f"base_model must be {DEFAULT_BASE_MODEL!r}; this tool fine-tunes that one handwriting reader."
            )
        normalized = {
            "base_model": base,
            "epochs": self._int(config, "epochs", 1, 50),
            "batch_size": self._int(config, "batch_size", 1, 8),
            "learning_rate": float(rate),
            "seed": self._int(config, "seed", 0, 2**31 - 1),
            "threads": self._int(config, "threads", 0, 64),
        }
        return normalized

    @staticmethod
    def _int(config: dict[str, Any], key: str, low: int, high: int) -> int:
        value = config.get(key, DEFAULTS[key])
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} must be a whole number between {low} and {high}.")
        return value

    # -- the download-gated start (the Teach panel) ---------------------------------------------------------
    def _unavailable(self, worker: dict[str, Any]) -> tuple[str, str]:
        if cached_base_model_ok():
            return "", ""
        allowed, who = downloads_for(worker)
        if allowed:
            return "", ""
        reason = (
            f"Downloading ready-made models is switched off on this computer, and the handwriting reader "
            f"(about {APPROX_MB} MB) has not been downloaded before, so it cannot be used. Only an administrator "
            "can change this."
        )
        if who == "console" and not worker.get("online"):
            reason += (
                " The teaching helper is not running, so this is the setting of the console itself; the helper must "
                "have the same setting."
            )
        return reason, tv.ENABLE_STEPS

    def start_options(self, worker: dict[str, Any]) -> list[dict[str, Any]]:
        reason, how = self._unavailable(worker)
        ready = cached_base_model_ok()
        return [{
            "value": "trocr", "title": START_TEXT["title"], "summary": START_TEXT["summary"],
            "licence_plain": START_TEXT["licence_plain"], "available": not reason, "ready": ready,
            "needs_acknowledgement": True, "unavailable_reason": reason, "how_to_enable": how,
            "size_note": (
                f"Already on this computer (about {APPROX_MB} MB, verified). Nothing will be downloaded."
                if ready else
                f"About {APPROX_MB} MB, downloaded once from {DOWNLOAD_HOSTS} and then kept in Athena's data folder."
            ),
        }]

    def start_check(self, config: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
        reason, how = self._unavailable(worker)
        return {"needs_acknowledgement": True, "blocked": f"{reason} {how}".strip()}

    def resolve_start(self, worker: dict[str, Any]) -> dict[str, Any]:
        options = self.start_options(worker)
        usable = [option for option in options if option["available"]]
        if usable:
            return {"resolved": usable[0]["value"], "available": usable, "reason_if_none": "", "how_to_enable": ""}
        return {
            "resolved": None, "available": [],
            "reason_if_none": (
                "The handwriting reader cannot be used on this computer right now: it has not been downloaded "
                "before, and downloading is switched off. Starting from nothing is not offered. Only an "
                "administrator can fix this."
            ),
            "how_to_enable": tv.AUTO_NONE_STEPS,
        }

    def prepare_config(self, config: dict[str, Any], *, train_pictures: int, worker: dict[str, Any]) -> dict[str, Any]:
        prepared = dict(config)
        # The one-click start's shared default is ``pretrained: "auto"`` (best ready-made start that can
        # run); this backend spells it ``base_model``, so translate it and never store the foreign key.
        if prepared.get("pretrained") == "auto":
            prepared.pop("pretrained")
            prepared.setdefault("base_model", "auto")
        if prepared.get("base_model") in (None, "", "auto"):
            # The Teach panel sends "auto" (its radio names the one start this tool has); resolve
            # it to the pinned reader, or block in plain words when downloads are off.
            plan = self.resolve_start(worker)
            if plan["resolved"] is None:
                raise StartBlocked(f"{plan['reason_if_none']} {plan['how_to_enable']}", start_from=plan)
            prepared["base_model"] = DEFAULT_BASE_MODEL
        return prepared

    def runtime_info(self) -> dict[str, Any]:
        import torch
        import transformers

        return {
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "device": "cpu",
            "threads": torch.get_num_threads(),
        }

    # -- dataset (the runner's generic export: images/<split>/... + transcripts.json) ------------------------
    @staticmethod
    def read_transcripts(dataset_dir: Path) -> list[dict[str, Any]]:
        data = json.loads((Path(dataset_dir) / "transcripts.json").read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("transcripts.json is not the list this backend expected. Create a new dataset version.")
        return data

    def build_dataset(self, run, dest: Path) -> tuple[list[EvalItem], dict[str, Any]]:
        from .runner import build_generic_dataset

        return build_generic_dataset(run, dest)

    # -- training ---------------------------------------------------------------------------------------------
    def train(
        self,
        dataset_dir: Path,
        config: dict[str, Any],
        progress_cb: ProgressCallback,
        cancel_cb: CancelCallback,
    ) -> TrainResult:
        import torch

        cfg = self.validate_config(config)
        if cfg["base_model"] == "auto":
            raise ValueError("This run still says 'auto' for its starting model: it was never resolved. Start it again.")
        provenance = ensure_base_model()  # fails fast, before any work, when downloads are off
        entries = self.read_transcripts(dataset_dir)
        train_entries = [entry for entry in entries if entry["split"] == "train" and entry["text"]]
        if not train_entries:
            raise ValueError(
                "The train split has no pictures with a typed transcript, so there is nothing to learn from."
            )
        if cfg["threads"]:
            torch.set_num_threads(cfg["threads"])
        torch.manual_seed(cfg["seed"])

        progress_cb(0.05, f"Loading the handwriting reader ({DEFAULT_BASE_MODEL})")
        processor, model = self._load_base_model()

        dataset = _LineDataset(train_entries, dataset_dir, processor)
        loader = torch.utils.data.DataLoader(
            dataset, batch_size=cfg["batch_size"], shuffle=False, collate_fn=_collate
        )
        optimizer = torch.optim.AdamW(model.parameters(), lr=cfg["learning_rate"])
        total_steps = cfg["epochs"] * max(1, len(loader))
        step = 0
        history = []
        started = time.monotonic()
        for epoch in range(1, cfg["epochs"] + 1):
            epoch_started = time.monotonic()
            running, count = 0.0, 0
            for batch in loader:
                if cancel_cb():
                    raise TrainingCancelled("Training was cancelled.")
                optimizer.zero_grad()
                outputs = model(pixel_values=batch["pixel_values"], labels=batch["labels"])
                loss = outputs.loss
                if not torch.isfinite(loss):
                    raise ValueError("Training diverged (the loss became infinite). Lower learning_rate and try again.")
                loss.backward()
                optimizer.step()
                running += float(loss.detach())
                count += 1
                step += 1
                progress_cb(step / total_steps, f"epoch {epoch}/{cfg['epochs']} step {step}/{total_steps} loss {float(loss.detach()):.4f}")
            history.append({
                "epoch": epoch,
                "mean_loss": round(running / max(count, 1), 5),
                "seconds": round(time.monotonic() - epoch_started, 1),
            })

        progress_cb(0.95, "Saving the fine-tuned reader")
        artifact = Path(dataset_dir).parent / ARTIFACT_NAME
        torch.save(
            {
                "format": ARTIFACT_FORMAT,
                "base_model": cfg["base_model"],
                "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
                "config": json.loads(model.config.to_json_string()),
                "decoder_config": json.loads(model.decoder.config.to_json_string()),
                "encoder_config": json.loads(model.encoder.config.to_json_string()),
                "processor_settings": _processor_settings(processor),
            },
            artifact,
        )
        return TrainResult(
            artifact_path=artifact,
            metrics={
                "train_pictures": len(train_entries),
                "epochs": history,
                "train_seconds": round(time.monotonic() - started, 1),
                "pretrained": provenance,
                "note": "Training loss only; see the held-out evaluation for quality.",
            },
            label_names=["transcript"],
        )

    @staticmethod
    def _load_base_model():
        """The pinned checkpoint from the verified cache only; never touches the network."""
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel

        folder = str(base_model_dir())
        processor = TrOCRProcessor.from_pretrained(folder)
        model = VisionEncoderDecoderModel.from_pretrained(folder)
        model.config.decoder_start_token_id = processor.tokenizer.cls_token_id
        model.config.pad_token_id = processor.tokenizer.pad_token_id
        model.config.vocab_size = model.config.decoder.vocab_size
        return processor, model

    # -- inference ---------------------------------------------------------------------------------------------
    def _load(self, artifact: Path):
        import torch

        stat = Path(artifact).stat()
        key = (str(artifact), stat.st_mtime_ns, stat.st_size)
        if self._cache and self._cache[0] == key:
            return self._cache[1], self._cache[2]
        checkpoint = torch.load(artifact, map_location="cpu", weights_only=True)
        if checkpoint.get("format") != ARTIFACT_FORMAT:
            raise ValueError("This file is not a model saved by the ocr-trocr backend.")
        processor, model = self._rebuild(checkpoint)
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        meta = {"base_model": checkpoint["base_model"]}
        self._cache = (key, (processor, model), meta)
        return (processor, model), meta

    @staticmethod
    def _rebuild(checkpoint: dict[str, Any]):
        from transformers import (
            PretrainedConfig,
            TrOCRConfig,
            TrOCRProcessor,
            VisionEncoderDecoderConfig,
            VisionEncoderDecoderModel,
        )

        config = VisionEncoderDecoderConfig.from_dict(
            {
                "encoder": checkpoint["encoder_config"],
                "decoder": checkpoint["decoder_config"],
                **{key: value for key, value in checkpoint["config"].items() if key not in ("encoder", "decoder")},
            }
        )
        model = VisionEncoderDecoderModel(config)
        settings = checkpoint.get("processor_settings") or {}
        tokenizer = TrOCRConfig.from_dict(checkpoint["decoder_config"])  # vocab lives on the decoder config
        image_processor = _RebuiltImageProcessor(settings)
        processor = TrOCRProcessor(image_processor=image_processor, tokenizer=_RebuiltTokenizer(tokenizer, settings))
        return processor, model

    def predict_text(self, artifact: Path, image: Image.Image) -> str:
        """Read one picture: the transcript this model produces, as plain text (used by ``evaluate``)."""
        import torch

        (processor, model), _meta = self._load(artifact)
        pixel_values = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values
        with torch.no_grad():
            generated = model.generate(pixel_values, max_length=GENERATION_MAX_LENGTH)
        text = processor.batch_decode(generated, skip_special_tokens=True)[0]
        return (text or "").strip()

    def predict_transcript(self, artifact: Path, image: Image.Image) -> dict[str, Any]:
        """Read one picture: the serving contract for a text-output model.

        Returns ``{"text": str, "confidence": None}``. The reader generates its answer token by
        token, so it has no confidence number to give; the field is an honest ``None`` rather
        than a made-up score. The inference worker calls this for screens whose output is text."""
        return {"text": self.predict_text(artifact, image), "confidence": None}

    def predict(self, artifact: Path, image: Image.Image, thresholds: dict[str, Any]) -> list:
        # A transcript model has no boxes; the inference pipeline calls ``predict_transcript`` for
        # text-output screens. This stays an honest refusal at the box-prediction level.
        raise NotImplementedError("This model reads text; it does not find boxes. Use predict_transcript.")

    def evaluate(self, artifact: Path, items: list[EvalItem], labels: list[str], thresholds: dict[str, Any]) -> dict[str, Any]:
        """Held-out evaluation: CER and word accuracy per test picture plus the aggregate.

        The transcripts come from the export next to the artifact (the runner calls ``evaluate``
        with the artifact still in the scratch work dir, whose sibling ``dataset/`` holds the
        export). Each item's ``path`` names the same file the export wrote, so item and
        transcript pair up without any database access."""
        manifest = Path(artifact).parent / "dataset" / "transcripts.json"
        if not manifest.is_file():
            manifest = Path(artifact).parent / "transcripts.json"
        entries = self.read_transcripts(manifest.parent)
        test = {str(item.path) for item in items}
        details = []
        cers = []
        words = []
        for entry in entries:
            if entry["split"] != "test":
                continue
            path = (manifest.parent / entry["path"]).resolve()
            if str(path) not in test and str(path) not in {str(Path(p).resolve()) for p in test}:
                continue
            with Image.open(path) as opened:
                hypothesis = self.predict_text(artifact, opened)
            cer = compute_cer(entry["text"], hypothesis)
            accuracy = word_accuracy(entry["text"], hypothesis)
            cers.append(cer)
            words.append(accuracy)
            details.append(
                {
                    "asset_id": entry["asset_id"],
                    "filename": entry["filename"],
                    "cer": round(cer, 4),
                    "word_accuracy": round(accuracy, 4),
                    "characters": len(entry["text"]),
                    "reference": entry["text"],
                    "hypothesis": hypothesis,
                }
            )
        mean_cer = sum(cers) / len(cers) if cers else 1.0
        mean_words = sum(words) / len(words) if words else 0.0
        return {
            "schema": EVALUATION_SCHEMA,
            "available": bool(details),
            "reason": "" if details else "The dataset version's test split has no pictures, so there is nothing held out to measure against.",
            "split": "test",
            "task": "handwriting-ocr",
            "counts": {"test_pictures": len(details)},
            "cer": round(mean_cer, 4),
            "word_accuracy": round(mean_words, 4),
            "plain_language": plain_language_from_cer(mean_cer, mean_words if details else None),
            "per_picture": details,
            "limits": [],
        }


# -- helpers the training loop needs ----------------------------------------------------------------------------
def _collate(batch: list[dict[str, Any]]) -> dict[str, Any]:
    import torch

    return {
        "pixel_values": torch.stack([item["pixel_values"] for item in batch]),
        "labels": torch.stack([item["labels"] for item in batch]),
    }


class _LineDataset:
    """One picture + its typed text, exactly as ``_LineDataset`` in athena-forge's trainer."""

    def __init__(self, entries: list[dict[str, Any]], dataset_dir: Path, processor) -> None:
        self.entries = entries
        self.root = Path(dataset_dir)
        self.processor = processor

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, index: int) -> dict[str, Any]:
        import torch

        entry = self.entries[index]
        with Image.open(self.root / entry["path"]) as image:
            pixel_values = self.processor(images=image.convert("RGB"), return_tensors="pt").pixel_values[0]
        labels = self.processor.tokenizer(
            entry["text"],
            padding="max_length",
            max_length=MAX_TARGET_LENGTH,
            truncation=True,
            return_tensors="pt",
        ).input_ids[0]
        labels[labels == self.processor.tokenizer.pad_token_id] = -100
        return {"pixel_values": pixel_values, "labels": labels}


# -- rebuilt processor pieces (the artifact carries their settings, not pickle) ----------------------------------
class _RebuiltImageProcessor:
    """The few settings ``processor(images=...)`` needs, replayed from the artifact.

    TrOCR's image processor resizes to 384x384 and normalizes with the values its config
    records; both are stable for this model family, so the artifact carries them explicitly."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.size = {"height": int(settings.get("height", 384)), "width": int(settings.get("width", 384))}
        self.image_mean = list(settings.get("image_mean", [0.5, 0.5, 0.5]))
        self.image_std = list(settings.get("image_std", [0.5, 0.5, 0.5]))
        self.do_rescale = True

    def __call__(self, images=None, return_tensors=None, **kwargs):
        import numpy as np
        import torch

        if not isinstance(images, list):
            images = [images]
        tensors = []
        for image in images:
            resized = image.convert("RGB").resize((self.size["width"], self.size["height"]), Image.BILINEAR)
            array = np.asarray(resized, dtype=np.float32) / 255.0
            array = (array - np.asarray(self.image_mean, dtype=np.float32)) / np.asarray(self.image_std, dtype=np.float32)
            tensors.append(torch.from_numpy(array.transpose(2, 0, 1)).contiguous())
        pixel_values = torch.stack(tensors)
        from types import SimpleNamespace

        return SimpleNamespace(pixel_values=pixel_values)


class _RebuiltTokenizer:
    """The tokenizer half of the rebuilt processor: decode from the decoder config's vocab map."""

    def __init__(self, config, settings: dict[str, Any]) -> None:
        self.config = config
        self.pad_token_id = int(settings.get("pad_token_id", 1))
        self.cls_token_id = int(settings.get("cls_token_id", 0))

    def batch_decode(self, sequences, skip_special_tokens: bool = True) -> list[str]:
        vocab = getattr(self.config, "vocab", None) or {}
        lines = []
        for sequence in sequences:
            ids = sequence.tolist() if hasattr(sequence, "tolist") else list(sequence)
            tokens = [vocab.get(int(token), "") for token in ids]
            text = "".join(tokens)
            if skip_special_tokens:
                text = text.replace("<pad>", "").replace("<s>", "").replace("</s>", "").strip()
            lines.append(text)
        return lines


def _processor_settings(processor) -> dict[str, Any]:
    """The settings the rebuilt processor replays, read from the live one at save time."""
    image = getattr(processor, "image_processor", None)
    tokenizer = getattr(processor, "tokenizer", None)
    return {
        "height": 384,
        "width": 384,
        "image_mean": list(getattr(image, "image_mean", [0.5, 0.5, 0.5]) or [0.5, 0.5, 0.5]),
        "image_std": list(getattr(image, "image_std", [0.5, 0.5, 0.5]) or [0.5, 0.5, 0.5]),
        "pad_token_id": getattr(tokenizer, "pad_token_id", 1),
        "cls_token_id": getattr(tokenizer, "cls_token_id", 0),
    }
