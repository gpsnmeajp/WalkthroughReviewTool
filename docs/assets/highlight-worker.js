/* Prismと同じWorkerで実行する。旧版・新版を別々に解析し、行をまたぐspanを閉じ直す。 */
self.onmessage = ({ data }) => {
  try {
    const aliases = { js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript", py: "python", html: "markup", xml: "markup", svg: "markup", sh: "bash", shell: "bash", yml: "yaml" };
    const language = aliases[data.language] || data.language;
    const grammar = Prism.languages[language];
    if (!grammar) { self.postMessage(null); return; }
    const result = {};
    for (const [side, text] of Object.entries(data.sources)) {
      const open = [];
      result[side] = Prism.highlight(text, grammar, language).split("\n").map((line) => {
        const prefix = open.join("");
        for (const tag of line.match(/<\/?span\b[^>]*>/g) || []) {
          if (tag.startsWith("</")) open.pop(); else open.push(tag);
        }
        return prefix + line + "</span>".repeat(open.length);
      });
    }
    self.postMessage(result);
  } catch (_) { self.postMessage(null); }
};
