"""The agent, assembled: LLM + storage + tools + RAG, and the operations on them.

`AgentRuntime` is the one object everything else is handed. It owns:

    llm        the client every request goes through (`llm.RoutingClient`:
               DeepSeek, or the local llama-server by model id)
    stores     the JSON stores under `data/` (`storage`)
    index      the document index the chat retrieves from (`rag.indexing.IndexService`,
               `corpus.json`)
    services   conversations, memory, personality, invariants, mcp, project
               (`core.services`) - what the web routes call
    tools      a fresh `tools.ToolRegistry` per turn (`tool_registry()`); an
               agentic-code turn gets the open project's instead
               (`project.code_session()`, day 34)
    qa         the QA agent (day 35): simulators, the XCUITest bridge, runs of a
               scenario on an app, the suite and its generated tests (`qa_agent.json`)
    project    the folder the assistant works on (day 31): the index reads it,
               its git MCP server runs on it (`core.project`), the file tools
               work in it (`tools.files`, `code_agent.json`)

and runs a turn: `chat(message, ...)` -> `core.turn.TurnResult`.

Nothing here imports a web framework, so the same runtime serves the web app
(`web.app.create_app`) and a plain script:

    from core.runtime import AgentRuntime

    rt = AgentRuntime.build()                 # DEEPSEEK_API_KEY from .env, data/ next to this
    print(rt.chat("What does core/turn.py do?").answer)
    coding = ChatSettings(agentic_code=True)
    print(rt.chat("Find every place ToolRegistry is used", settings=coding).answer)

`build(llm=..., data_dir=...)` takes a client of your own (anything with
`complete(body)`) and puts every store under one directory - which is how the
tests run a turn with a fake model and no web app at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core import agent as agent_module
from core.coder import CodeAgentConfig
from core.project import ProjectServers, ProjectToolsConfig
from core.services.conversations import ConversationService
from core.services.invariants import InvariantService
from core.services.mcp import McpService
from core.services.memory import MemoryService
from core.services.personality import PersonalityService
from core.services.project import ProjectService
from core.services.qa import QaService
from core.settings import ChatSettings
from core.turn import TurnResult, run_turn
from llm import local as local_llm
from llm.client import DeepSeekClient
from llm.routing import RoutingClient
from qa.config import QaConfig
from rag.corpus import CorpusConfig
from rag.embedders import Embedder
from rag.indexing import IndexService
from storage import (
    SINGLE_CONVERSATION_ID,
    ConversationStore,
    InvariantStore,
    LongTermStore,
    McpStore,
    PersonalityStore,
    ProjectStore,
    TaskStore,
)
from tools.registry import ToolRegistry


@dataclass
class Stores:
    """Every JSON store, one per lifetime: a conversation's memory dies with the
    conversation, a task's outlives every chat about it, and the long-term
    layer, the profiles, the rules and the MCP servers outlive everything."""

    conversations: ConversationStore
    tasks: TaskStore
    long_term: LongTermStore
    personalities: PersonalityStore
    invariants: InvariantStore
    mcp_servers: McpStore
    project: ProjectStore

    @classmethod
    def under(cls, data_dir: Path | str | None = None) -> "Stores":
        """All of them under one directory - or, with None, each where its
        environment variable or default says (`storage`)."""
        if data_dir is None:
            return cls(ConversationStore(), TaskStore(), LongTermStore(), PersonalityStore(),
                       InvariantStore(), McpStore(), ProjectStore())
        root = Path(data_dir)
        return cls(
            ConversationStore(root / "conversations"),
            TaskStore(root / "tasks"),
            LongTermStore(root / "memory"),
            PersonalityStore(root / "personality"),
            InvariantStore(root / "invariants"),
            McpStore(root / "mcp"),
            ProjectStore(root / "project"),
        )


class AgentRuntime:
    """The composition root: one per process (or per test)."""

    def __init__(
        self,
        llm,
        stores: Stores,
        index: IndexService,
        local_config: local_llm.LocalLLMConfig | None = None,
        local_server: local_llm.LocalServer | None = None,
        seed: bool = True,
        project_tools: ProjectToolsConfig | None = None,
        code: CodeAgentConfig | None = None,
        qa: QaConfig | None = None,
        qa_dir: Path | None = None,
    ) -> None:
        self.llm = llm
        self.stores = stores
        self.index = index
        self.local_config = local_config
        self.local_server = local_server
        self.conversations = ConversationService(self)
        self.memory = MemoryService(self)
        self.personality = PersonalityService(self)
        self.invariants = InvariantService(self)
        self.mcp = McpService(self)
        self.project = ProjectService(self, ProjectServers(
            project_tools if project_tools is not None else ProjectToolsConfig(),
            log=lambda line: print(line, flush=True)), code)
        # Day 35: the QA agent - the simulator, its bridge, runs and the suite.
        self.qa = QaService(self, qa, qa_dir)
        if seed:
            # Written once, on the first start, and only into stores nobody
            # has written yet - see each `seed` for what and why.
            self.personality.seed()
            self.invariants.seed()
            self.mcp.seed()

    # -- building one ----------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        llm=None,
        data_dir: Path | str | None = None,
        corpus: CorpusConfig | None = None,
        embedder: Embedder | None = None,
        local_config: local_llm.LocalLLMConfig | None = None,
        seed: bool = True,
    ) -> "AgentRuntime":
        """A runtime from config files and paths.

        `llm` - any object with `complete(body)`; None builds the app's own:
        DeepSeek (key from `.env`, `DEEPSEEK_API_KEY`) routed together with
        the local model when `local_llm.json` enables one. Raises
        `llm.client.LLMClientError` without a key, `ValueError` for a bad
        config file.

        `data_dir` - every store and the index under one directory; None keeps
        each in its default place under `data/`, or wherever its environment
        variable points.

        `corpus` - what the index is built from; None reads `corpus.json`.
        `embedder` - None is bge-m3 over Ollama (`rag.embedders`).
        The project's MCP servers come from `project_mcp.json` (`core.project`),
        agentic code's budget, limits and scenarios from `code_agent.json`.
        """
        if local_config is None:
            local_config = local_llm.load_config()
        local_server = None
        if local_config.enabled:
            # Day 26: the local model joins the model list, and every request
            # whose `model` is its id goes to llama-server instead of DeepSeek.
            if local_config.model_id not in agent_module.MODELS:
                agent_module.MODELS.append(local_config.model_id)
            local_server = local_llm.LocalServer(local_config, log=lambda line: print(line, flush=True))
        if llm is None:
            remote = DeepSeekClient.from_env()
            llm = (RoutingClient(remote, local_llm.LlamaCppClient(local_config), local_config.model_id)
                   if local_config.enabled else RoutingClient(remote))
        root = Path(data_dir) if data_dir is not None else None
        index = IndexService(root / "index" if root else None,
                             corpus=corpus if corpus is not None else CorpusConfig.load(),
                             embedder=embedder)
        return cls(llm, Stores.under(root), index, local_config=local_config,
                   local_server=local_server, seed=seed, project_tools=ProjectToolsConfig.load(),
                   code=CodeAgentConfig.load(), qa=QaConfig.load(), qa_dir=root / "qa" if root else None)

    # -- what it does ----------------------------------------------------------

    def tool_registry(self) -> ToolRegistry:
        """The tools the next turn is offered - built fresh, off the stored catalogues.

        One provider: the MCP servers switched on in the panel. The open
        project's git server and file tools are not in it - they are agentic
        code's, `project.code_session()`.
        """
        return ToolRegistry([self.mcp.provider()])

    def chat(
        self,
        message: str,
        conversation_id: str = SINGLE_CONVERSATION_ID,
        settings: ChatSettings | None = None,
    ) -> TurnResult:
        """One turn: the message answered, checked and stored (`core.turn.run_turn`)."""
        return run_turn(self, message, conversation_id, settings)

    # -- the local model's process ---------------------------------------------

    def start(self) -> None:
        """Start llama-server, if `local_llm.json` asks for it and nothing switched it off,
        and the open project's MCP servers.

        Neither is waited for - loading a model takes seconds and a first
        download minutes, and a chat asking it early gets "still loading"
        rather than an app that will not boot. `LOCAL_LLM_AUTOSTART=0` and
        `PROJECT_MCP_AUTOSTART=0` keep them off.
        """
        if (self.local_server is not None and self.local_config is not None
                and self.local_config.autostart and local_llm.autostart_enabled()):
            self.local_server.start()
        self.project.start()

    def stop(self) -> None:
        self.qa.shutdown()
        self.project.stop()
        if self.local_server is not None:
            self.local_server.stop()
