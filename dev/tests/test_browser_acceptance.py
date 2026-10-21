"""受入確認用データの期待値を、実際に生成した全ケース・Hash更新段階と照合する。"""

from collections import Counter
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import prepare_browser_acceptance as acceptance


class BrowserAcceptanceTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".test-tmp"
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="browser-fixtures-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name).resolve() / "output"
        patches = mock.patch.multiple(acceptance, OUTPUT=self.output, MARKER=self.output / ".fixture-owner")
        patches.start()
        self.addCleanup(patches.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            acceptance.prepare()
        self.expected = json.loads((self.output / "expectations.json").read_text("utf-8"))

    def review(self, name):
        return json.loads((self.output / name / "review.json").read_text("utf-8"))

    @staticmethod
    def line_count(file, side="new"):
        return sum(
            block["text"].count("\n") + (not block["text"].endswith("\n"))
            for block in file["blocks"] if block[side + "_start"] is not None
        )

    def assert_metrics(self, review, expected, source=None):
        files = {file["path"]: file for file in review["files"]}
        comments = [comment for file in files.values() for comment in file["comments"]]
        starts = Counter(comment["start_line"] for comment in comments)
        actual = {
            "mode": review["mode"], "targets": len(files),
            "excluded": sum(not item["selected"] for item in review["inventory"]),
            "comments": len(comments),
            "verified": sum(comment["hash_status"] == "verified" for comment in comments),
            "missing_hash": sum(comment["hash_status"] == "missing" for comment in comments),
            "mismatch": sum(comment["hash_status"] == "mismatch" for comment in comments),
            "missing_range": sum(comment["range_status"] == "missing" for comment in comments),
            "unattached": len(review["unattached_comments"]),
            "diagrams": sum(len(comment.get("diagrams", [])) for comment in comments),
            "overlapping_start_line": next((line for line, count in starts.items() if count > 1), None),
        }
        for kind, key in (("added", "added_rows"), ("removed", "removed_rows")):
            actual[key] = sum(
                block["text"].count("\n") + (not block["text"].endswith("\n"))
                for file in files.values() for block in file["blocks"] if block["kind"] == kind
            )
        if "lines" in expected:
            actual["lines"] = self.line_count(files[source])
        if "main_lines" in expected:
            actual["main_lines"] = self.line_count(files["main.py"])
        if "deleted" in expected:
            name = expected["deleted"]
            self.assertEqual(next(item["status"] for item in review["inventory"] if item["path"] == name), "deleted")
            actual["deleted_lines"] = self.line_count(files[name], "old")
        # 新しい件数の項目を追加した場合も、照合し忘れたまま通さない。
        numeric = {key for key, value in expected.items() if type(value) is int}
        self.assertFalse(numeric - actual.keys(), f"未検証の期待値: {numeric - actual.keys()}")
        for key in expected.keys() & actual.keys():
            self.assertEqual(actual[key], expected[key], key)

    def test_generated_cases_match_all_numeric_expectations(self):
        cases = ("full", "diff", "unmerged", "no-targets", "large", "long", "diagrams", "metadata")
        for name in cases:
            with self.subTest(case=name):
                self.assertTrue((self.output / name / "index.html").is_file())
                self.assert_metrics(self.review(name), self.expected[name], source=name + ".py")
        large = self.review("large")
        self.assertEqual(self.line_count(next(file for file in large["files"] if file["path"] == "large.py")), 50000)
        self.assertEqual(sum(len(file["comments"]) for file in large["files"]), 2)
        unmerged = {file["path"]: file for file in self.review("unmerged")["files"]}
        self.assertEqual(self.line_count(unmerged[self.expected["unmerged"]["empty_file"]]), 0)
        self.assertGreater(self.line_count(unmerged[self.expected["unmerged"]["nonempty_file"]]), 0)
        base = self.expected["clipboard-failure"]["base"]
        clipboard = self.review("clipboard-failure")
        self.assert_metrics(clipboard, self.expected[base])
        self.assertEqual(clipboard["files"], self.review(base)["files"])

    def test_hash_phases_match_expectations_and_keep_snapshot_until_rebuild(self):
        self.assert_metrics(self.review("hash"), self.expected["hash_initial"])
        index = self.output / "hash/index.html"
        review_json = self.output / "hash/review.json"
        before = index.read_bytes(), review_json.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            acceptance.mutate_hash()
        expected = self.expected["hash_mutated_source_only"]
        snapshot = self.review("hash")
        main = next(file for file in snapshot["files"] if file["path"] == "main.py")
        self.assertEqual(self.line_count(main), expected["snapshot_main_lines"])
        source = (self.output / "projects/hash/main.py").read_text("utf-8")
        self.assertEqual(len(source.splitlines()), expected["source_main_lines"])
        self.assert_metrics(snapshot, {"verified": expected["verified"]})
        self.assertEqual(index.read_bytes() == before[0], expected["html_unchanged"])
        self.assertEqual(review_json.read_bytes(), before[1])
        with contextlib.redirect_stdout(io.StringIO()):
            acceptance.rebuild_hash()
        self.assert_metrics(self.review("hash"), self.expected["hash_rebuilt"])


if __name__ == "__main__":
    unittest.main()
