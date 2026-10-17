"""The web front end: a FastAPI app over the core, and the page in `static/`.

    app        create_app(runtime) - the app, the static files, the error mapping,
               llama-server started and stopped with the app
    deps       Runtime - the dependency that hands a route the `AgentRuntime`
    schemas    the request bodies
    routes/    one APIRouter per domain, each handler a line or two over the core

Nothing below `web/` decides anything: every handler parses a request, calls
the runtime, and returns what it got. `server.py` is the entry point.
"""
