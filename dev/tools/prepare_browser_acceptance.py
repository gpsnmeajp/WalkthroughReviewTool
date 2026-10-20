"""開発用: 実ブラウザー受入確認に使う静的レビューと専用入力を生成する。

リポジトリのサンプルや製品コードは変更しない。生成物はすべて
output/browser-acceptance/ に限定する。このスクリプトの実行自体は
ブラウザー受入テストの成功を意味せず、表示と操作は別途確認する。

    python tools/prepare_browser_acceptance.py
    python tools/prepare_browser_acceptance.py hash-mutate
    python tools/prepare_browser_acceptance.py hash-rebuild
    python tools/prepare_browser_acceptance.py clipboard-failure

prepare（省略時）は生成済みの専用fixtureも初期状態へ戻す。
hash-mutateは入力コードだけを変更し、HTMLが自動更新されないことを確認する。
hash-rebuildは同じ出力先を再ビルドし、不一致・消失行・対象外解説を再現する。
clipboard-failureはdiffのコピーに開発用の故障注入を追加する。他のfixtureは更新しない。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import walkthrough  # noqa: E402


OUTPUT = ROOT / "output" / "browser-acceptance"
MARKER = OUTPUT / ".fixture-owner"
MARKER_TEXT = "WalkthroughReviewTool browser acceptance fixtures v1\n"


def checked_path(path):
    """固定の生成先配下にある通常パスだけを許可し、リンク経由の書込みを拒否する。"""
    path = Path(path)
    if not walkthrough.is_inside(path.resolve(), OUTPUT.resolve()):
        raise ValueError(f"Fixture output must stay inside {OUTPUT}: {path}")
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (candidate.exists() and walkthrough.is_link(candidate)):
            raise ValueError(f"Fixture paths cannot use links: {candidate}")
        if candidate == ROOT:
            break
    return path


def save(path, text):
    """検証済みの開発用パスへUTF-8/LFのテキストを書き込む。"""
    path = checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    walkthrough.write_text(path, text)


def save_json(path, value):
    save(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def initialize_output():
    """専用マーカーがある出力先だけを再利用し、無関係な既存フォルダを保護する。"""
    checked_path(OUTPUT)
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        if not MARKER.is_file() or MARKER.read_text(encoding="utf-8") != MARKER_TEXT:
            raise ValueError(f"Existing directory is not this fixture builder's output: {OUTPUT}")
    save(MARKER, MARKER_TEXT)


def require_output():
    checked_path(MARKER)
    if not MARKER.is_file() or MARKER.read_text(encoding="utf-8") != MARKER_TEXT:
        raise ValueError("Run prepare before hash-mutate or hash-rebuild")


def write_fixture(name, project, targets, comments=None, patch=None):
    """製品APIで新規スナップショットを生成し、現行のUI素材をコピーする。"""
    destination = checked_path(OUTPUT / name)
    walkthrough.require_external_output(destination.resolve(), project.resolve())
    review = walkthrough.create_review(project.resolve(), targets, patch)
    if comments is not None:
        walkthrough.merge_comments(review, comments(review) if callable(comments) else comments)
    walkthrough.write_review(review, destination, copy_assets=True)
    return review


def comment(review, identity, file, start, end, title, body, missing_hash=False):
    """表示用fixtureの解説を作る。未記録ケース以外は生成済みコードHashを使う。"""
    result = {
        "id": identity, "file": file, "start_line": start, "end_line": end,
        "side": "new", "title": title, "body": body,
    }
    if not missing_hash:
        result["code_hash"] = next(item for item in review["files"] if item["path"] == file)["code_hashes"]["new"]
    return result


def long_comments(review):
    paragraphs = "\n\n".join(
        f"段落 {index:02d}: 長い解説を読み進めるための文章です。"
        "コード側の同じ開始行に留まる区間と、次の解説へ移動する区間を確認します。"
        "スクロールを止めた後に左右の位置が往復し続けないことも確認します。"
        for index in range(1, 31)
    )
    return {
        "overview": "# 長文・重複範囲・未照合の確認\n\n[先頭](review:long.py#L1)から160行を表示します。",
        "comments": [
            comment(review, "long-start", "long.py", 1, 8, "先頭の解説", "先頭の8行を確認します。"),
            comment(review, "long-body", "long.py", 40, 60, "長い解説", paragraphs),
            comment(review, "long-overlap", "long.py", 40, 70, "同じ開始行の二枚目", "前のカードと開始行が同じです。このカードの行範囲を押しても前のカードへ戻りません。"),
            comment(review, "long-middle", "long.py", 100, 110, "後半の解説", "コードと解説の両側からこの範囲へ移動します。"),
            comment(review, "long-missing", "long.py", 150, 160, "Hash未記録の末尾解説", "未照合警告があっても本文・質問操作を使用できます。", missing_hash=True),
        ],
    }


def hash_comments(review):
    return {
        "overview": "# コード変更の段階確認\n\n初期状態は全解説が一致します。[末尾](review:main.py#L10)と[追加ファイル](review:extra.py#L1)も確認します。",
        "comments": [
            comment(review, "hash-start", "main.py", 1, 2, "残る行の説明", "再ビルド後も範囲は残り、コードHashだけが不一致になります。"),
            comment(review, "hash-later", "main.py", 10, 12, "消える行の説明", "再ビルド後は元の行範囲がなくなります。他の行へ誤って結び付けないことを確認します。"),
            comment(review, "hash-extra", "extra.py", 1, 2, "対象から外れるファイル", "再ビルド時に対象から外します。解説は警告とself-review.mdに保持されます。"),
        ],
    }


def prepare():
    initialize_output()
    fixtures = ROOT / "tests" / "fixtures"
    write_fixture(
        "full", fixtures / "project",
        walkthrough.read_json(fixtures / "targets.json"),
        walkthrough.read_json(fixtures / "comments.json"),
    )
    write_fixture(
        "diff", fixtures / "project",
        walkthrough.read_json(fixtures / "targets-diff.json"),
        walkthrough.read_json(fixtures / "comments-diff.json"),
        (fixtures / "diff.patch").read_text(encoding="utf-8"),
    )
    prepare_clipboard_failure()
    empty_project = OUTPUT / "projects" / "empty"
    save(empty_project / "empty.py", "")
    save(empty_project / "main.py", 'def greeting():\n    return "browser acceptance"\n')
    write_fixture("unmerged", empty_project, {"title": "未マージ・空ファイル", "files": ["empty.py", "main.py"]})
    write_fixture("no-targets", empty_project, {"title": "対象なし", "files": []})
    long_project = OUTPUT / "projects" / "long"
    save(long_project / "long.py", "".join(f"value_{line:03d} = {line}  # acceptance line {line}\n" for line in range(1, 161)))
    write_fixture("long", long_project, {"title": "長文とスクロール連動", "files": ["long.py"]}, long_comments)
    hash_project = OUTPUT / "projects" / "hash"
    save(hash_project / "main.py", "".join(f"value_{line:02d} = {line}\n" for line in range(1, 13)))
    save(hash_project / "extra.py", 'extra = "retained source"\nprint(extra)\n')
    hash_targets = {"title": "Hashの段階確認", "files": ["main.py", "extra.py"]}
    save_json(OUTPUT / "hash-targets.json", hash_targets)
    save_json(OUTPUT / "hash-targets-rebuild.json", {"title": "Hashの段階確認", "files": ["main.py"]})
    write_fixture("hash", hash_project, hash_targets, hash_comments)
    save_json(OUTPUT / "expectations.json", {
        "full": {"mode": "full", "targets": 2, "excluded": 2, "comments": 4, "verified": 4},
        "diff": {"mode": "diff", "targets": 3, "excluded": 2, "comments": 4, "verified": 4, "deleted": "legacy.py", "deleted_lines": 3},
        "clipboard-failure": {"base": "diff", "fault": "Clipboard API rejects and execCommand copy returns false", "expected": "Manual Ctrl+C notice; complete prompt text selected"},
        "unmerged": {"targets": 2, "comments": 0, "empty_file": "empty.py", "nonempty_file": "main.py"},
        "no-targets": {"targets": 0, "excluded": 2, "comments": 0},
        "long": {"targets": 1, "lines": 160, "comments": 5, "verified": 4, "missing_hash": 1, "overlapping_start_line": 40},
        "hash_initial": {"targets": 2, "comments": 3, "verified": 3, "main_lines": 12},
        "hash_mutated_source_only": {"snapshot_main_lines": 12, "source_main_lines": 3, "verified": 3, "html_unchanged": True},
        "hash_rebuilt": {"targets": 1, "main_lines": 3, "mismatch": 2, "missing_range": 1, "unattached": 1, "excluded": 1},
    })
    print(f"Prepared: {OUTPUT}")
    for name in ("full", "diff", "clipboard-failure", "unmerged", "no-targets", "long", "hash"):
        print((OUTPUT / name / "index.html").as_uri())
    print("\nNext: python tools/prepare_browser_acceptance.py hash-mutate")
    print("Reload hash/index.html: it must still show 12 original lines and 3 verified comments.")
    print("Then: python tools/prepare_browser_acceptance.py hash-rebuild")
    print("Reload hash/index.html: 3 new lines, 2 mismatches, 1 missing range, 1 unattached comment.")


def prepare_clipboard_failure():
    """diffの専用コピーにだけ自動コピー失敗を注入し、手動コピー表示を再現する。"""
    require_output()
    source = checked_path(OUTPUT / "diff" / "review.json")
    destination = checked_path(OUTPUT / "clipboard-failure")
    review = walkthrough.read_json(source)
    review["title"] = f"{review['title']} [開発用: 自動コピー故障注入]"
    review["overview"] = (
        "> 開発用fixture: 自動コピーを意図的に失敗させています。"
        "質問文を作成し、コピーを押すと手動コピーの案内と全文選択が表示されます。\n\n"
        + review["overview"]
    )
    walkthrough.write_review(review, destination, copy_assets=True)
    fault = """/* DEVELOPMENT FIXTURE ONLY. Never included in production assets.
 * Fail both automatic clipboard paths to verify the manual-copy fallback.
 * Native keyboard copying and all unrelated execCommand calls remain intact.
 */
