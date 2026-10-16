"""Backend-neutral types for model-assisted box proposals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

from PIL import Image

QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True)
class Proposal:
    """One proposed box in normalized (0-1) image coordinates."""

    x: float
    y: float
    width: float
    height: float
    label: str
    confidence: float


class AutoLabeler(Protocol):
    """Anything that can propose labelled boxes for an image.

    Implementations must only return labels from ``labels`` and never raise for an
    image that merely contains no objects (return an empty list instead).
    """

    @property
    def name(self) -> str: ...

    def propose(
        self,
        image: Image.Image,
        labels: list[str],
        *,
        min_confidence: float,
        max_boxes: int,
    ) -> list[Proposal]: ...


def normalized_box(proposal: Proposal) -> dict[str, Decimal] | None:
    """Clamp and quantize a proposal so it satisfies the database bounds checks."""

    def q(value: float) -> Decimal:
        return Decimal(str(min(1.0, max(0.0, float(value))))).quantize(QUANTUM, rounding=ROUND_DOWN)

    x, y = q(proposal.x), q(proposal.y)
    width = min(q(proposal.width), Decimal(1) - x)
    height = min(q(proposal.height), Decimal(1) - y)
    if width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}
