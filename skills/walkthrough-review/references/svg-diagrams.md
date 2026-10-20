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

`self-review.md` には図のタイトル・説明・SVG全文が出る。コード全文と照合して分岐条件、実行順序、矢印の向き、旧版と新版の区別を確認する。表示確認できる場合は文字切れ、線と文字の重なり、カード幅での読みやすさ、図を含むカードとコードのスクロール連動も確認する。

再ビルドは図を含む旧解説を保持する。コードが変わったときは文章と図の両方を読み直し、ハッシュだけを更新しない。質問ボタンから作られる解説の質問文にもタイトル・説明・SVG全文が含まれるため、図に関する質問にはその内容を参照できる。
