"""Day 13: a task as a state machine - which stage it is in, which step of
that stage is next, and what is expected of whom.

Day 11 gave this app a task: a record with a title, a memory of its own, and
a status with two values in it - `open` and `closed`. That is enough to
answer "should this still be sent?" and nothing else. It cannot answer the
question a person actually has when they come back to a piece of work after
a fortnight, which is not *is this still alive* but **where was I**.

So the status is gone and a stage has taken its place:

    planning     what is being built, and what "done" will mean
    execution    building it
    validation   checking it against what planning said
    done         finished

Four values instead of two, and that on its own would be a label. Three
things make it a machine, and all three are in this file rather than in a
prompt:

**1. Not every stage can follow every other.** `TRANSITIONS` is the whole
rule, and it is deliberately not a straight line. Work goes backwards more
often than it goes forwards - validation fails and you are executing again,
or executing shows the plan was wrong and you are planning again - so those
edges exist. The ones that do not exist are the ones that would be a lie:
`planning -> done` says work happened that did not, `execution -> done` says
it was checked when nothing checked it. A model in a hurry to please will
propose both. `check` is where that is refused.

**2. A stage has steps, and some of them are required.** A stage with no
steps is a label again: "execution" tells the agent to execute, which it was
going to do anyway. `STEPS` is a checklist per stage - the ordinary best
practice for that kind of work, written down once - and the items marked
`required` hold the exit. You cannot leave planning for execution until
there is a goal and a plan, because the failure that produces is exactly the
one everybody has seen: an agent that starts building on the third sentence
of the conversation and is still building the wrong thing an hour later.

Going *backwards* is never held. Re-planning is what you do when the plan
was wrong, and a machine that made you tick boxes before admitting that
would be a machine people route around.

**3. Every attempt is written down, refusals included.** `Task.log` is not
an audit trail for its own sake. A refused move is the most informative
event this app produces - it is the moment where what the model wanted and
what the work actually supported came apart - and a state machine that threw
those away would leave nothing on screen but the outcome it forced.

**The model is never given the transition table.** That is the design
decision worth defending, because the opposite is so tempting: list the legal
moves in the prompt and the model will mostly obey them. Mostly is the
problem. A rule that lives in a prompt is a request, is followed at whatever
rate the model follows requests, and cannot be tested. So the division of
labour is the other one:

    the model reports where the work now is
    the code decides whether it can get there from here

`PhaseAdvisor` asks one question - which stage does this conversation sound
like - and `check` answers the other. When the model says "done" three
messages into planning, that is not a bug to be prompted away: it is the
machine doing its job, and it shows up in the log as a refusal with a reason.

Like `memory.py` and `personality.py`, this file knows nothing about files,
HTTP or conversations. `render` and `check` take any object with `phase`,
`steps`, `paused` and `log`; `store.py` is what decides those live in JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import tokens
from facts import excerpt
from llm_client import LLMClient, LLMClientError

# --------------------------------------------------------------------------
# The four stages

PLANNING = "planning"
EXECUTION = "execution"
VALIDATION = "validation"
DONE = "done"

#: In order. The index in this tuple is what "forwards" means below, and it
#: is the only thing the order is used for - `TRANSITIONS` is the authority
#: on what may follow what, not this.
PHASES = (PLANNING, EXECUTION, VALIDATION, DONE)

DEFAULT_PHASE = PLANNING

#: What each stage is, sent to the frontend so the panel and this file cannot
#: drift apart - the same trick `memory.LAYER_INFO`, `personality.FIELD_INFO`
#: and `strategies.STRATEGY_INFO` play, and for the same reason.
#:
#: `doing` is the line the model is given and `hint` is the line the person
#: reads, because they are addressed differently: the prompt tells an agent
#: what this stage is for, and the panel tells a human what they are looking
#: at.
PHASE_INFO = {
    PLANNING: {
        "label": "Planning",
        "short": "Deciding what to build",
        "doing": "Work out what is being built and what “done” will mean. "
                 "Ask about anything that would change the plan. Do not start "
                 "building yet.",
        "hint": "Nothing is built here. What comes out of this stage is a goal, "
                "the constraints around it, and an agreed list of steps.",
    },
    EXECUTION: {
        "label": "Execution",
        "short": "Building it",
        "doing": "Carry out the agreed plan, one step at a time. If the plan "
                 "turns out to be wrong, say so rather than quietly building "
                 "something else.",
        "hint": "The plan is settled; this is the work. Decisions taken while "
                "working belong in the task's memory - that is what /task is for.",
    },
    VALIDATION: {
        "label": "Validation",
        "short": "Checking it against the plan",
        "doing": "Check what was built against the goal agreed in planning. "
                 "Look for what does not work, not for confirmation that it does.",
        "hint": "The stage that is skipped first and costs the most. It has its "
                "own name here so that skipping it has to be a decision.",
    },
    DONE: {
        "label": "Done",
        "short": "Finished",
        "doing": "This work is finished. Do not carry it further, and do not "
                 "start anything new under it.",
        "hint": "The end of the line. The task's memory stops being sent, and "
                "stays on file - reopening is one click and loses nothing.",
    },
}

# --------------------------------------------------------------------------
# The machine itself

#: Which stages may follow which. The whole rule, in one dict, and the only
#: place in the app that is allowed to have an opinion about it.
#:
#: Read the gaps rather than the entries. `planning -> validation` is missing
#: because there is nothing to validate; `planning -> done` and
#: `execution -> done` are missing because both are a claim that work was
#: finished without anything having checked it. Those three are the jumps a
#: model proposes when it is being agreeable, which is why they are refused
#: here rather than discouraged in a prompt.
#:
#: `done -> execution` is the one edge out of the end, and it is there because
#: work comes back. Calling it "reopened" and routing it through the same
#: `check` as every other move is what keeps it in the log instead of being a
#: quiet edit of a field.
TRANSITIONS = {
    PLANNING: (EXECUTION,),
    EXECUTION: (PLANNING, VALIDATION),
    VALIDATION: (PLANNING, EXECUTION, DONE),
    DONE: (EXECUTION,),
}


@dataclass(frozen=True)
class Step:
    """One item of a stage's checklist.

    `label` is what the panel shows and `expects` is what the model is told to
    do about it - the same split `PHASE_INFO` makes, for the same reason.
    `required` is the one that has teeth: it holds the exit forwards out of
    the stage it belongs to.
    """

    key: str
    label: str
    expects: str
    required: bool = False
    hint: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "expects": self.expects,
            "required": self.required,
            "hint": self.hint,
        }


#: The checklists. Ordinary practice for each kind of work, written down once
#: so that every task gets it rather than the ones whose first message
#: happened to ask for it.
#:
#: They are short on purpose. A twelve-item checklist is not followed, it is
#: dismissed, and the two or three items marked `required` are the ones worth
#: stopping a task over. Everything else is a reminder that shows on screen
#: and holds nothing up.
STEPS = {
    PLANNING: (
        Step("goal", "Goal and definition of done",
             "State what is being built and what will be true when it is finished.",
             required=True,
             hint="The one thing validation will later check against. Without it "
                  "that stage has nothing to do."),
        Step("constraints", "Constraints, risks, what is out of scope",
             "Name the constraints, the risks and what is deliberately not being done.",
             hint="Cheaper to write down now than to discover in execution."),
        Step("plan", "An agreed list of steps",
             "Lay out the steps, and get them agreed before building anything.",
             required=True,
             hint="Agreed is the operative word: a plan the user has not seen is "
                  "a guess with a table of contents."),
    ),
    EXECUTION: (
        Step("work", "Every planned step done, or dropped on purpose",
             "Work the plan step by step; nothing left silently unfinished.",
             required=True,
             hint="Dropped on purpose counts. Dropped quietly does not."),
        Step("record", "Decisions taken while working are filed",
             "File decisions made along the way with /task so the next "
             "conversation inherits them.",
             hint="This is the working memory layer earning its keep - what is "
                  "filed here is what the sixth chat about this task starts from."),
        Step("deviations", "Departures from the plan are called out",
             "Say plainly where the work departed from the plan, and why.",
             hint="A plan quietly abandoned makes validation check the wrong thing."),
    ),
    VALIDATION: (
        Step("criteria", "Checked against the definition of done",
             "Go back to the goal from planning and check the work against it, "
             "point by point.",
             required=True,
             hint="Against what planning wrote down - not against what the work "
                  "turned out to be."),
        Step("edges", "Failure and edge cases tried",
             "Try what is supposed to fail, not only what is supposed to work.",
             hint="The happy path passing is the weakest evidence available."),
        Step("verdict", "A verdict, out loud",
             "Say whether it passes, or list the gaps. Do not leave it implied.",
             required=True,
             hint="“Looks good” is not a verdict. Either it meets the goal or "
                  "here is what is missing."),
    ),
    DONE: (
        Step("summary", "What was delivered, in a paragraph",
             "Summarise what was built and what it does.",
             hint="The thing you will want in three months, when the task is a "
                  "row in a list."),
        Step("leftovers", "What was left out, and why",
             "Name what was deliberately not done, so it is not rediscovered as a bug.",
             hint="An empty list is an answer too - write it."),
    ),
}

#: How many log entries a task keeps. Long enough that the last few weeks of
#: a piece of work are readable; short enough that the file stays a file.
MAX_LOG = 60

#: What a log entry can be: an applied transition, and the pause switch. All
#: three are the same kind of thing - something that *happened* to the state of
#: this task, with a time and an author on it.
#:
#: There is deliberately no entry for a refused move. A transition the machine
#: would not make did not happen, and a log that carried it would be a history
#: of things that are not true. What stops an illegal move is `check`; nothing
#: needs to be written down for that to hold.
MOVE = "move"
PAUSE = "pause"
RESUME = "resume"

#: Who did it. `agent` means this came out of `PhaseAdvisor` and through
#: `check`; `you` means somebody clicked.
BY_AGENT = "agent"
BY_USER = "you"


# --------------------------------------------------------------------------
# Refusals

#: The codes `check` can refuse with. They are named rather than being bare
#: strings so the panel can treat "you cannot get there from here" (an edge
#: that does not exist, permanent) differently from "not yet" (a required step
#: that is not ticked, and might be in a minute).
UNKNOWN = "unknown"
SAME = "same"
ILLEGAL = "illegal"
PAUSED = "paused"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class Decision:
    """The answer to "may this task move there", and why.

    A boolean would do for the machine and would be useless everywhere else.
    The reason is what the panel puts in a disabled button's tooltip, what the
    log stores against a refusal, and what makes a refused move readable weeks
    later - so it is part of the answer rather than something the caller
    reconstructs.
    """

    ok: bool
    code: str = ""
    reason: str = ""

    def __bool__(self) -> bool:
        """`if check(task, target):` reads the way it should.

        Note the trap that comes with it, since this class is also passed
        around and stored: a refusal is a perfectly good `Decision` and a
        falsy object, so anything asking *whether there is a decision* has to
        say `is not None`. Writing `if self.decision` there silently means
        "if the decision was yes".
        """
        return self.ok


def phase_of(state) -> str:
    """The stage `state` is in, with anything unrecognised read as planning.

    Unrecognised includes missing, which is what every task written before
    today looks like. Planning is the safe reading of "I do not know where
    this is": it is the only stage from which nothing has been claimed.
    """
    phase = getattr(state, "phase", None)
    return phase if phase in PHASE_INFO else DEFAULT_PHASE


def done_steps(state, phase: str | None = None) -> list[str]:
    """The keys ticked in one stage of `state`, dropping anything unknown."""
    phase = phase or phase_of(state)
    raw = getattr(state, "steps", None) or {}
    ticked = raw.get(phase) if isinstance(raw, dict) else None
    keys = {step.key for step in STEPS.get(phase, ())}
    return [key for key in (ticked or []) if key in keys]


def missing_required(state, phase: str | None = None) -> list[Step]:
    """The required steps of the current stage that are not ticked yet."""
    phase = phase or phase_of(state)
    ticked = set(done_steps(state, phase))
    return [s for s in STEPS.get(phase, ()) if s.required and s.key not in ticked]


def is_forward(source: str, target: str) -> bool:
    """Whether `target` is further along than `source`.

    The one use of the order in `PHASES`, and it decides whether the required
    steps are checked at all. Forwards claims progress and has to earn it;
    backwards admits the previous stage was not finished, which is the
    admission the checklist was trying to force anyway.
    """
    try:
        return PHASES.index(target) > PHASES.index(source)
    except ValueError:
        return False


def check(state, target: str) -> Decision:
    """May this task move to `target`? The whole of the machine's authority.

    Five rules, in the order they are cheapest to state:

    1. the target has to be a stage,
    2. it has to be a different one,
    3. a paused task does not move at all - that is what pausing is,
    4. there has to be an edge from here to there in `TRANSITIONS`,
    5. and going forwards, the required steps of the current stage have to
       be ticked.

    Nothing else may refuse a transition, and nothing else may allow one. The
    server calls this for a click, the chat endpoint calls it for whatever the
    model proposed, and both get the same answer - which is the only way the
    rule is a rule rather than two implementations that agree for now.
    """
    source = phase_of(state)
    if target not in PHASE_INFO:
        return Decision(False, UNKNOWN, f"“{target}” is not a stage.")
    if target == source:
        return Decision(False, SAME, f"Already in {PHASE_INFO[source]['label'].lower()}.")
    if getattr(state, "paused", False):
        return Decision(
            False, PAUSED,
            "The task is paused. Resume it before moving it on.",
        )
    if target not in TRANSITIONS.get(source, ()):
        return Decision(
            False, ILLEGAL,
            f"{PHASE_INFO[source]['label']} cannot go straight to "
            f"{PHASE_INFO[target]['label'].lower()}.",
        )
    if is_forward(source, target):
        missing = missing_required(state, source)
        if missing:
            names = ", ".join(f"“{step.label}”" for step in missing)
            return Decision(
                False, INCOMPLETE,
                f"Not yet: {names} still to do in "
                f"{PHASE_INFO[source]['label'].lower()}.",
            )
    return Decision(True)


def allowed(state) -> dict:
    """Every stage, with the machine's verdict on moving there from here.

    All four rather than the ones that pass, because a panel that only draws
    the legal moves teaches nobody what the machine is. Drawn greyed out with
    the reason on them, the illegal ones are the day's whole argument sitting
    on screen: the jump from planning to done is not missing, it is refused,
    and here is why.
    """
    return {phase: check(state, phase) for phase in PHASES}


# --------------------------------------------------------------------------
# What the state looks like, in words

#: How long a task can go untouched before the prompt says so out loud. Under
#: a day, "last touched" is noise - the model is being told about a
#: conversation it is already holding. Over one, it is the single most useful
#: thing in the block: it is the difference between carrying on and coming
#: back.
IDLE_HOURS = 20


def parse_time(value) -> datetime | None:
    """An ISO timestamp off disk, or None if it is not one.

    Never raises. A task with a corrupt date is a task whose idle time is
    unknown, not a request that fails.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def idle_for(state, now: datetime | None = None) -> float | None:
    """Hours since this task was last touched, or None if unknown."""
    touched = parse_time(getattr(state, "updated_at", None))
    if touched is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - touched).total_seconds() / 3600.0)


