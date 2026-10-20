"""スキルと実行に必要なファイルを、指定したスキル配置先へ一式でコピーする。

配置先は --skills-dir で必ず明示する。ユーザー全体の設定や探索先を自動変更せず、
ダウンロードも行わない。既存スキルは同じ内容なら再利用し、異なる内容なら保護する。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

if __package__:
    from .package_skill import SKILL_NAME, absolute_path, collect_payload, reject_links
else:
    from package_skill import SKILL_NAME, absolute_path, collect_payload, reject_links


ROOT = Path(__file__).resolve().parents[2]


def install_skill(skills_dir, root=ROOT):
    """指定フォルダ直下へ自己完結したスキルを配置し、絶対パスを返す。

    同じ内容の再実行は書き込みを行わない。内容が異なる既存スキルの上書きや、
    不足ファイルだけの追記は行わず、利用者の編集や別バージョンを保護する。
    新規配置は同じ親の一時フォルダで完成させてから名前を変更する。
    """
    skills_dir = absolute_path(skills_dir)
    destination = skills_dir / SKILL_NAME
    reject_links(destination)
    payload = collect_payload(absolute_path(root) / "skills" / SKILL_NAME)
    if destination.exists():
        if not destination.is_dir():
            raise ValueError(f"Skill destination is not a directory: {destination}")
        for name, content in payload.items():
            existing = destination / name
            reject_links(existing)
            if not existing.is_file() or existing.read_bytes() != content:
                raise ValueError(
                    f"Existing skill differs; no files were changed: {destination}. "
                    "Use a new --skills-dir or preserve the old skill before replacing it."
                )
        return destination
    skills_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".walkthrough-install-", dir=skills_dir) as temporary:
        staging = Path(temporary) / SKILL_NAME
        for name, content in payload.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        # 生成中に別の実行が配置先を作成した場合も上書きせず停止する。
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"Skill destination appeared during installation: {destination}")
        reject_links(destination)
        staging.rename(destination)
    return destination


def main(argv=None):
    """明示された配置先へインストールし、失敗理由を標準エラーと終了値で伝える。"""
    parser = argparse.ArgumentParser(description="walkthrough-reviewを実行ツール一式と配置する")
    parser.add_argument("--skills-dir", required=True, type=Path, help="スキルを配置する親フォルダ")
    args = parser.parse_args(argv)
    try:
        destination = install_skill(args.skills_dir)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"スキル配置完了: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
