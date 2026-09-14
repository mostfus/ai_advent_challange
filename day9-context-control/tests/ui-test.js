// Headless UI test: loads static/index.html into jsdom, points it at a real
// server, and drives the same clicks a user would - tabs, adding and closing
// compare panes, settings controls, sending messages.
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
// settings each pane sends, where the answer and the debug entry land, what
// the debug panel reports), not about model output. Nothing here touches the
// network.
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
const PORT = process.env.TEST_PORT || "8765";
const BASE = `http://127.0.0.1:${PORT}`;
// A throwaway store, so the suite can assert on "everything that is saved"
// without the real data/conversations getting in the way - or being harmed.
const STORE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "chat-store-test-"));
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
  fs.rmSync(STORE_DIR, { recursive: true, force: true });
}

// What is actually on disk right now - the other half of every persistence
// check: the page shows it, and the store really has it.
function storedConversations() {
  return fs
    .readdirSync(STORE_DIR)
    .filter((name) => name.endsWith(".json"))
    .map((name) => JSON.parse(fs.readFileSync(path.join(STORE_DIR, name), "utf8")));
}

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
  check("exactly two tabs", qa(".tab").length === 2, `${qa(".tab").length} tabs`);
  check("no free-models tab left behind",
    qa(".tab").every((t) => t.dataset.view !== "free") && q("#free-view") === null);
  check("compare view hidden initially", !q("#compare-view").classList.contains("active"));
  check("single chat body mounted", !!q("#chat-inner .chat-body"));

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
  // Day 9 puts two controls in here, and they are deliberately not model
  // parameters: the context window and the compression switch are about what
  // the chat remembers, which is the one thing the single view has always let
  // you decide. Model, temperature and the rest still belong to compare.
  check("no model parameters in the popover",
    popover.querySelectorAll("select, input[type=range], input[type=text], textarea").length === 0,
    `${popover.querySelectorAll("select, input[type=range], input[type=text], textarea").length} controls`);
  check("the context window is the one number in it",
    popover.querySelectorAll("input[type=number]").length === 1 &&
    !!popover.querySelector("#context-window") && !!popover.querySelector("#compression-toggle"));

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

  // ---- switch to compare ----
  const compareTab = qa(".tab").find((t) => t.dataset.view === "compare");
  compareTab.click();
  check("compare tab activates", q("#compare-view").classList.contains("active"));
  check("single view hidden while comparing", !q("#single-view").classList.contains("active"));
  check("compare seeds 2 panes", qa(".pane").length === 2, `${qa(".pane").length} panes`);
  check("counter shows 2 of 5", q("#compare-count").textContent === "2 of 5 chats", q("#compare-count").textContent);

  // ---- add up to the max of 5 ----
  const addBtn = q("#add-chat-btn");
  addBtn.click();
  addBtn.click();
  addBtn.click();
  check("can add up to 5 panes", qa(".pane").length === 5, `${qa(".pane").length} panes`);
  check("add button disabled at the cap", addBtn.disabled === true);
  addBtn.click();
  check("clicking past the cap adds nothing", qa(".pane").length === 5, `${qa(".pane").length} panes`);
  check("counter shows 5 of 5", q("#compare-count").textContent === "5 of 5 chats", q("#compare-count").textContent);

  // ---- per-pane settings / debug / close ----
  const pane0 = qa(".pane")[0];
  const buttons = (pane) => Array.from(pane.querySelectorAll(".pane-header .icon-btn"));
  const [settingsBtn0, debugBtn0, closeBtn0] = buttons(pane0);

  check("each pane has its own settings form", pane0.querySelectorAll(".pane-settings .field").length === 7,
    `${pane0.querySelectorAll(".pane-settings .field").length} fields`);
  // Seeded panes start collapsed (only panes added by hand open their settings),
  // so assert the toggle flips from whatever the pane started at.
  const settingsOpenAtStart = pane0.classList.contains("show-settings");
  settingsBtn0.click();
  check("pane settings toggle flips", pane0.classList.contains("show-settings") === !settingsOpenAtStart);
  check("settings button reflects the state",
    settingsBtn0.classList.contains("active") === !settingsOpenAtStart);
  settingsBtn0.click();
  check("pane settings toggle flips back", pane0.classList.contains("show-settings") === settingsOpenAtStart);

  const addedPane = qa(".pane")[4];
  check("a hand-added pane opens with its settings visible", addedPane.classList.contains("show-settings"));

  check("pane debug hidden by default", !pane0.classList.contains("show-debug"));
  debugBtn0.click();
  check("per-pane debug toggles on", pane0.classList.contains("show-debug") && debugBtn0.classList.contains("active"));
  check("pane debug has its own log", !!pane0.querySelector(".pane-debug .debug-log"));

  closeBtn0.click();
  check("closing a pane removes it", qa(".pane").length === 4, `${qa(".pane").length} panes`);
  check("add button re-enabled below the cap", addBtn.disabled === false);
  check("counter follows removals", q("#compare-count").textContent === "4 of 5 chats", q("#compare-count").textContent);

  // ---- give panes different parameters ----
  const panes = qa(".pane");
  const setSelect = (pane, idx, value) => {
    const select = pane.querySelectorAll(".pane-settings select")[idx];
    select.value = value;
    select.dispatchEvent(new window.Event("change"));
  };
  const setTemp = (pane, value) => {
    const slider = pane.querySelector('.pane-settings input[type="range"]');
    slider.value = String(value);
    slider.dispatchEvent(new window.Event("change"));
  };

  setSelect(panes[0], 0, "deepseek-v4-pro");     // model
  setTemp(panes[0], 0);
  setSelect(panes[1], 3, "max");                 // reasoning effort
  setTemp(panes[1], 1.8);
  setSelect(panes[2], 2, "json_object");         // response format

  check("pane header follows the model", panes[0].querySelector(".pane-title").textContent === "deepseek-v4-pro",
    panes[0].querySelector(".pane-title").textContent);
  const summary0 = panes[0].querySelector(".pane-summary").textContent;
  check("pane summary shows its own params", summary0.includes("deepseek-v4-pro") && summary0.includes("T 0.0"), summary0);
  const summary1 = panes[1].querySelector(".pane-summary").textContent;
  check("second pane keeps different params", summary1.includes("T 1.8") && summary1.includes("reasoning: max"), summary1);

  // ---- compare panes: bare temperature slider, no hints, no preset chips ----
  // (the compare columns are the only place parameters can be set at all now)
  const tempField = Array.from(panes[3].querySelectorAll(".pane-settings .field"))
    .find((f) => f.querySelector("label").textContent === "Temperature");
  check("pane settings include a Temperature field", !!tempField);
  const paneSlider = tempField && tempField.querySelector('input[type="range"]');
  check("that field is a slider with a value readout",
    !!paneSlider && !!tempField.querySelector(".slider-value"));
  check("no preset chips anywhere", qa(".preset-btn").length === 0);
  check("no explanatory hints in a pane", panes[3].querySelectorAll(".pane-settings .hint").length === 0,
    `${panes[3].querySelectorAll(".pane-settings .hint").length} hints`);
  check("a pane still has all 6 parameter fields",
    panes[3].querySelectorAll(".pane-settings .field").length === 7,
    `${panes[3].querySelectorAll(".pane-settings .field").length} fields`);

  paneSlider.value = "1.3";
  paneSlider.dispatchEvent(new window.Event("change"));
  check("the pane slider updates value and summary",
    tempField.querySelector(".slider-value").textContent === "1.3" &&
    panes[3].querySelector(".pane-summary").textContent.includes("T 1.3"),
    panes[3].querySelector(".pane-summary").textContent);

  // ---- per-pane send ----
  sentBodies.length = 0;
  const paneInput = panes[0].querySelector(".prompt-input");
  paneInput.value = "hello from pane 0";
  panes[0].querySelector(".send-btn").click();
  await waitFor(() => panes[0].querySelectorAll(".bubble").length >= 2 &&
    !panes[0].querySelector(".bubble.pending"));
  check("per-pane send produces a user + assistant bubble",
    panes[0].querySelectorAll(".msg-row.user").length === 1 &&
    panes[0].querySelectorAll(".msg-row.assistant").length === 1);
  check("only the pane that sent got a message", panes[1].querySelectorAll(".msg-row").length === 0);
  check("that request carried the pane's own settings",
    chatBodies().length === 1 && chatBodies()[0].settings.model === "deepseek-v4-pro" &&
    chatBodies()[0].settings.temperature === 0,
    JSON.stringify(chatBodies()[0] && chatBodies()[0].settings));
  check("and the conversation it belongs to",
    !!chatBodies()[0].conversation_id && chatBodies()[0].view === "compare",
    `${chatBodies()[0].conversation_id} / ${chatBodies()[0].view}`);
  check("pane debug log recorded the exchange",
    panes[0].querySelectorAll(".pane-debug .debug-entry").length === 1);

  // ---- broadcast: one prompt, N independent requests ----
  sentBodies.length = 0;
  q("#broadcast-input").value = "compare this";
  q("#broadcast-btn").click();
  const allAnswered = await waitFor(() =>
    panes.every((p) => p.querySelectorAll(".msg-row.assistant").length >= 1) &&
    !doc.querySelector("#compare-grid .bubble.pending"));
  check("every pane answered the broadcast", allAnswered);
  check("broadcast fanned out to one request per pane", chatBodies().length === 4, `${chatBodies().length} requests`);

  const settingsSent = chatBodies().map((b) => `${b.settings.model}/${b.settings.temperature}/` +
    `${b.settings.reasoning_effort}/${b.settings.response_format}`);
  check("each request carried its own distinct settings",
    new Set(settingsSent).size === 4, settingsSent.join("  |  "));
  check("all four requests carried the same message",
    chatBodies().every((b) => b.message === "compare this"));
  check("each pane sent its own conversation id",
    new Set(chatBodies().map((b) => b.conversation_id)).size === 4,
    chatBodies().map((b) => b.conversation_id).join(", "));
  check("each pane logged its own debug entry",
    panes.every((p) => p.querySelectorAll(".pane-debug .debug-entry").length >= 1),
    panes.map((p) => p.querySelectorAll(".pane-debug .debug-entry").length).join(","));

  // ---- switching tabs keeps both views' state ----
  qa(".tab").find((t) => t.dataset.view === "single").click();
  check("can switch back to the single chat", q("#single-view").classList.contains("active"));
  compareTab.click();
  check("compare panes survive a tab round-trip",
    qa("#compare-grid .pane").length === 4, `${qa("#compare-grid .pane").length} panes`);
  check("transcripts survive a tab round-trip",
    panes[0].querySelectorAll(".msg-row").length === 4,
    `${panes[0].querySelectorAll(".msg-row").length} rows`);
  check("compare panes are not re-seeded on reopen", qa("#compare-grid .pane").length === 4);

  // ---- single chat still works on its own ----
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
  check("compare panes untouched by the single chat",
    panes[1].querySelectorAll(".msg-row").length === 2,
    `${panes[1].querySelectorAll(".msg-row").length} rows`);

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
  check("every compare column has its own usage strip",
    panes.every((p) => !!p.querySelector(".usage-bar")),
    `${panes.filter((p) => !!p.querySelector(".usage-bar")).length} of ${panes.length}`);

  // ======================================================================
  // Day 7: the conversation outlives the page
  // ======================================================================

  // ---- what is actually on disk ----
  const stored = storedConversations();
  const storedSingle = stored.find((c) => c.id === "single");
  const storedPanes = stored.filter((c) => c.view === "compare");

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

  check("every open compare column was stored", storedPanes.length === 4,
    `${storedPanes.length} stored columns`);
  check("the closed column was deleted from the store",
    stored.length === 5, `${stored.length} records`);
  check("columns stored the parameters they were given",
    storedPanes.some((c) => c.settings.model === "deepseek-v4-pro" && c.settings.temperature === 0) &&
    storedPanes.some((c) => c.settings.reasoning_effort === "max" && c.settings.temperature === 1.8) &&
    storedPanes.some((c) => c.settings.response_format === "json_object"),
    storedPanes.map((c) => `${c.settings.model}/${c.settings.temperature}`).join("  |  "));

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

  // Compare columns come back without the tab being opened, in their original
  // order, with their own parameters and their own transcripts.
  const panes2 = qa2("#compare-grid .pane");
  check("every compare column is restored", panes2.length === 4, `${panes2.length} panes`);
  check("restored columns keep their order and their models",
    panes2.map((p) => p.querySelector(".pane-title").textContent).join(",") ===
      "deepseek-v4-pro,deepseek-v4-flash,deepseek-v4-flash,deepseek-v4-flash",
    panes2.map((p) => p.querySelector(".pane-title").textContent).join(","));
  check("restored columns keep their parameters",
    panes2[0].querySelector(".pane-summary").textContent.includes("T 0.0") &&
    panes2[1].querySelector(".pane-summary").textContent.includes("reasoning: max") &&
    panes2[2].querySelector(".pane-summary").textContent.includes("json_object") &&
    panes2[3].querySelector(".pane-summary").textContent.includes("T 1.3"),
    panes2.map((p) => p.querySelector(".pane-summary").textContent).join("  |  "));
  check("restored columns keep their own transcripts",
    panes2.map((p) => p.querySelectorAll(".msg-row").length).join(",") === "4,2,2,2",
    panes2.map((p) => p.querySelectorAll(".msg-row").length).join(","));
  check("a restored column's settings form is filled in from the store",
    panes2[0].querySelectorAll(".pane-settings select")[0].value === "deepseek-v4-pro",
    panes2[0].querySelectorAll(".pane-settings select")[0].value);

  // Opening the compare tab must not seed two empty columns on top of the
  // restored ones.
  qa2(".tab").find((t) => t.dataset.view === "compare").click();
  check("opening the compare tab does not re-seed it",
    qa2("#compare-grid .pane").length === 4, `${qa2("#compare-grid .pane").length} panes`);

  // ---- the clear-context button ----
  const clearBtn2 = q2("#clear-context-btn");
  const status2 = q2("#context-status");
  check("the settings popover has a clear-context button", !!clearBtn2);
  check("it reports how much is saved", status2.textContent === "2 messages saved on the server.",
    status2.textContent);
  check("each compare column has its own clear button",
    panes2.every((p) => !!p.querySelector(".pane-settings .settings-foot .danger-btn")));
  check("the clear button is not one of the seven parameters",
    panes2[0].querySelectorAll(".pane-settings .field").length === 7,
    `${panes2[0].querySelectorAll(".pane-settings .field").length} fields`);

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

  // ---- closing a restored column deletes it for good ----
  Array.from(panes2[3].querySelectorAll(".pane-header .icon-btn"))[2].click();
  await waitFor(() => storedConversations().length === 4);
  check("closing a restored column removes it from the store",
    storedConversations().filter((c) => c.view === "compare").length === 3,
    `${storedConversations().filter((c) => c.view === "compare").length} columns`);

  // ---- third load: the cleared chat stays cleared ----
  const dom3 = loadPage();
  const doc3 = dom3.window.document;
  await waitFor(() => doc3.querySelector("#chat-inner .chat-body"));
  await waitFor(() => doc3.querySelectorAll("#compare-grid .pane").length === 3);
  check("a cleared chat comes back empty, not restored",
    doc3.querySelectorAll("#chat-inner .msg-row").length === 0 &&
    !doc3.querySelector("#chat-inner .restored-divider"),
    `${doc3.querySelectorAll("#chat-inner .msg-row").length} rows`);
  check("a closed column does not come back",
    doc3.querySelectorAll("#compare-grid .pane").length === 3,
    `${doc3.querySelectorAll("#compare-grid .pane").length} panes`);
  check("the columns that were kept still have their transcripts",
    Array.from(doc3.querySelectorAll("#compare-grid .pane"))
      .map((p) => p.querySelectorAll(".msg-row").length).join(",") === "4,2,2",
    Array.from(doc3.querySelectorAll("#compare-grid .pane"))
      .map((p) => p.querySelectorAll(".msg-row").length).join(","));

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
    view: "compare",
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

  const dom4 = loadPage();
  const doc4 = dom4.window.document;
  await waitFor(() => doc4.querySelectorAll("#compare-grid .pane").length === 4);
  const demoPane = Array.from(doc4.querySelectorAll("#compare-grid .pane")).pop();
  const demoBar = demoPane.querySelector(".usage-bar");
  demoBar.dispatchEvent(new dom4.window.MouseEvent("click", { bubbles: true }));
  const demoDetail = demoPane.querySelector(".usage-detail");
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

  // ======================================================================
  // More than one conversation, and a menu to pick between them
  // ======================================================================
  // The single view used to be one conversation with a fixed id. It is now a
  // list of them - all `view: "single"` on the server, one mounted at a time -
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
  await waitFor(() => storedConversations().filter((c) => c.view === "single").length === 2);
  check("+ New chat adds one", items5().length === 2, `${items5().length} chats`);
  check("and it is registered on the server straight away, before anything is said",
    storedConversations().filter((c) => c.view === "single").length === 2,
    storedConversations().filter((c) => c.view === "single").map((c) => c.id).join(", "));
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
  check("compare columns are not in the single view's menu",
    doc6.querySelectorAll(".chat-item").length <
      storedConversations().length,
    `${doc6.querySelectorAll(".chat-item").length} of ${storedConversations().length} records`);

  // ---- deleting one ----
  const singlesBefore = storedConversations().filter((c) => c.view === "single").length;
  dom6.window.document.querySelector("#chats-btn")
    .dispatchEvent(new dom6.window.MouseEvent("click", { bubbles: true }));
  doc6.querySelectorAll(".chat-item")[1].querySelector(".chat-item-remove")
    .dispatchEvent(new dom6.window.MouseEvent("click", { bubbles: true }));
  const deleted = await waitFor(() =>
    storedConversations().filter((c) => c.view === "single").length === singlesBefore - 1);
  check("deleting a conversation removes it from the store", deleted,
    `${storedConversations().filter((c) => c.view === "single").length} left`);
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
      view: "compare",
      settings: { model: "deepseek-v4-flash", system_prompt: "You are a helpful assistant.",
        stop: [], response_format: "text", reasoning_effort: "low", temperature: 1.0,
        context_messages: 4, context_compression: true },
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
    body: JSON.stringify({ message, conversation_id: id, view: "compare", settings }),
  }).then((r) => r.json());
  const onSettings = { model: "deepseek-v4-flash", system_prompt: "You are a helpful assistant.",
    stop: [], response_format: "text", reasoning_effort: "low", temperature: 1.0,
    context_messages: 4, context_compression: true };

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
    { context_compression: false }), "without the summary this time");
  check("compression off is day 7 again: the window, and nothing older",
    off.debug.request.messages.length === 6 &&
    !off.debug.request.messages.some((m) => m.content.includes(SUMMARY_TEXT)),
    off.debug.request.messages.map((m) => m.role).join(","));
  check("and the summary is not deleted by switching it off",
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
  // The compare view holds five columns at most, and there are already five
  // worth of saved ones by now, so the strip is driven where it is certain to
  // be mounted: a single-view conversation, picked from the switcher.
  plantChat("ctx-view", { view: "single", created_at: "2031-01-04T00:00:00.000+00:00",
    summary: plantedSummary });
  const dom7 = loadPage();
  const win7 = dom7.window;
  const doc7 = dom7.window.document;
  const click7 = (el) => el.dispatchEvent(new win7.MouseEvent("click", { bubbles: true }));
  await waitFor(() => doc7.querySelector("#chat-inner .chat-body"));

  // ---- the single view's own two controls, on the chat it opens with ----
  const windowInput = doc7.querySelector("#context-window");
  const compressToggle = doc7.querySelector("#compression-toggle");
  check("the single chat can be given a window without a parameter form",
    !!windowInput && !!compressToggle && Number(windowInput.value) === 40,
    windowInput && windowInput.value);

  windowInput.value = "999";
  windowInput.dispatchEvent(new win7.Event("change", { bubbles: true }));
  check("a window outside the bounds snaps back to them",
    windowInput.value === "200", windowInput.value);
  windowInput.value = "6";
  windowInput.dispatchEvent(new win7.Event("change", { bubbles: true }));
  compressToggle.checked = true;
  compressToggle.dispatchEvent(new win7.Event("change", { bubbles: true }));

  const singleId = storedConversations()
    .filter((c) => c.view === "single" && c.id !== "ctx-view")[0].id;
  const saved9 = await waitFor(() => {
    const record = storedConversations().find((c) => c.id === singleId);
    return record.settings.context_messages === 6 && record.settings.context_compression === true;
  });
  check("and both controls are saved with the conversation, not in the browser", saved9,
    JSON.stringify(storedConversations().find((c) => c.id === singleId).settings));
  check("the strip follows the controls before anything is sent",
    doc7.querySelector("#chat-inner .context-bar").textContent.includes("compressed"),
    doc7.querySelector("#chat-inner .context-bar").textContent.trim());

  // ---- and the chat that already has a summary ----
  click7(doc7.querySelector("#chats-btn"));
  const viewItem = Array.from(doc7.querySelectorAll(".chat-item"))
    .find((i) => i.querySelector(".chat-item-preview").textContent.includes("ctx-view question 1"));
  click7(viewItem);
  await waitFor(() => doc7.querySelectorAll("#chat-inner .msg-row").length === 12);

  check("a compressed chat carries its window with it, not the view's",
    Number(doc7.querySelector("#context-window").value) === 4 &&
    doc7.querySelector("#compression-toggle").checked === true,
    doc7.querySelector("#context-window").value);

  const ctxBar = doc7.querySelector("#chat-inner .context-bar");
  const ctxDetail = doc7.querySelector("#chat-inner .context-detail");
  check("every chat has a context strip, next to the usage one",
    !!ctxBar && !!doc7.querySelector("#chat-inner .usage-bar") &&
    ctxDetail.classList.contains("hidden"));
  check("a compare column says in its header how much context it carries",
    Array.from(doc7.querySelectorAll(".pane-summary"))
      .every((el) => /ctx \d+/.test(el.textContent)),
    Array.from(doc7.querySelectorAll(".pane-summary"))
      .map((el) => el.textContent).join("  |  "));
  check("the strip says whether this chat is compressed without being opened",
    ctxBar.textContent.includes("compressed"), ctxBar.textContent.trim());

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
  check("a reloaded chat remembers how much it is meant to remember",
    Number(doc8.querySelector("#context-window").value) === 6 &&
    doc8.querySelector("#compression-toggle").checked === true,
    doc8.querySelector("#context-window").value);

  console.log(failures ? `\n${failures} FAILURES` : "\nAll checks passed");
  stopServer();
  process.exit(failures ? 1 : 0);
})().catch((err) => {
  console.error(err);
  stopServer();
  process.exit(1);
});
