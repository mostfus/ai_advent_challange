# LLM agent

A developer assistant you point at a project folder: switch a chat to **Agentic code** and give it a goal - find every use of an API, update the docs after a change, write a CHANGELOG or an ADR, check the code against the project's rules - and it reads, searches and edits the project's files itself, reads its git history through an MCP server, and shows what it changed as a diff. The **QA agent** tests an iOS app the way a tester does: you write a scenario in words, and the model carries it out in the Simulator, checks every expectation, reports with screenshots, traces a failure into the project's code, and turns a passed run into an XCUITest - in the QA tab, and on a self-hosted runner in CI. The same core reviews pull requests on GitHub: a GitHub Action reads the diff, searches the project's docs and code, and comments on the code. Underneath is a chat agent over DeepSeek or a local llama.cpp model, with layered memory, rules it may not break and tasks as a state machine. The agent is a headless Python core; the web UI is one front end on top of it.

## Capabilities

- **QA agent for iOS apps** (day 35): a scenario in words is carried out on an app in the iOS Simulator by the model - through its own XCUITest bridge, reading each screen as text - with every expectation checked and proved, a screenshot per step, the crash report when the app crashes, a read-only code diagnosis of a failure (`path:line` and a fix), and a passed run turned into an XCUITest that repeats it without a model. The QA tab builds the app from the project's scheme or tests any installed one; the suite runs in CI on the self-hosted Mac runner. See [the real task](#qa-agent---the-real-task-day-35) and [docs/qa.md](docs/qa.md).
- **AI review of pull requests**: a GitHub Action (`action.yml`) for any repository, on a self-hosted Mac runner. It gets the diff and the changed files (copies and renames found, so only real differences are reviewed), searches the project's documentation and code with RAG for each changed file, and asks the model for potential bugs, architecture problems and recommendations. Every finding is checked against the lines the model was shown; they are posted as comments on the code lines and one summary comment, kept up to date on every push without repeating a comment.
- **Agentic code** - a mini Claude Code over the project open in the Project tab. A switch in the chat's top bar; with it on, a message is a task stated as a goal, and the model decides which files to search, read and change, or asks one question when the goal is ambiguous. Its tools (`tools/files.py`): list the files, read one with line numbers, search every file for a text or a regex, write a file, replace an exact fragment, search the index by meaning; plus git - status, diffs, log - through MCP. Writes go to disk at once; every answer that changed something gets a **Changes** button that opens the diff of each file, and **All changes** shows the whole chat's, with a `.patch` to download and re-apply. The project's `AGENTS.md` (or `CLAUDE.md`) goes into the system prompt. One-click scenarios above the box: find usages, update docs, generate a CHANGELOG, write an ADR, check the rules.
- **Kept to the project**: paths outside the folder and symlinks out of it are refused, `.git` is never touched, secrets like `.env` are never read or written, and in a git repository a file git ignores is not written. Limits, lists and scenarios are in `code_agent.json`.
- **Project MCP server**: the official `mcp-server-git` over stdio, read-only - only the tools listed in `project_mcp.json` are offered.
- **RAG over the project**: Markdown, Swift, Python and schema files (YAML, SQL, GraphQL, protobuf), cut by structure, embedded with bge-m3 via Ollama into SQLite; in a git repository `.gitignore` applies. Reranking, grounded answers with citations and quotes, and a task state for RAG chats.
- Chats, each with its own model and settings, three context strategies (sliding window, sticky facts, rolling summary) and a token readout per turn and per conversation.
- Memory in three layers: the conversation, a task's working memory, and long-term memory; the agent proposes what to file, a person accepts it.
- Personality profiles, and invariants - rules every answer is checked against and re-asked once if it breaks one.
- Tasks as a state machine (planning, execution, validation, done) with checklists, pauses and agent-proposed moves.
- MCP tools from any remote server added in the MCP panel, called through one tool registry (`server__tool` routing).
- Local model via llama.cpp (`llama-server`), routed by model id.

Docs: [architecture](docs/architecture.md), [HTTP API](docs/api.md), [config and data](docs/data.md), [AI review of pull requests](docs/review.md), [QA agent](docs/qa.md).

## Run

