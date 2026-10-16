# SPDX-License-Identifier: Apache-2.0
"""Classification logic of scripts/check_licenses.py, exercised with synthetic metadata
(no real packages are inspected, so these tests do not depend on the environment)."""

from __future__ import annotations

import importlib.util
import json
from email.message import Message
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_licenses.py"
_spec = importlib.util.spec_from_file_location("check_licenses", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cl = importlib.util.module_from_spec(_spec)
# dataclasses resolve annotations through sys.modules; register before executing.
import sys  # noqa: E402

sys.modules["check_licenses"] = cl
_spec.loader.exec_module(cl)


class FakeDist:
    def __init__(self, name, version="1.0", *, expression=None, license=None, classifiers=()):
        self.metadata = Message()
        self.metadata["Name"] = name
        self.metadata["Version"] = version
        if expression:
            self.metadata["License-Expression"] = expression
        if license is not None:
            self.metadata["License"] = license
        for c in classifiers:
            self.metadata["Classifier"] = c
        self.version = version


def level(**kw):
    return cl.classify_metadata(FakeDist("x", **kw).metadata)[0]


@pytest.mark.parametrize(
    "expression",
    ["MIT", "Apache-2.0", "BSD-3-Clause", "MIT-CMU", "PSF-2.0", "ISC", "Apache-2.0 OR BSD-2-Clause",
     "Apache-2.0 AND CNRI-Python", "0BSD", "Unlicense"],
)
def test_permissive_expressions_are_ok(expression):
    assert level(expression=expression) == cl.OK


@pytest.mark.parametrize(
    "expression",
    ["AGPL-3.0-only", "AGPL-3.0-or-later", "GPL-2.0-only", "GPL-3.0-or-later", "GPL-3.0", "SSPL-1.0",
     "LicenseRef-Commons-Clause", "CC-BY-NC-4.0", "MIT AND GPL-3.0-only", "BUSL-1.1", "(MIT AND AGPL-3.0-only)"],
)
def test_denylisted_expressions_fail(expression):
    # LicenseRef-Commons-Clause hits the Commons Clause rule before the generic LicenseRef warn.
    assert level(expression=expression) == cl.FAIL


def test_or_expression_lets_licensee_pick_the_permissive_branch():
    assert level(expression="GPL-3.0-only OR MIT") == cl.OK
    assert level(expression="GPL-3.0-only OR AGPL-3.0-only") == cl.FAIL


def test_and_expression_takes_the_worst_branch():
    assert level(expression="MIT AND LGPL-2.1-or-later") == cl.WARN
    assert level(expression="MIT AND (Apache-2.0 OR GPL-2.0-only)") == cl.OK


@pytest.mark.parametrize("name", ["LGPL-2.1-or-later", "GNU Lesser General Public License v3 (LGPLv3)", "MPL-2.0",
                                  "Mozilla Public License 2.0 (MPL 2.0)", "EPL-2.0"])
def test_lgpl_and_weak_copyleft_warn_not_fail(name):
    assert level(expression=name) == cl.WARN


def test_gpl_with_runtime_exception_is_warn_not_fail():
    assert level(expression="GPL-3.0-with-GCC-exception") == cl.WARN
    assert level(expression="GPL-2.0-only WITH Classpath-exception-2.0") == cl.WARN


def test_proprietary_and_unrecognised_warn():
    assert level(expression="LicenseRef-NVIDIA-Proprietary") == cl.WARN
    assert level(classifiers=["License :: Other/Proprietary License"]) == cl.WARN
    assert level(license="Totally Custom Terms v9") == cl.WARN


def test_no_information_is_unknown():
    assert level() == cl.UNKNOWN
    assert level(license="UNKNOWN") == cl.UNKNOWN


def test_classifiers_are_used_and_worst_wins():
    assert level(classifiers=["License :: OSI Approved :: MIT License"]) == cl.OK
    both = ["License :: OSI Approved :: MIT License",
            "License :: OSI Approved :: GNU General Public License v3 (GPLv3)"]
    assert level(classifiers=both) == cl.FAIL
    assert level(classifiers=["License :: OSI Approved :: GNU Affero General Public License v3"]) == cl.FAIL
    assert level(classifiers=["License :: Free for non-commercial use"]) == cl.FAIL


def test_long_license_text_field_is_ignored_to_avoid_bundled_notice_false_positives():
    text = "Copyright (c) NumPy Developers.\nBundled: GCC runtime library, License: GPL-3.0-with-GCC-exception\n" * 3
    assert level(license=text, classifiers=["License :: OSI Approved :: BSD License"]) == cl.OK
    assert level(license=text) == cl.UNKNOWN


def test_expression_takes_precedence_over_classifiers():
    assert level(expression="MIT", classifiers=["License :: OSI Approved :: GNU General Public License v3 (GPLv3)"]) == cl.OK


def test_allowlist_downgrades_only_matching_entries():
    allow = [{"name": "Some_Pkg", "version": "1.0", "reason": "reviewed: dynamically linked, replaceable"}]
    bad = FakeDist("some-pkg", "1.0", expression="LGPL-3.0-only")
    other_version = FakeDist("some-pkg", "2.0", expression="LGPL-3.0-only")
    gpl = FakeDist("another", "1.0", expression="GPL-3.0-only")
    findings = {f"{f.name}-{f.version}": f for f in cl.evaluate([bad, other_version, gpl], allow)}
    assert findings["some-pkg-1.0"].allowlisted and findings["some-pkg-1.0"].effective == cl.OK
    assert not findings["some-pkg-2.0"].allowlisted
    assert findings["another-1.0"].effective == cl.FAIL
    assert cl.exit_code(findings.values()) == 1


def test_exit_code_zero_when_all_fail_findings_are_allowlisted():
    allow = [{"name": "gpl-thing", "license": "GPL-3.0-only", "reason": "dev-only tool, never shipped"}]
    findings = cl.evaluate([FakeDist("gpl-thing", expression="GPL-3.0-only"), FakeDist("ok", expression="MIT")], allow)
    assert cl.exit_code(findings) == 0


def test_strict_unknown_flag():
    findings = cl.evaluate([FakeDist("mystery")])
    assert cl.exit_code(findings) == 0
    assert cl.exit_code(findings, strict_unknown=True) == 1


def test_allowlist_entries_require_a_reason(tmp_path):
    path = tmp_path / "allow.json"
    path.write_text(json.dumps({"allow": [{"name": "x", "reason": "  "}]}))
    with pytest.raises(ValueError):
        cl.load_allowlist(path)
    path.write_text(json.dumps({"allow": [{"name": "x", "reason": "reviewed"}]}))
    assert cl.load_allowlist(path)[0]["name"] == "x"


def test_shipped_allowlist_is_well_formed():
    entries = cl.load_allowlist(_SCRIPT.parent / "license_allowlist.json")
    assert all(e["reason"].strip() for e in entries)


def test_cli_fails_on_a_real_gpl_dist_info_directory(tmp_path):
    info = tmp_path / "evilpkg-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: evilpkg\nVersion: 1.0\nLicense-Expression: AGPL-3.0-or-later\n")
    allow = tmp_path / "none.json"
    allow.write_text(json.dumps({"allow": []}))
    assert cl.main(["--path", str(tmp_path), "--allowlist", str(allow), "--quiet"]) == 1
    allow.write_text(json.dumps({"allow": [{"name": "evilpkg", "reason": "test"}]}))
    assert cl.main(["--path", str(tmp_path), "--allowlist", str(allow), "--quiet"]) == 0
