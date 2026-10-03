# LLM Agent that answers from its own documents when asked to, after reading what the search found and keeping only what is about the question, orchestrates several MCP servers through one long flow, runs a whole pipeline behind one tool call, keeps working while nobody is asking, calls tools on servers of its own, knows which tools are on the shelf, refuses to break your rules, whose tasks are a state machine, with three layers of memory, a personality you can switch, three ways of remembering a conversation, and a conversation that can fork

A small **agent** that takes a question, sends it to an LLM over plain HTTP, shows the whole exchange in a web GUI, keeps as many conversations as you like, remembers all of them across restarts and reports what that costs — since day 11, **remembers some things for longer than the conversation they were said in**, since day 12, **answers the way you told it to rather than the way it was built to**, since day 13, **knows which stage the work is at, and will not pretend it is further along than it is** — and since day 14, **will not propose the thing you already ruled out, and is checked rather than trusted on it** — since day 16, **knows what tools exist on servers it did not write** — since day 17, **runs them**, against an MCP server of our own over Google Maps — since day 18, **keeps working when nobody is asking**: a scheduler of its own watches Cape Town's event listings every day and posts a digest into a chat — since day 19, **chains tools without the model in between**: one call to `plan_meetup` runs five steps over Google Maps and says where a group of people should meet — and since day 20, **works across five MCP servers in one request**: it is told which server is for what, every call is routed to the server that owns it, and a request like "jazz on a dry evening, dinner first, save the plan" becomes a flow of calls over events, weather, maps and a planner, recorded round by round with where each argument came from, and checked — and since day 21, **keeps a local index of documents**: its own README and modules, cut two ways, embedded by a local model and stored with where every piece came from, with a check that says which way of cutting finds answers better — and since day 22, **answers from that index when you switch it on**: the question finds its nearest chunks, they are joined onto it as numbered excerpts, and the answer cites the ones it used, with ten control questions that say what a right answer contains and a table of how the same model does with the documents and without them — and since day 23, **reads what the search found before answering from it**: twenty candidates instead of five, each scored for the question by the model in one request, and only those that hold at least a substantial part of the answer sent — none at all for a question the documents do not answer — with three questions of that kind added to the control set, and a sweep that replays every threshold over the stored scores.

Day 7 gave the agent a memory by replaying the transcript into every request. Day 8 put the price of that on screen: the API is stateless, the whole conversation goes up every time, and the bill grows with the square of the chat rather than with the message. Day 9 added the first answer to that — summarise the overflow — and, with it, one switch. Day 10 replaced the switch with a choice, and added a second, unrelated one.

Day 11 asks a question none of them did. All of the above is about **one conversation**: how much of it fits, what stands in for the rest, which fork of it you are in. Start a new chat and every last bit of it is gone — the agent meets you again as a stranger, and you type your name for the fourth time. So that day is about the other axis: **how long a thing stays true, and who is allowed to see it.**

Day 14 is about the one word none of the thirteen ever said: **no.** Everything above configures what the agent knows, who it thinks you are and where the work stands, and every last piece of it is *advisory* — day 12 will happily hold "Python + FastAPI, free APIs only" and this README already conceded the hole in that, calling those lines "the easiest to check an answer against" while nothing checked them. So this day writes the rules down somewhere of their own, sends them last, and then **reads the answer back against them before you see it**: one that broke a rule is thrown away and asked for again, and one that broke a rule because *you* asked for it comes back naming the rule and offering ways round it.

Day 13 is about the third thing, and it is neither of those. A memory says what is *true*; a personality says who is *asking*. Neither says **where the work has got to** — and that is the thing you actually lose when you shut the laptop. Day 11's task had a status with two values in it, `open` and `closed`, which answers *is this alive* and nothing else. So this day replaces it with four stages, a table of which may follow which, a checklist per stage that holds the way forward, and a pause that works in all of them — all in code, so that a model in a hurry to be agreeable cannot call something finished that nothing ever checked.

Day 12 asks the question all eleven of them kept answering by accident. Every one is about what the agent **knows** — how much of it, for how long, carried how far. None is about **who is asking**, and the two had quietly ended up in the same places: a reasoning style in the chat's settings since day 3, `preference.style: no apologies` filed into a memory layer since day 10. So this day pulls those apart and gives the second one an object of its own, with a switch on it — three fields covering how you want answers written, the choices you have already settled whatever the topic, and who you are and what you are building.

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

```
PERSONALITY  ⟨ Work ⟩                                                  YOU ▸

  ○  — no personality —    answers on the agent's default configuration
  ●  Work                  STYLE  PREFERENCES  CONTEXT
  ○  Weekend project       STYLE  preferences  CONTEXT

  SENT WITH EVERY REQUEST
    How they want answers written:
    - in Russian, short and direct, no preamble
    Standing preferences, whatever the topic:
    - Python + FastAPI, minimum dependencies, free APIs only
    Who they are and what they are working on:
    - senior developer, voice assistant, team of 3, two weeks left
```

```
TASK  ⟨ Build the iOS app · Execution ⟩    + New   [ Pause ]   [ Delete ]

  Planning  ›  ● EXECUTION  ›  Validation  ›  Done
                                  ╰─ greyed: "Not yet: “Every planned step
                                      done…” still to do in execution."
                                                    ╰─ greyed: "Execution
                                                        cannot go straight
                                                        to done."
  Next: Work the plan step by step; nothing left silently unfinished.

  ☑ Every planned step done, or dropped on purpose      • required
  ☐ Decisions taken while working are filed
  ☐ Departures from the plan are called out

  ▸ History (4)
      09-14 16:02  execution → validation  (agent) – the build is finished
      09-12 09:31  planning → execution  (you)
```

**Nothing is deleted, and nothing is copied.** The transcript stays whole on disk, exactly as day 7 left it. What changes is which part of it goes up the wire, what stands in for the part that does not — and, now, *which* transcript it is.

## Day 23: a second stage after the search — the model reads the candidates, a threshold cuts them

The brief: add a **second stage after the search** — a reranker or a relevance filter (a similarity threshold, a separate model, or a heuristic); set **the threshold** below which a result counts as irrelevant, and **top-K before and after** the filter; compare the answers **without the filter and with it**.

Day 22 sent the five nearest chunks, whatever they were, and its own run shows both ways that goes wrong. The scores were all alike — 0.579, 0.564, 0.564, 0.559 for the first control question, with an unrelated function first and the right chunk second — so the order inside the top five was close to arbitrary. And there is always a nearest chunk: a question the documents cannot answer still went up with five excerpts, about whatever shared its words.

```
                                    ┌─▶ the first 5 ──────────────────────────────────────────────▶ RAG · top 5     (day 22)
question ─▶ bge-m3 ─▶ 20 nearest ───┤
            (Ollama)  (one search)  └─▶ judge: one request, a score 0-10 each ─▶ ≥ 6, at most 5 ─▶ RAG + rerank    (day 23)
                                                                                 (possibly none)
```

### The judge: a chat model reading all twenty candidates at once

`rag/rerank.py`. The search asks for **20 candidates** — the *top-K before* — and `LLMReranker.judge` sends all of them, numbered, with the question, in **one** request to the same DeepSeek model that answers: temperature 0, JSON mode, no reasoning. Back comes a score per candidate, and `keep` sends the ones **at the threshold or above, best first, at most k = 5** — the *top-K after* — renumbered [1]…[n]. Both RAG modes start from the same search, so they differ only in how its list is cut.

- **A model, not a threshold on the cosine.** The cosine compares two vectors made apart — the question's and the chunk's — and a Russian question against English prose puts every chunk in the same narrow band. In this run the top cosine of the three off-topic questions below is 0.54, 0.43 and 0.51, while two answerable questions have nothing above 0.52 and 0.53 at all (`chunking-winner`, `long-prefix`). A similarity cut that empties all three off-topic questions empties those two with them; one low enough to keep every answerable question's right chunk (0.49) empties one off-topic question of three. The judge reads the question and the chunk *together* — a cross-encoder with a prompt for a head — and gave every one of the 60 off-topic candidates a 0, and the place where each answerable question's answer lives a 10.
- **One request for twenty, not twenty requests.** The instructions and the question go once, there is one round trip instead of twenty, and the candidates are graded side by side, which is what a ranking is. The price is a prompt the size of twenty chunks: ~8,000 tokens and 1–2 s, reported with every answer rather than folded into it.
- **An anchored scale**, so that one threshold means the same thing for every question:
  ```
  10 - answers the question, or one part of it, outright
   7 - holds a substantial part of the answer
   4 - on the same subject, but does not answer it
   1 - shares words with the question and nothing more
   0 - unrelated
  ```
  The judge kept to the anchors: across both runs it gave nothing but 0, 1, 4, 7 and 10. So the eleven thresholds are three cuts — **1–4** drops only the unrelated, **5–7** drops "on the subject, but does not answer", **8–10** keeps only what answers outright.
- **A judgement that cannot be read is a failure, not a fallback.** A reply that is not the JSON asked for fails the mode — `502` in the chat, an error in its column of the tab — rather than quietly sending the unfiltered top five: an answer shown as filtered has to have been. The parser takes `{"scores": {"1": 8}}`, a bare object, a list, or any of them inside a fence; a candidate the reply skipped is scored `—` and not kept.
- **Nothing kept is said, not sent bare.** When no candidate reaches the threshold, the excerpts block goes up saying so — *none: the nearest chunks of the index were read for this question, and none of them was judged to be about it* — under day 22's instruction to say so rather than guess. A question sent without the block would read as one asked without the documents, which is the opposite of what happened.

Like the embedder, the judge is a seam. `LLMReranker` is handed a function — a chat-completions body in, the response JSON out — so `rag/` still imports nothing from the app around it, and another reranker is another class with a `judge(question, hits)`.

### Before, after, and the threshold — tuned without asking again

Three settings, in the RAG tab's run settings and in the chat's RAG field: **candidates** (before: default 20, up to 30), **threshold** (0–10, default 6) and **top k** (after: default 5, up to 10).

The judge scores every candidate once, and the scores are stored with the run. So the tab's **threshold sweep** cuts them again — every threshold, for any number of candidates up to what was judged and any top k — and shows what each setting *would have sent*, without a single request; a click on a line puts its settings in for the next run. Over the 13 questions, 20 candidates, k = 5:

| keep when the judge says ≥ | excerpts sent, mean | expected place kept | of them from it | off-topic left empty |
|---|---:|---:|---:|---:|
| no judge: the first 5 by cosine | 5.0 | 10 of 10 | 44% | 0 of 3 |
| 0 — reordered, nothing cut | 5.0 | 10 of 10 | 58% | 0 of 3 |
| 1 – 4 | 4.9 | 10 of 10 | 60% | 3 of 3 |
| **5 – 7** | **4.1** | **10 of 10** | **71%** | **3 of 3** |
| 8 – 10 | 2.0 | 10 of 10 | 82% | 3 of 3 |

- **Before: twenty, because the judge can only promote what the search returned.** Of the twenty chunks it scored 10, eight sat below cosine rank 5, and five of those are where the answer lives: `mcp-routing`'s at rank 13, `task-stages`' at 9 and 15, `chunking-winner`'s and `invariant-retry`'s at 7. Replayed with 5 candidates, the strict cut keeps the expected place for 7 questions of 10; with 10, for 9; with 15 or 20, for all of them.
- **After: five is a cap, not a quota.** At ≥ 6 half the answerable questions still reach it — `invariant-retry` had nine candidates at 7 or 10 — and the other half send two to four.
- **The threshold: 6 — the cut between 4 and 7.** It drops what is on the subject but does not answer, and keeps whatever holds a substantial part of the answer. The strict cut looks better in the table — half the excerpts, 82% of them from the right place — and the second run below shows what it costs an answer.

### Three questions the documents do not answer

A filter that only ever sees answerable questions is never asked to send *nothing*. So the control set gains three, marked `answerable: false`. They ask about the project's infrastructure in the same register as the other ten, and the project has none of what they ask about. Each has a single keyword line — the ways an answer says the documents do not cover it (`не содерж | не упомина | нет информац | … | not covered`) — and no sources; for them the number that matters is how many excerpts went up with the question. They are in `rag_questions.json` and on the tab, and on purpose not quoted here: this README is in the corpus, and a question written out in it would stop being one the documents do not answer.

### In the tab and in the chat

**The RAG tab** asks every question three ways at once — without the index, day 22's top k, and the reranked cut — from one search, one judge request and three answers that differ in their last message only. A row shows each mode's verdict, where the expected place landed and whether it was cited, how many candidates the judge kept, and, for an off-topic question, how many excerpts went up. Opened, it has the three answers side by side and, under the reranked one, every candidate in cosine order with its score and where it went (`[2]` or `cut`), the expected places marked, and the judge's reply verbatim.

**In the chat**, the RAG field (⚙ Settings, and every compare column) gains a **Rerank** switch with candidates and threshold beside it, **on by default** — the measurement below is why — and one click from day 22's top k. The top-bar tooltip names the cut (`reranked: 20 candidates scored by the model, those ≥ 6 kept, at most 5`). Under an answer, the folded **Sources** block says how the list was cut (`reranked: 3 of 20 scored ≥ 6`) and holds every candidate with its cosine and its score; when nothing passed, it says the question went up with no excerpts, and why. The judge runs once per turn, before the answer, and day 13's and 14's retries reuse its cut as they reused day 22's retrieval. A judge that fails refuses the turn, for the reason a silent Ollama does.

**Static files now go out with `Cache-Control: no-cache`**, and so does the page. Every day's app is served from the same `localhost:8000/static/…`, and with no caching header a browser guesses how long a file stays fresh: one that had day 22's `rag.js` kept running it on this day's page without asking — two columns where there are three, next to a chat whose Rerank switch was already there. `no-cache` keeps the copy and makes the browser ask first; an unchanged file comes back as an empty `304`.

### The comparison — 13 questions, deepseek-v4-flash, structural index

One run at the defaults — 20 candidates, threshold 6, top 5 — with the answers at temperature 1.0 and reasoning `low`, as on day 22. Ten answerable questions, so one question is 10 points: read it as a direction, not a decimal.

| | without RAG | RAG · top 5 | RAG + rerank · 20 → ≥ 6 → 5 |
|---|---:|---:|---:|
| **ten answerable questions** | | | |
| keyword coverage, mean | 52% | 89% | 89% |
| full / partial / miss | 2 / 5 / 3 | 8 / 1 / 1 | 8 / 1 / 1 |
| expected source among the excerpts | — | 10 of 10 | 10 of 10 |
| … at rank, mean | — | 2.2 | **1.4** |
| answer cites the expected source | — | 9 of 10 | **10 of 10** |
| excerpts sent, mean | — | 5.0 | 4.1 of 20 |
| of them from the expected place | — | 44% | **71%** |
| **three off-topic questions** | | | |
| says the documents do not cover it | 1 of 3 | 3 of 3 | 3 of 3 |
| excerpts sent | — | 5, 5, 5 | **0, 0, 0** |
| **cost per question, mean** | | | |
| prompt tokens, the answer | 63 | 2,135 | 1,368 |
| completion tokens (of them reasoning) | 2,901 (2,402) | 564 (333) | 518 (292) |
| judge tokens | — | — | 8,114 |
| total tokens | 2,964 | **2,700** | 10,001 |
| seconds | 13.4 | **3.2** | 4.5 |

| question | without RAG | RAG · top 5 | RAG + rerank | excerpts sent | expected place at |
|---|---|---|---|---:|---|
| `embedder-model` | partial 2/3 | full 3/3 | full 3/3 | 5 → 4 | 4 → 1 |
| `why-sqlite` | partial 2/4 | full 4/4 | full 4/4 | 5 → 2 | 1 → 1 |
| `chunking-winner` | miss 0/3 | full 3/3 | full 3/3 | 5 → 4 | 2 → 1 |
| `long-prefix` | partial 2/3 | full 3/3 | full 3/3 | 5 → 4 | 2 → 1 |
| `task-stages` | miss 1/5 | full 5/5 | full 5/5 | 5 → 5 | 3 → 2, and now cited |
| `mcp-routing` | full 3/3 | full 3/3 | full 3/3 | 5 → 5 | 5 → 2 |
| `meetup-pipeline` | partial 2/4 | full 4/4 | full 4/4 | 5 → 5 | 2 → 2 |
| `scheduler-clock` | full 3/3 | full 3/3 | full 3/3 | 5 → 5 | 1 → 1 |
| `invariant-retry` | partial 2/3 | partial 2/3 | partial 2/3 ¹ | 5 → 5 | 1 → 2 |
| `upload-limits` | miss 0/4 | miss 1/4 | miss 1/4 | 5 → 2 | 1 → 1 |
| three off-topic | 1 of 3 | 3 of 3 | 3 of 3 | 5 → 0 | — |

¹ A keyword miss: the reranked answer says *ровно одна повторная попытка*, which is the fact; the line has *одна попытка* and *один повтор*, and the word in between defeats both. The top-5 answer never says how many retries there are.

What the numbers say, and what they do not:

- **On this set the filter does not change what the answers say — it changes what they are built from.** Coverage is level, 89% and 89%, verdict for verdict. What moves is upstream: the right place moves up (2.2 → 1.4) and is cited every time, and the share of excerpts that come from it goes from 44% to 71%. Day 22's model was already good at ignoring the four excerpts it did not need; this day stops sending them.
- **Off-topic, both RAG modes say the right thing — one of them after reading fifteen useless excerpts.** Day 22's *say so rather than guess* does the work in both: all six RAG answers say the documents do not cover it. The difference is what went up — five excerpts per question, ~2,500 prompt tokens about something else, against none — and it shows in the answers: the top-5 ones go on to describe what their excerpts *do* contain (a database path, the MCP servers' ports), the reranked ones are a sentence. Without the documents, two answers of three describe somebody else's stack; the third counts as declining because it opens with *I have no access to your project* — and then guesses anyway, which is day 22's keyword weakness again.
- **It is not cheaper.** The answer's prompt shrinks by a third, ~770 tokens, and the judge spends 8,100 to get there: ~10,000 tokens a question against 2,700, nearly all of it prompt (7,990 of the judge's 8,114), and 1.3 s more. What that buys is the excerpt list above — and an empty one when there is nothing to send.
- **Neither miss is the second stage's to fix.** `upload-limits` needs the list of loaders, and no chunk of `rag/loaders.py` is among the twenty candidates: the judge scored the upload function 10 and the rest of `indexing_api.py` 7, 4 and 0, and the answer says the formats are not in its excerpts rather than guessing them. That is recall, and a judge can only reorder what the search returned. `invariant-retry` is the keyword line's (¹).

### The strict cut, on answers: a second run at ≥ 8

The sweep says what ≥ 8 would have sent; only answers say what that costs. A second run, all three modes again, at 20 → ≥ 8 → 5:

| | RAG · top 5 (second run) | RAG + rerank · 20 → ≥ 8 → 5 |
|---|---:|---:|
| keyword coverage, mean | 92% | 93% |
| full / partial / miss | 8 / 2 / 0 | 9 / 0 / 1 |
| excerpts sent, mean | 5.0 | 2.2 |
| of them from the expected place | 44% | 83% |
| prompt tokens, the answer | 2,135 | 776 |
| total tokens | 2,671 | 9,395 |

Better on paper, and one of the nine *full* is the keyword check being fooled. `task-stages` scores 5/5 at ≥ 8 with an answer that says it **cannot tell** whether execution may go straight to done — *нельзя утверждать*, and *нельзя* is a keyword. The chunk that says it — the README's *Where the controls are*, `Execution cannot go straight to done` — was scored 7, a substantial part of the answer rather than the whole of it, and ≥ 8 cut it; at ≥ 6 it went up fifth and the answer quoted it. That is the case the strict cut loses by construction: an answer assembled from parts, none of which answers outright. Every question in this set was written with one section that answers it, so the set rarely tests the strict cut where it fails — which is why the default is 6, at ~600 more prompt tokens a question, 6% of what a reranked answer costs.

Two more things the second run shows:

- **The modes move on their own.** Between the two runs, at temperature 1.0, *without RAG* went from 52% to 43% and *top 5* from 89% to 92%, with nothing changed. A difference of a question or two between modes is inside that.
- **The judge is close to repeatable, and repeatable where it matters.** At temperature 0, 21 of the 260 scores differ between the runs, each by a step or two on the scale (1 ↔ 4, 0 ↔ 4, 7 ↔ 10). None crosses 6, so at the default both runs sent the same excerpts; two crossed 8 — which is how ≥ 8 sent 2.2 excerpts where the first run's sweep predicted 2.0.

Both runs are in `data/rag/` — `last_run.json` at the default, `run_threshold8.json` the second — and the first is on the tab after a reload, row by row, with every candidate's score.

### What is deliberately not here

- **Query rewriting.** The day's result line mentions it; it was discussed for this day and left out by decision. Note what it would have done to this comparison: change the candidates themselves, so a difference in the table could no longer be put down to the cut.
- **A similarity threshold, a heuristic, or a local cross-encoder as a mode.** The cosine cut is measured above and does not separate the two kinds of question; a heuristic reranker, or `bge-reranker` behind Ollama, would be another class with a `judge`; a cross-encoder through `sentence-transformers` is a torch install for one number per chunk.
- **The judge's reasons.** One number per candidate and no reasoning: reasoning tokens are billed as output, and a threshold needs a number. Its raw reply is on the tab for anyone who wants to read it.
- **Reranking across the conversation.** The chat still retrieves for the message as typed; day 22's note on follow-ups stands.

### Checking it

```bash
uv run python -m unittest discover -s tests -v   # test_rag.py: the judge too - no model, no network
npm test                                         # + the third mode and the rerank switch, against an empty index
```

`test_rag.py`'s fake LLM now plays the judge as well: asked with the reranker's prompt, it scores a candidate 9 when it shares a word with the question and 1 when it does not. That covers the parser on every shape a model sends and the replies it must refuse; the cut — threshold, order, renumbering, the cap; the empty cut being said rather than sent bare; the judge's request (temperature 0, JSON, no reasoning, every candidate); the three modes from one search, with one judge request and three answers that differ in their last message only; an off-topic question emptied by the judge and still sent three by top k; the sweep and its replay of candidates and k; and an unreadable judgement failing the reranked mode alone. The UI suite checks the tab's three columns and run settings, the questions marked off-topic, both RAG modes refused for want of an index, and the chat's rerank switch — on by default, carried in the request, greyed with its numbers when off, saved with the chat.

