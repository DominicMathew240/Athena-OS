# SPDX-License-Identifier: Apache-2.0
"""Validated runner configuration (``connector.json``).

Secrets never appear in this file: the console password comes from
``ATHENA_CONNECTOR_USER`` / ``ATHENA_CONNECTOR_PASSWORD`` and the HTTP push secret from the
environment variable named by ``secret_env``. Unknown keys are errors, so a typo cannot
silently change behaviour.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from athena_sdk.connectors.base import DataSourceConnector, Sink
from athena_sdk.connectors.console_sink import AthenaConsoleSink
from athena_sdk.connectors.folder import FolderDropConnector
from athena_sdk.connectors.http_push import HttpPushConnector
from athena_sdk.connectors.ledger import Ledger
from athena_sdk.connectors.pipeline import Pipeline
from athena_sdk.connectors.retry import RetryPolicy

_CREDENTIAL_KEYS = {"password", "passwd", "username", "user", "secret", "token", "api_key"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class ConfigError(ValueError):
    """The configuration is unusable; ``errors`` is a list of plain-language problems."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _no_credentials(cls, data: Any) -> Any:
        if isinstance(data, dict):
            bad = sorted(key for key in data if key in _CREDENTIAL_KEYS)
            if bad:
                raise ValueError(
                    f"{', '.join(bad)} must not be written in the config file: credentials come from the "
                    "environment (ATHENA_CONNECTOR_USER / ATHENA_CONNECTOR_PASSWORD, or secret_env)."
                )
        return data


class FolderDropConfig(_Strict):
    type: Literal["folder_drop"]
    path: str
    source_label: str = Field("folder", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    stable_seconds: float = Field(2.0, ge=0, le=3600)
    max_bytes: int = Field(MAX_IMAGE_BYTES, ge=1, le=MAX_IMAGE_BYTES)
    retry_cooldown: float = Field(30.0, ge=0, le=86400)
    reject_hardlinks: bool = True


class HttpPushConfig(_Strict):
    type: Literal["http_push"]
    spool_dir: str
    secret_env: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    host: str = "127.0.0.1"
    port: int = Field(8820, ge=0, le=65535)
    allow_non_loopback: bool = False
    max_bytes: int = Field(MAX_IMAGE_BYTES, ge=1, le=MAX_IMAGE_BYTES)
    max_pending: int = Field(64, ge=1, le=100000)
    source_label: str = Field("http-push", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    retry_cooldown: float = Field(30.0, ge=0, le=86400)


class ConsoleSinkConfig(_Strict):
    type: Literal["athena_console"]
    base_url: str
    project_id: str
    app: str = Field(min_length=1)
    poll_timeout: float = Field(60.0, gt=0, le=3600)
    poll_interval: float = Field(1.0, gt=0, le=60)
    request_timeout: float = Field(30.0, gt=0, le=600)
    allow_insecure_http: bool = False


class RetryConfig(_Strict):
    max_attempts: int = Field(5, ge=1, le=100)
    base_delay: float = Field(0.5, ge=0, le=600)
    max_delay: float = Field(30.0, ge=0, le=3600)
    multiplier: float = Field(2.0, ge=1, le=10)
    jitter: Literal["full", "equal", "none"] = "full"


class PipelineConfig(_Strict):
    ledger_path: str = "connector-ledger.sqlite"
    queue_size: int = Field(16, ge=1, le=10000)
    overflow: Literal["block", "drop_oldest"] = "block"
    retry: RetryConfig = RetryConfig()
    poll_interval: float = Field(1.0, gt=0, le=60)
    drain_timeout: float = Field(20.0, ge=0, le=600)
    health_interval: float = Field(10.0, gt=0, le=3600)
    health_file: str | None = None


class ConnectorConfig(_Strict):
    connector: Annotated[FolderDropConfig | HttpPushConfig, Field(discriminator="type")]
    sink: ConsoleSinkConfig
    pipeline: PipelineConfig = PipelineConfig()


def _describe(error: ValidationError) -> list[str]:
    out = []
    for item in error.errors():
        where = ".".join(str(part) for part in item["loc"])
        out.append(f"{where or 'config'}: {item['msg'].removeprefix('Value error, ')}")
    return out


def load_config(path: str | Path) -> tuple[ConnectorConfig, Path]:
    """Parse and validate ``path``; returns the config and the directory relative paths resolve against."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError([f"cannot read {path}: {error.strerror or error}"]) from None
    except ValueError as error:
        raise ConfigError([f"{path} is not valid JSON: {error}"]) from None
    try:
        return ConnectorConfig.model_validate(raw), path.resolve().parent
    except ValidationError as error:
        raise ConfigError(_describe(error)) from None


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else base / candidate


def build_pipeline(
    config: ConnectorConfig,
    base_dir: Path,
    env: Mapping[str, str] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[Pipeline, Ledger]:
    env = os.environ if env is None else env
    source_cfg, sink_cfg, pipe = config.connector, config.sink, config.pipeline
    connector: DataSourceConnector
    try:
        if isinstance(source_cfg, FolderDropConfig):
            connector = FolderDropConnector(
                _resolve(base_dir, source_cfg.path), source_label=source_cfg.source_label,
                stable_seconds=source_cfg.stable_seconds, max_bytes=source_cfg.max_bytes,
                retry_cooldown=source_cfg.retry_cooldown, reject_hardlinks=source_cfg.reject_hardlinks,
            )
        else:
            secret = env.get(source_cfg.secret_env, "")
            if not secret:
                raise ConfigError([f"environment variable {source_cfg.secret_env} (the shared secret) is not set"])
            connector = HttpPushConnector(
                _resolve(base_dir, source_cfg.spool_dir), secret, host=source_cfg.host, port=source_cfg.port,
                allow_non_loopback=source_cfg.allow_non_loopback, max_bytes=source_cfg.max_bytes,
                max_pending=source_cfg.max_pending, source_label=source_cfg.source_label,
                retry_cooldown=source_cfg.retry_cooldown,
            )
        sink: Sink = AthenaConsoleSink.from_env(
            sink_cfg.base_url, sink_cfg.project_id, sink_cfg.app, env,
            poll_timeout=sink_cfg.poll_timeout, poll_interval=sink_cfg.poll_interval,
            request_timeout=sink_cfg.request_timeout, allow_insecure_http=sink_cfg.allow_insecure_http,
        )
        retry = RetryPolicy(**pipe.retry.model_dump())
    except ValueError as error:
        if isinstance(error, ConfigError):
            raise
        raise ConfigError([str(error)]) from None
    ledger = Ledger(_resolve(base_dir, pipe.ledger_path))
    pipeline = Pipeline(
        connector, sink, ledger, queue_size=pipe.queue_size, overflow=pipe.overflow, retry=retry,
        poll_interval=pipe.poll_interval, drain_timeout=pipe.drain_timeout, on_event=on_event,
    )
    return pipeline, ledger
