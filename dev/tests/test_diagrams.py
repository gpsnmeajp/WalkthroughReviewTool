"""解説図の安全な取込み、自己確認資料への出力、再生成時の保持を検証する。"""

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

import walkthrough


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 120" width="100%">
<title>処理の流れ</title><desc>入力を検証して保存する</desc>
<defs>
  <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
    <path d="M0 0 L8 4 L0 8 Z" fill="#334155"/>
  </marker>
  <linearGradient id="paint"><stop offset="0%" stop-color="#fff"/>
    <stop offset="100%" stop-color="#dbeafe"/></linearGradient>
  <clipPath id="clip"><rect width="320" height="120"/></clipPath>
</defs>
<g clip-path="url(#clip)" font-family="sans-serif">
  <rect x="10" y="30" width="100" height="60" rx="8" fill="url(#paint)" stroke="#334155"/>
  <text x="60" y="65" font-size="16" text-anchor="middle"><tspan>入力</tspan></text>
  <path d="M110 60 L200 60" stroke="#334155" marker-end="url(#arrow)"/>
  <text x="240" y="65" font-size="16" text-anchor="middle">保存</text>
</g>
</svg>"""


def svg_with(content="", attributes='viewBox="0 0 100 80"'):
    """危険な要素や属性を一つずつ検証するため、最小のSVGへ包む。"""
    return f'<svg xmlns="http://www.w3.org/2000/svg" {attributes}>{content}</svg>'


class DiagramTests(unittest.TestCase):
    """図を実際のコメントへ結合し、出力保護と再ビルドまで確認する。"""

    def setUp(self):
        temporary_parent = Path(__file__).resolve().parents[1] / ".test-tmp"
        temporary_parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="diagram-test-", dir=temporary_parent)
        self.root = Path(self.temp.name).resolve()
        # 削除対象はテスト専用の既知の親の直下であることを確認して登録する。
        self.assertEqual(self.root.parent, temporary_parent.resolve())
        self.addCleanup(self.temp.cleanup)
        self.project = self.root / "project"
        self.project.mkdir()
        self.source = self.project / "code.py"
        self.source.write_bytes(b"value = 1\n")
        self.output = self.root / "review"
        self.review = walkthrough.create_review(self.project, {"files": ["code.py"]})
        self.diagram = {"title": "保存までの流れ", "description": "入力から保存への責務の受渡しを示します。", "svg": SVG}
        self.comment = {
            "id": "flow", "file": "code.py", "start_line": 1, "end_line": 1,
            "title": "保存処理", "body": "ここで値を保存します。",
            "code_hash": self.review["files"][0]["code_hashes"]["new"],
            "diagrams": [self.diagram],
        }

    def test_diagram_is_saved_and_complete_svg_is_in_self_review(self):
        walkthrough.merge_comments(self.review, {"comments": [self.comment]})
        walkthrough.write_review(self.review, self.output, copy_assets=True)
        saved = walkthrough.read_json(self.output / "review.json")
        self.assertEqual(saved["files"][0]["comments"][0]["diagrams"], [self.diagram])
        report = (self.output / "self-review.md").read_text(encoding="utf-8")
        self.assertIn("##### 図: 保存までの流れ", report)
        self.assertIn(self.diagram["description"], report)
        self.assertIn("```svg\n" + SVG + "\n```", report)
        self.assertIn("\\u003csvg", (self.output / "index.html").read_text(encoding="utf-8"))

    def test_diagram_survives_rebuild_exclusion_and_reattachment(self):
        walkthrough.merge_comments(self.review, {"comments": [self.comment]})
        self.source.write_bytes(b"value = 2\n")
        updated = walkthrough.create_review(self.project, {"files": ["code.py"]})
        walkthrough.preserve_comments(self.review, updated)
        comment = updated["files"][0]["comments"][0]
        self.assertEqual(comment["hash_status"], "mismatch")
        self.assertEqual(comment["diagrams"], [self.diagram])
        excluded = walkthrough.create_review(self.project, {"files": []})
        walkthrough.preserve_comments(updated, excluded)
        self.assertEqual(excluded["unattached_comments"][0]["diagrams"], [self.diagram])
        report = walkthrough.self_review(excluded)
        self.assertIn(self.diagram["description"], report)
        self.assertIn("```svg\n" + SVG + "\n```", report)
        restored = walkthrough.create_review(self.project, {"files": ["code.py"]})
        walkthrough.preserve_comments(excluded, restored)
        self.assertFalse(restored["unattached_comments"])
        self.assertEqual(restored["files"][0]["comments"][0]["diagrams"], [self.diagram])

    def test_old_comments_and_empty_diagram_list_remain_compatible(self):
        comment = dict(self.comment)
        comment.pop("diagrams")
        walkthrough.merge_comments(self.review, {"comments": [comment]})
        self.assertNotIn("diagrams", self.review["files"][0]["comments"][0])
        comment["diagrams"] = []
        walkthrough.merge_comments(self.review, {"comments": [comment]})
        self.assertEqual(self.review["files"][0]["comments"][0]["diagrams"], [])

    def test_invalid_diagram_payloads_do_not_replace_comments(self):
        walkthrough.merge_comments(self.review, {"comments": [self.comment]})
        for diagrams in (
            None, {}, "svg", [None], [{}],
            [{**self.diagram, "title": " "}],
            [{**self.diagram, "description": 1}],
            [{**self.diagram, "svg": ""}],
            [{**self.diagram, "extra": "value"}],
        ):
            with self.subTest(diagrams=diagrams):
                before = copy.deepcopy(self.review)
                with self.assertRaises(ValueError):
                    walkthrough.merge_comments(self.review, {"comments": [{**self.comment, "diagrams": diagrams}]})
                self.assertEqual(self.review, before)

    def test_executable_elements_and_external_references_are_rejected(self):
        invalid = [
            svg_with("<script>alert(1)</script>"),
            svg_with('<foreignObject><p>HTML</p></foreignObject>'),
            svg_with('<image href="https://example.com/image.png"/>'),
            svg_with('<use href="#arrow"/>'),
            svg_with('<a href="https://example.com"><text>link</text></a>'),
            svg_with('<animate attributeName="x"/>'),
            svg_with('<style>rect { fill: red; }</style>'),
            svg_with('<rect onclick="alert(1)"/>'),
            svg_with('<rect onload="alert(1)"/>'),
            svg_with('<rect style="fill:red"/>'),
            svg_with('<rect fill="url(https://example.com/paint.svg#p)"/>'),
            svg_with('<rect fill="url(&#x68;ttps://example.com/paint.svg#p)"/>'),
            svg_with('<rect fill="url(data:image/svg+xml;base64,AA)"/>'),
            svg_with('<rect fill="url(//example.com/paint.svg#p)"/>'),
            svg_with('<rect fill="u\\72l(https://example.com/paint.svg#p)"/>'),
            svg_with('<rect xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#arrow"/>'),
            '<!DOCTYPE svg [<!ENTITY content "expanded">]>' + svg_with("<text>&content;</text>"),
            '<?xml-stylesheet href="https://example.com/style.css"?>' + svg_with(),
            svg_with('<?instruction value="x"?>'),
            svg_with('<rect xmlns="http://www.w3.org/1999/xhtml"/>'),
        ]
        for svg in invalid:
            with self.subTest(svg=svg), self.assertRaisesRegex(ValueError, "SVG"):
                walkthrough.validate_svg(svg)

    def test_viewbox_root_and_optional_dimensions_are_validated(self):
        invalid = [
            "<svg viewBox=\"0 0 10 10\"/>",
            '<g xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"/>',
            svg_with(attributes=""), svg_with(attributes='viewBox="0 0 0 10"'),
            svg_with(attributes='viewBox="0 0 10 -1"'),
            svg_with(attributes='viewBox="0 0 1e309 10"'),
            svg_with(attributes='viewBox="0 0 NaN 10"'),
            svg_with(attributes='viewBox="０ ０ １００ ８０"'),
            svg_with(attributes='viewBox="0 0 10"'),
            svg_with(attributes='viewBox="0 0 10 10" width="0"'),
            svg_with(attributes='viewBox="0 0 10 10" height="Infinity"'),
        ]
        for svg in invalid:
            with self.subTest(svg=svg), self.assertRaises(ValueError):
                walkthrough.validate_svg(svg)
        walkthrough.validate_svg(svg_with(attributes='viewBox="-10,-20,1e2,80" width="100%" height="80px"'))

    def test_ids_are_unique_and_local_references_must_exist(self):
        for svg in (
            svg_with('<path marker-end="url(#missing)"/>'),
            svg_with('<g id="same"/><path id="same"/>'),
            svg_with('<path id="has space"/>'),
        ):
            with self.subTest(svg=svg), self.assertRaises(ValueError):
                walkthrough.validate_svg(svg)
        walkthrough.validate_svg(SVG)

    def test_invalid_svg_cli_merge_leaves_every_artifact_unchanged(self):
        walkthrough.merge_comments(self.review, {"comments": [self.comment]})
        walkthrough.write_review(self.review, self.output, copy_assets=True)
        before = {path.relative_to(self.output): path.read_bytes() for path in self.output.rglob("*") if path.is_file()}
        bad = {**self.diagram, "svg": svg_with('<script>alert(1)</script>')}
        payload = {"comments": [{**self.comment, "diagrams": [bad]}]}
        comments_path = self.root / "comments.json"
        comments_path.write_text(json.dumps(payload), encoding="utf-8")
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            status = walkthrough.main(["merge", str(self.output), "--comments", str(comments_path)])
        self.assertEqual(status, 2)
        self.assertIn("SVG", errors.getvalue())
        after = {path.relative_to(self.output): path.read_bytes() for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
