"""スキル単体配布の内容と、展開先だけで動く全文・差分レビューを検証する。"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import package_skill as package


class PackageSkillTests(unittest.TestCase):
    """実際の配布先を変更せず、専用の作業先へソースを複製して試験する。"""

    def setUp(self):
        """テストごとに独立したcanonical skillフォルダを作る。"""
        parent = package.ROOT / "dev" / ".test-tmp"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="skill-package-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.skill = self.workspace / "配布 元" / package.SKILL_NAME
        shutil.copytree(package.SKILL_ROOT, self.skill)
        self.output = self.workspace / "成果物"

    def run_python(self, script, *arguments):
        """展開先のCLIを別プロセスで実行し、リポジトリのimportに依存させない。"""
        result = subprocess.run(
            [sys.executable, str(script), *map(str, arguments)],
            cwd=self.workspace,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": ""},
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_archive_contains_only_runtime_skill_with_matching_checksums(self):
        """他の成果物・evals・キャッシュを除き、.skillと.zipの内容が一致する。"""
        for name in ("evals/evals.json", "scripts/__pycache__/private.pyc", "private.txt"):
            path = self.skill / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"not shipped")
        paths = package.build_package(self.skill, self.output)
        self.assertEqual([path.name for path in paths], [
            "walkthrough-review.skill", "walkthrough-review.zip",
            "walkthrough-review.skill.sha256", "walkthrough-review.zip.sha256",
        ])
        payload = package.collect_payload(self.skill)
        archive_bytes = paths[0].read_bytes()
        self.assertEqual(archive_bytes, paths[1].read_bytes())
        for archive_path, checksum in ((paths[0], paths[2]), (paths[1], paths[3])):
            self.assertEqual(
                checksum.read_text("ascii"),
                f"{hashlib.sha256(archive_bytes).hexdigest()}  {archive_path.name}\n",
            )
        with zipfile.ZipFile(paths[0]) as archive:
            self.assertEqual(
                set(archive.namelist()), {f"walkthrough-review/{name}" for name in payload}
            )
            for name, content in payload.items():
                self.assertEqual(archive.read(f"walkthrough-review/{name}"), content)

    def test_repeated_package_is_byte_identical_and_preserves_other_outputs(self):
        """日時・列挙順による差を防ぎ、同じフォルダのリポジトリZIPも保持する。"""
        paths = package.build_package(self.skill, self.output)
        original = tuple(path.read_bytes() for path in paths)
        unrelated = self.output / "WalkthroughReviewTool.zip"
        unrelated.write_bytes(b"independent repo archive")
        repeated = package.build_package(self.skill, self.output)
        self.assertEqual(tuple(path.read_bytes() for path in repeated), original)
        self.assertEqual(unrelated.read_bytes(), b"independent repo archive")
        self.assertFalse(list(self.output.glob(".skill-package-*")))

    def test_validation_failure_keeps_previous_outputs(self):
        """候補ZIPの検証に失敗したとき、前回の正式成果物を書き換えない。"""
        paths = package.build_package(self.skill, self.output)
        original = tuple(path.read_bytes() for path in paths)
        with mock.patch.object(package, "verify_archive", side_effect=ValueError("injected failure")):
            with self.assertRaisesRegex(ValueError, "injected failure"):
                package.build_package(self.skill, self.output)
        self.assertEqual(tuple(path.read_bytes() for path in paths), original)
        self.assertFalse(list(self.output.glob(".skill-package-*")))

    def test_invalid_output_kind_is_rejected_before_replacing_archives(self):
        """チェックサム名がフォルダのとき、既存ZIPを置換する前に停止する。"""
        paths = package.build_package(self.skill, self.output)
        original = paths[0].read_bytes()
        paths[2].unlink()
        paths[2].mkdir()
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            package.build_package(self.skill, self.output)
        self.assertEqual(paths[0].read_bytes(), original)

    def test_output_inside_skill_is_rejected(self):
        """入力スキルに配布物を混入させる出力先を拒否する。"""
        with self.assertRaisesRegex(ValueError, "outside the source skill"):
            package.build_package(self.skill, self.skill / "dist")
        self.assertFalse((self.skill / "dist").exists())

    def test_missing_runtime_asset_is_rejected_without_output(self):
        """同梱ライブラリの欠品は、利用者が受け取る前に検出する。"""
        (self.skill / "assets/ui/vendor/marked.js").unlink()
        with self.assertRaisesRegex(ValueError, "Required skill file is missing"):
            package.build_package(self.skill, self.output)
        self.assertFalse(self.output.exists())

    def test_frontmatter_requires_valid_supported_fields(self):
        """必須項目の欠落・別名・重複・不正なYAML形式を検出する。"""
        cases = (
            b"# no frontmatter\n",
            b"---\nname: walkthrough-review\n---\n",
            b"---\nname: other\ndescription: test\n---\n",
            b"---\nname: walkthrough-review\ndescription: test\nname: duplicate\n---\n",
            b"---\nname: walkthrough-review\ndescription: >\n  test\n---\n",
            b"---\nname: walkthrough-review\ndescription: test\nunknown: value\n---\n",
            b'---\nname: walkthrough-review\ndescription: ""\n---\n',
            b"---\nname: walkthrough-review\ndescription: true\n---\n",
            b"---\nname: walkthrough-review\ndescription: 'bad' 'quote'\n---\n",
        )
        for content in cases:
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    package.validate_frontmatter(content)
        fields = package.validate_frontmatter(
            b'---\nname: walkthrough-review\ndescription: "A review skill"\n'
            b"compatibility: 'Python 3.9+'\n---\n"
        )
        self.assertEqual(fields["compatibility"], "Python 3.9+")

    def test_local_markdown_links_must_stay_in_packaged_skill(self):
        """相対参照の欠落と親への脱出を検出し、実在資料・外部参照を許可する。"""
        valid = {
            "SKILL.md": (
                b"[CLI](scripts/walkthrough.py) [guide](references/guide.md#flow)\n"
                b"[site](https://example.com) [old](review:src/main.py#O2)\n"
                b"```markdown\n[example](not-a-real-file.md)\n```\n"
            ),
            "scripts/walkthrough.py": b"",
            "references/guide.md": b"[back](../SKILL.md)\n[ref]: ../scripts/walkthrough.py\n",
        }
        package.validate_markdown_links(valid)
        for target in ("missing.md", "../README.md", "/absolute/path"):
            payload = {**valid, "SKILL.md": f"[bad]({target})".encode()}
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    package.validate_markdown_links(payload)

    def test_references_to_unshipped_files_are_rejected(self):
        """ファイルがディスクにあっても、配布対象でない参照先は拒否する。"""
        (self.skill / "evals").mkdir(exist_ok=True)
        (self.skill / "evals/evals.json").write_bytes(b"{}")
        with (self.skill / "SKILL.md").open("a", encoding="utf-8") as handle:
            handle.write("\n[eval](evals/evals.json)\n")
        with self.assertRaisesRegex(ValueError, "not packaged"):
            package.build_package(self.skill, self.output)
        self.assertFalse(self.output.exists())

    def test_linked_source_parent_is_rejected_before_packaging(self):
        """実行権限に依存せず、素材の親ディレクトリ拒否時に生成しないことを確認する。"""
        original = package.reject_links

        def reject_assets(path):
            if self.skill / "assets" in path.parents:
                raise ValueError("linked assets parent")
            return original(path)

        with mock.patch.object(package, "reject_links", side_effect=reject_assets):
            with self.assertRaisesRegex(ValueError, "linked assets parent"):
                package.build_package(self.skill, self.output)
        self.assertFalse(self.output.exists())

    def test_verifier_rejects_extra_and_changed_members(self):
        """有効なZIP形式でも、余分なファイルや改変された本文は拒否する。"""
        candidate = self.workspace / "candidate.skill"
        payload = {"SKILL.md": b"original"}
        package.write_archive(candidate, {"SKILL.md": b"changed"})
        with self.assertRaisesRegex(ValueError, "differs from source"):
            package.verify_archive(candidate, payload)
        package.write_archive(candidate, payload)
        with zipfile.ZipFile(candidate, "a") as archive:
            archive.writestr("walkthrough-review/private.txt", b"extra")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            package.verify_archive(candidate, payload)

    def test_extracted_skill_prepares_and_builds_full_and_diff_without_source(self):
        """配布物だけで準備・全文・削除を含む差分・解説のマージが動くことを確認する。"""
        archive_path, *_ = package.build_package(self.skill, self.output)
        extracted = self.workspace / "単独 展開"
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                self.assertTrue((extracted / name).resolve().is_relative_to(extracted.resolve()))
            archive.extractall(extracted)
        self.skill.rename(self.workspace / "もう使わない元スキル")
        standalone = extracted / package.SKILL_NAME
        cli = standalone / "scripts/walkthrough.py"
        self.run_python(cli, "--help")
        project = self.workspace / "project"
        project.mkdir()
        (project / "main.py").write_text("value = 2\n", encoding="utf-8")
        prepared = self.run_python(
            standalone / "scripts/prepare_review.py", project,
            "--work-parent", self.workspace / "work",
        )
        session = json.loads(prepared.stdout)
        self.assertEqual(Path(session["cli"]), cli)
        self.assertTrue(Path(session["inventory"]).is_file())
        work = Path(session["work"])
        patch = work / "diff.patch"
        patch.write_text(
            "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
            "--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-legacy = True\n",
            encoding="utf-8",
        )
        for mode in ("full", "diff"):
            target_files = ["main.py"] if mode == "full" else ["main.py", "old.py"]
            targets = work / f"{mode}-targets.json"
            targets.write_text(json.dumps({"title": mode, "files": target_files}), encoding="utf-8")
            output = work / mode
            arguments = ["build", project, "--targets", targets, "--output", output]
            if mode == "diff":
                arguments.extend(["--patch", patch])
            self.run_python(cli, *arguments)
            data = json.loads((output / "review.json").read_text("utf-8"))
            comments = []
            for number, file in enumerate(data["files"]):
                side = "old" if file["path"] == "old.py" else "new"
                comments.append({
                    "id": f"comment-{number}", "file": file["path"], "side": side,
                    "start_line": 1, "end_line": 1, "code_hash": file["code_hashes"][side],
                    "title": "設定値", "body": "この行で値を定義します。",
                })
            comments_path = work / f"{mode}-comments.json"
            comments_path.write_text(
                json.dumps({"overview": "# 配布後の確認", "comments": comments}), encoding="utf-8"
            )
            self.run_python(cli, "merge", output, "--comments", comments_path)
            final = json.loads((output / "review.json").read_text("utf-8"))
            self.assertEqual(final["mode"], mode)
            self.assertTrue(all(
                comment["hash_status"] == "verified"
                for file in final["files"] for comment in file["comments"]
            ))
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "assets/vendor/marked.js").is_file())
            if mode == "diff":
                deleted = next(file for file in final["files"] if file["path"] == "old.py")
                self.assertEqual(deleted["rows"][0]["kind"], "removed")


if __name__ == "__main__":
    unittest.main()
