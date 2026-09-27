"""The three ways this app decides what a request is allowed to remember.

Day 9 had one decision and a switch: cut the transcript at N messages, and
either drop the offcut or summarise it. Day 10 is that decision made
properly - a named strategy, chosen per conversation, with the switch in the
settings next to the model and the temperature.

Every strategy here starts from the same fact and the same cut. The fact is
that a chat-completions request is stateless: the whole context goes up every
time and is billed every time, so a conversation that is never trimmed gets
more expensive per message the longer it runs. The cut is the **window** -
the last N messages, sent as themselves, always. What the strategies disagree
about is what happens to everything in front of the window:

    window   nothing. It is not sent. The chat forgets, and the only honest
             thing to do about that is to say so on screen.

    facts    a key-value block, kept up to date after every user message and
             sent in the window's place. Small, stable, and re-read rather
             than re-derived: five lines that say what the user is trying to
             do survive a hundred messages that say how they got there.

    summary  day 9's paragraph: the offcut compressed into prose, written
             once per overflow and stored.

They are mutually exclusive by construction. Two of them injecting a block of
"here is what you have forgotten" into the same request would mean two
descriptions of the same messages disagreeing with each other in front of the
model, and there is no way for it to know which one is stale.

**Branching is not on this list and is not a fourth strategy.** A strategy
decides how much of a transcript goes into a request; a branch decides *which
transcript* that is. They compose - every branch is subject to whichever
strategy the conversation is set to - and `store.py` is where branching
lives, because a branch is a thing that is saved rather than a thing that is
decided per request.

This module holds the names, the defaults and the one piece of arithmetic all
three share. The work each one does is elsewhere: nothing for `window`,
`facts.py` for `facts`, `compaction.py` for `summary`.
"""

from __future__ import annotations

#: The strategy ids, in the order they are offered. `window` first because it
#: is the only one that makes no extra API call: it is the cheapest, the
#: simplest and the one every chat starts on.
WINDOW = "window"
FACTS = "facts"
SUMMARY = "summary"

STRATEGIES = (WINDOW, FACTS, SUMMARY)
DEFAULT_STRATEGY = WINDOW

#: What each one is called and what it actually does, sent to the frontend so
#: the selector and this file cannot drift apart. `cost` is the part worth
#: reading twice: it is the difference between a strategy that saves money and
#: one that spends it, and it is not visible anywhere on screen until a bill
#: arrives.
STRATEGY_INFO = {
    WINDOW: {
        "label": "Sliding window",
        "hint": "Only the last N messages are sent. Everything older is dropped "
                "from the request - the chat genuinely forgets it.",
        "cost": "No extra requests. The cheapest strategy there is.",
    },
    FACTS: {
        "label": "Sticky facts",
        "hint": "A key-value block of what matters - the goal, the constraints, "
                "the decisions - is kept up to date and sent with the last N "
                "messages. Older messages are still dropped; what they "
                "established is not.",
        "cost": "One small extra request per message you send, on a prompt that "
                "does not grow with the conversation.",
    },
    SUMMARY: {
        "label": "Rolling summary",
        "hint": "Everything outside the window is compressed into one paragraph, "
                "written once when the chat overflows and re-used until it "
                "overflows again.",
        "cost": "One extra request per overflow, not per message.",
    },
}


def normalise(name: str | None) -> str:
    """A strategy id that certainly exists - anything unknown falls back."""
    return name if name in STRATEGIES else DEFAULT_STRATEGY


def from_settings(settings: dict | None) -> str:
    """The strategy a *stored* settings block asks for.

    Its own function because of the second line. Day 9 wrote a boolean,
    `context_compression`, and conversations saved under it are still on disk
    and still being reloaded; a chat that was summarising yesterday should
    still be summarising today rather than quietly reverting to the default
    and dropping the messages its summary was standing in for.
    """
    settings = settings or {}
    if settings.get("context_strategy") in STRATEGIES:
        return settings["context_strategy"]
    if settings.get("context_compression"):
        return SUMMARY
    return DEFAULT_STRATEGY


def split(history: list[dict], window: int) -> tuple[list[dict], list[dict]]:
    """`(what falls outside the window, what goes in it)`.

    The one line of arithmetic every strategy starts from. What the two halves
    are *for* is the strategy's business: `window` throws the first away,
    `facts` has already read it, `summary` compresses it - but they all cut in
    the same place, which is what makes the three comparable at all.
    """
    if window is None or window < 0:
        window = 0
    if window >= len(history):
        return [], list(history)
    cut = len(history) - window
    return history[:cut], history[cut:]
