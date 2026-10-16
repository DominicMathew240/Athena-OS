#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail CI when an installed Python distribution carries a license that blocks
commercial use or imposes strong copyleft on Athena OS.

Standard library only. Reads the metadata of every distribution visible to the
running interpreter (or of the directories given with ``--path``) through
``importlib.metadata`` and classifies each license string as:

* ``FAIL``    - AGPL, GPL (any version), SSPL, Commons Clause, BUSL,
                 CC-BY-NC*, anything saying "non-commercial". Exit code 1 unless
                 allowlisted. (GPL "with ... exception" expressions are WARN.)
* ``WARN``    - LGPL, weak/file-level copyleft (MPL, EPL, CDDL, CPL), GPL
                 "with ... exception" expressions, proprietary / ``LicenseRef-``
                 / "Other" licenses, and unrecognized strings. Reported, exit 0.
* ``UNKNOWN`` - no license information at all. Reported, exit 0 (exit 1 with
                 ``--strict-unknown``).
* ``OK``      - recognised permissive license (MIT, BSD, Apache, ISC, PSF, ...).

SPDX expressions are evaluated: ``A AND B`` takes the worse of the two, ``A OR B``
takes the better (the licensee may choose). When no ``License-Expression`` is
present, the short ``License`` field and the ``License ::`` trove classifiers are
all considered and the WORST one wins (conservative for dual-licensed packages -
use the allowlist for those).

Reviewed exceptions live in ``scripts/license_allowlist.json``; every entry needs
a non-empty ``reason``. This is an engineering gate, not legal advice; see
``docs/LICENSE_AUDIT.md``.

Usage::

    python scripts/check_licenses.py [--allowlist FILE] [--path DIR ...]
                                     [--strict-unknown] [--json] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable, Sequence

OK, WARN, UNKNOWN, FAIL = "OK", "WARN", "UNKNOWN", "FAIL"
_SEVERITY = {OK: 0, UNKNOWN: 1, WARN: 2, FAIL: 3}

# Order matters inside classify_license_string: FAIL rules first.
_FAIL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bAGPL", "AGPL"),
    (r"AFFERO", "AGPL"),
    (r"(?<![A-Z])GPL(?![A-Z])", "GPL"),  # GPL-2.0, GPLv3, GPL-3.0-or-later; not LGPL/AGPL
    (r"GNU GENERAL PUBLIC", "GPL"),
    (r"\bSSPL\b", "SSPL"),
    (r"SERVER SIDE PUBLIC", "SSPL"),
    (r"COMMONS[ -]CLAUSE", "Commons Clause"),
    (r"\bBUSL\b|BUSINESS SOURCE", "BUSL"),
    (r"CC[ -]BY[ -]NC|CREATIVE COMMONS.{0,40}NON[ -]?COMMERCIAL", "CC-BY-NC"),
    (r"NON[ -]?COMMERCIAL", "non-commercial"),
)
_LGPL = re.compile(r"LGPL|LESSER GENERAL PUBLIC|LIBRARY GENERAL PUBLIC")
_WEAK_COPYLEFT = re.compile(r"\bMPL\b|MOZILLA PUBLIC|\bEPL\b|ECLIPSE PUBLIC|\bCDDL\b|\bCPL\b|COMMON PUBLIC LICENSE")
_EXCEPTION = re.compile(r"\bWITH\b|-WITH-|\bEXCEPTION\b", re.IGNORECASE)
_PROPRIETARY = re.compile(r"PROPRIETARY|LICENSEREF-|\bOTHER\b|COMMERCIAL LICENSE|ALL RIGHTS RESERVED")
_PERMISSIVE = re.compile(
    r"\bMIT\b|MIT-CMU|MIT LICENSE|\bBSD|APACHE|\bISC\b|\bPSF\b|PYTHON SOFTWARE FOUNDATION|PYTHON-2|CNRI-PYTHON|"
    r"\bHPND\b|\bZLIB\b|UNLICENSE|\bCC0|PUBLIC DOMAIN|\bWTFPL\b|\b0BSD\b|\bBSL-1\b|BOOST|\bPOSTGRESQL\b|"
    r"\bX11\b|\bCC-BY-4\.0\b|\bCC-BY-3\.0\b|PIL SOFTWARE LICENSE|HISTORICAL PERMISSION|ARTISTIC"
)
_PLACEHOLDERS = {"", "UNKNOWN", "NONE", "N/A", "NA", "-"}
# A License field longer than this (or multi-line) is licence TEXT, not a name; it is
# ignored for classification because bundled third-party notices inside it would
# otherwise cause false positives (e.g. numpy lists GCC-runtime GPL-with-exception).
_MAX_NAME_LEN = 100


