"""Reference backend: torchvision Faster R-CNN (MobileNetV3-Large, 320px FPN) on CPU.

Licence: torchvision and PyTorch are BSD-3-Clause. By default (``pretrained = "none"``) the network
is built with ``weights=None, weights_backbone=None`` so **no pretrained weights are downloaded or
shipped**; the model starts from random initialisation. That keeps licensing and offline use simple,
but a model trained from scratch on a few hundred images will usually perform poorly.

Opt-in: ``pretrained = "imagenet_backbone"`` (MobileNetV3-Large ImageNet-1K backbone, ~21 MB) or
``"coco"`` (whole torchvision COCO detector, ~74 MB, only the final class layer is re-learned).
These are fetched from download.pytorch.org **only** when downloads are switched on - by an administrator in the
console (stored setting, see ``projects.console_settings``; it wins when present) or, until someone chooses there,
by ``ATHENA_TRAINING_ALLOW_DOWNLOAD=1`` in the worker's environment; otherwise the run fails with a
plain message. The helper reads the setting itself each time a run starts; the web process never downloads. Files are cached under ``<data home>/models/pretrained/checkpoints``. The weights'
training-data terms are not BSD; see ``license_note``.
Read the evaluation report before believing any number.

``pretrained = "auto"`` (the one-click Teach start) picks ``coco``, else ``imagenet_backbone``, whichever can run right
now, and refuses (HTTP 409) rather than ever falling back to ``"none"``; the run stores the resolved value.
``derive_settings`` works out epochs and batch size from the number of training pictures.

Module import is torch-free; torch is imported inside the methods the worker calls.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

from PIL import Image

from .base import (
    Availability,
    BoxPrediction,
    CancelCallback,
    ProgressCallback,
    StartBlocked,
    TrainingBackend,
    TrainingCancelled,
    TrainResult,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIN_BOX_PIXELS = 1.0
DEFAULTS = {"epochs": 5, "batch_size": 4, "learning_rate": 0.005, "seed": 0, "threads": 0, "pretrained": "none"}
PRETRAINED_MODES = ("none", "imagenet_backbone", "coco")
AUTO = "auto"
"""``pretrained = "auto"`` is a *request*: the web process turns it into one of the ready-made starts below when the
run is created, and the run stores the resolved value. A stored run never carries "auto" in ``pretrained``."""
PRETRAINED_CHOICES = PRETRAINED_MODES + (AUTO,)
AUTO_PREFERENCE = ("coco", "imagenet_backbone")
"""Best first. Benchmark (docs/guides/model-quality.md, synthetic shapes, 8 epochs): COCO recall 0.986 and 0.965 in two
runs, ImageNet backbone 0.889 and 0.782. ``"none"`` is deliberately absent: from scratch it found 0 of 144 test boxes,
so "auto" never falls back to it."""
EXTRA_KEYS = ("quality", "pretrained_requested", "derived")
"""Optional config keys beyond DEFAULTS. ``quality`` is the caller's; ``pretrained_requested`` and ``derived`` are
written by ``prepare_config`` only (a caller who sends them is refused) and are kept so the record says which values
Athena picked."""
DERIVED_KEYS = ("epochs", "batch_size")  # ``image_size`` is not derived: this backend has a fixed 320 px network
QUALITIES = ("quick", "better")
DEFAULT_QUALITY = "quick"

# -- derive_settings ------------------------------------------------------------------------------------
BATCH_SIZE = DEFAULTS["batch_size"]
"""The size the benchmark used; a batch is never larger than the training set."""
MIN_EPOCHS = 3
"""Even a big set gets a few passes so the new class layer is not learned from one look at each picture."""
FLOOR_EPOCHS = 2
"""The time cap may cut a huge set down to this many passes but not below (500+ optimizer steps are plenty there)."""
QUALITY_PLANS = {
    # target_steps: optimizer steps we want to have taken. Few pictures per epoch means few steps per epoch, so a small
    #   set needs many epochs to take enough of them. The benchmark's 8 epochs x 50 steps = 400 steps reached recall
    #   0.986 with a COCO start, so "better" aims at 400 and "quick" at 240 (60%).
    # max_epochs: stops a tiny set repeating itself hundreds of times (it memorises instead of learning).
    # budget: cap on pictures x epochs, which is what CPU time is proportional to (about 0.21 s per picture-epoch with
    #   a COCO start on an M3 CPU): 1600 is the benchmark's own 200 pictures x 8 epochs, about 6 minutes; "better"
    #   allows 3x that, about 17 minutes with a COCO start (about 27 with the slower ImageNet start).
    "quick": {"target_steps": 240, "max_epochs": 40, "budget": 1600},
    "better": {"target_steps": 400, "max_epochs": 80, "budget": 4800},
}

# -- time estimate --------------------------------------------------------------------------------------
SECONDS_PER_PICTURE_EPOCH = {"coco": 0.21, "imagenet_backbone": 0.34, "none": 0.54}
"""Training seconds per (training picture x epoch) on a CPU like the benchmark's (Apple M3, 4 torch threads, CPU only).
From docs/guides/model-quality.md, 200 pictures x 8 epochs = 1600 picture-epochs: COCO 330.7 s and 326.1 s (0.207 and
0.204); ImageNet backbone 444.3 s and 541.8 s (0.278 and 0.339); from scratch 859.6 s (0.537). Each constant is the
slower of the measured runs, rounded up, so the estimate leans long. An estimate for that kind of machine, not a promise:
a slower CPU, other programs running, a bigger picture size or waiting in the queue will all make it longer."""
ESTIMATE_NOTE = (
    "This is an estimate, not a promise. It is worked out from timing on one laptop-class processor (an Apple M3) "
    "while other programs were running, and covers the teaching itself, not time spent waiting in the queue. A slower "
    "computer, or other programs running, will take longer. Checking the held-back pictures afterwards took about 5 to "
    "10 seconds there."
)


def derive_settings(n_train_pictures: int, quality: str, batch_size: int | None = None) -> dict[str, int]:
    """Pick ``epochs`` and ``batch_size`` from the number of training pictures and a ``quality`` choice.

    Pure and deterministic. ``batch_size`` is the caller's explicit choice, if any (a whole number from 1 to 16; it
    wins over the default ``BATCH_SIZE``). Rules, in this order:

    1. ``batch_size`` = the explicit one, else ``BATCH_SIZE``, but never more than the pictures there are.
    2. ``epochs`` = enough passes to take the plan's ``target_steps`` optimizer steps (a step is one batch), kept within
       ``MIN_EPOCHS``..``max_epochs``. Small sets need many passes; big sets already take many steps per pass.
    3. Time cap: ``epochs * pictures`` may not exceed the plan's ``budget`` (CPU time is proportional to it), except
       that ``FLOOR_EPOCHS`` passes are always allowed.
    """
    if isinstance(n_train_pictures, bool) or not isinstance(n_train_pictures, int) or n_train_pictures < 1:
        raise ValueError("There must be at least one training picture to work out the settings.")
    if not isinstance(quality, str) or quality not in QUALITIES:
        raise ValueError("quality must be one of: " + ", ".join(f"'{q}'" for q in QUALITIES) + ".")
    plan = QUALITY_PLANS[quality]
    batch = min(BATCH_SIZE if batch_size is None else batch_size, n_train_pictures)
    steps_per_epoch = -(-n_train_pictures // batch)
    epochs = -(-plan["target_steps"] // steps_per_epoch)
    epochs = max(MIN_EPOCHS, min(epochs, plan["max_epochs"]))
    epochs = min(epochs, max(FLOOR_EPOCHS, plan["budget"] // n_train_pictures))
    return {"epochs": epochs, "batch_size": batch}


def estimate_minutes(n_train_pictures: int, epochs: int, start: str | None) -> int | None:
    """Whole minutes (rounded up, at least 1) for the teaching itself, or ``None`` when no start is known."""
    per = SECONDS_PER_PICTURE_EPOCH.get(start or "")
    if per is None:
        return None
    return max(1, math.ceil(n_train_pictures * epochs * per / 60))
DOWNLOAD_ENV = "ATHENA_TRAINING_ALLOW_DOWNLOAD"
DOWNLOAD_TIMEOUT_SECONDS = 60
# Published torchvision 0.24.1 weights (a test checks these match the installed package's enums).
# hash_prefix is the first 8 hex digits of the file's sha256, as torchvision itself uses.
PRETRAINED_FILES = {
    "imagenet_backbone": {
        "enum": "MobileNet_V3_Large_Weights.IMAGENET1K_V1",
        "url": "https://download.pytorch.org/models/mobilenet_v3_large-8738ca79.pth",
        "hash_prefix": "8738ca79",
        "approx_mb": 21,
        "bytes": 22_139_423,  # size on disk; a download is cut off one byte past it (see MAX_DOWNLOAD_BYTES)
        "what": "MobileNetV3-Large backbone trained on ImageNet-1K",
    },
    "coco": {
        "enum": "FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1",
        "url": "https://download.pytorch.org/models/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth",
        "hash_prefix": "907ea3f9",
        "approx_mb": 74,
        "bytes": 77_844_807,
        "what": "Faster R-CNN MobileNetV3-Large 320 FPN trained on COCO",
    },
}
ARTIFACT_NAME = "model.pt"
ARTIFACT_FORMAT = "athena.torchvision-fasterrcnn/1"


CONSOLE_OFF_MESSAGE = (
    "Downloads were switched off in the console (Access > Ready-made model downloads); the environment setting is "
    "ignored while a console choice exists. Switch it on there."
)
CONSOLE_WINS = "once an administrator makes a choice in the console, that choice wins"


def download_blocked_message(source: str) -> str:
    """How to get downloads switched on, told from where the current answer comes from (``source`` is
    ``"console"``, ``"environment"`` or ``"default"``, as in :class:`projects.console_settings.Resolved`).

    Once a console choice exists the environment variable is ignored and there is no way back to "environment",
    so telling someone to set it would send them to a dead end. Shared by every backend that downloads."""
    from ..console_settings import SOURCE_CONSOLE

    if source == SOURCE_CONSOLE:
        return CONSOLE_OFF_MESSAGE
    return (
        "An administrator can switch it on in the console (Access, Ready-made model downloads) or set "
        f"{DOWNLOAD_ENV}=1 in the environment of the training helper (manage.py train_worker); {CONSOLE_WINS}."
    )


def _download_source() -> str:
    from ..console_settings import training_downloads

    return training_downloads().source


def downloads_allowed() -> bool:
    """May this process download ready-made weights? The stored console setting wins, then the environment
    variable, then off (see :mod:`projects.console_settings`). The helper calls this when a run starts."""
    from ..console_settings import training_downloads_allowed

    return training_downloads_allowed()


def pretrained_dir() -> Path:
    """Where published weights are cached: ``<data home>/models/pretrained`` (torch-hub layout)."""
    from . import models_root

    return models_root() / "pretrained"


def _weights_path(mode: str) -> Path:
    return pretrained_dir() / "checkpoints" / PRETRAINED_FILES[mode]["url"].rsplit("/", 1)[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(path: Path, hash_prefix: str) -> bool:
    return _sha256(path).startswith(hash_prefix)


class AllowedHosts(NamedTuple):
    """Where a download may come from: these exact host names, or any host ending in one of the dotted ``suffixes``."""

    exact: tuple[str, ...]
    suffixes: tuple[str, ...] = ()
    described: str = ""  # the words for a sentence, e.g. "download.pytorch.org"

    def allows(self, host: str) -> bool:
        return host in self.exact or any(host.endswith(suffix) and len(host) > len(suffix) for suffix in self.suffixes)


DOWNLOAD_HOSTS = AllowedHosts(("download.pytorch.org",), (), "download.pytorch.org")
"""Where torchvision's published weights come from, and the only place a redirect may lead."""


