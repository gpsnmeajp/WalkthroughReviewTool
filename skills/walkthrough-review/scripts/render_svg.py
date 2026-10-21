"""SVGだけをPNGへ描画し、AIが画像を開いて自己確認できるようにする。

Pythonは標準ライブラリだけを使う。描画には既存のChrome/Edge/Chromiumを
ヘッドレス起動し、SVG画像をCanvasへ描く。レビュー画面自体は開かない。
PNG生成は見た目や意味の合格判定ではない。確認はスキルを実行するAIが行う。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

import walkthrough


def find_browser(explicit=None):
    """指定パス、PATH、OSの標準配置から既存の描画エンジンを探す。"""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ValueError(f"ブラウザー実行ファイルがありません: {path}")
        return path.resolve()
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ]
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(key):
            root = Path(os.environ[key])
            candidates.extend(root / name for name in (
                "Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe",
            ))
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise ValueError("SVG描画にはChrome/Edge/Chromiumが必要です。既存の実行ファイルを--browserで指定できます")


def load_diagrams(source):
    """単独SVG、comments.json、最終review.jsonから図と対応するカードを取り出す。"""
    source = Path(source).resolve()
    if source.is_dir():
        source = source / "review.json"
    content = source.read_bytes()
    text = content.decode("utf-8-sig")
    if source.suffix.lower() == ".svg":
        diagrams = [{"title": source.stem, "description": "単独SVGの表示確認", "svg": text}]
        cards = [{"diagrams": diagrams}]
    else:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("入力JSONにはオブジェクトを指定してください")
        if "files" in data:
            cards = [card for file in data["files"] for card in file.get("comments", [])]
            cards += data.get("unattached_comments", [])
        elif "comments" in data:
            cards = data["comments"]
        else:
            raise ValueError("review.json・comments.jsonまたはSVGを指定してください")
    entries = []
    for card in cards:
        diagrams = card.get("diagrams", [])
        # HTMLへの埋込み前に、レビュー本体と同じ規則で外部参照等を拒否する。
        walkthrough.validate_diagrams(diagrams)
        for number, diagram in enumerate(diagrams, 1):
            entry = {key: card.get(key) for key in (
                "id", "file", "side", "start_line", "end_line", "code_hash",
            )}
            entry.update(diagram_index=number, **diagram)
            entries.append(entry)
    return source, hashlib.sha256(content).hexdigest(), entries


def render_page(svg, width, card_width):
    """隔離したSVG画像を実寸で描く。画像の縦横比はブラウザー自身に計算させる。"""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    # SVGをHTMLとして解釈させない。CDATA内のHTML風文字列も画像内に閉じ込める。
    return """<!doctype html><meta charset="utf-8"><body><script>