def idle_phrase(hours: float | None) -> str:
    """“6 days ago”. Rounded hard, because precision here is false.

    Three buckets and nothing finer: whether the gap was fifty or seventy
    hours changes nothing about how the agent should behave, and a sentence
    that reads like a measurement invites the model to reason about it.
    """
    if hours is None:
        return ""
    if hours < 1:
        return "just now"
    if hours < 48:
        return f"{int(hours)} hour{'s' if int(hours) != 1 else ''} ago"
    return f"{int(hours // 24)} days ago"


def expected_action(state) -> str:
    """The one sentence that answers “so what happens next”.

    The third of the day's three fields, and the only one that is derived
    rather than stored - because storing it is how it goes stale. It is read
    off the checklist every time it is asked for, so it cannot disagree with
    the boxes drawn next to it.

    Order matters here. Paused outranks everything, including a finished
    stage, because a paused task's next action is not the next step of the
    work - it is a person coming back.
    """
    phase = phase_of(state)
    note = (getattr(state, "note", "") or "").strip()
    if getattr(state, "paused", False):
        return "Paused" + (f" - {note}" if note else ". Waiting to be resumed.")
    missing = missing_required(state, phase)
    if missing:
        return missing[0].expects

    # Nothing required is outstanding, so the stage can be left. Whether it
    # *should* be is the other half of the sentence: an optional step still
    # unticked is worth doing and is not worth waiting for, and saying both
    # in one line is the only honest way to put that.
    onward = [p for p in TRANSITIONS.get(phase, ()) if is_forward(phase, p)]
    move_on = (
        f"move on to {PHASE_INFO[onward[0]]['label'].lower()}" if onward else ""
    )
    unticked = [s for s in STEPS.get(phase, ()) if s.key not in set(done_steps(state, phase))]
    if unticked:
        return unticked[0].expects + (f" Or {move_on}." if move_on else "")
    if not move_on:
        return "Nothing. This work is finished."
    return "Everything in this stage is done - " + move_on + "."


