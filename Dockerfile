FROM python:3.11-slim

# uv manages the venv; the image builds a venv from the committed lock so the
# native deps (onnxruntime, tokenizers, ...) are the right arch for the target.
RUN pip install --no-cache-dir uv

WORKDIR /app

# Metadata + lock first for layer caching; source after.
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY settings ./settings

# `uv export --frozen` emits only third-party deps (not the project), which we
# install into the venv. The `src` package is importable directly from /app, so
# we run the module rather than a console script and avoid building the project.
RUN uv venv /app/.venv \
    && uv export --frozen > /app/requirements.txt \
    && uv pip install --python /app/.venv/bin/python -r /app/requirements.txt

ENV PATH="/app/.venv/bin:$PATH" \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000

CMD ["python", "-m", "src.mcp.server"]
