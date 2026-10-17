// Day 21: the Index tab - the document pipeline, the structural chunker and
// what it does to the corpus, the build, a search and a chunk browser.
//
// Self-contained on purpose: it shares nothing with the chat code in
// index.html except the colour tokens and the tab switch, and talks only to
// /api/index/* and /api/project. Since day 22 the chat's RAG mode reads the
// index built here; since day 31 it is the Project tab - the folder the
// assistant works on is opened here, and /help reads it.
(function () {
  "use strict";

  const root = document.getElementById("index-view");
  if (!root) return;

  const PARAMS_KEY = "index-chunker-params";

  const PARAMS = [
    { key: "max_words", label: "Max words per chunk", min: 50, max: 1500, step: 10 },
    { key: "min_words", label: "Merge sections under, words", min: 0, max: (p) => p.max_words - 1, step: 5 },
    { key: "overlap", label: "Overlap M, words", min: 0, max: (p) => p.max_words - 1, step: 5, slider: true,
      hint: "Used only when a long section is split: the next piece repeats the last M words of the one before." },
    { key: "with_context", label: "Prepend title › section to the embedded text", bool: true },
  ];

  const S = {
    project: null,       // GET /api/project
    projectPoll: null,
    ov: null,            // GET /api/index
    params: {},          // the chunker's settings being edited
    preview: null,       // stats with the edited settings
    browse: { source: "" },
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
  // The one index, or null until it is built.
  const built = () => (S.ov && S.ov.indexes[0]) || null;
  function debounce(fn, ms) {
    let t = null;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  // ------------------------------------------------------------ skeleton
  function skeleton() {
    fill(root, h("div", { class: "ix-page" },
      h("div", { class: "ix-head" },
        h("h2", { text: "Project" }),
        h("span", { class: "ix-muted", text: "The folder the assistant works on. /help in the chat answers from its index and its git and file MCP servers." }),
        h("span", { class: "ix-spacer" }),
        h("span", { id: "ix-status", class: "ix-row" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Folder" }),
        h("form", { class: "ix-row ix-project-form", onsubmit: (e) => { e.preventDefault(); openProject(); } },
          h("input", { id: "ix-project-path", class: "ix-project-path ix-mono", type: "text", spellcheck: "false",
            placeholder: "/Users/me/src/MyApp", "aria-label": "Project folder" }),
          h("button", { id: "ix-project-open", type: "submit", class: "primary-btn", text: "Open" })),
        h("div", { id: "ix-project-info", class: "ix-project-info" }),
        h("div", { id: "ix-project-servers", class: "ix-row" }),
        h("div", { id: "ix-project-note", class: "ix-muted ix-small" }),
        h("div", { id: "ix-project-error", class: "ix-error" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Index pipeline" }),
        h("div", { id: "ix-pipeline", class: "ix-pipeline" }),
        h("div", { id: "ix-job" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Corpus" }),
        h("p", { class: "ix-lead" }, "Which files of the project are indexed is set in ",
          h("span", { class: "ix-mono", text: "corpus.json" }),
          " in the app folder - glob patterns to include and exclude, and a size cap. In a git repository only the files git knows are read, so the project's .gitignore applies. Uploaded files are always included, as ",
          h("span", { class: "ix-mono", text: "uploads/<name>" }), "."),
        h("div", { id: "ix-corpus-config", class: "ix-corpus-config" }),
        h("div", { class: "ix-row", style: "margin-top:8px" },
          h("button", { type: "button", class: "ghost-btn", onclick: () => $("ix-file").click(), text: "Upload files…" }),
          h("input", { type: "file", id: "ix-file", multiple: true, accept: ".md,.markdown,.txt,.py,.swift,.yaml,.yml,.graphql,.gql,.proto,.sql", style: "display:none", onchange: uploadFiles }),
          h("span", { id: "ix-uploads", class: "ix-row" })),
        h("div", { id: "ix-corpus-error", class: "ix-error" }),
        h("details", { class: "ix-details" }, h("summary", { id: "ix-docs-summary", text: "Documents" }),
          h("div", { id: "ix-docs", class: "ix-table-wrap" }))),

      h("section", { class: "ix-card" },
        h("h3", {}, "Chunking", h("span", { id: "ix-built" })),
        h("p", { class: "ix-lead", text: "One chunk per section (heading, def, class, file). Long sections are split on paragraphs, short ones merged with a neighbour. The stats below are recomputed as you move the controls - chunking only, no model involved. Build embeds the chunks and writes the index; unchanged chunks come from the embedding cache." }),
        h("div", { id: "ix-chunker", class: "ix-chunker" }),
        h("div", { class: "ix-row", style: "margin-top:10px" },
          h("button", { type: "button", class: "primary-btn", id: "ix-build", onclick: build, text: "Build index" }),
          h("button", { type: "button", class: "ghost-btn", onclick: resetIndex, text: "Reset index" }),
          h("button", { type: "button", class: "ghost-btn", onclick: clearCache, text: "Clear embedding cache" }),
          h("span", { id: "ix-cache", class: "ix-muted ix-small" })),
        h("div", { id: "ix-chunk-error", class: "ix-error" })),

      h("section", { class: "ix-card" },
        h("h3", { text: "Search" }),
        h("p", { class: "ix-lead", text: "One query against the index - the same cosine search the chat's RAG mode starts from." }),
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
          h("select", { id: "ix-browse-source", style: "width:auto;max-width:100%", onchange: (e) => { S.browse.source = e.target.value; loadBrowser(); } }),
          h("span", { id: "ix-browse-count", class: "ix-muted ix-small" })),
        h("div", { id: "ix-browser", class: "ix-chunks", style: "margin-top:10px" }))));
  }

  // ------------------------------------------------------------ project
  // Day 31. The folder, its git repository, and its MCP servers - which start
  // in the background, so the card polls until none is still starting.
  async function refreshProject() {
    S.project = await api("GET", "/api/project");
    renderProject();
  }

  function renderProject() {
    const p = S.project.project;
    const input = $("ix-project-path");
    if (document.activeElement !== input) input.value = p.path;
    fill("ix-project-info", h("div", { class: "ix-row" },
      h("strong", { text: p.name }),
      h("span", { class: "ix-chip " + (p.is_git ? "good" : ""), title: p.git_root || "",
        text: p.is_git ? "git: " + p.git_root : "not a git repository" }),
      p.is_app && h("span", { class: "ix-muted ix-small", text: "this app's own folder" })));
    const servers = S.project.servers || [];
    fill("ix-project-servers",
      !servers.length && h("span", { class: "ix-muted ix-small",
        text: S.project.autostart ? "No MCP servers yet - they start with the first /help." :
          "MCP servers are off (PROJECT_MCP_AUTOSTART=0)." }),
      servers.map((sv) => h("span", {
        class: "ix-chip " + ({ ready: "good", failed: "bad" }[sv.state] || ""),
        title: sv.state === "ready" ? sv.tools.join(", ") : (sv.error || sv.command),
      }, ({ ready: "● ", failed: "✕ ", skipped: "– ", starting: "… " }[sv.state] || "") + sv.label + " · " +
        (sv.state === "ready" ? sv.tools.length + " tools" : sv.state + (sv.error ? ": " + sv.error : "")))));
    if (servers.some((sv) => sv.state === "starting") && !S.projectPoll) {
      S.projectPoll = setTimeout(async () => {
        S.projectPoll = null;
        try { await refreshProject(); } catch (err) { showError("ix-project-error", err); }
      }, 1500);
    }
  }

  async function openProject() {
    showError("ix-project-error", null);
    fill("ix-project-note");
    const button = $("ix-project-open");
    button.disabled = true;
    try {
      S.project = await api("PUT", "/api/project", { path: $("ix-project-path").value });
      $("ix-project-path").blur();
      renderProject();
      if (S.project.note) fill("ix-project-note", S.project.note);
      await refreshAfterCorpus();
      if (S.ov.job && S.ov.job.running) startPolling();
    } catch (err) {
      showError("ix-project-error", err);
    } finally {
      button.disabled = false;
    }
  }

  // ------------------------------------------------------------ overview
  async function refresh() {
    S.ov = await api("GET", "/api/index");
    renderStatus();
    renderPipeline();
    renderCorpus();
    renderBuildStatus();
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
    const index = built();
    const cached = ov.cache.reduce((a, r) => a + r.vectors, 0);
    const job = ov.job || {};
    const active = job.running ? { load: 0, chunk: 2, embed: 3, store: 4 }[job.stage] : -1;
    const stages = [
      ["Loader", c.documents.length + " files", c.formats.join(", ") + " · loaders " + c.loaders.join(" ") +
        (c.skipped.length ? " · " + c.skipped.length + " skipped" : "")],
      ["Document", num(sections) + " sections", num(c.words) + " words · corpus " + c.sha],
      ["Chunker", index ? num(index.stats.chunks) + " chunks" : "not built",
        "structural" + (index ? " · overlap " + index.params.overlap : "")],
      ["Embedder", ov.embedder.model || ov.embedder.name,
        (index && index.dim ? index.dim + "-d · " : "") + (ov.embedder.status.ok ? "ready" : "unavailable") + " · cache " + num(cached)],
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
          "Building · " + job.stage + (job.total ? " " + job.done + "/" + job.total : "")));
    } else if (job.error) {
      fill("ix-job", h("div", { class: "ix-error", text: "Build failed: " + job.error }));
    } else if (job.stage === "done" && job.finished_at) {
      fill("ix-job", h("div", { class: "ix-muted ix-small", style: "margin-top:8px" },
        "Last build: " + Math.round(job.finished_at - job.started_at) + " s · " + ago(job.finished_at)));
    }
    const button = $("ix-build");
    if (button) button.disabled = !!job.running;
  }

  // ------------------------------------------------------------ corpus
  function renderCorpus() {
    const c = S.ov.corpus;
    // Day 31: the corpus is `corpus.json`'s - shown here, edited in the file.
    const patterns = (label, list) => h("div", { class: "ix-row" },
      h("span", { class: "ix-muted", text: label }),
      list.length ? list.map((p) => h("span", { class: "ix-chip ix-mono", text: p }))
        : h("span", { class: "ix-muted", text: "none" }));
    fill("ix-corpus-config", [
      h("div", { class: "ix-row" }, h("span", { class: "ix-muted", text: "folder" }),
        h("span", { class: "ix-mono", text: c.root_path })),
      patterns("include", c.include || []),
      patterns("exclude", c.exclude || []),
      h("div", { class: "ix-row" }, h("span", { class: "ix-muted", text: "files over " + num(c.max_file_kb) + " KB are skipped" })),
      c.config ? null : h("div", { class: "ix-muted", text: "No corpus.json - these are the defaults." }),
    ]);
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
    runPreview();
    loadBrowser();
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

  // ------------------------------------------------------------ chunker
  function loadParams() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(PARAMS_KEY) || "{}"); } catch (_) { saved = {}; }
    const index = built();
    S.params = Object.assign({}, S.ov.chunker.defaults, index ? index.params : {}, saved);
  }

  function saveParams() {
    try { localStorage.setItem(PARAMS_KEY, JSON.stringify(S.params)); } catch (_) { /* private mode */ }
  }

  function renderChunker() {
    const p = S.params;
    const rows = PARAMS.map((param) => {
      if (param.bool) {
        return h("label", { class: "ix-row ix-small" },
          h("input", { type: "checkbox", checked: !!p[param.key],
            onchange: (e) => setParam(param.key, e.target.checked) }), param.label);
      }
      const max = typeof param.max === "function" ? param.max(p) : param.max;
      const numberInput = h("input", { type: "number", min: param.min, max, step: 1, value: p[param.key],
        "data-param": param.key,
        onchange: (e) => setParam(param.key, e.target.value) });
      const slider = h("input", { type: "range", min: param.min, max, step: param.step, value: p[param.key],
        "data-param": param.key,
        oninput: (e) => setParam(param.key, e.target.value) });
      return h("div", { class: "ix-param" },
        h("label", { text: param.label }), slider, numberInput,
        param.hint && h("div", { class: "hint", text: param.hint }));
    });
    fill("ix-chunker",
      rows,
      h("div", { class: "ix-muted ix-small", text: "With these settings:" }),
      h("div", { id: "ix-pstats", class: "ix-stats" }),
      h("div", { id: "ix-pstatus", class: "ix-small ix-muted" }));
    renderBuildStatus();
  }

  function setParam(key, value) {
    const spec = PARAMS.find((x) => x.key === key);
    const p = S.params;
    if (spec.bool) p[key] = !!value;
    else {
      const max = typeof spec.max === "function" ? spec.max(p) : spec.max;
      p[key] = Math.max(spec.min, Math.min(max, Math.round(Number(value) || 0)));
    }
    // Clamp the dependants (overlap and min < max) and redraw their limits.
    for (const other of PARAMS) {
      if (other.bool || typeof other.max !== "function") continue;
      const max = other.max(p);
      if (p[other.key] > max) p[other.key] = max;
    }
    for (const input of root.querySelectorAll("[data-param]")) {
      const other = PARAMS.find((x) => x.key === input.dataset.param);
      if (typeof other.max === "function") input.max = other.max(p);
      if (document.activeElement !== input || input.type === "range") input.value = p[other.key];
    }
    saveParams();
    renderBuildStatus();
    previewSoon();
  }

  let previewTimer = null;
  function previewSoon() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
      runPreview();
      loadBrowser();
    }, 250);
  }

  async function runPreview() {
    try {
      const res = await api("POST", "/api/index/preview", { params: S.params });
      S.preview = res.stats;
      showError("ix-chunk-error", null);
    } catch (err) { showError("ix-chunk-error", err); }
    renderPreviewStats();
  }

  function renderPreviewStats() {
    const s = S.preview;
    const el = $("ix-pstats");
    if (!el) return;
    if (!s) return fill(el);
    const cell = (v, k, title) => h("div", { class: "ix-stat", title }, h("div", { class: "v", text: v }), h("div", { class: "k", text: k }));
    fill(el,
      cell(num(s.chunks), "chunks"),
      cell(s.words_median + " / " + s.words_p95, "median / p95 words"),
      cell(pct(s.mid_sentence_share), "cut mid-sentence", "Chunk ends neither at a sentence end nor at a blank line"),
      cell(pct(s.cross_section_share), "cross a boundary", "Chunk runs across a section boundary it was not built around (planned merges are not counted)"),
      cell(num(s.split_code_blocks), "split code blocks", "Odd number of ``` fences inside the chunk"),
      cell("×" + s.redundancy, "redundancy", "Words embedded / words in the corpus - what overlap costs"),
      cell(num(s.tiny_chunks), "tiny (<30 words)"));
  }

  function renderBuildStatus() {
    if (!S.ov) return;
    const cache = S.ov.cache.reduce((a, r) => a + r.vectors, 0);
    const cacheEl = $("ix-cache");
    if (cacheEl) cacheEl.textContent = "cache: " + num(cache) + " vectors";
    const chipEl = $("ix-built");
    const statusEl = $("ix-pstatus");
    if (!chipEl || !statusEl) return;
    const index = built();
    if (!index) {
      fill(chipEl, h("span", { class: "ix-chip", text: "not built" }));
      fill(statusEl, "No index yet - Build embeds " + (S.preview ? num(S.preview.chunks) + " chunks" : "the chunks") + ".");
      return;
    }
    const changed = !sameParams(normalise(index.params), normalise(S.params));
    const chips = [h("span", { class: "ix-chip good", text: "built " + ago(index.built_at) })];
    if (index.stale) chips.push(h("span", { class: "ix-chip warn", text: "corpus changed" }));
    if (index.embedder_changed) chips.push(h("span", { class: "ix-chip warn", text: "other embedder" }));
    if (changed) chips.push(h("span", { class: "ix-chip warn", text: "settings changed" }));
    fill(chipEl, chips);
    fill(statusEl, "Built with " + Object.entries(index.params).map(([k, v]) => k + "=" + v).join(", ") +
      " · " + num(index.stats.chunks) + " chunks · embedded " + num(index.stats.embedded_now) +
      " (+" + num(index.stats.from_cache) + " cached) in " + index.timings.embed + " s" +
      (changed ? " · rebuild to apply the new settings" : ""));
  }

  function normalise(params) {
    const out = {};
    for (const spec of PARAMS) out[spec.key] = spec.bool ? !!params[spec.key] : Number(params[spec.key]);
    return out;
  }

  async function build() {
    showError("ix-chunk-error", null);
    try {
      S.ov.job = await api("POST", "/api/index/build", { params: S.params });
      renderJob();
      startPolling();
    } catch (err) { showError("ix-chunk-error", err); }
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
        showError("ix-chunk-error", err);
      }
    }, 700);
  }

  async function resetIndex() {
    if (!confirm("Drop the index - its chunks and vectors? The embedding cache is kept, so a rebuild with the same settings is fast.")) return;
    try { S.ov = await api("DELETE", "/api/index/indexes"); await refresh(); } catch (err) { showError("ix-chunk-error", err); }
  }

  async function clearCache() {
    if (!confirm("Forget every cached vector? The next build re-embeds all chunks with the model.")) return;
    try { S.ov = await api("DELETE", "/api/index/cache"); await refresh(); } catch (err) { showError("ix-chunk-error", err); }
  }

  // ------------------------------------------------------------ search
  async function search() {
    const query = $("ix-query").value.trim();
    if (!query) return;
    try {
      const res = await api("POST", "/api/index/search", { query, k: Number($("ix-k").value) || 5 });
      showError("ix-search-error", null);
      fill("ix-search", h("div", {},
        h("h4", { text: res.hits.length + " hits · " + res.ms + " ms (query embedded in " + res.embed_ms + " ms)" }),
        res.hits.map((hit) => chunkCard(hit, { score: hit.score }))));
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
    const { source } = S.browse;
    if (!source) return;
    try {
      const res = await api("POST", "/api/index/preview", { params: S.params, source, limit: 300 });
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
      await refreshProject();
      await refresh();
      loadParams();
      renderChunker();
      runPreview();
      loadBrowser();
    } catch (err) {
      fill(root, h("div", { class: "ix-page" }, h("div", { class: "ix-error", text: "Project tab failed to load: " + err.message })));
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
