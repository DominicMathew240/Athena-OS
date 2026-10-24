"""Data health: what the pictures and boxes of a project would give a teaching run. Read-only.

Answers, before anyone teaches the computer anything:

* how many accepted examples each label has, and whether that is enough to learn from;
* whether any pictures would be held back for the honest final score, and how many boxes of each label
  that group would hold;
* which pictures look almost the same (so a score measured on one of them would flatter the model that
  learned from its twin).

Only accepted boxes count, exactly as in a dataset snapshot. Boxes marked "Needs review" are reported
and never counted. Nothing here writes a row or a file (the one thing kept is the in-process memory of
``validation.file_problem(cached=True)``, which remembers that a file matched its checksum until its size or
modification time changes). The split is computed with ``datasets.splits`` and the same default configuration a
snapshot uses, without creating a version.

Near-duplicate method (``dhash-9x8``): each picture is turned into grey, shrunk to 9x8 cells by
averaging, and each cell is compared with its right neighbour (64 yes/no bits). Two pictures whose bits
differ in at most ``HAMMING_THRESHOLD`` places, and whose overall brightness is close, count as
"nearly identical". This catches re-saved, re-compressed, slightly noisy or slightly re-lit copies. It
does NOT catch rotated, mirrored, noticeably cropped or re-framed copies, and it is weak on pictures
with almost no structure. It has only been tried on generated pictures. Groups are single-link: A~B and
B~C put A, B and C together even if A and C alone would not match. 16-bit pictures are scaled to 8 bit
before hashing. A picture is listed as not compared (never hidden) when its file is missing or unreadable, when its
stored size is over ``MAX_PICTURE_PIXELS`` (not opened at all), or when it was not reached within ``MAX_HASH_SECONDS``
(one budget for the whole request: the checksum re-check and the hashing draw on the same clock).
"""

from __future__ import annotations

import array
import bisect
import math
import time
from collections import defaultdict
from typing import Any, Iterable, Iterator

from django.core.exceptions import SuspiciousOperation
from django.core.files.storage import default_storage
from PIL import Image

from ..models import Asset, BoxAnnotation, Project
from ..training import evaluation as test_side
from . import max_images, service, splits, validation
from .service import ACCEPTED, SUGGESTED  # the one definition of the two statuses (this module's old names stay)

# --- Thresholds (all advice; none of them is a measured rule) ------------------------------------
# docs/guides/model-quality.md gives no minimum for LEARNING; it only discusses the held-back group.
# 30 TRAIN boxes per label is a judgement call that reuses that guide's ~30 figure: below it, one
# unusual picture can decide what the computer thinks the label looks like. It is not validated here. It is
# deliberately its own number, not an alias of the test-side ones below: it could change without them.
MIN_TRAIN_BOXES_ENOUGH = 30
# The TEST-side numbers are the ones the results page uses, so they are read from training/evaluation.py (their
# one home) under the names this response has always exposed:
#   about 30 Final test boxes per label for a number worth reading (model-quality.md, Minimum quantities),
#   a warning below 10 test boxes per label, a warning below 30 test pictures.
MIN_TEST_BOXES_READABLE = test_side.MIN_BOXES_PER_LABEL_FOR_VERDICT
MIN_TEST_BOXES_FLOOR = test_side.MIN_BOXES_PER_LABEL
MIN_TEST_PICTURES = test_side.MIN_IMAGES_FOR_CONFIDENCE

