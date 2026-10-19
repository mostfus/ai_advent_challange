// Headless UI test: loads static/index.html into jsdom, points it at a real
// server, and drives the same clicks a user would - tabs, the chat menu,
// settings controls, sending messages.
//
// Day 7 adds a second act: after all of that, the page is loaded *again* into
// a fresh jsdom against the same server, which is exactly what a reload (or a
// restart - the conversations live in files, not in the process) looks like
// from the browser's side. The checks after that point are all one question:
// did what was on screen come back?
//
// The server it starts is given CHAT_STORE_DIR pointing at a fresh temp
// directory, so the suite never reads or writes the conversations you have
// been having in the real app, and starts from nothing every run.
//
//   npm install     # once, pulls jsdom
//   npm test
//
// It starts its own uvicorn on TEST_PORT (8765 by default) and stops it again,
// so it never collides with a dev server on :8000. The API key handed to that
// process is deliberately a dummy: python-dotenv does not override an
// already-set variable, so a real key in .env is ignored and the test can
// never spend tokens. DeepSeek therefore answers every call with a 401, which
// is all these checks need - they are about the plumbing (which model and
// settings a chat sends, where the answer and the debug entry land, what
// the debug panel reports), not about model output.
//
// Adding a check: call check("what it should do", <condition>, <detail shown
// on the line>). Anything that fails sets the exit code.
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const { JSDOM } = require("jsdom");

const PROJECT_DIR = path.join(__dirname, "..");
const HTML = fs.readFileSync(path.join(PROJECT_DIR, "static", "index.html"), "utf8");
// Day 24: the page loads static/markdown.js before its own script, and jsdom
// fetches no scripts - so every page gets it evaluated before parsing, as a
// browser would have it.
const MARKDOWN_JS = fs.readFileSync(path.join(PROJECT_DIR, "static", "markdown.js"), "utf8");
const PORT = process.env.TEST_PORT || "8765";
const BASE = `http://127.0.0.1:${PORT}`;
// A throwaway store, so the suite can assert on "everything that is saved"
// without the real data/conversations getting in the way - or being harmed.
const STORE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "chat-store-test-"));
// Day 11 adds two more stores, and they need redirecting for a stronger
// reason than the conversations did. A task and the long-term layer are not
// scoped to a chat, so a suite run against the real ones would file test
// facts into the memory the app carries into *every* conversation - and then
// delete them again at the end.
const TASKS_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "chat-tasks-test-"));
const MEMORY_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "chat-memory-test-"));
// Day 12, and it needs redirecting for the strongest reason of the four: the
// profiles are not scoped to a chat *or* to a task, so a suite run against
// the real file would edit the personality the app answers every question
// under - and the server seeds three profiles the first time it starts, so an
// un-redirected run would also write them into a directory the user may have
// deliberately emptied.
const PERSONALITY_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "chat-personality-test-"));
// Day 14, and the same argument once more - with the sharpest consequence of
// the five if it is skipped. The invariants are not scoped to a chat or to a
// task either, so an un-redirected run would write rules into the set the app
// holds *every* answer to, in every conversation, and then delete them again.
const INVARIANTS_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "chat-invariants-test-"));
// Day 16, and the one redirect here that is not about protecting the user's
// data from the suite - `data/mcp/` is a cache and losing it costs a refresh.
// It is about the reverse: this file writes a *fabricated* catalogue into the
// store below, so that the render path can be checked without five requests
// to other people's servers. Pointed at the real directory, that invention
// would end up on screen in the actual app.
const MCP_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "chat-mcp-test-"));
const PROJECT_STORE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "chat-project-test-"));
// Day 21: the document index - its SQLite file and uploads. The suite edits
// them, so never the real ones. (Which files the corpus is made of is
// corpus.json's, and the suite only reads it.)
const INDEX_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "chat-index-test-"));
const sentBodies = [];
// Only the messages: the page also PUTs and DELETEs conversation records, and
// the fan-out checks below count chat requests, not HTTP requests.
const chatBodies = () => sentBodies.filter((b) => b.url === "/api/chat");
let server = null;

