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
FastAPI, no `store.py`. The web tab (`indexing_api.py`) is its caller today,
and the next day's retrieval will be the second.
"""

from .chunkers import Chunk, Chunker, FixedChunker, StructuralChunker, make_chunker
from .documents import Document, Section, section_label
from .embedders import Embedder, EmbedderError, OllamaEmbedder, make_embedder
from .index_store import IndexStore, SearchHit
from .loaders import load_file, loader_for

__all__ = [
    "Chunk",
    "Chunker",
    "Document",
    "Embedder",
    "EmbedderError",
    "FixedChunker",
    "IndexStore",
    "OllamaEmbedder",
    "SearchHit",
    "Section",
    "StructuralChunker",
    "load_file",
    "loader_for",
    "make_chunker",
    "make_embedder",
    "section_label",
]