# --- Near-duplicate detection --------------------------------------------------------------------
METHOD = "dhash-9x8"
HASH_COLUMNS, HASH_ROWS = 9, 8  # 8 comparisons per row x 8 rows = 64 bits
HASH_BITS = (HASH_COLUMNS - 1) * HASH_ROWS
# 6 of 64 bits (about 9%), picked from measurements on generated 160x128 scenes: JPEG re-encoding and mild noise
# moved 1-3 bits, a 5% border crop 5, a 5-degree turn 10, a 90-degree turn 28, and 780 pairs of unrelated scenes
# differed by 11-47 bits (mean 26), none by 6 or fewer. So 6 keeps re-saved and slightly cropped copies together
# and leaves a gap of five bits before the nearest unrelated pair. Not tuned on real imagery.
HAMMING_THRESHOLD = 6
# A cell must beat its neighbour by more than this many grey levels to set a bit. Without it, sensor
# noise decides the bits of featureless areas and a noisy copy of a flat picture would not match.
DEADBAND = 1
# Pictures with the same bits but very different overall brightness (all-black vs all-white frames both
# hash to zero) are not copies. Mean grey level (0-255) must be within this.
MAX_MEAN_GREY_DIFFERENCE = 32
# Hashing decodes every picture; this keeps one request bounded. Pictures beyond it are listed, not hidden.
MAX_PICTURES_HASHED = 2000
# Decoding time grows with picture size (about 0.04 s for a 3000x2000 PNG; JPEGs are decoded at reduced size
# and are far faster). Pictures are hashed in a fixed order (by id); once this many wall-clock seconds have
# gone, the rest are listed in ``not_checked`` with reason ``time`` instead of making the request wait. ONE
# budget covers the whole request: the re-check that stored files still match their recorded checksum runs
# first and the hashing gets what is left, so a request cannot take twice as long as this.
MAX_HASH_SECONDS = 4
# A picture whose stored width x height is over this is not opened at all (``not_checked`` reason ``too_large``):
# uploads are capped at the same figure (views.MAX_IMAGE_PIXELS, pinned by a test), so a bigger one is a row that
# no longer matches its file or a file put there by hand, and decoding it could take long and a lot of memory.
MAX_PICTURE_PIXELS = 25_000_000
_clock = time.monotonic  # replaced in tests
LIST_LIMIT = 200  # ids listed per list in the response (counts are always exact)


