"""unified diffの読み取りと、全文復元の境界条件を文字列だけで検証する。

各テストはパッチ文字列と変更後本文をその場で用意する。ディスクやネットワークは
使用せず、parse_patchのメタデータ解釈とbuild_rowsの行対応を切り分けて確認する。
正常例では旧行・新行・追加削除・改行情報を確認し、不正例ではValueErrorを要求する。
"""

import unittest

from diff_parser import build_rows, parse_patch


def single(patch):
    """1ファイル分のパッチ文字列を解析し、そのエントリ辞書を返す。

    ファイル数が1でないときはテストデータの組み立て間違いとしてAssertionError。
    parse_patchの例外はそのまま伝える。ファイル入出力や入力文字列の変更はない。
    """
    entries = parse_patch(patch)
    if len(entries) != 1:
        raise AssertionError("テスト用パッチは一つのファイルを想定しています")
    return entries[0]


class DiffParserTests(unittest.TestCase):
    """最小限の差分を組み立て、全文との照合に必要な条件を個別に確かめる。"""

    def test_full_text_and_multiple_hunks(self):
        """離れた2ハンクとその前後・間の未変更行を持つ本文を準備する。

        build_rowsへ変更後全文を渡し、ハンク外の行も省略されないことを確認する。
        追加・削除による行番号のずれと、反対側に行番号がないことも確認する。
        """
        entry = single(
            "--- a/code.py\n+++ b/code.py\n"
            "@@ -2,2 +2,3 @@ function\n keep\n-old\n+new\n+extra\n"
            "@@ -6 +7 @@\n-tail\n+end\n"
        )
        rows = build_rows(entry, "before\nkeep\nnew\nextra\nmiddle\nbetween\nend\nafter\n")
        self.assertEqual([row["text"] for row in rows], [
            "before", "keep", "old", "new", "extra", "middle", "between", "tail", "end", "after",
        ])
        self.assertEqual(rows[-1], {"old": 7, "new": 8, "kind": "context", "text": "after", "newline": True})
        self.assertEqual(rows[2]["new"], None)
        self.assertEqual(rows[3]["old"], None)

    def test_insert_at_start_and_delete_at_end(self):
        """先頭の追加と末尾の削除を、一つのパッチで同時に実行する。

        0行の旧範囲・新範囲という境界表現を準備し、生成した3行について
        新規行はoldがNone、削除行はnewがNone、中央行は両番号を持つことを確認する。
        """
        entry = single("--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+first\n@@ -2 +2,0 @@\n-last\n")
        rows = build_rows(entry, "first\nmiddle\n")
        self.assertEqual([(row["old"], row["new"]) for row in rows], [
            (None, 1), (1, 2), (2, None),
        ])

    def test_plain_paths_and_timestamps(self):
        """空白入りの通常パスにタブ区切りの日時を付けたパッチを解析する。

        日時をファイル名に含めず、空白自体は残すことを確認する。
        そのパスのエントリで本文照合まで進み、新しい行がaddedになることも確認する。
        """
        entry = single("--- src/a file.py\t2026-10-21\n+++ src/a file.py\t2026-10-22\n@@ -1 +1 @@\n-x\n+y\n")
        self.assertEqual(entry["new_path"], "src/a file.py")
        self.assertEqual(build_rows(entry, "y\n")[1]["kind"], "added")

    def test_git_quoted_utf8_path(self):
        """Gitの引用パスに含まれるUTF-8の八進数表記を準備する。

        旧・新ヘッダーを解析して「日本.py」へ復号し、Gitのa/b接頭辞だけを
        除去することを確認する。復号後のエントリで本文照合も成功させる。
        """
        path = r'"\141/\346\227\245\346\234\254.py"'
        new_path = r'"\142/\346\227\245\346\234\254.py"'
        entry = single(
            f"diff --git {path} {new_path}\nindex 123..abc 100644\n"
            f"--- {path}\n+++ {new_path}\n@@ -1 +1 @@\n-x\n+y\n"
        )
        self.assertEqual(entry["new_path"], "日本.py")
        build_rows(entry, "y\n")

    def test_git_unquoted_spaces(self):
        """引用符のないGitヘッダー内に、複数の空白を含むパスを用意する。

        単純な空白分割でパスを途中までしか読まない事故を防ぐため、
        最終的なnew_pathがディレクトリとファイル名を完全に保持することを確認する。
        """
        entry = single(
            "diff --git a/a b/file name b/a b/file name\n"
            "--- a/a b/file name\n+++ b/a b/file name\n@@ -1 +1 @@\n-x\n+y\n"
        )
        self.assertEqual(entry["new_path"], "a b/file name")

    def test_git_no_prefix_preserves_a_directory(self):
        """--no-prefix相当のヘッダーで、実ディレクトリ名aを残すことを確認する。

        旧・新の両パスをa/fileにそろえて解析する。a/を常にGitの接頭辞とみなすと
        誤ってfileだけになるため、戻り値と本文照合の両方を確認する。
        """
        entry = single(
            "diff --git a/file a/file\n--- a/file\n+++ a/file\n"
            "@@ -1 +1 @@\n-x\n+y\n"
        )
        self.assertEqual(entry["new_path"], "a/file")
        build_rows(entry, "y\n")

    def test_git_no_prefix_new_file_preserves_b_directory(self):
        """接頭辞なしの新規ファイルで、実ディレクトリ名bを保持する。

        旧側を/dev/null、新側をb/new.pyとした入力を用意する。
        新規追加でもb/を機械的に落とさず、追加本文との照合に成功することを確認する。
        """
        entry = single(
            "diff --git b/new.py b/new.py\nnew file mode 100644\n"
            "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+new\n"
        )
        self.assertEqual(entry["new_path"], "b/new.py")
        build_rows(entry, "new\n")

    def test_new_file_requires_whole_current_content(self):
        """新規追加パッチが2行を宣言するケースで、変更後全文を照合する。

        宣言どおりの2行はすべてaddedとして返る。一方、実ファイルに3行目があれば
        パッチが全文を表していないためValueErrorになることを確認する。
        """
        entry = single(
            "diff --git a/new b/new\nnew file mode 100644\n"
            "--- /dev/null\n+++ b/new\n@@ -0,0 +1,2 @@\n+a\n+b\n"
        )
        self.assertEqual([row["kind"] for row in build_rows(entry, "a\nb\n")], ["added", "added"])
        with self.assertRaises(ValueError):
            build_rows(entry, "a\nb\nunexpected\n")

    def test_deleted_file_reconstructed_completely(self):
        """削除済みの2行ファイルを、現在本文なしのNoneから復元する。

        旧本文が順序どおり戻り、全行のnew番号がNoneになることを確認する。
        空文字列は「存在する空ファイル」を意味するため、Noneの代わりには認めない。
        """
        entry = single(
            "diff --git a/old b/old\ndeleted file mode 100644\n"
            "--- a/old\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
        )
        rows = build_rows(entry, None)
        self.assertEqual([row["text"] for row in rows], ["a", "b"])
        self.assertTrue(all(row["new"] is None for row in rows))
        with self.assertRaises(ValueError):
            build_rows(entry, "")

    def test_partial_deleted_file_is_rejected(self):
        """削除ファイルの途中2行だけを含む不完全なパッチを用意する。

        変更後本文は存在しないので、欠けた旧先頭行を推測して補えない。
        build_rowsが部分的な全文を正常扱いせず、ValueErrorで拒否することを確認する。
        """
        patch = "--- old\n+++ /dev/null\n@@ -3,2 +2,0 @@\n-c\n-d\n"
        entry = single(patch)
        with self.assertRaises(ValueError):
            build_rows(entry, None)

    def test_empty_new_and_deleted_files(self):
        """ハンクを持たない空ファイルの追加・削除メタデータを解析する。

        新規側は空文字列、削除側はNoneを渡し、どちらも空のrowsを返すことを確認する。
        空ファイルの追加宣言に対して実際の本文がある場合は拒否する。
        """
        added = single("diff --git a/empty b/empty\nnew file mode 100644\nindex 000..e69\n")
        deleted = single("diff --git a/empty b/empty\ndeleted file mode 100644\nindex e69..000\n")
        self.assertEqual(build_rows(added, ""), [])
        self.assertEqual(build_rows(deleted, None), [])
        with self.assertRaises(ValueError):
            build_rows(added, "not empty\n")

    def test_rename_without_hunks(self):
        """本文変更がなく、名称だけを変える100%一致のrenameを準備する。

        old_path/new_pathが別々に保持され、変更後全文を渡すと
        既存の行がcontextとして扱われることを確認する。
        """
        entry = single(
            "diff --git a/old name.py b/new name.py\nsimilarity index 100%\n"
            "rename from old name.py\nrename to new name.py\n"
        )
        self.assertEqual((entry["old_path"], entry["new_path"]), ("old name.py", "new name.py"))
        self.assertEqual(build_rows(entry, "same\n")[0]["kind"], "context")

    def test_rename_with_hunks_and_copy(self):
        """内容変更を伴うrenameとcopyに同じ本文差分を与える。

        操作名だけを入れ替えて各エントリを解析・照合し、
        メタデータが存在しても新しい本文yが正しく生成されることを確認する。
        """
        for operation in ("rename", "copy"):
            entry = single(
                f"diff --git a/old b/new\nsimilarity index 80%\n"
                f"{operation} from old\n{operation} to new\n"
                "--- a/old\n+++ b/new\n@@ -1 +1 @@\n-x\n+y\n"
            )
            self.assertEqual(build_rows(entry, "y\n")[1]["text"], "y")

    def test_multiple_copies_can_share_the_same_old_path(self):
        """一つの旧ファイルから二つの宛先へコピーするパッチを用意する。

        共有されたold_pathを重複エラーとせず、異なるnew_pathを2件返すことを確認する。
        各エントリへ同じ変更後本文を渡し、それぞれ独立に全文を扱えることも確認する。
        """
        patch = "".join(
            f"diff --git a/source.py b/{destination}\nsimilarity index 100%\n"
            f"copy from source.py\ncopy to {destination}\n"
            for destination in ("copy1.py", "copy2.py")
        )
        entries = parse_patch(patch)
        self.assertEqual([entry["new_path"] for entry in entries], ["copy1.py", "copy2.py"])
        for entry in entries:
            self.assertEqual(build_rows(entry, "source\n")[0]["text"], "source")

    def test_copy_and_modification_can_share_the_same_old_path(self):
        """コピー元自身の変更と、同じコピー元からのcopyを並べる。

        入力順を入れ替えてparse_patchへ渡し、いずれも2エントリとして受理されることを
        確認する。同じ旧パスの利用と、同じ出力パスの重複を混同しないための確認。
        """
        copy_patch = (
            "diff --git a/source.py b/copy.py\nsimilarity index 100%\n"
            "copy from source.py\ncopy to copy.py\n"
        )
        modified_patch = (
            "diff --git a/source.py b/source.py\n--- a/source.py\n+++ b/source.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        self.assertEqual(len(parse_patch(copy_patch + modified_patch)), 2)
        self.assertEqual(len(parse_patch(modified_patch + copy_patch)), 2)

    def test_mode_only(self):
        """実行権限だけを変更するGitメタデータを準備する。

        本文ハンクがなくても現在本文を捨てず、元の行を表示用データとして
        返すことを確認する。ファイルモードを実際に変更するテストではない。
        """
        entry = single("diff --git a/f b/f\nold mode 100644\nnew mode 100755\n")
        self.assertEqual(build_rows(entry, "x\n")[0]["text"], "x")

    def test_no_final_newline(self):
        """旧・新とも末尾改行なしと明示された1行差分を解析する。

        改行なしの変更後本文は受理し、文字が同じでも末尾改行を加えた本文は
        パッチと不一致として拒否する。本文末尾の厳密な照合を確認する。
        """
        entry = single(
            "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n"
            "\\ No newline at end of file\n+new\n\\ No newline at end of file\n"
        )
        self.assertEqual(build_rows(entry, "new")[1]["text"], "new")
        with self.assertRaises(ValueError):
            build_rows(entry, "new\n")

    def test_final_newline_added_and_removed(self):
        """文字内容を変えず、末尾改行だけを追加・削除する2種類を準備する。

        それぞれ対応する変更後本文は受理し、改行の有無を逆にした本文は拒否する。
        行文字列が同じことだけで一致判定してしまわないことを確認する。
        """
        added = single("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n\\ No newline at end of file\n+x\n")
        removed = single("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+x\n\\ No newline at end of file\n")
        build_rows(added, "x\n")
        build_rows(removed, "x")
        with self.assertRaises(ValueError):
            build_rows(added, "x")
        with self.assertRaises(ValueError):
            build_rows(removed, "x\n")

    def test_rows_preserve_newline_for_each_reconstructed_version(self):
        """旧側だけ末尾改行がない差分から、旧版・新版の全文を再結合する。

        rowsのnewlineを使って復元した各版が期待する末尾を保つことを確認する。
        追加で、ハンク外の最終行についても改行なし情報が残ることを確認する。
        """
        entry = single(
            "--- a/f\n+++ b/f\n@@ -2 +2 @@\n-old\n"
            "\\ No newline at end of file\n+new\n"
        )
        rows = build_rows(entry, "first\nnew\n")
        old_text = "".join(row["text"] + ("\n" if row["newline"] else "") for row in rows if row["old"] is not None)
        new_text = "".join(row["text"] + ("\n" if row["newline"] else "") for row in rows if row["new"] is not None)
        self.assertEqual(old_text, "first\nold")
        self.assertEqual(new_text, "first\nnew\n")
        first_line = single("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\n")
        rows = build_rows(first_line, "y\ntail")
        self.assertFalse(rows[-1]["newline"])
    def test_no_newline_marker_must_be_at_end(self):
        """末尾改行なしマーカーの後ろに、同じ版の次行がある入力を作る。

        「その行がファイル末尾」という宣言と実際の行構成が矛盾するため、
        build_rowsがValueErrorにすることを確認する。
        """
        entry = single(
            "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n-old\n"
            "\\ No newline at end of file\n+new\n second\n"
        )
        with self.assertRaises(ValueError):
            build_rows(entry, "new\nsecond\n")

    def test_crlf_patch_and_body(self):
        """パッチ自体と変更後本文の両方をCRLFで用意する。

        Windows由来の改行形式でも、余分なCRをコードの差と誤認せず
        解析・本文照合が最後まで完了することを確認する。
        """
        entry = single("--- a/f\r\n+++ b/f\r\n@@ -1 +1 @@\r\n-x\r\n+y\r\n")
        build_rows(entry, "y\r\n")

    def test_bom_on_first_line_matches_raw_source_and_is_normalized_for_display(self):
        """両版の先頭行にBOMを含むパッチと、それに一致する実本文を用意する。

        元のBOM込みの文字列で整合性を確認した後、表示行から先頭BOMだけを除去する。
        BOMを欠いた実本文は見た目が同じでもパッチと一致しないため拒否する。
        """
        entry = single("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-\ufeffold\n+\ufeffnew\n")
        rows = build_rows(entry, "\ufeffnew\n")
        self.assertEqual([row["text"] for row in rows], ["old", "new"])
        with self.assertRaises(ValueError):
            build_rows(entry, "new\n")

    def test_bom_inside_source_is_not_removed(self):
        """先頭への追加で、旧版先頭のBOM文字が新版の2行目へ移る例を用意する。

        新版では本文内部の文字として残し、旧版では先頭BOMとして除く必要がある。
        共通行のtextとold_textが、それぞれの版に合った値を保持することを確認する。
        """
        entry = single("--- a/f\n+++ b/f\n@@ -1 +1,2 @@\n+first\n \ufefforiginal\n")
        rows = build_rows(entry, "first\n\ufefforiginal\n")
        self.assertEqual(rows[1]["text"], "\ufefforiginal")
        self.assertEqual(rows[1]["old_text"], "original")

    def test_context_bom_moving_to_first_line_preserves_old_inner_character(self):
        """先頭行の削除で、本文内部のBOM文字が新版先頭へ移る例を用意する。

        新版のtextではBOMを除き、旧版のold_textでは内部文字として残す。
        old_textを優先して旧版全文を再結合し、旧内容が欠けていないことを確認する。
        """
        entry = single("--- a/f\n+++ b/f\n@@ -1,2 +1 @@\n-first\n \ufefforiginal\n")
        rows = build_rows(entry, "\ufefforiginal\n")
        self.assertEqual(rows[1]["text"], "original")
        self.assertEqual(rows[1]["old_text"], "\ufefforiginal")
        old_text = "".join(
            row.get("old_text", row["text"]) + ("\n" if row["newline"] else "")
            for row in rows if row["old"] is not None
        )
        self.assertEqual(old_text, "first\n\ufefforiginal\n")

    def test_context_bom_moving_from_first_line_reconstructs_old_without_bom(self):
        """先頭追加によりBOM付き共通行が下へ移る場合、旧版を再構成する。

        各行のold_textがあればそれを使って結合し、旧版の先頭BOMだけが
        正規化で除去された全文になることを確認する。
        """
        entry = single("--- a/f\n+++ b/f\n@@ -1 +1,2 @@\n+first\n \ufefforiginal\n")
        rows = build_rows(entry, "first\n\ufefforiginal\n")
        old_text = "".join(
            row.get("old_text", row["text"]) + ("\n" if row["newline"] else "")
            for row in rows if row["old"] is not None
        )
        self.assertEqual(old_text, "original\n")

    def test_context_outside_hunk_bom_moving_from_first_line(self):
        """パッチが先頭追加だけを含み、BOM付き旧先頭行はハンク外に置く。

        ハンク本文に現れない共通行でも、新版では内部BOMを保持し、
        old_textでは旧版先頭BOMを除くという版別の扱いを確認する。
        """
        entry = single("--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+first\n")
        rows = build_rows(entry, "first\n\ufefforiginal\n")
        self.assertEqual(rows[1]["text"], "\ufefforiginal")
        self.assertEqual(rows[1]["old_text"], "original")

    def test_context_outside_hunk_bom_moving_to_first_line(self):
        """先頭削除だけのハンクにより、ハンク外の共通行を新版先頭へ移す。

        新版表示のtextからBOMを除去しつつ、旧版の内部文字はold_textに残す。
        BOMの扱いがハンク内の行だけに限定されないことを確認する。
        """
        entry = single("--- a/f\n+++ b/f\n@@ -1 +0,0 @@\n-first\n")
        rows = build_rows(entry, "\ufefforiginal\n")
        self.assertEqual(rows[1]["text"], "original")
        self.assertEqual(rows[1]["old_text"], "\ufefforiginal")

    def test_only_one_leading_bom_is_removed(self):
        """先頭にBOM文字が二つ連続する旧・新本文を用意する。

        エンコーディング印として除くのは最初の一つだけであり、
        残る一つは本文として各表示行に保持されることを確認する。
        """
        entry = single(
            "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-\ufeff\ufeffold\n+\ufeff\ufeffnew\n"
        )
        rows = build_rows(entry, "\ufeff\ufeffnew\n")
        self.assertEqual([row["text"] for row in rows], ["\ufeffold", "\ufeffnew"])

    def test_vertical_tab_is_part_of_code(self):
        """追加行の途中に垂直タブを含めて解析・本文照合する。

        垂直タブを改行として分割せず、一つの行のtextにそのまま保持する。
        一般的なsplitlinesの挙動に引きずられた行番号ずれを防ぐ確認。
        """
        entry = single("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\vvalue\n")
        self.assertEqual(build_rows(entry, "y\vvalue\n")[1]["text"], "y\vvalue")

    def test_content_mismatch_is_rejected(self):
        """共通行違い・追加行違い・不足行・末尾改行違いの本文を順に渡す。

        同一パッチに対する各不一致をsubTestで分離し、すべてValueErrorを要求する。
        削除パッチでないのに現在本文がNoneの場合も受理しないことを確認する。
        """
        entry = single("--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n keep\n-old\n+new\n")
        for text in ("wrong\nnew\n", "keep\nwrong\n", "keep\n", "keep\nnew"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                build_rows(entry, text)
        with self.assertRaises(ValueError):
            build_rows(entry, None)

    def test_missing_and_excess_hunk_lines_are_rejected(self):
        """ハンクが宣言する行数より、本文が少ない例と多い例を用意する。

        実ファイルとの照合以前にparse_patchで拒否されることを確認する。
        宣言行数に合わせて勝手に切り捨てたり補ったりしないための検証。
        """
        for hunk in ("@@ -1,2 +1 @@\n-x\n+y\n", "@@ -1 +1 @@\n-x\n+y\n+extra\n"):
            with self.subTest(hunk=hunk), self.assertRaises(ValueError):
                parse_patch("--- a/f\n+++ b/f\n" + hunk)

    def test_bad_coordinates_are_rejected(self):
        """0行番号の誤用、旧新の位置不整合、空ハンク、重複・ずれを準備する。

        各ハンク文字列を独立に解析し、座標から一貫した全文を構成できない入力は
        例外にすることを確認する。本文文字列の正しさとは別に座標を検証する。
        """
        for hunk in (
            "@@ -0 +1 @@\n-x\n+y\n",
            "@@ -2 +1 @@\n-x\n+y\n",
            "@@ -0,0 +0,0 @@\n",
            "@@ -1 +1 @@\n-x\n+y\n@@ -1 +1 @@\n-z\n+w\n",
            "@@ -1 +1,2 @@\n-x\n+y\n+z\n@@ -3 +3 @@\n-p\n+q\n",
        ):
            with self.subTest(hunk=hunk), self.assertRaises(ValueError):
                parse_patch("--- a/f\n+++ b/f\n" + hunk)

    def test_hunk_outside_current_file_is_rejected(self):
        """5行目の変更を宣言するパッチへ、1行しかない実本文を渡す。

        パッチ単独では構文上読めても、実ファイルの範囲を超える座標は
        build_rowsで拒否されることを確認する。
        """
        entry = single("--- a/f\n+++ b/f\n@@ -5 +5 @@\n-x\n+y\n")
        with self.assertRaises(ValueError):
            build_rows(entry, "short\n")

    def test_unsafe_paths_are_rejected(self):
        """親参照・絶対パス・ドライブ・逆斜線・代替ストリーム等を列挙する。

        各パスを通常の差分ヘッダーに埋めて解析し、プロジェクト相対パスとして
        受理できない値をValueErrorにする。実ファイルにはアクセスしない。
        """
        for path in ("../bad", "/absolute", "C:/bad", "dir/../bad", r"dir\bad", "file:stream", "./bad"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                parse_patch(f"--- {path}\n+++ {path}\n@@ -1 +1 @@\n-x\n+y\n")

    def test_unsafe_quoted_paths_are_rejected(self):
        """引用パスへ八進数の親参照・NUL・タブを埋め込む。

        見かけの表記だけで判定せず、復号した後の危険なパスも
        parse_patchで拒否することを確認する。
        """
        for path in (r'"a/\056\056/bad"', r'"a/x\000bad"', r'"a/x\tbad"'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                parse_patch(f"--- {path}\n+++ b/good\n@@ -1 +1 @@\n-x\n+y\n")

    def test_unsupported_formats_are_rejected(self):
        """combined diff、バイナリ差分、説明文だけの入力を順に渡す。

        このツールが扱うunified diffと異なる形式を空の差分等として
        黙って受理せず、明示的にValueErrorへすることを確認する。
        """
        for patch in (
            "diff --cc f\n", "diff --combined f\n", "@@@ -1 -1 +1 @@@\n",
            "diff --git a/f b/f\nGIT binary patch\n",
            "diff --git a/f b/f\nBinary files a/f and b/f differ\n",
            "random prose\n",
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                parse_patch(patch)

    def test_inconsistent_metadata_is_rejected(self):
        """Gitヘッダー同士のパス矛盾や、不完全なrename/mode宣言等を準備する。

        個々の行が読めても、同じエントリ内のメタデータが両立しなければ
        parse_patchが拒否することを各入力について確認する。
        """
        for patch in (
            "diff --git a/f b/f\n--- a/other\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\n",
            "diff --git a/f b/g\nrename from other\nrename to g\n",
            "diff --git a/f b/g\nrename from f\n",
            "diff --git a/f b/f\nold mode 100644\n",
            "diff --git a/f b/f\nnew file mode 100644\ndeleted file mode 100644\n",
            "diff --git a/f b/f\nnew file mode 100644\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\n",
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                parse_patch(patch)

    def test_multiple_files_and_duplicate_rejection(self):
        """別パスへの2差分と、同一パスを2回記載した差分を比較する。

        異なる2ファイルは2エントリを返し、同じ出力パスの重複は
        後の差分で上書きせずValueErrorにすることを確認する。
        """
        first = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\n"
        second = "--- a/g\n+++ b/g\n@@ -1 +1 @@\n-p\n+q\n"
        self.assertEqual(len(parse_patch(first + second)), 2)
        with self.assertRaises(ValueError):
            parse_patch(first + first)

    def test_empty_patch(self):
        """空文字列と空行だけのパッチを解析する。

        どちらも変更対象のない入力として空リストを返し、例外にしない。
        差分なしのプロジェクトでも同じAPIを利用できることを確認する。
        """
        self.assertEqual(parse_patch(""), [])
        self.assertEqual(parse_patch("\n"), [])


if __name__ == "__main__":
    # __main__として読み込まれた場合の入口。通常はルートからunittest discoverで実行する。
    unittest.main()
