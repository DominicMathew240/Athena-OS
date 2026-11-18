"""Audio classification backend: a tiny CNN over log mel-spectrograms of WAV clips.

Ported from Athena Forge's ``backend/ml/audio_trainer.py`` (the algorithm, not the FastAPI
wiring) so an ``audio-classification`` project can actually be taught: each clip becomes a
log mel-spectrogram (numpy; librosa is never required), a small 2-conv CNN is trained on CPU
with torch, and the artifact is exported to ONNX (``model.onnx``) plus a ``metadata.json``
sidecar carrying the labels and feature settings. When onnxruntime is missing the artifact is
a torch state-dict plus the same sidecar; predictions then run through torch. Inference runs
the ONNX model with onnxruntime (``CPUExecutionProvider``, session cached per file) with the
same torch fallback.

Module import stays light (no torch, no numpy at import time): the web process lists and
describes the backend; only the worker calls ``build_dataset`` / ``train`` / ``evaluate`` /
``predict``.

Artifact layout on disk (the file the runner copies and hashes):

- ``model.bin`` - the ONNX graph when onnxruntime is importable **at export time**, else a
  torch ``state_dict``. ``metadata.json`` says which (``"format": "onnx" | "torch"``).
- ``metadata.json`` - labels, feature settings, artifact format.

Dataset layout under ``dest`` (what ``build_dataset`` writes):

- ``entries/<split>/<n>_<sha12>.wav`` - the runner's own sha-verified export; this backend
  does not move or copy those files, it reads them where they are.
- ``classes.txt`` - one label per line (the generic export's convention).
- ``labels.csv`` - ``filename,label`` for every entry (human-readable, and the pairing the
  evaluation uses).

Evaluation shape (keys the console reads; documented because it is not the box-detection
one): ``available``, ``reason``, ``split``, ``accuracy``, ``per_label`` (label -> counts and
precision/recall/f1), ``confusion`` (true label -> predicted label -> count), ``data``
(``{"clips": n}``), ``limits``, ``eval_kind: "clip_classification"``. The plain results card
answers "shown in its evaluation report" for this task (see ``results_views``), so nothing in
the box-shaped wording is reused.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import math
import time
import wave
from pathlib import Path
from typing import Any

from .base import Availability, CancelCallback, ProgressCallback, TrainingBackend, TrainingCancelled, TrainResult

ARTIFACT_NAME = "model.bin"
METADATA_NAME = "metadata.json"
ARTIFACT_FORMAT = "athena.audio-wav-cnn/1"
#: Fixed feature settings (Forge's defaults): mel bands, FFT size, hop, the rate every clip is
#: resampled to, and the time span a clip is padded or cropped to. They live in the artifact's
#: metadata so a saved model always preprocesses the way it was trained.
N_MELS = 64
N_FFT = 1024
HOP_LENGTH = 256
TARGET_SR = 16_000
TARGET_FRAMES = 128

DEFAULTS = {"epochs": 5, "batch_size": 4, "learning_rate": 1e-3, "seed": 0}


def _torch_and_numpy():
    """(torch, nn, np) - imported lazily; raises with a plain message when either is missing."""
    try:
        import numpy as np
        import torch
        from torch import nn
    except ImportError as error:  # pragma: no cover - the availability check normally catches this first
        raise ValueError(
            f"Sound training needs torch and numpy in the worker environment ({error}). "
            "Install the worker requirements in the environment that runs manage.py train_worker."
        ) from error
    return torch, nn, np


# -- WAV reading and mel features (ported from Forge; numpy only) ------------------------------
def _read_wav(path: Path):
    """Read a WAV file: (mono float32 signal, sample_rate). Handles 8/16/24/32-bit PCM."""
    import numpy as np

    with wave.open(str(path), "rb") as clip:
        channels = clip.getnchannels()
        sample_width = clip.getsampwidth()
        sample_rate = clip.getframerate()
        raw = clip.readframes(clip.getnframes())

    if sample_width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 3:
        # 24-bit samples arrive as packed little-endian triples; numpy has no dtype for them, so
        # widen to 32 bits and sign-extend in place.
        usable = len(raw) - len(raw) % 3
        bytes32 = np.frombuffer(raw[:usable] + b"\x00" * (usable // 3), dtype=np.uint8).reshape(-1, 4)
        values = (
            bytes32[:, 0].astype(np.int32) | (bytes32[:, 1].astype(np.int32) << 8)
            | (bytes32[:, 2].astype(np.int32) << 16)
        )
        values = (values ^ 0x800000) - 0x800000  # sign bit of the 24-bit value
        data = values.astype(np.float32) / 8388608.0
    elif sample_width == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width {sample_width}.")

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data.astype(np.float32), sample_rate


def _hz_to_mel(hz: float) -> float:
    return 2595.0 * math.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _mel_filterbank(sample_rate: int, n_fft: int, n_mels: int):
    """The standard triangular mel filterbank matrix, (n_mels, n_fft//2+1)."""
    import numpy as np

    mel_points = np.linspace(_hz_to_mel(0.0), _hz_to_mel(sample_rate / 2.0), n_mels + 2)
    hz_points = _mel_to_hz(mel_points)
    fft_bin = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)

    bank = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for i in range(1, n_mels + 1):
        left, center, right = fft_bin[i - 1], fft_bin[i], fft_bin[i + 1]
        for j in range(left, center):
            bank[i - 1, j] = (j - left) / max(center - left, 1)
        for j in range(center, right):
            bank[i - 1, j] = (right - j) / max(right - center, 1)
    return bank


