# SPDX-License-Identifier: Apache-2.0
"""Domain Pack interface.

A Domain Pack binds a versioned `DomainPolicy` (label taxonomy, severity
mapping) to a concrete `AssetStateFinding -> RecommendedAction` decision
function. It lets Athena Core stay domain-neutral while community or
proprietary domain knowledge (solar, assembly, etc.) plugs in behind a
stable interface, independent of which `ModelAdapter` produced the finding.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from athena_sdk.contracts import AssetStateFinding, DomainPolicy, RecommendedAction


class DomainPack(ABC):
    """Interface every domain pack implements, regardless of origin."""

    @property
    @abstractmethod
    def policy(self) -> DomainPolicy:
        """The versioned DomainPolicy this pack implements."""

    @abstractmethod
    def evaluate(self, finding: AssetStateFinding) -> list[RecommendedAction]:
        """Map a single AssetStateFinding to zero or more RecommendedActions.

        Contract: every returned RecommendedAction must reference
        `finding.finding_id` and `self.policy.policy_id`.
        """


__all__ = ["DomainPack"]
