"""unified diff を検査し、変更後の全文と削除行を組み合わせるための処理。

このモジュールはファイルを読み書きせず、文字列と辞書だけを扱う。
呼び出し元は「変更後のプロジェクト」から読み取った本文を渡す。
パッチにない本文はその現在の本文から補い、パッチの追加行・前後行は
現在の本文と照合する。旧ファイルそのものは入力されないため、削除行は
パッチに記録された内容を復元するものであり、旧実ファイルとの照合ではない。

公開する処理は次の二つ。
・parse_patch: パッチの形式、パス、行数、旧新の座標を検査して分解する。
・build_rows: 分解済みの各ファイルを現在の全文と照合し、表示行を作る。

主な辞書の形:
・entry: old_path / new_path / hunks / metadata。
  追加ファイルの old_path、削除ファイルの new_path は None。
・hunk: old_start / old_count / new_start / new_count / lines。
  lines の各要素は kind / text / newline を持つ。
・表示行: old / new / kind / text / newline と、必要時だけ old_text。
  old と new は 1 始まり。存在しない側の行番号は None。
  text には改行文字を含めず、末尾の LF の有無は newline に保持する。

BOM は照合が終わるまで保持し、最後に旧新それぞれの先頭から一つだけ除く。
一つの前後行が旧版では先頭、新版では途中にある場合などは正規化結果が
異なるため、旧版復元用の old_text を追加する。これを旧版のハッシュにも使う。
不正・未対応のパッチを読み飛ばして続行せず、ValueError で理由を伝える。
"""

from __future__ import annotations

import re


# hunk は「ひとかたまりの変更範囲」を指す。
# 例: @@ -2,3 +2,4 @@ は、旧版 2 行目から 3 行、新版 2 行目から 4 行。
# ,行数 の省略は 1 行を表す。最後の @@ より後ろの関数名などは表示本文ではない。
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")
# これはソースの一行ではなく、直前のソース行に末尾改行がないことを示す。
_NO_NEWLINE = "\\ No newline at end of file"


def _quoted_path(value: str) -> tuple[str, str]:
    """二重引用符で始まる Git パスを復号し、(パス, 引用符の後ろ) を返す。

    入力 value は先頭が '"' であることを呼び出し側が確認済みとする。
    Git の C 形式エスケープをバイト列に戻してから UTF-8 として読む。
    例えば日本語を表す複数の八進数エスケープも、一文字ずつではなく
    全バイトをそろえて復号するため、途中のバイトだけを誤解釈しない。

    入力文字列の変更、ファイルアクセスなどの副作用はない。
    引用符の未終了、未対応のエスケープ、255 を超える八進数、
    復号後の不正な UTF-8 は ValueError。パスの安全性はここでは判定せず、
    呼び出し側が後で _safe_path に渡す。
    """
    # 通常の Unicode 文字と八進数指定を、同じ UTF-8 バイト列にそろえる。
    data = bytearray()
    # 先頭の開始引用符は読み終えたものとして、次の文字から走査する。
    index = 1
    # C 形式で意味が定義されている一文字エスケープだけを許可する。
    escapes = {
        "a": 7, "b": 8, "t": 9, "n": 10, "v": 11,
        "f": 12, "r": 13, "\\": 92, '"': 34,
    }
    while index < len(value):
        # index は「次に読む文字」を指す。取得した直後に進めておく。
        char = value[index]
        index += 1
        if char == '"':
            # 終了引用符に到達した。後続の新パスやタイムスタンプを勝手に
            # 消費せず、そのまま返し、呼び出し側に解釈・検証させる。
            try:
                return data.decode("utf-8"), value[index:]
            except UnicodeDecodeError as error:
                raise ValueError("パッチの引用パスが UTF-8 ではありません") from error
        if char != "\\":
            # 引用されていても、エスケープされていない日本語などは許可する。
            data.extend(char.encode("utf-8"))
            continue
        if index >= len(value):
            # 最後がバックスラッシュの場合は、後続文字も終了引用符もない。
            # ループ後の「引用符が閉じていない」エラーへ進む。
            break
        char = value[index]
        index += 1
        if char in escapes:
            # "\\t" などを対応する一バイトに戻す。
            data.append(escapes[char])
        elif char in "01234567":
            # 一つの八進数指定は最大 3 桁。次の通常文字まで取り込まない。
            digits = char
            while index < len(value) and len(digits) < 3 and value[index] in "01234567":
                digits += value[index]
                index += 1
            number = int(digits, 8)
            if number > 255:
                # bytearray の値域を超える入力を、明示的な形式エラーにする。
                raise ValueError("パッチのパスに不正な八進数エスケープがあります")
            data.append(number)
        else:
            # 不明なエスケープをそのまま文字にすると別のパスを指す危険がある。
            raise ValueError("パッチのパスに未対応のエスケープがあります")
    raise ValueError("パッチのパスの引用符が閉じていません")


