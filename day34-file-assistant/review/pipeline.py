"""One review, end to end - from two commits to checked findings. Nothing is posted here.

    Reviewer(llm, config, knowledge).review(repo, base, head, title, description)
        git.merge_base, git.review_diff        the change, whole files, copies found
        diff.parse, context.plan               files -> batches within the budget
        knowledge.for_batch                    RAG: the rest of the project, per batch
        llm.complete                           one request per batch (in parallel), JSON back,
                                               one retry when it is not JSON
        findings.read_findings, dedupe         checked against what the model was shown
        -> ReviewResult

`publish` takes the result to GitHub; `report` writes it as Markdown. A
request that fails does not fail the others: its files are listed in the
summary as not reviewed, and only a review where every request failed is an
error.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from core.tokens import sum_usage, usage_from_response
from llm.client import LLMClientError

from . import git, prompt
from .config import ReviewConfig
from .context import Batch, Plan, plan
from .diff import FileDiff, parse
from .findings import Finding, dedupe, parse_reply, read_findings
from .knowledge import Knowledge, KnowledgeError


@dataclass
class BatchRun:
    batch: Batch
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    usage: list[dict] = field(default_factory=list)
    requests: int = 0
    error: str | None = None
    seconds: float = 0.0


@dataclass
class ReviewResult:
    title: str
    base: str
    head: str
    merge_base: str
    model: str
    files: list[FileDiff]
    plan: Plan
    runs: list[BatchRun]
    knowledge: dict | None = None
    knowledge_problem: str | None = None
    seconds: float = 0.0

    @property
    def findings(self) -> list[Finding]:
        return dedupe([f for run in self.runs for f in run.findings])

    @property
    def summaries(self) -> list[str]:
        return [run.summary for run in self.runs if run.summary]

    @property
    def failed(self) -> list[BatchRun]:
        return [run for run in self.runs if run.error]

    @property
    def usage(self) -> dict:
        return sum_usage(u for run in self.runs for u in run.usage)

    @property
    def requests(self) -> int:
        return sum(run.requests for run in self.runs)

    @property
    def ok(self) -> bool:
        return not self.runs or len(self.failed) < len(self.runs)


class Reviewer:
    def __init__(self, llm, config: ReviewConfig, knowledge: Knowledge | None = None,
                 knowledge_problem: str | None = None, log=print) -> None:
        self.llm = llm
        self.config = config
        self.knowledge = knowledge
        self.knowledge_problem = knowledge_problem
        self.log = log

    def review(self, repo: Path, base: str, head: str, title: str = "", description: str = "") -> ReviewResult:
        started = time.monotonic()
        fork_point = git.merge_base(repo, base, head)
        files = parse(git.review_diff(repo, fork_point, head, copies=self.config.detect_copies))
        the_plan = plan(files, self.config)
        self.log(f"[review ] {len(files)} files changed: {len(the_plan.files)} reviewed in "
                 f"{len(the_plan.batches)} request(s), {len(the_plan.unchanged)} unchanged copies or "
                 f"renames, {len(the_plan.skipped)} skipped, {len(the_plan.deleted)} deleted")
        problem = self.knowledge_problem
        for batch in the_plan.batches:
            if self.knowledge is None:
                break
            try:
                batch.excerpts = self.knowledge.for_batch(
                    batch, self.config.excerpts_per_file, self.config.excerpts_max,
                    self.config.excerpt_chars, extra_query=f"{title}\n{description}")
            except KnowledgeError as exc:
                problem = f"the search failed: {exc}"
                self.log(f"[review ] RAG search failed, reviewing without excerpts: {problem}")
                break
        deleted = [f.path for f in the_plan.deleted]
        jobs = []
        for n, batch in enumerate(the_plan.batches, 1):
            others = [p for b in the_plan.batches if b is not batch for p in b.paths]
            # A search that failed half-way leaves the batches before it their excerpts.
            message = prompt.user_message(title, description, batch, (n, len(the_plan.batches)),
                                          others, deleted, None if batch.excerpts else problem)
            jobs.append((batch, message))
        workers = max(1, min(self.config.parallel, len(jobs) or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            runs = list(pool.map(lambda job: self.run_batch(*job), jobs))
        return ReviewResult(title, base, head, fork_point, self.config.model, files, the_plan, runs,
                            self.knowledge.describe() if self.knowledge else None, problem,
                            time.monotonic() - started)

    def body(self, messages: list[dict]) -> dict:
        body = {"model": self.config.model, "messages": messages,
                "response_format": {"type": "json_object"}, "temperature": self.config.temperature}
        effort = self.config.reasoning_effort
        if effort:
            body["reasoning_effort"] = "none" if effort == "off" else effort
        return body

    def run_batch(self, batch: Batch, message: str) -> BatchRun:
        run = BatchRun(batch)
        started = time.monotonic()
        messages = [{"role": "system", "content": prompt.system(self.config.language)},
                    {"role": "user", "content": message}]
        names = ", ".join(batch.paths[:3]) + (" ..." if len(batch.paths) > 3 else "")
        self.log(f"[review ] asking {self.config.model}: {len(batch.files)} file(s) ({names}), "
                 f"{batch.chars} chars of diff, {len(batch.excerpts)} excerpts")
        try:
            for attempt in range(2):
                response = self.llm.complete(self.body(messages))
                run.requests += 1
                usage = usage_from_response(response.response)
                if usage:
                    run.usage.append(usage)
                choice = (response.response.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content") or ""
                summary, raw, problem = parse_reply(content)
                cut = choice.get("finish_reason") == "length"
                if problem and cut:
                    problem = "the reply was cut off at the model's output limit"
                if problem is None:
                    run.summary = summary
                    run.findings, run.dropped = read_findings(raw, batch)
                    break
                if attempt == 0:
                    self.log(f"[review ] reply not JSON ({problem}) - asking once more")
                    if cut:
                        # Half a reply is no use to repeat; the same question, asked for less.
                        messages = messages[:-1] + [{"role": "user",
                                                     "content": messages[-1]["content"] + prompt.SHORTER}]
                    else:
                        messages = messages + [{"role": "assistant", "content": content},
                                               {"role": "user", "content": prompt.CORRECTION.format(problem=problem)}]
                else:
                    run.error = f"the model did not answer with the review JSON: {problem}"
        except LLMClientError as exc:
            run.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - a reply of an unexpected shape fails its batch only
            run.error = f"{exc.__class__.__name__}: {exc}"
        run.seconds = time.monotonic() - started
        if run.error:
            self.log(f"[review ] request failed: {run.error}")
        else:
            self.log(f"[review ] {len(run.findings)} finding(s) in {run.seconds:.0f} s"
                     + (f", {len(run.dropped)} dropped" if run.dropped else ""))
        return run
