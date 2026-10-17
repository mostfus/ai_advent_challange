// Day 24: Markdown for the chat's answers - built as DOM nodes, never as HTML.
//
// Until today an answer went into its bubble as text, so a model that wrote
// a table, a list or **bold** was shown as the pipes and asterisks it typed.
// This renders the part of Markdown a chat model actually writes: headings,
// paragraphs, emphasis, inline code and fenced blocks, lists (nested),
// block quotes, tables, rules and links.
//
// **Nodes, not innerHTML.** The text comes from a model, and a model can be
// talked into writing `<img onerror=…>`. Every piece of it here goes in
// through createTextNode or textContent, and a link is only a link when it
// starts with http(s): or mailto: - so there is no string this file can be
// handed that turns into markup.
//
// **Citations are a hook.** `[2]` and `[1, 3]` outside a link are handed to
// `opts.citation(n)`, which returns the node to draw in their place - the
// chat draws a chip that opens the excerpt. Without the hook they stay text.
//
// A single line break inside a paragraph is kept as a line break, not folded
// into a space as CommonMark would: answers were shown pre-wrapped until
// today, and a model that breaks a line in a chat usually means it.
//
//   Markdown.render(text, { citation: (n) => node }) -> DocumentFragment
(function (global) {
  "use strict";

  const FENCE = /^(\s{0,3})(`{3,}|~{3,})\s*([^`\s]*)[^`]*$/;
  const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
  const RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
  const QUOTE = /^\s{0,3}>\s?/;
  const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
  const CITATION = /^\[(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\](?!\()/;
  const LINK = /^\[([^\]\n]+)\]\(\s*(<[^>\n]*>|(?:[^\s()]|\([^\s()]*\))+)(?:\s+"[^"\n]*")?\s*\)/;
  const AUTOLINK = /^(https?:\/\/[^\s<>()]*[^\s<>().,;:!?'"*_~\]])/;
  const ESCAPABLE = /^\\([\\`*_{}\[\]()#+\-.!|>~"'])/;

  function el(doc, tag, className) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function safeHref(raw) {
    const url = raw.replace(/^<|>$/g, "").trim();
    return /^(https?:|mailto:)/i.test(url) ? url : null;
  }

  // ------------------------------------------------------------ inline

  // Emphasis: the closing run of the same delimiter, with something that is
  // not a space just inside both ends. `_` must also sit at a word boundary,
  // so snake_case_names stay as they are.
  function closing(text, from, delim) {
    let at = from;
    while (true) {
      const j = text.indexOf(delim, at);
      if (j < 0) return -1;
      const inner = text.slice(from, j);
      const after = text[j + delim.length] || "";
      const okEdge = inner.length && !/\s/.test(inner[0]) && !/\s/.test(inner[inner.length - 1]);
      const okWord = delim[0] !== "_" || !/[\p{L}\p{N}]/u.test(after);
      const okSingle = delim.length > 1 || text[j + 1] !== delim;    // `*` is not half of `**`
      if (okEdge && okWord && okSingle) return j;
      at = j + (okSingle ? delim.length : 2);
    }
  }

  function inline(doc, text, opts) {
    const out = [];
    let buf = "";
    const flush = () => { if (buf) { out.push(doc.createTextNode(buf)); buf = ""; } };
    let i = 0;
    while (i < text.length) {
      const rest = text.slice(i);
      const ch = text[i];
      let m;

      if (ch === "\\" && (m = ESCAPABLE.exec(rest))) {
        buf += m[1];
        i += 2;
        continue;
      }
      if (ch === "\n") {
        flush();
        out.push(el(doc, "br"));
        i += 1;
        continue;
      }
      if (ch === "`") {
        const run = /^`+/.exec(rest)[0];
        const end = text.indexOf(run, i + run.length);
        if (end > 0) {
          flush();
          const code = el(doc, "code");
          code.textContent = text.slice(i + run.length, end).replace(/^ (.+) $/, "$1");
          out.push(code);
          i = end + run.length;
          continue;
        }
        buf += run;
        i += run.length;
        continue;
      }
      if (ch === "[") {
        if (opts.citation && (m = CITATION.exec(rest))) {
          flush();
          for (const part of m[1].split(/[,;]/)) {
            const node = opts.citation(parseInt(part, 10));
            out.push(node || doc.createTextNode("[" + part.trim() + "]"));
          }
          i += m[0].length;
          continue;
        }
        if ((m = LINK.exec(rest))) {
          const href = safeHref(m[2]);
          flush();
          if (href) {
            const a = el(doc, "a");
            a.href = href;
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            for (const kid of inline(doc, m[1], { citation: null })) a.appendChild(kid);
            out.push(a);
          } else {
            for (const kid of inline(doc, m[1], opts)) out.push(kid);
          }
          i += m[0].length;
          continue;
        }
      }
      if (ch === "h" && (m = AUTOLINK.exec(rest)) && !/[\p{L}\p{N}]/u.test(text[i - 1] || "")) {
        flush();
        const a = el(doc, "a");
        a.href = m[1];
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = m[1];
        out.push(a);
        i += m[1].length;
        continue;
      }
      if (ch === "*" || ch === "_" || ch === "~") {
        const double = text[i + 1] === ch;
        const delim = double ? ch + ch : ch;
        const leftOk = ch !== "_" || !/[\p{L}\p{N}]/u.test(text[i - 1] || "");
        if (ch !== "~" || double) {
          const end = leftOk ? closing(text, i + delim.length, delim) : -1;
          if (end > 0) {
            flush();
            const tag = ch === "~" ? "del" : double ? "strong" : "em";
            const node = el(doc, tag);
            for (const kid of inline(doc, text.slice(i + delim.length, end), opts)) node.appendChild(kid);
            out.push(node);
            i = end + delim.length;
            continue;
          }
        }
        buf += delim;
        i += delim.length;
        continue;
      }
      buf += ch;
      i += 1;
    }
    flush();
    return out;
  }

  function fillInline(doc, node, text, opts) {
    for (const kid of inline(doc, text, opts)) node.appendChild(kid);
    return node;
  }

  // ------------------------------------------------------------ blocks

  function startsBlock(lines, i) {
    const line = lines[i];
    return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) ||
      ITEM.test(line) || isTableStart(lines, i);
  }

  function splitRow(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    const cells = [];
    let cur = "";
    let inCode = false;
    for (let k = 0; k < s.length; k++) {
      const c = s[k];
      if (c === "\\" && s[k + 1] === "|") { cur += "|"; k++; continue; }
      if (c === "`") inCode = !inCode;
      if (c === "|" && !inCode) { cells.push(cur.trim()); cur = ""; continue; }
      cur += c;
    }
    cells.push(cur.trim());
    return cells;
  }

  function isTableStart(lines, i) {
    return i + 1 < lines.length && lines[i].includes("|") && lines[i + 1].includes("-") &&
      TABLE_SEP.test(lines[i + 1]) && splitRow(lines[i]).length === splitRow(lines[i + 1]).length;
  }

  function table(doc, lines, i, opts) {
    const head = splitRow(lines[i]);
    const align = splitRow(lines[i + 1]).map((c) =>
      c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : "");
    const wrap = el(doc, "div", "md-table");
    const t = el(doc, "table");
    const thead = el(doc, "thead");
    const tr = el(doc, "tr");
    head.forEach((cell, k) => {
      const th = fillInline(doc, el(doc, "th"), cell, opts);
      if (align[k]) th.style.textAlign = align[k];
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    t.appendChild(thead);
    const tbody = el(doc, "tbody");
    let j = i + 2;
    while (j < lines.length && lines[j].trim() && lines[j].includes("|")) {
      const row = el(doc, "tr");
      const cells = splitRow(lines[j]);
      head.forEach((_, k) => {
        const td = fillInline(doc, el(doc, "td"), cells[k] || "", opts);
        if (align[k]) td.style.textAlign = align[k];
        row.appendChild(td);
      });
      tbody.appendChild(row);
      j++;
    }
    t.appendChild(tbody);
    wrap.appendChild(t);
    return [wrap, j];
  }

  function indentOf(line) {
    return /^\s*/.exec(line)[0].replace(/\t/g, "    ").length;
  }

  function list(doc, lines, i, opts) {
    const first = ITEM.exec(lines[i]);
    const indent = indentOf(first[1]);
    const ordered = /\d/.test(first[2]);
    const node = el(doc, ordered ? "ol" : "ul");
    if (ordered && parseInt(first[2], 10) !== 1) node.start = parseInt(first[2], 10);
    while (i < lines.length) {
      const m = ITEM.exec(lines[i]);
      if (!m || indentOf(m[1]) !== indent || /\d/.test(m[2]) !== ordered) break;
      const contentIndent = indent + m[2].length + 1;
      const body = [m[3]];
      i++;
      while (i < lines.length) {
        const line = lines[i];
        if (!line.trim()) {
          // A blank line ends the item unless what follows is indented into it.
          const next = lines[i + 1];
          if (next != null && next.trim() && indentOf(next) >= contentIndent) { body.push(""); i++; continue; }
          break;
        }
        const lead = indentOf(line);
        if (lead > indent) { body.push(line.replace(new RegExp("^\\s{0," + contentIndent + "}"), "")); i++; continue; }
        // A same-level line that starts nothing new is the paragraph going on.
        if (!startsBlock(lines, i) && body[body.length - 1] !== "") { body.push(line.trim()); i++; continue; }
        break;
      }
      const li = el(doc, "li");
      const kids = blocks(doc, body, opts);
      if (kids.length && kids[0].tagName === "P") {
        // Tight: the item's first paragraph is its text, not a paragraph in it.
        for (const kid of Array.from(kids[0].childNodes)) li.appendChild(kid);
        kids.shift();
      }
      for (const kid of kids) li.appendChild(kid);
      node.appendChild(li);
      // A blank line between two items of this list does not end it.
      if (i < lines.length && !lines[i].trim()) {
        const next = lines[i + 1];
        const nm = next != null ? ITEM.exec(next) : null;
        if (nm && indentOf(nm[1]) === indent && /\d/.test(nm[2]) === ordered) i++;
      }
    }
    return [node, i];
  }

  function blocks(doc, lines, opts) {
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if (!line.trim()) { i++; continue; }

      if ((m = FENCE.exec(line))) {
        const marker = m[2];
        const pad = m[1].length;
        const body = [];
        i++;
        while (i < lines.length && !new RegExp("^\\s{0,3}" + marker[0] + "{" + marker.length + ",}\\s*$").test(lines[i])) {
          body.push(lines[i].replace(new RegExp("^\\s{0," + pad + "}"), ""));
          i++;
        }
        i++;                                    // the closing fence, if there was one
        const pre = el(doc, "pre", "md-code");
        const code = el(doc, "code");
        if (m[3]) code.dataset.lang = m[3];
        code.textContent = body.join("\n");
        pre.appendChild(code);
        out.push(pre);
        continue;
      }
      if ((m = HEADING.exec(line))) {
        out.push(fillInline(doc, el(doc, "h" + m[1].length, "md-h"), m[2], opts));
        i++;
        continue;
      }
      if (RULE.test(line)) {
        out.push(el(doc, "hr"));
        i++;
        continue;
      }
      if (QUOTE.test(line)) {
        const body = [];
        while (i < lines.length && QUOTE.test(lines[i])) {
          body.push(lines[i].replace(QUOTE, ""));
          i++;
        }
        const quote = el(doc, "blockquote");
        for (const kid of blocks(doc, body, opts)) quote.appendChild(kid);
        out.push(quote);
        continue;
      }
      if (ITEM.test(line)) {
        const [node, next] = list(doc, lines, i, opts);
        out.push(node);
        i = next;
        continue;
      }
      if (isTableStart(lines, i)) {
        const [node, next] = table(doc, lines, i, opts);
        out.push(node);
        i = next;
        continue;
      }
      const para = [line.trim()];
      i++;
      while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) {
        para.push(lines[i].trim());
        i++;
      }
      out.push(fillInline(doc, el(doc, "p"), para.join("\n"), opts));
    }
    return out;
  }

  function render(text, opts) {
    opts = opts || {};
    const doc = opts.document || global.document;
    const frag = doc.createDocumentFragment();
    const lines = String(text == null ? "" : text).replace(/\r\n?/g, "\n").split("\n");
    for (const node of blocks(doc, lines, opts)) frag.appendChild(node);
    return frag;
  }

  global.Markdown = { render };
})(typeof window !== "undefined" ? window : globalThis);