def checklist(state, phase: str | None = None, keys: bool = False) -> list[str]:
    """The current stage's steps as `[x] label` lines.

    `keys` puts the step key in front of each one. On for the advisor, which
    has to name them back; off for the answering agent, which does not and
    would only be given a vocabulary to hallucinate with.
    """
    phase = phase or phase_of(state)
    ticked = set(done_steps(state, phase))
    lines = []
    for step in STEPS.get(phase, ()):
        mark = "x" if step.key in ticked else " "
        name = f"{step.key} - {step.label}" if keys else step.label
        lines.append(f"  [{mark}] {name}" + (" (required)" if step.required else ""))
    return lines


def recent(state, limit: int = 3) -> list[str]:
    """The last few things that *happened* to this task, one line each.

    Only moves that were actually made, because only those are recorded -
    see `MOVE` above. This list goes into a prompt whose whole job is "here is
    where to carry on from", and it would be the wrong place for attempts in
    any case: "planning → done refused" reads to a model as evidence that
    somebody wanted this finished.
    """
    log = [
        entry for entry in (getattr(state, "log", None) or [])
        if isinstance(entry, dict)
    ]
    out = []
    for entry in log[-limit:]:
        when = (str(entry.get("at") or "")[:10]) or "?"
        by = entry.get("by") or BY_USER
        kind = entry.get("kind") or MOVE
        if kind in (PAUSE, RESUME):
            out.append(f"{when}: {kind}d ({by})")
            continue
        arrow = f"{entry.get('from') or '?'} → {entry.get('to') or '?'}"
        why = (entry.get("why") or "").strip()
        tail = f" – {why}" if why else ""
        out.append(f"{when}: {arrow} ({by}){tail}")
    return out


