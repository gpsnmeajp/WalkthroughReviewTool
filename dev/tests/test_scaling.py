"""大きな対象外差分の解放と、範囲照合の計算量・意味を検証する。"""
import gc
from pathlib import Path
import random
import tempfile
import tracemalloc
import unittest
from unittest import mock

import diff_parser
import walkthrough


class ScalingTests(unittest.TestCase):
    def test_patch_iterator_validates_late_errors_and_duplicate_paths(self):
        first = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\n"
        for tail in (first, "--- a/g\n+++ b/g\n@@ -1 +1 @@\n-x\n"):
            stream = diff_parser.iter_patch(first + tail)
            self.assertEqual(next(stream)["new_path"], "f")
            with self.assertRaises(ValueError):
                list(stream)

    def test_row_iterator_still_checks_late_mismatch(self):
        entry = diff_parser.parse_patch("--- a/f\n+++ b/f\n@@ -2 +2 @@\n-old\n+new\n")[0]
        rows = diff_parser.iter_build_rows(entry, "first\nwrong\n")
        self.assertEqual(next(rows)["text"], "first")
        with self.assertRaises(ValueError):
            list(rows)

    def test_unselected_patch_memory_does_not_accumulate_by_file_count(self):
        parent = Path(__file__).resolve().parents[1] / ".test-tmp"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            project = Path(temporary)
            text = "data = '" + "x" * 80 + "'\n"
            body = text * 4000
            patches = []
            for index in range(6):
                name = f"file-{index}.py"
                (project / name).write_bytes(body.encode())
                patches.append(f"--- a/{name}\n+++ b/{name}\n@@ -1,4000 +1,4000 @@\n" + (" " + text) * 4000)
            # 入力の本文・パッチ自体は事前に用意し、create_reviewの追加確保だけを測る。
            inputs = (patches[0], "".join(patches))
            peaks = []
            with mock.patch.object(walkthrough, "annotate_git_ignored"):
                for patch in inputs:
                    gc.collect()
                    tracemalloc.start()
                    try:
                        review = walkthrough.create_review(project, {"files": []}, patch)
                        peaks.append(tracemalloc.get_traced_memory()[1])
                    finally:
                        tracemalloc.stop()
                    self.assertEqual(review["files"], [])
                    self.assertEqual(len(review["inventory"]), 6)
            self.assertLess(peaks[1], peaks[0] * 1.5 + 100000, peaks)
            # 対象0件でも、最後の対象外ファイルの本文不一致を検出する。
            with self.assertRaises(ValueError):
                walkthrough.create_review(project, {"files": []}, inputs[1].replace(" data =", " wrong =", 1))

    def test_merged_coverage_matches_reference_for_both_versions(self):
        rows = [
            {"old": 1, "new": 1, "kind": "context", "text": "a", "newline": True},
            {"old": 2, "new": None, "kind": "removed", "text": "old", "newline": True},
            {"old": None, "new": 2, "kind": "added", "text": "new", "newline": True},
        ] + [{"old": n, "new": n, "kind": "context", "text": "rest", "newline": True} for n in range(3, 51)]
        randomizer = random.Random(137)
        for _ in range(30):
            comments = []
            for _ in range(35):
                start = randomizer.randint(1, 50)
                comment = {"side": randomizer.choice(["old", "new"]), "start_line": start,
                           "end_line": randomizer.randint(start, 50), "range_status": randomizer.choice(["missing", "valid"])}
                comments.append(comment)
            expected = {"old": [], "new": []}
            for row in rows:
                if not any(c["range_status"] != "missing" and row[c["side"]] is not None and
                           c["start_line"] <= row[c["side"]] <= c["end_line"] for c in comments):
                    side = "new" if row["new"] is not None else "old"
                    expected[side].append(row[side])
            file = {"blocks": walkthrough.pack_rows(rows), "comments": comments}
            self.assertEqual(walkthrough.uncovered_lines(file), expected)

    def test_coverage_reads_comments_once_instead_of_once_per_line(self):
        class Counted(dict):
            reads = 0

            def get(self, *args):
                type(self).reads += 1
                return super().get(*args)

        comments = [Counted(start_line=n, end_line=n + 1) for n in range(1, 40000, 20)]
        file = {"blocks": [{"kind": "context", "old_start": 1, "new_start": 1, "text": "x\n" * 40000}], "comments": comments}
        result = walkthrough.uncovered_lines(file)
        self.assertEqual(len(result["new"]), 36000)
        self.assertEqual(result["old"], [])
        self.assertLessEqual(Counted.reads, len(comments) * 2)


if __name__ == "__main__":
    unittest.main()
