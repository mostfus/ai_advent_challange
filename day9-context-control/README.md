# LLM Agent that keeps everything and sends a summary of it

A small **agent** that takes a question, sends it to an LLM over plain HTTP, shows the whole exchange in a web GUI, keeps as many conversations as you like, remembers all of them across restarts and reports what that costs — and, since day 9, **decides how much of a conversation to send, and compresses the rest instead of forgetting it.**

Day 7 gave the agent a memory by replaying the transcript into every request. Day 8 put the price of that on screen: the API is stateless, the whole conversation goes up every time, and the bill grows with the square of the chat rather than with the message. Between them they left one crude lever — a hard cap at 40 messages, past which the chat simply forgot, silently, and you found out by being asked your own name again.

Day 9 replaces the lever with a control:

```
CONTEXT                                                       compressed ▸
```

```
NEXT REQUEST
  sent verbatim · window                    8
  stood for by the summary                 46
  ──────────────────────────────────────────
  kept on the server                       54
  Everything outside the window is compressed into the summary below,
  written once when the chat overflows rather than on every turn.

SUMMARY · STANDS FOR 46 MESSAGES
  ┌──────────────────────────────────────────────────────────────┐
  │ The user is called Maksim and is working through an advent   │
  │ challenge, one project per day… They asked for answers in    │
  │ Russian and for code comments in English. Unresolved:        │
  │ whether day 10 keeps the same store.                         │
  └──────────────────────────────────────────────────────────────┘
  rewritten 3 times · 1,840 tokens to produce · by deepseek-v4-flash
  [ Forget summary ]
```

**Nothing is deleted.** The transcript stays whole on disk, exactly as day 7 left it; what changes is how much of it goes up the wire, and what stands in for the part that does not.

## Three rules, and the whole feature follows

1. **Everything is kept.** Compression is a property of the *request*, never of the store. `data/conversations/<id>.json` still holds every message ever sent, failed turns included, and turning compression off (or on, or up, or down) does not touch a single one of them.
2. **The window is a setting.** How many messages go up verbatim is a number you choose per conversation — 2 to 200 — stored with the chat like the model and the temperature, not a constant in the source.
3. **The summary lives beside the history, not in it.** It is not something anyone said. It is written down under its own key, with a note of how far through the transcript it reaches, and it is inserted into the request at send time — one system message, between the role and the window.

## What a compressed request looks like

```
messages: [
  { role: "system",    content: "You are a helpful assistant." },      ← who it is
  { role: "system",    content: "Summary of the earlier part of this   ← 46 messages
                                 conversation… : The user is called
                                 Maksim and is working through…" },
  { role: "user",      content: "…" },                                 ┐
  { role: "assistant", content: "…" },                                 │ the window:
  { role: "user",      content: "…" },                                 │ the last 8
  { role: "assistant", content: "…" },                                 ┘ messages
  { role: "user",      content: "and what was my name again?" }        ← the question
]
```

The summary sits **after** the system prompt and **before** the window, because it describes what came before those messages and reading it afterwards would put the conversation in the wrong order. It is a `system` message rather than an `assistant` one for the same reason the system prompt is: it is a statement *about* the conversation, not a turn in it. And it is labelled — "no longer included verbatim, treat it as established fact" — so the model can tell a compressed account of the past from the four exchanges it is being shown in full.

The one invariant underneath all of it: **every message is in the summary or in the window, and never in both.** `Summary.covers` is an index into the replayed transcript, the window starts at exactly that index, and the two cannot overlap by construction.

## When the summary is written — and why that is the whole economy

Naively, "summarise the old messages" means one extra API call per turn, which costs more than it saves and makes the feature a lie. So the summary is written **once per overflow**, not once per message:

```
turn 21  window full, nothing older      →  no compression, no extra call
turn 22  2 messages fall outside         →  compress those 2, store it      ← paid
turn 23  the summary already reaches     →  reuse it as it stands
         the cut
turn 24  2 more fall outside             →  fold those 2 into the summary   ← paid
```

Each compression only reads what has appeared *since the last one* — `covers` is what makes that possible — and the result replaces the paragraph it was built from. A hundred-turn conversation therefore buys a handful of summaries, and every turn after the first overflow sends a paragraph instead of a hundred messages.

What it costs is not hidden either. Compression tokens are counted separately in the usage panel, under `compression`, and are deliberately **not** folded into the chat total:

```
COMPRESSION · 3 SUMMARIES
  requests · prompt                     6,200
  answers · completion                    900
  ───────────────────────────────────────────
  total                                 7,100
```

That is money spent on the app's own bookkeeping rather than on an answer, and the only comparison worth making is between this number and what not sending those 46 messages saves on every turn since. Folding it into the chat total would hide exactly that.

