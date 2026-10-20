# 生成データの開発仕様

この文書は、Python実装と画面を保守するための出力形式・内部APIを記載する。
原要求は [DRECTION.md](DRECTION.md)、AIの実行手順と完了条件は
[SKILL.md](../../skills/walkthrough-review/SKILL.md) に従う。
対象・解説JSONの作成、行参照、作成時ハッシュの記録方法は
[入力形式・解説・ハッシュ](../../skills/walkthrough-review/references/input-format.md) を参照する。

実装の正本は [scripts/walkthrough.py](../../skills/walkthrough-review/scripts/walkthrough.py) と
[scripts/diff_parser.py](../../skills/walkthrough-review/scripts/diff_parser.py)。
ここにはレビューを依頼する人向けの操作手順や、個別の実行結果を置かない。

## review.json

`build` は毎回プロジェクトを走査し、選択されたファイルの全文を取り込む。
`merge` は保存済みの `review.json` を読み、元プロジェクトを再走査せず概要・解説を更新する。
現在の `schema_version` は `1`。既存データを使う `build` と `merge` は、他の版を拒否する。

| フィールド | 保存する値 |
| --- | --- |
| `schema_version` | 整数 `1` |
| `title` | レビューのタイトル |
| `mode` | `full` または `diff`。空のパッチでも指定されていれば `diff` |
| `project_name` | 対象ルートのフォルダ名 |
| `project_root` | 対象ルートの絶対パス。区切りは `/` |
| `generated_at` | `build` 時のUTC日時をISO 8601形式で記録。`merge` では更新しない |
| `overview` | 概要のMarkdown。初回ビルドでは空文字列 |
| `inventory` | 全走査項目と削除済み項目の配列。相対パス順 |
| `files` | 本文を取り込んだ対象ファイルの配列。対象JSONの指定順 |
| `warnings` | リンク・特殊ファイル・走査不能などの走査警告 |
| `hash_warnings` | ハッシュ照合、消失した行、参照切れ、対象を失った解説の警告 |
| `unattached_comments` | 再ビルドで対象ファイルを失った旧解説 |

### inventory

各項目は `path`, `status`, `size`, `selected` を持ち、必要時に説明用の `message` が付く。
`path` はプロジェクト相対パス、`size` はバイト数または不明を示す `null`、
`selected` は本文の取込対象かどうかを示す真偽値。

| `status` | 意味 |
| --- | --- |
| `file` | 通常ファイル。走査時点では本文の読取可否や文字コードを未検証 |
| `symlink` | リンクやWindowsの再解析ポイント。リンク先は追跡しない |
| `special` | 通常ファイル・ディレクトリ以外の項目 |
| `unreadable` | 属性取得やフォルダ内の走査に失敗した項目 |
| `deleted` | パッチにある削除済みファイル。現在のファイルがないため `size` は `0` |

通常ディレクトリ自体は項目にせず中身を走査する。走査できないディレクトリはそのパスと
エラーを残すが、内部の個々のファイルまでは列挙できない。隠しファイルも名前では除外しない。
対象外の本文は `files` に保存しない。差分モードでは対象外の削除済みファイルも一覧に補う。

### files

| フィールド | 保存する値 |
| --- | --- |
| `path` | 表示・参照に使う相対パス。通常は変更後パス、削除ファイルは変更前パス |
| `old_path` | パッチにある変更前パス。追加ファイルやパッチのない対象は `null` |
| `language` | 拡張子から決めたハイライト言語。未対応拡張子は `plain` |
| `sha256` | 現在ファイルの正規化前バイト列のSHA-256。削除ファイルは `null` |
| `text` | BOM除去・改行LF化後の変更後全文。削除ファイルは空文字列 |
| `rows` | 変更前・変更後の行番号を持つ全文の表示行 |
| `code_hashes` | 正規化した全文のSHA-256を持つ `new` / `old` の辞書 |
| `comments` | 入力解説に実装が計算した照合結果を付けた配列。初回ビルドでは空配列 |

`sha256` は実ファイルのバイト列の識別値で、`code_hashes` は解説との照合用である。
空の版にも空文字列のSHA-256を記録する。

## rowsと旧新本文の復元

各行は `old`, `new`, `kind`, `text`, `newline` を持つ。
行番号は1始まりで、存在しない版は `null`。`text` に末尾のLFは含めず、
`newline` の真偽値でその有無を保持する。

| `kind` | `old` | `new` |
| --- | --- | --- |
| `context` | 変更前の行番号 | 変更後の行番号 |
| `added` | `null` | 変更後の行番号 |
| `removed` | 変更前の行番号 | `null` |

全体モードとパッチにない対象は、旧新同じ行番号の `context` 行だけを持つ。
空文字列は0行、LFだけの本文は末尾改行を持つ空行1行として扱う。

