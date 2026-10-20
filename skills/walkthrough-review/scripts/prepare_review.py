"""AIがレビュー準備を始めるための作業先と全ファイル一覧を作る。

PROJECTを省略すると現在の作業ディレクトリを対象にする。ツール自身の配置先を
レビュー対象として推測しない。出力先を省略した場合はOSの一時領域に毎回固有の
作業フォルダを作り、次のbuild/mergeや完成資料の閲覧に使えるよう保持する。

この処理の成功はレビューの完成を意味しない。対象の選定、解説の作成、マージ、
自己確認はスキルを実行するAIが続ける。元プロジェクトは書き換えない。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


# CLIと同じscripts/内に置き、スキルフォルダ全体を移しても参照が切れない。
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import walkthrough  # noqa: E402


def reject_links(path, stop=None):
    """出力先から親へたどり、リンクやWindowsのリパースポイントを拒否する。

    resolve前のパスを検査し、別の場所へ向くリンクを見落とさない。stopを指定した
    場合はその場所まで検査する。OSが提供した一時領域は先に実体へ解決して使うため、
    macOS等のシステム管理の一時領域のリンクまで一律に拒否しない。
    """
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (
            candidate.exists() and walkthrough.is_link(candidate)
        ):
            raise ValueError(f"作業先にリンクは使えません: {candidate}")
        if candidate == stop:
            break


def prepare_review(project=None, work_parent=None):
    """指定プロジェクトの一覧と、AIが使う絶対パスの辞書を作成して返す。

    出力親の内部にmkdtempで固有フォルダを確保するため、既存資料は上書きしない。
    ファイルの内容・対象の妥当性はここでは判定せず、scan_projectの全一覧を保存する。
    返すtargets/comments/review_dir/indexは後続処理の保存予定先であり、まだ作らない。
    """
    project = Path(project if project is not None else Path.cwd()).resolve()
    if not project.is_dir():
        raise ValueError(f"プロジェクトフォルダが見つかりません: {project}")

    if work_parent is None:
        # OS一時領域自体を先に解決し、その配下の専用出力親がリンクなら拒否する。
        temporary_root = Path(tempfile.gettempdir()).resolve()
        parent = temporary_root / "walkthrough-review-work"
        reject_links(parent, stop=temporary_root)
    else:
        parent = Path(work_parent).absolute()
        reject_links(parent)
    parent = parent.resolve()
    walkthrough.require_external_output(parent, project)

    # 走査を先に完了させ、対象不正などの失敗で空の作業フォルダを量産しない。
    inventory = walkthrough.scan_project(project)
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="review-", dir=parent)).resolve()
    session = {
        "project": project.as_posix(),
        "work": work.as_posix(),
        "cli": (ROOT / "walkthrough.py").as_posix(),
        "inventory": (work / "inventory.json").as_posix(),
        "targets": (work / "targets.json").as_posix(),
        "comments": (work / "comments.json").as_posix(),
        "review_dir": (work / "review").as_posix(),
        "index": (work / "review" / "index.html").as_posix(),
    }
    inventory_data = {"project_root": project.as_posix(), "inventory": inventory}
    for path, data in (
        (Path(session["inventory"]), inventory_data),
        (work / "session.json", session),
    ):
        # 新規作業先のみに書き、Windowsの既定文字コード・改行に依存させない。
        path.write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return session


def main(argv=None):
    """引数を検証し、成功時はセッションJSONだけを標準出力に返す。"""
    parser = argparse.ArgumentParser(description="AI用のレビュー作業先と全ファイル一覧を準備")
    parser.add_argument("project", nargs="?", type=Path, help="省略時は現在の作業ディレクトリ")
    parser.add_argument("--work-parent", type=Path, help="プロジェクト外の出力親。省略時はOS一時領域")
    args = parser.parse_args(argv)
    try:
        session = prepare_review(args.project, args.work_parent)
    except (OSError, ValueError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 2
    print(json.dumps(session, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
