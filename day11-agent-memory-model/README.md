# LLM Agent with three layers of memory, three ways of remembering a conversation, and a conversation that can fork

A small **agent** that takes a question, sends it to an LLM over plain HTTP, shows the whole exchange in a web GUI, keeps as many conversations as you like, remembers all of them across restarts and reports what that costs — and, since day 11, **remembers some things for longer than the conversation they were said in.**

Day 7 gave the agent a memory by replaying the transcript into every request. Day 8 put the price of that on screen: the API is stateless, the whole conversation goes up every time, and the bill grows with the square of the chat rather than with the message. Day 9 added the first answer to that — summarise the overflow — and, with it, one switch. Day 10 replaced the switch with a choice, and added a second, unrelated one.

Day 11 asks a question none of them did. All of the above is about **one conversation**: how much of it fits, what stands in for the rest, which fork of it you are in. Start a new chat and every last bit of it is gone — the agent meets you again as a stranger, and you type your name for the fourth time. So this day is about the other axis: **how long a thing stays true, and who is allowed to see it.**

```
CONTEXT                                                      sticky facts ▸
```

```
NEXT REQUEST
  sent verbatim · window                    8
  carried as facts                          4
  dropped · only the facts remain          46
  ──────────────────────────────────────────
  kept on the server                       58

FACTS · 4 LINES SENT WITH EVERY REQUEST
  goal                   port the day-7 store to sqlite
  constraint.language    answer in russian
  decision.schema        one table per conversation
  preference.style       no apologies, no preamble
  updated 29 times · 3,140 tokens to maintain · by deepseek-v4-flash
  [ Forget facts ]
```

```
BRANCH   [ main 54 ]  [ plan A 62 ]  [ plan B 58 ]     ⚑ Checkpoint   ⑂ Branch…
```

**Nothing is deleted, and nothing is copied.** The transcript stays whole on disk, exactly as day 7 left it. What changes is which part of it goes up the wire, what stands in for the part that does not — and, now, *which* transcript it is.

## Two decisions, not four

The exercise asks for three strategies and a switch. They are not three of a kind, and pretending otherwise is the quickest way to build the wrong thing:

- **Sliding window**, **sticky facts** and **rolling summary** all answer the same question — *how much of this conversation goes into the next request, and what stands in for the rest.* They are mutually exclusive. One selector, three values.
- **Branching** answers a different question — *which conversation is this?* It composes with all three: every branch is subject to whichever strategy the chat is set to. It lives in the store, not in the request.

So: one dropdown, and a branch bar. What follows is each of them.

## Three layers, and what actually separates them

A layer is not a kind of data. Sorting facts into "profile", "decisions" and "knowledge" sounds like a memory model and is not one, because nothing about those words tells you when to stop sending something. What separates a layer is the answer to a harder question — **when should this be forgotten?** — and, right behind it, **who put it there?**

| | Scope | Lives until | Who writes it | Where it is kept |
|---|---|---|---|---|
| **Short-term** | this branch of this chat | the chat ends | the agent, automatically | `data/conversations/<id>.json` |
| **Working** | one task, across every chat about it | the task is closed | **you**, with `/task` | `data/tasks/<id>.json` |
| **Long-term** | everything, always | you delete it | **you**, with `/remember` | `data/memory/long_term.json` |

The first row is the whole of day 10. That is worth saying plainly, because it is what keeps this day from being a rewrite: a sticky-fact block outlives the window but dies with the conversation, so by the test above it *is* short-term memory, and `window` / `facts` / `summary` are three ways of building it. Nothing in `strategies.py`, `facts.py` or `compaction.py` changed.

### A task is a record, not a tag

The middle layer needs a thing to be scoped to, and it cannot be the chat — if it were, there would be two layers, not three. So a **task** is a record of its own, with a title and a status, and several chats can join one. That is the point: you work on something for a week, in six conversations, and the sixth starts where the fifth left off.

Closing a task is what turns a scope into a *lifetime*. Nothing is deleted — the record stays, its memory stays readable in the panel — but it stops being sent, because a finished piece of work's decisions are exactly what should stop turning up in conversations about something else. Reopening is free, which is what makes closing something you will actually do.

### Writing is explicit, and it is three words long

You remember something by typing it where you were already typing. Press `/` in the message box and the list comes up above it:

```
  /task …      Remember this while the current task is open
  /remember …  Remember this in every conversation, until you delete it
  /always …    Remember it, and send it with every request whatever is asked
```

```
/remember the billing day is the 5th
```

The command never reaches the model — it is caught before the request, so filing something costs nothing — and it leaves a receipt in the transcript rather than a message, because nobody said it and it is never replayed.

**There are no keys to invent.** Keys still exist on disk, because filing the same thing twice has to *correct* it rather than leave the prompt holding two lines that disagree — but that is an identity, not a label, so it is derived from what you wrote (`memory.slug`). Retype a note and it corrects the old one; word it differently and you get a second entry, visible in the panel and one click to remove. That asymmetry is on purpose: a duplicate you can see beats an overwrite you cannot.

