# 開発・配布

このフォルダは開発者向けです。通常の利用は[ルートのREADME](../README.md)、元の要求は [docs/DRECTION.md](docs/DRECTION.md) を参照してください。

## 保守する場所

製品の正本は [../skills/walkthrough-review/](../skills/walkthrough-review/SKILL.md) です。Python実装・HTMLテンプレート・JavaScript・CSS・同梱ライブラリは、このスキル内だけで保守します。

| 場所 | 内容 |
| --- | --- |
| `docs/DRECTION.md` | 元の要求。要求の確認に使う |
| [docs/data-format.md](docs/data-format.md) | 生成データと差分処理APIの開発資料 |
| `tests/` | Python・UIロジックの自動テスト |
| `tests/fixtures/` | 全体・差分・削除の検証に使う固定入力 |
| [tests/evals.json](tests/evals.json) | AIが一言の依頼から完了できるかを確認する評価シナリオ |
| `tools/` | 配布、導入、素材取得、ブラウザー確認の開発ツール |
| `walkthrough.py`, `diff_parser.py`, `tools/prepare_review.py`, `tools/comment_cards.py`, `tools/render_svg.py` | スキル内の実装を読み込む開発用の入口 |
| `package.bat` | Windows向けの配布物生成 |
| `dist/`, `output/`, `.test-tmp/` | 配布物、表示確認用の生成物、テスト用一時ファイル（Git管理対象外） |

以前のリポジトリ直下にあったCLI・`tools/`・`package.bat` は、この `dev/` 配下へ移動しました。直接呼び出すスクリプトではパスを更新してください。以下のコマンドは **`dev/` を作業ディレクトリ**として実行します。

AIの実行手順・完了条件は [SKILL.md](../skills/walkthrough-review/SKILL.md)、解説JSON・行参照・ハッシュのルールは [入力形式](../skills/walkthrough-review/references/input-format.md)、任意の図解は [SVG図](../skills/walkthrough-review/references/svg-diagrams.md)、パッチ取得は [差分レビュー](../skills/walkthrough-review/references/diff-review.md) に集約しています。

## 自動テスト

```powershell
python -m unittest discover -s tests -v
node tests/ui_logic.test.cjs
```

Pythonテストは標準ライブラリだけを使います。UIロジックのテストにはNode.jsが必要です。テストには、配布物を展開して元リポジトリへの依存なしで実行する検証も含みます。

保存形式v2のブロック復元・末尾改行・BOM例外・不正データ拒否は `tests/test_walkthrough.py`、五万行の表示範囲描画・DOM再利用・行操作の集約・Workerの取消と再利用・キャッシュ上限・印刷は `tests/ui_logic.test.cjs` で確認します。旧保存形式の互換処理は持ちません。

対象外差分のメモリがファイル数に比例して増えないこと、反復APIで後半の不正も検出すること、未解説範囲の意味と計算量は `tests/test_scaling.py` で確認します。`python tools/benchmark_scaling.py` は合成入力で追加確保メモリ、範囲判定の改善前との比較、自己確認資料の生成時間を測り、`output/scaling/benchmark.json` へ保存します。入力自体のメモリは計測外です。

カード単位の作成・修正、Markdown/SVG入力、並行保存、分割読出し、再開・Hash引継ぎ・自己確認記録・一括集約は `tests/test_comment_cards.py` で確認します。展開スキルだけでのカード作成から最終HTMLまでの実行は `tests/test_package_skill.py` に含めます。サブエージェントの推奨と依頼例はスキル内の [担当分担](../skills/walkthrough-review/references/delegation.md) を参照してください。

受入データの全ケースについて、生成結果と `expectations.json` の件数・行数・照合状態・図数が一致することを `tests/test_browser_acceptance.py` で確認します。コード変更のみの段階ではHTMLとスナップショットが変わらず、再ビルド後に不一致・消失範囲・対象外解説が期待値どおりに現れることも検証します。これらは生成データの整合性の検査です。UIを変更する開発時には、実ブラウザーでの表示・操作確認を別途行います。通常のスキル実行の完了条件には含めません。