def normalize_name(name: str) -> str:
    """PEP 503 normalisation."""
    return re.sub(r"[-_.]+", "-", name).lower()


def classify_license_string(value: str) -> tuple[str, str]:
    """Classify ONE license name (no SPDX AND/OR operators). Returns (level, reason)."""
    text = value.strip()
    upper = text.upper()
    if upper in _PLACEHOLDERS:
        return UNKNOWN, "no license information"
    # "GPL ... with ... exception" is not an automatic pass, but it is also not the
    # plain-GPL fail: flag for review. Check before the FAIL patterns.
    for pattern, label in _FAIL_PATTERNS:
        if re.search(pattern, upper):
            if label in ("GPL",) and _EXCEPTION.search(text):
                return WARN, f"{label} with exception - review the exception ({text})"
            return FAIL, f"{label} ({text})"
    if _LGPL.search(upper):
        return WARN, f"LGPL - dynamic linking only, keep it replaceable ({text})"
    if _WEAK_COPYLEFT.search(upper):
        return WARN, f"weak/file-level copyleft ({text})"
    if _PROPRIETARY.search(upper):
        return WARN, f"proprietary or non-SPDX license ({text})"
    if _PERMISSIVE.search(upper):
        return OK, text
    return WARN, f"unrecognized license string ({text})"


def _strip_parens(s: str) -> str:
    s = s.strip()
    while s.startswith("(") and s.endswith(")") and _balanced(s[1:-1]):
        s = s[1:-1].strip()
    return s


def _balanced(s: str) -> bool:
    depth = 0
    for ch in s:
        depth += (ch == "(") - (ch == ")")
        if depth < 0:
            return False
    return depth == 0


def _split_top(expr: str, op: str) -> list[str]:
    """Split on a top-level (paren depth 0) ' AND ' / ' OR ' operator, case-insensitive."""
    parts, depth, start = [], 0, 0
    token = f" {op} "
    upper = expr.upper()
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and upper.startswith(token, i):
            parts.append(expr[start:i])
            i += len(token)
            start = i
            continue
        i += 1
    parts.append(expr[start:])
    return [p.strip() for p in parts if p.strip()]


def classify_expression(expr: str) -> tuple[str, str]:
    """Evaluate an SPDX-ish expression. OR = licensee's best choice, AND = worst."""
    expr = _strip_parens(expr)
    # OR binds looser than AND, so split on OR first.
    or_parts = _split_top(expr, "OR")
    if len(or_parts) > 1:
        results = [classify_expression(p) for p in or_parts]
        return min(results, key=lambda r: _SEVERITY[r[0]])
    and_parts = _split_top(expr, "AND")
    if len(and_parts) > 1:
        results = [classify_expression(p) for p in and_parts]
        return max(results, key=lambda r: _SEVERITY[r[0]])
    return classify_license_string(expr)


@dataclass
class Finding:
    name: str
    version: str
    level: str
    licenses: list[str]
    reason: str
    allowlisted: bool = False
    allow_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def effective(self) -> str:
        return OK if self.allowlisted else self.level


def license_strings(meta: Any) -> tuple[list[str], bool]:
    """Return (license strings, is_spdx_expression) from a PackageMetadata-like object."""
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return [expression], True
    found: list[str] = []
    short = (meta.get("License") or "").strip()
    if short and "\n" not in short and len(short) <= _MAX_NAME_LEN:
        found.append(short)
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            leaf = classifier.split("::")[-1].strip()
            if leaf.upper().startswith("OSI APPROVED") and classifier.count("::") == 1:
                continue
            found.append(leaf)
    return found, False


