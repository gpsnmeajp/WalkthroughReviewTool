"""開発用のimport入口。差分処理の実体は配布スキル内に一つだけ置く。"""

import importlib.util
from pathlib import Path
import sys


_path = Path(__file__).resolve().parents[1] / "skills/walkthrough-review/scripts/diff_parser.py"
_spec = importlib.util.spec_from_file_location("_walkthrough_review_diff", _path)
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
sys.modules[__name__] = _module
