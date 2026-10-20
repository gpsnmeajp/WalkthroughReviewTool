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
include("  function isVisibleExcluded", "  function commentHashInfo");

let cases = 0;
function test(name, check) {
  check();
  cases++;
  console.log(`OK ${name}`);
}
const { makeScrollMaps, interpolateScroll, commentHashInfo, buildPrompt } = context;

test("対象外一覧はGitの除外対象とドットフォルダを省き、通常ファイルとドットファイルを残す", () => {
  for (const path of [".git", ".git/config", ".cache/data.json", "src/.hidden/config.py"]) {
    assert.equal(context.isVisibleExcluded({ path, selected: false }), false, path);
  }
  assert.equal(context.isVisibleExcluded({ path: "build/output.js", selected: false, git_ignored: true }), false);
  assert.equal(context.isVisibleExcluded({ path: "src/main.py", selected: true }), false);
  for (const path of ["src/unreviewed.py", ".gitignore", ".env", "src/.config"]) {
    assert.equal(context.isVisibleExcluded({ path, selected: false }), true, path);
    assert.equal(context.isVisibleExcluded({ path, selected: false, git_ignored: false }), true, path);
  }
});

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

test("本文変更なしの差分でも質問文に実行権限とrenameの情報を残す", () => {
  const summary = ["モード変更: 100644 → 100755", "ファイル名変更: before.py → after.py"];
  const prompt = buildPrompt("変更内容は？", { file: { ...file, diff_summary: summary }, side: "new", start: null, end: null });
  assert.ok(prompt.includes("本文以外の差分:"));
  for (const item of summary) assert.ok(prompt.includes(item));
  assert.ok(!buildPrompt("旧入力", { file, side: "new", start: null, end: null }).includes("本文以外の差分:"));
});

