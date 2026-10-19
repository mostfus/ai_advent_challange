"""How a route gets at the runtime: `rt: Runtime` in its signature.

`web.app.create_app` keeps the one `AgentRuntime` on `app.state.runtime`;
this dependency hands it to every handler that names it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from core.runtime import AgentRuntime


def get_runtime(request: Request) -> AgentRuntime:
    return request.app.state.runtime


Runtime = Annotated[AgentRuntime, Depends(get_runtime)]