def _path_text(value: str, timestamp: bool = False) -> str:
    """ヘッダのパス文字列を取り出し、引用があれば復号して返す。

    timestamp=True は --- / +++ ヘッダ用で、タブ以降を日時欄として除く。
    通常の空白はファイル名に使えるので、空白では分割しない。
    False は rename 等のメタデータ用で、文字列全体を一つのパスとみなす。
    入力は変更しない。引用後に許可していない文字が続く場合、または引用の
    復号に失敗した場合は ValueError。返り値のパス安全性は別途検査する。
    """
    if value.startswith('"'):
        result, tail = _quoted_path(value)
        # 引用符の後ろは空、または明示的に許可したタブ始まりの日時欄だけ。
        if tail and not (timestamp and tail.startswith("\t")):
            raise ValueError("パッチの引用パスの後ろに不正な文字があります")
        return result
    # 非引用のパスでも、日時欄を除く規則は引用の場合と同じにする。
    return value.split("\t", 1)[0] if timestamp else value


def _safe_path(path: str) -> str:
    """プロジェクト相対の / 区切りパスとして許可できる形かを検査する。

    入力が条件を満たせば文字列をそのまま返す。補正や正規化はしない。
    空文字、絶対パス、Windows 用の区切り・ドライブ等、制御文字、
    空の要素・"."・".." を含む場合は ValueError。
    ファイルの存在やリンク先は調べないため、実ファイルへのアクセス時の
    プロジェクト外判定・リンク判定は呼び出し元の責務となる。
    ファイルアクセスや入力変更の副作用はない。
    """
    # Windows のドライブ名・代替データストリーム・区切り文字も拒否する。
    # パスを正規化してから検査すると ../ を見落とすため、そのまま検査する。
    if (
        # 空パスや先頭 / は、特定のプロジェクト内ファイルを表していない。
        not path
        or path.startswith("/")
        # \ は Windows の区切り、: はドライブ名や代替データストリームになる。
        or "\\" in path
        or ":" in path
        # NUL・改行・タブ・DEL 等を経由したパスの誤解釈を防ぐ。
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        # 二重 /、末尾 /、現在ディレクトリ、親ディレクトリへの移動を拒否する。
        or any(part in ("", ".", "..") for part in path.split("/"))
    ):
        raise ValueError(f"安全でないパッチのパスです: {path!r}")
    return path


def _git_paths(value: str) -> tuple[str, str, bool]:
    """diff --git の後ろを読み、(旧パス, 新パス, a/b 接頭辞の有無) を返す。

    value には "diff --git " 自体を含めない。引用パスと、Git が引用せず
    出力する空白入りパスに対応する。旧 a/・新 b/ の組を検出したときだけ
    それぞれを外し、三つ目の返り値を True にする。
    この真偽値を --- / +++ にも引き継ぎ、--no-prefix の実ディレクトリ名
    "a" や "b" を、ヘッダ側だけで誤って切り落とさない。

    入力変更・ファイルアクセスはない。二つのパスの境界が不正・曖昧、
    引用が不正、またはパスが安全でない場合は ValueError。
    """
    if value.startswith('"'):
        # 旧パスが引用されていれば、その終了位置で境界を確定できる。
        old, tail = _quoted_path(value)
        if not tail.startswith(" "):
            raise ValueError("diff --git のパスが二つありません")
        new = _path_text(tail[1:])
    elif ' "' in value:
        # 旧パスは非引用、新パスだけが引用されている形を扱う。
        old, new_text = value.split(' "', 1)
        new = _path_text('"' + new_text)
    elif value.startswith("a/") and " b/" in value:
        # Git は空白だけを含む名前を引用しない。同名の変更なら一意に決められる。
        candidates = [
            (value[:match.start()], value[match.start() + 1:])
            for match in re.finditer(r" b/", value)
        ]
        # 名前自身に " b/" が含まれる可能性もあるため、全候補を確認する。
        # a/b を除いた名前が同じになる候補が一つなら、通常の同名変更と判断。
        same = [(old, new) for old, new in candidates if old[2:] == new[2:]]
        if len(same) == 1:
            old, new = same[0]
        elif len(candidates) == 1:
            # 名前が異なる rename 等でも、境界候補が一つなら確定できる。
            old, new = candidates[0]
        else:
            # 複数の解釈が残るときは、任意の候補を選んで別ファイルを読まない。
            raise ValueError("diff --git の空白入りパスを一意に解釈できません")
    else:
        # a/b の組や引用で区別できない形は、空白なしの二つのパスに限定する。
        parts = value.split(" ")
        if len(parts) != 2:
            raise ValueError("diff --git のパスを解釈できません")
        old, new = parts
    prefixed = old.startswith("a/") and new.startswith("b/")
    if prefixed:
        old, new = old[2:], new[2:]
    return _safe_path(old), _safe_path(new), prefixed


