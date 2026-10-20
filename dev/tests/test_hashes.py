"""解説の作成時ハッシュと、生成時のコード全文との対応を検証する。

元バイト列のsha256と、BOM除去・LF化した各版のcode_hashesは別の役割を持つ。
このテスト群では正常な一致だけでなく、未記録・不一致・消失行・対象外化を扱い、
古い解説を黙って更新・破棄せず、ユーザーが警告として確認できることを確かめる。
実ファイルを扱うが、すべてケースごとに作成する一時領域内に限定する。
"""

import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import walkthrough


def digest(text):
    """期待値用の本文文字列をUTF-8へ変換し、SHA-256の小文字16進表現を返す。

    このhelperはBOM除去や改行統一をしない。テスト側で期待する正規化済みの本文を
    明示して渡すことで、製品の正規化結果を同じ処理で再計算するだけの検証を避ける。
    入力を変更せず、ファイル・ネットワークへの副作用もない。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CommentHashTests(unittest.TestCase):
    """2行のcode.pyを基準に、ハッシュ照合と古い解説の保持を段階的に確認する。"""

    def setUp(self):
        """新しい一時プロジェクトに、LF改行の2行ファイルと対象指定を準備する。

        unittestが各ケースの直前に呼び出す。self.source等へPathを保存し、
        後片付けをaddCleanupへ登録する。実行中の書き換えが他ケースや
        リポジトリのサンプル本文へ影響しないよう、毎回独立した領域を使う。
        """
        temporary_parent = Path(__file__).resolve().parents[1] / ".test-tmp"
        temporary_parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="review-hash-", dir=temporary_parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.source = self.project / "code.py"
        self.source.write_bytes(b"first\nsecond\n")
        self.targets = {"files": ["code.py"]}

    def review(self, patch=None):
        """現在の一時プロジェクトを読み、コードと各版ハッシュを持つ辞書を返す。

        patchがNoneなら全体レビュー、文字列なら差分レビューを生成する。
        create_reviewはプロジェクトを読むが、HTML等の成果物はまだ書き出さない。
        """
        return walkthrough.create_review(self.project, self.targets, patch)

    def comment(self, code_hash=None, **changes):
        """code.pyの2行目を説明する新しいコメント辞書を作って返す。

        code_hashがNoneならキー自体を省略し、未記録状態を再現する。
        指定されたハッシュとchangesは検証せずそのまま入れるため、
        型不正や消えた行などの入力も製品の検証へ渡せる。既存辞書は変更しない。
        """
        comment = {
            "id": "intent", "file": "code.py", "start_line": 2, "end_line": 2,
            "title": "二つ目の処理", "body": "この処理の意図を説明します。",
        }
        if code_hash is not None:
            comment["code_hash"] = code_hash
        comment.update(changes)
        return comment

    def merge(self, review, *comments):
        """可変個数のコメントを固定の概要とともにreviewへマージして返す。

        引数reviewは製品関数によって更新される。コメントの照合結果・警告も
        この辞書に保存されるが、このhelper自身はディスクへ何も書かない。
        入力検証の例外はテスト側のassertRaisesで受けるため、そのまま伝える。
        """
        return walkthrough.merge_comments(review, {"overview": "概要", "comments": list(comments)})

    def test_full_hash_is_sha256_of_complete_normalized_code(self):
        """先頭BOM・CRLF・末尾改行なしの日本語ファイルを実際のbytesで用意する。

        全体レビューの旧新ハッシュが、BOMなし・LF改行の全文の期待値に一致することを
        確認する。同時に元bytesのsha256とは異なる値になり、用途を区別できることを確認する。
        """
        self.source.write_bytes(b"\xef\xbb\xbf" + "日本語\r\n最後".encode())
        file = self.review()["files"][0]
        self.assertEqual(file["code_hashes"], {"new": digest("日本語\n最後"), "old": digest("日本語\n最後")})
        self.assertNotEqual(file["sha256"], file["code_hashes"]["new"])

    def test_final_newline_changes_hash_but_line_endings_do_not(self):
        """初期LF本文のハッシュを保存し、CRLF化、末尾改行削除を順に行う。

        CRLF化だけなら正規化後の内容は同じなので値が一致する。
        末尾改行の削除では値が変わり、ファイル末尾の情報を捨てていないことを確認する。
        """
        initial = self.review()["files"][0]["code_hashes"]["new"]
        self.source.write_bytes(b"first\r\nsecond\r\n")
        self.assertEqual(self.review()["files"][0]["code_hashes"]["new"], initial)
        self.source.write_bytes(b"first\nsecond")
        self.assertNotEqual(self.review()["files"][0]["code_hashes"]["new"], initial)

    def test_old_and_new_hashes_include_full_context_and_final_newline(self):
        """2行目だけを変更し、新版だけ末尾改行なしとなるパッチを準備する。

        旧・新それぞれの期待全文には、ハンク外の1行目も含める。
        各版ハッシュがその全文と末尾改行の違いに対応することを確認する。
        """
        self.source.write_bytes(b"first\nnew")
        patch = "--- a/code.py\n+++ b/code.py\n@@ -2 +2 @@\n-old\n+new\n\\ No newline at end of file\n"
        file = self.review(patch)["files"][0]
        self.assertEqual(file["code_hashes"], {"old": digest("first\nold\n"), "new": digest("first\nnew")})

    def test_deleted_file_hash_is_reconstructed_old_content(self):
        """現在ファイルを一時領域から削除し、全2行の削除パッチを渡す。

        旧ハッシュは復元した本文、新ハッシュは空本文に対応することを確認する。
        旧ハッシュを記録したold側の解説もマージし、verifiedになることを確認する。
        """
        self.source.unlink()
        patch = "--- a/code.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-first\n-second\n"
        review = self.review(patch)
        self.assertEqual(review["files"][0]["code_hashes"], {"old": digest("first\nsecond\n"), "new": digest("")})
        self.merge(review, self.comment(digest("first\nsecond\n"), side="old"))
        self.assertEqual(review["files"][0]["comments"][0]["hash_status"], "verified")

    def test_new_side_comment_works_in_both_full_and_diff(self):
        """同じ変更後本文について、全体レビューと差分レビューを作る。

        同一のnew側ハッシュを持つ解説を両方にマージして警告がないことを確認する。
        表示モードの違いだけでコード照合が不一致にならないための検証。
        """
        patch = "--- a/code.py\n+++ b/code.py\n@@ -2 +2 @@\n-old\n+second\n"
        for review in (self.review(), self.review(patch)):
            self.merge(review, self.comment(digest("first\nsecond\n")))
            self.assertFalse(review["hash_warnings"])

    def test_matching_hash_verified_and_original_value_retained(self):
        """正しいSHA-256を大文字表記でコメントへ記録してマージする。

        大小文字の違いを内容不一致とせずverifiedにする一方、入力code_hashの表記は
        そのまま残す。比較先actual_code_hashは小文字の計算値であり、警告はない。
        """
        review = self.review()
        original = digest("first\nsecond\n").upper()
        self.merge(review, self.comment(original))
        comment = review["files"][0]["comments"][0]
        self.assertEqual(comment["hash_status"], "verified")
        self.assertEqual(comment["code_hash"], original)
        self.assertEqual(comment["actual_code_hash"], original.lower())
        self.assertFalse(review["hash_warnings"])

    def test_mismatch_remains_visible_without_rewriting_authoring_hash(self):
        """現在コードとは異なる本文から計算した、形式上は正しいハッシュを渡す。

        マージは成功するがmismatch警告が付き、作成時code_hashは上書きされない。
        self-reviewにも元ハッシュと不一致状態が出ることを確認する。
        """
        review = self.review()
        old_hash = digest("old code\n")
        self.merge(review, self.comment(old_hash))
        comment = review["files"][0]["comments"][0]
        self.assertEqual(comment["code_hash"], old_hash)
        self.assertEqual(comment["hash_status"], "mismatch")
        self.assertTrue(review["hash_warnings"])
        report = walkthrough.self_review(review)
        self.assertIn(old_hash, report)
        self.assertIn("mismatch", report)

    def test_missing_hash_is_unverified_and_invalid_hash_rejected(self):
        """ハッシュ省略と、ハッシュの形式不正を別の扱いとして確認する。

        省略した解説はmissing警告付きで保持する。文字数不正・非16進文字・数値型は
        記録値として不正なので、各入力のマージがValueErrorになることを確認する。
        """
        review = self.review()
        self.merge(review, self.comment())
        self.assertEqual(review["files"][0]["comments"][0]["hash_status"], "missing")
        self.assertTrue(review["hash_warnings"])
        for value in ("not-a-sha256", "x" * 64, 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.merge(self.review(), self.comment(value))

    def test_old_side_compares_old_code_not_new_code(self):
        """2行目の置換差分に対して、旧版全文のハッシュをold側へ指定する。

        新版ハッシュを誤って比較先にすると不一致になる入力を使い、
        正しく旧版を参照してverifiedになることを確認する。
        """
        patch = "--- a/code.py\n+++ b/code.py\n@@ -2 +2 @@\n-old\n+second\n"
        review = self.review(patch)
        self.merge(review, self.comment(digest("first\nold\n"), side="old"))
        self.assertEqual(review["files"][0]["comments"][0]["hash_status"], "verified")

    def test_rebuild_preserves_comments_and_warns_if_code_changed(self):
        """旧スナップショットへ正しい解説をマージした後、実本文を変更する。

        新レビューへpreserve_commentsで解説を移し、概要を保持したままmismatchと
        判定することを確認する。記録済みcode_hashが新しい値へ改ざんされないことも確認する。
        """
        previous = self.review()
        self.merge(previous, self.comment(digest("first\nsecond\n")))
        self.source.write_bytes(b"first\nchanged\n")
        rebuilt = self.review()
        walkthrough.preserve_comments(previous, rebuilt)
        self.assertEqual(rebuilt["overview"], "概要")
        self.assertEqual(rebuilt["files"][0]["comments"][0]["hash_status"], "mismatch")
        self.assertEqual(rebuilt["files"][0]["comments"][0]["code_hash"], digest("first\nsecond\n"))

    def test_disappeared_range_retained_with_warning_instead_of_attached_to_other_line(self):
        """2行目を説明する解説を作ってから、実本文を1行だけに縮める。

        再生成したreviewへ旧解説を移したとき、start_lineを他の行へ付け替えず2のまま残す。
        range_statusがmissing、hash_statusがmismatchになり、消失を明示できることを確認する。
        """
        previous = self.review()
        self.merge(previous, self.comment(digest("first\nsecond\n")))
        self.source.write_bytes(b"first\n")
        rebuilt = self.review()
        walkthrough.preserve_comments(previous, rebuilt)
        comment = rebuilt["files"][0]["comments"][0]
        self.assertEqual(comment["range_status"], "missing")
        self.assertEqual(comment["start_line"], 2)
        self.assertEqual(comment["hash_status"], "mismatch")

    def test_missing_range_does_not_hide_unexplained_lines(self):
        """4行本文の2〜4行を説明した後、本文を3行へ縮める。

        存在しなくなった終端を持つ解説は、有効な説明範囲として数えない。
        self-reviewの未解説範囲に現在の1〜3行すべてが残ることを確認する。
        """
        self.source.write_bytes(b"one\ntwo\nthree\nfour\n")
        previous = self.review()
        self.merge(previous, self.comment(digest("one\ntwo\nthree\nfour\n"), end_line=4))
        self.source.write_bytes(b"one\ntwo\nthree\n")
        rebuilt = self.review()
        walkthrough.preserve_comments(previous, rebuilt)
        self.assertIn("新行: 1–3", walkthrough.self_review(rebuilt))

    def test_stale_merge_retains_outdated_range_and_link(self):
        """不一致ハッシュと、消えた5〜7行・存在しない9行へのリンクを同時に渡す。

        古い解説の再確認に必要な情報を捨てず、範囲missingとリンク警告を保存する。
        ハッシュ・範囲・リンクの3種の警告が一覧に揃うことを確認する。
        """
        review = self.review()
        self.merge(review, self.comment(
            digest("old code\n"), start_line=5, end_line=7,
            body="[以前の処理](review:code.py#L9)",
        ))
        comment = review["files"][0]["comments"][0]
        self.assertEqual(comment["range_status"], "missing")
        self.assertTrue(comment["link_warning"])
        self.assertEqual(len(review["hash_warnings"]), 3)

    def test_removed_target_comment_is_retained_and_restorable(self):
        """正しい解説があるファイルを対象リストから外し、その後再び対象に戻す。

        対象なしのレビューではunattached_commentsとself-reviewへ旧解説を残す。
        対象復帰後は同じ解説をファイルへ戻し、verifiedかつ未所属解説なしになることを確認する。
        """
        previous = self.review()
        self.merge(previous, self.comment(digest("first\nsecond\n")))
        empty = walkthrough.create_review(self.project, {"files": []})
        walkthrough.preserve_comments(previous, empty)
        self.assertEqual(len(empty["unattached_comments"]), 1)
        self.assertIn("二つ目の処理", walkthrough.self_review(empty))
        restored = self.review()
        walkthrough.preserve_comments(empty, restored)
        self.assertEqual(restored["files"][0]["comments"][0]["hash_status"], "verified")
        self.assertFalse(restored["unattached_comments"])

    def test_new_comments_clear_previous_hash_warnings(self):
        """最初に古いハッシュの解説をマージし、続いて確認済みのハッシュで置換する。

        警告が前回の状態から残留せず消えることと、同じIDの解説が
        重複追加されず1件だけ保存されることを確認する。
        """
        review = self.review()
        self.merge(review, self.comment(digest("old\n")))
        self.merge(review, self.comment(digest("first\nsecond\n")))
        self.assertFalse(review["hash_warnings"])
        self.assertEqual(len(review["files"][0]["comments"]), 1)

    def test_saved_text_and_rows_must_agree(self):
        """生成済みreviewのtextだけを書き換え、rowsとの矛盾を意図的に作る。

        マージ時に片方だけを信じて照合済みとせず、ValueErrorを要求する。
        保存データの内部不整合から誤ったハッシュ保証を作らないための検証。
        """
        review = self.review()
        review["files"][0]["text"] = "modified separately\n"
        with self.assertRaises(ValueError):
            self.merge(review, self.comment(digest("first\nsecond\n")))

    def test_rebuild_into_other_project_output_is_rejected(self):
        """reviewをdeepcopyし、コピー側のproject_rootだけを別フォルダへ変える。

        preserve_commentsが別プロジェクトの解説を誤って引き継がないよう、
        プロジェクトの同一性が失われた入力をValueErrorで拒否することを確認する。
        """
        previous = self.review()
        different = copy.deepcopy(previous)
        different["project_root"] = str(self.root / "other")
        with self.assertRaises(ValueError):
            walkthrough.preserve_comments(previous, different)

    def test_case_and_entity_encoded_review_links_are_validated(self):
        """大文字混在のReview:とHTML実体参照によるreview&#58;を準備する。

        どちらも不在ファイル・範囲外行を指す本文としてマージする。
        表記の違いだけで内部リンク検証を回避できず、例外になることを確認する。
        """
        for link in ("Review:missing.py#L999", "review&#58;missing.py#L999"):
            with self.subTest(link=link), self.assertRaises(ValueError):
                self.merge(self.review(), self.comment(body=f"[参照]({link})"))

    def test_actual_cli_rebuild_keeps_hash_and_emits_warning(self):
        """対象JSONを書き、CLI入口を通じてbuild→merge→本文変更→再buildを行う。

        初回処理の標準出力を抑え、最後の再buildの出力だけを取得して警告文を確認する。
        メモリ上の戻り値に加え、ディスクへ保存したreview.jsonのmismatchと、
        HTMLへ埋め込まれたhash_warningsも確認する。
        """
        targets = self.root / "targets.json"
        targets.write_text(json.dumps(self.targets), encoding="utf-8")
        output = self.root / "review"
        args = ["build", str(self.project), "--targets", str(targets), "--output", str(output)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(walkthrough.main(args), 0)
        comments = self.root / "comments.json"
        comments.write_text(json.dumps({"comments": [self.comment(digest("first\nsecond\n"))]}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(walkthrough.main(["merge", str(output), "--comments", str(comments)]), 0)
        self.source.write_bytes(b"first\nnew code\n")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(walkthrough.main(args), 0)
        self.assertIn("警告:", printed.getvalue())
        generated = walkthrough.read_json(output / "review.json")
        self.assertEqual(generated["files"][0]["comments"][0]["hash_status"], "mismatch")
        self.assertIn("hash_warnings", (output / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    # モジュールとしての読み込みでは準備だけに留め、直接実行した場合にテストを開始する。
    unittest.main()
