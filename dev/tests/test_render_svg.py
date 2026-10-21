"""SVG描画の入力検証、画像とカードの対応、失敗時の扱いを確認する。"""

import base64
import hashlib
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

from tools import render_svg as renderer


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80"><text x="10" y="40">入力 → 保存</text></svg>'


def png_bytes(width=20, height=10):
    """外部画像パッケージなしで、既知寸法の白いPNGをテスト入力にする。"""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff" * width * 3) * height)) + chunk(b"IEND", b""))


class RenderSvgTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".test-tmp"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="render-svg-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "flow.svg"
        self.source.write_text(SVG, encoding="utf-8")
        self.browser = self.root / "browser.exe"
        self.browser.write_bytes(b"not executed")
        self.output = self.root / "images"

    def test_all_diagrams_keep_card_mapping_and_exact_svg_hash(self):
        """複数図・旧版・対象を失ったカードも漏れず対応表に入る。"""
        diagram = {"title": "流れ", "description": "入力から保存へ", "svg": SVG}
        old_card = {"id": "old-flow", "file": "gone.py", "side": "old", "start_line": 2,
                    "end_line": 4, "code_hash": "sha256:old", "diagrams": [diagram, diagram]}
        card = {**old_card, "id": "new-flow", "file": "main.py", "side": "new", "diagrams": [diagram]}
        source = self.root / "review.json"
        source.write_text(json.dumps({"files": [{"comments": [card]}], "unattached_comments": [old_card]}), encoding="utf-8")
        with mock.patch.object(renderer, "render_png", return_value=(png_bytes(), 20, 10)) as paint:
            report = renderer.render(self.root, self.output, self.browser)
        self.assertEqual(paint.call_count, 3)
        self.assertEqual(report["diagram_count"], 3)
        self.assertEqual(report["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual([(d["id"], d["diagram_index"]) for d in report["diagrams"]],
                         [("new-flow", 1), ("old-flow", 1), ("old-flow", 2)])
        for item in report["diagrams"]:
            self.assertEqual(Path(item["svg"]).read_text("utf-8"), SVG)
            self.assertEqual(item["svg_sha256"], hashlib.sha256(SVG.encode()).hexdigest())
            self.assertEqual(Path(item["png"]).read_bytes(), png_bytes())
            self.assertEqual(item["start_line"], 2)
        self.assertEqual(json.loads((self.output / "report.json").read_text("utf-8")), report)

    def test_empty_input_needs_no_browser_and_existing_output_is_not_reused(self):
        source = self.root / "comments.json"
        source.write_text('{"comments": []}', encoding="utf-8")
        with mock.patch.object(renderer, "find_browser", side_effect=AssertionError("must not launch")):
            report = renderer.render(source, self.output)
            self.assertEqual(report["diagram_count"], 0)
            original = (self.output / "report.json").read_bytes()
            with self.assertRaises(FileExistsError):
                renderer.render(source, self.output)
        self.assertEqual((self.output / "report.json").read_bytes(), original)

    def test_unsafe_svg_is_rejected_before_launch_or_output(self):
        for unsafe in (SVG.replace("</svg>", '<script>alert(1)</script></svg>'),
                       SVG.replace("</svg>", '<image href="https://example.com/a.png"/></svg>')):
            self.source.write_text(unsafe, encoding="utf-8")
            with mock.patch.object(renderer, "find_browser") as find:
                with self.assertRaises(ValueError):
                    renderer.render(self.source, self.output)
                find.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_missing_engine_or_failed_render_does_not_claim_completion(self):
        with self.assertRaisesRegex(ValueError, "実行ファイル"):
            renderer.render(self.source, self.output, self.root / "missing.exe")
        self.assertFalse(self.output.exists())
        with mock.patch.object(renderer, "render_png", side_effect=ValueError("cannot render")):
            with self.assertRaisesRegex(ValueError, "cannot render"):
                renderer.render(self.source, self.output, self.browser)
        self.assertFalse((self.output / "report.json").exists())

    def test_browser_result_is_decoded_and_png_dimensions_are_checked(self):
        payload = {"png": base64.b64encode(png_bytes()).decode(), "width": 20, "height": 10}
        result = SimpleNamespace(returncode=0, stderr="", stdout=f'<pre id="svg-render-result">{json.dumps(payload)}</pre>')
        with mock.patch.object(renderer.subprocess, "run", return_value=result) as run:
            png, width, height = renderer.render_png(self.browser, SVG, 960, 360, self.root)
            self.assertEqual((png, width, height), (png_bytes(), 20, 10))
            arguments = run.call_args.args[0]
            self.assertEqual(arguments[0], str(self.browser))
            self.assertEqual(arguments[-1], (self.root / "render.html").as_uri())
            self.assertNotIn("shell", run.call_args.kwargs)
            for bad in ("not a PNG", base64.b64encode(png_bytes(21, 10)).decode()):
                payload["png"] = bad
                result.stdout = f'<pre id="svg-render-result">{json.dumps(payload)}</pre>'
                with self.assertRaises(ValueError):
                    renderer.render_png(self.browser, SVG, 960, 360, self.root)

    def test_browser_errors_and_incomplete_dom_are_failures(self):
        for result in (
            SimpleNamespace(returncode=1, stdout="", stderr="failed"),
            SimpleNamespace(returncode=0, stdout="<body>still loading</body>", stderr=""),
            SimpleNamespace(returncode=0, stdout='<pre id="svg-render-result">{"error":"too large"}</pre>', stderr=""),
        ):
            with mock.patch.object(renderer.subprocess, "run", return_value=result):
                with self.assertRaises(ValueError):
                    renderer.render_png(self.browser, SVG, 960, 360, self.root)


if __name__ == "__main__":
    unittest.main()
