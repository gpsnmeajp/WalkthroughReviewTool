"""開発用の呼び出しパスから、スキル同梱の準備処理を実行する入口。

実装はskills/walkthrough-review/scripts/prepare_review.pyの一か所だけに置く。
import時もそのモジュールを返し、既存のPython呼び出し元と同じAPIを提供する。
"""

import importlib.util
from pathlib import Path
import sys


IMPLEMENTATION = (
    Path(__file__).resolve().parents[2]
    / "skills" / "walkthrough-review" / "scripts" / "prepare_review.py"
)
spec = importlib.util.spec_from_file_location("_walkthrough_review_prepare", IMPLEMENTATION)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(module.main())
else:
    sys.modules[__name__] = module