def _header_paths(old: str, new: str, prefixed: bool | None = None) -> tuple[str | None, str | None]:
    """--- / +++ の値を (旧相対パス, 新相対パス) に変換する。

    old と new にはヘッダの先頭記号を含めない。引用とタブ区切りの日時を
    処理し、必要な場合だけ a/・b/ を除く。Git ヘッダから渡された prefixed
    が True/False ならその判断に従い、None の通常 diff だけ組から推定する。
    /dev/null は実際に読むパスではなく、追加・削除で「その版がない」ことを
    表す None に変換する。両方 None の矛盾は _finish_entry が検査する。
    副作用はなく、パスの復号や安全性の検証に失敗した場合は ValueError。
    """
    old = _path_text(old, timestamp=True)
    new = _path_text(new, timestamp=True)
    # 通常の unified diff にも a/・b/ の組を使える。
    if prefixed is None:
        prefixed = (
            (old.startswith("a/") or old == "/dev/null")
            and (new.startswith("b/") or new == "/dev/null")
        )
    if prefixed:
        # /dev/null 自体は変更せず、a/ または b/ が付いている側だけを外す。
        old = old[2:] if old.startswith("a/") else old
        new = new[2:] if new.startswith("b/") else new
    return (
        # /dev/null を先に特別扱いし、それ以外の絶対パスは _safe_path で拒否。
        None if old == "/dev/null" else _safe_path(old),
        None if new == "/dev/null" else _safe_path(new),
    )


