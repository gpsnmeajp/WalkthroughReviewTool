# WalkthroughReviewTool 全体ウォークスルーレビュー

現在の作業コピーを全体モードで取り込んだ資料です。配布スキル、Python実装、オフライン画面、開発用入口、テスト、fixture、要求・設計資料、SVG素材の66ファイルを説明します。コード全文はPythonが実ファイルから取得した固定スナップショットです。今回の資料更新では製品ソースを変更していません。

## 入口とデータの流れ

1. [スキル手順](review:skills/walkthrough-review/SKILL.md#L29)が対象・モード・完了条件を定め、[準備処理](review:skills/walkthrough-review/scripts/prepare_review.py#L1)がプロジェクト外に作業先と全件一覧を用意します。
2. [生成本体](review:skills/walkthrough-review/scripts/walkthrough.py#L1)のbuildが選択したコード全文・行番号・旧新のHashを固定します。差分時は[パーサー](review:skills/walkthrough-review/scripts/diff_parser.py#L1)が変更後ファイルとパッチを照合し、削除本文を復元し、権限・名前変更等の付加情報を保持します。
3. AIは[カード作成ツール](review:skills/walkthrough-review/scripts/comment_cards.py#L1)のreadで固定版を読み、責務・保証・境界条件・失敗時の動作を1件ずつ説明します。担当別に全文と全カードを確認してreviewedを記録し、親が概要とファイル間の接続を統合してexport、mergeします。
4. 最終buildで解説作成後のソース変更を再確認します。図があれば[描画ツール](review:skills/walkthrough-review/scripts/render_svg.py#L1)で最終版の全図をPNG化し、AIが画像を開いて意味と見た目を確かめます。
5. [画面](review:skills/walkthrough-review/assets/ui/app.js#L1)は埋込データから概要・全文・解説を表示します。生成後はネット接続や外部APIなしで閲覧できます。画面はソースを監視せず、AIへの自動送信も行いません。

実装・画面素材の正本はskills/walkthrough-review配下です。devのCLIはその実体を呼び出す薄い入口で、実装を複製しません。[スキル単体配布](review:dev/tools/package_skill.py#L235)と開発用ZIPは収録範囲が異なります。要求の記録は[DRECTION.md](review:dev/docs/DRECTION.md#L7)、保存仕様は[data-format.md](review:dev/docs/data-format.md#L13)へ分かれています。

## 今回の更新で確認する点

- [質問文](review:skills/walkthrough-review/assets/ui/app.js#L766)はプロジェクト・パスと行範囲・質問が基本です。コード本文、Hash、生成日時、重複するレビュー参照、SVG全文を添付しません。解説カードから質問した場合だけ、その解説と図の文章を添えます。旧版の区別、本文以外の差分、確認が必要な警告は短く残します。
- [SVGの手順](review:skills/walkthrough-review/references/svg-diagrams.md#L59)では、生成成功と画像を開いた確認を区別します。今回新たにrender_svg.py、対応する開発用入口、test_render_svg.pyも本文対象へ含めました。通常の資料作成では図のPNGだけを確認し、レビュー全体のブラウザー操作確認は行いません。
- 保存形式v2は連続する本文をblocksへまとめます。[表示](review:skills/walkthrough-review/assets/ui/app.js#L1)は選択したファイルの行モデルを展開し、画面付近のDOMだけを保持します。構文強調のWorkerとキャッシュにより全文の保存と画面の描画負荷を分けています。
- [カード保存](review:skills/walkthrough-review/scripts/comment_cards.py#L1)はIDの大小文字だけが違う衝突を拒否し、担当名の表記もそろえます。固定コードと現在のカード内容に確認記録を結び付け、内容を変えた後は再確認を要求します。

## 対象と対象外

全走査結果から、自作の実装・設定・テスト・資料66ファイルを本文対象に選びました。対象外はGit内部情報、既存docs、dev/output・dev/dist・テスト一時領域等の生成物、Pythonキャッシュ、同梱の第三者ライブラリとそのライセンス等6ファイル、PNG素材2ファイル、定型のMITライセンス本文です。SVGの元素材は対象です。走査の読取警告はありません。

対象外は「確認済み」「問題なし」を意味しません。画面の対象外一覧はGit無視設定等で表示を絞りますが、全件一覧は「すべて」に残ります。docs自身は資料が自分を取り込む再帰を避けるため本文対象外です。更新前のdocsはdev/output/review-historyへZIPで退避しています。

fixtureのconfig.json・notes.txtを「対象外」とするカードは、サンプルのtargets.jsonを使ったときの位置付けです。現在のツール全体レビューでは、これらfixture自身も説明対象です。fixture中のJSONに書かれたsrc/main.py等の参照は、サンプルprojectをルートとして使う入力例です。

## 確認の範囲

全対象の固定全文・全カードを担当全体で自己確認し、行範囲・版・内部リンクとファイル間の接続を照合しました。最終版の全コメントのHash一致と有効な行範囲、参照警告の解消、HTMLの埋込JSONと同梱素材、図のPNGを確認しました。確認結果と対象件数は同梱verification.jsonに記録します。

直前の質問文修正時にPython176件中175件成功・1件スキップ、UIロジック40件成功を確認済みです。スキップはWindowsのシンボリックリンク作成権限によるものです。今回は資料更新のため全テストを再実行せず、成果物の整合検査と単独配布検証を行います。evals.jsonは行動評価のシナリオ集で、宣言された全ケースを今回実行したという意味ではありません。

確認中、補助資料に次の表記差があり、該当カードに保証範囲を明記しました。元ソースは修正していません。

- [受入fixtureの自由文](review:dev/tools/prepare_browser_acceptance.py#L265)には「質問文のSVGソース」という旧期待値が残ります。現行の質問文に含む図情報はタイトルと文章による説明です。数値期待値のテストはこの自由文を検査しません。
- [スケール試験](review:dev/tests/test_scaling.py#L57)の「最後の対象外」というコメントに対し、replace(..., 1)は最初の一致箇所を変えます。対象外の不正を拒否する確認として読み、後半ファイルを壊した試験とは扱いません。

Hash一致はコードと作成時記録の対応を示し、解説の意味や不具合の不存在を証明しません。未解説範囲も機械的な被覆の指標で、宣言・例を含む全行へのカード付与を要求しません。差分の旧本文はパッチから復元し、旧実ファイルに照合した結果ではありません。仮想化された画面ではブラウザー標準の検索・選択がDOM上の範囲に制約されますが、全文はreview.jsonとself-review.mdにも保存します。
