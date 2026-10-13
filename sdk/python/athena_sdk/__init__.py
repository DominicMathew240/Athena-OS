# SPDX-License-Identifier: Apache-2.0
"""Athena SDK — client library for the open Athena industrial AI standard.

Ships the versioned Core contracts (`athena_sdk.contracts`) plus thin,
implementation-free interfaces for model registration and inference
(`athena_sdk.registry`, `athena_sdk.client`). No proprietary model,
training, or runtime logic lives in this package.
"""

from athena_sdk.client import AthenaClient, InferenceClient
from athena_sdk.registry import ModelHandle, ModelRegistry

__version__ = "0.1.0"

__all__ = [
    "AthenaClient",
    "InferenceClient",
    "ModelHandle",
    "ModelRegistry",
    "__version__",
]