def _read_hunk(lines: list[str], index: int) -> tuple[dict, int]:
    """index の hunk 一つを読み、(hunk 辞書, 次に読む位置) を返す。

    lines は LF で分割済みのパッチ、index はそのヘッダを指す 0 始まりの位置。
    返す位置は読み終えた直後で、次の hunk やファイルヘッダは消費しない。
    本文の "+" は新側だけ、"-" は旧側だけ、空白は両側の一行として数える。
    末尾改行なしマーカーは本文行数に含めず、直前の行の newline を False にする。

    入力 lines は変更せず、新しい辞書と内部リストだけを組み立てる。
    不正なヘッダ、本文行数の過不足、未知の本文記号、マーカーの誤配置は
    ValueError。ファイル全体に対する座標や末尾位置の妥当性は後段でも検査する。
    """
    match = _HUNK.fullmatch(lines[index])
    if not match:
        raise ValueError(f"不正な hunk ヘッダです（パッチ {index + 1} 行目）")
    old_start, old_count, new_start, new_count = match.groups()
    # ,行数 を省略した形式は 1 行を表す。0 行とは区別して記録する。
    hunk = {
        "old_start": int(old_start),
        "old_count": int(old_count) if old_count is not None else 1,
        "new_start": int(new_start),
        "new_count": int(new_count) if new_count is not None else 1,
        "lines": [],
    }
    for side in ("old", "new"):
        # 行数が正なら 1 始まりの実在行を指す。0 行の挿入位置だけは 0 を許す。
        if hunk[side + "_count"] and not hunk[side + "_start"]:
            raise ValueError("行数のある hunk の開始行は 1 以上にしてください")
    if not hunk["old_count"] and not hunk["new_count"]:
        # どちらも 0 では変更も文脈もなく、意味のない hunk となる。
        raise ValueError("旧行・新行ともに空の hunk は不正です")
    index += 1
    old_seen = new_seen = 0
    while index < len(lines):
        line = lines[index]
        if line == _NO_NEWLINE:
            # 行数を読み終えた直後にもマーカーが置けるため、終了判定より先に
            # 処理する。直前の本文行がない場合や、二重指定は受け付けない。
            if not hunk["lines"] or not hunk["lines"][-1]["newline"]:
                raise ValueError("末尾改行マーカーの位置が不正です")
            hunk["lines"][-1]["newline"] = False
            index += 1
            continue
        if old_seen == hunk["old_count"] and new_seen == hunk["new_count"]:
            # ヘッダが宣言した双方の行数に達した。次の行は外側の解析へ戻す。
            break
        if not line or line[0] not in " +-":
            # 空のソース行も、パッチでは "+" などの記号が必要。
            # 本当に空のパッチ行をソースの空行として補ってはいけない。
            raise ValueError(f"hunk の本文・行数が不正です（パッチ {index + 1} 行目）")
        prefix = line[0]
        # bool は加算時に True=1 / False=0 として扱われる。
        # "+" は旧行を増やさず、"-" は新行を増やさず、空白は双方を増やす。
        old_seen += prefix != "+"
        new_seen += prefix != "-"
        if old_seen > hunk["old_count"] or new_seen > hunk["new_count"]:
            raise ValueError("hunk の本文がヘッダの行数を超えています")
        hunk["lines"].append({
            # 先頭のパッチ記号一文字だけを除く。ソース自身の空白等は保持する。
            "kind": {" ": "context", "+": "added", "-": "removed"}[prefix],
            "text": line[1:],
            "newline": True,
        })
        index += 1
    if old_seen != hunk["old_count"] or new_seen != hunk["new_count"]:
        # パッチの終端が早く来た場合も、欠けた本文を推測せず拒否する。
        raise ValueError("hunk の本文がヘッダの行数に足りません")
    return hunk, index


def _check_coordinates(hunks: list[dict]) -> None:
    """各 hunk の旧新の位置が、同じ未変更部分でつながるか検査する。

    入力は _read_hunk の結果をファイル内の順番で並べたリスト。
    正常時の返り値は None。入力の辞書・リストは変更しない。
    重複・逆順、または hunk 間の旧新の未変更行数が違えば ValueError。

    行数が正の開始行 N は、0 始まりの配列位置 N-1 に変換する。
    行数が 0 の開始値 N は「N 行の後ろの挿入位置」なので、そのまま N。
    例: -0,0 は旧先頭への挿入、-3,0 は旧 3 行目の後ろへの挿入を表す。
    old_end / new_end は直前 hunk の終端直後を表す 0 始まりの境界位置。
    """
    # 最初の hunk より前も未変更部分として検査するため、先頭境界 0 から始める。
    old_end = new_end = 0
    for hunk in hunks:
        old_index = hunk["old_start"] - (1 if hunk["old_count"] else 0)
        new_index = hunk["new_start"] - (1 if hunk["new_count"] else 0)
        if old_index < old_end or new_index < new_end:
            # 二つの版のどちらかでも前の範囲へ戻る場合は、同じ行を二度扱う。
            raise ValueError("hunk の範囲が重複、または逆順になっています")
        if old_index - old_end != new_index - new_end:
            # hunk に含まれない部分は変更なしのため、旧新で同じ行数になるはず。
            # 直前 hunk による増減は、すでに old_end / new_end の差へ反映済み。
            raise ValueError("hunk 間の旧行番号と新行番号が整合しません")
        old_end = old_index + hunk["old_count"]
        new_end = new_index + hunk["new_count"]


