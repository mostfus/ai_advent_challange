"""Reading the token counts the API reports - `usage`, and nothing else.

Day 8's subject. Days 1-7 sent messages and read answers; this module is
about the number underneath both of them, because a chat-completions API does
not charge per message, it charges per token - and since day 7 this app
replays a growing transcript into every single request. The cost of a
conversation is therefore not a property of what you just typed. It is a
property of everything you have said so far.

There is exactly one place that number can honestly come from: the `usage`
object the provider sends back with every answer. It is the provider's own
reckoning, it is what the bill is written from, and it is exact. Counting
characters locally to guess at it would only ever produce a second, worse
number sitting next to the real one, so this module does not try. Every
figure this app shows was reported by DeepSeek.

The price of that is that a count exists only *after* a request has been
made: there is no number for a message still being typed, and none at all for
a turn that failed. Both are shown as what they are - nothing - rather than
as a zero.

What is here is the reading and the adding up:

    usage_from_response(payload)   ->  one turn's cost, normalised
    sum_usage(list of those)       ->  what a whole conversation has cost

This module imports nothing from the app: it reads dicts, and knows nothing
about agents, stores or HTTP.
"""

from __future__ import annotations

#: Zeroed usage, and the shape every usage dict in this app has. Written out
#: once so that "nothing billed yet" and "usage from a response" are the same
#: shape and no caller has to test for None field by field.
EMPTY_USAGE = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "reasoning_tokens": 0,
    "content_tokens": 0,
    "cached_tokens": 0,
}


def as_int(value) -> int:
    """A count from an untrusted dict, as an int - 0 for anything else.

    Usage blocks arrive as provider JSON and are read back out of files
    written by earlier versions of this app, so "the field is missing" and
    "the field is null" are ordinary cases rather than bugs.
    """
    return int(value) if isinstance(value, (int, float)) else 0


def usage_from_response(payload: dict | None) -> dict | None:
    """The `usage` block of a response, normalised - or None if there is none.

    Normalised because the interesting numbers are spread across three
    different places in an OpenAI-compatible response, and two of them are
    optional:

    * `prompt_tokens` / `completion_tokens` / `total_tokens` - always there.
      The first is the *whole request*: system prompt, replayed history and
      the new question together. The second is the answer.
    * `completion_tokens_details.reasoning_tokens` - what the model spent
      *thinking*. DeepSeek thinks by default (see `agent.REASONING_EFFORTS`),
      those tokens are billed as output, and they are invisible in the answer
      on screen - so a reply of two words can cost hundreds of tokens, and
      this is the field that explains why.
    * `prompt_cache_hit_tokens` (also seen as `prompt_tokens_details
      .cached_tokens`) - the part of the prompt DeepSeek served from its own
      cache, billed at a fraction of the normal rate. It grows on its own as
      a conversation gets longer, because the replayed prefix stops changing.

    `content_tokens` is the one derived field - completion minus reasoning -
    because the API reports the total and the thinking, while what is
    actually on screen is the difference.
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        return None

    details = raw.get("completion_tokens_details")
    reasoning = as_int(details.get("reasoning_tokens")) if isinstance(details, dict) else 0

    prompt_details = raw.get("prompt_tokens_details")
    cached = as_int(raw.get("prompt_cache_hit_tokens"))
    if not cached and isinstance(prompt_details, dict):
        cached = as_int(prompt_details.get("cached_tokens"))

    completion = as_int(raw.get("completion_tokens"))
    return {
        "prompt_tokens": as_int(raw.get("prompt_tokens")),
        "completion_tokens": completion,
        "total_tokens": as_int(raw.get("total_tokens")),
        "reasoning_tokens": reasoning,
        # Never negative: a provider that reported reasoning tokens outside
        # the completion total would otherwise produce a nonsense readout.
        "content_tokens": max(completion - reasoning, 0),
        "cached_tokens": cached,
    }


def sum_usage(usages) -> dict:
    """Add up every turn's usage - what a whole conversation has cost.

    Every term was reported by the provider for one request, and they are
    simply added; nothing here is derived from the text. `turns` counts how
    many answers contributed, which is what makes the total readable: a chat
    of twelve turns has paid for its history twelve times over, because a
    stateless API has to be sent the whole thing again on every one of them.
    """
    total = dict(EMPTY_USAGE)
    turns = 0
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        turns += 1
        for key in EMPTY_USAGE:
            total[key] += as_int(usage.get(key))
    total["turns"] = turns
    return total