The summarisation request is also written to the debug panel, labelled `CONTEXT COMPRESSION · SUMMARISING 46 MESSAGES`, with its own request body, response and token count. A call made on your behalf, and billed to you, that you did not type should not be invisible.

## When it fails

A compression is one HTTP request, and requests fail. When one does:

- **the turn still goes.** The question is sent with the window and whatever summary was already stored, so the answer arrives.
- **nothing is written down.** No half-summary, no placeholder. A conversation with no summary is one that has not been compressed yet, which is true; a made-up one would be wrong forever, and quietly, since it would go into every request from then on.
- **the strip says so**, in the readout under the numbers: *the last compression failed; that turn was sent with the window only.*

The same restraint applies to reading the response: if the summariser returns nothing but reasoning, that is not used as a summary. `Agent.extract_answer` falls back to the reasoning trace because it has to put *something* in a chat bubble; `compaction.extract_summary` refuses to, because what it is writing goes into the context of every future request.

## Two kinds of forgetting

The summary has its own undo, and it is deliberately not the same button as day 7's:

| | Messages | Summary | What it is for |
|---|---|---|---|
| **Forget summary** | kept | gone | the summary came out wrong, or the window changed — rebuild it from the transcript |
| **Clear context** | gone | gone | start this chat over |
| **✕** on a compare column | gone | gone | the column is finished with |

Clearing the context takes the summary with it, and has to: it is a description of those exact messages, and a description that outlived the thing it described would be the one way this app could still remember something you explicitly told it to forget.

## Where the controls are

In the single chat, **⚙ Settings** now opens on two of them:

```
Context window
[   8   ] messages sent verbatim
[x] Summarise everything older
```

In the compare view they are a seventh field in each column's own settings form, which means two columns can differ *only* in how much context they carry — same model, same temperature, one with a window of 4 and one with 40 — and the answers are the comparison. The header line says which is which: `deepseek-v4-flash · T 1.0 · reasoning: low · text · ctx 8+summary`.

With the checkbox **off**, day 7 is back exactly as it was: the window is a hard cut and everything older is simply not sent. That is still the default, because compression costs an extra request and a feature that spends money should be switched on deliberately.

---

Day 8, underneath all of this, is what makes it possible to tell whether any of it worked.

Under every chat, between the transcript and the message box, one folded-away line:

```
USAGE                                                                    ▸
```

Click it, and it opens on what the API actually reported:

```
LAST TURN
  request · prompt                      1,204
  of which served from cache            1,024
  answer · completion                     252
  of which thinking                        96
  ───────────────────────────────────────────
  total                                 1,456
  Thinking is billed as output and never appears in the answer on screen.

THIS CHAT · 12 TURNS
  requests · prompt                     9,900
  of which served from cache            6,272
  answers · completion                  2,580
  of which thinking                     1,320
  ───────────────────────────────────────────
  total                                12,480
  The whole replayed conversation is sent again on every turn, which is why
  the prompt total grows faster than the chat does.
```

## Every number here was reported by DeepSeek

There is exactly one honest source for a token count, and it is the `usage` object that comes back with every answer:

```json
"usage": {
  "prompt_tokens": 1204,
  "completion_tokens": 252,
  "total_tokens": 1456,
  "completion_tokens_details": { "reasoning_tokens": 96 },
  "prompt_cache_hit_tokens": 1024
}
```

It is the provider's own reckoning, it is what the bill is written from, and it is exact. The app reads it, stores it, and adds it up. It does not count characters, does not ship a tokenizer, and does not estimate: a guess sitting next to the real number is just a second, worse number, and the whole point of showing a count is to be able to trust it.

The price of that is honest too. A count exists only **after** a request has been made, so there is nothing to show for a message still being typed, and nothing at all for a turn that failed — and both are shown as nothing rather than as a zero. A row of zeroes would read as *these answers were free*, which is a different claim from *no answer has been paid for yet*.

## What each number means

| Row | Field | What it actually counts |
|---|---|---|
| **request · prompt** | `prompt_tokens` | the *entire* request: system prompt + replayed history + your new question. This is the number day 7 made grow. |
| of which served from cache | `prompt_cache_hit_tokens` | the part of that prompt DeepSeek served from its own cache, at a fraction of the price. It grows on its own as a chat gets longer, because the replayed prefix stops changing — the one figure here that gets *better* with a long conversation. |
| **answer · completion** | `completion_tokens` | everything the model produced for this turn. |
| of which thinking | `completion_tokens_details.reasoning_tokens` | what it spent *reasoning*. DeepSeek thinks before answering by default, those tokens are billed as output, and they never appear in the bubble on screen — which is how a two-word reply comes to cost hundreds of tokens. |
| **total** | `total_tokens` | prompt + completion, as the provider reports it. |