The agent still has an opinion. After each answer it reads the turn and says what *it* would file and in which layer, and those suggestions wait in the **Memory** panel behind a badge until you accept one, move it to the other layer, or dismiss it. It is the one request in this app that costs money without making any reply better, so it has its own switch in **⚙ Settings**.

Every filed item records which conversation it came out of. That is not bookkeeping for its own sake: an item that turns up weeks later in a chat about something else, next to notes filed by a different task, is unprunable without it — and a memory layer nobody dares prune stops being a memory and becomes a liability.

### Reading is the hard half

Three layers that are all sent in full on every request are one layer with three headings. So the long-term layer is filtered against the question being asked — but filtering on relevance alone has a hole:

```
you ask:    what would be a better name for this function?
on file:    constraint.language = answer in Russian
overlap:    none. The fact stays home. The agent answers in English.
```

The filter worked and the memory broke anyway, because notes come in two sorts. Some are rules about the *shape* of any answer — there are few of them and they bear on every question ever asked, so scoring them for relevance is a category error. Everything else is subject matter. So:

- **always sent:** anything starred — set by `/always`, or by clicking the ☆ next to any note;
- **scored:** the rest, by word overlap with the question, top-K;
- **and if nothing scores at all:** the three most recently filed, rather than nothing.

The star used to be a key prefix. `constraint.*` and `preference.*` still mean "this is a rule" — the extractor and the proposer are told to use them, and anything filed under one is starred automatically — but a rule that only fires for keys *the agent* happened to invent is not a rule. Nobody types `constraint.language`. So there is one flag now, it is visible, and one click sets it.

That last rule earns its place. Keys are English by convention (`decision.schema`) and this app is mostly used in Russian, so "напомни схему" scores zero against `decision.schema` however the words are cut. Matching is done on four-character stems, which is enough for Russian inflection (`таблиц|а` → `табл`) and deliberately not enough to cross alphabets — `деплой` will never find `knowledge.deploy`, and fixing that needs a dictionary rather than a heuristic. The fallback is what keeps that limitation from looking like a broken memory.

### Where the layers sit in a request

```
[system]  the role
[system]  LONG-TERM — what you know about this user            ← widest scope
[system]  WORKING — what this task has established
[system]  summary | facts — what fell out of this chat's window
          the window, verbatim                                 ← narrowest
[user]    the question
```

Widest first, because that is the order of narrowing and it puts each block *after* everything it might need to contradict. A task that has settled on English is stated after a user who generally prefers Russian; this conversation's own facts are stated after both. Models weight later context more heavily, so the narrower scope wins by construction rather than by a precedence rule written in prose that something would have to enforce.

They are separate system messages rather than one joined block so that the request in the debug panel shows which layer contributed what. Joining them would save a few tokens and make the day unprovable.

### Checking it

Three chats is the whole demonstration:

1. Chat **A**, in task **T**. Type `/task one row per message`, then `/always answer in Russian`.
2. Chat **B**, also in task **T** — a conversation that never heard either said. Ask about the schema: it knows. *The working layer crossed a conversation boundary.*
3. Chat **C**, in task **U**. Ask about the schema: it does not know. Ask anything at all: it still answers in Russian. *Scope is real in both directions.*
4. Close task **T**, go back to chat **B**: the working block is gone from the request, and every item is still in the panel. *Lifetime is real too.*

## The three strategies

These are the short-term layer's internals — three ways of deciding what a *conversation* contributes to a request, unchanged from day 10. Every one of them starts from the same cut. The window — the last N messages — is sent verbatim, always. What they disagree about is everything in front of it.

| | In front of the window | Extra requests | What it is good at | What it loses |
|---|---|---|---|---|
| **Sliding window** | nothing. Dropped. | **none** | being free, and being obvious | everything older than N messages, silently, unless something says so |
| **Sticky facts** | a key-value block | **one per message**, on a prompt that never grows | goals, constraints and decisions surviving indefinitely | how you got there — the reasoning, the wording, the detail |
| **Rolling summary** | one paragraph | **one per overflow** | narrative: what was tried, what happened | precision. A paragraph blurs; it cannot be corrected line by line |

`strategies.py` holds the names, the defaults and the one line of arithmetic all three share:

```python
def split(history, window):
    cut = len(history) - window
    return history[:cut], history[cut:]     # (what falls outside, what goes in)
```

### 1. Sliding window — the honest cheap one

The whole strategy is that slice. The last N messages go up; everything older is not in the request, and the chat cannot answer questions about it. No second call, nothing stored, nothing to go stale.

The only thing this app adds is that it **says so**. A chat that forgets silently is the failure mode day 9 existed to fix, so the strip reports the loss as a number rather than leaving you to discover it:

```
  sent verbatim · window                    8
  dropped · not sent at all                46
```

### 2. Sticky facts — a key-value memory

A block of short `key: value` lines, kept up to date after every user message and sent in the window's place:

```
goal: port the day-7 store to sqlite
constraint.language: answer in russian
decision.schema: one table per conversation
preference.style: no apologies, no preamble
```

Four lines. They survive a hundred messages without being rewritten, they are readable by the person whose conversation they describe, and — unlike a paragraph — **a wrong one can be deleted on its own.** That is the real argument for this over a summary: a summary is true or false as a whole, a fact block is true or false a line at a time.

Four decisions inside it, and each one is load-bearing:

**It returns a patch, not a rewrite.** The extractor answers with `{"set": {...}, "unset": [...]}` — what *changed* — rather than restating the block. A rewrite that quietly drops a fact looks identical to a correct one. A patch that drops a fact has to say `unset` out loud, and the debug panel shows it.

**It runs before the turn, not after.** The requirement is that facts are updated after each user message; doing it before the answer rather than after means the fact you have just stated is already in the block that answers you, instead of arriving one message late. It costs exactly the same either way.

**Its prompt does not grow.** The extractor is sent the current block, the previous turn and the new message. Three short strings at message four hundred exactly as at message four — never the transcript. That is what makes a per-message request affordable at all, and it is the precise inversion of the problem this whole day is about: the thing it replaces grows without limit, and the thing replacing it does not.

**It is capped.** At most 40 keys, each of them short, and re-setting a key moves it to the end — so when the block is full, the fact that has gone longest without being mentioned is the one that makes way. A fact block allowed to grow without limit is just the transcript again, with worse recall.

The prompt behind it is written to make the model **decline**. Most messages in a conversation establish nothing durable, and `{"set": {}, "unset": []}` is stated in it as the normal, expected answer.

### 3. Rolling summary — day 9, unchanged

Everything outside the window compressed into one paragraph, written **once per overflow** rather than once per turn (a stored `covers` index is what makes that possible) and re-used until the next one. `compaction.py` is exactly as day 9 left it; it moved from a `bool` to one of three values on a dropdown and nothing else about it changed.

## What each request actually looks like

```
messages: [
  { role: "system", content: "You are a helpful assistant." },          ← who it is
  { role: "system", content: "Established facts about this              ← the strategy's
                              conversation… goal: port the store…" },     block, if any
  { role: "user",      content: "…" },                                  ┐
  { role: "assistant", content: "…" },                                  │ the window:
  { role: "user",      content: "…" },                                  │ the last 8
  { role: "assistant", content: "…" },                                  ┘ messages
  { role: "user", content: "and what was my name again?" }              ← the question
]
```

The block sits **after** the system prompt and **before** the window, because it accounts for what came before those messages and reading it afterwards would put the conversation in the wrong order. It is a `system` message rather than an `assistant` one for the same reason the system prompt is: it is a statement *about* the conversation, not a turn in it. And it is labelled — *"carried forward from earlier messages that are no longer included verbatim"* — so the model can tell a stand-in for the past from the four exchanges it is being shown in full.

**Never two of them.** The facts block and the summary are two descriptions of the same missing messages, and a request carrying both would be asking the model to work out which had gone stale. `Agent.build_messages` enforces it with an `elif`, and the strategies are exclusive upstream of that.

Two invariants hold whichever one is on: **every message is in the block or in the window, never in both** (`Summary.covers` is an index into the replayed transcript, and the window starts at exactly that index), and **nothing that goes into a request was invented** — a failed extraction or compression sends what was already stored, never a placeholder.

## When it fails

Both memory strategies make an HTTP request the user did not ask for, and requests fail. When one does:

- **the turn still goes.** The question is sent with the window and whatever block was already stored, so the answer arrives.
- **nothing is written down.** No half-summary, no guessed patch. A conversation with no summary is one that has not been compressed yet, which is true; a made-up one would be wrong forever, and quietly, since it would go into every request from then on.
- **the strip says so**, in the readout under the numbers: *the last fact update failed; that turn was sent with the facts as they already stood.*

The same restraint applies to reading the response. `facts.parse_patch` forgives exactly one thing — a bare object of facts where the patch envelope was asked for, because that is unambiguous and it is the mistake that actually happens — and treats anything that is not JSON as an error. `compaction.extract_summary` refuses to fall back to the model's reasoning trace, though `Agent.extract_answer` does, because one of them is filling a chat bubble and the other is writing into every future request.

## Branching: a conversation that can fork

The other half of day 10, and it has nothing to do with what a request carries.

```
BRANCH   [ main 54 ]  [ plan A 62 ]  [ plan B 58 ]     ⚑ Checkpoint   ⑂ Branch…
```

The flow the exercise asks for, end to end:

1. **Save a checkpoint.** Hover any message and press ⚑, or press **⚑ Checkpoint** to mark the end of the branch you are in. A checkpoint is drawn in the transcript where it actually is — forking happens *somewhere*, and a bookmark that lived only in a menu would leave you to work out where.
2. **Fork it twice.** **⑂ Branch from here**, on the same checkpoint, twice. Two branches, one shared past.
3. **Talk in each.** Whatever you send lands in the branch on screen. The two diverge.
4. **Switch.** Click a chip. The transcript is redrawn from the other branch.

