"""スキル単体の移動後も、元リポジトリに依存せず資料を生成できることを検証する。"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tools import install_skill as installer


class InstallSkillTests(unittest.TestCase):
    """実インストール先ではなく、作業ツリー内の専用一時領域で検証する。"""

    def setUp(self):
        """コピー元も一時領域へ複製し、移動・欠品・既存編集を安全に再現する。"""
        temporary_parent = installer.ROOT / "dev" / ".test-tmp"
        temporary_parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="skill-install-", dir=temporary_parent)
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.source = self.workspace / "配布 元"
        shutil.copytree(
            installer.ROOT / "skills/walkthrough-review",
            self.source / "skills/walkthrough-review",
        )
        self.skills_dir = self.workspace / "個人 skills"

    def run_python(self, script, *arguments, expected=0):
        """シェルを介さず別プロセスで起動し、実際の終了値と出力を確認する。"""
        result = subprocess.run(
            [sys.executable, str(script), *map(str, arguments)],
            cwd=self.workspace,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_installed_bundle_runs_after_source_and_skill_are_moved(self):
        """導入元がなくなり、スキル自体も移動した状態でscan/build/mergeが完了する。"""
        skill = installer.install_skill(self.skills_dir, self.source)
        moved = self.workspace / "移動 後" / "walkthrough-review"
        moved.parent.mkdir()
        skill.rename(moved)
        self.source.rename(self.workspace / "使わない コピー元")
        cli = moved / "scripts/walkthrough.py"
        self.run_python(cli, "--help")
        project = self.workspace / "対象 project"
        project.mkdir()
        (project / "main.py").write_text("def total(values):\n    return sum(values)\n", encoding="utf-8")
        prepared = self.run_python(
            moved / "scripts/prepare_review.py", project,
            "--work-parent", self.workspace / "レビュー work",
        )
        session = json.loads(prepared.stdout)
        self.assertEqual(Path(session["cli"]), cli)
        work = Path(session["work"])
        self.assertTrue(Path(session["inventory"]).is_file())
        self.run_python(cli, "scan", project, "--output", work / "inventory.json")
        targets = work / "targets.json"
        targets.write_text(json.dumps({"title": "試験", "files": ["main.py"]}), encoding="utf-8")
        review = work / "review"
        self.run_python(cli, "build", project, "--targets", targets, "--output", review)
        data = json.loads((review / "review.json").read_text("utf-8"))
        comments = work / "comments.json"
        comments.write_text(json.dumps({
            "overview": "# 合計処理\n入力を集計する。",
            "comments": [{
                "id": "total", "file": "main.py", "start_line": 1, "end_line": 2,
                "side": "new", "code_hash": data["files"][0]["code_hashes"]["new"],
                "title": "合計", "body": "引数の要素を集計して返します。",
            }],
        }), encoding="utf-8")
        self.run_python(cli, "merge", review, "--comments", comments)
        final = json.loads((review / "review.json").read_text("utf-8"))
        self.assertEqual(final["files"][0]["comments"][0]["hash_status"], "verified")
        self.assertTrue((review / "index.html").is_file())
        self.assertTrue((review / "self-review.md").is_file())
        self.assertTrue((review / "assets/vendor/marked.js").is_file())
        self.assertTrue((moved / "scripts/prepare_review.py").is_file())

    def test_identical_install_is_idempotent_and_preserves_unrelated_files(self):
        """同一版の再配置はファイルを書き直さず、既存の別ファイルも変更しない。"""
        skill = installer.install_skill(self.skills_dir, self.source)
        manifest = skill / "SKILL.md"
        original = (manifest.read_bytes(), manifest.stat().st_mtime_ns)
        extra = skill / "notes.txt"
        extra.write_bytes(b"keep")
        self.assertEqual(installer.install_skill(self.skills_dir, self.source), skill)
        self.assertEqual((manifest.read_bytes(), manifest.stat().st_mtime_ns), original)
        self.assertEqual(extra.read_bytes(), b"keep")

    def test_edited_install_is_rejected_without_overwriting_any_files(self):
        """旧スキルへの利用者の編集を、再実行で破壊しない。"""
        skill = installer.install_skill(self.skills_dir, self.source)
        changed = skill / "scripts/walkthrough.py"
        changed.write_bytes(b"my custom version")
        original_skill = (skill / "SKILL.md").read_bytes()
        with self.assertRaisesRegex(ValueError, "Existing skill differs"):
            installer.install_skill(self.skills_dir, self.source)
        self.assertEqual(changed.read_bytes(), b"my custom version")
        self.assertEqual((skill / "SKILL.md").read_bytes(), original_skill)

    def test_missing_dependency_does_not_leave_partial_install(self):
        """必要ファイルの欠落はコピー開始前に検出する。"""
        (self.source / "skills/walkthrough-review/assets/ui/vendor/marked.js").unlink()
        with self.assertRaisesRegex(ValueError, "Required skill file is missing"):
            installer.install_skill(self.skills_dir, self.source)
        self.assertFalse(self.skills_dir.exists())

    def test_optional_references_and_metadata_are_carried_with_skill(self):
        """追加された解説資料と表示設定は、リンク先の階層を保って同梱する。"""
        for name in ("references/nested/flow.md", "agents/openai.yaml"):
            path = self.source / "skills/walkthrough-review" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode("utf-8"))
        skill = installer.install_skill(self.skills_dir, self.source)
        for name in ("references/nested/flow.md", "agents/openai.yaml"):
            self.assertEqual((skill / name).read_bytes(), name.encode("utf-8"))

    def test_links_and_reparse_points_are_rejected(self):
        """Windows管理者権限なしでも、経路検査の拒否分岐を確認する。"""
        path = mock.Mock()
        path.parents = []
        path.is_symlink.return_value = True
        with self.assertRaisesRegex(ValueError, "Links cannot"):
            installer.reject_links(path)
        path.is_symlink.return_value = False
        path.exists.return_value = True
        path.lstat.return_value = SimpleNamespace(st_file_attributes=0x400)
        with self.assertRaisesRegex(ValueError, "Reparse points"):
            installer.reject_links(path)

    def test_parent_path_is_checked_before_destination_creation(self):
        """配置先自体が未作成でも、リンク相当の親を経由した書き込みは開始しない。"""
        original = installer.reject_links

        def reject_parent(path):
            if self.skills_dir in path.parents:
                raise ValueError("linked parent")
            return original(path)

        with mock.patch.object(installer, "reject_links", side_effect=reject_parent):
            with self.assertRaisesRegex(ValueError, "linked parent"):
                installer.install_skill(self.skills_dir, self.source)
        self.assertFalse(self.skills_dir.exists())

    def test_cli_requires_explicit_destination(self):
        """引数なしの実行がユーザー全体のスキルを暗黙に変更しない。"""
        result = self.run_python(installer.ROOT / "dev/tools/install_skill.py", expected=2)
        self.assertIn("--skills-dir", result.stderr)


if __name__ == "__main__":
    unittest.main()
