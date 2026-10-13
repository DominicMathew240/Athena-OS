# SPDX-License-Identifier: Apache-2.0
"""Stable interop interfaces: Model Adapter, Domain Pack, Runtime.

These are the "open protocol, proprietary implementation" surfaces —
community, third-party, and Athena proprietary models/runtimes all
implement the same interfaces defined here, with no implementation-specific
logic in the interfaces themselves.
"""

from athena_sdk.interfaces.adapter import ModelAdapter
from athena_sdk.interfaces.capability import (
    AdapterCapabilities,
    AdapterInterfaceVersion,
)
from athena_sdk.interfaces.conformance import (
    AdapterConformanceError,
    assert_adapter_conforms,
)
from athena_sdk.interfaces.domain_pack import DomainPack
from athena_sdk.interfaces.runtime import LocalRuntime, Runtime

__all__ = [
    "AdapterCapabilities",
    "AdapterConformanceError",
    "AdapterInterfaceVersion",
    "DomainPack",
    "LocalRuntime",
    "ModelAdapter",
    "Runtime",
    "assert_adapter_conforms",
]