### Forking copies nothing

A branch stores **only the messages said in it**, plus the id of its parent and `forked_at` — how much of the parent it inherits:

```
main      [ m1 m2 m3 m4 m5 m6 | m7 m8 m9 m10 m11 m12 ]
                              ↑ checkpoint · after 6 messages
plan A                        └→ [ a1 a2 ]        shows 8, stores 2
plan B                        └→ [ b1 b2 ]        shows 8, stores 2
```

`Conversation.transcript()` walks the chain and rebuilds the prefix on demand. So forking a hundred-message conversation is a write of a few dozen bytes, and it is reasonable to fork on a whim — which is the only time anyone ever wants to.

The branch bar says both numbers: the count on a chip is what the branch *shows you*, and `/api/conversations/{id}` reports `own` next to it — what it actually costs to keep.

### Each branch has its own memory

`facts` and `summary` hang off the branch, not the conversation. Both are descriptions of a conversation's past, and two branches stop having the same past the moment they diverge; one shared block would end up describing whichever branch spoke last.

A fork **inherits a copy** of what its parent knew at the fork — the fact block outright, the summary only when `covers` still fits inside the messages the fork actually has, otherwise it is dropped and rebuilt. Spending does not come along: a fresh branch has made no requests of its own, and inheriting a total would double it the moment anyone added the two up.

### The rules that are refused rather than fudged

- **`main` cannot be deleted.** It is the trunk every other branch's past is made of.
- **Neither can a branch that has been forked from.** Its children inherit a past that would no longer be stored, and silently shortening two other conversations is not what anyone means by closing one. The ✕ simply is not drawn on those chips — the server decides that and the page draws what it is told.
- **Deleting a checkpoint leaves its branches alone.** They have their own record of where they started. A branch vanishing because a bookmark was tidied away would be the worst surprise in the app.

## Six kinds of forgetting, kept distinct

| | Messages | Facts / summary | Branches | Working layer | Long-term layer |
|---|---|---|---|---|---|
| **Forget facts** / **Forget summary** | kept | gone | kept | kept | kept |
| **✕** on a branch chip | that branch's only | that branch's only | one gone | kept | kept |
| **Clear context** | gone | gone | gone | kept | kept |
| **✕** on a compare column | gone | gone | gone | kept | kept |
| **Close a task** | kept | kept | kept | kept, not sent | kept |
| **✕** on an item | kept | kept | kept | that one gone | that one gone |

The last two rows are day 11's, and the first four gained a column that says *kept* twice. That is deliberate: clearing a chat is a statement about that chat. Something you moved into another layer on purpose is not part of it any more, and "clear this conversation" is not a statement about the task or about you.

The first throws away only what the app made *of* the conversation, and nothing anybody said. It matters more for facts than it did for summaries: a summary is rebuilt from the transcript on the next overflow, so a bad one has a limited life, while a fact is carried forward untouched for as long as it stands and is never re-derived from what was actually said. A wrong one does not fade. It compounds.

Clearing the context takes the branches with it, and has to: a fork of a conversation that no longer exists is not a conversation anyone can read.

## Where the controls are

Day 11 adds one button to the topbar, next to the settings, with a badge on it when the agent has something waiting to be ruled on:

```
Memory  ②

  Task  [ Port the store to SQLite  ▾ ]  [ + New ]  [ Close ]

  SUGGESTED BY THE AGENT (2)
    preference.style: no preamble          said twice
    [ Working ]  [ ✓ Long-term ]  [ Dismiss ]

  WORKING                        YOU, WITH /task            1 of 3 sent last turn
    one row per message
    single · 2026-09-19                                                    ☆  ✕

  LONG-TERM                      YOU, WITH /remember        2 of 7 sent last turn
    answer in Russian  ALWAYS
    single · 2026-09-14                                                    ★  ✕

  To remember something, type /task or /remember in the message box.

  SHORT-TERM           THE AGENT, AUTOMATICALLY
    Shown in the context strip under the chat, not here.
```

The **✓** marks the layer the agent proposed, so accepting its judgement and overruling it are visibly different acts. The **★** is the whole of the always-send rule: one click, and that note rides on every request whatever the question. Each layer's label, scope and — the column the day turns on — *who writes to it* come from `GET /api/config`, so a layer cannot exist in the panel and not on the server. The two numbers are the ones the exercise asks you to compare: what has accumulated, and what the last request actually carried.

The panel is where you *look* at memory and prune it. It is not where you write to it: writing happens in the message box, because that is where the thing worth remembering was just said.

The short layer is described by the context strip instead. Repeating a worse version of it here would give the two something to disagree about.

Writing is in the message box, so **⚙ Settings** gains only one thing: whether the agent is allowed to make those suggestions at all. It opens on three:

```
Context window
[   8   ] messages sent verbatim

Everything older
[ Sticky facts               ▾ ]
A key-value block of what matters — the goal, the constraints, the
decisions — is kept up to date and sent with the last N messages. One
small extra request per message you send, on a prompt that does not grow.
```

