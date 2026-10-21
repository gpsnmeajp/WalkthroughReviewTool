# カード単位の作成と分割確認

この手順のコマンド・Markdown・SVGはAIが用意する。人間にJSONやCLIの準備を頼まない。
`CARDS_CLI` は `SKILL_DIR/scripts/comment_cards.py` の絶対パス、`STORE` は `WORK/authoring`。
準備ツールの `session.json` にも `cards_cli` と `authoring` の絶対パスが入る。

## 初期化・再開

```text
PYTHON CARDS_CLI init REVIEW --output STORE
PYTHON CARDS_CLI status STORE
```

同じ保存先の `init` は既存カードを保持して再開する。コード・対象・差分付加情報が違えば拒否する。
ストアには固定スナップショット、概要Markdown、担当別の独立したカードJSON、確認記録を保存する。
既存レビューから始めた場合は、その解説を `existing` 担当として取り込む。作成時Hashは維持する。
通常の新規レビューではカードは0件で始まる。

## コードを分割して読む

```text
PYTHON CARDS_CLI read STORE --file src/main.py --side new --start 1 --limit 120
```

返るJSONの `lines` に番号付き本文、`code_hash` に固定した版のHash、`diff_summary` に本文以外の差分が入る。
`next` があれば、その `start` / `offset` を次の `--start` / `--offset` に渡して続ける。
通常は120行・本文12000文字まで。長い1行も文字位置で継続でき、途中は `line_complete: false` になる。
行の一部を読んだだけで全文確認済みにしない。`next: null` まで必要な各版を読み切る。
旧新のHashが異なる差分では両版を確認する。削除ファイルは `--side old` で読む。
同じHashの両版を機械的に二度読む必要はない。空ファイルは0行と明示される。
`--limit` は1〜200、`--max-chars` は1〜20000。本文以外のメタ情報はこの文字上限に含まない。

## カードを1件保存する

AIが `WORK/loading.md` に、実装の責務・保証・失敗時の動作を説明する本文を書く。
コード本文の転写や全コメント配列の生成は不要。

```text
PYTHON CARDS_CLI add STORE --owner python --id python-loading --file src/main.py --side new --start 4 --end 12 --title "入力の検証と後続処理" --body-file WORK/loading.md
```

新規IDは担当名とハイフンで始める。例は `python-loading`、別担当なら `ui-navigation`。
IDは全体で一意な1〜64文字の英数字・ハイフン・下線で、英数字から始める。
ストアではIDの大文字・小文字だけが違う重複も拒否する。担当名も既存の表記にそろえ、
Windows等で同じ保存先を別名として上書きしない。既存レビューの取込時も同じ規則で確認する。
作成時Hashは固定スナップショットからツールが付ける。LLMに64桁の値を転記させない。
対象・行範囲・リンク・図は既存マージと同じ規則で保存前に検証する。
成功時はID・保存結果・件数だけを返し、全カード本文やHTMLは出力しない。

図が有効なカードでは [SVG規約](svg-diagrams.md) を読み、AIがSVGと説明Markdownを別ファイルに書く。
`add` または `update` に次を付ける。

```text
--svg-file WORK/flow.svg --diagram-title "検証から出力まで" --diagram-description-file WORK/flow-description.md
```

CLIで指定する図はカード1件につき一つ。文章で足りるカードには付けない。
SVG文字列のJSONエスケープはツールが担当する。

## 個別修正・取得・削除

```text
PYTHON CARDS_CLI status STORE --file src/main.py
PYTHON CARDS_CLI show STORE --owner python --id python-loading
PYTHON CARDS_CLI update STORE --owner python --id python-loading --body-file WORK/loading-fixed.md
PYTHON CARDS_CLI remove STORE --owner python --id python-loading
```

`status` は本文を再掲しない。通常は対象ファイル別の件数・確認状況、`--file` 指定時は担当・ID・行範囲・題名を返す。
既定50件で、`next_offset` を次の `--offset` に渡して全件確認する。`--limit` は1〜100。
`unattached` に対象を失ったカードのIDと担当が出る。最初のページを修正・削除してから再取得すれば後続も確認できる。
`show` はカード1件と照合結果、図の説明・SVG全文を返す。必要なカードだけを取得する。
`update` は指定した項目だけを置換する。`--start` / `--end` / `--file` / `--side` / `--title` も変更できる。
図を外す場合は `--clear-diagrams`。別担当のカードを指定した担当名で上書きすることはできない。
`remove` はそのカードだけを `removed/` に退避し、集約対象から外す。
既存カードは `--owner existing` で修正する。

## 自己確認と確認記録

担当ごとに、担当ファイルの全文、全カード、旧新の対応、リンク、図の説明・SVG全文を確認する。
`read` でコードを分割確認し、`status --file` と `show` で全カードを確認する。
`self-review.md` を読む場合も対象ファイルの節ごとに分割し、全プロジェクト分を一度に読み戻さない。
修正を終えたファイルに確認記録を付ける。

```text
PYTHON CARDS_CLI reviewed STORE --file src/main.py --owner python
```

記録は固定コードと現在のカード内容に結び付く。カードの追加・修正・削除で、そのファイルは再確認が必要になる。
空ファイルも状態を確認して記録する。非空ファイルでカード不要と判断した場合は、
`--reason "定数宣言だけなので概要で説明する"` のように理由を記録し、親は概要にもその扱いを記す。
確認記録はAIの確認申告を保存するものであり、説明の正しさを機械的に証明するものではない。

## 概要と集約

親が概要をMarkdownとして書き、ファイル間の流れ、対象・対象外、制約を説明する。

```text
PYTHON CARDS_CLI overview STORE --body-file WORK/overview.md
PYTHON CARDS_CLI export STORE --output WORK/comments.json
PYTHON SKILL_DIR/scripts/walkthrough.py merge REVIEW --comments WORK/comments.json
```

`export` は全ファイルの最新確認記録・概要・全カードを検証し、対象順・版・行・IDで決定的に集約する。
未確認・不正な参照・Hash不一致・対象を失ったカードがあれば既存の集約ファイルを置換しない。
1件の追記ごとにHTML一式を生成しない。途中確認したいまとまりと、最終版の段階で集約・マージする。
集約したJSONをLLMの出力へ丸ごと再掲しない。手書きで全体配列を作り直さない。

## コード変更後の再確認

最後に同じ対象・パッチで `build` を再実行する。現在のプロジェクトの変更を取り込むのはこの処理であり、
カードツールが元プロジェクトを監視することはない。コード・対象・比較条件が同じなら、再build・merge後もストアを使える。
本文や比較条件が変わった場合は、新しい保存先へカードを引き継ぐ。

```text
PYTHON CARDS_CLI init REVIEW --output WORK/authoring-next --from-store STORE
```

引継ぎは旧カードの本文・担当・作成時Hashを保持し、確認記録は引き継がない。
変更したファイルの新しいコードを読み、対応するカードを修正する。
旧Hashと現在Hashが異なるカードの修正には、再読・本文確認後に `--body-file` と `--confirm-code` の両方を付ける。
Hashだけを最新化して照合警告を消さない。未変更のカードも新しい範囲・参照との対応を確認して記録し、再集約する。

同時実行では各担当が独立したカードJSONを作る。共有ファイルの短い更新と集約はOSロックで保護される。
ツールは標準ライブラリのみで動き、サブエージェントが使えない環境でも同じ手順で順番に完了できる。
