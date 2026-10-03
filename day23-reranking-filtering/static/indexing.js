// Day 21: the Index tab - the document pipeline, its two chunking strategies
// side by side, and a retrieval check you can extend with your own questions.
//
// Self-contained on purpose: it shares nothing with the chat code in
// index.html except the colour tokens and the tab switch, and talks only to
// /api/index/*. Since day 22 the chat's RAG mode and the RAG tab read the
// indexes built here.
(function () {
  "use strict";

  const root = document.getElementById("index-view");
  if (!root) return;

  const PARAMS_KEY = "index-strategy-params";

  const STRATEGIES = {
    fixed: {
      title: "Fixed size",
      lead: "Windows of N words over the whole file. Ignores headings, so a chunk ends wherever the count runs out.",
      params: [
        { key: "size", label: "Chunk size, words", min: 20, max: 1000, step: 10 },
        { key: "overlap", label: "Overlap M, words", min: 0, max: (p) => p.size - 1, step: 5, slider: true,
          hint: "Each chunk starts M words before the previous one ended. More overlap = fewer broken sentences, more duplicate text." },
        { key: "with_context", label: "Prepend title › section to the embedded text", bool: true },
      ],
    },
    structural: {
      title: "Structural",
      lead: "One chunk per section (heading, def, class, file). Long sections are split on paragraphs, short ones merged with a neighbour.",
      params: [
        { key: "max_words", label: "Max words per chunk", min: 50, max: 1500, step: 10 },
        { key: "min_words", label: "Merge sections under, words", min: 0, max: (p) => p.max_words - 1, step: 5 },
        { key: "overlap", label: "Overlap M, words", min: 0, max: (p) => p.max_words - 1, step: 5, slider: true,
          hint: "Used only when a long section is split: the next piece repeats the last M words of the one before." },
        { key: "with_context", label: "Prepend title › section to the embedded text", bool: true },
      ],
    },
  };
  const NAMES = Object.keys(STRATEGIES);

  const S = {
    ov: null,            // GET /api/index
    params: {},          // strategy -> params being edited
    preview: {},         // strategy -> stats with the edited params
    questions: [],
    dirty: false,
    sections: {},        // source -> [{label, depth}]
    evalResult: null,
    openQuestion: null,
    browse: { strategy: "structural", source: "" },
    polling: null,
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
    el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
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

  function showError(id, err) { fill(id, err ? String(err.message || err) : null); }
  const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
  const num = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US"));
  const fixed2 = (v) => (v == null ? "—" : Number(v).toFixed(2));
  function ago(ts) {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return new Date(ts * 1000).toLocaleDateString();
  }
  function mb(bytes) { return (bytes / 1048576).toFixed(1) + " MB"; }
  function sameParams(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  const indexOf = (name) => (S.ov ? S.ov.indexes.find((i) => i.name === name) : null);
  function debounce(fn, ms) {
    let t = null;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  // ------------------------------------------------------------ skeleton
  function skeleton() {
    fill(root, h("div", { class: "ix-page" },
      h("div", { class: "ix-head" },
        h("h2", { text: "Document index" }),
        h("span", { class: "ix-muted", text: "Loader → Document → Chunker → Embedder → SQLite. Read by the chat's RAG mode and the RAG tab." }),
        h("span", { class: "ix-spacer" }),
        h("span", { id: "ix-status", class: "ix-row" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Pipeline" }),
        h("div", { id: "ix-pipeline", class: "ix-pipeline" }),
        h("div", { id: "ix-job" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Corpus" }),
        h("p", { class: "ix-lead" }, "Glob patterns relative to the project folder, one per line. Uploaded files are always included, as ",
          h("span", { class: "ix-mono", text: "uploads/<name>" }), "."),
        h("textarea", { id: "ix-patterns", class: "ix-patterns", rows: 3, spellcheck: "false" }),
        h("div", { class: "ix-row", style: "margin-top:8px" },
          h("button", { type: "button", class: "ghost-btn", onclick: savePatterns, text: "Save patterns" }),
          h("button", { type: "button", class: "ghost-btn", onclick: () => $("ix-file").click(), text: "Upload files…" }),
          h("input", { type: "file", id: "ix-file", multiple: true, accept: ".md,.markdown,.txt,.py", style: "display:none", onchange: uploadFiles }),
          h("span", { id: "ix-uploads", class: "ix-row" })),
        h("div", { id: "ix-corpus-error", class: "ix-error" }),
        h("details", { class: "ix-details" }, h("summary", { id: "ix-docs-summary", text: "Documents" }),
          h("div", { id: "ix-docs", class: "ix-table-wrap" }))),

      h("section", { class: "ix-card" },
        h("h3", { text: "Chunking strategies" }),
        h("p", { class: "ix-lead", text: "Stats under each strategy are recomputed as you move the controls - chunking only, no model involved. Build embeds the chunks and writes the index; unchanged chunks come from the embedding cache." }),
        h("div", { class: "ix-row", style: "margin-bottom:10px" },
          h("button", { type: "button", class: "primary-btn", id: "ix-build-all", onclick: () => build(NAMES), text: "Build both" }),
          h("button", { type: "button", class: "ghost-btn", onclick: resetAll, text: "Reset all indexes" }),
          h("button", { type: "button", class: "ghost-btn", onclick: clearCache, text: "Clear embedding cache" }),
          h("span", { id: "ix-cache", class: "ix-muted ix-small" })),
        h("div", { id: "ix-strategies", class: "ix-cols" }),
        h("div", { id: "ix-strat-error", class: "ix-error" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Comparison" }),
        h("p", { class: "ix-lead", text: "Built indexes only. Structure: what each strategy did to the text. Retrieval: from the last run of the check below. Best value per row in green." }),
        h("div", { id: "ix-compare", class: "ix-table-wrap" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Retrieval check" }),
        h("p", { class: "ix-lead", text: "Each question names where its answer lives: a file and, optionally, a section (with everything under it). A retrieved chunk counts when at least half of it lies there, or it covers at least half of that place." }),
        h("div", { class: "ix-row", style: "margin-bottom:10px" },
          h("button", { type: "button", class: "primary-btn", id: "ix-run-eval", onclick: runEval, text: "Run check" }),
          h("label", { class: "ix-muted ix-small" }, "depth ",
            h("input", { type: "number", id: "ix-depth", class: "ix-num", value: 10, min: 1, max: 50, style: "width:60px" })),
          h("span", { class: "ix-spacer" }),
          h("button", { type: "button", class: "ghost-btn", onclick: addQuestion, text: "+ Add question" }),
          h("button", { type: "button", class: "ghost-btn", id: "ix-save-q", onclick: () => saveQuestions(), text: "Save questions" })),
        h("div", { id: "ix-eval-error", class: "ix-error" }),
        h("div", { id: "ix-eval" }),
        h("details", { class: "ix-details", id: "ix-q-details" }, h("summary", { id: "ix-q-summary", text: "Questions" }),
          h("div", { id: "ix-questions", class: "ix-questions" }))),

      h("section", { class: "ix-card" },
        h("h3", { text: "Search" }),
        h("p", { class: "ix-lead", text: "One query against every built index, side by side - the same cosine search the check uses." }),
        h("form", { class: "ix-row", onsubmit: (e) => { e.preventDefault(); search(); } },
          h("input", { type: "text", id: "ix-query", placeholder: "e.g. как агент выбирает MCP-сервер", style: "flex:1;min-width:200px" }),
          h("label", { class: "ix-muted ix-small" }, "k ",
            h("input", { type: "number", id: "ix-k", class: "ix-num", value: 5, min: 1, max: 20, style: "width:56px" })),
          h("button", { type: "submit", class: "primary-btn", text: "Search" })),
        h("div", { id: "ix-search-error", class: "ix-error" }),
        h("div", { id: "ix-search", class: "ix-hits", style: "margin-top:10px" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Chunks" }),
        h("p", { class: "ix-lead" }, "How the current settings cut one document. ",
          h("mark", { style: "background:var(--ix-overlap)", text: "Highlighted" }), " text repeats the previous chunk (overlap); ",
          h("span", { style: "color:var(--accent)", text: "blue" }), " is the context prefix that is embedded but not stored as text."),
        h("div", { class: "ix-row" },
          h("select", { id: "ix-browse-strategy", style: "width:auto", onchange: (e) => { S.browse.strategy = e.target.value; loadBrowser(); } },
            NAMES.map((n) => h("option", { value: n, text: STRATEGIES[n].title }))),
          h("select", { id: "ix-browse-source", style: "width:auto;max-width:100%", onchange: (e) => { S.browse.source = e.target.value; loadBrowser(); } }),
          h("span", { id: "ix-browse-count", class: "ix-muted ix-small" })),
        h("div", { id: "ix-browser", class: "ix-chunks", style: "margin-top:10px" }))));
  }

  // ------------------------------------------------------------ overview
  async function refresh() {
    S.ov = await api("GET", "/api/index");
    renderStatus();
    renderPipeline();
    renderCorpus();
    renderStrategyStatus();
    renderCompare();
    if (S.ov.job && S.ov.job.running) startPolling();
  }

  function renderStatus() {
    const e = S.ov.embedder;
    fill("ix-status",
      h("span", { class: "ix-chip " + (e.status.ok ? "good" : "bad"), title: e.status.detail },
        (e.status.ok ? "● " : "✕ ") + e.name),
      !e.status.ok && h("span", { class: "ix-muted ix-small", text: e.status.detail }));
  }

  function renderPipeline() {
    const ov = S.ov, c = ov.corpus;
    const sections = c.documents.reduce((a, d) => a + d.sections, 0);
    const built = ov.indexes;
    const dims = built.map((i) => i.dim).filter(Boolean);
    const cached = ov.cache.reduce((a, r) => a + r.vectors, 0);
    const job = ov.job || {};
    const active = job.running ? { load: 0, chunk: 2, embed: 3, store: 4 }[job.stage] : -1;
    const stages = [
      ["Loader", c.documents.length + " files", c.formats.join(", ") + " · loaders " + c.loaders.join(" ") +
        (c.skipped.length ? " · " + c.skipped.length + " skipped" : "")],
      ["Document", num(sections) + " sections", num(c.words) + " words · corpus " + c.sha],
      ["Chunker", built.length ? built.map((i) => i.name + " " + num(i.stats.chunks)).join(" · ") : "not built",
        built.length ? built.map((i) => i.name + " overlap " + i.params.overlap).join(" · ") : "fixed | structural"],
      ["Embedder", ov.embedder.model || ov.embedder.name,
        (dims.length ? dims[0] + "-d · " : "") + (ov.embedder.status.ok ? "ready" : "unavailable") + " · cache " + num(cached)],
      ["SQLite", mb(ov.db.bytes), ov.db.path],
    ];
    fill("ix-pipeline", stages.map(([name, big, sub], i) =>
      h("div", { class: "ix-stage" + (i === active ? " active" : "") },
        h("div", { class: "name", text: name }), h("div", { class: "big", text: big }), h("div", { class: "sub", text: sub }))));
    renderJob();
  }

  function renderJob() {
    const job = (S.ov && S.ov.job) || {};
    if (!job.stage) return fill("ix-job");
    if (job.running) {
      const share = job.total ? job.done / job.total : 0;
      fill("ix-job",
        h("div", { class: "ix-progress" }, h("div", { style: "width:" + Math.round(share * 100) + "%" })),
        h("div", { class: "ix-muted ix-small", style: "margin-top:4px" },
          "Building " + (job.current || "") + " · " + job.stage +
          (job.total ? " " + job.done + "/" + job.total : "") +
          (job.queue && job.queue.length > 1 ? " · queue: " + job.queue.join(", ") : "")));
    } else if (job.error) {
      fill("ix-job", h("div", { class: "ix-error", text: "Build failed: " + job.error }));
    } else if (job.stage === "done" && job.finished_at) {
      fill("ix-job", h("div", { class: "ix-muted ix-small", style: "margin-top:8px" },
        "Last build: " + (job.built || []).join(", ") + " · " + Math.round(job.finished_at - job.started_at) + " s · " + ago(job.finished_at)));
    }
    const running = !!job.running;
    for (const b of root.querySelectorAll("[data-build]")) b.disabled = running;
    const all = $("ix-build-all");
    if (all) all.disabled = running;
  }

  // ------------------------------------------------------------ corpus
  function renderCorpus() {
    const c = S.ov.corpus;
    const ta = $("ix-patterns");
    if (document.activeElement !== ta) ta.value = c.patterns.join("\n");
    fill("ix-uploads", c.uploads.map((name) =>
      h("span", { class: "ix-chip" }, name,
        h("button", { type: "button", class: "icon-btn close", title: "Remove upload", text: "✕",
          onclick: async () => { S.ov = await api("DELETE", "/api/index/uploads/" + encodeURIComponent(name)); refreshAfterCorpus(); } }))));
    $("ix-docs-summary").textContent = "Documents: " + c.documents.length + " files, " + num(c.words) + " words" +
      (c.skipped.length ? " · " + c.skipped.length + " skipped" : "");
    fill("ix-docs", h("table", { class: "ix-table" },
      h("thead", {}, h("tr", {}, h("th", { text: "Source" }), h("th", { text: "Format" }), h("th", { text: "Title" }),
        h("th", { class: "num", text: "Sections" }), h("th", { class: "num", text: "Words" }))),
      h("tbody", {},
        c.documents.map((d) => h("tr", {}, h("td", { class: "ix-mono", text: d.source }), h("td", { text: d.format }),
          h("td", { text: d.title.length > 70 ? d.title.slice(0, 70) + "…" : d.title }),
          h("td", { class: "num", text: d.sections }), h("td", { class: "num", text: num(d.words) }))),
        c.skipped.map((s) => h("tr", {}, h("td", { class: "ix-mono", text: s.source }),
          h("td", { colspan: 4, class: "ix-muted", text: "skipped: " + s.reason }))))));
    const sel = $("ix-browse-source");
    const keep = S.browse.source;
    fill(sel, c.documents.map((d) => h("option", { value: d.source, text: d.source })));
    S.browse.source = c.documents.some((d) => d.source === keep) ? keep :
      (c.documents.find((d) => d.source === "README.md") || c.documents[0] || {}).source || "";
    sel.value = S.browse.source;
  }

  async function refreshAfterCorpus() {
    showError("ix-corpus-error", null);
    await refresh();
    for (const n of NAMES) runPreview(n);
    loadBrowser();
    renderQuestions();
  }

  async function savePatterns() {
    try {
      const patterns = $("ix-patterns").value.split("\n").map((s) => s.trim()).filter(Boolean);
      S.ov = await api("PUT", "/api/index/corpus", { patterns });
      await refreshAfterCorpus();
    } catch (err) { showError("ix-corpus-error", err); }
  }

  async function uploadFiles(e) {
    try {
      for (const file of e.target.files) {
        const content = await file.text();
        await api("POST", "/api/index/uploads", { name: file.name, content });
      }
      e.target.value = "";
      await refreshAfterCorpus();
    } catch (err) { showError("ix-corpus-error", err); }
  }

  // ------------------------------------------------------------ strategies
  function loadParams() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(PARAMS_KEY) || "{}"); } catch (_) { saved = {}; }
    for (const n of NAMES) {
      const built = indexOf(n);
      S.params[n] = Object.assign({}, S.ov.strategies[n].defaults, built ? built.params : {}, saved[n] || {});
    }
  }

  function saveParams() {
    try { localStorage.setItem(PARAMS_KEY, JSON.stringify(S.params)); } catch (_) { /* private mode */ }
  }

  function renderStrategies() {
    fill("ix-strategies", NAMES.map((name) => {
      const spec = STRATEGIES[name];
      const p = S.params[name];
      const rows = spec.params.map((param) => {
        if (param.bool) {
          return h("label", { class: "ix-row ix-small" },
            h("input", { type: "checkbox", checked: !!p[param.key],
              onchange: (e) => setParam(name, param.key, e.target.checked) }), param.label);
        }
        const max = typeof param.max === "function" ? param.max(p) : param.max;
        const numberInput = h("input", { type: "number", min: param.min, max, step: 1, value: p[param.key],
          "data-param": name + "." + param.key,
          onchange: (e) => setParam(name, param.key, e.target.value) });
        const slider = h("input", { type: "range", min: param.min, max, step: param.step, value: p[param.key],
          "data-param": name + "." + param.key,
          oninput: (e) => setParam(name, param.key, e.target.value) });
        return h("div", { class: "ix-param" },
          h("label", { text: param.label }), slider, numberInput,
          param.hint && h("div", { class: "hint", text: param.hint }));
      });
      return h("div", { class: "ix-strategy" },
        h("h4", {}, spec.title, h("span", { id: "ix-built-" + name })),
        h("div", { class: "ix-muted ix-small", text: spec.lead }),
        rows,
        h("div", { class: "ix-muted ix-small", text: "With these settings:" }),
        h("div", { id: "ix-pstats-" + name, class: "ix-stats" }),
        h("div", { id: "ix-pstatus-" + name, class: "ix-small ix-muted" }),
        h("div", { class: "ix-row" },
          h("button", { type: "button", class: "primary-btn", "data-build": name, onclick: () => build([name]), text: "Build index" }),
          h("button", { type: "button", class: "ghost-btn", onclick: () => resetIndex(name), text: "Reset index" }),
          h("button", { type: "button", class: "ghost-btn", text: "Show chunks",
            onclick: () => { S.browse.strategy = name; $("ix-browse-strategy").value = name; loadBrowser(); $("ix-browser").scrollIntoView({ behavior: "smooth", block: "center" }); } })));
    }));
    renderStrategyStatus();
  }

  function setParam(name, key, value) {
    const spec = STRATEGIES[name].params.find((x) => x.key === key);
    const p = S.params[name];
    if (spec.bool) p[key] = !!value;
    else {
      const max = typeof spec.max === "function" ? spec.max(p) : spec.max;
      p[key] = Math.max(spec.min, Math.min(max, Math.round(Number(value) || 0)));
    }
    // Clamp the dependants (overlap < size, min < max) and redraw their limits.
    for (const other of STRATEGIES[name].params) {
      if (other.bool || typeof other.max !== "function") continue;
      const max = other.max(p);
      if (p[other.key] > max) p[other.key] = max;
    }
    for (const input of root.querySelectorAll('[data-param^="' + name + '."]')) {
      const k = input.dataset.param.split(".")[1];
      const other = STRATEGIES[name].params.find((x) => x.key === k);
      if (typeof other.max === "function") input.max = other.max(p);
      if (document.activeElement !== input || input.type === "range") input.value = p[k];
    }
    saveParams();
    renderStrategyStatus();
    previewSoon(name);
  }

  const previewTimers = {};
  function previewSoon(name) {
    clearTimeout(previewTimers[name]);
    previewTimers[name] = setTimeout(() => {
      runPreview(name);
      if (S.browse.strategy === name) loadBrowser();
    }, 250);
  }

  async function runPreview(name) {
    try {
      const res = await api("POST", "/api/index/preview", { strategy: name, params: S.params[name] });
      S.preview[name] = res.stats;
      showError("ix-strat-error", null);
    } catch (err) { showError("ix-strat-error", err); }
    renderPreviewStats(name);
  }

  function renderPreviewStats(name) {
    const s = S.preview[name];
    const el = $("ix-pstats-" + name);
    if (!el) return;
    if (!s) return fill(el);
    const cell = (v, k, title) => h("div", { class: "ix-stat", title }, h("div", { class: "v", text: v }), h("div", { class: "k", text: k }));
    fill(el,
      cell(num(s.chunks), "chunks"),
      cell(s.words_median + " / " + s.words_p95, "median / p95 words"),
      cell(pct(s.mid_sentence_share), "cut mid-sentence", "Chunk ends neither at a sentence end nor at a blank line"),
      cell(pct(s.cross_section_share), "cross a boundary", "Chunk runs across a section boundary it was not built around (planned structural merges are not counted)"),
      cell(num(s.split_code_blocks), "split code blocks", "Odd number of ``` fences inside the chunk"),
      cell("×" + s.redundancy, "redundancy", "Words embedded / words in the corpus - what overlap costs"),
      cell(num(s.tiny_chunks), "tiny (<30 words)"));
  }

  function renderStrategyStatus() {
    if (!S.ov) return;
    for (const name of NAMES) {
      const built = indexOf(name);
      const chipEl = $("ix-built-" + name);
      const statusEl = $("ix-pstatus-" + name);
      if (!chipEl || !statusEl) continue;
      if (!built) {
        fill(chipEl, h("span", { class: "ix-chip", text: "not built" }));
        fill(statusEl, "No index yet - Build embeds " + (S.preview[name] ? num(S.preview[name].chunks) + " chunks" : "the chunks") + ".");
        continue;
      }
      const changed = !sameParams(normalise(name, built.params), normalise(name, S.params[name]));
      const chips = [h("span", { class: "ix-chip good", text: "built " + ago(built.built_at) })];
      if (built.stale) chips.push(h("span", { class: "ix-chip warn", text: "corpus changed" }));
      if (built.embedder_changed) chips.push(h("span", { class: "ix-chip warn", text: "other embedder" }));
      if (changed) chips.push(h("span", { class: "ix-chip warn", text: "settings changed" }));
      fill(chipEl, chips);
      fill(statusEl, "Built with " + Object.entries(built.params).map(([k, v]) => k + "=" + v).join(", ") +
        " · " + num(built.stats.chunks) + " chunks · embedded " + num(built.stats.embedded_now) +
        " (+" + num(built.stats.from_cache) + " cached) in " + built.timings.embed + " s" +
        (changed ? " · rebuild to apply the new settings" : ""));
    }
  }

  function normalise(name, params) {
    const out = {};
    for (const spec of STRATEGIES[name].params) out[spec.key] = spec.bool ? !!params[spec.key] : Number(params[spec.key]);
    return out;
  }

  async function build(names) {
    showError("ix-strat-error", null);
    try {
      S.ov.job = await api("POST", "/api/index/build", { items: names.map((n) => ({ strategy: n, params: S.params[n] })) });
      renderJob();
      startPolling();
    } catch (err) { showError("ix-strat-error", err); }
  }

  function startPolling() {
    if (S.polling) return;
    S.polling = setInterval(async () => {
      try {
        const job = await api("GET", "/api/index/job");
        S.ov.job = job;
        renderPipeline();
        if (!job.running) {
          clearInterval(S.polling);
          S.polling = null;
          await refresh();
        }
      } catch (err) {
        clearInterval(S.polling);
        S.polling = null;
        showError("ix-strat-error", err);
      }
    }, 700);
  }

  async function resetIndex(name) {
    if (!confirm("Drop the " + name + " index - its chunks and vectors? The embedding cache is kept.")) return;
    try { S.ov = await api("DELETE", "/api/index/indexes/" + name); await refresh(); } catch (err) { showError("ix-strat-error", err); }
  }

  async function resetAll() {
    if (!confirm("Drop every index? The embedding cache is kept, so a rebuild with the same settings is fast.")) return;
    try { S.ov = await api("DELETE", "/api/index/indexes"); await refresh(); } catch (err) { showError("ix-strat-error", err); }
  }

  async function clearCache() {
    if (!confirm("Forget every cached vector? The next build re-embeds all chunks with the model.")) return;
    try { S.ov = await api("DELETE", "/api/index/cache"); await refresh(); } catch (err) { showError("ix-strat-error", err); }
  }

  // ------------------------------------------------------------ comparison
  function renderCompare() {
    const built = NAMES.map(indexOf).filter(Boolean);
    const cache = S.ov.cache.reduce((a, r) => a + r.vectors, 0);
    $("ix-cache").textContent = "cache: " + num(cache) + " vectors";
    if (!built.length) return fill("ix-compare", h("div", { class: "ix-muted", text: "Build an index to compare." }));
    const ev = S.evalResult ? S.evalResult.summary : (S.ov.last_eval ? S.ov.last_eval.summary : {});
    const rows = [
      ["group", "Structure"],
      ["Chunks", (i) => i.stats.chunks, num],
      ["Median / p95 words", (i) => i.stats.words_median + " / " + i.stats.words_p95],
      ["Tiny chunks (<30 words)", (i) => i.stats.tiny_chunks, num, "low"],
      ["Cut mid-sentence", (i) => i.stats.mid_sentence_share, pct, "low"],
      ["Cross a section boundary", (i) => i.stats.cross_section_share, pct, "low"],
      ["Split code blocks", (i) => i.stats.split_code_blocks, num, "low"],
      ["Redundancy (overlap cost)", (i) => i.stats.redundancy, (v) => "×" + v, "low"],
      ["group", "Build"],
      ["Embedding time, s", (i) => i.timings.embed, (v) => v, null],
      ["Embedded now / from cache", (i) => num(i.stats.embedded_now) + " / " + num(i.stats.from_cache)],
      ["Settings", (i) => Object.entries(i.params).map(([k, v]) => k + "=" + v).join(" ")],
      ["group", "Retrieval" + (S.ov.last_eval && S.ov.last_eval.at ? " · checked " + ago(S.ov.last_eval.at) : "")],
      ["hit@1", (i) => ev[i.name] && ev[i.name]["hit@1"], pct, "high"],
      ["hit@3", (i) => ev[i.name] && ev[i.name]["hit@3"], pct, "high"],
      ["hit@5", (i) => ev[i.name] && ev[i.name]["hit@5"], pct, "high"],
      ["MRR", (i) => ev[i.name] && ev[i.name].mrr, fixed2, "high"],
      ["Not found in top depth", (i) => ev[i.name] && ev[i.name].not_found, num, "low"],
      ["Words read to reach the answer", (i) => ev[i.name] && ev[i.name].mean_words_to_hit, num, "low"],
    ];
    fill("ix-compare", h("table", { class: "ix-table" },
      h("thead", {}, h("tr", {}, h("th", { text: "" }), built.map((i) => h("th", { class: "num", text: STRATEGIES[i.name] ? STRATEGIES[i.name].title : i.name })))),
      h("tbody", {}, rows.map(([label, get, fmt, better]) => {
        if (label === "group") return h("tr", { class: "group" }, h("td", { colspan: built.length + 1, text: get }));
        const values = built.map(get);
        const numeric = values.filter((v) => typeof v === "number");
        let best = null;
        if (better && numeric.length > 1 && new Set(numeric).size > 1) best = better === "low" ? Math.min(...numeric) : Math.max(...numeric);
        return h("tr", {}, h("td", { text: label }), values.map((v) =>
          h("td", { class: "num" + (best != null && v === best ? " best" : ""), text: v == null ? "—" : fmt ? fmt(v) : v })));
      }))));
  }

  // ------------------------------------------------------------ questions
  async function loadQuestions() {
    const res = await api("GET", "/api/index/questions");
    S.questions = res.questions;
    S.dirty = false;
    renderQuestions();
  }

  async function sectionsFor(source) {
    if (!source || S.sections[source]) return;
    S.sections[source] = "loading";
    try {
      const res = await api("GET", "/api/index/sections?source=" + encodeURIComponent(source));
      S.sections[source] = res.sections;
    } catch (_) {
      S.sections[source] = [];
    }
    renderQuestions();
  }

  function markDirty() {
    S.dirty = true;
    $("ix-save-q").textContent = "Save questions •";
  }

  function addQuestion() {
    const first = (S.ov.corpus.documents[0] || {}).source || "";
    S.questions.push({ id: "", question: "", expected: [{ source: first, section: "" }], note: "" });
    markDirty();
    $("ix-q-details").open = true;
    renderQuestions();
    const inputs = $("ix-questions").querySelectorAll("input[type=text]");
    if (inputs.length) inputs[inputs.length - 1].focus();
  }

  function renderQuestions() {
    const docs = S.ov ? S.ov.corpus.documents.map((d) => d.source) : [];
    $("ix-q-summary").textContent = "Questions (" + S.questions.length + ")" + (S.dirty ? " - unsaved changes" : "");
    $("ix-save-q").textContent = S.dirty ? "Save questions •" : "Save questions";
    fill("ix-questions", S.questions.map((q, qi) =>
      h("div", { class: "ix-question" },
        h("div", { class: "ix-row" },
          h("input", { type: "text", value: q.question, placeholder: "Question", style: "flex:1;min-width:200px",
            oninput: (e) => { q.question = e.target.value; markDirty(); } }),
          h("button", { type: "button", class: "icon-btn close", title: "Delete question", text: "✕",
            onclick: () => { S.questions.splice(qi, 1); markDirty(); renderQuestions(); } })),
        q.expected.map((ex, ei) => {
          const secs = S.sections[ex.source];
          if (!secs) sectionsFor(ex.source);
          const options = [h("option", { value: "", text: "(whole file)" })];
          if (Array.isArray(secs)) {
            const seen = new Set();
            for (const s of secs) {
              if (seen.has(s.label)) continue;
              seen.add(s.label);
              const parts = s.label.split(" › ");
              options.push(h("option", { value: s.label, title: s.label,
                text: "  ".repeat(Math.max(0, s.depth - 1)) + parts[parts.length - 1] }));
            }
          }
          if (ex.section && !(Array.isArray(secs) && secs.some((s) => s.label === ex.section))) {
            options.push(h("option", { value: ex.section, text: (secs === "loading" ? "" : "⚠ not in corpus: ") + ex.section }));
          }
          const docOptions = docs.map((d) => h("option", { value: d, text: d }));
          if (ex.source && !docs.includes(ex.source)) docOptions.push(h("option", { value: ex.source, text: "⚠ " + ex.source }));
          const srcSel = h("select", { onchange: (e) => { ex.source = e.target.value; ex.section = ""; markDirty(); renderQuestions(); } }, docOptions);
          srcSel.value = ex.source;
          const secSel = h("select", { title: ex.section, onchange: (e) => { ex.section = e.target.value; markDirty(); } }, options);
          secSel.value = ex.section;
          return h("div", { class: "ix-expected" }, srcSel, secSel,
            h("button", { type: "button", class: "icon-btn close", title: "Remove this place", text: "✕",
              onclick: () => { q.expected.splice(ei, 1); markDirty(); renderQuestions(); } }));
        }),
        h("div", {}, h("button", { type: "button", class: "icon-btn", text: "+ another place the answer may be",
          onclick: () => { q.expected.push({ source: (q.expected[0] || {}).source || docs[0] || "", section: "" }); markDirty(); renderQuestions(); } })))));
  }

  async function saveQuestions() {
    try {
      const res = await api("PUT", "/api/index/questions", { questions: S.questions });
      S.questions = res.questions;
      S.dirty = false;
      renderQuestions();
      showError("ix-eval-error", null);
      S.ov.questions = S.questions.length;
    } catch (err) { showError("ix-eval-error", err); }
  }

  async function runEval() {
    const btn = $("ix-run-eval");
    btn.disabled = true;
    btn.textContent = "Checking…";
    try {
      if (S.dirty) await saveQuestions();
      const depth = Number($("ix-depth").value) || 10;
      S.evalResult = await api("POST", "/api/index/evaluate", { depth });
      showError("ix-eval-error", null);
      await refresh();
      renderEval();
    } catch (err) {
      showError("ix-eval-error", err);
    } finally {
      btn.disabled = false;
      btn.textContent = "Run check";
    }
  }

  function rankChip(rank) {
    if (!rank) return h("span", { class: "ix-rank miss", text: "–" });
    return h("span", { class: "ix-rank " + (rank === 1 ? "r1" : rank <= 3 ? "r3" : ""), text: rank });
  }

  function renderEval() {
    const r = S.evalResult;
    if (!r || !r.questions) return fill("ix-eval");
    const names = Object.keys(r.summary);
    const head = h("div", { class: "ix-muted ix-small", style: "margin-bottom:6px" },
      r.questions.length + " questions · " + r.embedder + " · top " + r.depth + " · " + r.seconds + " s" +
      (r.invalid ? " · " + r.invalid + " with an expected place missing from the corpus" : "") +
      Object.entries(r.skipped || {}).map(([n, why]) => " · " + n + ": " + why).join(""));
    const body = [];
    for (const q of r.questions) {
      const open = S.openQuestion === q.id;
      body.push(h("tr", { class: "clickable", onclick: () => { S.openQuestion = open ? null : q.id; renderEval(); } },
        h("td", {}, q.question, !q.valid && h("div", { class: "ix-small", style: "color:var(--ix-bad)", text: q.problem })),
        names.map((n) => h("td", { class: "num" }, q.valid && q.results[n] ? rankChip(q.results[n].rank) : "—"))));
      if (open && q.valid) {
        body.push(h("tr", {}, h("td", { colspan: names.length + 1 },
          h("div", { class: "ix-small ix-muted", style: "margin-bottom:6px" }, "Expected: ",
            q.expected.map((e) => e.source + (e.section ? " › " + e.section : "")).join("  |  ")),
          h("div", { class: "ix-hits" }, names.map((n) => h("div", {},
            h("h4", { text: (STRATEGIES[n] ? STRATEGIES[n].title : n) + (q.results[n].rank ? " · rank " + q.results[n].rank : " · not found") }),
            q.results[n].top.map((t) => h("div", { class: "ix-chunk" + (t.match ? " match" : "") },
              h("div", { class: "meta" }, h("b", { text: "#" + t.rank }), t.score.toFixed(3), t.match ? "✓ expected place" : "",
                h("span", { class: "ix-mono", text: t.source }), h("span", { text: t.section }), t.n_words + " words"),
              h("div", { class: "ix-small", text: t.preview + "…" })))))))));
      }
    }
    fill("ix-eval", head, h("div", { class: "ix-table-wrap" }, h("table", { class: "ix-table" },
      h("thead", {}, h("tr", {}, h("th", { text: "Question (click for the top results)" }),
        names.map((n) => h("th", { class: "num", text: STRATEGIES[n] ? STRATEGIES[n].title : n })))),
      h("tbody", {}, body))));
  }

  // ------------------------------------------------------------ search
  async function search() {
    const query = $("ix-query").value.trim();
    if (!query) return;
    try {
      const res = await api("POST", "/api/index/search", { query, k: Number($("ix-k").value) || 5 });
      showError("ix-search-error", null);
      fill("ix-search", Object.entries(res.results).map(([name, r]) => h("div", {},
        h("h4", { text: (STRATEGIES[name] ? STRATEGIES[name].title : name) + " · " + r.ms + " ms (query embedded in " + res.embed_ms + " ms)" }),
        r.hits.map((hit) => chunkCard(hit, { score: hit.score })))));
    } catch (err) { showError("ix-search-error", err); }
  }

  function chunkCard(c, opts) {
    opts = opts || {};
    const overlap = opts.overlap || 0;
    const text = c.text || "";
    return h("div", { class: "ix-chunk" },
      h("div", { class: "meta" },
        opts.score != null && h("b", { text: "#" + c.rank + " · " + opts.score.toFixed(3) }),
        h("span", { class: "ix-mono", text: c.chunk_id }),
        h("span", { title: "section", text: c.section }),
        h("span", { text: c.n_words + " words" }),
        c.sections_spanned > 1 && h("span", { text: "spans " + c.sections_spanned + " sections" }),
        c.merged > 1 && h("span", { text: "merged " + c.merged + " sections" }),
        h("span", { text: "chars " + c.start + "–" + c.end })),
      h("pre", {},
        opts.prefix && h("span", { class: "prefix", text: opts.prefix }),
        overlap > 0 && h("mark", { text: text.slice(0, overlap) }),
        text.slice(overlap)));
  }

  // ------------------------------------------------------------ browser
  const loadBrowser = debounce(async () => {
    const { strategy, source } = S.browse;
    if (!source || !S.params[strategy]) return;
    try {
      const res = await api("POST", "/api/index/preview", { strategy, params: S.params[strategy], source, limit: 300 });
      $("ix-browse-count").textContent = res.shown_of + " chunks in " + source +
        (res.chunks.length < res.shown_of ? " (first " + res.chunks.length + " shown)" : "") + " · current settings, not embedded";
      let prevEnd = -1;
      fill("ix-browser", res.chunks.map((c) => {
        const overlap = prevEnd > c.start ? Math.min(prevEnd - c.start, c.text.length) : 0;
        prevEnd = c.end;
        return chunkCard(c, { overlap, prefix: c.embed_prefix });
      }));
    } catch (err) {
      fill("ix-browser", h("div", { class: "ix-error", text: err.message }));
    }
  }, 150);

  // ------------------------------------------------------------ boot
  async function start() {
    if (S.started) return;
    S.started = true;
    skeleton();
    try {
      await refresh();
      loadParams();
      renderStrategies();
      NAMES.forEach(runPreview);
      $("ix-browse-strategy").value = S.browse.strategy;
      loadBrowser();
      await loadQuestions();
      const last = await api("GET", "/api/index/evaluate");
      if (last && last.questions) { S.evalResult = last; renderEval(); renderCompare(); }
    } catch (err) {
      fill(root, h("div", { class: "ix-page" }, h("div", { class: "ix-error", text: "Index tab failed to load: " + err.message })));
      S.started = false;
    }
  }

  // Loaded lazily: the first time the tab is opened, or at once if the page
  // comes back with it already open.
  for (const tab of document.querySelectorAll('.tab[data-view="index"]')) tab.addEventListener("click", start);
  if (root.classList.contains("active")) start();
  else new MutationObserver((_, obs) => {
    if (root.classList.contains("active")) { obs.disconnect(); start(); }
  }).observe(root, { attributes: true, attributeFilter: ["class"] });
})();
