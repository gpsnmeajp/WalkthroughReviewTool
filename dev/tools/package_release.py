"""実行に必要な一式と監査用ソースを、dev/dist/の出荷用ZIPへまとめる。

実行方法:
    Windowsではdev/package.batを実行する。
    他の環境ではdevでpython tools/package_release.pyを実行できる。

この処理はコードの変換やライブラリのダウンロードを行わない。
SHIP_FILESで明示した既存ファイルのバイト列を、そのままZIPへ保存する。
出力先はこのリポジトリのdev/dist/に固定し、現在の作業ディレクトリに依存しない。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import sys
import tempfile
import zipfile


# 配布名はZIPの名前とZIP内の最上位フォルダ名に共通して使う。
# 一つのフォルダにまとめることで、展開先に個々のファイルが散らばらない。
PACKAGE_NAME = "WalkthroughReviewTool"
ROOT = Path(__file__).resolve().parents[2]

# 出荷範囲は再帰的な「全部コピー」ではなく、具体的なファイル名で固定する。
# そのため.git、.test-tmp、output、dist、個人の設定や過去のレビューは混入しない。
# 実行時に必要なファイルを増やした場合は、この一覧にも追加する。
# テストと再梱包用コードも含め、受け取った人が挙動を確認・再出荷できるようにする。
SHIP_FILES = (
    ".gitattributes",
    ".gitignore",
    "dev/walkthrough.py",
    "dev/diff_parser.py",
    "dev/package.bat",
    "AGENTS.md",
    "README.md",
    "dev/docs/DRECTION.md",
    "dev/README.md",
    "dev/docs/data-format.md",
    "skills/walkthrough-review/SKILL.md",
    "skills/walkthrough-review/scripts/walkthrough.py",
    "skills/walkthrough-review/scripts/diff_parser.py",
    "skills/walkthrough-review/scripts/prepare_review.py",
    "skills/walkthrough-review/scripts/comment_cards.py",
    "skills/walkthrough-review/scripts/render_svg.py",
    "skills/walkthrough-review/assets/templates/index.html",
    "skills/walkthrough-review/assets/ui/app.js",
    "skills/walkthrough-review/assets/ui/highlight-worker.js",
    "skills/walkthrough-review/assets/ui/style.css",
    "skills/walkthrough-review/assets/ui/vendor/marked.js",
    "skills/walkthrough-review/assets/ui/vendor/marked-LICENSE.txt",
    "skills/walkthrough-review/assets/ui/vendor/prism.js",
    "skills/walkthrough-review/assets/ui/vendor/prism.css",
    "skills/walkthrough-review/assets/ui/vendor/prism-LICENSE.txt",
    "skills/walkthrough-review/assets/ui/vendor/versions.json",
    "skills/walkthrough-review/references/input-format.md",
    "skills/walkthrough-review/references/card-authoring.md",
    "skills/walkthrough-review/references/delegation.md",
    "skills/walkthrough-review/references/svg-diagrams.md",
    "skills/walkthrough-review/references/diff-review.md",
    "skills/walkthrough-review/references/third-party-notices.md",
    "skills/walkthrough-review/agents/openai.yaml",
    "dev/tests/fixtures/targets.json",
    "dev/tests/fixtures/targets-diff.json",
    "dev/tests/fixtures/comments.json",
    "dev/tests/fixtures/comments-diff.json",
    "dev/tests/fixtures/diff.patch",
    "dev/tests/fixtures/project/config.json",
    "dev/tests/fixtures/project/notes.txt",
    "dev/tests/fixtures/project/src/main.py",
    "dev/tests/fixtures/project/src/pricing.py",
    "dev/tests/fixtures/project-before/config.json",
    "dev/tests/fixtures/project-before/notes.txt",
    "dev/tests/fixtures/project-before/legacy.py",
    "dev/tests/fixtures/project-before/src/main.py",
    "dev/tests/fixtures/project-before/src/pricing.py",
    "dev/tests/test_diff_parser.py",
    "dev/tests/test_walkthrough.py",
    "dev/tests/test_scaling.py",
    "dev/tests/test_hashes.py",
    "dev/tests/test_diagrams.py",
    "dev/tests/test_package_release.py",
    "dev/tests/test_package_skill.py",
    "dev/tests/test_install_skill.py",
    "dev/tests/test_prepare_review.py",
    "dev/tests/test_browser_acceptance.py",
    "dev/tests/test_comment_cards.py",
    "dev/tests/test_render_svg.py",
    "dev/tests/ui_logic.test.cjs",
    "dev/tools/vendor_assets.py",
    "dev/tools/package_release.py",
    "dev/tools/package_skill.py",
    "dev/tools/install_skill.py",
    "dev/tools/prepare_review.py",
    "dev/tools/comment_cards.py",
    "dev/tools/render_svg.py",
    "dev/tools/prepare_browser_acceptance.py",
    "dev/tools/benchmark_scaling.py",
    "dev/tests/evals.json",
)


def reject_link(path):
    """既存のパスがリンクなら停止する。存在しない通常パスには何もしない。

    シンボリックリンクはリンク先の有無に関係なく拒否する。
    Windowsではディレクトリジャンクションも再解析ポイントとして検出する。
    読み取り元や出力先のリンクを通じ、リポジトリ外の内容を出荷・上書きしないため。
    """
    if path.is_symlink():
        raise ValueError(f"Links cannot be packaged or used as output: {path}")
    if path.exists():
        # statではなくlstatを使い、リンク先ではなく指定した項目自身を調べる。
        info = path.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(f"Reparse points cannot be used: {path}")


def validate_output_file(path):
    """出力名が未使用か、置き換え可能な種類の通常ファイルであることを確認する。

    リンクに加え、ディレクトリや特殊ファイルも拒否する。ZIPだけを更新した後で
    ハッシュ出力先がフォルダだと判明する事態を、書き込み前に防ぐための検査である。
    書き込み権限や他プロセスのファイルロックまで保証するものではなく、
    実際の置換がOSに拒否された場合は、その例外を呼び出し元へ伝える。
    """
    reject_link(path)
    if path.exists() and not path.is_file():
        raise ValueError(f"Output path is not a regular file: {path}")


def collect_files(root):
    """出荷一覧の全ファイルを検査し、相対パス→元バイト列の辞書を返す。

    この関数ではファイルを作成・変更しない。必須ファイルの欠落やリンク、
    読み取り中の更新を発見した場合は例外を返し、梱包を開始させない。
    辞書にバイト列を保持し、後続のZIP作成とハッシュ計算で同じ内容を使う。
    """
    payload = {}
    for relative in sorted(SHIP_FILES):
        path = root / relative
        # 対象ファイルだけでなく、その親フォルダにもリンクがないことを確認する。
        # root自身は呼び出し元でresolve済みなので、そこに到達したら検査を終える。
        for component in (path, *path.parents):
            if component == root:
                break
            reject_link(component)
        # 一覧が将来誤って編集された場合にも、親へ抜けるパスを収録しない。
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"A shipping file is outside the project: {relative}")
        if not path.is_file():
            raise ValueError(f"Required shipping file is missing: {relative}")
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        # 通常の編集・保存によるサイズ/更新時刻の変化を検出する。
        # OS全体のロックではないため、全ファイルを同時点に固定する保証ではない。
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError(f"A shipping file changed while being read: {relative}")
        payload[relative] = content
    return payload


def checksum_manifest(payload):
    """収録ファイルごとのSHA-256を、展開後の相対パスとともに記載する。

    マニフェスト自身のハッシュを自分に含めることはできないので、
    対象はSHIP_FILESのファイルだけとする。改行をLFに固定し、順序もそろえる。
    元ファイルの改行・BOMを変換せず、実際にZIPへ入れるバイト列をハッシュ化する。
    """
    lines = [
        f"{hashlib.sha256(content).hexdigest()}  {relative}\n"
        for relative, content in sorted(payload.items())
    ]
    return "".join(lines).encode("utf-8")


def write_zip(path, payload):
    """元バイト列と検証一覧をZIPへ保存する。日時・権限・順序を固定する。

    ファイルの更新日時をZIPに入れると、内容が同じでもZIPが毎回変わる。
    ZIP形式で使える固定日時と通常ファイル権限を与え、同じ内容・実行環境で
    再梱包した場合に同じZIPを得られるようにする。元ソースは変更しない。
    """
    contents = {**payload, "SHA256SUMS": checksum_manifest(payload)}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, content in sorted(contents.items()):
            info = zipfile.ZipInfo(f"{PACKAGE_NAME}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            # 明示したZipInfoにも圧縮レベルを渡す。本文は加工せずそのまま書く。
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def verify_zip(path, payload):
    """完成候補を読み直し、一覧・CRC・各ファイルの内容を照合する。

    ファイル名の欠落・余計な収録・重複を検出し、圧縮データのCRCも検査する。
    さらに全ファイルを展開せずメモリへ読み、元バイト列と一致するか確認する。
    失敗時は例外を返すので、呼び出し元は前回の正式ZIPを置き換えない。
    """
    contents = {**payload, "SHA256SUMS": checksum_manifest(payload)}
    expected = {f"{PACKAGE_NAME}/{name}" for name in contents}
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(expected) or set(names) != expected:
            raise ValueError("The archive has missing, unexpected, or duplicate entries")
        if archive.testzip() is not None:
            raise ValueError("The archive failed its CRC check")
        for relative, content in contents.items():
            if archive.read(f"{PACKAGE_NAME}/{relative}") != content:
                raise ValueError(f"The archived file differs from its source: {relative}")


def build_package(root=ROOT):
    """出荷ZIPとそのSHA-256ファイルを作り、二つの絶対パスを返す。

    書き込み先はroot/dev/distだけ。入力確認と候補ZIP検証が終わるまで、
    既存のWalkthroughReviewTool.zipには触れない。既存フォルダの削除も行わない。
    ZIPと横に置くハッシュファイルは、それぞれ一時ファイルから置換する。
    二ファイル全体を一度に確定するトランザクションではない点に注意する。
    """
    root = Path(root).resolve()
    payload = collect_files(root)
    development = root / "dev"
    reject_link(development)
    destination = development / "dist"
    reject_link(destination)
    destination.mkdir(exist_ok=True)
    # resolve後の配置がroot/dev/distであることを確認してから固定名へ書き込む。
    if destination.resolve() != root / "dev" / "dist":
        raise ValueError("The distribution directory is outside the project")
    zip_path = destination / f"{PACKAGE_NAME}.zip"
    checksum_path = destination / f"{PACKAGE_NAME}.zip.sha256"
    # 両方の出力先の種類を先に確認する。一方を更新してから他方の型違いに気づかない。
    validate_output_file(zip_path)
    validate_output_file(checksum_path)
    temporary_paths = []
    try:
        # OSに重複しない一時名を作らせ、利用者の別ファイル名を上書きしない。
        # Windowsでは開いたままのファイルをZipFileで再オープンできないため一旦閉じる。
        with tempfile.NamedTemporaryFile(prefix=".package-", suffix=".tmp", dir=destination, delete=False) as stream:
            candidate_zip = Path(stream.name)
        temporary_paths.append(candidate_zip)
        write_zip(candidate_zip, payload)
        verify_zip(candidate_zip, payload)
        archive_hash = hashlib.sha256(candidate_zip.read_bytes()).hexdigest()
        with tempfile.NamedTemporaryFile(prefix=".checksum-", suffix=".tmp", dir=destination, delete=False) as stream:
            candidate_checksum = Path(stream.name)
            temporary_paths.append(candidate_checksum)
            stream.write(f"{archive_hash}  {zip_path.name}\n".encode("ascii"))
        # replaceの直前にも出力パスを検査する。正常な再実行では前回の生成物を更新する。
        validate_output_file(zip_path)
        validate_output_file(checksum_path)
        candidate_zip.replace(zip_path)
        candidate_checksum.replace(checksum_path)
    finally:
        # この呼び出しがdist直下に作った一時ファイルだけを後片付けする。
        # ディレクトリの再帰削除や、他のシェルへパスを渡す操作は行わない。
        for temporary in temporary_paths:
            if temporary.parent.resolve() != destination.resolve():
                raise ValueError("An unexpected temporary-file location was detected")
            if temporary.exists():
                temporary.unlink()
    return zip_path, checksum_path


def main():
    """コマンドラインの窓口。成功は0、梱包失敗は1としてバッチへ伝える。"""
    try:
        archive, checksums = build_package()
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    # バッチの既定コードページを変更しなくても読めるよう、案内の文言はASCIIにする。
    # 出力パスには利用者のフォルダ名がそのまま入る。
    print(f"Created: {archive}")
    print(f"SHA-256: {checksums}")
    print(f"Files: {len(SHIP_FILES)} + SHA256SUMS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
