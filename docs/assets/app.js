/* データは埋め込みJSONだけから読み取る。サーバーも通信も必要ない。 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const escapeHTML = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  let data;
  try {
    data = JSON.parse($("review-data").textContent);
    if (data.schema_version !== 1 || !Array.isArray(data.files) || !Array.isArray(data.inventory)) throw new Error("未対応のデータ形式です。");
  } catch (error) {
    const alert = node("div", "fatal-error", `レビューデータを読み込めませんでした。HTMLを再生成してください。\n${error.message}`);
    alert.setAttribute("role", "alert");
    document.body.replaceChildren(alert);
    return;
  }

  const fileMap = new Map(data.files.map((file) => [file.path, file]));
  const inventoryMap = new Map(data.inventory.map((item) => [item.path, item]));
  const excluded = data.inventory.filter(isVisibleExcluded);
  const state = { file: null, path: null, filter: "selected", sync: true, source: null, anchors: [], scrollMaps: null, commentEntries: [], activeComment: -1, question: null, rowElements: [], rowMaps: { old: new Map(), new: new Map() }, suppressUntil: 0, frame: null };
  const statuses = { file: "通常ファイル", deleted: "削除済み", symlink: "シンボリックリンク", unreadable: "読み取り不可", error: "読み取りエラー", binary: "バイナリ", directory: "ディレクトリ", other: "特殊ファイル" };
  const statusLabel = (status) => statuses[status] || status || "ファイル";
  const prettySize = (size) => typeof size !== "number" ? "サイズ不明" : size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;
  const rangeLabel = (start, end, side = "new") => `${side === "old" ? "旧 " : ""}L${start}${end > start ? `–${end}` : ""}`;
  const announce = (text) => { $("live-status").textContent = text; };

  function isVisibleExcluded(item) {
    const parts = item.path.split("/");
    // 管理用フォルダやGitの除外対象は全一覧に残し、確認したい対象外一覧からだけ隠す。
    return !item.selected && !item.git_ignored && !parts.includes(".git") && !parts.slice(0, -1).some((part) => part.startsWith("."));
  }

  function commentHashInfo(comment, file) {
    const status = ["verified", "mismatch", "missing"].includes(comment.hash_status) ? comment.hash_status : "missing";
    return {
      status,
      label: { verified: "コード照合済み", mismatch: "コード不一致", missing: "コード未照合" }[status],
      expected: comment.code_hash || "",
      actual: comment.actual_code_hash || (file.code_hashes || {})[comment.side || "new"] || "",
    };
  }

  function renderHashWarnings() {
    const comments = data.files.flatMap((file) => (file.comments || []).map((comment) => ({ comment, file })));
    const mismatch = comments.filter(({ comment, file }) => commentHashInfo(comment, file).status === "mismatch").length;
    const missing = comments.filter(({ comment, file }) => commentHashInfo(comment, file).status === "missing").length;
    const unattached = (data.unattached_comments || []).length;
    const warnings = [...(data.hash_warnings || [])];
    if (!warnings.length) {
      for (const { comment, file } of comments) {
        const hash = commentHashInfo(comment, file);
        if (hash.status !== "verified") warnings.push(`${file.path} / ${comment.id}: ${hash.label}`);
        if (comment.range_status === "missing") warnings.push(`${file.path} / ${comment.id}: 参照先の行範囲が存在しません`);
        if (comment.link_warning) warnings.push(`${file.path} / ${comment.id}: ${comment.link_warning}`);
      }
    }
    $("hash-alert").hidden = !warnings.length && !unattached;
    $("hash-alert-title").textContent = mismatch ? "解説作成時からコードが変わっています" : "解説とコードの照合に確認事項があります";
    $("hash-alert-summary").textContent = [mismatch ? `不一致 ${mismatch} 件` : "", missing ? `未照合 ${missing} 件` : "", unattached ? `対象外の解説 ${unattached} 件` : "", !mismatch && !missing && !unattached ? `確認事項 ${warnings.length} 件` : ""].filter(Boolean).join(" · ");
    $("hash-warning-list").replaceChildren(...warnings.map((warning) => node("li", "", warning)));
    $("unattached-warning").hidden = !unattached;
  }

  /* MarkdownのHTML記法は文字として表示し、出力も許可リストで検査する。 */
  if (window.marked) {
    window.marked.use({ renderer: { html(token) { return escapeHTML(typeof token === "string" ? token : token.text || token.raw || ""); } } });
  }
  function renderMarkdown(text, target) {
    target.replaceChildren();
    if (!window.marked) {
      const plain = node("div", "", text || "");
      plain.style.whiteSpace = "pre-wrap";
      target.append(plain);
      return;
    }
    const template = document.createElement("template");
    template.innerHTML = window.marked.parse(String(text || ""), { gfm: true, breaks: false });
    const allowed = new Set(["P", "BR", "HR", "H1", "H2", "H3", "H4", "H5", "H6", "STRONG", "B", "EM", "I", "DEL", "S", "UL", "OL", "LI", "BLOCKQUOTE", "PRE", "CODE", "A", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD", "INPUT"]);
    for (const element of [...template.content.querySelectorAll("*")]) {
      if (!allowed.has(element.tagName)) {
        if (element.tagName === "IMG") element.replaceWith(document.createTextNode(element.getAttribute("alt") || "[画像]"));
        else element.replaceWith(...element.childNodes);
        continue;
      }
      const href = element.getAttribute("href");
      const title = element.getAttribute("title");
      const checked = element.hasAttribute("checked");
      const type = element.getAttribute("type");
      const start = element.getAttribute("start");
      for (const attribute of [...element.attributes]) element.removeAttribute(attribute.name);
      if (element.tagName === "INPUT") {
        if (type !== "checkbox") { element.remove(); continue; }
        element.type = "checkbox"; element.disabled = true; element.checked = checked;
        element.setAttribute("aria-label", checked ? "完了" : "未完了");
      }
      if (element.tagName === "OL" && /^\d+$/.test(start || "")) element.setAttribute("start", start);
      if (element.tagName === "A" && href) {
        if (/^review:/i.test(href)) {
          element.setAttribute("href", href);
          element.dataset.reviewLink = "true";
        } else if (/^https?:\/\//i.test(href)) {
          element.setAttribute("href", href);
          element.setAttribute("target", "_blank");
          element.setAttribute("rel", "noopener noreferrer");
        } else if (href.startsWith("#")) element.setAttribute("href", href);
        if (title) element.setAttribute("title", title);
      }
    }
    target.append(template.content);
  }

  function makeFileButton(item) {
    const button = node("button", `file-button${item.selected ? "" : " is-excluded"}`);
    button.type = "button";
    button.dataset.filePath = item.path;
    button.setAttribute("title", `${item.path} · ${item.selected ? "レビュー対象" : "対象外"} · ${statusLabel(item.status)}`);
    if (state.path === item.path) button.setAttribute("aria-current", "page");
    const extension = item.path.split(".").pop().toLowerCase();
    const icon = node("span", "file-icon", ["js", "ts", "jsx", "tsx"].includes(extension) ? "{}" : extension === "py" ? "py" : extension === "md" ? "≡" : "▤");
    icon.setAttribute("aria-hidden", "true");
    const flag = node("span", `file-flag${item.selected ? "" : " excluded"}`);
    flag.setAttribute("aria-label", item.selected ? "レビュー対象" : "対象外");
    button.append(icon, node("span", "file-name", item.path.split("/").pop()), flag);
    button.addEventListener("click", () => navigate(item.path));
    return button;
  }

  function renderTree() {
    const query = $("file-search").value.trim().toLocaleLowerCase();
    const folderStates = new Map([...$("file-tree").querySelectorAll("details")].map((item) => [item.dataset.folder, item.open]));
    const root = { folders: new Map(), files: [] };
    let count = 0;
    for (const item of data.inventory) {
      if (state.filter === "selected" && !item.selected || state.filter === "excluded" && !isVisibleExcluded(item)) continue;
      if (query && !item.path.toLocaleLowerCase().includes(query)) continue;
      count++;
      const parts = item.path.split("/");
      let branch = root;
      for (const part of parts.slice(0, -1)) {
        if (!branch.folders.has(part)) branch.folders.set(part, { folders: new Map(), files: [] });
        branch = branch.folders.get(part);
      }
      branch.files.push(item);
    }
    const folderCount = (branch) => branch.files.length + [...branch.folders.values()].reduce((sum, child) => sum + folderCount(child), 0);
    function list(branch, prefix, depth) {
      const ul = node("ul", "tree-list");
      for (const [name, child] of [...branch.folders.entries()].sort(([a], [b]) => a.localeCompare(b))) {
        const li = node("li"), details = node("details", "tree-folder"), summary = node("summary");
        const path = prefix ? `${prefix}/${name}` : name;
        details.dataset.folder = path;
        details.open = query ? true : folderStates.has(path) ? folderStates.get(path) : depth < 2;
        summary.append(node("span", "folder-name", name), node("span", "folder-count", folderCount(child)));
        details.append(summary, list(child, path, depth + 1));
        li.append(details); ul.append(li);
      }
      for (const item of branch.files.sort((a, b) => a.path.localeCompare(b.path))) {
        const li = node("li"); li.append(makeFileButton(item)); ul.append(li);
      }
      return ul;
    }
    $("file-tree").replaceChildren(count ? list(root, "", 0) : node("p", "tree-empty", "一致するファイルがありません。"));
  }

  function renderExcluded() {
    const list = $("excluded-list");
    list.replaceChildren();
    for (const item of excluded) {
      const li = node("li"), button = node("button");
      button.type = "button";
      const path = node("span", "excluded-path", item.path);
      path.append(node("small", "", `${statusLabel(item.status)} · ${prettySize(item.size)}`));
      button.append(node("span", "file-icon", "▤"), path, node("span", "exclude-arrow", "↗"));
      button.addEventListener("click", () => navigate(item.path));
      li.append(button); list.append(li);
    }
    if (!excluded.length) {
      const empty = node("li", "empty-state");
      empty.append(node("span", "empty-icon", "✓"), node("strong", "", "表示する対象外ファイルはありません"), node("p", "", "全ファイルは左の「すべて」から確認できます。"));
      list.append(empty);
    }
  }

  function renderOverview() {
    $("review-title").textContent = data.title || "ウォークスルーレビュー";
    document.title = `${data.title || data.project_name} — Walkthrough Review`;
    $("project-name").textContent = data.project_name || "PROJECT";
    $("inventory-count").textContent = data.inventory.length;
    $("mode-badge").textContent = data.mode === "diff" ? "差分レビュー" : "全体レビュー";
    for (const [value, label, className] of [[data.inventory.length, "走査したファイル", ""], [data.files.length, "レビュー対象", " target"], [excluded.length, "対象外一覧", ""]]) {
      const stat = node("div", `stat${className}`);
      stat.append(node("strong", "", value), node("span", "", label));
      $("project-stats").append(stat);
    }
    if (data.overview) renderMarkdown(data.overview, $("overview-content"));
    else $("overview-content").append(node("p", "empty-overview", "プロジェクトの解説はまだ追加されていません。対象ファイルのコードは、左の一覧から確認できます。"));
    const warnings = data.warnings || [];
    if (warnings.length) {
      $("warnings-panel").hidden = false;
      $("warnings-title").textContent = `確認事項 ${warnings.length} 件`;
      warnings.forEach((warning) => $("warnings-list").append(node("li", "", typeof warning === "string" ? warning : JSON.stringify(warning))));
    }
    const date = data.generated_at ? new Date(data.generated_at) : null;
    const dateLabel = date && !Number.isNaN(date.getTime()) ? new Intl.DateTimeFormat("ja-JP", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Tokyo" }).format(date) + " JST" : data.generated_at || "";
    $("generation-meta").textContent = `${data.project_root || data.project_name} · 生成 ${dateLabel}`;
  }

  /* トークンのspanを行境界で閉じて開き直し、複数行文字列の色を保つ。 */
  function highlightedLines(file) {
    const aliases = { js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript", py: "python", html: "markup", xml: "markup", svg: "markup", sh: "bash", shell: "bash", yml: "yaml" };
    const language = aliases[file.language] || file.language;
    const grammar = window.Prism && window.Prism.languages[language];
    if (!grammar) return file.rows.map((row) => escapeHTML(row.text));
    try {
      function highlightSide(side) {
        const rows = file.rows.filter((row) => row[side] != null);
        const html = window.Prism.highlight(rows.map((row) => side === "old" ? row.old_text ?? row.text : row.text).join("\n"), grammar, language);
        const open = [], result = new Map();
        html.split("\n").forEach((line, index) => {
          const prefix = open.join("");
          for (const tag of line.match(/<\/?span\b[^>]*>/g) || []) {
            if (tag.startsWith("</")) open.pop(); else open.push(tag);
          }
          if (rows[index]) result.set(rows[index][side], prefix + line + "</span>".repeat(open.length));
        });
        return result;
      }
      // 削除行を変更後コードに混ぜず、それぞれの版で字句解析する。
      const current = highlightSide("new");
      const previous = file.rows.some((row) => row.kind === "removed") ? highlightSide("old") : new Map();
      return file.rows.map((row) => (row.new == null ? previous.get(row.old) : current.get(row.new)) || escapeHTML(row.text));
    } catch (_) { return file.rows.map((row) => escapeHTML(row.text)); }
  }

  function rowIndexFor(line, side = "new") {
    if (!state.file) return -1;
    return state.rowMaps[side].get(Number(line)) ?? -1;
  }
  function codeRange(comment) {
    const side = comment.side || "new";
    const start = rowIndexFor(comment.start_line, side);
    const end = rowIndexFor(comment.end_line || comment.start_line, side);
    return { side, start, end: end < 0 ? start : end };
  }

  function renderDiffSummary(file) {
    const summary = Array.isArray(file.diff_summary) ? file.diff_summary : [];
    $("diff-summary").hidden = !summary.length;
    $("diff-summary-list").replaceChildren(...summary.map((item) => node("li", "", item)));
  }

  function renderCode(file) {
    const fragment = document.createDocumentFragment(), highlighted = highlightedLines(file), isDiff = data.mode === "diff";
    renderDiffSummary(file);
    state.rowElements = [];
    state.rowMaps = { old: new Map(), new: new Map() };
    file.rows.forEach((row, index) => {
      for (const side of ["old", "new"]) if (row[side] != null) state.rowMaps[side].set(row[side], index);
      const element = node("div", `code-row ${row.kind}${isDiff ? "" : " full-mode"}`);
      element.dataset.rowIndex = index;
      const number = (value, side) => {
        const label = node("button", "line-number", value == null ? "" : value);
        label.type = "button"; label.disabled = value == null;
        if (value != null) {
          label.setAttribute("aria-label", `${side === "old" ? "旧" : "新"} ${value} 行へのリンク`);
          label.title = `${side === "old" ? "旧" : "新"} ${value} 行`;
          label.addEventListener("click", () => navigate(file.path, value, side));
        }
        return label;
      };
      if (isDiff) element.append(number(row.old, "old"));
      element.append(number(row.new, "new"), node("span", "line-sign", row.kind === "added" ? "+" : row.kind === "removed" ? "−" : ""));
      const code = node("code", "code-text");
      code.innerHTML = highlighted[index] || " ";
      if (row.newline === false) code.append(node("span", "newline-marker", "  ↵ 末尾改行なし"));
      const ask = node("button", "ask-line", "?");
      ask.type = "button";
      ask.title = "この行について質問";
      ask.setAttribute("aria-label", `${row.new == null ? "旧" : "新"} ${row.new ?? row.old} 行について質問`);
      ask.addEventListener("click", () => openQuestion({ side: row.new == null ? "old" : "new", start: row.new ?? row.old, end: row.new ?? row.old }));
      element.append(code, ask); fragment.append(element); state.rowElements.push(element);
    });
    $("code-rows").replaceChildren(fragment);
    if (!file.rows.length) $("code-rows").append(node("div", "empty-state", "空のファイルです。"));
    $("line-columns").textContent = isDiff ? "旧行 / 新行" : "行番号";
    document.querySelectorAll(".diff-only").forEach((element) => { element.hidden = !isDiff; });
    const additions = file.rows.filter((row) => row.kind === "added").length, removals = file.rows.filter((row) => row.kind === "removed").length;
    const currentLines = file.rows.filter((row) => row.new != null).length;
    $("code-status").textContent = isDiff ? `${currentLines} 行 · +${additions} / −${removals}` : `${currentLines} 行 · ${file.language || "text"}`;
    $("file-meta").textContent = `${file.language || "text"} · ${currentLines} 行`;
    $("selection-label").textContent = "";
    $("code-scroll").scrollTop = 0;
    $("code-scroll").scrollLeft = 0;
  }

  /* SVGは画像として隔離し、MarkdownのHTML許可やページのDOMを広げない。 */
  function renderDiagram(diagram, expanded = false) {
    const value = diagram && typeof diagram === "object" ? diagram : {};
    const title = value.title || "図";
    const description = value.description || "図の説明がありません。";
    const figure = node("figure", `comment-diagram${expanded ? " is-expanded" : ""}`);
    const caption = node("figcaption");
    caption.append(node("strong", "diagram-title", title), node("p", "diagram-description", description));
    const frame = node("div", "diagram-image-frame"), image = node("img", "comment-diagram-image");
    image.alt = description;
    image.decoding = "async";
    const error = node("p", "diagram-error", "図を表示できませんでした。上の説明を確認し、エージェントに図の再生成を依頼してください。");
    error.setAttribute("role", "status");
    error.hidden = true;
    let enlarge;
    if (!expanded) {
      enlarge = node("button", "text-button diagram-enlarge", "図を拡大 ↗");
      enlarge.type = "button";
      enlarge.setAttribute("aria-label", `${title}を拡大`);
      enlarge.addEventListener("click", () => openDiagram(value));
    }
    image.addEventListener("load", () => {
      requestAnimationFrame(refreshAnchors);
    });
    const fail = () => {
      image.hidden = true;
      error.hidden = false;
      if (enlarge) enlarge.disabled = true;
      requestAnimationFrame(refreshAnchors);
    };
    image.addEventListener("error", fail);
    frame.append(image, error);
    figure.append(caption, frame);
    if (enlarge) figure.append(enlarge);
    if (typeof value.svg === "string" && value.svg.trim()) image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(value.svg)}`;
    else fail();
    return figure;
  }

  function openDiagram(diagram) {
    $("diagram-dialog-title").textContent = diagram.title || "図";
    $("diagram-dialog-content").replaceChildren(renderDiagram(diagram, true));
    $("diagram-dialog").showModal();
  }

  function renderComments(file) {
    const comments = [...(file.comments || [])].sort((a, b) => rowIndexFor(a.start_line, a.side || "new") - rowIndexFor(b.start_line, b.side || "new"));
    $("comments-view").replaceChildren();
    state.commentEntries = [];
    state.activeComment = -1;
    comments.forEach((comment, index) => {
      const range = codeRange(comment), card = node("article", "comment-card");
      const hash = commentHashInfo(comment, file);
      const invalidRange = comment.range_status === "missing" || range.start < 0 || range.end < 0;
      card.dataset.commentId = comment.id;
      if (hash.status !== "verified" || invalidRange || comment.link_warning) card.classList.add("needs-review");
      const top = node("div", "comment-topline"), link = node("button", "comment-range", rangeLabel(comment.start_line, comment.end_line || comment.start_line, comment.side));
      link.type = "button";
      link.disabled = invalidRange;
      link.setAttribute("aria-label", `${rangeLabel(comment.start_line, comment.end_line || comment.start_line, comment.side)} のコードへ移動`);
      const jumpToCode = () => navigate(file.path, comment.start_line, comment.side || "new", comment.end_line, comment.id);
      link.addEventListener("click", jumpToCode);
      if (!invalidRange) {
        card.dataset.navigable = "true";
        card.addEventListener("click", (event) => {
          // 子の質問・リンク・図拡大と、説明文を選択してコピーする操作を優先する。
          if (event.defaultPrevented || event.target.closest("a,button,input,textarea,select,summary")) return;
          const selection = window.getSelection();
          if (selection && !selection.isCollapsed && (card.contains(selection.anchorNode) || card.contains(selection.focusNode))) return;
          jumpToCode();
        });
      }
      top.append(node("span", "comment-index", `${String(index + 1).padStart(2, "0")} / WALKTHROUGH`), link);
      const body = node("div", "markdown"); renderMarkdown(comment.body, body);
      const actions = node("div", "comment-actions"), ask = node("button", "text-button", "この解説に質問 ↗");
      ask.type = "button";
      ask.addEventListener("click", () => openQuestion({ side: comment.side || "new", start: comment.start_line, end: comment.end_line || comment.start_line, comment }));
      actions.append(ask);
      card.append(top, node("h3", "", comment.title || "コードの解説"));
      if (hash.status !== "verified") {
        const notice = node("div", `hash-notice ${hash.status}`);
        notice.append(node("strong", "", hash.label), node("p", "", hash.status === "mismatch" ? "解説作成時と表示中のコードが異なります。説明と行の対応を再確認してください。" : "解説作成時のコードHashがなく、対応を照合できません。"));
        const hashes = node("dl", "hash-values");
        hashes.append(node("dt", "", "作成時"), node("dd", "", hash.expected || "未保存"), node("dt", "", "表示中"), node("dd", "", hash.actual || "未取得"));
        notice.append(hashes); card.append(notice);
      }
      if (invalidRange) card.append(node("p", "reference-warning", "参照先の行範囲が存在しません。コードへの移動はできませんが、この解説について質問できます。"));
      if (comment.link_warning) card.append(node("p", "reference-warning", `参照リンクの確認: ${comment.link_warning}`));
      card.append(body);
      if (Array.isArray(comment.diagrams)) for (const diagram of comment.diagrams) card.append(renderDiagram(diagram));
      card.append(actions);
      $("comments-view").append(card);
      state.commentEntries.push({ comment, card, invalidRange, ...range });
    });
    if (!comments.length) {
      const empty = node("div", "empty-state");
      empty.append(node("span", "empty-icon", "≡"), node("strong", "", "解説はまだありません"), node("p", "", "コード全文は左で確認できます。行の「?」から参照付きの質問文を作れます。"));
      $("comments-view").append(empty);
    }
    $("detail-count").textContent = comments.length;
    $("detail-scroll").scrollTop = 0;
    $("sync-toggle").disabled = !state.commentEntries.some((entry) => !entry.invalidRange);
  }

  function activateComment(index) {
    if (state.activeComment === index) return;
    const previous = state.commentEntries[state.activeComment];
    if (previous) {
      previous.card.classList.remove("active");
      if (!previous.invalidRange) for (let i = previous.start; i <= previous.end; i++) state.rowElements[i]?.classList.remove("active-range");
    }
    state.activeComment = index;
    const active = state.commentEntries[index];
    if (active) {
      active.card.classList.add("active");
      if (!active.invalidRange) for (let i = active.start; i <= active.end; i++) {
        if (state.file.rows[i]?.[active.side] != null) state.rowElements[i]?.classList.add("active-range");
      }
    }
  }

  /* 到達可能なスクロール位置へそろえ、方向ごとの折れ線を作る純粋関数。
     同じコード行の複数カードは残す。右から読むと、その区間のコードは動かない。 */
  function makeScrollMaps(anchors, codeMaximum, detailMaximum) {
    const maxima = { code: Math.max(0, codeMaximum), detail: Math.max(0, detailMaximum) };
    const pairs = anchors.map((anchor) => ({
      code: Math.max(0, Math.min(maxima.code, anchor.code)),
      detail: Math.max(0, Math.min(maxima.detail, anchor.detail)),
    }));
    const maps = {};
    for (const source of ["code", "detail"]) {
      const target = source === "code" ? "detail" : "code";
      if (!maxima[source]) { maps[source] = [{ from: 0, to: 0 }]; continue; }
      const points = new Map([[0, 0]]);
      for (const pair of pairs) if (!points.has(pair[source])) points.set(pair[source], pair[target]);
      // 末尾では両ペインとも末尾に到達する。画面下部の到達不能アンカーを残さない。
      points.set(maxima[source], maxima[target]);
      maps[source] = [...points].map(([from, to]) => ({ from, to })).sort((a, b) => a.from - b.from);
    }
    return maps;
  }

  function interpolateScroll(points, value) {
    if (!points || !points.length) return 0;
    if (value <= points[0].from) return points[0].to;
    for (let i = 1; i < points.length; i++) {
      if (value <= points[i].from) {
        const lower = points[i - 1], upper = points[i];
        const progress = (value - lower.from) / (upper.from - lower.from);
        return lower.to + progress * (upper.to - lower.to);
      }
    }
    return points[points.length - 1].to;
  }

  function refreshAnchors() {
    if (!state.file) return;
    const code = $("code-scroll"), detail = $("detail-scroll"), codeRect = code.getBoundingClientRect(), detailRect = detail.getBoundingClientRect();
    const anchors = [];
    for (let index = 0; index < state.commentEntries.length; index++) {
      const entry = state.commentEntries[index], row = state.rowElements[entry.start];
      if (!row || entry.invalidRange) continue;
      const codeY = row.getBoundingClientRect().top - codeRect.top + code.scrollTop;
      const detailY = entry.card.getBoundingClientRect().top - detailRect.top + detail.scrollTop;
      anchors.push({ code: Math.max(0, codeY - 12), detail: Math.max(0, detailY - 12), index });
    }
    state.anchors = anchors;
    state.scrollMaps = makeScrollMaps(anchors, code.scrollHeight - code.clientHeight, detail.scrollHeight - detail.clientHeight);
  }

  function syncScroll(source) {
    if (!state.file || !state.commentEntries.length || performance.now() < state.suppressUntil) return;
    const from = source === "code" ? $("code-scroll") : $("detail-scroll");
    const target = source === "code" ? $("detail-scroll") : $("code-scroll");
    const value = from.scrollTop;
    const anchors = state.anchors;
    let active = -1, previousCode = null;
    for (const anchor of anchors) {
      if (anchor[source] > value + 20) break;
      if (source !== "code" || anchor.code !== previousCode) active = anchor.index;
      previousCode = anchor.code;
    }
    if (source === "detail") {
      const rect = from.getBoundingClientRect();
      state.commentEntries.forEach((entry, index) => { if (entry.card.getBoundingClientRect().top - rect.top <= 45) active = index; });
      if (from.scrollTop > 0 && from.scrollTop >= from.scrollHeight - from.clientHeight - 1) active = state.commentEntries.length - 1;
    }
    activateComment(active < 0 ? 0 : active);
    if (!state.sync || !anchors.length || !state.scrollMaps) return;
    const desired = interpolateScroll(state.scrollMaps[source], value);
    if (Math.abs(target.scrollTop - desired) > 1) {
      state.source = source;
      target.scrollTop = desired;
    }
  }

  function onScroll(source) {
    if (state.source && state.source !== source) { state.source = null; return; }
    state.source = null;
    if (state.frame) cancelAnimationFrame(state.frame);
    state.frame = requestAnimationFrame(() => { state.frame = null; syncScroll(source); });
  }

  function show(path, line, side = "new", end, commentId) {
    state.suppressUntil = performance.now() + 80;
    state.source = null;
    state.path = path || null;
    state.file = path ? fileMap.get(path) || null : null;
    const file = state.file;
    $("home-button").classList.toggle("active", !path);
    $("overview-view").hidden = !!path;
    $("code-view").hidden = !file;
    $("unavailable-view").hidden = !path || !!file;
    $("excluded-view").hidden = !!file;
    $("comments-view").hidden = !file;
    $("sync-toggle").hidden = !file;
    $("detail-title").firstChild.textContent = file ? "コードの解説 " : "対象外のファイル ";
    $("detail-count").textContent = file ? (file.comments || []).length : excluded.length;
    $("main-title").textContent = path || "プロジェクト概要";
    $("main-title").title = path || "プロジェクト概要";
    $("file-meta").textContent = "";
    $("detail-footer").textContent = file ? "行範囲に対応した解説 · 質問はエージェントへ" : "全ファイル一覧と対象リストを照合";
    if (file) {
      renderCode(file);
      renderComments(file);
      requestAnimationFrame(() => {
        refreshAnchors();
        if (line) focusRange(Number(line), side, Number(end || line), true, commentId);
      });
    } else if (path) {
      const item = inventoryMap.get(path);
      $("unavailable-title").textContent = item ? item.selected ? "コードを表示できないファイル" : "レビュー対象外のファイル" : "ファイルが見つかりません";
      $("unavailable-message").textContent = item ? item.selected ? "このファイルのコードを表示できません。" : "このファイルはレビュー対象外です。" : "この参照先は、保存されたプロジェクト一覧に存在しません。";
      $("unavailable-details").replaceChildren();
      if (item) for (const [label, value] of [["パス", item.path], ["状態", statusLabel(item.status)], ["サイズ", prettySize(item.size)]]) $("unavailable-details").append(node("dt", "", label), node("dd", "", value));
    }
    $("file-tree").querySelectorAll("[data-file-path]").forEach((button) => {
      if (button.dataset.filePath === path) {
        button.setAttribute("aria-current", "page");
        let parent = button.parentElement;
        while (parent && parent !== $("file-tree")) { if (parent.tagName === "DETAILS") parent.open = true; parent = parent.parentElement; }
      } else button.removeAttribute("aria-current");
    });
    $("file-sidebar").classList.remove("is-open");
    $("toggle-tree").setAttribute("aria-expanded", "false");
    announce(path ? `${path} を表示しました。` : "プロジェクト概要を表示しました。");
  }

  function chooseCommentIndex(entries, rowIndex, side, commentId) {
    const eligible = (entry) => !entry.invalidRange && entry.side === side;
    const containsRow = (entry) => eligible(entry) && rowIndex >= entry.start && rowIndex <= entry.end;
    if (commentId) {
      const requested = entries.findIndex((entry) => entry.comment.id === commentId && containsRow(entry));
      if (requested >= 0) return requested;
    }
    const matching = entries.findIndex(containsRow);
    return matching >= 0 ? matching : entries.findIndex((entry) => eligible(entry) && entry.start >= rowIndex);
  }

  function focusRange(start, side, end, withComment, commentId) {
    const index = rowIndexFor(start, side), last = rowIndexFor(end, side);
    if (index < 0) { announce("参照する行が見つかりません。"); return; }
    state.suppressUntil = performance.now() + 100;
    state.rowElements.forEach((row, i) => row.classList.toggle("highlighted", state.file.rows[i][side] != null && i >= index && i <= (last < 0 ? index : last)));
    const scroller = $("code-scroll"), row = state.rowElements[index];
    scroller.scrollTop += row.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 34;
    $("selection-label").textContent = rangeLabel(start, end, side);
    if (withComment) {
      const commentIndex = chooseCommentIndex(state.commentEntries, index, side, commentId);
      if (commentIndex >= 0) {
        activateComment(commentIndex);
        const detail = $("detail-scroll"), card = state.commentEntries[commentIndex].card;
        detail.scrollTop += card.getBoundingClientRect().top - detail.getBoundingClientRect().top - 20;
      } else activateComment(-1);
    }
  }

  function navigate(path, line, side = "new", end, commentId) {
    const params = new URLSearchParams();
    if (path) params.set("file", path);
    if (line) {
      params.set("line", line); params.set("side", side);
      if (end && end !== line) params.set("end", end);
      if (commentId) params.set("comment", commentId);
    }
    const hash = params.toString();
    if (location.hash.slice(1) === hash) {
      if (state.path === path && line) focusRange(Number(line), side, Number(end || line), true, commentId);
      else show(path, line, side, end, commentId);
    } else location.hash = hash;
  }
  function fromHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const path = params.get("file"), line = params.get("line"), side = params.get("side") === "old" ? "old" : "new", end = params.get("end"), commentId = params.get("comment");
    if (state.path === path && state.file && line) focusRange(Number(line), side, Number(end || line), true, commentId);
    else show(path, line, side, end, commentId);
  }

  function openQuestion(context) {
    state.question = { ...context, file: state.file };
    $("question-reference").textContent = `${state.file.path}${context.start ? ` · ${rangeLabel(context.start, context.end, context.side)}` : ` · ファイル全体${context.side === "old" ? "（旧版）" : ""}`}${context.comment ? ` · ${context.comment.title}` : ""}`;
    $("question-input").value = "";
    $("prompt-output").value = "";
    $("prompt-result").hidden = true;
    $("copy-prompt").hidden = true;
    $("copy-status").textContent = "";
    $("question-dialog").showModal();
    $("question-input").focus();
  }

  function buildPrompt(question, context) {
    const file = context.file;
    const rangeMissing = context.comment?.range_status === "missing";
    const relevant = rangeMissing ? [] : context.start ? file.rows.filter((row) => row[context.side] != null && row[context.side] >= context.start && row[context.side] <= context.end) : file.rows;
    const code = relevant.map((row) => `${row.kind === "added" ? "+" : row.kind === "removed" ? "-" : " "} [旧:${row.old ?? "—"} 新:${row.new ?? "—"}] ${context.side === "old" ? row.old_text ?? row.text : row.text}${row.newline === false ? "\n\\ No newline at end of file" : ""}`).join("\n");
    const maxBackticks = (code.match(/`+/g) || []).reduce((longest, item) => Math.max(longest, item.length), 2);
    const fence = "`".repeat(maxBackticks + 1);
    const reference = `review:${file.path.split("/").map(encodeURIComponent).join("/")}${context.start ? `#${context.side === "old" ? "O" : "L"}${context.start}` : ""}`;
    const related = context.comment ? [context.comment] : (file.comments || []).filter((comment) => !context.start || (comment.side || "new") === context.side && comment.start_line <= context.end && comment.end_line >= context.start);
    const hashDetails = related.flatMap((comment) => {
      const hash = commentHashInfo(comment, file);
      return [`解説 ${comment.id}: ${hash.label}`, `  作成時コードHash: ${hash.expected || "未保存"}`, `  表示中コードHash: ${hash.actual || "未取得"}`, ...(comment.range_status === "missing" ? ["  元の参照行範囲は現在のスナップショットに存在しません。"] : []), ...(comment.link_warning ? [`  参照リンクの警告: ${comment.link_warning}`] : [])];
    });
    const diagramDetails = related.flatMap((comment) => (Array.isArray(comment.diagrams) ? comment.diagrams : []).flatMap((diagram) => {
      const svg = typeof diagram?.svg === "string" ? diagram.svg : "";
      const diagramFence = "`".repeat((svg.match(/`+/g) || []).reduce((longest, item) => Math.max(longest, item.length), 2) + 1);
      return [`解説 ${comment.id} の図: ${diagram?.title || "図"}`, `説明: ${diagram?.description || "図の説明がありません。"}`, `${diagramFence}svg`, svg || "（図のSVGがありません）", diagramFence, ""];
    }));
    const diffSummary = Array.isArray(file.diff_summary) ? file.diff_summary : [];
    return [
      "以下のウォークスルーレビューについて質問します。",
      "", `レビュー: ${data.title || data.project_name}`, `プロジェクト: ${data.project_root || data.project_name}`,
      `モード: ${data.mode === "diff" ? "差分レビュー（変更後のファイル全文内に差分表示）" : "全体レビュー"}`,
      `ファイル: ${file.path}`, `参照: ${reference}`,
      `範囲: ${context.start ? rangeLabel(context.start, context.end, context.side) : "ファイル全体"}`,
      `スナップショット生成日時: ${data.generated_at || "不明"}`,
      file.sha256 ? `変更後ファイル SHA-256: ${file.sha256}` : "",
      "コードHashはUTF-8 BOM除去・改行LF・末尾改行保持で照合:",
      `  表示中の新版Hash: ${(file.code_hashes || {}).new || "未取得"}`,
      `  表示中の旧版Hash: ${(file.code_hashes || {}).old || "未取得"}`,
      ...hashDetails,
      ...(related.some((comment) => commentHashInfo(comment, file).status !== "verified") ? ["注意: 未照合またはコード不一致の解説があります。説明と行範囲の対応が正しいと仮定しないでください。"] : []),
      ...(diffSummary.length ? ["", "本文以外の差分:", ...diffSummary.map((item) => `- ${item}`)] : []),
      "", "質問:", question.trim(), "",
      ...(context.comment ? [`対象の解説（${context.comment.id}）: ${context.comment.title || ""}`, context.comment.body || "", ""] : []),
      ...diagramDetails,
      "該当コード（+ は追加、- は削除。行番号はレビュー生成時点）:",
      fence, code || (rangeMissing ? "（元の参照行範囲が存在しないため、該当コードを取得できません）" : "（空のファイル）"), fence, "",
      "該当箇所と周辺の設計意図を確認し、参照先を示しながら日本語で回答してください。",
      "現在のプロジェクトと上記スナップショットが異なる場合は、その違いを明示してください。"
    ].filter((value, index, array) => value !== "" || index === 0 || array[index - 1] !== "").join("\n");
  }

  async function copyPrompt() {
    const output = $("prompt-output");
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(output.value);
      $("copy-status").textContent = "コピーしました。コーディングエージェントに貼り付けてください。";
    } catch (_) {
      output.focus(); output.select(); output.setSelectionRange(0, output.value.length);
      let copied = false;
      try { copied = document.execCommand("copy"); } catch (_) { /* file://では選択テキストからコピーできる。 */ }
      $("copy-status").textContent = copied ? "コピーしました。コーディングエージェントに貼り付けてください。" : "自動コピーを利用できません。質問文を選択しました。Ctrl+C（Macでは ⌘C）でコピーしてください。";
    }
  }

  $("file-search").addEventListener("input", renderTree);
  $("skip-link").addEventListener("click", (event) => {
    event.preventDefault();
    $("main-panel").focus();
  });
  document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    renderTree();
  }));
  $("home-button").addEventListener("click", () => navigate(null));
  $("unavailable-home").addEventListener("click", () => navigate(null));
  $("toggle-tree").addEventListener("click", () => {
    const open = $("file-sidebar").classList.toggle("is-open");
    $("toggle-tree").setAttribute("aria-expanded", String(open));
    if (open) $("file-search").focus();
  });
  $("close-tree").addEventListener("click", () => {
    $("file-sidebar").classList.remove("is-open");
    $("toggle-tree").setAttribute("aria-expanded", "false");
    $("toggle-tree").focus();
  });
  $("sync-toggle").addEventListener("click", () => {
    state.sync = !state.sync;
    $("sync-toggle").setAttribute("aria-pressed", String(state.sync));
    announce(`スクロール連動を${state.sync ? "有効" : "無効"}にしました。`);
  });
  $("code-scroll").addEventListener("scroll", () => onScroll("code"), { passive: true });
  $("detail-scroll").addEventListener("scroll", () => onScroll("detail"), { passive: true });
  for (const id of ["code-scroll", "detail-scroll"]) {
    $(id).addEventListener("wheel", () => { state.source = null; }, { passive: true });
    $(id).addEventListener("touchstart", () => { state.source = null; }, { passive: true });
  }
  $("ask-file").addEventListener("click", () => openQuestion({ side: inventoryMap.get(state.file.path)?.status === "deleted" ? "old" : "new", start: null, end: null }));
  $("close-diagram").addEventListener("click", () => $("diagram-dialog").close());
  $("diagram-dialog").addEventListener("click", (event) => {
    if (event.target === $("diagram-dialog")) {
      const rect = event.target.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close();
    }
  });
  $("close-question").addEventListener("click", () => $("question-dialog").close());
  $("question-dialog").addEventListener("click", (event) => {
    if (event.target === $("question-dialog")) {
      const rect = event.target.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close();
    }
  });
  $("question-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("question-input").value.trim()) { $("question-input").focus(); return; }
    $("prompt-output").value = buildPrompt($("question-input").value, state.question);
    $("prompt-result").hidden = false;
    $("copy-prompt").hidden = false;
    $("copy-status").textContent = "質問文を作成しました。「質問文をコピー」でエージェントへ渡せます。";
    $("prompt-output").focus();
  });
  $("copy-prompt").addEventListener("click", copyPrompt);
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-review-link]");
    if (!link) return;
    event.preventDefault();
    const match = /^review:(.+?)#([LO])(\d+)(?:-(?:[LO])?(\d+))?$/i.exec(link.getAttribute("href"));
    if (!match) { announce("レビュー参照の形式が正しくありません。"); return; }
    let path;
    try { path = decodeURIComponent(match[1]); } catch (_) { announce("参照先のパスを読み取れません。"); return; }
    if (!fileMap.has(path)) { announce("この参照先はレビュー対象に含まれていません。"); return; }
    navigate(path, Number(match[3]), match[2].toUpperCase() === "O" ? "old" : "new", Number(match[4] || match[3]));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName) && !$("question-dialog").open && !$("diagram-dialog").open) {
      event.preventDefault();
      if (window.matchMedia("(max-width: 950px)").matches) { $("file-sidebar").classList.add("is-open"); $("toggle-tree").setAttribute("aria-expanded", "true"); }
      $("file-search").focus();
    }
    if (event.key === "Escape" && !$("question-dialog").open) { $("file-sidebar").classList.remove("is-open"); $("toggle-tree").setAttribute("aria-expanded", "false"); }
  });
  window.addEventListener("hashchange", fromHash);
  window.addEventListener("resize", () => requestAnimationFrame(refreshAnchors));
  if (window.ResizeObserver) {
    const scrollObserver = new ResizeObserver(() => requestAnimationFrame(refreshAnchors));
    for (const id of ["comments-view", "code-scroll", "detail-scroll"]) scrollObserver.observe($(id));
  }
  renderOverview();
  renderHashWarnings();
  renderExcluded();
  renderTree();
  fromHash();
})();