変更後の本文は `new` が `null` でない行の `text` を順に結合し、
`newline` が真の行にLFを付けて復元する。変更前も同様に `old` で抽出するが、
`old_text` がある行では `text` の代わりに使う。
`old_text` は同じ文脈行でも旧新のBOM正規化結果が違うときだけ保存する。
各版の先頭行からBOMを一つだけ除き、途中のU+FEFFは本文として保持するためである。

`update_code_hashes` はこの復元結果から両版のハッシュを再計算し、
変更後の復元本文と `file.text` が異なるデータを拒否する。
旧形式で `newline` がない行には互換処理があるが、失われた変更前の末尾改行情報を
完全には復元できない。新規生成データでは全行に `newline` を持たせる。

## 解説の派生フィールドと再ビルド

以下は保存・表示用の派生値であり、解説入力JSONの項目ではない。

| フィールド | 計算内容 |
| --- | --- |
| `hash_status` | 作成時ハッシュと該当版の全文を比較した `verified` / `mismatch` / `missing` |
| `actual_code_hash` | 比較に使った現在の `file.code_hashes[side]` |
| `range_status` | 該当版に開始行・終了行があるとき `valid`、消失していれば `missing` |
| `link_warning` | 解説本文の参照検証エラー。問題がなければ空文字列 |

同じ出力先への再ビルドでは、解決後の `project_root` が一致することを確認し、
旧概要・解説と作成時の `code_hash` を保持して派生値を再計算する。
対象ファイルを失った解説は入力項目の形で `unattached_comments` に残す。
次の再ビルドで再び対象に入れば、関連付けを再試行する。

通常の `merge` は対象外ファイルへの解説と概要の参照切れを拒否する。
行範囲・解説本文のリンクが失われた場合も通常は拒否するが、ハッシュ不一致の解説は
警告付きで保持できる。再ビルドによる引継ぎでは、旧概要・解説を失わないよう
これらの参照エラーを警告として保持する。ハッシュ不一致・未記録だけでは処理を中断しない。
具体的な修正手順と完了判断はスキルの入力形式・完了条件に従う。

## HTMLと補助出力

`write_review` は同じレビュー辞書から `review.json`、`self-review.md`、`index.html` を作る。
`self-review.md` には走査一覧、概要、コード全文、解説、未解説範囲、照合結果が含まれる。
空の入力ひな形 `comments.example.json` は未作成時のみ生成し、既存の内容は上書きしない。
CLIの `build` / `merge` は同梱UIとvendor一式を出力の `assets/` へコピーする。

HTMLテンプレートの `__REVIEW_DATA__` は一か所だけ存在し、
`<script id="review-data" type="application/json">` 内のJSONを画面が読む。
埋込み時には `&`, `<`, `>`, U+2028, U+2029をJSONのUnicodeエスケープへ変える。
JSONとして復元した値は `review.json` と同じであり、元コードの `</script>` 等が
埋込み要素を閉じることを防ぐ。出力ファイル一式の更新は単一トランザクションではない。

準備ツールは `project_root` と `inventory` を持つ `inventory.json` と、
絶対パスの辞書 `session.json` を作る。セッションのキーは
`project`, `work`, `cli`, `inventory`, `targets`, `comments`, `review_dir`, `index`。
`targets` / `comments` / `review_dir` / `index` は後続工程の保存予定先であり、
準備時点でそのファイルやフォルダが存在するという意味ではない。

## diff_parserの公開API

```python
parse_patch(text: str) -> list[dict]
build_rows(entry: dict, current_text: str | None) -> list[dict]
```

`parse_patch` はGitまたは通常のunified diffをファイルごとに分解する。
パッチ先頭のBOMは呼び出し元で除去する。空パッチは空配列を返す。
各entryは `old_path`, `new_path`, `hunks`, `metadata` を持ち、
追加ファイルの `old_path`、削除ファイルの `new_path` は `None`。
hunkは `old_start`, `old_count`, `new_start`, `new_count`, `lines`、
各hunk行は `kind`, `text`, `newline` を持つ。

`build_rows` は一つのentryと変更後全文を照合し、パッチの前後・間にある未変更部分も
補って全文のrowsを返す。`current_text` は改行を正規化し、BOMを保持したUTF-8復号済み本文。
削除ファイルだけ `None` を渡す。空文字列は存在する空ファイルなので削除とは区別する。
パッチの文脈行・追加行は本文と末尾改行を照合する。削除行はパッチから復元するもので、
変更前の実ファイルそのものと照合した結果ではない。

両APIはファイルを読み書きしない。不正な形式・パス・行数・座標や内容不一致、
binary / combined diffなどの未対応形式は `ValueError` にする。
呼び出し元の `create_review` は対象外を含むパッチの全entryを検証してから、
選択対象だけの本文を出力データへ保存する。