def _finish_entry(state: dict) -> dict:
    """一つのファイルの解析状態を照合し、公開用 entry 辞書を返す。

    state は parse_patch が作った途中状態で、hunks / metadata と、
    任意の git_paths / headers を含む。ヘッダ同士、追加・削除、rename/copy、
    モード変更、hunk 座標の矛盾を検査し、矛盾があれば ValueError。
    hunk がなくても空ファイルの追加・削除や rename、モード変更は成立する。

    state 自体は変更しない。ただし返す entry の hunks と metadata は
    state 内のオブジェクトをそのまま参照するため、深いコピーではない。
    ファイルへのアクセスは行わず、現在本文との一致は build_rows が担当する。
    """
    metadata = state["metadata"]
    git_paths = state.get("git_paths")
    headers = state.get("headers")
    if headers:
        # 本文用ヘッダを優先するが、Git ヘッダの名前との食い違いは許さない。
        # None は /dev/null に由来するので、通常の名前との比較から外す。
        old_path, new_path = headers
        if git_paths:
            if old_path is not None and old_path != git_paths[0]:
                raise ValueError("diff --git と --- のパスが一致しません")
            if new_path is not None and new_path != git_paths[1]:
                raise ValueError("diff --git と +++ のパスが一致しません")
    elif git_paths:
        # 空ファイルや rename / mode-only には --- / +++ がない場合がある。
        old_path, new_path = git_paths[:2]
    else:
        raise ValueError("パッチにファイルヘッダがありません")
    if "new file mode" in metadata:
        # Git メタデータだけで空ファイルの追加も判定できる。
        # 本文用ヘッダもあるなら、旧ファイルがないという記述と一致させる。
        if headers and old_path is not None:
            raise ValueError("追加ファイルの旧パスは /dev/null にしてください")
        old_path = None
    if "deleted file mode" in metadata:
        # 削除も追加と対称に検査し、新パスを None として明示する。
        if headers and new_path is not None:
            raise ValueError("削除ファイルの新パスは /dev/null にしてください")
        new_path = None
    if old_path is None and new_path is None:
        # 旧新ともに存在しない entry は、表示・照合の対象を特定できない。
        raise ValueError("旧パス・新パスを両方 /dev/null にはできません")
    for operation in ("rename", "copy"):
        # 移動元だけ、移動先だけという不完全な指定は認めない。
        # 両方ある場合は、他のヘッダで確定したパスと同じであることを確認する。
        source = metadata.get(operation + " from")
        destination = metadata.get(operation + " to")
        if (source is None) != (destination is None):
            raise ValueError(f"{operation} の from と to は両方必要です")
        if source is not None and (source != old_path or destination != new_path):
            raise ValueError(f"{operation} のパスとファイルヘッダが一致しません")
    if "rename from" in metadata and "copy from" in metadata:
        # 一つの entry を「元を移す」と「元を残して複製する」の両方にしない。
        raise ValueError("rename と copy を同時には指定できません")
    if ("old mode" in metadata) != ("new mode" in metadata):
        # モード変更は前後の二つが必要。片側だけの情報では変更を確定できない。
        raise ValueError("old mode と new mode は両方必要です")
    meaningful_metadata = any(key in metadata for key in (
        "new file mode", "deleted file mode", "rename from", "copy from", "old mode",
    ))
    # index や similarity だけでは、実際に何が変わったのかを確定できない。
    if not state["hunks"] and not meaningful_metadata:
        raise ValueError("パッチに hunk またはファイル変更情報がありません")
    _check_coordinates(state["hunks"])
    return {
        "old_path": old_path,
        "new_path": new_path,
        "hunks": state["hunks"],
        "metadata": metadata,
    }