def classify_metadata(meta: Any) -> tuple[str, str, list[str]]:
    strings, is_expr = license_strings(meta)
    if not strings:
        return UNKNOWN, "no License-Expression, short License field or License classifier", []
    if is_expr:
        level, reason = classify_expression(strings[0])
        return level, reason, strings
    results = [classify_expression(s) for s in strings]
    level, reason = max(results, key=lambda r: _SEVERITY[r[0]])
    return level, reason, strings


def load_allowlist(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("allow", []) if isinstance(data, dict) else data
    for entry in entries:
        if not str(entry.get("name", "")).strip():
            raise ValueError(f"allowlist entry without a name: {entry!r}")
        if not str(entry.get("reason", "")).strip():
            raise ValueError(f"allowlist entry for {entry['name']!r} needs a non-empty 'reason'")
    return list(entries)


def allowlist_match(entries: Sequence[dict[str, Any]], name: str, version: str, licenses: Sequence[str]) -> dict[str, Any] | None:
    for entry in entries:
        if normalize_name(entry["name"]) != normalize_name(name):
            continue
        if entry.get("version") and entry["version"] != version:
            continue
        if entry.get("license") and entry["license"] not in licenses:
            continue
        return entry
    return None


def evaluate(dists: Iterable[Any], allow: Sequence[dict[str, Any]] = ()) -> list[Finding]:
    findings = []
    seen: set[tuple[str, str]] = set()
    for dist in dists:
        meta = dist.metadata
        name = meta.get("Name") or getattr(dist, "name", None) or "<unnamed>"
        version = meta.get("Version") or getattr(dist, "version", "") or ""
        key = (normalize_name(name), version)
        if key in seen:
            continue
        seen.add(key)
        level, reason, strings = classify_metadata(meta)
        finding = Finding(name=name, version=version, level=level, licenses=strings, reason=reason)
        entry = allowlist_match(allow, name, version, strings) if level != OK else None
        if entry:
            finding.allowlisted = True
            finding.allow_reason = entry["reason"]
        findings.append(finding)
    findings.sort(key=lambda f: (-_SEVERITY[f.effective], normalize_name(f.name)))
    return findings


def exit_code(findings: Iterable[Finding], strict_unknown: bool = False) -> int:
    for f in findings:
        if f.effective == FAIL or (strict_unknown and f.effective == UNKNOWN):
            return 1
    return 0


def _print_report(findings: list[Finding], quiet: bool) -> None:
    counts = {level: 0 for level in (OK, WARN, UNKNOWN, FAIL)}
    for f in findings:
        counts[f.effective] += 1
    for f in findings:
        if quiet and f.effective == OK and not f.allowlisted:
            continue
        shown = ", ".join(f.licenses) or "-"
        tag = f"ALLOWED (was {f.level})" if f.allowlisted else f.effective
        print(f"{tag:<20} {f.name} {f.version}  [{shown}]")
        if f.effective != OK or f.allowlisted:
            print(f"{'':<20}   -> {f.allow_reason if f.allowlisted else f.reason}")
    print(
        f"\n{len(findings)} distributions: "
        + ", ".join(f"{level}={n}" for level, n in counts.items())
    )


def main(argv: Sequence[str] | None = None) -> int:
    default_allowlist = Path(__file__).resolve().parent / "license_allowlist.json"
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--allowlist", type=Path, default=default_allowlist)
    parser.add_argument("--path", action="append", help="site-packages dir to scan (repeatable); default: sys.path")
    parser.add_argument("--strict-unknown", action="store_true", help="treat distributions with no license info as failures")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="hide OK rows")
    args = parser.parse_args(argv)

    try:
        allow = load_allowlist(args.allowlist)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: bad allowlist {args.allowlist}: {exc}", file=sys.stderr)
        return 2

    dists = metadata.distributions(path=args.path) if args.path else metadata.distributions()
    findings = evaluate(dists, allow)
    if not findings:
        print("error: no distributions found - wrong environment?", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([{**asdict(f), "effective": f.effective} for f in findings], indent=2))
    else:
        _print_report(findings, args.quiet)
    code = exit_code(findings, args.strict_unknown)
    if code and not args.json:
        print("\nFAILED: add a reviewed entry with a reason to scripts/license_allowlist.json only after "
              "the license has been checked (see docs/LICENSE_AUDIT.md).", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