1. `cp .env.example .env` and set `DEEPSEEK_API_KEY`. RAG needs Ollama with `bge-m3`; the git MCP server needs `uvx`.
2. `uv run server.py`, then open http://127.0.0.1:8000.
3. In the Project tab, open a folder (it starts as this app's own) and let the index build.
4. In the chat, switch **Agentic code** on and give it a task - or pick a scenario above the box. Open **Changes** under the answer to see what it wrote.
5. QA (needs Xcode with an iOS simulator runtime and `brew install xcodegen`): open an iOS project in the Project tab, then in the QA tab pick a simulator, **Build & install** a scheme (or pick an installed app), write a scenario and press **Run**.
6. Optional: the local model is in `local_llm.json`, the indexed files in `corpus.json`, the project's MCP servers in `project_mcp.json`, agentic code's limits and scenarios in `code_agent.json`, the QA agent's model and budget in `qa_agent.json`.

Tests: `uv run python -m unittest discover -s tests -v` and `npm install && npm test`.

## AI review of pull requests

1. On the Mac that will run reviews: `brew install uv ollama`, `ollama pull bge-m3`.
2. In the GitHub repository: register a self-hosted runner on that Mac with the label `ai-review` (Settings → Actions → Runners), add the secret `DEEPSEEK_API_KEY`, and require approval for workflows from external contributors.
3. Add `.github/workflows/ai-review.yml` that runs `mostfus/ai_advent_challange/day32-ai-code-review@<full commit SHA>` on `pull_request` (pinned: the code runs on your machine) - the file is in [docs/review.md](docs/review.md), with the inputs (`model`, `language`, `project-path`, ...). This repository has one at the root.
4. Open a pull request: the review appears as comments on the code and an "AI review" summary comment.

Without GitHub, `uv run python -m review --repo <repo> --base main --head HEAD` prints the review of a local range. What is sent and how much is in `review.json`.

## QA agent - the real task (day 35)

### The task

Checking that an iOS app still does what it should is manual work: build it, open the simulator, tap through the same screens after every change, look whether the right thing shows up, and - when it does not - write down the steps, take screenshots, find the crash log and go looking for the code. UI tests would automate it, but writing and maintaining XCUITests for every flow is work nobody gets to. The QA agent takes the tester's job as it is written down - **a scenario in plain words**:

> Open Diffable Data Source → Mountains Search. Type K2 into the search field.
> Expected: K2 is in the results, Everest is not.

and does the rest: builds and installs the app, carries the scenario out in the Simulator, checks every expectation with proof, reports with screenshots, and - on the way - produces the regression test that would have taken an hour to write.

### How AI takes part

```
scenario (words) ──► QA agent (LLM) ◄──── screen as text: elements, refs, values ──── XCUITest bridge ◄── iOS Simulator
                        │   tap e7 / type "K2" / scroll / check / finish  ─────────────►  (HTTP, 127.0.0.1)
                        ▼
              verdict + checks with evidence + steps with screenshots (+ crash report)
                        │ failed or crashed                         │ passed
                        ▼                                           ▼
     developer agent (LLM, read-only file tools)          XCUITest generated from the steps,
     reads the project: suspects as path:line, a fix      run later without a model - in CI too
```

1. **Understanding the scenario.** The model turns the words into actions - which section to open, which field to type into - on screens it reads as text (`tools/simulator.py` cuts the accessibility tree down to controls, texts and containers, each with a ref).
2. **Finding its way.** Nothing is scripted: it scrolls when a row is not on the screen, goes back, waits for loading, answers a system alert, notices that a tap changed nothing.
3. **Judging.** Every expectation becomes a `check`: passed or not, with the elements that prove it (and texts that must be absent). The verdict is computed from the recorded checks and a crash, not from what the model says.
4. **Diagnosing.** A failure or a crash goes to a second agent - a developer with the project's files, read-only - with the steps, the failed check, the last screen and the crash's stack; it answers with suspects as `path:line` and a fix.
5. **Writing the test.** A passed run becomes an XCUITest (`qa/xcuitest.py`): each step finds its element by type and label, each check becomes an assertion. Rows that only show "the list has items" (live or random data) are asserted as "rows besides the known ones" - the model marks them as `examples` when the scenario says the data changes.

### Live results (Modern Collection Views, iPhone 17, iOS 26.4, deepseek-v4-flash)

| Scenario | Verdict | Time | Tokens |
|---|---|---|---|
| Mountains Search: K2 is found, Everest is not | passed - 4 steps, 2 checks | 16 s | 17k |
| Settings: Wi-Fi: switch off, the networks disappear | passed - 4 steps, 3 checks | 17-27 s | 20k |
| Emoji Explorer: the Smileys section shows emoji | passed (the section was already open, the agent noticed) | 20 s | 20k |
| Mountains Search in Russian: «Эльбрус» is found | **failed** - the agent also tried «Elbrus» to show search itself works | 54 s with the diagnosis | 70k |

The diagnosis of the failure, in 8 read-only tool calls: the data has only `Mount Elbrus` (`MountainsRawData.swift:265`), `Mountain.contains` matches one `name` with no aliases or transliteration (`MountainsController.swift:22-27`) - suggested fix: localized names per mountain, matched all at once.

`python -m qa` on a clean checkout of the app, as the CI job runs it: build 9 s, bridge built and started, 3 of 3 scenarios passed (13-15 s each), then the 3 generated XCUITests - 1 min 57 s in all. That run found a real flaky test: the Wi-Fi sample's network list is random, and the first generated test asserted "breeno-net is listed". The fix is the `examples` above: the model cannot know from one screen that a list is random, the scenario says so ("the list is live"), and the test now asserts "a row besides Wi-Fi". All 3 tests pass (11-24 s each, no model).

### In CI

`.github/workflows/qa-agent.yml` (repository root) runs the suite on the self-hosted Mac runner for pull requests that touch `day35-qa-agent/` and on demand (`workflow_dispatch`, optionally one scenario or another model): it checks out the app (`mostfus/UIKitDeepCollectionView`), and the composite action `qa-action/` builds it, runs every scenario and every generated test, posts the summary to the pull request and keeps the screenshots as the job's artifact. The exit code fails the job when a scenario did not pass or a test failed. Setup and inputs: [docs/qa.md](docs/qa.md).

### What is not fully working (keys, access, limits)

- The CI job needs the runner to have Xcode with an iOS runtime, `xcodegen` and `uv`, and the `DEEPSEEK_API_KEY` secret; it was run here as `python -m qa`, not yet by GitHub Actions. The runner's package is the x86_64 one: Xcode's tools are started as arm64 under Rosetta (`qa/simctl.native`), which is untested on the runner itself.
- Simulator only: a real device needs signing and a device-side runner.
- The model sees the accessibility tree, not pixels: a broken layout, a missing image or a wrong colour passes unnoticed, and a control without an accessibility label is shown as "Button (no label) at x,y".
- Crash reports come from the Mac's `~/Library/Logs/DiagnosticReports` - checked with a written `.ips`, not with a real crash of the sample app (it does not crash). TestFlight or App Store crashes would need App Store Connect access.
- The local Qwen model is selectable in the tab but was not tried on day 35.
