"""One chat turn, end to end: the context, the agent, the checks, the store.

    run_turn(rt, message, conversation_id, settings)
        -> context.prepare_context     what the request remembers (may buy a summary or facts)
        -> RAG: task state, retrieval, rerank, the gate          (a chat switched to RAG)
           or, for `/help`, the project's excerpts and its tools (`core.help`, day 31)
        -> build_agent(...).ask(message)                         the answer, with tools
        -> grounding retry, stage advisor, invariant check       each may ask once more
        -> store.append_turn, then the memory proposer
        -> TurnResult                                            `reports.turn_report` is its JSON

Moved here from the web handler unchanged in what it does, so a turn can be
run from plain Python - a script, a test, a CI job - with nothing but an
`AgentRuntime`: `rt.chat("...").answer`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from core import facts as facts_module
from core import help as help_module
from core import invariants as invariants_module
from core import memory as memory_module
from core import personality as personality_module
from core import phases as phases_module
from core import task_state as task_state_module
from core.agent import Agent, AgentConfig, AgentReply
from core.context import ContextPlan, plan_task_state, prepare_context, stored_task_state
from core.errors import Invalid, ServiceError, UpstreamFailed
from core.invariants import CheckRun, InvariantWatcher
from core.memory import MemoryProposer, ProposalRun
from core.phases import AdviceRun, PhaseAdvisor
from core.services.conversations import valid_id
from core.settings import ChatSettings, as_dict, validate_settings
from llm.client import LLMClientError
from rag import augment as augment_module
from rag import grounding as grounding_module
from rag import rerank as rerank_module
from storage import SINGLE_CONVERSATION_ID, Conversation, Invariants, Personality, Profile
from tools.registry import ToolRegistry

if TYPE_CHECKING:
    from core.runtime import AgentRuntime


@dataclass
class TurnResult:
    """Everything one turn produced - the reply, the stored conversation, and what it bought.

    `answer` is the text a person reads; the rest is what the page's readouts
    and debug panel are drawn from (`reports.turn_report`).
    """

    reply: AgentReply
    conversation: Conversation
    plan: ContextPlan
    proposals: ProposalRun | None = None
    personality: Personality | None = None
    advice: AdviceRun | None = None
    invariant_record: Invariants | None = None
    check: CheckRun | None = None
    sources: dict | None = None

    @property
    def answer(self) -> str:
        return self.reply.answer


def build_agent(
    rt: "AgentRuntime",
    settings: ChatSettings,
    plan: ContextPlan,
    profile: Profile | None = None,
    correction: str | None = None,
    invariant_record: Invariants | None = None,
    invariant_correction: str | None = None,
    tools: ToolRegistry | None = None,
    knowledge: str | None = None,
    knowledge_template: str | None = None,
    json_answer: bool | None = None,
) -> Agent:
    """One short-lived agent, configured and handed the context to run on.

    Day 7's three lines, minus the third - except that what it is handed is no
    longer simply "the transcript so far". It is whatever `prepare_context`
    decided the transcript should look like from in here: cut to the window,
    with at most one block standing in for the rest.
    """
    return Agent(
        rt.llm,
        AgentConfig(
            model=settings.model,
            system_prompt=settings.system_prompt,
            stop=settings.stop or None,
            # Day 24: a grounded answer is asked for as JSON - answer,
            # sources, quotes - and JSON mode is what makes it come back
            # parseable, whatever the chat's own format setting says. Day 31:
            # /help's excerpts come with a template too, and its answer is prose.
            response_format=("json_object" if (bool(knowledge_template) if json_answer is None else json_answer)
                             else settings.response_format),
            reasoning_effort=settings.reasoning_effort,
            temperature=settings.temperature,
        ),
        history=plan.history,
        context_messages=settings.context_messages,
        summary=plan.summary,
        # Rendered here rather than in the agent: `key: value` lines are this
        # app's idea of what a fact block looks like in a prompt, and the agent
        # is deliberately given text it does not have to interpret.
        facts=facts_module.render(plan.facts),
        # The same arrangement for day 11's two, down to the rendering
        # happening here. `memory.render` produces the same `key: value`
        # shape `facts.render` does, so all three blocks read alike in the
        # prompt and the agent cannot tell which layer it was handed.
        long_term=memory_module.render(plan.long_sent),
        working=memory_module.render(plan.working_sent),
        task_title=plan.task.title if plan.task else None,
        # Day 13, and rendered here for the same reason the four above are:
        # what a stage, a checklist and an expected action look like in a
        # prompt is this app's idea, not the agent's. A task in `done` sends
        # no state block and no working layer - it is finished, and a finished
        # task's stage is no more current than its decisions are.
        state=(
            phases_module.render(plan.task) + ("\n\n" + correction if correction else "")
            if plan.task is not None and plan.task.is_active
            else None
        ),
        # Day 12, and the one argument here that did not come out of
        # `prepare_context`. It could not have: `ContextPlan` is the answer to
        # "what of the past goes into this request", and a profile is not part
        # of the past. It is not in `settings` either, for the reason the
        # module docstring gives - it is one object for the whole app, not a
        # property of this chat - so it arrives as its own parameter, from the
        # only place that knows which profile is currently switched on.
        personality=personality_module.render(profile),
        # Day 14, arriving as its own parameter for exactly the reason the
        # profile above does: `ContextPlan` answers "what of the past goes
        # into this request", and a rule the work is committed to is not part
        # of the past. The correction is appended the way day 13's is - the
        # block says what the rules are, and the paragraph after it says which
        # one the last attempt broke.
        invariants=(
            invariants_module.render(invariant_record)
            + ("\n\n" + invariant_correction if invariant_correction else "")
            if invariant_record is not None
            else None
        ),
        # Day 17, and the first argument here that is not a block of prose.
        # Everything above tells the agent what it knows; this tells it what
        # it can *do* - a registry it lists the tools of and calls them
        # through, so the agent never learns that MCP was involved. Its guide
        # (day 20: which server is for what) rides along as one more block.
        tools=tools,
        # Day 22: the excerpts retrieved for this question, numbered. Joined
        # onto the question by the agent, not sent as a block of their own.
        knowledge=knowledge,
        # Day 24: and what they are joined with - `rag.grounding`'s template,
        # which asks for the answer with its sources and quotes, or the one
        # that forbids an answer when nothing passed the threshold.
        knowledge_template=knowledge_template,
        # Day 25: what this RAG conversation is for, and what it has settled.
        task_state=task_state_module.render(plan.task_state) if plan.task_state else None,
    )


def rerank(rt: "AgentRuntime", question: str, candidates: augment_module.Retrieval, model: str,
           threshold: float, k: int) -> augment_module.Retrieval:
    """Day 23: the second stage over the app's LLM client - its failure a refusal.

    An unread judgement is not a filter: a judge that cannot be asked, or
    whose reply cannot be read, refuses the turn rather than letting the
    unfiltered candidates through as if they had been read.
    """
    def complete(body: dict) -> dict:
        try:
            return rt.llm.complete(body).response
        except LLMClientError as exc:
            raise rerank_module.RerankError(f"The reranker's request failed: {exc}") from exc

    try:
        judgement = rerank_module.LLMReranker(complete, model).judge(question, candidates.hits)
    except rerank_module.RerankError as exc:
        raise UpstreamFailed(str(exc)) from exc
    return rerank_module.keep(candidates, judgement, threshold, k)


def help_retrieval(rt: "AgentRuntime", question: str, k: int
                   ) -> tuple[augment_module.Retrieval | None, str | None]:
    """Day 31: the project's excerpts for a /help question - or None, and why there are none.

    Unlike a RAG chat's, a failed search does not refuse the turn: /help has
    the project's tools to fall back on, and the system prompt says the
    documentation was not searched.
    """
    if rt.index.built() is None:
        return None, "this project's index is not built yet"
    try:
        return rt.index.retrieve(question, k), None
    except ServiceError as exc:
        return None, exc.detail


def read_grounded(reply: AgentReply, retrieval: augment_module.Retrieval | None,
                  gate: dict | None) -> dict | None:
    """Day 24: a RAG reply's JSON read and its quotes looked up - `reply.answer` becomes the text.

    The JSON is the request's business, not the transcript's: what is shown,
    stored and replayed into the next turn is the answer itself (and, for "I
    do not know", the question back), while the sources and quotes travel on
    the message's `rag` block. Run on every reply a RAG turn produces - the
    first and each retry - so the advisor and the invariant check read the
    answer a person will read, not the JSON around it.
    """
    if retrieval is None or reply.error is not None:
        return None
    grounded = grounding_module.parse(reply.answer)
    report = grounding_module.check(grounded, retrieval, gate)
    reply.answer = grounded.text or reply.answer
    return report


def run_turn(
    rt: "AgentRuntime",
    message: str,
    conversation_id: str = SINGLE_CONVERSATION_ID,
    settings: ChatSettings | None = None,
) -> TurnResult:
    """One message in, one answer out - stored, with everything it bought on the way.

    The whole of a chat turn, and the only place where "an agent" and "a
    saved transcript" are introduced to each other:

        conversation = store.load(id)
        agent = Agent(client, config, history=conversation.history())
        store.append_turn(id, message, reply.answer)

    Each turn is still served by a brand-new short-lived `Agent`. Memory did
    not come from keeping objects alive; it came from writing the transcript
    down and handing it back. Everything else here sits between those three
    lines: the context plan (window, facts or summary, the layers), the RAG
    retrieval, and the checks an answer is held to before it is stored.
    """
    message = (message or "").strip()
    if not message:
        raise Invalid("Message must not be empty")
    settings = validate_settings(settings if settings is not None else ChatSettings())
    conversation_id = valid_id(conversation_id)
    store = rt.stores.conversations

    # The three day-7 lines. Everything the agent knows about the past comes
    # from `history()`; everything the next process will know comes from
    # `append_turn`.
    saved = store.load(conversation_id)
    # Days 9 and 10 sit between the two: what the agent is handed is no longer
    # "everything", it is whatever the window and the chosen strategy come to.
    # This is also where an extraction or a summarisation request may be made
    # and stored, before the question below is asked.
    plan = prepare_context(rt, saved, settings, conversation_id, message)
    # Day 12. Read here rather than inside `build_agent` so that the reply can
    # report the same record the request was built from: a profile switched on
    # between the two reads would otherwise make the screen claim an answer was
    # produced under a personality it never saw.
    profile_record = rt.stores.personalities.load()
    # Day 14, read here for exactly the reason the profile above is: so the
    # reply reports the same rules the request was built from. A rule switched
    # off between the two reads would otherwise put an answer on screen under
    # a set of invariants it was never held to.
    invariant_record = rt.stores.invariants.load()
    # Day 17, read here for the same reason as the two above: the tools this
    # answer was offered are reported back with it, and a server switched off
    # mid-turn should not make the screen claim a tool the request never
    # carried. Nothing is fetched - this is the stored catalogue.
    tools = rt.tool_registry()
    # Day 22: retrieval, once per turn and before anything is asked. Both
    # retries below reuse it - they are new answers to the same question, and
    # the same question has the same nearest chunks. A missing index or a
    # silent Ollama refuses the turn rather than quietly answering without
    # the documents: in a chat switched to RAG, an answer that did not read
    # them would be presented as one that did.
    #
    # Day 23: with the second stage on, the search returns `rag_candidates`
    # and the judge decides which of them go up - before the answer, since
    # the answer is asked with what it kept. A judge that fails refuses the
    # turn for the same reason a silent Ollama does.
    #
    # Day 24: the answer comes back with its evidence - sources and quotes, as
    # JSON (`rag/grounding.py`). And when the judge kept nothing - no
    # candidate reached the threshold - the gate trips: the question goes up
    # with the titles of the nearest sections instead of excerpts, and an
    # instruction not to answer but to say "I do not know" and ask what was
    # meant. Only the reranked mode has a relevance to fall below; day 22's
    # top k sends the nearest chunks whatever they are.
    #
    # Day 25: the task state moves first, and the search is asked with it -
    # the question as typed, then the goal, the clarifications and the terms
    # (`task_state.search_query`). The judge reads the same string, so "and
    # why that number?" is graded as the follow-up it is.
    #
    # Day 31: `/help` swaps three of those for the project's (`core.help`):
    # the system prompt, the excerpts - searched in the project's index with
    # or without RAG on, and never a refusal - and the tools, which are the
    # project's git and file servers instead of the panel's. The answer is
    # prose citing excerpts by number, not day 24's JSON, so nothing below
    # reads it as grounded.
    retrieval = None
    gate = None
    query = message
    agent_settings = settings
    asked = message
    knowledge = template = None
    help_request = help_module.parse(message)
    if help_request is not None:
        asked = help_request.asked
        tools = rt.project.tools()
        retrieval, problem = help_retrieval(rt, asked, settings.rag_k)
        agent_settings = replace(settings, system_prompt=help_module.system_prompt(rt.project.current(), problem))
        if retrieval is not None:
            knowledge, template = augment_module.render_excerpts(retrieval), help_module.TEMPLATE
    else:
        if settings.rag:
            plan = plan_task_state(rt, plan, saved, settings, message)
            query = task_state_module.search_query(message, plan.task_state)
        if settings.rag and settings.rag_rerank:
            candidates = rt.index.retrieve(query, settings.rag_candidates)
            retrieval = rerank(rt, query, candidates, settings.model, settings.rag_threshold, settings.rag_k)
            gate = grounding_module.gate(retrieval)
        elif settings.rag:
            retrieval = rt.index.retrieve(query, settings.rag_k)
        if retrieval is not None:
            knowledge = grounding_module.render_gate(gate) if gate else augment_module.render_excerpts(retrieval)
            template = grounding_module.UNKNOWN_TEMPLATE if gate else grounding_module.TEMPLATE
    grounding_on = retrieval if help_request is None else None
    json_answer = help_request is None and template is not None
    agent = build_agent(rt, agent_settings, plan, profile_record.current(),
                        invariant_record=invariant_record, tools=tools,
                        knowledge=knowledge, knowledge_template=template, json_answer=json_answer)

    reply = agent.ask(asked)
    grounded = read_grounded(reply, grounding_on, gate)

    # Day 24, and before the stage and the rules are checked, since those
    # read the answer this produces: an answer without its evidence - no
    # source, no quote, a quote that is not in its excerpt - is asked for
    # again, once, with what was wrong. Day 14's rule, one retry and never
    # two; what the retry still gets wrong is shown under the answer.
    if grounded is not None and grounded["problems"]:
        second = build_agent(
            rt, agent_settings, plan, profile_record.current(),
            invariant_record=invariant_record, tools=tools, knowledge=knowledge,
            knowledge_template=grounding_module.with_correction(template, grounded["problems"]),
            json_answer=json_answer,
        ).ask(asked)
        second_grounded = read_grounded(second, grounding_on, gate)
        # A retry that fails in transport leaves the first answer standing,
        # as the two retries below do.
        if second_grounded is not None:
            second_grounded["retried"] = {"problems": grounded["problems"], "answer": reply.answer}
            reply, grounded = second, second_grounded

    # Day 13, and it happens *before* the turn is stored, which is the whole
    # of why it is here rather than at the end with the proposer.
    #
    # The machine can refuse a transition all day and the conversation will
    # carry on regardless: the user asks for the work to be called finished,
    # the model obliges, `check` says no, and the only trace is a field
    # nobody is looking at. So the answer is judged against the stage before
    # anybody sees it, and one that did the work of a stage this task cannot
    # reach is thrown away and asked for again.
    advice = run_advisor(rt, settings, plan, asked, reply)
    if advice is not None and advice.rejected:
        second = build_agent(
            rt, agent_settings, plan, profile_record.current(),
            correction=phases_module.correction(
                plan.task, advice.advice.stage, advice.decision
            ),
            invariant_record=invariant_record,
            # The retry gets the same tools. It is a new answer to the same
            # question, and one that could not look anything up would be a
            # worse answer held to a stricter standard - it also means a
            # rejected turn can pay for the tool calls twice, which is the
            # honest price of asking again rather than editing.
            tools=tools,
            knowledge=knowledge,
            knowledge_template=template,
            json_answer=json_answer,
        ).ask(asked)
        second_grounded = read_grounded(second, grounding_on, gate)
        # A retry that itself fails leaves the first answer standing. It was
        # the wrong answer, but it is an answer, and swapping it for a
        # transport error would punish the user for the model's mistake.
        if second.error is None:
            reply, grounded = second, second_grounded
            advice.retried = True

    # Day 14, and it happens here for day 13's reason twice over: before the
    # turn is stored, because the answer may not survive it - and *after* the
    # stage retry above, because what has to be checked is the answer that
    # would actually be shown. Judging the first draft of an answer that was
    # already thrown away would be checking a thing nobody will ever read.
    #
    # The honest cost, since it is easy to hide: a turn that trips both
    # subsystems buys three answers and two side requests. It is not collapsed
    # into one retry, because they refuse for unrelated reasons - and the
    # stage retry's answer is a new answer that nothing has checked yet.
    check = run_invariant_check(rt, settings, invariant_record, asked, reply)
    if check is not None and check.rejected:
        second = build_agent(
            rt, agent_settings, plan, profile_record.current(),
            invariant_record=invariant_record,
            invariant_correction=invariants_module.correction(
                invariant_record, check.violations
            ),
            tools=tools,
            knowledge=knowledge,
            knowledge_template=template,
            json_answer=json_answer,
        ).ask(asked)
        second_grounded = read_grounded(second, grounding_on, gate)
        # A retry that itself fails leaves the first answer standing, exactly
        # as above. It is the answer that broke a rule, which is bad - but
        # replacing it with a transport error would punish the user twice for
        # something the model did.
        if second.error is None:
            reply, grounded = second, second_grounded
            check.retried = True

    # Day 22: which excerpts went up, and which of them the answer cites -
    # read off the answer that will be shown, after both retries. Day 24 adds
    # each excerpt's full text, for the popup a quote opens, and the grounding
    # report: the sources the answer rests on, its quotes and where each was
    # found, "I do not know" and why, and anything still wrong with it.
    sources = (grounding_module.report(retrieval, reply.answer if reply.error is None else None, grounded)
               if retrieval else None)

    # Failed turns are written down too, so a reload shows the same screen you
    # were looking at - but `Conversation.history()` leaves them out of what
    # gets replayed, so an error never becomes something the model "said".
    conversation = store.append_turn(
        conversation_id,
        message,
        reply.answer,
        settings=as_dict(settings),
        error=reply.error is not None,
        # Day 8: the provider's own count for this turn, saved with the answer
        # it paid for. A failed call has no usage at all - nothing was billed,
        # so nothing is recorded.
        usage=reply.usage,
        # Day 17: what this answer ran to be able to say what it says, stored
        # with it. A reload redraws the cards; nothing has to be called again.
        tools=reply.tool_calls,
        # Day 22: stored for `tools`' reason - the excerpts were this
        # question's, from the index as it was built at the time.
        rag=sources,
        # Day 25: what this message changed in the task state, under its bubble.
        task_state=plan.task_state_changes,
    )
    # Day 25: written with the turn, not before it - see `save_task_state`.
    if settings.rag:
        conversation = store.save_task_state(
            conversation_id, stored_task_state(plan, saved, settings.model)
        ) or conversation

    # Day 11, and deliberately the last thing that happens. The proposer runs
    # *after* the answer has been produced and stored, so a turn that fails
    # here still leaves the user with their reply and their transcript - and
    # so the proposer can be shown what was answered, which is often where
    # the fact worth filing actually is ("use SQLite then" is a decision; the
    # message that prompted it was a question).
    #
    # Nothing it returns is written to a layer. It is stored on the
    # conversation as a suggestion and waits there for somebody to accept it,
    # which is what "you chose explicitly what goes where" means when the
    # agent is allowed to have an opinion.
    proposals = run_proposer(rt, settings, conversation, plan, asked, reply)
    if proposals is not None and proposals.ok:
        conversation = store.set_proposals(
            conversation_id, [p.to_dict() for p in proposals.proposals]
        ) or conversation

    return TurnResult(reply=reply, conversation=conversation, plan=plan, proposals=proposals,
                      personality=profile_record, advice=advice,
                      invariant_record=invariant_record, check=check, sources=sources)


def run_proposer(
    rt: "AgentRuntime",
    settings: ChatSettings,
    conversation: Conversation,
    plan: ContextPlan,
    message: str,
    reply: AgentReply,
) -> ProposalRun | None:
    """Ask the agent what it would file, or don't. Never raises.

    Skipped entirely when the setting is off, and when the turn failed:
    there is no point paying to extract durable facts from an exchange whose
    assistant half is an error message.

    The keys already on file are sent so the model does not propose them
    again - from both layers at once, because a fact that is already
    long-term should not come back as a suggestion for the working one.
    """
    if not settings.memory_proposals or reply.error is not None:
        return None
    filed = rt.stores.long_term.load() + (plan.task.items if plan.task else [])
    return MemoryProposer(rt.llm, settings.model).propose(
        message, reply.answer, filed
    )


def run_invariant_check(
    rt: "AgentRuntime",
    settings: ChatSettings,
    record: Invariants,
    message: str,
    reply: AgentReply,
) -> CheckRun | None:
    """Read the answer back against the rules. Never raises.

    Runs *before* the turn is stored, for `run_advisor`'s reason and more
    bluntly: the caller may throw the answer away on what this returns, and an
    answer that broke a rule must never reach the disk, because the transcript
    is replayed into later requests. One stored violation becomes an example
    the model follows for the rest of the conversation.

    Skipped in three cases, and each is a rule rather than a saving:

    * the setting is off - the rules still went up with the request, nothing
      reads the answer back. See `Settings.invariant_check`;
    * there are no rules in force, or the set is switched off - there is
      nothing to check against, and paying for a model to confirm that is
      the clearest waste in the file;
    * **the turn failed** - an error message is not evidence about anything.
      A 401 does not break an invariant, and a checker handed one would be
      reading a transport failure for opinions about architecture.
    """
    if not settings.invariant_check or reply.error is not None:
        return None
    if not invariants_module.numbered(record):
        return None
    return InvariantWatcher(rt.llm, settings.model).check(
        record, message, reply.answer
    )


def run_advisor(
    rt: "AgentRuntime",
    settings: ChatSettings,
    plan: ContextPlan,
    message: str,
    reply: AgentReply,
) -> AdviceRun | None:
    """Read the answer, work out which stage it belongs to, let the machine rule.

    Runs *before* the turn is stored, because the caller may throw the answer
    away on what this returns. That is the difference between this and
    `run_proposer`, which runs last and changes nothing anybody sees.

    Skipped in four cases, and each one is a rule rather than a saving:

    * the setting is off - the machine is still enforced, just moved by hand;
    * the turn failed - an error message is not evidence about a task;
    * the chat is in no task, or the task is finished - there is no machine
      to move;
    * **the task is paused** - which is what pausing means. Nothing is bought
      and nothing is asked, so a paused task cannot be nudged along by a
      conversation that happens to be going on next to it.

    What comes back is applied in one order and it matters: steps first, then
    the move. The ticks are observations about the checklist and are true
    whether or not the stage changed - and the required ones among them are
    what `check` reads when deciding whether the move it is about to be asked
    for is allowed. Ticking after moving would mean every forward move was
    judged against a checklist one turn out of date.

    The ticks stand even when the answer is then rejected. They are claims
    about what the exchange settled - a goal agreed is a goal agreed - and
    the answer being out of stage does not unsettle them. It also leaves the
    retry looking at an accurate checklist, which is what `correction` needs
    to say what is actually outstanding.
    """
    if not settings.task_state or reply.error is not None:
        return None
    task = plan.task
    if task is None or not task.is_active or task.paused:
        return None

    run = PhaseAdvisor(rt.llm, settings.model).advise(task, message, reply.answer)
    if not run.ok or run.advice is None:
        return run

    already = set(task.ticked())
    for key in run.advice.steps:
        if key in already:
            continue
        task = rt.stores.tasks.set_step(task.id, key, True) or task
        run.ticked.append(key)
    if run.advice.note:
        task = rt.stores.tasks.set_note(task.id, run.advice.note) or task

    if run.advice.stage and run.advice.stage != task.phase:
        # `check` rules, and what happens next depends on *which* rule said no
        # - the one distinction this function exists to draw.
        #
        #   reachable, or only the checklist in the way -> a suggestion. The
        #     agent has an opinion about where the work stands and a person
        #     presses the button. It never moves the task itself: a stage that
        #     changed because a model said something is a stage nobody
        #     decided.
        #
        #   no such edge -> the answer did the work of a stage this task
        #     cannot reach. That is not an opinion to be offered, it is an
        #     answer to be thrown away, and the caller does that.
        decision = phases_module.check(task, run.advice.stage)
        run.decision = decision
        if decision.ok or decision.code == phases_module.INCOMPLETE:
            task = rt.stores.tasks.suggest(task.id, run.advice.stage, run.advice.why) or task
            run.suggested = True

    # The panel is drawn from the plan, and the plan is holding the task as it
    # was when the request was built. Putting the moved record back means the
    # screen that shows the answer also shows the stage that answer moved it
    # to, rather than one that has to be refreshed to be true.
    plan.task = task
    return run
