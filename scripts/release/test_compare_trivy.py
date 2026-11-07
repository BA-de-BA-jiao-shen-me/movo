import json
import tempfile
import unittest
from pathlib import Path

import compare_trivy


def report(*items):
    return {"Results": [{"Target": t, "Vulnerabilities": [{"PkgName": p, "VulnerabilityID": v}]} for t, p, v in items]}


class CompareTrivyTest(unittest.TestCase):
    def run_case(self, base, head):
        with tempfile.TemporaryDirectory() as tmp:
            b, h = Path(tmp, "b.json"), Path(tmp, "h.json")
            b.write_text(json.dumps(base)); h.write_text(json.dumps(head))
            return compare_trivy.main(str(b), str(h))

    def test_existing_backlog_passes(self):
        item = ("services/chat-api/requirements.txt", "certifi", "GHSA-x")
        base = report(("base-tree/" + item[0], item[1], item[2]))
        self.assertEqual(self.run_case(base, report(item)), 0)

    def test_new_finding_fails(self):
        self.assertEqual(self.run_case(report(), report(("a/requirements.txt", "x", "CVE-1"))), 1)

    def test_fixed_finding_passes(self):
        self.assertEqual(self.run_case(report(("a", "x", "CVE-1")), {"Results": None}), 0)


if __name__ == "__main__":
    unittest.main()