Only one figure in the whole panel is derived rather than reported: the text actually written is `completion_tokens − reasoning_tokens`, because the API gives the total and the thinking and what you see is the difference. It is used to colour the split, not shown as a row of its own.

### Last turn vs. this chat

The two halves answer different questions, and the second is the one worth having:

- **Last turn** is what the exchange you just had cost.
- **This chat** is the same fields summed over every turn in the conversation (`tokens.sum_usage`). It is *not* the size of the transcript — it is the sum of **every copy of the transcript that has been uploaded**, one per turn. A twelve-turn chat has paid for its own history twelve times, because the API is stateless and has to be sent all of it again every time.

That second number is day 8's actual finding, and it is what the context window is defending against — crudely on day 7, and with the summary behind it since day 9.

There is a third section when a chat has been compressed, **Compression**, counting the summarisation requests: separate from both of the above, because it is what the app spent on its own bookkeeping rather than on any answer. Set against the prompt column it is the only honest way to tell whether compressing this conversation was worth doing.

## Where the counts are kept

A `usage` block is written down with the answer it paid for — on the assistant message, in the conversation's JSON file:

```json
{ "role": "assistant", "content": "OK", "ts": "…",
  "usage": { "prompt_tokens": 230, "completion_tokens": 90, "total_tokens": 320,
             "reasoning_tokens": 60, "content_tokens": 30, "cached_tokens": 128 } }
```

It has to be stored, because it is the one thing in this app that **cannot be worked out again later**. The messages can be re-read and the settings re-applied, but what a request cost was reported exactly once, in one response; if it is not saved, "what has this chat cost" resets to zero on every reload. A failed turn stores none, for the same reason it shows none.

`GET /api/conversations` — the single call the page is rebuilt from — therefore hands back each chat with its `usage` summary attached, and `POST /api/chat` returns the recomputed one alongside the answer. The browser asks for nothing extra: no polling, no second endpoint, no count of its own.

## More than one conversation

The single chat used to be exactly one conversation, on a fixed id. It is now a list of them, and the topbar says which one you are in:

```
LLM Agent            [ Привет! Как считаются токены…  ▾ ]     ⚙ Settings
                     ┌──────────────────────────────────┐
                     │ ▌Привет! Как считаются токены…   │
                     │   6 messages                   ✕ │
                     │  Расскажи про BPE-токенизацию    │
                     │   2 messages                   ✕ │
                     │  New chat                        │
                     │   empty                        ✕ │
                     ├──────────────────────────────────┤
                     │          + New chat              │
                     └──────────────────────────────────┘
```

A menu rather than a sidebar: the list is consulted when you switch and ignored the rest of the time, and a permanent column would spend width on it all day.

**Chats are named by their opening line**, and by nothing else. There is no title field, nothing generated, nothing to type — the first thing you said is both the most recognisable label a conversation can have and the only one that is certainly true of it. The preview is read straight off the first user bubble, so it cannot drift from what is on screen: clear a chat's context and it goes back to being *New chat*, because that is now what it is.

**Nothing new was needed on the server.** A chat in this list is a conversation record with `view: "single"` — the same thing a compare column has been since day 7, differing only in the view it belongs to. `PUT` creates one, `DELETE` forgets it, `GET /api/conversations` lists them, and `/api/chat` already took a `conversation_id`. The whole feature is the browser mounting one of them at a time.

- **+ New chat** registers an empty conversation immediately, so a chat you created and did not use is still there after a restart.
- **✕** deletes one for good. Deleting the last one starts a fresh one rather than leaving nowhere to type.
- The first chat ever created keeps the id `single`, so `curl -d '{"message": "..."}'` still lands somewhere the browser can see — and the conversation day 7 left behind is still the one that comes back.

One conversation is mounted at a time, and the others are held off the page rather than hidden in it: `#chat-inner` contains exactly one composer and `#debug-panel` exactly one log, as they did when there was only ever one. Each chat keeps its own transcript, its own debug log and its own usage panel while it waits.

**Which chat you were last in** is remembered in `localStorage`, next to whether the debug panel was open. That is the day-7 line, unmoved: every chat in the menu exists on the server, and *which one is on screen* is a preference of this screen. Open the app in a second browser and you get the same conversations, at whichever one that browser was looking at.

## Also on day 8

While adding all this, a race from day 7 surfaced: conversation records were created, updated and deleted with fire-and-forget `fetch`es, and restored columns are ordered by *when the server created them*. Two registrations in flight could come back swapped, and a column closed right after being added could have its `DELETE` overtaken by its own `PUT` — deleting the record and then immediately recreating it.

