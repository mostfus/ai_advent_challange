// Day 22: the RAG tab - one question asked with and without the document
// index, and ten control questions that say what a right answer contains.
//
// Self-contained like the Index tab: it borrows that tab's styles (ix-*) and
// the chat page's colour tokens, and talks only to /api/rag/* and, for the
// question editor's file and section lists, /api/index/*.
(function () {
  "use strict";

  const root = document.getElementById("rag-view");
  if (!root) return;

  const SETTINGS_KEY = "rag-run-settings";
  const MODES = [
    { key: "plain", title: "Without RAG", lead: "the question alone" },
    { key: "rag", title: "With RAG", lead: "the question + the nearest chunks" },
  ];

  const S = {
    ov: null,              // GET /api/rag
    settings: null,        // {index, k, model}
    questions: [],         // being edited
    dirty: false,
    results: {},           // id -> row of the last run
    summary: null,
    open: {},              // id -> expanded
    running: null,         // {queue, stop}
    docs: null,            // corpus documents, for the editor
    sections: {},          // source -> [labels]
    started: false,
  };

  // ------------------------------------------------------------ helpers
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "value" || k === "checked" || k === "disabled" || k === "selected") el[k] = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid instanceof Node ? kid : String(kid));
    }
    return el;
  }
  const $ = (id) => document.getElementById(id);
  function fill(id, ...kids) {
    const el = typeof id === "string" ? $(id) : id;
    if (el) el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
  }
  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : res.status + " " + res.statusText);
    }
    return data;
  }
  const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
  const num = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US"));
  const VERDICT = { full: ["good", "full"], partial: ["warn", "partial"], miss: ["bad", "miss"] };

  function verdictChip(result) {
    if (!result) return h("span", { class: "ix-muted", text: "—" });
    if (result.error) return h("span", { class: "ix-chip bad", title: result.error, text: "error" });
    const kw = result.keywords;
    if (!kw || kw.coverage == null) return h("span", { class: "ix-muted", text: "no keywords" });
    const [cls, label] = VERDICT[kw.verdict];
    return h("span", { class: "ix-chip " + cls, title: kw.hit + " of " + kw.of + " keyword lines" },
      label + " · " + kw.hit + "/" + kw.of);
  }

  function sourceChip(result) {
    if (!result || result.error || !result.sources) return h("span", { class: "ix-muted", text: "—" });
    const s = result.sources;
    if (!s.checked) return h("span", { class: "ix-chip warn", title: s.reason, text: "not checked" });
    const found = s.retrieved
      ? h("span", { class: "ix-chip good", title: "excerpts in the expected place: " + s.right.map((n) => "[" + n + "]").join(" ") },
        "found @" + s.first_rank)
      : h("span", { class: "ix-chip bad", title: "no excerpt came from the expected place", text: "not found" });
    const cited = s.cited
      ? h("span", { class: "ix-chip good", text: "cited " + s.cited_right.map((n) => "[" + n + "]").join("") })
      : h("span", { class: "ix-chip " + (s.cited_any ? "warn" : "bad"),
        text: s.cited_any ? "cited others" : "no citation" });
    return h("span", { class: "ix-row rg-chips" }, found, cited);
  }

  // ------------------------------------------------------------ settings
  function loadSettings() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null"); } catch (e) { saved = null; }
    S.settings = Object.assign({}, S.ov.defaults, saved || {});
    delete S.settings.max_k;
    if (S.ov.models.indexOf(S.settings.model) === -1) S.settings.model = S.ov.defaults.model;
  }
  function saveSettings() {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(S.settings)); } catch (e) { /* private mode */ }
  }

  // ------------------------------------------------------------ skeleton
  function skeleton() {
    fill(root, h("div", { class: "ix-page" },
      h("div", { class: "ix-head" },
        h("h2", { text: "RAG: with the documents and without" }),
        h("span", { class: "ix-muted", text: "question → nearest chunks → joined onto the question → the LLM" }),
        h("span", { class: "ix-spacer" }),
        h("span", { id: "rg-status", class: "ix-row" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Run settings" }),
        h("p", { class: "ix-lead", text: "Both modes are the same agent - same model, the default system prompt, no memory, no tools, no personality - so the excerpts are the only difference between the two requests. The chat has its own RAG switch (top bar), with everything else the chat does still on." }),
        h("div", { class: "ix-row" },
          h("label", { class: "ix-muted ix-small" }, "index ", h("select", { id: "rg-index", onchange: onSettings })),
          h("label", { class: "ix-muted ix-small" }, "top k ",
            h("input", { type: "number", id: "rg-k", class: "ix-num", min: 1, style: "width:60px", onchange: onSettings })),
          h("label", { class: "ix-muted ix-small" }, "model ", h("select", { id: "rg-model", onchange: onSettings }))),
        h("div", { id: "rg-settings-error", class: "ix-error" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Ask both ways" }),
        h("p", { class: "ix-lead", text: "Any question, answered twice at the same time. Not saved - the control questions below are what gets kept." }),
        h("form", { class: "ix-row", onsubmit: (e) => { e.preventDefault(); askBoth(); } },
          h("input", { type: "text", id: "rg-question", placeholder: "e.g. почему структурный чанкинг выиграл у фиксированного?", style: "flex:1;min-width:220px" }),
          h("button", { type: "submit", class: "primary-btn", id: "rg-ask", text: "Ask" })),
        h("div", { id: "rg-ask-error", class: "ix-error" }),
        h("div", { id: "rg-ask-result" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Control questions" }),
        h("p", { class: "ix-lead" },
          "Each question says what a right answer contains - one ", h("b", { text: "keyword line" }),
          " per fact, ", h("span", { class: "ix-mono", text: "a | b" }), " meaning either - and where it lives. ",
          "An answer is scored by code: full = every line found, partial = at least half, miss = less. ",
          "With RAG it is also checked whether an excerpt came from the expected place, and whether the answer cites it."),
        h("div", { class: "ix-row", style: "margin-bottom:10px" },
          h("button", { type: "button", class: "primary-btn", id: "rg-run-all", onclick: runAll, text: "Run all" }),
          h("button", { type: "button", class: "ghost-btn", id: "rg-stop", onclick: stop, text: "Stop", disabled: true }),
          h("button", { type: "button", class: "ghost-btn", onclick: clearResults, text: "Clear results" }),
          h("span", { id: "rg-progress", class: "ix-muted ix-small" }),
          h("span", { class: "ix-spacer" }),
          h("button", { type: "button", class: "ghost-btn", onclick: addQuestion, text: "+ Add question" }),
          h("button", { type: "button", class: "ghost-btn", id: "rg-save", onclick: saveQuestions, text: "Save questions" })),
        h("div", { id: "rg-run-error", class: "ix-error" }),
        h("div", { id: "rg-summary", class: "ix-table-wrap" }),
        h("div", { id: "rg-rows", class: "ix-table-wrap", style: "margin-top:12px" }))));
  }

  // ------------------------------------------------------------ status
  function renderStatus() {
    const e = S.ov.embedder;
    const usable = S.ov.indexes.filter((i) => i.usable);
    fill("rg-status",
      h("span", { class: "ix-chip " + (e.status.ok ? "good" : "bad"), title: e.name, text: e.status.ok ? e.status.detail : e.status.detail }),
      usable.length
        ? usable.map((i) => h("span", { class: "ix-chip", text: i.name + " · " + num(i.chunks) + " chunks" }))
        : h("span", { class: "ix-chip bad", text: "no index built - see the Index tab" }));
  }

  function renderSettings() {
    const sel = $("rg-index");
    fill(sel, S.ov.indexes.map((i) => h("option", { value: i.name, text: i.name + (i.usable ? "" : " (other embedder)") })));
    if (!S.ov.indexes.some((i) => i.name === S.settings.index)) {
      sel.append(h("option", { value: S.settings.index, text: S.settings.index + " (not built)" }));
    }
    sel.value = S.settings.index;
    const k = $("rg-k");
    k.max = S.ov.defaults.max_k;
    k.value = S.settings.k;
    fill("rg-model", S.ov.models.map((m) => h("option", { value: m, text: m })));
    $("rg-model").value = S.settings.model;
  }

  function onSettings() {
    S.settings.index = $("rg-index").value;
    S.settings.k = Math.max(1, Math.min(S.ov.defaults.max_k, parseInt($("rg-k").value, 10) || S.ov.defaults.k));
    $("rg-k").value = S.settings.k;
    S.settings.model = $("rg-model").value;
    saveSettings();
  }

  // ------------------------------------------------------------ one answer
  function answerBlock(mode, result, question) {
    const box = h("div", { class: "rg-answer" });
    box.append(h("div", { class: "rg-answer-head" },
      h("b", { text: mode.title }), h("span", { class: "ix-muted ix-small", text: " · " + mode.lead }),
      h("span", { class: "ix-spacer" }),
      question && question.keywords && question.keywords.length ? verdictChip(result) : null));
    if (!result) return box;
    if (result.error && !result.answer) {
      box.append(h("div", { class: "ix-error", text: result.error }));
      return box;
    }
    box.append(h("div", { class: "rg-answer-text" + (result.error ? " error" : ""), text: result.answer }));
    const u = result.usage || {};
    box.append(h("div", { class: "ix-muted ix-small rg-meta",
      text: (result.elapsed_ms / 1000).toFixed(1) + " s · " + num(u.prompt_tokens) + " prompt tokens · " +
        num(u.completion_tokens) + " completion" + (u.reasoning_tokens ? " (" + num(u.reasoning_tokens) + " reasoning)" : "") }));
    if (result.rag) box.append(sourcesBlock(result.rag, result.sources));
    if (result.prompt) {
      box.append(h("details", { class: "ix-details rg-prompt" },
        h("summary", { text: "The message sent (" + num(result.prompt.split(/\s+/).length) + " words)" }),
        h("pre", { class: "ix-mono", text: result.prompt })));
    }
    return box;
  }

  function sourcesBlock(rag, scored) {
    const right = new Set((scored && scored.right) || []);
    const cited = rag.cited || [];
    return h("div", { class: "rg-sources" },
      h("div", { class: "ix-small" }, h("b", { text: "Sources" }),
        " · " + rag.hits.length + " excerpts from " + rag.index + ", " + num(rag.words) + " words · retrieved in " +
        rag.embed_ms + " ms · " + (cited.length ? "cites " + cited.map((n) => "[" + n + "]").join(" ") : "cites none")),
      rag.hits.map((hit) => h("div", { class: "rg-hit" + (hit.cited ? " cited" : "") + (right.has(hit.n) ? " right" : "") },
        h("div", { class: "rg-hit-head" },
          h("span", { class: "rg-n", text: "[" + hit.n + "]" }),
          hit.source + (hit.section ? " › " + hit.section : ""),
          h("span", { class: "ix-muted", text: " · " + Number(hit.score).toFixed(3) }),
          hit.cited ? h("span", { class: "ix-chip good rg-tag", text: "cited" }) : null,
          right.has(hit.n) ? h("span", { class: "ix-chip rg-tag", text: "expected place" }) : null),
        h("div", { class: "rg-hit-preview ix-muted ix-small", text: hit.preview }))));
  }

  function pair(results, question) {
    return h("div", { class: "rg-pair" }, MODES.map((m) => answerBlock(m, results[m.key], question)));
  }

  // ------------------------------------------------------------ ask
  async function askBoth() {
    const question = $("rg-question").value.trim();
    if (!question) return;
    const btn = $("rg-ask");
    btn.disabled = true;
    btn.textContent = "Asking…";
    fill("rg-ask-error");
    fill("rg-ask-result", h("div", { class: "ix-muted", text: "Two answers on the way - the slower one sets the pace." }));
    try {
      const res = await api("POST", "/api/rag/ask", Object.assign({ question }, S.settings));
      fill("rg-ask-result", pair(res));
    } catch (err) {
      fill("rg-ask-result");
      fill("rg-ask-error", err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Ask";
    }
  }

  // ------------------------------------------------------------ summary
  function renderSummary() {
    const s = S.summary;
    if (!s || (!s.plain.answered && !s.rag.answered)) {
      fill("rg-summary", h("div", { class: "ix-muted", text: "No run yet. Run all asks every question both ways - about two answers' time per question." }));
      return;
    }
    const best = (a, b, higher) => (a == null || b == null || a === b ? [false, false] : higher ? [a > b, b > a] : [a < b, b < a]);
    const row = (label, a, b, fmt, higher, hint) => {
      const [ba, bb] = higher == null ? [false, false] : best(a, b, higher);
      return h("tr", null, h("td", { title: hint || "" }, label),
        h("td", { class: "num" + (ba ? " best" : ""), text: fmt(a) }),
        h("td", { class: "num" + (bb ? " best" : ""), text: fmt(b) }));
    };
    const p = s.plain, r = s.rag;
    const count = (v) => (v == null ? "—" : String(v));
    const outOf = (v, n) => (n ? v + " of " + n : "—");
    fill("rg-summary", h("table", { class: "ix-table rg-summary" },
      h("thead", null, h("tr", null, h("th", { text: "" }), h("th", { class: "num", text: "Without RAG" }), h("th", { class: "num", text: "With RAG" }))),
      h("tbody", null,
        row("questions answered", p.answered, r.answered, count, null),
        row("keyword coverage, mean", p.coverage, r.coverage, pct, true, "share of keyword lines found, averaged over the questions"),
        row("full", p.full, r.full, count, true),
        row("partial", p.partial, r.partial, count, null),
        row("miss", p.miss, r.miss, count, false),
        h("tr", null, h("td", { text: "expected source among the excerpts" }), h("td", { class: "num ix-muted", text: "—" }),
          h("td", { class: "num", text: outOf(r.retrieved, r.sources_checked) })),
        h("tr", null, h("td", { text: "answer cites the expected source" }), h("td", { class: "num ix-muted", text: "—" }),
          h("td", { class: "num", text: outOf(r.cited, r.sources_checked) })),
        h("tr", null, h("td", { text: "answer cites anything" }), h("td", { class: "num ix-muted", text: "—" }),
          h("td", { class: "num", text: outOf(r.cited_any, r.answered) })),
        row("prompt tokens, mean", p.prompt_tokens, r.prompt_tokens, num, null, "what the excerpts cost per question"),
        row("completion tokens, mean", p.completion_tokens, r.completion_tokens, num, null),
        row("seconds per answer, mean", p.seconds, r.seconds, (v) => (v == null ? "—" : v.toFixed(1)), null))));
  }

  // ------------------------------------------------------------ rows
  function renderRows() {
    const rows = S.questions.map((q, i) => {
      const res = S.results[q.id];
      const stale = res && res.question !== q.question;
      const running = S.running && S.running.current === q.id;
      const tr = h("tr", { class: "clickable", onclick: () => { S.open[q.id] = !S.open[q.id]; renderRows(); } },
        h("td", { class: "num ix-muted", text: String(i + 1) }),
        h("td", null, q.question, stale ? h("span", { class: "ix-chip warn rg-tag", text: "edited since the run" }) : null),
        h("td", null, running ? h("span", { class: "ix-muted", text: "running…" }) : verdictChip(res && res.plain)),
        h("td", null, running ? null : verdictChip(res && res.rag)),
        h("td", null, running ? null : sourceChip(res && res.rag)),
        h("td", null, h("button", { type: "button", class: "ghost-btn rg-run-one", disabled: !!S.running || S.dirty,
          title: S.dirty ? "Save the questions first" : "",
          onclick: (e) => { e.stopPropagation(); runQueue([q.id]); }, text: "Run" })));
      const out = [tr];
      if (S.open[q.id]) out.push(h("tr", { class: "rg-detail" }, h("td", { colspan: 6 }, detail(q, i, res))));
      return out;
    });
    fill("rg-rows", h("table", { class: "ix-table" },
      h("thead", null, h("tr", null, h("th", { text: "#" }), h("th", { text: "Question" }), h("th", { text: "Without RAG" }),
        h("th", { text: "With RAG" }), h("th", { text: "Source" }), h("th", { text: "" }))),
      h("tbody", null, rows)));
  }

  function keywordTable(q, res) {
    if (!q.keywords.length) return h("div", { class: "ix-muted ix-small", text: "No keyword lines - nothing to score." });
    const line = (mode, i) => {
      const r = res && res[mode];
      if (!r || r.error || !r.keywords) return h("td", { class: "ix-muted", text: "—" });
      const found = r.keywords.lines[i] && r.keywords.lines[i].found;
      return h("td", { class: found ? "rg-yes" : "rg-no", text: found ? "✓ " + found : "✗" });
    };
    return h("table", { class: "ix-table rg-kw" },
      h("thead", null, h("tr", null, h("th", { text: "Must contain (any of)" }), h("th", { text: "Without RAG" }), h("th", { text: "With RAG" }))),
      h("tbody", null, q.keywords.map((alts, i) => h("tr", null,
        h("td", { class: "ix-mono", text: alts.join(" | ") }), line("plain", i), line("rag", i)))));
  }

  function detail(q, i, res) {
    return h("div", { class: "rg-detail-body" },
      h("div", { class: "rg-expect" },
        h("div", null, h("b", { text: "Expected: " }), q.expected || h("span", { class: "ix-muted", text: "—" })),
        h("div", { class: "ix-small" }, h("b", { text: "Sources: " }),
          q.sources.length ? q.sources.map((s, j) => [j ? "; " : "", h("span", { class: "ix-mono", text: s.section ? s.source + " › " + s.section : s.source })])
            : h("span", { class: "ix-muted", text: "none given" }))),
      keywordTable(q, res),
      res ? pair(res, q) : h("div", { class: "ix-muted", text: "Not run yet." }),
      h("details", { class: "ix-details" }, h("summary", { text: "Edit this question" }), editor(q, i)));
  }

  // ------------------------------------------------------------ editor
  async function loadDocs() {
    if (S.docs) return S.docs;
    try {
      const ov = await api("GET", "/api/index");
      S.docs = ov.corpus.documents.map((d) => d.source);
    } catch (e) {
      S.docs = [];
    }
    return S.docs;
  }
  async function loadSections(source) {
    if (!source) return [];
    if (!S.sections[source]) {
      try {
        const res = await api("GET", "/api/index/sections?source=" + encodeURIComponent(source));
        S.sections[source] = res.sections.map((s) => s.label);
      } catch (e) {
        S.sections[source] = [];
      }
    }
    return S.sections[source];
  }

  function markDirty() {
    S.dirty = true;
    $("rg-save").classList.add("primary-btn");
    $("rg-save").textContent = "Save questions *";
  }

  function sourceRow(q, s, j) {
    const fileSel = h("select", { class: "rg-src-file", onchange: async (e) => {
      s.source = e.target.value; s.section = ""; markDirty(); await fillSections(); } });
    const secSel = h("select", { class: "rg-src-section", onchange: (e) => { s.section = e.target.value; markDirty(); } });
    async function fillSections() {
      const labels = await loadSections(s.source);
      fill(secSel, h("option", { value: "", text: "(the whole file)" }), labels.map((l) => h("option", { value: l, text: l })));
      if (s.section && labels.indexOf(s.section) === -1) secSel.append(h("option", { value: s.section, text: s.section + " (not in the corpus)" }));
      secSel.value = s.section || "";
    }
    loadDocs().then((docs) => {
      fill(fileSel, docs.map((d) => h("option", { value: d, text: d })));
      if (docs.indexOf(s.source) === -1) fileSel.append(h("option", { value: s.source, text: s.source + " (not in the corpus)" }));
      fileSel.value = s.source;
      fillSections();
    });
    return h("div", { class: "ix-expected" }, fileSel, secSel,
      h("button", { type: "button", class: "ghost-btn", text: "×", title: "Remove this source",
        onclick: () => { q.sources.splice(j, 1); markDirty(); renderRows(); } }));
  }

  function editor(q, i) {
    const keywordsText = q.keywords.map((alts) => alts.join(" | ")).join("\n");
    return h("div", { class: "rg-editor" },
      h("label", { class: "ix-small ix-muted", text: "Question" }),
      h("input", { type: "text", value: q.question, oninput: (e) => { q.question = e.target.value; markDirty(); } }),
      h("label", { class: "ix-small ix-muted", text: "Expected - what a right answer says" }),
      h("textarea", { rows: 2, value: q.expected, oninput: (e) => { q.expected = e.target.value; markDirty(); } }),
      h("label", { class: "ix-small ix-muted", text: "Keyword lines - one fact per line, alternatives separated by |" }),
      h("textarea", { rows: Math.max(3, q.keywords.length + 1), class: "ix-mono", value: keywordsText,
        oninput: (e) => {
          q.keywords = e.target.value.split("\n").map((l) => l.split("|").map((a) => a.trim()).filter(Boolean)).filter((l) => l.length);
          markDirty();
        } }),
      h("label", { class: "ix-small ix-muted", text: "Sources - where the answer lives" }),
      q.sources.map((s, j) => sourceRow(q, s, j)),
      h("div", { class: "ix-row" },
        h("button", { type: "button", class: "ghost-btn", text: "+ source",
          onclick: async () => { const docs = await loadDocs(); q.sources.push({ source: docs[0] || "README.md", section: "" }); markDirty(); renderRows(); } }),
        h("span", { class: "ix-spacer" }),
        h("button", { type: "button", class: "ghost-btn", text: "Delete question",
          onclick: () => { S.questions.splice(i, 1); markDirty(); renderRows(); } })));
  }

  function addQuestion() {
    const q = { id: "", question: "Новый вопрос?", expected: "", keywords: [], sources: [] };
    S.questions.push(q);
    S.open[""] = true;
    markDirty();
    renderRows();
  }

  async function saveQuestions() {
    fill("rg-run-error");
    try {
      const ov = await api("PUT", "/api/rag/questions", { questions: S.questions });
      apply(ov);
      S.dirty = false;
      $("rg-save").classList.remove("primary-btn");
      $("rg-save").textContent = "Save questions";
      renderRows();
    } catch (err) {
      fill("rg-run-error", err.message);
    }
  }

  // ------------------------------------------------------------ running
  function runAll() { runQueue(S.questions.map((q) => q.id)); }

  function stop() { if (S.running) S.running.stop = true; }

  async function runQueue(ids) {
    if (S.running) return;
    if (S.dirty) { fill("rg-run-error", "Save the questions first - a run asks the saved set."); return; }
    fill("rg-run-error");
    S.running = { queue: ids.slice(), stop: false, current: null, done: 0 };
    $("rg-run-all").disabled = true;
    $("rg-stop").disabled = false;
    try {
      for (const id of ids) {
        if (S.running.stop) break;
        S.running.current = id;
        fill("rg-progress", "question " + (S.running.done + 1) + " of " + ids.length + "…");
        renderRows();
        try {
          const res = await api("POST", "/api/rag/check/" + encodeURIComponent(id), S.settings);
          S.results[id] = res.row;
          S.summary = res.summary;
        } catch (err) {
          fill("rg-run-error", "“" + id + "”: " + err.message);
          if (/index|Ollama|embedder/i.test(err.message)) break;
        }
        S.running.done += 1;
        renderSummary();
      }
    } finally {
      const done = S.running.done;
      S.running = null;
      $("rg-run-all").disabled = false;
      $("rg-stop").disabled = true;
      fill("rg-progress", done ? done + " run" : "");
      renderRows();
    }
  }

  async function clearResults() {
    if (S.running) return;
    try {
      apply(await api("DELETE", "/api/rag/results"));
      renderRows();
    } catch (err) {
      fill("rg-run-error", err.message);
    }
  }

  // ------------------------------------------------------------ boot
  function apply(ov) {
    S.ov = ov;
    S.questions = JSON.parse(JSON.stringify(ov.questions));
    S.results = ov.results || {};
    S.summary = ov.summary;
    renderStatus();
    renderSummary();
  }

  async function start() {
    if (S.started) return;
    S.started = true;
    skeleton();
    try {
      apply(await api("GET", "/api/rag"));
      loadSettings();
      renderSettings();
      renderRows();
    } catch (err) {
      fill(root, h("div", { class: "ix-page" }, h("div", { class: "ix-error", text: "RAG tab failed to load: " + err.message })));
      S.started = false;
    }
  }

  // Coming back to the tab after a build in the Index tab: the indexes and
  // the embedder are re-read, the questions being edited are left alone.
  async function revisit() {
    if (!S.started) return start();
    if (S.running) return;
    try {
      const ov = await api("GET", "/api/rag");
      S.ov.indexes = ov.indexes;
      S.ov.embedder = ov.embedder;
      renderStatus();
      renderSettings();
    } catch (err) { /* the tab keeps what it had */ }
  }

  for (const tab of document.querySelectorAll('.tab[data-view="rag"]')) tab.addEventListener("click", revisit);
  if (root.classList.contains("active")) start();
  else new MutationObserver((_, obs) => {
    if (root.classList.contains("active")) { obs.disconnect(); start(); }
  }).observe(root, { attributes: true, attributeFilter: ["class"] });
})();
