"""対象外差分の追加確保メモリと、未解説範囲・確認資料の生成時間を測る。"""
import gc
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter
import tracemalloc

DEV = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEV))
import walkthrough


def timed(call):
    start = perf_counter()
    result = call()
    return result, round(perf_counter() - start, 6)


def reference_uncovered(file):
    """改善前と同じ、各行で全解説を探索する比較用の実装。"""
    result = {"old": [], "new": []}
    for row in walkthrough.iter_rows(file):
        if not any(c.get("range_status") != "missing" and row[c.get("side", "new")] is not None and
                   c["start_line"] <= row[c.get("side", "new")] <= c["end_line"] for c in file["comments"]):
            side = "new" if row["new"] is not None else "old"
            result[side].append(row[side])
    return result


def main():
    parent = DEV / ".test-tmp"
    parent.mkdir(exist_ok=True)
    measurements = {"python": sys.version.split()[0], "unselected_patch": [], "coverage": []}
    with tempfile.TemporaryDirectory(prefix="scaling-", dir=parent) as temporary:
        project = Path(temporary)
        line = "value = '" + "x" * 84 + "'\n"
        body = line * 10000
        patches = []
        for index in range(6):
            name = f"file-{index}.py"
            (project / name).write_bytes(body.encode())
            patches.append(f"--- a/{name}\n+++ b/{name}\n@@ -1,10000 +1,10000 @@\n" + (" " + line) * 10000)
        for count in (1, 6):
            patch = "".join(patches[:count])
            gc.collect()
            tracemalloc.start()
            try:
                result, seconds = timed(lambda: walkthrough.create_review(project, {"files": []}, patch))
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            assert result["files"] == []
            measurements["unselected_patch"].append({"files": count, "body_bytes": len(body.encode()) * count,
                                                       "peak_additional_bytes": peak, "seconds": seconds})
        for lines, cards in ((10000, 500), (40000, 2000)):
            (project / "coverage.py").write_bytes(("value = 1\n" * lines).encode())
            review = walkthrough.create_review(project, {"files": ["coverage.py"]})
            file = review["files"][0]
            file["comments"] = [{"id": f"c{index}", "title": "説明", "body": "処理を確認する。", "side": "new",
                                 "start_line": index * 20 + 1, "end_line": index * 20 + 5} for index in range(cards)]
            actual, seconds = timed(lambda: walkthrough.uncovered_lines(file))
            expected, reference_seconds = timed(lambda: reference_uncovered(file))
            assert actual == expected
            _, document_seconds = timed(lambda: walkthrough.self_review(review))
            measurements["coverage"].append({"lines": lines, "comments": cards, "range_seconds": seconds,
                                              "reference_seconds": reference_seconds, "self_review_seconds": document_seconds})
    output = DEV / "output/scaling/benchmark.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(measurements, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(measurements, ensure_ascii=False, indent=2))
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
