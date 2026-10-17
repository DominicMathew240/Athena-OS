# SPDX-License-Identifier: Apache-2.0
"""Checks a third-party connector should pass (mirrors ``athena_sdk.interfaces.conformance``)."""

from __future__ import annotations

import threading

from athena_sdk.connectors.base import DataSourceConnector, Health, Outcome, SourceItem


class ConnectorConformanceError(AssertionError):
    """The connector breaks the contract."""


def assert_connector_conforms(connector: DataSourceConnector, *, expect_items: bool = True) -> None:
    """Open ``connector``, poll it and exercise ack/nack on what it returns, then close it twice.

    The connector must already be set up so that ``poll`` has something to return when
    ``expect_items`` is true. Raises :class:`ConnectorConformanceError` with the first violation.
    """
    if not connector.name or not connector.version:
        raise ConnectorConformanceError("name and version must be non-empty strings")
    connector.open()
    try:
        health = connector.health()
        if not isinstance(health, Health):
            raise ConnectorConformanceError("health() must return Health")
        items = connector.poll(max_items=2)
        if len(items) > 2:
            raise ConnectorConformanceError("poll(max_items=n) returned more than n items")
        if expect_items and not items:
            raise ConnectorConformanceError("poll() returned nothing although items were expected")
        for item in items:
            if not isinstance(item, SourceItem):
                raise ConnectorConformanceError("poll() must return SourceItem objects")
        keys = [item.idempotency_key for item in items]
        if len(set(keys)) != len(keys):
            raise ConnectorConformanceError("poll() returned the same idempotency key twice")
        again = connector.poll(max_items=2)
        if {i.idempotency_key for i in again} & set(keys):
            raise ConnectorConformanceError("poll() re-offered an item that is still in flight")
        if items:
            connector.ack(items[0], Outcome.SUBMITTED)
            connector.ack(items[0], Outcome.DUPLICATE)  # a second ack must be harmless
        if len(items) > 1:
            connector.nack(items[1], "conformance check", permanent=False)
        stopped = threading.Event()
        stopped.set()
        if list(connector.stream(stopped, 0.0)):
            raise ConnectorConformanceError("stream() must return at once when stop is already set")
    finally:
        connector.close()
        connector.close()