With Ollama and a key: `uv run server.py`, **Index → Build both**, then **RAG → Run all**.

## Day 22: the first RAG request — with the documents and without

The brief: **question → search for relevant chunks → join them with the question → ask the LLM**, an agent with **two modes** (with RAG and without), and **ten control questions** over the knowledge base, each with what the answer should contain and which sources it should come from — then compare.

Day 21 built the index and measured whether it *finds* the place an answer lives. This day asks the next question — does the answer *say the right thing* — and asks it of the same model twice.

```
question ─▶ bge-m3 ─▶ top k chunks of one index ─▶ [1] file › section … [k] ─▶ joined onto the question ─▶ DeepSeek ─▶ answer with [n] ─▶ which excerpts it cited
           (Ollama)    (structural, k = 5)          numbered excerpts              one user message                       parsed back to chunks
```

The knowledge base is day 21's: this folder's `README.md`, its top-level modules and — new today — `rag/*.py`, since the code that answers from the index is part of what it can be asked about. 34 files, ~140,000 words, 852 structural chunks.

### Two modes, in two places

**In the chat** — a **RAG** switch in the top bar (`off`, or `structural · top 5`), and the same switch in ⚙ Settings with the index and k beside it; the compare columns have it in their forms, so one column can be asked with the documents and the next without. It is a setting of the chat (`settings.rag`, `rag_index`, `rag_k`) and is stored with it, off by default — every chat from before this day answers exactly as it did. Everything else the chat does stays on: memory, personality, invariants, tools. Under an answer, a folded **Sources** block lists the excerpts it was given, ranked, with score and size, and marks the ones it cites; it is stored on the message, so a reload shows the sources that answer was actually given, not whatever the index returns today.

**In the RAG tab** — the clean comparison. Both modes are **the same bare agent**: same model, the default system prompt, the chat's default settings, and no memory, no tools, no personality, no history. The two requests differ in the last message and nothing else (a test asserts exactly that), so whatever differs in the answers is the excerpts' doing. *Ask both ways* sends any question twice at once; the control set below is what gets kept.

A RAG question with no index to read — never built, built with another embedder, Ollama down — is **refused** with what is missing (`409` / `503`), in the chat and in the tab alike. Quietly answering without the documents would put an answer on screen as one that read them.

### What the model is sent

```
--- Excerpts from the project's documents, found for this question ---
[1] README.md › Day 21: … › The embedder is a seam, and Ollama is on the other side of it
`OllamaEmbedder` is one subclass: `POST /api/embed` on the local Ollama, …

[2] rag/embedders.py › module docstring
…
--- End of excerpts ---

Answer the question below using these excerpts. Rely on what they say rather than on general
knowledge; if they do not contain the answer, say so plainly instead of guessing. Cite the
excerpts you used by their number in square brackets, like [2]. Answer in the language of the
question.

Question: Какая модель эмбеддингов строит индекс …?
```

- **Joined onto the question, not a system block.** Every block since day 9 is a system message of its own; this one is the user message, because "here is what the documents say — now, the question" is one thing, and the model meets the evidence where it meets what it is evidence for. The stored transcript keeps the question **as typed**: a later turn replays what was asked, not five excerpts picked for a different question.
- **Numbered, not quoted by id.** `[2]` is two tokens and cannot be misspelt; `structural:README.md#0042` is neither. Numbers are mapped back to chunks after the answer, and a citation of an excerpt that was never sent (`[9]` out of five) is dropped rather than shown as a source.
- **Whole chunks, in rank order, k of them.** A chunk is what was embedded and scored; trimming it would send text the score was not computed on. k is the budget, and its price is on screen: five structural chunks are ~1,000 words, ~2,000 prompt tokens against ~60 for the bare question.
- **Three instructions, each one measured below:** answer from the excerpts, say so when they do not hold the answer, cite by number.

The pieces are small on purpose. `rag/augment.py` — `retrieve`, `render_excerpts`, `cited` — returns text and never calls a model, so the `rag` package still imports nothing from the app; the agent gets the rendered excerpts as one more argument (`knowledge`) and puts them where `KNOWLEDGE_TEMPLATE` says; retrieval happens once per turn in `post_chat`, and both of the day-13/14 retries reuse it — a new answer to the same question has the same nearest chunks.

### Ten control questions

`rag_questions.json`, seeded into `data/rag/questions.json` on first start and edited in the tab (question, expected answer, keyword lines, sources with day 21's file/section dropdowns). Each one carries:

- **expected** — what a right answer says, in a sentence, for the person reading the table;
- **keywords** — the facts it must contain, one line per fact; `a | b` on a line means either;
- **sources** — where the answer lives: a file and optionally a section (with everything under it), compared by character range exactly as day 21's check compares them.

| id | about | must contain (one line each) | should come from |
|---|---|---|---|
| `embedder-model` | the embedding model, and why that one | bge-m3 · multilingual · 1024 | README › Day 21 › The embedder is a seam; `rag/embedders.py` |
| `why-sqlite` | SQLite rather than FAISS or JSON | sqlite · millisecond · dependency / second file · json | README › Day 21 › Storage; `rag/index_store.py` |
| `chunking-winner` | which strategy won, hit@5 of each | structural · 92 · 68 | README › Day 21 › The comparison |
| `long-prefix` | why the README-title prefix hurt, and its replacement | the same words · file name · 0.42 / distinguish | README › Day 21 › The comparison |
| `task-stages` | the four stages, and execution → done | planning · execution · validation · done · not allowed | README › The task, as a state machine; `phases.py` |
| `mcp-routing` | how a call finds its server, and a name that routes nowhere | prefix/name · `__` · did you mean / error | README › Day 20 › Routing; `orchestrator.py` |
| `meetup-pipeline` | the steps of `plan_meetup`, and why one tool | geocode · matrix · rank · one call | README › Day 19; `maps_mcp_server.py` |
| `scheduler-clock` | who holds the schedule, and why | server · clock / wake · session | README › Day 18 › Who holds the clock; `events_mcp_server.py` |
| `invariant-retry` | what happens to an answer that broke a rule | thrown away · asked again · once | README › Invariants; `invariants.py` |
| `upload-limits` | the upload size limit and accepted formats | 2 MB · .md · .py · .txt | `indexing_api.py`; `rag/loaders.py` — code only, in no prose |

All ten are about this project, so a model without the documents cannot know any of it — the point is to see what it does instead. The table names the topics rather than quoting the questions on purpose: this README is in the corpus, and a question written out here verbatim would be the nearest chunk to itself, which teaches the check nothing.

### Scoring: keywords and ranges, no judge model

Per answer, by code: **coverage** = the share of keyword lines found; **full** = all of them, **partial** = at least half, **miss** = less. With RAG, also: was an excerpt from an expected place among those sent (**found @rank**), and does the answer **cite** one of those. Matching ignores case, `ё`/`е`, and folds dashes and spaces (`MCP-сервер` = `MCP сервер`, `bge-m3` = `BGE m3`); keyword lines are stems with alternatives in both languages, because the answer may come in either.

Why not a model as judge: it is a third answer nobody checks, priced per question, and it grades a fluent paraphrase as kindly as a fact. A keyword line is a claim a person wrote down before the run and checked the same way every time; when it is wrong, the fix is editing a visible line rather than arguing with a prompt. The cost is paraphrase, and the results below show both ways it bites.

### The comparison — ten questions, deepseek-v4-flash, structural index, k = 5

One run, both modes at the default settings (temperature 1.0, reasoning `low`). Ten questions, so one question is 10 points — read it as a direction, not a decimal.

| | without RAG | with RAG |
|---|---:|---:|
| keyword coverage, mean | 42% | **92%** |
| full / partial / miss | 1 / 3 / 6 | **8 / 2 / 0** |
| expected source among the 5 excerpts | — | 10 of 10 |
| answer cites the expected source | — | 9 of 10 |
| prompt tokens, mean | 64 | 2,066 |
| completion tokens, mean (of them reasoning) | 2,537 (2,023) | **565 (343)** |
| total tokens, mean | 2,600 | 2,631 |
| seconds per answer, mean | 12.5 | **3.2** |

| question | without RAG | with RAG | expected source found at | cited |
|---|---|---|---:|---|
| `embedder-model` | partial 2/3 | full 3/3 | 2 | ✓ |
| `why-sqlite` | partial 2/4 | full 4/4 | 1 | ✓ |
| `chunking-winner` | miss 0/3 | full 3/3 | 2 | ✓ |
| `long-prefix` | miss 1/3 | full 3/3 | 1 | ✓ |
| `task-stages` | miss 2/5 | full 5/5 | 3 | ✗ cites `phases.py` code instead |
| `mcp-routing` | full 3/3 | full 3/3 | 5 | ✓ |
| `meetup-pipeline` | miss 1/4 | full 4/4 | 2 | ✓ |
| `scheduler-clock` | partial 2/3 | full 3/3 | 1 | ✓ |
| `invariant-retry` | miss 1/3 | partial 2/3 | 1 | ✓ |
| `upload-limits` | miss 0/4 | partial 2/4 | 1 | ✓ |

What the numbers do not say on their own:

- **Without the documents the model does not say "I don't know".** Two answers of ten open by saying it cannot see the project; the other eight answer how such things are *usually* done. The upload question gets a confident answer about a different product — Algolia's Index tab, 10 MB, JSON/CSV. The scheduler question gets this project's decision reversed — "the agent app or a dedicated scheduler, not the MCP server". That is the failure the brief is about: not ignorance, a plausible guess.
- **Two of the plain mode's points are the keyword check being fooled**, and they are worth knowing about before trusting any table like this. `scheduler-clock` scores 2/3 by saying *not* the MCP server — the keyword is there, negated. `mcp-routing` scores 3/3 with a correct description of how MCP clients in general namespace tools (`mcp__github__create_issue`), which happens to contain all three lines. A keyword can say a fact was mentioned, never that it was asserted; the mean of 42% is, if anything, generous.
- **The documents replace the guessing, at the same total.** The RAG prompt is ~32× larger, and the answers are 4.5× shorter, with a sixth of the reasoning: the model stops deliberating over possibilities it has been handed the answer to. Total tokens come out level (2,600 vs 2,631) — and prompt tokens are the cheap kind on DeepSeek — while the answer arrives in a quarter of the time.
- **Both partials are retrieval's, not the model's.** `invariant-retry` misses *one retry, never two*, which lives in a paragraph none of the five excerpts came from. `upload-limits` finds `MAX_UPLOAD_BYTES = 2_000_000` and quotes it, and then — the *say so* instruction working — writes that the list of loaders is not among the excerpts and it will not name the formats, instead of guessing them. A miss that admits it is a different thing from a miss that does not.
- **The one uncited source is the question's to fix.** `task-stages` is answered right from `phases.py`'s module-level code — the `TRANSITIONS` table itself — and a README section about the UI, while the question lists the README chapter and `phases.py`'s *docstring*. Exactly day 21's situation: the answer was written down in a place the question did not name. The routing question's own section was not retrieved at all (the orchestrator's docstring came in fifth), and the RAG answer said what the excerpts did not cover rather than filling it in.

The run is in `data/rag/last_run.json` and on the tab after a reload; the answers above can be read in full there, row by row, with the excerpts each was given and the exact message that went up.

### What is deliberately not here

- **A relevance threshold, reranking, query rewriting.** Every question gets its k nearest chunks however far they are; whether a low score should mean "send nothing" is the obvious next measurement, and the control set is now there to make it.
- **Retrieval over the conversation.** The chat retrieves for the message as typed, so a follow-up like "and why?" finds chunks for "and why?". Folding the last turn into the query is a decision with its own failure (the previous topic drags along) and deserves its own day.
- **A judge model** — see above; and no hand-written override of a verdict either: a wrong verdict is a wrong keyword line, and the line is what gets edited.
- **Streaming the ten.** Each check is its own request; the tab runs them one after another and draws each row as it lands.

### Checking it

```bash
uv run python -m unittest discover -s tests -v   # test_rag.py: no model, no network
npm test                                         # + the RAG tab and the chat's switch, against an empty index
```

`test_rag.py` uses day 21's hashed-words embedder and a fake LLM that answers from excerpt [1] and cites it (and `[9]`, which was never sent) when it was given excerpts, and says it does not know otherwise. That covers the whole path both ways: what goes into the request and what does not, the two requests being identical but for the last message, citations mapped back and the stray one dropped, keyword and source scoring, the summary, and a missing index failing the RAG mode while the plain one still answers. The UI suite runs against an empty index on purpose, so it checks the refusal: the plain side answers (the dummy key's 401), the RAG side names the missing index, and the chat's RAG message is refused rather than answered without the documents.

With Ollama and a key: `uv run server.py`, **Index → Build both**, then **RAG → Run all**.

## Day 21: a local index of documents, and two ways of cutting them

The brief: build an indexing pipeline — **chunking, embeddings, a saved index** — with **metadata on every chunk** (source, title/file, section, chunk_id), and **two chunking strategies compared**: fixed size, and by structure.

It lives in a tab of its own, **Index**, next to *Single chat* and *Compare models*. It is not connected to the chat yet: this day builds the index and measures it; answering from it is a later one.

```
Loader (by format)  →  Document: [Section(title_path, start, end)]  →  Chunker (fixed | structural)  →  Embedder  →  SQLite
 .md  headings           one shape for every format:                    Chunk + metadata               Ollama        data/index/index.sqlite
 .py  def / class        the file as read, and ranges into it            chunk_id, source, title,       bge-m3        vectors and metadata
 .txt the whole file                                                     section, position, range       1024-d        in the same row
```

Every stage is a module of its own in `rag/`, and each one knows only the stage before it: a chunker reads a `Document` and never a file, the embedder reads strings and never a chunk, the store reads chunks and vectors and never an HTTP response. The package imports nothing from the app around it — `indexing_api.py` is its only caller today, and the chat will be the next.

### The embedder is a seam, and Ollama is on the other side of it

```python
class Embedder(ABC):
    name                      # "ollama:bge-m3" - written next to every index built with it
    embed_documents(texts)    # -> (n, dim) unit vectors, in batches, with progress
    embed_query(text)         # -> (dim,)
    status()                  # is the server up and the model pulled - for the UI
```

`OllamaEmbedder` is one subclass: `POST /api/embed` on the local Ollama, a batch of strings in, vectors out, plain HTTP like every other model call in this app. The model is **bge-m3** — multilingual, 1024 dimensions, 8k context — because the corpus is written in English and the questions are asked in Russian, and a Russian question has to land next to the English paragraph that answers it. Vectors are normalised whatever the model returns, so a dot product is a cosine everywhere downstream.

