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
  function loadPage() {
    return new JSDOM(HTML, {
      runScripts: "dangerously",
      pretendToBeVisual: true,
      url: BASE + "/",
      beforeParse(window) {
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
  check("no parameter controls in the popover",
    popover.querySelectorAll("select, input:not([type=checkbox])").length === 0,
    `${popover.querySelectorAll("select, input:not([type=checkbox])").length} controls`);

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

  check("each pane has its own settings form", pane0.querySelectorAll(".pane-settings .field").length === 6,
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
    panes[3].querySelectorAll(".pane-settings .field").length === 6,
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
  check("the clear button is not one of the six parameters",
    panes2[0].querySelectorAll(".pane-settings .field").length === 6,
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

  console.log(failures ? `\n${failures} FAILURES` : "\nAll checks passed");
  stopServer();
  process.exit(failures ? 1 : 0);
})().catch((err) => {
  console.error(err);
  stopServer();
  process.exit(1);
});