def _printable(text: str, limit: int = 80) -> str:
    cleaned = "".join(ch if ch.isprintable() else "?" for ch in text)
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


_HOST_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")


def _plain_host_name(host: str) -> bool:
    """True for a name made only of letters, digits and hyphens between single dots: no empty part (``x..hf.co``, a
    leading or trailing dot) and nothing like ``%2e``, a tab or a colon hiding in it. Capital letters were already
    lowered by the caller; an international name arrives as its ``xn--`` form."""
    return all(_HOST_LABEL.fullmatch(label) for label in host.split("."))


def redirect_refusal(url: str, allowed: AllowedHosts) -> str | None:
    """``None`` when a redirect to ``url`` may be followed, else a plain sentence saying why not (naming the host).

    Only https, only the default port, only a host in ``allowed`` (compared exactly, ignoring capital letters, so
    ``download.pytorch.org.evil.example`` and a trailing dot do not pass). An address with a user name or password
    in it (``https://evil.example@good.host/``) is refused whatever host follows the ``@``, a host name with an
    empty part (``x..hf.co``) or with anything but letters, digits and hyphens is refused, and so is an address with
    spaces or control characters around it. Shared by every backend that downloads, and decided without touching
    the network.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
        scheme = parts.scheme.lower()
        has_userinfo = parts.username is not None or parts.password is not None
    except ValueError:  # a malformed port or bracketed address
        return _refused(_printable(url.split("/")[2] if url.count("/") >= 2 else url), allowed)
    where = _printable(host if port in (None, 443) else f"{host}:{port}")
    if not host:
        return _refused(_printable(url) or "an empty address", allowed)
    if scheme != "https":
        return (
            f"The download was redirected to {where} over a plain, unencrypted connection "
            f"({_printable(scheme) if scheme else 'an unknown scheme'}), "
            "which is not allowed, so it was stopped and nothing was saved."
        )
    if has_userinfo:
        return (
            f"The download was redirected to {where} using a web address with a user name or password in it, which is "
            f"not allowed (Athena only fetches ready-made models from {allowed.described}), so it was stopped and "
            "nothing was saved."
        )
    if url != url.strip() or any(not ch.isprintable() for ch in url):
        return _refused(where, allowed)
    if port not in (None, 443) or not _plain_host_name(host) or not allowed.allows(host):
        return _refused(where, allowed)
    return None


def _refused(where: str, allowed: AllowedHosts) -> str:
    return (
        f"The download was redirected to {where}, which is not where Athena fetches ready-made models from "
        f"({allowed.described}), so it was stopped and nothing was saved."
    )


class PinnedRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only when :func:`redirect_refusal` says the target is allowed; otherwise a ``ValueError``
    with the plain sentence (not a ``URLError``, so it is not re-worded as "no internet")."""

    def __init__(self, allowed: AllowedHosts) -> None:
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        refusal = redirect_refusal(newurl, self.allowed)
        if refusal:
            raise ValueError(refusal)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_pinned(url: str, allowed: AllowedHosts, *, timeout: float):
    """Open ``url`` like ``urlopen``, but follow redirects only to ``allowed`` hosts (see :class:`PinnedRedirects`).
    Built per call and used instead of the process-wide default opener, so a proxy or handler installed elsewhere
    cannot loosen the rule."""
    opener = urllib.request.build_opener(PinnedRedirects(allowed))
    return opener.open(url, timeout=timeout)  # noqa: S310


MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
"""The most a torchvision download may send when its exact size is not known (200 MB; the two published files are 21
and 74 MB). Known files are cut off at their own size, see ``PRETRAINED_FILES[...]["bytes"]``."""


def _size_limit(url: str, expected_bytes: int | None) -> int:
    if expected_bytes is not None:
        return expected_bytes
    for info in PRETRAINED_FILES.values():
        if info["url"] == url:
            return info["bytes"]
    return MAX_DOWNLOAD_BYTES


def _fetch(url: str, destination: Path, hash_prefix: str, expected_bytes: int | None = None) -> None:
    """Download ``url`` to ``destination`` and check the published hash prefix. Tests replace this.

    The transfer is cut off one byte past the file's size (``expected_bytes``, else the published size of a known
    file, else ``MAX_DOWNLOAD_BYTES``), so a server that keeps sending cannot fill the disk before the checksum is
    looked at; the partial file is discarded."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    limit = _size_limit(url, expected_bytes)
    try:
        with open_pinned(url, DOWNLOAD_HOSTS, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, open(partial, "wb") as out:
            total = 0
            while chunk := response.read(min(1 << 20, limit + 1 - total)):
                total += len(chunk)
                if total > limit:
                    raise ValueError(
                        f"The download was larger than expected ({limit:,} bytes for this file), so it was stopped "
                        "and discarded."
                    )
                out.write(chunk)
        if not _verify(partial, hash_prefix):
            raise ValueError("The downloaded weights file did not match its published checksum, so it was discarded.")
        partial.replace(destination)
    except urllib.error.URLError as error:
        raise ValueError(
            f"Could not download the pretrained weights ({getattr(error, 'reason', error)}). Check this "
            "computer's internet access, or start the run with pretrained set to 'none'."
        ) from None
    except OSError as error:
        raise ValueError(f"Could not save the pretrained weights ({error.__class__.__name__}).") from None
    finally:
        partial.unlink(missing_ok=True)


def ensure_pretrained_weights(mode: str) -> dict[str, Any]:
    """Make sure the weights for ``mode`` are cached; returns provenance for the run record.

    Nothing is fetched unless downloads are switched on (:func:`downloads_allowed`). A file that is already
    in the cache and matches its published checksum is used without the switch (no download happens).
    """
    if mode == "none":
        return {"mode": "none"}
    if mode not in PRETRAINED_FILES:
        raise ValueError(
            f"The starting model {mode!r} was never resolved to a real one, so there is nothing to load. "
            "Start the run again."
        )
    info = PRETRAINED_FILES[mode]
    path = _weights_path(mode)
    if path.is_file() and _verify(path, info["hash_prefix"]):
        source = "cache"
    elif not downloads_allowed():
        raise ValueError(
            f"This run asked for pretrained weights ('{mode}': {info['what']}, about {info['approx_mb']} MB), but "
            f"downloading is switched off on this computer. {download_blocked_message(_download_source())} Then run "
            "it again - the file is fetched once from download.pytorch.org and kept in your Athena data folder under "
            "models/pretrained - or start the run with pretrained set to 'none', which downloads nothing. Read the "
            "licence note first: the pretrained weights come with their own training-data terms."
        )
    else:
        path.unlink(missing_ok=True)  # a stale or corrupt file must not survive
        _fetch(info["url"], path, info["hash_prefix"])
        source = "download"
    return {
        "mode": mode,
        "what": info["what"],
        "torchvision_weights": info["enum"],
        "source_url": info["url"],
        "sha256": _sha256(path) if path.is_file() else None,
        "from": source,
    }


_VERIFIED: dict[tuple[str, int, int, str], bool] = {}


def cached_weights_ok(mode: str) -> bool:
    """True when the published file for ``mode`` is in the cache and matches its published checksum.

    Hashing 75 MB takes a moment, so the answer is remembered per (path, size, modified time)."""
    info = PRETRAINED_FILES[mode]
    path = _weights_path(mode)
    try:
        stat = path.stat()
    except OSError:
        return False
    key = (str(path), stat.st_size, stat.st_mtime_ns, info["hash_prefix"])
    if key not in _VERIFIED:
        _VERIFIED[key] = _verify(path, info["hash_prefix"])
    return _VERIFIED[key]


def downloads_for(worker: dict[str, Any]) -> tuple[bool, str]:
    """(allowed, who said so). Public: every backend that downloads uses this rule (D-FINE calls it too).

    ``who`` is ``"setting"`` (a choice stored in the console), ``"helper"`` (the running helper's own report) or
    ``"console"`` (no choice stored and no helper report: the console process's environment, a guess). A choice stored in the console wins and is read live from the database, which the
    helper reads too, so a helper report up to ten seconds old cannot hide it. Otherwise the helper that trains
    is the one that downloads, so its own report (of its environment) wins; with no helper running the
    console's environment is the best guess, and the page says it is a guess."""
    from ..console_settings import SOURCE_CONSOLE, training_downloads

    resolved = training_downloads()
    if resolved.source == SOURCE_CONSOLE:
        return resolved.allow, "setting"
    reported = worker.get("downloads_allowed")
    if reported is not None:
        return bool(reported), "helper"
    return resolved.allow, "console"


_downloads_for = downloads_for  # the name used before this was public; kept so older imports keep working


def steps_source(who: str) -> str:
    """The ``source`` that :func:`download_blocked_message` wants, from the ``who`` that :func:`downloads_for` gives."""
    return "console" if who == "setting" else "environment"


ENABLE_STEPS = (
    "An administrator can switch this on in the console, under “Access”, in “Ready-made model downloads”; then press "
    "“Check again” here. Or ask your administrator to set ATHENA_TRAINING_ALLOW_DOWNLOAD=1 in the environment of the "
    "teaching helper (manage.py train_worker), restart the helper, then press “Check again” here. That setting only "
    f"counts until someone chooses in the console: {CONSOLE_WINS}."
)  # the words while no console choice exists; with one, enable_steps("console")


def enable_steps(source: str = "environment") -> str:
    """The Teach panel's ``how_to_enable`` for a ready-made start, told from where the current answer comes from."""
    if source == "console":
        return CONSOLE_OFF_MESSAGE + " Then press “Check again” here."
    return ENABLE_STEPS
LAWYER = "Ask a lawyer before commercial use."
AUTO_NONE_REASON = (
    "No ready-made starting model can be used on this computer right now: none has been downloaded before, and "
    "downloading is switched off. In Athena’s one test run, a model started from nothing found none of the test "
    "objects, so Athena will not start one for you. Only an administrator can fix this."
)
NO_DOWNLOAD_WAY = (
    "No download wanted? Put the ready-made model "
    f"files into Athena’s data folder under models/pretrained/checkpoints/ "
    f"({' and '.join(info['url'].rsplit('/', 1)[1] for info in PRETRAINED_FILES.values())}); a file that is there and "
    "matches its published checksum is used without any download. Then press “Check again” here."
)
AUTO_NONE_STEPS = (
    "An administrator can switch this on in the console, under “Access”, in “Ready-made model downloads” (it needs an "
    "administrator account; the Trainer and Approver roles cannot). A choice made there wins over the setting below. "
    "The other way is a one-time step for the person who installed Athena (not something to type into this page). On "
    "the computer that runs Athena, find the setting ATHENA_TRAINING_ALLOW_DOWNLOAD for the teaching helper (the program "
    "started with `python manage.py train_worker`) and set it to 1, then restart that helper. If you use Docker: open "
    "the file web/.env, change ATHENA_TRAINING_ALLOW_DOWNLOAD=0 to 1, then run `docker compose up -d train_worker` in "
    f"the web folder. That setting only counts until someone chooses in the console: {CONSOLE_WINS}, over this file "
    "too. The two ready-made models are then downloaded once, from download.pytorch.org, and kept in "
    "Athena’s data folder. Details: web/DEPLOYMENT.md, section “Ready-made starting models in the Teach panel”. Read "
    "the licence note first: ready-made models come with their own terms. " + NO_DOWNLOAD_WAY
)


def auto_none_steps(source: str = "environment") -> str:
    """``how_to_enable`` for the one-click start when nothing ready-made can run, told from where the answer comes from."""
    if source == "console":
        return (
            CONSOLE_OFF_MESSAGE + " It needs an administrator account; the Trainer and Approver roles cannot. Read the "
            "licence note first: ready-made models come with their own terms. " + NO_DOWNLOAD_WAY
        )
    return AUTO_NONE_STEPS
GENERAL_TERMS = (
    "Ready-made models come with their own terms, separate from the free software that trains them. The makers of the "
    "software say it is your responsibility to decide whether you may use them."
)
START_OPTION_TEXT = {
    "none": {
        "title": "Start from scratch",
        "summary": (
            "The computer starts knowing nothing and learns only from your own pictures. Nothing is downloaded and there are "
            "no extra licence terms, so this is the safest choice. But learning from nothing needs many labelled pictures: "
            "with only a few it often learns almost nothing, so read the results before you trust it."
        ),
    },
    "imagenet_backbone": {
        "title": "Start from a general picture model (ImageNet)",
        "summary": (
            "The computer starts with the “eyes” of a ready-made model that was taught on a huge collection of everyday photos "
            "(ImageNet), then learns your items on top. It usually needs far fewer pictures than starting from scratch."
        ),
        "licence_plain": (
            f"{GENERAL_TERMS} The ImageNet collection says it may be used only for non-commercial research and education. "
            f"Nobody has confirmed whether that also covers a model that was taught from it. {LAWYER}"
        ),
    },
    "coco": {
        "title": "Start from a general object model (COCO)",
        "summary": (
            "The computer starts with a ready-made model that already finds everyday objects in photos (taught on the COCO "
            "collection), and only learns your own kinds of item on top. It usually needs far fewer pictures than starting "
            "from scratch."
        ),
        "licence_plain": (
            f"{GENERAL_TERMS} The photos in the COCO collection come from many owners with mixed terms, and the collection says "
            f"it does not own them. Nobody has confirmed whether those terms reach a model that was taught from them. {LAWYER}"
        ),
    },
}


@contextmanager
def _torch_hub_dir(path: Path):
    """Point torch.hub at our cache for the duration of a model build, then restore it."""
    import torch

    previous = torch.hub.get_dir()
    torch.hub.set_dir(str(path))
    try:
        yield
    finally:
        torch.hub.set_dir(previous)


def _int(config: dict[str, Any], key: str, low: int, high: int) -> int:
    value = config.get(key, DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{key} must be a whole number between {low} and {high}.")
    return value


class TorchvisionFasterRCNN(TrainingBackend):
    name = "torchvision-fasterrcnn"
    description = (
        "Faster R-CNN (MobileNetV3-Large 320 FPN) on CPU. Starts from random weights unless you opt in to "
        "published pretrained weights (setting 'pretrained')."
    )
    license_note = (
        "Code: torchvision and PyTorch, BSD-3-Clause. Default (pretrained='none'): No pretrained weights are "
        "downloaded or bundled (weights=None, weights_backbone=None), so there is no separate model-weight "
        "licence. Opt-in (pretrained='imagenet_backbone' or 'coco', and only if downloads are switched on, in the console or with ATHENA_TRAINING_ALLOW_DOWNLOAD=1): "
        "torchvision's published weights are fetched from download.pytorch.org. Those weights are NOT covered by "
        "torchvision's BSD-3 code licence alone. Checked: torchvision's README says its pre-trained models 'may "
        "have their own licenses or terms and conditions derived from the dataset used for training' and that "
        "you must decide whether you may use them; the weight metadata shipped in torchvision 0.24.1 has no "
        "licence field. ImageNet (backbone): image-net.org's terms of access say the Database may be used 'only "
        "for non-commercial research and educational purposes'; whether that reaches weights trained on it is "
        "unverified, ask a lawyer before commercial use. COCO (full detector): cocodataset.org says the "
        "annotations are CC BY 4.0, the COCO Consortium does not own the images and their use must follow the "
        "Flickr terms (mixed per-image licences); whether that reaches weights trained on them is unverified, "
        "ask a lawyer. Models you train are derived from your own images; check the licence of those images."
    )
    dataset_format = "yolo"
    constraints = (
        "Needs torch==2.9.1 and torchvision==0.24.1 in the worker environment (web/requirements-autolabel.txt).",
        "CPU only. Expect minutes per epoch for a few hundred images on a laptop; no GPU is used.",
        "Starts from random weights by default, so small datasets give weak models. Check the evaluation report.",
        "Optional pretrained start (setting 'pretrained'; needs downloads switched on (console, or ATHENA_TRAINING_ALLOW_DOWNLOAD=1) for the first "
        "download, about 21 MB for 'imagenet_backbone' or 74 MB for 'coco'). See the licence note.",
        "Uses the train split only; the validation split is not used by this backend.",
    )

    def __init__(self) -> None:
        self._cache: tuple[tuple[str, int, int], Any, list[str]] | None = None

    # -- availability / config ---------------------------------------------------------
    def check_available(self) -> Availability:
        missing = [name for name in ("torch", "torchvision") if importlib.util.find_spec(name) is None]
        if not missing:
            return Availability(True, "torch and torchvision are installed.")
        return Availability(
            False,
            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not installed in this environment.",
            "Install the worker requirements (pip install -r web/requirements-autolabel.txt) in the "
            "environment that runs `manage.py train_worker`, then start the worker.",
        )

    def default_config(self) -> dict[str, Any]:
        return dict(DEFAULTS)

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        unknown = set(config) - set(DEFAULTS) - set(EXTRA_KEYS)
        if unknown:
            raise ValueError(f"Unsupported training setting: {', '.join(sorted(map(str, unknown)))}.")
        rate = config.get("learning_rate", DEFAULTS["learning_rate"])
        try:  # an integer too large for a float (10**400) raises OverflowError, which must not reach the caller
            rate_ok = not isinstance(rate, bool) and isinstance(rate, (int, float)) and 0 < float(rate) <= 1
        except (OverflowError, ValueError, TypeError):
            rate_ok = False
        if not rate_ok:
            raise ValueError("learning_rate must be a number above 0 and at most 1.")
        pretrained = config.get("pretrained", DEFAULTS["pretrained"])
        if not isinstance(pretrained, str) or pretrained not in PRETRAINED_CHOICES:
            raise ValueError("pretrained must be one of: " + ", ".join(f"'{m}'" for m in PRETRAINED_CHOICES) + ".")
        normalized = {
            "pretrained": pretrained,
            "epochs": _int(config, "epochs", 1, 100),
            "batch_size": _int(config, "batch_size", 1, 16),
            "learning_rate": float(rate),
            "seed": _int(config, "seed", 0, 2**31 - 1),
            "threads": _int(config, "threads", 0, 64),
        }
        # Optional keys are only echoed when present, so a config without them validates to exactly what it always did.
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
            if not isinstance(derived, list) or not derived or not set(derived) <= set(DERIVED_KEYS) or len(set(derived)) != len(derived):
                raise ValueError("derived must list which of " + ", ".join(DERIVED_KEYS) + " Athena chose.")
            normalized["derived"] = sorted(derived)
        return normalized

    # -- "auto": the best ready-made start that can run right now, and settings worked out from the data ----------
    def resolve_start(self, worker: dict[str, Any]) -> dict[str, Any]:
        options = {o["value"]: o for o in self.start_options(worker)}
        usable = [options[mode] for mode in AUTO_PREFERENCE if options[mode]["available"]]
        if usable:
            return {"resolved": usable[0]["value"], "available": usable, "reason_if_none": "", "how_to_enable": ""}
        source = steps_source(downloads_for(worker)[1])
        return {"resolved": None, "available": [], "reason_if_none": AUTO_NONE_REASON, "how_to_enable": auto_none_steps(source)}

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
            # An explicit batch size wins over the default but is never above the training set, and the epochs are
            # worked out for the batch that will actually be used. Explicit epochs always win. Anything that is not a
            # whole number in range is left alone for ``validate_config`` to refuse in plain words.
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
            "seconds_per_picture_epoch": SECONDS_PER_PICTURE_EPOCH.get(start or ""),
            "estimate_note": ESTIMATE_NOTE,
        }
        for quality in QUALITIES:
            if train_pictures is None or train_pictures < 1:
                preview[quality] = None
                continue
            settings = derive_settings(train_pictures, quality)
            preview[quality] = {**settings, "estimated_minutes": estimate_minutes(train_pictures, settings["epochs"], start)}
        return preview

    # -- the Teach panel's "start from" choices ------------------------------------------------
    def _unavailable(self, mode: str, worker: dict[str, Any]) -> tuple[str, str]:
        """(reason, how to fix) for a ready-made start that cannot be used right now; ("", "") when it can."""
        if cached_weights_ok(mode):
            return "", ""
        allowed, who = downloads_for(worker)
        if allowed:
            return "", ""
        mb = PRETRAINED_FILES[mode]["approx_mb"]
        reason = (
            f"Downloading ready-made models is switched off on this computer, and this one (about {mb} MB) has not been "
            "downloaded before, so it cannot be used. Only an administrator can change this."
        )
        if who == "console" and not worker.get("online"):
            reason += (
                " The teaching helper is not running, so this is the setting of the console itself; the helper must have "
                "the same setting."
            )
        return reason, enable_steps(steps_source(who))

    def start_options(self, worker: dict[str, Any]) -> list[dict[str, Any]]:
        options = []
        for value in PRETRAINED_MODES:
            text = START_OPTION_TEXT[value]
            if value == "none":
                options.append({
                    "value": value, "title": text["title"], "summary": text["summary"], "licence_plain": "",
                    "available": True, "ready": True, "needs_acknowledgement": False, "unavailable_reason": "",
                    "how_to_enable": "", "size_note": "",
                })
                continue
            ready = cached_weights_ok(value)
            reason, how = self._unavailable(value, worker)
            mb = PRETRAINED_FILES[value]["approx_mb"]
            options.append({
                "value": value, "title": text["title"], "summary": text["summary"], "licence_plain": text["licence_plain"],
                "available": not reason, "ready": ready, "needs_acknowledgement": True,
                "unavailable_reason": reason, "how_to_enable": how,
                "size_note": (
                    f"Already on this computer (about {mb} MB, verified). Nothing will be downloaded."
                    if ready else f"About {mb} MB. It is downloaded once, from download.pytorch.org, and then kept in Athena’s data folder."
                ),
            })
        return options

    def start_check(self, config: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
        mode = config.get("pretrained", "none")
        if mode == "none":
            return {"needs_acknowledgement": False, "blocked": ""}
        reason, how = self._unavailable(mode, worker)
        return {"needs_acknowledgement": True, "blocked": f"{reason} {how}".strip()}

    def runtime_info(self) -> dict[str, Any]:
        import torch
        import torchvision

        return {
            "torch": torch.__version__,
            "torchvision": torchvision.__version__,
            "device": "cpu",
            "threads": torch.get_num_threads(),
        }

    # -- dataset -----------------------------------------------------------------------
    @staticmethod
    def read_classes(dataset_dir: Path) -> list[str]:
        text = (dataset_dir / "classes.txt").read_text(encoding="utf-8")
        return [line for line in text.splitlines() if line]

    @staticmethod
    def read_split(dataset_dir: Path, split: str) -> list[tuple[Path, list[tuple[int, float, float, float, float]], int, int]]:
        """(image path, [(class, x, y, w, h) normalized top-left], width, height) per image."""
        folder = dataset_dir / "images" / split
        samples = []
        if not folder.is_dir():
            return samples
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            with Image.open(path) as image:
                width, height = image.size
            label_file = dataset_dir / "labels" / split / f"{path.stem}.txt"
            boxes = []
            if label_file.is_file():
                for line in label_file.read_text(encoding="ascii").splitlines():
                    parts = line.split()
                    if len(parts) != 5:
                        continue
                    cls, cx, cy, w, h = int(parts[0]), *map(float, parts[1:])
                    boxes.append((cls, cx - w / 2, cy - h / 2, w, h))
            samples.append((path, boxes, width, height))
        return samples

    def eval_items(self, dataset_dir: Path, split: str = "test"):
        from .base import EvalItem

        labels = self.read_classes(dataset_dir)
        return [
            EvalItem(path, [(labels[c], x, y, w, h) for c, x, y, w, h in boxes], width, height)
            for path, boxes, width, height in self.read_split(dataset_dir, split)
        ]

    # -- model -------------------------------------------------------------------------
    @staticmethod
    def build_model(num_classes: int, pretrained: str = "none"):
        """Build the network. ``pretrained`` other than 'none' needs the weights already cached
        (see ``ensure_pretrained_weights``); torchvision then reads them from our cache folder."""
        from torchvision.models import MobileNet_V3_Large_Weights
        from torchvision.models.detection import (
            FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
            fasterrcnn_mobilenet_v3_large_320_fpn,
        )

        if pretrained == "none":  # Nothing is downloaded: both weight arguments are None.
            return fasterrcnn_mobilenet_v3_large_320_fpn(weights=None, weights_backbone=None, num_classes=num_classes)
        with _torch_hub_dir(pretrained_dir()):
            if pretrained == "imagenet_backbone":
                return fasterrcnn_mobilenet_v3_large_320_fpn(
                    weights=None,
                    weights_backbone=MobileNet_V3_Large_Weights.IMAGENET1K_V1,
                    num_classes=num_classes,
                )
            from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

            model = fasterrcnn_mobilenet_v3_large_320_fpn(
                weights=FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1, weights_backbone=None
            )
        model.roi_heads.box_predictor = FastRCNNPredictor(model.roi_heads.box_predictor.cls_score.in_features, num_classes)
        return model

    @classmethod
    def build_structure(cls, num_classes: int, frozen_bn: bool):
        """The same network without any weights, for loading a saved artifact (never downloads).
        Pretrained starts use frozen batch-norm layers, which have a different state-dict layout."""
        from torch import nn
        from torchvision.ops.misc import FrozenBatchNorm2d

        model = cls.build_model(num_classes)
        if frozen_bn:

            def freeze(module):
                for name, child in module.named_children():
                    if isinstance(child, nn.BatchNorm2d):
                        setattr(module, name, FrozenBatchNorm2d(child.num_features, eps=child.eps))
                    else:
                        freeze(child)

            freeze(model)
        return model

    def train(
        self,
        dataset_dir: Path,
        config: dict[str, Any],
        progress_cb: ProgressCallback,
        cancel_cb: CancelCallback,
    ) -> TrainResult:
        import torch
        from torchvision.transforms.functional import pil_to_tensor

        cfg = self.validate_config(config)
        if cfg["pretrained"] == AUTO:
            raise ValueError("This run still says 'auto' for its starting model: it was never resolved. Start it again.")
        provenance = ensure_pretrained_weights(cfg["pretrained"])  # fails fast, before any work
        labels = self.read_classes(dataset_dir)
        samples = self.read_split(dataset_dir, "train")
        if not samples:
            raise ValueError("The train split has no images, so there is nothing to learn from.")
        if cfg["threads"]:
            torch.set_num_threads(cfg["threads"])
        torch.manual_seed(cfg["seed"])
        shuffler = random.Random(cfg["seed"])

        model = self.build_model(len(labels) + 1, cfg["pretrained"])
        model.train()
        parameters = [p for p in model.parameters() if p.requires_grad]
        optimizer = torch.optim.SGD(parameters, lr=cfg["learning_rate"], momentum=0.9, weight_decay=1e-4)

        def load(sample, flip: bool):
            path, boxes, width, height = sample
            with Image.open(path) as opened:
                tensor = pil_to_tensor(opened.convert("RGB")).float() / 255.0
            xyxy, classes = [], []
            for cls, x, y, w, h in boxes:
                x1, y1 = x * width, y * height
                x2, y2 = max(x1 + MIN_BOX_PIXELS, (x + w) * width), max(y1 + MIN_BOX_PIXELS, (y + h) * height)
                if flip:
                    x1, x2 = width - x2, width - x1
                xyxy.append([x1, y1, x2, y2])
                classes.append(cls + 1)  # 0 is background
            if flip:
                tensor = tensor.flip(-1)
            return tensor, {
                "boxes": torch.tensor(xyxy, dtype=torch.float32).reshape(-1, 4),
                "labels": torch.tensor(classes, dtype=torch.int64),
            }

        batches_per_epoch = -(-len(samples) // cfg["batch_size"])
        total_steps = cfg["epochs"] * batches_per_epoch
        step = 0
        history = []
        started = time.monotonic()
        for epoch in range(1, cfg["epochs"] + 1):
            order = list(range(len(samples)))
            shuffler.shuffle(order)
            running, count = 0.0, 0
            for start in range(0, len(order), cfg["batch_size"]):
                if cancel_cb():
                    raise TrainingCancelled("Training was cancelled.")
                batch = [load(samples[i], shuffler.random() < 0.5) for i in order[start : start + cfg["batch_size"]]]
                losses = model([item[0] for item in batch], [item[1] for item in batch])
                loss = sum(losses.values())
                if not torch.isfinite(loss):
                    raise ValueError(
                        "Training diverged (the loss became infinite). Lower learning_rate and try again."
                    )
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                running += float(loss.detach())
                count += 1
                step += 1
                progress_cb(step / total_steps, f"epoch {epoch}/{cfg['epochs']} step {step}/{total_steps} loss {float(loss.detach()):.4f}")
            history.append({"epoch": epoch, "mean_loss": round(running / max(count, 1), 5)})

        artifact = dataset_dir.parent / ARTIFACT_NAME
        torch.save(
            {
                "format": ARTIFACT_FORMAT,
                "labels": labels,
                "pretrained": cfg["pretrained"],
                "frozen_bn": cfg["pretrained"] != "none",
                "state_dict": model.state_dict(),
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
                "note": "Training loss only; see the held-out evaluation for quality.",
            },
            label_names=labels,
        )

    # -- inference ---------------------------------------------------------------------
    def _load(self, artifact: Path):
        import torch

        stat = Path(artifact).stat()
        key = (str(artifact), stat.st_mtime_ns, stat.st_size)
        if self._cache and self._cache[0] == key:
            return self._cache[1], self._cache[2]
        checkpoint = torch.load(artifact, map_location="cpu", weights_only=True)
        if checkpoint.get("format") != ARTIFACT_FORMAT:
            raise ValueError("This file is not a model saved by the torchvision-fasterrcnn backend.")
        labels = list(checkpoint["labels"])
        model = self.build_structure(len(labels) + 1, bool(checkpoint.get("frozen_bn", False)))
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        self._cache = (key, model, labels)
        return model, labels

    def predict(self, artifact: Path, image: Image.Image, thresholds: dict[str, Any]) -> list[BoxPrediction]:
        import torch
        from torchvision.transforms.functional import pil_to_tensor

        model, labels = self._load(artifact)
        confidence = float(thresholds.get("confidence", 0.5))
        max_boxes = int(thresholds.get("max_boxes", 100))
        model.roi_heads.score_thresh = confidence
        model.roi_heads.detections_per_img = max_boxes
        rgb = image.convert("RGB")
        width, height = rgb.size
        with torch.no_grad():
            output = model([pil_to_tensor(rgb).float() / 255.0])[0]
        found = []
        for box, label, score in zip(output["boxes"].tolist(), output["labels"].tolist(), output["scores"].tolist()):
            x1, y1 = max(0.0, box[0] / width), max(0.0, box[1] / height)
            x2, y2 = min(1.0, box[2] / width), min(1.0, box[3] / height)
            if x2 <= x1 or y2 <= y1 or not 1 <= label <= len(labels):
                continue
            found.append(BoxPrediction(labels[label - 1], x1, y1, x2 - x1, y2 - y1, float(score)))
        return found