Another model is an environment variable (`EMBEDDING_MODEL=ollama:qwen3-embedding`); another provider is a subclass and a line in `PROVIDERS`. Documents and queries are separate calls because some models want them marked differently (e5's `passage:`/`query:`); bge-m3 wants neither, and the prefixes default to empty.

```bash
brew install ollama && ollama serve     # once, and keep it running
ollama pull bge-m3                      # ~1.2 GB
```

Without Ollama the whole app still starts; the tab says *Ollama is not answering* in red, and Build and Search refuse with the same words. Chunking and its preview need no model at all.

### Loaders: the structure a format actually has

| Format | Sections come from | Title |
|---|---|---|
| Markdown | ATX headings `#`…`######`; a `#` inside a ``` fence is a shell comment, not a heading | the single `#` heading, if there is exactly one — then it is not part of the path |
| Python | the syntax tree: module docstring, top-level `def`/`class`, methods one level in; what is between them is "module level" | the file name |
| Text | nothing to find: the file is one section | the file name |

A section is a **range into the file as read**, not a copy, and has a **title path**, not a title: `("Day 20: …", "Routing: the name is the route…")`. The README alone has a dozen sections called *Checking it*; the path is what tells them apart, and a prefix of it names a whole subtree. A `.py` that does not parse is loaded as plain text rather than failing the corpus; a format with no loader is skipped and listed as skipped.

### Two strategies

**fixed** — windows of `size` words over the whole file, each starting `size − overlap` words after the last. It never looks at a section. It is the baseline.

**structural** — one chunk per section. A section over `max_words` is split on blank lines (never inside a code fence) and the paragraphs packed back up; a section under `min_words` is merged with a neighbour under the same heading, so a two-line heading does not become a chunk that means nothing alone.

**Overlap M** means the same in both — how many words at the start of a chunk repeat the end of the one before — and is a slider in the tab. In *fixed* it is on every boundary; in *structural* only where a long section had to be split. It is paid for in duplicate text, and the stats say how much (`redundancy`: words embedded ÷ words in the corpus).

**Context prefix** (`with_context`) puts `file › section path` in front of the text that is *embedded* — not the text that is stored or shown. It turned out to matter more than anything else here; see below.

### Metadata, and what it is for

Every chunk carries `chunk_id` (`structural:README.md#0042`), `source`, `title`, `section` (the path, as a label), `position`, its character range `start`–`end`, `n_words`, `sections_spanned` and `merged`. Today it does three jobs:

- **the check is fair** — whether a chunk is "the answer" is decided by laying its character range over the expected section's, whichever strategy cut it; comparing labels would penalise a fixed chunk for starting three lines before the heading it is about;
- **the numbers exist** — "crossed a section boundary", "split a code block" are read off the ranges;
- **you can see what happened** — the chunk browser shows every chunk's id, section and range, with the overlap highlighted.

The fourth job — citing the source in an answer — is the next day's.

### Storage: one SQLite file

`data/index/index.sqlite`: `documents` and `sections` (the corpus, shared by every index), `indexes` (one row per strategy: params, embedder, corpus hash, stats, timings), `chunks` (metadata, text and the vector as a float32 BLOB, in one row) and `embedding_cache`.

At ~800 chunks a brute-force cosine over a numpy matrix takes a millisecond, so FAISS would buy nothing and cost a binary dependency and a second file to keep in step with the metadata; JSON would store the vectors as decimal text and could only be filtered by reading all of it. **The embedding cache** is keyed by embedder and by the exact text embedded: move the overlap slider and rebuild, and only chunks whose text changed go to the model. **Reset index** drops an index's chunks and vectors and keeps the cache; **Clear embedding cache** is the separate button for making the model do all of it again.

### The retrieval check: questions that say where their answer is

A question names **where its answer lives**: a file and, optionally, a section — meaning that section and everything under it; several places may be listed when the answer is written down twice (the README and a module's docstring often both explain the same thing). Each index is searched, and the rank of the first chunk *inside* one of those places is scored:

- **hit@k** — the answer was in the top k;
- **MRR** — the mean of 1/rank of the first right chunk (0 when it is not in the top `depth`);
- **words to reach it** — how much a model would read, top down, before the answer.

"Inside" means at least half of the chunk lies in the expected range, or the chunk covers at least half of it. A question whose expected place is not in the corpus any more (renamed heading, removed file) is flagged and left out of the averages — then it is the question that is wrong, not the index.

The questions are edited in the tab — a file dropdown and a section dropdown built from that file's outline, `+ another place`, Save — and stored in `data/index/questions.json`. 25 are seeded on first start (`seed_questions.json`), most in Russian, about this README and the modules. Add a file, add the questions it should answer, run the check, and the comparison table shows both strategies against them.

### The comparison — this corpus, bge-m3

The corpus is this folder's `README.md` (every day, since each README is the last one plus a chapter) and its top-level `.py` modules: 24 files when these numbers were taken, two formats, ~129,000 words. Defaults on both sides: fixed `size=200, overlap=40`; structural `max_words=300, min_words=60, overlap=40`, context prefix on. 25 questions, so one question is 4 points of hit@k — read differences under ~8 points as noise.

| | fixed | structural |
|---|---:|---:|
| chunks | 811 | 774 |
| median words | 200 | 174 |
| cut mid-sentence | 80% | **2%** |
| cross a section boundary (unplanned) | 65% | **0%** |
| split code blocks | 63 | **12** |
| redundancy (overlap cost) | ×1.244 | **×1.066** |
| first full embedding, M-series, bge-m3 | ~45 s | ~45 s |
| **hit@1** | 36% | **40%** |
| **hit@3** | 60% | **72%** |
| **hit@5** | 68% | **92%** |
| **MRR** | 0.51 | **0.60** |
| not in the top 10 | 4 | **2** |
| words read to reach the answer | 584 | **433** |

Structure wins, but **not for the reason one would guess** — and moving the controls in the tab is how that was found out:

| variant | chunks | redundancy | hit@5 | MRR |
|---|---:|---:|---:|---:|
| fixed, overlap 0 | 654 | ×1.00 | 64% | 0.43 |
| fixed, overlap 40 (default) | 811 | ×1.24 | 68% | 0.51 |
| fixed, overlap 100 | 1,274 | ×1.97 | 68% | 0.54 |
| fixed + `file › section` prefix | 811 | ×1.24 | 76% | 0.55 |
| structural, **no** prefix | 774 | ×1.07 | 64% | 0.51 |
| structural, prefix built from the README's **title** (first version, measured before this chapter was written) | 749 | ×1.07 | 64% | 0.42 |
| structural, overlap 0 | 758 | ×1.00 | 88% | 0.57 |
| **structural, overlap 40 + `file › section` prefix (default)** | 774 | ×1.07 | **92%** | **0.60** |
| structural, overlap 120 | 830 | ×1.24 | 88% | 0.60 |
| structural, `max_words=200` | 1,048 | ×1.14 | 84% | 0.59 |

- **Clean cuts on their own buy nothing.** Structural chunks without a prefix do no better than fixed windows: a section boundary is a better place to cut, but the chunk still does not say what it is about.
- **The section path is what the vector needs** — and only a structural chunk has a true one. A fixed window's "section" is wherever it happened to start; the same prefix helps it a little (+8 points hit@5), and a structural chunk a lot (+28).
- **Overlap matters where cuts are blind.** For fixed windows 0 → 40 words lifts MRR from 0.43 to 0.51; 40 → 100 doubles the text embedded for another 0.03. For structural chunks it only applies where a long section had to be split, so it moves the numbers within noise — and it is the reason structural splits any code blocks at all (0 at overlap 0, 12 at 40, 23 at 120): the carried-over words drag a fence opener into the next piece.
- **Fixed windows are not stable under edits — and neither are their scores.** Writing this chapter changed ~2% of the corpus, all of it near the top of the README, and every window below the edit moved. The same 25 questions, three versions of the same README:

  | README version | fixed hit@5 / MRR | structural hit@5 / MRR | re-embedded on rebuild, fixed / structural |
  |---|---:|---:|---:|
  | before this chapter | 72% / 0.50 | 92% / 0.60 | — |
  | first draft of this chapter (the tables above) | 68% / 0.51 | 92% / 0.60 | — |
  | with the tables above written in | 52% / 0.33 | 92% / 0.60 | 232 / 7 |
  | with this table written in | 64% / 0.46 | 92% / 0.60 | 230 / 2 |

  Nothing a question asks about changed; only where the fixed windows fell did. A structural index re-cuts only the sections that were edited, so it re-embeds 7 chunks instead of 232 and answers the same. The fixed column will have moved again by the time you build this README yourself — which is the point.
- **A long constant prefix is poison.** The first version used the document title; this README's title is a sixty-word sentence, the same sixty words in front of every chunk pulled all their vectors together, and structural lost to fixed (MRR 0.42). Switching to the file name fixed it. Metadata goes into an embedding only when it *distinguishes* chunks.
- Some misses left are arguably not misses: *"what happens when a pipeline step returns a bad result"* lands on day 14's "the model answers, the code reads it" and on the scenario judge in `scenarios.py` — checking a result, in other words. The question set is where that gets settled: add the place as another expected answer, or rephrase the question.

### What is deliberately not here

- **Answering from the index.** The chat does not read it yet; retrieval-augmented answers with sources are the next day.
- **An ANN index.** Brute force is exact and a millisecond at this size.
- **Semantic chunking** (cutting where embeddings of neighbouring sentences diverge) — a third strategy that would cost a model call per sentence to *build*; the two here are what the brief asked to compare.
- **Model tokens as the unit.** Sizes are in words, so they mean the same whichever embedder is plugged in and can be checked by eye; for bge-m3 a word is ~1.3–1.5 tokens.

### Checking it

```bash
uv run python -m unittest discover -s tests -v   # test_indexing.py: 21 tests, no model needed
npm test                                         # + the Index tab: pipeline, overlap slider, questions, chunk browser
```

`test_indexing.py` uses a bag-of-hashed-words embedder: deterministic, instant, and good enough that a query sharing rare words with one chunk ranks it first — which is all the store, the cache, the check and the API need to be exercised end to end.

With Ollama running: `uv run server.py`, open **Index**, **Build both**, **Run check**.

## Day 20: several servers, one agent, a long flow

The brief: register several MCP servers, and have the agent **pick the right tool, route each request correctly, and carry out a long interaction** - then check a scenario that uses tools from different servers, and that the choice and the order of the calls were right.

Five servers are registered and switched on out of the box: four of ours and one of somebody else's.

| Server | Where | Tools | What it knows |
|---|---|---|---|
| `cape-town-events` | ours, day 18 (`:8788` or your VPS) | 7 | what's on in Cape Town, collected on a schedule |
| `google-maps` | ours, days 17 and 19 (`:8787`) | 3 | distances, restaurants, where a group should meet |
| `weather` | **ours, new** (`:8789`) | 1 | the forecast, with an evening outlook and a dry/rain verdict (Open-Meteo, no key) |
| `planner` | **ours, new** (`:8790`) | 4 | saves a timed itinerary to `data/plans/`, refuses steps out of time order |
| `deepwiki` | mcp.deepwiki.com | 3 | any public GitHub repository's wiki |

Eighteen tools in one request. The flow the day is built around needs four of the servers, in an order that is not negotiable:

```
you   "Jazz this weekend with Anna (Kloof St 10) and Ben (Bree St 80), I'm at the Waterfront.
       An evening without rain, dinner first somewhere fair for all of us, how long from there
       to the venue - and save the plan."
        │
agent ─► DeepSeek  + guide: which server is for what, today's date, how to chain       (system message)
        │
 R1 ────┼─► cape-town-events__get_summary{interest: jazz}  ┐ side by side:
        └─► weather__get_forecast{Cape Town, 7 days}        ┘ neither needs the other
 R2 ──────► google-maps__plan_meetup{people: ←you, query: dinner}
 R3 ──────► google-maps__compute_distance{origin: ←R2 winner, destination: ←R1 venue}
 R4 ──────► planner__save_plan{day: ←R1/R1 dry day, steps: ←R1 concert, ←R2 place, ←R3 13 min}
        │
        ▼
   answer + the cards of every server + the flow + (for a scenario) the checks
```

### Choosing: the model is told which server is for what

A tool's description says what *it* does. Nothing said what a *server* is for, or that a request touching three of them has an order to it - and with eighteen tools on offer, that is the difference between `plan_meetup` and three `find_restaurants`. So the request carries one more system message, right after the system prompt, written by `orchestrator.guide()`:

```
--- Tools: which server does what ---
Today is Sunday, 2026-09-27. You can call tools on 5 MCP servers. A tool's name is
`<server>__<tool>`: the part before the two underscores is the server it runs on, and
the call goes to that server only.

* weather - Weather (local): Weather forecasts for up to 16 days, from Open-Meteo. Use
  get_forecast whenever a plan depends on the weather: whether an evening will be dry, …
  tools: weather__get_forecast
* planner - Planner (local): Saves plans … Call save_plan LAST, once everything in the
  plan has been looked up with the other tools …
  tools: planner__save_plan, planner__list_plans, planner__get_plan, planner__delete_plan
…
How to work through a request that needs several of these:
1. Decide which servers the request needs, and in what order: a step that needs another
   step's result comes after it.
2. Calls that do not depend on each other go in the same round - ask for them together.
3. Pass values from one tool's result to the next exactly as they came back …
…
```

What each server is for is not written in this app. It is the `instructions` string the server sends back from `initialize` - MCP's own place for exactly this. The app had been storing it since day 16 and never using it; now it is the one line per server the model chooses by. A server that says nothing about itself falls back to the note in `KNOWN_SERVERS`. And **today's date**, because "this weekend" without one is a guess.

### Routing: the name is the route, and nothing is guessed

`weather__get_forecast` goes to `weather`, at the URL in the MCP panel, and nowhere else. The orchestrator is built fresh for every turn from the stored catalogues - switched-off and silent servers contribute nothing - and a call that does not route never leaves the process. It comes back as an error the model can act on, naming the names that *would* have routed:

| The model asked for | It is told |
|---|---|
| `google-maps__get_forecast` | Google Maps (local) has no tool 'get_forecast' - its tools are … Did you mean `weather__get_forecast`? |
| `save_plan` | 'save_plan' has no server in front of it. Did you mean `planner__save_plan`? |
| `weather__get_forcast` | Did you mean `weather__get_forecast`? |
| `deepwiki__ask_wiki_question` with DeepWiki switched off | there is no server 'deepwiki' connected |

Nothing is rerouted silently. A model told "no such tool" invents another one; a model told the right name calls it on the next round - and the flow shows both calls, which is the honest record.

### A long flow, and what one server never made anyone think about

`agent.py`'s loop was built on day 17 for a tool or two. A flow over four servers is five rounds when everything goes right, so:

- **ten rounds, not five**, and the last is asked with `tool_choice: "none"` - a flow that runs out of rounds ends in an answer built from what it has, not in a request for one more tool;
- **calls asked for in the same round run side by side** (up to four at once). The model asked for them together, so none can need another's result; running them in a row would only add their latencies up. Their `tool` messages go back in the order asked, whatever order they finished in;
- **24 calls per turn at most**, because a round can ask for several and the round budget alone does not bound the bill. A call past it gets a `tool` message saying so rather than being run;
- **a server that failed is not called again that turn.** A transport error (refused, timed out, HTTP 500) marks the server down; every later call to it returns at once - "did not answer earlier in this turn" - so a dead server costs its timeout once, not once per round. A tool that *answered* with an error (the planner refusing a plan out of order) is not a server that is down;
- **an identical call is answered from the first one.** A long flow is where a model asks for the same forecast twice - once to choose the day, once to write the note in the plan. Same server, same tool, same arguments: the same answer, marked `cached`, no request made.

All of that is state, and all of it lives for exactly one turn, in the orchestrator the turn was built with. The agent still does not know that there is more than one server: it is handed `functions`, a guide and a callable, as on day 17.

### The flow is written down: which server, which round, what fed what

After the turn, `orchestrator.build_flow` turns the calls into a record: for every call, its round, its server, whether it ran side by side with another, whether it was cached - and, for every argument, **where the value came from**: the user's message, an earlier call's result, or neither (the model's own words, like `query: "dinner"`).

```
▾ ⇄ flow · 5 calls in 4 rounds across 4 servers: cape-town-events → weather → google-maps → planner · 7 handoffs across servers
  R1 │ ✓ #1 [Cape Town events] get_summary · 180 ms
     │ ✓ #2 [Weather (local)]  get_forecast · 240 ms
  R2   ✓ #3 [Google Maps]      plan_meetup · 1911 ms       ← you → people[0], people[1]
  R3   ✓ #4 [Google Maps]      compute_distance · 410 ms   ← #3 plan_meetup → origin · #1 get_summary → destination
  R4   ✓ #5 [Planner (local)]  save_plan · 12 ms           ← #1 get_summary → day · #2 get_forecast → day ·
                                                             #1 get_summary → steps[1].time · #3 plan_meetup → steps[0].place ·
                                                             #4 compute_distance → steps[1].note · …
```

(What a run of `evening-out` is drawn as - the shape, with illustrative timings.)

A value is traced by finding it in an earlier result: exactly, or on word edges inside a longer string (`The Alma Cafe` inside `The Alma Cafe, Rosebank`), with ISO timestamps also read as a day and a time (`2026-10-03T19:30:00+02:00` feeds `day: 2026-10-03` and `time: 19:30`). Two rules keep it honest: only results from **earlier rounds** count - calls asked for together cannot have fed each other, whatever they share - and a short value found somewhere in a paragraph (`dinner` in an event's description) is a coincidence, not a handoff. It is a heuristic, and it is labelled as one: "found in #1's result", not "the model read it from #1".

The flow is stored on the answer, like the cards, and drawn under them - collapsed, except when it carries checks.

### Scenarios: the order written down before it runs

"The agent chose the right tools in the right order" is a claim about a transcript, and a claim nobody checks is a claim about how the demo went. So `scenarios.py` holds three requests that need several servers, each with what a correct run must look like, and the checks run against the flow after every run:

| Check | Passes when |
|---|---|
| `calls a` | `a` was called and succeeded |
| `a before b` | the first successful `a` ran in an **earlier round** than the first successful `b` - side by side is not before |
| `a feeds b` | an argument of a `b` call was found in an `a` result: the data travelled, not just the order |
| `ends with a` | the last successful call was `a` - the delivery step |
| `only …` | nothing was called on any other server |
| `every call routed` | no unknown tool, no call to a server already down |

A turn that called nothing passes nothing: "nothing stray was called" is true of a turn that gave up, and a check that rewarded that would be worse than none.

| Scenario | Servers | What it proves |
|---|---|---|
| `evening-out` | events, weather, maps, planner | the flow above: 15 checks, including the venue from the listing reaching Routes and the plan |
| `weather-meetup` | weather, maps, planner | the weather *decides* the next call: a terrace bar if dry, a café if not - so the forecast must come first |
| `study-sunday` | DeepWiki, weather, planner | a remote server in the middle of a flow of local ones; no keys at all |

Run one from the **Scenarios** panel in the top bar (it opens a new chat and sends the request with the scenario named; each row says which of its servers are missing), or from a terminal against the running app:

```bash
uv run scenarios.py                     # the list, and what each needs
uv run scenarios.py weather-meetup      # run it: the answer, the flow, the checks
```

What the terminal prints (illustrative - the place is whatever `plan_meetup` ranked first):

```
CHECKS  9/9  Weather decides the place: weather → meeting point → plan
  PASS  calls weather/get_forecast                                 step #1
  PASS  weather/get_forecast before google-maps/plan_meetup        #1 in round 1, #2 in round 2
  PASS  google-maps/plan_meetup feeds planner/save_plan            #3.steps[0].place = 'The Waiting Room, 273 Long St' from #2
  PASS  ends with planner/save_plan                                last was #3 planner/save_plan
  …
```

A scenario is an ordinary message: asked, answered and stored like any other, in a chat you can carry on in. The id only says which checks the flow is held to - and a message after it is held to none.

### Two new servers, and why these two

A flow worth routing needs servers that know different things. The weather is what every plan for going out runs into and neither Maps nor a listing can answer; a planner is where a flow **ends** - every step before it finds something, the last one does something with what was found, in a form that outlives the chat.

- **`weather_mcp_server.py`** - Open-Meteo, chosen because it needs no key and no account: a day about orchestrating servers should not start with a sign-up form. Per day: temperatures, chance and amount of rain, wind, and the same for 17:00-22:00, because an evening out is decided by the evening. The verdict - dry, showers possible, rain - is a threshold, so it is code, not the model. Place names go through Open-Meteo's geocoder, which knows cities and districts, not streets; `lat,lng` for anything exact.
- **`planner_mcp_server.py`** - `save_plan(title, day, steps[])` writes `data/plans/<id>.md` and `.json`. It checks the two things a model assembling a plan out of four other tools' answers gets wrong: the steps are in **time order** (dinner at 17:30 after a concert at 19:30 is refused, with both steps named, so the model fixes the plan rather than the file holding a wrong one), and the day is not in the past.

Four local servers were four terminals, and a flow over four servers is exactly where one of them is forgotten. So **`uv run server.py` starts them itself** and stops them when it stops:

```
$ uv run server.py
[maps   ] starting http://127.0.0.1:8787/mcp  (pid 469)
[events ] starting http://127.0.0.1:8788/mcp  (pid 471)
[weather] starting http://127.0.0.1:8789/mcp  (pid 473)
[planner] starting http://127.0.0.1:8790/mcp  (pid 475)
…
[mcp] cape-town-events: 7 tools
[mcp] google-maps: 3 tools
[mcp] weather: 1 tools
[mcp] planner: 4 tools
```

Each is still a process of its own, reached over HTTP like DeepWiki - the app presses the button, it does not swallow the server. The app does not wait for them: once their ports answer, a background thread asks each for its tools, so the first message carries their catalogues with nobody pressing refresh, and a server that fails to start is a red row in the MCP panel rather than an app that will not boot. Three rules:

- **a port that already answers is used and left alone** - started in another terminal, or the events server kept running 24/7 on its own - and is not stopped on the way out, because it was not the app's to stop;
- **the events server is skipped when `EVENTS_MCP_URL` points at your VPS**;
- **`MCP_AUTOSTART=0`** in `.env` switches it off. Then start them by hand, all at once or some - `uv run run_mcp_servers.py [maps events weather planner]` - and press **Scenarios → Refresh servers** (`POST /api/mcp/refresh`, every server switched on at once).

DeepWiki is not refreshed at boot: it is on the internet, and an app that contacted somebody else's server every time it started would be doing it on your behalf without being asked.

### Checking it

```bash
uv run python -m unittest discover -s tests -v   # test_orchestration.py: 34 tests
npm test                                         # the page: the flow, the cards, the Scenarios panel
```

`tests/test_orchestration.py` has two halves. The first is the orchestrator against a caller made of dicts: each call reaches the server that owns it, the wrong-server / bare-name / misspelt / disconnected cases each name the right tool, a dead server is tried once per turn, an identical call is made once; the agent's side - calls of one round really run at the same time (a barrier that breaks if they ran one after the other), come back in the order asked, the last round is `tool_choice: "none"`, calls past the budget are answered and not run; the flow's provenance, including that calls side by side do not feed each other; and the checks, including the orders and handoffs they must refuse.

The second half is the long flow over the wire. **Four real MCP servers** - events, weather, maps, planner - each on its own port, reached over HTTP by the hand-written client exactly as the app reaches them; only the world outside them is fake (Google and Open-Meteo answering from dicts, a temp events database with two jazz nights, one on a rainy evening). The model is a script that **reads the tool results it is given**: it picks the concert on the evening the weather server says is dry, meets at the place `plan_meetup` ranks first, measures the trip from there to the venue the listing gave, and saves a plan out of all of it. Then the test reads **the plan on disk**: the dry day, dinner at the fairest place, the venue, "13 min by car" - so a value lost or changed between two servers shows up as a wrong file, not as a log line. And `evening-out`'s 15 checks pass against that flow.

With real keys: `uv run server.py`, **Scenarios → Run in a new chat**.

### What is deliberately not here

A planner the model writes code for - the flow is chosen by the model and routed by code, and a DAG language between the two would be a third thing to get wrong. Retrying a failed call automatically: the model reads the error and decides, which is the point of an error it can read. Parallelism across rounds - a round waits for all its calls, because the next round is asked with all of them in front of it. Holding sessions open across the calls of a turn: a local handshake is a few milliseconds, and day 16's argument against a pool still stands.

## Day 19: a pipeline of five steps, behind one tool

The brief is a chain: one tool gets the data, the next one works on it, the last one delivers - and it has to run by itself and hand the data along intact. This day builds one on the Google Maps server from day 17, and it is useful on its own: **where should a group of people meet?**

```
you   "Anna is on Kloof St, Ben on Bree St, I'm at the Waterfront - where do we get coffee?"
        │
agent ─► DeepSeek ─► maps__plan_meetup{people:[…], query:"coffee"} ── MCP ──► maps_mcp_server.py
                                                                               │
          ┌────────────────────────────── one call, five steps ────────────────┘
          │
          ├─ 1 geocode   3 addresses → 3 points            Places searchText, one per address
          ├─ 2 center    3 points → the middle + a radius  code: mean on the sphere, radius from the spread
          ├─ 3 search    the middle → up to 20 places      Places searchText, restricted to the circle
          ├─ 4 matrix    3 people × 20 places → 60 trips   Routes computeRouteMatrix, one request
          └─ 5 rank      60 trips → options, fairest first code
          │
          ▼
   { options[] with every person's travel time, verdict, trace[] }  ─► cards in the chat
```

`find_restaurants` answers "where to eat near X". It cannot answer this, because *near whom* is the whole question: the answer is the place that is fair to everybody, and that takes a travel time from every person to every candidate - a matrix - and a choice made over it.

### Why the chain is code, and the model sees one tool

The model could run the chain itself: three tools, three rounds of the day-17 loop. It would then be copying twenty places out of one tool's answer into the next one's arguments, sixty durations out of the matrix into its own head, and doing the arithmetic there. That is exactly where "the data passed between the tools correctly" stops being true - a model retyping coordinates gets one digit wrong, a model comparing sixty numbers compares fifty-eight - and every round trip is paid for in tokens twice, once going out and once coming back.

So the chain lives where the data is. `plan_meetup` is one MCP tool; the five steps are plain functions inside the server, and what passes between them is Python values, not text a model re-read. The model makes **one** call, gets **one** structured answer, and does the part it is good at: picking between three options and saying why, in the user's language.

Only `plan_meetup` is exported. The steps are not tools of their own on purpose: a `geocode` tool on the shelf is a few hundred tokens on every request for something no user asks for by name.

### Each step is checked before the next is given its output

A chain that runs automatically fails automatically too - quietly, at the end, as a confident wrong answer. So every step's output is checked against its input before it is handed on, and a check that fails stops the chain **at the step that broke it**:

| Step | Receives | Produces | Checked before handing on |
|---|---|---|---|
| geocode | 2-6 addresses (`Name: address`, an address or `lat,lng`) | one point per person | one point per person, no more, no less |
| center | the points | the middle, and a search radius | the middle lies inside the people's bounding box |
| search | the middle, the radius, the query | candidate places | unique, all inside the circle, no more than the matrix can take |
| matrix | the points and the places | a time for every person × place | every pair answered exactly once, placed by its indices |
| rank | the places and the matrix | options, fairest first | every option came from the search, with one trip per person |

The matrix is the handoff most worth checking, because Google does not answer it in order. Each element says which origin and which destination it is for, and it is those indices - not the element's position in the array - that decide its cell. Two more things the wire format does to a client that has not read it closely: an index of `0` is **left out** (proto3 JSON omits zeros), and an unreachable pair is an element with `condition: ROUTE_NOT_FOUND`, not a missing one. A pair that *is* missing, or answered twice, stops the chain: "11 of 12 pairs" is not something to rank over.

When a step fails, the error the model reads says which step and what had been done by then - `plan_meetup stopped at step 4 (matrix): the matrix came back with 11 of 12 pairs. Done before it: geocode: 3 points; center: …; search: 4 places.` "No such place" at step 1 and "nobody can get there by transit" at step 5 are different problems, and the model should be able to tell the user which one it was.

### The trace comes back with the answer

Every run returns `trace[]`: per step, what it **received**, what it **produced**, what was **checked**, and how long it took. It is drawn under the options, collapsed:

```
▸ pipeline · geocode → center → search → matrix → rank · 1911 ms
  1. geocode  3 addresses → 3 points · 410 ms
     ✓ one point per person
  2. center   3 points → -33.92430,18.42000, radius 800 m · 0 ms
     ✓ inside the people's bounding box
  3. search   'coffee' within 800 m of the middle → 4 places · 620 ms
     ✓ unique, all within the radius, at most 20 for the matrix
  4. matrix   3 people × 4 places → 12 routes, 0 unreachable · 880 ms
     ✓ all 12 pairs answered once, put in place by their indices
  5. rank     4 places × 3 trips → 4 reachable by all, 2 fair · 1 ms
     ✓ every option came from the search, with one trip per person
```

That is the "check that the chain ran automatically" of the brief, on screen: one receipt for one tool call, and under it five steps nobody asked for separately.

### Fair first, then good

"Fair" is **the longest trip anybody has to make**. Not the average: a place ten minutes from two people and fifty from the third has a fine average and is not a meeting point. Two places whose longest trips differ by a minute are equally fair, though, and then the question is which one is better - so everything within five minutes (or a tenth of the longest trip, whichever is more) of the fairest counts as fair, and those are ordered by rating. The rest follow, fairest first.

Ratings are pulled toward 4.0 as if every place had twenty more reviews of 4.0, so a 5.0 from three reviews does not beat a 4.7 from nine hundred. The verdict line is written by code, not by the model: "Rooftop Coffee: the longest trip is 17 min (Person 3), and nobody travels more than 2 min longer than anyone else."

On the card, every person's trip is a bar, and the bars of all the options share one scale - fair is something you see, equal bars, rather than a number you compare.

### The numbers that shape it

- **2 to 6 people.** One is not a meetup; seven start being an event.
- **The matrix limit sets the search size.** Google allows 625 pairs per request, but only 100 by transit - so six people by transit get 16 candidates, not 20. The search asks for exactly as many as the matrix can take.
- **The radius follows the spread.** Unless the model gives one, it is 40 % of the distance from the middle to the farthest person, between 800 m and 10 km: two people across the street still get a neighbourhood, two people across the city get a district.
- **Three Google requests plus one per address.** An address given as `lat,lng` costs nothing to geocode.
- **No new key and no new API.** Places (New) and Routes were already enabled for day 17; the route matrix is part of the Routes API.

### Checking it

```bash
uv run python -m unittest discover -s tests -v      # test_meetup.py: 21 tests, no network
npm test                                            # the page, including the meetup card
```

`tests/test_meetup.py` replaces `google_post` with a Google made of dicts, and the fake answers **from what each request says**, not in call order: places are geocoded by the text asked for, and the matrix is computed from the coordinates in its body - then shuffled, with its zero indices left out. So a place handed from step 3 to step 4 with the wrong coordinates, or a matrix put together by position instead of by index, comes back with the wrong durations and the wrong winner, where the tests can see it. (Replacing the index lookup with position fails four of them - which is how the tests were checked.) The rest: each place a step stops at, and what it says; the matrix-size cap; the whole tool through `mcp.call_tool`, including arguments refused before any step runs.

With a real key, `uv run maps_mcp_server.py` and `uv run server.py` as on day 17, and ask: *"Anna is at Kloof Street 10, Ben at Bree Street 80, I'm at the V&A Waterfront. Where do the three of us meet for coffee? We're driving."*

### What is deliberately not here

A file at the end of the chain - the answer is the card in the chat, and the chat already keeps it. The steps as tools of their own. Travel at a time other than now (`departureTime` is one field away). Weighing people differently - the one with a child, the one on a bicycle - which is a different fairness, not a different pipeline.

## Day 18: a tool with a clock

Every tool so far - ours on day 17, other people's on day 16 - is a function. The model asks, the tool answers, and between two questions nothing happens at all. The agent exists for as long as an HTTP request does.

This day gives it something to do while nobody is asking. `events_mcp_server.py` is a second MCP server of our own, and the first one with a **clock**: it watches Cape Town's event listings on a schedule, keeps what it finds in SQLite, and every run that turns up something new leaves a **digest** behind.

And it lives **somewhere else**. The server runs 24/7 on a VPS; the agent is this app, started on your own machine when you want it. It reaches the server through an SSH tunnel - to the agent it is one more MCP URL, as DeepWiki is - to set watches up, and when it comes back after a day or a week it picks up everything collected meanwhile and posts it into a chat called **Digests**, written by the model.

```
YOUR MACHINE (when you want it)                     VPS (24/7, an ordinary user, no root)
────────────────────────────────                    ──────────────────────────────────────────
you  "Follow jazz and theatre in Cape Town,         events_mcp_server.py   127.0.0.1:8788/mcp
      every morning."                                 ├─ kept up by cron + keepalive.sh
        │                                             ├─ Scheduler thread: daily at 07:00 Cape Town
agent.py ─► DeepSeek ─► create_watch{...} ── MCP ──►  ├─ collectors: JSON-LD · Events Calendar · iCal
                                      ssh -L tunnel   ├─ SQLite: watches · events · runs · digests
server.py ─ digests.Courier ◄── events://digests ──   └─ keeps collecting while the agent is off
   │   "anything new since my cursor?"
   ▼
DeepSeek writes it ─► the "Digests" chat, with cards   (one "while you were away" digest after a gap)
```

### Who holds the clock, and why it is the server

Not the model: it has no clock, and it cannot wake itself up. Not the agent app either, and that is the less obvious half: MCP is request → response, and this app holds no session open (day 16's decision) - so there is nothing a server could call back on. The schedule therefore lives in the only process that is always running and owns the data: the MCP server. The model's job is to **set a watch up** and to **read the result**; running it is code's.

The same argument decides where each half runs. The server is the part that must not stop, so it goes on a machine that does not stop; the agent is a program you open and close, so it runs where you are. Nothing about the protocol changes between the two - an MCP server on a VPS is a URL, exactly as DeepWiki is - which is the point of putting the scheduler behind MCP rather than inside the app.

That split is also the answer to "why is this an MCP tool at all rather than a cron job": the tool is the scheduler's *interface*. The model says what to watch in words, the tool turns that into a row in `watches`, and a week later the same model reads back an aggregate it did not have to compute.

### Code counts, the model writes

A day of collecting is a hundred-odd listings. Handing those to a model would be paid for in tokens and still come back with the arithmetic wrong. So the server does every part of the work that has one right answer:

- **dedup** - the same show on two listings (same name, same day) is counted once;
- **what is new** - `first_seen_at` later than the watch's last digest. This is the one fact a list of current events cannot tell you and a database can, which is why the brief's "store the data" is not a formality here;
- **filtering** - upcoming only, in the Cape Town area (coordinates within 50 km, or an address in a known suburb), matching the watch's interests on whole words;
- **counts** - total, new, this weekend, per interest, per source, per day.

`get_summary` and every digest return those counts over *all* matching events, plus the nearest few as cards. The model gets exact totals and a handful of events it can actually say something about - "two or three sentences, date and time, the link" is the format it is asked for.

Collecting costs **no tokens at all**. A digest costs one request, once a day, and it goes on the message it paid for like every other price in this app. If the model cannot be reached, `digests.fallback_text` renders the same aggregate as a plain list and the message says which of the two wrote it: a failed call costs the digest its prose, never its content.

### Nine sources, and why only nine

Every source was checked against the live site before it went on the list, and the test was one question: **can code read it?** A source that needed a model to parse would put a bill on a clock, which is the one thing this day must not do.

| Source | Kind | Read as |
|---|---|---|
| Quicket (search: Cape Town) | ticketing | schema.org JSON-LD, 3 pages |
| Eventbrite - Cape Town | ticketing | JSON-LD, filtered by coordinates |
| Luma - Cape Town | community (tech) | JSON-LD |
| Meetup - Cape Town | community (tech) | JSON-LD |
| Cape Town Etc - What's on | city guide | iCal |
| Artscape Theatre Centre | venue | The Events Calendar REST API |
| Kalk Bay Theatre | venue | The Events Calendar REST API |
| Norval Foundation | venue | The Events Calendar REST API |
| The Alma Cafe | venue (jazz) | JSON-LD |

Left out, having been checked: Computicket and Howler render their listings in the browser; ra.co, Cape Town Tourism and The Inside Guide refuse a script outright; a dozen venue sites publish their programme as prose. All of `robots.txt` on the nine above allow what is done here - one request per listing per run.

Two things the live sites taught that no specification would: the Alma stamps local times with `+00:00` (a show its own page says is at 6:30 pm arrives as `18:30+00:00`), and Eventbrite's search results carry a date and no time. The first is a flag on the source; the second is `enrich` - a *new* event with no description or no time is looked up once on its own page, which has both in the same schema.org shape.

### Periodic and deferred

The brief asks for either; this has both, because they answer different questions:

- **Periodic** - a watch runs daily at a time of day, Cape Town time (the default is 07:00), or every N minutes. A scheduled run leaves a digest only when there is something new: a daily "nothing new" would be noise.
- **Deferred** - `run_now` queues a run and returns in milliseconds. Collecting nine sites takes a minute, which is longer than this client's MCP timeout and longer than anybody should wait for a chat bubble. The result arrives later as a digest - and an asked-for run always leaves one, because somebody is waiting for an answer. For "what's on this weekend?" the model does not wait at all: `get_summary` answers instantly from what is already collected.

### Seven tools and one resource

| | For | What it does |
|---|---|---|
| `list_sources` | model | The nine sources, with kinds and tags to pick from |
| `create_watch` | model | Interests (with keywords), sources, schedule; queues a first run |
| `list_watches` | model, panel | Every watch, its next run and how the last one went |
| `update_watch` | model, panel | Pause, resume, reschedule, change interests or sources |
| `delete_watch` | model, panel | Stop and forget one |
| `run_now` | model, panel | Deferred collection |
| `get_summary` | model | The aggregate, instantly, from the database |
| `events://digests` | **app** | The digest log, append-only |

The last row is not a tool on purpose. In MCP, tools are what the *model* decides to call and resources are what the *application* reads; delivering a digest is the app's job, not something a model should decide to fetch. So `mcp_client` grows `read_resource` - day 16 listed resources among the things it did not do, "same protocol, same three lines, different method name", and this is that claim coming due.

The Events panel in the top bar is drawn from the same tools the model uses. The app has no back door into the server's database - which is what keeps the server a server.

### A 24/7 process is a process that gets restarted

Everything here is shaped by the fact that it will be restarted, often, at bad moments:

- **A missed run is made up once.** Each watch keeps `next_run_at` in the database. A server that was down through its slot runs once when it comes back (`catch-up`), and the next slot is computed from *now* - a week of downtime is one run, not seven.
- **A run interrupted mid-way is not left "running".** On start, any run still marked running is marked `interrupted`.
- **Delivery has cursors, on the app's side of the wire.** `data/digests/state.json` remembers, per watch, the last digest posted and where its "new" ended. Restart the app and it carries on; restart the server and nothing is posted twice. The log carries a `db_id`, and cursors into a database that no longer exists are thrown away rather than trusted.
- **Coming back is one digest, not a backlog.** The agent is off most of the time - that is the design. Open it after a week and each watch may have seven digests waiting. Posting them in a row would be seven stale messages and seven model calls, so the courier asks the server for one aggregate instead: `get_summary(new_since=<where the last delivered digest ended>)`, everything first seen since then, counted by the server. It arrives as a single digest marked *while the agent was offline (7 digests in one)*.
- **One source down is a partial run, not a failed one.** The error goes into the run and into the digest ("could not read: …"); the other eight carry on.

### The Digests chat

An ordinary conversation with one difference: its messages were not asked for. Each digest is stored as an assistant message carrying a `digest` block - which watch, what triggered it, who wrote it, and the aggregate - and drawn with a line saying so, the prose, and the events as cards whose titles are links. `get_summary` in any other chat draws the same cards, keyed off the shape of the result as day 17's restaurant cards are.

Because it is an ordinary conversation, you can reply to it: "tell me more about the jazz one" is asked with the digests in front of it. The API wants a question before every answer, so a digest is replayed behind a line that says exactly what happened - *(A scheduled digest was posted here - nobody asked a question.)* - rather than being dropped.

The badge on **Events** counts digests posted while you were away. Opening the Digests chat is reading them.

### Deploying it: the server on a VPS, the agent at home

All the VPS needs is an ordinary user you can `ssh` in as - no root, no open port, no certificate. The server listens on the VPS's own `127.0.0.1`, and your machine reaches it through an SSH tunnel: ssh is already the encryption and the login, so the server adds neither.

```
your machine                                        VPS
agent ─► http://127.0.0.1:8788/mcp ─► ssh -L ─────► 127.0.0.1:8788  events_mcp_server.py
```

**On the VPS**, as that user:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh        # uv, into ~/.local/bin - no root
export PATH="$HOME/.local/bin:$PATH"
git clone -b day18-mcp-scheduler https://github.com/mostfus/ai_advent_challange.git
cd ai_advent_challange/day18-mcp-scheduler
uv sync --frozen --python 3.11                         # fetches Python 3.11 into ~ if the system has another
cp .env.vps.example .env
sed -i "s/^EVENTS_TOKEN=.*/EVENTS_TOKEN=$(openssl rand -hex 32)/" .env
grep ^EVENTS_TOKEN .env                                # the value goes into the agent's .env at home
./keepalive.sh install                                 # into your crontab, and started
./keepalive.sh status                                  # "... Uvicorn running on http://127.0.0.1:8788" / "running, pid ..."
```

`keepalive.sh` is the supervisor you can have without root. There is no systemd service to install, so it is cron and `flock`: cron runs the script every five minutes, and the server holds a lock for as long as it lives - so while it is up the script does nothing, and after a crash or a reboot it starts it again. A run missed meanwhile is made up once, as above. It starts the server in a session of its own, so logging out of ssh does not take it down. To update: `git pull && uv sync --frozen && ./keepalive.sh restart`. The log is `data/server.log`, cut in place at 5 MB; `./keepalive.sh uninstall` takes it out of the crontab and stops it.

**On your machine**, the tunnel - in `~/.ssh/config`:

```
Host events-vps
    HostName <the VPS's address>
    User <your user there>
    LocalForward 8788 127.0.0.1:8788
    ExitOnForwardFailure yes
    ServerAliveInterval 30
```

`ssh -N events-vps` opens it and holds it for as long as it runs (add `-f` to send it to the background). If 8788 is taken on your machine - by a local events server, say - pick any other number on the left of `LocalForward` and use it in the URL below. Then, in the agent's `.env`:

```
EVENTS_MCP_URL=http://127.0.0.1:8788/mcp
EVENTS_MCP_TOKEN=<the EVENTS_TOKEN from the VPS>
```

and `uv run server.py` as always. The MCP panel shows *Cape Town events (remote)*; tell the agent what to follow, close the laptop, and open it again tomorrow - tunnel first. With the tunnel down the Events panel shows the connection error and the rest of the app carries on; once it is back, the courier picks up where it stopped, and a watch that ran several times meanwhile arrives as one digest.

Why this is safe enough:

- **Nothing is on the internet.** The server listens on 127.0.0.1; the only port open is SSH's, which was open already.
- **ssh is the encryption and the login.** Only someone who can log in as you can open the tunnel.
- **The token is still checked** - in constant time, before the MCP layer sees anything (`BearerAuth`) - because every program on the VPS can reach its 127.0.0.1 too. It lives in `.env` on both sides and nowhere else: the app adds it to requests to that one server, and never writes it under `data/` or sends it to the browser.
- **The Host header is checked.** The SDK's DNS-rebinding protection admits `127.0.0.1` and `localhost` on any port - which is what a request through the tunnel carries - and nothing else.

**With root**, the same server can instead be served over HTTPS on your domain, with no tunnel: fill in the HTTPS block of `.env.vps.example` and `docker compose up -d --build`, and the agent gets `EVENTS_MCP_URL=https://<domain>:8788/mcp`. On a public address the server refuses to start without a token of 24+ characters, the host name and a certificate (`server_config`); it serves TLS itself, pins the Host header to the domain, and `restart: unless-stopped` stands in for cron. certbot renews the certificate but a running process keeps the old one, so add a deploy hook that restarts the container:

```bash
printf '#!/bin/sh\ndocker restart day18-events\n' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/day18-events.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/day18-events.sh
```

For local development nothing of this is needed: leave `EVENTS_MCP_URL` empty and run `uv run events_mcp_server.py` next to the app, on 127.0.0.1 with no token.

### What is deliberately not here

MCP's own `tasks` for long-running calls, and server-to-client notifications - both need a session held open, and this client still holds none. A model reading pages that have no markup. Push to a phone: the chat is the delivery channel, and a Telegram bot is one `deliver` function away from `digests.Courier`. Sources outside Cape Town, which is a list, not an architecture.

## MCP: the tools somebody else publishes

Every day so far has been about what the agent is *told* — how much of the conversation, which facts, whose voice, which rules. All of it is text, all of it was typed here, and the agent's entire relationship with the outside world is one HTTP POST to one model.

Day 16 is the first crack in that. An MCP server is a thing on the internet that publishes **what it can do**, in a format anything can read, and that day stopped one step short of doing any of it: connect, ask what is there, draw the list. Nothing was called. That was not a shortcut — the catalogue is the part that makes tool use *discoverable* rather than hard-coded, and the calling half, it claimed, is the same three requests with a different method name.

[Day 17](#day-17-a-server-of-our-own-and-an-agent-that-calls-it) cashes that claim, and adds the other end of the wire: a **local MCP server of our own**, over Google Maps, whose two tools the agent picks up out of the same catalogue as anybody else's.

### The mental model, corrected

The intuition most people arrive with is that an MCP server needs a client, that a client is a process, and that N servers mean N processes to deploy. Two of those three are wrong.

A client is **an object in your agent's process** — `MCPClient(url)` in `mcp_client.py`, instantiated the way `LLMClient` is. There is nothing to deploy.

What *can* need deploying is the **server**, and only for one of the two transports:

| Transport | Where the server lives | What the client does |
|---|---|---|
| **stdio** | a local subprocess you spawn (`npx -y @modelcontextprotocol/server-filesystem …`) | writes JSON-RPC to its stdin, reads stdout |
| **Streamable HTTP** | somewhere else, at a URL | POSTs JSON-RPC; optionally holds a GET open for anything coming back |

A public MCP server is the second kind, and nothing is launched locally at all. This app speaks only that one, because the servers worth demonstrating are all remote and because stdio would add a process lifetime to a day that does not otherwise have one.

The one thing that *is* per-server is a **session** — which is state, not a process. One server, one handshake, one session.

### Three requests, and that is the whole protocol

```
POST /mcp   {"jsonrpc":"2.0","id":1,"method":"initialize",
             "params":{"protocolVersion":"2025-06-18","capabilities":{},
                       "clientInfo":{"name":"day16-agent","version":"0.1.0"}}}
            ← result + maybe a Mcp-Session-Id header
POST /mcp   {"jsonrpc":"2.0","method":"notifications/initialized"}   ← 202, no body
POST /mcp   {"jsonrpc":"2.0","id":2,"method":"tools/list"}
            ← {"tools":[{"name","description","inputSchema"},…], "nextCursor":…}
DELETE /mcp                                                          ← hang up
```

That is `mcp_client.MCPClient.list_tools`, in full. It is why this day imports no SDK: the official `mcp` package would hide all four lines behind an `async with`, and its client is **async-only** while `agent.py` is not — so using it would mean either turning the agent async for a reason unrelated to agents, or running an event loop in a thread to bridge to it. Sixty lines of `httpx` cost less than either.

**Where that trade stops being worth it** is worth saying plainly, because it is not "when you feel like it": OAuth. Every server offered in the panel is open. A server behind OAuth 2.1 with dynamic client registration and PKCE is a day's work on its own, and that is the point to import the SDK rather than grow this file.

### Four things real servers do that a toy client gets wrong

None of these came out of the specification. They came out of pointing this client at five servers and watching what happened.

**A POST is answered with JSON *or* with an SSE stream**, at the server's discretion, per request. Of the five known servers, AWS answers with `application/json` and the other four with `text/event-stream`. A client that calls `.json()` works against one of them and fails on the rest with a decoding error rather than an HTTP one. `mcp_client.parse_body` is that fork, and it is the single most load-bearing twenty lines in the file.

**A session id may or may not be issued.** Microsoft Learn, AWS and CoinGecko hand one back on `initialize`; DeepWiki and Context7 do not. When there is one it is required on everything after, and a `404` later means *the session expired*, not *the URL is wrong* — which is why that status gets a sentence of its own in the error.

**The protocol version is negotiated, not declared.** This client asks for `2025-06-18`; AWS and GitMCP answer `2025-03-26`. What goes in the header from then on is their answer.

**`tools/list` is paged.** Not one of the five returns a `nextCursor` today, which is precisely why the loop is written now rather than the first time someone connects a server with forty tools.

### Two servers exporting `search` is the first thing that happens

A tool's name is not its identity; the pair `(server, tool)` is. So the catalogue reports a **qualified** name — `deepwiki__ask_question` — built in `mcp_client.qualify` against the stricter of the two rule sets in play: MCP puts no limit on a tool name, and a chat-completions `function.name` must match `^[a-zA-Z0-9_-]{1,64}$`.

The panel shows that qualified name and not the server's own, for one reason: it is the exact string a request would carry. A prettier version would be a name that exists nowhere outside this app.

Which is also the whole argument of the day, in one object. An MCP tool is `{name, description, inputSchema}`, and `inputSchema` is already JSON Schema — already the shape `tools[].function.parameters` wants. So `Tool.to_function` is four lines and not a translation layer:

```python
{"type": "function",
 "function": {"name": qualify(server_id, self.name),
              "description": self.description,
              "parameters": self.input_schema}}
```

Day 16 computed that object and sent nothing; day 17 sends it. The schema is still on screen under every tool, because it is the shortest available proof that a catalogue fetched over a protocol DeepSeek has never heard of drops into a request with a rename and no adaptation.

### No connection is held open, and that is a decision

`list_tools` opens a session, reads and hangs up. An MCP session is stateful by design, and holding one is what buys the things a catalogue does not need: `notifications/tools/list_changed` when a server's tools change under you, progress on a long call, and the server asking the *client* for something — sampling, roots, elicitation. None of that exists in a read of `tools/list`, and a pool of live sessions serving a button nobody has pressed is a reconnect policy, a keepalive and a lifetime bug in exchange for nothing.

It is the right answer for exactly this day and the wrong one the moment anything is called. `MCPClient` is where that changes.

### The catalogue is cached, and the timestamp is load-bearing

Opening the panel contacts nobody — it draws `data/mcp/servers.json`, which is the last thing each server said. Five servers at a second or two each would be a popover that opens empty for five seconds, and fetching on open would mean opening a panel makes machines on the internet do work.

So every row says **when it was last true**. A failed listing is written down rather than discarded, for the reason that decides it: dropping it would leave the previous good catalogue on screen under a stale timestamp, and the panel would quietly claim a server is up for as long as it stays down.

`data/mcp/` is also the only directory here holding something nobody in this app wrote. Delete it and nothing is lost that a refresh does not fetch again — which is true of none of the other five.

### Eight servers, five of them somebody else's

Offered in the add form, and served from `mcp_client.KNOWN_SERVERS` so a URL cannot rot in the page while the module that knows about it is corrected. The five remote ones answered this client with no credentials on the day it was written:

| Server | URL | Tools | Why it is here |
|---|---|---|---|
| **Google Maps (local)** | `127.0.0.1:8787/mcp` | 3 | **Ours** — `maps_mcp_server.py`, day 17. Distance A→B, restaurants nearby; since day 19, where a group should meet. |
| **Weather (local)** | `127.0.0.1:8789/mcp` | 1 | **Ours**, day 20 — `weather_mcp_server.py` over Open-Meteo, no key. |
| **Planner (local)** | `127.0.0.1:8790/mcp` | 4 | **Ours**, day 20 — `planner_mcp_server.py`, saves timed itineraries. |
| **DeepWiki** | `mcp.deepwiki.com/mcp` | 3 | Questions about any public GitHub repo's wiki. |
| **AWS Knowledge** | `knowledge-mcp.global.api.aws/mcp` | 5 | The other branch of both forks: plain JSON, protocol `2025-03-26`. |
| **Microsoft Learn** | `learn.microsoft.com/api/mcp` | 3 | Issues a session id. |
| **Context7** | `mcp.context7.com/mcp` | 2 | Up-to-date library documentation. |
| **GitMCP** | `gitmcp.io/docs` | 5 | Documentation and code search over GitHub. |

Two shipped seeded and switched **on** on day 17 — our own and DeepWiki — and since day 20 five do: all four of ours and DeepWiki. That breaks the pattern the personality and invariant seeds set, since both of those land off because they change every answer. On day 16 a connected server changed nothing at all. On day 17 it does: its tools ride on every request, which is a few hundred tokens whether or not they are used, and the switch in the panel is the place to turn that off.

### What is deliberately not here

`resources/list` and `prompts/list` — three of the five remote servers advertise both, and it is the same three requests with a different method name. OAuth. Progress notifications on a long call. And a held-open connection, which is the one of the four that is a design decision rather than a scope line.

## Day 17: a server of our own, and an agent that calls it

Day 16 ended with a catalogue and a promise: calling a tool is the same three requests with a different method name. This day cashes it, and to have something worth calling it writes **the other end of the wire** — `maps_mcp_server.py`, a local MCP server over Google Maps, with two tools on it.

```
you   "Где поесть рядом с Красной площадью? Лучшие по рейтингу."
        │
        ▼
agent.py ── POST /chat/completions ── tools[] ──►  DeepSeek
        ◄── tool_calls: google-maps-local__find_restaurants{location, sort_by:"rating"}
        │
        ▼
mcp_client.call_tool ──► maps_mcp_server.py ──► Google Places ──► 5 cards
        │
        ▼
agent.py ── POST /chat/completions ── the same question, with the cards in it ──► DeepSeek
        ◄── the answer you read
```

Three requests where yesterday there was one, two of them to the model. That is the shape of every tool-using agent there has ever been, and the only interesting question about it is what each half is allowed to decide.

### The loop, and the two rules in it

`Agent.ask` is now a loop rather than a call (`MAX_TOOL_ROUNDS = 5`). Two things about it are decisions rather than mechanics:

**The model is never told what a tool returned except by being asked again.** There is no branch in `agent.py` that reads a result and writes an answer from it — the result goes back into `messages` as a `tool` message and the *same question* is asked again with more of the world in front of it. That is why five restaurants come back as a recommendation and not as a table: nothing in this app formats anything.

**A failed tool is an answer, not an error.** A tool that ran and failed comes back as `isError: true` with a sentence in it, and that sentence is put in the `tool` message exactly as a success would be. "Google found no walking route across the Baltic" is something a model can say back to a person; raising here would be this app deciding, on the model's behalf, that the turn was over. What is *not* a tool error is a call that never reached the tool — unreachable server, HTTP 500, unknown tool name — and `ToolResult` keeps the two apart.

The budget is a budget, not a safety net: each round is a full chat-completions request with the whole conversation in it. A turn that searches twice and measures once is three requests and three bills, and the debug panel prints each round under its own heading — `TOOL ROUND 1 · ASKED FOR google-maps-local__find_restaurants` — because the one thing tool use makes easy is hiding what a turn cost.

### The server is the one place an SDK is worth importing

The client is still hand-written, for day 16's reasons — it is sync, `agent.py` is not, and every request it makes is on screen. The server is not, and the asymmetry is the point: what a server has to get right is protocol work — the handshake, version negotiation, sessions, argument validation against each tool's schema, `outputSchema`, DNS-rebinding protection — and none of that is this project's argument. `maps_mcp_server.py` is `MCPServer` (the SDK's class, formerly FastMCP) plus two decorated functions, and everything left in the file is ours:

```python
@mcp.tool(title="Restaurants nearby")
def find_restaurants(location: str, query: str = "", sort_by: SortBy = "relevance", …) -> Restaurants:
    """Find restaurants around a location via Google Places, as at most 5 mini-cards."""
```

The type annotations *are* the `inputSchema` the agent will be offered, and the `Restaurants` model *is* the `outputSchema` — which means the panel from day 16 draws this server's tools out of the same catalogue as DeepWiki's, and neither the agent nor the page knows which of them we wrote.

### Two tools, and what they actually call

| Tool | Google API | What it returns |
|---|---|---|
| `compute_distance` | Routes API (`directions/v2:computeRoutes`) | Route distance and time A→B by car / foot / bicycle / transit / two-wheeler, plus the straight line between the two points |
| `find_restaurants` | Places API (New) (`places:searchText`) | At most **5** mini-cards: name, cuisine, rating, reviews, price level, address, distance, open now, Maps link |

Both take an address, a place name or `lat,lng` — Routes geocodes an address itself, and the restaurant search resolves one through Places rather than the Geocoding API, so there are two APIs to enable instead of three.

Neither is the legacy Distance Matrix or Places API: **a Google Cloud project created after March 2025 cannot enable those at all**, and a new organisation is exactly that.

Three things in `find_restaurants` are decisions rather than parameters:

- **Twenty candidates are fetched and five are returned.** Sorting five results by rating is not sorting, it is reordering whatever relevance already chose.
- **`locationRestriction`, not `locationBias`.** Text Search only *restricts* to a rectangle; a circle is merely a bias, and a bias lets a famous place across town outrank the one around the corner. The box is computed from the radius and then trimmed back to the circle by distance, so the answer is the circle after all.
- **`sort_by` is the model's job.** "Лучшие" → `rating`, "поближе" → `distance`, "популярные" → `popularity`. The tool description says so in as many words, which is the whole of how a tool teaches a model to use it.

### The cards are drawn from `structuredContent`, not parsed out of the text

A tool result carries text for whoever can only read text, and — since `2025-06-18` — a `structuredContent` object beside it for whoever can do better. The page draws the cards from the object, keyed off its **shape** rather than off the tool's name: a result with a `cards` array gets cards, one with `distance_text` and `duration_text` gets the trip line, and anything else degrades to its receipt. A second server exporting the same shape would be drawn the same way, and one exporting a shape this page has never seen does not throw.

The receipt is always there, under the bubble and never inside it:

```
⚙ google-maps-local__find_restaurants(location: "Красная площадь", sort_by: "rating") · 412 ms
┌──────────────────────────────────────────────────────────┐
│ Dr. Живаго                          ★ 4.6 (12043)  340 m │
│ Русский ресторан · $$$ · Моховая ул., 15/1 · open now    │
└──────────────────────────────────────────────────────────┘
```

A tool result is not something the model said, and the one thing that block must never look like is part of the answer above it. It is stored on the assistant message (`Message.tools`), so a reload draws the same cards without calling anything — for `usage`'s reason: it was true once, of this turn, and cannot be recomputed from the text.

### The API key never leaves the maps server

`GOOGLE_MAPS_API_KEY` is read by `maps_mcp_server.py` and travels to Google in an `X-Goog-Api-Key` header, never in a URL. The agent talks MCP to `127.0.0.1:8787` and has no idea a key exists — which is the argument for wrapping an API in an MCP server rather than in a function, and the reason this one binds to localhost and checks `Origin` rather than listening on `0.0.0.0`.

## Invariants: the rules an answer may not break

Day 12 already lets you type `Python + FastAPI, free APIs only` into a profile and have it ride on every request forever. So the obvious objection to this day is that it, too, already exists — and this time the objection is half right, which is more than it was on day 12.

What exists is the **stating**. What does not is anything that finds out. The line is not what the rule says, it is what happens when it is ignored:

|  | Personalisation (day 12) | Invariants (day 14) |
|---|---|---|
| Written by | you | you |
| Can it be wrong? | no — it can only stop being what you want | no — same |
| How it reaches a request | sent whole, unconditionally, every time | sent whole, unconditionally, every time |
| **What ignoring it looks like** | an answer that reads wrong | **an answer that *is* wrong** |
| **Who finds out** | you, eventually, reading it | **the code, before it reaches you** |
| What you do about it | edit the profile | nothing — you never saw the bad answer |

The first three rows are identical, which is why the two panels sit side by side and share most of their shape. The last three are the day.

And it is day 13's argument, moved onto a different object:

```
day 13   the model reports where work stands  ·  phases.check rules  ·  you click
day 14   the model answers                    ·  the answer is read back  ·  a broken
                                                 one is thrown away and asked again
```

### Four kinds, and why those four

```
ARCHITECTURE   Storage is JSON files on disk. No database, no ORM, no migrations.
               Why: the app has to run from a clone with no services to start first

STACK          Python and FastAPI on the server, vanilla JS on the page. No
               frontend framework and no build step.
               Why: the page is one file you can open and read

DECISIONS      Authentication is a signed cookie, not JWT.
ALREADY TAKEN  Why: we argued about this in March and nobody wants it reopened

BUSINESS       Nothing the user writes leaves their machine.
RULES          Why: it is the one promise the product makes
```

They look like four topics and they are not. They are four different **sources of authority**, which is the thing a model actually needs in order to weigh one: an architecture rule is a shape the code already has, a stack limit is a boundary somebody else drew, a decision is an argument that already happened, and a business rule is not technical at all — it is the one kind that no amount of technical elegance is allowed to trade away.

**`because` is the field people skip and the one that does the work.** A rule with no reason attached gets argued with every single time it bites, because the only thing the agent can say is "you told me not to". A rule with a reason gets *worked around*, which is the behaviour this day is actually for: the answer that refuses Postgres and then offers an in-memory index, sharding by entity and `orjson` is an answer that understood what the rule was protecting.

### Two checks, and neither of them is the prompt

The rules go into the prompt, because a rule the model never sees is one it can only keep by luck. The block ends by telling it what to do when the rules and the request disagree, and **in the ordinary case that is the whole feature working** — the refusal arrives inside the first answer and nothing is re-asked.

But a rule that lives *only* in a prompt is a request. It is followed at whatever rate prompts are followed and it cannot be tested, which is exactly `phases.py`'s argument. So the answer is read back, twice, by two things that fail differently:

```
the answer ──▶ invariants.scan        free, deterministic, no network
                     │                marker words, matched on a word boundary
                     ▼
               InvariantWatcher       one model call, catches paraphrase
                     │                "a document store" has no markers in it
                     ▼
               {"violations": [{"rule": 1, "where": "request", …}]}
```

**A marker hit is a signal, never a verdict**, and that sentence is load-bearing. `"we are not using Postgres here, because the app has to run from a clone"` contains `postgres` and breaks no rule — it *obeys* one, out loud. A scan that ruled on its own would flag the best answer the agent is capable of giving. So the markers do two jobs, neither of which is ruling:

1. they tell the watcher **where to look**, which is worth a great deal to a judge running at `reasoning_effort: none`;
2. they are the only part of this day that works **with nothing plugged in**, which is what makes it testable — the UI suite runs against a dummy key where every model call is a 401.

That second one is why `POST /api/invariants/scan` exists as an endpoint rather than an internal function. A day whose entire claim is *enforced, not merely stated* cannot rest on a call the test suite is unable to make.

**The one place this day fails closed.** When the watcher call fails, the marker hits stand as violations on their own. That is not symmetrical with day 13, where a failed watcher leaves the task exactly where it was, and the asymmetry is deliberate: a state machine standing still is a defensible state a person fixes with one click, while a rule that stops being enforced the moment a call fails is not a rule, it is a note. The cost of being wrong that way is one wasted retry on an innocent mention. The cost of being wrong the other way is the day not working.

### The model answers, the code reads it, and you never see the bad one

```python
check = run_invariant_check(settings, invariant_record, message, reply)
if check is not None and check.rejected:
    second = build_agent(..., invariant_correction=invariants.correction(...)).ask(message)
    if second.error is None:
        reply = second
        check.retried = True
```

Three things about where that sits. It runs **before the turn is stored**, which matters more here than it did on day 13: the transcript is replayed into later requests, so one stored violation becomes an example the model follows for the rest of the conversation. It runs **after** day 13's retry, so that what gets judged is the answer that would actually be shown rather than a draft already thrown away. And it is **one retry, never two** — a loop that argued with a model until it complied would spend money to reach the place a plain instruction reaches.

`AdviceRun.rejected` had to distinguish a jump from the ordinary end of a stage. `CheckRun.rejected` is just `bool(self.violations)`, and the difference says something: there is no ordinary end of an invariant, and nothing here is one confirmation away from being allowed. A rule was broken or it was not.

The retry carries `invariants.correction`, which has day 13's three parts — what the last answer did, why it is refused, what to do instead — and **two shapes, chosen by `where`**, because the same broken rule wants opposite answers depending on who reached for it first.

### When it was your request that broke the rule

```
you        →  "Давай перенесём всё хранилище на Postgres, так будет быстрее."

the agent  →  Инвариант 1 не даёт это сделать: хранилище — это JSON-файлы на
              диске, без базы, ORM и миграций; правило существует ровно потому,
              что приложение должно запускаться из клона, не поднимая никаких
              сервисов. Переносить хранилище на него я не буду и урезанную
              версию этого тоже делать не стану.

              Если цель — скорость (а не именно Postgres):
              1. Горячий индекс в памяти. …
              2. Шардировать файл по сущностям. …
```

That answer is the **first** attempt — the prompt did it, nothing was re-asked, and the watcher looked at both marker hits and cleared them, because naming a rule in order to refuse it is not breaking it. The judge prompt says so explicitly, and it has to: without that line every correct refusal is reported as a violation, and the panel fills with warnings about the agent behaving perfectly.

When the prompt does *not* take, the correction is the backstop, and it is the opposite of invisible:

```
--- Your last answer was rejected before it was shown ---

What the user asked for cannot be done without breaking an invariant they
set themselves:

1. "Storage is JSON files on disk. No database, no ORM, no migrations."
   Why it exists: the app has to run from a clone with no services to start

Do not do it, and do not do a smaller version of it. …

Answer the user's message again. Say in one line which invariant is in the
way and why it exists. Then give two or three concrete ways to get what they
were actually after that hold every invariant. Do not ask permission to break
it, and do not present breaking it as one of the options.
```

The other shape — the agent proposed something forbidden entirely on its own — is invisible by design: *answer again, doing the same job inside the rules*, and do not mention that there was a first attempt, because the user never saw it. When both happen at once the request case leads, since an answer that quietly fixed its own slip while ignoring what you actually asked for would be the worse of the two.

**There is no override button.** It was the obvious feature and it undoes the day: a rule that opens for anyone who presses *open the rule* is decoration, and the honest way to stop keeping a rule is the switch next to it in the panel, which leaves it on screen saying it is off.

### Where it sits in a request, and why after personalisation

```
[system]  the role                                        ← the developer's
[system]  LONG-TERM — what you know about this user
[system]  WORKING — what this task has established
[system]  WHERE THIS TASK HAS GOT TO
[system]  summary | facts
[system]  PERSONALISATION — who is asking, and how               ← yours
[system]  INVARIANTS — what an answer may not do                 ← also yours
          the window, verbatim
[user]    the question
```

This is the day's one arguable decision, so the suite pins it. Day 12's block ends by claiming that where it disagrees with anything above, *it* wins — and against these it must not. A preference that contradicts a decision the work is already committed to is the preference that yields. Later context is weighted more heavily, so the block nothing may override is the one nearest the question, and personalisation's claim stops one message short of it.

The two are not rivals for the slot. They are the two halves of what you declared, in the order of how hard they bind.

### What it costs, and the switch

A set of rules is sent whole with every request — roughly 250–400 tokens at twelve rules, forever, which is the same standing cost a profile is. The checking is one further small request per message, at `reasoning_effort: none` and capped at 400 tokens.

The honest worst case, since it is easy to hide: a turn that trips **both** subsystems buys three answers and two side requests. They are not collapsed into one retry, because they refuse for unrelated reasons — and day 13's replacement answer is a new answer that nothing has checked yet.

There is a switch in **⚙ Settings**, next to day 13's, and what it governs is worth being exact about: **the checking, not the rules.** Off, the invariants still ride on every request and the model is still told it may not break one; what stops is the request that reads the answer back, and with it the retry. So turning it off does not relax a rule, it **demotes** one — from enforced back to merely stated, which is precisely what day 12 already had and what this day exists to improve on.

Twelve rules, and the cap is the design rather than a backstop. Forty rules is not a stricter agent; it is a set that contradicts itself somewhere with nobody able to say where.

### The panel, and the two switches

```
INVARIANTS  ⟨ 3 in force ⟩                                              ▸

  ☑ In force                                          3 of 4 sent

  ARCHITECTURE
   1.  Storage is JSON files on disk. No database, no ORM, no migrations.
       Why: the app has to run from a clone with no services to start
       ⌞postgres⌝ ⌞sqlite⌝ ⌞mongodb⌝ ⌞orm⌝            ☑  Edit  Delete
  STACK
   —   Python and FastAPI on the server, vanilla JS on the page.
                                                      ☐  Edit  Delete
  BUSINESS RULES
   2.  Nothing the user writes leaves their machine.
       Why: it is the one promise the product makes
                                                      ☑  Edit  Delete

  [ + New invariant ]                                 3 of 12

  SENT WITH EVERY REQUEST
    ARCHITECTURE
      1. Storage is JSON files on disk. No database, no ORM, …
```

Two levels of switch, answering different questions: a rule switched off has stopped being true, while the set switched off is the day itself turned off — which is how it gets demonstrated, since *before* is half of it.

**A rule that is not in force has no number, and is drawn with a dash.** It is not rule 2 that is off; it is not a rule right now. The numbering is an address — the prompt uses it, the watcher reports it, the refusal in the transcript cites it — so it is computed on the server and served, and the page is not allowed its own opinion about it. That is also why editing a rule keeps its id and its position: re-deriving the id on a typo fix would file it at the end and renumber everything after it, making every earlier refusal in the transcript cite the wrong line.

The **shipped rules are written down and switched off**, which is day 12's rule about seeded profiles plus one that is not about demonstrating anything: those two rules describe *this* app, and an app that enforced them on first run would be holding somebody's unrelated project to the architecture of the thing they had just cloned.

Under an answer that was caught, a receipt — a sibling row in the transcript, never anything hung on the bubble, because the one thing it must not look like is part of what the model said:

```
   ⚠ The answer was asked for again — it broke an invariant
     2. Python and FastAPI on the server, vanilla JS on the page.
        Why it exists: the page is one file you can open and read
        ▸ what was thrown away
```

### Checking it

```
1. Open Invariants. The two shipped rules are there and switched off.
2. Ask "как лучше хранить историю чатов?"   → an ordinary answer, databases
                                               and all
3. Switch the set on. Ask it again.         → the same question, answered
                                               inside the rules
4. Ask for the forbidden thing outright:
   "давай перенесём всё на Postgres"        → it names invariant 1, quotes why
                                               it exists, and offers three ways
                                               to get the speed without it
```

Then open the debug panel on that last turn. The **Invariants** entry shows the second request: two markers found, and `clear — 2 markers checked`, because the refusal mentioned Postgres in order to rule it out. That pair is the day in one line — three markers found and nothing reported is the checker working, and it is indistinguishable from the checker being asleep unless both halves are on screen.

Two more, and they are the ones that prove it is not all prompt:

5. **The deterministic half, with nothing plugged in.** `curl -s localhost:8000/api/invariants/scan -d '{"text":"put it in Postgres"}'` → the hit, the rule number, the clause. Then `'{"text":"the format of that file is fine"}'` → nothing, because `orm` matches on a word boundary and `format` is not a marker.
6. **Turn the checking off** in ⚙ Settings and repeat step 4. The block still goes up — the debug panel proves it — and no second request is bought. The rule is stated and no longer enforced, which is the difference this whole day is about.

Then `grep -l "no migrations" data/conversations/*.json` and find nothing: the rules were applied to requests and written into no transcript, the same claim day 12 makes and the one thing checkable against the filesystem.

## The task, as a state machine

Day 11 built a **task**: a record with a title, a memory of its own, and a status with two values in it — `open` and `closed`. That is enough to answer *should this still be sent?* and nothing else. It cannot answer the question a person actually has when they come back to a piece of work after a fortnight, which is not *is this alive* but **where was I**.

So the status is gone, and a stage has taken its place:

| | What happens in it | What comes out of it |
|---|---|---|
| **Planning** | deciding what is being built and what *done* will mean | a goal, the constraints, an agreed list of steps |
| **Execution** | building it | the plan, worked through — and what was decided on the way |
| **Validation** | checking it against what planning agreed | a verdict: it passes, or here are the gaps |
| **Done** | finished | the task's memory stops being sent, and stays on file |

Four values instead of two would be a label. Three things make it a machine, and all three are in `phases.py` rather than in a prompt.

### 1. Not every stage can follow every other

```
forwards    planning  ──▶  execution  ──▶  validation  ──▶  done

backwards   planning  ◀──  execution
            planning  ◀─────────────────  validation
                           execution  ◀──  validation
                           execution  ◀──────────────────  done    (reopened)
```

```python
TRANSITIONS = {
    PLANNING:   (EXECUTION,),
    EXECUTION:  (PLANNING, VALIDATION),
    VALIDATION: (PLANNING, EXECUTION, DONE),
    DONE:       (EXECUTION,),
}
```

Read the gaps rather than the entries. Work goes backwards more often than forwards — validation fails and you are executing again, executing shows the plan was wrong and you are planning again — so those edges exist and are free. The three that do not exist are the ones that would be a lie:

- `planning → validation` — there is nothing to validate;
- `planning → done` — a claim that work happened that did not;
- `execution → done` — a claim it was checked when nothing checked it.

Those are exactly the jumps a model proposes when it is being agreeable, which is why they are refused in code as well as discouraged in a prompt — [see below](#3-the-model-reports-the-code-decides--and-the-answer-is-checked-too) for why both, and why refusing the transition turned out not to be enough on its own. `done → execution` is the one edge out of the end, because work comes back; calling it *reopening* and routing it through the same check as every other move is what puts it in the log as the transition it is, instead of a quiet edit of a field.

Reaching `done` ends the work and does not tidy the list — a finished task stays selectable and stays readable, which is exactly what makes finishing one cheap. The price is a list that only grows, so there is a **Delete** next to it: two clicks, like *Clear context* and for the same reason, and it really does forget the stage, the log and every item the task ever filed. It is offered in every stage rather than only in `done`, because a task you could only delete once it was finished would have to be driven through two checklists and three transitions before it could be abandoned — and abandoning is precisely what you want to do with work that should never have been started.

### 2. A stage has steps, and some of them hold the exit

A stage with no steps is a label again: "execution" tells the agent to execute, which it was going to do anyway. So each one carries a checklist — the ordinary best practice for that kind of work, written down once in `phases.STEPS` so that every task gets it rather than the ones whose first message happened to ask for it:

```
PLANNING     ☑ Goal and definition of done                    • required
             ☑ Constraints, risks, what is out of scope
             ☐ An agreed list of steps                        • required
                 → moving on is refused: "Not yet: “An agreed list
                   of steps” still to do in planning."

EXECUTION    ☐ Every planned step done, or dropped on purpose • required
             ☐ Decisions taken while working are filed
             ☐ Departures from the plan are called out

VALIDATION   ☐ Checked against the definition of done         • required
             ☐ Failure and edge cases tried
             ☐ A verdict, out loud                            • required
```

The checklists are short on purpose. A twelve-item checklist is not followed, it is dismissed; the two or three marked **required** are the ones worth stopping a task over, and everything else is a reminder that shows on screen and holds nothing up. What the required ones buy is the failure everybody has seen: an agent that starts building on the third sentence of the conversation and is still building the wrong thing an hour later. It cannot leave planning until there is a goal and a plan.

**Going backwards is never held**, and going backwards *unticks the required steps of the stage you return to*. Re-planning is what you do when the plan was wrong, so a `plan` still ticked from the first pass would let the task walk straight forward again through a gate that has stopped meaning anything. The constraints you wrote down stay written down — only the required boxes clear.

### 3. The model reports, the code decides, the person presses the button

The tempting design is to let the prompt carry the rules and leave it there. It cannot be tested, it is followed at whatever rate prompts are followed, and the first time a model is in a hurry to be agreeable it is gone. So the rules live in `phases.check`, and the model is asked one question it cannot get wrong by being agreeable — *where does this work stand?*

```
the model reports    where the work stands after this exchange
the code decides     whether it can get there from here
the person decides   whether it does
```

**The agent never moves the task.** It says where it thinks the work is; `check` rules on that; and what reaches the screen is an offer:

```
  The agent says the work is ready for execution.
  the plan is agreed
  Moving marks as done: An agreed list of steps
  [ Move to execution ]  [ Not yet ]
```

One click, in the panel or in the transcript where the exchange that prompted it is — and the click **answers the agent**, not just the record. Moving the stage and stopping there was the first version of this, and from the outside it looked broken: the row vanished, a field changed behind a popover, and the agent that had just asked to move on sat there waiting. So accepting sends one short message into the chat — *Approved — the task is now in execution. Carry on from there.* — which goes up carrying the new stage's block like any other turn. Visible, rather than a silent request: a hidden turn would put an answer on screen with nothing above it explaining why the assistant suddenly started building, and the honest description of what happened is that you told it to. That is day 11's rule applied to a different kind of write — a stage that changed because a model produced a sentence is a stage nobody decided — and it is also the answer to the way this first went wrong, which was worse than a jump: the task *stopped moving*. The checklist wants two boxes ticked before planning can be left, nobody ticks boxes, and the agent ends up explaining for the third time that it cannot change the stage itself while the user types *согласовано*.

So **Move** carries a confirmation, and that is the one thing a person adds here. The required steps exist to make somebody say *this stage is done*; pressing the button is somebody saying it, so it ticks what is outstanding and asks the machine again. It buys nothing else — an edge that does not exist still does not exist, and a paused task still does not move:

```python
>>> tasks.move(t, "done", confirm=True)        # from planning
(task, Decision(ok=False, reason="Planning cannot go straight to done."))
>>> tasks.move(t, "execution", confirm=True)   # from planning
(task, Decision(ok=True))                      # ...and "goal" and "plan" are now ticked
```

**A jump is a different thing from the end of a stage, and they are told apart by which rule said no.** `INCOMPLETE` — the stage is reachable, the checklist just has not been confirmed — is the ordinary end of a stage and becomes the offer above. `ILLEGAL` — there is no edge at all — means the answer did the work of a stage this task cannot reach, and refusing the *transition* does nothing whatever about that:

```
you        →  "давай сразу в готово"
the agent  →  "Done - here is the full working calculator..."    ← never shown
the watcher→  {"stage": "done"}
check      →  planning cannot go straight to done   (no such edge)
the agent  →  asked again, with the refusal attached
you see    →  "Before I build anything: what should the calculator support?"
```

The second attempt is given `phases.correction()`: what its last answer did, why that is not where the work is, that the user never saw it — so it must not apologise for it or offer it again in a shorter form — and what this stage still needs. One retry, never two: a loop that argued with a model until it complied would spend money to reach the same place a plain instruction reaches.

The prompt carries the rules as well, now, and the two are not alternatives. The state block names the stages reachable from here, says to stay put *especially* when asked to skip — and, just as importantly, tells the model **not to stall**: when the stage's work really is finished, say so in a line and carry on, because the app is reading the answer and will make the offer. "I cannot change the stage myself" is not caution, it is a dead end, and an agent that says it has found a way to be stuck that no rule asked for.

It all has one switch in **⚙ Settings**, next to day 11's, and it decides *who may move the task*. On, nothing in the panel sets a stage by hand: the agent reports, `check` rules, and the only thing to press is the offer. Off, nothing re-reads the answers and so nothing will ever raise one — the machine is still there and still enforced, but the panel grows a **Move to** menu, because a machine nothing can drive is not stricter, it is stuck.

**And none of it is written down except what happened.** `Task.log` records transitions that were *made*, and the pause switch. A move the machine would not make did not happen; an offer that was declined did not happen either. A log carrying them would be a history of things that are not true — a worse record than one that is simply correct.

### The rail reports; it does not move anything

The four stages are drawn as a rail, always all four, with the unreachable ones greyed and the machine's own sentence on them. That is the day's argument sitting on screen: the jump from planning to done is not *missing*, it is *refused*, and here is why.

They are not buttons. They were, for a while, and it was a trap one click deep — a stray click changed which stage a piece of work was in, and the way back cost two ticked boxes, because coming back a stage clears what that stage had claimed. A control that destructive should not be four targets wide and fire on a single click.

There was a **Move to** menu underneath for a while, always drawn, and it quietly undid the day. Choosing a stage forward in it sends `confirm`, and `confirm` means *a person says this stage is done* — so it ticked every outstanding required step and asked the machine again. Which means the checklist that is supposed to hold the exit could be walked straight through: planning to done in four choices and two minutes, with no plan agreed, nothing built and nothing checked. A gate that opens for anyone who presses *open the gate* is decoration, and a task driven that way leaves a log that is a history of work which never happened.

So it is gone, and the stage moves the one way this day argues it should: **the agent reports, the code rules, and a person accepts the offer.** Nothing else in the panel sets a stage — which is also what makes the rail honest, since there is now nothing on it or under it that could move anything.

Two exceptions, and they are the same argument read from the other end:

- **the agent half switched off** — no answer is read, so no offer is ever raised. The **Move to** menu comes back, as the fallback it always should have been, and says on its face why it is there;
- **`done`** — a finished task is not sent and its answers are not read, so the watcher never runs on one and no offer can ever reopen it. That is a single **Reopen in execution** button, and it is a move *backwards*: it goes through `check` like every other, ticks nothing, confirms nothing, and lands in the log as the transition it is.

### Pause, at any stage

Pause is a flag, not a fifth stage, and that is the whole of why it works everywhere. A stage says what kind of work this is; a pause says nobody is doing it right now, and the two are independent — you can stop in any of the four. A fifth pill would have meant inventing edges back out of it to all four and losing the answer to *paused from where*.

```
Task  ⟨ Build the iOS app · Execution ⟩   + New   [ Resume ]   [ Delete ]

  Planning ›  ● Execution ›  Validation ›  Done
                             ╰── The task is paused. Resume it before moving it on.

  Paused – waiting for the design review. Nothing moves it — not you,
  not the agent — until it is resumed.
```

While it is set, `phases.check` refuses every transition, from whoever asks; the watcher request is not made at all, so a paused task cannot be nudged along by a conversation going on next to it; and the prompt says *answer what is asked, but do not carry the work forward*.

**What it does not stop is the memory.** The task's items keep going up with every request, and so does the state block. That is the point: a task you pick up in a fortnight that had forgotten what it knew would be a task you start again.

### Coming back, without being told it all again

Everything above lives in `data/tasks/<id>.json`, so it survives a restart for the same reason day 7's conversations do — it was never in the process to begin with:

```json
{
  "id": "task-1a0ba26d505",
  "title": "Build the iOS app",
  "phase": "execution",
  "steps": { "planning": ["goal", "constraints", "plan"], "execution": ["work"] },
  "paused": false,
  "note": "",
  "log": [
    { "at": "…09-12T09:31:40", "kind": "move", "by": "you",
      "from": "planning", "to": "execution" },
    { "at": "…09-14T16:02:11", "kind": "move", "by": "agent",
      "from": "execution", "to": "validation", "why": "the build is finished" },
    { "at": "…09-14T16:40:55", "kind": "move", "by": "you",
      "from": "validation", "to": "execution", "why": "two screens missing" }
  ],
  "items": [ … ]
}
```

Nothing has to be reconstructed on the way back in, because nothing was ever derived from the conversation. Open any chat joined to that task — including one opened for the first time this morning — and the request carries:

```
--- Where this task has got to ---
The recorded state of the work, kept outside this conversation and true
whoever is asking. It is not a summary to be repeated back: it is where
to carry on from:

The task - “Build the iOS app”
Stage 2 of 4: EXECUTION

Last worked on: 6 days ago.

Carry out the agreed plan, one step at a time. If the plan turns out to be
wrong, say so rather than quietly building something else.

Steps of this stage:
  [x] Every planned step done, or dropped on purpose (required)
  [ ] Decisions taken while working are filed
  [ ] Departures from the plan are called out

Expected next action: File decisions made along the way with /task so the
next conversation inherits them.

How it got here:
  2026-09-12: planning → execution (you)
  2026-09-14: execution → validation (agent) – the build is finished
  2026-09-14: validation → execution (you) – two screens missing

This is where the work already is. Carry on from it: do not re-plan what
earlier stages settled, do not restate the task back to the user, and do
not repeat explanations they have already had. If the work has moved on to
another stage, say so plainly in your answer - something else decides
whether it may.
```

Three things in there are doing work that is easy to miss.

**“Last worked on: 6 days ago”** appears only past twenty hours, and is rounded hard — hours, then days, and nothing finer. Whether the gap was fifty or seventy hours changes nothing about how the agent should behave, and a sentence that reads like a measurement invites the model to reason about it.

**The closing paragraph is the day's second requirement**, and it is one instruction: *carry on, do not start again.* Without it the failure is reliable and familiar — an agent handed a task summary re-summarises it, re-proposes the plan that was agreed a week ago, and asks the questions planning already answered, because restating context is what a model does when it is handed context and nothing to do with it.

**“How it got here” is every move that was made**, and there are no others to leave out. Refused transitions are not recorded anywhere, by anybody: they did not happen. What would happen if they were is worth stating, because it was the first design here — a prompt whose job is *carry on from here* would be carrying `planning → done refused`, which reads to a model as evidence that somebody wanted this finished.

**The closing paragraphs are the stage discipline.** The block names which stages are reachable from this one, says to stay put *especially* when asked to skip ahead, lists what is still outstanding so that refusing can be specific — and ends by telling the model not to narrate any of it, because the stage and the checklist are already on the user's screen and repeating them back is noise.

### Where it sits in a request

```
[system]  the role
[system]  LONG-TERM — what you know about this user              ← widest scope
[system]  WORKING — what this task has established
[system]  WHERE THIS TASK HAS GOT TO — stage, steps, next        ← day 13
[system]  summary | facts — what fell out of this chat's window
[system]  personalisation — who is asking, and how they answer
          the window, verbatim                                   ← narrowest
[user]    the question
```

Directly after the working layer, because it has exactly that scope: both are about one task and neither is about this conversation. The order between the two is the order they are read in — *what the task has established*, then *where the task has got to*.

A task in `done` sends neither. It is finished, and a finished task's stage is no more current than its decisions are.

### The stage, where the typing happens

The memory panel has all of this in detail, and a popover you have to open is not where *which stage is this* belongs — it decides what the next message will get you, so it sits where the next message is written:

```
│  PLANNING  step 3 of 3  · Lay out the steps, and get them agreed before
│                           building anything.
┌─────────────────────────────────────────────────────────┐
│  давай сразу в готово                                      [ Send ] │
└─────────────────────────────────────────────────────────┘
```

One line, no detail view, and it turns red and says `· PAUSED` when the machine is frozen. Clicking it opens the panel. It is drawn from the same `task` the panel draws from and redrawn in the same breath, so the two cannot disagree; a chat that is in no task has no strip at all.

### Checking it

Four things, and the first two are the ones the day asks for by name:

1. **Pause anywhere.** In any stage, click **Pause**. Every pill greys out with the same reason on it, the API refuses a move with `409`, and the debug panel shows no watcher request was even bought. Resume: the pills come back exactly as they were.
2. **Come back cold.** Stop the server. Start it a week later — or edit `updated_at` in the task file, which is the same thing to everything downstream. Open a *new* chat, join it to the task, and ask it to carry on. It picks up at the stage it stopped in, with the steps still ticked, and does not re-explain the plan.
3. **Try an illegal jump — by hand.** With a task in planning, `curl -X POST …/api/tasks/<id>/phase -d '{"to":"done"}'` → `409 Planning cannot go straight to done.` In the panel the same move is a greyed pill with that sentence on it. Nothing is written to the log either way.

   **And in words.** In a chat joined to that task, ask for it: *давай сразу в готово*. What comes back is a planning question, not a finished thing — and the debug panel's **Task state** entry shows the answer that was thrown away to get it (`answer re-asked — Planning cannot go straight to done`).

5. **Finish a stage properly.** Agree the plan and say so. The answer stands, and underneath it the agent offers the move: **Move to execution**. One click and the task is in execution, with the boxes that were waiting on your word now ticked. This is the one to run if only one gets run — a machine that refuses jumps and never advances is worse than no machine.
4. **Try a legal one too early.** Same task, `{"to":"execution"}` → `409 Not yet: “Goal and definition of done”, “An agreed list of steps” still to do in planning.` Tick both boxes in the panel and it goes through.

## Personalisation: what you declared, as against what it learned

The obvious objection to this day is that it already exists. Day 11 will happily hold `preference.style: no apologies` in the long-term layer, star it so that it rides on every single request, and carry it into every chat you open. That *is* personalisation, by any reasonable reading. So what is being added?

The answer is a line those eleven days never had to draw, and it is not about lifetime. Day 11 sorts memories by asking **when should this be forgotten?**, which is a good question and sorts facts perfectly. It cannot sort this, because the difference here is **refutability**:

|  | Memory | Personalisation |
|---|---|---|
| Where it came from | the agent noticed it | you declared it |
| Can it be wrong? | **yes** — "my name is not Maksim" | **no** — it can only stop being what you want |
| How it reaches a request | scored against the question, a few sent (`memory.recall`) | sent whole, unconditionally, every time |
| How big does it get | unbounded, hence the recall | three fields, by design |
| What a failure looks like | the agent did not know | the agent knew and did it anyway |
| How you undo it | file a correction over it | edit it, or switch profiles |

Every row follows from the one above it. A memory can be wrong, so it needs a source and a way to correct it; it accumulates, so it needs recall. A profile cannot be wrong, so it needs neither — **`personality.py` imports nothing from `memory.py`, and there is no scoring in it at all.** That absence is the clearest statement of the difference the codebase can make.

### Three fields, and what each is for

```
Style          in Russian
               short and direct, no preamble
               code first, explanation after

Preferences    Python + FastAPI, no heavy frameworks
               minimum dependencies
               free APIs only

Context        senior developer
               building a voice assistant
               team of 3
               deadline in two weeks
```

They are three because a person configuring an assistant is answering three different questions, and the answers behave differently once written down:

- **Style** is taste. How an answer should read — language, length, tone, what never to do. A model drifting here is not a bug; a model answering in the wrong language is.
- **Preferences** are choices already made, and they hold *whatever the topic*. Not "use FastAPI for this endpoint" — "this is the stack, these are the rules, do not propose around them". They are the closest thing here to a specification, and the easiest to check an answer against.
- **Context** is who is asking and what for, and it is the field that makes the whole thing worth building. "Senior developer" changes what may be skipped. "Team of 3" changes what is worth automating. "Deadline in two weeks" changes which of two designs gets recommended. None of that is inferable from the question, none of it is worth retyping into every chat, and none of it is a *memory* — nothing there was learned, and nothing there can be contradicted by something said later.

One free-text field would be strictly more expressive. It would also be, precisely, a second custom system prompt — the duplication this day exists to remove — and, worse, an empty box gets nothing out of anybody. Three labelled prompts do: a box that says *who you are and what you are working on* is how "senior dev, voice assistant, team of 3" gets typed at all.

An earlier cut of this day had four fields — `language`, `tone`, `format`, `constraints` — which on inspection were one question asked four ways. They fold into **Style** on read, so a profile written under the old shape still works; `constraints` was a list of things *never to do*, so each of its lines gets "never" put back on it as it moves, because a migration that silently inverts four of somebody's rules is worse than one that drops them.

### The system prompt stays exactly where it was

The other tempting move is to replace `system_prompt` with this. It is the same slot in the request, it holds free text, the UI has had a custom-prompt box since day 3.

It stays anyway, and the reason is ownership rather than mechanism:

```
[system]  You are a helpful assistant.               ← whoever built the app
…
[system]  --- Personalisation, written by the user ---
          … where it disagrees with anything above, it wins:

          How they want answers written:
          - in Russian, short and direct
          Standing preferences, whatever the topic:
          - Python + FastAPI, free APIs only
          Who they are and what they are working on:
          - senior developer, team of 3, two weeks left
```

The first line is the developer's. It is where an identity, a safety framing and, one day, instructions about tools would live, and none of that is a user preference — a user who can empty that box is a user who can empty *those*. The second block is yours, and the app has to be able to hand it to you as something editable without also handing over the first. Merging them would make that impossible and would buy one field.

Which makes the label load-bearing rather than decorative. A request now has two authors in it, they are not equally binding, and the second must never read as though the first said it — so the block says who wrote it, in the first line, before anything else.

What else changed is four words. `LONG_TERM_PREFIX` used to introduce the long-term layer as "what you know about this user **and how they want to be answered**". It no longer says the second half, because a request that describes two of its blocks the same way is asking the model to work out which one meant it.

### Where it sits, and why last

```
[system]  the role                                        ← the developer's
[system]  LONG-TERM — what you know about this user
[system]  WORKING — what this task has established
[system]  summary | facts — what fell out of this chat's window
[system]  PERSONALISATION — who is asking, and how                 ← yours
          the window, verbatim
[user]    the question
```

The three memory blocks are ordered widest-scope-first, because that is the order of narrowing (see below). This one is not in that argument at all: it is not a scope, and "who is asking" cannot narrow "what is true".

It goes **last** for a different reason that happens to use the same mechanism. The layers above can still hold something that reads as a preference — day 11 lets you file one and pin it to every request — and two blocks giving different orders with no stated precedence is the one failure a prompt cannot recover from on its own. Later context is weighted more heavily, so last is where *the declaration beats the inference* is enforced rather than hoped for. Sitting closest to the question is the same argument twice: it is also where a standing instruction is most reliably obeyed.

The alternative was to join it onto the system prompt — one message carrying the developer's half and the user's half — which reads beautifully as *one configured agent* and gives up exactly that precedence. It also gives up the thing every other block here has: its own message, so the debug panel can show what contributed what. The label does the framing instead, by naming its author in its first line, which is cheaper than a placement and does the same job.

### It asks at the start of a new chat

An empty conversation is the only moment at which answering costs nothing — the profile is applied when a request is assembled, so configuring it before the first question and configuring it after the fourth differ only in how many answers came back wrong first. So that is where it asks:

```
┌──────────────────────────────────────────────────────────────┐
│  Tell the agent who you are                                  │
│  Three things it cannot work out from the question: how you  │
│  want answers written, the choices you have already made     │
│  that hold whatever the topic, and who you are and what you  │
│  are building. It goes into every request, in every chat.    │
│                                                              │
│  [ Set it up ]  [ Work ]  [ Weekend project ]      Not now   │
└──────────────────────────────────────────────────────────────┘
```

Drawn in the transcript rather than over it, and gone the moment anything is said. A dialog is a thing to dismiss; this is a thing to ignore. The saved profiles are on it because somebody who has already written one is not being asked to write another — they are being asked *which*.

Once a profile is on, the card stops asking and becomes one line — `Personalised as Work · …  Change` — because at that point the only thing an empty chat needs to be told is which personality the answers are about to arrive under.

It appears in the single view only. The compare columns are five agents being held against each other, and five copies of the same invitation is not five invitations, it is noise.

### Profiles, and why switching is one click with no warning

There are up to twelve of them, one is in force at a time, and **off is one of the choices** — not the absence of one. That row is half the demonstration: ask something, switch a profile on, ask it again.

The part worth stating plainly is what switching does *not* do. **Nothing a profile says is ever written into a conversation.** It is applied when a request is assembled and stored in no transcript, so:

- switching changes **every** chat on the machine, from the next message onwards, including the ones already open;
- there is no such thing as a message that "was sent under the old profile" for anything to stay consistent with;
- and deleting the active profile leaves personalisation **off**, rather than silently falling back to another one.

Which is why it can be a single click with nothing to confirm. The worst outcome is an agent that answers plainly until you say otherwise.

Creating a profile does not activate it, and editing one keeps its id — renaming "Work" to "Day job" must not detach it from the active pointer, or renaming the profile you are using would quietly switch personalisation off.

### What it costs

A profile is sent whole with every request — roughly 300–400 tokens at the length the fields allow, on every single message, forever. That is a standing cost rather than an occasional one, and it is the opposite trade from a memory layer, which is paid for only when it matches the question.

It is also why the fields are capped. The cap is not a backstop against abuse; it is the design. A profile that needs a fifth paragraph has stopped being a preference and become a prompt, and there is already a box for those, one line higher up.

### Checking it

```
1. Ask a design question with personalisation off. Keep the answer.
2. Fill in Context: "senior developer, team of 3, deadline in two weeks".
3. Ask the same question.          → it stops explaining what you know,
                                      and weighs the answer against the time
4. Add Preferences: "free APIs only, minimum dependencies".
5. Ask it again.                   → the paid service it recommended is gone
```

Then open the debug panel on any of those requests: the block is there as its own labelled system message, after everything the agent remembers and before the transcript. Then open `data/conversations/single.json` and search it for any of those words — they are not in it, and never were.

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
| **Working** | one task, across every chat about it | the task reaches `done` | **you**, with `/task` | `data/tasks/<id>.json` |
| **Long-term** | everything, always | you delete it | **you**, with `/remember` | `data/memory/long_term.json` |

The first row is the whole of day 10. That is worth saying plainly, because it is what keeps this day from being a rewrite: a sticky-fact block outlives the window but dies with the conversation, so by the test above it *is* short-term memory, and `window` / `facts` / `summary` are three ways of building it. Nothing in `strategies.py`, `facts.py` or `compaction.py` changed.

### A task is a record, not a tag

The middle layer needs a thing to be scoped to, and it cannot be the chat — if it were, there would be two layers, not three. So a **task** is a record of its own, with a title and a status, and several chats can join one. That is the point: you work on something for a week, in six conversations, and the sixth starts where the fifth left off.

Finishing a task is what turns a scope into a *lifetime*. Nothing is deleted — the record stays, its memory stays readable in the panel — but it stops being sent, because a finished piece of work's decisions are exactly what should stop turning up in conversations about something else. Reopening is free, which is what makes finishing something you will actually do.

Day 11 spelled "finished" as a status with two values in it. [Day 13](#the-task-as-a-state-machine) replaced that with four stages and a table of which may follow which; `done` inherited the whole of this paragraph, and the rest of that day is about the three stages in front of it.

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

(Day 12 adds one more between the last block and the window — the personalisation, which is not a scope and is not in the argument that follows. See [above](#where-it-sits-and-why-last).)

Widest first, because that is the order of narrowing and it puts each block *after* everything it might need to contradict. A task that has settled on English is stated after a user who generally prefers Russian; this conversation's own facts are stated after both. Models weight later context more heavily, so the narrower scope wins by construction rather than by a precedence rule written in prose that something would have to enforce.

They are separate system messages rather than one joined block so that the request in the debug panel shows which layer contributed what. Joining them would save a few tokens and make the day unprovable.

### Checking it

Three chats is the whole demonstration:

1. Chat **A**, in task **T**. Type `/task one row per message`, then `/always answer in Russian`.
2. Chat **B**, also in task **T** — a conversation that never heard either said. Ask about the schema: it knows. *The working layer crossed a conversation boundary.*
3. Chat **C**, in task **U**. Ask about the schema: it does not know. Ask anything at all: it still answers in Russian. *Scope is real in both directions.*
4. Drive task **T** to **done** (the [state machine](#the-task-as-a-state-machine) will make you pass through execution and validation to get there), go back to chat **B**: the working block is gone from the request, and every item is still in the panel. *Lifetime is real too.*

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

## Seven kinds of forgetting, kept distinct

| | Messages | Facts / summary | Branches | Working layer | Long-term layer | Personality |
|---|---|---|---|---|---|---|
| **Forget facts** / **Forget summary** | kept | gone | kept | kept | kept | kept |
| **✕** on a branch chip | that branch's only | that branch's only | one gone | kept | kept | kept |
| **Clear context** | gone | gone | gone | kept | kept | kept |
| **✕** on a compare column | gone | gone | gone | kept | kept | kept |
| **Finish a task** | kept | kept | kept | kept, not sent | kept | kept |
| **✕** on an item | kept | kept | kept | that one gone | that one gone | kept |
| **— no personality —** | kept | kept | kept | kept | kept | kept, not sent |
| **Delete** a profile | kept | kept | kept | kept | kept | that one gone |

The bottom two rows are day 12's, and they are the only pair in the table that does the same thing to different depths: switching personality off is `Finish a task` for the thing you declared — nothing is deleted, it simply stops being sent — while deleting a profile is `✕` on an item. Everything else in the last column says *kept*, and that is the point: none of those six gestures is a statement about who you are or how you want to be answered.

The two rows above them are day 11's, and the first four gained a column that says *kept* twice. That is deliberate: clearing a chat is a statement about that chat. Something you moved into another layer on purpose is not part of it any more, and "clear this conversation" is not a statement about the task or about you.

The first throws away only what the app made *of* the conversation, and nothing anybody said. It matters more for facts than it did for summaries: a summary is rebuilt from the transcript on the next overflow, so a bad one has a limited life, while a fact is carried forward untouched for as long as it stands and is never re-derived from what was actually said. A wrong one does not fade. It compounds.

Clearing the context takes the branches with it, and has to: a fork of a conversation that no longer exists is not a conversation anyone can read.

## Where the controls are

Day 11 adds one button to the topbar, next to the settings, with a badge on it when the agent has something waiting to be ruled on:

```
Memory  ②

  Task  [ Port the store to SQLite · Execution  ▾ ]  [ + New ]  [ Pause ]  [ Delete ]

  Planning  ›  ● EXECUTION  ›  Validation  ›  Done
                                              └ greyed: "Execution cannot
                                                go straight to done."
  The agent says the work is ready for validation.
  the build is finished · Moving marks as done: Every planned step done
  [ Move to validation ]  [ Not yet ]

  Next: Work the plan step by step; nothing left silently unfinished.
  ☑ Every planned step done, or dropped on purpose  • required
  ☐ Decisions taken while working are filed
  ☐ Departures from the plan are called out
  ▸ History (6)

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

Day 12 adds a second button beside it, with the active profile's name on it rather than a badge:

```
Personality  ⟨ WORK ⟩

  PERSONALITY                                                          YOU

  ○  — no personality —    Answers on the agent's default configuration
  ●  Work                  in Russian · Python + FastAPI · senior dev…   Edit  Delete
     STYLE  PREFERENCES  CONTEXT
  ○  Weekend project       plain English · Go, no deps · side project     Edit  Delete
     STYLE  preferences  CONTEXT

  [ + New profile ]                                                   2 of 12

  SENT WITH EVERY REQUEST
    How they want answers written:
    - in Russian
    - short and direct, no preamble
    Standing preferences, whatever the topic:
    - Python + FastAPI, minimum dependencies
    - free APIs only
    Who they are and what they are working on:
    - senior developer, team of 3, deadline in two weeks
```

A sibling of the memory button rather than a section inside it, because the two are not the same kind of thing — one holds what the agent learned, the other what you declared. The chip is the only part of this that has to be visible without opening anything: which personality the answer on screen was given under is exactly the question a panel cannot answer while it is shut.

Under each name, three chips, always all three, the unfilled ones greyed. A profile with only a style in it is a different thing from a finished one, and a truncated summary line cannot tell you which you are looking at — the absence is the information, so a list that only showed what *is* filled would be showing the wrong half.

The three fields in the editor come from `GET /api/config`, which reads them off `personality.FIELD_INFO` — so a field cannot exist in the form and not in the prompt. **Sent with every request** is the rendered block as the server will actually send it, served rather than rebuilt in the page: a screen showing its own idea of what was sent cannot be used to find out what was sent.

And the panel is not where you first meet this. A new chat asks for it [in the transcript](#it-asks-at-the-start-of-a-new-chat), which is the one place a person is looking at the moment the question is worth asking.

Day 14 adds a third, and the chip on it counts rather than names:

```
Invariants  ⟨ 3 IN FORCE ⟩

  INVARIANTS                                                            YOU

  ☑ In force                                                  3 of 4 sent

  ARCHITECTURE
   1.  Storage is JSON files on disk. No database, no ORM, no migrations.
       Why: the app has to run from a clone with no services to start
       ⌞postgres⌝ ⌞sqlite⌝ ⌞mongodb⌝                   ☑   Edit  Delete
  STACK
   —   Python and FastAPI on the server, vanilla JS on the page.
                                                       ☐   Edit  Delete
  BUSINESS RULES
   2.  Nothing the user writes leaves their machine.    ☑   Edit  Delete

  [ + New invariant ]                                              3 of 12

  SENT WITH EVERY REQUEST
    ARCHITECTURE
      1. Storage is JSON files on disk. No database, no ORM, …
```

A sibling of the personality button for the reason that one is a sibling of the memory button, and the line between them is what happens when each is ignored: a profile ignored gives you an answer that reads wrong, and an invariant ignored gives you one that was thrown away before you saw it. The chip says *how many* rather than on/off, because the count is the part you cannot work out from outside — and it turns red for one turn after something was caught, since the panel being shut is exactly when that is worth knowing.

Two levels of switch, and they answer different questions: a rule switched off has stopped being true, while the set switched off is the day turned off. A rule that is not in force is drawn with a **dash instead of a number**, because the numbering is an address the prompt and every refusal cite — it is not rule 2 that is off, it is not a rule right now. The kinds in the editor come from `GET /api/config`, reading `invariants.KIND_INFO`, so a kind cannot exist in the form and be dropped on the way in.

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

Day 16 adds the last button on the bar, and the first one that is not about this app at all — the three beside it show something somebody typed here, while this shows what somebody else's server said when it was asked what it can do:

```
MCP  [ 3 tools ]

  MCP SERVERS
  Tools this agent could be given, fetched from servers that publish them.
  Nothing here is called and nothing reaches a request yet.

  ● DeepWiki                              ☑  [ Refresh ]  [ Remove ]
    https://mcp.deepwiki.com/mcp
    DeepWiki 2.14.3 · protocol 2025-06-18 · no session · 3 tools · 1586ms
                                                        · checked 20:25
    ▸ What this server says about itself
    ▸ What went over the wire (3 requests)
        200   initialize                → text/event-stream   412ms
        202   notifications/initialized → application/json     98ms
        200   tools/list                → text/event-stream   611ms

    deepwiki__ask_question
    Ask any question about a GitHub repository.
    [ question: string ] [ repoName: string ]
    ▸ Input schema

    deepwiki__read_wiki_contents
    …

  ● Downed                                ☑  [ Refresh ]  [ Remove ]
    https://downed.example/mcp
    initialize: could not reach the server · 09:12

  [ + Connect a server ]                                        2 of 8
```

The chip counts **tools**, not servers, and only from servers that are switched on and answered — the number an agent would actually be offered, which is the one you cannot work out by looking. It goes red while a connected server is down.

Three things on a row are there because they are the things a list of tool names cannot tell you. The **status line** describes the connection rather than the catalogue: what answered, which protocol version the two of them settled on, whether a session was issued, how long it took, and when. The **wire trace** is the day's subject made visible — the same instinct as the debug panel's request JSON, applied to a different protocol, and the first thing to open when a row goes red. And the **timestamp** is what stops a cached catalogue from lying.

A server switched **off** keeps its row, its tools and its catalogue, greyed, and stops contributing to the count. That is a different state from removing it, and the difference is the one worth drawing: *not offering these* against *never heard of these*.

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

One new file this time (★), and nothing refactored. Each layer still knows only about the one below it.

```
static/index.html     the interface: draws what the server remembers
        │  GET  /api/conversations          (on load: rebuild the page)
        │  POST /api/chat                   {"message", "conversation_id", "branch", …}
        │  DELETE /api/conversations/{id}/messages     (clear context)
        │  DELETE /api/conversations/{id}/summary      (forget the compression)
        │  DELETE /api/conversations/{id}/facts        (forget the facts)
        │  POST/DELETE   …/checkpoints, …/branches     (fork, switch, abandon)
        │  GET/POST/PUT  /api/personality              (the profiles, and which is on)
        │  POST /api/personality/activate              (switch one on, or none)
        │  POST /api/tasks/{id}/phase                ★ (move a stage, if allowed)
        │  POST /api/tasks/{id}/steps/{key}          ★ (tick one checklist item)
        │  POST /api/tasks/{id}/pause                ★ (freeze it where it stands)
server.py             transport, the introduction between a saved transcript
        │              and an agent, and the one place that decides what a
        │              request is allowed to carry
        ├──────────┬──────────┬──────────┬────────────┬──────────┬────────┐
agent.py  strategies.py facts.py   memory.py  personality.py  phases.py  store.py
 the agent:  the three   the key-   the three   the three     ★ four      branches,
 facts=      names, the  value      layers, and fields, and   ★ stages,   facts,
 summary=    default and memory and what the    how they      ★ the table tasks with
 long_term=  the one line the patch agent would render into   ★ of legal  ★ a stage,
 working=    of arithmetic that     propose     a prompt      ★ moves,    ★ steps and
 personality= all three   updates it filing                   ★ checklists★ a log, the
 ★ state=     share                                           ★ and the   long-term
 role,params                                                  ★ watcher   layer, the
 request,rep                                                              profiles
        │
tokens.py                  reads `usage` out of a response, and adds usage up
        │                  (imports nothing from the app)
llm_client.py         HTTP: POST /chat/completions
   DeepSeek
```

`phases.py` is day 13's, and it is the one sibling that `store.py` imports. Everywhere else in this diagram the arrows point one way — the storage layer knows nothing about the modules above it — and the exception is deliberate: a stage is not a field, it is the *result* of a rule, so `TaskStore.move` calls `phases.check` and there is no way to write a stage to disk that the machine did not allow. A dumb setter here and a check in `server.py` would read the same on the day it was written and drift the first time somebody added a second route.

`personality.py` is the shortest of the six siblings and the only one that makes no request and stores nothing: it is three field names, a cleaner, and a renderer. It imports nothing from the app — **not even `memory.py`**, which is the part worth noticing. Personalisation and memory are next to each other in the diagram and share no code at all, because they share no mechanism: one is scored and sampled, the other is sent whole.

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

Day 12 adds one more argument and one `if`, and the argument arrives from somewhere none of the others do:

```python
agent = build_agent(settings, plan, profile)      # ← not settings, not plan
```

`plan` is the answer to "what of the past goes into this request", and a profile is not part of the past. `settings` is the chat's own configuration, stored with it and sent up with every message, and a profile is not that either — it is one object for the whole app. So it is its own parameter, read once in `post_chat` from the only place that knows which profile is switched on, and read there rather than inside `build_agent` so that the reply can report the same record the request was built from.

The agent itself is handed a string and a place to put it, exactly as it is for the other four blocks. It does not know the string came from a profile, that there are others, or that any of it can be switched — which is what keeps "add a fourth field" a change to `personality.py` and the form, and no change at all to `Agent`.

Day 13 adds a sixth block on exactly the same terms, and the sameness is the point:

```python
agent = Agent(client, config, …, state=phases.render(plan.task))
```

A stage, a checklist and one sentence saying what is expected next — rendered by `phases.py`, handed over as text, and put after the working layer because that is the scope it shares. The agent does not know a machine decided what could go in that string, which is what keeps "there is now a fifth stage" a change to `phases.py` and no change at all to `Agent`.

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

### And what a personality looks like on disk

`data/personality/profiles.json` — one file, every profile, and one pointer:

```json
{
  "profiles": [
    { "id": "work",
      "name": "Work",
      "fields": {
        "style": "in Russian\nshort and direct, no preamble",
        "preferences": "Python + FastAPI\nminimum dependencies\nfree APIs only",
        "context": "senior developer\nvoice assistant\nteam of 3\ndeadline in two weeks"
      },
      "created_at": "2026-09-20T09:14:02.118+00:00",
      "updated_at": "2026-09-20T09:31:55.702+00:00" }
  ],
  "active": "work",
  "updated_at": "2026-09-20T09:31:55.702+00:00"
}
```

One file rather than one per profile, for the reason `long_term.json` is one file: the whole of it is read on every single request. `active` lives in the same file as the list it points into, so an id naming a profile that has been deleted is not a state anything has to handle — `Personality.resolve` clears it on the way in, which is also why deleting the profile you are using switches personalisation off instead of quietly promoting another.

`updated_at` is separate from `created_at` here and nowhere else in the app. That is the whole difference between a profile and a `MemoryItem`: an item is filed once and afterwards only replaced, and a profile is *edited*.

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
- Nothing new since day 20: the weather server (Open-Meteo) and the planner need no key.
- Since day 17, for the maps server only: a **Google Maps Platform** API key with the **Routes API** and **Places API (New)** enabled on its project ([console](https://console.cloud.google.com/google/maps-apis)). Without it the agent still runs — the two maps tools answer with "no key" and the model says so.

The server refuses to start without the DeepSeek key, and says so rather than failing on the first message.

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
   cd day20-mcp-orchestration
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
   GOOGLE_MAPS_API_KEY=your_google_maps_api_key_here
   ```
   The `.env` file is git-ignored, so your key never gets committed. So are editor swap files (`*.swp`) — a swap of `.env` would carry its contents — and `data/`, which is where your conversations end up.

## Running

```bash
uv run server.py
```

Then open **http://127.0.0.1:8000**.

Since day 17 there is a second process, and it is optional: the local MCP server the agent calls its two Google Maps tools on. Start it in its own terminal, before or after the app — the catalogue is fetched when you press refresh in the MCP panel, not at boot:

```bash
uv run maps_mcp_server.py      # http://127.0.0.1:8787/mcp
```

Since day 18 there is a third, and it is the one that makes the agent work while nobody is asking: the events scheduler. It needs no key, and it keeps its clock and its database (`data/events/events.db`) whether or not the app is up:

```bash
uv run events_mcp_server.py    # http://127.0.0.1:8788/mcp
```

Then tell the agent what you want to follow ("jazz and theatre in Cape Town, every morning") and watch the **Events** panel.

**Since day 20 you do not have to**: `uv run server.py` starts all four of ours - maps `:8787`, events `:8788`, weather `:8789`, planner `:8790` - fetches their tools, and stops them when you stop it. A server already running on its port is used and left running, and `MCP_AUTOSTART=0` in `.env` turns this off (then `uv run run_mcp_servers.py` starts them by hand). One consequence worth knowing: an events server the app started stops with the app, so its schedule only runs while the app does - for 24/7, run it on its own (or on the VPS) and the app will find it. That is the development setup; the real one has this server on a VPS and the agent connecting to it - see [Deploying it](#deploying-it-the-server-on-a-vps-the-agent-at-home).

It is a separate process because an MCP server *is* a separate thing: the agent reaches it over HTTP exactly as it reaches DeepWiki, and a server that only ever ran inside this app would be a function with extra steps. Stop it and the panel's row goes red with the reason; the rest of the app carries on. Send a message, stop the server with `Ctrl-C`, start it again, reload the page: the conversation is still there, and the agent still knows what is in it.

Conversations are written to `data/conversations/` next to `server.py`. Point `CHAT_STORE_DIR` somewhere else to keep them elsewhere:

```bash
CHAT_STORE_DIR=~/chats uv run server.py
```

Three more directories sit beside it, one per thing that is not a conversation, each with its own override: `data/tasks/` (`CHAT_TASKS_DIR`), `data/memory/` (`CHAT_MEMORY_DIR`) and, since day 12, `data/personality/` (`CHAT_PERSONALITY_DIR`). The last one is a fourth directory rather than a corner of the third on purpose — `data/memory/` is where a reader goes to find out what the agent *knows*, and filing standing instructions under that name would make the one distinction this day exists to draw invisible on disk.

Two seeded profiles — both switched off — are written the first time the server starts and never again. The condition is the file, not an empty list: deleting them is a thing a person can decide, and an app that quietly put them back on the next restart would be overruling them once a day.

They are two different kinds of thing, which is the whole of why there are not six:

- **`Example`** fills all three fields, so the shape is visible before you have filled any of them in. Read it, then delete it.
- **`Rational`** fills only `style`, and is meant to be used as it stands: answer first and briefly, then name what the question leaves out — the assumption it rests on, the option not considered, the cost not priced — make the strongest case *against* the answer just given, and say plainly when there is not enough information to answer and what is missing.

The first cut of this day shipped three writing styles, because a profile *was* a writing style. A profile is now largely about a particular person — their seniority, their project, their team — and shipping invented people would be noise in the one list that is supposed to be yours. But `style` is the one field of the three that is **not** about a particular person: nothing about "answer first, then say what the question leaves out" belongs to anybody, which is exactly what makes a style-only profile shippable when a filled-in `context` is not.

`Rational` is also written as instructions about the *shape of an answer* rather than as a character. "You are a rigorous analyst" is something a model performs; "name the assumption the question rests on" either happened or it did not, and only the second kind can be checked against the answer that comes back.

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

Day 17 adds the last act, and it checks the two ends of tool use that do not need a network. A catalogue is written into the store, a message is sent, and the **outgoing request** is checked for the tool it should now be carrying under its qualified name — then the server is switched off and the same message carries no `tools` key at all. Then a turn that *did* call something is planted on disk, calls and all, and the page is loaded against it: the cards come back with nothing called, the failed call comes back as a receipt with no cards under it, and the whole block sits after the bubble rather than inside it. No tool is ever called in the suite — the dummy key means the model never gets as far as asking for one.

It starts its own server on port 8765 (override with `TEST_PORT`) and shuts it down afterwards, so it never collides with a dev server on :8000. It points that server's `CHAT_STORE_DIR` at a fresh temp directory, so it never reads or writes the conversations you have been having in the real app — and the same for the other three, which matter more: a task, the long-term layer and the personality profiles are not scoped to a chat, so a suite run against the real files would edit the memory and the voice the app carries into *every* conversation. **It cannot spend tokens:** the API key handed to that process is a dummy, and since `python-dotenv` does not override an already-set variable, a real key in `.env` is ignored. DeepSeek answers every call with a 401 — which is all these checks need, since they cover the plumbing, not model output. (It also makes the error-turn behaviour easy to assert: every stored answer in the suite is a failed one.) Nothing in the suite touches the network.

Day 8 adds a third act, in two halves. The first drives the usage strip on a live page, where every answer is a 401: that it is there, that it is folded away, that it opens — and that it then claims **no numbers at all**, because nothing was billed. The second plants a conversation on disk carrying two `usage` blocks — the one thing a dummy API key can never produce — and loads the page against it, checking that the last turn's request, answer and thinking come back, that the chat totals are the *sum* of both turns rather than a copy of the last one, and that the same figures come out of `/api/conversations`.

Day 9 adds a fourth, and it is the only act that looks at a **request body** rather than at the page: a conversation is planted on disk with twelve messages and a summary covering the first eight — the one thing, again, that a dummy key can never produce — and a message is then sent to it. What comes back is checked against the rule the whole feature rests on: the request is the system prompt, the summary as a labelled system message, exactly four messages verbatim, and the question; and not one of the eight messages the summary stands for appears in it twice. The same act switches compression off and watches the summary drop out of the request while staying on disk, drives a compression that **fails** (a dummy key makes that easy) and checks that the turn still goes and that nothing was written down that was never produced, then drives the strip on the page: the numbers, the summary text in full, and **Forget summary** — after which the store has no summary and still has all twelve messages.

Day 10 adds a fifth, in three parts. The first sends a message to a planted twelve-message conversation under each strategy in turn and reads the **request body** that came out: the sliding window is one system message and the last four, with the strip reporting eight dropped and no second request made at all; sticky facts is the same window with a labelled block in front of it, one `key: value` per line, and not one of the eight messages it stands for sent twice. The second drives an extraction that **fails** — a dummy key makes that easy — and checks that the turn still goes, that the block goes up exactly as it already stood, and that nothing was written down that was never produced. The third needs no model at all: a checkpoint is saved, forked **twice from the same position**, a message sent into each branch, and the suite asserts that both forks show eight messages while storing two, that `main` is still exactly twelve on disk, that switching gives back the other continuation intact, that a message lands in the branch on screen, and that the trunk cannot be deleted out from under its children. Then all of it again on the page, through the branch bar — including the checkpoint drawn in the transcript at the message it marks, and the whole tree coming back after a reload.

Day 12 adds another act. It starts a brand-new chat and checks that it is **asked** — the card in the transcript, its saved profiles one click each — then sends a question and confirms two things at once: no personalisation rode along, and the invitation got out of the way the moment anything was said. It starts another chat, switches a profile on *from the card itself*, and watches the card stop asking and become one line. Then it asks the API for a request body and asserts the placement the day turns on — the one decision here that is arguable, and therefore the one worth pinning down: the personalisation is its own labelled system message, the developer's line is still first and is *not* what carries it, and the block sits **after** everything the agent remembers, with all three fields under labels of their own — including `team of 3`, which is the field the agent could never have worked out. Then, against the filesystem: **not one conversation record mentions any of it.** The rest is the panel — three named fields rather than one box, the three filled/unfilled chips under each name, an edit saved against the same id so the active profile stays active, a new profile that does *not* switch itself on, a profile written under this day's first four-field shape folding into `style` **without its prohibitions being inverted**, and deleting the active one leaving personalisation **off** rather than falling back to somebody else's.

Day 13 adds another act, and it is the one act in the suite where most of the checks are that something **did not happen**. A task is made in the panel and the machine is walked end to end through the UI a person would use: all four stages drawn, the illegal jump greyed with the reason on it, the legal one greyed too because the checklist is not done, both boxes ticked and the gate opening, the move, the fresh checklist on the other side. Each refusal is then asked for again over HTTP, because a rule that only exists in a disabled button is a styling choice — `409`, the machine's own sentence, and nothing whatever added to the task's log, since a move that did not happen is not history. It checks the state block in a real request body (the stage, the checklist, the expected action, the line that tells the agent not to explain the task back, and the stage discipline that tells it not to answer out of stage), the strip above the message box saying the same stage in three words, that a pause refuses every move from either direction while still sending everything the task knows, that a reload comes back at the stage the work stopped at with its boxes still ticked, that going back a stage unticks the required boxes and only those, and finally that a task driven to `done` keeps every item it learned and sends neither them nor its stage.

Day 14 adds the last act, and it is shaped by what a dummy key allows. The panel, the store and the numbering are all driven through the page a person would use: the shipped rules written down but switched off, the set switched on and the rules acquiring numbers, one written in the form and found on disk with its kind, its reason and its markers, one edited and its id and its place surviving the rewrite — which is what stops an earlier refusal in the transcript citing the wrong line — and one switched off, whereupon it loses its number entirely and the rules after it renumber rather than leaving a gap. Then the placement in a real request body, which is this day's one arguable decision and therefore the one worth pinning down: the invariants are their own labelled system message, they sit **after** the personalisation block, and nothing of the system's comes after them. Then `off` as a state rather than a missing value, the checking declined while the rules still ride on the request, and the cap refusing a thirteenth.

Day 16 adds one more, and it is the act with the strongest reason to invent its data. The suite writes a **fabricated catalogue** straight into the store — one server that answered with a tool, a session and an older protocol, one that did not answer at all — and loads the page against it. Everything drawn from that comes out of a shape a real server produced once, so the render path is asserted on every run without anybody's server being asked anything: the chip counting tools rather than servers and going red while one is down, the failed server keeping its row and saying *what* went wrong, the status line reporting the connection rather than the catalogue, the tool listed under its **qualified** name, its required arguments marked, its input schema verbatim, and the wire trace on screen. The endpoints that refuse a URL are driven directly, and both refuse before reaching the network — a duplicate and a non-`http(s)` scheme. Switching a server off drops its tools from the count while leaving its catalogue on disk. Nothing in the act opens a connection, which is what keeps *nothing in the suite touches the network* true.

The one thing the suite cannot do is ask a model, since every call here is a 401 — which is exactly why the free half of the checking is an endpoint. `POST /api/invariants/scan` is driven directly: `Postgres` in a sentence is a hit with the rule's number on it, and `the format of that file is fine` is not, because markers match on a word boundary and `format` is not `orm`. A day whose whole claim is *enforced rather than merely stated* should not rest entirely on a call the suite is unable to make.

What it does *not* cover: how anything looks. jsdom has no layout engine, so widths, wrapping and overflow are only checked as computed style values, never as rendered pixels.

Day 18 adds the last act, and it is the one about things nobody asked for. Two digests are planted exactly as the courier stores them, with an unread count of two, and the page is loaded against them: the count is on the **Events** button, the chat is in the menu as **Digests** rather than under its first line, each digest says which watch posted it, why and who wrote it (the model, or the template when the model was unavailable), the events come back as cards whose titles are links with the counts the server computed over them, and opening the chat clears the count on screen *and* on disk. Then the part with no events server running: the panel says it cannot reach it rather than drawing nothing, a poll delivers nothing and records why, and a server switched off in the MCP panel is respected here too. Last, a question asked in the Digests chat goes up with the digests in front of it. The courier's timer is off for the whole suite (`DIGEST_POLL_SECONDS=0`), so the result cannot depend on whether an events server happens to be running on this machine.

The scheduler itself is not a page, so it has tests of its own, in Python and with no network - every fetch goes through `events_mcp_server.http_get`, which the tests replace with a dictionary of pages shaped like the real ones:

```bash
uv run python -m unittest discover -s tests -v
```

They cover the three parsers (four date shapes, a site that stamps local time as UTC, an iCal offset spelled as a zone name, online and cancelled events dropped), the area filter, the clock (a week of downtime is **one** catch-up run; an on-demand run does not move the schedule; a run interrupted by a crash is not left "running"), what counts as new, one source down being a partial run, dedup across listings, whole-word interest matching, the courier - each digest delivered once across restarts, several waiting for one watch arriving as **one** catch-up that starts where the last delivered digest ended, a watch deleted meanwhile represented by its newest digest, one watch failing without holding back or repeating another, a recreated database resetting the cursors - and serving: localhost needs nothing, a public address is refused without a token, TLS and a host name, and a request without the right token never reaches the MCP layer.

Day 20 adds an act about a turn that went through several servers. One is planted on disk - six calls over four servers, a forecast, a saved plan, one call answered from the cache, and a scenario's checks with one of them failed - and the page is loaded against it: the flow is drawn under the cards, open because it carries checks, one row per round with the side-by-side round marked, each step naming its server and which earlier call fed which argument (and "you" for what the user typed), the cached call saying so, the failed check saying why; the forecast as a tile a day with the server's verdict; the plan as the itinerary and the file it went into. Then the **Scenarios** panel against catalogues written into the store, with the events server silent: the scenario that needs it is marked and cannot be run, the other two can, an unknown scenario id is refused before anything is spent, and **Run** sends the prompt from a new chat with the scenario named - and since the key is a dummy the model never answers, so the checks come back **0/9** and red, stored with the answer they judged, rather than passing because nothing was called. A message after that is held to no checks. The long flow itself, over four real servers, is `tests/test_orchestration.py`.

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
| `POST` | `/api/chat` | ask a question in a conversation — `{"branch"}` optional; `settings.rag` (day 22) answers it from the index, `409`/`503` if there is none to read |
| `GET` | `/api/tasks` | every task, newest first, plus the long-term layer |
| `POST` | `/api/tasks` | start a piece of work — `{"title"}` |
| `POST` | `/api/tasks/{id}/phase` | move a stage — `{"to", "why", "confirm"}`; `409` with the machine's reason if it may not. `confirm` ticks the current stage's outstanding required steps and answers that rule only |
| `DELETE` | `/api/tasks/{id}/suggestion` | “not yet” — drop the agent's pending offer, changing nothing |
| `POST` | `/api/tasks/{id}/steps/{key}` | tick or untick one step of the current stage — `{"done"}` |
| `POST` | `/api/tasks/{id}/pause` | `{"paused": true, "note"}` — freezes the machine in whatever stage it is in |
| `POST` | `/api/tasks/{id}/note` | what the work is waiting on — `{"note"}` |
| `DELETE` | `/api/tasks/{id}` | forget a task, its stage, its log and what it learned; chats that joined it are fine |
| `POST` | `/api/tasks/{id}/memory` | file into the working layer — `{"value"}`, plus optional `{"key", "source", "pinned"}` |
| `DELETE` | `/api/tasks/{id}/memory/{key}` | forget one working item |
| `GET` / `POST` | `/api/memory` | read / file into the long-term layer |
| `DELETE` | `/api/memory/{key}` | forget one long-term item |
| `POST` | `/api/conversations/{id}/task` | join a task — `{"task_id"}`, or `null` to leave |
| `POST` | `/api/conversations/{id}/proposals/{n}` | accept a suggestion — `{"layer"}` overrules the agent |
| `DELETE` | `/api/conversations/{id}/proposals/{n}` | dismiss one |
| `GET` | `/api/personality` | every profile, which is active, and the block that rides on the next request |
| `POST` | `/api/personality` | a new profile — `{"name", "fields"}`; does not activate it |
| `PUT` | `/api/personality/{id}` | edit one in place, id and name unchanged unless you say so |
| `POST` | `/api/personality/activate` | `{"profile_id"}`, or `null` to switch personality off |
| `DELETE` | `/api/personality/{id}` | forget a profile; deleting the active one leaves personality off |
| `GET` | `/api/index` | day 21: embedder and its status, corpus, built indexes with stats, the running build, last check |
| `PUT` | `/api/index/corpus` | `{"patterns": [...]}` — globs relative to the project folder |
| `POST` | `/api/index/uploads` | `{"name", "content"}` — a `.md`/`.txt`/`.py` file into `data/index/uploads/` |
| `DELETE` | `/api/index/uploads/{name}` | remove an upload |
| `GET` | `/api/index/sections?source=` | a document's outline, for the question editor |
| `POST` | `/api/index/preview` | `{"strategy", "params", "source"}` — chunk without embedding: stats, and one file's chunks |
| `POST` | `/api/index/build` | `{"items": [{"strategy", "params"}]}` — chunk, embed, store, in the background; `409` if one is running |
| `GET` | `/api/index/job` | how far the build has got |
| `DELETE` | `/api/index/indexes/{name}` · `/api/index/indexes` | reset one index, or all — chunks and vectors; the cache stays |
| `DELETE` | `/api/index/cache` | forget cached vectors |
| `GET` | `/api/index/indexes/{name}/chunks` | browse a built index — `?source=&offset=&limit=` |
| `POST` | `/api/index/search` | `{"query", "k", "source"}` — every built index, side by side |
| `GET` · `PUT` | `/api/index/questions` | the retrieval check's questions |
| `POST` | `/api/index/evaluate` | run the check — `{"depth"}`; `GET` returns the last run |
| `GET` | `/api/rag` | day 22: indexes and embedder, models, the control questions, the last run and its summary |
| `PUT` | `/api/rag/questions` | the control set — `[{"question", "expected", "keywords": ["a \| b", …], "sources": [{"source", "section"}]}]` |
| `POST` | `/api/rag/ask` | a free question both ways, side by side — `{"question", "index", "k", "model"}`; not saved |
| `POST` | `/api/rag/check/{id}` | one control question both ways, scored and saved — `{"index", "k", "model"}` |
| `DELETE` | `/api/rag/results` | forget the last run |

Not one of the five personality endpoints takes a conversation id, and that is the shape of the day's claim: a personality is one object the whole app runs under, so there is nowhere for a chat to appear in the paths.

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
#  "working":["one-row-per-message"],"working_held":1,"task_done":false}
```

`constraint.language` rode along although the question said nothing about language; it was starred on the way in, and starred notes are not filtered. Drive the task to `done` and send the same question again, and `working` comes back empty while `working_held` stays at 1 — the memory is still there, it has simply stopped being true of anything you are doing.

And the whole of day 13, which is the shortest of the three, because most of it is one refusal:

```bash
curl -s localhost:8000/api/tasks/$T/phase -H 'Content-Type: application/json' \
  -d '{"to": "done"}'
# {"detail": "Planning cannot go straight to done."}                  ← 409

curl -s localhost:8000/api/tasks/$T/phase -H 'Content-Type: application/json' \
  -d '{"to": "execution"}'
# {"detail": "Not yet: \u201cGoal and definition of done\u201d, \u201cAn agreed
#             list of steps\u201d still to do in planning."}           ← 409

for s in goal plan; do
  curl -s localhost:8000/api/tasks/$T/steps/$s -H 'Content-Type: application/json' \
    -d '{"done": true}' > /dev/null
done
curl -s localhost:8000/api/tasks/$T/phase -H 'Content-Type: application/json' \
  -d '{"to": "execution"}' | jq '.task | {phase, expected}'
# {"phase": "execution",
#  "expected": "Work the plan step by step; nothing left silently unfinished."}

# Neither refusal left a trace - a move the machine would not make did not
# happen, so the log holds the one that did and nothing else:
curl -s localhost:8000/api/tasks | jq '.tasks[0].log'
# [{"at": "…", "kind": "move", "by": "you", "from": "planning", "to": "execution"}]
```

And the whole of day 12, which is shorter because there is less to it — one object, one switch:

```bash
curl -s localhost:8000/api/personality -H 'Content-Type: application/json' -d '{
  "name": "Work",
  "fields": {
    "style":       "in Russian\nshort and direct, no preamble",
    "preferences": "Python + FastAPI\nminimum dependencies\nfree APIs only",
    "context":     "senior developer\nvoice assistant\nteam of 3\ndeadline in two weeks"
  }}' | jq '{active, names: [.profiles[].name]}'
# {"active": null, "names": ["Example", "Work"]}   ← created, not switched on

curl -s localhost:8000/api/personality/activate -H 'Content-Type: application/json' \
  -d '{"profile_id": "work"}' | jq -r .block
# How they want answers written:
# - in Russian
# - short and direct, no preamble
#
# Standing preferences, whatever the topic:
# - Python + FastAPI
# ...

# Every chat on the machine now answers that way - including ones opened
# before the switch, because nothing about a profile is stored in a chat.
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message": "is this a good schema?", "conversation_id": "single"}' \
| jq -r '.personality.active, (.debug.request.messages | map(.role) | join(", "))'
# "work"
# system, system, user          ← the role, the profile, the question

# And off is a state, not a missing value:
curl -s localhost:8000/api/personality/activate -H 'Content-Type: application/json' \
  -d '{"profile_id": null}' | jq '{active, block}'
# {"active": null, "block": ""}
```

Note where the block landed: its own system message, after anything the agent remembers and before the question. Then `grep -l "voice assistant" data/conversations/*.json` and find nothing — the profile was applied to the request and written into no transcript, which is the one claim of the day that can be checked against the filesystem.

And day 14, which is the same shape again with one endpoint that is not — `scan`, the half of the checking that needs no model:

```bash
curl -s localhost:8000/api/invariants -H 'Content-Type: application/json' -d '{
  "kind":     "architecture",
  "text":     "Storage is JSON files on disk. No database, no ORM, no migrations.",
  "because":  "the app has to run from a clone with no services to start first",
  "markers":  ["postgres", "sqlite", "mongodb", "orm"]
}' | jq '{in_force, ids: [.rules[].id]}'
# {"in_force": 1, "ids": ["storage-is-json-files-on"]}   ← created in force,
#                                                          unlike a profile

# The deterministic half, on its own, with nothing plugged in. A hit is a
# place to look - never a verdict, which is why it is reported as one:
curl -s localhost:8000/api/invariants/scan -H 'Content-Type: application/json' \
  -d '{"text": "I would put the whole thing in Postgres"}' | jq -c .hits
# [{"rule_id":"storage-is-json-files-on","marker":"postgres","number":1, …}]

curl -s localhost:8000/api/invariants/scan -H 'Content-Type: application/json' \
  -d '{"text": "the format of that file is fine"}' | jq -c .hits
# []          ← markers match on a word boundary: `orm` is not `format`

# Switching one rule off renumbers the rest rather than leaving a gap, which
# is why every write returns the whole set:
curl -s localhost:8000/api/invariants/storage-is-json-files-on/toggle \
  -H 'Content-Type: application/json' -d '{"enabled": false}' \
| jq '{in_force, numbers: [.rules[].number]}'
# {"in_force": 0, "numbers": [0]}        ← not rule 1 switched off: not a rule

# And off is a state here too, at both levels:
curl -s localhost:8000/api/invariants/enabled -H 'Content-Type: application/json' \
  -d '{"enabled": false}' | jq '{enabled, block}'
# {"enabled": false, "block": ""}
```

Ask something that breaks one and the refusal is in the answer, with the alternatives under it — and `violations` says which rule was in the way and whether what you are reading is the second attempt:

```bash
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message": "move it all to Postgres", "conversation_id": "single"}' \
| jq '{v: .violations, checked: .debug.invariants.describe,
       roles: [.debug.request.messages[].role]}'
# {"v": null,                            ← the prompt did it; nothing re-asked
#  "checked": "clear - 1 marker checked", ← naming a rule to refuse it is
#  "roles": ["system","system","user"]}     not breaking it
```

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

To read the tool catalogue from the command line — this one contacts nobody, it reads the cache:

```bash
curl -s localhost:8000/api/mcp | jq '{tools, connected, servers: [.servers[].id]}'
# {"tools":3,"connected":1,"servers":["deepwiki"]}
```

To go and ask a server again, which is the one call in this app that opens a connection to a third party:

```bash
curl -s -X POST localhost:8000/api/mcp/deepwiki/refresh \
| jq '.servers[0].catalogue | {server_name, protocol, session,
        steps: [.steps[] | "\(.status) \(.label) \(.content_type)"],
        tools: [.tools[].function_name]}'
# {
#   "server_name": "DeepWiki",
#   "protocol": "2025-06-18",
#   "session": false,
#   "steps": [
#     "200 initialize text/event-stream",
#     "202 notifications/initialized application/json",
#     "200 tools/list text/event-stream"
#   ],
#   "tools": ["deepwiki__ask_question", "deepwiki__read_wiki_contents",
#             "deepwiki__read_wiki_structure"]
# }
```

To connect another one — the catalogue is fetched in the same request, so the reply already says whether it works:

```bash
curl -s localhost:8000/api/mcp -H 'Content-Type: application/json' \
  -d '{"label": "AWS Knowledge", "url": "https://knowledge-mcp.global.api.aws/mcp"}' \
| jq '[.servers[] | {id, ok: .catalogue.ok, protocol: .catalogue.protocol,
                     n: (.catalogue.tools | length)}]'
# [{"id":"deepwiki","ok":true,"protocol":"2025-06-18","n":3},
#  {"id":"aws-knowledge","ok":true,"protocol":"2025-03-26","n":5}]
```

A server that cannot be reached is stored as a failed listing rather than refused, so the row stays and can be retried. `POST /api/mcp/{id}/toggle` switches one off without forgetting it, and `DELETE /api/mcp/{id}` disconnects — which closes nothing, because nothing was held open.

`conversation_id` defaults to `single`, so a bare `{"message": "..."}` lands in the single chat — the same conversation the browser is looking at. Ids become file names, so they are restricted rather than escaped: letters, digits, `-` and `_`, up to 64 characters. Anything else is a 400.