def _stft_magnitude(signal, n_fft: int, hop_length: int):
    """STFT magnitude spectrogram via numpy: (n_fft//2+1, frames), centre-padded."""
    import numpy as np

    pad = n_fft // 2
    padded = np.pad(signal, (pad, pad), mode="reflect")
    n_frames = 1 + (len(padded) - n_fft) // hop_length
    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(n_frames, n_fft),
        strides=(padded.strides[0] * hop_length, padded.strides[0]),
        writeable=False,
    ).copy()
    window = np.hanning(n_fft).astype(np.float32)
    spec = np.fft.rfft(frames * window, n=n_fft)
    return np.abs(spec).astype(np.float32).T


def compute_mel_spectrogram(signal, sample_rate: int, *, n_mels: int = N_MELS, n_fft: int = N_FFT,
                            hop_length: int = HOP_LENGTH, target_sr: int = TARGET_SR):
    """A (n_mels, time) log mel-spectrogram, normalised per clip."""
    import numpy as np

    if sample_rate != target_sr:
        # Naive linear resampling (Forge's numpy fallback): enough for classification features.
        new_len = int(len(signal) * target_sr / sample_rate)
        signal = np.interp(
            np.linspace(0, len(signal) - 1, new_len), np.arange(len(signal)), signal
        ).astype(np.float32)
    mag = _stft_magnitude(signal, n_fft, hop_length) + 1e-10
    mel = _mel_filterbank(target_sr, n_fft, n_mels) @ mag
    mel_db = 20.0 * np.log10(mel)
    mel_db = (mel_db - mel_db.mean()) / max(mel_db.std(), 1e-9)
    return mel_db.astype(np.float32)


def pad_or_crop(spec, target_frames: int):
    """Centre-pad or crop a (n_mels, time) spectrogram to ``target_frames``."""
    import numpy as np

    n_mels, frames = spec.shape
    if frames < target_frames:
        return np.concatenate([spec, np.zeros((n_mels, target_frames - frames), dtype=spec.dtype)], axis=1)
    if frames > target_frames:
        start = (frames - target_frames) // 2
        return spec[:, start:start + target_frames]
    return spec


def clip_features(path: Path) -> Any:
    """One WAV file as a (1, 1, n_mels, target_frames) float32 array ready for the network."""
    import numpy as np

    signal, sample_rate = _read_wav(path)
    spec = compute_mel_spectrogram(signal, sample_rate)
    spec = pad_or_crop(spec, TARGET_FRAMES)
    return spec[np.newaxis, np.newaxis, :, :]