def render(state) -> str:
    """The state block, as the model is given it.

    Everything a person would want on coming back to this after a fortnight,
    in the order they would want it: where the work is, how long it has been
    sitting there, what is ticked, what is next.

    The last paragraph is the day's second requirement and it is one
    instruction: **carry on, do not start again.** Without it the failure is
    reliable and familiar - an agent handed a task summary re-summarises it,
    re-proposes the plan that was agreed a week ago and asks the questions
    planning already answered, because restating context is what a model does
    when it is handed context and nothing to do with it.

    Nothing here says which stages this one may move to. The model is being
    asked what it sees, not what it is permitted to do, and the permission
    lives in `check` where it can be tested.
    """
    phase = phase_of(state)
    info = PHASE_INFO[phase]
    title = (getattr(state, "title", "") or "").strip()
    paused = bool(getattr(state, "paused", False))

    head = f"Stage {PHASES.index(phase) + 1} of {len(PHASES)}: {info['label'].upper()}"
    if paused:
        head += " - PAUSED"
    lines = [f"The task{f' - “{title}”' if title else ''}", head, ""]

    idle = idle_for(state)
    if idle is not None and idle >= IDLE_HOURS:
        lines.append(f"Last worked on: {idle_phrase(idle)}.")
        lines.append("")

    lines.append(info["doing"])
    lines.append("")
    steps = checklist(state, phase)
    if steps:
        lines.append("Steps of this stage:")
        lines.extend(steps)
        lines.append("")
    note = (getattr(state, "note", "") or "").strip()
    if note and not paused:
        lines.append("Waiting on: " + note)
    lines.append("Expected next action: " + expected_action(state))

    history = recent(state)
    if history:
        lines.append("")
        lines.append("How it got here:")
        lines.extend(f"  {line}" for line in history)

    lines.append("")
    if paused:
        lines.append(
            "This task is on hold. Answer what is asked, but do not carry the "
            "work forward, do not start the next step and do not re-plan until "
            "it is resumed."
        )
        return "\n".join(lines)

    lines.append(
        "This is where the work already is. Carry on from it: do not re-plan "
        "what earlier stages settled, do not restate the task back to the "
        "user, and do not repeat explanations they have already had."
    )
    lines.append("")
    lines.append(
        "Stay in this stage. Do not do the work of a later one - not a draft "
        "of it, not \"just the outline\", not \"here it is anyway\" - even if "
        "you are asked to, and especially if you are asked to. Being asked to "
        "skip ahead is the ordinary way this goes wrong: agreeing produces "
        "work nobody planned and nobody checked, and it is the failure this "
        "task is tracked against."
    )
    onward = TRANSITIONS.get(phase, ())
    if onward:
        names = ", ".join(PHASE_INFO[p]["label"].lower() for p in onward)
        lines.append(
            f"From {info['label'].lower()} the work can only go to: {names}. "
            "Nothing else is reachable from here."
        )
    missing = missing_required(state, phase)
    if missing:
        outstanding = "; ".join(f"\"{step.label}\"" for step in missing)
        lines.append(
            f"Still outstanding in this stage: {outstanding}. If you are asked "
            "to move on before that is settled, say what is left and settle it."
        )
    lines.append(
        "When this stage's work really is finished - or the user says it is - "
        "say so plainly in one line and carry on answering. Do not say you "
        "are unable to change the stage, do not ask to be switched over and "
        "do not wait for permission: the app reads your answer, works out "
        "where the work stands, and offers the user the move. Refusing to "
        "proceed is not caution here, it is a dead end."
    )
    lines.append("")
    lines.append(
        "Do not narrate any of this. The stage, the checklist and what is "
        "expected next are on the user's screen already; repeating them back, "
        "or announcing that you have ticked something off, is noise. Just do "
        "the work this stage calls for."
    )
    return "\n".join(lines)


