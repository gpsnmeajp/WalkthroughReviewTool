"""配布物の内容、失敗時の保存、展開後の実行を実ファイルで検証する。

各テストは専用の一時フォルダへ出荷対象をコピーする。リポジトリのdistや元コードは
変更しない。ZIPを作るだけでなく、収録内容の独立したハッシュ計算と、展開したCLIの
起動まで確認する。ブラウザーやネットワーク接続、外部Pythonパッケージは使用しない。
"""

import hashlib
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
import zipfile

from tools import package_release as release


class PackageReleaseTests(unittest.TestCase):
    """実行ごとに独立した出荷元を作り、成功・失敗のどちらも観測する。"""

    def setUp(self):
        """出荷一覧のファイルだけを日本語・空白を含むパスへコピーする。

        ファイル名の引用が不十分なバッチを検出するため、あえて単純な英数字パスにしない。
        TemporaryDirectoryが管理する専用領域だけを終了時に片付ける。
        """
        temporary_parent = release.ROOT / "dev" / ".test-tmp"
        temporary_parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="walkthrough-package-", dir=temporary_parent)
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.root = self.workspace / "出荷 テスト"
        for relative in release.SHIP_FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(release.ROOT / relative, target)

    def assert_no_temporary_files(self):
        """出荷ディレクトリに候補ZIP・ハッシュ用の一時ファイルが残っていないか調べる。"""
        self.assertEqual(list((self.root / "dev/dist").glob(".*.tmp")), [])

    def run_python(self, script, *arguments, cwd=None):
        """現在のPythonで子プロセスを起動し、終了値を確認して結果を返す。

        引数は配列で渡すためシェル展開されない。標準出力の文字コードだけをUTF-8に固定し、
        日本語パスを含むメッセージも親プロセスが損なわずに読めるようにする。
        """
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, str(script), *map(str, arguments)],
            cwd=cwd or self.workspace, env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_archive_contains_only_shipping_files_and_correct_hashes(self):
        """作業データが混入せず、元バイト列・内部一覧・ZIP全体のハッシュが一致する。

        出荷元へ紛らわしいファイルを置き、再帰コピーで収録してしまう回帰を検出する。
        期待ハッシュは出荷処理の関数を使わず、元ファイルから独立に計算する。
        """
        for relative in (".git/HEAD", ".env", "dev/output/private.json", "dev/.test-tmp/note.txt", "dev/dist/old.zip"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"NOT FOR SHIPPING")
        archive_path, checksum_path = release.build_package(self.root)
        prefix = release.PACKAGE_NAME + "/"
        with zipfile.ZipFile(archive_path) as archive:
            expected_names = {prefix + name for name in release.SHIP_FILES} | {prefix + "SHA256SUMS"}
            self.assertEqual(set(archive.namelist()), expected_names)
            self.assertEqual(len(archive.namelist()), len(expected_names))
            self.assertIsNone(archive.testzip())
            manifest = {}
            for line in archive.read(prefix + "SHA256SUMS").decode("utf-8").splitlines():
                digest, relative = line.split("  ", 1)
                manifest[relative] = digest
            self.assertEqual(set(manifest), set(release.SHIP_FILES))
            for relative in release.SHIP_FILES:
                original = (self.root / relative).read_bytes()
                self.assertEqual(archive.read(prefix + relative), original)
                self.assertEqual(manifest[relative], hashlib.sha256(original).hexdigest())
        expected_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        self.assertEqual(checksum_path.read_text("ascii"), f"{expected_hash}  {archive_path.name}\n")
        self.assertEqual((self.root / "dev/dist/old.zip").read_bytes(), b"NOT FOR SHIPPING")
        self.assert_no_temporary_files()

    def test_repeated_packaging_is_byte_identical(self):
        """同じ実行環境・入力内容なら、再出荷してもZIPと外部ハッシュの全バイトが同じになる。"""
        archive, checksum = release.build_package(self.root)
        original = archive.read_bytes(), checksum.read_bytes()
        release.build_package(self.root)
        self.assertEqual((archive.read_bytes(), checksum.read_bytes()), original)
        self.assert_no_temporary_files()

    def test_missing_required_file_preserves_previous_distribution(self):
        """必須ファイル欠落時は成功した前回ZIPを残し、不完全なZIPへ置き換えない。"""
        archive, checksum = release.build_package(self.root)
        original = archive.read_bytes(), checksum.read_bytes()
        (self.root / "skills/walkthrough-review/assets/ui/vendor/marked-LICENSE.txt").unlink()
        with self.assertRaisesRegex(ValueError, "Required shipping file is missing"):
            release.build_package(self.root)
        self.assertEqual((archive.read_bytes(), checksum.read_bytes()), original)
        self.assert_no_temporary_files()

    def test_failed_archive_verification_preserves_previous_distribution(self):
        """候補ZIPの検証が失敗した場合も前回成果物を保ち、今回の一時ファイルだけを消す。"""
        archive, checksum = release.build_package(self.root)
        original = archive.read_bytes(), checksum.read_bytes()
        with mock.patch.object(release, "verify_zip", side_effect=ValueError("verification failed")):
            with self.assertRaisesRegex(ValueError, "verification failed"):
                release.build_package(self.root)
        self.assertEqual((archive.read_bytes(), checksum.read_bytes()), original)
        self.assert_no_temporary_files()

    def test_directory_at_checksum_path_does_not_replace_previous_zip(self):
        """ハッシュ出力名と同名のフォルダがあれば、前回ZIPを変更する前に停止する。

        入力を変更してから再出荷することで、停止前にZIPを置換してしまう不具合を検出する。
        フォルダ内の既存ファイルも残り、別用途のデータを削除しないことを確認する。
        """
        archive, checksum = release.build_package(self.root)
        previous = archive.read_bytes()
        checksum.unlink()
        checksum.mkdir()
        retained = checksum / "keep.txt"
        retained.write_bytes(b"keep")
        (self.root / "README.md").write_bytes(b"changed source")
        with self.assertRaisesRegex(ValueError, "Output path is not a regular file"):
            release.build_package(self.root)
        self.assertEqual(archive.read_bytes(), previous)
        self.assertEqual(retained.read_bytes(), b"keep")
        self.assert_no_temporary_files()

    def test_verifier_rejects_changed_archive_content(self):
        """有効なZIPでも、収録バイト列が元データと違う場合は検証が拒否する。"""
        candidate = self.workspace / "changed.zip"
        release.write_zip(candidate, {"source.py": b"changed\n"})
        with self.assertRaisesRegex(ValueError, "differs from its source"):
            release.verify_zip(candidate, {"source.py": b"original\n"})

    def test_verifier_rejects_unexpected_archive_entry(self):
        """予定にないファイルが一つでも収録されたZIPは公開候補にできない。"""
        candidate = self.workspace / "extra.zip"
        payload = {"source.py": b"code\n"}
        release.write_zip(candidate, payload)
        with zipfile.ZipFile(candidate, "a") as archive:
            archive.writestr(release.PACKAGE_NAME + "/private.txt", b"unexpected")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            release.verify_zip(candidate, payload)

    def test_shipping_paths_cannot_leave_project(self):
        """出荷一覧を将来誤編集しても、親ディレクトリのファイルを取り込まない。"""
        (self.workspace / "outside.txt").write_bytes(b"private")
        with mock.patch.object(release, "SHIP_FILES", ("../outside.txt",)):
            with self.assertRaisesRegex(ValueError, "outside the project"):
                release.collect_files(self.root)
        self.assertFalse((self.root / "dev/dist").exists())

    def test_link_and_windows_reparse_points_are_rejected(self):
        """管理者権限に依存せず、リンクとWindowsジャンクションの拒否分岐を確認する。

        ファイルシステムに実リンクを作る代わりにPathが返す検査結果だけを模擬する。
        実際のOSがその属性を返すところまでは、このテストの検証範囲に含めない。
        """
        path = mock.Mock()
        path.is_symlink.return_value = True
        with self.assertRaisesRegex(ValueError, "Links cannot"):
            release.reject_link(path)
        path.is_symlink.return_value = False
        path.exists.return_value = True
        path.lstat.return_value = SimpleNamespace(st_file_attributes=0x400)
        with self.assertRaisesRegex(ValueError, "Reparse points"):
            release.reject_link(path)

    def test_linked_parent_directory_is_checked(self):
        """ファイル自体だけでなく親フォルダの検査失敗でも出荷を止める。"""
        original_check = release.reject_link

        def check(path):
            """特定の親だけをリンク相当として拒否し、それ以外は実際の検査を行う。"""
            if path == self.root / "skills":
                raise ValueError("linked parent")
            original_check(path)

        with mock.patch.object(release, "reject_link", side_effect=check):
            with self.assertRaisesRegex(ValueError, "linked parent"):
                release.build_package(self.root)
        self.assertFalse((self.root / "dev/dist").exists())

    def test_extracted_package_builds_full_and_diff_reviews(self):
        """出荷ZIPだけから全文・削除差分レビューを生成し、同梱解説のHash一致を確認する。

        実リポジトリからのimportやassets参照で欠品が隠れないよう、展開先のCLIを別プロセスで
        起動する。展開対象はこのテストが作ったZIPに限定し、各パスが展開先内に収まることも
        確認してから展開する。ブラウザーによる表示・操作はここでは確認しない。
        """
        archive_path, _ = release.build_package(self.root)
        unpacked = self.workspace / "展開 後"
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                self.assertTrue((unpacked / name).resolve().is_relative_to(unpacked.resolve()))
            archive.extractall(unpacked)
        package = unpacked / release.PACKAGE_NAME
        for mode, suffix in (("full", ""), ("diff", "-diff")):
            output = self.workspace / ("review-" + mode)
            arguments = [
                "build", package / "dev/tests/fixtures/project",
                "--targets", package / f"dev/tests/fixtures/targets{suffix}.json", "--output", output,
            ]
            if mode == "diff":
                arguments.extend(["--patch", package / "dev/tests/fixtures/diff.patch"])
            self.run_python(package / "dev/walkthrough.py", *arguments)
            self.run_python(
                package / "dev/walkthrough.py", "merge", output,
                "--comments", package / f"dev/tests/fixtures/comments{suffix}.json",
            )
            data = json.loads((output / "review.json").read_text("utf-8"))
            comments = [comment for file in data["files"] for comment in file["comments"]]
            self.assertTrue(comments)
            self.assertTrue(all(comment["hash_status"] == "verified" for comment in comments))
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "assets/vendor/marked.js").is_file())
            self.assertTrue((output / "self-review.md").is_file())
            if mode == "diff":
                deleted = next(file for file in data["files"] if file["path"] == "legacy.py")
                self.assertTrue(any(row["kind"] == "removed" for row in deleted["blocks"]))
        # 開発資料で使うブラウザー確認用ツールも、展開先の入力だけで再実行できる。
        # 素材の生成までを確認し、ブラウザーによる受入確認の代わりにはしない。
        self.run_python(package / "dev/tools/prepare_browser_acceptance.py")
        acceptance = package / "dev/output/browser-acceptance"
        for name in ("full", "diff", "clipboard-failure", "unmerged", "no-targets", "long", "large", "hash", "diagrams", "metadata"):
            self.assertTrue((acceptance / name / "index.html").is_file())
        expectations = json.loads((acceptance / "expectations.json").read_text("utf-8"))
        for mode in ("full", "diff"):
            data = json.loads((acceptance / mode / "review.json").read_text("utf-8"))
            self.assertEqual(len(data["files"]), expectations[mode]["targets"])
            self.assertEqual(data["mode"], expectations[mode]["mode"])
        # 配布先でも同じ構成で再出荷できるか確認し、ツールやテストの収録漏れを検出する。
        self.run_python(package / "dev/tools/package_release.py")
        self.assertTrue((package / "dev/dist/WalkthroughReviewTool.zip").is_file())
        self.run_python(package / "dev/tools/package_skill.py")
        self.assertTrue((package / "dev/dist/walkthrough-review.skill").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows batch entry point")
    def test_batch_runs_from_another_directory_and_propagates_failure(self):
        """日本語・空白付きバッチを別の作業フォルダから起動し、成功と失敗の終了値を確認する。

        cmdへ渡す文字列はテストが生成した絶対パスのみを二重引用符で囲む。
        /dでユーザーのAutoRun設定を無効にする。/s /cの外側引用符とバッチパスの引用符を
        分け、cmdの規則に合わせる。バッチ側のPython選択は本物の環境で動かす。
        """
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}

        def run_batch():
            """同じバッチを再起動し、標準出力と終了コードをそのまま返す。"""
            # 配列引数のWindows Cランタイム向け引用では、cmd用の引用符がバックスラッシュで
            # エスケープされてしまう。ここだけはcmdへ渡すコマンドラインを明示的に構成する。
            command = os.environ.get("COMSPEC", "cmd.exe")
            return subprocess.run(
                f'"{command}" /d /s /c ""{self.root / "dev/package.bat"}""',
                cwd=self.workspace, env=environment, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=60,
            )

        result = run_batch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archive = self.root / "dev/dist/WalkthroughReviewTool.zip"
        previous = archive.read_bytes()
        (self.root / "skills/walkthrough-review/assets/templates/index.html").unlink()
        result = run_batch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Required skill file is missing", result.stdout + result.stderr)
        self.assertEqual(archive.read_bytes(), previous)
        self.assertFalse((self.workspace / "dist").exists())
        self.assert_no_temporary_files()

    @unittest.skipUnless(os.name == "nt", "Windows delayed expansion")
    def test_batch_preserves_exclamation_marks_with_delayed_expansion_enabled(self):
        """cmd /v:onから呼ばれても、バッチ内部で配置パスの!が消えない。

        呼び出し元のcmdがバッチ起動前に!を展開しないよう、作業ディレクトリを
        対象へ設定して固定名で起動する。検証対象はバッチ内の%~dp0の扱いである。
        """
        moved_root = self.workspace / "出荷 !テスト"
        self.root.rename(moved_root)
        self.root = moved_root
        command = os.environ.get("COMSPEC", "cmd.exe")
        result = subprocess.run(
            [command, "/d", "/v:on", "/c", "package.bat"], cwd=self.root / "dev",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "dev/dist/WalkthroughReviewTool.zip").is_file())
        self.assert_no_temporary_files()


if __name__ == "__main__":
    # __main__として読み込まれた場合の入口。通常はdevからunittest discoverで実行する。
    unittest.main()