# --- picture hashing -----------------------------------------------------------------------------
def _grey_cells(picture: Image.Image) -> list[int]:
    """The 9x8 cell averages of a picture as grey levels 0-255.

    16-bit (``I;16*``) and 32-bit integer (``I``) pictures are read as 0-65535 and scaled down to 8 bit
    (divided by 256); converting them straight to grey would clip everything above 255 to white. Floating
    point pictures (``F``) and integer pictures outside 0-65535 have no fixed scale, so their own lowest and
    highest value are stretched to 0 and 255 (a flat one keeps its value, clipped).
    """
    size = (HASH_COLUMNS, HASH_ROWS)
    mode = picture.mode
    if mode != "F" and mode != "I" and not mode.startswith("I;16"):
        return list(picture.convert("L").resize(size, Image.Resampling.BOX).tobytes())
    if mode == "F":
        values = list(array.array("f", picture.resize(size, Image.Resampling.BOX).tobytes()))
    else:
        values = list(array.array("i", picture.convert("I").resize(size, Image.Resampling.BOX).tobytes()))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("picture holds values that are not finite numbers")
    low, high = min(values), max(values)
    if mode != "F" and low >= 0 and high <= 65535:
        scaled = [int(value) // 256 for value in values]
    elif high > low:
        scaled = [round((value - low) * 255 / (high - low)) for value in values]
    else:
        scaled = [round(value) for value in values]
    return [min(255, max(0, value)) for value in scaled]


def dhash(handle) -> tuple[int, int]:
    """(64-bit difference hash, mean grey level) of an image file object. Raises on unreadable data."""
    with Image.open(handle) as picture:
        picture.draft("L", (HASH_COLUMNS * 8, HASH_ROWS * 8))  # JPEG: decode at reduced size, no-op elsewhere
        cells = _grey_cells(picture)
    value = 0
    for row in range(HASH_ROWS):
        line = cells[row * HASH_COLUMNS : (row + 1) * HASH_COLUMNS]
        for column in range(HASH_COLUMNS - 1):
            value = (value << 1) | (1 if line[column] - line[column + 1] > DEADBAND else 0)
    return value, round(sum(cells) / len(cells))


def _hash_of_stored(storage_key: str) -> tuple[int, int] | str:
    """A hash, or the reason it could not be made: ``"missing"`` or ``"unreadable"``."""
    try:
        with default_storage.open(storage_key, "rb") as handle:
            return dhash(handle)
    except (FileNotFoundError, IsADirectoryError):
        return "missing"
    except (OSError, ValueError, OverflowError, SyntaxError, SuspiciousOperation, Image.DecompressionBombError):
        return "unreadable"


def near_pairs(hashes: dict[str, tuple[int, int]], threshold: int) -> Iterator[tuple[str, str]]:
    """Every pair of ids whose hashes differ in at most ``threshold`` bits and whose brightness is close.

    Pigeonhole: cut the 64 bits into ``threshold + 1`` slices; two hashes that differ in at most
    ``threshold`` bits must agree exactly on at least one slice, so only pictures sharing a slice
    value need comparing. Same answer as comparing every pair, without the quadratic cost for pictures that
    merely differ. It still LISTS every pair, so n identical pictures give n(n-1)/2 of them: the check itself uses
    ``near_groups``, which answers the question it needs (who is connected to whom) without listing pairs.
    """
    slices = threshold + 1
    edges = [round(i * HASH_BITS / slices) for i in range(slices + 1)]
    ordered = sorted(hashes)  # once, not once per slice
    seen: set[tuple[str, str]] = set()
    for low, high in zip(edges, edges[1:]):
        mask = (1 << (high - low)) - 1
        buckets: dict[int, list[str]] = defaultdict(list)
        for key in ordered:
            buckets[(hashes[key][0] >> low) & mask].append(key)
        for members in buckets.values():
            for index, first in enumerate(members):
                bits, grey = hashes[first]
                for second in members[index + 1 :]:
                    if (first, second) in seen:
                        continue
                    other_bits, other_grey = hashes[second]
                    if abs(grey - other_grey) <= MAX_MEAN_GREY_DIFFERENCE and (bits ^ other_bits).bit_count() <= threshold:
                        seen.add((first, second))
                        yield first, second


def near_groups(hashes: dict[str, tuple[int, int]], threshold: int) -> list[list[str]]:
    """The groups (size >= 2, ids sorted, groups sorted) that single-link ``near_pairs`` would give, found without
    listing the pairs.

    1. Pictures with the SAME 64 bits are joined in one pass over their brightness values: two of them are
       neighbours exactly when their brightness differs by at most ``MAX_MEAN_GREY_DIFFERENCE``, so after sorting by
       brightness the connected runs are the stretches whose consecutive gaps stay within it. n identical pictures
       cost O(n log n), not n^2 pairs.
    2. Each run is then one node. Different nodes are compared only if they share a slice value (pigeonhole, as in
       ``near_pairs``), and inside a slice a node is tested against the members of a group it has not joined yet
       only until the first neighbour is found, so a dense cluster costs about nodes x groups, not nodes^2.
    """
    ordered = sorted(hashes)  # once
    parent = {key: key for key in ordered}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def join(first: str, second: str) -> None:
        parent[find(first)] = find(second)

    by_bits: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for key in ordered:
        bits, grey = hashes[key]
        by_bits[bits].append((grey, key))
    nodes: list[tuple[int, list[int], str]] = []  # (bits, sorted brightness values, one member id)
    for bits, members in by_bits.items():
        members.sort()
        start = 0
        for index in range(1, len(members) + 1):
            if index == len(members) or members[index][0] - members[index - 1][0] > MAX_MEAN_GREY_DIFFERENCE:
                run = members[start:index]
                for _, key in run[1:]:
                    join(key, run[0][1])
                nodes.append((bits, [grey for grey, _ in run], run[0][1]))
                start = index

    node_bits = [node[0] for node in nodes]

    def close_in_brightness(first: int, second: int) -> bool:
        small, large = sorted((nodes[first][1], nodes[second][1]), key=len)
        for grey in small:
            at = bisect.bisect_left(large, grey - MAX_MEAN_GREY_DIFFERENCE)
            if at < len(large) and large[at] <= grey + MAX_MEAN_GREY_DIFFERENCE:
                return True
        return False

    slices = threshold + 1
    edges = [round(i * HASH_BITS / slices) for i in range(slices + 1)]
    for low, high in zip(edges, edges[1:]):
        mask = (1 << (high - low)) - 1
        buckets: dict[int, list[int]] = defaultdict(list)
        for index, bits in enumerate(node_bits):
            buckets[(bits >> low) & mask].append(index)
        for members in buckets.values():
            if len(members) < 2:
                continue
            classes: list[list[int]] = []  # nodes of this slice value already known to be connected
            for index in members:
                bits = node_bits[index]
                joined: list[list[int]] = []
                for group in classes:
                    for other in group:
                        if (bits ^ node_bits[other]).bit_count() <= threshold and close_in_brightness(index, other):
                            joined.append(group)
                            break  # one neighbour is enough to join this group
                if not joined:
                    classes.append([index])
                    continue
                for group in joined:
                    join(nodes[index][2], nodes[group[0]][2])
                merged = [index] + [other for group in joined for other in group]
                classes = [group for group in classes if all(group is not j for j in joined)] + [merged]
    groups: dict[str, list[str]] = defaultdict(list)
    for key in ordered:
        groups[find(key)].append(key)
    return sorted(group for group in groups.values() if len(group) > 1)


def _components(ids: Iterable[str], pairs: Iterable[tuple[str, str]]) -> list[list[str]]:
    """Union-find: connected groups of size >= 2, ids sorted, groups sorted."""
    parent = {key: key for key in ids}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for first, second in pairs:
        parent[find(first)] = find(second)
    groups: dict[str, list[str]] = defaultdict(list)
    for key in parent:
        groups[find(key)].append(key)
    return sorted(sorted(group) for group in groups.values() if len(group) > 1)


# --- wording ---------------------------------------------------------------------------------------
def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _label_text(label: str, accepted: int, train: int, test: int, val: int) -> tuple[str, str]:
    """(verdict, message) about learning from this label."""
    if accepted == 0:
        return "none", (
            f"Nothing is boxed as '{label}' yet, so the computer cannot learn it. "
            f"Draw boxes around '{label}' in your pictures, or leave it out of the project's labels."
        )
    held = val + test
    detail = f"{train} to learn from, {held} held back for checking and scoring"
    short_detail = f"{held} held back for checking and scoring"
    if train == 0:
        return "none", (
            f"'{label}' has {_n(accepted, 'boxed example')}, but all of them would be held back for checking "
            f"and scoring, so none is left to learn from. Add more '{label}' examples."
        )
    if train < MIN_TRAIN_BOXES_ENOUGH:
        count = (
            f"only {_n(accepted, 'boxed example')}"
            if accepted < MIN_TRAIN_BOXES_ENOUGH
            else f"{_n(accepted, 'boxed example')}, but only {train} would be used to learn from"
        )
        return "few", (
            f"'{label}' has {count} ({detail if accepted < MIN_TRAIN_BOXES_ENOUGH else short_detail}). "
            f"Aim for at least {MIN_TRAIN_BOXES_ENOUGH} to learn from; with fewer, the computer may learn the "
            "look of a few pictures rather than the label."
        )
    return "enough", f"'{label}' has {_n(accepted, 'boxed example')} ({detail}), enough to start with."


def _held_out_text(label: str, test: int) -> tuple[str, str]:
    """(verdict, message) about the held-back boxes of this label."""
    examples = _n(test, f"'{label}' example")
    if test == 0:
        return "none", (
            f"No '{label}' example would be held back for the final test, so the score cannot say "
            f"how well '{label}' is found."
        )
    if test < MIN_TEST_BOXES_FLOOR:
        return "few", (
            f"Only {examples} would be held back for the final test. One or two mistakes would swing the "
            f"score; about {MIN_TEST_BOXES_READABLE} are needed for a number worth reading."
        )
    if test < MIN_TEST_BOXES_READABLE:
        return "few", (
            f"{examples} would be held back for the final test. The score for '{label}' will be rough; "
            f"about {MIN_TEST_BOXES_READABLE} are needed for a number worth reading."
        )
    return "enough", (
        f"{test} '{label}' examples would be held back for the final test, enough for a number worth reading "
        "(not a guarantee that it holds on new pictures)."
    )


# --- the check ---------------------------------------------------------------------------------------
def build(project: Project, config: splits.SplitConfig | None = None) -> dict[str, Any]:
    config = config or splits.SplitConfig.default()
    started = _clock()  # the one time budget of this request (MAX_HASH_SECONDS), shared by every file read below
    boxes = list(service.accepted_boxes(project))
    assets: dict[Any, Asset] = {}
    for box in boxes:
        assets.setdefault(box.asset_id, box.asset)
    total_pictures = project.assets.count()
    needs_review = BoxAnnotation.objects.filter(asset__project=project, status=SUGGESTED).count()

    split_of_sha = splits.assign_splits((asset.sha256 for asset in assets.values()), config)
    split_of_asset = {asset_id: split_of_sha[asset.sha256] for asset_id, asset in assets.items()}
    picture_counts = {name: 0 for name in splits.SPLIT_NAMES}
    for name in split_of_asset.values():
        picture_counts[name] += 1

    # Per label: boxes by group, and the pictures they sit on.
    per_label: dict[str, dict[str, int]] = {}
    label_pictures: dict[str, set] = defaultdict(set)
    for box in boxes:
        row = per_label.setdefault(box.label, {name: 0 for name in splits.SPLIT_NAMES})
        row[split_of_asset[box.asset_id]] += 1
        label_pictures[box.label].add(box.asset_id)

    blockers: list[str] = []
    advice: list[str] = []
    missing_ids: set[str] = set()

    # --- labels --------------------------------------------------------------------------------------
    names = [label for label in project.labels if isinstance(label, str)]
    names += sorted(label for label in per_label if label not in names)
    labels_out = []
    few_held_out: list[str] = []
    for label in names:
        by_split = per_label.get(label, {name: 0 for name in splits.SPLIT_NAMES})
        accepted = sum(by_split.values())
        verdict, message = _label_text(label, accepted, by_split["train"], by_split["test"], by_split["val"])
        held_verdict, held_message = _held_out_text(label, by_split["test"])
        if label not in project.labels:
            message = f"'{label}' is used on boxes but is not in the project's label list. " + message
        labels_out.append(
            {
                "label": label,
                "accepted_boxes": accepted,
                "pictures": len(label_pictures.get(label, ())),
                "verdict": verdict,
                "message": message,
                "expected_boxes": dict(by_split),
                "held_out_verdict": held_verdict,
                "held_out_message": held_message,
            }
        )
        if verdict != "enough":
            advice.append(message)
        if accepted and held_verdict != "enough":
            few_held_out.append(f"{label} ({by_split['test']})")
    if boxes and few_held_out:
        advice.append(
            f"The final test would hold fewer than {MIN_TEST_BOXES_READABLE} boxes for: {', '.join(few_held_out)}. "
            "Scores for those labels would be rough."
        )

    # --- pictures ------------------------------------------------------------------------------------
    pictures = {
        "total": total_pictures,
        "with_boxes": len(assets),
        "without_boxes": total_pictures - len(assets),
        "needs_review_boxes": needs_review,
    }
    if needs_review:
        advice.append(
            f"{_n(needs_review, 'box', 'boxes')} still {'says' if needs_review == 1 else 'say'} Needs review. "
            "Boxes in that state are not used until someone accepts or removes them."
        )
    if assets and pictures["without_boxes"]:
        left_out = pictures["without_boxes"]
        advice.append(
            f"{_n(left_out, 'picture')} {'has' if left_out == 1 else 'have'} no accepted boxes and will be left out."
        )

    # --- the held-back group -------------------------------------------------------------------------
    test_pictures = picture_counts["test"]
    if not assets:
        held_message = "Nothing to hold back yet: no picture has an accepted box."
    elif config.test <= 0:
        held_message = (
            "The split you chose keeps no pictures for the final test, so there would be no honest score. "
            "Give the final test a share above 0."
        )
    elif test_pictures == 0:
        held_message = (
            "No pictures would be held back for the honest score. "
            f"{' '.join(splits.shortfall_notes(len(set(split_of_sha)), config)) or 'Add more pictures with boxes.'}"
        )
    elif test_pictures < MIN_TEST_PICTURES:
        held_message = (
            f"{_n(test_pictures, 'picture')} would be held back for the final test - fewer than "
            f"{MIN_TEST_PICTURES}, so a few mistakes can swing the score."
        )
    else:
        held_message = f"{test_pictures} pictures would be held back for the final test."
    if assets and test_pictures < MIN_TEST_PICTURES:
        advice.append(held_message)
    held_out = {
        "would_have_test_pictures": test_pictures > 0,
        "expected_counts": picture_counts,
        "split_config": {**config.as_dict(), "split_algorithm": splits.ALGORITHM},
        "message": held_message,
    }

    # --- blockers a snapshot would also hit (no file hashing; the readiness check does that) -----------
    if not boxes:
        blockers.append(
            "No picture has an accepted box yet. Draw a box around something, or accept a suggested one, "
            "before teaching. Boxes marked Needs review do not count."
        )
    else:
        outside_list = sum(1 for box in boxes if validation.label_problem(box.label, project.labels))
        bad_shape = sum(1 for box in boxes if validation.geometry_problem(box))
        if outside_list:
            blockers.append(
                f"{_n(outside_list, 'box', 'boxes')} use a label that is not in the project's label list "
                "(spelling and capital letters must match exactly). Fix them in the picture editor."
            )
        if bad_shape:
            blockers.append(f"{_n(bad_shape, 'box', 'boxes')} reach outside their picture or have no size. Redraw them.")
        snapshot_lines, missing_ids, unverified = _snapshot_refusals(
            project, config, boxes, assets, total_pictures, needs_review, started
        )
        blockers.extend(snapshot_lines)
        if unverified:
            advice.append(
                f"The file contents of {_n(unverified, 'picture')} {'was' if unverified == 1 else 'were'} not re-checked "
                f"against the checksum recorded at upload because the check ran out of time (it stops after "
                f"{MAX_HASH_SECONDS} seconds). Run it again; files already checked are remembered and answer faster."
            )
        # Nothing to learn from: a snapshot would be made, but a teaching run on it would have no pictures
        # (or no boxes) in the learning group.
        if picture_counts["train"] == 0 or all(row["verdict"] == "none" for row in labels_out):
            if config.train <= 0:
                blockers.append(
                    "The split you chose keeps no pictures to learn from, so the computer would have nothing to "
                    "study. Give learning a share above 0."
                )
            else:
                blockers.append(
                    "With these pictures and boxes there would be nothing to learn from: no accepted box would be "
                    "left in the group the computer studies after some are held back for checking and scoring. "
                    "Add more pictures or boxes."
                )

    # --- near duplicates -----------------------------------------------------------------------------------
    near, file_blockers, near_advice = _near_duplicates(assets, split_of_asset, missing_ids if boxes else set(), started)
    blockers.extend(file_blockers)
    advice.extend(near_advice)

    no_held_out = (
        f"{held_message} A model taught on this would have no results to read, so it could not be approved."
        if assets and test_pictures == 0
        else None
    )
    priority_notes = _priority_notes(labels_out, near, no_held_out)
    return {
        "labels": labels_out,
        "pictures": pictures,
        "held_out": held_out,
        "near_duplicates": near,
        "ready_to_teach": not blockers,
        "reasons": blockers + advice,
        "blockers": blockers,
        "advice": advice,
        "thresholds": thresholds(),
        "priority_notes": priority_notes,
        "headline": _headline(blockers, priority_notes),
    }


def _list_names(names: list[str], limit: int = 5) -> str:
    quoted = [f"'{name}'" for name in names[:limit]]
    if len(names) > limit:
        quoted.append(f"{len(names) - limit} more")
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def _priority_notes(
    labels_out: list[dict[str, Any]], near: dict[str, Any], no_held_out: str | None = None
) -> list[str]:
    """The few things worth reading before the rest of the advice, as plain sentences (at most 3, in this order).

    1. near-identical pictures that would be split between learning and the final test: this inflates the score
       and nothing else in the response is as likely to mislead;
    2. no picture would be held back for the final test (``no_held_out`` is the sentence): the model could be
       taught but never staged or approved;
    3. labels with no accepted box at all: the computer cannot learn them;
    4. the label with the fewest examples (when it is short of the learning figure).

    Each sentence is also still in ``advice``/``reasons`` (this list repeats, it does not move them).
    """
    notes: list[str] = []
    crossing = near["groups_crossing_splits"]
    if crossing:
        notes.append(
            f"{_n(crossing, 'group')} of nearly identical pictures would be split between learning and the final "
            "test, which would make the score look better than it is. Keep one picture from each group before you "
            "rely on the score."
        )
    if no_held_out:
        notes.append(no_held_out)
    empty = [row["label"] for row in labels_out if row["accepted_boxes"] == 0]
    if empty:
        notes.append(
            f"Nothing is boxed as {_list_names(empty)} yet, so the computer cannot learn "
            f"{'it' if len(empty) == 1 else 'them'}. Draw boxes for {'it' if len(empty) == 1 else 'them'}, "
            "or leave "
            f"{'it' if len(empty) == 1 else 'them'} out of the project's labels."
        )
    short = [row for row in labels_out if row["accepted_boxes"] and row["verdict"] != "enough"]
    if short:
        row = min(short, key=lambda row: (row["accepted_boxes"], row["label"]))
        notes.append(
            f"'{row['label']}' has the fewest examples: {_n(row['accepted_boxes'], 'boxed example')}, "
            f"{row['expected_boxes']['train']} of them to learn from. Aim for at least {MIN_TRAIN_BOXES_ENOUGH} to "
            "learn from."
        )
    return notes[:3]


def _headline(blockers: list[str], priority_notes: list[str]) -> dict[str, str]:
    """``not_ready`` when anything blocks a snapshot; ``ready_with_warnings`` when it does not but a priority note
    exists; else ``ready``."""
    if blockers:
        return {"level": "not_ready", "text": f"Not ready to teach yet. {blockers[0]}"}
    if priority_notes:
        return {"level": "ready_with_warnings", "text": "Ready to teach, but read the warnings below first."}
    return {"level": "ready", "text": "Ready to teach."}


def thresholds() -> dict[str, Any]:
    return {
        "min_train_boxes_enough": MIN_TRAIN_BOXES_ENOUGH,
        "min_test_boxes_readable": MIN_TEST_BOXES_READABLE,
        "min_test_boxes_floor": MIN_TEST_BOXES_FLOOR,
        "min_test_pictures": MIN_TEST_PICTURES,
        "hamming_threshold": HAMMING_THRESHOLD,
        "hash_bits": HASH_BITS,
        "max_mean_grey_difference": MAX_MEAN_GREY_DIFFERENCE,
        "max_pictures_hashed": MAX_PICTURES_HASHED,
        "max_hash_seconds": MAX_HASH_SECONDS,
        "max_picture_pixels": MAX_PICTURE_PIXELS,
    }


def _snapshot_refusals(
    project: Project,
    config: splits.SplitConfig,
    boxes: list[BoxAnnotation],
    assets: dict[Any, Asset],
    total_pictures: int,
    needs_review: int,
    started: float | None = None,
) -> tuple[list[str], set[str], int]:
    """Blockers a snapshot would raise beyond label/box shape, worded exactly as ``/datasets/readiness`` words them.

    Returns (sentences, ids of pictures whose file is missing, count of pictures not re-checked for lack of time).
    Same checks as ``service.analyse``: label characters, picture count, and the stored file against the
    checksum recorded at upload (``file_problem(..., cached=True)``: a file is hashed once, then a size and
    modification-time check answers until it changes).
    """
    problems = list(validation.unsafe_label_problems(project.labels))
    if len(assets) > max_images():
        problems.append(validation.Problem("too_many_images", f"A dataset version can hold at most {max_images()} images."))
    missing: set[str] = set()
    unverified = 0
    started = _clock() if started is None else started
    for asset in sorted(assets.values(), key=lambda asset: str(asset.id)):
        if unverified or _clock() - started >= MAX_HASH_SECONDS:
            unverified += 1
            continue
        found = validation.file_problem(asset.storage_key, asset.sha256, str(asset.id), cached=True)
        if found and found.code == "hash_mismatch":
            problems.append(found)
        elif found:
            missing.add(str(asset.id))
    # The sentences live in service.Analysis.blockers; only the codes added here are present, so only those are worded.
    wording = service.Analysis(
        project=project,
        config=config,
        accepted=boxes,
        assets=assets,
        suggested_boxes=needs_review,
        images_total=total_pictures,
        images_without_accepted=total_pictures - len(assets),
        problems=problems,
    )
    return wording.blockers, missing, unverified


def _near_duplicates(
    assets: dict[Any, Asset],
    split_of_asset: dict[Any, str],
    known_missing: set[str] = frozenset(),
    started: float | None = None,
):
    """(response block, blockers, advice) for the pictures that would enter a snapshot.

    ``started`` is when the request's time budget began (``build`` passes it so the checksum re-check and this
    hashing share one budget); alone, the budget starts now."""
    ordered = sorted(assets.values(), key=lambda asset: str(asset.id))
    hashes: dict[str, tuple[int, int]] = {}
    not_checked: list[dict[str, str]] = []
    started = _clock() if started is None else started
    out_of_time = False
    for attempted, asset in enumerate(ordered):
        key = str(asset.id)
        if attempted >= MAX_PICTURES_HASHED:
            not_checked.append({"asset_id": key, "reason": "skipped_limit"})
            continue
        if (asset.width or 0) * (asset.height or 0) > MAX_PICTURE_PIXELS:
            not_checked.append({"asset_id": key, "reason": "too_large"})
            continue
        out_of_time = out_of_time or _clock() - started >= MAX_HASH_SECONDS
        if out_of_time:
            not_checked.append({"asset_id": key, "reason": "time"})
            continue
        result = _hash_of_stored(asset.storage_key)
        if isinstance(result, str):
            not_checked.append({"asset_id": key, "reason": result})
        else:
            hashes[key] = result

    sha_of = {str(asset.id): asset.sha256 for asset in ordered}
    by_sha: dict[str, list[str]] = defaultdict(list)
    for key, sha in sha_of.items():
        by_sha[sha].append(key)
    identical = sorted(sorted(group) for group in by_sha.values() if len(group) > 1)

    components = near_groups(hashes, HAMMING_THRESHOLD)
    # A component of byte-identical files is already kept together by the splitter; it is not a leak.
    groups = [group for group in components if len({sha_of[key] for key in group}) > 1]
    split_of_key = {str(asset_id): name for asset_id, name in split_of_asset.items()}
    crossing = sum(1 for group in groups if len({split_of_key[key] for key in group}) > 1)
    involved = sum(len(group) for group in groups)

    missing = [row for row in not_checked if row["reason"] == "missing"]
    unreadable = [row for row in not_checked if row["reason"] == "unreadable"]
    skipped = [row for row in not_checked if row["reason"] == "skipped_limit"]
    too_slow = [row for row in not_checked if row["reason"] == "time"]
    too_large = [row for row in not_checked if row["reason"] == "too_large"]

    blockers: list[str] = []
    advice: list[str] = []
    gone = {row["asset_id"] for row in missing} | set(known_missing)
    if gone:
        blockers.append(
            f"{_n(len(gone), 'picture file')} {'is' if len(gone) == 1 else 'are'} missing from storage, "
            "so a saved copy of your labelled pictures could not be made. Upload them again or delete those pictures."
        )

    if groups:
        message = (
            f"{involved} pictures form {_n(len(groups), 'group')} of nearly identical pictures. "
            + (
                f"{_n(crossing, 'group')} would be split between learning and the final test, which would make "
                "the score look better than it is. "
                if crossing
                else "Each group would stay on one side, but they add less new information than their count suggests. "
            )
            + "Keep one picture from each group before you rely on the score."
        )
        advice.append(message)
    else:
        message = (
            f"No nearly identical pictures found among the {len(hashes)} checked. This check catches re-saved or "
            "lightly changed copies; it does not catch rotated, flipped or noticeably cropped copies."
        )
    notes = []
    if unreadable:
        notes.append(f"{_n(len(unreadable), 'picture')} could not be opened as an image and {'was' if len(unreadable) == 1 else 'were'} not compared.")
    if missing:
        notes.append(f"{_n(len(missing), 'picture file')} could not be found and {'was' if len(missing) == 1 else 'were'} not compared.")
    if too_large:
        notes.append(
            f"{_n(len(too_large), 'picture')} {'is' if len(too_large) == 1 else 'are'} too large to open here (over "
            f"{MAX_PICTURE_PIXELS // 1_000_000} million pixels) and {'was' if len(too_large) == 1 else 'were'} not compared."
        )
    if skipped:
        notes.append(f"Only the first {MAX_PICTURES_HASHED} pictures were compared; {len(skipped)} were skipped.")
    if too_slow:
        notes.append(
            f"{_n(len(too_slow), 'picture')} {'was' if len(too_slow) == 1 else 'were'} not compared because the check ran out "
            f"of time (it stops after {MAX_HASH_SECONDS} seconds), so nearly identical copies among "
            f"{'it' if len(too_slow) == 1 else 'them'} would be missed."
        )
    if notes:
        message += " " + " ".join(notes)
        advice.extend(note for note in notes if note not in advice)

    near = {
        "groups": groups[:LIST_LIMIT],
        "pictures_involved": involved,
        "group_count": len(groups),
        "groups_crossing_splits": crossing,
        "identical_groups": identical[:LIST_LIMIT],
        "identical_pictures_involved": sum(len(group) for group in identical),
        "method": METHOD,
        "hamming_threshold": HAMMING_THRESHOLD,
        "checked": len(hashes),
        "not_checked": not_checked[:LIST_LIMIT],
        "not_checked_count": len(not_checked),
        "compared": "pictures with at least one accepted box (the ones a snapshot would contain)",
        "message": message,
    }
    return near, blockers, advice
