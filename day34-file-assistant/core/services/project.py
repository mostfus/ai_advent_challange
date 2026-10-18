"""Day 31: the open project - which folder, its index and its MCP servers, and switching them.

    current()        the project open now: the folder last opened, or the app's own
    report()         the project, its servers and its index, for the Project tab
    open(path)       switch: remember the folder, point the index at it (the old
                     project's chunks dropped), restart the servers, start a build
    start() / stop() the servers of the project open at launch, and at exit
    code_session()   day 34: what one agentic-code turn works with - the file
                     tools and the git server, a change log, the system prompt

The servers start in the background and the index builds in the background,
so opening a project answers at once; the tab polls `report()` to watch both.
`PROJECT_MCP_AUTOSTART=0` keeps the servers from starting at all - the tests
and the UI tests run without `uvx` and `npx`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from dataclasses import dataclass

from core import coder
from core.errors import Conflict, Invalid, ServiceError, Unavailable
from core.project import APP_DIR, Project, ProjectServers
from rag import augment as augment_module
from rag.chunkers import DEFAULT_PARAMS
from tools.files import ChangeLog, FilesProvider
from tools.registry import ToolRegistry

if TYPE_CHECKING:
    from core.runtime import AgentRuntime


def autostart_enabled() -> bool:
    return os.getenv("PROJECT_MCP_AUTOSTART", "1") != "0"


@dataclass
class CodeSession:
    """One agentic-code turn's equipment: what it may call, what its writes did, who it is."""

    project: Project
    tools: ToolRegistry
    changes: ChangeLog
    system_prompt: str
    max_tool_rounds: int
    max_tool_calls: int


class ProjectService:
    """The open project, over `rt.stores.project`, `rt.index` and the project's servers."""

    def __init__(self, rt: "AgentRuntime", servers: ProjectServers,
                 code: coder.CodeAgentConfig | None = None) -> None:
        self.rt = rt
        self.servers = servers
        self.code = code if code is not None else coder.CodeAgentConfig()
        self._project: Project | None = None
        # The index reads the open project's folder from the first request on.
        self.rt.index.corpus_config = self.rt.index.corpus_config.at(self.current().path)

    def current(self) -> Project:
        """The folder last opened - or the app's own, when none was or it is gone."""
        if self._project is None:
            stored = self.rt.stores.project.load()
            try:
                self._project = Project.at(stored or APP_DIR)
            except ValueError:
                self._project = Project.at(APP_DIR)
        return self._project

    def report(self) -> dict:
        record = self.rt.index.built()
        stats = (record or {}).get("stats") or {}
        return {
            "project": self.current().describe(),
            "servers": self.servers.status(),
            "servers_config": self.servers.config.path.name if self.servers.config.path else None,
            "autostart": autostart_enabled(),
            "index": {
                "built": record is not None,
                "built_at": (record or {}).get("built_at"),
                "documents": stats.get("documents"),
                "chunks": stats.get("chunks"),
                "job": self.rt.index.job.snapshot(),
            },
        }

    def open(self, path: str) -> dict:
        """Switch to the project in `path` - an absolute folder path."""
        try:
            project = Project.at(path)
        except ValueError as exc:
            raise Invalid(str(exc)) from exc
        if self.rt.index.job.snapshot().get("running"):
            raise Conflict("The index is being built - wait for it to finish, then open the project")
        self.rt.stores.project.save(str(project.path))
        self._project = project
        self.rt.index.switch(project.path)
        if autostart_enabled():
            self.servers.open(project)
        else:
            self.servers.stop()
        note = None
        if self.rt.index.built() is None:
            try:
                self.rt.index.build(DEFAULT_PARAMS)
            except (Unavailable, Conflict) as exc:
                note = f"The index was not built: {exc.detail}"
        return {**self.report(), "note": note}

    def start(self) -> None:
        """Start the open project's servers, if nothing switched them off. Not waited for."""
        if autostart_enabled() and self.servers.project is None:
            self.servers.open(self.current())

    def stop(self) -> None:
        self.servers.stop()

    def code_session(self) -> CodeSession:
        """The file tools and the git server for one agentic-code turn - waiting a
        little for servers still starting - with a fresh change log.

        `search_docs` is offered only when the project's index is built; the
        system prompt says which tools there are, so a model is not told about
        git in a folder that has none.
        """
        self.start()
        self.servers.wait()
        project = self.current()
        docs = self.search_docs if self.rt.index.built() is not None else None
        log = ChangeLog()
        files = FilesProvider(project.path, self.code.files, git=project.git_root is not None,
                              changes=log, docs=docs)
        servers = self.servers.provider()
        prompt = coder.system_prompt(project, docs=docs is not None, git_tools=bool(servers.tools()))
        instructions = coder.project_instructions(project, self.code)
        if instructions:
            prompt += "\n\n" + instructions
        return CodeSession(project, ToolRegistry([files, servers]), log, prompt,
                           self.code.max_tool_rounds, self.code.max_tool_calls)

    def search_docs(self, query: str, k: int) -> str:
        """The project's index searched by meaning - `search_docs`'s answer."""
        try:
            retrieval = self.rt.index.retrieve(query, k)
        except ServiceError as exc:
            raise RuntimeError(exc.detail) from exc
        return augment_module.render_excerpts(retrieval)