const source = 'data:image/svg+xml;base64,ENCODED';
const widths = WIDTHS;
function finish(value) {
  const result = document.createElement('pre');
  result.id = 'svg-render-result';
  result.textContent = JSON.stringify(value);
  document.body.replaceChildren(result);
}
window.addEventListener('load', async () => {
  try {
    const images = await Promise.all(widths.map(async width => {
      const image = new Image();
      image.src = source;
      image.style.cssText = `display:block;width:${width}px;height:auto`;
      document.body.append(image);
      await image.decode();
      const height = Math.ceil(image.getBoundingClientRect().height);
      return {image, width, height};
    }));
    const width = Math.max(...widths) + 32;
    const height = images.reduce((sum, item) => sum + item.height + 48, 16);
    // 極端な縦長図を黙って切り取らず、図の分割や寸法調整を促す。
    if (images.some(item => item.height < 1) || width * height > 16000000 || height > 16000) {
      throw new Error('図が大きすぎるか高さが不正です。SVGを分割するか描画幅を調整してください');
    }
    const canvas = document.createElement('canvas');
    canvas.width = width; canvas.height = height;
    const context = canvas.getContext('2d');
    context.fillStyle = '#fff'; context.fillRect(0, 0, width, height);
    let y = 16;
    images.forEach((item, index) => {
      context.fillStyle = '#334155'; context.font = '14px sans-serif';
      context.fillText(`${index ? 'Card' : 'Detail'} (${item.width}px)`, 16, y + 14);
      y += 28;
      context.drawImage(item.image, 16, y, item.width, item.height);
      y += item.height + 20;
    });
    finish({width, height, png: canvas.toDataURL('image/png').split(',')[1]});
  } catch (error) { finish({error: String(error)}); }
});
</script>""".replace("WIDTHS", json.dumps([width, card_width])).replace("ENCODED", encoded)


def render_png(browser, svg, width, card_width, temporary):
    """1図を描画し、ブラウザーの完了結果とPNG寸法を確認してバイト列を返す。"""
    page = temporary / "render.html"
    page.write_text(render_page(svg, width, card_width), encoding="utf-8")
    result = subprocess.run([
        str(browser), "--headless", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
        "--disable-sync", "--no-proxy-server", "--host-resolver-rules=MAP * ~NOTFOUND",
        f"--user-data-dir={temporary / 'profile'}", "--virtual-time-budget=3000",
        "--dump-dom", page.as_uri(),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    match = re.search(r'<pre id="svg-render-result">([^<]+)</pre>', result.stdout)
    if result.returncode or not match:
        raise ValueError(f"SVGの描画が完了しませんでした: {result.stderr[-1500:]}")
    value = json.loads(html.unescape(match[1]))
    if "error" in value:
        raise ValueError(value["error"])
    png = base64.b64decode(value["png"], validate=True)
    if (len(png) < 33 or png[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            or struct.unpack(">II", png[16:24]) != (value["width"], value["height"])):
        raise ValueError("描画結果がPNGでないか、報告された画像寸法と一致しません")
    return png, value["width"], value["height"]


def render(source, output, browser=None, width=960, card_width=360):
    """検証済みの全図を新しいフォルダへ描画し、最後に対応表を保存する。"""
    if not 100 <= width <= 2400 or not 100 <= card_width <= 2400:
        raise ValueError("描画幅は100〜2400pxにしてください")
    source, source_hash, entries = load_diagrams(source)
    engine = find_browser(browser) if entries else None
    output = Path(output).resolve()
    # 前回の画像を今回の結果と取り違えないよう、既存フォルダには書かない。
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "rendered", "source": str(source), "source_sha256": source_hash,
              "browser": str(engine) if engine else None, "widths": [width, card_width],
              "diagram_count": len(entries), "diagrams": []}
    for number, entry in enumerate(entries, 1):
        svg = entry.pop("svg")
        with tempfile.TemporaryDirectory(prefix=".render-", dir=output) as name:
            png, image_width, image_height = render_png(engine, svg, width, card_width, Path(name))
        stem = f"diagram-{number:04d}"
        (output / f"{stem}.svg").write_bytes(svg.encode("utf-8"))
        (output / f"{stem}.png").write_bytes(png)
        entry.update(svg_sha256=hashlib.sha256(svg.encode("utf-8")).hexdigest(),
                     svg=str(output / f"{stem}.svg"), png=str(output / f"{stem}.png"),
                     width=image_width, height=image_height)
        report["diagrams"].append(entry)
    # 途中失敗時はreport.jsonを作らず、部分的な出力を全図成功と扱わせない。
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    """標準出力は画像パスとカード対応表だけ。PNGやSVG全文を会話へ流さない。"""
    parser = argparse.ArgumentParser(description="SVGだけをPNG化してAIの画像確認に使う")
    parser.add_argument("source", type=Path, help="SVG・comments.json・review.json・REVIEWフォルダ")
    parser.add_argument("--output", type=Path, required=True, help="未作成の画像出力フォルダ")
    parser.add_argument("--browser", type=Path, help="Chrome/Edge/Chromiumの実行ファイル")
    parser.add_argument("--width", type=int, default=960, help="拡大確認幅。既定960px")
    parser.add_argument("--card-width", type=int, default=360, help="カード幅。既定360px")
    args = parser.parse_args(argv)
    try:
        report = render(args.source, args.output, args.browser, args.width, args.card_width)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
