# 解説カードに添えるSVG図

図がコードの理解に役立つと判断した場合に読む。分岐の合流、複数関数の呼び出し順、データの受け渡しなど、文章だけでは追いづらい関係を図示する。単純な宣言や一行の代入を装飾するために付ける必要はない。

## コメントへの追加

既存コメントの `diagrams` に図の配列を追加する。省略または `[]` は図なし。各図は次の3項目を持つ。

| 項目 | 内容 |
| --- | --- |
| `title` | 空でない文字列。図が説明する関係の名前 |
| `description` | 空でない文字列。図の読み方と、コードについて読み取れることを説明する文章 |
| `svg` | SVG要素一つをルートとするXML文字列 |

たとえば合計計算と割引適用の順序を説明するカードには、次のように追加する。これは形式例なので、実際の関数名・式・説明は対象コードに合わせる。

```json
{
  "diagrams": [
    {
      "title": "合計から支払額への流れ",
      "description": "calculate_totalで得た合計から割引を引き、maxで支払額の下限を0にします。",
      "svg": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 440 100\"><rect x=\"10\" y=\"20\" width=\"180\" height=\"60\" rx=\"8\" fill=\"#eff6ff\" stroke=\"#2563eb\"/><text x=\"100\" y=\"45\" text-anchor=\"middle\" font-size=\"16\">calculate_total</text><text x=\"100\" y=\"65\" text-anchor=\"middle\" font-size=\"16\">(prices)</text><line x1=\"195\" y1=\"50\" x2=\"232\" y2=\"50\" stroke=\"#334155\" stroke-width=\"2\"/><polygon points=\"232,50 224,45 224,55\" fill=\"#334155\"/><rect x=\"240\" y=\"20\" width=\"190\" height=\"60\" rx=\"8\" fill=\"#f0fdf4\" stroke=\"#16a34a\"/><text x=\"335\" y=\"45\" text-anchor=\"middle\" font-size=\"16\">max(0,</text><text x=\"335\" y=\"65\" text-anchor=\"middle\" font-size=\"16\">subtotal - discount)</text></svg>"
    }
  ]
}
```

図はカード本文の下にタイトル・説明とともに表示される。`body` へSVGを直接埋め込まず、`diagrams` を使う。JSONは文字列を連結して作るより、オブジェクトをJSONシリアライザーで保存すると引用符や改行を正しく扱える。

## SVGの形式

- ルートは `xmlns="http://www.w3.org/2000/svg"` を持つ `<svg>`。`viewBox` は有限の4数値とし、幅・高さを正にする。ルートの `width` と `height` は省略可。指定する場合は正の有限寸法とし、単位なしまたは `px`, `pt`, `pc`, `mm`, `cm`, `in`, `em`, `ex`, `%` を使う。カード幅に合わせて縮小されるため、短いラベルと十分な文字サイズを使う。
- 使用できる要素は `svg`, `g`, `defs`, `marker`, `path`, `rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`, `text`, `tspan`, `title`, `desc`, `linearGradient`, `radialGradient`, `stop`, `clipPath`。
- スクリプト、CSSの `style`、`foreignObject`、`image`、`use`、`a`、`href`、イベント属性は使用できない。外部URL、DOCTYPE、ENTITY、処理命令も使用しない。
- 矢印マーカー・グラデーション・クリップの参照には同じ図の `id` を指す `url(#id)` のみを使い、`fill`, `stroke`, `clip-path`, `marker-start`, `marker-mid`, `marker-end` に指定する。IDは英字または `_` で始め、以降は英数字・`_`・`.`・`:`・`-` を使う。各IDは図の中で一意にし、参照先を必ず定義する。外部画像やフォント、ネット接続に依存させない。
- 許可外の要素や属性を除去して通す処理ではなく、入力エラーとして拒否する。エラーに示された箇所を修正して再マージする。

使用できる属性は次のとおり。SVGの大文字・小文字を区別する。

