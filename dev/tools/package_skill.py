"""自己完結したwalkthrough-reviewスキルだけを、.skillとZIPへ梱包する。

スキル本体を唯一の入力にし、dev内のCLIや作業資料は取り込まない。
.skillは先頭にwalkthrough-review/を持つZIPで、通常の.zip版も同じバイト列を使う。
Python標準ライブラリだけで検証・梱包し、外部コードの取得や実行は行わない。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SKILL_NAME = "walkthrough-review"
SKILL_ROOT = ROOT / "skills" / SKILL_NAME
# 配布に必要な実行ファイル・画面素材を固定し、evalsやキャッシュの混入を防ぐ。
REQUIRED_FILES = (
    "SKILL.md",
    "scripts/walkthrough.py",
    "scripts/diff_parser.py",
    "scripts/prepare_review.py",
    "scripts/comment_cards.py",
    "assets/templates/index.html",
    "assets/ui/app.js",
    "assets/ui/highlight-worker.js",
    "assets/ui/style.css",
    "assets/ui/vendor/marked.js",
    "assets/ui/vendor/marked-LICENSE.txt",
    "assets/ui/vendor/prism.js",
    "assets/ui/vendor/prism.css",
    "assets/ui/vendor/prism-LICENSE.txt",
    "assets/ui/vendor/versions.json",
)


def absolute_path(path):
    """リンク検査の前にresolveせず、相対パスだけを絶対パスへ変換する。"""
    return Path(os.path.abspath(Path(path).expanduser()))


def reject_links(path):
    """指定した項目と親に、シンボリックリンク・再解析ポイントがあれば拒否する。"""
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"Links cannot be used for skill packaging: {component}")
        if component.exists():
            attributes = getattr(component.lstat(), "st_file_attributes", 0)
            if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError(f"Reparse points cannot be used: {component}")


def validate_frontmatter(content):
    """このスキルで使う一行文字列のYAMLヘッダーを、外部パッケージなしで検査する。

    汎用YAMLパーサーを模倣せず、name/description/compatibilityの一行値だけを扱う。
    構文を拡張するときは、この検査も明示的に更新する。未知の形式は推測しない。
    """
    text = content.decode("utf-8-sig")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError("SKILL.md frontmatter must end with ---") from None
    fields = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([a-z][a-z-]*):[ \t]*(.*)", line)
        if not match:
            raise ValueError("Frontmatter must use supported single-line string fields")
        key, value = match.groups()
        if key not in {"name", "description", "compatibility"} or key in fields:
            raise ValueError(f"Unsupported or duplicate frontmatter field: {key}")
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid quoted frontmatter value: {key}") from error
        elif value.startswith("'"):
            if not re.fullmatch(r"'(?:[^']|'')*'", value):
                raise ValueError(f"Invalid quoted frontmatter value: {key}")
            value = value[1:-1].replace("''", "'")
        elif (
            not value or value in {"|", ">", "|-", ">-", "|+", ">+"}
            or value.startswith(("[", "{", "&", "*", "!"))
            or ": " in value or " #" in value
            or value.lower() in {"null", "~", "true", "false", "yes", "no", "on", "off"}
            or re.fullmatch(r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?", value)
        ):
            raise ValueError(f"Use a plain or quoted single-line string for {key}")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Frontmatter {key} must be a nonempty string")
        fields[key] = value
    if "name" not in fields or "description" not in fields:
        raise ValueError("SKILL.md requires name and description")
    if fields["name"] != SKILL_NAME:
        raise ValueError(f"Skill name must be {SKILL_NAME}")
    if len(fields["description"]) > 1024 or any(
        character in fields["description"] for character in "<>"
    ):
        raise ValueError("Description must be at most 1024 characters without angle brackets")
    if len(fields.get("compatibility", "")) > 500:
        raise ValueError("Compatibility must be at most 500 characters")
    return fields


def validate_markdown_links(payload):
    """配布するMarkdownのローカルリンクが、同じスキル内の収録物を指すか検査する。

    コード例内の擬似リンクやHTTP等の外部参照は対象にしない。フラグメントは文書内の
    見出しなどを指すため、ここではファイルの存在だけを確認する。
    """
    inline = re.compile(r"!?\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)")
    reference = re.compile(r"^\s*\[[^\]]+\]:\s*(?:<([^>]+)>|(\S+))", re.MULTILINE)
    for name, content in payload.items():
        if not name.endswith(".md"):
            continue
        # コードフェンス内は実際に辿るリンクではない。可変長のフェンスも扱う。
        visible = []
        fence = None
        for line in content.decode("utf-8-sig").splitlines():
            marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
                continue
            if fence is None:
                visible.append(line)
        markdown = "\n".join(visible)
        for match in (*inline.finditer(markdown), *reference.finditer(markdown)):
            target = match.group(1) or match.group(2)
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            path = unquote(parsed.path)
            if "\\" in path or path.startswith("/"):
                raise ValueError(f"Local Markdown link must be relative: {name}: {target}")
            resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(name).parent), path))
            if resolved == ".." or resolved.startswith("../"):
                raise ValueError(f"Markdown link leaves the skill: {name}: {target}")
            if resolved != "." and resolved not in payload and not any(
                item.startswith(resolved.rstrip("/") + "/") for item in payload
            ):
                raise ValueError(f"Markdown link is not packaged: {name}: {target}")


def collect_payload(skill_dir=SKILL_ROOT):
    """必須ファイルと補助資料を検査し、相対パスから元バイト列への辞書を返す。"""
    skill_dir = absolute_path(skill_dir)
    reject_links(skill_dir)
    names = list(REQUIRED_FILES)
    references = skill_dir / "references"
    reject_links(references)
    if references.exists():
        if not references.is_dir():
            raise ValueError(f"Skill references are not a directory: {references}")

        def report_walk_error(error):
            """資料の読み取り失敗を、資料が存在しないものとして扱わない。"""
            raise error

        for current, directories, files in os.walk(
            references, followlinks=False, onerror=report_walk_error
        ):
            current = Path(current)
            for name in directories:
                reject_links(current / name)
            directories[:] = [name for name in directories if name != "__pycache__"]
            for name in files:
                if name.endswith(".md"):
                    names.append((current / name).relative_to(skill_dir).as_posix())
    metadata = skill_dir / "agents/openai.yaml"
    reject_links(metadata)
    if metadata.exists():
        names.append("agents/openai.yaml")
    payload = {}
    for name in sorted(names):
        source = skill_dir / name
        reject_links(source)
        if not source.is_file():
            raise ValueError(f"Required skill file is missing: {source}")
        before = source.stat()
        content = source.read_bytes()
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"Skill file changed while being read: {source}")
        payload[name] = content
    validate_frontmatter(payload["SKILL.md"])
    validate_markdown_links(payload)
    return payload


def write_archive(path, payload):
    """収録順・日時・権限を固定し、同じ本文から同じZIPを生成する。"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(payload.items()):
            info = zipfile.ZipInfo(f"{SKILL_NAME}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def verify_archive(path, payload):
    """ZIPの収録名・重複・CRCと元バイト列を、保存後に読み直して照合する。"""
    expected = {f"{SKILL_NAME}/{name}" for name in payload}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(expected) or set(names) != expected:
            raise ValueError("Skill archive has missing, unexpected, or duplicate entries")
        if archive.testzip() is not None:
            raise ValueError("Skill archive failed its CRC check")
        for name, content in payload.items():
            if archive.read(f"{SKILL_NAME}/{name}") != content:
                raise ValueError(f"Skill archive differs from source: {name}")


def build_package(skill_dir=SKILL_ROOT, output_dir=None):
    """検証済みのスキルを.skill/.zipと各SHA-256ファイルへ出力する。

    出力名を全て確認し、一時領域で作成・検証が成功してから正式ファイルを更新する。
    OSのI/O障害による複数ファイルの同時更新までは保証しない。旧成果物の削除はしない。
    """
    skill_dir = absolute_path(skill_dir)
    output_dir = absolute_path(output_dir if output_dir is not None else ROOT / "dev" / "dist")
    reject_links(output_dir)
    if output_dir == skill_dir or skill_dir in output_dir.parents:
        raise ValueError("Package output must be outside the source skill")
    paths = tuple(
        output_dir / name for name in (
            f"{SKILL_NAME}.skill", f"{SKILL_NAME}.zip",
            f"{SKILL_NAME}.skill.sha256", f"{SKILL_NAME}.zip.sha256",
        )
    )
    for path in paths:
        reject_links(path)
        if path.exists() and not path.is_file():
            raise ValueError(f"Package output is not a regular file: {path}")
    payload = collect_payload(skill_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".skill-package-", dir=output_dir) as temporary:
        candidate = Path(temporary) / paths[0].name
        write_archive(candidate, payload)
        verify_archive(candidate, payload)
        archive_bytes = candidate.read_bytes()
        digest = hashlib.sha256(archive_bytes).hexdigest()
        contents = (
            archive_bytes, archive_bytes,
            f"{digest}  {paths[0].name}\n".encode("ascii"),
            f"{digest}  {paths[1].name}\n".encode("ascii"),
        )
        staged = []
        for path, content in zip(paths, contents):
            target = Path(temporary) / path.name
            target.write_bytes(content)
            staged.append(target)
        verify_archive(staged[1], payload)
        for source, target in zip(staged, paths):
            reject_links(target)
            source.replace(target)
    return paths


def main(argv=None):
    """既定のスキルを梱包する。検証失敗時は終了値1と具体的な理由を返す。"""
    parser = argparse.ArgumentParser(description="walkthrough-review単体を.skillとZIPで配布する")
    parser.add_argument("--skill-dir", type=Path, default=SKILL_ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dev" / "dist")
    args = parser.parse_args(argv)
    try:
        paths = build_package(args.skill_dir, args.output_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