def correction(state, attempted: str, decision: Decision) -> str:
    """What to tell the model when its answer belonged to a stage it is not in.

    The block that makes the machine bind the *conversation* rather than only
    the record. Refusing the transition keeps the file honest and does nothing
    at all about the answer that earned the refusal - the user asked for the
    work to be called finished, the model obliged, and a stage field quietly
    saying `planning` underneath is not a correction anybody sees.

    So the answer is thrown away and this is sent with the second attempt. It
    says three things, and the third is what stops the retry being an argument:
    what the last answer did, why that is not where the work is, and what to
    do instead.
    """
    label = PHASE_INFO[phase_of(state)]["label"]
    lines = [
        "--- Your last answer was rejected before it was shown ---",
        "",
        f"It did the work of {PHASE_INFO.get(attempted, {}).get('label', attempted).lower()}. "
        f"This task is in {label.lower()}, and: {decision.reason}",
        "",
        "That decision was made by code and is not open to discussion. The "
        "user has not seen the rejected answer, so do not refer to it, do not "
        "apologise for it and do not offer it again in a shorter form.",
        "",
        f"Answer the user's message again, doing only what {label.lower()} "
        "calls for.",
    ]
    missing = missing_required(state, phase_of(state))
    if missing:
        outstanding = "; ".join(f'"{step.label}"' for step in missing)
        lines.append(
            f"What this stage still needs: {outstanding}. Move that forward, "
            "and if the user wants to skip ahead say plainly what is left "
            "before the work can."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The agent's half: where does this look like it is now?

ADVICE_TEMPERATURE = 0.0
ADVICE_MAX_TOKENS = 220

#: The stage names, and nothing about how they connect. See the module
#: docstring: the model reports, the code decides. Handing it `TRANSITIONS`
#: here would move the rule into a prompt, where it would be followed at
#: whatever rate prompts are followed and could not be tested at all.
ADVICE_SYSTEM_PROMPT = (
    "You are watching one piece of work and reporting where it stands. You are "
    "not doing the work and you are not talking to the user.\n\n"
    "Work goes through four stages:\n"
    "  planning   - deciding what to build and what done will mean\n"
    "  execution  - building it\n"
    "  validation - checking it against what planning agreed\n"
    "  done       - finished and checked\n\n"
    "You are given the stage the work is recorded in, that stage's checklist, "
    "and the last exchange. Reply with JSON and nothing else:\n\n"
    '{"stage": "execution", "steps": ["work"], "why": "eight words", '
    '"note": ""}\n\n'
    "  stage  - where the work stands after this exchange. Repeat the current "
    "one unless it has genuinely moved on. If this stage's work is finished - "
    "its checklist met, or the user saying it is settled - name the stage the "
    "work is ready for, even when the exchange itself was only an agreement.\n"
    "  steps  - keys from the checklist that are satisfied *now*. Not only "
    "what this exchange produced: an item the user has agreed to, or that the "
    "conversation has plainly settled, is satisfied. Keys already marked [x] "
    "may be repeated or left out. Only keys you were given.\n"
    "  why    - one short clause, only if the stage changed.\n"
    "  note   - what the work is waiting on, if it is waiting on something. "
    "Otherwise \"\".\n\n"
    "Two mistakes, and they are opposite. Saying work is finished when nobody "
    "did it or checked it is the worse one. But an exchange where the user "
    "agreed the plan, or said \"go ahead\", has settled something - reporting "
    "nothing there leaves the work stuck in a stage it has outgrown, which is "
    "the other way this fails."
)


@dataclass
class Advice:
    """What the watcher reported: a stage, some ticked steps, a reason."""

    stage: str = ""
    steps: list = field(default_factory=list)
    why: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {"stage": self.stage, "steps": self.steps, "why": self.why, "note": self.note}


@dataclass
class AdviceRun:
    """One watcher request - what it reported, what the machine did about it,
    and what it cost.

    The same shape as `memory.ProposalRun`, so the debug panel draws it with
    the code it already has. `decision` and `moved` are filled in by the
    caller *after* `check` has had its say - the run records what the model
    reported and what happened to that report, because either half on its own
    is unreadable: "the agent said done" and "the task is in execution" only
    mean something together.
    """

    advice: Advice | None = None
    decision: Decision | None = None
    moved: bool = False
    #: Whether the answer this run judged was thrown away and asked for again.
    #: Set by the caller, like `decision` - this class records what happened to
    #: what it reported, and being re-asked is the most consequential of those.
    retried: bool = False
    #: Whether what it reported became a suggestion waiting for a person.
    suggested: bool = False
    ticked: list = field(default_factory=list)
    error: str | None = None
    request: dict | None = None
    response: dict | None = None
    usage: dict | None = None
    elapsed_ms: float | None = None
    url: str | None = None
    status_code: int | None = None
    request_headers: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def rejected(self) -> bool:
        """Whether the answer this run read should be thrown away and re-asked.

        Only for a stage that cannot be reached at all. A stage that is
        reachable once somebody confirms the checklist is not a jump - it is
        the ordinary end of a stage, and it becomes a suggestion instead.
        Re-asking there would throw away a perfectly good answer for agreeing
        with the user.
        """
        return (
            self.decision is not None
            and not self.decision.ok
            and self.decision.code in (ILLEGAL, UNKNOWN)
        )

    def describe(self) -> str:
        if self.error:
            return "failed"
        if self.retried and self.decision is not None:
            return f"answer re-asked - {self.decision.reason}"
        if self.suggested and self.advice:
            return f"suggested moving to {self.advice.stage}"
        if self.decision is not None and not self.decision.ok and self.decision.code != SAME:
            return f"held - {self.decision.reason}"
        if self.ticked:
            return f"{len(self.ticked)} step{'s' if len(self.ticked) != 1 else ''} ticked"
        return "no change"


def parse_advice(payload: dict, phase: str) -> tuple[Advice | None, str | None]:
    """`{"stage": ..., "steps": [...]}` out of a completion.

    Forgiving about shape in the same one direction `memory.parse_proposals`
    is, and unforgiving about content: a stage that is not one of the four is
    dropped rather than guessed at, and a step key that does not belong to
    `phase` is dropped rather than filed. The model is reporting on a
    checklist it was shown - a key it invented is not an observation about the
    work, it is a token that scored well.
    """
    choices = (payload or {}).get("choices") or []
    if not choices:
        return None, "the watcher returned no choices"
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        return None, "the watcher returned nothing"

    if content.startswith("```"):
        content = content.strip("`")
        content = content.split("\n", 1)[-1] if content.lower().startswith("json") else content

    try:
        data = json.loads(content)
    except ValueError:
        return None, "the watcher did not return JSON"
    if not isinstance(data, dict):
        return None, "the watcher did not return an object"

    stage = str(data.get("stage") or "").strip().lower()
    known = {step.key for step in STEPS.get(phase, ())}
    raw_steps = data.get("steps")
    steps = [
        key for key in (raw_steps if isinstance(raw_steps, list) else [])
        if isinstance(key, str) and key.strip().lower() in known
    ]
    return (
        Advice(
            stage=stage if stage in PHASE_INFO else "",
            steps=[key.strip().lower() for key in steps],
            why=re.sub(r"\s+", " ", str(data.get("why") or "")).strip()[:80],
            note=re.sub(r"\s+", " ", str(data.get("note") or "")).strip()[:120],
        ),
        None,
    )


class PhaseAdvisor:
    """Reads one turn and says which stage the work is now in.

    Built like `memory.MemoryProposer` - client handed in, prompt a constant
    size whatever the conversation has grown to, answer parsed rather than
    read - and it differs in the one way that matters: what it returns is not
    a suggestion waiting for somebody to accept it. It goes straight to
    `check`, and `check` is what stands between a model's opinion and the
    file on disk.

    That is a different answer from the one day 11 gave, and deliberately.
    A proposed memory that nobody reviews is a fact nobody said; a proposed
    transition is checked by a machine that knows the rules, so the review
    it needs has already happened by the time a person could do it. And when
    the answer it read belonged to a stage this task cannot reach, the caller
    throws that answer away and asks again - so what a person is left with is
    a task that is where the work actually is, and an agent that answered
    inside the stage it was in.
    """

    def __init__(self, client: LLMClient, model: str) -> None:
        self.client = client
        self.model = model

    def build_request(self, state, message: str, answer: str) -> dict:
        phase = phase_of(state)
        title = (getattr(state, "title", "") or "").strip()
        parts = [
            (f"The work: “{title}”\n" if title else "")
            + f"Recorded stage: {phase}\n"
            + "Checklist for that stage:\n"
            + "\n".join(checklist(state, phase, keys=True)),
            "The user said:\n\n" + excerpt(message),
        ]
        if answer:
            parts.append("You answered:\n\n" + excerpt(answer))
        parts.append("Reply with the JSON now, and nothing else.")

        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": ADVICE_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(parts)},
            ],
            "temperature": ADVICE_TEMPERATURE,
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
            "max_tokens": ADVICE_MAX_TOKENS,
        }

    def advise(self, state, message: str, answer: str) -> AdviceRun:
        """One request: a turn in, a stage out. Never raises.

        A failed run leaves the task exactly where it was, which is the right
        failure: the state machine standing still is always a defensible
        state, and it is the one a person can fix with one click.
        """
        request = self.build_request(state, message, answer)
        try:
            result = self.client.complete(request)
        except LLMClientError as exc:
            return AdviceRun(
                error=f"Error: {exc}",
                request=request,
                url=exc.url,
                status_code=exc.status_code,
                request_headers=self.client.redacted_headers,
            )

        advice, error = parse_advice(result.response, phase_of(state))
        return AdviceRun(
            advice=advice,
            error=error,
            request=result.request,
            response=result.response,
            usage=tokens.usage_from_response(result.response),
            elapsed_ms=result.elapsed_ms,
            url=result.url,
            status_code=result.status_code,
            request_headers=result.request_headers,
        )