Record writes now go through one promise chain in the client (`queued()` in `index.html`), so they arrive in the order they were issued. Messages deliberately do **not**: `/api/chat` is exactly what five panes are supposed to run in parallel.

## Day 7, still standing: where the context lives

The memory this readout is pricing. It had four plausible homes.

| Where | Survives a reload | Survives a restart | Who owns the truth |
|---|---|---|---|
| In the `Agent` object (keep it alive per chat) | yes | **no** | the process |
| In the browser (`localStorage`) | yes | yes | **the client** |
| **JSON files on the server** ← chosen | yes | yes | the server |
| SQLite / Postgres on the server | yes | yes | the server |

**Not in the agent.** The obvious first idea — keep one long-lived `Agent` per chat in a dict — gives you memory for exactly as long as the process lives, which is the one thing the brief rules out. Persistence is not a lifetime problem; it is a *writing-down* problem. So agents stay short-lived: still one per request, as in day 6. They just inherit a transcript now.

**Not in the browser.** This one is tempting, because `localStorage` is free and the browser is already drawing the transcript. But the transcript has two jobs: it is *drawn on screen*, and it is *replayed to the model*. Chat-completions APIs are stateless — every request carries the entire conversation — and in this app the agent is what assembles that request. Keeping the history client-side would mean the browser uploading the whole conversation with every message and the agent taking dictation from it: the client would own the context, the server would forget everything the moment the tab closed, and opening the same chat on a phone would show an empty screen. The division that survives scrutiny is **the browser owns what is on screen; the server owns what was said.**

**Files, not a database — for now.** One JSON file per conversation, written whole, replaced atomically. It is the smallest thing that answers the requirement and it stays legible: `cat data/conversations/single.json` is the entire debugging story. The costs are honest ones — every write rewrites the whole file, and concurrent writers need a lock (there is one; the compare view fires five requests at once). SQLite is the next step if this ever grows past one person on one machine, and the only file that would change is `store.py`: `load`, `save`, `delete`, `all` is the whole interface the rest of the app uses.

What stays in `localStorage`: whether the debug panel is open, and which tab you were on. Those are preferences of *this screen*, not parts of the conversation — which is exactly the line being drawn.

## The layers

One new file again. Each layer still knows only about the one below it.

```
static/index.html     the interface: draws what the server remembers
        │  GET  /api/conversations          (on load: rebuild the page)
        │  POST /api/chat                   {"message", "conversation_id", "settings"}
        │  DELETE /api/conversations/{id}/messages   (clear context)
        │  DELETE /api/conversations/{id}/summary  ★ (forget the compression)
server.py             transport, the introduction between a saved transcript
        │              and an agent, and — day 9 — the one place that decides
        │              what a request is allowed to carry
        ├───────────────────────────┬──────────────────────────┐
agent.py          the agent:        │ compaction.py  ★ NEW     │ store.py
        │  ★ summary=  role, params,│   where the cut falls,   │  ★ summary
        │              request,reply│   and the request that   │   conversations
        │                           │   compresses the offcut  │   as JSON files
tokens.py                  reads `usage` out of a response, and adds usage up
        │                  (imports nothing from the app)
llm_client.py         HTTP: POST /chat/completions
   DeepSeek
```

`compaction.py` sits next to `agent.py` rather than under it, and imports nothing from it. It is handed an `LLMClient` exactly as the agent is, knows nothing about conversations, files or the web app, and its policy half — `split(history, window, already_covered)` — makes no request at all: it is a pure function returning *(what to summarise, what to send verbatim, what the summary will then cover)*, which is why that decision can be tested without an API key.

The agent, for its part, still does not know that any of this happened. It is handed a list of messages and a string:

```python
Agent(client, config, history=window, summary="The user is called Maksim…")
```

and puts the string where a statement about the past belongs. Who wrote it, what it cost and how far it reaches are somebody else's business.

The day-7 wiring is unchanged, with one step in front of it — `server.py`:

```python
saved = store.load(conversation_id)
plan  = prepare_context(saved, settings, conversation_id)   # ← day 9
agent = Agent(client, config, history=plan.history, summary=plan.summary)
store.append_turn(conversation_id, message, reply.answer, usage=reply.usage)
```

`prepare_context` is the only function in the app where the shape of a request is decided by policy rather than by what was said, and it is the only one that can make an API call the user did not ask for. Both of those are reasons to keep it in one readable place.

### What changed in `agent.py`

One argument, and three lines in `build_messages`:

```python
agent = Agent(client, config, history=[…], summary="The user is called Maksim…")
agent.build_messages("and what was my name again?")
# [system prompt] + [summary] + [history] + [question]
```

Plus `context_messages`, which existed since day 7 and was a constant nobody could reach; it is now passed in from the conversation's settings.

