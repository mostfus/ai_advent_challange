# Day 18: one image, three processes - the agent app and its two MCP servers.
# docker-compose.yml runs it three times with a different command each.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir "uv==0.8.17"

WORKDIR /app

# Dependencies first and from the lock file, so a code change does not
# reinstall them and the VPS runs exactly the versions tested here.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY *.py ./
COPY static ./static

# Conversations, memory, the MCP catalogue, the events database and the
# courier's cursor all live under data/ - the one thing that must outlive a
# container, so it is a volume.
VOLUME ["/app/data"]

CMD ["python", "server.py"]
