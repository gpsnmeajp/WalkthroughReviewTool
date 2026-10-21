# WalkthroughReviewTool 全体ウォークスルーレビュー

現在の作業コピーを全体モードで取り込んだ資料です。配布スキル、画面、開発用入口、テスト、fixture、要求・設計資料、SVG素材の63ファイルを説明します。コード本文はPythonが取得した固定スナップショットで、AIによる抜粋・転写ではありません。今回の資料更新では製品ソースを変更していません。

## 入口から完成資料まで

利用の入口は[スキル手順](review:skills/walkthrough-review/SKILL.md#L29)です。[準備処理](review:skills/walkthrough-review/scripts/prepare_review.py#L1)がプロジェクト外へ作業領域と全件一覧を作り、AIが本文対象を選びます。[生成本体](review:skills/walkthrough-review/scripts/walkthrough.py#L1)のbuildが全文・行番号・旧新Hashを固定します。差分指定時は[パーサー](review:skills/walkthrough-review/scripts/diff_parser.py#L556)がパッチと現在ファイルを照合し、削除本文や権限・名前変更等の付加情報も保持します。

AIは[カード作成ツール](review:skills/walkthrough-review/scripts/comment_cards.py#L237)で固定版を読み、責務・保証・失敗時の挙動を1件ずつ説明します。担当別に全文と全カードを確認し、reviewedを記録してexport、mergeします。最後のbuildでソース変更を再検出し、[画面](review:skills/walkthrough-review/assets/ui/app.js#L1)が同じデータから概要・コード・解説・質問文を表示します。スキル手順の図はこの受け渡しを示します。

実装と画面素材の正本はskills/walkthrough-review配下です。devのCLIはその正本を読み込む薄い入口です。[スキル単体配布](review:dev/tools/package_skill.py#L234)と[開発用ZIP](review:dev/tools/package_release.py#L216)は目的と収録範囲が異なります。要求の原本は[DRECTION.md](review:dev/docs/DRECTION.md#L7)、保存仕様は[data-format.md](review:dev/docs/data-format.md#L13)にあります。

## 今回取り込んだ修正

- カードID・担当名の大小文字だけが異なる保存先を拒否し、Windows上の上書きを防ぎます。作成時Hashが同じ値なら大文字表記も一致とし、元の表記を保持します。[実装](review:skills/walkthrough-review/scripts/comment_cards.py#L237)と[回帰確認](review:dev/tests/test_comment_cards.py#L1)を対応させました。
- no-prefix形式のrename/copyで、実パス先頭のa/・b/を誤って取り除かないよう、[付加情報との照合](review:skills/walkthrough-review/scripts/diff_parser.py#L203)でパスを確定します。[テスト](review:dev/tests/test_diff_parser.py#L1)がプレフィックス条件を組み合わせて確認します。
- largeケースの[期待値](review:dev/tools/prepare_browser_acceptance.py#L259)は50,000行・解説2件・verified 2件へ修正済みです。旧10,000行・解説1件という不一致の説明を更新しました。[専用テスト](review:dev/tests/test_browser_acceptance.py#L1)で全ケースの数値期待値と生成結果、Hashの3段階を照合します。

## 対象と対象外

全ファイルを走査したうえで、自作の意味のある実装・設定・テスト・資料を本文へ選びました。対象外は.gitの内部情報、既存docs、dev/output・dev/dist・テスト一時領域等の生成物、Pythonキャッシュ、同梱の第三者ライブラリ6ファイル、PNG素材2ファイルです。SVGの元素材は対象に含めています。対象外を確認済み・問題なしとは扱いません。走査の読取警告はありません。

画面の対象外一覧はGit無視設定などで初期表示を絞るため、全件は「すべて」で確認できます。レビュー自身の生成物を再帰的に本文へ含めないため、旧docsは今回の対象外です。以前のdocsはdev/output/review-historyへZIPで退避しました。旧資料に存在した指摘1.mdは現在のプロジェクトにないため、その旧解説は今回の資料から外しました。

fixture中のconfig.json・notes.txtを「対象外」とする説明は、サンプルのtargets.jsonを使う資料での位置付けです。現在のツール全体レビューではfixture自身も取り込んでいます。fixture内JSONの内部リンクはサンプルprojectをルートとする入力例です。

## 確認の範囲と制約

全63対象の固定全文・全カード・図を担当全体で自己確認し、行範囲・版・内部リンクとファイル間の接続を照合しました。Hash一致はコードと作成時記録の一致を示し、説明の意味や作成者の真正性の証明ではありません。未解説の行は機械的な被覆率であり、宣言や例を含む全行へ解説を付ける要件ではありません。

直前の修正確認ではPython 170件中169件成功・1件スキップ、UIロジック38件成功、単独配布検証成功でした。スキップはWindowsでシンボリックリンク作成権限がない条件です。これらは実ブラウザーの見た目・操作や、AIの説明品質すべてを保証するものではありません。今回の成果物確認の詳細は同梱verification.jsonへ記録します。

HTMLは静的で、元コードの自動監視やAIへの自動送信を行いません。更新検出にはbuildが必要です。差分の削除本文はパッチから復元し、旧実ファイルに照合した結果ではありません。画面は表示付近の行だけをDOMへ置くため、標準のページ内検索・選択には範囲の制約があります。全文はreview.json・self-review.mdにも残ります。Workerが使えない場合も本文を表示します。

ファイル保存や配布物の置換は個々のファイルごとに行い、複数ファイル全体を同時確定するトランザクションではありません。生成ツールの成功、Hashの一致、受入データの整合、画面の表示確認はそれぞれ異なる確認です。


今回のブラウザー確認はループバックHTTPで、概要・コード・解説・内部リンク・図の拡大とスクロール連動・図付き質問文を確認しました。通信を切ってHTMLを直接開くfileスキームでの確認、モバイル表示、全受入ケースの実ブラウザー操作は今回実施していません。