The options and their descriptions come from `GET /api/config`, so a strategy cannot exist in the form and not on the server.

In the compare view they are the seventh field of each column's own settings form, which means two columns can differ *only* in how they remember — same model, same temperature, one on the window and one on facts — and the answers are the comparison. The header line says which is which: `deepseek-v4-flash · T 1.0 · reasoning: low · text · ctx 8 · facts`.

The branch bar sits above every transcript, in both views, whether or not the chat has ever been forked: the first checkpoint has to be reachable from somewhere, and a control that appears only once you have used it is not discoverable.

**Sliding window is the default**, because it makes no extra request, and a feature that spends money should be switched on deliberately.

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

Two new files this time, and one refactored. Each layer still knows only about the one below it.

```
static/index.html     the interface: draws what the server remembers
        │  GET  /api/conversations          (on load: rebuild the page)
        │  POST /api/chat                   {"message", "conversation_id", "branch", …}
        │  DELETE /api/conversations/{id}/messages     (clear context)
        │  DELETE /api/conversations/{id}/summary      (forget the compression)
        │  DELETE /api/conversations/{id}/facts      ★ (forget the facts)
        │  POST/DELETE   …/checkpoints, …/branches  ★ (fork, switch, abandon)
server.py             transport, the introduction between a saved transcript
        │              and an agent, and the one place that decides what a
        │              request is allowed to carry
        ├──────────────┬──────────────┬──────────────┬──────────────┐
agent.py      strategies.py ★NEW  facts.py ★NEW  compaction.py   store.py
  the agent:   the three names,    the key-value   the summary,    ★ branches,
  ★ facts=     the default, and    memory and the  and where the   ★ facts,
  ★ summary=   the one line of     patch that      cut falls        conversations
  role,params  arithmetic all      updates it                       as JSON files
  request,rep  three share
        │
tokens.py                  reads `usage` out of a response, and adds usage up
        │                  (imports nothing from the app)
llm_client.py         HTTP: POST /chat/completions
   DeepSeek
```

`strategies.py`, `facts.py` and `compaction.py` all sit next to `agent.py` rather than under it, and none of them imports it. The two that make requests are handed an `LLMClient` exactly as the agent is, and know nothing about conversations, files or the web app. `strategies.py` makes no request at all — it is names, a default, and a pure `split()` — which is why the decision underneath all three can be read in one screen and tested without an API key.

The agent, for its part, still does not know which strategy it is running under. It is handed a list of messages and at most one string:

```python
Agent(client, config, history=window, facts="goal: port the store to sqlite\n…")
Agent(client, config, history=window, summary="The user is called Maksim…")
```

and puts the string where a statement about the past belongs. Who wrote it, what it cost and how far it reaches are somebody else's business — which is what keeps adding a fourth strategy a change to `strategies.py` and not to the agent.

The day-7 wiring is unchanged, with two steps in front of it — `server.py`:

```python
saved  = store.load(conversation_id)
branch = req.branch or saved.active_branch                          # ← day 10
plan   = prepare_context(saved, settings, conversation_id, message, branch)
agent  = Agent(client, config, history=plan.history,
               summary=plan.summary, facts=render(plan.facts))
store.append_turn(conversation_id, message, reply.answer,
                  usage=reply.usage, branch=branch)
```

`prepare_context` is the only function in the app where the shape of a request is decided by policy rather than by what was said, and the only one that can make an API call the user did not ask for. Both of those are reasons to keep it in one readable place; day 10 splits its body into three named functions — `plan_with_facts`, `plan_with_summary`, and the two-line sliding window — but leaves the entry point exactly where day 9 put it.

### What changed in `agent.py`

One more argument, and one `elif`:

```python
agent = Agent(client, config, history=[…], facts="goal: port the store to sqlite")
agent.build_messages("and what was my name again?")
# [system prompt] + [facts] + [history] + [question]
```

The `elif` is the whole of "never two blocks at once" — the guarantee is made upstream, in mutually exclusive strategies, and enforced here.

## What a stored conversation looks like

`data/conversations/single.json`:

