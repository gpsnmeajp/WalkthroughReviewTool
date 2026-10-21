"""実ファイルの走査からHTML生成まで、CLIと保存データの境界を検証する。

各テストは専用の一時フォルダ内に小さなプロジェクトを作り、終了時に片付ける。
走査漏れ・対象外本文の混入・不正入力での上書きなど、メモリ上の関数だけでは
確認しにくい振る舞いを扱う。ブラウザーは起動せず、生成されたJSON/HTML/Markdown
とファイルのバイト列から、入出力の整合性を確かめる。
"""

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import walkthrough


class WalkthroughTests(unittest.TestCase):
    """共通の5ファイル構成を基準に、各ケースが必要な差だけを加えて確認する。"""

    def setUp(self):
        """テストごとにプロジェクト・出力先・対象JSON・最小パッチを準備する。

        unittestから引数なしで呼ばれ、戻り値はない。一時フォルダを作り、
        self.project等へその場所を保存する。addCleanupにより途中のassertが失敗しても
        作成物を回収する。出力先は対象プロジェクトの兄弟として走査対象から分離する。
        通常コードに加え、隠しファイル・Git管理ファイル・バイナリを意図的に含める。
        """
        temporary_parent = Path(__file__).resolve().parents[1] / ".test-tmp"
        temporary_parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="walkthrough-test-", dir=temporary_parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.output = self.root / "review"
        self.write_source("src/main.py", "before\nnew\nafter\n")
        self.write_source("src/helper.py", "helper first\nhelper second\n")
        self.write_source(".git/HEAD", "ref: refs/heads/main\n")
        self.write_source(".env", "EXCLUDED_PRIVATE_CONTENT=example\n")
        self.write_source("ignored/blob.bin", b"\0\xff")
        self.targets_path = self.root / "targets.json"
        self.write_json(self.targets_path, {"title": "確認用レビュー", "files": ["src/main.py"]})
        self.patch = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n+++ b/src/main.py\n@@ -2 +2 @@\n-old\n+new\n"
        )

    def write_source(self, name, content):
        """プロジェクト相対名へ本文を書き、作成したPathを返す。

        contentがbytesなら変換せず、文字列ならUTF-8へ変換する。
        親フォルダも作成し、同名ファイルは上書きする。バイナリやBOM等のケースでも
        書き込み時の自動改行変換を避け、テストが指定したバイト列を保持する。
        """
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    def write_json(self, path, data):
        """指定PathへJSON化できるdataをUTF-8で書く。戻り値はない。

        日本語はエスケープせず保存する。親フォルダは作成しないため、
        setUpが準備した一時領域内の既存ディレクトリへ書くことを前提とする。
        """
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def review(self, targets=None, patch=None):
        """一時プロジェクトを読み、ディスクへ出力しないレビューデータを返す。

        targetsを省略または空にするとsrc/main.pyを選ぶ、このテスト用の補助関数。
        patchがNoneなら全体、文字列なら差分としてcreate_reviewへ渡す。
        プロジェクトは読み取るが、元コードやself.outputへ書き込む処理はない。
        """
        return walkthrough.create_review(
            self.project, {"title": "確認用レビュー", "files": targets or ["src/main.py"]}, patch,
        )

    def comment(self, **changes):
        """src/main.pyの2行目を説明する新しいコメント辞書を返す。

        changesで任意の項目を上書きできるため、正常例と不正入力例の両方で使う。
        基本辞書にはsideとcode_hashを入れず、省略時の新側扱い・未照合扱いも検証する。
        呼び出しごとに辞書を作るため、前のケースの変更は次のケースへ持ち越さない。
        """
        value = {
            "id": "c1", "file": "src/main.py", "start_line": 2, "end_line": 2,
            "title": "処理の意図", "body": "この行が変更の中心です。",
        }
        value.update(changes)
        return value

    def run_cli(self, *args):
        """CLI相当の引数をmainへ渡し、終了値・標準出力・標準エラーを返す。

        Pathも受け取り、各引数を文字列化する。別プロセスは起動せず、
        呼び出し中だけstdout/stderrをメモリへ切り替える。指定されたCLI処理による
        一時領域へのファイル作成・更新は通常どおり起きる。
        """
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = walkthrough.main([str(arg) for arg in args])
        return status, output.getvalue(), errors.getvalue()

    def build(self, patch=None):
        """共通対象JSONでbuildを実行し、保存されたreview.jsonを読み戻す。

        patchを渡した場合は一時ルートにdiff.patchを書き、差分オプションを付ける。
        成果物はself.outputへ作成・更新される。CLIが成功しなければこのhelper内で
        assertを失敗させ、その後のケースが壊れた成果物を検査しないようにする。
        """
        args = ["build", self.project, "--targets", self.targets_path, "--output", self.output]
        if patch is not None:
            patch_path = self.root / "diff.patch"
            patch_path.write_text(patch, encoding="utf-8")
            args.extend(["--patch", patch_path])
        status, _, error = self.run_cli(*args)
        self.assertEqual(status, 0, error)
        return walkthrough.read_json(self.output / "review.json")

    def output_bytes(self):
        """出力配下の全通常ファイルを、相対パスからbytesへの辞書として返す。

        読み取り専用で、フォルダやファイルを変更しない。変更前後をこの辞書で比較し、
        特定のJSONだけでなくHTML・資産・補助資料の上書きも漏れなく検出する。
        """
        return {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*") if path.is_file()
        }

    def test_full_and_diff_inventory_include_hidden_and_excluded_files(self):
        """同じ5ファイルを全体モードと差分モードでそれぞれ走査する。

        隠しファイルやGit管理内も一覧へ入り、選択はmain.pyだけであることを確認する。
        .envは一覧には必要だが本文は対象外なので、レビューデータ全体に
        .env内の文字列が混入しないことも併せて確認する。
        """
        for patch in (None, self.patch):
            with self.subTest(mode="full" if patch is None else "diff"):
                review = self.review(patch=patch)
                inventory = {item["path"]: item for item in review["inventory"]}
                self.assertEqual(set(inventory), {
                    "src/main.py", "src/helper.py", ".git/HEAD", ".env", "ignored/blob.bin",
                })
                self.assertTrue(inventory["src/main.py"]["selected"])
                self.assertFalse(inventory[".env"]["selected"])
                self.assertEqual(len(review["files"]), 1)
                self.assertNotIn("EXCLUDED_PRIVATE_CONTENT", json.dumps(review))

    def test_full_and_diff_both_preserve_complete_source(self):
        """全体と差分で同じ現在ファイルを読み込み、全文保持を比較する。

        保存するtextは両モードで同一、差分rowsには削除した旧行も入ることを確認する。
        ハンク前後のcontextと最終新行番号も確認し、変更行だけの表示を防ぐ。
        """
        full = self.review()["files"][0]
        changed = self.review(patch=self.patch)["files"][0]
        self.assertEqual(walkthrough.file_text(full), "before\nnew\nafter\n")
        self.assertEqual(walkthrough.file_text(changed), walkthrough.file_text(full))
        self.assertEqual([row["text"] for row in list(walkthrough.iter_rows(changed))], ["before", "old", "new", "after"])
        self.assertEqual([row["kind"] for row in list(walkthrough.iter_rows(changed))], [
            "context", "removed", "added", "context",
        ])
        self.assertEqual(list(walkthrough.iter_rows(changed))[-1]["new"], 3)

    @unittest.skipUnless(walkthrough.shutil.which("git"), "Gitがない環境では実Gitの無視規則検証を省略")
    def test_git_ignore_metadata_keeps_inventory_and_respects_nested_rules(self):
        """Gitと同じ否定規則・入れ子・追跡状態で対象外表示用の情報を付ける。"""
        def git(*args):
            result = subprocess.run(
                ["git", "-C", str(self.project), *args], capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        git("init", "--quiet")
        self.write_source(".gitignore", "*.log\n!keep.log\nbuild/\n/root-only.tmp\n")
        self.write_source("src/.gitignore", "*.cache\n!keep.cache\n")
        for name in ("日本 語.log", "keep.log", "tracked.log", "build/output.py",
                     "root-only.tmp", "src/root-only.tmp", "src/data.cache", "src/keep.cache"):
            self.write_source(name, "retained\n")
        git("add", "-f", "tracked.log")
        inventory = {item["path"]: item for item in walkthrough.scan_project(self.project)}
        for name in ("日本 語.log", "build/output.py", "root-only.tmp", "src/data.cache"):
            self.assertTrue(inventory[name]["git_ignored"], name)
        for name in ("keep.log", "tracked.log", "src/root-only.tmp", "src/keep.cache", "src/main.py"):
            self.assertFalse(inventory[name]["git_ignored"], name)
        # サブフォルダを対象にしても親のGit規則を使い、相対パスを取り違えない。
        nested = {item["path"]: item for item in walkthrough.scan_project(self.project / "src")}
        self.assertTrue(nested["data.cache"]["git_ignored"])
        self.assertFalse(nested["keep.cache"]["git_ignored"])
        # 無視対象を明示的に選んだレビューでは本文を取り込める。
        review = self.review(["日本 語.log"])
        self.assertEqual(walkthrough.file_text(review["files"][0]), "retained\n")
        chosen = next(item for item in review["inventory"] if item["path"] == "日本 語.log")
        self.assertTrue(chosen["selected"])
        self.assertTrue(chosen["git_ignored"])
        # 現在は存在しない削除差分も、対象外一覧用の無視規則に従う。
        patch = "diff --git a/deleted.log b/deleted.log\n--- a/deleted.log\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
        deleted = next(item for item in self.review(patch=patch)["inventory"] if item["path"] == "deleted.log")
        self.assertTrue(deleted["git_ignored"])

    def test_git_ignore_detection_failure_does_not_block_review(self):
        """Gitが無い・応答しない・リポジトリでない場合も本文と全一覧を取得する。"""
        failures = (
            FileNotFoundError("git unavailable"),
            subprocess.TimeoutExpired("git", 15),
            subprocess.CompletedProcess(["git"], 128, stdout=b".env\0", stderr=b"not a repository"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with mock.patch.object(walkthrough.subprocess, "run", **kwargs):
                    review = self.review()
                self.assertEqual(len(review["inventory"]), 5)
                self.assertEqual(walkthrough.file_text(review["files"][0]), "before\nnew\nafter\n")
                self.assertTrue(all(item["git_ignored"] is False for item in review["inventory"]))

    def test_unchanged_selected_file_in_diff_keeps_full_source(self):
        """パッチに含まれないhelper.pyもレビュー対象へ選ぶ。

        差分生成時でも未変更の選択ファイルが全文を持ち、
        その全行がcontextとして表示できることを確認する。
        """
        review = self.review(["src/main.py", "src/helper.py"], self.patch)
        helper = review["files"][1]
        self.assertEqual(walkthrough.file_text(helper), "helper first\nhelper second\n")
        self.assertTrue(all(row["kind"] == "context" for row in walkthrough.iter_rows(helper)))

    def test_mode_only_diff_is_preserved_for_display_and_self_review(self):
        """行の追加削除がなくても、実行権限の差分を資料から確認できる。"""
        patch = "diff --git a/src/main.py b/src/main.py\nold mode 100644\nnew mode 100755\n"
        review = self.build(patch)
        file = review["files"][0]
        self.assertEqual(file["diff_metadata"], {"old mode": "100644", "new mode": "100755"})
        self.assertEqual(file["diff_summary"], ["権限: 100644 → 100755"])
        self.assertTrue(all(row["kind"] == "context" for row in list(walkthrough.iter_rows(file))))
        self.assertIn("権限: 100644 → 100755", (self.output / "self-review.md").read_text(encoding="utf-8"))

    def test_rename_copy_and_empty_file_changes_have_visible_summaries(self):
        """本文に差がない名称変更・複製・空ファイルの追加削除も明示する。"""
        for operation, label in (("rename", "名称変更"), ("copy", "複製")):
            patch = (
                "diff --git a/old.py b/src/main.py\nsimilarity index 100%\n"
                f"{operation} from old.py\n{operation} to src/main.py\n"
            )
            review = self.review(patch=patch)
            self.assertEqual(review["files"][0]["diff_summary"], [f"{label}: old.py → src/main.py"])
            self.assertIn(f"{label}: old.py → src/main.py", walkthrough.self_review(review))
        self.write_source("empty.py", "")
        added = self.review(["empty.py"], "diff --git a/empty.py b/empty.py\nnew file mode 100644\nindex 000..e69\n")
        self.assertEqual(added["files"][0]["diff_summary"], ["新規ファイル: empty.py", "追加時の権限: 100644"])
        deleted = self.review(["gone.py"], "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\nindex e69..000\n")
        self.assertEqual(deleted["files"][0]["diff_summary"], ["削除ファイル: gone.py", "削除前の権限: 100644"])
        self.assertEqual(self.review()["files"][0]["diff_summary"], [])

    def test_deleted_file_is_in_inventory_even_when_excluded(self):
        """現在存在しないdeleted.pyの削除パッチを既存差分へ追加する。

        未選択時はdeleted状態で一覧に残り、旧本文は混入しないことを確認する。
        次にそのファイルを選択して生成し、削除本文の全2行が復元されることを確認する。
        """
        deletion = "--- a/deleted.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-old first\n-old second\n"
        review = self.review(patch=self.patch + deletion)
        item = next(item for item in review["inventory"] if item["path"] == "deleted.py")
        self.assertEqual(item["status"], "deleted")
        self.assertFalse(item["selected"])
        self.assertNotIn("old first", json.dumps(review))
        selected = self.review(["deleted.py"], self.patch + deletion)
        self.assertEqual([row["text"] for row in walkthrough.iter_rows(selected["files"][0])], [
            "old first", "old second",
        ])

    def test_unselected_patch_mismatch_is_not_ignored(self):
        """helper.pyだけを選び、対象外main.pyの差分内容を実本文と不一致にする。

        対象外だから検証を飛ばすことがないよう、create_reviewの段階で
        ValueErrorになることを確認する。
        """
        with self.assertRaises(ValueError):
            self.review(["src/helper.py"], self.patch.replace("+new", "+wrong"))

    def test_deleted_file_still_present_is_rejected_even_when_excluded(self):
        """現在残っているmain.pyについて、削除済みと宣言するパッチを渡す。

        helper.pyだけを選んだ場合でも、変更後プロジェクトと削除宣言の矛盾を
        見逃さず拒否することを確認する。
        """
        deletion = "--- a/src/main.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
        with self.assertRaises(ValueError):
            self.review(["src/helper.py"], deletion)

    def test_utf8_bom_crlf_and_original_hash(self):
        """BOM・CRLF・日本語・末尾改行なしを組み合わせた実ファイルを作る。

        表示text/rowsはBOM除去・LF化され、sha256は元バイト列に対応することを確認する。
        対象JSONにBOMを付けたCLI入力と、BOMを含む差分ハンクでのbuildも成功させ、
        ファイル取り込みとCLI読込の双方で同じ正規化結果になることを確認する。
        """
        original = b"\xef\xbb\xbf" + "日本語\r\n最後".encode("utf-8")
        self.write_source("src/main.py", original)
        file = self.review()["files"][0]
        self.assertEqual(walkthrough.file_text(file), "日本語\n最後")
        self.assertEqual([row["text"] for row in list(walkthrough.iter_rows(file))], ["日本語", "最後"])
        self.assertEqual(file["sha256"], hashlib.sha256(original).hexdigest())
        self.targets_path.write_bytes(b"\xef\xbb\xbf" + json.dumps({
            "files": ["src/main.py"],
        }).encode("utf-8"))
        self.assertEqual(walkthrough.file_text(self.build()["files"][0]), "日本語\n最後")
        patch = (
            "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n"
            "-\ufeff変更前\n+\ufeff日本語\n"
        )
        self.assertEqual(walkthrough.file_text(self.build(patch)["files"][0]), "日本語\n最後")

    def test_invalid_utf8_or_binary_selected_source_is_rejected(self):
        """不正UTF-8のファイルとNULを含むファイルを別々に作る。

        それぞれを明示的にレビュー対象へ選択し、文字化けや欠落した本文を
        出力する代わりにValueErrorを返すことを確認する。
        """
        for name, content in (("bad-encoding.py", b"\xff"), ("zero.py", b"abc\0xyz")):
            self.write_source(name, content)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.review([name])

    def test_source_line_boundaries_ignore_vertical_tabs(self):
        """垂直タブを含む行と、末尾の空行を持つ本文を書き込む。

        生成rowsがLFだけを行区切りとして扱い、垂直タブを本文に残し、
        実在する空行も消さないことを確認する。
        """
        self.write_source("src/main.py", "one\vpart\nlast\n\n")
        rows = list(walkthrough.iter_rows(self.review()["files"][0]))
        self.assertEqual([row["text"] for row in rows], ["one\vpart", "last", ""])

    def test_unreadable_directory_is_retained_in_inventory(self):
        """一つのフォルダだけscandirがPermissionErrorを返すよう差し替える。

        実際のOS権限を変更せずに走査不能を再現し、そのフォルダを
        unreadableとして一覧へ残すこと、警告にもパスが出ることを確認する。
        """
        restricted = self.project / "restricted"
        restricted.mkdir()
        real_scandir = os.scandir

        def scan(path):
            """restrictedだけを失敗させ、他のパスは元のscandir結果を返す。

            mockのside_effect用で、PermissionError以外の挙動は実走査へ委譲する。
            フォルダの権限や内容自体には手を加えない。
            """
            if Path(path) == restricted:
                raise PermissionError("test access denied")
            return real_scandir(path)

        with mock.patch.object(walkthrough.os, "scandir", side_effect=scan):
            review = self.review()
        item = next(item for item in review["inventory"] if item["path"] == "restricted")
        self.assertEqual(item["status"], "unreadable")
        self.assertTrue(any("restricted" in warning for warning in review["warnings"]))

    def test_symlinks_are_listed_but_not_followed(self):
        """プロジェクト外のフォルダへ向くディレクトリリンクを一時領域に作る。

        リンク自身はsymlinkとして一覧化するが、外部secret.pyは列挙しない。
        リンクを対象に選ぶことも拒否する。OS設定でリンク作成が不可の場合は、
        製品の失敗と混同せず、その環境では検証できないケースとしてskipする。
        """
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.py").write_text("external\n", encoding="utf-8")
        link = self.project / "external"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"この環境ではシンボリックリンク作成不可: {error}")
        inventory = walkthrough.scan_project(self.project)
        paths = {item["path"]: item for item in inventory}
        self.assertEqual(paths["external"]["status"], "symlink")
        self.assertNotIn("external/secret.py", paths)
        with self.assertRaises(ValueError):
            self.review(["external"])

    def test_valid_comments_links_old_lines_and_replacement(self):
        """新旧行・別ファイルリンクを含む2コメントをマージする。

        side省略時のnew扱いを確認し、続いて別の1コメントで全体を置換する。
        同じ入力を再度マージしても増殖せず、空配列では解説を消せることまで確認する。
        """
        review = self.review(["src/main.py", "src/helper.py"], self.patch)
        first = {
            "overview": "[入口](review:src/main.py#L1)",
            "comments": [
                self.comment(body="[補助](review:src/helper.py#L2)"),
                self.comment(id="old", side="old", body="[旧処理](review:src/main.py#O2)"),
            ],
        }
        walkthrough.merge_comments(review, first)
        self.assertEqual(len(review["files"][0]["comments"]), 2)
        self.assertEqual(review["files"][0]["comments"][0]["side"], "new")
        replacement = {"overview": "更新した概要", "comments": [self.comment(id="replacement")]}
        walkthrough.merge_comments(review, replacement)
        self.assertEqual([comment["id"] for comment in review["files"][0]["comments"]], ["replacement"])
        walkthrough.merge_comments(review, replacement)
        self.assertEqual(len(review["files"][0]["comments"]), 1)
        self.assertEqual(review["overview"], "更新した概要")
        walkthrough.merge_comments(review, {"overview": "", "comments": []})
        self.assertEqual(review["files"][0]["comments"], [])

    def test_invalid_comment_ranges_and_ids_do_not_mutate_review(self):
        """行範囲・型・対象パス・side・ID・本文等の不正値を個別に用意する。

        各操作前にdeepcopyを取り、マージが例外になっても元reviewが部分更新されない
        ことを比較する。最後に同一IDの2コメントも拒否されることを確認する。
        """
        for changes in (
            {"start_line": 0}, {"start_line": 3, "end_line": 2},
            {"end_line": 4}, {"start_line": True}, {"end_line": 2.0},
            {"file": "src/helper.py"}, {"side": "other"}, {"id": ""},
            {"body": ""}, {"title": ""}, {"unexpected": "field"},
        ):
            with self.subTest(changes=changes):
                review = self.review()
                before = copy.deepcopy(review)
                with self.assertRaises(ValueError):
                    walkthrough.merge_comments(review, {"comments": [self.comment(**changes)]})
                self.assertEqual(review, before)
        review = self.review()
        with self.assertRaises(ValueError):
            walkthrough.merge_comments(review, {"comments": [self.comment(), self.comment()]})

    def test_internal_links_require_selected_real_line(self):
        """範囲外行、未選択・不在ファイル、不正形式、親参照のリンクを作る。

        同じ不正リンクを概要と解説本文の両方へ入れてマージし、
        どちらの掲載箇所でも同じ参照検証が働くことを確認する。
        """
        for link in (
            "review:src/main.py#L99", "review:src/main.py#O0",
            "review:src/helper.py#L1", "review:missing.py#L1",
            "review:src/main.py", "review:../outside.py#L1",
            "review:%2e%2e/outside.py#L1",
        ):
            for field in ("overview", "body"):
                with self.subTest(link=link, field=field), self.assertRaises(ValueError):
                    payload = {"comments": [self.comment()]}
                    if field == "overview":
                        payload["overview"] = f"[参照]({link})"
                    else:
                        payload["comments"][0]["body"] = f"[参照]({link})"
                    walkthrough.merge_comments(self.review(), payload)

    def test_url_encoded_file_paths_in_internal_links(self):
        """空白を含むファイルを作成して選択し、%20表記でそこへリンクする。

        マージが成功しコメントが保存されることを確認する。
        ファイル名の空白をリンクの区切りと誤認せず復号できることの確認。
        """
        self.write_source("src/a file.py", "value\n")
        review = self.review(["src/main.py", "src/a file.py"])
        walkthrough.merge_comments(review, {"comments": [
            self.comment(body="[別ファイル](review:src/a%20file.py#L1)"),
        ]})
        self.assertEqual(len(review["files"][0]["comments"]), 1)

    def test_large_continuous_source_is_saved_once_as_one_block(self):
        """一万行を一つの区間にし、全文・行メタ情報の二重保持を防ぐ。"""
        text = "".join(f"value_{n} = {n}\n" for n in range(10000))
        self.write_source("src/main.py", text)
        review = self.review()
        file = review["files"][0]
        self.assertEqual(review["schema_version"], 2)
        self.assertNotIn("text", file)
        self.assertNotIn("rows", file)
        self.assertEqual(file["blocks"], [{"kind": "context", "old_start": 1, "new_start": 1, "text": text}])
        self.assertEqual(walkthrough.file_text(file), text)
        self.assertEqual(len(list(walkthrough.iter_rows(file))), 10000)
        encoded = json.dumps(file["blocks"], ensure_ascii=False)
        self.assertLess(len(encoded), len(json.dumps(text)) + 100)

    def test_blocks_roundtrip_empty_lines_and_unusual_characters(self):
        """LFだけで区切り、空本文・空行・末尾改行・制御文字を正確に復元する。"""
        for text in ("", "\n", "first\n\n", "日本語\n最後", "one\vpart\nlast\n\n"):
            with self.subTest(text=repr(text)):
                rows = walkthrough.plain_rows(text)
                file = {"blocks": walkthrough.pack_rows(rows)}
                self.assertEqual(list(walkthrough.iter_rows(file)), rows)
                self.assertEqual(walkthrough.file_text(file), text)
                self.assertEqual(walkthrough.file_text(file, "old"), text)

    def test_blocks_preserve_diff_endings_and_bom_exceptions(self):
        """追加・削除の末尾改行と、単独区間に閉じたBOM例外を照合する。"""
        rows = [
            {"kind": "added", "old": None, "new": 1, "text": "first", "newline": True},
            {"kind": "context", "old": 1, "new": 2, "text": "\ufefforiginal", "old_text": "original", "newline": True},
            {"kind": "removed", "old": 2, "new": None, "text": "old", "newline": False},
            {"kind": "added", "old": None, "new": 3, "text": "new", "newline": True},
        ]
        file = {"blocks": walkthrough.pack_rows(rows)}
        self.assertEqual(list(walkthrough.iter_rows(file)), rows)
        self.assertEqual(walkthrough.file_text(file, "old"), "original\nold")
        self.assertEqual(walkthrough.file_text(file), "first\n\ufefforiginal\nnew\n")
        walkthrough.update_code_hashes(file)
        self.assertEqual(file["code_hashes"]["old"], hashlib.sha256(b"original\nold").hexdigest())

    def test_empty_bom_exception_keeps_the_parser_line_coordinates(self):
        """BOMの除去後に空になる行でも、パッチの行参照を失わない。"""
        rows = [{"kind": "context", "old": 1, "new": 1, "text": "", "old_text": "\ufeff", "newline": False}]
        file = {"blocks": walkthrough.pack_rows(rows)}
        self.assertEqual(list(walkthrough.iter_rows(file)), rows)
        self.assertEqual(walkthrough.file_text(file), "")
        self.assertEqual(walkthrough.file_text(file, "old"), "\ufeff")

    def test_old_schema_is_rejected_without_rewriting_outputs(self):
        """古い保存形式をbuild/mergeで黙って解釈せず、成果物を保持して拒否する。"""
        review = self.build()
        review["schema_version"] = 1
        self.write_json(self.output / "review.json", review)
        comments = self.root / "comments.json"
        self.write_json(comments, {"overview": "", "comments": []})
        before = self.output_bytes()
        for arguments in [
            ("build", self.project, "--targets", self.targets_path, "--output", self.output),
            ("merge", self.output, "--comments", comments),
        ]:
            with self.subTest(command=arguments[0]):
                status, _, _ = self.run_cli(*arguments)
                self.assertEqual(status, 2)
                self.assertEqual(self.output_bytes(), before)

    def test_invalid_blocks_are_rejected_before_comment_merge(self):
        """区分・番号の欠落や不連続、末尾改行後の続行等を拒否する。"""
        valid = {"kind": "context", "old_start": 1, "new_start": 1, "text": "first\nsecond\n"}
        cases = [
            [{**valid, "kind": "unknown"}], [{**valid, "old_start": True}],
            [{**valid, "new_start": 2}], [{**valid, "text": "first\r\n"}],
            [{**valid, "kind": "added"}], [{**valid, "old_text": "different\ncount\n"}],
            [{**valid, "text": "first"}, {**valid, "old_start": 2, "new_start": 2}],
            [{k: v for k, v in valid.items() if k != "new_start"}],
        ]
        for blocks in cases:
            review = self.review()
            review["files"][0]["blocks"] = blocks
            with self.subTest(blocks=blocks), self.assertRaises(ValueError):
                walkthrough.merge_comments(review, {"overview": "", "comments": []})

    def test_all_artifacts_are_local_and_source_is_safe_in_embedded_json(self):
        """script終了タグと長いバッククォートを含む本文を実ファイルに書く。

        build後に資産とライセンスの同梱、埋め込みJSONの「<」エスケープ、
        JSON復号後の本文同一性、外部URLのないHTMLを確認する。
        self-reviewのコードフェンスが本文より長く、対象外一覧も残ることを確認する。
        """
        source = '</script><script>alert("source")</script>\n` ``` `````\n__REVIEW_DATA__\n__HIGHLIGHT_WORKER__\n'
        self.write_source("src/main.py", source)
        review = self.build()
        expected = {
            "index.html", "review.json", "comments.example.json", "self-review.md",
            "assets/app.js", "assets/style.css", "assets/vendor/marked.js",
            "assets/vendor/prism.js", "assets/vendor/marked-LICENSE.txt",
            "assets/vendor/prism-LICENSE.txt",
        }
        self.assertTrue(expected.issubset(self.output_bytes()))
        html = (self.output / "index.html").read_text(encoding="utf-8")
        match = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match)
        self.assertNotIn("<", match.group(1))
        self.assertNotIn("\n", match.group(1))
        self.assertIn("\n  ", (self.output / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(match.group(1)), review)
        worker = re.search(r'<script id="highlight-worker-source" type="application/json">(.*?)</script>', html, re.S)
        self.assertIn("self.onmessage", json.loads(worker.group(1)))
        self.assertNotIn("<", worker.group(1))
        self.assertNotIn('src="assets/vendor/prism.js"', html)
        self.assertEqual(walkthrough.file_text(review["files"][0]), source)
        self.assertNotRegex(html, r'(?:src|href)="https?://')
        self_review = (self.output / "self-review.md").read_text(encoding="utf-8")
        self.assertIn("コード全文", self_review)
        self.assertIn("未解説範囲", self_review)
        self.assertIn("``````text", self_review)
        self.assertIn("対象外 / file / .env", self_review)

    def test_cli_build_merge_keeps_snapshot_after_source_changes(self):
        """差分build後に元main.pyを消し、別ファイルを追加してからmergeする。

        mergeが再走査しないことを、rows・元hash・一覧・生成日時の維持で確認する。
        コメントIDは新しく保存され、self-reviewにも新しい解説と概要が反映される。
        """
        before = self.build(self.patch)
        (self.project / "src" / "main.py").unlink()
        self.write_source("new-after-scan.py", "later content\n")
        comments_path = self.root / "comments.json"
        self.write_json(comments_path, {
            "overview": "保存したコードについての説明",
            "comments": [self.comment()],
        })
        status, _, error = self.run_cli("merge", self.output, "--comments", comments_path)
        self.assertEqual(status, 0, error)
        merged = walkthrough.read_json(self.output / "review.json")
        self.assertEqual(merged["files"][0]["blocks"], before["files"][0]["blocks"])
        self.assertEqual(merged["files"][0]["sha256"], before["files"][0]["sha256"])
        self.assertEqual(merged["inventory"], before["inventory"])
        self.assertEqual(merged["generated_at"], before["generated_at"])
        self.assertEqual(merged["files"][0]["comments"][0]["id"], "c1")
        self_review = (self.output / "self-review.md").read_text(encoding="utf-8")
        self.assertIn("処理の意図", self_review)
        self.assertIn("この行が変更の中心です。", self_review)
        self.assertIn("保存したコードについての説明", self_review)

    def test_invalid_cli_merge_preserves_every_existing_artifact(self):
        """正しいbuildの全成果物を記録し、範囲外の解説JSONでmergeする。

        CLIの終了値2と説明的なエラーを確認し、全ファイルのbytesが実行前と一致する
        ことも確認する。不正マージで有効なHTMLまで失われないための検証。
        """
        self.build()
        before = self.output_bytes()
        comments_path = self.root / "bad-comments.json"
        self.write_json(comments_path, {"comments": [self.comment(end_line=999)]})
        status, _, error = self.run_cli("merge", self.output, "--comments", comments_path)
        self.assertEqual(status, 2)
        self.assertIn("存在しない行範囲", error)
        self.assertEqual(self.output_bytes(), before)

    def test_existing_comments_example_is_not_overwritten(self):
        """生成したcomments.example.jsonを、ユーザー編集中の内容へ置き換える。

        同じ出力先へ再buildしても編集内容がそのまま残ることを確認する。
        補助テンプレートの再生成で作業中の解説を消さないための検証。
        """
        self.build()
        example = self.output / "comments.example.json"
        edited = '{"overview":"編集中","comments":[]}\n'
        example.write_text(edited, encoding="utf-8")
        self.build()
        self.assertEqual(example.read_text(encoding="utf-8"), edited)

    def test_output_inside_project_is_rejected_without_creating_it(self):
        """buildの出力フォルダとscanの一覧JSONを、走査対象の内側へ指定する。

        両コマンドで終了値2・出力先説明を確認し、拒否された出力が
        実際には作成されていないことも確認する。
        """
        for command, output in (("build", self.project / "review"), ("scan", self.project / "inventory.json")):
            args = [command, self.project, "--output", output]
            if command == "build":
                args += ["--targets", self.targets_path]
            with self.subTest(command=command):
                status, _, error = self.run_cli(*args)
                self.assertEqual(status, 2)
                self.assertIn("外に指定", error)
                self.assertFalse(output.exists())

    def test_unknown_nonempty_output_directory_is_not_overwritten(self):
        """レビュー成果物ではないimportant.txtだけの出力先を準備する。

        buildがそのフォルダを勝手に再利用せず失敗し、既存本文を保つことを確認する。
        ディレクトリ内容も比較し、中途半端な新規資産が追加されていないことを確かめる。
        """
        self.output.mkdir()
        existing = self.output / "important.txt"
        existing.write_text("keep me", encoding="utf-8")
        status, _, _ = self.run_cli(
            "build", self.project, "--targets", self.targets_path, "--output", self.output,
        )
        self.assertEqual(status, 2)
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep me")
        self.assertEqual(list(self.output.iterdir()), [existing])

    def test_actual_cli_process_scan(self):
        """同じPython実行ファイルでwalkthrough.pyを別プロセスとして起動する。

        関数直接呼び出しだけでは分からないエントリポイント・終了値・UTF-8出力を
        確認する。保存された一覧が5件で、scan単体ではどのファイルも未選択である
        ことを確認する。子プロセスの出力は取得し、ユーザーの端末へ直接流さない。
        """
        output = self.root / "inventory.json"
        result = subprocess.run(
            [sys.executable, str(walkthrough.TOOL_ROOT / "walkthrough.py"), "scan",
             str(self.project), "--output", str(output)],
            capture_output=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        inventory = json.loads(output.read_text(encoding="utf-8"))["inventory"]
        self.assertEqual(len(inventory), 5)
        self.assertTrue(all(not item["selected"] for item in inventory))


if __name__ == "__main__":
    # discoverからのimport時には実行せず、直接起動した場合だけテストランナーを呼ぶ。
    unittest.main()