"use strict";
Object.defineProperty(navigator, "clipboard", {
  configurable: true,
  value: {
    writeText() {
      return Promise.reject(new DOMException("Development fixture: clipboard intentionally unavailable", "NotAllowedError"));
    }
  }
});
const originalExecCommand = document.execCommand.bind(document);
document.execCommand = function(command, ...args) {
  if (String(command).toLowerCase() === "copy") return false;
  return originalExecCommand(command, ...args);
};
"""
    save(destination / "assets" / "dev-clipboard-failure.js", fault)
    index = destination / "index.html"
    html = index.read_text(encoding="utf-8")
    anchor = '  <script defer src="assets/app.js"></script>'
    if html.count(anchor) != 1:
        raise ValueError("Cannot inject development clipboard failure before app.js")
    save(index, html.replace(
        anchor,
        '  <!-- DEVELOPMENT FIXTURE ONLY: intentionally fail automatic clipboard copying. -->\n'
        '  <script defer src="assets/dev-clipboard-failure.js" data-dev-only="clipboard-failure"></script>\n'
        + anchor,
    ))
    print(f"Prepared clipboard-failure only: {index.as_uri()}")


def mutate_hash():
    require_output()
    index = checked_path(OUTPUT / "hash" / "index.html")
    before = hashlib.sha256(index.read_bytes()).hexdigest()
    save(OUTPUT / "projects" / "hash" / "main.py", 'value_01 = "changed"\nvalue_02 = 200\nvalue_03 = 300\n')
    after = hashlib.sha256(index.read_bytes()).hexdigest()
    if before != after:
        raise RuntimeError("Source-only mutation unexpectedly changed the HTML")
    print("Only projects/hash/main.py changed to 3 lines.")
    print(f"HTML unchanged, SHA-256: {after}")
    print("Reload hash/index.html before running hash-rebuild.")


def rebuild_hash():
    require_output()
    if (OUTPUT / "projects" / "hash" / "main.py").read_text(encoding="utf-8").splitlines() != [
        'value_01 = "changed"', "value_02 = 200", "value_03 = 300",
    ]:
        raise ValueError("Run hash-mutate before hash-rebuild")
    code = walkthrough.main([
        "build", str(OUTPUT / "projects" / "hash"),
        "--targets", str(OUTPUT / "hash-targets-rebuild.json"),
        "--output", str(OUTPUT / "hash"),
    ])
    if code:
        raise RuntimeError(f"Hash rebuild failed with exit code {code}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "hash-mutate", "hash-rebuild", "clipboard-failure"), nargs="?", default="prepare")
    args = parser.parse_args()
    {"prepare": prepare, "hash-mutate": mutate_hash, "hash-rebuild": rebuild_hash, "clipboard-failure": prepare_clipboard_failure}[args.phase]()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
