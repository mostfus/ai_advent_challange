"""The FastAPI app: the page, one router per domain, and the runtime behind them.

    browser -> /api/... -> web.routes.<domain> -> AgentRuntime (core) -> storage / LLM / tools / RAG

`create_app(runtime)` is the whole of it. Without a runtime it builds the
app's own (`AgentRuntime.build()`: DeepSeek key from `.env`, `local_llm.json`,
`corpus.json`, stores under `data/`); the tests hand in one over a fake model
and temp directories.

Every handler is a plain `def`, so FastAPI runs it in its threadpool and a
turn that takes a minute does not hold up the page's other calls - the build
progress, a panel refresh - behind it; the stores take a lock for the same
reason. A refusal from the core
(`core.errors.ServiceError`) becomes `{"detail": ...}` with its status - the
shape FastAPI's own errors have, so the page reads both the same way.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from core.errors import ServiceError
from core.runtime import AgentRuntime
from web.routes import ROUTERS
from web.routes.config import STATIC_DIR


class RevalidatedStaticFiles(StaticFiles):
    """Static files a browser must check before reusing - `Cache-Control: no-cache`.

    Day 23. Every day's app is served from the same `localhost:8000/static/...`,
    and without a caching header a browser guesses how long a file stays
    fresh. One that had the previous day's script kept running it on the next
    day's page without asking. `no-cache` still keeps
    the copy; it only makes the browser ask first, and an unchanged file
    comes back as an empty 304.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


async def service_error(_request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status)


def create_app(runtime: AgentRuntime | None = None) -> FastAPI:
    """The web app over `runtime` - or over the app's own, built from config and `.env`."""
    runtime = runtime if runtime is not None else AgentRuntime.build()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Day 26: llama-server, if `local_llm.json` asks for it - and, day 31,
        # the open project's MCP servers - started with the app and stopped
        # with it, never waited for.
        runtime.start()
        yield
        runtime.stop()

    app = FastAPI(title="LLM agent", lifespan=lifespan)
    app.state.runtime = runtime
    app.add_exception_handler(ServiceError, service_error)
    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")
    for router in ROUTERS:
        app.include_router(router)
    return app
