"""Day 21: a local document index - load, split, embed, store.

    Loader (by format)  ->  Document: [Section(title_path, start, end)]
                        ->  Chunker (fixed | structural)  ->  Chunk + metadata
                        ->  Embedder (Ollama, behind an interface)
                        ->  IndexStore (one SQLite file)

Every stage is a module of its own and each one knows only about the stage
before it: a chunker reads a `Document` and never a file, an embedder reads
strings and never a chunk, the store reads chunks and vectors and never an
HTTP response. So a new format is a loader, a new model is an embedder, and
neither touches the other four.

Nothing in this package imports from the app around it - no agent, no
FastAPI, no `store.py`. The web tab (`indexing_api.py`) was its first caller.

Day 22 adds the two modules that put the index to use:

    augment     the question -> the k nearest chunks -> numbered excerpts for a prompt
    control     control questions: what an answer must say, where it should come from

Still no model call in here: the excerpts are text, and the agent puts them
in a request the way it puts every other block in one.

Day 23 adds the second stage:

    rerank      more candidates than will be sent -> a judge model scores each -> the ones
                at the threshold or above, at most k (possibly none)

It needs a model, and is handed one as a function - a chat-completions body
in, the response JSON out - so the package still imports nothing from the app.

Day 24 adds what the answer must bring back:

    grounding   the answer as JSON - sources, quotes, or "I don't know" with a question
                back - every quote looked up in the chunk it names; and the gate: when the
                judge kept nothing, the model is told not to answer
"""

from .augment import Retrieval, RetrievalError, render_excerpts, retrieve
from .chunkers import Chunk, Chunker, FixedChunker, StructuralChunker, make_chunker
from .documents import Document, Section, section_label
from .embedders import Embedder, EmbedderError, OllamaEmbedder, make_embedder
from .index_store import IndexStore, SearchHit
from .loaders import load_file, loader_for
from .rerank import LLMReranker, RerankError, keep

__all__ = [
    "Chunk",
    "Chunker",
    "Document",
    "Embedder",
    "EmbedderError",
    "FixedChunker",
    "IndexStore",
    "LLMReranker",
    "OllamaEmbedder",
    "RerankError",
    "Retrieval",
    "RetrievalError",
    "SearchHit",
    "Section",
    "StructuralChunker",
    "keep",
    "load_file",
    "loader_for",
    "make_chunker",
    "make_embedder",
    "render_excerpts",
    "retrieve",
    "section_label",
]
