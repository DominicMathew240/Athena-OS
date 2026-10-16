"""COCO and YOLO export writers plus a streaming, path-safe zip.

Every path inside the archive is generated here (``images/<split>/<n>_<sha12>.<ext>``);
no user-supplied filename, label or storage key ever becomes a path component.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterator

from django.core.files.storage import default_storage

from .splits import SPLIT_NAMES

CHUNK = 1024 * 1024
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
Q9 = Decimal("0.000000001")


class ExportIntegrityError(Exception):
    """A file changed while it was being streamed; the download is aborted rather than corrupted."""


@dataclass
class TextFile:
    path: str
    data: bytes


@dataclass
class ImageFile:
    path: str
    storage_key: str
    sha256: str


def image_name(index: int, image: dict[str, Any]) -> str:
    ext = EXTENSIONS.get(image["content_type"], "bin")
    return f"{index:06d}_{image['sha256'][:12]}.{ext}"


def _by_split(images: list[dict[str, Any]]) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {name: [] for name in SPLIT_NAMES}
    for image in images:
        grouped[image["split"]].append(image)
    return {
        split: list(enumerate(items, start=1)) for split, items in grouped.items()
    }


def _json_bytes(document: Any) -> bytes:
    return (json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def build_coco(dataset: dict[str, Any], images: list[dict[str, Any]], storage_keys: dict[str, str]) -> list[Any]:
    labels = dataset["label_names"]
    category_id = {label: index for index, label in enumerate(labels, start=1)}
    categories = [{"id": index, "name": label, "supercategory": ""} for label, index in category_id.items()]
    info = {
        "description": f"{dataset['project']['name']} (Athena OS dataset v{dataset['version']})",
        "version": str(dataset["version"]),
        "year": int(dataset["created_at"][:4]),
        "date_created": dataset["created_at"],
        "contributor": "Athena OS",
        "athena_version": dataset["athena_version"],
        "project_id": dataset["project"]["id"],
        "project_name": dataset["project"]["name"],
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["version"],
        "manifest_sha256": dataset["_manifest_sha256"],
        "bbox_format": "[x, y, width, height] in absolute pixels from the top-left corner",
    }
    files: list[Any] = []
    image_id = 0
    annotation_id = 0
    for split, entries in _by_split(images).items():
        coco_images, coco_annotations = [], []
        for index, image in entries:
            image_id += 1
            name = image_name(index, image)
            coco_images.append(
                {"id": image_id, "file_name": name, "width": image["width"], "height": image["height"]}
            )
            files.append(ImageFile(f"images/{split}/{name}", storage_keys[image["asset_id"]], image["sha256"]))
            for box in image["boxes"]:
                annotation_id += 1
                x, y, w, h = (
                    Decimal(box[key]) * image[axis]
                    for key, axis in (("x", "width"), ("y", "height"), ("width", "width"), ("height", "height"))
                )
                coco_annotations.append(
                    {
                        "id": annotation_id,
                        "image_id": image_id,
                        "category_id": category_id[box["label"]],
                        "bbox": [float(x), float(y), float(w), float(h)],
                        "area": float(w * h),
                        "iscrowd": 0,
                    }
                )
        document = {
            "info": {**info, "split": split},
            "licenses": [],
            "categories": categories,
            "images": coco_images,
            "annotations": coco_annotations,
        }
        files.append(TextFile(f"annotations/instances_{split}.json", _json_bytes(document)))
    return files


def build_yolo(dataset: dict[str, Any], images: list[dict[str, Any]], storage_keys: dict[str, str]) -> list[Any]:
    labels = dataset["label_names"]
    class_index = {label: index for index, label in enumerate(labels)}
    files: list[Any] = []
    present = []
    for split, entries in _by_split(images).items():
        if entries:
            present.append(split)
        for index, image in entries:
            name = image_name(index, image)
            stem = name.rsplit(".", 1)[0]
            files.append(ImageFile(f"images/{split}/{name}", storage_keys[image["asset_id"]], image["sha256"]))
            lines = []
            for box in image["boxes"]:
                x, y, w, h = (Decimal(box[key]) for key in ("x", "y", "width", "height"))
                values = (x + w / 2, y + h / 2, w, h)  # exact in Decimal; 9 places is lossless
                lines.append(
                    f"{class_index[box['label']]} " + " ".join(format(v.quantize(Q9), "f") for v in values)
                )
            files.append(TextFile(f"labels/{split}/{stem}.txt", ("\n".join(lines) + "\n").encode("ascii")))
    files.append(TextFile("classes.txt", ("\n".join(labels) + "\n").encode("utf-8")))
    yaml = ["# Generated by Athena OS", "path: ."]
    yaml += [f"{split}: images/{split}" for split in present]
    yaml += [f"nc: {len(labels)}", f"names: {json.dumps(labels, ensure_ascii=True)}"]
    files.append(TextFile("data.yaml", ("\n".join(yaml) + "\n").encode("ascii")))
    return files


WRITERS = {"coco": build_coco, "yolo": build_yolo}


def build_files(fmt: str, document: dict[str, Any], manifest_sha256: str, storage_keys: dict[str, str]) -> list[Any]:
    """Return the ordered files of an export. ``document`` is the version's stored manifest."""
    dataset = {**document, "_manifest_sha256": manifest_sha256}
    return WRITERS[fmt](dataset, document["images"], storage_keys)


class _Sink:
    """Write-only buffer so ZipFile can stream without seeking."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> int:
        self.buffer += data
        return len(data)

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        data = bytes(self.buffer)
        self.buffer.clear()
        return data


def _info(path: str, compress: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, ZIP_DATE)
    info.compress_type = compress
    info.external_attr = 0o644 << 16
    return info


def stream_zip(fmt: str, document: dict[str, Any], manifest_sha256: str, files: list[Any]) -> Iterator[bytes]:
    sink = _Sink()
    listing: list[dict[str, Any]] = []
    with zipfile.ZipFile(sink, "w", allowZip64=True) as archive:
        for item in files:
            if isinstance(item, TextFile):
                archive.writestr(_info(item.path, zipfile.ZIP_DEFLATED), item.data)
                listing.append(
                    {"path": item.path, "bytes": len(item.data), "sha256": hashlib.sha256(item.data).hexdigest()}
                )
                yield sink.drain()
                continue
            digest = hashlib.sha256()
            size = 0
            with default_storage.open(item.storage_key, "rb") as source:
                with archive.open(_info(item.path, zipfile.ZIP_STORED), "w", force_zip64=True) as target:
                    for chunk in iter(lambda: source.read(CHUNK), b""):
                        digest.update(chunk)
                        size += len(chunk)
                        target.write(chunk)
                        yield sink.drain()
            if digest.hexdigest() != item.sha256:
                raise ExportIntegrityError(f"{item.path} changed while it was being exported.")
            listing.append({"path": item.path, "bytes": size, "sha256": item.sha256})
        manifest_document = {
            "manifest_sha256": manifest_sha256,
            "manifest_sha256_covers": "SHA-256 of the canonical JSON (sorted keys, compact separators, UTF-8) of `dataset`.",
            "dataset": document,
            "export": {"format": fmt, "files": sorted(listing, key=lambda entry: entry["path"])},
        }
        archive.writestr(_info("manifest.json", zipfile.ZIP_DEFLATED), _json_bytes(manifest_document))
    yield sink.drain()
