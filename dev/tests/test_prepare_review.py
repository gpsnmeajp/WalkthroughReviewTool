"""AI用準備処理の対象・出力分離と、後続CLIで使えるセッションを検証する。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import prepare_review as prepare


ROOT = Path(__file__).resolve().parents[1]


class PrepareReviewTests(unittest.TestCase):
    def setUp(self):
        """作成物と削除範囲をリポジトリ内の専用一時領域に限定する。"""
        temporary_parent = ROOT / ".test-tmp"
        temporary_parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="prepare-", dir=temporary_parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "日本語 プロジェクト"
        self.project.mkdir()
        (self.project / "main.py").write_bytes(b"print('hello')\n")
        (self.project / ".hidden").write_bytes(b"metadata\n")
        (self.project / "blob.bin").write_bytes(b"\x00\xff")
        self.parent = self.root / "review work"

    def run_python(self, script, *arguments, cwd=None, expected=0):
        """引数配列で起動し、日本語パスとUTF-8の標準出力を保存する。"""
        result = subprocess.run(
            [sys.executable, str(script), *map(str, arguments)],
            cwd=cwd or self.root,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_default_project_is_callers_cwd_and_stdout_is_session_json(self):
        result = self.run_python(
            ROOT / "tools/prepare_review.py",
            "--work-parent", self.parent, cwd=self.project,
        )
        session = json.loads(result.stdout)
        self.assertEqual(session["project"], self.project.as_posix())
        saved = json.loads((Path(session["work"]) / "session.json").read_text("utf-8"))
        self.assertEqual(saved, session)
        self.assertTrue(all(Path(value).is_absolute() for value in session.values()))
        self.assertFalse(Path(session["targets"]).exists())
        self.assertFalse(Path(session["comments"]).exists())
        self.assertFalse(Path(session["review_dir"]).exists())

    def test_every_file_is_listed_without_reading_or_selecting_contents(self):
        session = prepare.prepare_review(self.project, self.parent)
        data = json.loads(Path(session["inventory"]).read_text("utf-8"))
        self.assertEqual(data["project_root"], self.project.as_posix())
        self.assertEqual(
            {item["path"] for item in data["inventory"]},
            {".hidden", "blob.bin", "main.py"},
        )
        self.assertTrue(all(not item["selected"] for item in data["inventory"]))
        self.assertNotIn("print('hello')", Path(session["inventory"]).read_text("utf-8"))

    def test_repeated_runs_keep_existing_work_and_create_unique_directories(self):
        first = prepare.prepare_review(self.project, self.parent)
        retained = Path(first["work"]) / "keep.txt"
        retained.write_bytes(b"keep")
        second = prepare.prepare_review(self.project, self.parent)
        self.assertNotEqual(first["work"], second["work"])
        self.assertEqual(retained.read_bytes(), b"keep")
        self.assertEqual(
            len(list(self.parent.iterdir())), 2,
        )

    def test_default_output_uses_os_temp_outside_project(self):
        with mock.patch.object(prepare.tempfile, "gettempdir", return_value=str(self.root)):
            session = prepare.prepare_review(self.project)
        self.assertEqual(Path(session["work"]).parent, self.root / "walkthrough-review-work")
        self.assertFalse(Path(session["work"]).is_relative_to(self.project))

    def test_output_inside_project_is_rejected_before_creation(self):
        rejected = self.project / "review-output"
        result = self.run_python(
            ROOT / "tools/prepare_review.py", self.project,
            "--work-parent", rejected, expected=2,
        )
        self.assertIn("プロジェクトフォルダの外", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(rejected.exists())

    def test_missing_project_does_not_create_work_parent(self):
        with self.assertRaisesRegex(ValueError, "見つかりません"):
            prepare.prepare_review(self.root / "absent", self.parent)
        self.assertFalse(self.parent.exists())

    def test_reparse_parent_is_rejected_without_writing(self):
        """リンク作成権限に依存せず、実在する親のリパース検出結果を模擬する。"""
        original = prepare.walkthrough.is_link
        with mock.patch.object(
            prepare.walkthrough, "is_link",
            side_effect=lambda path: path == self.root or original(path),
        ):
            with self.assertRaisesRegex(ValueError, "リンク"):
                prepare.prepare_review(self.project, self.parent)
        self.assertFalse(self.parent.exists())

    def test_returned_paths_support_build_and_verified_merge(self):
        session = prepare.prepare_review(self.project, self.parent)
        Path(session["targets"]).write_text(
            json.dumps({"title": "準備からの実行", "files": ["main.py"]}), encoding="utf-8",
        )
        self.run_python(
            session["cli"], "build", session["project"],
            "--targets", session["targets"], "--output", session["review_dir"],
        )
        review_path = Path(session["review_dir"]) / "review.json"
        review = json.loads(review_path.read_text("utf-8"))
        comments = {
            "overview": "# 動作\n挨拶を標準出力へ表示する。",
            "comments": [{
                "id": "hello", "file": "main.py", "start_line": 1, "end_line": 1,
                "side": "new", "code_hash": review["files"][0]["code_hashes"]["new"],
                "title": "表示", "body": "このプログラムの出力はhelloの一行です。",
            }],
        }
        Path(session["comments"]).write_text(json.dumps(comments), encoding="utf-8")
        self.run_python(
            session["cli"], "merge", session["review_dir"], "--comments", session["comments"],
        )
        final = json.loads(review_path.read_text("utf-8"))
        self.assertEqual(final["files"][0]["comments"][0]["hash_status"], "verified")
        self.assertTrue(Path(session["index"]).is_file())
        self.assertTrue((Path(session["review_dir"]) / "self-review.md").is_file())


if __name__ == "__main__":
    unittest.main()
