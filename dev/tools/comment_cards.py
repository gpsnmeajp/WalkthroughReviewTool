"""開発用: スキル内のカード作成ツールをそのまま呼び出す。"""

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[2] / "skills/walkthrough-review/scripts/comment_cards.py"
sys.path.insert(0, str(_path.parent))
_spec = importlib.util.spec_from_file_location("_walkthrough_comment_cards", _path)
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(_module.main())
else:
    sys.modules[__name__] = _module
