# Contributing to Athena OS

Thanks for your interest in contributing. This repository hosts the open
components of Athena OS: core contracts, the developer SDK, CLI, examples,
documentation, and an early Django web app. It does not host proprietary
model weights, private training data, or a proprietary managed control plane.

See the [Athena OS roadmap](ROADMAP.md) for the industrial labeling, training, and deployment scope. Feature work should match an accepted roadmap item and include tests; do not include private datasets, model weights, credentials, or unreviewed third-party assets.

## Ways to contribute

- **Bug reports** — use the Bug Report issue template.
- **Feature requests** — use the Feature Request issue template.
- **Contract changes (breaking or new fields)** — go through the RFC
  process below before a PR is opened; contracts are a shared standard and
  changes affect every downstream implementer.
- **Suggestion PRs** — propose a roadmap or documentation change with the user problem and expected outcome.
- **Vulnerabilities** — use the private reporting process in [SECURITY.md](SECURITY.md), never a public issue or PR.
- **Docs fixes** — small doc PRs (typos, clarity) don't need an RFC.

## RFC process

Any change to a Core contract (`contracts/v1/*.schema.json` /
`sdk/python/athena_sdk/contracts/v1.py`) — new field, changed field,
version bump — requires an RFC before implementation:

1. Open an issue using the **RFC** template describing the motivation, the
   proposed shape, and backward-compatibility impact.
2. Allow at least 5 days for maintainer and community feedback.
3. Once a maintainer marks the RFC "accepted," implementation PRs may
   reference it.
4. Breaking changes always land as a new major version directory
   (e.g. `contracts/v2/`) — `v1/` is never edited in place once released.

Non-contract changes (SDK internals, CLI commands, examples, docs, CI)
don't need an RFC — open a PR directly.

## Development setup

From the root of this repository, run:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e sdk/python -e cli
pip install pytest
pytest
```

Regenerate JSON Schema files after changing a contract model:

```bash
python contracts/generate_json_schema.py
```

## Pull requests

- One logical change per PR.
- Add or update tests for any behavior change — contract changes need a
  round-trip test in `tests/test_contracts.py`.
- `pytest` must pass locally before requesting review; CI runs the same
  suite plus lint and the schema-drift check.
- Fill out the PR template, including the scope-boundary checklist.

## Scope boundary

This repository is **open-source only**. Do not submit, and maintainers
will not merge:

- Proprietary model weights, checkpoints, or training datasets.
- Production or customer-derived data of any kind (examples must use
  synthetic/illustrative data only).
- Internals of Athena Forge, Athena Lab, or any closed-source runtime,
  registry, or evidence-store implementation.
- Real inference, training, or policy-evaluation logic — Core contracts
  are schema-only by design; implementations live outside this repo.

If you're unsure whether something belongs here, open an issue and ask
before submitting code.

## Release process and semver

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities, and
[`docs/architecture-overview.md`](docs/architecture-overview.md) for the
open/closed boundary this repo sits behind.

- **SDK, CLI, contracts** version independently, each with its own
  semantic version (`MAJOR.MINOR.PATCH`).
- A contract's `contract_version` field (e.g. `"v1"`) tracks wire-format
  major versions and is independent of the package's semver.
- Breaking changes to any public interface (SDK class/method signature,
  CLI command/flag, contract field) require a MAJOR bump.
- Releases are tagged `sdk-vX.Y.Z`, `cli-vX.Y.Z`, or `contracts-vX.Y.Z` as
  appropriate. The first public tag is `v0.1.0` across all three, cut only
  after the licensing and IP-firewall review referenced in
  the [public release preparation milestone](ROADMAP.md#m0-prepare-the-public-release) is complete.
