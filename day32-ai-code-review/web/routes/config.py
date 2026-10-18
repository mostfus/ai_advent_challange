"""The page itself, and `/api/config` - everything the page needs to draw its forms.

Every list a form offers - models, strategies, memory layers, profile fields,
task stages, invariant kinds, MCP suggestions, RAG bounds - is served from
the module that owns it, so an option cannot exist in the page and not on
the server, or the other way round.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from core import agent as agent_module
from core import invariants as invariants_module
from core import memory as memory_module
from core import personality as personality_module
from core import phases as phases_module
from core import strategies
from core.settings import ChatSettings, as_dict
from rag import augment as augment_module
from rag import rerank as rerank_module
from storage import MAX_MCP_SERVERS, MAX_PROFILES, MAX_RULES, SINGLE_CONVERSATION_ID
from tools import mcp_client
from web.deps import Runtime

#: The page and its scripts - `static/` in the day folder.
STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

router = APIRouter()


@router.get("/")
def index() -> FileResponse:
    # The page names its scripts by path, so it is held to the same rule as they are.
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/config")
def get_config(rt: Runtime) -> dict:
    """Everything the frontend needs to render a settings form."""
    return {
        "models": agent_module.MODELS,
        # Day 26: which of those is the local one, and how it is doing.
        "local_llm": rt.local_server.status() if rt.local_server is not None else None,
        "system_prompts": [
            {"name": name, "prompt": prompt} for name, prompt in agent_module.SYSTEM_PROMPTS
        ],
        "response_formats": agent_module.RESPONSE_FORMATS,
        "reasoning_efforts": agent_module.REASONING_EFFORTS,
        "stop_sequences_limit": agent_module.STOP_SEQUENCES_LIMIT,
        "temperature": {
            "min": agent_module.TEMPERATURE_MIN,
            "max": agent_module.TEMPERATURE_MAX,
            "step": agent_module.TEMPERATURE_STEP,
            "default": agent_module.DEFAULT_TEMPERATURE,
        },
        "defaults": as_dict(ChatSettings()),
        "single_conversation_id": SINGLE_CONVERSATION_ID,
        "context_messages": {
            "min": agent_module.CONTEXT_MESSAGES_MIN,
            "max": agent_module.CONTEXT_MESSAGES_MAX,
            "default": agent_module.DEFAULT_CONTEXT_MESSAGES,
        },
        # Day 10. The selector is built from this rather than from a list in
        # the page, so a strategy cannot exist in the UI and not on the server
        # - or, worse, the other way round.
        "context_strategies": [
            {"id": name, **strategies.STRATEGY_INFO[name]} for name in strategies.STRATEGIES
        ],
        "context_strategy_default": strategies.DEFAULT_STRATEGY,
        # Day 11. Same trick as the strategies above, for the same reason: the
        # memory panel's three headings, their scopes and - the column that
        # matters - who writes to each, come from `memory.py` rather than from
        # a list in the page.
        "memory_layers": [
            {"id": name, **memory_module.LAYER_INFO[name]} for name in memory_module.LAYERS
        ],
        "memory_writable": list(memory_module.WRITABLE),
        # Day 12. The same trick a third time: the four fields of a profile,
        # their labels and their hints come from `personality.py`, so a field
        # cannot exist in the form and not in the prompt. `lines` is how tall
        # the textarea is, which belongs here for the same reason - "Never do"
        # is a list and "Language" is a word, and the form should say so
        # without the page having to know why.
        "personality_fields": [
            {"id": name, **personality_module.FIELD_INFO[name]}
            for name in personality_module.FIELDS
        ],
        "personality_limit": MAX_PROFILES,
        # Day 13, and the same trick a fourth time - with more riding on it
        # than any of the three before. The stages, their checklists and the
        # table of which may follow which are served from `phases.py`, so the
        # panel draws the machine the server is actually enforcing. A page
        # carrying its own copy of the transition table would eventually draw
        # a button for a move the server refuses, and the day would be over.
        "phases": [
            {
                "id": name,
                **phases_module.PHASE_INFO[name],
                "steps": [step.to_dict() for step in phases_module.STEPS.get(name, ())],
                "next": list(phases_module.TRANSITIONS.get(name, ())),
            }
            for name in phases_module.PHASES
        ],
        "phase_default": phases_module.DEFAULT_PHASE,
        # Day 14, and the fifth time. The four kinds, their labels and their
        # placeholders come from `invariants.KIND_INFO` for the reason all
        # four before it did: a page carrying its own copy of the kinds would
        # eventually offer one the server drops on the way in.
        "invariant_kinds": [
            {"id": name, **invariants_module.KIND_INFO[name]}
            for name in invariants_module.KINDS
        ],
        "invariant_limit": MAX_RULES,
        "invariant_text_limit": invariants_module.MAX_TEXT_CHARS,
        "invariant_because_limit": invariants_module.MAX_BECAUSE_CHARS,
        # Day 16, and the sixth time - with the least riding on it and the
        # most obvious payoff. The suggestions in the add form are the
        # servers `tools.mcp_client` was actually tested against, served from there
        # so that a URL cannot rot in the page while the module that knows
        # about it is corrected.
        "mcp_known": list(mcp_client.KNOWN_SERVERS),
        "mcp_limit": MAX_MCP_SERVERS,
        "mcp_protocol": mcp_client.PROTOCOL_VERSION,
        # Day 22. The bounds of the chat's RAG controls.
        "rag": {"default_k": augment_module.DEFAULT_K, "max_k": augment_module.MAX_K,
                # Day 23: the second stage's bounds.
                "default_candidates": augment_module.DEFAULT_CANDIDATES,
                "max_candidates": augment_module.MAX_CANDIDATES,
                "default_threshold": rerank_module.DEFAULT_THRESHOLD, "max_score": rerank_module.MAX_SCORE},
    }
