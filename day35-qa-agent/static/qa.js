// Day 35: the QA tab - a test scenario in words, carried out on an iOS app in
// the Simulator by the QA agent, and its result: the verdict, every check with
// its evidence, each step with the screenshot it led to, a crash report, the
// code diagnosis of a failure - and, from a passed run, a generated XCUITest.
//
// Self-contained like the Project tab (indexing.js): it shares the page's
// colour tokens, the tab switch and the Markdown renderer, and talks only to
// /api/qa/*. The left column sets a run up (simulator, app, scenario) and keeps
// the suite and the history; the right one shows the run, live while it goes.
(function () {
  "use strict";

  const root = document.getElementById("qa-view");
  if (!root) return;

  const PREFS_KEY = "qa-tab-prefs";
  const POLL_MS = 1200;

  const VERDICTS = {
    passed: { label: "Passed", cls: "good", icon: "✓" },
    failed: { label: "Failed", cls: "bad", icon: "✗" },
    crashed: { label: "Crashed", cls: "bad", icon: "💥" },
    blocked: { label: "Blocked", cls: "warn", icon: "⛔" },
    unchecked: { label: "No checks recorded", cls: "warn", icon: "!" },
    incomplete: { label: "Incomplete", cls: "warn", icon: "…" },
    stopped: { label: "Stopped", cls: "", icon: "■" },
    error: { label: "Error", cls: "bad", icon: "!" },
  };
  const STATUS = { starting: "Starting…", running: "Running…", diagnosing: "Reading the code…" };

  const S = {
    ov: null,          // GET /api/qa
    apps: [],
    schemes: [],
    runId: null,       // the run on the right
    run: null,
    tests: null,       // the last tests job's result
    poll: null,
    started: false,
    prefs: loadPrefs(),
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
  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {}; } catch (_) { return {}; }
  }
  function savePrefs() {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(S.prefs)); } catch (_) { /* private mode */ }
  }
  function showError(err) { fill("qa-error", err ? String(err.message || err) : null); }
  function ago(ts) {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return new Date(ts * 1000).toLocaleDateString();
  }
  function verdictChip(verdict, status, big) {
    if (status && status !== "done") {
      return h("span", { class: "qa-verdict" + (big ? " big" : "") }, STATUS[status] || status);
    }
    const v = VERDICTS[verdict] || { label: verdict || "—", cls: "", icon: "•" };
    return h("span", { class: "qa-verdict " + v.cls + (big ? " big" : "") }, v.icon + " " + v.label);
  }
  function markdown(text) {
    if (window.Markdown) return window.Markdown.render(text || "");
    return h("div", { style: "white-space:pre-wrap", text: text || "" });
  }
  const jobRunning = () => !!(S.ov && S.ov.job && S.ov.job.status === "running");
  const device = () => $("qa-device") && $("qa-device").value;
  const app = () => $("qa-app") && $("qa-app").value;

  // ------------------------------------------------------------ skeleton
  function skeleton() {
    fill(root, h("div", { class: "qa-page" },
      h("div", { class: "qa-head" },
        h("h2", { text: "QA agent" }),
        h("span", { class: "ix-muted", text: "Write a test scenario in words. The agent carries it out on an iOS app in the Simulator, checks every expectation and reports with screenshots; a failure is traced into the project's code, and a passed run becomes an XCUITest." })),

      h("div", { class: "qa-side" },
        h("section", { class: "ix-card" },
          h("h3", { text: "Simulator and app" }),
          h("label", { class: "qa-field" }, h("span", { text: "Simulator" }),
            h("select", { id: "qa-device", onchange: onDevice })),
          h("label", { class: "qa-field" }, h("span", { text: "App under test" }),
            h("div", { class: "qa-inline" },
              h("select", { id: "qa-app", onchange: () => { S.prefs.app = app(); savePrefs(); } }),
              h("button", { type: "button", class: "ghost-btn", title: "Reload the installed apps", onclick: loadApps, text: "↻" }))),
          h("div", { class: "qa-sub" },
            h("div", { class: "ix-muted ix-small", id: "qa-project-line" }),
            h("label", { class: "qa-field" }, h("span", { text: "Xcode project" }),
              h("select", { id: "qa-container", onchange: loadSchemes })),
            h("label", { class: "qa-field" }, h("span", { text: "Scheme" }),
              h("div", { class: "qa-inline" },
                h("select", { id: "qa-scheme" }),
                h("button", { type: "button", class: "ghost-btn", id: "qa-build", onclick: build, text: "Build & install" }))))),

        h("section", { class: "ix-card" },
          h("h3", {}, "Scenario", h("span", { id: "qa-editing", class: "ix-muted ix-small" })),
          h("label", { class: "qa-field" }, h("span", { text: "Title" }),
            h("input", { id: "qa-title", type: "text", placeholder: "Search finds K2" })),
          h("label", { class: "qa-field" }, h("span", { text: "Steps and expectations" }),
            h("textarea", { id: "qa-text", spellcheck: "false",
              placeholder: "Open Diffable Data Source → Mountains Search. Type K2 into the search field.\nExpected: K2 is in the results, Everest is not." })),
          h("label", { class: "qa-field" }, h("span", { text: "Model" }),
            h("select", { id: "qa-model", onchange: () => { S.prefs.model = $("qa-model").value; savePrefs(); } })),
          h("label", { class: "qa-check" },
            h("input", { type: "checkbox", id: "qa-diagnose", checked: true }),
            h("span", { id: "qa-diagnose-label", text: "On a failure, look for the cause in the project's code (read-only)" })),
          h("div", { class: "ix-row" },
            h("button", { type: "button", class: "primary-btn", id: "qa-run", onclick: startRun, text: "Run" }),
            h("button", { type: "button", class: "ghost-btn", id: "qa-save", onclick: saveScenario, text: "Save to suite" }),
            h("button", { type: "button", class: "ghost-btn", id: "qa-new", onclick: newScenario, text: "New" }),
            h("span", { class: "ix-spacer" }),
            h("button", { type: "button", class: "ghost-btn", id: "qa-stop", onclick: stop, text: "Stop", disabled: true })),
          h("div", { id: "qa-error", class: "ix-error" })),

        h("section", { class: "ix-card" },
          h("h3", {}, "Suite", h("span", { id: "qa-suite-count", class: "ix-muted ix-small" }),
            h("span", { class: "ix-spacer" }),
            h("button", { type: "button", class: "ghost-btn", id: "qa-run-tests", onclick: () => runTests(null), text: "Run all XCUITests" })),
          h("p", { class: "ix-muted ix-small", style: "margin:0 0 8px" }, "Saved in ",
            h("span", { class: "ix-mono", id: "qa-suite-path", text: "qa_suite/suite.json" }),
            " - the CI job on the self-hosted runner runs the same suite."),
          h("div", { id: "qa-suite", class: "qa-list" })),

        h("section", { class: "ix-card" },
          h("h3", { text: "Recent runs" }),
          h("div", { id: "qa-runs", class: "qa-list" }))),

      h("div", { class: "qa-main" },
        h("section", { class: "ix-card", id: "qa-job-card", hidden: true },
          h("div", { id: "qa-job", class: "qa-job" })),
        h("section", { class: "ix-card", id: "qa-run-card" },
          h("div", { id: "qa-run-view" }, h("div", { class: "qa-empty", text: "Pick a simulator and an app, write a scenario and press Run. The run shows up here step by step - and in the Simulator window." }))))));
  }

  // ------------------------------------------------------------ the overview
  async function refresh() {
    const ov = await api("GET", "/api/qa");
    const firstLoad = !S.ov;
    S.ov = ov;
    renderSetup(firstLoad);
    renderSuite();
    renderRuns();
    renderJob();
    if (firstLoad) {
      await loadApps();
      await loadSchemes();
      if (ov.job && ov.job.run_id) openRun(ov.job.run_id);
      else if (ov.runs.length) openRun(ov.runs[0].id);
    }
    schedule();
  }

  function renderSetup(firstLoad) {
    const ov = S.ov;
    const wanted = S.prefs.device || ov.default_device;
    const current = device() || wanted;
    fill("qa-device", ov.devices.map((d) => h("option", {
      value: d.udid, selected: d.udid === current,
      text: (d.state === "Booted" ? "● " : "") + d.name + " · " + d.runtime,
    })));
    if (!ov.devices.length) fill("qa-device", h("option", { value: "", text: "No iOS simulators (install a runtime in Xcode)" }));

    const project = ov.project;
    fill("qa-project-line", "Build from the project open in the Project tab: ",
      h("b", { text: project.name }), project.containers.length ? null : " - it has no Xcode project");
    const container = $("qa-container").value || S.prefs.container;
    fill("qa-container", project.containers.map((c) => h("option", { value: c.path, selected: c.path === container, text: c.path })));
    $("qa-build").disabled = !project.containers.length || jobRunning();
    $("qa-diagnose-label").textContent = "On a failure, look for the cause in the code of " + project.name + " (read-only)";

    if (firstLoad || !$("qa-model").options.length) {
      const model = S.prefs.model || ov.config.model;
      fill("qa-model", ov.models.map((m) => h("option", { value: m, selected: m === model, text: m + (m === ov.config.model ? " (default)" : "") })));
    }
    $("qa-suite-path").textContent = ov.suite.path;
    $("qa-run").disabled = jobRunning();
    $("qa-stop").disabled = !jobRunning();
    $("qa-run-tests").disabled = jobRunning() || !ov.suite.scenarios.some((s) => s.test);
  }

  async function onDevice() {
    S.prefs.device = device();
    savePrefs();
    await loadApps();
  }

  async function loadApps() {
    const udid = device();
    if (!udid) return;
    try {
      const res = await api("GET", "/api/qa/apps?udid=" + encodeURIComponent(udid));
      S.apps = res.apps;
      showError(null);
    } catch (err) {
      S.apps = [];
      fill("qa-app", h("option", { value: "", text: "Boot the simulator, or build an app for it" }));
      return;
    }
    const wanted = app() || S.prefs.app;
    fill("qa-app", S.apps.length ? S.apps.map((a) => h("option", { value: a.bundle_id, selected: a.bundle_id === wanted, text: a.name + " · " + a.bundle_id }))
      : h("option", { value: "", text: "No apps installed - build one below" }));
  }

  async function loadSchemes() {
    const container = $("qa-container").value;
    S.prefs.container = container;
    savePrefs();
    if (!container) { fill("qa-scheme"); return; }
    fill("qa-scheme", h("option", { value: "", text: "Loading…" }));
    try {
      const res = await api("GET", "/api/qa/schemes?container=" + encodeURIComponent(container));
      S.schemes = res.schemes;
      const wanted = S.prefs.scheme;
      fill("qa-scheme", res.schemes.map((s) => h("option", { value: s, selected: s === wanted, text: s })));
    } catch (err) {
      fill("qa-scheme", h("option", { value: "", text: "No schemes" }));
      showError(err);
    }
  }

  // ------------------------------------------------------------ the suite and the history
  function renderSuite() {
    const suite = S.ov.suite;
    $("qa-suite-count").textContent = suite.scenarios.length ? " · " + suite.scenarios.length : "";
    if (!suite.scenarios.length) {
      fill("qa-suite", h("div", { class: "ix-muted ix-small", text: "No scenarios yet. Write one and Save to suite, or turn a passed run into a test." }));
      return;
    }
    fill("qa-suite", suite.scenarios.map((s) => {
      const test = s.test;
      const last = test && test.last;
      return h("div", { class: "qa-item" + (S.prefs.editing === s.id ? " current" : "") },
        h("div", { class: "qa-item-title" }, s.title,
          test ? h("span", { class: "ix-chip " + (last ? (last.status === "passed" ? "good" : "bad") : ""),
            title: last && last.failure ? last.failure : (last ? "Last run " + last.at : "Not run yet"),
            text: "XCUITest" + (last ? " · " + last.status : "") }) : null),
        h("div", { class: "ix-muted ix-small", text: (s.app_name || s.bundle_id || "") }),
        h("div", { class: "qa-item-actions" },
          h("button", { type: "button", class: "ghost-btn", onclick: () => editScenario(s), text: "Edit" }),
          h("button", { type: "button", class: "ghost-btn", disabled: jobRunning(), onclick: () => runScenario(s), text: "Run with AI" }),
          test ? h("button", { type: "button", class: "ghost-btn", disabled: jobRunning(), onclick: () => runTests([s.id]), text: "Run test" }) : null,
          test ? h("button", { type: "button", class: "ghost-btn", onclick: () => showTest(s), text: "Code" }) : null,
          h("button", { type: "button", class: "ghost-btn", onclick: () => deleteScenario(s), text: "Delete" })));
    }));
  }

  function renderRuns() {
    const runs = S.ov.runs;
    if (!runs.length) { fill("qa-runs", h("div", { class: "ix-muted ix-small", text: "No runs yet." })); return; }
    fill("qa-runs", runs.map((r) => h("div", {
      class: "qa-item qa-run-row" + (r.id === S.runId ? " current" : ""), onclick: () => openRun(r.id),
    },
      verdictChip(r.verdict, r.status),
      h("span", { class: "qa-grow", text: r.title || "(untitled)" }),
      h("span", { class: "ix-muted ix-small", text: ago(r.started_at) }))));
  }

  function renderJob() {
    const job = S.ov.job;
    const card = $("qa-job-card");
    if (!job || (job.status !== "running" && job.kind === "run")) { card.hidden = true; return; }
    card.hidden = false;
    const kids = [h("div", { class: "ix-row" },
      h("b", { text: job.title }),
      h("span", { class: "ix-chip " + (job.status === "done" ? "good" : job.status === "failed" ? "bad" : ""), text: job.status }),
      h("span", { class: "ix-spacer" }),
      job.status !== "running" ? h("button", { type: "button", class: "ghost-btn", onclick: () => { card.hidden = true; }, text: "Hide" }) : null)];
    if (job.error) kids.push(h("div", { class: "ix-error", text: job.error }));
    if (job.kind === "tests" && job.result) kids.push(testsTable(job.result));
    if (job.kind === "build" && job.result && job.result.app) {
      kids.push(h("div", { class: "ix-muted", text: "Installed " + job.result.app.name + " (" + job.result.app.bundle_id + ") - it is selected as the app under test." }));
    }
    if (job.log.length) kids.push(h("details", { open: job.status === "running" }, h("summary", { class: "ix-muted ix-small", text: "Log" }), h("pre", { text: job.log.join("\n") })));
    fill("qa-job", kids);
  }

  function testsTable(result) {
    return h("table", { class: "qa-tests" },
      h("tr", {}, h("th", { text: "Test" }), h("th", { text: "Result" }), h("th", { text: "Seconds" }), h("th", { text: "Failure" })),
      result.tests.map((t) => h("tr", {},
        h("td", { class: "ix-mono", text: t.class }),
        h("td", {}, h("span", { class: "ix-chip " + (t.status === "passed" ? "good" : "bad"), text: t.status })),
        h("td", { text: t.seconds == null ? "" : String(t.seconds) }),
        h("td", { class: "ix-small", text: (t.failures[0] || {}).message || "" }))));
  }

  // ------------------------------------------------------------ the run
  async function openRun(id) {
    S.runId = id;
    try {
      S.run = await api("GET", "/api/qa/runs/" + encodeURIComponent(id));
      renderRun();
      if (S.ov) renderRuns();
    } catch (err) {
      showError(err);
    }
    schedule();
  }

  function stepTitle(step) {
    const t = step.target || {};
    const name = t.label || (t.texts || [])[0] || t.identifier || "";
    const args = step.arguments || {};
    if (step.action === "launch") return "Launch the app";
    if (step.action === "look") return "Look at the screen";
    if (step.action === "wait") return "Wait " + (args.seconds || "") + " s";
    if (step.action === "scroll") return "Scroll " + (args.direction || "") + (name ? " in " + name : "");
    if (step.action === "type") return "Type “" + (args.text || "") + "”" + (name || t.type ? " into " + (name || t.type) : "");
    return (step.action === "long_press" ? "Long-press " : "Tap ") + (t.type ? t.type + " " : "") + (name ? "“" + name + "”" : "");
  }

  function zoom(src) {
    const layer = h("div", { class: "qa-zoom", onclick: () => layer.remove() }, h("img", { src, alt: "Screenshot" }));
    document.body.append(layer);
  }

  function renderRun() {
    const run = S.run;
    if (!run) return;
    const base = "/api/qa/runs/" + encodeURIComponent(run.id) + "/files/";
    const done = run.status === "done";
    const seconds = (run.finished_at || Date.now() / 1000) - run.started_at;
    const kids = [];
    kids.push(h("div", { class: "qa-run-head" },
      verdictChip(run.verdict, run.status, true),
      h("h3", { text: run.scenario.title || "Untitled scenario" }),
      h("span", { class: "ix-spacer" }),
      done && run.verdict === "passed" && !run.test ? h("button", { type: "button", class: "primary-btn", onclick: makeTest, text: "Make XCUITest" }) : null,
      run.test ? h("span", { class: "ix-chip good", text: "XCUITest: " + run.test.class }) : null,
      done ? h("a", { class: "ghost-btn", href: "/api/qa/runs/" + encodeURIComponent(run.id) + "/report", target: "_blank", text: "Report (.md)" }) : null));
    kids.push(h("div", { class: "qa-meta" },
      h("span", { text: (run.app.name || run.app.bundle_id) }),
      run.device.name ? h("span", { text: run.device.name + " · " + (run.device.runtime || "") }) : null,
      h("span", { text: run.model }),
      h("span", { text: Math.round(seconds) + " s" }),
      h("span", { text: run.steps.length + " steps · " + run.tool_calls + " tool calls" }),
      run.usage && run.usage.total_tokens ? h("span", { text: run.usage.total_tokens.toLocaleString("en-US") + " tokens" }) : null));
    kids.push(h("div", { class: "qa-scenario", text: run.scenario.text }));
    if (!done && run.events.length) {
      kids.push(h("div", { class: "ix-muted ix-small", text: run.events[run.events.length - 1].text + "…" }));
    }
    if (run.error) kids.push(h("div", { class: "ix-error", text: run.error }));
    if (run.answer) kids.push(h("div", { class: "qa-answer" }, markdown(run.answer)));

    if (run.checks.length) {
      kids.push(h("div", { class: "qa-section-title", text: "Checks" }));
      kids.push(h("div", { class: "qa-checks" }, run.checks.map((c) => h("div", { class: "qa-check-row" },
        h("span", { class: "qa-check-mark " + (c.passed ? "good" : "bad"), text: c.passed ? "✓" : "✗" }),
        h("div", {},
          h("div", { text: c.expectation }),
          h("div", { class: "ix-muted ix-small", text: [c.note,
            c.evidence.length ? "evidence: " + c.evidence.map((e) => e.type + " “" + (e.label || (e.texts || [])[0] || e.value || "") + "”").join(", ") : "",
            (c.examples || []).length ? "examples: " + c.examples.map((e) => "“" + (e.label || (e.texts || [])[0] || "") + "”").join(", ") : "",
            c.absent.length ? "absent: " + c.absent.join(", ") : ""].filter(Boolean).join(" · ") }))))));
    }

    if (run.crash) {
      kids.push(h("div", { class: "qa-crash" },
        h("b", { text: run.crash.summary || "The app crashed" }),
        run.crash.frames && run.crash.frames.length ? h("pre", { text: run.crash.frames.map((f) => f.text).join("\n") }) : null,
        run.crash.file ? h("div", { class: "ix-muted ix-small ix-mono", text: run.crash.file }) : null));
    }
    if (run.diagnosis) {
      kids.push(h("div", { class: "qa-section-title", text: "Code diagnosis" + (run.diagnosis.project ? " · " + run.diagnosis.project.split("/").pop() : "") }));
      kids.push(h("div", { class: "qa-diagnosis" },
        run.diagnosis.text ? markdown(run.diagnosis.text) : h("div", { class: "ix-error", text: run.diagnosis.error || "No answer" }),
        run.diagnosis.calls && run.diagnosis.calls.length ? h("details", {},
          h("summary", { class: "ix-muted ix-small", text: run.diagnosis.calls.length + " tool calls" }),
          h("pre", { class: "qa-code", text: run.diagnosis.calls.map((c) => c.name + " " + JSON.stringify(c.arguments)).join("\n") })) : null));
    } else if (run.status === "diagnosing") {
      kids.push(h("div", { class: "ix-muted", text: "Reading the project's code to find the cause…" }));
    }

    kids.push(h("div", { class: "qa-section-title", text: "Steps" }));
    if (!run.steps.length) kids.push(h("div", { class: "ix-muted ix-small", text: done ? "No steps." : "Starting the simulator bridge and the app…" }));
    kids.push(h("div", { class: "qa-steps" }, run.steps.map((step) => h("div", { class: "qa-step" + (step.error ? " err" : "") },
      step.screenshot ? h("img", { src: base + encodeURIComponent(step.screenshot), loading: "lazy", alt: "Step " + step.n, onclick: () => zoom(base + encodeURIComponent(step.screenshot)) }) : null,
      h("div", { class: "qa-step-body" },
        h("div", { class: "qa-step-act", text: step.n + ". " + stepTitle(step) }),
        step.why ? h("div", { class: "qa-step-why", text: step.why }) : null,
        step.title ? h("div", { class: "ix-small ix-muted", text: "→ " + step.title }) : null,
        !step.changed && !["launch", "look", "wait"].includes(step.action) ? h("div", { class: "qa-step-flag", text: "the screen did not change" }) : null,
        step.error ? h("div", { class: "qa-step-err", text: step.error }) : null,
        h("details", {}, h("summary", { text: "What the agent saw" }), h("pre", { text: step.screen || "" })))))));
    fill("qa-run-view", kids);
  }

  // ------------------------------------------------------------ actions
  function scenarioFields() {
    return { title: $("qa-title").value.trim(), text: $("qa-text").value.trim() };
  }

  async function startRun(scenarioId) {
    const { title, text } = scenarioFields();
    showError(null);
    try {
      const res = await api("POST", "/api/qa/runs", {
        udid: device(), bundle_id: app(), text, title,
        scenario_id: typeof scenarioId === "string" ? scenarioId : (S.prefs.editing || null),
        model: $("qa-model").value, diagnose: $("qa-diagnose").checked,
      });
      S.run = null;
      await refresh();
      openRun(res.run.id);
    } catch (err) { showError(err); }
  }

  function runScenario(s) {
    editScenario(s);
    if (s.bundle_id && [...$("qa-app").options].some((o) => o.value === s.bundle_id)) $("qa-app").value = s.bundle_id;
    startRun(s.id);
  }

  async function saveScenario() {
    const { title, text } = scenarioFields();
    const option = $("qa-app").selectedOptions[0];
    showError(null);
    try {
      const res = S.prefs.editing
        ? await api("PUT", "/api/qa/scenarios/" + encodeURIComponent(S.prefs.editing), { title, text })
        : await api("POST", "/api/qa/scenarios", { title, text, bundle_id: app() || "", app_name: option ? option.text.split(" · ")[0] : "" });
      editScenario(res.scenario);
      await refresh();
    } catch (err) { showError(err); }
  }

  function editScenario(s) {
    $("qa-title").value = s.title || "";
    $("qa-text").value = s.text || "";
    S.prefs.editing = s.id;
    savePrefs();
    $("qa-editing").textContent = " · editing “" + s.title + "”";
    $("qa-save").textContent = "Save changes";
    if (S.ov) renderSuite();
  }

  function newScenario() {
    $("qa-title").value = "";
    $("qa-text").value = "";
    S.prefs.editing = null;
    savePrefs();
    $("qa-editing").textContent = "";
    $("qa-save").textContent = "Save to suite";
    if (S.ov) renderSuite();
  }

  async function deleteScenario(s) {
    if (!confirm("Delete “" + s.title + "”" + (s.test ? " and its XCUITest" : "") + " from the suite?")) return;
    try {
      await api("DELETE", "/api/qa/scenarios/" + encodeURIComponent(s.id));
      if (S.prefs.editing === s.id) newScenario();
      await refresh();
    } catch (err) { showError(err); }
  }

  async function showTest(s) {
    try {
      const res = await fetch("/api/qa/scenarios/" + encodeURIComponent(s.id) + "/test");
      const code = await res.text();
      fill("qa-run-view",
        h("div", { class: "qa-run-head" }, h("h3", { text: s.test.class + ".swift" }), h("span", { class: "ix-spacer" }),
          h("button", { type: "button", class: "ghost-btn", onclick: () => S.run ? renderRun() : null, text: "Back to the run" })),
        h("div", { class: "ix-muted ix-small", text: "qa_suite/" + s.test.file + " · generated from run " + s.test.from_run + " · compiled into the bridge's UI-test target" }),
        h("pre", { class: "qa-code", text: code }));
    } catch (err) { showError(err); }
  }

  async function makeTest() {
    try {
      const res = await api("POST", "/api/qa/runs/" + encodeURIComponent(S.run.id) + "/test");
      editScenario(res.scenario);
      await refresh();
      await openRun(S.run.id);
    } catch (err) { showError(err); }
  }

  async function runTests(ids) {
    showError(null);
    try {
      await api("POST", "/api/qa/tests", { udid: device(), scenario_ids: ids });
      await refresh();
    } catch (err) { showError(err); }
  }

  async function build() {
    showError(null);
    S.prefs.scheme = $("qa-scheme").value;
    savePrefs();
    try {
      await api("POST", "/api/qa/build", { udid: device(), container: $("qa-container").value, scheme: $("qa-scheme").value });
      await refresh();
    } catch (err) { showError(err); }
  }

  async function stop() {
    try { await api("POST", "/api/qa/stop"); await refresh(); } catch (err) { showError(err); }
  }

  // ------------------------------------------------------------ polling
  function schedule() {
    const live = jobRunning() || (S.run && S.run.status !== "done");
    if (live && !S.poll) S.poll = setTimeout(tick, POLL_MS);
  }

  async function tick() {
    S.poll = null;
    const wasRunning = jobRunning();
    const job = S.ov && S.ov.job;
    try {
      await refresh();
      if (S.runId && S.run && S.run.status !== "done") {
        S.run = await api("GET", "/api/qa/runs/" + encodeURIComponent(S.runId));
        renderRun();
      }
      if (wasRunning && !jobRunning() && job) {
        if (job.kind === "build" && S.ov.job.result && S.ov.job.result.app) {
          S.prefs.app = S.ov.job.result.app.bundle_id;
          savePrefs();
          await loadApps();
        }
        if (job.run_id && job.run_id === S.runId) await openRun(S.runId);
      }
    } catch (err) {
      showError(err);
    }
    schedule();
  }

  // ------------------------------------------------------------ boot
  async function start() {
    if (S.started) return;
    S.started = true;
    skeleton();
    try {
      await refresh();
      if (S.prefs.editing) {
        const s = S.ov.suite.scenarios.find((x) => x.id === S.prefs.editing);
        if (s) editScenario(s); else newScenario();
      }
    } catch (err) {
      fill(root, h("div", { class: "qa-page" }, h("div", { class: "ix-error", text: "QA tab failed to load: " + err.message })));
      S.started = false;
    }
  }

  for (const tab of document.querySelectorAll('.tab[data-view="qa"]')) tab.addEventListener("click", start);
  if (root.classList.contains("active")) start();
  else new MutationObserver((_, obs) => {
    if (root.classList.contains("active")) { obs.disconnect(); start(); }
  }).observe(root, { attributes: true, attributeFilter: ["class"] });
})();