def parse_patch(text: str) -> list[dict]:
    """パッチ全文を読み、ファイルごとの entry 辞書を順番に返す公開関数。

    text は UTF-8 から文字列へ復号済みの、Git または通常 unified diff。
    空のパッチは空リストになる。戻り値の形式はモジュール冒頭に記載。
    パッチの BOM がある場合は、呼び出し元の読込時に取り除いておく。

    入力文字列は変更せず、ファイルの存在確認・読み書きは行わない。
    形式・行数・パス・座標の不正、同じ変更対象の重複、binary/combined 等の
    未対応形式は ValueError。未知の行を無視して部分的なレビューを作らない。
    現在のコード本文との照合は、返した全 entry を build_rows に渡して行う。
    """
    # CRLF で保存されたパッチも利用可能にする。splitlines は本文中の
    # 垂直タブ等まで分割するため使わない。
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        # split が最後の LF の後ろに作った要素だけを除く。本文の空行は
        # hunk 内で先頭記号を持つため、この処理で削除されない。
        lines.pop()
    entries = []
    # state は現在解析中の一ファイル。まだヘッダがなければ None。
    state = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            line.startswith(("diff --cc ", "diff --combined ", "@@@", "Binary files "))
            or line == "GIT binary patch"
        ):
            # 通常 unified diff と異なる構造を、その形式のまま明確に拒否する。
            raise ValueError(f"未対応の binary / combined diff です（パッチ {index + 1} 行目）")
        if line.startswith("diff --git "):
            if state is not None:
                # 新ファイルの開始前に、前のファイルを必ず完全に検査する。
                entries.append(_finish_entry(state))
            state = {
                "git_paths": _git_paths(line[len("diff --git "):]),
                "hunks": [], "metadata": {},
            }
            # この分岐は一行を消費した。次のループではメタデータ等を読む。
            index += 1
            continue
        if line.startswith("--- "):
            if state is not None and "headers" in state:
                # 通常 unified diff では diff --git を挟まず次の --- が現れる。
                entries.append(_finish_entry(state))
                state = None
            if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
                # 旧新の組が欠けたまま hunk を別ファイルへ結び付けない。
                raise ValueError("--- の直後に +++ ファイルヘッダが必要です")
            if state is None:
                # Git ヘッダなしの通常 unified diff 用の状態をここで作る。
                state = {"hunks": [], "metadata": {}}
            state["headers"] = _header_paths(
                line[4:], lines[index + 1][4:],
                state["git_paths"][2] if "git_paths" in state else None,
            )
            # --- と +++ の二行をまとめて消費する。
            index += 2
            continue
        if line.startswith("@@"):
            if state is None or "headers" not in state:
                raise ValueError("hunk の前に --- と +++ ファイルヘッダが必要です")
            hunk, index = _read_hunk(lines, index)
            # _read_hunk が次の未読位置を返すため、ここで index を増やさない。
            state["hunks"].append(hunk)
            continue
        if not line:
            # hunk 外の区切り用空行は許可する。hunk 本文中の空行は
            # _read_hunk が記号付きかどうかを別に厳密検査する。
            index += 1
            continue
        if state is not None and "git_paths" in state and "headers" not in state:
            # Git メタデータは diff --git と本文用ヘッダの間だけに許可する。
            # hunk の後ろの不明な記述をメタデータ扱いして飲み込まない。
            key = value = None
            for candidate in (
                "new file mode", "deleted file mode", "old mode", "new mode",
                "rename from", "rename to", "copy from", "copy to",
                "similarity index", "dissimilarity index", "index",
            ):
                # キーの直後に空白がある場合だけ一致させ、似たキーを誤認しない。
                if line.startswith(candidate + " "):
                    key, value = candidate, line[len(candidate) + 1:]
                    break
            if key is not None:
                if key in state["metadata"]:
                    # 同じキーの後勝ち・先勝ちを避け、矛盾した入力を隠さない。
                    raise ValueError(f"パッチの {key} が重複しています")
                if key.endswith((" from", " to")):
                    # rename/copy の名前にも、引用復号と相対パス検査を適用する。
                    value = _safe_path(_path_text(value))
                elif key.endswith("mode"):
                    # Git のモード表記は 6 桁の八進数。ここでは権限を実際に変更しない。
                    if not re.fullmatch(r"[0-7]{6}", value):
                        raise ValueError("パッチのファイルモードが不正です")
                elif key in ("similarity index", "dissimilarity index"):
                    # 類似度の許容範囲は 0〜100%。表示本文の照合とは別の形式検査。
                    if not re.fullmatch(r"(?:100|[0-9]{1,2})%", value):
                        raise ValueError("パッチの類似度が不正です")
                elif key == "index":
                    # Git オブジェクトIDの旧..新と、任意のモード欄を検査する。
                    # このIDをコードハッシュとみなしたり、Gitへ問い合わせたりしない。
                    if not re.fullmatch(r"[0-9a-fA-F]+\.\.[0-9a-fA-F]+(?: [0-7]{6})?", value):
                        raise ValueError("パッチの index 行が不正です")
                state["metadata"][key] = value
                index += 1
                continue
        # どの既知形式にも当てはまらない行は、位置と先頭部分を添えて拒否する。
        raise ValueError(f"未対応または不正なパッチ行です（{index + 1} 行目）: {line[:100]!r}")
    if state is not None:
        # 最後のファイルには次ヘッダが来ないので、終端で確定する。
        entries.append(_finish_entry(state))
    seen_old, seen_new, seen_display = set(), set(), set()
    for entry in entries:
        old, new = entry["old_path"], entry["new_path"]
        display = new or old
        # 削除ファイルは旧名、それ以外は新名で表示されるため、
        # 新旧それぞれの重複だけでなく、表示キーの衝突も検査する。
        is_copy = "copy from" in entry["metadata"]
        if (
            (not is_copy and old is not None and old in seen_old)
            or (new is not None and new in seen_new)
            or display in seen_display
        ):
            raise ValueError(f"パッチの同一ファイルが重複しています: {display}")
        # 一つの旧ファイルから複数コピーを作る Git パッチは正常。
        # copy 以外で同じ旧ファイルを二度変更する場合だけ拒否する。
        if not is_copy:
            seen_old.add(old)
        seen_new.add(new)
        seen_display.add(display)
    return entries