## What a stored conversation looks like

`data/conversations/single.json`:

```json
{
  "id": "single",
  "view": "single",
  "settings": { "model": "deepseek-v4-flash", "temperature": 1.0, "...": "..." },
  "messages": [
    { "role": "user",      "content": "My name is Maksim...", "ts": "2026-09-12T12:14:07.881+00:00" },
    { "role": "assistant", "content": "OK",                   "ts": "2026-09-12T12:14:09.204+00:00",
      "usage": { "prompt_tokens": 230, "completion_tokens": 90, "total_tokens": 320,
                 "reasoning_tokens": 60, "content_tokens": 30, "cached_tokens": 128 } }
  ],
  "summary": {
    "text": "The user is called Maksim and is working through an advent challenge…",
    "covers": 46,
    "model": "deepseek-v4-flash",
    "compressions": 3,
    "updated_at": "2026-09-13T09:02:11.400+00:00",
    "usage":       { "prompt_tokens": 2100, "completion_tokens": 300, "...": "..." },
    "usage_total": { "prompt_tokens": 6200, "completion_tokens":  900, "...": "..." }
  },
  "created_at": "2026-09-12T12:14:07.881+00:00",
  "updated_at": "2026-09-12T12:14:09.204+00:00"
}
```

Day 8 adds the `usage` block, on the assistant message and nowhere else — that is the message it paid for. It is stored rather than derived because it is the one number here that cannot be worked out again from what is on disk.

Day 9 adds `summary`, beside `messages` and never inside it. `covers: 46` is the whole of the bookkeeping: the first 46 messages of the replayed transcript are what this paragraph speaks for, so the next compression starts at 46 and the window starts at 46. `compressions` and `usage_total` are what it has cost to keep up to date. Every file written before day 9 simply has no `summary` key and reads back as a conversation that has never needed one.

The single chat always uses the id `single`, so it is the same conversation on every reload and in every browser. Each compare column gets its own id when it is added and keeps it until it is closed.

**Settings are stored with the conversation**, not just the messages. A compare column with nothing said in it is still a column, and the parameters you just dialled into it are worth keeping — so adding a column, or moving one of its controls, saves it immediately rather than waiting for a message.

### Kept, but not replayed

Two things are stored and then deliberately left out of the request:

- **Failed turns.** When a call comes back 401, or times out, the error is written down with `"error": true` and redrawn after a reload — it is part of what you saw. But `Conversation.history()` skips error turns *and* the question that got them, so the model is never shown a hanging question, and never taught to apologise for an HTTP status.
- **Old turns.** Everything is kept on disk; only the last N messages are replayed, where N is the window (40 by default). A conversation grows without limit while a request is paid for by the token every single time, so the window is what stops a months-old chat from quietly costing more per message than a new one. DeepSeek's 1M-token window is not the binding constraint — the bill is. Day 8 is where that stops being an assertion in a README: `prompt_tokens` stops climbing once the window bites, and the usage panel is where you watch it happen. Day 9 is where the part that falls outside it stops being *lost*: switch compression on and those turns come back as a paragraph instead of as nothing.

The debug panel is **not** persisted. It is a log of HTTP calls this page made, not a record of the conversation, and it says so by emptying on reload.

## Clearing the context

**⚙ Settings → Clear context**, in the single chat's top bar. It shows how much is saved (`8 messages saved on the server.`), and clearing takes two clicks — the first arms the button, the second fires it. A `confirm()` dialog would be the usual answer, but it moves the question out of the panel it was asked in, and this one is asked inside a popover that a stray click closes.

In the compare view every column has its own **Clear context** button, at the foot of its own settings form — each column is its own conversation, so each is cleared on its own. It sits outside the parameter fields on purpose: the form is seven parameters, and a destructive button is not an eighth.

Four different ways of forgetting, kept distinct — the first throws away only what the app made *of* the conversation, and nothing anybody said:

| Action | Messages | Summary | The chat itself | Its settings |
|---|---|---|---|---|
| **Forget summary** | kept | gone | kept | kept |
| **Clear context** | gone | gone | kept | kept |
| **✕** on a compare column | gone | gone | gone | gone |
| Deleting `data/conversations/` | gone | gone | gone | gone |

Closing a column really does delete its conversation — otherwise it would reappear on the next reload, which is not what "close" means anywhere else.

## What happens when the page loads

One request: `GET /api/conversations`. Whatever comes back is what gets drawn — every single-view conversation into the menu (with the last one you were in mounted), and one compare column per saved column, in the order they were created, each with its own settings form filled in from the store. Nothing about *which* chats exist lives in the browser, so clearing site data costs you nothing, and the same chats come back in a different browser.

