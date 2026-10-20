"""開発用CLI/importを、自己完結したスキル内の唯一の実装へ接続する。"""

import importlib.util
from pathlib import Path
import sys


_path = Path(__file__).resolve().parents[1] / "skills/walkthrough-review/scripts/walkthrough.py"
_spec = importlib.util.spec_from_file_location("_walkthrough_review_cli", _path)
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
if __name__ == "__main__":
    raise SystemExit(_module.main())
else:
    # import先も実体に統一し、呼び出し側のmockや設定が実装へ反映されるようにする。
    sys.modules[__name__] = _module