```json
{
  "id": "single",
  "view": "single",
  "settings": { "model": "deepseek-v4-flash", "context_messages": 8,
                "context_strategy": "facts", "...": "..." },
  "messages": [
    { "role": "user",      "content": "My name is Maksim...", "ts": "2026-09-12T12:14:07.881+00:00" },
    { "role": "assistant", "content": "OK",                   "ts": "2026-09-12T12:14:09.204+00:00",
      "usage": { "prompt_tokens": 230, "completion_tokens": 90, "total_tokens": 320,
                 "reasoning_tokens": 60, "content_tokens": 30, "cached_tokens": 128 } }
  ],
  "facts": {
    "items": { "goal": "port the day-7 store to sqlite",
               "constraint.language": "answer in russian" },
    "model": "deepseek-v4-flash",
    "updates": 29,
    "updated_at": "2026-09-14T09:02:11.400+00:00",
    "usage":       { "prompt_tokens": 180, "completion_tokens": 22, "...": "..." },
    "usage_total": { "prompt_tokens": 2900, "completion_tokens": 240, "...": "..." }
  },
  "summary": { "text": "…", "covers": 46, "compressions": 3, "...": "..." },
  "branches": [
    { "id": "branch-2", "name": "plan A", "parent": "main", "forked_at": 6,
      "messages": [ { "role": "user", "content": "what if we kept JSON?", "...": "..." } ],
      "facts": { "items": { "...": "..." }, "updates": 0 },
      "created_at": "2026-09-14T09:31:02.118+00:00" }
  ],
  "checkpoints": [
    { "id": "cp-1", "branch": "main", "index": 6, "label": "before the fork",
      "created_at": "2026-09-14T09:30:44.002+00:00" }
  ],
  "active_branch": "branch-2",
  "created_at": "2026-09-12T12:14:07.881+00:00",
  "updated_at": "2026-09-12T12:14:09.204+00:00"
}
```

Day 8 adds the `usage` block, on the assistant message and nowhere else — that is the message it paid for. It is stored rather than derived because it is the one number here that cannot be worked out again from what is on disk.

Day 9 adds `summary`, beside `messages` and never inside it. `covers: 46` is the whole of the bookkeeping: the first 46 messages of the replayed transcript are what that paragraph speaks for, so the next compression starts at 46 and the window starts at 46.

Day 10 adds `facts` in the same position and for the same reason, and then does one thing worth pointing at: **`main` is written where messages have always been written.** The forks go in `branches`, the bookmarks in `checkpoints`, and a conversation that has never been forked — which is most of them — looks on disk exactly as it did on day 7. Every file days 7, 8 and 9 wrote is still a valid file: no `branches` key reads back as a conversation with one branch, no `facts` key as one that has never needed any, and `"context_compression": true` in an old settings block still means the rolling summary.

On the wire it is not quite the same file. `GET /api/conversations/{id}` overwrites `messages` with the **active branch's** transcript — the branch you are reading — and adds a `branching` block describing the tree. On disk that key is main's, because that is what makes the format compatible; in the browser it is whatever is on screen.

The single chat always uses the id `single`, so it is the same conversation on every reload and in every browser. Each compare column gets its own id when it is added and keeps it until it is closed.

**Settings are stored with the conversation**, not just the messages. A compare column with nothing said in it is still a column, and the parameters you just dialled into it are worth keeping — so adding a column, or moving one of its controls, saves it immediately rather than waiting for a message.

### Kept, but not replayed

Two things are stored and then deliberately left out of the request:

- **Failed turns.** When a call comes back 401, or times out, the error is written down with `"error": true` and redrawn after a reload — it is part of what you saw. But `Conversation.history()` skips error turns *and* the question that got them, so the model is never shown a hanging question, and never taught to apologise for an HTTP status.
- **Old turns.** Everything is kept on disk; only the last N messages are replayed, where N is the window (40 by default). A conversation grows without limit while a request is paid for by the token every single time, so the window is what stops a months-old chat from quietly costing more per message than a new one. DeepSeek's 1M-token window is not the binding constraint — the bill is. Day 8 is where that stops being an assertion in a README: `prompt_tokens` stops climbing once the window bites, and the usage panel is where you watch it happen. Days 9 and 10 are where the part that falls outside it stops being *lost* — as a paragraph, or as four lines of `key: value`, depending on the strategy. Under the sliding window it really is lost, and the strip says how much.

The debug panel is **not** persisted. It is a log of HTTP calls this page made, not a record of the conversation, and it says so by emptying on reload.

## Clearing the context

**⚙ Settings → Clear context**, in the single chat's top bar. It shows how much is saved (`8 messages saved on the server.`), and clearing takes two clicks — the first arms the button, the second fires it. A `confirm()` dialog would be the usual answer, but it moves the question out of the panel it was asked in, and this one is asked inside a popover that a stray click closes.

In the compare view every column has its own **Clear context** button, at the foot of its own settings form — each column is its own conversation, so each is cleared on its own. It sits outside the parameter fields on purpose: the form is seven parameters, and a destructive button is not an eighth.

