# SPDX-License-Identifier: Apache-2.0
"""CLI smoke tests: --help, and one example pipeline run against synthetic data."""

from __future__ import annotations

import importlib.util

import pytest
from athena_cli.main import build_parser, main

_NUMPY_AVAILABLE = importlib.util.find_spec("numpy") is not None


def test_help_exits_cleanly(capsys) -> None:
    parser = build_parser()
    try:
        parser.parse_args(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("--help should exit")

    out = capsys.readouterr().out
    assert "athena" in out


def test_validate_contracts_command() -> None:
    assert main(["validate-contracts"]) == 0


def test_run_example_command(capsys) -> None:
    assert main(["run-example"]) == 0
    out = capsys.readouterr().out
    assert "AssetStateFinding" in out


@pytest.mark.skipif(
    not _NUMPY_AVAILABLE,
    reason="numpy is an optional example dependency, not installed by default",
)
def test_run_audio_example_command(capsys) -> None:
    assert main(["run-audio-example"]) == 0
    out = capsys.readouterr().out
    assert "nominal" in out
    assert "signal_masked_by_noise" in out


def test_run_audio_example_without_numpy_fails_gracefully(monkeypatch, capsys) -> None:
    """Even without numpy, the command must not crash with a raw traceback."""
    import examples.adapters.audio_noise_adapter as audio_noise_adapter

    monkeypatch.setattr(audio_noise_adapter, "np", None)
    assert main(["run-audio-example"]) == 1
    out = capsys.readouterr().out
    assert "numpy" in out
