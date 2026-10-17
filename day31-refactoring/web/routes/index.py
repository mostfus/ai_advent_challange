"""Day 21: the Index tab - `rag.indexing.IndexService`, over HTTP.

    GET    /api/index                       embedder, corpus (as `corpus.json` says), the index, job
    POST   /api/index/uploads               add a file to the index's uploads/
    DELETE /api/index/uploads/{name}
    POST   /api/index/preview               chunk without embedding: stats + the chunks of one file
    POST   /api/index/build                 chunk, embed, store - in the background
    GET    /api/index/job                   how far the build has got
    DELETE /api/index/indexes[/{name}]      reset: drop the chunks and vectors of one index, or all
    DELETE /api/index/cache                 forget cached vectors, so a rebuild re-embeds everything
    GET    /api/index/indexes/{name}/chunks browse what was stored
    POST   /api/index/search                one query against the index
"""

from __future__ import annotations

from fastapi import APIRouter

from web.deps import Runtime
from web.schemas import BuildRequest, ChunkingRequest, SearchRequest, UploadRequest

router = APIRouter(prefix="/api/index")


@router.get("")
def get_overview(rt: Runtime) -> dict:
    return rt.index.overview()


@router.post("/uploads")
def upload(req: UploadRequest, rt: Runtime) -> dict:
    return rt.index.upload(req.name, req.content)


@router.delete("/uploads/{name}")
def delete_upload(name: str, rt: Runtime) -> dict:
    return rt.index.delete_upload(name)


@router.post("/preview")
def preview(req: ChunkingRequest, rt: Runtime) -> dict:
    return rt.index.preview(req.params, req.source, req.limit)


@router.post("/build")
def build(req: BuildRequest, rt: Runtime) -> dict:
    return rt.index.build(req.params)


@router.get("/job")
def get_job(rt: Runtime) -> dict:
    return rt.index.job.snapshot()


@router.delete("/indexes")
def reset_all(rt: Runtime) -> dict:
    return rt.index.reset(None)


@router.delete("/indexes/{name}")
def reset_index(name: str, rt: Runtime) -> dict:
    return rt.index.reset(name)


@router.delete("/cache")
def clear_cache(rt: Runtime) -> dict:
    return rt.index.clear_cache()


@router.get("/indexes/{name}/chunks")
def list_chunks(name: str, rt: Runtime, source: str | None = None, offset: int = 0, limit: int = 30) -> dict:
    return rt.index.chunks(name, source, offset, limit)


@router.post("/search")
def search(req: SearchRequest, rt: Runtime) -> dict:
    return rt.index.search(req.query, req.k, req.source)
