# SPDX-License-Identifier: Apache-2.0
"""Vendor-neutral data-source connectors for Athena.

``DataSourceConnector`` (where bytes come from) -> ``Pipeline`` (bounded queue, retries,
idempotency ledger) -> ``Sink`` (where they go; normally ``AthenaConsoleSink``).

Status: reference implementation of a *contract*. No real scanner, hardware or vendor
protocol has been selected or tested. See ``docs/guides/writing-a-connector.md``.
"""

from athena_sdk.connectors.base import (
    ConnectorError,
    DataSourceConnector,
    Health,
    ItemError,
    Outcome,
    PermanentItemError,
    Receipt,
    Sink,
    SinkError,
    SinkUnavailableError,
    SourceItem,
    TransientSinkError,
)
from athena_sdk.connectors.bounded_queue import BoundedQueue
from athena_sdk.connectors.console_sink import AthenaConsoleSink
from athena_sdk.connectors.folder import FolderDropConnector
from athena_sdk.connectors.http_push import HttpPushConnector
from athena_sdk.connectors.ledger import Ledger, LedgerEntry
from athena_sdk.connectors.pipeline import Pipeline
from athena_sdk.connectors.retry import RetryPolicy

__all__ = [
    "AthenaConsoleSink",
    "BoundedQueue",
    "ConnectorError",
    "DataSourceConnector",
    "FolderDropConnector",
    "Health",
    "HttpPushConnector",
    "ItemError",
    "Ledger",
    "LedgerEntry",
    "Outcome",
    "PermanentItemError",
    "Pipeline",
    "Receipt",
    "RetryPolicy",
    "Sink",
    "SinkError",
    "SinkUnavailableError",
    "SourceItem",
    "TransientSinkError",
]