test("解説と行からの質問に対象の図・説明・SVG全文を安全なフェンスで含める", () => {
  const diagram = { title: "入力から出力", description: "入力を検証して結果を渡す。", svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><text x="4" y="25">```</text></svg>' };
  const withDiagram = { ...comment, diagrams: [diagram] };
  const diagramFile = { ...file, comments: [withDiagram, { ...comment, id: "unrelated", start_line: 8, end_line: 9, diagrams: [{ ...diagram, title: "範囲外の図" }] }] };
  for (const questionContext of [
    { file: diagramFile, side: "old", start: 3, end: 3, comment: withDiagram },
    { file: diagramFile, side: "old", start: 3, end: 3 },
  ]) {
    const prompt = buildPrompt("図の流れは？", questionContext);
    assert.ok(prompt.includes("解説 c1 の図: 入力から出力"));
    assert.ok(prompt.includes("説明: 入力を検証して結果を渡す。"));
    assert.ok(prompt.includes("````svg\n" + diagram.svg + "\n````"));
    assert.ok(!prompt.includes("範囲外の図"));
  }
  const fullPrompt = buildPrompt("全体は？", { file: diagramFile, side: "old", start: null, end: null });
  assert.ok(fullPrompt.includes("範囲外の図"));
});

function renderingHarness() {
  const created = [];
  const node = (tag, className, text) => {
    const element = {
      tagName: tag.toUpperCase(), className: className || "", textContent: text === undefined ? "" : String(text),
      children: [], attributes: {}, events: {}, dataset: {}, hidden: false, disabled: false,
      append(...children) { for (const child of children) child.parentElement = this; this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
      setAttribute(name, value) { this.attributes[name] = String(value); },
      addEventListener(name, callback) { this.events[name] = callback; },
      showModal() { this.open = true; },
      contains(child) { return child === this || this.children.some((item) => item.contains(child)); },
      closest(selector) {
        const tags = selector.toUpperCase().split(",");
        return tags.includes(this.tagName) ? this : this.parentElement?.closest(selector) || null;
      },
    };
    element.classList = { add: (name) => { element.className += ` ${name}`; } };
    Object.defineProperty(element, "innerHTML", { set() { throw new Error("図や差分情報をHTMLとして挿入しない"); } });
    created.push(element);
    return element;
  };
  const elements = Object.fromEntries(["diff-summary", "diff-summary-list", "diagram-dialog-title", "diagram-dialog-content", "diagram-dialog"].map((id) => [id, node("div")]));
  let refreshes = 0;
  Object.assign(context, {
    node, $: (id) => elements[id],
    requestAnimationFrame: (callback) => callback(),
    refreshAnchors: () => { refreshes++; },
  });
  include("  function renderDiffSummary", "  function renderCode");
  include("  function renderDiagram", "  function renderComments");
  return { elements, created, refreshes: () => refreshes };
}

test("対象外フィルタは一覧と一致し、対象・すべてフィルタは隠したファイルも保持する", () => {
  const harness = renderingHarness(), savedData = context.data, savedState = context.state;
  const inventory = [
    { path: "main.py", selected: true },
    { path: ".config/selected.py", selected: true, git_ignored: true },
    { path: "docs/readme.md", selected: false },
    { path: ".gitignore", selected: false },
    { path: ".git/config", selected: false },
    { path: "src/.cache/data.json", selected: false },
    { path: "build/generated.js", selected: false, git_ignored: true },
  ];
  harness.elements["file-search"] = { value: "" };
  harness.elements["file-tree"] = context.node("nav");
  harness.elements["file-tree"].querySelectorAll = () => [];
  context.data = { inventory };
  context.state = { filter: "excluded" };
  context.makeFileButton = (item) => {
    const button = context.node("button");
    button.dataset.filePath = item.path;
    return button;
  };
  include("  function renderTree", "  function renderExcluded");
  const paths = (element) => [...(element.dataset.filePath ? [element.dataset.filePath] : []), ...element.children.flatMap(paths)].sort();
  try {
    context.renderTree();
    assert.deepEqual(paths(harness.elements["file-tree"]), inventory.filter(context.isVisibleExcluded).map((item) => item.path).sort());
    context.state.filter = "selected";
    context.renderTree();
    assert.deepEqual(paths(harness.elements["file-tree"]), inventory.filter((item) => item.selected).map((item) => item.path).sort());
    context.state.filter = "all";
    context.renderTree();
    assert.deepEqual(paths(harness.elements["file-tree"]), inventory.map((item) => item.path).sort());
    harness.elements["file-search"].value = ".git/config";
    context.renderTree();
    assert.deepEqual(paths(harness.elements["file-tree"]), [".git/config"]);
  } finally {
    context.data = savedData;
    context.state = savedState;
  }
});

test("省略後の対象外一覧が空でも、全ファイルをレビュー済みとは表示しない", () => {
  const harness = renderingHarness();
  harness.elements["excluded-list"] = context.node("ul");
  context.excluded = [];
  include("  function renderExcluded", "  function renderOverview");
  context.renderExcluded();
  const empty = harness.elements["excluded-list"].children[0];
  assert.equal(empty.children[1].textContent, "表示する対象外ファイルはありません");
  assert.equal(empty.children[2].textContent, "全ファイルは左の「すべて」から確認できます。");
});

test("本文以外の差分は文字列で表示し、次の旧形式ファイルでは消す", () => {
  const harness = renderingHarness();
  context.renderDiffSummary({ diff_summary: ["権限: 100644 → 100755", "名前変更: <name>.py → b.py"] });
  assert.equal(harness.elements["diff-summary"].hidden, false);
  assert.deepEqual(harness.elements["diff-summary-list"].children.map((item) => item.textContent), ["権限: 100644 → 100755", "名前変更: <name>.py → b.py"]);
  context.renderDiffSummary({});
  assert.equal(harness.elements["diff-summary"].hidden, true);
  assert.equal(harness.elements["diff-summary-list"].children.length, 0);
});

test("図は説明付き画像として隔離し、読み込み後に連動を再計算して拡大できる", () => {
  const harness = renderingHarness();
  const diagram = { title: "構造 <1>", description: "AからBへの流れ", svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 300"><text x="10" y="30">A → B</text></svg>' };
  const figure = context.renderDiagram(diagram);
  assert.equal(figure.tagName, "FIGURE");
  assert.equal(figure.children[0].tagName, "FIGCAPTION");
  assert.equal(figure.children[0].children[0].textContent, diagram.title);
  assert.equal(figure.children[0].children[1].textContent, diagram.description);
  const image = harness.created.find((item) => item.tagName === "IMG");
  assert.equal(image.alt, diagram.description);
  assert.equal(image.src, "data:image/svg+xml;charset=utf-8," + encodeURIComponent(diagram.svg));
  assert.ok(!harness.created.some((item) => item.tagName === "SVG"));
  image.events.load();
  assert.equal(harness.refreshes(), 1);
  const enlarge = harness.created.find((item) => item.tagName === "BUTTON");
  enlarge.events.click();
  assert.equal(harness.elements["diagram-dialog"].open, true);
  const expandedFigure = harness.elements["diagram-dialog-content"].children[0];
  assert.ok(expandedFigure.className.includes("is-expanded"));
  assert.equal(expandedFigure.children.length, 2, "拡大画面からさらに拡大画面を開かない");
});

test("図の欠損と画像エラーでも説明を残し、スクロール位置を再計算する", () => {
  for (const svg of ["", "<svg/>"]) {
    const harness = renderingHarness();
    const figure = context.renderDiagram({ title: "欠損時の図", description: "処理の説明", svg });
    const image = harness.created.find((item) => item.tagName === "IMG");
    if (svg) image.events.error();
    assert.equal(image.hidden, true);
    assert.equal(figure.children[0].children[1].textContent, "処理の説明");
    assert.equal(figure.children[1].children[1].hidden, false);
    assert.ok(figure.children[1].children[1].textContent.includes("図を表示できません"));
    assert.equal(figure.children[2].disabled, true);
    assert.equal(harness.refreshes(), 1);
  }
});

function withCommentCard(commentOverride, check) {
  const harness = renderingHarness(), jumps = [];
  const savedNavigate = context.navigate, savedOpenQuestion = context.openQuestion;
  for (const id of ["comments-view", "detail-count", "detail-scroll", "sync-toggle"]) harness.elements[id] = context.node("div");
  let selection = null;
  Object.assign(context, {
    state: { file, rowMaps: { old: new Map([[3, 0]]), new: new Map([[3, 1]]) } },
    window: { getSelection: () => selection },
    renderMarkdown: (body, target) => target.append(context.node("p", "", body)),
    navigate: (...args) => jumps.push(args),
  });
  include("  function rowIndexFor", "  function renderDiffSummary");
  include("  function renderComments", "  function activateComment");
  context.renderComments({ ...file, comments: [{ ...comment, hash_status: "verified", ...commentOverride }] });
  const card = harness.elements["comments-view"].children[0];
  try {
    check({ ...harness, card, jumps, select: (value) => { selection = value; } });
  } finally {
    context.navigate = savedNavigate;
    context.openQuestion = savedOpenQuestion;
  }
}

test("解説カードの本文・見出し・余白から対応する旧行とカードへ移動できる", () => {
  withCommentCard({}, ({ card, jumps }) => {
    const body = card.children.find((item) => item.className === "markdown");
    for (const target of [card, card.children.find((item) => item.tagName === "H3"), body.children[0]]) card.events.click({ target });
    assert.equal(jumps.length, 3);
    for (const args of jumps) assert.deepEqual(args, [file.path, 3, "old", 3, "c1"]);
    assert.equal(card.dataset.navigable, "true");
    assert.ok(!card.children.some((item) => item.className === "hash-verified"));
  });
});

test("カード内の質問・リンク・図拡大・選択中の文章ではカードのジャンプを実行しない", () => {
  withCommentCard({ diagrams: [{ title: "図", description: "説明", svg: "<svg/>" }] }, ({ card, created, jumps, select }) => {
    for (const button of created.filter((item) => item.tagName === "BUTTON" && card.contains(item))) {
      const child = context.node("span");
      button.append(child);
      card.events.click({ target: child });
    }
    const link = context.node("a"), linkText = context.node("strong");
    link.append(linkText);
    card.append(link);
    card.events.click({ target: linkText });
    const body = card.children.find((item) => item.className === "markdown");
    select({ isCollapsed: false, anchorNode: body.children[0], focusNode: body.children[0] });
    card.events.click({ target: body });
    assert.equal(jumps.length, 0);
    select({ isCollapsed: true, anchorNode: body, focusNode: body });
    card.events.click({ target: card, defaultPrevented: true });
    assert.equal(jumps.length, 0);
    const range = created.find((item) => item.className === "comment-range");
    assert.equal(range.disabled, false);
    assert.equal(range.attributes["aria-label"], "旧 L3 のコードへ移動");
    range.events.click();
    assert.equal(jumps.length, 1, "既存の行番号ボタンはキーボード操作の入口として残す");
  });
});

test("参照行が消えたカードはジャンプせず、照合警告と質問を残す", () => {
  withCommentCard({ range_status: "missing", hash_status: "mismatch" }, ({ card, created }) => {
    assert.equal(card.events.click, undefined);
    assert.equal(card.dataset.navigable, undefined);
    assert.equal(created.find((item) => item.className === "comment-range").disabled, true);
    assert.ok(card.children.some((item) => item.className === "hash-notice mismatch"));
    assert.ok(card.children.some((item) => item.className === "reference-warning"));
    let question;
    context.openQuestion = (value) => { question = value; };
    created.find((item) => item.tagName === "BUTTON" && item.textContent === "この解説に質問 ↗").events.click();
    assert.equal(question.comment.id, "c1");
  });
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
  include('  $("ask-file").addEventListener', '  $("close-diagram").addEventListener');
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