SVG図の形式検証・自己確認資料への出力・再ビルドでの保持は `tests/test_diagrams.py` で確認します。差分付加情報と質問文の検証も含め、形式を変更した際はPython・UI両方のテストを実行してください。配布後の図付き解説の動作は展開スキルのテストでも確認します。

SVG描画の入力検証、複数図とカードの対応、画像寸法、描画失敗、既存出力の保護は `tests/test_render_svg.py` で確認します。自動テストはブラウザーの起動境界を模擬するため、描画ツールを変更したら次の手順で実PNGも確認してください。

AIの行動評価は `tests/evals.json` の各依頼を対象プロジェクトで実行し、対象選定から最終確認・提示まで完了するかを確かめます。JSON内の `files` はリポジトリルートからの相対パスです。過去の実行結果を現行版の保証として扱わず、変更した範囲に応じて確認します。

## 検証用の入力

`tests/fixtures/project/` は変更後の小さなプロジェクト、`project-before/` は変更前を確認するための資料です。`targets.json` と `comments.json` は全体・通常差分向け、末尾が `-diff.json` のものは削除ファイルも含める差分向けです。

全体レビューを生成する例：

```powershell
python walkthrough.py build tests/fixtures/project --targets tests/fixtures/targets.json --output output/full
python walkthrough.py merge output/full --comments tests/fixtures/comments.json
```

削除を含む差分レビューを生成する例：

```powershell
python walkthrough.py build tests/fixtures/project --targets tests/fixtures/targets-diff.json --patch tests/fixtures/diff.patch --output output/diff
python walkthrough.py merge output/diff --comments tests/fixtures/comments-diff.json
```

生成した `output/full/index.html` または `output/diff/index.html` を開きます。これらは開発用の固定解説を使う動作確認です。実際の依頼では、AIが対象コードを読み、解説を作成して自己確認します。

## SVGだけの描画確認

```powershell
python tools/render_svg.py output/full --output output/svg-check-1
```

図が入ったレビューのフォルダ、`review.json`、export後の `comments.json`、単独の `.svg` を入力できます。ローカルのChrome/Edge/Chromiumを自動検出し、必要なら `--browser` で実行ファイルを指定します。外部PythonパッケージやHTTPサーバーは不要です。

未作成の出力フォルダに、各図の元SVG、拡大幅960pxとカード幅360pxを1枚にしたPNG、カード対応表 `report.json` ができます。**PNGを開いて**日本語・文字切れ・重なり・矢印・縮小時の読みやすさを確認します。図がない場合は図数0の対応表だけを作り、ブラウザーは起動しません。画像出力と目視確認は別で、`rendered` は描画成功のみを表します。