def _text_lines(text: str) -> list[tuple[str, bool]]:
    """現在本文を、(改行を除いた一行, 末尾改行の有無) のリストへ分ける。

    入力は BOM を保持した本文。ここでは BOM、空白、タブを取り除かない。
    CRLF は LF にそろえるが、単独 CR 等の取扱いは読込側の正規化に従う。
    空文字は 0 行、"\\n" は改行付きの空行 1 行、"x\\n\\n" は 2 行になる。
    入力変更・ファイルアクセスはなく、有効な文字列に対する独自例外はない。
    """
    if not text:
        return []
    parts = text.replace("\r\n", "\n").split("\n")
    # split の末尾の空要素は「余分な一行」ではなく、最後の行の改行を示す。
    final_newline = parts[-1] == ""
    if final_newline:
        parts.pop()
    return [
        # 最後以外の行には必ず LF がある。最後だけ、元本文の終端状態に従う。
        (part, index < len(parts) - 1 or final_newline)
        for index, part in enumerate(parts)
    ]


def _context_row(old_number: int, new_number: int, content: tuple[str, bool]) -> dict:
    """パッチ外の未変更行を表す、新しい表示行辞書を返す。

    old_number / new_number は 1 始まりの行番号、content は _text_lines の一要素。
    旧新ともに行番号を持つ context 行を作る。BOM は build_rows 最後の
    版別正規化まで残すため、ここでは内容をそのままコピーする。
    入力変更・外部への副作用はなく、契約どおりの入力に対する独自例外はない。
    """
    text, newline = content
    return {
        "old": old_number, "new": new_number, "kind": "context",
        "text": text, "newline": newline,
    }