def _softmax(logits) -> list[float]:
    """Numerically stable softmax over a 1-D array (numpy or a plain sequence)."""
    values = [float(v) for v in logits]
    top = max(values)
    exp = [math.exp(v - top) for v in values]
    total = sum(exp)
    return [e / total for e in exp]


# -- the network (ported from Forge; torch is only imported inside methods) --------------------
def build_model(num_classes: int, n_mels: int = N_MELS):
    """TinyAudioCNN: two conv+pool blocks, adaptive average pool, one linear layer."""
    _, nn, _ = _torch_and_numpy()

    class TinyAudioCNN(nn.Module):
        def __init__(self, classes: int, mels: int):
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(1, 16, kernel_size=3, padding=1),
                nn.ReLU(),
                nn.MaxPool2d(2, 2),
                nn.Conv2d(16, 32, kernel_size=3, padding=1),
                nn.ReLU(),
                nn.AdaptiveAvgPool2d((1, 1)),
            )
            self.classifier = nn.Linear(32, classes)

        def forward(self, x):
            x = self.features(x)
            x = x.view(x.size(0), -1)
            return self.classifier(x)

    return TinyAudioCNN(num_classes, n_mels)


class ClipEvalItem:
    """One held-out clip: its exported file, its true label and the asset it came from.

    This backend's own eval shape (the image ``EvalItem`` is about boxes and picture sizes);
    ``evaluate`` consumes exactly this."""

    def __init__(self, path: Path, label: str, asset_id: str | None = None) -> None:
        self.path = path
        self.label = label
        self.asset_id = asset_id


def _session(artifact: Path):
    """The onnxruntime InferenceSession for an ONNX artifact, cached per backend instance."""
    onnx_path, _ = _artifact_files(artifact)
    if str(onnx_path) not in _SESSIONS:
        import onnxruntime as ort

        _SESSIONS[str(onnx_path)] = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    return _SESSIONS[str(onnx_path)]


_SESSIONS: dict[str, Any] = {}


def _artifact_files(artifact: Path) -> tuple[Path, Path]:
    """(weights file, metadata file) of an artifact directory or file, Forge-layout compatible."""
    artifact = Path(artifact)
    if artifact.is_dir():
        meta = artifact / METADATA_NAME
        weights = artifact / "model.bin" if (artifact / "model.bin").is_file() else (
            artifact / "model.onnx" if (artifact / "model.onnx").is_file() else artifact / "model.pt"
        )
        return weights, meta
    return artifact, artifact.parent / METADATA_NAME


def _load_metadata(artifact: Path) -> dict[str, Any]:
    weights, meta = _artifact_files(artifact)
    if not meta.is_file():
        raise ValueError("This model's audio metadata file is missing, so it cannot be used. Re-train the model.")
    metadata = json.loads(meta.read_text(encoding="utf-8"))
    if metadata.get("format") not in ("athena.audio-wav-cnn/1",):
        raise ValueError("This file is not a model saved by the audio-wav-cnn backend.")
    return metadata