配布ツールを変更した場合は `.skill` を展開し、展開先の `scripts/render_svg.py` から同じSVGを描画できることも確認します。通常のスキル実行での最終版の全図確認・修正方法・確認メモは [SVGの手順](../skills/walkthrough-review/references/svg-diagrams.md#レンダリングして自己確認する) を参照してください。

## 開発時のブラウザー確認

以下はUI変更時の開発検証です。通常のレビュー資料作成では実施せず、SVGのPNG確認だけを行います。

```powershell
python tools/prepare_browser_acceptance.py
```

`output/browser-acceptance/` に全体・差分・図付き解説・差分付加情報・長い解説・空状態・ハッシュ警告・コピー失敗の確認用HTMLを作ります。`diagrams/index.html` は図付き解説、`metadata/index.html` は本文が同じ権限変更の確認用です。生成成功は表示・操作確認の合格を意味しません。以下をブラウザーで確認してください。

- 概要本文が冒頭から読め、全体ヘッダがなく、ソース表示のヘッダがコンパクトになっている。
- ファイルツリーは「対象」が初期選択され、フィルタは「対象」「対象外」「すべて」の順に並ぶ。
- 「すべて」で全走査結果を確認できる。右ペインと「対象外」フィルタには、`.git`、Gitの無視対象、`.` で始まるフォルダ配下を表示しない。
- 選択したファイルの全文が表示され、対象外ファイルの案内は「このファイルはレビュー対象外です。」となる。
- 差分の追加・削除と未変更部分、削除ファイルの旧行番号が読める。
- 本文の追加・削除がない権限変更でも変更内容を表示し、質問文と自己確認資料にも残る。
- 別ファイル・旧行へのリンク、ブラウザーの戻る・進むが機能する。
- コードと解説のどちらからも対応箇所にスクロールでき、長文や同じ開始行のカードでも位置が往復し続けない。
- 解説カードの本文・余白をクリックして該当行へ移動できる。カード内の質問・参照リンク・図拡大と文章の選択はそれぞれの操作を保ち、行番号ボタンはキーボードでも使える。
- SVG図がカード本文の下に表示され、文字切れや矢印の欠落がなく、図を含むカードでもスクロールが連動する。
- 質問文に参照が入り、自動コピーと手動コピーの両方を利用できる。
- 図付きカードから作る質問文にタイトル・説明・SVG全文が含まれる。
- 正常時はコード照合済みの表示がなく、ハッシュ未記録・不一致、消失した行、対象外になった解説は警告で把握できる。
- 未マージ、空ファイル、対象なしでも表示と操作が成立する。
- `large/index.html` で五万行の先頭・中間・末尾への移動、質問、改行なし表示を確認する。DOMは表示範囲と前後20行に収まり、複数行文字列の色が境界で切れない。`small.py` へ切り替えて古い行や構文強調が混ざらず、再訪時に解析結果を再利用する。
- 横長行を表示して横へスクロールし、縦に移動しても横位置が戻らない。画面幅変更後も行高・解説の対応が合い、コードから解説・解説から画面外の行へも移動できる。印刷時は全文が対象になり、印刷終了後は表示範囲のDOMへ戻る。
- 狭い画面とキーボードで操作できる。
- 通信を切った状態で `index.html` を直接開き、同梱素材だけで閲覧できる。

### HTTP経由で表示する

ブラウザー操作ツールが `file://` に対応していない場合は、上の準備コマンドでHTMLを生成した後、`dev/` から次を実行します。

```powershell
python -m http.server 8766 --bind 127.0.0.1 --directory output/browser-acceptance
```

サーバーを動かしたまま、実ブラウザーで次のURLを開きます。

- 差分レビュー: <http://127.0.0.1:8766/diff/index.html>
- 全体レビュー・長い選択範囲: <http://127.0.0.1:8766/long/index.html>

UI素材を変更した場合は準備コマンドを再実行して素材をコピーし、ブラウザーを再読み込みしてから確認します。HTTPでの表示確認と、上のオフラインでの直接表示確認は別々に記録します。

サンドボックスでローカル通信が制限されている場合、接続がタイムアウトしたり `WinError 10013` になったりすることがあります。これは画面の検証失敗と区別し、利用可能な承認手段でループバック限定サーバーの実行権限を確保してから再接続します。HTTP接続も拒否された場合は、未実施の確認内容と理由を記録します。

確認が終わったら、起動したターミナルで `Ctrl+C` を押してサーバーを停止します。

### カード選択時の枠なし強調を確認する

左のファイル一覧から対象を開き、表にある解説カードの見出し・本文・余白をクリックします。行範囲ボタンだけでなく、カード本体からの選択を確認します。

| 画面 | ファイル | クリックするカード | 強調される範囲 | 行数 |
| --- | --- | --- | --- | --- |
| `diff/index.html` | `src/pricing.py` | 変更のない依存先も全文で確認する | 新 L1–5 | 5 |
| `diff/index.html` | `src/main.py` | 割引を検証して0以上の請求額を返す | 新 L4–9 | 6 |
| `diff/index.html` | `legacy.py` | 削除された合計処理の役割を確認する | 旧 L1–3 | 3 |
| `long/index.html` | `long.py` | 同じ開始行の二枚目 | 新 L40–70 | 31 |

合格条件は次のとおりです。

- 対応範囲に移動し、選択したカードとコード行の範囲が一致する。
- コード行は背景色で強調され、各行の上下の罫線や選択範囲の囲み枠が出ない。
- 差分の追加・削除を示す記号と既存の左側の色帯は選択前後で変わらない。
- 別のカードを選ぶと強調範囲が切り替わり、以前の範囲に強調が残らない。同じ開始行のカードでも選択したカードを維持する。

見た目に加え、ブラウザーの開発者ツールで `.code-row.highlighted` の計算済みスタイルを確認します。`box-shadow: none`、`outline-style: none`、`border-top-width: 0px`、`border-bottom-width: 0px` が期待値です。差分ではない行の左枠は透明で、追加・削除行の左側の色帯は差分表示として残ります。

実行ごとのスクリーンショットと検証結果は `output/browser-acceptance/verification/` に保存します。2026-10-21のHTTP経由の実施では上の4ケースを確認し、`results.json`、`card-selected-diff.jpg`、`card-selected-deleted.jpg`、`card-selected-long.jpg` を保存しました。この実施ではオフラインの直接表示は確認していません。証跡は開発用生成物であり、配布物には含めません。

### ハッシュ変更を段階的に確認する

ハッシュの段階確認は、最初に `hash/index.html` を開き、次を一つずつ実行して再読み込みします。

```powershell
python tools/prepare_browser_acceptance.py hash-mutate
python tools/prepare_browser_acceptance.py hash-rebuild
```

最初の操作は検証用コードだけを変更するため、HTMLは元の12行・一致3件のままです。再ビルド後は3行・不一致2件・消失範囲1件・対象外の解説1件になります。`clipboard-failure/index.html` は自動コピーを意図的に失敗させる開発専用画面です。

## 配布物を作る

Windowsでは `package.bat`、他の環境では次を実行します。

```powershell
python tools/package_skill.py
python tools/package_release.py
```

| 出力先（`dev/` からの相対パス） | 配布内容 |
| --- | --- |
| `dist/walkthrough-review.skill` | `walkthrough-review/` 一式を収めたZIP形式のスキル |
| `dist/walkthrough-review.zip` | `.skill` と同じバイト列の通常ZIP |
| `dist/WalkthroughReviewTool.zip` | `skills/` と `dev/` を含む開発・監査・再梱包用のリポジトリZIP |
| 各配布物の `.sha256` | 内容照合用のSHA-256 |

通常の利用者には `.skill` またはスキルZIPを渡します。開発用ZIPはスキル導入には不要です。

配布ツールは必須ファイルと収録内容を検証してから既存の同名配布物を更新します。スキル側のローカルリンクが単独配布内で解決することも確認します。自動テストやブラウザー操作は梱包コマンドから実行しないため、出荷前に別途確認してください。

`tools/package_release.py` の `SHIP_FILES` が開発用ZIPの収録一覧です。ソースや開発資料を追加・移動した場合はここも更新します。レビュー生成物、キャッシュ、過去の実行結果は収録しません。

## 導入補助・同梱素材

スキルの配置を補助する場合は、探索先を明示します。同じ内容なら再利用し、内容が異なる既存スキルは上書きしません。

```text
python tools/install_skill.py --skills-dir スキル探索先の絶対パス
```

ライブラリの版・ライセンス・取得元は、スキル内の[第三者ライセンス案内](../skills/walkthrough-review/references/third-party-notices.md)とそのリンク先を参照してください。再取得が必要な開発作業に限り、ネット接続可能な環境で `python tools/vendor_assets.py` を実行します。
