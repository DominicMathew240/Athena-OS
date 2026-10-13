# SPDX-License-Identifier: Apache-2.0
"""Athena CLI — thin developer entrypoint over athena_sdk.

`athena --help`                 show commands
`athena run-example`            run the bundled synthetic example pipeline
`athena run-audio-example`      run the acoustic noise-analysis example pipeline
`athena validate-contracts`     round-trip validate the seven Core contracts
`athena knowledge`              manage a local document library with citations
"""

from __future__ import annotations

import argparse
import sys

from athena_cli.commands import (
    knowledge,
    run_audio_example,
    run_example,
    validate_contracts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="athena",
        description="Athena Open Source CLI — examples, contract checks, and local document search.",
    )
    parser.add_argument(
        "--version", action="version", version="athena-cli 0.1.0"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "run-example",
        help="Run the bundled synthetic example pipeline end to end.",
    )
    subparsers.add_parser(
        "run-audio-example",
        help="Run the acoustic noise-analysis example pipeline end to end.",
    )
    subparsers.add_parser(
        "validate-contracts",
        help="Round-trip serialize/deserialize the seven Core contracts.",
    )
    knowledge.add_parser(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run-example":
        return run_example.run()
    if args.command == "run-audio-example":
        return run_audio_example.run()
    if args.command == "validate-contracts":
        return validate_contracts.run()
    if args.command == "knowledge":
        return knowledge.run(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