| 用途 | 許可属性 |
| --- | --- |
| 識別・表示領域 | `id`, `viewBox`, `width`, `height`, `preserveAspectRatio` |
| 座標・形状 | `x`, `y`, `x1`, `y1`, `x2`, `y2`, `cx`, `cy`, `r`, `rx`, `ry`, `fx`, `fy`, `fr`, `d`, `points`, `pathLength`, `transform` |
| 塗り・線 | `fill`, `fill-opacity`, `fill-rule`, `stroke`, `stroke-width`, `stroke-opacity`, `stroke-linecap`, `stroke-linejoin`, `stroke-miterlimit`, `stroke-dasharray`, `stroke-dashoffset`, `opacity`, `vector-effect` |
| クリップ | `clip-path`, `clip-rule`, `clipPathUnits` |
| 矢印マーカー | `marker-start`, `marker-mid`, `marker-end`, `markerWidth`, `markerHeight`, `refX`, `refY`, `orient`, `markerUnits` |
| 文字 | `dx`, `dy`, `rotate`, `textLength`, `lengthAdjust`, `font-family`, `font-size`, `font-weight`, `font-style`, `text-anchor`, `dominant-baseline`, `alignment-baseline`, `letter-spacing`, `word-spacing` |
| グラデーション | `gradientUnits`, `gradientTransform`, `spreadMethod`, `offset`, `stop-color`, `stop-opacity` |

## 作成・自己確認

図のノードと矢印を実装の処理・条件・値に対応させ、カード本文で省略した前提があれば説明する。推測した設計意図は推測と明記する。図を付けたカードの `file`、行範囲、`side`、`code_hash` は文章と同じ規則で記録する。

`self-review.md` には図のタイトル・説明・SVG全文が出る。コード全文と照合して分岐条件、実行順序、矢印の向き、旧版と新版の区別を確認する。見た目は次のPNGで確認する。レビュー画面全体やカードとコードのスクロール連動をブラウザーで確認する工程はない。

再ビルドは図を含む旧解説を保持する。コードが変わったときは文章と図の両方を読み直し、ハッシュだけを更新しない。解説カードの質問文には図のタイトルと文章による説明だけを添え、SVG全文は含めない。図の実体を確認する必要がある場合は、利用できる `review.json` / `self-review.md` を参照する。

## レンダリングして自己確認する

図がある場合だけ実行する。Pythonの外部パッケージは不要。ツールがローカルのChrome/Edge/Chromiumをヘッドレス起動し、SVGだけをCanvas経由でPNGへ描く。ブラウザーの操作ツールやHTTPサーバーは使わない。ブラウザーはPATHとOSの標準配置から探し、見つからない場合はAIが既存の実行ファイルを探して `--browser ABSOLUTE_PATH` に渡す。

作成中の単独SVGを確認する場合：

```text
PYTHON SKILL_DIR/scripts/render_svg.py WORK/flow.svg --output WORK/flow-preview-1
```

最終build・merge後、完成資料に含まれる全図を確認する場合：

```text
PYTHON SKILL_DIR/scripts/render_svg.py REVIEW --output WORK/svg-check-1
```

`review.json` またはexport後の `comments.json` も直接入力できる。出力先は未作成のフォルダを指定する。再実行時は末尾の番号を変え、古い画像との混同を避ける。図がない入力ではブラウザーを起動せず、図数0の対応表だけを返す。

1. 戻る対応表と `report.json` の `diagram_count`・全 `diagrams` を確認する。各項目には図のタイトル・説明、カードID・対象ファイル・行・版・Hash、元SVGのSHA-256、PNGとSVGの絶対パスがある。図付きカードの数と図の数は同じとは限らない。
2. **AIが全PNGを画像表示ツールで開いて見る。** 1枚に拡大確認幅（既定960px）とカード幅（既定360px）を並べる。文字切れ、文字と線・枠の重なり、矢印の先端と方向、欠けた図形、日本語の文字化け、縮小時の読みやすさを確認し、該当コード・図の説明と照合する。必要なら `--width 1200 --card-width 320` のように幅を調整する。
3. 問題があれば元SVGと必要な説明を修正し、`CARDS_CLI update ... --svg-file ...` でカードへ反映する。該当ファイルを再確認して `reviewed` を付け、再export/mergeする。新しい出力先へ再描画し、変更後の画像を確認する。最終資料の全図が確認対象になっていることを確かめる。
4. 確認結果は対応表のカードID・図番号・SVGのSHA-256と結び付け、短いメモを同じ作業先に残す。`status: "rendered"` は描画完了のみを表し、目視確認や意味の正しさの証明ではない。図を変更したら以前の確認結果を流用しない。

描画ツールはSVGをレビュー本体と同じ規則で検証してから画像化する。外部参照やスクリプトを含む図は拒否する。エンジンがない・起動できない・描画に失敗した・AIがPNGを読めない場合は、確認未完了の図と理由を概要・最終回答に残す。レンダリングできないことを理由にSVGソースの確認だけで表示確認済みとしない。
