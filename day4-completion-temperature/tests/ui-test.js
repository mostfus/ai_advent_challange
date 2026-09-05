// Headless UI test: loads static/index.html into jsdom, points it at a real
// server, and drives the same clicks a user would - tabs, adding and closing
// compare panes, settings controls, sending messages.
//
//   npm install     # once, pulls jsdom
//   npm test
//
// It starts its own uvicorn on TEST_PORT (8765 by default) and stops it again,
// so it never collides with a dev server on :8000. The API key handed to that
// process is deliberately a dummy: python-dotenv does not override an
// already-set variable, so a real key in .env is ignored and the test can
// never spend tokens. DeepSeek therefore answers every call with an auth
// error, which is all these checks need - they are about the plumbing (which
// settings each pane sends, where the answer and the debug entry land), not
// about model output.
//
// Adding a check: call check("what it should do", <condition>, <detail shown
// on the line>). Anything that fails sets the exit code.
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { JSDOM } = require("jsdom");

const PROJECT_DIR = path.join(__dirname, "..");
const HTML = fs.readFileSync(path.join(PROJECT_DIR, "static", "index.html"), "utf8");
const PORT = process.env.TEST_PORT || "8765";
const BASE = `http://127.0.0.1:${PORT}`;
const sentBodies = [];
let server = null;

async function startServer() {
  server = spawn(
    "uv",
    ["run", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", PORT, "--log-level", "warning"],
    { cwd: PROJECT_DIR, env: { ...process.env, DEEPSEEK_API_KEY: "ui-test-dummy-key" }, stdio: "inherit" }
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
  const dom = new JSDOM(HTML, {
    runScripts: "dangerously",
    pretendToBeVisual: true,
    url: BASE + "/",
    beforeParse(window) {
      window.fetch = async (url, init) => {
        if (init && init.body) sentBodies.push(JSON.parse(init.body));
        const res = await fetch(BASE + url, init);
        const text = await res.text();
        return { ok: res.ok, status: res.status, statusText: res.statusText, json: async () => JSON.parse(text) };
      };
      window.HTMLElement.prototype.scrollIntoView = () => {};
      Object.defineProperty(window.navigator, "clipboard", { value: { writeText: async () => {} } });
    },
  });
  const { window } = dom;
  const doc = window.document;

  const q = (sel) => doc.querySelector(sel);
  const qa = (sel) => Array.from(doc.querySelectorAll(sel));

  // ---- boot ----
  const booted = await waitFor(() => q("#single-settings-mount .settings-body"));
  check("config loads and single view mounts", booted);

  // ---- single view ----
  check("single view is the default tab", q("#single-view").classList.contains("active"));
  check("compare view hidden initially", !q("#compare-view").classList.contains("active"));
  const singleFields = qa("#single-settings-mount .field").length;
  check("single view has all 6 parameter fields", singleFields === 6, `found ${singleFields}`);
  check("single chat body mounted", !!q("#chat-inner .chat-body"));
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
  const tempField = Array.from(panes[3].querySelectorAll(".pane-settings .field"))
    .find((f) => f.querySelector("label").textContent === "Temperature");
  check("pane settings include a Temperature field", !!tempField);
  const paneSlider = tempField && tempField.querySelector('input[type="range"]');
  check("that field is a slider with a value readout",
    !!paneSlider && !!tempField.querySelector(".slider-value"));
  check("no preset chips in a pane", tempField.querySelectorAll(".preset-btn").length === 0);
  check("no explanatory hints in a pane", panes[3].querySelectorAll(".pane-settings .hint").length === 0,
    `${panes[3].querySelectorAll(".pane-settings .hint").length} hints`);
  check("single view keeps its hints and chips",
    qa("#single-settings-mount .hint").length > 0 && qa("#single-settings-mount .preset-btn").length === 4,
    `${qa("#single-settings-mount .hint").length} hints, ${qa("#single-settings-mount .preset-btn").length} chips`);

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
    sentBodies.length === 1 && sentBodies[0].settings.model === "deepseek-v4-pro" &&
    sentBodies[0].settings.temperature === 0,
    JSON.stringify(sentBodies[0] && sentBodies[0].settings));
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
  check("broadcast fanned out to one request per pane", sentBodies.length === 4, `${sentBodies.length} requests`);

  const settingsSent = sentBodies.map((b) => `${b.settings.model}/${b.settings.temperature}/` +
    `${b.settings.reasoning_effort}/${b.settings.response_format}`);
  check("each request carried its own distinct settings",
    new Set(settingsSent).size === 4, settingsSent.join("  |  "));
  check("all four requests carried the same message",
    sentBodies.every((b) => b.message === "compare this"));
  check("each pane logged its own debug entry",
    panes.every((p) => p.querySelectorAll(".pane-debug .debug-entry").length >= 1),
    panes.map((p) => p.querySelectorAll(".pane-debug .debug-entry").length).join(","));

  // ---- switching tabs keeps both views' state ----
  qa(".tab").find((t) => t.dataset.view === "single").click();
  check("can switch back to the single chat", q("#single-view").classList.contains("active"));
  compareTab.click();
  check("compare panes survive a tab round-trip", qa(".pane").length === 4, `${qa(".pane").length} panes`);
  check("transcripts survive a tab round-trip",
    panes[0].querySelectorAll(".msg-row").length === 4,
    `${panes[0].querySelectorAll(".msg-row").length} rows`);

  // ---- single chat still works on its own ----
  sentBodies.length = 0;
  const singleInput = q("#chat-inner .prompt-input");
  singleInput.value = "single mode still works";
  q("#chat-inner .send-btn").click();
  await waitFor(() => q("#chat-inner .msg-row.assistant") && !q("#chat-inner .bubble.pending"));
  check("single chat sends with its own settings",
    sentBodies.length === 1 && sentBodies[0].settings.model === "deepseek-v4-flash",
    JSON.stringify(sentBodies[0] && sentBodies[0].settings));
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

  console.log(failures ? `\n${failures} FAILURES` : "\nAll checks passed");
  stopServer();
  process.exit(failures ? 1 : 0);
})().catch((err) => {
  console.error(err);
  stopServer();
  process.exit(1);
});