Restored messages are drawn above a divider — `2 messages restored` — because without it a reloaded page looks like a conversation you are already in the middle of, with nothing to say where "now" begins.

The compare view used to seed itself with two empty columns the first time it was opened. It still does, unless there are saved columns: whatever you left behind is what you get back, including none.

### Memory in the compare view

Day 6 argued that compare columns should *not* remember, since "the columns are only trustworthy if they differ by their parameters and nothing else". That argument survives one turn and no more: ask five columns the same question and each one answers differently, so from the second turn onward each column is carrying a different conversation — because each is carrying *its own* answer. That is what a chat is, and it is visible rather than hidden: the summary line still shows the parameters, and **Clear context** on each column is one click away when you want a clean comparison again.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- A DeepSeek API key ([get one here](https://platform.deepseek.com))

The server refuses to start without the key, and says so rather than failing on the first message.

### Installing uv

```bash
# macOS (Homebrew)
brew install uv

# or the official installer (any OS)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Setup

1. Go to this folder:
   ```bash
   cd day8-tokenomika
   ```
2. Install the pinned dependencies (creates a local `.venv`):
   ```bash
   uv sync
   ```
3. Create your `.env` file from the example and add your API key:
   ```bash
   cp .env.example .env
   ```
   Then open `.env` and replace the placeholder:
   ```
   DEEPSEEK_API_KEY=your_deepseek_api_key_here
   ```
   The `.env` file is git-ignored, so your key never gets committed. So are editor swap files (`*.swp`) — a swap of `.env` would carry its contents — and `data/`, which is where your conversations end up.

## Running

```bash
uv run server.py
```

Then open **http://127.0.0.1:8000**. Send a message, stop the server with `Ctrl-C`, start it again, reload the page: the conversation is still there, and the agent still knows what is in it.

Conversations are written to `data/conversations/` next to `server.py`. Point `CHAT_STORE_DIR` somewhere else to keep them elsewhere:

```bash
CHAT_STORE_DIR=~/chats uv run server.py
```

Two tabs at the top switch between the modes: **Single chat** and **Compare models**. The last tab you used is remembered across reloads.

### Single chat

Chats — each one an agent on its default `AgentConfig` (`deepseek-v4-flash`, the default system prompt, `temperature 1.0`, `reasoning_effort: low`, plain text), which is what makes it the plainest demonstration of the brief. The menu in the top bar switches between them and makes new ones; the ⚙ **Settings** popover next to it is where you can see how much the current one remembers and throw it away. The other setting there is **"Show debug panel"**. Click anywhere else, or press **Escape**, to close the popover.

The message box is a real `<textarea>`: paste or type text of any length, including line breaks. Press **Enter** to send, **Shift+Enter** for a line break. Directly above it, two folded strips: **Context**, which unfolds into what the next request will carry and the summary standing in for the rest, and **Usage**, into what the last turn and the whole chat have cost. The ⚙ **Settings** popover is where the window is set and compression is switched on.

### Compare models

Several chats side by side, up to **5**, for answering the only question that matters when tuning parameters: what actually changes when I change this?

- **+ Add chat** appends a column; the counter shows how many of the 5 slots are used. Columns split the width evenly, and below ~260px each the row scrolls sideways rather than squeezing them into slivers.
- **Every column is its own agent**: its own model, system prompt, stop sequences, response format, reasoning effort, temperature and — since day 9 — its own context window and compression switch; its own message box; its own transcript; and since day 7, its own saved conversation.
- The column header shows the model and a one-line recap of that column's parameters (`deepseek-v4-pro · T 0.0 · reasoning: max · text · ctx 40`) — the line you scan when comparing, so it stays visible with the settings folded away.
- **⚙ Settings** folds that column's parameter form in and out. A newly added chat opens with its settings visible. Its **Clear context** button is at the bottom of that form.
- **Debug** opens a request/response log *for that column only*.
- **✕** removes the column, and deletes its saved conversation.
- The box at the bottom sends **one prompt to every column at once**, each as its own parallel request, so a slow model never holds up the others.
- **Its own context strip**, which is what makes "the same chat with a window of 4 and a window of 40" a comparison you can actually run: same model, same temperature, different memory, and the answers are the difference.
- **Its own usage strip**, which makes the view a price comparison as well as an answer comparison: the same prompt, five parameter sets, five bills side by side. `reasoning: max` against `reasoning: off` is the one to try first — the answers may come out similar, and the `reasoning_tokens` will not.

Requests still carry their settings with them; what the server now keeps is the transcript, which is what makes a column the same column tomorrow. The columns still run genuinely in parallel — the handler is a plain `def`, so FastAPI runs it in its threadpool, and the store holds its lock only for the moment it writes a file.

### Debug panel

In single-chat mode, open **⚙ Settings** in the top bar and tick **"Show debug panel"** to open a third column on the right. For every message it logs:

- a one-line summary — model, temperature, **HTTP status**, the token split (`1,204 in / 252 out · 96 thinking · 1,024 cached`), latency, finish reason;
- the **HTTP call itself**: `POST https://api.deepseek.com/chat/completions → 200`, green on success, red on failure;
- the exact **request** body, the exact **response** body, and the **request headers** — each collapsible, each with a Copy button and the token count for that half of the exchange.

Since day 7 the request body is where you can watch the memory work: `messages` carries the replayed conversation ahead of your new question, which is also the honest picture of what it costs — the prompt token count grows with the conversation. Since day 8 the badge on that block says how much of it DeepSeek served from its cache (hover). Since day 9 it is also where you can see the compression: the summary as a system message near the top of `messages`, and — on the turns that had to write one — a second, labelled entry above the turn's own, `CONTEXT COMPRESSION · SUMMARISING 46 MESSAGES`, with the full transcript that was sent to be compressed and what came back.

The `Authorization` header is redacted to `Bearer ***` before it reaches the browser.

The bodies are wrapped to the panel's width rather than scrolled sideways. The panel logs every message for the current page session whether shown or hidden, so toggling it back on doesn't lose earlier entries; "Clear log" empties it without touching the conversation — and, unlike **Clear context**, without touching anything on the server.

## Tests

A headless check of the interface lives in `tests/ui-test.js`: it loads `static/index.html` into [jsdom](https://github.com/jsdom/jsdom) and drives it the way a user would — switching tabs, adding and closing compare columns, folding settings and debug panels, moving sliders, sending messages — then asserts what came out.

Day 7 adds a second act: the page is loaded **again** into a fresh jsdom against the same server, which is what a reload (or a restart — the conversations are in files, not in the process) looks like from the browser's side. Everything after that point asks one question: did what was on screen come back? It checks the restored transcripts, the restored columns and their order, the parameters in their settings forms, the two-click clear button, and that a closed column and a cleared chat stay gone on the next load — against both the DOM and the JSON files on disk.

```bash
npm install   # once, pulls jsdom
npm test
```

It starts its own server on port 8765 (override with `TEST_PORT`) and shuts it down afterwards, so it never collides with a dev server on :8000. It points that server's `CHAT_STORE_DIR` at a fresh temp directory, so it never reads or writes the conversations you have been having in the real app. **It cannot spend tokens:** the API key handed to that process is a dummy, and since `python-dotenv` does not override an already-set variable, a real key in `.env` is ignored. DeepSeek answers every call with a 401 — which is all these checks need, since they cover the plumbing, not model output. (It also makes the error-turn behaviour easy to assert: every stored answer in the suite is a failed one.) Nothing in the suite touches the network.

Day 8 adds a third act, in two halves. The first drives the usage strip on a live page, where every answer is a 401: that it is there, that it is folded away, that it opens — and that it then claims **no numbers at all**, because nothing was billed. The second plants a conversation on disk carrying two `usage` blocks — the one thing a dummy API key can never produce — and loads the page against it, checking that the last turn's request, answer and thinking come back, that the chat totals are the *sum* of both turns rather than a copy of the last one, and that the same figures come out of `/api/conversations`.

Day 9 adds a fourth, and it is the only act that looks at a **request body** rather than at the page: a conversation is planted on disk with twelve messages and a summary covering the first eight — the one thing, again, that a dummy key can never produce — and a message is then sent to it. What comes back is checked against the rule the whole feature rests on: the request is the system prompt, the summary as a labelled system message, exactly four messages verbatim, and the question; and not one of the eight messages the summary stands for appears in it twice. The same act switches compression off and watches the summary drop out of the request while staying on disk, drives a compression that **fails** (a dummy key makes that easy) and checks that the turn still goes and that nothing was written down that was never produced, then drives the strip on the page: the numbers, the summary text in full, and **Forget summary** — after which the store has no summary and still has all twelve messages.

What it does *not* cover: how anything looks. jsdom has no layout engine, so widths, wrapping and overflow are only checked as computed style values, never as rendered pixels.

To add a check, call `check("what it should do", <condition>, <detail>)` — a failure sets the exit code.

## Available models

- `deepseek-v4-flash` (default)
- `deepseek-v4-pro`
- `deepseek-v4-flash-vision-exp`

All three share a 1M token context window and a maximum output of 384K tokens (per the [DeepSeek pricing docs](https://api-docs.deepseek.com/quick_start/pricing)).

## Available system prompts

- `Default` — "You are a helpful assistant." (default)
- `Step-by-step reasoning` — asks the model to break the problem into numbered steps, reason through them, then give a final answer

Pick **"Custom…"** in the system prompt dropdown to type a prompt of your own.

## Notes on parameters

These are the fields of `AgentConfig`. Every one except `model` is optional, and an unset field is simply left out of the request body.

- **`stop`** — optional; up to 16 sequences. Leave empty to clear.
- **`response_format`** — only `text` (default) and `json_object` are supported by the DeepSeek API. When using `json_object`, you must also ask for JSON explicitly in your message — otherwise the model may generate whitespace until it hits the token limit (see the [DeepSeek API docs](https://api-docs.deepseek.com/api/create-chat-completion)).
- **Reasoning effort** — DeepSeek's models think before answering by default, even with nothing set: every response carries a `reasoning_content` field and spends billed `reasoning_tokens`. `off`, `low`, `high`, `max` map to the API's `reasoning_effort`, except `off`, which sends `reasoning_effort: "none"` — the one value that disables thinking entirely. Default here is `low`.
- **`temperature`** — a slider from `0.0` to `2.0` in `0.1` steps, defaulting to `1.0` (the API's own default). Low values make the model pick the most likely next token almost every time — focused, repeatable answers — while high values flatten the odds, which reads as more varied but less reliable. Always sent, even at the default, so the debug panel shows exactly what produced the answer. DeepSeek's [recommended values per use case](https://api-docs.deepseek.com/quick_start/parameter_settings), for reference when setting the slider:

  | Use case                | Temperature |
  |-------------------------|-------------|
  | Coding / math           | 0.0         |
  | Data analysis           | 1.0         |
  | Conversation            | 1.3         |
  | Creative writing        | 1.5         |

- **Context window** (`context_messages`) — how many messages are replayed verbatim, 2–200, default 40. Counted in messages rather than turns, because that is what a request is a list of. The server clamps anything outside the range, so `curl` cannot talk its way past it.
- **Summarise older messages** (`context_compression`) — off by default. On, everything outside the window is compressed into a stored summary and sent in its place; off, it is simply not sent. Either way the messages themselves stay on disk. Switching it on costs one extra request per overflow, and the usage panel reports what those came to, separately from the turns.

**Note on response length:** there is no reliable API parameter to force an exact response length — `max_tokens` is only a hard cutoff that truncates mid-sentence if hit, not a length target. To control how long the answer is, ask for it explicitly in your message (e.g. "in 2-3 sentences", "under 50 words").

## API

Everything the frontend uses, in case you want to drive it from `curl`.

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/config` | models, prompts, limits, defaults |
| `GET` | `/api/conversations` | every saved chat, oldest first — `view` says which list it belongs to |
| `GET` | `/api/conversations/{id}` | one saved chat |
| `PUT` | `/api/conversations/{id}` | create it / update its settings, messages untouched |
| `DELETE` | `/api/conversations/{id}` | forget it entirely |
| `DELETE` | `/api/conversations/{id}/messages` | clear the context, keep the chat |
| `DELETE` | `/api/conversations/{id}/summary` | forget the summary, keep every message |
| `POST` | `/api/chat` | ask a question in a conversation |

```bash
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message": "What is my name?", "conversation_id": "single"}' | jq .answer
```

What a chat has cost — the same numbers the strip draws:

```bash
curl -s localhost:8000/api/conversations | jq '.conversations[] | {id, usage: .usage.total}'
```

Every conversation the API hands back carries a `usage` summary (`last` and `total`), and `/api/chat` returns the recomputed one alongside its answer — read *after* the turn has been stored, so the readout describes the conversation as it now is: one turn longer, and one turn more expensive to continue.

What a chat currently remembers, and what it has been compressed into:

```bash
curl -s localhost:8000/api/conversations/single | jq '.context'
```

```json
{
  "compression": true,
  "window": 8,
  "messages": 54,
  "replayable": 54,
  "summary": { "text": "The user is called Maksim…", "covers": 46, "compressions": 3, "...": "..." }
}
```

Every conversation the API hands back carries this `context` block alongside its `usage` one, and `POST /api/chat` adds a `context.sent` to it saying what the request that was just made actually carried — how many messages went up as themselves, how many the summary stood for, and whether one had to be written first. The summarisation exchange itself, when there was one, comes back under `debug.compaction` in exactly the shape an ordinary turn's debug block has.

To compress a long chat by hand, or to rebuild a summary you did not like:

```bash
curl -s -X DELETE localhost:8000/api/conversations/single/summary | jq '.context.summary'
# null — and every message is still there
```

`conversation_id` defaults to `single`, so a bare `{"message": "..."}` lands in the single chat — the same conversation the browser is looking at. Ids become file names, so they are restricted rather than escaped: letters, digits, `-` and `_`, up to 64 characters. Anything else is a 400.