class AudioWavBackend(TrainingBackend):
    name = "audio-wav-cnn"
    description = (
        "Sound classification on CPU: log mel-spectrogram features per WAV clip and a tiny CNN, "
        "exported to ONNX when onnxruntime is installed."
    )
    license_note = (
        "Code: PyTorch and torchvision are not used here except via the shared runner; this backend "
        "uses PyTorch (BSD-3-Clause), numpy (BSD-3-Clause) and, optionally, onnxruntime (MIT) for "
        "prediction. No pretrained weights are downloaded or shipped: the network starts from random "
        "initialisation and learns only from your own clips. Models you train are derived from your "
        "own recordings; check the licence of those recordings."
    )
    constraints = (
        "Needs torch and numpy in the worker environment; onnxruntime is optional (prediction falls "
        "back to torch when it is missing).",
        "CPU only. Expect seconds per epoch for a few hundred short clips on a laptop.",
        "WAV clips only (the audio modality plugin's limits): 0.25-60 s, 8-48 kHz, mono or stereo.",
        "Every clip is resampled to 16 kHz mono and padded or cropped to about 2 s of features, so "
        "clips much longer than 2 s are represented by their centre.",
        "Starts from random weights, so small datasets give weak models. Check the evaluation report.",
        "Uses the train split only; the validation split is not used by this backend.",
    )

    # -- availability / config ------------------------------------------------------------------
    def check_available(self) -> Availability:
        missing = [name for name in ("torch", "numpy") if importlib.util.find_spec(name) is None]
        if not missing:
            note = "torch and numpy are installed."
            if importlib.util.find_spec("onnxruntime") is None:
                note += " onnxruntime is not installed: models are saved as torch files."
            return Availability(True, note)
        return Availability(
            False,
            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not installed in this environment.",
            "Install torch and numpy in the environment that runs `manage.py train_worker`, then "
            "start the worker.",
        )

    def default_config(self) -> dict[str, Any]:
        return dict(DEFAULTS)

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        unknown = set(config) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"Unsupported training setting: {', '.join(sorted(map(str, unknown)))}.")
        epochs = config.get("epochs", DEFAULTS["epochs"])
        if isinstance(epochs, bool) or not isinstance(epochs, int) or not 1 <= epochs <= 200:
            raise ValueError("epochs must be a whole number between 1 and 200.")
        batch = config.get("batch_size", DEFAULTS["batch_size"])
        if isinstance(batch, bool) or not isinstance(batch, int) or not 1 <= batch <= 64:
            raise ValueError("batch_size must be a whole number between 1 and 64.")
        rate = config.get("learning_rate", DEFAULTS["learning_rate"])
        try:
            rate_ok = not isinstance(rate, bool) and isinstance(rate, (int, float)) and 0 < float(rate) <= 1
        except (OverflowError, ValueError, TypeError):
            rate_ok = False
        if not rate_ok:
            raise ValueError("learning_rate must be a number above 0 and at most 1.")
        seed = config.get("seed", DEFAULTS["seed"])
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**31 - 1:
            raise ValueError("seed must be a whole number between 0 and 2147483647.")
        return {
            "epochs": epochs,
            "batch_size": batch,
            "learning_rate": float(rate),
            "seed": seed,
        }

    def runtime_info(self) -> dict[str, Any]:
        torch, _, np = _torch_and_numpy()
        info = {"torch": torch.__version__, "numpy": np.__version__, "device": "cpu"}
        if importlib.util.find_spec("onnxruntime") is not None:
            import onnxruntime as ort

            info["onnxruntime"] = ort.__version__
        return info

    # -- dataset --------------------------------------------------------------------------------
    def build_dataset(self, run, dest: Path) -> tuple[list[ClipEvalItem], list[str]]:
        """Materialize the frozen clips under ``dest`` and return (test items, labels).

        The runner's ``export_entries`` writes sha-verified files under ``entries/<split>/``
        and ``entry_ground_truth`` returns the matching ground truth by position; this hook
        adds the generic export's ``classes.txt`` plus a ``labels.csv`` and collects the test
        split as :class:`ClipEvalItem` objects."""
        from .runner import entry_ground_truth, export_entries

        written = export_entries(run, dest)
        ground_truth = entry_ground_truth(run)
        labels = list(run.dataset_version.label_names)
        (dest / "classes.txt").write_text("\n".join(labels) + "\n", encoding="utf-8")
        with open(dest / "labels.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["filename", "label"])
            for item, path in zip(ground_truth, written):
                writer.writerow([path.relative_to(dest), item["label"]])
        items = [
            ClipEvalItem(path, item["label"], item.get("asset_id"))
            for item, path in zip(ground_truth, written)
            if item["split"] == "test"
        ]
        return items, labels

    # -- training -------------------------------------------------------------------------------
    def train(
        self,
        dataset_dir: Path,
        config: dict[str, Any],
        progress_cb: ProgressCallback,
        cancel_cb: CancelCallback,
    ) -> TrainResult:
        import random

        torch, nn, np = _torch_and_numpy()
        cfg = self.validate_config(config)
        labels = [line for line in (dataset_dir / "classes.txt").read_text(encoding="utf-8").splitlines() if line]
        with open(dataset_dir / "labels.csv", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        samples = [
            (dataset_dir / row["filename"], row["label"]) for row in rows if row["label"] in labels
        ]
        train = [(path, label) for path, label in samples if "/train/" in f"/{path.relative_to(dataset_dir)}"]
        if not train:
            raise ValueError("The train split has no sound clips, so there is nothing to learn from.")

        torch.manual_seed(cfg["seed"])
        shuffler = random.Random(cfg["seed"])
        index_of = {label: i for i, label in enumerate(labels)}
        features = [(clip_features(path), index_of[label]) for path, label in train]

        model = build_model(len(labels))
        model.train()
        criterion = nn.CrossEntropyLoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=cfg["learning_rate"])
        # Small sets need a schedule that keeps stepping while the signal settles: drop the
        # learning rate twice (a mild step schedule, applied per epoch so it is deterministic).
        scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=[8, 16], gamma=0.5)

        batches_per_epoch = -(-len(features) // cfg["batch_size"])
        total_steps = cfg["epochs"] * batches_per_epoch
        step = 0
        history = []
        started = time.monotonic()
        for epoch in range(1, cfg["epochs"] + 1):
            order = list(range(len(features)))
            shuffler.shuffle(order)
            running, count = 0.0, 0
            for start in range(0, len(order), cfg["batch_size"]):
                if cancel_cb():
                    raise TrainingCancelled("Training was cancelled.")
                batch = [features[i] for i in order[start:start + cfg["batch_size"]]]
                inputs = torch.from_numpy(np.concatenate([item[0] for item in batch])).float()
                targets = torch.tensor([item[1] for item in batch], dtype=torch.int64)
                optimizer.zero_grad()
                loss = criterion(model(inputs), targets)
                if not torch.isfinite(loss):
                    raise ValueError("Training diverged (the loss became infinite). Lower learning_rate and try again.")
                loss.backward()
                optimizer.step()
                running += float(loss.detach())
                count += 1
                step += 1
                progress_cb(
                    step / total_steps,
                    f"epoch {epoch}/{cfg['epochs']} step {step}/{total_steps} loss {float(loss.detach()):.4f}",
                )
            scheduler.step()
            history.append({"epoch": epoch, "mean_loss": round(running / max(count, 1), 5)})

        # Artifact: ONNX when onnxruntime is importable here (export only needs torch), else torch.
        # The weights file is named model.bin so the runner's register_candidate finds it next to
        # this metadata sidecar (it copies every file of the artifact folder).
        artifact_dir = dataset_dir.parent / "artifact"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        model.eval()
        dummy = torch.randn(1, 1, N_MELS, TARGET_FRAMES)
        if importlib.util.find_spec("onnxruntime") is not None:
            torch.onnx.export(
                model,
                dummy,
                str(artifact_dir / "model.bin"),
                input_names=["input"],
                output_names=["output"],
                dynamic_axes={"input": {0: "batch", 3: "time"}, "output": {0: "batch"}},
                opset_version=13,
                dynamo=False,
            )
            artifact_format = "onnx"
        else:
            torch.save(model.state_dict(), artifact_dir / "model.bin")
            artifact_format = "torch"
        (artifact_dir / METADATA_NAME).write_text(
            json.dumps(
                {
                    "format": ARTIFACT_FORMAT,
                    "weights": artifact_format,
                    "labels": labels,
                    "n_mels": N_MELS,
                    "n_fft": N_FFT,
                    "hop_length": HOP_LENGTH,
                    "sample_rate": TARGET_SR,
                    "target_frames": TARGET_FRAMES,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return TrainResult(
            artifact_path=artifact_dir,
            metrics={
                "train_clips": len(train),
                "epochs": history,
                "train_seconds": round(time.monotonic() - started, 1),
                "artifact_format": artifact_format,
                "note": "Training loss only; see the held-out evaluation for quality.",
            },
            label_names=labels,
        )

    # -- inference ------------------------------------------------------------------------------
    def _run_network(self, artifact: Path, features) -> list[float]:
        """Logits for one clip's features, through ONNX (onnxruntime) or torch."""
        import numpy as np

        metadata = _load_metadata(artifact)
        weights, _ = _artifact_files(artifact)
        if metadata.get("weights") == "onnx":
            session = _session(artifact)
            input_name = session.get_inputs()[0].name
            return [float(v) for v in session.run(None, {input_name: features.astype(np.float32)})[0][0]]
        torch, _, _ = _torch_and_numpy()
        model = build_model(len(metadata["labels"]))
        model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
        model.eval()
        with torch.no_grad():
            logits = model(torch.from_numpy(features).float())
        return [float(v) for v in logits[0]]

    def classify(self, artifact: Path, clip_path: Path) -> tuple[str, float, dict[str, float]]:
        """One WAV clip: (label, confidence, probabilities)."""
        metadata = _load_metadata(artifact)
        labels = list(metadata["labels"])
        logits = self._run_network(artifact, clip_features(Path(clip_path)))
        probabilities = _softmax(logits)
        best = max(range(len(labels)), key=lambda i: probabilities[i])
        return labels[best], probabilities[best], {name: probabilities[i] for i, name in enumerate(labels)}

    def predict(self, artifact: Path, image, thresholds) -> list:  # pragma: no cover - images are not this task's input
        raise NotImplementedError("This backend classifies sound clips, not pictures.")

    def evaluate(self, artifact: Path, items: list[ClipEvalItem], labels: list[str],
                 thresholds: dict[str, Any]) -> dict[str, Any]:
        """Held-out clip classification: accuracy, per-label precision/recall/F1, confusion counts.

        The keys stay simple and documented (module docstring): the box-detection report does
        not read this shape, and the results card shows the evaluation report instead."""
        if not items:
            return {
                "available": False,
                "reason": "The dataset version's test split has no sound clips, so there is nothing "
                          "held out to measure against.",
                "eval_kind": "clip_classification",
            }
        label_set = set(labels)
        correct = 0
        confusion: dict[str, dict[str, int]] = {label: {name: 0 for name in labels} for label in labels}
        for item in items:
            predicted, _, _ = self.classify(artifact, item.path)
            predicted = predicted if predicted in label_set else (predicted or "")
            if predicted in confusion.get(item.label, {}):
                confusion[item.label][predicted] += 1
            if predicted == item.label:
                correct += 1
        per_label = {}
        for label in labels:
            true_positive = confusion[label].get(label, 0)
            predicted_total = sum(confusion[true].get(label, 0) for true in labels)
            actual_total = sum(confusion[label].values())
            precision = true_positive / predicted_total if predicted_total else None
            recall = true_positive / actual_total if actual_total else None
            f1 = (
                2 * precision * recall / (precision + recall)
                if precision is not None and recall is not None and precision + recall
                else None
            )
            per_label[label] = {
                "clips": actual_total,
                "correct": true_positive,
                "wrong": actual_total - true_positive,
                "precision": round(precision, 4) if precision is not None else None,
                "recall": round(recall, 4) if recall is not None else None,
                "f1": round(f1, 4) if f1 is not None else None,
            }
        limits = [f"Only {len(items)} held-out clip(s) in total: treat these figures as a rough indication."]
        return {
            "available": True,
            "reason": "",
            "split": "test",
            "eval_kind": "clip_classification",
            "accuracy": round(correct / len(items), 4),
            "clips_correct": correct,
            "per_label": per_label,
            "confusion": confusion,
            "data": {"clips": len(items)},
            "limits": limits,
        }

    def predict_split(self, artifact: Path, items, labels, thresholds):
        """No operating-point pass for clips (confidence thresholds are a box-detection tool)."""
        return None
