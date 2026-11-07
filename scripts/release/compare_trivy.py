"""Fail when the head scan has vulnerabilities the base scan does not.

Findings are keyed by (target, package, vulnerability id) so pre-existing
backlog does not block unrelated pull requests, while any new fixable
CRITICAL introduced by the change does.
"""

from __future__ import annotations

import json
import sys


def findings(path: str) -> set[tuple[str, str, str]]:
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    keys = set()
    for result in report.get("Results") or []:
        target = str(result.get("Target", "")).removeprefix("base-tree/")
        for vuln in result.get("Vulnerabilities") or []:
            keys.add((target, str(vuln.get("PkgName", "")), str(vuln.get("VulnerabilityID", ""))))
    return keys


def main(base_path: str, head_path: str) -> int:
    base, head = findings(base_path), findings(head_path)
    new = sorted(head - base)
    print(f"base findings: {len(base)}  head findings: {len(head)}  new: {len(new)}")
    for target, package, vuln in new:
        print(f"::error file={target}::new fixable CRITICAL {vuln} in {package}")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
