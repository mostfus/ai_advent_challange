"""Day 31: the open project - which folder, its index and its MCP servers, and switching them.

    current()        the project open now: the folder last opened, or the app's own
    report()         the project, its servers and its index, for the Project tab
    open(path)       switch: remember the folder, point the index at it (the old
                     project's chunks dropped), restart the servers, start a build
    start() / stop() the servers of the project open at launch, and at exit
    tools()          the registry a /help turn is offered: the project's servers only

The servers start in the background and the index builds in the background,
so opening a project answers at once; the tab polls `report()` to watch both.
`PROJECT_MCP_AUTOSTART=0` keeps the servers from starting at all - the tests
and the UI tests run without `uvx` and `npx`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from core.errors import Conflict, Invalid, Unavailable
from core.project import APP_DIR, Project, ProjectServers
from rag.chunkers import DEFAULT_PARAMS
from tools.registry import ToolRegistry

if TYPE_CHECKING:
    from core.runtime import AgentRuntime


def autostart_enabled() -> bool:
    return os.getenv("PROJECT_MCP_AUTOSTART", "1") != "0"


class ProjectService:
    """The open project, over `rt.stores.project`, `rt.index` and the project's servers."""

    def __init__(self, rt: "AgentRuntime", servers: ProjectServers) -> None:
        self.rt = rt
        self.servers = servers
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

    def tools(self) -> ToolRegistry:
        """The project's tools for one /help turn - waiting a little for servers still starting."""
        self.start()
        self.servers.wait()
        return ToolRegistry([self.servers.provider()])