async function startServer() {
  server = spawn(
    "uv",
    ["run", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", PORT, "--log-level", "warning"],
    {
      cwd: PROJECT_DIR,
      env: {
        ...process.env,
        DEEPSEEK_API_KEY: "ui-test-dummy-key",
        CHAT_STORE_DIR: STORE_DIR,
        CHAT_TASKS_DIR: TASKS_DIR,
        CHAT_MEMORY_DIR: MEMORY_DIR,
        CHAT_PERSONALITY_DIR: PERSONALITY_DIR,
        CHAT_INVARIANTS_DIR: INVARIANTS_DIR,
        CHAT_MCP_DIR: MCP_DIR,
        CHAT_PROJECT_DIR: PROJECT_STORE_DIR,
        INDEX_DIR,
        // Day 26: llama-server starts with the app when local_llm.json says
        // so. Not here - the suite never asks the local model anything.
        LOCAL_LLM_AUTOSTART: "0",
        // Day 31: nor the project's MCP servers - no uvx, no npx, no network.
        PROJECT_MCP_AUTOSTART: "0",
      },
      stdio: "inherit",
    }
  );
  const up = await waitFor(async () => {
    try {
      return (await fetch(BASE + "/api/config")).ok;
    } catch {
      return false;
    }
  }, 30000);
  if (!up) {
    stopServer();
    throw new Error(`server did not come up on ${BASE}`);
  }
}

function stopServer() {
  if (server && !server.killed) server.kill("SIGTERM");
  for (const dir of [STORE_DIR, TASKS_DIR, MEMORY_DIR, INDEX_DIR, PROJECT_STORE_DIR]) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// What is actually on disk right now - the other half of every persistence
// check: the page shows it, and the store really has it.
function storedConversations() {
  return fs
    .readdirSync(STORE_DIR)
    .filter((name) => name.endsWith(".json"))
    .map((name) => JSON.parse(fs.readFileSync(path.join(STORE_DIR, name), "utf8")));
}

// The long-term layer as it is on disk - the other half of every day-11
// check, exactly as `storedConversations` is for the transcripts.
function storedLongTerm() {
  const file = path.join(MEMORY_DIR, "long_term.json");
  if (!fs.existsSync(file)) return [];
  return JSON.parse(fs.readFileSync(file, "utf8")).items || [];
}

function storedPersonality() {
  const file = path.join(PERSONALITY_DIR, "profiles.json");
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : null;
}

function storedInvariants() {
  const file = path.join(INVARIANTS_DIR, "rules.json");
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : null;
}

function mcpFile() {
  return path.join(MCP_DIR, "servers.json");
}

function storedMcp() {
  return fs.existsSync(mcpFile()) ? JSON.parse(fs.readFileSync(mcpFile(), "utf8")) : null;
}

// A catalogue as `tools.mcp_client.Listing.to_dict` would have produced one, written
// straight into the store. The point is the render path: everything the panel
// draws below - the qualified names, the status line, the wire trace, the
// count on the chip - comes from a shape a real server produced once and this
// object copies, so it can be asserted on every run without anyone's server
// being asked anything.
function writeCatalogues(servers) {
  fs.writeFileSync(mcpFile(), JSON.stringify({ servers: servers }, null, 2));
}

function storedTasks() {
  return fs
    .readdirSync(TASKS_DIR)
    .filter((name) => name.endsWith(".json"))
    .map((name) => JSON.parse(fs.readFileSync(path.join(TASKS_DIR, name), "utf8")));
}

let promptAnswer = "";

let failures = 0;
function check(name, cond, extra) {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}${extra ? "  → " + extra : ""}`);
  if (!cond) failures++;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return true;
    await sleep(50);
  }
  return false;
}

(async () => {
  await startServer();

  // The page's script runs during parsing, so the stubs have to be installed
  // in beforeParse - jsdom ships no fetch of its own.
  //
  // A function, because day 7 needs to do this twice: the second call is the
  // reload, and it shares nothing with the first except the server.
  //
  // `storage` is optional: pass the same object to two calls and the pages
  // share a localStorage, which is what a reload in one browser actually
  // looks like. Without it each page gets its own, as every earlier act
  // assumes.
  function loadPage(storage) {
    return new JSDOM(HTML, {
      runScripts: "dangerously",
      pretendToBeVisual: true,
      url: BASE + "/",
      beforeParse(window) {
        window.eval(MARKDOWN_JS);
        if (storage) {
          Object.defineProperty(window, "localStorage", {
            value: {
              getItem: (k) => (k in storage ? storage[k] : null),
              setItem: (k, v) => { storage[k] = String(v); },
              removeItem: (k) => { delete storage[k]; },
            },
          });
        }
        window.fetch = async (url, init) => {
          if (init && init.body) sentBodies.push(Object.assign({ url }, JSON.parse(init.body)));
          const res = await fetch(BASE + url, init);
          const text = await res.text();
          return { ok: res.ok, status: res.status, statusText: res.statusText, json: async () => JSON.parse(text) };
        };
        window.HTMLElement.prototype.scrollIntoView = () => {};
        // Day 11 names a new task with a prompt, and jsdom throws on the real
        // one. `promptAnswer` is what the next one returns.
        window.prompt = () => promptAnswer;
        Object.defineProperty(window.navigator, "clipboard", { value: { writeText: async () => {} } });
      },
    });
  }

  const dom = loadPage();
  const { window } = dom;
  const doc = window.document;

  const q = (sel) => doc.querySelector(sel);
  const qa = (sel) => Array.from(doc.querySelectorAll(sel));

  // ---- boot ----
  const booted = await waitFor(() => q("#chat-inner .chat-body"));
  check("config loads and single view mounts", booted);

  // ---- single view ----
  check("single view is the default tab", q("#single-view").classList.contains("active"));
  // The chat and, since day 21, the Index.
  check("exactly two tabs", qa(".tab").length === 2, `${qa(".tab").length} tabs`);
  check("no free-models, compare or RAG tab left behind",
    qa(".tab").every((t) => ["single", "index"].includes(t.dataset.view)) &&
    q("#free-view") === null && q("#compare-view") === null && q("#rag-view") === null);
  check("the page loads no RAG-tab script or styles",
    !/rag\.(js|css)/.test(HTML), (HTML.match(/[\w/.]*rag\.(js|css)/) || [""])[0]);
  check("single chat body mounted", !!q("#chat-inner .chat-body"));

  // ---- day 21: the Index tab ----
  // jsdom does not fetch <script src>, so the tab's script is run by hand -
  // the same file the browser loads. Nothing here needs a model: the
  // overview and the chunking preview are model-free.
  window.eval(fs.readFileSync(path.join(PROJECT_DIR, "static", "indexing.js"), "utf8"));
  qa(".tab").find((t) => t.dataset.view === "index").click();
  check("index tab shows its view", q("#index-view").classList.contains("active"));
  const pipelineUp = await waitFor(() => qa("#ix-pipeline .ix-stage").length === 5);
  check("index pipeline has five stages", pipelineUp, `${qa("#ix-pipeline .ix-stage").length} stages`);
  check("pipeline starts at a loader over the real corpus",
    /files/.test(q("#ix-pipeline .ix-stage .big").textContent), q("#ix-pipeline .ix-stage .big").textContent);
  // Day 31: the tab is the Project tab, and its first card is the folder.
  check("the tab is called Project", qa(".tab").find((t) => t.dataset.view === "index").textContent === "Project");
  check("the project starts as the app's own folder, in a git repository",
    q("#ix-project-path").value === PROJECT_DIR && /this app's own folder/.test(q("#ix-project-info").textContent) &&
    /git: /.test(q("#ix-project-info").textContent),
    q("#ix-project-path").value + " | " + q("#ix-project-info").textContent);
  check("with autostart off, the card says the MCP servers are off",
    /servers are off/.test(q("#ix-project-servers").textContent), q("#ix-project-servers").textContent);
  check("the corpus is read from the project's folder",
    q("#ix-corpus-config").textContent.includes(PROJECT_DIR), q("#ix-corpus-config").textContent);
  q("#ix-project-path").value = "not/absolute";
  q("#ix-project-open").click();
  const pathRefused = await waitFor(() => /absolute path/.test(q("#ix-project-error").textContent));
  check("a relative path is refused with a reason, and nothing is opened", pathRefused,
    q("#ix-project-error").textContent);
  const statsUp = await waitFor(() => qa("#ix-pstats .ix-stat").length > 0);
  check("the structural chunker shows its preview stats", statsUp);
  check("one chunker, one Build button - no second strategy to build or compare",
    qa("#ix-chunker [data-param]").every((i) => ["max_words", "min_words", "overlap"].includes(i.dataset.param)) &&
    qa("#index-view button").filter((b) => /^Build/.test(b.textContent)).map((b) => b.textContent).join() === "Build index",
    qa("#index-view button").map((b) => b.textContent).join(" | "));
  check("no comparison and no retrieval check on the tab",
    q("#ix-compare") === null && q("#ix-questions") === null && q("#ix-eval") === null &&
    !/Comparison|Retrieval check/.test(q("#index-view").textContent));
  check("the chunk browser has no strategy to pick", q("#ix-browse-strategy") === null);
  const chunksBefore = q("#ix-pstats .ix-stat .v").textContent;
  const maxWords = qa('#ix-chunker input[type="range"]').find((i) => i.dataset.param === "max_words");
  maxWords.value = "60";
  maxWords.dispatchEvent(new window.Event("input", { bubbles: true }));
  const moved = await waitFor(() => q("#ix-pstats .ix-stat .v").textContent !== chunksBefore, 5000);
  check("moving the max-words slider recomputes the chunk count", moved,
    `${chunksBefore} -> ${q("#ix-pstats .ix-stat .v").textContent}`);
  const browserUp = await waitFor(() => qa("#ix-browser .ix-chunk").length > 0);
  check("chunk browser shows the chunks of one document", browserUp);
  qa(".tab").find((t) => t.dataset.view === "single").click();
  check("back to the single view", q("#single-view").classList.contains("active"));

  // The single view runs on the agent's defaults, so it has no parameter form
  // and no settings sidebar - one button in the topbar opens one popover.
  check("the settings sidebar is gone", q("#settings-panel") === null);
  check("single view has no parameter form", q("#single-view .settings-body") === null);
  check("the settings button lives in the topbar", !!q("#topbar #settings-btn"));

  // ---- the settings popover ----
  const settingsBtn = q("#settings-btn");
  const popover = q("#settings-popover");
  check("popover starts closed",
    popover.classList.contains("hidden") && settingsBtn.getAttribute("aria-expanded") === "false");
  check("the debug toggle lives inside the popover", !!popover.querySelector("#debug-toggle"));
  // Days 9 and 10 put two controls in here, and they are deliberately not
  // model parameters: the context window and the strategy are about what the
  // chat remembers, which is the one thing the single view has always let you
  // decide. Temperature and the rest are not offered on the page at all; day
  // 26 brings one exception, the model, so a chat can be switched to the
  // local one.
  check("no model parameters in the popover",
    popover.querySelectorAll("input[type=range], input[type=text], textarea").length === 0,
    `${popover.querySelectorAll("input[type=range], input[type=text], textarea").length} controls`);
  // Day 22 adds the RAG field - k - which is about what the question is
  // answered from, not a model parameter either. Day 23 adds the second stage
  // to it: how many candidates, and the threshold. There is one index, so
  // nothing to pick it from.
  const ragControls = popover.querySelectorAll("#rag-field input[type=number], #rag-field select").length;
  check("the model, the window and the strategy are the only controls in it, besides the RAG field",
    popover.querySelectorAll("input[type=number]").length - popover.querySelectorAll("#rag-field input[type=number]").length === 1 &&
    popover.querySelectorAll("select").length === 2 &&
    ragControls === 3 &&
    !!popover.querySelector("#single-model") &&
    !!popover.querySelector("#context-window") && !!popover.querySelector("#context-strategy"));
  // Day 26: the local model (local_llm.json) is one of the options, labelled
  // as local, and picking it says what is loaded - nothing else.
  const singleModel = popover.querySelector("#single-model");
  const localOption = Array.from(singleModel.options).find((o) => o.text.includes("local (llama.cpp)"));
  check("the single chat's model list has the local model, labelled", !!localOption,
    Array.from(singleModel.options).map((o) => o.text).join(", "));
  check("its hint is hidden while a DeepSeek model is picked", popover.querySelector("#single-model-hint").hidden);
  if (localOption) {
    singleModel.value = localOption.value;
    singleModel.dispatchEvent(new window.Event("change"));
    const hint = popover.querySelector("#single-model-hint");
    check("picking the local model shows where it runs", !hint.hidden && hint.textContent.includes("local_llm.json"),
      hint.textContent);
    const remembered = JSON.parse(window.localStorage.getItem("deepseek-chat-menu-settings") || "{}");
    check("and it is the chat's model now - remembered for the next chat too", remembered.model === localOption.value,
      JSON.stringify(remembered.model));
    singleModel.value = "deepseek-v4-flash";
    singleModel.dispatchEvent(new window.Event("change"));
  }

  settingsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  check("clicking the button opens the popover",
    !popover.classList.contains("hidden") && settingsBtn.getAttribute("aria-expanded") === "true");

  // A click on the checkbox must not close the popover out from under it.
  popover.querySelector(".checkbox-label")
    .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  check("clicking inside the popover keeps it open", !popover.classList.contains("hidden"));

  doc.body.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  check("clicking outside closes the popover",
    popover.classList.contains("hidden") && settingsBtn.getAttribute("aria-expanded") === "false");

  settingsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  doc.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check("Escape closes the popover", popover.classList.contains("hidden"));

  settingsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  check("the button reopens it", !popover.classList.contains("hidden"));
  settingsBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  check("the button toggles it shut again", popover.classList.contains("hidden"));

  check("single debug body mounted", !!q("#debug-panel .debug-body"));

  q("#debug-toggle").checked = true;
  q("#debug-toggle").dispatchEvent(new window.Event("change"));
  check("single debug panel toggles on", q("#single-view").classList.contains("show-debug"));

  // ---- the single chat ----
  sentBodies.length = 0;
  const singleInput = q("#chat-inner .prompt-input");
  singleInput.value = "single mode still works";
  q("#chat-inner .send-btn").click();
  await waitFor(() => q("#chat-inner .msg-row.assistant") && !q("#chat-inner .bubble.pending"));
  check("single chat sends the agent's default configuration",
    chatBodies().length === 1 && chatBodies()[0].settings.model === "deepseek-v4-flash" &&
    chatBodies()[0].settings.temperature === 1 &&
    chatBodies()[0].settings.reasoning_effort === "low" &&
    chatBodies()[0].settings.response_format === "text",
    JSON.stringify(chatBodies()[0] && chatBodies()[0].settings));
  check("the single chat always uses the same conversation id",
    chatBodies()[0].conversation_id === "single", chatBodies()[0].conversation_id);
  check("the request names no view and no branch - a conversation is one line",
    !("view" in chatBodies()[0]) && !("branch" in chatBodies()[0]), Object.keys(chatBodies()[0]).join(", "));
  check("single debug log recorded it", q("#debug-panel .debug-entry") !== null);

  // ---- debug JSON must wrap instead of running off to the right ----
  const jsonView = q("#debug-panel .json-view");
  const jsonStyle = window.getComputedStyle(jsonView);
  check("debug JSON wraps inside the panel",
    jsonStyle.whiteSpace === "pre-wrap" && jsonStyle.overflowWrap === "anywhere",
    `white-space: ${jsonStyle.whiteSpace}, overflow-wrap: ${jsonStyle.overflowWrap}`);
  check("Copy button no longer overlays the JSON text",
    q("#debug-panel .debug-block summary .copy-btn") !== null &&
    q("#debug-panel .json-wrap .copy-btn") === null);

  const copyBtn = q("#debug-panel .debug-block summary .copy-btn");
  const block = copyBtn.closest("details");
  const openBefore = block.open;
  copyBtn.click();
  check("clicking Copy does not fold the block", block.open === openBefore);

  // ---- day 6: the agent's own HTTP call is visible ----
  // The keys handed to this process are dummies, so DeepSeek answers 401 -
  // which is exactly the case that used to show no HTTP information at all.
  const wire = q("#debug-panel .debug-wire");
  check("debug entry shows the HTTP call the agent made", !!wire, wire && wire.textContent);
  check("the wire line names the real endpoint",
    wire.textContent.includes("POST") &&
    wire.textContent.includes("https://api.deepseek.com/chat/completions"),
    wire.textContent);
  check("a failed call still reports its status code",
    wire.textContent.includes("401") &&
    !!q("#debug-panel .wire-status-bad"),
    wire.textContent);
  check("the meta line carries the status too",
    q("#debug-panel .debug-entry-meta").textContent.includes("HTTP 401"),
    q("#debug-panel .debug-entry-meta").textContent);

  const headerBlocks = Array.from(qa("#debug-panel .debug-block summary"))
    .filter((el) => el.textContent.includes("Request headers"));
  check("request headers are logged", headerBlocks.length === 1, `${headerBlocks.length} blocks`);
  const headerJson = headerBlocks[0].closest("details").querySelector(".json-view").textContent;
  check("the API key is redacted in the debug panel",
    headerJson.includes("Bearer ***") && !headerJson.includes("ui-test-dummy-key"),
    headerJson);

  // ======================================================================
  // Day 8: what everything cost
  // ======================================================================
  // The usage strip under the transcript. Every number in it comes out of the
  // API's `usage` block, so most of it cannot be exercised here: the key is a
  // dummy and a 401 carries no usage at all. That case is the one worth
  // checking on a live page - nothing billed, nothing claimed - and the full
  // readout is checked at the very end, against a conversation planted on
  // disk with real counts in it.
  const usageBar = q("#chat-inner .usage-bar");
  const usageDetail = q("#chat-inner .usage-detail");

  check("the single chat has a usage strip", !!usageBar);
  check("it is labelled, and nothing more",
    usageBar.textContent.trim().replace(/[\u25b8\u25be]/g, "").trim() === "Usage",
    JSON.stringify(usageBar.textContent));
  check("the breakdown starts folded away",
    usageDetail.classList.contains("hidden") &&
    usageBar.getAttribute("aria-expanded") === "false");

  usageBar.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  check("clicking the strip unfolds it",
    !usageDetail.classList.contains("hidden") &&
    usageBar.getAttribute("aria-expanded") === "true");
  // Every answer in this suite is a 401. A row of zeroes here would read as
  // "these answers were free" rather than "no answer has been paid for".
  check("a chat nobody was charged for claims no numbers",
    usageDetail.querySelectorAll(".token-row").length === 0 &&
    usageDetail.textContent.includes("Nothing billed"),
    usageDetail.textContent.trim().slice(0, 60));

  check("the page never asks the server to price anything",
    sentBodies.every((b) => b.url !== "/api/tokens"),
    sentBodies.map((b) => b.url).join(", "));

  // ======================================================================
  // Day 7: the conversation outlives the page
  // ======================================================================

  // ---- what is actually on disk ----
  const stored = storedConversations();
  const storedSingle = stored.find((c) => c.id === "single");

  check("the single chat was written to the store", !!storedSingle);
  check("it stored the turn that was sent",
    storedSingle.messages.length === 2 &&
    storedSingle.messages[0].role === "user" &&
    storedSingle.messages[0].content === "single mode still works",
    `${storedSingle.messages.length} messages`);
  // The key is a dummy, so every answer here is a 401. Those are kept - they
  // are part of what was on screen - but flagged, so they can be left out of
  // what gets replayed to the model.
  check("a failed answer is stored, and marked as an error",
    storedSingle.messages[1].role === "assistant" && storedSingle.messages[1].error === true);
  // Day 8: and it carries no usage, because nothing was billed for it. A zero
  // here would be a lie of a different kind than a missing number.
  check("a turn that failed records no cost",
    storedSingle.messages[1].usage === undefined,
    JSON.stringify(storedSingle.messages[1].usage));

  check("it is the only conversation stored, and it is one line - no view, no branches",
    stored.length === 1 && !("view" in storedSingle) && !("branches" in storedSingle) &&
    !("checkpoints" in storedSingle),
    `${stored.length} records: ${Object.keys(storedSingle).join(", ")}`);

  // ---- the reload: a brand new page against the same server ----
  const dom2 = loadPage();
  const doc2 = dom2.window.document;
  const q2 = (sel) => doc2.querySelector(sel);
  const qa2 = (sel) => Array.from(doc2.querySelectorAll(sel));

  const rebooted = await waitFor(() => q2("#chat-inner .msg-row"));
  check("the reloaded page restores the single chat", rebooted);
  check("it restores exactly the messages that were saved",
    qa2("#chat-inner .msg-row").length === 2,
    `${qa2("#chat-inner .msg-row").length} rows`);
  check("the restored text is the text that was sent",
    q2("#chat-inner .msg-row.user .bubble").textContent === "single mode still works",
    q2("#chat-inner .msg-row.user .bubble").textContent);
  check("a failed turn comes back as an error bubble",
    !!q2("#chat-inner .msg-row.assistant .bubble.error"));
  check("a divider marks where the restored history ends",
    !!q2("#chat-inner .restored-divider"),
    q2("#chat-inner .restored-divider") && q2("#chat-inner .restored-divider").textContent);
  check("the empty state is gone once there is something to show",
    q2("#chat-inner .empty-state").style.display === "none");

  // ---- the clear-context button ----
  const clearBtn2 = q2("#clear-context-btn");
  const status2 = q2("#context-status");
  check("the settings popover has a clear-context button", !!clearBtn2);
  check("it reports how much is saved", status2.textContent === "2 messages saved on the server.",
    status2.textContent);

  clearBtn2.click();
  check("one click only arms the button - nothing is cleared yet",
    clearBtn2.classList.contains("armed") &&
    qa2("#chat-inner .msg-row").length === 2,
    clearBtn2.textContent);

  clearBtn2.click();
  const cleared = await waitFor(() => qa2("#chat-inner .msg-row").length === 0);
  check("the second click clears the transcript", cleared,
    `${qa2("#chat-inner .msg-row").length} rows left`);
  check("the empty state comes back",
    q2("#chat-inner .empty-state").style.display === "block");
  check("the button disarms itself again",
    !clearBtn2.classList.contains("armed") && clearBtn2.textContent === "Clear context",
    clearBtn2.textContent);
  check("and says there is nothing left to clear",
    status2.textContent === "No saved messages yet." && clearBtn2.disabled === true,
    status2.textContent);

  const afterClear = storedConversations().find((c) => c.id === "single");
  check("clearing empties the stored conversation", afterClear.messages.length === 0,
    `${afterClear.messages.length} messages`);
  check("but keeps the conversation itself", !!afterClear && afterClear.id === "single");

  // ---- third load: the cleared chat stays cleared ----
  const dom3 = loadPage();
  const doc3 = dom3.window.document;
  await waitFor(() => doc3.querySelector("#chat-inner .chat-body"));
  check("a cleared chat comes back empty, not restored",
    doc3.querySelectorAll("#chat-inner .msg-row").length === 0 &&
    !doc3.querySelector("#chat-inner .restored-divider"),
    `${doc3.querySelectorAll("#chat-inner .msg-row").length} rows`);

  // ---- day 8: what the turns cost, read back off disk ----
  // A dummy key never produces a `usage` block, so two paid turns are planted
  // in exactly the shape /api/chat writes when DeepSeek does answer - and then
  // the page is asked what it makes of them. Two turns, not one, so the chat
  // totals cannot accidentally agree with the last turn.
  const paidTurn = (prompt, completion, reasoning, cached) => ({
    prompt_tokens: prompt, completion_tokens: completion,
    total_tokens: prompt + completion, reasoning_tokens: reasoning,
    content_tokens: completion - reasoning, cached_tokens: cached,
  });
  fs.writeFileSync(path.join(STORE_DIR, "usage-demo.json"), JSON.stringify({
    id: "usage-demo",
    settings: { model: "deepseek-v4-pro", system_prompt: "You are a helpful assistant.",
      stop: [], response_format: "text", reasoning_effort: "low", temperature: 1.0 },
    messages: [
      { role: "user", content: "first", ts: "2030-01-01T00:00:00.000+00:00" },
      { role: "assistant", content: "one", ts: "2030-01-01T00:00:01.000+00:00",
        usage: paidTurn(100, 40, 25, 0) },
      { role: "user", content: "second", ts: "2030-01-01T00:00:02.000+00:00" },
      { role: "assistant", content: "two", ts: "2030-01-01T00:00:03.000+00:00",
        usage: paidTurn(230, 90, 60, 128) },
    ],
    created_at: "2030-01-01T00:00:00.000+00:00",
    updated_at: "2030-01-01T00:00:03.000+00:00",
  }));

  // Opened the way a browser that was last looking at it would open it.
  const dom4 = loadPage({ "deepseek-chat-active": "usage-demo" });
  const doc4 = dom4.window.document;
  await waitFor(() => doc4.querySelectorAll("#chat-inner .msg-row").length === 4);
  const demoBar = doc4.querySelector("#chat-inner .usage-bar");
  demoBar.dispatchEvent(new dom4.window.MouseEvent("click", { bubbles: true }));
  const demoDetail = doc4.querySelector("#chat-inner .usage-detail");
  const sections = Array.from(demoDetail.querySelectorAll(".token-section"));
  const rows = (section) => Array.from(section.querySelectorAll(".token-row"))
    .map((r) => `${r.children[0].textContent}=${r.children[1].textContent}`);

  check("a restored chat still knows what it was billed",
    sections.length === 2, `${sections.length} sections`);
  check("the last turn: its request, its answer, its total",
    rows(sections[0]).join(" ").includes("request · prompt=230") &&
    rows(sections[0]).join(" ").includes("answer · completion=90") &&
    rows(sections[0]).join(" ").includes("total=320"),
    rows(sections[0]).join("  |  "));
  check("and how much of that answer was thinking",
    rows(sections[0]).some((r) => r === "of which thinking=60"),
    rows(sections[0]).join("  |  "));
  check("the whole chat: every turn added up, not just the last",
    sections[1].querySelector("h3").textContent.includes("2 turns") &&
    rows(sections[1]).join(" ").includes("requests · prompt=330") &&
    rows(sections[1]).join(" ").includes("answers · completion=130") &&
    rows(sections[1]).join(" ").includes("total=460"),
    sections[1].querySelector("h3").textContent + " :: " + rows(sections[1]).join("  |  "));
  check("thinking is summed across the chat too",
    rows(sections[1]).some((r) => r === "of which thinking=85"),
    rows(sections[1]).join("  |  "));
  check("cached prompt tokens are reported where the API reported them",
    rows(sections[0]).some((r) => r === "of which served from cache=128") &&
    rows(sections[1]).some((r) => r === "of which served from cache=128"),
    rows(sections[1]).join("  |  "));

  // The same numbers, from the endpoint the page was rebuilt from.
  const listed = await (await fetch(BASE + "/api/conversations")).json();
  const demoRecord = listed.conversations.find((c) => c.id === "usage-demo");
  check("the API reports the same totals it drew",
    demoRecord.usage.total.total_tokens === 460 &&
    demoRecord.usage.total.reasoning_tokens === 85 &&
    demoRecord.usage.last.total_tokens === 320 &&
    demoRecord.usage.total.turns === 2,
    JSON.stringify(demoRecord.usage.total));
  // Planted for this act only: the menu act below counts the chats there are.
  fs.unlinkSync(path.join(STORE_DIR, "usage-demo.json"));

  // ======================================================================
  // More than one conversation, and a menu to pick between them
  // ======================================================================
  // The single view used to be one conversation with a fixed id. It is now a
  // list of them - every conversation on the server, one mounted at a time -
  // and the topbar menu is what picks. Two pages share a localStorage here, so
  // this act can ask whether the choice survived a reload.
  const browser = {};
  const dom5 = loadPage(browser);
  const win5 = dom5.window;
  const doc5 = win5.document;
  const q5 = (sel) => doc5.querySelector(sel);
  const qa5 = (sel) => Array.from(doc5.querySelectorAll(sel));
  const click5 = (el) => el.dispatchEvent(new win5.MouseEvent("click", { bubbles: true }));
  const items5 = () => qa5(".chat-item");
  const previews5 = () => items5().map((i) => i.querySelector(".chat-item-preview").textContent.trim());
  const activeItem5 = () => items5().find((i) => i.classList.contains("active"));
  const shown5 = () => qa5("#chat-inner .msg-row .bubble").map((b) => b.textContent);

  await waitFor(() => q5("#chat-inner .chat-body"));
  const chatsBtn5 = q5("#chats-btn");
  const chatsPopover5 = q5("#chats-popover");

  check("the topbar has a conversations menu", !!chatsBtn5 && !!chatsPopover5);
  check("it starts closed",
    chatsPopover5.classList.contains("hidden") &&
    chatsBtn5.getAttribute("aria-expanded") === "false");

  click5(chatsBtn5);
  check("clicking it opens the list",
    !chatsPopover5.classList.contains("hidden") &&
    chatsBtn5.getAttribute("aria-expanded") === "true");
  check("the saved conversation is listed, and marked as the one on screen",
    items5().length === 1 && !!activeItem5(),
    `${items5().length} chats`);
  // The single chat was cleared earlier in this suite, so it has no opening
  // line to be named by.
  check("a chat with nothing in it says so rather than showing a blank row",
    previews5()[0] === "New chat" &&
    items5()[0].querySelector(".chat-item-meta").textContent === "empty",
    previews5()[0] + " / " + items5()[0].querySelector(".chat-item-meta").textContent);
  check("the button carries the current chat's name",
    q5("#chats-btn-label").textContent === "New chat",
    q5("#chats-btn-label").textContent);

  // ---- a second conversation ----
  click5(q5("#new-chat-btn"));
  await waitFor(() => storedConversations().length === 2);
  check("+ New chat adds one", items5().length === 2, `${items5().length} chats`);
  check("and it is registered on the server straight away, before anything is said",
    storedConversations().length === 2,
    storedConversations().map((c) => c.id).join(", "));
  check("the new chat is the one on screen",
    activeItem5() === items5()[1] && shown5().length === 0,
    `${shown5().length} messages shown`);
  check("the menu closes once a chat is picked", chatsPopover5.classList.contains("hidden"));

  // ---- what you say in it becomes what it is called ----
  const firstLine = "what is this conversation about";
  q5("#chat-inner .prompt-input").value = firstLine;
  q5("#chat-inner .send-btn").click();
  await waitFor(() => q5("#chat-inner .msg-row.assistant") && !q5("#chat-inner .bubble.pending"));
  check("a chat is named by its opening line",
    q5("#chats-btn-label").textContent === firstLine,
    q5("#chats-btn-label").textContent);
  click5(chatsBtn5);
  check("the menu says the same thing, and counts what is in it",
    previews5()[1] === firstLine &&
    items5()[1].querySelector(".chat-item-meta").textContent === "2 messages",
    previews5().join("  |  "));

  // ---- switching ----
  click5(items5()[0]);
  check("picking the other chat swaps the transcript",
    shown5().length === 0 && q5("#chats-btn-label").textContent === "New chat",
    `${shown5().length} messages shown`);
  check("only the chat on screen is in the page",
    qa5("#chat-inner .prompt-input").length === 1 &&
    qa5("#chat-inner .chat-body").length === 1 &&
    qa5("#debug-panel .debug-body").length === 1,
    `${qa5("#chat-inner .chat-body").length} chat bodies`);
  click5(chatsBtn5);
  click5(items5()[1]);
  check("and switching back brings the other one's messages with it",
    shown5().length === 2 && shown5()[0] === firstLine,
    shown5().map((t) => t.slice(0, 24)).join("  |  "));
  check("its usage strip came along too", !!q5("#chat-inner .usage-bar"));

  // ---- the choice survives a reload ----
  const activeBefore = q5("#chats-btn-label").textContent;
  const dom6 = loadPage(browser);
  const doc6 = dom6.window.document;
  await waitFor(() => doc6.querySelector("#chat-inner .chat-body"));
  await waitFor(() => doc6.querySelectorAll("#chat-inner .msg-row").length === 2);
  check("a reload comes back to the conversation you were in",
    doc6.querySelector("#chats-btn-label").textContent === activeBefore,
    doc6.querySelector("#chats-btn-label").textContent);
  check("with its transcript restored from the server",
    doc6.querySelectorAll("#chat-inner .msg-row").length === 2,
    `${doc6.querySelectorAll("#chat-inner .msg-row").length} rows`);
  check("and both conversations still in the menu",
    doc6.querySelectorAll(".chat-item").length === 2,
    `${doc6.querySelectorAll(".chat-item").length} chats`);
  check("every stored conversation is in the menu",
    doc6.querySelectorAll(".chat-item").length === storedConversations().length,
    `${doc6.querySelectorAll(".chat-item").length} of ${storedConversations().length} records`);

  // ---- deleting one ----
  const singlesBefore = storedConversations().length;
  dom6.window.document.querySelector("#chats-btn")
    .dispatchEvent(new dom6.window.MouseEvent("click", { bubbles: true }));
  doc6.querySelectorAll(".chat-item")[1].querySelector(".chat-item-remove")
    .dispatchEvent(new dom6.window.MouseEvent("click", { bubbles: true }));
  const deleted = await waitFor(() => storedConversations().length === singlesBefore - 1);
  check("deleting a conversation removes it from the store", deleted,
    `${storedConversations().length} left`);
  check("and falls back to the one next to it",
    doc6.querySelectorAll(".chat-item").length === 1 &&
    doc6.querySelectorAll("#chat-inner .msg-row").length === 0,
    `${doc6.querySelectorAll(".chat-item").length} chats, ` +
    `${doc6.querySelectorAll("#chat-inner .msg-row").length} rows`);

  // ======================================================================
  // Day 9: the window, the summary, and what actually goes up the wire
  // ======================================================================
  // The two things worth checking here are the two the feature is made of:
  // that the request carries the summary plus exactly N messages and not one
  // more, and that the messages the summary stands for are not *also* sent.
  // Both are questions about a request body, which is the one thing this
  // suite can see in full even though every answer is a 401.
  //
  // A summary is something only a real key can produce, so - as in day 8 -
  // one is planted on disk in exactly the shape the server writes.
  const SUMMARY_TEXT = "The user is called Maksim, is building an advent-calendar app, " +
    "and has asked for answers in Russian.";
  const plantedTurns = (count, tag) => {
    const messages = [];
    for (let i = 1; i <= count; i++) {
      messages.push({ role: "user", content: `${tag} question ${i}`,
        ts: `2031-01-01T00:00:${String(i * 2).padStart(2, "0")}.000+00:00` });
      messages.push({ role: "assistant", content: `${tag} answer ${i}`,
        ts: `2031-01-01T00:00:${String(i * 2 + 1).padStart(2, "0")}.000+00:00` });
    }
    return messages;
  };
  const plantChat = (id, extra) => {
    fs.writeFileSync(path.join(STORE_DIR, id + ".json"), JSON.stringify(Object.assign({
      id: id,
      settings: { model: "deepseek-v4-flash", system_prompt: "You are a helpful assistant.",
        stop: [], response_format: "text", reasoning_effort: "low", temperature: 1.0,
        context_messages: 4, context_strategy: "summary" },
      messages: plantedTurns(6, id),
      created_at: "2031-01-01T00:00:00.000+00:00",
      updated_at: "2031-01-01T00:00:13.000+00:00",
    }, extra)));
  };
  const plantedSummary = {
    text: SUMMARY_TEXT, covers: 8, updated_at: "2031-01-01T00:00:13.000+00:00",
    model: "deepseek-v4-flash", compressions: 2,
    usage: { prompt_tokens: 400, completion_tokens: 80, total_tokens: 480,
      reasoning_tokens: 0, content_tokens: 80, cached_tokens: 0 },
    usage_total: { prompt_tokens: 700, completion_tokens: 140, total_tokens: 840,
      reasoning_tokens: 0, content_tokens: 140, cached_tokens: 0 },
  };
  const ask = (id, settings, message) => fetch(BASE + "/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: id, settings }),
  }).then((r) => r.json());
  const onSettings = { model: "deepseek-v4-flash", system_prompt: "You are a helpful assistant.",
    stop: [], response_format: "text", reasoning_effort: "low", temperature: 1.0,
    context_messages: 4, context_strategy: "summary" };

  // ---- summary + window, on the wire ----
  plantChat("ctx-wire", { created_at: "2031-01-02T00:00:00.000+00:00",
    summary: plantedSummary });
  const wireReply = await ask("ctx-wire", onSettings, "and what was my name again?");
  const sent = wireReply.debug.request.messages;

  check("the request is the system prompt, the summary, the window and the question",
    sent.length === 7 && sent[0].role === "system" && sent[6].role === "user" &&
    sent[6].content === "and what was my name again?",
    `${sent.length} messages: ${sent.map((m) => m.role).join(",")}`);
  check("the summary rides as a system message, labelled for what it is",
    sent[1].role === "system" && sent[1].content.includes(SUMMARY_TEXT) &&
    sent[1].content.includes("no longer included verbatim"),
    JSON.stringify(sent[1].content).slice(0, 80));
  check("exactly the last 4 messages are sent verbatim",
    sent.slice(2, 6).length === 4 &&
    sent[2].content === "ctx-wire question 5" && sent[4].content === "ctx-wire question 6",
    sent.slice(2, 6).map((m) => m.content).join(" | "));
  check("and nothing the summary stands for is sent again as itself",
    !sent.some((m) => /question [1-4]$/.test(m.content || "")),
    sent.map((m) => m.content.slice(0, 20)).join(" | "));
  check("the server says the same thing about the turn it just sent",
    wireReply.context.sent.messages === 4 && wireReply.context.sent.summarised === 8 &&
    wireReply.context.sent.summary_used === true && wireReply.context.sent.compressed_now === false,
    JSON.stringify(wireReply.context.sent));
  check("a summary that already reaches the cut is reused, not rewritten",
    wireReply.debug.compaction === null,
    JSON.stringify(wireReply.debug.compaction && wireReply.debug.compaction.error));
  check("the whole transcript is still on the server behind it",
    wireReply.context.messages === 14 && wireReply.context.replayable === 12,
    `${wireReply.context.messages} stored, ${wireReply.context.replayable} replayable`);

  // ---- the same chat with compression switched off ----
  const off = await ask("ctx-wire", Object.assign({}, onSettings,
    { context_strategy: "window" }), "without the summary this time");
  check("the sliding window is day 7 again: the window, and nothing older",
    off.debug.request.messages.length === 6 &&
    !off.debug.request.messages.some((m) => m.content.includes(SUMMARY_TEXT)),
    off.debug.request.messages.map((m) => m.role).join(","));
  check("and the summary is not deleted by switching strategy",
    !!storedConversations().find((c) => c.id === "ctx-wire").summary,
    JSON.stringify(!!storedConversations().find((c) => c.id === "ctx-wire").summary));

  // ---- a compression that fails ----
  // The key here is a dummy, so asking for a summary gets a 401 - which is the
  // one path that must not cost the user their answer, or their transcript.
  plantChat("ctx-fail", { created_at: "2031-01-03T00:00:00.000+00:00" });
  const failed = await ask("ctx-fail", Object.assign({}, onSettings,
    { context_messages: 2 }), "does this still work?");
  check("a failed compression still sends the turn",
    failed.debug.request.messages.length === 4 &&
    failed.debug.request.messages[3].content === "does this still work?",
    failed.debug.request.messages.map((m) => m.role).join(","));
  check("the summarisation request is in the debug log, with its error",
    !!failed.debug.compaction && failed.debug.compaction.status_code === 401 &&
    !!failed.debug.compaction.error,
    JSON.stringify(failed.debug.compaction && failed.debug.compaction.error));
  check("and the failure is reported rather than passed off as a summary",
    failed.context.sent.compressed_now === false && !!failed.context.sent.error &&
    failed.context.summary === null,
    JSON.stringify(failed.context.sent.error));
  check("nothing was written down that was never produced",
    !storedConversations().find((c) => c.id === "ctx-fail").summary);

  // ---- the strip, on the page ----
  // On a conversation picked from the switcher; the page opens on the oldest
  // one, the suite's own `single`.
  plantChat("ctx-view", { created_at: "2031-01-04T00:00:00.000+00:00",
    summary: plantedSummary });
  const dom7 = loadPage();
  const win7 = dom7.window;
  const doc7 = dom7.window.document;
  const click7 = (el) => el.dispatchEvent(new win7.MouseEvent("click", { bubbles: true }));
  await waitFor(() => doc7.querySelector("#chat-inner .chat-body"));

  // ---- the single view's own two controls, on the chat it opens with ----
  const windowInput = doc7.querySelector("#context-window");
  const strategyPick = doc7.querySelector("#context-strategy");
  check("the single chat can be given a window without a parameter form",
    !!windowInput && !!strategyPick && Number(windowInput.value) === 40,
    windowInput && windowInput.value);
  check("and a strategy, offered as the three the server knows about",
    Array.from(strategyPick.options).map((o) => o.value).join(",") === "window,facts,summary" &&
    strategyPick.value === "window",
    Array.from(strategyPick.options).map((o) => o.value).join(","));

  windowInput.value = "999";
  windowInput.dispatchEvent(new win7.Event("change", { bubbles: true }));
  check("a window outside the bounds snaps back to them",
    windowInput.value === "200", windowInput.value);
  windowInput.value = "6";
  windowInput.dispatchEvent(new win7.Event("change", { bubbles: true }));
  strategyPick.value = "summary";
  strategyPick.dispatchEvent(new win7.Event("change", { bubbles: true }));

  const singleId = "single";
  const saved9 = await waitFor(() => {
    const record = storedConversations().find((c) => c.id === singleId);
    return record.settings.context_messages === 6 && record.settings.context_strategy === "summary";
  });
  check("and both controls are saved with the conversation, not in the browser", saved9,
    JSON.stringify(storedConversations().find((c) => c.id === singleId).settings));
  check("the strip follows the controls before anything is sent",
    doc7.querySelector("#chat-inner .context-bar").textContent.includes("rolling summary"),
    doc7.querySelector("#chat-inner .context-bar").textContent.trim());

  // ---- and the chat that already has a summary ----
  click7(doc7.querySelector("#chats-btn"));
  const viewItem = Array.from(doc7.querySelectorAll(".chat-item"))
    .find((i) => i.querySelector(".chat-item-preview").textContent.includes("ctx-view question 1"));
  click7(viewItem);
  await waitFor(() => doc7.querySelectorAll("#chat-inner .msg-row").length === 12);

  check("a compressed chat carries its window and strategy with it, not the view's",
    Number(doc7.querySelector("#context-window").value) === 4 &&
    doc7.querySelector("#context-strategy").value === "summary",
    doc7.querySelector("#context-window").value);

  const ctxBar = doc7.querySelector("#chat-inner .context-bar");
  const ctxDetail = doc7.querySelector("#chat-inner .context-detail");
  check("every chat has a context strip, next to the usage one",
    !!ctxBar && !!doc7.querySelector("#chat-inner .usage-bar") &&
    ctxDetail.classList.contains("hidden"));
  check("the strip names this chat's strategy without being opened",
    ctxBar.textContent.includes("rolling summary"), ctxBar.textContent.trim());

  click7(ctxBar);
  const ctxRows = Array.from(ctxDetail.querySelectorAll(".token-row"))
    .map((r) => `${r.children[0].textContent}=${r.children[1].textContent}`);
  check("opened, it says what the next request will carry",
    ctxRows.some((r) => r === "sent verbatim · window=4") &&
    ctxRows.some((r) => r === "stood for by the summary=8") &&
    ctxRows.some((r) => r === "kept on the server=12"),
    ctxRows.join("  |  "));
  check("the summary itself is shown in full, not summarised again",
    ctxDetail.querySelector(".summary-text").textContent === SUMMARY_TEXT,
    ctxDetail.querySelector(".summary-text").textContent.slice(0, 40));
  check("with what it stands for, and what it cost to write",
    ctxDetail.querySelector(".token-section:nth-child(2) h3").textContent.includes("8 messages") &&
    ctxDetail.querySelector(".summary-meta").textContent.includes("rewritten 2 times") &&
    ctxDetail.querySelector(".summary-meta").textContent.includes("840 tokens"),
    ctxDetail.querySelector(".summary-meta").textContent);

  // ---- forgetting the summary, but not the conversation ----
  const forgetBtn = ctxDetail.querySelector(".summary-forget");
  click7(forgetBtn);
  check("forgetting the summary asks first", forgetBtn.classList.contains("armed"),
    forgetBtn.textContent);
  click7(forgetBtn);
  const forgotten = await waitFor(() =>
    !storedConversations().find((c) => c.id === "ctx-view").summary);
  check("the second click deletes it from the store", forgotten);
  check("and every message it stood for is still there",
    storedConversations().find((c) => c.id === "ctx-view").messages.length === 12,
    `${storedConversations().find((c) => c.id === "ctx-view").messages.length} messages`);
  await waitFor(() => !ctxDetail.querySelector(".summary-text"));
  check("the strip stops claiming a summary it no longer has",
    !ctxDetail.querySelector(".summary-text") &&
    ctxDetail.textContent.includes("will compress what is outside the window"),
    ctxDetail.textContent.trim().slice(-70));

  // ---- and they come back on the next page ----
  const dom8 = loadPage();
  const doc8 = dom8.window.document;
  await waitFor(() => doc8.querySelector("#chat-inner .chat-body"));
  await waitFor(() => Number(doc8.querySelector("#context-window").value) === 6);
  check("a reloaded chat remembers how much it is meant to remember, and how",
    Number(doc8.querySelector("#context-window").value) === 6 &&
    doc8.querySelector("#context-strategy").value === "summary",
    doc8.querySelector("#context-window").value);

  // ======================================================================
  // DAY 10: three strategies for the same window
  // ======================================================================

  const FACT_ITEMS = {
    "goal": "port the store to sqlite",
    "constraint.language": "answer in russian",
    "decision.schema": "one row per message",
  };
  const plantFacts = (id, extra) => plantChat(id, Object.assign({
    settings: { model: "deepseek-v4-flash", system_prompt: "You are a helpful assistant.",
      stop: [], response_format: "text", reasoning_effort: "low", temperature: 1.0,
      context_messages: 4, context_strategy: "facts" },
    facts: { items: FACT_ITEMS, updated_at: "2031-01-01T00:00:13.000+00:00",
      model: "deepseek-v4-flash", updates: 5,
      usage_total: { prompt_tokens: 300, completion_tokens: 60, total_tokens: 360,
        reasoning_tokens: 0, content_tokens: 60, cached_tokens: 0 } },
  }, extra));
  const windowSettings = Object.assign({}, onSettings, { context_strategy: "window" });
  const factSettings = Object.assign({}, onSettings, { context_strategy: "facts" });

  // ---- strategy 1: the sliding window, and what it admits to losing ----
  plantChat("s10-window", { created_at: "2031-02-01T00:00:00.000+00:00" });
  const win10 = await ask("s10-window", windowSettings, "what did I ask you first?");
  const winSent = win10.debug.request.messages;
  check("sliding window: the system prompt, the last 4 messages and the question",
    winSent.length === 6 && winSent.filter((m) => m.role === "system").length === 1,
    `${winSent.length} messages: ${winSent.map((m) => m.role).join(",")}`);
  check("sliding window: nothing stands in for what was cut",
    !winSent.some((m) => /^(Summary|Established facts)/.test(m.content)),
    winSent[1].content);
  check("sliding window: and the strip says how many were dropped, not hidden",
    win10.context.strategy === "window" && win10.context.sent.dropped === 8 &&
    win10.context.replayable === 12,
    JSON.stringify(win10.context.sent));
  check("sliding window: no second request is made, so it costs nothing extra",
    win10.debug.facts === null && win10.debug.compaction === null);

  // ---- strategy 2: sticky facts, on the wire ----
  plantFacts("s10-facts", { created_at: "2031-02-02T00:00:00.000+00:00" });
  const facts10 = await ask("s10-facts", factSettings, "so what was the plan?");
  const factSent = facts10.debug.request.messages;
  check("sticky facts: the block rides as a labelled system message",
    factSent.length === 7 && factSent[1].role === "system" &&
    factSent[1].content.includes("Treat them as current"),
    `${factSent.length} messages: ${factSent.map((m) => m.role).join(",")}`);
  check("sticky facts: one key-value line each, not prose",
    factSent[1].content.includes("goal: port the store to sqlite") &&
    factSent[1].content.includes("constraint.language: answer in russian"),
    JSON.stringify(factSent[1].content.slice(-80)));
  check("sticky facts: the window is still the window behind it",
    factSent.slice(2, 6).length === 4 && factSent[2].content === "s10-facts question 5" &&
    factSent[6].content === "so what was the plan?",
    factSent.slice(2).map((m) => m.content).join(" | "));
  check("sticky facts: the messages the block stands for are not sent as well",
    !factSent.some((m) => /question [1-4]$/.test(m.content || "")),
    factSent.map((m) => m.content.slice(0, 24)).join(" | "));
  check("sticky facts: the strip reports the block it sent",
    facts10.context.strategy === "facts" && facts10.context.facts.count === 3 &&
    facts10.context.sent.facts_used === true && facts10.context.sent.dropped === 8,
    JSON.stringify(facts10.context.sent));
  check("sticky facts: what maintaining it has cost is reported on its own line",
    facts10.usage.facts.updates === 5 && facts10.usage.facts.total_tokens === 360 &&
    facts10.usage.total.total_tokens === 0,
    JSON.stringify(facts10.usage.facts));

  // ---- an extraction that fails ----
  // The key here is a dummy, so every update gets a 401 - which is the one
  // path that must not cost the user their answer or their memory.
  check("sticky facts: a failed update still sends the turn",
    factSent[6].content === "so what was the plan?" &&
    facts10.debug.facts.status_code === 401 && !!facts10.debug.facts.error,
    JSON.stringify(facts10.debug.facts.error));
  check("sticky facts: and the turn goes up on the block as it already stood",
    facts10.context.sent.facts_changed === false && !!facts10.context.sent.error,
    JSON.stringify(facts10.context.sent.error));
  check("sticky facts: nothing was written down that was never produced",
    JSON.stringify(storedConversations().find((c) => c.id === "s10-facts").facts.items) ===
      JSON.stringify(FACT_ITEMS));

  // ---- the same chat, switched to the window ----
  const noFacts = await ask("s10-facts", windowSettings, "and without them?");
  check("switching strategy changes the next request, not the stored memory",
    noFacts.debug.request.messages.length === 6 &&
    !!storedConversations().find((c) => c.id === "s10-facts").facts,
    noFacts.debug.request.messages.map((m) => m.role).join(","));

  // ---- all of it, on the page ----
  plantFacts("s10-view", { created_at: "2031-02-04T00:00:00.000+00:00" });
  const dom10 = loadPage();
  const win10doc = dom10.window;
  const doc10 = dom10.window.document;
  const click10 = (el) => el.dispatchEvent(new win10doc.MouseEvent("click", { bubbles: true }));
  await waitFor(() => doc10.querySelector("#chat-inner .chat-body"));
  click10(doc10.querySelector("#chats-btn"));
  const viewItem10 = Array.from(doc10.querySelectorAll(".chat-item"))
    .find((i) => i.querySelector(".chat-item-preview").textContent.includes("s10-view question 1"));
  click10(viewItem10);
  await waitFor(() => doc10.querySelectorAll("#chat-inner .msg-row").length === 12);

  const strip10 = doc10.querySelector("#chat-inner .context-bar");
  check("the strip names the strategy this chat is on",
    strip10.textContent.includes("sticky facts"), strip10.textContent.trim());
  click10(strip10);
  const detail10 = doc10.querySelector("#chat-inner .context-detail");
  const factRows = Array.from(detail10.querySelectorAll(".fact-row"))
    .map((r) => `${r.querySelector(".fact-key").textContent}=${r.querySelector(".fact-value").textContent}`);
  check("opened, it shows every fact in full - one line each, key first",
    factRows.length === 3 && factRows[0] === "goal=port the store to sqlite",
    factRows.join("  |  "));
  check("with what keeping them has cost",
    detail10.querySelector(".summary-meta").textContent.includes("updated 5 times") &&
    detail10.querySelector(".summary-meta").textContent.includes("360 tokens"),
    detail10.querySelector(".summary-meta").textContent);
  const ctxRows10 = Array.from(detail10.querySelectorAll(".token-row"))
    .map((r) => `${r.children[0].textContent}=${r.children[1].textContent}`);
  check("and says the messages are dropped while the facts are not",
    ctxRows10.some((r) => r === "carried as facts=3") &&
    ctxRows10.some((r) => r === "dropped \u00b7 only the facts remain=8"),
    ctxRows10.join("  |  "));

  const forgetFacts = detail10.querySelector(".facts-forget");
  click10(forgetFacts);
  click10(forgetFacts);
  const factsGone = await waitFor(() =>
    !storedConversations().find((c) => c.id === "s10-view").facts);
  check("forgetting the facts asks first, then keeps every message", factsGone &&
    storedConversations().find((c) => c.id === "s10-view").messages.length === 12,
    `${storedConversations().find((c) => c.id === "s10-view").messages.length} messages`);

  // ---- a conversation is one line: no branch bar, no checkpoints ----
  check("there is no branch bar, no checkpoint and no per-message mark",
    !doc10.querySelector(".branch-bar") && !doc10.querySelector(".checkpoint-mark") &&
    !doc10.querySelector(".msg-mark") && !/Checkpoint|Branch\u2026/.test(doc10.querySelector("#chat-inner").textContent));
  const branchRoute = await fetch(BASE + "/api/conversations/s10-view/branches",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  check("and no branch or checkpoint routes behind them",
    [404, 405].includes(branchRoute.status), String(branchRoute.status));

  // A record written while chats could branch opens as the branch that was
  // active, and is written back flat the next time it is saved.
  fs.writeFileSync(path.join(STORE_DIR, "s10-old.json"), JSON.stringify({
    id: "s10-old",
    view: "single",
    settings: windowSettings,
    messages: plantedTurns(3, "s10-old"),
    branches: [
      { id: "branch-2", name: "plan B", parent: "main", forked_at: 4,
        messages: [{ role: "user", content: "s10-old forked question", ts: "2031-02-05T00:01:00.000+00:00" },
          { role: "assistant", content: "s10-old forked answer", ts: "2031-02-05T00:01:01.000+00:00" }],
        facts: { items: { "branch.fact": "only in the fork" }, updates: 1 } },
    ],
    active_branch: "branch-2",
    checkpoints: [{ id: "cp-1", branch: "main", index: 4, label: "before" }],
    created_at: "2031-02-05T00:00:00.000+00:00",
    updated_at: "2031-02-05T00:01:01.000+00:00",
  }));
  const dom11 = loadPage({ "deepseek-chat-active": "s10-old" });
  const doc11 = dom11.window.document;
  await waitFor(() => doc11.querySelectorAll("#chat-inner .msg-row").length === 6);
  const oldRows = Array.from(doc11.querySelectorAll("#chat-inner .msg-row .bubble")).map((b) => b.textContent);
  check("an old branched chat opens as its active branch: the shared past, then the fork's own",
    oldRows.length === 6 && oldRows[3] === "s10-old answer 2" && oldRows[5] === "s10-old forked answer" &&
    !oldRows.includes("s10-old question 3"),
    oldRows.join(" | "));
  const oldRecord = await (await fetch(BASE + "/api/conversations/s10-old")).json();
  check("with the fork's own memory, and nothing about branches on the wire",
    oldRecord.facts && oldRecord.facts.items["branch.fact"] === "only in the fork" &&
    !("branching" in oldRecord) && !("branches" in oldRecord) && !("view" in oldRecord),
    Object.keys(oldRecord).join(", "));
  await ask("s10-old", windowSettings, "s10-old after the move");
  const oldOnDisk = storedConversations().find((c) => c.id === "s10-old");
  check("and the next save writes it flat",
    oldOnDisk.messages.length === 8 && !("branches" in oldOnDisk) && !("active_branch" in oldOnDisk) &&
    !("checkpoints" in oldOnDisk) && !("view" in oldOnDisk) && oldOnDisk.messages[5].content === "s10-old forked answer",
    Object.keys(oldOnDisk).join(", "));

  // ------------------------------------------------------------------ day 11
  //
  // Three layers, and the question this act asks of each of them is the same
  // one: what lands in it, what reaches the model out of it, and what is
  // still there on the next page. The short layer is covered by the day-9
  // and day-10 acts above; these checks are about the two that outlive a
  // conversation, and about the fact that a person - not the agent - is what
  // puts anything in them.

  const dom12 = loadPage();
  const doc12 = dom12.window.document;
  const q12 = (sel) => doc12.querySelector(sel);
  const qa12 = (sel) => Array.from(doc12.querySelectorAll(sel));
  const click12 = (el) => el.dispatchEvent(new dom12.window.MouseEvent("click", { bubbles: true }));
  // The memory button toggles, and filing something leaves the popover open,
  // so "click it to look at the panel" would sometimes shut it instead.
  const openMemory12 = () => {
    if (q12("#memory-popover").classList.contains("hidden")) click12(q12("#memory-btn"));
  };
  await waitFor(() => q12("#chat-inner .chat-body"));

  check("the memory button lives in the topbar next to the settings",
    !!q12("#topbar #memory-btn") && !!q12("#memory-popover"));
  check("the memory popover starts closed",
    q12("#memory-popover").classList.contains("hidden"));

  openMemory12();
  await waitFor(() => qa12("#memory-body .layer").length >= 3);
  check("opening it actually shows something",
    dom12.window.getComputedStyle(q12("#memory-popover")).display !== "none",
    dom12.window.getComputedStyle(q12("#memory-popover")).display);
  const titles = qa12("#memory-body .layer-title").map((t) => t.textContent.trim());
  check("all three layers are named, and named by the server",
    titles.some((t) => t.includes("Working")) && titles.some((t) => t.includes("Long-term")) &&
    titles.some((t) => t.includes("Short-term")),
    titles.join("  |  "));
  const writers = qa12("#memory-body .layer-writer").map((w) => w.textContent.trim());
  check("each layer says who writes to it - the column the day turns on",
    writers.some((w) => w.includes("agent")) && writers.filter((w) => w.includes("You")).length === 2,
    writers.join("  |  "));
  check("nothing is modal on arrival - there is no dialog to get stuck in",
    q12("#file-modal") === null && q12(".modal-backdrop") === null);

  // Every check in this file passed while the popover was opening off the
  // side of the page, because "is it in the DOM with the class off" and "can
  // you see it" are different questions. `.popover` is absolutely
  // positioned, so its anchor has to establish the containing block, and
  // that is the part a class-name assertion cannot notice.
  for (const id of ["memory-anchor", "settings-anchor", "chats-anchor"]) {
    check(`#${id} gives its popover something to be positioned against`,
      dom12.window.getComputedStyle(q12("#" + id)).position === "relative",
      dom12.window.getComputedStyle(q12("#" + id)).position || "(static)");
  }
  check("and the memory button shows its pressed state like the others",
    !!HTML.match(/#memory-btn\[aria-expanded="true"\]/));
  check("with no task joined, there is no working layer to speak of",
    q12("#memory-body .task-closed-note") !== null &&
    q12("#memory-body .task-closed-note").textContent.includes("not part of a task"));

  // ---- a task, and a chat joined to it ----
  promptAnswer = "Port the store to SQLite";
  click12(Array.from(q12("#memory-body .task-row").querySelectorAll("button"))
    .find((b) => b.textContent.includes("New")));
  await waitFor(() => storedTasks().length === 1);
  check("a task is a record of its own, not a field on a chat",
    storedTasks().length === 1 && storedTasks()[0].title === "Port the store to SQLite" &&
    storedTasks()[0].phase === "planning",
    JSON.stringify(storedTasks()[0] && { t: storedTasks()[0].title, p: storedTasks()[0].phase }));
  const taskId = storedTasks()[0].id;
  await waitFor(() => storedConversations().some((c) => c.task_id === taskId));
  check("and the chat that made it has joined it",
    storedConversations().some((c) => c.task_id === taskId));

  // ---- say something, then file it by hand ----
  sentBodies.length = 0;
  q12("#chat-inner .prompt-input").value = "we are storing one row per message";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => q12("#chat-inner .msg-row.assistant") && !q12("#chat-inner .bubble.pending"));

  // ---- the slash menu: what you can type, above where you type it ----
  const input12 = q12("#chat-inner .prompt-input");
  const menu12 = q12("#chat-inner .slash-menu");
  check("the command menu starts hidden", menu12.classList.contains("hidden"));
  input12.value = "/";
  input12.dispatchEvent(new dom12.window.Event("input", { bubbles: true }));
  check("typing a slash offers the commands, above the box",
    !menu12.classList.contains("hidden") &&
    Array.from(menu12.children).filter((c) => !c.classList.contains("hidden")).length === 3,
    Array.from(menu12.querySelectorAll("code")).map((c) => c.textContent.trim()).join(" "));
  input12.value = "/ta";
  input12.dispatchEvent(new dom12.window.Event("input", { bubbles: true }));
  const shown = Array.from(menu12.children).filter((c) => !c.classList.contains("hidden"));
  check("and narrows as you type",
    shown.length === 1 && shown[0].textContent.includes("/task"),
    shown.map((s) => s.querySelector("code").textContent.trim()).join(" "));
  input12.value = "/task something";
  input12.dispatchEvent(new dom12.window.Event("input", { bubbles: true }));
  check("once there is a note to file, the menu gets out of the way",
    menu12.classList.contains("hidden"));
  // Day 34: /help is gone - agentic code is a switch, not a command.
  input12.value = "/he";
  input12.dispatchEvent(new dom12.window.Event("input", { bubbles: true }));
  check("/help is no longer offered",
    menu12.classList.contains("hidden") && !menu12.textContent.includes("/help"), menu12.textContent);

  // ---- filing with a command, no keys anywhere ----
  sentBodies.length = 0;
  input12.value = "/task one row per message";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => storedTasks()[0].items.length === 1);
  const filed = storedTasks()[0].items[0];
  check("a command files the note and never asks for a key",
    filed.value === "one row per message" && filed.key === "one-row-per-message" &&
    filed.source.conversation === "single",
    JSON.stringify(filed));
  check("and in no other layer", storedLongTerm().length === 0);
  check("filing costs no request to the model", chatBodies().length === 0);
  check("the transcript says what happened, without pretending anyone said it",
    !!q12("#chat-inner .mem-receipt") &&
    q12("#chat-inner .mem-receipt").textContent.includes("Port the store to SQLite"),
    q12("#chat-inner .mem-receipt") && q12("#chat-inner .mem-receipt").textContent);

  // The key is derived from the note, so re-filing the same note is a
  // correction and re-filing a differently-worded one is a second entry.
  // That asymmetry is deliberate: a duplicate is visible in the panel and
  // one click to remove, while a silent overwrite destroys something the
  // person deliberately filed.
  input12.value = "/task one row per message";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => qa12("#chat-inner .mem-receipt").length === 2);
  check("filing the same note again corrects it rather than duplicating it",
    storedTasks()[0].items.length === 1, `${storedTasks()[0].items.length} items`);

  input12.value = "/task one row per message, JSON-encoded";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => storedTasks()[0].items.length === 2);
  check("a differently worded note is a second entry, visible and prunable",
    storedTasks()[0].items.length === 2 &&
    storedTasks()[0].items[1].value.includes("JSON"),
    storedTasks()[0].items.map((i) => i.value).join("  |  "));

  // ---- the long-term layer, and the rule that rides on every request ----
  input12.value = "/always answer in Russian";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => storedLongTerm().length === 1);
  check("the long-term layer is one file for everything, not one per chat",
    storedLongTerm().length === 1 && storedLongTerm()[0].value === "answer in Russian",
    JSON.stringify(storedLongTerm()[0]));
  check("/always is the whole of what key prefixes used to mean",
    storedLongTerm()[0].pinned === true);
  check("the task layer did not grow with it", storedTasks()[0].items.length === 2,
    `${storedTasks()[0].items.length} items`);

  openMemory12();
  await waitFor(() => qa12("#memory-body .mem-always").length === 1);
  check("and the panel marks it as riding on every request",
    qa12("#memory-body .mem-always").length === 1);

  // ---- the star does the same job with one click ----
  input12.value = "/remember the billing day is the 5th";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => storedLongTerm().length === 2);
  const loose = storedLongTerm().find((i) => i.value.includes("billing"));
  check("a plain /remember is not always-sent", !loose.pinned);
  // The star on *that* item, rather than the first unstarred one on the panel:
  // the working layer is drawn above the long-term one and its items have
  // stars of their own, so "the first one that is off" names whichever row
  // happens to be on top.
  const billingRow = qa12("#memory-body .mem-item")
    .find((r) => r.textContent.includes("billing"));
  const unstarred = billingRow && billingRow.querySelector(".mem-star");
  check("every item has a star saying whether it always rides",
    !!unstarred && !unstarred.classList.contains("on"));
  click12(unstarred);
  await waitFor(() => (storedLongTerm().find((i) => i.value.includes("billing")) || {}).pinned);
  check("clicking it is the whole interface for always-send",
    storedLongTerm().find((i) => i.value.includes("billing")).pinned === true);
  check("and nothing was duplicated by the re-file", storedLongTerm().length === 2,
    `${storedLongTerm().length} items`);

  // ---- a command that cannot work says so instead of failing quietly ----
  input12.value = "/task";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => qa12("#chat-inner .mem-receipt.error").length === 1);
  check("a command with nothing after it is explained, not swallowed",
    qa12("#chat-inner .mem-receipt.error").length === 1,
    qa12("#chat-inner .mem-receipt.error").map((r) => r.textContent)[0]);

  // ---- what actually reaches the model ----
  sentBodies.length = 0;
  q12("#chat-inner .prompt-input").value = "what did we decide about naming?";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => chatBodies().length === 1);
  await waitFor(() => !q12("#chat-inner .bubble.pending"));

  const entries = qa12("#debug-panel .debug-entry");
  const requestText = entries.map((e) => e.textContent).join("\n");
  check("the two layers ride as their own labelled system messages",
    requestText.includes("What you know about this user") &&
    requestText.includes("What has been established about the task"),
    [requestText.includes("What you know about this user"),
     requestText.includes("What has been established about the task")].join(","));
  check("the task's name is in the block, so the scope is nameable",
    requestText.includes("Port the store to SQLite"));
  check("the language rule rode along although nothing asked about language",
    requestText.includes("answer in Russian"));

  const counts = qa12("#memory-body .layer-count").map((c) => c.textContent.trim());
  check("the panel reports what was sent against what is held",
    counts.some((c) => c.includes("sent last turn")), counts.join("  |  "));

  // ---- a second chat, same task ----
  click12(q12("#chats-btn"));
  click12(q12("#new-chat-btn"));
  await waitFor(() => q12("#chat-inner .msg-row") === null);
  openMemory12();
  // Wait for the panel to actually say "no task" rather than reading the
  // selector the instant the chat switches: the chat-scoped half of the
  // panel is confirmed with the server, so there is a round trip in here.
  await waitFor(() => {
    const note = q12("#memory-body .task-closed-note");
    return note && note.textContent.includes("not part of a task");
  });
  const selector = q12("#memory-body .task-row select");
  check("a new chat starts in no task at all", selector.value === "", selector.value);

  q12("#chat-inner .prompt-input").value = "/task this should not be filed anywhere";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => q12("#chat-inner .mem-receipt.error"));
  check("/task in a chat with no task explains itself and files nothing",
    q12("#chat-inner .mem-receipt.error").textContent.includes("not part of a task") &&
    storedTasks()[0].items.length === 2,
    q12("#chat-inner .mem-receipt.error").textContent);
  selector.value = taskId;
  selector.dispatchEvent(new dom12.window.Event("change", { bubbles: true }));
  await waitFor(() => qa12("#memory-body .mem-value")
    .some((k) => k.textContent.includes("one row per message")));
  check("joining the task hands the second chat everything the first filed",
    qa12("#memory-body .mem-value").some((k) => k.textContent.includes("one row per message")));

  sentBodies.length = 0;
  q12("#chat-inner .prompt-input").value = "remind me of the schema";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => chatBodies().length === 1);
  await waitFor(() => !q12("#chat-inner .bubble.pending"));
  const secondRequest = qa12("#debug-panel .debug-entry").map((e) => e.textContent).join("\n");
  check("and carries it into a conversation that never heard it said",
    secondRequest.includes("one row per message"));

  // ------------------------------------------------------------------
  // Day 13: the task is a state machine. Four stages, a table of which may
  // follow which, a checklist per stage that holds the way forward, and a
  // pause that works in all four - every one of them enforced in `phases.py`
  // rather than asked for in a prompt.
  //
  // These checks come in pairs throughout: what the panel drew, and what the
  // file on disk says. A machine whose refusals were only in the UI would be
  // a styling choice.
  // ------------------------------------------------------------------
  const pills = () => qa12("#memory-body .phase-pill");
  const pill = (label) => pills().find((p) => p.textContent.trim() === label);
  // Day 13: the rail reports, it does not move anything - an unreachable
  // stage wears `.blocked` rather than being a disabled button, with the
  // machine's own sentence on it, and a stage that is only waiting on its
  // checklist says *that* rather than looking the same as a refusal.
  //
  // Nothing on this panel sets a stage by hand while the agent is reading
  // answers. That was the bug this act now checks for: a "Move to" menu that
  // was always there sent `confirm`, `confirm` ticks every outstanding
  // required step, and so planning could be walked through to done in four
  // choices with nothing agreed, built or checked. The menu exists only as
  // the fallback for a chat where the agent half is switched off - checked
  // further down - and otherwise the stage moves one way: the agent reports,
  // `check` rules, a person accepts.
  const blocked = (label) => pill(label).classList.contains("blocked");
  const why = (label) => pill(label).title;
  const moveMenu = () => q12("#memory-body .phase-go select");
  const offered = () => (moveMenu()
    ? Array.from(moveMenu().options).slice(1).map((o) => o.textContent)
    : []);
  const moveTo = (label) => {
    const option = Array.from(moveMenu().options).find((o) => o.textContent.startsWith(label));
    moveMenu().value = option.value;
    moveMenu().dispatchEvent(new dom12.window.Event("change", { bubbles: true }));
  };
  const taskStateToggle = () => q12("#task-state-toggle");
  const letAgentMove = (on) => {
    taskStateToggle().checked = on;
    taskStateToggle().dispatchEvent(new dom12.window.Event("change", { bubbles: true }));
  };
  const stepBoxes = () => qa12("#memory-body .step-row input");
  const stepLabel = (i) => qa12("#memory-body .step-row .step-label")[i].textContent.trim();
  const taskFile = () => storedTasks()[0];
  const postApi = (path, body) => fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  // The agent's half, planted on disk rather than driven, because raising a
  // suggestion needs a model that answers: the suite's key is a dummy, so
  // every turn errors and the watcher is never asked. What is checked
  // throughout is the half a person deals with - that an offer is drawn,
  // that accepting it moves the task through the same machine, and that
  // accepting it cannot buy a jump.
  const plantSuggestion = (to, reason) => {
    const file = path.join(TASKS_DIR, `${taskId}.json`);
    const task = JSON.parse(fs.readFileSync(file, "utf8"));
    task.suggested = { to: to, why: reason, at: "2026-09-20T10:00:00.000+00:00" };
    fs.writeFileSync(file, JSON.stringify(task));
  };
  const reopenMemory = async () => {
    click12(q12("#memory-btn"));
    click12(q12("#memory-btn"));
    await waitFor(() => q12("#memory-body .task-row"));
  };
  const suggest = () => q12("#memory-body .phase-suggest");
  const suggestBtn = (label) => Array.from(suggest().querySelectorAll("button"))
    .find((b) => b.textContent.toLowerCase().includes(label));
  // One accepted offer, end to end: plant it, redraw the panel, press the
  // button a person would press, and wait for the turn it sends in answer.
  const acceptMove = async (to, reason) => {
    plantSuggestion(to, reason);
    await reopenMemory();
    await waitFor(() => suggest());
    click12(suggestBtn("move to"));
    await waitFor(() => taskFile().phase === to);
    await waitFor(() => !q12("#chat-inner .bubble.pending"));
  };

  check("every stage is drawn, not only the reachable ones",
    pills().length === 4 &&
    pills().map((p) => p.textContent.trim()).join(",") ===
      "Planning,Execution,Validation,Done",
    pills().map((p) => p.textContent.trim()).join(","));
  check("a new task starts in planning, and that pill is the current one",
    pill("Planning").classList.contains("current") && taskFile().phase === "planning");

  // The two refusals, side by side, and they are refused for different
  // reasons - which is the whole point of `Decision` carrying one.
  check("a jump over a stage is drawn as out of reach, with the reason on it",
    blocked("Done") && pill("Done").title.includes("cannot go straight to done"),
    pill("Done").title);
  check("and there is nothing to press anywhere on the panel that sets a stage",
    !moveMenu() && !q12("#memory-body .phase-go"),
    offered().join("  |  "));
  check("the stage waiting on its checklist is not refused, it is not yet",
    !blocked("Execution") && why("Execution").includes("Not yet") &&
    why("Execution").includes("Goal and definition of done"),
    why("Execution"));
  check("the checklist is the one in phases.py, required items marked",
    stepBoxes().length === 3 &&
    stepLabel(0).includes("Goal and definition of done") &&
    qa12("#memory-body .step-row")[0].classList.contains("required"),
    stepLabel(0));
  check("and the expected action is the first required step still outstanding",
    q12("#memory-body .phase-expected").textContent.includes("what is being built"),
    q12("#memory-body .phase-expected").textContent.trim());

  // The refusal is not only a disabled button: the rule is in the server, and
  // a client that ignored the button gets the same sentence back.
  const refused = await postApi(`/api/tasks/${taskId}/phase`, { to: "done" });
  const refusedBody = await refused.json();
  check("the same jump over HTTP is refused with the machine's own reason",
    refused.status === 409 && refusedBody.detail.includes("cannot go straight to done"),
    `${refused.status} ${refusedBody.detail}`);
  check("and nothing is written down about it - a move that did not happen is not history",
    !taskFile().log.some((e) => e.to === "done"),
    JSON.stringify(taskFile().log));

  // ---- ticking the gate open ----
  click12(stepBoxes()[0]);
  await waitFor(() => (taskFile().steps.planning || []).includes("goal"));
  await waitFor(() => stepBoxes()[0].checked);
  check("one required step ticked still leaves one, and the rail says which",
    why("Execution").includes("Not yet") &&
    why("Execution").includes("An agreed list of steps") &&
    !why("Execution").includes("Goal and definition"),
    why("Execution"));
  click12(stepBoxes()[2]);
  await waitFor(() => (taskFile().steps.planning || []).includes("plan"));
  await waitFor(() => why("Execution") === "Reachable from here");
  check("both of them, and the gate is open - but nothing here opens it",
    why("Execution") === "Reachable from here" && blocked("Done") && !moveMenu(),
    why("Execution"));

  // ---- the only way the panel moves a task: the agent offers, you accept --
  await acceptMove("execution", "the plan is agreed");
  await waitFor(() => pill("Execution").classList.contains("current"));
  check("the stage moves because an offer was accepted, and the file says so too",
    taskFile().phase === "execution" &&
    taskFile().log.some((e) => e.kind === "move" && e.to === "execution"));
  check("the checklist is now execution's, and nothing is ticked in it",
    stepBoxes().length === 3 && stepBoxes().every((b) => !b.checked) &&
    stepLabel(0).includes("Every planned step"),
    stepLabel(0));
  check("the rail keeps teaching: back is free, on is not yet, and done is refused",
    why("Planning") === "Reachable from here" &&
    why("Validation").includes("Not yet") &&
    blocked("Done") && why("Done").includes("cannot go straight to done"),
    [why("Planning"), why("Validation"), why("Done")].join("  |  "));

  // ---- and the fallback, which is the reason the menu still exists at all -
  //
  // With "let the agent move the task between stages" off nothing reads the
  // answers, so no offer will ever be raised: a panel with no control on it
  // would leave the work frozen where it stands. Switched back on, the
  // by-hand menu has to go again - that switch is the whole of who may move
  // the task.
  letAgentMove(false);
  await waitFor(() => moveMenu());
  check("switching the agent half off brings the by-hand menu back",
    !!moveMenu() &&
    q12("#memory-body .phase-go-why").textContent.includes("not reading answers"),
    offered().join("  |  "));
  check("and it still lists only what the machine allows",
    offered().some((o) => o === "Planning") &&
    offered().some((o) => o.startsWith("Validation") && o.includes("marks 1 step")) &&
    !offered().some((o) => o.startsWith("Done")),
    offered().join("  |  "));
  letAgentMove(true);
  await waitFor(() => !moveMenu());
  check("switching it back on takes it away again - the agent offers, you accept",
    !moveMenu() && !q12("#memory-body .phase-go"));

  // ---- the state block reaches the model ----
  sentBodies.length = 0;
  q12("#chat-inner .prompt-input").value = "carry on then";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => chatBodies().length === 1);
  await waitFor(() => !q12("#chat-inner .bubble.pending"));
  const stateRequest = qa12("#debug-panel .debug-entry").slice(-1)
    .map((e) => e.textContent).join("\n");
  check("the request carries where the task has got to",
    stateRequest.includes("Where this task has got to") &&
    stateRequest.includes("Stage 2 of 4: EXECUTION"));
  check("with the checklist, and the one sentence that says what is next",
    stateRequest.includes("Every planned step done") &&
    stateRequest.includes("Expected next action:"));
  check("and the instruction that stops it explaining the task back",
    stateRequest.includes("do not restate the task back to the user"));
  check("and the rule the model is held to, so it does not answer out of stage",
    stateRequest.includes("Stay in this stage") &&
    stateRequest.includes("can only go to"));
  check("with what is outstanding, so refusing to skip ahead can be specific",
    stateRequest.includes("Still outstanding in this stage"));

  // ---- the agent's opinion about the stage, waiting for a person ----
  //
  // The invariant first, and asked the hard way: the task is in execution, so
  // `done` has no edge to it. Confirming is a person saying "this stage is
  // done" and that is the only rule it can answer.
  const jumped = await postApi(`/api/tasks/${taskId}/phase`,
    { to: "done", confirm: true });
  check("confirming cannot buy a move that has no edge",
    jumped.status === 409 && taskFile().phase === "execution",
    `${jumped.status} in ${taskFile().phase}`);

  plantSuggestion("validation", "the build is finished");
  await reopenMemory();
  await waitFor(() => q12("#memory-body .phase-suggest"));
  check("the agent's suggestion is drawn where the stage is",
    suggest().textContent.includes("ready for validation"),
    suggest().textContent.trim().slice(0, 60));
  check("and it says which boxes accepting it will tick",
    suggest().textContent.includes("Every planned step done"),
    suggest().textContent.trim());
  check("it is an offer, not a move - the task has not budged",
    taskFile().phase === "execution", taskFile().phase);

  sentBodies.length = 0;
  click12(suggestBtn("move to"));
  await waitFor(() => taskFile().phase === "validation");
  check("accepting it moves the task, ticking what the stage was waiting for",
    taskFile().phase === "validation" &&
    (taskFile().steps.execution || []).includes("work"),
    JSON.stringify({ phase: taskFile().phase, steps: taskFile().steps }));
  check("and the suggestion is gone, having been answered",
    !taskFile().suggested, JSON.stringify(taskFile().suggested));

  // Saying yes to "shall we move on" and then doing nothing is not an answer:
  // the agent asked, so accepting sends it a turn.
  await waitFor(() => chatBodies().length === 1);
  await waitFor(() => !q12("#chat-inner .bubble.pending"));
  check("accepting answers the agent rather than only the record",
    chatBodies().length === 1 &&
    chatBodies()[0].message.includes("now in validation"),
    chatBodies()[0] && chatBodies()[0].message);
  const movedRequest = qa12("#debug-panel .debug-entry").slice(-1)
    .map((e) => e.textContent).join("\n");
  check("and that turn goes up under the stage it was just moved to",
    movedRequest.includes("Stage 3 of 4: VALIDATION"));

  // "Not yet" is the other answer, and it leaves no trace either.
  plantSuggestion("done", "checked and passing");
  await reopenMemory();
  await waitFor(() => q12("#memory-body .phase-suggest"));
  click12(suggestBtn("not yet"));
  await waitFor(() => !taskFile().suggested);
  check("declining one leaves the stage exactly where it was",
    taskFile().phase === "validation" && !taskFile().suggested,
    taskFile().phase);

  // Back where the rest of the act expects it: execution, freshly re-entered,
  // so its required box is clear again.
  await postApi(`/api/tasks/${taskId}/phase`, { to: "execution" });
  await waitFor(() => taskFile().phase === "execution");
  await reopenMemory();
  // The panel refreshes over the network, so the screen trails the disk by a
  // round trip - wait for the screen, since that is what is checked next.
  await waitFor(() =>
    q12("#chat-inner .stage-bar").textContent.includes("EXECUTION"));

  // ---- the stage, next to the box you type in ----
  const stageBar = () => q12("#chat-inner .stage-bar");
  check("the stage is on screen without opening a panel",
    !stageBar().classList.contains("hidden") &&
    stageBar().querySelector(".stage-name").textContent.includes("EXECUTION"),
    stageBar().textContent.trim());
  check("with the step count and what is expected next",
    /step 1 of 3/.test(stageBar().querySelector(".stage-step").textContent) &&
    stageBar().querySelector(".stage-next").textContent.includes("Work the plan"),
    stageBar().textContent.trim());

  // ---- pause: at this stage, as it would be at any other ----
  promptAnswer = "waiting for the design review";
  click12(Array.from(q12("#memory-body .task-row").querySelectorAll("button"))
    .find((b) => b.textContent.trim() === "Pause"));
  await waitFor(() => taskFile().paused === true);
  await waitFor(() => blocked("Planning"));
  check("a task can be paused where it stands, stage unchanged",
    taskFile().paused === true && taskFile().phase === "execution" &&
    taskFile().note === "waiting for the design review",
    JSON.stringify({ p: taskFile().phase, n: taskFile().note }));
  check("the strip says so too, where the typing happens",
    stageBar().classList.contains("paused") &&
    stageBar().querySelector(".stage-name").textContent.includes("PAUSED"),
    stageBar().textContent.trim());
  check("and every stage is out of reach while it is, for the same reason",
    pills().filter((p) => !p.classList.contains("current"))
      .every((p) => p.classList.contains("blocked")) &&
    pill("Planning").title.includes("paused"),
    pill("Planning").title);
  plantSuggestion("validation", "the build is finished");
  await reopenMemory();
  check("with nothing on the machine to press - not a menu, not even an offer",
    !moveMenu() && !suggest() &&
    q12("#memory-body .phase-paused").textContent.includes("until it is resumed"),
    q12("#memory-body .phase-paused").textContent.trim());
  await fetch(`${BASE}/api/tasks/${taskId}/suggestion`, { method: "DELETE" });
  const pausedMove = await postApi(`/api/tasks/${taskId}/phase`, { to: "validation" });
  check("a pause holds over HTTP too, not only in the panel",
    pausedMove.status === 409, String(pausedMove.status));

  sentBodies.length = 0;
  q12("#chat-inner .prompt-input").value = "anything to do?";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => chatBodies().length === 1);
  await waitFor(() => !q12("#chat-inner .bubble.pending"));
  const pausedRequest = qa12("#debug-panel .debug-entry").slice(-1)
    .map((e) => e.textContent).join("\n");
  check("a paused task tells the model to hold rather than carry on",
    pausedRequest.includes("PAUSED") &&
    pausedRequest.includes("do not carry the work forward"));
  check("but keeps sending what it knows, so resuming needs no catching up",
    pausedRequest.includes("one row per message"));

  click12(Array.from(q12("#memory-body .task-row").querySelectorAll("button"))
    .find((b) => b.textContent.trim() === "Resume"));
  await waitFor(() => taskFile().paused === false);
  await waitFor(() => !blocked("Planning"));
  check("resuming puts the machine back exactly where it was",
    taskFile().paused === false && taskFile().phase === "execution" &&
    !blocked("Planning"));

  // ---- and all of it survives a reload, which is the day's other half ----
  const domPause = loadPage();
  const docPause = domPause.window.document;
  await waitFor(() => docPause.querySelector("#chat-inner .chat-body"));
  docPause.querySelector("#memory-btn")
    .dispatchEvent(new domPause.window.MouseEvent("click", { bubbles: true }));
  await waitFor(() => docPause.querySelectorAll("#memory-body .phase-pill").length === 4);
  const reloadedPills = Array.from(docPause.querySelectorAll("#memory-body .phase-pill"));
  check("a reloaded page comes back at the stage the work stopped at",
    reloadedPills.find((p) => p.classList.contains("current")).textContent.trim() ===
      "Execution",
    reloadedPills.find((p) => p.classList.contains("current")).textContent.trim());
  check("with the planning boxes it ticked a week ago still ticked",
    (taskFile().steps.planning || []).length === 2,
    JSON.stringify(taskFile().steps));

  // ---- going back unticks what going back means you have not done ----
  click12(stepBoxes()[0]);
  await waitFor(() => (taskFile().steps.execution || []).includes("work"));
  await acceptMove("planning", "the plan turned out to be wrong");
  await waitFor(() => pill("Planning").classList.contains("current"));
  check("re-planning clears planning's required boxes, so the gate means something again",
    !(taskFile().steps.planning || []).includes("plan") &&
    why("Execution").includes("Not yet") &&
    why("Execution").includes("Goal and definition of done"),
    JSON.stringify(taskFile().steps) + " :: " + why("Execution"));
  check("but what was merely written down is left written down",
    (taskFile().steps.execution || []).includes("work"),
    JSON.stringify(taskFile().steps));

  // ---- to the end, and what that does to the working layer ----
  for (const to of ["execution", "validation", "done"]) {
    const current = taskFile().phase;
    const required = { planning: ["goal", "plan"], execution: ["work"],
      validation: ["criteria", "verdict"] }[current] || [];
    for (const key of required) {
      await postApi(`/api/tasks/${taskId}/steps/${key}`, { done: true });
    }
    await postApi(`/api/tasks/${taskId}/phase`, { to: to });
  }
  check("driven to the end, one legal move at a time",
    taskFile().phase === "done", taskFile().phase);
  check("finishing keeps every item it learned",
    taskFile().items.length === 2, String(taskFile().items.length));

  sentBodies.length = 0;
  q12("#chat-inner .prompt-input").value = "and the schema again?";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => chatBodies().length === 1);
  await waitFor(() => !q12("#chat-inner .bubble.pending"));
  // The last entry only: the ones before it are turns made while the task was
  // still running, and they quite correctly carry the blocks this checks for.
  const closedRequest = qa12("#debug-panel .debug-entry").slice(-1).map((e) => e.textContent).join("\n");
  check("a finished task stops being sent",
    !closedRequest.includes("What has been established about the task"));
  check("and so does its stage - a finished task has no current stage either",
    !closedRequest.includes("Where this task has got to"));
  check("while the long-term layer carries on regardless",
    closedRequest.includes("What you know about this user"));

  // ---- and all of it survives the page ----
  const dom13 = loadPage();
  const doc13 = dom13.window.document;
  await waitFor(() => doc13.querySelector("#chat-inner .chat-body"));
  doc13.querySelector("#memory-btn")
    .dispatchEvent(new dom13.window.MouseEvent("click", { bubbles: true }));
  await waitFor(() => doc13.querySelectorAll("#memory-body .mem-value").length > 0);
  const reloadedKeys = Array.from(doc13.querySelectorAll("#memory-body .mem-value"))
    .map((k) => k.textContent.trim());
  check("a reloaded page draws the layers back out of the store",
    reloadedKeys.some((k) => k.includes("answer in Russian")),
    reloadedKeys.join("  |  "));
  // Waited for rather than read at once: the layers can be drawn from the
  // chat's own record before the task list has arrived, so reading the menu
  // the moment the first item shows up was a race the suite sometimes lost.
  const taskOptions = () => Array.from(doc13.querySelectorAll("#memory-body .task-row option"))
    .map((o) => o.textContent.trim());
  await waitFor(() => taskOptions().some((t) => t.includes("Done")), 5000);
  const reloadedTasks = taskOptions();
  check("and knows which stage the task finished in",
    reloadedTasks.some((t) => t.includes("Done")), reloadedTasks.join("  |  "));

  // ---- reopening, which is the one move no offer can ever make ----
  //
  // A finished task is not sent and its answers are not read, so the watcher
  // never runs on one: with nothing to raise an offer, a `done` task with no
  // control on it would be a dead end, and the `done -> execution` edge - the
  // one that exists because work comes back - would be unreachable from the
  // app that draws it. So it has a button, and it is a move backwards: it
  // goes through `check` like every other, and ticks nothing.
  await reopenMemory();
  await waitFor(() => q12("#memory-body .phase-reopen button"));
  const reopenBtn = () => q12("#memory-body .phase-reopen button");
  check("a finished task offers the one edge out of the end",
    reopenBtn().textContent.includes("Reopen in execution"),
    reopenBtn().textContent.trim());
  click12(reopenBtn());
  await waitFor(() => taskFile().phase === "execution");
  check("reopening moves it back through the machine, and into the log",
    taskFile().phase === "execution" &&
    taskFile().log.slice(-1)[0].from === "done" &&
    taskFile().log.slice(-1)[0].to === "execution",
    JSON.stringify(taskFile().log.slice(-1)[0]));
  check("and it ticks nothing on the way - going back never confirms anything",
    (taskFile().steps.validation || []).length === 2,
    JSON.stringify(taskFile().steps));

  // Back to done for the rest of the act, one legal move at a time.
  await postApi(`/api/tasks/${taskId}/phase`, { to: "validation", confirm: true });
  await postApi(`/api/tasks/${taskId}/phase`, { to: "done", confirm: true });
  await reopenMemory();
  await waitFor(() => taskFile().phase === "done");

  // ---- a finished task can be removed, not only reopened ----
  const deleteBtn = () => Array.from(q12("#memory-body .task-row").querySelectorAll("button"))
    .find((b) => /Delete|Click again/.test(b.textContent));
  check("a finished task offers a way out of the list, not only a reopen",
    !!deleteBtn(), deleteBtn() && deleteBtn().textContent);
  click12(deleteBtn());
  check("one click only arms it - losing everything a task learned takes two",
    storedTasks().length === 1 && deleteBtn().textContent.includes("Click again"),
    `${storedTasks().length} task(s), button says "${deleteBtn().textContent}"`);
  click12(deleteBtn());
  await waitFor(() => storedTasks().length === 0);
  check("the second click forgets the task, its stage and its memory",
    storedTasks().length === 0, `${storedTasks().length} task(s)`);
  await waitFor(() => q12("#chat-inner .stage-bar").classList.contains("hidden"));
  check("and the strip goes with it - no task, no stage",
    q12("#chat-inner .stage-bar").classList.contains("hidden"));
  await waitFor(() => !q12("#memory-body .phase-pill"));
  check("and the panel falls back to a chat that is in no task at all",
    !q12("#memory-body .phase-pill") &&
    q12("#memory-body .task-closed-note").textContent.includes("not part of a task"),
    q12("#memory-body .task-closed-note").textContent);

  // ------------------------------------------------------------------
  // Day 12: personalisation. Three fields the user writes - how they want
  // answers written, the choices that hold whatever the topic, and who they
  // are - joined onto the developer's system prompt under a label naming
  // their author, and written into no transcript. Most of these checks are
  // therefore of the form "the request changed and the stored conversation
  // did not".
  // ------------------------------------------------------------------
  const openPersonality12 = () => {
    if (q12("#personality-popover").classList.contains("hidden")) {
      click12(q12("#personality-btn"));
    }
  };
  const newChat12 = async () => {
    click12(q12("#chats-btn"));
    click12(q12("#new-chat-btn"));
    await waitFor(() => q12("#chat-inner .msg-row") === null);
  };

  check("the personality button is a sibling of the memory one, not a section in it",
    !!q12("#topbar #personality-btn") && !!q12("#personality-popover"));
  openPersonality12();
  await waitFor(() => qa12("#personality-body .prof-name").length > 1);
  check("nothing is switched on to begin with - \"before\" is half the demonstration",
    q12("#personality-chip").textContent.trim() === "off" &&
    storedPersonality().active === null);
  check("off is a row you can choose, not the absence of a choice",
    qa12("#personality-body .prof-name")[0].textContent.includes("no personality"));
  // Two seeds, and the pair is the argument: one shows the shape of all three
  // fields and is meant to be deleted, the other fills only `style` - the one
  // field that is not about a particular person - and is meant to be used.
  const seeded = storedPersonality().profiles;
  check("it ships one profile to read and one to use",
    seeded.length === 2 && seeded.map((p) => p.id).join(",") === "example,rational",
    seeded.map((p) => p.id).join(", "));
  check("and the usable one is style only, because style is the field that is not about you",
    Object.keys(seeded[1].fields).join(",") === "style" &&
    seeded[1].fields.style.includes("what the question leaves out"),
    Object.keys(seeded[1].fields).join(", "));
  click12(q12("#personality-btn"));

  // ---- a new conversation is where it asks ----
  await newChat12();
  await waitFor(() => q12("#chat-inner .pers-invite:not(.hidden)"));
  const invite = q12("#chat-inner .pers-invite");
  check("a new chat offers to set personalisation up",
    !invite.classList.contains("hidden") &&
    invite.querySelector(".pers-invite-title").textContent.includes("who you are"),
    invite.querySelector(".pers-invite-title").textContent);
  check("and offers the profiles already saved, one click each",
    Array.from(invite.querySelectorAll(".ghost-btn")).some((b) => b.textContent.trim() === "Example"));

  // ---- a request under no personality ----
  sentBodies.length = 0;
  q12("#chat-inner .prompt-input").value = "what is a good name for this?";
  click12(q12("#chat-inner .send-btn"));
  await waitFor(() => chatBodies().length === 1);
  await waitFor(() => !q12("#chat-inner .bubble.pending"));
  const plainRequest = qa12("#debug-panel .debug-entry").slice(-1)
    .map((e) => e.textContent).join("\n");
  check("with nothing set up, no personalisation rides along",
    !plainRequest.includes("Personalisation, written by the user"));
  check("and the invitation gets out of the way the moment anything is said",
    q12("#chat-inner .pers-invite").classList.contains("hidden"));

  // ---- switching one on, from the invitation itself ----
  await newChat12();
  await waitFor(() => q12("#chat-inner .pers-invite:not(.hidden)"));
  const useExample = Array.from(q12("#chat-inner .pers-invite").querySelectorAll(".ghost-btn"))
    .find((b) => b.textContent.trim() === "Example");
  click12(useExample);
  await waitFor(() => storedPersonality().active === "example");
  check("picking one in the invitation is the whole of switching it on",
    storedPersonality().active === "example");
  await waitFor(() => q12("#personality-chip").textContent.trim() === "Example");
  check("and the button says which, without opening anything",
    q12("#personality-chip").classList.contains("on"),
    q12("#personality-chip").textContent.trim());
  await waitFor(() => q12("#chat-inner .pers-invite.compact"));
  check("the invitation stops asking once it has an answer",
    q12("#chat-inner .pers-invite.compact").textContent.includes("Example"));

  // Nothing about that click touched a conversation: a profile is applied at
  // request time, so there is nothing in any transcript for it to have
  // written. This is the day's whole claim, checked against the files.
  const storedFiles = fs.readdirSync(STORE_DIR).filter((n) => n.endsWith(".json"));
  const anyRecordMentions = storedFiles.some((name) =>
    fs.readFileSync(path.join(STORE_DIR, name), "utf8").includes("voice assistant"));
  check("and no conversation record learned anything about it",
    !anyRecordMentions, storedFiles.length + " records");

  // ---- where it lands in the request ----
  // Read straight off the API rather than out of the debug panel: the claim
  // is about the *shape* of `messages`, and counting roles in pretty-printed
  // JSON on a page would be asserting on a rendering of the answer.
  const probe = await (await fetch(BASE + "/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: "probe", conversation_id: "probe-12" }),
  })).json();
  const probeMessages = probe.debug.request.messages;
  const carriers = probeMessages
    .map((m, i) => ({ i: i, m: m }))
    .filter((e) => (e.m.content || "").includes("Personalisation, written by the user"));
  check("the personalisation rides as its own labelled system message",
    carriers.length === 1 && carriers[0].m.role === "system" &&
    carriers[0].i !== 0,
    probeMessages.map((m) => m.role).join(", "));
  // Placement is the one thing about this block that is arguable, so it is
  // the one thing worth asserting: the developer's line stays at the top, and
  // the profile goes *after* everything the agent remembers, where a
  // declaration outranks an inference.
  check("the developer's line is still first, and is not what carries it",
    probeMessages[0].content.includes("You are a helpful assistant") &&
    !probeMessages[0].content.includes("Personalisation, written by the user"));
  check("and it sits after what the agent knows, where a declaration beats an inference",
    carriers[0].i > probeMessages
      .findIndex((m) => (m.content || "").includes("What you know about this user")));
  const sentBlock = carriers[0].m.content;
  check("all three fields are in it, each under a label of its own",
    sentBlock.includes("How they want answers written") &&
    sentBlock.includes("Standing preferences") &&
    sentBlock.includes("Who they are and what they are working on"));
  check("including the one the agent could never have worked out",
    sentBlock.includes("team of 3"));

  // ---- the editor ----
  openPersonality12();
  await waitFor(() => qa12("#personality-body .prof-name").length > 1);
  const exampleRow = qa12("#personality-body .prof-entry").find((row) => {
    const name = row.querySelector(".prof-name");
    return name && name.textContent.trim() === "Example";
  });
  check("every profile can be edited from its own row", !!exampleRow);
  check("and says which of the three it has anything in",
    Array.from(exampleRow.querySelectorAll(".prof-mark")).length === 3 &&
    Array.from(exampleRow.querySelectorAll(".prof-mark.on")).length === 3);
  click12(Array.from(exampleRow.querySelectorAll(".prof-actions button"))
    .find((b) => b.textContent.trim() === "Edit"));
  await waitFor(() => q12("#personality-body .prof-form"));
  const labels = qa12("#personality-body .prof-form .prof-field label")
    .map((l) => l.textContent.trim());
  check("the form is three named fields, not one free-text box",
    qa12("#personality-body .prof-form textarea").length === 3 &&
    labels.join(",") === "Name,Style,Preferences,Context", labels.join(", "));

  qa12("#personality-body .prof-form textarea")[2].value = "staff engineer\nteam of 9";
  click12(Array.from(q12("#personality-body .prof-form-actions").querySelectorAll("button"))
    .find((b) => b.textContent.trim() === "Save"));
  await waitFor(() => {
    const found = storedPersonality().profiles.find((p) => p.id === "example");
    return found && (found.fields.context || "").includes("team of 9");
  });
  check("an edit is saved against the same id, so it stays the active one",
    storedPersonality().active === "example");

  // ---- a profile of your own ----
  click12(Array.from(q12("#personality-body .prof-foot").querySelectorAll("button"))
    .find((b) => b.textContent.includes("New profile")));
  await waitFor(() => q12("#personality-body .prof-form"));
  q12("#personality-body .prof-form input[type=text]").value = "Mine";
  qa12("#personality-body .prof-form textarea")[1].value = "only free APIs";
  click12(Array.from(q12("#personality-body .prof-form-actions").querySelectorAll("button"))
    .find((b) => b.textContent.trim() === "Create"));
  await waitFor(() => storedPersonality().profiles.some((p) => p.id === "mine"));
  check("a profile of your own is a name and the same three fields",
    storedPersonality().profiles.find((p) => p.id === "mine").fields.preferences
      === "only free APIs");
  check("creating one does not switch to it - those are two decisions",
    storedPersonality().active === "example", storedPersonality().active);

  // ---- the shape this day started with still reads ----
  const legacy = await (await fetch(BASE + "/api/personality", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: "Legacy",
      fields: {
        tone: "blunt",
        format: "bullet points",
        language: "Russian",
        constraints: "restate the question",
      },
    }),
  })).json();
  const folded = legacy.profiles.find((p) => p.id === "legacy");
  // The old `constraints` box held things never to do, and said so only in
  // its label. Folded flat, "restate the question" would become an
  // instruction to do it - so the prohibition moves with the line.
  check("and the old list of prohibitions is not inverted on the way",
    !!folded && folded.fields.style.endsWith("never restate the question"),
    folded && folded.fields.style);
  check("a profile written under the first shape of this day folds into style",
    !!folded && folded.fields.style.startsWith("Russian\nblunt\nbullet points") &&
    !folded.fields.tone, JSON.stringify(folded && folded.fields));

  // ---- and it survives the page ----
  const dom14 = loadPage();
  const doc14 = dom14.window.document;
  await waitFor(() => doc14.querySelector("#chat-inner .chat-body"));
  await waitFor(() => doc14.querySelector("#personality-chip").textContent.trim() === "Example");
  check("a reloaded page comes back under the same personality",
    doc14.querySelector("#personality-chip").textContent.trim() === "Example");

  // ---- deleting the active one switches personalisation off ----
  doc14.querySelector("#personality-btn")
    .dispatchEvent(new dom14.window.MouseEvent("click", { bubbles: true }));
  await waitFor(() => doc14.querySelectorAll("#personality-body .prof-entry").length > 1);
  const liveExample = Array.from(doc14.querySelectorAll("#personality-body .prof-entry"))
    .find((row) => {
      const name = row.querySelector(".prof-name");
      return name && name.textContent.trim() === "Example";
    });
  Array.from(liveExample.querySelectorAll(".prof-actions button"))
    .find((b) => b.textContent.trim() === "Delete")
    .dispatchEvent(new dom14.window.MouseEvent("click", { bubbles: true }));
  await waitFor(() => !storedPersonality().profiles.some((p) => p.id === "example"));
  check("deleting the active profile leaves personalisation off, not somebody else's",
    storedPersonality().active === null, JSON.stringify(storedPersonality().active));
  await waitFor(() => doc14.querySelector("#personality-chip").textContent.trim() === "off");
  check("and the button says so",
    !doc14.querySelector("#personality-chip").classList.contains("on"));

  // ------------------------------------------------------------------ day 14
  //
  // Invariants, and the act divides cleanly in two by what a dummy API key
  // allows. The panel, the store, the numbering and the placement of the
  // block in a real request are all checkable, because none of them needs a
  // model. The watcher is not, because it is a model call and every model
  // call here is a 401 - so what stands in for it is the half of the checking
  // that was built to need nothing: `POST /api/invariants/scan`.
  //
  // That is not a workaround for the test, it is the reason the markers
  // exist. A day whose whole claim is "the rule is enforced, not merely
  // stated" cannot rest entirely on a call the suite is unable to make.

  const domInv = loadPage();
  const docInv = domInv.window.document;
  const qInv = (sel) => docInv.querySelector(sel);
  const qaInv = (sel) => Array.from(docInv.querySelectorAll(sel));
  const clickInv = (el) =>
    el.dispatchEvent(new domInv.window.MouseEvent("click", { bubbles: true }));
  await waitFor(() => docInv.querySelector("#chat-inner .chat-body"));

  check("the invariants button is a sibling of the personality one, not a section in it",
    !!qInv("#topbar #invariants-btn") && !!qInv("#invariants-popover")
      && !qInv("#personality-popover #invariants-btn"));

  // ---- nothing is in force to begin with ----
  check("the shipped rules are written down but not switched on - \"before\" is half of it",
    storedInvariants().enabled === false && storedInvariants().rules.length === 2,
    JSON.stringify({ on: storedInvariants().enabled, n: storedInvariants().rules.length }));
  check("and the button says off",
    qInv("#invariants-chip").textContent.trim() === "off");

  const openInv = () => {
    if (qInv("#invariants-popover").classList.contains("hidden")) clickInv(qInv("#invariants-btn"));
  };
  openInv();
  await waitFor(() => qaInv("#invariants-body .inv-row").length === 2);

  check("a rule that is not in force is drawn without a number, because it has none",
    qaInv("#invariants-body .inv-num").every((n) => n.textContent.trim() === "—"),
    qaInv("#invariants-body .inv-num").map((n) => n.textContent.trim()).join(" "));
  check("and the block that would be sent is empty",
    qInv("#invariants-body .inv-block").textContent.startsWith("Nothing"));

  // ---- switching the set on ----
  qInv("#invariants-enabled").checked = true;
  qInv("#invariants-enabled").dispatchEvent(new domInv.window.Event("change", { bubbles: true }));
  await waitFor(() => storedInvariants().enabled === true);
  await waitFor(() => qInv("#invariants-chip").textContent.trim() === "2 in force");
  check("switching the set on numbers the rules and fills the block",
    qaInv("#invariants-body .inv-num").map((n) => n.textContent.trim()).join(" ") === "1. 2."
      && qInv("#invariants-body .inv-block").textContent.includes("1. Storage is JSON files"),
    qaInv("#invariants-body .inv-num").map((n) => n.textContent.trim()).join(" "));

  // ---- writing one ----
  clickInv(qaInv("#invariants-body .inv-foot .ghost-btn")[0]);
  await waitFor(() => !!qInv("#invariants-body .inv-form"));
  const invForm = qInv("#invariants-body .inv-form");
  invForm.querySelector("select").value = "business";
  invForm.querySelector("textarea").value = "Nothing the user writes leaves their machine.";
  invForm.querySelectorAll("input[type=text]")[0].value = "it is the one promise the product makes";
  invForm.querySelectorAll("input[type=text]")[1].value = "s3, telemetry, analytics";
  clickInv(Array.from(invForm.querySelectorAll("button"))
    .find((b) => b.textContent.trim() === "Create"));
  await waitFor(() => storedInvariants().rules.length === 3);

  const written = storedInvariants().rules[2];
  check("a rule written in the panel is on disk with its kind, its reason and its markers",
    written.kind === "business" && written.enabled === true
      && written.because.startsWith("it is the one promise")
      && written.markers.join(",") === "s3,telemetry,analytics",
    JSON.stringify(written.markers));
  check("a new invariant is in force the moment it is written, unlike a new profile",
    qInv("#invariants-chip").textContent.trim() === "3 in force");

  // ---- editing keeps the id, because the id is what the numbering rests on ----
  const invId = written.id;
  const invCreated = written.created_at;
  const bizRow = qaInv("#invariants-body .inv-row")[2];
  clickInv(Array.from(bizRow.querySelectorAll(".inv-actions button"))
    .find((b) => b.textContent.trim() === "Edit"));
  await waitFor(() => !!qInv("#invariants-body .inv-form"));
  const editForm = qInv("#invariants-body .inv-form");
  editForm.querySelector("textarea").value = "Nothing the user writes leaves their machine, ever.";
  clickInv(Array.from(editForm.querySelectorAll("button"))
    .find((b) => b.textContent.trim() === "Save"));
  await waitFor(() => storedInvariants().rules[2].text.endsWith("ever."));
  check("editing a rule keeps its id and its place, so earlier refusals still cite the right number",
    storedInvariants().rules[2].id === invId
      && storedInvariants().rules[2].created_at === invCreated,
    storedInvariants().rules[2].id);

  // ---- switching one off renumbers the rest ----
  const firstToggle = qaInv("#invariants-body .inv-row")[0].querySelector("input[type=checkbox]");
  firstToggle.checked = false;
  firstToggle.dispatchEvent(new domInv.window.Event("change", { bubbles: true }));
  await waitFor(() => storedInvariants().rules[0].enabled === false);
  await waitFor(() => qInv("#invariants-chip").textContent.trim() === "2 in force");
  check("switching one rule off renumbers the others rather than leaving a gap",
    qaInv("#invariants-body .inv-num").map((n) => n.textContent.trim()).join(" ") === "— 1. 2.",
    qaInv("#invariants-body .inv-num").map((n) => n.textContent.trim()).join(" "));
  check("and it is still on file - off is not deleted",
    storedInvariants().rules.length === 3);
  check("the block it would send has dropped it too",
    !qInv("#invariants-body .inv-block").textContent.includes("no migrations"));

  firstToggle.checked = true;
  firstToggle.dispatchEvent(new domInv.window.Event("change", { bubbles: true }));
  await waitFor(() => storedInvariants().rules[0].enabled === true);

  // ---- the markers, which is the only checking a dummy key can reach ----
  const hit = await (await postApi("/api/invariants/scan",
    { text: "I would put the whole thing in Postgres and be done with it" })).json();
  check("the deterministic half catches a forbidden word with no model at all",
    hit.hits.length === 1 && hit.hits[0].marker === "postgres" && hit.hits[0].number === 1,
    JSON.stringify(hit.hits));
  const clean = await (await postApi("/api/invariants/scan",
    { text: "the format of that file is fine, and the platform is not the issue" })).json();
  check("and does not fire on a word that merely contains a marker",
    clean.hits.length === 0, JSON.stringify(clean.hits));

  // ---- the placement in a real request: the day's one arguable decision ----
  await postApi("/api/personality", {
    name: "Inv", fields: { style: "in Russian", preferences: "free APIs only" },
  });
  await postApi("/api/personality/activate", { profile_id: "inv" });
  await fetch(BASE + "/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: "where should this be stored?", conversation_id: "single" }),
  });
  const invBody = await (await fetch(BASE + "/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: "where should this be stored?", conversation_id: "single" }),
  })).json();
  const invMsgs = invBody.debug.request.messages;
  const invIndex = invMsgs.findIndex((m) => m.content.startsWith("--- Invariants"));
  const persIndex = invMsgs.findIndex((m) => m.content.startsWith("--- Personalisation"));
  check("the invariants ride as their own labelled system message",
    invIndex > -1 && invMsgs[invIndex].role === "system",
    invMsgs.map((m) => m.role).join(", "));
  check("after personalisation, because a preference must not be able to outrank a rule",
    persIndex > -1 && invIndex === persIndex + 1,
    `personalisation ${persIndex}, invariants ${invIndex}`);
  check("it is the last system message, sitting closest to the question",
    invMsgs.slice(invIndex + 1).every((m) => m.role !== "system"),
    invMsgs.map((m) => m.role).join(", "));
  check("the block carries the rules, their reasons and the instruction to refuse",
    invMsgs[invIndex].content.includes("1. Storage is JSON files")
      && invMsgs[invIndex].content.includes("Why: the app has to run from a clone")
      && invMsgs[invIndex].content.includes("do not do a smaller version of it"));
  check("and says that naming a rule in order to refuse is not breaking it",
    invMsgs[invIndex].content.includes("Naming a rule in order to refuse"));

  // Nothing was checked, because the turn failed - a 401 is not evidence
  // about anybody's architecture.
  check("a failed turn is not read against the rules",
    invBody.debug.invariants === null && invBody.violations === null,
    JSON.stringify(invBody.violations));

  // ---- none of it is written into a transcript ----
  const invStored = JSON.parse(fs.readFileSync(path.join(STORE_DIR, "single.json"), "utf8"));
  check("not one message mentions a rule - they are applied to requests and stored nowhere",
    !JSON.stringify(invStored.messages).includes("no migrations"));

  // ---- off is a state, not a missing value ----
  await postApi("/api/invariants/enabled", { enabled: false });
  const offBody = await (await fetch(BASE + "/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: "and now?", conversation_id: "single" }),
  })).json();
  check("with the set switched off no invariants message is sent at all",
    !offBody.debug.request.messages.some((m) => m.content.startsWith("--- Invariants")),
    offBody.debug.request.messages.map((m) => m.role).join(", "));
  check("and the rules are all still on file",
    storedInvariants().rules.length === 3 && storedInvariants().enabled === false);
  await postApi("/api/invariants/enabled", { enabled: true });

  // ---- the check can be declined without the rules going away ----
  const quietBody = await (await fetch(BASE + "/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: "still here?", conversation_id: "single",
      settings: { invariant_check: false },
    }),
  })).json();
  check("with the checking off the rules still ride on the request - it demotes, it does not relax",
    quietBody.debug.request.messages.some((m) => m.content.startsWith("--- Invariants"))
      && quietBody.debug.invariants === null);

  // ---- the cap ----
  for (let i = 0; i < 12; i += 1) {
    await postApi("/api/invariants", { kind: "decision", text: `filler rule number ${i}` });
  }
  const capped = await (await postApi("/api/invariants",
    { kind: "decision", text: "one rule too many" })).json();
  check("the twelfth is the last - a set nobody can hold in mind is not a stricter agent",
    storedInvariants().rules.length === 12 && String(capped.detail || "").includes("No room"),
    JSON.stringify(capped.detail));

  // ---- and it all comes back on a reload ----
  const domInv2 = loadPage();
  const docInv2 = domInv2.window.document;
  await waitFor(() => docInv2.querySelector("#chat-inner .chat-body"));
  await waitFor(() => docInv2.querySelector("#invariants-chip").textContent.trim() !== "off");
  check("a reloaded page comes back with the same rules in force",
    docInv2.querySelector("#invariants-chip").textContent.trim() === "12 in force",
    docInv2.querySelector("#invariants-chip").textContent.trim());

  // ------------------------------------------------------------------
  // Day 16: MCP - a catalogue of tools, and where it is drawn
  // ------------------------------------------------------------------

  check("the MCP button is a sibling of the invariants one, not a section in it",
    Boolean(docInv2.querySelector("#mcp-anchor")) &&
    docInv2.querySelector("#invariants-anchor #mcp-anchor") === null);

  const seededMcp = storedMcp();
  check("one server ships connected - somebody else's, switched on",
    seededMcp.servers.map((s) => s.id).join(",") === "deepwiki"
      && seededMcp.servers.every((s) => s.enabled === true),
    JSON.stringify(seededMcp.servers.map((s) => s.id)));
  check("and they ship with no catalogue - connecting is not contacting",
    seededMcp.servers.every((s) => Object.keys(s.catalogue).length === 0));

  // ---- nothing that refuses a URL may reach the network first ----
  const mcpDupe = await (await postApi("/api/mcp",
    { label: "again", url: "https://mcp.deepwiki.com/mcp" })).json();
  check("the same server cannot be connected twice",
    String(mcpDupe.detail || "").includes("already connected"), JSON.stringify(mcpDupe.detail));
  const mcpScheme = await (await postApi("/api/mcp", { label: "nope", url: "ftp://example.com" })).json();
  check("a URL that is not http(s) is refused before anything is fetched",
    String(mcpScheme.detail || "").includes("http://"), JSON.stringify(mcpScheme.detail));

  // ---- the render path, off a catalogue this file wrote ----
  //
  // Two servers, and they are unalike on purpose: one answered with tools and
  // a session over an older protocol, the other did not answer at all. Both
  // states have to be drawable, and the failed one has to keep its place -
  // a server that is down is a row, not a missing row.
  writeCatalogues([
    {
      id: "fixture",
      label: "Fixture",
      url: "https://fixture.invalid/mcp",
      enabled: true,
      catalogue: {
        ok: true,
        server_name: "FixtureServer",
        server_version: "9.9.9",
        protocol: "2025-03-26",
        capabilities: ["tools"],
        session: true,
        pages: 1,
        elapsed_ms: 42,
        checked_at: "2026-01-01T00:00:00+00:00",
        error: "",
        steps: [
          { label: "initialize", status: 200, content_type: "application/json", ms: 20, note: "" },
          { label: "tools/list", status: 200, content_type: "text/event-stream", ms: 22, note: "" },
        ],
        tools: [
          {
            name: "ask-thing",
            title: "Ask a thing",
            description: "Answers a question about a thing.",
            input_schema: {
              type: "object",
              properties: { question: { type: "string" }, depth: { type: "number" } },
              required: ["question"],
            },
            arguments: [
              { name: "question", type: "string", required: true },
              { name: "depth", type: "number", required: false },
            ],
            function_name: "fixture__ask-thing",
          },
        ],
      },
    },
    {
      id: "downed",
      label: "Downed",
      url: "https://downed.invalid/mcp",
      enabled: true,
      catalogue: {
        ok: false,
        error: "initialize: could not reach the server",
        protocol: "2025-06-18",
        session: false,
        elapsed_ms: 12,
        checked_at: "2026-01-01T00:00:00+00:00",
        tools: [],
        steps: [{ label: "initialize", status: 0, content_type: "", ms: 12, note: "ConnectError" }],
      },
    },
  ]);

  const domMcp = loadPage();
  const docMcp = domMcp.window.document;
  await waitFor(() => docMcp.querySelector("#chat-inner .chat-body"));
  await waitFor(() => docMcp.querySelector("#mcp-chip").textContent.trim() !== "off");

  check("the chip counts tools rather than servers - the number you cannot see otherwise",
    docMcp.querySelector("#mcp-chip").textContent.trim() === "1 tool",
    docMcp.querySelector("#mcp-chip").textContent.trim());
  check("and goes red while a connected server is down",
    docMcp.querySelector("#mcp-chip").classList.contains("down"));

  docMcp.getElementById("mcp-btn").dispatchEvent(
    new domMcp.window.MouseEvent("click", { bubbles: true }));
  await waitFor(() => docMcp.querySelectorAll("#mcp-body .mcp-server").length === 2);

  const mcpRows = [...docMcp.querySelectorAll("#mcp-body .mcp-server")];
  check("a server that failed keeps its row rather than disappearing",
    mcpRows.length === 2 && mcpRows[1].querySelector(".mcp-dot").classList.contains("down"));
  check("and says what went wrong, not just that something did",
    mcpRows[1].querySelector(".mcp-status.bad").textContent.includes("could not reach"),
    mcpRows[1].querySelector(".mcp-status.bad").textContent.trim());

  const mcpStatus = mcpRows[0].querySelector(".mcp-status").textContent;
  check("the status line reports the connection, not the catalogue",
    mcpStatus.includes("FixtureServer 9.9.9") && mcpStatus.includes("protocol 2025-03-26")
      && mcpStatus.includes("session") && mcpStatus.includes("1 tool"),
    mcpStatus.trim());

  // The one string in this panel that has to be exact: it is what a request
  // would carry as `function.name`, and a prettier version of it would be a
  // name that exists nowhere else.
  check("a tool is listed under its qualified name, not the server's own",
    mcpRows[0].querySelector(".mcp-tool-name").textContent === "fixture__ask-thing",
    mcpRows[0].querySelector(".mcp-tool-name").textContent);
  const mcpArgs = [...mcpRows[0].querySelectorAll(".mcp-arg")].map(
    (a) => a.textContent + (a.classList.contains("required") ? "*" : ""));
  check("with its arguments, required ones marked", mcpArgs.join(" ") === "question: string* depth: number",
    mcpArgs.join(" "));
  check("and the input schema verbatim, because that is the part needing no translation",
    mcpRows[0].querySelector(".mcp-tool .mcp-pre").textContent.includes('"required"'));
  check("the wire trace is on screen - the protocol is the thing being shown",
    [...mcpRows[0].querySelectorAll(".mcp-details > summary")]
      .some((el) => el.textContent.includes("2 requests")));

  // ---- switching one off changes what would be offered, not what is known ----
  const mcpToggled = await (await postApi("/api/mcp/fixture/toggle", { enabled: false })).json();
  check("a server switched off stops contributing tools",
    mcpToggled.tools === 0 && mcpToggled.connected === 0, JSON.stringify(mcpToggled.tools));
  check("but keeps its catalogue - off is not forgotten",
    storedMcp().servers[0].catalogue.tools.length === 1);

  const mcpRemoved = await (await fetch(BASE + "/api/mcp/downed", { method: "DELETE" })).json();
  check("removing one needs no connection closed, because none was held",
    mcpRemoved.servers.length === 1 && mcpRemoved.servers[0].id === "fixture",
    JSON.stringify(mcpRemoved.servers.map((s) => s.id)));

  // ------------------------------------------------------------------
  // Day 17: the catalogue reaches the request, and a call reaches the page
  // ------------------------------------------------------------------
  //
  // Nothing here calls a tool: the API key is a dummy, so the model never
  // gets as far as asking for one. What can be checked without the network is
  // the two ends the day actually added - what a stored catalogue does to an
  // outgoing request, and what a stored call does to the page.

  writeCatalogues([
    {
      id: "docs",
      label: "Docs",
      url: "http://127.0.0.1:8799/mcp",
      enabled: true,
      catalogue: {
        ok: true,
        server_name: "docs-fixture",
        protocol: "2025-06-18",
        tools: [
          {
            name: "search_docs",
            description: "Search the library documentation.",
            input_schema: {
              type: "object",
              properties: { query: { type: "string" }, sort_by: { type: "string" } },
              required: ["query"],
            },
            arguments: [{ name: "query", type: "string", required: true }],
            function_name: "docs__search_docs",
          },
        ],
        checked_at: "2030-01-01T02:00:00+00:00",
      },
    },
  ]);

  const toolTurn = await (await fetch(BASE + "/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: "how do I retry a request",
      conversation_id: "tools-demo",
      settings: { model: "deepseek-v4-flash", system_prompt: "You are a helpful assistant.",
        stop: [], response_format: "text", reasoning_effort: "low", temperature: 1.0 },
    }),
  })).json();
  const offeredTools = ((toolTurn.debug || {}).request || {}).tools || [];
  check("a connected server's tools ride on the request, under their qualified name",
    offeredTools.length === 1 && offeredTools[0].function.name === "docs__search_docs",
    JSON.stringify(offeredTools.map((t) => t.function.name)));
  check("the input schema is the parameters, with no translation in between",
    JSON.stringify(offeredTools[0].function.parameters.required) === '["query"]',
    JSON.stringify(offeredTools[0].function.parameters));
  check("and the count on the reply is what was offered, not what exists",
    toolTurn.toolbox && toolTurn.toolbox.offered === 1, JSON.stringify(toolTurn.toolbox));

  const mcpOff = await (await postApi("/api/mcp/docs/toggle", { enabled: false })).json();
  const noTools = await (await fetch(BASE + "/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: "and now",
      conversation_id: "tools-demo",
      settings: { model: "deepseek-v4-flash", system_prompt: "You are a helpful assistant.",
        stop: [], response_format: "text", reasoning_effort: "low", temperature: 1.0 },
    }),
  })).json();
  check("a server switched off sends no tools at all - the switch is the economy",
    mcpOff.tools === 0 && !((noTools.debug || {}).request || {}).tools,
    JSON.stringify(((noTools.debug || {}).request || {}).tools || null));

  // ---- a turn that did call something, planted, and drawn on reload ----
  //
  // Stored exactly as `core.turn` stores one: the calls on the assistant
  // message they paid for. A reload has to draw the receipts again without
  // anything being called, which is the whole reason they are on the message.
  fs.writeFileSync(path.join(STORE_DIR, "tools-demo.json"), JSON.stringify({
    id: "tools-demo",
    settings: { model: "deepseek-v4-flash", system_prompt: "You are a helpful assistant.",
      stop: [], response_format: "text", reasoning_effort: "low", temperature: 1.0 },
    messages: [
      { role: "user", content: "how do I retry a request", ts: "2032-01-01T00:00:00.000+00:00" },
      { role: "assistant", content: "Wrap the transport in a retry.",
        ts: "2032-01-01T00:00:01.000+00:00",
        tools: [
          {
            id: "call_1", name: "docs__search_docs", ok: true, is_error: false,
            arguments: { query: "retry", sort_by: "relevance" },
            elapsed_ms: 412,
            text: "1. Transports - retries",
            structured: { hits: [{ title: "Transports", section: "Retries" }] },
          },
          {
            id: "call_2", name: "docs__read_page", ok: false, is_error: true,
            arguments: { page: "nowhere" },
            elapsed_ms: 90,
            text: "Error executing tool read_page: no such page.",
            structured: null,
          },
          {
            id: "call_3", name: "docs__search_docs", ok: true, is_error: false, cached: true,
            arguments: { query: "retry", sort_by: "relevance" },
            elapsed_ms: 0,
            text: "1. Transports - retries",
            structured: { hits: [{ title: "Transports", section: "Retries" }] },
          },
        ],
      },
    ],
    created_at: "2032-01-01T00:00:00.000+00:00",
    updated_at: "2032-01-01T00:00:01.000+00:00",
  }));

  const domCards = loadPage({ "deepseek-chat-active": "tools-demo" });
  const docCards = domCards.window.document;
  await waitFor(() => docCards.querySelectorAll(".tool-call").length > 0);

  const receipts = [...docCards.querySelectorAll(".tool-call")];
  check("a stored call draws its receipt again on reload, with nothing called",
    receipts.length === 3 && receipts[0].textContent.includes("docs__search_docs")
      && receipts[0].textContent.includes('sort_by: "relevance"'),
    receipts.map((r) => r.textContent).join(" / "));
  check("a failed call says so, and why",
    receipts[1].classList.contains("bad")
      && receipts[1].textContent.includes("no such page"));
  check("a call answered from an identical one earlier in the turn says so",
    receipts[2].textContent.includes("identical call earlier") && !receipts[0].textContent.includes("identical"));
  check("the block sits after the bubble, not inside it - a result is not something said",
    docCards.querySelector(".bubble .tool-run") === null
      && docCards.querySelector(".tool-run") !== null);

  // ---- days 22-23: RAG - the chat's switch ----
  // A page of its own, so the transcripts the acts above count are left
  // alone. The suite's index is empty, so every RAG answer is refused - which
  // is the behaviour worth checking: it says what is missing, and nothing
  // pretends.
  const dom22 = loadPage();
  const win22 = dom22.window;
  const doc22 = win22.document;
  const q22 = (sel) => doc22.querySelector(sel);
  const qa22 = (sel) => Array.from(doc22.querySelectorAll(sel));
  await waitFor(() => q22("#chat-inner .chat-body"));
  const ragBtn22 = q22("#rag-btn");
  check("the RAG switch is in the top bar and starts off", ragBtn22 && q22("#rag-chip").textContent === "off");
  ragBtn22.click();
  check("switching it on says so, and the tooltip names the reranked cut",
    q22("#rag-chip").textContent === "on" && /document index, reranked: 20 candidates/.test(ragBtn22.title) &&
      /at most 5/.test(ragBtn22.title),
    q22("#rag-chip").textContent + " / " + ragBtn22.title);
  check("and the popover's RAG field follows it, rerank on by default",
    q22("#rag-field .rag-toggle").checked === true && q22("#rag-field .rag-rerank").checked === true &&
      !q22("#rag-field .rag-candidates").disabled && !q22("#rag-field .rag-threshold").disabled);
  sentBodies.length = 0;
  const errorsBefore22 = qa22("#chat-inner .bubble.error").length;
  q22("#chat-inner .prompt-input").value = "what embedding model?";
  q22("#chat-inner .send-btn").click();
  await waitFor(() => qa22("#chat-inner .bubble.error").length > errorsBefore22 && !q22("#chat-inner .bubble.pending"));
  const lastError22 = qa22("#chat-inner .bubble.error").pop();
  check("a RAG message carries the switch in its settings, the second stage's included",
    chatBodies().length === 1 && chatBodies()[0].settings.rag === true && chatBodies()[0].settings.rag_k === 5 &&
      chatBodies()[0].settings.rag_rerank === true && chatBodies()[0].settings.rag_candidates === 20 &&
      typeof chatBodies()[0].settings.rag_threshold === "number" && !("rag_index" in chatBodies()[0].settings),
    JSON.stringify(chatBodies()[0] && chatBodies()[0].settings));
  check("and with no index it is refused, not answered without the documents",
    /no 'structural' index/.test(lastError22.textContent), lastError22.textContent);
  q22("#rag-field .rag-rerank").click();
  check("rerank off: the candidates and threshold are greyed and the tooltip is day 22's top k",
    q22("#rag-field .rag-candidates").disabled && q22("#rag-field .rag-threshold").disabled &&
      /document index, top 5/.test(ragBtn22.title), ragBtn22.title);
  const rerankOff22 = () => sentBodies.filter((b) => b.url.startsWith("/api/conversations/") && b.settings)
    .some((b) => b.settings.rag_rerank === false);
  check("and that is saved with the chat", await waitFor(rerankOff22, 5000));
  q22("#rag-field .rag-toggle").click();
  check("the popover switch turns it off again, chip included",
    q22("#rag-chip").textContent === "off" && !ragBtn22.classList.contains("on"));
  const metaOff22 = () => sentBodies.filter((b) => b.url.startsWith("/api/conversations/") && b.settings)
    .some((b) => b.settings.rag === false);
  check("and the setting is saved with the chat", await waitFor(metaOff22, 5000));

  // Day 23: every day's app is served from the same localhost:8000/static/...,
  // so a browser has to ask before reusing a file - or it runs yesterday's script.
  const staticRes23 = await fetch(BASE + "/static/indexing.js");
  check("static files go out with Cache-Control: no-cache",
    staticRes23.headers.get("cache-control") === "no-cache", String(staticRes23.headers.get("cache-control")));
  const pageRes23 = await fetch(BASE + "/");
  check("and so does the page that names them",
    pageRes23.headers.get("cache-control") === "no-cache", String(pageRes23.headers.get("cache-control")));

  // ---- day 24: answers as Markdown, with their sources, quotes and "I don't know" ----
  // A stored chat, so nothing has to be asked: one grounded answer (Markdown,
  // two citations, a quote found and one that is not, a retry behind it), one
  // "I don't know" from the gate, and an answer that tries to be HTML.
  const zebra24 = "## Zebra\n\nThe zebra section mentions quokka and narwhal and nothing else here.";
  const quoteAt24 = zebra24.indexOf("mentions quokka and narwhal");
  const hit24 = (n, section, text, extra) => Object.assign({
    n, chunk_id: "structural:guide.md#000" + n, source: "guide.md", section, score: 0.61 - n / 100,
    n_words: text.split(/\s+/).length, start: 0, end: text.length, cited: true, preview: text.slice(0, 240),
    text, cos_rank: n, llm: 10 }, extra || {});
  const settings24 = { model: "deepseek-v4-flash", system_prompt: "You are a helpful assistant.",
    stop: [], response_format: "text", reasoning_effort: "low", temperature: 1.0, rag: true };
  const answer24 = "### Итог\n\nВ разделе есть **квокка** и нарвал [1], а настройка описана отдельно [2].\n\n" +
    "- первый пункт\n- второй пункт\n\n| что | где |\n|---|---|\n| квокка | Zebra |";
  fs.writeFileSync(path.join(STORE_DIR, "grounded-demo.json"), JSON.stringify({
    id: "grounded-demo",
    settings: settings24,
    messages: [
      { role: "user", content: "где **квокка**?", ts: "2034-01-01T00:00:00.000+00:00" },
      { role: "assistant", content: answer24, ts: "2034-01-01T00:00:01.000+00:00",
        rag: { index: "structural", k: 5, query: "где квокка?", embed_ms: 12, search_ms: 0.4, words: 40, cited: [1, 2],
          hits: [hit24(1, "Zebra", zebra24), hit24(2, "Setup", "## Setup\n\nRun uv sync to install.")],
          rerank: { method: "llm", model: "deepseek-v4-flash", threshold: 6, k: 5, candidates: 2, kept: 2,
            elapsed_ms: 900, usage: { total_tokens: 800 }, reply: "{}", scored: [] },
          grounding: { status: "answered", answer: answer24, clarification: "",
            sources: [
              { n: 1, chunk_id: "structural:guide.md#0001", source: "guide.md", section: "Zebra", score: 0.6, llm: 10, quoted: true, named: true },
              { n: 2, chunk_id: "structural:guide.md#0002", source: "guide.md", section: "Setup", score: 0.59, llm: 10, quoted: false, named: true }],
            quotes: [
              { n: 1, quote: "mentions quokka and narwhal", found: true,
                spans: [[quoteAt24, quoteAt24 + "mentions quokka and narwhal".length]],
                source: "guide.md", section: "Zebra", chunk_id: "structural:guide.md#0001" },
              { n: 2, quote: "Setup takes forty minutes", found: false, spans: [],
                source: "guide.md", section: "Setup", chunk_id: "structural:guide.md#0002" }],
            problems: ["This quote is not in excerpt [2] - copy it exactly, in the excerpt's language: \"Setup takes forty minutes\""],
            retried: { problems: ["No quotes: every claim needs a fragment copied from its excerpt."], answer: "first" },
            gate: null } } },
      { role: "user", content: "сколько реплик в kubernetes?", ts: "2034-01-01T00:00:02.000+00:00" },
      { role: "assistant", content: "Не знаю: в документах проекта этого нет.\n\nО какой части проекта вопрос?",
        ts: "2034-01-01T00:00:03.000+00:00",
        rag: { index: "structural", k: 5, query: "k8s", embed_ms: 10, search_ms: 0.3, words: 0, cited: [], hits: [],
          rerank: { method: "llm", model: "deepseek-v4-flash", threshold: 6, k: 5, candidates: 20, kept: 0,
            elapsed_ms: 800, usage: { total_tokens: 700 }, reply: "{}", scored: [] },
          grounding: { status: "unknown", answer: "Не знаю: в документах проекта этого нет.",
            clarification: "О какой части проекта вопрос?", sources: [], quotes: [], problems: [],
            gate: { best: 1, threshold: 6, candidates: 20,
              nearest: [{ source: "README.md", section: "Day 18", llm: 1 }] } } } },
      { role: "user", content: "html?", ts: "2034-01-01T00:00:04.000+00:00" },
      { role: "assistant", content: "<img src=x onerror=\"window.__xss24=1\"> [click](javascript:window.__xss24=2)",
        ts: "2034-01-01T00:00:05.000+00:00" },
    ],
    created_at: "2034-01-01T00:00:00.000+00:00",
    updated_at: "2034-01-01T00:00:05.000+00:00",
  }));

  const dom24 = loadPage({ "deepseek-chat-active": "grounded-demo" });
  const win24 = dom24.window;
  const doc24 = win24.document;
  await waitFor(() => doc24.querySelector(".grounding"));
  const bubbles24 = [...doc24.querySelectorAll(".msg-row.assistant .bubble")];
  const grounded24 = bubbles24.find((b) => b.textContent.includes("квокка"));
  check("an answer is drawn as Markdown: heading, bold, list and table, no raw asterisks or pipes",
    grounded24 && grounded24.classList.contains("md") && grounded24.querySelector("h3")
      && grounded24.querySelector("strong").textContent === "квокка"
      && grounded24.querySelectorAll("ul li").length === 2 && grounded24.querySelector("table td")
      && !grounded24.textContent.includes("**") && !grounded24.textContent.includes("|---"),
    grounded24 ? grounded24.innerHTML.slice(0, 160) : "no bubble");
  const user24 = [...doc24.querySelectorAll(".msg-row.user .bubble")].find((b) => b.textContent.includes("квокка"));
  check("what the user typed stays the text it was", user24 && user24.textContent === "где **квокка**?");
  const chips24 = grounded24 ? [...grounded24.querySelectorAll(".cite-chip")] : [];
  check("its [n] citations are chips naming their excerpt",
    chips24.length === 2 && chips24[0].textContent === "1" && chips24[0].title === "guide.md › Zebra",
    chips24.map((c) => c.textContent + ":" + c.title).join(" "));
  const card24 = grounded24 && grounded24.parentNode.nextSibling;
  check("under the answer, one folded line - nothing to read until it is opened",
    card24 && card24.tagName === "DETAILS" && card24.classList.contains("grounding") && !card24.open
      && card24.querySelector("summary").textContent ===
        "Sources · 2 excerpts · 2 quotes · ⚠ 1 not in its excerpt · asked again once",
    card24 ? card24.querySelector("summary").textContent : "no card");
  check("the quotes are not listed under the answer - its [n] are how they are reached",
    !doc24.querySelector(".g-quote")
      && ![...card24.querySelectorAll(".g-label")].some((l) => l.textContent.startsWith("Quotes")));
  card24.open = true;
  check("opened: the sources, with their chunk ids",
    card24.querySelectorAll(".g-source").length === 2
      && card24.querySelector(".g-source").textContent.includes("structural:guide.md#0001")
      && card24.querySelectorAll(".g-source")[1].textContent.includes("no quote found in it"),
    card24.textContent.slice(0, 200));
  check("the retry and what is still wrong are in it, and day 22's block, folded again, as Retrieval",
    card24.querySelector("details.g-note.warn summary").textContent.includes("Asked again once")
      && card24.querySelector(".g-note.bad").textContent.includes("Still wrong after the retry")
      && card24.querySelector(".rag-sources summary b").textContent === "Retrieval"
      && !card24.querySelector(".rag-sources").open);

  chips24[0].click();
  const pop24 = doc24.querySelector(".quote-pop");
  const mark24 = pop24 && pop24.querySelector("mark");
  check("a click on [n] in the answer opens the excerpt with its quote marked in it",
    pop24 && !pop24.hidden && pop24.querySelector(".qp-title").textContent.startsWith("[1] guide.md › Zebra")
      && mark24 && mark24.textContent === "mentions quokka and narwhal"
      && pop24.querySelector(".qp-body").textContent === zebra24
      && pop24.querySelector(".qp-foot").textContent === "1 quote from this excerpt, marked."
      && pop24.querySelector(".qp-missing").hidden,
    pop24 ? pop24.textContent.slice(0, 200) : "no popup");
  doc24.dispatchEvent(new win24.KeyboardEvent("keydown", { key: "Escape" }));
  check("Escape closes it", pop24.hidden);
  check("an excerpt with a quote that is not in it has a red chip",
    chips24[1].classList.contains("warn") && !chips24[0].classList.contains("warn")
      && chips24[1].title.includes("a quote from it is not in it"), chips24[1].className);
  chips24[1].click();
  check("and its popup puts that quote above the text it was supposed to come from",
    !pop24.hidden && pop24.querySelector(".qp-title").textContent.startsWith("[2] guide.md › Setup")
      && !pop24.querySelector(".qp-missing").hidden
      && pop24.querySelector(".qp-missing").textContent.includes("Setup takes forty minutes")
      && pop24.querySelector(".qp-body").textContent.includes("Run uv sync")
      && !pop24.querySelector("mark") && pop24.querySelector(".qp-foot").textContent.includes("No quote"),
    pop24.textContent.slice(0, 200));
  doc24.body.dispatchEvent(new win24.MouseEvent("mousedown", { bubbles: true }));
  check("and a click elsewhere closes it", pop24.hidden);
  card24.querySelector(".g-source").click();
  check("a source in the opened fold opens its excerpt too",
    !pop24.hidden && pop24.querySelector(".qp-title").textContent.startsWith("[1] guide.md › Zebra"));
  doc24.dispatchEvent(new win24.KeyboardEvent("keydown", { key: "Escape" }));

  const unknown24 = bubbles24.find((b) => b.classList.contains("unknown"));
  check("“I don't know” says so at a glance, with the gate's numbers",
    unknown24 && unknown24.querySelector(".unknown-head").textContent.includes("Don’t know")
      && unknown24.querySelector(".unknown-head").textContent.includes("best 1/10, needs 6"),
    unknown24 ? unknown24.textContent : "no unknown bubble");
  check("and its question back stands apart from the refusal",
    unknown24.querySelector(".clarify").textContent.includes("О какой части проекта вопрос?")
      && !unknown24.querySelector(".clarify").previousSibling.textContent.includes("О какой части"));
  const unknownCard24 = unknown24.parentNode.nextSibling;
  check("its fold says there are no sources, and names the nearest sections below the threshold",
    unknownCard24.classList.contains("grounding") && !unknownCard24.open
      && unknownCard24.querySelector("summary").textContent === "Sources \u00b7 none - nothing reached the threshold"
      && unknownCard24.textContent.includes("Nearest, below the threshold of 6")
      && unknownCard24.textContent.includes("README.md › Day 18 (1/10)"),
    unknownCard24.textContent.slice(0, 160));
  const html24 = bubbles24[bubbles24.length - 1];
  check("an answer that tries to be HTML stays text: no element, no handler, no javascript: link",
    html24 && !html24.querySelector("img") && !html24.querySelector("a") && html24.textContent.includes("<img")
      && win24.__xss24 === undefined,
    html24 ? html24.innerHTML : "no bubble");

  // ---- day 25: the task state - the strip, what each message changed, the search it fed ----
  const state25 = { goal: "find the quokka", focus: "the quokka's section",
    clarified: ["the zebra section, not the setup one"], constraints: ["answer briefly"],
    terms: { Q: "quokka" } };
  const zebra25 = "## Zebra\n\nThe zebra section mentions quokka and narwhal.";
  fs.writeFileSync(path.join(STORE_DIR, "state-demo.json"), JSON.stringify({
    id: "state-demo",
    settings: Object.assign({}, settings24, { rag: true }),
    messages: [
      { role: "user", content: "where is the quokka25?", ts: "2035-01-01T00:00:00.000+00:00",
        task_state: [{ section: "goal", op: "+", text: "find the quokka" },
          { section: "constraints", op: "+", text: "answer briefly" }] },
      { role: "assistant", content: "In Zebra [1].", ts: "2035-01-01T00:00:01.000+00:00",
        rag: { index: "structural", k: 5, query: "where is the quokka25?", embed_ms: 1, search_ms: 0.1, words: 9,
          cited: [1], hits: [hit24(1, "Zebra", zebra25)],
          grounding: { status: "answered", answer: "In Zebra [1].", clarification: "", problems: [], gate: null,
            sources: [{ n: 1, chunk_id: "structural:guide.md#0001", source: "guide.md", section: "Zebra",
              score: 0.6, llm: 10, quoted: true, named: true }], quotes: [] } } },
      { role: "user", content: "the zebra one", ts: "2035-01-01T00:00:02.000+00:00",
        task_state: [{ section: "clarified", op: "+", text: "the zebra section, not the setup one" }] },
      { role: "assistant", content: "Yes, Zebra [1].", ts: "2035-01-01T00:00:03.000+00:00",
        rag: { index: "structural", k: 5, query: "the zebra one\nConversation context: the quokka's section; find the quokka",
          embed_ms: 1, search_ms: 0.1, words: 9, cited: [1], hits: [hit24(1, "Zebra", zebra25)],
          grounding: { status: "answered", answer: "Yes, Zebra [1].", clarification: "", problems: [], gate: null,
            sources: [{ n: 1, chunk_id: "structural:guide.md#0001", source: "guide.md", section: "Zebra",
              score: 0.6, llm: 10, quoted: true, named: true }], quotes: [] } } },
    ],
    task_state: { content: state25, updates: 2, model: "deepseek-v4-flash", usage_total: { total_tokens: 640 } },
    created_at: "2035-01-01T00:00:00.000+00:00",
    updated_at: "2035-01-01T00:00:03.000+00:00",
  }));

  const dom25 = loadPage({ "deepseek-chat-active": "state-demo" });
  const doc25 = dom25.window.document;
  const pane25 = () => [...doc25.querySelectorAll(".chat-body")]
    .find((b) => b.textContent.includes("where is the quokka25?"));
  await waitFor(() => pane25() && pane25().querySelector(".task-bar"));
  const body25 = pane25();
  const bar25 = body25.querySelector(".task-bar");
  check("a RAG chat shows its task state strip, the goal and the count on it",
    !bar25.classList.contains("hidden") && bar25.textContent.includes("find the quokka")
      && bar25.textContent.includes("5 items"), bar25.textContent);
  const deltas25 = [...body25.querySelectorAll(".state-delta")];
  check("under each message of yours, what it changed - drawn from the stored record",
    deltas25.length === 2 && deltas25[0].textContent.includes("+ goal: find the quokka")
      && deltas25[0].textContent.includes("+ constraint: answer briefly")
      && deltas25[1].previousElementSibling.textContent.includes("the zebra one"),
    deltas25.map((d) => d.textContent).join(" | "));
  const queries25 = [...body25.querySelectorAll(".g-query")];
  check("the sources say what the search was asked with, when it was more than the question",
    queries25.length === 1 && queries25[0].textContent.includes("the quokka's section; find the quokka"),
    queries25.map((q) => q.textContent).join(" | "));
  bar25.click();
  const rows25 = () => [...body25.querySelectorAll(".task-detail .ts-row")];
  check("opened, every item is listed by section with its ×",
    rows25().length === 5 && rows25().every((r) => r.querySelector(".ts-x"))
      && rows25().map((r) => r.dataset.section).join(",") === "goal,focus,clarified,constraints,terms"
      && body25.querySelector(".task-detail").textContent.includes("640 tokens to maintain"),
    rows25().map((r) => r.dataset.section + ":" + r.textContent).join(" | "));
  const sent25 = sentBodies.length;
  rows25().find((r) => r.dataset.section === "constraints").querySelector(".ts-x").click();
  await waitFor(() => rows25().length === 4);
  const forgot25 = sentBodies.slice(sent25).find((b) => b.url.endsWith("/task-state/forget"));
  check("× takes one item out, on the server, and the strip is redrawn from its reply",
    forgot25 && forgot25.section === "constraints" && forgot25.item === "answer briefly"
      && !rows25().some((r) => r.textContent.includes("answer briefly")) && bar25.textContent.includes("4 items"),
    JSON.stringify(forgot25));

  // ---- day 34: agentic code - the switch, the bar, the Changes window ----
  // A stored chat with agentic code on and one answer that changed two files,
  // stored the way `core.turn` stores one - so nothing has to be asked.
  const diffA34 = "diff --git a/src/util.py b/src/util.py\n--- a/src/util.py\n+++ b/src/util.py\n" +
    "@@ -1,2 +1,3 @@\n def greet(name):\n+    \"\"\"Say hello.\"\"\"\n-    return 'hi'\n+    return 'hello'\n";
  const diffB34 = "diff --git a/docs/usage.md b/docs/usage.md\nnew file mode 100644\n--- /dev/null\n" +
    "+++ b/docs/usage.md\n@@ -0,0 +1 @@\n+# Usage\n";
  fs.writeFileSync(path.join(STORE_DIR, "code-demo.json"), JSON.stringify({
    id: "code-demo",
    settings: { model: "deepseek-v4-flash", system_prompt: "You are a helpful assistant.", stop: [],
      response_format: "text", reasoning_effort: "low", temperature: 1.0, agentic_code: true },
    messages: [
      { role: "user", content: "document greet", ts: "2033-01-01T00:00:00.000+00:00" },
      { role: "assistant", content: "Added a docstring to `src/util.py:1` and wrote `docs/usage.md`.",
        ts: "2033-01-01T00:00:01.000+00:00",
        tools: [{ id: "c1", name: "write_file", ok: true, is_error: false, elapsed_ms: 2,
          arguments: { path: "docs/usage.md", content: "x".repeat(400) + " ... (900 characters)" },
          text: "Created docs/usage.md (1 lines)." }],
        changes: [
          { path: "docs/usage.md", status: "added", added: 1, removed: 0, diff: diffB34 },
          { path: "src/util.py", status: "modified", added: 2, removed: 1, diff: diffA34 },
        ] },
    ],
    created_at: "2033-01-01T00:00:00.000+00:00",
    updated_at: "2033-01-01T00:00:01.000+00:00",
  }));
  const dom34 = loadPage({ "deepseek-chat-active": "code-demo" });
  const win34 = dom34.window;
  const doc34 = win34.document;
  const q34 = (sel) => doc34.querySelector(sel);
  const qa34 = (sel) => Array.from(doc34.querySelectorAll(sel));
  await waitFor(() => q34("#chat-inner .code-changes-btn") && q34("#chat-inner .code-bar b"));
  check("a chat with agentic code on says so in the top bar, and greys RAG",
    q34("#agentic-chip").textContent === "on" && q34("#agentic-btn").classList.contains("on") &&
      q34("#rag-btn").disabled && /search_docs/.test(q34("#rag-btn").title),
    q34("#agentic-chip").textContent + " / " + q34("#rag-btn").title);
  const bar34 = q34("#chat-inner .code-bar");
  const chips34 = Array.from(bar34.querySelectorAll(".code-chip")).map((c) => c.textContent);
  check("the bar above the box names the project and offers the scenarios",
    !bar34.classList.contains("hidden") && bar34.textContent.includes("Agentic code in") &&
      chips34.includes("Find usages") && chips34.includes("Update docs") && chips34.includes("Generate CHANGELOG") &&
      chips34.includes("Check the rules") && chips34.includes("All changes") &&
      /Describe a task/.test(q34("#chat-inner .prompt-input").placeholder),
    chips34.join(" | "));
  Array.from(bar34.querySelectorAll(".code-chip")).find((c) => c.textContent === "Find usages").click();
  const input34 = q34("#chat-inner .prompt-input");
  check("a scenario fills the box with its goal and selects the blank to type over",
    input34.value.startsWith("Find every place") &&
      input34.value.slice(input34.selectionStart, input34.selectionEnd) === "{name}",
    input34.value.slice(input34.selectionStart, input34.selectionEnd));
  const changesBtn34 = q34("#chat-inner .code-changes-btn");
  check("an answer that changed files gets a Changes button, above its tool calls",
    /2 files changed \u00b7 \+3 \u22121/.test(changesBtn34.textContent) &&
      changesBtn34.parentNode.nextSibling && changesBtn34.parentNode.nextSibling.classList.contains("tool-run") &&
      q34("#chat-inner .code-changes-files").textContent === "docs/usage.md, src/util.py",
    changesBtn34.textContent);
  check("a whole file passed to a tool is one short line in its receipt",
    q34("#chat-inner .tool-call").textContent.length < 260, String(q34("#chat-inner .tool-call").textContent.length));
  changesBtn34.click();
  const overlay34 = q34("#changes-overlay");
  check("it opens the Changes window: one block per file, lines added and removed marked",
    overlay34 && qa34("#changes-overlay .diff-file").length === 2 &&
      qa34("#changes-overlay .dl.add").length === 3 && qa34("#changes-overlay .dl.del").length === 1 &&
      qa34("#changes-overlay .diff-status.added").length === 1 &&
      !overlay34.textContent.includes("diff --git") && overlay34.textContent.includes("Download .patch"),
    overlay34 && overlay34.textContent.slice(0, 200));
  doc34.dispatchEvent(new win34.KeyboardEvent("keydown", { key: "Escape" }));
  check("and Escape closes it", q34("#changes-overlay") === null);
  Array.from(bar34.querySelectorAll(".code-chip")).find((c) => c.textContent === "All changes").click();
  await waitFor(() => q34("#changes-overlay"));
  check("All changes opens every turn's changes, read from the server",
    q34("#changes-overlay .changes-turn") && q34("#changes-overlay .changes-turn").textContent.includes("document greet") &&
      qa34("#changes-overlay .diff-file").length === 2,
    q34("#changes-overlay").textContent.slice(0, 160));
  q34("#changes-overlay .icon-btn.close").click();
  check("and its close button closes it", q34("#changes-overlay") === null);
  sentBodies.length = 0;
  input34.value = "find every place greet is used";
  q34("#chat-inner .send-btn").click();
  await waitFor(() => chatBodies().length === 1 && !q34("#chat-inner .bubble.pending"));
  check("a message in an agentic chat carries the switch in its settings",
    chatBodies()[0].settings.agentic_code === true, JSON.stringify(chatBodies()[0].settings));
  q34("#agentic-btn").click();
  check("switching it off hides the bar, frees RAG and is saved with the chat",
    q34("#agentic-chip").textContent === "off" && bar34.classList.contains("hidden") && !q34("#rag-btn").disabled &&
      await waitFor(() => sentBodies.some((b) => b.url === "/api/conversations/code-demo" && b.settings &&
        b.settings.agentic_code === false), 5000));

  // ---- the Settings menu is remembered: a new chat starts from the last choice ----
  // Two pages over one storage, so the second load is a reload in the same browser.
  const storageMenu = {};
  const domMenu = loadPage(storageMenu);
  const docMenu = domMenu.window.document;
  const qm = (sel) => docMenu.querySelector(sel);
  await waitFor(() => qm("#chat-inner .chat-body"));
  const win = qm("#context-window");
  win.value = "12";
  win.dispatchEvent(new domMenu.window.Event("change"));
  qm("#rag-btn").click();
  check("a choice in the Settings menu is remembered in the browser",
    JSON.parse(storageMenu["deepseek-chat-menu-settings"] || "{}").context_messages === 12
      && JSON.parse(storageMenu["deepseek-chat-menu-settings"] || "{}").rag === true,
    storageMenu["deepseek-chat-menu-settings"]);
  const sentBefore = sentBodies.length;
  qm("#new-chat-btn").click();
  const newMeta = () => sentBodies.slice(sentBefore)
    .find((b) => b.url.startsWith("/api/conversations/") && b.settings);
  await waitFor(newMeta, 5000);
  check("a new chat starts from it, not from the defaults",
    newMeta() && newMeta().settings.context_messages === 12 && newMeta().settings.rag === true
      && qm("#context-window").value === "12" && qm("#rag-chip").textContent === "on",
    JSON.stringify(newMeta() && { w: newMeta().settings.context_messages, rag: newMeta().settings.rag }));
  const domMenu2 = loadPage(storageMenu);
  const docMenu2 = domMenu2.window.document;
  await waitFor(() => docMenu2.querySelector("#chat-inner .chat-body"));
  const sentBefore2 = sentBodies.length;
  docMenu2.querySelector("#new-chat-btn").click();
  const newMeta2 = () => sentBodies.slice(sentBefore2)
    .find((b) => b.url.startsWith("/api/conversations/") && b.settings);
  await waitFor(newMeta2, 5000);
  check("and after a reload, too",
    newMeta2() && newMeta2().settings.context_messages === 12 && newMeta2().settings.rag === true,
    JSON.stringify(newMeta2() && newMeta2().settings.context_messages));

  console.log(failures ? `\n${failures} FAILURES` : "\nAll checks passed");
  stopServer();
  process.exit(failures ? 1 : 0);
})().catch((err) => {
  console.error(err);
  stopServer();
  process.exit(1);
});
