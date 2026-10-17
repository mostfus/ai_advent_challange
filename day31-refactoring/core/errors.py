"""Why an operation was refused - with the HTTP status a web front end should give it.

The core and the RAG services refuse things for reasons a caller has to be
told: no such conversation, a model nobody configured, an index that was
never built, an embedder that is not running. Each refusal is a
`ServiceError` with a readable `detail` and a `status` - the code the web
layer answers with (`web.app` turns every one into `{"detail": ...}` with
that status, exactly the shape FastAPI's own errors have). A script calling
the core directly catches `ServiceError` and reads `detail`; nothing here
knows what HTTP is beyond that one number.
"""

from __future__ import annotations


class ServiceError(RuntimeError):
    """An operation the core refused, and why."""

    status = 400

    def __init__(self, detail: str, status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if status is not None:
            self.status = status


class Invalid(ServiceError):
    """The request is malformed or names something that cannot be used."""

    status = 400


class NotFound(ServiceError):
    """The thing the request names does not exist."""

    status = 404


class Conflict(ServiceError):
    """The request is fine, the state is not: a build is running, a move is not allowed."""

    status = 409


class TooLarge(ServiceError):
    status = 413


class UpstreamFailed(ServiceError):
    """A model the core depends on answered, and the answer was unusable."""

    status = 502


class Unavailable(ServiceError):
    """Something the core depends on is not there: no LLM client, no embedder."""

    status = 503