def build_rows(entry: dict, current_text: str | None) -> list[dict]:
    """一ファイルのパッチと現在全文を照合し、旧新行番号付きの全表示行を返す。

    entry は parse_patch が返した一つの辞書。current_text は変更後の UTF-8
    本文で、先頭 BOM は保持して渡す。削除済みファイルだけ None を渡す。
    追加行は old=None、削除行は new=None、未変更行は双方の行番号を持つ。
    text に改行は含めず、newline に末尾 LF の有無を保持する。
    旧新の BOM 正規化結果が異なる文脈行には、旧版復元用 old_text も付ける。

    パッチの context/added 行は本文だけでなく末尾改行も現在本文と比較する。
    removed 行の内容は旧実ファイルを取得せず、パッチを根拠として復元する。
    パッチの前・途中・後ろにある未変更部分は、現在全文からそのまま補う。
    削除ファイルは新しい本文を空として検査するため、一部の行だけを削除する
    パッチから、旧ファイル全体を復元したことにしてしまわない。

    entry や入力文字列は変更せず、返すリストと辞書だけを新たに作る。
    ファイル操作は行わない。削除/存在の指定、行数、座標、現在本文の内容・
    末尾改行が一致しない場合は ValueError。現在ファイルの実際の存在確認と
    文字コードの復号は、文字列を渡す呼び出し元が担当する。
    """
    path = entry["new_path"] or entry["old_path"]
    if entry["new_path"] is None:
        if current_text is not None:
            # 空文字であっても「存在する空ファイル」と「削除済み」は区別する。
            raise ValueError(f"削除対象のファイルが現在も存在します: {path}")
        current_text = ""
    elif current_text is None:
        # 変更後の版が必要な追加・変更・rename 等では、本文を省略できない。
        raise ValueError(f"変更後のファイルが存在しません: {path}")
    current = _text_lines(current_text)
    hunks = entry["hunks"]
    _check_coordinates(hunks)
    # 新総行数 + (旧範囲行数 - 新範囲行数) の合計で旧総行数を求める。
    # hunk 外は未変更なので、その行数は新旧で共通とみなせる。
    old_total = len(current) + sum(hunk["old_count"] - hunk["new_count"] for hunk in hunks)
    if old_total < 0 or (entry["old_path"] is None and old_total != 0):
        # 旧総行数が負になるパッチは不正。新規ファイルは旧総行数が必ず 0。
        # これにより、新規パッチに書かれていない現在行の見落としも検出する。
        raise ValueError(f"追加・削除の行数と現在の本文が一致しません: {path}")
    rows = []
    # 以後の番号は「次に処理する行」を指す 1 始まり。配列位置とは 1 だけ違う。
    old_number = new_number = 1
    for hunk in hunks:
        # 正の行数は開始行-1、0 行の範囲は挿入境界そのものを配列位置とする。
        new_index = hunk["new_start"] - (1 if hunk["new_count"] else 0)
        if new_index > len(current) or new_index + hunk["new_count"] > len(current):
            # 削除 hunk でも、その挿入境界が現在全文の範囲内である必要がある。
            raise ValueError(f"hunk が現在の本文の範囲を超えています: {path}")
        # hunk 間の本文は現在のファイルからそのまま補う。
        while new_number - 1 < new_index:
            rows.append(_context_row(old_number, new_number, current[new_number - 1]))
            old_number += 1
            new_number += 1
        for line in hunk["lines"]:
            kind = line["kind"]
            line_text = line["text"]
            if kind != "added" and not line["newline"] and old_number != old_total:
                # 旧側に存在する行に「改行なし」が付くなら、旧最終行のはず。
                # 途中にマーカーを付けて後続行をつなげてしまう入力を拒否する。
                raise ValueError(f"旧ファイルの末尾以外に改行なしマーカーがあります: {path}")
            if kind != "removed":
                # 新側に存在する context/added は、現在全文の同じ一行と照合。
                # 内容が同じでも末尾改行が違えば別のコードとして拒否する。
                if new_number > len(current) or current[new_number - 1] != (
                    line_text, line["newline"],
                ):
                    raise ValueError(f"パッチと現在の本文が一致しません: {path} の新 {new_number} 行目")
            rows.append({
                # 存在しない版の番号は 0 等ではなく None にし、誤参照を防ぐ。
                "old": old_number if kind != "added" else None,
                "new": new_number if kind != "removed" else None,
                "kind": kind, "text": line_text, "newline": line["newline"],
            })
            old_number += kind != "added"
            new_number += kind != "removed"
    # 最後の hunk より後ろにも全文が続く。パッチにない末尾を省略しない。
    while new_number <= len(current):
        rows.append(_context_row(old_number, new_number, current[new_number - 1]))
        old_number += 1
        new_number += 1
    # raw 本文での照合が済んだ後、旧新それぞれの先頭 BOM だけを除去する。
    # 先頭行の移動で同じ文脈行の表示が旧新で異なる場合だけ old_text を持つ。
    # これにより前後行のない U0 パッチでも旧版を正確に復元できる。
    for row in rows:
        raw_text = row["text"]
        # 版ごとの行番号 1 だけを対象にする。同じ U+FEFF でも途中の行なら
        # ソース文字の一部なので残す。二重 BOM でも除去するのは先頭の一つ。
        old_text = raw_text.removeprefix("\ufeff") if row["old"] == 1 else raw_text
        new_text = raw_text.removeprefix("\ufeff") if row["new"] == 1 else raw_text
        row["text"] = old_text if row["new"] is None else new_text
        if row["kind"] == "context" and old_text != new_text:
            # 旧版のハッシュ・ハイライト等は old_text を優先して復元する。
            # 同じ内容の大半の行には、この追加フィールドを持たせない。
            row["old_text"] = old_text
    return rows