See [Four kinds of forgetting](#four-kinds-of-forgetting-kept-distinct) above for what each button takes with it. Clearing the context resets a chat to a single empty `main` branch: the fork you made, the checkpoint you set and the fact block the app had built are all descriptions of messages that no longer exist.

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

The message box is a real `<textarea>`: paste or type text of any length, including line breaks. Press **Enter** to send, **Shift+Enter** for a line break. Directly above it, two folded strips: **Context**, which unfolds into what the next request will carry and whatever is standing in for the rest — the fact block, line by line, or the summary in full — and **Usage**, into what the last turn and the whole chat have cost. Above the transcript, the **branch bar**. The ⚙ **Settings** popover is where the window is set and the strategy chosen.

### Compare models

Several chats side by side, up to **5**, for answering the only question that matters when tuning parameters: what actually changes when I change this?

- **+ Add chat** appends a column; the counter shows how many of the 5 slots are used. Columns split the width evenly, and below ~260px each the row scrolls sideways rather than squeezing them into slivers.
- **Every column is its own agent**: its own model, system prompt, stop sequences, response format, reasoning effort, temperature and — since days 9 and 10 — its own context window and memory strategy, and its own branches; its own message box; its own transcript; and since day 7, its own saved conversation.
- The column header shows the model and a one-line recap of that column's parameters (`deepseek-v4-pro · T 0.0 · reasoning: max · text · ctx 40 · window`) — the line you scan when comparing, so it stays visible with the settings folded away.
- **⚙ Settings** folds that column's parameter form in and out. A newly added chat opens with its settings visible. Its **Clear context** button is at the bottom of that form.
- **Debug** opens a request/response log *for that column only*.
- **✕** removes the column, and deletes its saved conversation.
- The box at the bottom sends **one prompt to every column at once**, each as its own parallel request, so a slow model never holds up the others.
- **Its own context strip**, which is what makes "the same chat on facts and on the sliding window" a comparison you can actually run: same model, same temperature, different memory, and the answers are the difference. Ask five columns the same long conversation's worth of questions and the strip prices each strategy against the others.
- **Its own usage strip**, which makes the view a price comparison as well as an answer comparison: the same prompt, five parameter sets, five bills side by side. `reasoning: max` against `reasoning: off` is the one to try first — the answers may come out similar, and the `reasoning_tokens` will not.

Requests still carry their settings with them; what the server now keeps is the transcript, which is what makes a column the same column tomorrow. The columns still run genuinely in parallel — the handler is a plain `def`, so FastAPI runs it in its threadpool, and the store holds its lock only for the moment it writes a file.

### Debug panel

In single-chat mode, open **⚙ Settings** in the top bar and tick **"Show debug panel"** to open a third column on the right. For every message it logs:

- a one-line summary — model, temperature, **HTTP status**, the token split (`1,204 in / 252 out · 96 thinking · 1,024 cached`), latency, finish reason;
- the **HTTP call itself**: `POST https://api.deepseek.com/chat/completions → 200`, green on success, red on failure;
- the exact **request** body, the exact **response** body, and the **request headers** — each collapsible, each with a Copy button and the token count for that half of the exchange.

Since day 7 the request body is where you can watch the memory work: `messages` carries the replayed conversation ahead of your new question, which is also the honest picture of what it costs — the prompt token count grows with the conversation. Since day 8 the badge on that block says how much of it DeepSeek served from its cache (hover). Since day 9 it is also where you can see the memory being bought: the block as a system message near the top of `messages`, and — on the turns that had to write one — a second, labelled entry above the turn's own, with the request that produced it and what came back. `CONTEXT COMPRESSION · SUMMARISING 46 MESSAGES` for the summary, once per overflow; `STICKY FACTS · 2 SET · 1 REMOVED` for the fact block, on every single message. The second of those is deliberately loud: a call charged to you on every turn should be as visible as the answer it precedes.

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

Day 10 adds a fifth, in three parts. The first sends a message to a planted twelve-message conversation under each strategy in turn and reads the **request body** that came out: the sliding window is one system message and the last four, with the strip reporting eight dropped and no second request made at all; sticky facts is the same window with a labelled block in front of it, one `key: value` per line, and not one of the eight messages it stands for sent twice. The second drives an extraction that **fails** — a dummy key makes that easy — and checks that the turn still goes, that the block goes up exactly as it already stood, and that nothing was written down that was never produced. The third needs no model at all: a checkpoint is saved, forked **twice from the same position**, a message sent into each branch, and the suite asserts that both forks show eight messages while storing two, that `main` is still exactly twelve on disk, that switching gives back the other continuation intact, that a message lands in the branch on screen, and that the trunk cannot be deleted out from under its children. Then all of it again on the page, through the branch bar — including the checkpoint drawn in the transcript at the message it marks, and the whole tree coming back after a reload.

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
- **Everything older** (`context_strategy`) — `window` (default), `facts` or `summary`. What becomes of the messages outside the window: dropped, kept as a key-value block, or compressed into a paragraph. The messages themselves stay on disk under all three. `facts` costs one extra request per message, `summary` one per overflow, `window` none — and the usage panel reports each separately from the turns, because that is the only way to tell whether a strategy is paying for itself.

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
| `DELETE` | `/api/conversations/{id}/facts` | forget the fact block, keep every message |
| `POST` | `/api/conversations/{id}/checkpoints` | mark a position — `{"label", "at", "branch"}`, all optional |
| `DELETE` | `/api/conversations/{id}/checkpoints/{cp}` | forget a bookmark; its branches stay |
| `POST` | `/api/conversations/{id}/branches` | fork — `{"checkpoint"}` or `{"at", "branch"}`, plus `{"name"}` |
| `POST` | `/api/conversations/{id}/branches/{b}/activate` | switch to a branch |
| `DELETE` | `/api/conversations/{id}/branches/{b}` | abandon a fork (not `main`, not one with children) |
| `POST` | `/api/chat` | ask a question in a conversation — `{"branch"}` optional |
| `GET` | `/api/tasks` | every task, newest first, plus the long-term layer |
| `POST` | `/api/tasks` | start a piece of work — `{"title"}` |
| `POST` | `/api/tasks/{id}/status` | `{"status": "closed"}` or `"open"` — deletes nothing |
| `DELETE` | `/api/tasks/{id}` | forget a task and what it learned; chats that joined it are fine |
| `POST` | `/api/tasks/{id}/memory` | file into the working layer — `{"value"}`, plus optional `{"key", "source", "pinned"}` |
| `DELETE` | `/api/tasks/{id}/memory/{key}` | forget one working item |
| `GET` / `POST` | `/api/memory` | read / file into the long-term layer |
| `DELETE` | `/api/memory/{key}` | forget one long-term item |
| `POST` | `/api/conversations/{id}/task` | join a task — `{"task_id"}`, or `null` to leave |
| `POST` | `/api/conversations/{id}/proposals/{n}` | accept a suggestion — `{"layer"}` overrules the agent |
| `DELETE` | `/api/conversations/{id}/proposals/{n}` | dismiss one |

The whole of day 11 from the shell — file two things, see which of them the next request carries:

```bash
T=$(curl -s localhost:8000/api/tasks -H 'Content-Type: application/json' \
      -d '{"title": "port the store"}' | jq -r .task.id)
curl -s localhost:8000/api/conversations/single/task -H 'Content-Type: application/json' \
  -d "{\"task_id\": \"$T\"}" > /dev/null

# No key: one is derived from the note, exactly as /task and /remember do.
curl -s localhost:8000/api/tasks/$T/memory -H 'Content-Type: application/json' \
  -d '{"value": "one row per message"}' > /dev/null
# A rule prefix files itself as always-sent - this is what /always is for.
curl -s localhost:8000/api/memory -H 'Content-Type: application/json' \
  -d '{"key": "constraint.language", "value": "answer in Russian"}' > /dev/null

curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message": "what is the schema?", "conversation_id": "single"}' \
| jq '.memory.sent'
# {"long_term":["constraint.language"],"long_held":1,
#  "working":["one-row-per-message"],"working_held":1,"task_closed":false}
```

`constraint.language` rode along although the question said nothing about language; it was starred on the way in, and starred notes are not filtered. Close the task and send the same question again, and `working` comes back empty while `working_held` stays at 1 — the memory is still there, it has simply stopped being true of anything you are doing.

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
  "strategy": "facts",
  "window": 8,
  "messages": 54,
  "replayable": 54,
  "branch": "branch-2",
  "branches": 3,
  "summary": null,
  "facts": {
    "items": { "goal": "port the day-7 store to sqlite", "constraint.language": "answer in russian" },
    "count": 2, "updates": 29, "usage_total": { "...": "..." }
  }
}
```

Every conversation the API hands back carries this `context` block alongside its `usage` one, and `POST /api/chat` adds a `context.sent` to it saying what the request that was just made actually carried — how many messages went up as themselves, how many were dropped, which block stood in for them, and whether one had to be bought first. The exchange that bought it comes back under `debug.facts` or `debug.compaction`, in exactly the shape an ordinary turn's debug block has, with the patch (`set` / `unset`) alongside the raw JSON.

To rebuild a memory you did not like — the messages it was derived from are all still there:

```bash
curl -s -X DELETE localhost:8000/api/conversations/single/facts | jq '.context.facts'
# null — and every message is still there
```

To fork a conversation from the command line, twice from one place:

```bash
CP=$(curl -s localhost:8000/api/conversations/single/checkpoints \
       -H 'Content-Type: application/json' -d '{"label": "before the fork"}' \
     | jq -r .checkpoint.id)

for name in "plan A" "plan B"; do
  curl -s localhost:8000/api/conversations/single/branches \
    -H 'Content-Type: application/json' \
    -d "{\"checkpoint\": \"$CP\", \"name\": \"$name\"}" \
  | jq '{branch, shows: .branching.branches[-1].messages, stores: .branching.branches[-1].own}'
done
# {"branch":"branch-2","shows":54,"stores":0}
# {"branch":"branch-3","shows":54,"stores":0}
```

Both show the whole conversation and store none of it. `POST /api/chat` then lands in whichever branch is active, or in the one you name:

```bash
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message": "what if we kept JSON?", "conversation_id": "single", "branch": "branch-2"}' \
| jq '{branch: .conversation.branch, messages: .conversation.message_count}'
```

`conversation_id` defaults to `single`, so a bare `{"message": "..."}` lands in the single chat — the same conversation the browser is looking at. Ids become file names, so they are restricted rather than escaped: letters, digits, `-` and `_`, up to 64 characters. Anything else is a 400.
