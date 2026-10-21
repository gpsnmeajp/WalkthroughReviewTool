"""カード単位の作成・並行保存・再開・照合・集約を実ファイルで検証する。"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import comment_cards as c
import walkthrough as w


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT.parent / "skills/walkthrough-review/scripts/comment_cards.py"


class CommentCardTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-tmp"
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="cards-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "main.py").write_bytes(b"first\nsecond\nthird\n")
        (self.project / "helper.py").write_bytes(b"helper\n")
        (self.project / "empty.py").write_bytes(b"")
        self.review_dir, self.store = self.root / "review", self.root / "authoring"
        self.build()
        c.initialize(self.review_dir, self.store)
        self.body = self.root / "body.md"
        self.body.write_text("値を検証して次の処理へ渡す。\n", encoding="utf-8")

    def build(self, targets=None, patch=None, preserve=False):
        review = w.create_review(self.project, {"files": targets or ["main.py"]}, patch)
        if preserve:
            previous = w.read_json(self.review_dir / "review.json")
            w.preserve_comments(previous, review)
        w.write_review(review, self.review_dir)
        return review

    def card(self, ident="main-loading", **changes):
        value = {"id": ident, "file": "main.py", "start_line": 1, "end_line": 2,
                 "title": "入力の確認", "body": "入力を確認して値を渡す。"}
        value.update(changes)
        return value

    def put(self, ident="main-loading", **changes):
        return c.put_card(self.store, ident.split("-")[0], self.card(ident, **changes))

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=self.root,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def complete(self):
        c.set_overview(self.store, "# 全体\n[入口](review:main.py#L1)を確認する。")
        c.mark_reviewed(self.store, "main.py", "reviewer")

    def test_add_update_and_resume_keep_other_cards_and_outputs(self):
        html = (self.review_dir / "index.html").read_bytes()
        self.put()
        self.put("ui-second", start_line=3, end_line=3)
        first = c.show_card(self.store, "main", "main-loading")["card"]
        self.assertEqual(first["hash_status"], "verified")
        self.assertEqual(first["range_status"], "valid")
        self.assertEqual(first["code_hash"], w.read_json(self.review_dir / "review.json")["files"][0]["code_hashes"]["new"])
        c.put_card(self.store, "main", {"id": "main-loading", "body": "修正した説明"}, update=True)
        self.assertEqual(c.show_card(self.store, "ui", "ui-second")["card"]["body"], "入力を確認して値を渡す。")
        self.assertTrue(c.initialize(self.review_dir, self.store)["resumed"])
        self.assertEqual(c.status(self.store)["cards"], 2)
        self.assertEqual((self.review_dir / "index.html").read_bytes(), html)

    def test_duplicate_ids_and_wrong_owner_do_not_overwrite(self):
        self.put()
        before = (self.store / "cards/main/main-loading.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "重複"):
            self.put(body="overwrite")
        with self.assertRaisesRegex(ValueError, "担当"):
            c.put_card(self.store, "other", {"id": "main-loading", "body": "overwrite"}, update=True)
        with self.assertRaisesRegex(ValueError, "担当名"):
            c.put_card(self.store, "main", self.card("foreign-id"))
        self.assertEqual((self.store / "cards/main/main-loading.json").read_bytes(), before)

    def test_case_collisions_do_not_overwrite_or_remove_existing_cards(self):
        """大文字・小文字だけ違うIDや担当を、Windowsでも別カードとして保存しない。"""
        self.put("main-first")
        path = self.store / "cards/main/main-first.json"
        before = path.read_bytes()
        for owner, ident in (("main", "main-FIRST"), ("MAIN", "MAIN-first"), ("MAIN", "MAIN-second")):
            with self.subTest(owner=owner, ident=ident), self.assertRaises(ValueError):
                c.put_card(self.store, owner, self.card(ident, body="上書きしてはいけない"))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(c.status(self.store)["cards"], 1)
        for owner, ident in (("MAIN", "main-first"), ("main", "main-FIRST")):
            with self.subTest(owner=owner, ident=ident), self.assertRaises(ValueError):
                c.remove_card(self.store, owner, ident)
            self.assertEqual(path.read_bytes(), before)

    def test_empty_owner_directory_keeps_its_original_case(self):
        self.put("main-first")
        c.remove_card(self.store, "main", "main-first")
        with self.assertRaises(ValueError):
            c.put_card(self.store, "MAIN", self.card("MAIN-second"))
        self.put("main-second")
        self.assertEqual(c.show_card(self.store, "main", "main-second")["card"]["id"], "main-second")

    def test_imported_case_collisions_are_rejected_before_store_creation(self):
        """既存レビューの有効な別IDも、ストア上で衝突するなら無言で上書きしない。"""
        review = w.read_json(self.review_dir / "review.json")
        code_hash = review["files"][0]["code_hashes"]["new"]
        w.merge_comments(review, {"overview": "概要", "comments": [
            self.card("main-first", code_hash=code_hash),
            self.card("main-FIRST", code_hash=code_hash),
        ]})
        w.write_review(review, self.review_dir)
        destination = self.root / "imported-authoring"
        with self.assertRaisesRegex(ValueError, "重複"):
            c.initialize(self.review_dir, destination)
        self.assertFalse(destination.exists())

    def test_uppercase_authoring_hash_allows_title_only_update(self):
        """mergeで照合済みの大文字Hashは再確認を要求せず、記録した表記も保つ。"""
        review = w.read_json(self.review_dir / "review.json")
        code_hash = review["files"][0]["code_hashes"]["new"].upper()
        original = self.card(code_hash=code_hash)
        w.merge_comments(review, {"overview": "概要", "comments": [original]})
        self.assertEqual(review["files"][0]["comments"][0]["hash_status"], "verified")
        w.write_review(review, self.review_dir)
        store = self.root / "imported-authoring"
        c.initialize(self.review_dir, store)
        c.put_card(store, "existing", {"id": original["id"], "title": "修正した題名"}, update=True)
        updated = c.show_card(store, "existing", original["id"])["card"]
        self.assertEqual(updated["title"], "修正した題名")
        self.assertEqual(updated["body"], original["body"])
        self.assertEqual(updated["code_hash"], code_hash)
        self.assertEqual(updated["hash_status"], "verified")

    def test_invalid_card_links_ranges_svg_and_hash_input_are_rejected(self):
        for changes in ({"end_line": 999}, {"body": "[不在](review:missing.py#L1)"},
                        {"side": "wrong"}, {"code_hash": "0" * 64},
                        {"diagrams": [{"title": "図", "description": "説明", "svg": '<svg viewBox="0 0 10 10"><script/></svg>'}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.put(**changes)
        self.assertEqual(c.status(self.store)["cards"], 0)

    def test_cli_accepts_markdown_and_svg_without_large_json(self):
        svg = self.root / "diagram.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><text x="5" y="25">入力</text></svg>', encoding="utf-8")
        description = self.root / "description.md"
        description.write_text("入力を次の処理へ渡す。", encoding="utf-8")
        result = self.cli("add", self.store, "--owner", "main", "--id", "main-loading", "--file", "main.py",
                          "--start", 1, "--end", 2, "--title", "入力", "--body-file", self.body,
                          "--svg-file", svg, "--diagram-title", "値の流れ", "--diagram-description-file", description)
        self.assertLess(len(result.stdout), 200)
        self.assertNotIn("入力を次の処理", result.stdout)
        shown = json.loads(self.cli("show", self.store, "--owner", "main", "--id", "main-loading").stdout)
        self.assertEqual(shown["card"]["diagrams"][0]["svg"], svg.read_text("utf-8"))
        self.assertEqual(shown["card"]["body"], self.body.read_text("utf-8"))

    def test_long_source_line_and_blank_lines_are_paginated_without_loss(self):
        text = "日本語" * 9000 + "\n\nlast\vpart"
        (self.project / "main.py").write_text(text, encoding="utf-8")
        self.build()
        store = self.root / "long-authoring"
        c.initialize(self.review_dir, store)
        position, fragments = {"start": 1, "offset": 0}, []
        while position is not None:
            page = c.read_source(store, "main.py", limit=2, max_chars=1000, **position)
            self.assertLessEqual(sum(len(line["text"]) for line in page["lines"]), 1000)
            for line in page["lines"]:
                fragments.append(line["text"] + ("\n" if line["line_complete"] and line["newline"] else ""))
            position = page["next"]
        self.assertEqual("".join(fragments), text)
        with self.assertRaises(ValueError):
            c.read_source(store, "main.py", offset=999999)

    def test_old_deleted_source_and_empty_version_are_readable(self):
        patch = "--- a/legacy.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old value\n"
        self.build(["legacy.py"], patch)
        store = self.root / "diff-authoring"
        c.initialize(self.review_dir, store)
        page = c.read_source(store, "legacy.py", "old")
        self.assertEqual(page["lines"][0]["text"], "old value")
        self.assertEqual(c.read_source(store, "legacy.py", "new")["lines"], [])
        c.put_card(store, "old", self.card("old-legacy", file="legacy.py", side="old", end_line=1))
        self.assertEqual(c.show_card(store, "old", "old-legacy")["card"]["hash_status"], "verified")

    def test_export_requires_overview_and_current_review_receipts(self):
        self.put()
        output = self.root / "comments.json"
        with self.assertRaisesRegex(ValueError, "未確認"):
            c.export_comments(self.store, output)
        c.mark_reviewed(self.store, "main.py", "reviewer")
        with self.assertRaisesRegex(ValueError, "概要"):
            c.export_comments(self.store, output)
        c.set_overview(self.store, "# 説明")
        c.export_comments(self.store, output)
        payload = w.read_json(output)
        review = w.read_json(self.review_dir / "review.json")
        w.merge_comments(review, payload)
        self.assertEqual(len(review["files"][0]["comments"]), 1)
        self.assertFalse(review["hash_warnings"])
        w.write_review(review, self.review_dir)
        self.assertEqual(c.status(self.store)["reviewed_files"], 1)
        c.put_card(self.store, "main", {"id": "main-loading", "body": "修正"}, update=True)
        self.assertEqual(c.status(self.store)["reviewed_files"], 0)
        saved = output.read_bytes()
        with self.assertRaises(ValueError):
            c.export_comments(self.store, output)
        self.assertEqual(output.read_bytes(), saved)

    def test_nonempty_without_cards_needs_reason_and_empty_file_can_be_checked(self):
        with self.assertRaisesRegex(ValueError, "理由"):
            c.mark_reviewed(self.store, "main.py", "reviewer")
        c.mark_reviewed(self.store, "main.py", "reviewer", "宣言のみなので概要で説明する")
        self.assertTrue(c.status(self.store)["items"][0]["no_card_reason"])
        self.build(["empty.py"])
        store = self.root / "empty-authoring"
        c.initialize(self.review_dir, store)
        c.mark_reviewed(store, "empty.py", "reviewer")
        self.assertEqual(c.read_source(store, "empty.py")["total_lines"], 0)
        self.assertEqual(c.status(store)["reviewed_files"], 1)

    def test_rebuild_changes_block_old_store_and_preserve_authoring_hash(self):
        self.put()
        self.complete()
        payload_path = self.root / "comments.json"
        c.export_comments(self.store, payload_path)
        review = w.read_json(self.review_dir / "review.json")
        w.merge_comments(review, w.read_json(payload_path))
        w.write_review(review, self.review_dir)
        old_hash = review["files"][0]["comments"][0]["code_hash"]
        (self.project / "main.py").write_bytes(b"new first\nnew second\n")
        self.build(preserve=True)
        with self.assertRaisesRegex(ValueError, "コードまたは比較条件"):
            c.put_card(self.store, "main", {"id": "main-loading", "body": "old"}, update=True)
        store = self.root / "new-authoring"
        c.initialize(self.review_dir, store)
        shown = c.show_card(store, "existing", "main-loading")["card"]
        self.assertEqual(shown["code_hash"], old_hash)
        self.assertEqual(shown["hash_status"], "mismatch")
        with self.assertRaisesRegex(ValueError, "confirm-code"):
            c.put_card(store, "existing", {"id": "main-loading", "title": "タイトルのみ"}, update=True)
        with self.assertRaises(ValueError):
            c.put_card(store, "existing", {"id": "main-loading", "title": "タイトルのみ"}, update=True, confirm_code=True)
        c.put_card(store, "existing", {"id": "main-loading", "body": "新本文を読み直して修正"}, update=True, confirm_code=True)
        self.assertEqual(c.show_card(store, "existing", "main-loading")["card"]["hash_status"], "verified")

    def test_unmerged_work_can_move_to_new_snapshot_without_losing_cards(self):
        self.put()
        original = c.show_card(self.store, "main", "main-loading")["card"]
        c.set_overview(self.store, "# 作業中の概要")
        (self.project / "main.py").write_bytes(b"changed first\nchanged second\n")
        self.build()
        destination = self.root / "continued"
        c.initialize(self.review_dir, destination, from_store=self.store)
        moved = c.show_card(destination, "main", "main-loading")["card"]
        self.assertEqual(moved["body"], original["body"])
        self.assertEqual(moved["code_hash"], original["code_hash"])
        self.assertEqual(moved["hash_status"], "mismatch")
        self.assertEqual((destination / "overview.md").read_text("utf-8"), "# 作業中の概要")
        self.assertEqual(c.status(destination)["reviewed_files"], 0)

    def test_target_change_keeps_unattached_cards_until_explicit_removal(self):
        self.put()
        self.build(["helper.py"])
        store = self.root / "changed-targets"
        c.initialize(self.review_dir, store, from_store=self.store)
        report = c.status(store)
        self.assertEqual(report["unattached_cards"], 1)
        self.assertEqual(report["unattached"][0]["id"], "main-loading")
        self.assertEqual(c.show_card(store, "main", "main-loading")["card"]["body"], "入力を確認して値を渡す。")
        c.mark_reviewed(store, "helper.py", "reviewer", "定数のみ")
        c.set_overview(store, "# helperだけを確認")
        output = self.root / "changed-comments.json"
        with self.assertRaises(ValueError):
            c.export_comments(store, output)
        self.assertFalse(output.exists())
        c.remove_card(store, "main", "main-loading")
        c.export_comments(store, output)
        self.assertEqual(w.read_json(output)["comments"], [])

    def test_diff_metadata_change_invalidates_store_without_text_change(self):
        review = w.read_json(self.review_dir / "review.json")
        before_hash = review["files"][0]["code_hashes"]
        review["files"][0]["diff_metadata"] = {"old mode": "100644", "new mode": "100755"}
        review["files"][0]["diff_summary"] = ["権限: 100644 → 100755"]
        w.write_review(review, self.review_dir)
        self.assertEqual(review["files"][0]["code_hashes"], before_hash)
        with self.assertRaisesRegex(ValueError, "比較条件"):
            c.status(self.store)

    def test_parallel_cli_writers_preserve_every_card_and_export_order(self):
        def write(index):
            owner = "python" if index % 2 else "ui"
            return self.cli("add", self.store, "--owner", owner, "--id", f"{owner}-{index}", "--file", "main.py",
                            "--start", 1, "--end", 1, "--title", str(index), "--body-file", self.body)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(12)))
        self.assertEqual(c.status(self.store)["cards"], 12)
        self.complete()
        output = self.root / "comments.json"
        c.export_comments(self.store, output)
        ids = [card["id"] for card in w.read_json(output)["comments"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 12)

    def test_failed_atomic_update_keeps_previous_card(self):
        self.put()
        path = self.store / "cards/main/main-loading.json"
        saved = path.read_bytes()
        with mock.patch.object(c.os, "replace", side_effect=OSError("injected replace failure")):
            with self.assertRaises(OSError):
                c.put_card(self.store, "main", {"id": "main-loading", "body": "changed"}, update=True)
        self.assertEqual(path.read_bytes(), saved)
        self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_removed_card_is_archived_and_receipt_becomes_stale(self):
        self.put()
        self.complete()
        c.remove_card(self.store, "main", "main-loading")
        self.assertEqual(c.status(self.store)["cards"], 0)
        self.assertEqual(c.status(self.store)["reviewed_files"], 0)
        self.assertEqual(len(list((self.store / "removed").glob("*.json"))), 1)

    def test_snapshot_tampering_and_manual_duplicate_card_are_detected(self):
        self.put()
        duplicate = self.store / "cards/other/main-loading.json"
        duplicate.parent.mkdir()
        duplicate.write_bytes((self.store / "cards/main/main-loading.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "重複"):
            c.status(self.store)
        duplicate.unlink()
        (self.store / "snapshot.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "固定スナップショット"):
            c.read_source(self.store, "main.py")

    def test_paths_and_unrelated_existing_directories_are_not_overwritten(self):
        with self.assertRaises(ValueError):
            c.initialize(self.review_dir, self.project / "cards")
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            c.initialize(self.review_dir, unrelated)
        for invalid in ("../other", "CON", "/abs"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                c.put_card(self.store, invalid, self.card())
        self.put()
        self.complete()
        with self.assertRaises(ValueError):
            c.export_comments(self.store, self.store / "snapshot.json")

    def test_status_is_paged_and_does_not_echo_card_bodies(self):
        self.put()
        self.put("main-two", title="二枚目")
        page = c.status(self.store, "main.py", limit=1)
        self.assertEqual(page["next_offset"], 1)
        self.assertEqual(page["items"][0]["owner"], "main")
        self.assertNotIn("body", page["items"][0])
        result = self.cli("status", self.store, "--limit", 0, expected=2)
        self.assertIn("件数", result.stderr)


if __name__ == "__main__":
    unittest.main()
