"""カード単位の解説作成・分割読出し・確認記録・一括集約を行う。

LLMに全コメント配列を作り直させず、本文Markdownと任意のSVGを1件ずつ受け取る。
固定スナップショットと現在のレビューを照合し、コード変更を無言で追認しない。
共有ストアの短い更新・集約はOSのファイルロックで直列化し、書込みは個別に置換する。
外部パッケージや、サブエージェント機能自体への依存はない。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

import walkthrough as w


INPUT_KEYS = {"id", "file", "side", "start_line", "end_line", "title", "body", "code_hash", "diagrams"}


def checked(path):
    """絶対化した経路とその親のリンク・再解析ポイントを拒否する。"""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or part.exists() and w.is_link(part):
            raise ValueError(f"作業先にリンクは使えません: {part}")
    return path


def token(value):
    """カードIDや担当名を、安全なファイル名としても使える値に限定する。"""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("ID・担当名は英数字・ハイフン・下線の1〜64文字にしてください")
    if value.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("OSの予約名は使えません")
    return value


def atomic_text(path, text):
    """固有の一時ファイルを完成させて置換し、途中のJSONを見せない。"""
    path = checked(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".card-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@contextmanager
def store_lock(root):
    """プロセス終了時にも解除されるOSロック。取得待ちは最大10秒に限定する。"""
    path = checked(Path(root) / ".store.lock")
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        if os.name == "nt":
            import msvcrt
            def acquire():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + 10
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError("別の処理が更新中です。完了後に再試行してください") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            release()


def review_info(review):
    """本文・比較条件から固定IDを作る。概要・解説・生成日時の変更は含めない。"""
    if not isinstance(review, dict) or review.get("schema_version") != 2:
        raise ValueError("形式v2のreview.jsonが必要です")
    files, identity = [], []
    for file in review["files"]:
        w.update_code_hashes(file)
        counts = {side: 0 for side in ("old", "new")}
        for block in file["blocks"]:
            count = block["text"].count("\n") + (not block["text"].endswith("\n"))
            for side in counts:
                if block[side + "_start"] is not None:
                    counts[side] += count
        files.append({"path": file["path"], "code_hashes": file["code_hashes"], "lines": counts,
                      "diff_summary": file.get("diff_summary", [])})
        identity.append({"path": file["path"], "code_hashes": file["code_hashes"],
                         "old_path": file.get("old_path"), "diff_metadata": file.get("diff_metadata", {})})
    return digest({"project_root": review["project_root"], "mode": review["mode"], "files": identity}), files


def bind(root):
    """現在のコードが変わっていれば、旧ストアへの追記・出力を止める。"""
    root = checked(root)
    manifest = w.read_json(checked(root / "manifest.json"))
    if manifest.get("schema_version") != 1:
        raise ValueError("未対応のカードストア形式です")
    current = w.read_json(checked(manifest["review_json"]))
    current_id, _ = review_info(current)
    if current_id != manifest["snapshot_id"]:
        raise ValueError("コードまたは比較条件が変わりました。新しい保存先でinitし、変更した説明を再確認してください")
    context = {item["path"]: {
        "file": {"path": item["path"], "code_hashes": item["code_hashes"]},
        "line_numbers": {side: range(1, item["lines"][side] + 1) for side in ("old", "new")},
    } for item in manifest["files"]}
    return manifest, context


def snapshot(root, manifest):
    raw = checked(Path(root) / "snapshot.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["snapshot_sha256"]:
        raise ValueError("固定スナップショットが変更されています")
    return json.loads(raw)


def owner_folder(root, owner):
    """既存の担当フォルダと大小文字だけ違う名前で保存・取得・削除しない。"""
    folder = checked(Path(root) / "cards")
    for directory in folder.iterdir():
        if directory.name.lower() == owner.lower() and directory.name != owner:
            raise ValueError(f"担当名の大文字・小文字を既存の表記に合わせてください: {directory.name}")
    return checked(folder / owner)


def cards(root):
    """担当別のJSONを読み、手動変更を含むID重複・経路不整合も検出する。"""
    result, seen, owners = [], set(), set()
    folder = checked(Path(root) / "cards")
    for owner_dir in sorted(folder.iterdir()):
        checked(owner_dir)
        token(owner_dir.name)
        owner_key = owner_dir.name.lower()
        if owner_key in owners:
            raise ValueError(f"担当名の大文字・小文字だけが異なるフォルダが重複しています: {owner_dir.name}")
        owners.add(owner_key)
        if not owner_dir.is_dir():
            raise ValueError("cards直下には担当フォルダだけを置いてください")
        for path in sorted(owner_dir.iterdir()):
            checked(path)
            if path.suffix != ".json":
                continue
            card = w.read_json(path)
            ident = token(card.get("id")) if isinstance(card, dict) else token(None)
            if path.name != ident + ".json" or ident.lower() in seen:
                raise ValueError(f"カードIDの重複またはファイル名不整合: {ident}")
            seen.add(ident.lower())
            result.append((owner_dir.name, card))
    return result


def initialize(review_dir, root, from_store=None):
    root, review_path = checked(root), checked(Path(review_dir) / "review.json")
    review = w.read_json(review_path)
    ident, files = review_info(review)
    w.require_external_output(root, Path(review["project_root"]).resolve())
    if w.is_inside(root, review_path.parent):
        raise ValueError("カードストアはレビュー出力の外へ置いてください")
    if root.exists() and any(root.iterdir()):
        if not (root / "manifest.json").is_file():
            raise ValueError("空の保存先か、このツールの既存ストアを指定してください")
        with store_lock(root):
            manifest, _ = bind(root)
            if manifest["snapshot_id"] != ident or Path(manifest["review_json"]) != review_path:
                raise ValueError("別のレビュー用のストアです")
        return {"store": root.as_posix(), "resumed": True, "files": len(files)}
    overview = review.get("overview", "")
    if from_store is not None:
        previous_root = checked(from_store)
        with store_lock(previous_root):
            previous_manifest = w.read_json(checked(previous_root / "manifest.json"))
            snapshot(previous_root, previous_manifest)
            if previous_manifest["project_root"] != review["project_root"]:
                raise ValueError("別のプロジェクトのカードは引き継げません")
            previous = cards(previous_root)
            overview = checked(previous_root / "overview.md").read_text(encoding="utf-8")
    else:
        previous = [("existing", c) for file in review["files"] for c in file.get("comments", [])]
        previous.extend(("existing", c) for c in review.get("unattached_comments", []))
    # 入力エラーは保存先を作る前に検出する。作成時Hashは引継ぎ時にも書き換えない。
    prepared, seen = [], set()
    for owner, card in previous:
        owner, card_id = token(owner), token(card["id"])
        # Windowsへ移したストアでも同じカードが別名で上書きされないようにする。
        if card_id.lower() in seen:
            raise ValueError(f"引継ぎカードIDが重複しています: {card_id}")
        seen.add(card_id.lower())
        prepared.append((owner, {k: v for k, v in card.items() if k in INPUT_KEYS}))
    root.mkdir(parents=True, exist_ok=True)
    with store_lock(root):
        (root / "cards").mkdir()
        (root / "checks").mkdir()
        raw = (json.dumps(review, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        atomic_text(root / "snapshot.json", raw.decode("utf-8"))
        atomic_text(root / "overview.md", overview)
        for owner, card in prepared:
            save_json(root / "cards" / owner / (card["id"] + ".json"), card)
        save_json(root / "manifest.json", {"schema_version": 1, "snapshot_id": ident,
                  "snapshot_sha256": hashlib.sha256(raw).hexdigest(), "review_json": review_path.as_posix(),
                  "project_root": review["project_root"], "files": files})
    return {"store": root.as_posix(), "resumed": False, "files": len(files), "cards": len(prepared)}


def put_card(root, owner, card, update=False, confirm_code=False):
    root, owner = checked(root), token(owner)
    ident = token(card["id"])
    with store_lock(root):
        manifest, context = bind(root)
        all_cards = cards(root)
        folder = owner_folder(root, owner)
        existing = next(((who, c) for who, c in all_cards if c["id"].lower() == ident.lower()), None)
        if update:
            if existing is None or existing[0] != owner or existing[1]["id"] != ident:
                raise ValueError("修正するIDと担当が一致しません")
            value = {**existing[1], **card}
        else:
            if existing is not None:
                raise ValueError(f"カードIDが重複しています: {ident}")
            if not ident.startswith(owner + "-"):
                raise ValueError("新規IDは担当名とハイフンで始めてください（例: python-loading）")
            value = dict(card)
        if "code_hash" in card or set(card) - (INPUT_KEYS - {"code_hash"}):
            raise ValueError("code_hashは固定スナップショットから取得します。入力項目を確認してください")
        name, side = value.get("file"), value.get("side", "new")
        if name not in context or side not in ("old", "new"):
            raise ValueError("レビュー対象ファイルと版を指定してください")
        actual = context[name]["file"]["code_hashes"][side]
        original_hash = existing[1].get("code_hash") if update else None
        hash_matches = isinstance(original_hash, str) and original_hash.lower() == actual
        if update and not hash_matches and not (confirm_code and "body" in card):
            raise ValueError("作成時からコードが変わっています。再読後、本文と--confirm-codeを指定してください")
        # 同じHashの大小文字だけを直すために、作成時の記録を書き換えない。
        if not hash_matches:
            value["code_hash"] = actual
        _, checked_card, warnings = w.validate_comment(value, context)
        if warnings:
            raise ValueError(" / ".join(warnings))
        save_json(folder / (ident + ".json"), {k: v for k, v in checked_card.items() if k in INPUT_KEYS})
    return {"id": ident, "saved": True, "cards": len(all_cards) + (existing is None)}


def remove_card(root, owner, ident):
    root, owner, ident = checked(root), token(owner), token(ident)
    with store_lock(root):
        bind(root)
        path = checked(owner_folder(root, owner) / (ident + ".json"))
        if not path.is_file():
            raise ValueError("指定カードがありません")
        card = w.read_json(path)
        if not isinstance(card, dict) or card.get("id") != ident:
            raise ValueError("指定したIDと保存済みカードのIDが一致しません")
        destination = checked(root / "removed" / (owner + "-" + ident + "-" + uuid.uuid4().hex + ".json"))
        destination.parent.mkdir(exist_ok=True)
        os.replace(path, destination)
    return {"id": ident, "removed": True}


def set_overview(root, text):
    root = checked(root)
    with store_lock(root):
        _, context = bind(root)
        if not text.strip():
            raise ValueError("概要には空でない文章を指定してください")
        w.validate_links(text, context)
        atomic_text(root / "overview.md", text)
    return {"overview_saved": True}


def card_digest(items):
    return digest(sorted(items, key=lambda card: card["id"]))


def file_status(root, manifest, context, items):
    result = []
    for file in manifest["files"]:
        selected = [card for _, card in items if card.get("file") == file["path"]]
        record_path = checked(Path(root) / "checks" / (digest(file["path"]) + ".json"))
        record = w.read_json(record_path) if record_path.exists() else {}
        current = record.get("snapshot_id") == manifest["snapshot_id"] and record.get("cards_digest") == card_digest(selected)
        warnings = []
        for card in selected:
            try:
                _, _, messages = w.validate_comment(card, context)
                warnings.extend(messages)
            except (ValueError, KeyError, TypeError) as error:
                warnings.append(str(error))
        result.append({"file": file["path"], "lines": file["lines"], "cards": len(selected),
                       "reviewed": current and not warnings, "reviewer": record.get("owner") if current else None,
                       "no_card_reason": record.get("no_card_reason", "") if current else "", "warnings": warnings[:5], "warning_count": len(warnings)})
    return result


def mark_reviewed(root, name, owner, reason=""):
    root, owner, name = checked(root), token(owner), w.relative_path(name)
    with store_lock(root):
        manifest, context = bind(root)
        if name not in context:
            raise ValueError("レビュー対象外です")
        selected = [card for _, card in cards(root) if card.get("file") == name]
        for card in selected:
            _, _, warnings = w.validate_comment(card, context)
            if warnings:
                raise ValueError(" / ".join(warnings))
        nonempty = any(context[name]["line_numbers"][side] for side in ("old", "new"))
        if nonempty and not selected and not reason.strip():
            raise ValueError("非空ファイルにカードがない場合は、不要と判断した理由を指定してください")
        save_json(root / "checks" / (digest(name) + ".json"), {"file": name, "owner": owner,
                  "snapshot_id": manifest["snapshot_id"], "cards_digest": card_digest(selected), "no_card_reason": reason})
    return {"file": name, "reviewed": True, "cards": len(selected)}


def status(root, name=None, offset=0, limit=50):
    root = checked(root)
    with store_lock(root):
        manifest, context = bind(root)
        items = cards(root)
        files = file_status(root, manifest, context, items)
        if name is not None:
            name = w.relative_path(name)
            if name not in context:
                raise ValueError("レビュー対象外です")
            selected = [(who, card) for who, card in items if card.get("file") == name]
            values = [{"owner": who, **{k: c[k] for k in ("id", "side", "start_line", "end_line", "title") if k in c}} for who, c in selected]
        else:
            values = files
        return {"files": len(files), "cards": len(items), "reviewed_files": sum(f["reviewed"] for f in files),
                "unattached_cards": sum(c.get("file") not in context for _, c in items),
                "unattached": [{"owner": who, "id": c["id"], "file": c.get("file")} for who, c in items if c.get("file") not in context][:limit],
                "items": values[offset:offset + limit], "next_offset": offset + limit if offset + limit < len(values) else None}


def read_source(root, name, side="new", start=1, limit=120, offset=0, max_chars=12000):
    """長い1行も文字位置で継続できる、上限付きの旧新本文読出し。"""
    manifest, context = bind(root)
    name = w.relative_path(name)
    if name not in context or side not in ("old", "new"):
        raise ValueError("レビュー対象ファイルと版を指定してください")
    if start < 1 or offset < 0 or not 1 <= limit <= 200 or not 1 <= max_chars <= 20000:
        raise ValueError("読出し範囲・上限が不正です")
    total = len(context[name]["line_numbers"][side])
    if start > total + 1 or start == total + 1 and offset:
        raise ValueError("開始位置が本文を超えています")
    review = snapshot(root, manifest)
    file = next(f for f in review["files"] if f["path"] == name)
    result, remaining, following = [], max_chars, None
    for row in w.iter_rows(file):
        number = row[side]
        if number is None or number < start:
            continue
        text = row.get("old_text", row["text"]) if side == "old" else row["text"]
        begin = offset if number == start else 0
        if begin > len(text):
            raise ValueError("文字位置が行末を超えています")
        fragment = text[begin:begin + remaining]
        end = begin + len(fragment)
        complete = end == len(text)
        result.append({"line": number, "offset": begin, "text": fragment, "line_complete": complete, "newline": row["newline"]})
        remaining -= len(fragment)
        following = None if complete and number == total else ({"start": number + 1, "offset": 0} if complete else {"start": number, "offset": end})
        if not complete or not remaining or len(result) >= limit:
            break
    return {"file": name, "side": side, "code_hash": context[name]["file"]["code_hashes"][side],
            "total_lines": total, "lines": result, "next": following, "diff_summary": file.get("diff_summary", [])}


def show_card(root, owner, ident):
    _, context = bind(root)
    owner, ident = token(owner), token(ident)
    card = w.read_json(checked(owner_folder(root, owner) / (ident + ".json")))
    if not isinstance(card, dict) or card.get("id") != ident:
        raise ValueError("指定したIDと保存済みカードのIDが一致しません")
    _, checked_card, warnings = w.validate_comment(card, context, preserve=True)
    return {"owner": owner, "card": checked_card, "warnings": warnings}


def export_comments(root, output):
    root, output = checked(root), checked(output)
    with store_lock(root):
        manifest, context = bind(root)
        w.require_external_output(output, Path(manifest["project_root"]).resolve())
        if w.is_inside(output, root) or output == Path(manifest["review_json"]):
            raise ValueError("集約先はストア・review.jsonの外へ置いてください")
        items = cards(root)
        statuses = file_status(root, manifest, context, items)
        pending = [f["file"] for f in statuses if not f["reviewed"]]
        if pending:
            raise ValueError("未確認または修正後の再確認が必要です: " + ", ".join(pending[:10]))
        order = {file["path"]: i for i, file in enumerate(manifest["files"])}
        payload = {"overview": checked(root / "overview.md").read_text(encoding="utf-8"),
                   "comments": sorted([c for _, c in items], key=lambda c: (order.get(c.get("file"), len(order)), c.get("side", "new"), c.get("start_line", 0), c["id"]))}
        if not payload["overview"].strip():
            raise ValueError("概要を設定してください")
        # 最後は既存mergeと同じ全体検証を通す。各追記ではHTML一式を生成しない。
        review = snapshot(root, manifest)
        w.merge_comments(review, payload)
        if review["hash_warnings"] or review["unattached_comments"]:
            raise ValueError("解説の照合警告を解決してください")
        save_json(output, payload)
    return {"output": output.as_posix(), "cards": len(items), "reviewed_files": len(statuses)}


def read_text(path):
    return checked(path).read_text(encoding="utf-8-sig")


def main(argv=None):
    parser = argparse.ArgumentParser(description="カード単位の解説作成と、固定コードの分割読出し")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init"); init.add_argument("review", type=Path); init.add_argument("--output", required=True, type=Path); init.add_argument("--from-store", type=Path)
    for command in ("add", "update", "remove", "show", "overview", "reviewed", "status", "read", "export"):
        sub = commands.add_parser(command)
        sub.add_argument("store", type=Path)
        if command in ("add", "update", "remove", "show", "reviewed"):
            sub.add_argument("--owner", required=True)
        if command in ("add", "update", "remove", "show"):
            sub.add_argument("--id", required=True)
        if command in ("add", "update", "read", "reviewed", "status"):
            sub.add_argument("--file", required=command not in ("update", "status"))
        if command in ("add", "update"):
            sub.add_argument("--side", choices=("old", "new"))
            sub.add_argument("--start", type=int); sub.add_argument("--end", type=int)
            sub.add_argument("--title"); sub.add_argument("--body-file", type=Path, required=command == "add")
            sub.add_argument("--svg-file", type=Path); sub.add_argument("--diagram-title")
            sub.add_argument("--diagram-description-file", type=Path)
            sub.add_argument("--clear-diagrams", action="store_true")
            sub.add_argument("--confirm-code", action="store_true")
        if command == "overview":
            sub.add_argument("--body-file", required=True, type=Path)
        if command == "reviewed":
            sub.add_argument("--reason", default="")
        if command == "read":
            sub.add_argument("--side", choices=("old", "new"), default="new")
            sub.add_argument("--start", type=int, default=1); sub.add_argument("--limit", type=int, default=120)
            sub.add_argument("--offset", type=int, default=0); sub.add_argument("--max-chars", type=int, default=12000)
        if command == "status":
            sub.add_argument("--offset", type=int, default=0); sub.add_argument("--limit", type=int, default=50)
        if command == "export":
            sub.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init": result = initialize(args.review, args.output, args.from_store)
        elif args.command in ("add", "update"):
            value = {"id": args.id}
            for key, field in (("file", "file"), ("side", "side"), ("start", "start_line"), ("end", "end_line"), ("title", "title")):
                if getattr(args, key) is not None: value[field] = getattr(args, key)
            if args.body_file is not None: value["body"] = read_text(args.body_file)
            if args.clear_diagrams: value["diagrams"] = []
            if args.svg_file:
                if args.clear_diagrams or not args.diagram_title or not args.diagram_description_file:
                    raise ValueError("SVGにはタイトル・説明ファイルを指定し、図の削除と併用しないでください")
                value["diagrams"] = [{"title": args.diagram_title, "description": read_text(args.diagram_description_file), "svg": read_text(args.svg_file)}]
            elif args.diagram_title or args.diagram_description_file:
                raise ValueError("図のメタ情報には--svg-fileが必要です")
            result = put_card(args.store, args.owner, value, args.command == "update", args.confirm_code)
        elif args.command == "remove": result = remove_card(args.store, args.owner, args.id)
        elif args.command == "show": result = show_card(args.store, args.owner, args.id)
        elif args.command == "overview": result = set_overview(args.store, read_text(args.body_file))
        elif args.command == "reviewed": result = mark_reviewed(args.store, args.file, args.owner, args.reason)
        elif args.command == "status":
            if args.offset < 0 or not 1 <= args.limit <= 100: raise ValueError("表示位置・件数が不正です")
            result = status(args.store, args.file, args.offset, args.limit)
        elif args.command == "read": result = read_source(args.store, args.file, args.side, args.start, args.limit, args.offset, args.max_chars)
        else: result = export_comments(args.store, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
