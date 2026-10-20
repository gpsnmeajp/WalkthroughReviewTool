/* ブラウザー不要のUIロジック検証。node tests/ui_logic.test.cjs で実行する。 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { URLSearchParams } = require("node:url");
const root = path.resolve(__dirname, "../..");
const source = fs.readFileSync(path.join(root, "skills/walkthrough-review/assets/ui/app.js"), "utf8");
new vm.Script(source, { filename: "assets/app.js" });

const context = vm.createContext({
  data: { title: "レビュー", project_root: "C:/project", mode: "diff", generated_at: "2026-10-21T00:00:00Z" },
});
function include(start, end) {
  const begin = source.indexOf(start), finish = source.indexOf(end, begin);
  assert.ok(begin >= 0 && finish > begin, `テスト対象の関数が見つからない: ${start}`);
  vm.runInContext(source.slice(begin, finish), context);
}
include("  function makeScrollMaps", "  function refreshAnchors");
include("  function commentHashInfo", "  function renderHashWarnings");
include("  const rangeLabel", "  const announce");
include("  function buildPrompt", "  async function copyPrompt");
include("  function activateComment", "  function makeScrollMaps");
include("  function chooseCommentIndex", "  function openQuestion");

let cases = 0;
function test(name, check) {
  check();
  cases++;
  console.log(`OK ${name}`);
}
const { makeScrollMaps, interpolateScroll, commentHashInfo, buildPrompt } = context;

test("カードのアンカーが到達不能でも、右最下部からコード最下部へ移動する", () => {
  const maps = makeScrollMaps([{ code: 100, detail: 100 }, { code: 700, detail: 500 }, { code: 900, detail: 900 }], 1000, 600);
  assert.equal(interpolateScroll(maps.detail, 600), 1000);
  assert.equal(interpolateScroll(maps.code, 1000), 600);
  assert.equal(interpolateScroll(maps.detail, 0), 0);
  assert.equal(interpolateScroll(maps.code, 0), 0);
  assert.equal(maps.detail.filter((point) => point.from === 600).length, 1);
});

test("同じ行から始まる複数カードを読む間、コードは同じ位置に留まる", () => {
  const maps = makeScrollMaps([{ code: 100, detail: 80 }, { code: 100, detail: 300 }, { code: 500, detail: 550 }], 800, 700);
  assert.equal(interpolateScroll(maps.detail, 80), 100);
  assert.equal(interpolateScroll(maps.detail, 150), 100);
  assert.equal(interpolateScroll(maps.detail, 300), 100);
  assert.equal(interpolateScroll(maps.code, 100), 80);
  assert.equal(maps.code.filter((point) => point.from === 100).length, 1);
});

test("コード末尾に対応する複数カードも、右スクロールで最後まで読める", () => {
  const maps = makeScrollMaps([{ code: 50, detail: 100 }, { code: 900, detail: 300 }, { code: 1000, detail: 700 }], 500, 900);
  assert.equal(interpolateScroll(maps.detail, 300), 500);
  assert.equal(interpolateScroll(maps.detail, 600), 500);
  assert.equal(interpolateScroll(maps.detail, 900), 500);
  assert.equal(interpolateScroll(maps.code, 500), 900);
});

test("空・スクロール不能なペインでNaNや範囲外の位置を返さない", () => {
  const empty = makeScrollMaps([], 0, 0);
  assert.equal(interpolateScroll(empty.code, 50), 0);
  assert.equal(interpolateScroll(empty.detail, 50), 0);
  const shortCode = makeScrollMaps([{ code: 200, detail: 300 }], 0, 800);
  assert.equal(interpolateScroll(shortCode.detail, 500), 0);
  const noComments = makeScrollMaps([], 1000, 500);
  assert.equal(interpolateScroll(noComments.code, 500), 250);
});

test("双方向の位置は単調増加し、必ず各ペインの範囲内に収まる", () => {
  const maps = makeScrollMaps([{ code: 0, detail: 12 }, { code: 200, detail: 180 }, { code: 200, detail: 400 }, { code: 1500, detail: 900 }], 1000, 600);
  for (const [direction, maximum, targetMaximum] of [["code", 1000, 600], ["detail", 600, 1000]]) {
    let previous = -1;
    for (let value = 0; value <= maximum; value++) {
      const result = interpolateScroll(maps[direction], value);
      assert.ok(result >= previous && result >= 0 && result <= targetMaximum);
      previous = result;
    }
  }
});

const file = {
  path: "src/a%20 file.py",
  sha256: "original-byte-hash",
  code_hashes: { new: "new-code-hash", old: "old-code-hash" },
  rows: [
    { old: 3, new: null, kind: "removed", text: "```old```", newline: false },
    { old: null, new: 3, kind: "added", text: "new_value = 1", newline: true },
  ],
  comments: [],
};
const comment = {
  id: "c1", file: file.path, start_line: 3, end_line: 3, side: "old",
  title: "削除の説明", body: "元の設計意図", code_hash: "expected-hash",
  actual_code_hash: "old-code-hash", hash_status: "mismatch", range_status: "valid",
};

test("Hash状態はPythonの検証結果を使い、古いコメントは未照合として扱う", () => {
  assert.equal(commentHashInfo(comment, file).status, "mismatch");
  assert.equal(commentHashInfo({}, file).status, "missing");
  assert.equal(commentHashInfo({ ...comment, hash_status: "verified" }, file).status, "verified");
});

test("旧行質問にエンコード済み参照・Hash比較・改行情報・安全なフェンスを含む", () => {
  const prompt = buildPrompt("理由は？", { file, side: "old", start: 3, end: 3, comment });
  assert.ok(prompt.includes("review:src/a%2520%20file.py#O3"));
  assert.ok(prompt.includes("コード不一致"));
  assert.ok(prompt.includes("作成時コードHash: expected-hash"));
  assert.ok(prompt.includes("表示中コードHash: old-code-hash"));
  assert.ok(prompt.includes("\\ No newline at end of file"));
  assert.ok(prompt.includes("````\n- [旧:3 新:—] ```old```\n\\ No newline at end of file\n````"));
  assert.ok(!prompt.includes("new_value"));
  assert.ok(prompt.includes("元の設計意図"));
});

test("消えた参照範囲の質問に無関係なコードを入れない", () => {
  const prompt = buildPrompt("説明を確認したい", { file, side: "old", start: 3, end: 3, comment: { ...comment, range_status: "missing" } });
  assert.ok(prompt.includes("元の参照行範囲が存在しない"));
  assert.ok(!prompt.includes("```old```"));
  assert.ok(prompt.includes("元の設計意図"));
});

test("旧行の正規化済み文字列が別途ある場合は旧側の質問で使用する", () => {
  const specialFile = { ...file, rows: [{ old: 1, new: 2, kind: "context", text: "\ufefftext", old_text: "text", newline: true }] };
  const prompt = buildPrompt("旧側を確認", { file: specialFile, side: "old", start: 1, end: 1 });
  assert.ok(prompt.includes("[旧:1 新:2] text"));
  assert.ok(!prompt.includes("\ufefftext"));
});

function navigationHarness() {
  function classes() {
    const values = new Set();
    return {
      add: (value) => values.add(value),
      remove: (value) => values.delete(value),
      contains: (value) => values.has(value),
      toggle(value, active) { if (active) values.add(value); else values.delete(value); },
    };
  }
  const code = { scrollTop: 0, getBoundingClientRect: () => ({ top: 0 }) };
  const detail = { scrollTop: 0, getBoundingClientRect: () => ({ top: 0 }) };
  const rows = [
    { old: 1, new: 1, kind: "context", text: "first" },
    { old: 2, new: null, kind: "removed", text: "removed" },
    { old: null, new: 2, kind: "added", text: "added" },
    { old: 3, new: 3, kind: "context", text: "last" },
  ];
  const entries = [["old-first", "old"], ["new-first", "new"], ["new-second", "new"]].map(([id, side], index) => ({
    comment: { id }, side, start: 0, end: 3, invalidRange: false,
    card: { classList: classes(), getBoundingClientRect: () => ({ top: 80 + index * 220 - detail.scrollTop }) },
  }));
  const state = {
    file: { path: "file.py", rows }, path: "file.py", commentEntries: entries, activeComment: -1,
    rowElements: rows.map((_, index) => ({ classList: classes(), getBoundingClientRect: () => ({ top: index * 25 - code.scrollTop }) })),
  };
  const elements = { "code-scroll": code, "detail-scroll": detail, "selection-label": {} };
  let hash = "";
  const location = {};
  Object.defineProperty(location, "hash", {
    get: () => hash,
    set: (value) => { hash = value.startsWith("#") ? value : "#" + value; },
  });
  Object.assign(context, {
    state, location, URLSearchParams, performance: { now: () => 0 },
    $: (id) => elements[id],
    rowIndexFor: (line, side) => rows.findIndex((row) => row[side] === line),
    announce() {},
    show() { throw new Error("同じファイルの範囲移動で再描画しない"); },
  });
  return { state, elements, code, detail, location };
}

test("同じ行を説明する2枚目のカードをhashchange後も選択・表示し続ける", () => {
  const harness = navigationHarness();
  context.navigate("file.py", 1, "new", 3, "new-second");
  assert.equal(new URLSearchParams(harness.location.hash.slice(1)).get("comment"), "new-second");
  context.fromHash();
  assert.equal(harness.state.activeComment, 2);
  assert.equal(harness.detail.scrollTop, 500);
  assert.ok(harness.state.commentEntries[2].card.classList.contains("active"));
  assert.ok(!harness.state.commentEntries[1].card.classList.contains("active"));
  harness.detail.scrollTop = 0;
  context.navigate("file.py", 1, "new", 3, "new-second");
  assert.equal(harness.state.activeComment, 2);
  assert.equal(harness.detail.scrollTop, 500, "同じhashの再選択でも2枚目へ移動する");
});

test("重複範囲の後のカードを選んだとき、先に一致するカードへ戻らない", () => {
  const harness = navigationHarness();
  harness.state.commentEntries[2].start = 2;
  context.navigate("file.py", 2, "new", 3, "new-second");
  context.fromHash();
  assert.equal(harness.state.activeComment, 2);
  assert.equal(harness.detail.scrollTop, 500);
});

test("新行のクリックは旧側カードを選択せず、旧行・新行の強調を分離する", () => {
  const harness = navigationHarness();
  context.focusRange(2, "new", 2, true);
  assert.equal(harness.state.activeComment, 1);
  assert.ok(harness.state.rowElements[2].classList.contains("highlighted"));
  assert.ok(!harness.state.rowElements[1].classList.contains("active-range"));
  context.focusRange(1, "old", 3, true, "old-first");
  assert.equal(harness.state.activeComment, 0);
  assert.ok(harness.state.rowElements[1].classList.contains("highlighted"));
  assert.ok(harness.state.rowElements[1].classList.contains("active-range"));
  assert.ok(!harness.state.rowElements[2].classList.contains("highlighted"));
  assert.ok(!harness.state.rowElements[2].classList.contains("active-range"));
});

test("削除ファイルの質問ボタンは旧版を指定し、質問文に両版Hashを含める", () => {
  const harness = navigationHarness();
  let onClick, question;
  harness.elements["ask-file"] = { addEventListener: (_, callback) => { onClick = callback; } };
  context.inventoryMap = new Map([["file.py", { status: "deleted" }]]);
  context.openQuestion = (value) => { question = value; };
  include('  $("ask-file").addEventListener', '  $("close-question").addEventListener');
  onClick();
  assert.equal(question.side, "old");
  const deletedFile = { ...file, rows: [file.rows[0]], code_hashes: { new: "empty-hash", old: "deleted-code-hash" } };
  const prompt = buildPrompt("削除理由は？", { ...question, file: deletedFile });
  assert.ok(prompt.includes("表示中の新版Hash: empty-hash"));
  assert.ok(prompt.includes("表示中の旧版Hash: deleted-code-hash"));
  assert.ok(prompt.includes("```old```"));
});

test("スキップリンクで開いているファイルを維持したままメインへフォーカスする", () => {
  const harness = navigationHarness();
  let onClick, focused = false, prevented = false;
  harness.location.hash = "file=file.py";
  harness.elements["skip-link"] = { addEventListener: (_, callback) => { onClick = callback; } };
  harness.elements["main-panel"] = { focus: () => { focused = true; } };
  include('  $("skip-link").addEventListener', '  document.querySelectorAll("[data-filter]")');
  onClick({ preventDefault: () => { prevented = true; } });
  assert.ok(prevented && focused);
  assert.equal(harness.location.hash, "#file=file.py");
});

test("警告開閉によるペイン高さ変更もスクロール位置の再計算を通知する", () => {
  const harness = navigationHarness();
  harness.elements["comments-view"] = {};
  const observed = [];
  let onResize, refreshed = 0;
  context.window = { ResizeObserver: true };
  context.ResizeObserver = class {
    constructor(callback) { onResize = callback; }
    observe(element) { observed.push(element); }
  };
  context.requestAnimationFrame = (callback) => callback();
  context.refreshAnchors = () => { refreshed++; };
  include("  if (window.ResizeObserver)", "  renderOverview();");
  assert.ok(observed.includes(harness.elements["code-scroll"]));
  assert.ok(observed.includes(harness.elements["detail-scroll"]));
  onResize();
  assert.equal(refreshed, 1);
});

test("テンプレートにJavaScriptが参照するIDの欠落・重複がない", () => {
  const html = fs.readFileSync(path.join(root, "skills/walkthrough-review/assets/templates/index.html"), "utf8");
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(ids.length, new Set(ids).size);
  for (const match of source.matchAll(/\$\("([^"]+)"\)/g)) assert.ok(ids.includes(match[1]), `IDがない: ${match[1]}`);
});

console.log(`${cases} UI logic checks passed`);
